#!/usr/bin/env python3
"""GWO-owned cleanup evidence validation and two-phase action planning."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from archive_policy import authorize_agent_archive, authorize_worktree_archive


CLEANUP_DEADLINE_SECONDS = 5 * 60
CLEANABLE_AGENT_ROLES_BY_ACTOR = {
    "repository-coordinator": {"orchestrator"},
    "orchestrator": {"intake", "implementation", "review", "monitor"},
}
ARCHIVE_ERROR_BLOCKERS = {
    "SELF_ARCHIVE_FORBIDDEN": "self-archive-forbidden",
    "ROOT_ARCHIVE_REQUIRES_SUPERVISOR": "root-archive-requires-supervisor",
    "ARCHIVE_TARGET_NOT_DIRECT_CHILD": "target-not-direct-subagent",
    "FORCE_REQUIRES_SUPERVISOR": "force-requires-supervisor",
    "AGENT_NOT_IDLE": "agent-not-idle",
    "CONTROL_WORKTREE_PROTECTED": "control-worktree-protected",
    "WORKTREE_IN_USE": "worktree-in-use",
}
IDENTITY_FIELDS = ("repository", "campaign_id", "dispatch_id")


def _required_boolean(container: dict[str, Any], field: str, prefix: str) -> bool:
    value = container.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{prefix}.{field} must be boolean")
    return value


def _identity_evidence(
    actor: dict[str, Any],
    target: dict[str, Any] | None,
    execution: dict[str, Any],
) -> tuple[list[tuple[str, str, str]], list[str]]:
    values: list[tuple[str, str, str]] = []
    missing = False
    for container in (actor, target, execution):
        if container is None:
            continue
        identity = tuple(container.get(field) for field in IDENTITY_FIELDS)
        if any(not isinstance(value, str) or not value.strip() for value in identity):
            missing = True
        else:
            values.append((identity[0], identity[1], identity[2]))
    expected_count = 2 if target is None else 3
    if missing or len(values) < expected_count:
        return values, ["cleanup-identity-evidence-missing"]
    if len(set(values)) != 1:
        return values, ["cleanup-identity-mismatch"]
    return values, []


def cleanup_plan(
    *,
    event: str,
    seconds_since_event: int,
    execution_mode: str,
    protected_control_worktree: str,
    actor: dict[str, Any],
    target: dict[str, Any] | None,
    execution: dict[str, Any],
) -> dict[str, Any]:
    if event not in {"merged", "stopped", "campaign-closed"}:
        raise ValueError("event must be merged, stopped, or campaign-closed")
    if not isinstance(seconds_since_event, int) or isinstance(
        seconds_since_event, bool
    ):
        raise ValueError("seconds_since_event must be an integer")
    if seconds_since_event < 0:
        raise ValueError("seconds_since_event must be nonnegative")
    if execution_mode not in {"inline", "paseo-agent"}:
        raise ValueError("execution_mode must be inline or paseo-agent")
    if not isinstance(actor, dict):
        raise ValueError("actor must be an object")
    if target is not None and not isinstance(target, dict):
        raise ValueError("target must be an object or null")
    if not isinstance(execution, dict):
        raise ValueError("execution must be an object")

    actor_id = actor.get("agent_id")
    actor_worktree = actor.get("worktree")
    actor_role = actor.get("role")
    target_role = target.get("role") if target is not None else None
    campaign_agent_cleanup = target_role == "orchestrator"
    worktree = execution.get("worktree")
    branch = execution.get("branch")
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise ValueError("actor requires an exact Paseo Agent ID")
    if not isinstance(actor_worktree, str) or not Path(actor_worktree).is_absolute():
        raise ValueError("actor worktree must be an absolute path")
    if (
        not isinstance(protected_control_worktree, str)
        or not Path(protected_control_worktree).is_absolute()
    ):
        raise ValueError("protected control worktree must be an absolute path")
    bound_agent_ids = execution.get("bound_agent_ids")
    if not isinstance(bound_agent_ids, list) or any(
        not isinstance(agent_id, str) or not agent_id.strip()
        for agent_id in bound_agent_ids
    ):
        raise ValueError("bound_agent_ids must be a list of exact Paseo Agent IDs")
    work_durable = _required_boolean(execution, "durable", "execution")
    agent_only = _required_boolean(execution, "agent_only", "execution")
    if campaign_agent_cleanup:
        worktree_clean = True
        branch_merged = False
    else:
        if not isinstance(worktree, str) or not Path(worktree).is_absolute():
            raise ValueError("execution worktree must be an absolute path")
        if not isinstance(branch, str) or not branch.strip():
            raise ValueError("execution branch must be exact")
        worktree_clean = _required_boolean(execution, "clean", "execution")
        branch_merged = _required_boolean(execution, "branch_merged", "execution")

    identity_values, blockers = _identity_evidence(actor, target, execution)
    cleanable_roles = CLEANABLE_AGENT_ROLES_BY_ACTOR.get(actor_role)
    if cleanable_roles is None:
        blockers.append("actor-role-not-cleanup-owner")
    if execution_mode == "inline" and target is not None:
        blockers.append("inline-agent-target-forbidden")
    if execution_mode == "paseo-agent" and target is None:
        blockers.append("delegated-agent-target-required")
    if campaign_agent_cleanup:
        if event != "campaign-closed":
            blockers.append("campaign-close-event-required")
        if not agent_only:
            blockers.append("campaign-orchestrator-agent-only-evidence-required")
        if worktree is not None or branch is not None or bound_agent_ids:
            blockers.append("campaign-orchestrator-execution-resource-forbidden")
        if worktree in {actor_worktree, protected_control_worktree}:
            blockers.append("control-worktree-protected")
        if branch == "dev":
            blockers.append("integration-branch-protected")
    else:
        if event == "campaign-closed":
            blockers.append("campaign-close-target-required")
        if agent_only:
            blockers.append("agent-only-evidence-forbidden")

    target_archived = False
    target_requires_readback = False
    if target is not None:
        target_id = target.get("agent_id")
        target_idle = _required_boolean(target, "idle", "target")
        target_archived = _required_boolean(target, "archived", "target")
        agent_decision = authorize_agent_archive(
            actor_kind="agent",
            actor_agent_id=actor_id,
            target_agent_id=target_id,
            target_parent_agent_id=target.get("parent_agent_id"),
            target_idle=target_idle or target_archived,
            force=False,
        )
        blockers.extend(
            ARCHIVE_ERROR_BLOCKERS[error] for error in agent_decision["errors"]
        )
        if target.get("relationship") != "subagent":
            blockers.append("target-relationship-not-subagent")
        if actor_role == "orchestrator" and target_role == "orchestrator":
            blockers.append("target-role-orchestrator")
        elif cleanable_roles is None or target_role not in cleanable_roles:
            blockers.append("target-role-not-cleanable")
        if not target_archived and not campaign_agent_cleanup:
            if target_id not in bound_agent_ids:
                blockers.append("target-worktree-binding-missing")
            target_requires_readback = bound_agent_ids == [target_id]

    if not campaign_agent_cleanup:
        worktree_decision = authorize_worktree_archive(
            actor_kind="agent",
            actor_agent_id=actor_id,
            actor_worktree=actor_worktree,
            protected_control_worktree=protected_control_worktree,
            target_worktree=worktree,
            bound_agent_ids=bound_agent_ids,
        )
        for error in worktree_decision["errors"]:
            if error == "WORKTREE_IN_USE" and target_requires_readback:
                continue
            blockers.append(ARCHIVE_ERROR_BLOCKERS[error])
        if branch == "dev":
            blockers.append("integration-branch-protected")
        elif not branch.startswith("work/issue-"):
            blockers.append("execution-branch-not-work-issue")
        if not worktree_clean:
            blockers.append("worktree-not-clean")
    if not work_durable:
        blockers.append("work-not-durable")
    if event == "merged" and not campaign_agent_cleanup and not branch_merged:
        blockers.append("merged-event-without-merged-branch")

    terminal_receipt = execution.get("terminal_receipt")
    if not isinstance(terminal_receipt, dict):
        blockers.append("terminal-receipt-missing")
    else:
        receipt_identity = tuple(
            terminal_receipt.get(field) for field in IDENTITY_FIELDS
        )
        target_id = target.get("agent_id") if target is not None else None
        if target_role == "orchestrator":
            expected_terminal_event = "CAMPAIGN_CLOSED"
            expected_terminal_sender = target_id
        elif event == "stopped":
            expected_terminal_event = "STOPPED"
            expected_terminal_sender = target_id or actor_id
        else:
            expected_terminal_event = "COMPLETED"
            expected_terminal_sender = actor_id
        signal_id = terminal_receipt.get("signal_id")
        receipt_valid = (
            identity_values
            and receipt_identity == identity_values[0]
            and terminal_receipt.get("event_type") == expected_terminal_event
            and terminal_receipt.get("sender_agent_id") == expected_terminal_sender
            and isinstance(signal_id, str)
            and bool(signal_id.strip())
            and terminal_receipt.get("read_back") is True
        )
        if not receipt_valid:
            blockers.append("terminal-receipt-invalid")

    blockers = sorted(set(blockers))
    actions: list[dict[str, str]] = []
    next_required_readback = None
    if not blockers:
        if target is not None and not target_archived:
            actions.append(
                {"action": "archive-paseo-agent", "target": target["agent_id"]}
            )
            next_required_readback = (
                "target-agent-archived"
                if campaign_agent_cleanup
                else "target-agent-archived-and-worktree-unbound"
            )
        elif campaign_agent_cleanup:
            next_required_readback = None
        else:
            actions.append({"action": "archive-paseo-worktree", "target": worktree})
            if branch_merged:
                actions.append(
                    {"action": "delete-merged-remote-branch", "target": branch}
                )

    return {
        "schema_version": 2,
        "status": "protected" if blockers else "eligible",
        "event_triggered": True,
        "automatic_execution": not blockers and bool(actions),
        "cleanup_complete": not blockers and campaign_agent_cleanup and target_archived,
        "deadline_seconds": CLEANUP_DEADLINE_SECONDS,
        "overdue": seconds_since_event > CLEANUP_DEADLINE_SECONDS,
        "actions": actions,
        "next_required_readback": next_required_readback,
        "blockers": blockers,
    }
