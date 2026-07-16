#!/usr/bin/env python3
"""Deterministic execution-lane, capacity, and cleanup policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


INLINE_TARGET_MINUTES = 15
MAX_VISIBLE_ORCHESTRATORS_PER_ACTIVITY = 1
MAX_VISIBLE_WORKERS_GLOBAL = 3
MAX_SUBAGENTS_PER_ORCHESTRATOR = 4
CLEANUP_DEADLINE_SECONDS = 5 * 60
TASK_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _nonnegative(name: str, value: int | float) -> None:
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")


def classify_execution_lane(
    *,
    expected_minutes: int | None,
    same_boundary: bool,
    restart_persistence: bool = False,
    manual_ui_or_login: bool = False,
    prolonged_observation: bool = False,
    independent_visible_context: bool = False,
) -> dict[str, Any]:
    """Choose the lightest lane that satisfies explicit persistence needs."""

    if expected_minutes is not None:
        _nonnegative("expected_minutes", expected_minutes)
    visible_reasons = [
        name
        for name, required in (
            ("restart-persistence", restart_persistence),
            ("manual-ui-or-login", manual_ui_or_login),
            ("prolonged-observation", prolonged_observation),
            ("independent-visible-context", independent_visible_context),
        )
        if required
    ]
    if visible_reasons:
        lane = "visible-worker"
        reasons = visible_reasons
    elif (
        same_boundary
        and expected_minutes is not None
        and expected_minutes <= INLINE_TARGET_MINUTES
    ):
        lane = "inline"
        reasons = ["small-same-boundary"]
    else:
        lane = "subagent"
        reasons = ["bounded-implementation-default"]
    return {
        "schema_version": 1,
        "lane": lane,
        "reasons": reasons,
        "isolated_worktree_required": True,
        "visible_creation_guard_required": lane == "visible-worker",
    }


def capacity_report(
    *,
    visible_orchestrators_for_activity: int,
    visible_workers_global: int,
    active_subagents: int,
    host_subagent_slots: int,
) -> dict[str, Any]:
    """Report admissions without converting one exhausted lane into another."""

    for name, value in (
        ("visible_orchestrators_for_activity", visible_orchestrators_for_activity),
        ("visible_workers_global", visible_workers_global),
        ("active_subagents", active_subagents),
        ("host_subagent_slots", host_subagent_slots),
    ):
        _nonnegative(name, value)
    effective_subagent_limit = min(
        MAX_SUBAGENTS_PER_ORCHESTRATOR, host_subagent_slots
    )
    return {
        "schema_version": 1,
        "visible_orchestrator_limit": MAX_VISIBLE_ORCHESTRATORS_PER_ACTIVITY,
        "visible_worker_limit": MAX_VISIBLE_WORKERS_GLOBAL,
        "configured_subagent_limit": MAX_SUBAGENTS_PER_ORCHESTRATOR,
        "effective_subagent_limit": effective_subagent_limit,
        "can_add_orchestrator": (
            visible_orchestrators_for_activity
            < MAX_VISIBLE_ORCHESTRATORS_PER_ACTIVITY
        ),
        "can_add_visible_worker": (
            visible_workers_global < MAX_VISIBLE_WORKERS_GLOBAL
        ),
        "can_add_subagent": active_subagents < effective_subagent_limit,
        "visible_worker_slots_remaining": max(
            0, MAX_VISIBLE_WORKERS_GLOBAL - visible_workers_global
        ),
        "subagent_slots_remaining": max(
            0, effective_subagent_limit - active_subagents
        ),
    }


def cleanup_plan(
    *,
    event: str,
    seconds_since_event: int,
    worktree: str,
    branch: str | None,
    visible_task_id: str | None,
    worktree_clean: bool,
    durable: bool,
    ownership_unambiguous: bool,
    active_editor: bool,
    branch_merged: bool,
    visible_worker: bool,
) -> dict[str, Any]:
    """Plan event-triggered cleanup while preserving uncertain or useful WIP."""

    if event not in {"merged", "stopped"}:
        raise ValueError("event must be merged or stopped")
    _nonnegative("seconds_since_event", seconds_since_event)
    if not worktree.strip() or not Path(worktree).is_absolute():
        raise ValueError("worktree must be an absolute path")
    if branch_merged and (not isinstance(branch, str) or not branch.strip()):
        raise ValueError("merged branch cleanup requires an exact branch")
    if visible_worker and (
        not isinstance(visible_task_id, str)
        or not TASK_ID_RE.fullmatch(visible_task_id)
    ):
        raise ValueError("visible Worker cleanup requires an exact Task ID")
    blockers = []
    if not worktree_clean:
        blockers.append("worktree-not-clean")
    if not durable:
        blockers.append("work-not-durable")
    if not ownership_unambiguous:
        blockers.append("ownership-ambiguous")
    if active_editor:
        blockers.append("active-editor")

    actions: list[dict[str, str]] = []
    if not blockers:
        actions.append({"action": "remove-worktree", "target": worktree})
        if branch_merged:
            actions.append(
                {"action": "delete-merged-local-branch", "target": branch}
            )
        if visible_worker:
            actions.append(
                {
                    "action": "request-human-visible-task-archive",
                    "target": visible_task_id,
                }
            )
    overdue = seconds_since_event > CLEANUP_DEADLINE_SECONDS
    return {
        "schema_version": 1,
        "status": "protected" if blockers else "eligible",
        "event_triggered": True,
        "deadline_seconds": CLEANUP_DEADLINE_SECONDS,
        "overdue": overdue,
        "actions": actions,
        "automatic_task_archive": False,
        "blockers": blockers,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    lane = subparsers.add_parser("lane")
    lane.add_argument("--expected-minutes", type=int)
    lane.add_argument("--same-boundary", action="store_true")
    lane.add_argument("--restart-persistence", action="store_true")
    lane.add_argument("--manual-ui-or-login", action="store_true")
    lane.add_argument("--prolonged-observation", action="store_true")
    lane.add_argument("--independent-visible-context", action="store_true")

    capacity = subparsers.add_parser("capacity")
    capacity.add_argument("--visible-orchestrators", type=int, required=True)
    capacity.add_argument("--visible-workers", type=int, required=True)
    capacity.add_argument("--active-subagents", type=int, required=True)
    capacity.add_argument("--host-subagent-slots", type=int, required=True)

    cleanup = subparsers.add_parser("cleanup-plan")
    cleanup.add_argument("--event", choices=("merged", "stopped"), required=True)
    cleanup.add_argument("--seconds-since-event", type=int, required=True)
    cleanup.add_argument("--worktree", required=True)
    cleanup.add_argument("--branch")
    cleanup.add_argument("--visible-task-id")
    cleanup.add_argument("--worktree-clean", action="store_true")
    cleanup.add_argument("--durable", action="store_true")
    cleanup.add_argument("--ownership-unambiguous", action="store_true")
    cleanup.add_argument("--active-editor", action="store_true")
    cleanup.add_argument("--branch-merged", action="store_true")
    cleanup.add_argument("--visible-worker", action="store_true")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "lane":
            report = classify_execution_lane(
                expected_minutes=arguments.expected_minutes,
                same_boundary=arguments.same_boundary,
                restart_persistence=arguments.restart_persistence,
                manual_ui_or_login=arguments.manual_ui_or_login,
                prolonged_observation=arguments.prolonged_observation,
                independent_visible_context=arguments.independent_visible_context,
            )
        elif arguments.command == "capacity":
            report = capacity_report(
                visible_orchestrators_for_activity=arguments.visible_orchestrators,
                visible_workers_global=arguments.visible_workers,
                active_subagents=arguments.active_subagents,
                host_subagent_slots=arguments.host_subagent_slots,
            )
        else:
            report = cleanup_plan(
                event=arguments.event,
                seconds_since_event=arguments.seconds_since_event,
                worktree=arguments.worktree,
                branch=arguments.branch,
                visible_task_id=arguments.visible_task_id,
                worktree_clean=arguments.worktree_clean,
                durable=arguments.durable,
                ownership_unambiguous=arguments.ownership_unambiguous,
                active_editor=arguments.active_editor,
                branch_merged=arguments.branch_merged,
                visible_worker=arguments.visible_worker,
            )
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "policy": report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
