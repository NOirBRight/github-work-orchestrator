#!/usr/bin/env python3
"""Plan and validate the admission transaction for a Campaign Control Workspace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = 1
REPOSITORY_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{5,63}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _text(name: str, value: Any, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    result = value.strip()
    if pattern is not None and not pattern.fullmatch(result):
        raise ValueError(f"{name} is invalid")
    return result


def _count(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def _common(snapshot: Any) -> tuple[str, str, str]:
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("snapshot schema_version must be 1")
    repository = _text("repository", snapshot.get("repository"), REPOSITORY_RE)
    campaign_id = _text("campaign_id", snapshot.get("campaign_id"), CAMPAIGN_RE)
    purpose = _text("purpose", snapshot.get("purpose"))
    if len(purpose) > 80 or any(character in purpose for character in "\r\n"):
        raise ValueError("purpose must be one line of at most 80 characters")
    return repository, campaign_id, purpose


def _coordinator_evidence(snapshot: dict[str, Any], repository: str) -> tuple[str, list[str]]:
    value = snapshot.get("repository_coordinator")
    if not isinstance(value, dict):
        raise ValueError("repository_coordinator must be an object")
    agent_id = _text("repository_coordinator.agent_id", value.get("agent_id"), IDENTIFIER_RE)
    blockers: list[str] = []
    labels = value.get("labels")
    if value.get("read_back") is not True:
        blockers.append("repository-coordinator-not-read-back")
    if value.get("repository") != repository:
        blockers.append("repository-coordinator-repository-mismatch")
    if value.get("role") != "repository-coordinator":
        blockers.append("repository-coordinator-role-invalid")
    if value.get("relationship") != "root" or value.get("parent_agent_id") is not None:
        blockers.append("repository-coordinator-parentage-invalid")
    if not isinstance(labels, dict) or any(
        labels.get(field) != expected
        for field, expected in {
            "repository": repository,
            "role": "repository-coordinator",
        }.items()
    ):
        blockers.append("repository-coordinator-labels-invalid")
    return agent_id, blockers


def plan_create(snapshot: Any) -> dict[str, Any]:
    repository, campaign_id, purpose = _common(snapshot)
    coordinator_id, coordinator_blockers = _coordinator_evidence(snapshot, repository)
    base = snapshot.get("base")
    provider = snapshot.get("provider_binding")
    if not isinstance(base, dict):
        raise ValueError("base must be an object")
    if not isinstance(provider, dict):
        raise ValueError("provider_binding must be an object")
    base_sha = _text("base.sha", base.get("sha"), SHA_RE)
    provider_name = _text("provider_binding.provider", provider.get("provider"))
    model = _text("provider_binding.model", provider.get("model"))
    mode = _text("provider_binding.mode", provider.get("mode"))
    campaign_agents = _count(
        "existing_campaign_agents", snapshot.get("existing_campaign_agents")
    )
    control_workspaces = _count(
        "existing_control_workspaces", snapshot.get("existing_control_workspaces")
    )
    blockers: list[str] = list(coordinator_blockers)
    if base.get("read_back") is not True:
        blockers.append("campaign-base-not-read-back")
    if base.get("repository") != repository:
        blockers.append("campaign-base-repository-mismatch")
    if base.get("branch") != "dev":
        blockers.append("campaign-base-not-dev")
    if provider.get("read_back") is not True:
        blockers.append("provider-binding-not-read-back")
    if campaign_agents:
        blockers.append("campaign-agent-already-exists")
    if control_workspaces:
        blockers.append("campaign-control-workspace-already-exists")

    branch = f"gwo/campaign/{campaign_id}"
    worktree_slug = f"campaign-{campaign_id}"
    agent_title = f"Campaign · {campaign_id} · {purpose}"
    workspace_title = f"Campaign · {campaign_id} · {purpose}"
    labels = {
        "repository": repository,
        "campaign_id": campaign_id,
        "role": "orchestrator",
        "gwo.version": "4.3",
    }
    expected_readback = {
        "parent_agent_id": coordinator_id,
        "relationship": "subagent",
        "agent_title": agent_title,
        "workspace_title": workspace_title,
        "workspace_kind": "worktree",
        "worktree_slug": worktree_slug,
        "branch": branch,
        "head_sha": base_sha,
        "provider": provider_name,
        "model": model,
        "mode": mode,
        "labels": labels,
    }
    actions: list[dict[str, Any]] = []
    if not blockers:
        actions = [
            {
                "action": "create-campaign-agent",
                "name": agent_title,
                "relationship": "subagent",
                "parent_agent_id": coordinator_id,
                "workspace": "create/worktree",
                "branch": branch,
                "base_branch": "dev",
                "base_sha": base_sha,
                "worktree_slug": worktree_slug,
                "provider": provider_name,
                "model": model,
                "mode": mode,
                "labels": labels,
            },
            {"action": "read-back-campaign-runtime"},
            {"action": "rename-campaign-workspace", "title": workspace_title},
        ]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "protected" if blockers else "eligible",
        "automatic_execution": not blockers,
        "repository": repository,
        "campaign_id": campaign_id,
        "branch": branch,
        "worktree_slug": worktree_slug,
        "workspace_title": workspace_title,
        "agent_title": agent_title,
        "actions": actions,
        "expected_readback": expected_readback,
        "partial_failure_policy": "preserve-and-reconcile",
        "blockers": sorted(set(blockers)),
    }


def validate_readback(snapshot: Any) -> dict[str, Any]:
    repository, campaign_id, purpose = _common(snapshot)
    observed = snapshot.get("observed")
    if not isinstance(observed, dict):
        raise ValueError("observed must be an object")
    # Admission validation observes the newly created Campaign/Workspace, so
    # current duplicate counts are expected to be one. Re-derive immutable
    # expectations from authoritative Coordinator/base/provider evidence while
    # excluding the pre-create uniqueness gate already captured by create-plan.
    planned = plan_create(
        {
            **snapshot,
            "existing_campaign_agents": 0,
            "existing_control_workspaces": 0,
        }
    )
    if planned["status"] != "eligible":
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "protected",
            "admitted": False,
            "dispatch_allowed": False,
            "preserve_scene": True,
            "blockers": planned["blockers"],
        }
    expected = planned["expected_readback"]
    if observed.get("read_back") is not True:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "protected",
            "admitted": False,
            "dispatch_allowed": False,
            "preserve_scene": True,
            "blockers": ["campaign-runtime-not-read-back"],
        }
    _text("observed.agent_id", observed.get("agent_id"), IDENTIFIER_RE)
    current_campaign_agents = _count(
        "existing_campaign_agents", snapshot.get("existing_campaign_agents")
    )
    current_control_workspaces = _count(
        "existing_control_workspaces", snapshot.get("existing_control_workspaces")
    )
    blockers: list[str] = []
    if current_campaign_agents != 1:
        blockers.append("campaign-agent-count-after-create-invalid")
    if current_control_workspaces != 1:
        blockers.append("campaign-control-workspace-count-after-create-invalid")
    for field in (
        "parent_agent_id",
        "relationship",
        "agent_title",
        "workspace_title",
        "workspace_kind",
        "worktree_slug",
        "branch",
        "head_sha",
        "provider",
        "model",
        "mode",
    ):
        if observed.get(field) != expected.get(field):
            blockers.append(f"campaign-readback-{field.replace('_', '-')}-mismatch")
    expected_labels = expected.get("labels")
    observed_labels = observed.get("labels")
    if not isinstance(expected_labels, dict) or not isinstance(observed_labels, dict):
        blockers.append("campaign-readback-labels-missing")
    else:
        required_labels = {
            "repository": repository,
            "campaign_id": campaign_id,
            "role": "orchestrator",
            "gwo.version": "4.3",
        }
        if any(observed_labels.get(field) != value for field, value in required_labels.items()):
            blockers.append("campaign-readback-labels-mismatch")
    tracked_changes = observed.get("tracked_changes")
    if not isinstance(tracked_changes, bool):
        raise ValueError("observed.tracked_changes must be boolean")
    unique_commits = _count("observed.unique_commits", observed.get("unique_commits"))
    branch_local_only = observed.get("branch_local_only")
    if not isinstance(branch_local_only, bool):
        raise ValueError("observed.branch_local_only must be boolean")
    if tracked_changes:
        blockers.append("campaign-control-tracked-changes")
    if unique_commits:
        blockers.append("campaign-control-unique-commits")
    if not branch_local_only:
        blockers.append("campaign-control-branch-published")
    blockers = sorted(set(blockers))
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "protected" if blockers else "eligible",
        "admitted": not blockers,
        "dispatch_allowed": not blockers,
        "preserve_scene": bool(blockers),
        "blockers": blockers,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("create-plan", "validate-readback"):
        child = subparsers.add_parser(command)
        child.add_argument("--snapshot", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        report = (
            plan_create(snapshot)
            if args.command == "create-plan"
            else validate_readback(snapshot)
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "plan": report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
