#!/usr/bin/env python3
"""gwo store: SQLite coordination cache for GWO V7 (Phase 1 kernel).

Stdlib-only, Windows-compatible. The store lives under GWO_HOME in WAL mode and
is a rebuildable coordination cache: GitHub is the only durable business truth.
Every write resolves caller identity from the spawn-injected ``GWO_AGENT_ID``;
identity columns are derived inside the CLI/store boundary and callers can
never supply them. Each state transition is contained in one explicit SQLite
transaction so failures roll back cleanly without partial authority or
lifecycle state.

See docs/design/gwo-v7-architecture.md and ADRs 0007-0009.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1
SCHEMA_NAME = "gwo-store-v1"

TASK_STATUSES = ("pending", "ready", "dispatched", "done", "failed", "blocked")
DISPATCH_STATUSES = ("active", "done", "blocked", "stopped")
DONE_STATUSES = ("done", "blocked", "stopped")

TASK_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "pending": ("ready",),
    "ready": ("dispatched", "pending"),
    "dispatched": ("done", "failed", "blocked", "ready"),
    "blocked": ("ready", "failed"),
    "failed": ("ready", "pending"),
    "done": (),
}

DISPATCH_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "active": ("done", "blocked", "stopped"),
    "done": (),
    "blocked": ("active", "stopped"),
    "stopped": (),
}


class StoreError(RuntimeError):
    """Base class for gwo store errors."""


class IdentityError(StoreError):
    """Raised when caller identity is missing, ambiguous, or caller-supplied."""


class TransitionError(StoreError):
    """Raised when a requested lifecycle transition is invalid."""


class CoordinatorBusy(StoreError):
    """Raised when a second Coordinator claim is attempted."""


MIGRATIONS: tuple[tuple[str, str], ...] = (
    (
        "0001-initial",
        """
        CREATE TABLE IF NOT EXISTS coordinator (
            repo TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            claimed_at REAL NOT NULL,
            released_at REAL,
            PRIMARY KEY (repo)
        );

        CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            adapter TEXT NOT NULL,
            runtime_ref TEXT,
            session_id TEXT,
            pid INTEGER,
            role TEXT NOT NULL,
            group_label TEXT,
            created_at REAL NOT NULL,
            archived_at REAL
        );

        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            repo TEXT NOT NULL,
            issue INTEGER NOT NULL,
            group_label TEXT,
            risk TEXT NOT NULL,
            hotset_json TEXT NOT NULL DEFAULT '[]',
            deps_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending',
            created_by TEXT NOT NULL,
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_repo_issue ON tasks (repo, issue);
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status);

        CREATE TABLE IF NOT EXISTS dispatches (
            dispatch_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            worktree TEXT,
            branch TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            terminal_evidence_json TEXT,
            dispatched_by TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks (task_id)
        );

        CREATE INDEX IF NOT EXISTS idx_dispatches_task ON dispatches (task_id);
        CREATE INDEX IF NOT EXISTS idx_dispatches_agent ON dispatches (agent_id);

        CREATE TABLE IF NOT EXISTS messages (
            msg_id TEXT PRIMARY KEY,
            signal_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            from_agent TEXT NOT NULL,
            to_agent TEXT NOT NULL,
            type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            acked_at REAL,
            acked_by TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_messages_to ON messages (to_agent, acked_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_signal ON messages (signal_id);

        CREATE TABLE IF NOT EXISTS review_rounds (
            round_id TEXT PRIMARY KEY,
            dispatch_id TEXT NOT NULL,
            round INTEGER NOT NULL,
            candidate_sha TEXT NOT NULL,
            base_sha TEXT NOT NULL,
            diff_digest TEXT NOT NULL,
            acceptance_digest TEXT NOT NULL,
            scope TEXT NOT NULL,
            prior_round_id TEXT,
            issued_by TEXT NOT NULL,
            issued_at REAL NOT NULL,
            FOREIGN KEY (dispatch_id) REFERENCES dispatches (dispatch_id)
        );

        CREATE TABLE IF NOT EXISTS review_results (
            round_id TEXT NOT NULL,
            axis TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            verdict TEXT NOT NULL,
            findings_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            PRIMARY KEY (round_id, axis),
            FOREIGN KEY (round_id) REFERENCES review_rounds (round_id)
        );

        CREATE TABLE IF NOT EXISTS leases (
            lease_id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            holder_agent TEXT,
            acquired_at REAL,
            released_at REAL
        );
        """,
    ),
)


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:24]}"


def _repo_slug(repo: str) -> str:
    return repo.replace("/", "-")


def _resolve_caller(caller_agent_id: str | None) -> str:
    if caller_agent_id is None:
        caller_agent_id = os.environ.get("GWO_AGENT_ID", "")
    if not caller_agent_id or not caller_agent_id.strip():
        raise IdentityError("GWO_AGENT_ID is required for every write")
    return caller_agent_id.strip()


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise IdentityError("expected a list, got " + type(value).__name__)


class Store:
    """A SQLite coordination cache under GWO_HOME."""

    def __init__(
        self,
        db: sqlite3.Connection,
        home: str | os.PathLike[str],
        repo: str,
        caller_agent_id: str,
    ) -> None:
        self.db = db
        self.home = os.fspath(home)
        self.repo = repo
        self.caller_agent_id = caller_agent_id

    @classmethod
    def connect(
        cls,
        home: str | os.PathLike[str],
        repo: str,
        *,
        caller_agent_id: str | None = None,
    ) -> "Store":
        caller = _resolve_caller(caller_agent_id)
        home_path = os.fspath(home)
        repo_dir = os.path.join(home_path, _repo_slug(repo))
        os.makedirs(repo_dir, exist_ok=True)
        db_path = os.path.join(repo_dir, "state.db")
        connection = sqlite3.connect(db_path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            store = cls(connection, home_path, repo, caller)
            store.run_migrations()
        except Exception:
            connection.close()
            raise
        return store

    def close(self) -> None:
        self.db.close()

    def journal_mode(self) -> str:
        row = self.db.execute("PRAGMA journal_mode").fetchone()
        return str(row[0]).lower()

    def table_names(self) -> list[str]:
        rows = self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [str(row[0]) for row in rows if not str(row[0]).startswith("sqlite_")]

    def run_migrations(self) -> None:
        with self.db:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)"
            )
            applied = {
                str(row[0])
                for row in self.db.execute(
                    "SELECT name FROM schema_migrations"
                ).fetchall()
            }
            for name, ddl in MIGRATIONS:
                if name in applied:
                    continue
                self.db.executescript(ddl)
                self.db.execute(
                    "INSERT INTO schema_migrations (name) VALUES (?)", (name,)
                )

    def _identity(self, supplied: Any, field: str) -> str:
        if supplied is not None:
            raise IdentityError(
                f"{field} is derived from GWO_AGENT_ID and cannot be caller-supplied"
            )
        return _resolve_caller(None)

    def claim_coordinator(self) -> None:
        with self.db:
            row = self.db.execute(
                "SELECT agent_id, released_at FROM coordinator WHERE repo = ?",
                (self.repo,),
            ).fetchone()
            now = _now()
            if row is None:
                self.db.execute(
                    "INSERT INTO coordinator (repo, agent_id, claimed_at, released_at) "
                    "VALUES (?, ?, ?, NULL)",
                    (self.repo, self.caller_agent_id, now),
                )
                return
            if row["released_at"] is None:
                raise CoordinatorBusy(
                    f"coordinator already claimed by {row['agent_id']}"
                )
            self.db.execute(
                "UPDATE coordinator SET agent_id = ?, claimed_at = ?, released_at = NULL "
                "WHERE repo = ?",
                (self.caller_agent_id, now, self.repo),
            )

    def coordinator_holder(self) -> str | None:
        row = self.db.execute(
            "SELECT agent_id FROM coordinator WHERE repo = ? AND released_at IS NULL",
            (self.repo,),
        ).fetchone()
        return str(row["agent_id"]) if row is not None else None

    def release_coordinator(self) -> None:
        with self.db:
            row = self.db.execute(
                "SELECT agent_id, released_at FROM coordinator WHERE repo = ?",
                (self.repo,),
            ).fetchone()
            if row is None or row["released_at"] is not None:
                raise TransitionError("no active coordinator claim to release")
            if row["agent_id"] != self.caller_agent_id:
                raise TransitionError(
                    "only the claiming coordinator may release"
                )
            self.db.execute(
                "UPDATE coordinator SET released_at = ? WHERE repo = ?",
                (_now(), self.repo),
            )

    def create_task(
        self,
        *,
        issue: int,
        group_label: str,
        risk: str,
        hotset: list[str] | None = None,
        deps: list[str] | None = None,
        created_by: Any = None,
    ) -> dict[str, Any]:
        actor = self._identity(created_by, "created_by")
        task_id = _new_id("t")
        hotset_json = json.dumps(_ensure_list(hotset))
        deps_json = json.dumps(_ensure_list(deps))
        with self.db:
            self.db.execute(
                "INSERT INTO tasks (task_id, repo, issue, group_label, risk, "
                "hotset_json, deps_json, status, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (
                    task_id,
                    self.repo,
                    issue,
                    group_label,
                    risk,
                    hotset_json,
                    deps_json,
                    actor,
                    _now(),
                ),
            )
        return self._task_row(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM tasks WHERE repo = ? ORDER BY created_at", (self.repo,)
        ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def update_task(
        self,
        *,
        task_id: str,
        status: str | None = None,
        hotset: list[str] | None = None,
        deps: list[str] | None = None,
    ) -> dict[str, Any]:
        with self.db:
            row = self.db.execute(
                "SELECT status FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise TransitionError(f"unknown task {task_id}")
            if status is not None and status != row["status"]:
                allowed = TASK_TRANSITIONS.get(str(row["status"]), ())
                if status not in allowed:
                    raise TransitionError(
                        f"invalid task transition {row['status']} -> {status}"
                    )
                self.db.execute(
                    "UPDATE tasks SET status = ? WHERE task_id = ?",
                    (status, task_id),
                )
            if hotset is not None:
                self.db.execute(
                    "UPDATE tasks SET hotset_json = ? WHERE task_id = ?",
                    (json.dumps(_ensure_list(hotset)), task_id),
                )
            if deps is not None:
                self.db.execute(
                    "UPDATE tasks SET deps_json = ? WHERE task_id = ?",
                    (json.dumps(_ensure_list(deps)), task_id),
                )
        return self._task_row(task_id)

    def _task_row(self, task_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise TransitionError(f"unknown task {task_id}")
        return self._task_from_row(row)

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "repo": row["repo"],
            "issue": row["issue"],
            "group_label": row["group_label"],
            "risk": row["risk"],
            "hotset_json": json.loads(row["hotset_json"]),
            "deps_json": json.loads(row["deps_json"]),
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
        }

    def create_dispatch(
        self,
        *,
        task_id: str,
        agent_id: str,
        worktree: str,
        branch: str,
        attempt: int | None = None,
        dispatched_by: Any = None,
    ) -> dict[str, Any]:
        actor = self._identity(dispatched_by, "dispatched_by")
        dispatch_id = _new_id("d")
        with self.db:
            row = self.db.execute(
                "SELECT status FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise TransitionError(f"unknown task {task_id}")
            if row["status"] != "ready":
                raise TransitionError(
                    f"cannot dispatch task in status {row['status']}"
                )
            if attempt is None:
                count = self.db.execute(
                    "SELECT COUNT(*) FROM dispatches WHERE task_id = ?", (task_id,)
                ).fetchone()[0]
                attempt = int(count) + 1
            self.db.execute(
                "INSERT INTO dispatches (dispatch_id, task_id, agent_id, attempt, "
                "worktree, branch, status, dispatched_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (
                    dispatch_id,
                    task_id,
                    agent_id,
                    attempt,
                    worktree,
                    branch,
                    actor,
                    _now(),
                ),
            )
            self.db.execute(
                "UPDATE tasks SET status = 'dispatched' WHERE task_id = ?",
                (task_id,),
            )
        return self._dispatch_row(dispatch_id)

    def _dispatch_row(self, dispatch_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM dispatches WHERE dispatch_id = ?", (dispatch_id,)
        ).fetchone()
        if row is None:
            raise TransitionError(f"unknown dispatch {dispatch_id}")
        return self._dispatch_from_row(row)

    @staticmethod
    def _dispatch_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "dispatch_id": row["dispatch_id"],
            "task_id": row["task_id"],
            "agent_id": row["agent_id"],
            "attempt": row["attempt"],
            "worktree": row["worktree"],
            "branch": row["branch"],
            "status": row["status"],
            "terminal_evidence_json": row["terminal_evidence_json"],
            "dispatched_by": row["dispatched_by"],
            "created_at": row["created_at"],
        }

    def mark_done(
        self,
        *,
        task_id: str,
        dispatch_id: str,
        status: str,
        actor: Any = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved = self._identity(actor, "actor")
        if status not in DONE_STATUSES:
            raise TransitionError(f"invalid done status {status}")
        evidence_json = json.dumps(evidence or {})
        with self.db:
            dispatch = self.db.execute(
                "SELECT agent_id, status FROM dispatches WHERE dispatch_id = ?",
                (dispatch_id,),
            ).fetchone()
            if dispatch is None:
                raise TransitionError(f"unknown dispatch {dispatch_id}")
            if dispatch["agent_id"] != resolved:
                raise IdentityError(
                    "only the dispatched agent may mark done for its dispatch"
                )
            if dispatch["status"] != "active":
                raise TransitionError(
                    f"dispatch is {dispatch['status']}, not active"
                )
            task_row = self.db.execute(
                "SELECT status FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if task_row is None:
                raise TransitionError(f"unknown task {task_id}")
            self.db.execute(
                "UPDATE dispatches SET status = ?, terminal_evidence_json = ? "
                "WHERE dispatch_id = ?",
                (status, evidence_json, dispatch_id),
            )
            self.db.execute(
                "UPDATE tasks SET status = ? WHERE task_id = ?",
                (status, task_id),
            )
        return self._dispatch_row(dispatch_id)