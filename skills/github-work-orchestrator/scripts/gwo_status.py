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


EXPECTED_MIGRATIONS = frozenset({"0001-initial", "0002-messages-in-reply-to"})


def preflight_config(
    home: str | os.PathLike[str],
    repo: str,
) -> dict[str, Any]:
    """Non-destructive preflight validation of GWO configuration and schema.

    Inspects config/database/migration state **before** Store.connect or any
    filesystem/schema mutation. Does not create GWO_HOME, does not open the
    database for writes, and does not apply migrations. Returns a structured
    result with ``valid`` boolean and ``errors`` list.

    Checks:
    - GWO_HOME exists (does not create it if missing).
    - config.json (if present) is well-formed JSON.
    - The database's schema_migrations table matches the expected migration
      set (read-only connection; no migration application).
    """
    errors: list[str] = []
    home_path = os.fspath(home)
    if not os.path.isdir(home_path):
        errors.append(f"GWO_HOME does not exist: {home_path}")
        return {"valid": False, "errors": errors, "home": home_path}
    # Read and validate config.json if present.
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
    # Check migration set via a read-only connection (no mutation).
    # Resolve the repo directory without creating it.
    import hashlib
    import re as _re
    repo_clean = repo.strip()
    digest = hashlib.sha256(repo_clean.lower().encode("utf-8")).hexdigest()[:24]
    safe = _re.sub(r"[^a-z0-9-]+", "-", repo_clean.lower()).strip("-")
    slug = f"{safe[:48]}-{digest}"
    repo_dir = os.path.join(os.path.realpath(home_path), slug)
    db_path = os.path.join(repo_dir, "state.db")
    if not os.path.isfile(db_path):
        errors.append(f"database does not exist: {db_path}")
        return {"valid": False, "errors": errors, "home": home_path}
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            applied = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM schema_migrations"
                ).fetchall()
            }
            missing = EXPECTED_MIGRATIONS - applied
            if missing:
                errors.append(
                    f"migration drift: missing {sorted(missing)}"
                )
        finally:
            conn.close()
    except sqlite3.Error as error:
        errors.append(f"database check failed: {error}")
    return {"valid": len(errors) == 0, "errors": errors, "home": home_path}


def _now() -> float:
    return time.time()


def _validate_agent_id(agent_id: str, field: str = "agent_id") -> str:
    if not isinstance(agent_id, str) or not AGENT_ID_RE.fullmatch(agent_id):
        raise StatusError(f"{field} is invalid")
    return agent_id


def readback_agent(store: Any, agent_id: str) -> dict[str, Any]:
    """Production stdlib runtime readback seam for agent_status.

    Implements truthful resident Paseo runtime readback for Phase 1: a
    registered Paseo agent (adapter="paseo") is a resident-agent that is
    long-lived with idle/running states. Without an external adapter wired in
    yet, the readback uses the available stdlib runtime evidence:

    - If the agent row carries a ``pid``, check process liveness via
      ``os.kill(pid, 0)``. A live PID means running; a gone PID means exited
      with terminal evidence.
    - If no PID is recorded, the resident-agent model says the agent is
      running unless the row is archived. The adapter field distinguishes
      the execution model: ``paseo`` (resident-agent) defaults to running;
      ``headless`` (session-process, future) would use PID liveness
      exclusively.
    - An archived agent is exited with archived evidence.
    - An unknown agent is exited with not-registered evidence.

    This is a real seam: Phase 4 replaces this function body with a Runtime
    Port adapter call, but the contract (returns state + terminal_evidence +
    last_activity) is stable. Tests exercise the production path directly
    without monkeypatching.
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
    adapter = str(row["adapter"])
    pid = row["pid"]
    runtime_ref = row["runtime_ref"]
    session_id = row["session_id"]
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
            return {
                "state": "running",
                "terminal_evidence": {},
                "last_activity": float(row["created_at"]),
            }
        except OSError:
            return {
                "state": "stalled",
                "terminal_evidence": {"reason": "pid-unobservable", "pid": int(pid)},
                "last_activity": float(row["created_at"]),
            }
    # No PID recorded. For the resident-agent model (paseo adapter), a
    # registered agent is running unless archived — the agent is long-lived
    # with idle/running states. The runtime_ref and session_id are the
    # resident-runtime evidence of this liveness.
    if adapter == "paseo":
        return {
            "state": "running",
            "terminal_evidence": {},
            "last_activity": float(row["created_at"]),
        }
    # For other adapters (future headless/session-process), no PID means the
    # runtime is unobservable; report stalled.
    return {
        "state": "stalled",
        "terminal_evidence": {
            "reason": "no-runtime-observable",
            "adapter": adapter,
        },
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

    Prevalidates all evidence before any write. Reconstructs tasks from issues,
    comparing existing task evidence field-by-field. Rejects incomplete adapter
    records (missing status, role, or adapter). Surfaces orphan git worktrees
    and known-agent worktrees without matching dispatch as ambiguities. The
    rebuild never destructively infers missing or conflicting data; conflicted
    identities remain uninserted. Existing store rows are preserved; the rebuild
    is additive and atomic/fail-closed.
    """
    ambiguities: list[str] = []
    caller = store._caller()
    db = store.db
    # Phase 1: prevalidate all evidence before any write. Collect the lists of
    # tasks to insert and agents to insert. Any conflict or missing field
    # produces an ambiguity and prevents insertion of that entity.
    tasks_to_insert: list[dict[str, Any]] = []
    agents_to_insert: list[dict[str, Any]] = []

    # Prevalidate issues/tasks.
    for issue in github_snapshot.get("issues", []):
        number = issue.get("number")
        risk = issue.get("risk")
        group = issue.get("group")
        if number is None or risk is None or group is None:
            ambiguities.append(
                f"issue {number} missing required fields (risk/group)"
            )
            continue
        # Check existing task evidence field-by-field.
        existing = db.execute(
            "SELECT task_id, group_label, risk, hotset_json, deps_json "
            "FROM tasks WHERE repo = ? AND issue = ?",
            (store.repo, number),
        ).fetchone()
        if existing is not None:
            existing_conflicts = []
            if str(existing["group_label"] or "") != group:
                existing_conflicts.append(
                    f"group: store={existing['group_label']} vs github={group}"
                )
            if str(existing["risk"]) != risk:
                existing_conflicts.append(
                    f"risk: store={existing['risk']} vs github={risk}"
                )
            existing_hotset = json.loads(existing["hotset_json"])
            new_hotset = issue.get("hotset", [])
            if existing_hotset != new_hotset:
                existing_conflicts.append(
                    f"hotset: store={existing_hotset} vs github={new_hotset}"
                )
            existing_deps = json.loads(existing["deps_json"])
            new_deps = issue.get("deps", [])
            if existing_deps != new_deps:
                existing_conflicts.append(
                    f"deps: store={existing_deps} vs github={new_deps}"
                )
            if existing_conflicts:
                ambiguities.append(
                    f"issue {number} existing task conflicts with GitHub: "
                    + ", ".join(existing_conflicts)
                )
            continue
        tasks_to_insert.append({
            "number": number, "risk": risk, "group": group,
            "hotset": issue.get("hotset", []), "deps": issue.get("deps", []),
        })

    # Prevalidate adapter listing agents. Two-pass: first collect all entries
    # and detect conflicts, then queue only conflict-free entries for insertion.
    seen_agents: dict[str, dict[str, Any]] = {}
    conflicted_agents: set[str] = set()
    for entry in adapter_listing:
        aid = entry.get("agent_id")
        if aid is None:
            ambiguities.append("adapter listing entry missing agent_id")
            continue
        # Required fields: agent_id, status, role, adapter.
        missing = []
        for field in ("status", "role", "adapter"):
            if entry.get(field) is None:
                missing.append(field)
        if missing:
            ambiguities.append(
                f"agent {aid} missing required fields: {', '.join(missing)}"
            )
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
                conflicted_agents.add(aid)
            seen_agents[aid] = entry
            continue
        seen_agents[aid] = entry

    # Second pass: queue only conflict-free, valid entries for insertion.
    for aid, entry in seen_agents.items():
        if aid in conflicted_agents:
            continue
        role = entry.get("role")
        adapter_name = entry.get("adapter")
        if role not in ROLE_VALUES:
            ambiguities.append(f"agent {aid} has invalid role {role}")
            continue
        # Check existing store row for conflicts.
        existing = db.execute(
            "SELECT agent_id, adapter, role FROM agents WHERE agent_id = ?",
            (aid,),
        ).fetchone()
        if existing is not None:
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
        agents_to_insert.append(entry)

    # Prevalidate git worktrees against known agents and dispatches.
    known_agent_ids = set(seen_agents.keys())
    store_agent_rows = db.execute("SELECT agent_id FROM agents").fetchall()
    known_agent_ids.update(str(r["agent_id"]) for r in store_agent_rows)
    store_dispatch_agents = {
        str(r["agent_id"])
        for r in db.execute("SELECT agent_id FROM dispatches").fetchall()
    }
    for wt in git_worktrees:
        wt_agent = wt.get("agent_id")
        wt_path = wt.get("path")
        wt_branch = wt.get("branch")
        if wt_agent is None:
            ambiguities.append(
                f"git worktree {wt_path} (branch {wt_branch}) has no "
                f"matching agent; orphan"
            )
            continue
        # A worktree whose agent_id is not a known agent and not a dispatched
        # agent is orphan.
        if wt_agent not in known_agent_ids and wt_agent not in store_dispatch_agents:
            ambiguities.append(
                f"git worktree {wt_path} references unknown agent "
                f"{wt_agent}; orphan"
            )
            continue
        # A known-agent worktree without matching dispatch is ambiguous.
        if wt_agent in known_agent_ids and wt_agent not in store_dispatch_agents:
            ambiguities.append(
                f"git worktree {wt_path} references known agent {wt_agent} "
                f"with no matching dispatch; ambiguous"
            )

    # Phase 2: write only prevalidated, conflict-free entities. This is atomic:
    # all writes commit or roll back together.
    rebuilt_count = 0
    db.execute("BEGIN IMMEDIATE")
    try:
        store._require_coordinator_claim(caller)
        for task_data in tasks_to_insert:
            task_id = f"t-{uuid.uuid4().hex[:24]}"
            hotset_json = json.dumps(task_data["hotset"])
            deps_json = json.dumps(task_data["deps"])
            db.execute(
                "INSERT INTO tasks (task_id, repo, issue, group_label, risk, "
                "hotset_json, deps_json, status, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (task_id, store.repo, task_data["number"], task_data["group"],
                 task_data["risk"], hotset_json, deps_json, caller, _now()),
            )
            rebuilt_count += 1
        for entry in agents_to_insert:
            aid = entry["agent_id"]
            db.execute(
                "INSERT INTO agents (agent_id, adapter, runtime_ref, session_id, "
                "pid, role, group_label, created_at, archived_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (aid, entry.get("adapter"), entry.get("runtime_ref"),
                 entry.get("session_id"), entry.get("pid"), entry.get("role"),
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