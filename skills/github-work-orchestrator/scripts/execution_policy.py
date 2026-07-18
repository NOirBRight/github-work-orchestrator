#!/usr/bin/env python3
"""Deterministic Paseo execution-mode, capacity, and cleanup policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cleanup_policy import cleanup_plan
from contract_schema import DEFAULT_MAX_ACTIVE_AGENTS_PER_CAMPAIGN
from hotset_policy import (
    entries_overlap,
    normalize_hotset_entry,
)


INLINE_TARGET_MINUTES = 15
MAX_REPOSITORY_COORDINATORS_PER_REPOSITORY = 1
MAX_CAMPAIGN_ORCHESTRATORS_PER_CAMPAIGN = 1


def _nonnegative(name: str, value: int | float) -> None:
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")


def _nonnegative_integer(name: str, value: Any) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    _nonnegative(name, value)


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
    campaign_id: str,
    repository_coordinators: int,
    campaign_orchestrators_for_campaign: int,
    active_agents_for_campaign: int,
    active_agents_global: int,
    max_active_agents_global: int,
    max_active_agents_per_campaign: int = DEFAULT_MAX_ACTIVE_AGENTS_PER_CAMPAIGN,
) -> dict[str, Any]:
    if not isinstance(campaign_id, str) or not campaign_id.strip():
        raise ValueError("campaign_id must be exact")
    for name, value in (
        ("repository_coordinators", repository_coordinators),
        ("campaign_orchestrators_for_campaign", campaign_orchestrators_for_campaign),
        ("active_agents_for_campaign", active_agents_for_campaign),
        ("active_agents_global", active_agents_global),
        ("max_active_agents_global", max_active_agents_global),
        ("max_active_agents_per_campaign", max_active_agents_per_campaign),
    ):
        _nonnegative_integer(name, value)
    if max_active_agents_global == 0 or max_active_agents_per_campaign == 0:
        raise ValueError("active Agent limits must be positive")
    if active_agents_for_campaign > active_agents_global:
        raise ValueError("Campaign Agent count cannot exceed the global Agent count")
    if campaign_orchestrators_for_campaign > active_agents_for_campaign:
        raise ValueError(
            "Campaign Orchestrator count cannot exceed the Campaign Agent count"
        )
    if repository_coordinators > active_agents_global:
        raise ValueError(
            "Repository Coordinator count cannot exceed the global Agent count"
        )
    if repository_coordinators + active_agents_for_campaign > active_agents_global:
        raise ValueError(
            "Repository Coordinator and Campaign Agent counts cannot exceed the "
            "global Agent count"
        )
    if active_agents_global > max_active_agents_global:
        raise ValueError("global Agent count exceeds the configured limit")
    if active_agents_for_campaign > max_active_agents_per_campaign:
        raise ValueError("Campaign Agent count exceeds the configured limit")
    if active_agents_for_campaign and campaign_orchestrators_for_campaign == 0:
        raise ValueError("active Campaign Agents require a Campaign Orchestrator")
    repository_coordinator_conflict = repository_coordinators > 1
    campaign_orchestrator_conflict = campaign_orchestrators_for_campaign > 1
    campaign_has_capacity = active_agents_for_campaign < max_active_agents_per_campaign
    global_has_capacity = active_agents_global < max_active_agents_global
    repository_ready = (
        repository_coordinators == 1 and not repository_coordinator_conflict
    )
    admission_ready = (
        repository_ready
        and not campaign_orchestrator_conflict
        and campaign_has_capacity
        and global_has_capacity
    )
    return {
        "schema_version": 2,
        "campaign_id": campaign_id,
        "repository_coordinator_limit": MAX_REPOSITORY_COORDINATORS_PER_REPOSITORY,
        "campaign_orchestrator_limit": MAX_CAMPAIGN_ORCHESTRATORS_PER_CAMPAIGN,
        "campaign_agent_limit": max_active_agents_per_campaign,
        "global_agent_limit": max_active_agents_global,
        "repository_coordinator_conflict": repository_coordinator_conflict,
        "campaign_orchestrator_conflict": campaign_orchestrator_conflict,
        "can_add_repository_coordinator": (
            repository_coordinators < 1 and global_has_capacity
        ),
        "can_add_campaign_orchestrator": (
            admission_ready and campaign_orchestrators_for_campaign < 1
        ),
        "can_add_campaign_agent": (
            admission_ready and campaign_orchestrators_for_campaign == 1
        ),
        "campaign_slots_remaining": max(
            0, max_active_agents_per_campaign - active_agents_for_campaign
        ),
        "global_slots_remaining": max(
            0, max_active_agents_global - active_agents_global
        ),
    }


def campaign_concurrency_report(
    *,
    campaign_id: str,
    requested_hotset: list[str],
    active_hotsets: dict[str, list[str]],
    integration_lease_holder: str | None,
    pinned_dev_sha: str,
    current_dev_sha: str,
    case_sensitive_paths: bool | None = None,
) -> dict[str, Any]:
    """Report parallel-execution and serialized-integration eligibility."""

    if not isinstance(campaign_id, str) or not campaign_id.strip():
        raise ValueError("campaign_id must be exact")
    if not requested_hotset or any(
        not isinstance(entry, str) or not entry.strip() for entry in requested_hotset
    ):
        raise ValueError("requested_hotset must be a nonempty text list")
    if not isinstance(active_hotsets, dict) or any(
        not isinstance(active_campaign, str)
        or not active_campaign.strip()
        or not isinstance(hotset, list)
        or any(not isinstance(entry, str) or not entry.strip() for entry in hotset)
        for active_campaign, hotset in active_hotsets.items()
    ):
        raise ValueError("active_hotsets must map Campaign IDs to text lists")
    for name, sha in (
        ("pinned_dev_sha", pinned_dev_sha),
        ("current_dev_sha", current_dev_sha),
    ):
        if (
            not isinstance(sha, str)
            or len(sha) != 40
            or any(character not in "0123456789abcdef" for character in sha)
        ):
            raise ValueError(f"{name} must be lowercase 40-hex")
    if not isinstance(case_sensitive_paths, bool):
        raise ValueError("case_sensitive_paths readback must be boolean")

    normalized_requested_hotset = [
        normalize_hotset_entry(entry) for entry in requested_hotset
    ]
    normalized_active_hotsets = {
        active_campaign: [normalize_hotset_entry(entry) for entry in hotset]
        for active_campaign, hotset in active_hotsets.items()
    }

    conflicting_campaigns = sorted(
        active_campaign
        for active_campaign, hotset in normalized_active_hotsets.items()
        if active_campaign != campaign_id
        and any(
            entries_overlap(requested, active, case_sensitive=case_sensitive_paths)
            for requested in normalized_requested_hotset
            for active in hotset
        )
    )
    lease_held_by_other = (
        integration_lease_holder is not None and integration_lease_holder != campaign_id
    )
    integration_lease_held = integration_lease_holder == campaign_id
    dev_advanced = pinned_dev_sha != current_dev_sha
    blockers = []
    if conflicting_campaigns:
        blockers.append("hotset-conflict")
    if lease_held_by_other:
        blockers.append("integration-lease-held-by-other")
    elif not integration_lease_held:
        blockers.append("integration-lease-not-held")
    if dev_advanced:
        blockers.append("dev-advanced")
    can_execute = not conflicting_campaigns
    return {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "case_sensitive_paths": case_sensitive_paths,
        "can_execute": can_execute,
        "can_merge_dev": can_execute and integration_lease_held and not dev_advanced,
        "integration_lease_available": not lease_held_by_other,
        "integration_lease_held": integration_lease_held,
        "requires_base_refresh": dev_advanced,
        "conflicting_campaigns": conflicting_campaigns,
        "blockers": blockers,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    mode = subparsers.add_parser("mode")
    mode.add_argument("--expected-minutes", type=int)
    mode.add_argument("--same-boundary", action="store_true")

    capacity = subparsers.add_parser("capacity")
    capacity.add_argument("--campaign-id", required=True)
    capacity.add_argument("--repository-coordinators", type=int, required=True)
    capacity.add_argument("--campaign-orchestrators", type=int, required=True)
    capacity.add_argument("--campaign-active-agents", type=int, required=True)
    capacity.add_argument("--global-active-agents", type=int, required=True)
    capacity.add_argument("--global-max-active-agents", type=int, required=True)
    capacity.add_argument(
        "--campaign-max-active-agents",
        type=int,
        default=DEFAULT_MAX_ACTIVE_AGENTS_PER_CAMPAIGN,
    )

    concurrency = subparsers.add_parser("concurrency")
    concurrency.add_argument("--campaign-id", required=True)
    concurrency.add_argument("--requested-hotset", action="append", required=True)
    concurrency.add_argument("--active-hotsets-json", type=Path, required=True)
    concurrency.add_argument("--integration-lease-holder")
    concurrency.add_argument("--pinned-dev-sha", required=True)
    concurrency.add_argument("--current-dev-sha", required=True)
    case_semantics = concurrency.add_mutually_exclusive_group(required=True)
    case_semantics.add_argument(
        "--case-sensitive-paths",
        action="store_const",
        const=True,
        dest="case_sensitive_paths",
    )
    case_semantics.add_argument(
        "--case-insensitive-paths",
        action="store_const",
        const=False,
        dest="case_sensitive_paths",
    )

    cleanup = subparsers.add_parser("cleanup-plan")
    cleanup.add_argument(
        "--event", choices=("merged", "stopped", "campaign-closed"), required=True
    )
    cleanup.add_argument("--seconds-since-event", type=int, required=True)
    cleanup.add_argument(
        "--execution-mode", choices=("inline", "paseo-agent"), required=True
    )
    cleanup.add_argument("--actor-agent-id", required=True)
    cleanup.add_argument("--actor-role", required=True)
    cleanup.add_argument("--actor-worktree", required=True)
    cleanup.add_argument("--actor-repository")
    cleanup.add_argument("--actor-campaign-id")
    cleanup.add_argument("--actor-dispatch-id")
    cleanup.add_argument("--protected-control-worktree", required=True)
    cleanup.add_argument("--target-agent-id")
    cleanup.add_argument("--target-parent-agent-id")
    cleanup.add_argument("--target-relationship")
    cleanup.add_argument("--target-role")
    cleanup.add_argument("--target-repository")
    cleanup.add_argument("--target-campaign-id")
    cleanup.add_argument("--target-dispatch-id")
    cleanup.add_argument("--target-agent-idle", action="store_true")
    cleanup.add_argument("--target-agent-archived", action="store_true")
    cleanup.add_argument("--target-worktree")
    cleanup.add_argument("--branch")
    cleanup.add_argument("--execution-repository")
    cleanup.add_argument("--execution-campaign-id")
    cleanup.add_argument("--execution-dispatch-id")
    cleanup.add_argument("--worktree-agent-id", action="append", default=[])
    cleanup.add_argument("--worktree-clean", action="store_true")
    cleanup.add_argument("--work-durable", action="store_true")
    cleanup.add_argument("--branch-merged", action="store_true")
    cleanup.add_argument("--agent-only", action="store_true")
    cleanup.add_argument("--terminal-event")
    cleanup.add_argument("--terminal-signal-id")
    cleanup.add_argument("--terminal-sender-agent-id")
    cleanup.add_argument("--terminal-repository")
    cleanup.add_argument("--terminal-campaign-id")
    cleanup.add_argument("--terminal-dispatch-id")
    cleanup.add_argument("--terminal-read-back", action="store_true")
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
                campaign_id=arguments.campaign_id,
                repository_coordinators=arguments.repository_coordinators,
                campaign_orchestrators_for_campaign=arguments.campaign_orchestrators,
                active_agents_for_campaign=arguments.campaign_active_agents,
                active_agents_global=arguments.global_active_agents,
                max_active_agents_global=arguments.global_max_active_agents,
                max_active_agents_per_campaign=arguments.campaign_max_active_agents,
            )
        elif arguments.command == "concurrency":
            report = campaign_concurrency_report(
                campaign_id=arguments.campaign_id,
                requested_hotset=arguments.requested_hotset,
                active_hotsets=json.loads(
                    arguments.active_hotsets_json.read_text(encoding="utf-8")
                ),
                integration_lease_holder=arguments.integration_lease_holder,
                pinned_dev_sha=arguments.pinned_dev_sha,
                current_dev_sha=arguments.current_dev_sha,
                case_sensitive_paths=arguments.case_sensitive_paths,
            )
        else:
            target = None
            if arguments.target_agent_id is not None:
                target = {
                    "agent_id": arguments.target_agent_id,
                    "parent_agent_id": arguments.target_parent_agent_id,
                    "relationship": arguments.target_relationship,
                    "role": arguments.target_role,
                    "idle": arguments.target_agent_idle,
                    "archived": arguments.target_agent_archived,
                    "repository": arguments.target_repository,
                    "campaign_id": arguments.target_campaign_id,
                    "dispatch_id": arguments.target_dispatch_id,
                }
            report = cleanup_plan(
                event=arguments.event,
                seconds_since_event=arguments.seconds_since_event,
                execution_mode=arguments.execution_mode,
                protected_control_worktree=arguments.protected_control_worktree,
                actor={
                    "agent_id": arguments.actor_agent_id,
                    "role": arguments.actor_role,
                    "worktree": arguments.actor_worktree,
                    "repository": arguments.actor_repository,
                    "campaign_id": arguments.actor_campaign_id,
                    "dispatch_id": arguments.actor_dispatch_id,
                },
                target=target,
                execution={
                    "worktree": arguments.target_worktree,
                    "branch": arguments.branch,
                    "clean": arguments.worktree_clean,
                    "durable": arguments.work_durable,
                    "bound_agent_ids": arguments.worktree_agent_id,
                    "branch_merged": arguments.branch_merged,
                    "agent_only": arguments.agent_only,
                    "repository": arguments.execution_repository,
                    "campaign_id": arguments.execution_campaign_id,
                    "dispatch_id": arguments.execution_dispatch_id,
                    "terminal_receipt": (
                        {
                            "event_type": arguments.terminal_event,
                            "signal_id": arguments.terminal_signal_id,
                            "sender_agent_id": arguments.terminal_sender_agent_id,
                            "repository": arguments.terminal_repository,
                            "campaign_id": arguments.terminal_campaign_id,
                            "dispatch_id": arguments.terminal_dispatch_id,
                            "read_back": arguments.terminal_read_back,
                        }
                        if arguments.terminal_event is not None
                        else None
                    ),
                },
            )
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "policy": report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
