#!/usr/bin/env python3
"""Deterministic Paseo execution-mode, capacity, and cleanup policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


INLINE_TARGET_MINUTES = 15
MAX_ORCHESTRATORS_PER_ACTIVITY = 1
DEFAULT_MAX_ACTIVE_AGENTS = 4
CLEANUP_DEADLINE_SECONDS = 5 * 60


def _nonnegative(name: str, value: int | float) -> None:
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")


def classify_execution_mode(
    *, expected_minutes: int | None, same_boundary: bool
) -> dict[str, Any]:
    if expected_minutes is not None:
        _nonnegative("expected_minutes", expected_minutes)
    if (
        same_boundary
        and expected_minutes is not None
        and expected_minutes <= INLINE_TARGET_MINUTES
    ):
        mode = "inline"
        reasons = ["small-same-boundary"]
    else:
        mode = "paseo-agent"
        reasons = ["delegated-implementation-default"]
    return {
        "schema_version": 1,
        "execution_mode": mode,
        "reasons": reasons,
        "isolated_worktree_required": True,
        "room_required": mode == "paseo-agent",
    }


def capacity_report(
    *,
    orchestrators_for_activity: int,
    active_agents: int,
    max_active_agents: int = DEFAULT_MAX_ACTIVE_AGENTS,
) -> dict[str, Any]:
    for name, value in (
        ("orchestrators_for_activity", orchestrators_for_activity),
        ("active_agents", active_agents),
        ("max_active_agents", max_active_agents),
    ):
        _nonnegative(name, value)
    if max_active_agents == 0:
        raise ValueError("max_active_agents must be positive")
    return {
        "schema_version": 1,
        "orchestrator_limit": MAX_ORCHESTRATORS_PER_ACTIVITY,
        "active_agent_limit": max_active_agents,
        "can_add_orchestrator": orchestrators_for_activity < 1,
        "can_add_agent": active_agents < max_active_agents,
        "agent_slots_remaining": max(0, max_active_agents - active_agents),
    }


def cleanup_plan(
    *,
    event: str,
    seconds_since_event: int,
    worktree: str,
    branch: str | None,
    agent_id: str | None,
    agent_idle: bool,
    worktree_clean: bool,
    durable: bool,
    ownership_unambiguous: bool,
    branch_merged: bool,
) -> dict[str, Any]:
    if event not in {"merged", "stopped"}:
        raise ValueError("event must be merged or stopped")
    _nonnegative("seconds_since_event", seconds_since_event)
    if not worktree.strip() or not Path(worktree).is_absolute():
        raise ValueError("worktree must be an absolute path")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("cleanup requires an exact Paseo Agent ID")
    if branch_merged and (not isinstance(branch, str) or not branch.strip()):
        raise ValueError("merged branch cleanup requires an exact branch")

    blockers = []
    if not agent_idle:
        blockers.append("agent-not-idle")
    if not worktree_clean:
        blockers.append("worktree-not-clean")
    if not durable:
        blockers.append("work-not-durable")
    if not ownership_unambiguous:
        blockers.append("ownership-ambiguous")

    actions: list[dict[str, str]] = []
    if not blockers:
        actions.append({"action": "archive-paseo-agent", "target": agent_id})
        actions.append({"action": "archive-paseo-worktree", "target": worktree})
        if branch_merged:
            actions.append({"action": "delete-merged-remote-branch", "target": branch})

    return {
        "schema_version": 1,
        "status": "protected" if blockers else "eligible",
        "event_triggered": True,
        "deadline_seconds": CLEANUP_DEADLINE_SECONDS,
        "overdue": seconds_since_event > CLEANUP_DEADLINE_SECONDS,
        "actions": actions,
        "blockers": blockers,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    mode = subparsers.add_parser("mode")
    mode.add_argument("--expected-minutes", type=int)
    mode.add_argument("--same-boundary", action="store_true")

    capacity = subparsers.add_parser("capacity")
    capacity.add_argument("--orchestrators", type=int, required=True)
    capacity.add_argument("--active-agents", type=int, required=True)
    capacity.add_argument(
        "--max-active-agents", type=int, default=DEFAULT_MAX_ACTIVE_AGENTS
    )

    cleanup = subparsers.add_parser("cleanup-plan")
    cleanup.add_argument("--event", choices=("merged", "stopped"), required=True)
    cleanup.add_argument("--seconds-since-event", type=int, required=True)
    cleanup.add_argument("--worktree", required=True)
    cleanup.add_argument("--branch")
    cleanup.add_argument("--agent-id", required=True)
    cleanup.add_argument("--agent-idle", action="store_true")
    cleanup.add_argument("--worktree-clean", action="store_true")
    cleanup.add_argument("--durable", action="store_true")
    cleanup.add_argument("--ownership-unambiguous", action="store_true")
    cleanup.add_argument("--branch-merged", action="store_true")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "mode":
            report = classify_execution_mode(
                expected_minutes=arguments.expected_minutes,
                same_boundary=arguments.same_boundary,
            )
        elif arguments.command == "capacity":
            report = capacity_report(
                orchestrators_for_activity=arguments.orchestrators,
                active_agents=arguments.active_agents,
                max_active_agents=arguments.max_active_agents,
            )
        else:
            report = cleanup_plan(
                event=arguments.event,
                seconds_since_event=arguments.seconds_since_event,
                worktree=arguments.worktree,
                branch=arguments.branch,
                agent_id=arguments.agent_id,
                agent_idle=arguments.agent_idle,
                worktree_clean=arguments.worktree_clean,
                durable=arguments.durable,
                ownership_unambiguous=arguments.ownership_unambiguous,
                branch_merged=arguments.branch_merged,
            )
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "policy": report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
