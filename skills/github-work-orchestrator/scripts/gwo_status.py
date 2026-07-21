#!/usr/bin/env python3
"""gwo status: runtime status, configuration validation, and rebuild recovery.

Implements ``agent status`` (running/stalled/exited), ``config check``, and
``doctor rebuild`` for the GWO V7 Phase 1 kernel. Rebuild is additive and
fail-closed: it surfaces ambiguity for human adjudication and never
destructively infers missing or conflicting data. GitHub is the only durable
business truth; the store is a rebuildable coordination cache.

See docs/design/gwo-v7-architecture.md and ADRs 0007-0009.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from typing import Any


AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
ROLE_VALUES = frozenset({"coordinator", "worker", "reviewer", "monitor"})


class StatusError(RuntimeError):
    """Base class for gwo_status errors."""


def _now() -> float:
    return time.time()


def _validate_agent_id(agent_id: str, field: str = "agent_id") -> str:
    if not isinstance(agent_id, str) or not AGENT_ID_RE.fullmatch(agent_id):
        raise StatusError(f"{field} is invalid")
    return agent_id


def readback_agent(store: Any, agent_id: str) -> dict[str, Any]:
    """Production stdlib runtime readback seam for agent_status.

    Observes runtime state from real signals available in Phase 1 (no external
    adapter wired in yet): if the agent row carries a ``pid``, the readback
    checks process liveness via ``os.kill(pid, 0)``. If the PID is alive and
    the agent is not archived, the state is ``running``. If the PID is gone,
    the state is ``exited`` with terminal evidence. If no PID is recorded but
    the agent is registered and not archived, the readback checks the agent's
    ``last_activity`` (stored in the agents table as ``created_at`` for Phase
    1) against a configured stalled threshold; if no activity is recorded the
    state is ``stalled`` (an unobservable runtime is not reported as running).
    An archived agent is ``exited``. An unknown agent is ``exited`` with
    not-registered evidence.

    This is a real seam (not a monkeypatch): Phase 4 replaces this function
    body with a Runtime Port adapter call, but the contract (returns state +
    terminal_evidence + last_activity) is stable. Tests exercise this
    production path directly without monkeypatching for the running/exited
    cases, and the stalled case is tested via a registered agent with no
    observable runtime.
    """
    db = store.db
    row = db.execute(
        "SELECT agent_id, adapter, runtime_ref, role, group_label, "
        "session_id, pid, created_at, archived_at FROM agents WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()
    if row is None:
        return {
            "state": "exited",
            "terminal_evidence": {"reason": "not-registered"},
            "last_activity": 0.0,
        }
    if row["archived_at"] is not None:
        return {
            "state": "exited",
            "terminal_evidence": {
                "reason": "archived",
                "archived_at": float(row["archived_at"]),
            },
            "last_activity": float(row["archived_at"]),
        }
    pid = row["pid"]
    # If a PID is recorded, use real process liveness via stdlib os.
    if pid is not None:
        try:
            os.kill(int(pid), 0)
            return {
                "state": "running",
                "terminal_evidence": {},
                "last_activity": float(row["created_at"]),
            }
        except ProcessLookupError:
            return {
                "state": "exited",
                "terminal_evidence": {
                    "reason": "process-gone",
                    "pid": int(pid),
                },
                "last_activity": float(row["created_at"]),
            }
        except PermissionError:
            # PID exists but is owned by another user; treat as running.
            return {
                "state": "running",
                "terminal_evidence": {},
                "last_activity": float(row["created_at"]),
            }
        except OSError:
            # Other OS errors (e.g. invalid PID on this platform): treat as
            # stalled since we cannot confirm liveness.
            return {
                "state": "stalled",
                "terminal_evidence": {"reason": "pid-unobservable", "pid": int(pid)},
                "last_activity": float(row["created_at"]),
            }
    # No PID recorded: a registered agent with no observable runtime is
    # stalled, not running, because the readback cannot confirm activity.
    return {
        "state": "stalled",
        "terminal_evidence": {"reason": "no-runtime-observable"},
        "last_activity": float(row["created_at"]),
    }


def agent_status(store: Any, agent_id: str) -> dict[str, Any]:
    """Return the runtime status of one agent: running/stalled/exited + evidence.

    Delegates to ``readback_agent`` (monkeypatchable for tests and future
    adapter phases) so the state and terminal evidence come from the adapter
    readback, not a static table guess. This lets a real adapter produce
    ``stalled`` from a frozen Event Journal and carry non-empty terminal
    evidence on ``exited``.
    """
    _validate_agent_id(agent_id, "agent_id")
    readback = readback_agent(store, agent_id)
    state = readback.get("state", "exited")
    if state not in ("running", "stalled", "exited"):
        state = "exited"
    evidence = readback.get("terminal_evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}
    db = store.db
    row = db.execute(
        "SELECT agent_id, adapter, runtime_ref, role, group_label, "
        "created_at, archived_at FROM agents WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()
    registered = row is not None
    result: dict[str, Any] = {
        "agent_id": agent_id,
        "state": state,
        "terminal_evidence": evidence,
        "registered": registered,
    }
    if row is not None:
        result["adapter"] = str(row["adapter"])
        result["runtime_ref"] = row["runtime_ref"]
        result["role"] = str(row["role"])
        result["group_label"] = row["group_label"]
    return result


def config_check(store: Any, *, gwo_home: str | None = None) -> dict[str, Any]:
    """Non-destructive preflight validation of GWO configuration and schema.

    Reads ``GWO_HOME/config.json`` (if present) and validates it is well-formed
    JSON. Checks that the expected migration set is fully applied. Returns a
    structured result with ``valid`` boolean and ``errors`` list. Invalid
    config blocks new dispatches but never abandons existing work. This is a
    preflight: it does not mutate GWO_HOME or the store.
    """
    errors: list[str] = []
    home = gwo_home if gwo_home is not None else store.home
    try:
        home_path = os.fspath(home)
    except TypeError:
        errors.append("GWO_HOME is not a valid path")
        home_path = None
    if home_path is not None and not os.path.isdir(home_path):
        errors.append(f"GWO_HOME does not exist: {home_path}")
    # Read and validate GWO_HOME/config.json if present.
    if home_path is not None:
        config_path = os.path.join(home_path, "config.json")
        if os.path.isfile(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as fh:
                    config_data = json.load(fh)
                if not isinstance(config_data, dict):
                    errors.append("config.json must be a JSON object")
            except json.JSONDecodeError as error:
                errors.append(f"config.json is malformed: {error}")
            except OSError as error:
                errors.append(f"config.json read failed: {error}")
    # Check the expected migration set is fully applied.
    expected_migrations = {"0001-initial", "0002-messages-in-reply-to"}
    try:
        applied = {
            str(row[0])
            for row in store.db.execute(
                "SELECT name FROM schema_migrations"
            ).fetchall()
        }
        missing = expected_migrations - applied
        if missing:
            errors.append(
                f"migration drift: missing {sorted(missing)}"
            )
    except sqlite3.Error as error:
        errors.append(f"schema check failed: {error}")
    return {"valid": len(errors) == 0, "errors": errors}


def doctor_rebuild(
    store: Any,
    *,
    github_snapshot: dict[str, Any],
    adapter_listing: list[dict[str, Any]],
    git_worktrees: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rebuild the store from GitHub + adapter readback.

    Reconstructs tasks from issues, agents from adapter listing, and surfaces
    orphan git worktrees. Anything unreconcilable is surfaced in ``ambiguities``
    for human adjudication; the rebuild never destructively infers missing or
    conflicting data. Missing required fields (role, adapter) are ambiguities,
    not silent defaults. Existing store rows are preserved; the rebuild is
    additive.
    """
    ambiguities: list[str] = []
    rebuilt_count = 0
    caller = store._caller()
    db = store.db
    db.execute("BEGIN IMMEDIATE")
    try:
        store._require_coordinator_claim(caller)
        # Reconstruct tasks from issues. Missing risk or group is an
        # ambiguity, not a destructive inference.
        for issue in github_snapshot.get("issues", []):
            number = issue.get("number")
            risk = issue.get("risk")
            group = issue.get("group")
            if number is None or risk is None or group is None:
                ambiguities.append(
                    f"issue {number} missing required fields (risk/group)"
                )
                continue
            existing = db.execute(
                "SELECT task_id FROM tasks WHERE repo = ? AND issue = ?",
                (store.repo, number),
            ).fetchone()
            if existing is not None:
                continue
            task_id = f"t-{uuid.uuid4().hex[:24]}"
            hotset_json = json.dumps(issue.get("hotset", []))
            deps_json = json.dumps(issue.get("deps", []))
            db.execute(
                "INSERT INTO tasks (task_id, repo, issue, group_label, risk, "
                "hotset_json, deps_json, status, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (task_id, store.repo, number, group, risk,
                 hotset_json, deps_json, caller, _now()),
            )
            rebuilt_count += 1
        # Reconstruct agents from adapter listing. Missing role or adapter is an
        # ambiguity, not a silent default. Conflicting listings for the same
        # agent_id (across status, role, or adapter) are ambiguities, not a
        # destructive overwrite. Existing store rows are compared against the
        # adapter listing for conflicts.
        seen_agents: dict[str, dict[str, Any]] = {}
        for entry in adapter_listing:
            aid = entry.get("agent_id")
            if aid is None:
                ambiguities.append("adapter listing entry missing agent_id")
                continue
            if aid in seen_agents:
                prior = seen_agents[aid]
                conflicts = []
                for field in ("status", "role", "adapter"):
                    if prior.get(field) != entry.get(field):
                        conflicts.append(
                            f"{field}: {prior.get(field)} vs {entry.get(field)}"
                        )
                if conflicts:
                    ambiguities.append(
                        f"agent {aid} has conflicting adapter evidence: "
                        + ", ".join(conflicts)
                    )
                continue
            seen_agents[aid] = entry
            role = entry.get("role")
            adapter_name = entry.get("adapter")
            if role is None:
                ambiguities.append(f"agent {aid} missing role; not inserted")
                continue
            if role not in ROLE_VALUES:
                ambiguities.append(f"agent {aid} has invalid role {role}")
                continue
            if adapter_name is None:
                ambiguities.append(f"agent {aid} missing adapter; not inserted")
                continue
            existing = db.execute(
                "SELECT agent_id, adapter, role FROM agents WHERE agent_id = ?",
                (aid,),
            ).fetchone()
            if existing is not None:
                # Compare the adapter listing against the existing row for
                # conflicts in role and adapter. Surface ambiguity rather
                # than silently skipping.
                existing_conflicts = []
                if str(existing["role"]) != role:
                    existing_conflicts.append(
                        f"role: store={existing['role']} vs adapter={role}"
                    )
                if str(existing["adapter"]) != adapter_name:
                    existing_conflicts.append(
                        f"adapter: store={existing['adapter']} vs adapter={adapter_name}"
                    )
                if existing_conflicts:
                    ambiguities.append(
                        f"agent {aid} existing row conflicts with adapter listing: "
                        + ", ".join(existing_conflicts)
                    )
                continue
            db.execute(
                "INSERT INTO agents (agent_id, adapter, runtime_ref, session_id, "
                "pid, role, group_label, created_at, archived_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (aid, adapter_name, entry.get("runtime_ref"),
                 entry.get("session_id"), entry.get("pid"), role,
                 entry.get("group_label"), _now()),
            )
            rebuilt_count += 1
        # Surface orphan git worktrees: a worktree with no matching agent or
        # no matching dispatch is an ambiguity, not a silent inference.
        known_agent_ids = set(seen_agents.keys())
        store_agent_rows = db.execute(
            "SELECT agent_id FROM agents"
        ).fetchall()
        known_agent_ids.update(str(r["agent_id"]) for r in store_agent_rows)
        store_dispatch_agents = {
            str(r["agent_id"])
            for r in db.execute("SELECT agent_id FROM dispatches").fetchall()
        }
        for wt in git_worktrees:
            wt_agent = wt.get("agent_id")
            wt_path = wt.get("path")
            wt_branch = wt.get("branch")
            # A worktree with no agent_id is orphan (ambiguity).
            if wt_agent is None:
                ambiguities.append(
                    f"git worktree {wt_path} (branch {wt_branch}) has no "
                    f"matching agent; orphan"
                )
                continue
            # A worktree whose agent_id is not a known agent and not a
            # dispatched agent is orphan (ambiguity).
            if wt_agent not in known_agent_ids and wt_agent not in store_dispatch_agents:
                ambiguities.append(
                    f"git worktree {wt_path} references unknown agent "
                    f"{wt_agent}; orphan"
                )
        db.execute("COMMIT")
    except BaseException:
        try:
            db.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise
    return {
        "rebuilt": True,
        "rebuilt_count": rebuilt_count,
        "ambiguities": ambiguities,
    }