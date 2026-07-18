#!/usr/bin/env python3
"""Pure Coordinator-loop timing, heartbeat, and stale-recovery policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from contract_schema import (
    DEFAULT_COORDINATOR_WAIT_SECONDS,
    DEFAULT_MAX_ACTIVE_AGENTS_PER_CAMPAIGN,
    DEFAULT_MAX_DISPATCH_ATTEMPTS_PER_ISSUE,
    DEFAULT_STALE_RECHECK_SECONDS,
    DEFAULT_WORKER_HEARTBEAT_SECONDS,
    DEFAULT_WORKER_STALE_SECONDS,
)

DEFAULTS = {
    "max_active_agents_per_campaign": DEFAULT_MAX_ACTIVE_AGENTS_PER_CAMPAIGN,
    "max_dispatch_attempts_per_issue": DEFAULT_MAX_DISPATCH_ATTEMPTS_PER_ISSUE,
    "wait_timeout_seconds": DEFAULT_COORDINATOR_WAIT_SECONDS,
    "worker_heartbeat_target_seconds": DEFAULT_WORKER_HEARTBEAT_SECONDS,
    "worker_stale_after_seconds": DEFAULT_WORKER_STALE_SECONDS,
    "stale_recheck_cooldown_seconds": DEFAULT_STALE_RECHECK_SECONDS,
}


def _positive_integer(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def resolve_orchestration_config(preferences: dict[str, Any]) -> dict[str, int]:
    if not isinstance(preferences, dict):
        raise ValueError("orchestration preferences must be an object")
    supplied = preferences.get("orchestration", {})
    if not isinstance(supplied, dict):
        raise ValueError("orchestration must be an object")
    unknown = sorted(set(supplied) - set(DEFAULTS))
    if unknown:
        raise ValueError("unknown orchestration settings: " + ", ".join(unknown))
    resolved = {
        name: _positive_integer(name, supplied.get(name, default))
        for name, default in DEFAULTS.items()
    }
    if (
        resolved["max_dispatch_attempts_per_issue"]
        > DEFAULT_MAX_DISPATCH_ATTEMPTS_PER_ISSUE
    ):
        raise ValueError("max_dispatch_attempts_per_issue must not exceed 3")
    if resolved["wait_timeout_seconds"] > 60:
        raise ValueError("wait_timeout_seconds must not exceed 60")
    if resolved["worker_stale_after_seconds"] < DEFAULT_WORKER_STALE_SECONDS:
        raise ValueError("worker_stale_after_seconds must be at least 900")
    if resolved["stale_recheck_cooldown_seconds"] < DEFAULT_STALE_RECHECK_SECONDS:
        raise ValueError("stale_recheck_cooldown_seconds must be at least 900")
    if (
        resolved["worker_stale_after_seconds"]
        < resolved["worker_heartbeat_target_seconds"]
    ):
        raise ValueError(
            "worker_stale_after_seconds must be at least worker_heartbeat_target_seconds"
        )
    return resolved


def heartbeat_plan(
    *,
    phase_boundary: bool,
    material_progress: bool,
    safe_to_post: bool,
    terminal: bool,
    seconds_since_runtime_signal: int,
    heartbeat_target_seconds: int = DEFAULTS["worker_heartbeat_target_seconds"],
) -> dict[str, Any]:
    _nonnegative("seconds_since_runtime_signal", seconds_since_runtime_signal)
    _positive_integer("heartbeat_target_seconds", heartbeat_target_seconds)
    for name, value in (
        ("phase_boundary", phase_boundary),
        ("material_progress", material_progress),
        ("safe_to_post", safe_to_post),
        ("terminal", terminal),
    ):
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be boolean")
    if terminal:
        action = "no-signal-after-terminal"
    elif not safe_to_post:
        action = "continue-without-signal"
    elif material_progress:
        action = "post-progress"
    elif phase_boundary or seconds_since_runtime_signal >= heartbeat_target_seconds:
        action = "post-heartbeat"
    else:
        action = "continue-without-signal"
    return {
        "schema_version": 1,
        "action": action,
        "heartbeat_target_seconds": heartbeat_target_seconds,
        "heartbeat_is_advisory": True,
    }


def stale_recovery_plan(
    *,
    seconds_since_runtime_signal: int,
    seconds_since_last_inspection: int,
    agent_status: str,
    timeline_active: bool,
    identity_matches: bool,
    permission_pending: bool,
    terminal_event: bool,
    recovery_prompt_sent: bool,
    stale_after_seconds: int = DEFAULTS["worker_stale_after_seconds"],
    recheck_cooldown_seconds: int = DEFAULTS["stale_recheck_cooldown_seconds"],
) -> dict[str, Any]:
    _nonnegative("seconds_since_runtime_signal", seconds_since_runtime_signal)
    _nonnegative("seconds_since_last_inspection", seconds_since_last_inspection)
    _positive_integer("stale_after_seconds", stale_after_seconds)
    _positive_integer("recheck_cooldown_seconds", recheck_cooldown_seconds)
    if stale_after_seconds < DEFAULT_WORKER_STALE_SECONDS:
        raise ValueError("stale_after_seconds must be at least 900")
    if recheck_cooldown_seconds < DEFAULT_STALE_RECHECK_SECONDS:
        raise ValueError("recheck_cooldown_seconds must be at least 900")
    statuses = {"initializing", "running", "idle", "error", "closed", "missing"}
    if agent_status not in statuses:
        raise ValueError("unsupported agent_status")
    for name, value in (
        ("timeline_active", timeline_active),
        ("identity_matches", identity_matches),
        ("permission_pending", permission_pending),
        ("terminal_event", terminal_event),
        ("recovery_prompt_sent", recovery_prompt_sent),
    ):
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be boolean")

    actions: list[str] = []
    status = "inspect"
    if not identity_matches:
        status = "blocked"
        actions.append("post-blocked-identity-mismatch")
    elif terminal_event:
        status = "terminal"
    elif seconds_since_runtime_signal < stale_after_seconds:
        status = "wait"
    elif seconds_since_last_inspection < recheck_cooldown_seconds:
        status = "wait"
    elif permission_pending:
        actions.append("inspect-pending-permission")
    elif agent_status in {"initializing", "running"}:
        if not timeline_active:
            actions.append("record-suspected-stalled-checkpoint")
    elif agent_status == "idle":
        if not recovery_prompt_sent:
            actions.append("send-one-recovery-prompt")
    elif agent_status in {"error", "closed"}:
        actions.append("preserve-wip-and-evaluate-successor")
    else:
        status = "escalated"
        actions.append("post-escalation-agent-missing")
    return {
        "schema_version": 1,
        "status": status,
        "actions": actions,
        "replacement_authorized": False,
        "cancellation_authorized": False,
        "archive_authorized": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    config = subparsers.add_parser("resolve-config")
    config.add_argument("--preferences", type=Path, required=True)
    heartbeat = subparsers.add_parser("heartbeat-plan")
    heartbeat.add_argument("--snapshot", type=Path, required=True)
    stale = subparsers.add_parser("stale-recovery")
    stale.add_argument("--snapshot", type=Path, required=True)
    return parser


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("input must be a JSON object")
    return payload


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "resolve-config":
            report = resolve_orchestration_config(_read_json(arguments.preferences))
        elif arguments.command == "heartbeat-plan":
            report = heartbeat_plan(**_read_json(arguments.snapshot))
        else:
            report = stale_recovery_plan(**_read_json(arguments.snapshot))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "policy": report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
