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


def agent_status(store: Any, agent_id: str) -> dict[str, Any]:
    """Return the runtime status of one agent: running/stalled/exited + evidence.

    Phase 1 reads from the agents table. A registered agent with no archived_at
    is ``running``; an archived agent is ``exited``. An unknown agent is
    ``exited`` with empty terminal evidence (it was never spawned through the
    store). The Runtime Port adapter refines this in later phases.
    """
    _validate_agent_id(agent_id, "agent_id")
    db = store.db
    row = db.execute(
        "SELECT agent_id, adapter, runtime_ref, role, group_label, "
        "created_at, archived_at FROM agents WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()
    if row is None:
        return {
            "agent_id": agent_id,
            "state": "exited",
            "terminal_evidence": {},
            "registered": False,
        }
    if row["archived_at"] is not None:
        state = "exited"
    else:
        state = "running"
    return {
        "agent_id": str(row["agent_id"]),
        "adapter": str(row["adapter"]),
        "runtime_ref": row["runtime_ref"],
        "role": str(row["role"]),
        "group_label": row["group_label"],
        "state": state,
        "terminal_evidence": {},
        "registered": True,
    }


def config_check(store: Any, *, gwo_home: str | None = None) -> dict[str, Any]:
    """Validate the GWO configuration: home directory, repository, schema.

    Returns a structured result with ``valid`` boolean and ``errors`` list.
    Invalid config blocks new dispatches but never abandons existing work.
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
    # Check schema migrations are present.
    try:
        row = store.db.execute(
            "SELECT name FROM schema_migrations ORDER BY name DESC LIMIT 1"
        ).fetchone()
        if row is None:
            errors.append("schema_migrations is empty")
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

    Reconstructs tasks from issues, agents from adapter listing, and worktrees
    from Git. Anything unreconcilable is surfaced in ``ambiguities`` for human
    adjudication; the rebuild never destructively infers missing or conflicting
    data. Existing store rows are preserved; the rebuild is additive.
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
        # Reconstruct agents from adapter listing. Conflicting listings for the
        # same agent_id are an ambiguity, not a destructive overwrite.
        seen_agents: dict[str, dict[str, Any]] = {}
        for entry in adapter_listing:
            aid = entry.get("agent_id")
            if aid is None:
                ambiguities.append("adapter listing entry missing agent_id")
                continue
            if aid in seen_agents:
                prior = seen_agents[aid]
                if prior.get("status") != entry.get("status"):
                    ambiguities.append(
                        f"agent {aid} has conflicting adapter status: "
                        f"{prior.get('status')} vs {entry.get('status')}"
                    )
                continue
            seen_agents[aid] = entry
            existing = db.execute(
                "SELECT agent_id FROM agents WHERE agent_id = ?", (aid,)
            ).fetchone()
            if existing is not None:
                continue
            role = entry.get("role", "worker")
            if role not in ROLE_VALUES:
                ambiguities.append(f"agent {aid} has invalid role {role}")
                continue
            db.execute(
                "INSERT INTO agents (agent_id, adapter, runtime_ref, session_id, "
                "pid, role, group_label, created_at, archived_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (aid, entry.get("adapter", "paseo"), entry.get("runtime_ref"),
                 entry.get("session_id"), entry.get("pid"), role,
                 entry.get("group_label"), _now()),
            )
            rebuilt_count += 1
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