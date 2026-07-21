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


EXPECTED_MIGRATIONS = frozenset({
    "0001-initial", "0002-messages-in-reply-to", "0003-tasks-repo-issue-unique",
})

# Runtime readback source registry. Phase 1 uses an injected/read-only Paseo
# snapshot (set via set_readback_snapshot) so agent_status can observe actual
# runtime state rather than inferring it from the adapter name. Phase 4 will
# replace this with a Runtime Port adapter call. The snapshot is a dict with
# an "agents" list of {agent_id, state, terminal_evidence} entries.
_READBACK_SNAPSHOTS: dict[int, dict[str, Any]] = {}


def set_readback_snapshot(store: Any, snapshot: dict[str, Any]) -> None:
    """Inject a read-only Paseo runtime snapshot for agent_status readback.

    The snapshot maps agent_id -> {state, terminal_evidence} entries. This is
    the stdlib-safe boundary for Phase 1: tests and the Coordinator inject a
    snapshot obtained from a Paseo listing (get_agent_status readbacks). Phase
    4 replaces this with a direct Runtime Port adapter call.
    """
    _READBACK_SNAPSHOTS[id(store.db)] = snapshot


def _get_readback_snapshot(store: Any) -> dict[str, Any] | None:
    in_memory = _READBACK_SNAPSHOTS.get(id(store.db))
    if in_memory is not None:
        return in_memory
    # File-based readback: check GWO_HOME/readback.json for a cross-process
    # snapshot usable by independent CLI invocations.
    home = getattr(store, "home", None)
    if home is not None:
        snapshot_path = os.path.join(os.fspath(home), "readback.json")
        if os.path.isfile(snapshot_path):
            try:
                with open(snapshot_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
    return None


def load_readback_snapshot_file(path: str) -> dict[str, Any]:
    """Load a readback snapshot from a JSON file path.

    Raises StatusError if the file is malformed or not a JSON object.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as error:
        raise StatusError(f"readback snapshot is malformed: {error}")
    except OSError as error:
        raise StatusError(f"readback snapshot read failed: {error}")
    if not isinstance(data, dict):
        raise StatusError("readback snapshot must be a JSON object")
    return data


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

    Implements truthful resident Paseo runtime readback for Phase 1 using a
    read-only Paseo snapshot (injected via set_readback_snapshot) as the
    runtime readback source. The snapshot maps agent_id -> {state,
    terminal_evidence} entries obtained from Paseo listing readbacks.

    - If a snapshot is present and lists the agent, use its state and
      terminal_evidence. This is the truthful readback from the runtime.
    - If a snapshot is present but the agent is NOT listed, the agent is
      stalled (registered but not observed in the runtime).
    - If a PID is recorded, use os.kill(pid, 0) as a secondary signal (a gone
      PID means exited even if the snapshot says running).
    - If no snapshot is present, a paseo agent without PID is stalled (we
      cannot confirm liveness without a runtime source).
    - An archived agent is exited with archived evidence.
    - An unknown agent is exited with not-registered evidence.

    This is a real seam: Phase 4 replaces this function body with a Runtime
    Port adapter call, but the contract (returns state + terminal_evidence +
    last_activity) is stable.
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
    snapshot = _get_readback_snapshot(store)
    # If a snapshot is present, use it as the authoritative runtime source.
    if snapshot is not None:
        agents_map = {
            str(a.get("agent_id")): a
            for a in snapshot.get("agents", [])
            if isinstance(a, dict)
        }
        if agent_id in agents_map:
            entry = agents_map[agent_id]
            state = str(entry.get("state", "stalled"))
            if state not in ("running", "stalled", "exited"):
                state = "stalled"
            evidence = entry.get("terminal_evidence", {})
            if not isinstance(evidence, dict):
                evidence = {}
            # If a PID is recorded and says exited, override to exited.
            if pid is not None and state == "running":
                try:
                    os.kill(int(pid), 0)
                except ProcessLookupError:
                    state = "exited"
                    evidence = {"reason": "process-gone", "pid": int(pid)}
                except (PermissionError, OSError):
                    pass
            return {
                "state": state,
                "terminal_evidence": evidence,
                "last_activity": float(row["created_at"]),
            }
        # Agent is registered but not in the snapshot: stalled.
        return {
            "state": "stalled",
            "terminal_evidence": {"reason": "not-in-runtime-snapshot"},
            "last_activity": float(row["created_at"]),
        }
    # No snapshot: use PID liveness if available.
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
                "terminal_evidence": {"reason": "process-gone", "pid": int(pid)},
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
    # No snapshot and no PID: cannot confirm liveness; stalled.
    return {
        "state": "stalled",
        "terminal_evidence": {"reason": "no-runtime-source"},
        "last_activity": float(row["created_at"]),
    }


def agent_status(
    store: Any,
    agent_id: str,
    *,
    readback_snapshot_path: str | None = None,
) -> dict[str, Any]:
    """Return the runtime status of one agent: running/stalled/exited + evidence.

    Delegates to ``readback_agent`` so the state and terminal evidence come
    from the adapter readback, not a static table guess. An optional
    ``readback_snapshot_path`` loads a JSON file snapshot usable by independent
    CLI invocations.
    """
    _validate_agent_id(agent_id, "agent_id")
    if readback_snapshot_path is not None:
        snapshot = load_readback_snapshot_file(readback_snapshot_path)
        set_readback_snapshot(store, snapshot)
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

    # Prevalidate issues/tasks. Detect duplicate issue rows first.
    seen_issues: dict[int, dict[str, Any]] = {}
    conflicted_issues: set[int] = set()
    for issue in github_snapshot.get("issues", []):
        number = issue.get("number")
        risk = issue.get("risk")
        group = issue.get("group")
        if number is None or risk is None or group is None:
            ambiguities.append(
                f"issue {number} missing required fields (risk/group)"
            )
            continue
        if number in seen_issues:
            prior = seen_issues[number]
            conflicts = []
            for field in ("risk", "group"):
                if prior.get(field) != issue.get(field):
                    conflicts.append(
                        f"{field}: {prior.get(field)} vs {issue.get(field)}"
                    )
            if prior.get("hotset", []) != issue.get("hotset", []):
                conflicts.append(
                    f"hotset: {prior.get('hotset', [])} vs {issue.get('hotset', [])}"
                )
            if prior.get("deps", []) != issue.get("deps", []):
                conflicts.append(
                    f"deps: {prior.get('deps', [])} vs {issue.get('deps', [])}"
                )
            if conflicts:
                ambiguities.append(
                    f"issue {number} has conflicting duplicate evidence: "
                    + ", ".join(conflicts)
                )
                conflicted_issues.add(number)
            # Identical duplicates are not an ambiguity but still must not
            # create a duplicate task (handled by the uniqueness guard and
            # deduplication below).
            continue
        seen_issues[number] = issue

    for number, issue in seen_issues.items():
        if number in conflicted_issues:
            continue
        risk = issue.get("risk")
        group = issue.get("group")
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
            # Compare every identity/runtime field, not just status/role/adapter.
            for field in (
                "status", "role", "adapter", "runtime_ref",
                "session_id", "pid", "group_label",
            ):
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
            # Do NOT overwrite seen_agents with the later row on conflict;
            # keep the first row so the conflict is recorded without last-wins.
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
        # Check existing store row for conflicts across all persisted/runtime
        # identity fields, not just role/adapter.
        existing = db.execute(
            "SELECT agent_id, adapter, role, runtime_ref, session_id, "
            "pid, group_label FROM agents WHERE agent_id = ?",
            (aid,),
        ).fetchone()
        if existing is not None:
            existing_conflicts = []
            for field in ("role", "adapter", "runtime_ref", "session_id",
                          "group_label"):
                store_val = existing[field] if field != "group_label" else existing["group_label"]
                store_val_str = str(store_val) if store_val is not None else None
                entry_val = entry.get(field)
                entry_val_str = str(entry_val) if entry_val is not None else None
                if store_val_str != entry_val_str:
                    existing_conflicts.append(
                        f"{field}: store={store_val_str} vs adapter={entry_val_str}"
                    )
            # Compare pid as int.
            store_pid = existing["pid"]
            entry_pid = entry.get("pid")
            if store_pid is not None or entry_pid is not None:
                if int(store_pid) if store_pid is not None else None != (
                    int(entry_pid) if entry_pid is not None else None
                ):
                    existing_conflicts.append(
                        f"pid: store={store_pid} vs adapter={entry_pid}"
                    )
            if existing_conflicts:
                ambiguities.append(
                    f"agent {aid} existing row conflicts with adapter listing: "
                    + ", ".join(existing_conflicts)
                )
            continue
        agents_to_insert.append(entry)

    # Prevalidate git worktrees against known agents, dispatches, and
    # matching dispatch evidence (path/branch/agent linkage field-by-field).
    known_agent_ids = set(seen_agents.keys())
    store_agent_rows = db.execute("SELECT agent_id FROM agents").fetchall()
    known_agent_ids.update(str(r["agent_id"]) for r in store_agent_rows)
    store_dispatch_agents = {
        str(r["agent_id"])
        for r in db.execute("SELECT agent_id FROM dispatches").fetchall()
    }
    # Build a map of dispatch evidence by agent_id for field-by-field comparison.
    dispatch_evidence: dict[str, list[dict[str, Any]]] = {}
    for dr in db.execute(
        "SELECT agent_id, worktree, branch FROM dispatches"
    ).fetchall():
        aid = str(dr["agent_id"])
        dispatch_evidence.setdefault(aid, []).append({
            "worktree": str(dr["worktree"]) if dr["worktree"] is not None else None,
            "branch": str(dr["branch"]) if dr["branch"] is not None else None,
        })
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
            continue
        # The agent has a dispatch: compare path/branch field-by-field.
        if wt_agent in dispatch_evidence:
            matched = False
            for de in dispatch_evidence[wt_agent]:
                if de["worktree"] == wt_path and de["branch"] == wt_branch:
                    matched = True
                    break
            if not matched:
                ambiguities.append(
                    f"git worktree {wt_path} (branch {wt_branch}) for agent "
                    f"{wt_agent} does not match any dispatch evidence "
                    f"(path/branch mismatch); ambiguous"
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