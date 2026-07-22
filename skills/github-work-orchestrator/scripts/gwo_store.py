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

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from typing import Any


SCHEMA_VERSION = 1
SCHEMA_NAME = "gwo-store-v1"

REPOSITORY_RE = re.compile(r"^[^/\s\\]+/[^/\s\\]+$")

TASK_STATUSES = ("pending", "ready", "dispatched", "done", "failed", "blocked")
DISPATCH_STATUSES = ("active", "done", "blocked", "stopped")
DONE_STATUSES = ("done", "blocked", "stopped")

TASK_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "pending": ("ready",),
    "ready": ("dispatched", "pending"),
    "dispatched": (),
    "blocked": ("ready", "failed"),
    "failed": ("ready", "pending"),
    "done": (),
}

# Terminal task statuses reachable from ``dispatched`` through ``mark_done``.
# ``update_task`` may not move a dispatched task to any of these because that
# would bypass the dispatched-agent check in ``mark_done`` and could leave a
# terminal task with an active dispatch. Only ``mark_done`` (which updates the
# linked dispatch in the same transaction) may move ``dispatched`` to a
# terminal status.
DISPATCHED_TERMINAL_STATUSES = ("done", "failed", "blocked")

DISPATCH_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "active": ("done", "blocked", "stopped"),
    "done": (),
    "blocked": ("active", "stopped"),
    "stopped": (),
}

# When a dispatch terminates with one of these statuses, the task transitions
# to the mapped task status. ``stopped`` is a dispatch-only terminal state and
# must never be written to tasks.status (it is absent from TASK_STATUSES).
DONE_TO_TASK_STATUS: dict[str, str] = {
    "done": "done",
    "blocked": "blocked",
    "stopped": "failed",
}


class StoreError(RuntimeError):
    """Base class for gwo store errors."""


class IdentityError(StoreError):
    """Raised when caller identity is missing, ambiguous, or caller-supplied."""


class TransitionError(StoreError):
    """Raised when a requested lifecycle transition is invalid."""


class CoordinatorBusy(StoreError):
    """Raised when a second Coordinator claim is attempted."""


class LeaseBusy(StoreError):
    """Raised when a second holder attempts to acquire an active lease."""


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
        -- At most one active dispatch per task. Defense-in-depth against the
        -- dispatch race: even if two writers race past validation, this
        -- partial unique index rejects the second active insert atomically.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_dispatches_one_active
            ON dispatches (task_id) WHERE status = 'active';

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
        """,
    ),
    (
        "0002-messages-in-reply-to",
        """
        ALTER TABLE messages ADD COLUMN in_reply_to TEXT;
        CREATE INDEX IF NOT EXISTS idx_messages_from_seq
            ON messages (from_agent, seq);
        """,
    ),
    (
        "0003-tasks-repo-issue-unique",
        """
        CREATE INDEX IF NOT EXISTS idx_tasks_repo_issue ON tasks (repo, issue);
        """,
    ),
    (
        "0004-review-rounds-and-lease",
        """
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

        CREATE UNIQUE INDEX IF NOT EXISTS idx_leases_active_scope
            ON leases (scope) WHERE released_at IS NULL;

        CREATE TABLE IF NOT EXISTS integration_chain (
            chain_id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            candidate_sha TEXT NOT NULL,
            task_id TEXT NOT NULL,
            prior_chain_id TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY (prior_chain_id) REFERENCES integration_chain (chain_id)
        );

        CREATE INDEX IF NOT EXISTS idx_integration_chain_scope
            ON integration_chain (scope, created_at);
        """,
    ),
    (
        "0005-review-authority-and-chain-integrity",
        """
        -- Review authority: axis assignments are authored by the Coordinator.
        CREATE TABLE IF NOT EXISTS review_assignments (
            round_id TEXT NOT NULL,
            axis TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            assigned_at REAL NOT NULL,
            PRIMARY KEY (round_id, axis),
            FOREIGN KEY (round_id) REFERENCES review_rounds (round_id)
                ON DELETE CASCADE,
            FOREIGN KEY (agent_id) REFERENCES agents (agent_id)
        );

        -- Review rounds gain a current-tail flag and an assigned_axis metadata
        -- column so the round issuer can record the intended axis. The
        -- is_current flag is maintained by triggers during round creation and
        -- supersession so gate queries always look at the single tail.
        ALTER TABLE review_rounds ADD COLUMN is_current INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE review_rounds ADD COLUMN assigned_axis TEXT;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_review_rounds_dispatch_round
            ON review_rounds (dispatch_id, round);

        -- At most one current round per dispatch.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_review_rounds_current_dispatch
            ON review_rounds (dispatch_id) WHERE is_current = 1;

        -- Integration chain gains a transaction-local monotonic position and a
        -- head pointer. created_at remains metadata only.
        ALTER TABLE integration_chain ADD COLUMN position INTEGER;
        ALTER TABLE integration_chain ADD COLUMN head TEXT;

        -- One root per scope and one successor per prior chain node.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_integration_chain_scope_root
            ON integration_chain (scope) WHERE prior_chain_id IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_integration_chain_prior_unique
            ON integration_chain (prior_chain_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_integration_chain_scope_position
            ON integration_chain (scope, position);

        -- Trigger: when a new round is inserted, supersede any prior current
        -- round for the same dispatch and clear its is_current flag. This
        -- keeps the current tail unique per dispatch and links the delta chain.
        CREATE TRIGGER IF NOT EXISTS trg_supersede_review_round
        AFTER INSERT ON review_rounds
        FOR EACH ROW
        WHEN NEW.prior_round_id IS NOT NULL
        BEGIN
            UPDATE review_rounds
            SET is_current = 0
            WHERE dispatch_id = NEW.dispatch_id
              AND is_current = 1
              AND round_id != NEW.round_id;
        END;

        -- Trigger: maintain the integration chain head pointer on insert.
        -- The head is always the latest node for the scope.
        CREATE TRIGGER IF NOT EXISTS trg_integration_chain_head
        AFTER INSERT ON integration_chain
        FOR EACH ROW
        BEGIN
            UPDATE integration_chain
            SET head = NEW.chain_id
            WHERE scope = NEW.scope;
        END;
        """,
    ),
)


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:24]}"


def _validate_repository(repo: str) -> str:
    """Validate the repository string is owner/repo with no path traversal.

    Reject Windows backslash separators and ``..`` segments so a crafted
    repository cannot escape GWO_HOME. The owner/repo parts must not contain
    path separators, traversal segments, or control characters.
    """
    if not isinstance(repo, str):
        raise IdentityError("repository must be a string")
    candidate = repo.strip()
    if not REPOSITORY_RE.fullmatch(candidate):
        raise IdentityError("repository must be owner/repo with no separators")
    if "\\" in candidate or ".." in candidate.split("/") or any(
        part in ("", ".", "..") for part in candidate.split("/")
    ):
        raise IdentityError("repository contains forbidden path segment")
    return candidate


def _repo_slug(repo: str) -> str:
    """Return a collision-safe, path-safe slug for one repository.

    The slug encodes the validated owner/repo as a hex digest so that no
    caller-supplied character can ever reach the filesystem as a path
    component. This is defense-in-depth on top of ``_validate_repository``.
    """
    validated = _validate_repository(repo)
    digest = hashlib.sha256(validated.lower().encode("utf-8")).hexdigest()[:24]
    safe = re.sub(r"[^a-z0-9-]+", "-", validated.lower()).strip("-")
    return f"{safe[:48]}-{digest}"


def _repo_path(home: str, repo: str) -> str:
    """Resolve the repo directory and enforce it stays inside GWO_HOME."""
    slug = _repo_slug(repo)
    home_resolved = os.path.realpath(home)
    repo_dir = os.path.realpath(os.path.join(home_resolved, slug))
    if not (repo_dir == home_resolved or repo_dir.startswith(
        home_resolved + os.sep
    )):
        raise IdentityError("repository path escapes GWO_HOME")
    return repo_dir


def _resolve_caller() -> str:
    """Resolve caller identity from GWO_AGENT_ID on every write.

    The spawn-injected ``GWO_AGENT_ID`` environment variable is the sole source
    of caller authority. There is no override parameter and no cached identity:
    a write whose environment no longer carries a valid identity fails, even
    if ``connect()`` was called with an override. Identity columns are derived
    inside the store boundary and callers can never supply them.
    """
    caller = os.environ.get("GWO_AGENT_ID", "")
    if not caller or not caller.strip():
        raise IdentityError("GWO_AGENT_ID is required for every write")
    return caller.strip()


def _resolve_connect_identity(caller_agent_id: str | None) -> str:
    """Resolve identity at connect time from GWO_AGENT_ID.

    ``caller_agent_id`` is accepted only as a connection-time convenience and
    is NEVER stored or used to authorize writes. It cannot forge authority:
    identity always comes from the live ``GWO_AGENT_ID`` environment variable,
    and every write re-resolves from that source. The override is ignored so a
    caller-supplied value can never broaden authority beyond the environment.
    """
    return _resolve_caller()


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise IdentityError("expected a list, got " + type(value).__name__)


def _split_sql_statements(ddl: str) -> list[str]:
    """Split a DDL string into individual SQL statements.

    Uses ``sqlite3.complete_statement`` to find statement boundaries so
    semicolons inside string literals, comments, and trigger bodies do not
    falsely terminate a statement. Returns only non-empty stripped statements.
    """
    statements: list[str] = []
    buffer = ""
    remaining = ddl
    while remaining:
        buffer += remaining[:1]
        remaining = remaining[1:]
        if sqlite3.complete_statement(buffer):
            stripped = buffer.strip()
            if stripped:
                statements.append(stripped)
            buffer = ""
    stripped = buffer.strip()
    if stripped:
        statements.append(stripped)
    return statements


class Store:
    """A SQLite coordination cache under GWO_HOME."""

    def __init__(
        self,
        db: sqlite3.Connection,
        home: str | os.PathLike[str],
        repo: str,
    ) -> None:
        self.db = db
        self.home = os.fspath(home)
        self.repo = _validate_repository(repo)

    @classmethod
    def connect(
        cls,
        home: str | os.PathLike[str],
        repo: str,
        *,
        caller_agent_id: str | None = None,
    ) -> "Store":
        # Identity is resolved from GWO_AGENT_ID at connect time. The
        # caller_agent_id override is ignored and never stored; every write
        # re-resolves from GWO_AGENT_ID so a cached identity can never forge
        # authority.
        _resolve_connect_identity(caller_agent_id)
        home_path = os.fspath(home)
        repo_dir = _repo_path(home_path, repo)
        os.makedirs(repo_dir, exist_ok=True)
        db_path = os.path.join(repo_dir, "state.db")
        connection = sqlite3.connect(db_path)
        try:
            connection.row_factory = sqlite3.Row
            connection.isolation_level = None
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            store = cls(connection, home_path, repo)
            store.run_migrations()
        except Exception:
            connection.close()
            raise
        return store

    def _caller(self) -> str:
        """Resolve caller identity from GWO_AGENT_ID for this write."""
        return _resolve_caller()

    def _reject_supplied_identity(self, supplied: Any, field: str) -> None:
        if supplied is not None:
            raise IdentityError(
                f"{field} is derived from GWO_AGENT_ID and cannot be caller-supplied"
            )

    def close(self) -> None:
        # Clear any injected readback snapshot for this Store's connection
        # so a recycled connection ID cannot return stale runtime evidence
        # from a prior Store.
        try:
            import gwo_status
            gwo_status._READBACK_SNAPSHOTS.pop(id(self.db), None)
        except Exception:
            pass
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
        caller = self._caller()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)"
            )
            applied = {
                str(row[0])
                for row in self.db.execute(
                    "SELECT name FROM schema_migrations"
                ).fetchall()
            }
            self.db.execute("COMMIT")
        except BaseException:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        for name, ddl in MIGRATIONS:
            if name in applied:
                continue
            applied_now = self._apply_migration(name, ddl, caller)
            # If the migration was already applied by a concurrent writer,
            # _apply_migration commits a no-op; do not run post-migration hooks.
            if not applied_now:
                continue
            # Post-migration 0003: attempt the unique index. If duplicate tasks
            # exist (legacy Issue #21 stores), skip the unique index so
            # Store.connect does not crash; doctor_rebuild surfaces the
            # ambiguity. If no duplicates, the unique index is created safely.
            if name == "0003-tasks-repo-issue-unique":
                self._try_unique_index_or_skip()
            # Post-migration 0004: attempt the review/lease partial unique
            # index. If a legacy store already has a conflicting lease row,
            # skip the index so Store.connect does not crash; doctor_rebuild
            # surfaces the ambiguity.
            if name == "0004-review-rounds-and-lease":
                self._try_lease_unique_index_or_skip()

    def _try_unique_index_or_skip(self) -> None:
        """Attempt to create the unique index on tasks(repo, issue).

        If duplicate tasks exist (legacy Issue #21 stores), the unique index
        creation fails and we skip it so Store.connect does not crash. The
        non-unique index from migration 0003 remains, and doctor_rebuild can
        surface the legacy ambiguity. If no duplicates exist, the unique index
        is created safely.
        """
        try:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_repo_issue_unique "
                    "ON tasks (repo, issue)"
                )
                self.db.execute("COMMIT")
            except sqlite3.IntegrityError:
                self.db.execute("ROLLBACK")
        except sqlite3.OperationalError:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass

    def _try_lease_unique_index_or_skip(self) -> None:
        """Attempt to create the partial unique index on active leases.

        If a legacy store contains conflicting active lease rows, skip the
        index so Store.connect does not crash; doctor_rebuild can surface the
        ambiguity.
        """
        try:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_leases_active_scope "
                    "ON leases (scope) WHERE released_at IS NULL"
                )
                self.db.execute("COMMIT")
            except sqlite3.IntegrityError:
                self.db.execute("ROLLBACK")
        except sqlite3.OperationalError:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass

    def _apply_migration(self, name: str, ddl: str, caller: str) -> bool:
        # Execute each migration and its schema_migrations record in one
        # transaction so a failure in any DDL statement rolls back the whole
        # migration (no DDL left without a record). Re-check the migration row
        # under the same BEGIN IMMEDIATE so two concurrent writers cannot both
        # apply the same migration: the loser sees the row inserted by the
        # winner and skips cleanly instead of raising a uniqueness error.
        #
        # Returns True when this call actually applied the migration, False
        # when it was already present (concurrent application or idempotent
        # re-run). The post-hook control flow depends on this explicit result.
        statements = _split_sql_statements(ddl)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute(
                "SELECT name FROM schema_migrations WHERE name = ?", (name,)
            ).fetchone()
            if existing is not None:
                self.db.execute("ROLLBACK")
                return False
            if name == "0005-review-authority-and-chain-integrity":
                self._validate_0004_schema()
            for statement in statements:
                self.db.execute(statement)
            if name == "0005-review-authority-and-chain-integrity":
                self._validate_0005_schema()
            self.db.execute(
                "INSERT INTO schema_migrations (name) VALUES (?)", (name,)
            )
            self.db.execute("COMMIT")
            return True
        except BaseException:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise

    def _require_table_columns(
        self, table: str, columns: tuple[str, ...]
    ) -> None:
        actual = tuple(
            str(row["name"])
            for row in self.db.execute(f"PRAGMA table_info({table})").fetchall()
        )
        if actual != columns:
            raise StoreError(
                f"migration 0005 requires exact {table} columns {columns}, got {actual}"
            )

    def _require_foreign_keys(
        self, table: str, expected: set[tuple[str, str, str]]
    ) -> None:
        actual = {
            (str(row["from"]), str(row["table"]), str(row["to"]))
            for row in self.db.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        }
        if actual != expected:
            raise StoreError(
                f"migration 0005 requires exact {table} foreign keys"
            )

    def _require_schema_objects(self, names: set[str]) -> None:
        actual = {
            str(row["name"])
            for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('index', 'trigger')"
            ).fetchall()
        }
        missing = names - actual
        if missing:
            raise StoreError(
                f"migration 0005 missing required schema objects {sorted(missing)}"
            )

    def _validate_0004_schema(self) -> None:
        """Reject a legacy or malformed Phase 2 schema before 0005 DDL runs."""
        self._require_table_columns(
            "review_rounds",
            (
                "round_id", "dispatch_id", "round", "candidate_sha", "base_sha",
                "diff_digest", "acceptance_digest", "scope", "prior_round_id",
                "issued_by", "issued_at",
            ),
        )
        self._require_table_columns(
            "review_results",
            ("round_id", "axis", "agent_id", "verdict", "findings_json", "created_at"),
        )
        self._require_table_columns(
            "leases",
            ("lease_id", "scope", "holder_agent", "acquired_at", "released_at"),
        )
        self._require_table_columns(
            "integration_chain",
            ("chain_id", "scope", "candidate_sha", "task_id", "prior_chain_id", "created_at"),
        )
        self._require_foreign_keys(
            "review_rounds", {("dispatch_id", "dispatches", "dispatch_id")}
        )
        self._require_foreign_keys(
            "review_results", {("round_id", "review_rounds", "round_id")}
        )
        self._require_foreign_keys(
            "integration_chain", {("prior_chain_id", "integration_chain", "chain_id")}
        )
        self._require_schema_objects(
            {"idx_leases_active_scope", "idx_integration_chain_scope"}
        )

    def _validate_0005_schema(self) -> None:
        """Require the complete 0005 authority and chain-integrity shape."""
        self._require_table_columns(
            "review_rounds",
            (
                "round_id", "dispatch_id", "round", "candidate_sha", "base_sha",
                "diff_digest", "acceptance_digest", "scope", "prior_round_id",
                "issued_by", "issued_at", "is_current", "assigned_axis",
            ),
        )
        self._require_table_columns(
            "review_assignments", ("round_id", "axis", "agent_id", "assigned_at")
        )
        self._require_table_columns(
            "integration_chain",
            (
                "chain_id", "scope", "candidate_sha", "task_id", "prior_chain_id",
                "created_at", "position", "head",
            ),
        )
        self._require_foreign_keys(
            "review_assignments",
            {
                ("round_id", "review_rounds", "round_id"),
                ("agent_id", "agents", "agent_id"),
            },
        )
        self._require_schema_objects(
            {
                "idx_review_rounds_dispatch_round",
                "idx_review_rounds_current_dispatch",
                "idx_integration_chain_scope_root",
                "idx_integration_chain_prior_unique",
                "idx_integration_chain_scope_position",
                "trg_supersede_review_round",
                "trg_integration_chain_head",
            }
        )

    def _identity(self, supplied: Any, field: str) -> str:
        self._reject_supplied_identity(supplied, field)
        return self._caller()

    def _require_coordinator_claim(self, caller: str) -> None:
        """Require the caller to hold the active repository coordinator claim.

        Must be called inside the same BEGIN IMMEDIATE write transaction that
        performs the Coordinator-owned mutation so authorization and the state
        change are atomic. A foreign worker or an unclaimed repo cannot create
        or mutate tasks or dispatch.
        """
        row = self.db.execute(
            "SELECT agent_id FROM coordinator WHERE repo = ? AND released_at IS NULL",
            (self.repo,),
        ).fetchone()
        if row is None:
            raise IdentityError(
                f"coordinator claim required for repo {self.repo}"
            )
        if str(row["agent_id"]) != caller:
            raise IdentityError(
                "only the active coordinator claim holder may perform this operation"
            )

    def claim_coordinator(self) -> None:
        caller = self._caller()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT agent_id, released_at FROM coordinator WHERE repo = ?",
                (self.repo,),
            ).fetchone()
            now = _now()
            if row is None:
                self.db.execute(
                    "INSERT INTO coordinator (repo, agent_id, claimed_at, released_at) "
                    "VALUES (?, ?, ?, NULL)",
                    (self.repo, caller, now),
                )
                self.db.execute("COMMIT")
                return
            if row["released_at"] is None:
                raise CoordinatorBusy(
                    f"coordinator already claimed by {row['agent_id']}"
                )
            self.db.execute(
                "UPDATE coordinator SET agent_id = ?, claimed_at = ?, released_at = NULL "
                "WHERE repo = ?",
                (caller, now, self.repo),
            )
            self.db.execute("COMMIT")
        except BaseException:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise

    def coordinator_holder(self) -> str | None:
        row = self.db.execute(
            "SELECT agent_id FROM coordinator WHERE repo = ? AND released_at IS NULL",
            (self.repo,),
        ).fetchone()
        return str(row["agent_id"]) if row is not None else None

    def release_coordinator(self) -> None:
        caller = self._caller()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT agent_id, released_at FROM coordinator WHERE repo = ?",
                (self.repo,),
            ).fetchone()
            if row is None or row["released_at"] is not None:
                raise TransitionError("no active coordinator claim to release")
            if row["agent_id"] != caller:
                raise TransitionError(
                    "only the claiming coordinator may release"
                )
            self.db.execute(
                "UPDATE coordinator SET released_at = ? WHERE repo = ?",
                (_now(), self.repo),
            )
            self.db.execute("COMMIT")
        except BaseException:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise

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
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._require_coordinator_claim(actor)
            # Logical uniqueness guard: even when the unique index could not be
            # installed (legacy stores with duplicate tasks), enforce that no
            # task for this repo+issue exists before inserting. This catches
            # both the index-backed case and the legacy case.
            count_row = self.db.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE repo = ? AND issue = ?",
                (self.repo, issue),
            ).fetchone()
            if int(count_row["n"]) > 0:
                raise TransitionError(
                    f"task for issue {issue} already exists in repo {self.repo}"
                )
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
            self.db.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise TransitionError(
                f"task for issue {issue} already exists in repo {self.repo}"
            ) from error
        except BaseException:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
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
        caller = self._caller()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._require_coordinator_claim(caller)
            row = self.db.execute(
                "SELECT status FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise TransitionError(f"unknown task {task_id}")
            if status is not None and status != row["status"]:
                if status not in TASK_STATUSES:
                    raise TransitionError(f"invalid task status {status}")
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
            self.db.execute("COMMIT")
        except BaseException:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
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
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._require_coordinator_claim(actor)
            # Config preflight gate: a failed config check blocks new
            # dispatches but never abandons existing work. The check is
            # non-destructive (reads config.json + migration set).
            import gwo_status
            preflight = gwo_status.config_check(self)
            if not preflight["valid"]:
                raise TransitionError(
                    "config check failed; dispatch blocked: "
                    + "; ".join(preflight["errors"])
                )
            # Conditional DML: atomically flip the task from ready to
            # dispatched. If another writer already won the race, rowcount is
            # 0 and we reject without inserting. This makes validation and the
            # state transition a single atomic step relative to other writers.
            cursor = self.db.execute(
                "UPDATE tasks SET status = 'dispatched' "
                "WHERE task_id = ? AND status = 'ready'",
                (task_id,),
            )
            if cursor.rowcount == 0:
                row = self.db.execute(
                    "SELECT status FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    raise TransitionError(f"unknown task {task_id}")
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
            self.db.execute("COMMIT")
        except BaseException:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
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
        task_status = DONE_TO_TASK_STATUS[status]
        evidence_json = json.dumps(evidence or {})
        self.db.execute("BEGIN IMMEDIATE")
        try:
            dispatch = self.db.execute(
                "SELECT task_id, agent_id, status FROM dispatches WHERE dispatch_id = ?",
                (dispatch_id,),
            ).fetchone()
            if dispatch is None:
                raise TransitionError(f"unknown dispatch {dispatch_id}")
            # The dispatch must belong to the supplied task; a caller cannot
            # close one dispatch while marking an unrelated task done.
            if dispatch["task_id"] != task_id:
                raise TransitionError(
                    f"dispatch {dispatch_id} does not belong to task {task_id}"
                )
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
            current = str(task_row["status"])
            # Only a dispatched task may be moved to a terminal status through
            # mark_done. update_task cannot reach these because
            # TASK_TRANSITIONS["dispatched"] is empty; mark_done owns the
            # dispatched -> terminal transition and updates the linked dispatch
            # in this same transaction.
            if current != "dispatched":
                raise TransitionError(
                    f"task {task_id} is {current}, not dispatched"
                )
            if task_status not in DISPATCHED_TERMINAL_STATUSES:
                raise TransitionError(
                    f"mark_done cannot transition dispatched -> {task_status}"
                )
            self.db.execute(
                "UPDATE dispatches SET status = ?, terminal_evidence_json = ? "
                "WHERE dispatch_id = ?",
                (status, evidence_json, dispatch_id),
            )
            self.db.execute(
                "UPDATE tasks SET status = ? WHERE task_id = ?",
                (task_status, task_id),
            )
            self.db.execute("COMMIT")
        except BaseException:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        return self._dispatch_row(dispatch_id)

    # --- Phase 1 mailbox / event delivery (gwo_mailbox delegation) ---------

    def register_agent(
        self,
        *,
        agent_id: str,
        adapter: str,
        runtime_ref: str | None,
        role: str,
        group_label: str | None = None,
        session_id: str | None = None,
        pid: int | None = None,
    ) -> dict[str, Any]:
        """Register or refresh an agent row. Coordinator-only."""
        import gwo_mailbox  # local import to avoid circular at module load
        return gwo_mailbox.register_agent(
            self,
            agent_id=agent_id,
            adapter=adapter,
            runtime_ref=runtime_ref,
            role=role,
            group_label=group_label,
            session_id=session_id,
            pid=pid,
        )

    def send(
        self,
        *,
        to_agent: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        signal_id: str,
        in_reply_to: str | None = None,
    ) -> dict[str, Any]:
        """Post one mailbox event with entitlement and idempotency checks."""
        import gwo_mailbox
        return gwo_mailbox.send(
            self,
            to_agent=to_agent,
            event_type=event_type,
            payload=payload,
            signal_id=signal_id,
            in_reply_to=in_reply_to,
        )

    def ask(
        self,
        *,
        to_agent: str,
        payload: dict[str, Any] | None = None,
        signal_id: str,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Send an ask event and block for the correlated reply."""
        import gwo_mailbox
        return gwo_mailbox.ask(
            self,
            to_agent=to_agent,
            payload=payload,
            signal_id=signal_id,
            timeout=timeout,
        )

    def inbox(
        self,
        *,
        agent_id: str,
        ack_on_read: bool = False,
        dispatch_id: str | None = None,
        wait: float | None = None,
    ) -> list[dict[str, Any]]:
        """Read (and optionally acknowledge) messages addressed to agent_id."""
        import gwo_mailbox
        return gwo_mailbox.inbox(
            self,
            agent_id=agent_id,
            ack_on_read=ack_on_read,
            dispatch_id=dispatch_id,
            wait=wait,
        )

    def agent_status(
        self, agent_id: str, *, readback_snapshot_path: str | None = None
    ) -> dict[str, Any]:
        """Return the runtime status of one agent."""
        import gwo_mailbox
        return gwo_mailbox.agent_status(
            self, agent_id, readback_snapshot_path=readback_snapshot_path
        )

    def config_check(self, *, gwo_home: str | None = None) -> dict[str, Any]:
        """Validate the GWO configuration."""
        import gwo_mailbox
        return gwo_mailbox.config_check(self, gwo_home=gwo_home)

    def doctor_rebuild(
        self,
        *,
        github_snapshot: dict[str, Any],
        adapter_listing: list[dict[str, Any]],
        git_worktrees: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Rebuild the store from GitHub + adapter readback (additive, fail-closed)."""
        import gwo_mailbox
        return gwo_mailbox.doctor_rebuild(
            self,
            github_snapshot=github_snapshot,
            adapter_listing=adapter_listing,
            git_worktrees=git_worktrees,
        )

    # --- Phase 2 review rounds / Integration Lease ---------------------------

    def _require_registered_reviewer(self, agent_id: str) -> None:
        """Verify the caller is a registered, non-archived reviewer agent."""
        row = self.db.execute(
            "SELECT role, archived_at FROM agents WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        if row is None:
            raise IdentityError(f"agent {agent_id} is not registered")
        if str(row["role"]) != "reviewer":
            raise IdentityError(f"agent {agent_id} is not a reviewer")
        if row["archived_at"] is not None:
            raise IdentityError(f"agent {agent_id} is archived")

    def issue_review_round(
        self,
        *,
        dispatch_id: str,
        round: int,
        candidate_sha: str,
        base_sha: str,
        diff_digest: str,
        acceptance_digest: str,
        scope: str,
        prior_round_id: str | None = None,
        round_id: Any = None,
        issued_by: Any = None,
        assignments: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Issue a review-round identity row. Coordinator-only.

        The CLI is the sole authority that authors candidate/base SHA, diff and
        acceptance digests, scope, round number, round identity, and axis
        assignments. Reviewers can only reference an issued round via
        submit_review_result.
        """
        import gwo_review

        actor = self._identity(issued_by, "issued_by")
        self._reject_supplied_identity(round_id, "round_id")
        gwo_review.validate_scope(scope)
        gwo_review.validate_round(round)
        gwo_review.validate_sha("candidate_sha", candidate_sha)
        gwo_review.validate_sha("base_sha", base_sha)
        gwo_review.validate_sha256("diff_digest", diff_digest)
        gwo_review.validate_sha256("acceptance_digest", acceptance_digest)

        if scope == "full":
            if round != 1:
                raise TransitionError("full review must be round 1")
            if prior_round_id is not None:
                raise TransitionError("full review must not have a prior_round_id")
        else:
            if round < 2:
                raise TransitionError("delta review requires round >= 2")
            if prior_round_id is None:
                raise TransitionError("delta review requires prior_round_id")

        resolved_round_id = _new_id("rr")
        now = _now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._require_coordinator_claim(actor)
            dispatch = self.db.execute(
                "SELECT dispatch_id FROM dispatches WHERE dispatch_id = ?",
                (dispatch_id,),
            ).fetchone()
            if dispatch is None:
                raise TransitionError(f"unknown dispatch {dispatch_id}")

            # Ensure at most one current round per dispatch; a delta round must
            # name the current unsuperseded tail for the same dispatch and use
            # prior.round + 1.
            current = self.db.execute(
                "SELECT round_id, round, candidate_sha FROM review_rounds "
                "WHERE dispatch_id = ? AND is_current = 1",
                (dispatch_id,),
            ).fetchone()

            if prior_round_id is not None:
                prior = self.db.execute(
                    "SELECT dispatch_id, round_id, round, candidate_sha, is_current "
                    "FROM review_rounds WHERE round_id = ?",
                    (prior_round_id,),
                ).fetchone()
                if prior is None:
                    raise TransitionError(
                        f"prior_round_id {prior_round_id} does not exist"
                    )
                if str(prior["dispatch_id"]) != dispatch_id:
                    raise TransitionError(
                        "prior_round_id must belong to the same dispatch"
                    )
                if not int(prior["is_current"]):
                    raise TransitionError(
                        "delta prior_round_id must name the current unsuperseded tail"
                    )
                if current is None or current["round_id"] != prior_round_id:
                    raise TransitionError(
                        "delta prior_round_id must name the current unsuperseded tail"
                    )
                if prior["round"] + 1 != round:
                    raise TransitionError(
                        f"delta round must be {prior['round'] + 1}, got {round}"
                    )
                if prior["candidate_sha"] == candidate_sha:
                    raise TransitionError(
                        "delta candidate must differ from prior candidate"
                    )
            else:
                if current is not None:
                    raise TransitionError(
                        "round 1 cannot be issued when a current round exists"
                    )

            # Derive tier from the linked task and validate assignments.
            task_row = self.db.execute(
                "SELECT t.risk FROM tasks t JOIN dispatches d ON d.task_id = t.task_id "
                "WHERE d.dispatch_id = ?",
                (dispatch_id,),
            ).fetchone()
            if task_row is None:
                raise TransitionError(f"dispatch {dispatch_id} has no task")
            tier = str(task_row["risk"])
            if tier == "fast":
                raise TransitionError("fast issues use coordinator-inline integration, not review rounds")
            required_axes = gwo_review.tier_axes(tier)
            if assignments is None:
                assignments = {}
            provided_axes = set(assignments.keys())
            if required_axes and provided_axes != set(required_axes):
                raise TransitionError(
                    f"{tier} review requires axes {list(required_axes)}, got {list(provided_axes)}"
                )
            if tier == "strict" and len(set(assignments.values())) != 2:
                raise TransitionError(
                    "strict review requires spec and quality assignments to different reviewers"
                )

            # Supersede the prior round before inserting the new one so the
            # partial unique index idx_review_rounds_current_dispatch does not
            # reject the insert while the old tail still has is_current=1.
            if prior_round_id is not None:
                self.db.execute(
                    "UPDATE review_rounds SET is_current = 0 "
                    "WHERE dispatch_id = ? AND is_current = 1 AND round_id != ?",
                    (dispatch_id, resolved_round_id),
                )

            self.db.execute(
                "INSERT INTO review_rounds ("
                "round_id, dispatch_id, round, candidate_sha, base_sha, "
                "diff_digest, acceptance_digest, scope, prior_round_id, "
                "issued_by, issued_at, is_current, assigned_axis) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    resolved_round_id,
                    dispatch_id,
                    round,
                    candidate_sha,
                    base_sha,
                    diff_digest,
                    acceptance_digest,
                    scope,
                    prior_round_id,
                    actor,
                    now,
                    # assigned_axis metadata: store the first required axis for
                    # single-axis tiers; strict stores both via assignments.
                    required_axes[0] if len(required_axes) == 1 else None,
                ),
            )

            # Persist axis assignments atomically with the round.
            for axis, agent_id in assignments.items():
                self._require_registered_reviewer(agent_id)
                self.db.execute(
                    "INSERT INTO review_assignments (round_id, axis, agent_id, assigned_at) "
                    "VALUES (?, ?, ?, ?)",
                    (resolved_round_id, axis, agent_id, now),
                )

            self.db.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise TransitionError(
                f"review round {round} already issued for dispatch {dispatch_id}"
            ) from error
        except BaseException:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        return self._review_round_row(resolved_round_id)

    def _review_round_row(self, round_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM review_rounds WHERE round_id = ?", (round_id,)
        ).fetchone()
        if row is None:
            raise TransitionError(f"unknown review round {round_id}")
        return self._review_round_from_row(row)

    @staticmethod
    def _review_round_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "round_id": row["round_id"],
            "dispatch_id": row["dispatch_id"],
            "round": row["round"],
            "candidate_sha": row["candidate_sha"],
            "base_sha": row["base_sha"],
            "diff_digest": row["diff_digest"],
            "acceptance_digest": row["acceptance_digest"],
            "scope": row["scope"],
            "prior_round_id": row["prior_round_id"],
            "issued_by": row["issued_by"],
            "issued_at": row["issued_at"],
            "is_current": bool(row["is_current"]),
            "assigned_axis": row["assigned_axis"],
        }

    def submit_review_result(
        self,
        *,
        round_id: str,
        axis: str,
        verdict: str,
        agent_id: Any = None,
        findings: dict[str, Any] | None = None,
        candidate_sha: Any = None,
        base_sha: Any = None,
        diff_digest: Any = None,
        acceptance_digest: Any = None,
        scope: Any = None,
        round: Any = None,
        prior_round_id: Any = None,
    ) -> dict[str, Any]:
        """Record a Reviewer result that references an issued round.

        Reviewers may supply only axis, verdict, and findings. Any attempt to
        author or override lock identity fields is rejected. The caller must be
        a registered, non-archified reviewer assigned to exactly this round and
        axis, and the round must be the current unsuperseded tail.
        """
        import gwo_review

        resolved = self._identity(agent_id, "agent_id")
        gwo_review.validate_axis(axis)
        gwo_review.validate_verdict(verdict)
        self._reject_supplied_identity(candidate_sha, "candidate_sha")
        self._reject_supplied_identity(base_sha, "base_sha")
        self._reject_supplied_identity(diff_digest, "diff_digest")
        self._reject_supplied_identity(acceptance_digest, "acceptance_digest")
        self._reject_supplied_identity(scope, "scope")
        self._reject_supplied_identity(round, "round")
        self._reject_supplied_identity(prior_round_id, "prior_round_id")

        normalized_findings = gwo_review.normalize_findings(findings)
        findings_json = json.dumps(normalized_findings)
        now = _now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            round_row = self.db.execute(
                "SELECT dispatch_id, is_current FROM review_rounds WHERE round_id = ?",
                (round_id,),
            ).fetchone()
            if round_row is None:
                raise TransitionError(f"unknown review round {round_id}")
            if not int(round_row["is_current"]):
                raise TransitionError(
                    f"round {round_id} is stale or superseded"
                )

            # Authority check: caller is registered, role=reviewer, not archived,
            # and assigned to this exact round + axis.
            self._require_registered_reviewer(resolved)
            assignment = self.db.execute(
                "SELECT agent_id FROM review_assignments "
                "WHERE round_id = ? AND axis = ?",
                (round_id, axis),
            ).fetchone()
            if assignment is None:
                raise IdentityError(
                    f"no reviewer assigned to axis {axis} for round {round_id}"
                )
            if str(assignment["agent_id"]) != resolved:
                raise IdentityError(
                    f"axis {axis} is assigned to a different reviewer"
                )

            # For strict tiers, reject the same agent submitting both axes.
            other_result = self.db.execute(
                "SELECT agent_id FROM review_results "
                "WHERE round_id = ? AND axis != ?",
                (round_id, axis),
            ).fetchone()
            if other_result is not None and str(other_result["agent_id"]) == resolved:
                raise TransitionError(
                    "same agent cannot submit both axes on a strict review"
                )

            existing = self.db.execute(
                "SELECT axis FROM review_results WHERE round_id = ? AND axis = ?",
                (round_id, axis),
            ).fetchone()
            if existing is not None:
                raise TransitionError(
                    f"result for axis {axis} already exists on round {round_id}"
                )
            self.db.execute(
                "INSERT INTO review_results ("
                "round_id, axis, agent_id, verdict, findings_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (round_id, axis, resolved, verdict, findings_json, now),
            )
            self.db.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise TransitionError(
                f"result for axis {axis} already exists on round {round_id}"
            ) from error
        except BaseException:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        return self._review_result_row(round_id, axis)

    def check_review_gate(
        self,
        *,
        dispatch_id: str,
        candidate_sha: str,
    ) -> dict[str, Any]:
        """Evaluate the review gate for a dispatch at a candidate SHA.

        Accepts only the latest (current) round for the dispatch whose candidate
        matches. Fails closed for missing, stale, rejected, needs_work,
        withdrawn, wrong-axis, or incomplete evidence.
        """
        import gwo_review

        gwo_review.validate_sha("candidate_sha", candidate_sha)
        round_row = self.db.execute(
            "SELECT round_id, candidate_sha, assigned_axis FROM review_rounds "
            "WHERE dispatch_id = ? AND is_current = 1",
            (dispatch_id,),
        ).fetchone()
        if round_row is None:
            return {"approved": False, "reason": "no current review round"}
        if str(round_row["candidate_sha"]) != candidate_sha:
            return {"approved": False, "reason": "candidate mismatch"}

        task_row = self.db.execute(
            "SELECT t.risk FROM tasks t JOIN dispatches d ON d.task_id = t.task_id "
            "WHERE d.dispatch_id = ?",
            (dispatch_id,),
        ).fetchone()
        if task_row is None:
            return {"approved": False, "reason": "dispatch has no task"}
        tier = str(task_row["risk"])
        required_axes = gwo_review.tier_axes(tier)

        if not required_axes:
            return {"approved": False, "reason": "fast tier has no reviewer gate"}

        results = {
            str(row["axis"]): {
                "agent_id": str(row["agent_id"]),
                "verdict": str(row["verdict"]),
            }
            for row in self.db.execute(
                "SELECT axis, agent_id, verdict FROM review_results WHERE round_id = ?",
                (round_row["round_id"],),
            ).fetchall()
        }

        assignments = {
            str(row["axis"]): str(row["agent_id"])
            for row in self.db.execute(
                "SELECT axis, agent_id FROM review_assignments WHERE round_id = ?",
                (round_row["round_id"],),
            ).fetchall()
        }

        if any(
            r["verdict"] in ("rejected", "needs_work", "withdrawn")
            for r in results.values()
        ):
            return {"approved": False, "reason": "review result is negative"}

        provided_axes = set(results.keys())
        if provided_axes != set(required_axes):
            return {
                "approved": False,
                "reason": f"missing axes: {set(required_axes) - provided_axes}",
            }

        if set(assignments) != set(required_axes):
            return {"approved": False, "reason": "review assignments are incomplete"}

        if any(results[axis]["agent_id"] != assignments[axis] for axis in required_axes):
            return {"approved": False, "reason": "review result uses wrong axis authority"}

        assigned_agents = tuple(assignments.values())
        registered = {
            str(row["agent_id"])
            for row in self.db.execute(
                "SELECT agent_id FROM agents WHERE role = 'reviewer' "
                "AND archived_at IS NULL AND agent_id IN (?, ?)",
                assigned_agents if len(assigned_agents) == 2 else (assigned_agents[0], assigned_agents[0]),
            ).fetchall()
        }
        if set(assigned_agents) != registered:
            return {"approved": False, "reason": "assigned reviewer is not active"}

        if not all(r["verdict"] == "approved" for r in results.values()):
            return {"approved": False, "reason": "not all axes approved"}

        # For strict tier, require two distinct agents.
        if tier == "strict":
            agents = {r["agent_id"] for r in results.values()}
            if len(agents) < 2:
                return {
                    "approved": False,
                    "reason": "strict review requires two distinct agents",
                }

        return {"approved": True, "round_id": round_row["round_id"]}

    def _review_result_row(self, round_id: str, axis: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM review_results WHERE round_id = ? AND axis = ?",
            (round_id, axis),
        ).fetchone()
        if row is None:
            raise TransitionError(f"unknown review result {round_id}/{axis}")
        return {
            "round_id": row["round_id"],
            "axis": row["axis"],
            "agent_id": row["agent_id"],
            "verdict": row["verdict"],
            "findings_json": json.loads(row["findings_json"]),
            "created_at": row["created_at"],
        }

    def _integration_scope(self, scope: str) -> str:
        """Return the canonical scope for this repository.

        Lease scope is derived as repo:<Store.repo>:integration. Callers may not
        supply a foreign repository scope.
        """
        expected = f"repo:{self.repo}:integration"
        if scope != expected:
            raise IdentityError(
                f"lease scope must be {expected}, got {scope}"
            )
        return expected

    def acquire_integration_lease(
        self,
        *,
        scope: str,
        lease_id: Any = None,
        holder_agent: Any = None,
    ) -> dict[str, Any]:
        """Acquire the repository Integration Lease. Coordinator-only.

        The lease scope is derived as repo:<Store.repo>:integration and the
        caller must hold the active Coordinator claim. The partial unique
        index ``idx_leases_active_scope`` ensures at most one active lease per
        scope.
        """
        import gwo_lease

        actor = self._identity(holder_agent, "holder_agent")
        self._reject_supplied_identity(lease_id, "lease_id")
        canonical = self._integration_scope(scope)
        gwo_lease.validate_scope(canonical)

        resolved_lease_id = _new_id("lease")
        now = _now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._require_coordinator_claim(actor)
            row = self.db.execute(
                "SELECT lease_id, holder_agent, released_at FROM leases "
                "WHERE scope = ?",
                (canonical,),
            ).fetchone()
            if row is not None and row["released_at"] is None:
                raise LeaseBusy(
                    f"lease {canonical} already held by {row['holder_agent']}"
                )
            if row is None:
                self.db.execute(
                    "INSERT INTO leases ("
                    "lease_id, scope, holder_agent, acquired_at, released_at) "
                    "VALUES (?, ?, ?, ?, NULL)",
                    (resolved_lease_id, canonical, actor, now),
                )
            else:
                resolved_lease_id = row["lease_id"]
                self.db.execute(
                    "UPDATE leases SET holder_agent = ?, acquired_at = ?, "
                    "released_at = NULL WHERE lease_id = ?",
                    (actor, now, resolved_lease_id),
                )
            self.db.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise LeaseBusy(f"lease {canonical} is already held") from error
        except BaseException:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        return self._lease_row(resolved_lease_id)

    def integration_lease_holder(self, scope: str) -> str | None:
        canonical = self._integration_scope(scope)
        import gwo_lease

        gwo_lease.validate_scope(canonical)
        row = self.db.execute(
            "SELECT holder_agent FROM leases WHERE scope = ? AND released_at IS NULL",
            (canonical,),
        ).fetchone()
        return str(row["holder_agent"]) if row is not None else None

    def release_integration_lease(
        self,
        *,
        scope: str,
        holder_agent: Any = None,
    ) -> dict[str, Any]:
        """Release the Integration Lease. Only the Coordinator holder may release."""
        canonical = self._integration_scope(scope)
        actor = self._identity(holder_agent, "holder_agent")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._require_coordinator_claim(actor)
            row = self.db.execute(
                "SELECT lease_id, holder_agent, released_at FROM leases "
                "WHERE scope = ?",
                (canonical,),
            ).fetchone()
            if row is None or row["released_at"] is not None:
                raise TransitionError(f"no active lease for {canonical}")
            if row["holder_agent"] != actor:
                raise TransitionError(
                    f"only the lease holder may release {canonical}"
                )
            self.db.execute(
                "UPDATE leases SET released_at = ? WHERE lease_id = ?",
                (_now(), row["lease_id"]),
            )
            self.db.execute("COMMIT")
        except BaseException:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        return self._lease_row(row["lease_id"])

    def _lease_row(self, lease_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM leases WHERE lease_id = ?", (lease_id,)
        ).fetchone()
        if row is None:
            raise TransitionError(f"unknown lease {lease_id}")
        return {
            "lease_id": row["lease_id"],
            "scope": row["scope"],
            "holder_agent": row["holder_agent"],
            "acquired_at": row["acquired_at"],
            "released_at": row["released_at"],
        }

    def append_integration_chain(
        self,
        *,
        scope: str,
        candidate_sha: str,
        task_id: str,
        prior_chain_id: Any = None,
        chain_id: Any = None,
        tier: str | None = None,
    ) -> dict[str, Any]:
        """Append one node to the serial integration chain.

        Requires the caller to hold the active Coordinator claim and the current
        Integration Lease for the repository scope. Revalidates task/repo,
        candidate SHA, and the tier-appropriate review gate inside the same
        transaction. Uses a transaction-local monotonic position so chain order
        is independent of wall-clock time.
        """
        import gwo_lease
        import gwo_review

        canonical = self._integration_scope(scope)
        gwo_lease.validate_scope(canonical)
        gwo_review.validate_sha("candidate_sha", candidate_sha)
        gwo_review.validate_identifier("task_id", task_id)
        if tier is not None and tier not in ("fast", "standard", "strict"):
            raise ValueError("tier must be fast, standard, or strict")
        self._reject_supplied_identity(chain_id, "chain_id")
        self._reject_supplied_identity(prior_chain_id, "prior_chain_id")

        resolved_chain_id = _new_id("chain")
        now = _now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            actor = self._caller()
            self._require_coordinator_claim(actor)

            # Lease check: active and held by caller, in the same transaction.
            lease = self.db.execute(
                "SELECT lease_id, holder_agent, released_at FROM leases "
                "WHERE scope = ?",
                (canonical,),
            ).fetchone()
            if lease is None or lease["released_at"] is not None:
                raise IdentityError(f"active Integration Lease required for {canonical}")
            if str(lease["holder_agent"]) != actor:
                raise IdentityError(
                    f"only the lease holder may append to integration chain {canonical}"
                )

            # Task/repository check.
            task_row = self.db.execute(
                "SELECT task_id, risk FROM tasks WHERE task_id = ? AND repo = ?",
                (task_id, self.repo),
            ).fetchone()
            if task_row is None:
                raise TransitionError(
                    f"task {task_id} does not exist in repo {self.repo}"
                )
            resolved_tier = tier or str(task_row["risk"])
            if tier is not None and resolved_tier != str(task_row["risk"]):
                raise TransitionError(
                    f"integration tier {tier} does not match task risk {task_row['risk']}"
                )

            # Dispatch check: the supplied task must have an active dispatch with
            # the same candidate SHA.
            dispatch_row = self.db.execute(
                "SELECT dispatch_id FROM dispatches "
                "WHERE task_id = ? AND status = 'active'",
                (task_id,),
            ).fetchone()
            if dispatch_row is None:
                raise TransitionError(f"no active dispatch for task {task_id}")

            # Candidate check: the active dispatch must be at the supplied SHA.
            # For the V7 store we validate by comparing against the current review
            # round or, for fast tier, by an explicit coordinator-inline gate.
            if resolved_tier == "fast":
                # Fast tier uses an explicit coordinator-inline tier. The
                # Coordinator has already verified candidate evidence before
                # append; no reviewer gate exists.
                if tier != "fast":
                    raise TransitionError(
                        "fast integration requires explicit coordinator-inline tier"
                    )
            else:
                gate = self.check_review_gate(
                    dispatch_id=dispatch_row["dispatch_id"],
                    candidate_sha=candidate_sha,
                )
                if not gate["approved"]:
                    raise TransitionError(
                        f"review gate not passed for task {task_id}: {gate.get('reason')}"
                    )

            # Determine the next position transaction-locally under the chain
            # lock. created_at is metadata only.
            tail_row = self.db.execute(
                "SELECT MAX(position) AS max_position FROM integration_chain "
                "WHERE scope = ?",
                (canonical,),
            ).fetchone()
            next_position = (int(tail_row["max_position"]) if tail_row["max_position"] is not None else 0) + 1

            # Re-check lease holder inside the same transaction to detect a
            # release race.
            current_lease = self.db.execute(
                "SELECT holder_agent, released_at FROM leases WHERE scope = ?",
                (canonical,),
            ).fetchone()
            if current_lease is None or current_lease["released_at"] is not None:
                raise IdentityError(
                    f"lease was released during append for {canonical}"
                )
            if str(current_lease["holder_agent"]) != actor:
                raise IdentityError(
                    f"lease holder changed during append for {canonical}"
                )

            expected_prior = self.db.execute(
                "SELECT chain_id FROM integration_chain "
                "WHERE scope = ? AND position = ("
                "SELECT MAX(position) FROM integration_chain WHERE scope = ?)"
                "AND position < ?",
                (canonical, canonical, next_position),
            ).fetchone()
            prior_value = expected_prior["chain_id"] if expected_prior is not None else None

            self.db.execute(
                "INSERT INTO integration_chain ("
                "chain_id, scope, candidate_sha, task_id, prior_chain_id, position, head, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    resolved_chain_id,
                    canonical,
                    candidate_sha,
                    task_id,
                    prior_value,
                    next_position,
                    resolved_chain_id,
                    now,
                ),
            )
            # Maintain head pointer: every node in the scope points to the new head.
            self.db.execute(
                "UPDATE integration_chain SET head = ? WHERE scope = ?",
                (resolved_chain_id, canonical),
            )
            self.db.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise TransitionError(
                f"integration chain append failed for {canonical}: concurrent append or release"
            ) from error
        except sqlite3.OperationalError as error:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise StoreError(
                f"integration chain append failed for {canonical}: database locked or concurrent race"
            ) from error
        except BaseException:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        return self._chain_row(resolved_chain_id)

    def list_integration_chain(self, *, scope: str) -> list[dict[str, Any]]:
        canonical = self._integration_scope(scope)
        import gwo_lease

        gwo_lease.validate_scope(canonical)
        rows = self.db.execute(
            "SELECT * FROM integration_chain WHERE scope = ? ORDER BY position",
            (canonical,),
        ).fetchall()
        return [self._chain_from_row(row) for row in rows]

    def _chain_row(self, chain_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM integration_chain WHERE chain_id = ?", (chain_id,)
        ).fetchone()
        if row is None:
            raise TransitionError(f"unknown chain node {chain_id}")
        return self._chain_from_row(row)

    @staticmethod
    def _chain_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "chain_id": row["chain_id"],
            "scope": row["scope"],
            "candidate_sha": row["candidate_sha"],
            "task_id": row["task_id"],
            "prior_chain_id": row["prior_chain_id"],
            "position": row["position"],
            "head": row["head"],
            "created_at": row["created_at"],
        }
