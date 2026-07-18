#!/usr/bin/env python3
"""GWO-owned cleanup evidence validation and staged action planning."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from archive_policy import authorize_agent_archive, authorize_worktree_archive


CLEANUP_DEADLINE_SECONDS = 5 * 60
TARGET_KINDS = {"worker", "campaign", "ephemeral"}
RESOURCE_KINDS = {"issue-worktree", "campaign-control", "none"}
WORKER_TARGET_ROLES = {"intake", "implementation", "review", "monitor"}
EPHEMERAL_TARGET_ROLES = {"intake", "implementation", "review", "monitor"}
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


def _string_list(container: dict[str, Any], field: str, prefix: str) -> list[str]:
    value = container.get(field)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{prefix}.{field} must be a list of exact Agent IDs")
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
    target_kind: str,
    resource_kind: str,
) -> dict[str, Any]:
    if event not in {"merged", "stopped", "campaign-closed"}:
        raise ValueError("event must be merged, stopped, or campaign-closed")
    if not isinstance(seconds_since_event, int) or isinstance(seconds_since_event, bool):
        raise ValueError("seconds_since_event must be an integer")
    if seconds_since_event < 0:
        raise ValueError("seconds_since_event must be nonnegative")
    if execution_mode not in {"inline", "paseo-agent"}:
        raise ValueError("execution_mode must be inline or paseo-agent")
    if target_kind not in TARGET_KINDS:
        raise ValueError("target_kind must be worker, campaign, or ephemeral")
    if resource_kind not in RESOURCE_KINDS:
        raise ValueError(
            "resource_kind must be issue-worktree, campaign-control, or none"
        )
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

    bound_agent_ids = _string_list(execution, "bound_agent_ids", "execution")
    remaining_child_agent_ids = _string_list(
        execution, "remaining_child_agent_ids", "execution"
    )
    work_durable = _required_boolean(execution, "durable", "execution")
    agent_only = _required_boolean(execution, "agent_only", "execution")
    resource_archived = _required_boolean(execution, "resource_archived", "execution")
    branch_deleted = _required_boolean(execution, "branch_deleted", "execution")
    identity_values, blockers = _identity_evidence(actor, target, execution)

    if target_kind == "campaign":
        allowed_actor_role = "repository-coordinator"
        cleanable_roles = {"orchestrator"}
    elif target_kind == "worker":
        allowed_actor_role = "orchestrator"
        cleanable_roles = WORKER_TARGET_ROLES
    else:
        allowed_actor_role = actor_role if actor_role in {
            "repository-coordinator",
            "orchestrator",
        } else None
        cleanable_roles = EPHEMERAL_TARGET_ROLES
    if actor_role != allowed_actor_role:
        blockers.append("actor-role-not-cleanup-owner")
    if execution_mode == "inline" and target is not None:
        blockers.append("inline-agent-target-forbidden")
    if execution_mode == "paseo-agent" and target is None:
        blockers.append("delegated-agent-target-required")

    if target_kind == "campaign":
        if target is None or target_role != "orchestrator":
            blockers.append("campaign-target-role-required")
        if actor_role != "repository-coordinator":
            blockers.append("campaign-cleanup-requires-repository-coordinator")
        if event != "campaign-closed":
            blockers.append("campaign-close-event-required")
        children_read_back = _required_boolean(
            execution, "children_read_back", "execution"
        )
        if (
            not children_read_back
            or execution.get("children_repository") != execution.get("repository")
            or execution.get("children_campaign_id") != execution.get("campaign_id")
            or execution.get("children_scope") != "direct-subagent"
        ):
            blockers.append("campaign-children-not-read-back")
        if remaining_child_agent_ids:
            blockers.append("campaign-children-not-cleaned")
        if target is not None:
            campaign_control_expected = _required_boolean(
                target, "campaign_control_expected", "target"
            )
            campaign_generation = target.get("campaign_generation")
            if target.get("campaign_generation_read_back") is not True:
                blockers.append("campaign-generation-not-read-back")
            if campaign_generation not in {"v4.3", "legacy-v4.2"}:
                blockers.append("campaign-generation-invalid")
            elif campaign_control_expected != (campaign_generation == "v4.3"):
                blockers.append("campaign-generation-resource-mismatch")
            if campaign_control_expected and resource_kind != "campaign-control":
                blockers.append("campaign-control-resource-missing")
            if not campaign_control_expected and resource_kind != "none":
                blockers.append("legacy-campaign-resource-contradictory")
    elif event == "campaign-closed":
        blockers.append("campaign-close-target-required")

    if target_kind == "ephemeral":
        if target is None:
            blockers.append("ephemeral-agent-target-required")
        if resource_kind != "none":
            blockers.append("ephemeral-resource-forbidden")
        if event != "stopped":
            blockers.append("ephemeral-stop-event-required")
        if target is not None:
            if target_role not in EPHEMERAL_TARGET_ROLES:
                blockers.append("ephemeral-target-role-invalid")
            labels = target.get("labels")
            if (
                target.get("labels_read_back") is not True
                or not isinstance(labels, dict)
                or labels.get("gwo.lifecycle") != "ephemeral"
            ):
                blockers.append("ephemeral-lifecycle-label-missing")
            if (
                _required_boolean(target, "result_captured", "target") is not True
                or target.get("result_captured_read_back") is not True
            ):
                blockers.append("ephemeral-result-not-captured")
            if execution.get("no_worktree_read_back") is not True:
                blockers.append("ephemeral-no-worktree-not-read-back")
    elif (
        target is not None
        and isinstance(target.get("labels"), dict)
        and target["labels"].get("gwo.lifecycle") == "ephemeral"
    ):
        blockers.append("ephemeral-target-kind-required")

    if target_kind == "worker":
        if target_role == "orchestrator":
            blockers.append("worker-target-role-invalid")
        if target_role == "review" and resource_kind != "none":
            blockers.append("review-agent-resource-forbidden")
        if target is not None and target_role != "review" and resource_kind == "none":
            blockers.append("worker-execution-resource-required")

    worktree_clean = True
    branch_merged = False
    branch_local_only = False
    unique_commits = 0
    if resource_kind == "none":
        if not agent_only:
            blockers.append("agent-only-evidence-required")
        if worktree is not None or branch is not None or bound_agent_ids:
            blockers.append("agent-only-execution-resource-forbidden")
        if resource_archived or branch_deleted:
            blockers.append("agent-only-resource-readback-contradictory")
    else:
        if agent_only:
            blockers.append("agent-only-evidence-forbidden")
        if not isinstance(worktree, str) or not Path(worktree).is_absolute():
            raise ValueError("execution worktree must be an absolute path")
        if not isinstance(branch, str) or not branch.strip():
            raise ValueError("execution branch must be exact")
        worktree_clean = _required_boolean(execution, "clean", "execution")
        branch_merged = _required_boolean(execution, "branch_merged", "execution")
        unique_commits = execution.get("unique_commits")
        if (
            not isinstance(unique_commits, int)
            or isinstance(unique_commits, bool)
            or unique_commits < 0
        ):
            raise ValueError("execution.unique_commits must be nonnegative")
        branch_local_only = _required_boolean(
            execution, "branch_local_only", "execution"
        )
        if not worktree_clean:
            blockers.append("worktree-not-clean")
        if resource_archived and bound_agent_ids:
            blockers.append("archived-resource-still-bound")

    if resource_kind == "issue-worktree":
        if target_kind != "worker":
            blockers.append("issue-worktree-target-kind-invalid")
        if not isinstance(branch, str) or not branch.startswith("work/issue-"):
            blockers.append("execution-branch-not-work-issue")
        if branch == "dev":
            blockers.append("integration-branch-protected")
        if event == "merged" and not branch_merged:
            blockers.append("merged-event-without-merged-branch")
    elif resource_kind == "campaign-control":
        if target_kind != "campaign":
            blockers.append("campaign-control-target-kind-invalid")
        campaign_id = execution.get("campaign_id")
        expected_branch = f"gwo/campaign/{campaign_id}"
        expected_slug = f"campaign-{campaign_id}"
        if branch != expected_branch:
            blockers.append("campaign-control-branch-invalid")
        if (
            execution.get("resource_identity_read_back") is not True
            or execution.get("worktree_slug") != expected_slug
            or not isinstance(worktree, str)
            or Path(worktree).name != expected_slug
        ):
            blockers.append("campaign-control-worktree-identity-invalid")
        if branch == "dev":
            blockers.append("integration-branch-protected")
        if unique_commits:
            blockers.append("campaign-control-has-unique-commits")
        if not branch_local_only:
            blockers.append("campaign-control-branch-not-local-only")
        if branch_merged:
            blockers.append("campaign-control-branch-must-not-be-merged")

    target_archived = False
    target_requires_readback = False
    if target is not None:
        target_id = target.get("agent_id")
        target_idle = _required_boolean(target, "idle", "target")
        target_archived = _required_boolean(target, "archived", "target")
        decision = authorize_agent_archive(
            actor_kind="agent",
            actor_agent_id=actor_id,
            target_agent_id=target_id,
            target_parent_agent_id=target.get("parent_agent_id"),
            target_idle=target_idle or target_archived,
            force=False,
        )
        blockers.extend(ARCHIVE_ERROR_BLOCKERS[error] for error in decision["errors"])
        if target.get("relationship") != "subagent":
            blockers.append("target-relationship-not-subagent")
        if target_role not in cleanable_roles:
            blockers.append("target-role-not-cleanable")
        if resource_kind != "none" and not target_archived:
            if target_id not in bound_agent_ids:
                blockers.append("target-worktree-binding-missing")
            target_requires_readback = bound_agent_ids == [target_id]

    if resource_kind != "none" and not resource_archived:
        decision = authorize_worktree_archive(
            actor_kind="agent",
            actor_agent_id=actor_id,
            actor_worktree=actor_worktree,
            protected_control_worktree=protected_control_worktree,
            target_worktree=worktree,
            bound_agent_ids=bound_agent_ids,
        )
        for error in decision["errors"]:
            if error == "WORKTREE_IN_USE" and target_requires_readback:
                continue
            blockers.append(ARCHIVE_ERROR_BLOCKERS[error])

    if not work_durable:
        blockers.append("work-not-durable")

    terminal_receipt = execution.get("terminal_receipt")
    if not isinstance(terminal_receipt, dict):
        blockers.append("terminal-receipt-missing")
    else:
        receipt_identity = tuple(terminal_receipt.get(field) for field in IDENTITY_FIELDS)
        target_id = target.get("agent_id") if target is not None else None
        if target_kind == "campaign":
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
    next_required_readback: str | None = None
    cleanup_complete = False
    if not blockers:
        if target is not None and not target_archived:
            actions.append({"action": "archive-paseo-agent", "target": target["agent_id"]})
            next_required_readback = (
                "target-agent-archived"
                if resource_kind == "none"
                else "target-agent-archived-and-worktree-unbound"
            )
        elif resource_kind == "none":
            cleanup_complete = target is not None and target_archived
        elif resource_archived:
            if resource_kind == "campaign-control" and not branch_deleted:
                actions.append({"action": "delete-local-control-branch", "target": branch})
                next_required_readback = "campaign-control-branch-absent"
            else:
                cleanup_complete = resource_kind != "campaign-control" or branch_deleted
        else:
            actions.append({"action": "archive-paseo-worktree", "target": worktree})
            if resource_kind == "campaign-control":
                next_required_readback = "campaign-control-worktree-absent"
            elif branch_merged:
                actions.append({"action": "delete-merged-remote-branch", "target": branch})

    return {
        "schema_version": 2,
        "target_kind": target_kind,
        "resource_kind": resource_kind,
        "status": "protected" if blockers else "eligible",
        "event_triggered": True,
        "automatic_execution": not blockers and bool(actions),
        "cleanup_complete": cleanup_complete,
        "deadline_seconds": CLEANUP_DEADLINE_SECONDS,
        "overdue": seconds_since_event > CLEANUP_DEADLINE_SECONDS,
        "actions": actions,
        "next_required_readback": next_required_readback,
        "blockers": blockers,
    }
