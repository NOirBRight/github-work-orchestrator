#!/usr/bin/env python3
"""Fail-closed entry and wake routing for GWO Repository Coordinators."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

from request_safety import text_is_sensitive


SCHEMA_VERSION = 1
REPOSITORY_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SIGNAL_RE = re.compile(r"^repo-request-[A-Za-z0-9][A-Za-z0-9-]{7,127}$")
MAX_REQUEST_SUMMARY_CHARS = 500
MAX_RELAY_EXTERNAL_ACTIONS = 5
STABLE_WORKSPACE_CLASSES = {"stable-repository"}
EXECUTION_WORKSPACE_CLASSES = {
    "issue-worktree",
    "campaign-control",
    "dispatch-worktree",
}


def _protected(*blockers: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "protected",
        "route": "none",
        "automatic_execution": False,
        "actions": [],
        "external_action_budget": 0,
        "integration_control_workspace_id": None,
        "blockers": sorted(set(blockers)),
    }


def _nonempty_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value.strip()


def _repository(value: Any) -> str:
    repository = _nonempty_text("repository", value)
    if not REPOSITORY_RE.fullmatch(repository):
        raise ValueError("repository must be owner/repo")
    return repository


def _validate_common(snapshot: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    return snapshot, _repository(snapshot.get("repository"))


def _coordinators(
    value: Any, repository: str
) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(value, list):
        raise ValueError("repository_coordinators must be a list")
    result: list[dict[str, Any]] = []
    blockers: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"repository_coordinators[{index}] must be an object")
        agent_id = _nonempty_text(
            f"repository_coordinators[{index}].agent_id", item.get("agent_id")
        )
        if agent_id in seen:
            raise ValueError("duplicate coordinator agent_id")
        seen.add(agent_id)
        if _repository(item.get("repository")) != repository:
            raise ValueError("coordinator repository mismatch")
        status = _nonempty_text(
            f"repository_coordinators[{index}].status", item.get("status")
        )
        labels = item.get("labels")
        valid = bool(
            item.get("read_back") is True
            and item.get("role") == "repository-coordinator"
            and item.get("relationship") == "root"
            and item.get("parent_agent_id") is None
            and isinstance(labels, dict)
            and labels.get("repository") == repository
            and labels.get("role") == "repository-coordinator"
        )
        if not valid:
            blockers.append("repository-coordinator-evidence-invalid")
        result.append({"agent_id": agent_id, "status": status})
    return result, sorted(set(blockers))


def _dev_workspaces(value: Any, repository: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("stable_dev_workspaces must be a list")
    result: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"stable_dev_workspaces[{index}] must be an object")
        if item.get("stable") is not True:
            continue
        if _repository(item.get("repository")) != repository:
            raise ValueError("stable dev workspace repository mismatch")
        if item.get("branch") != "dev":
            raise ValueError("stable dev workspace must read back branch dev")
        result.append(
            {
                "workspace_id": _nonempty_text(
                    f"stable_dev_workspaces[{index}].workspace_id",
                    item.get("workspace_id"),
                )
            }
        )
    return result


def _request(value: Any) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(value, dict):
        return None, ["operator-request-evidence-missing"]
    blockers: list[str] = []
    signal_id = value.get("signal_id")
    if not isinstance(signal_id, str) or not SIGNAL_RE.fullmatch(signal_id):
        blockers.append("operator-request-signal-invalid")
    sequence = value.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence != 1:
        blockers.append("operator-request-sequence-invalid")
    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        blockers.append("request-summary-missing")
        summary = ""
    summary = summary.strip()
    if len(summary) > MAX_REQUEST_SUMMARY_CHARS:
        blockers.append("request-summary-too-long")
    if text_is_sensitive(summary):
        blockers.append("request-summary-sensitive")
    digest = value.get("original_message_sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        blockers.append("operator-request-digest-invalid")
    if blockers:
        return None, blockers
    return {
        "signal_id": signal_id,
        "sequence": sequence,
        "summary": summary,
        "original_message_sha256": digest,
    }, []


def entry_plan(snapshot: Any) -> dict[str, Any]:
    snapshot, repository = _validate_common(snapshot)
    coordinators, coordinator_blockers = _coordinators(
        snapshot.get("repository_coordinators"), repository
    )
    if coordinator_blockers:
        return _protected(*coordinator_blockers)
    if len(coordinators) > 1:
        return _protected("repository-coordinator-conflict")

    current = snapshot.get("current_agent")
    if not isinstance(current, dict):
        raise ValueError("current_agent must be an object")
    current_agent_id = _nonempty_text("current_agent.agent_id", current.get("agent_id"))
    if current.get("repository_readback") is not True:
        return _protected("current-agent-repository-not-read-back")
    workspace_class = _nonempty_text(
        "current_agent.workspace_class", current.get("workspace_class")
    )
    dispatch_bound = current.get("dispatch_bound")
    if not isinstance(dispatch_bound, bool):
        raise ValueError("current_agent.dispatch_bound must be boolean")
    dev_workspaces = _dev_workspaces(
        snapshot.get("stable_dev_workspaces", []), repository
    )
    integration_workspace_id = (
        dev_workspaces[0]["workspace_id"] if len(dev_workspaces) == 1 else None
    )
    coordinator_name = f"Coordinator · {repository}"
    repository_workspace_title = f"Repo · {repository} · dev"

    if coordinators:
        coordinator = coordinators[0]
        if coordinator["agent_id"] == current_agent_id:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "eligible",
                "route": "continue-current-coordinator",
                "automatic_execution": True,
                "actions": [
                    {
                        "action": "ensure-coordinator-ui-names",
                        "agent_name": coordinator_name,
                        "workspace_title": repository_workspace_title,
                    },
                    {"action": "replay-repository-room"},
                ],
                "ui_names": {
                    "agent": coordinator_name,
                    "workspace": repository_workspace_title,
                },
                "external_action_budget": 2,
                "integration_control_workspace_id": integration_workspace_id,
                "blockers": [],
            }
        request, blockers = _request(snapshot.get("request"))
        if blockers:
            return _protected(*blockers)
        assert request is not None
        relay_name = f"Relay · {repository} → Coordinator"
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "relay",
            "route": "relay-existing",
            "automatic_execution": True,
            "actions": [
                {
                    "action": "rename-current-agent-as-relay",
                    "name": relay_name,
                },
                {"action": "post-operator-request"},
                {"action": "read-coordinator-status-once"},
            ],
            "ui_names": {"agent": relay_name},
            "external_action_budget": MAX_RELAY_EXTERNAL_ACTIONS,
            "integration_control_workspace_id": integration_workspace_id,
            "coordinator_agent_id": coordinator["agent_id"],
            "request": request,
            "blockers": [],
        }

    relationship = _nonempty_text(
        "current_agent.relationship", current.get("relationship")
    )
    branch = _nonempty_text("current_agent.branch", current.get("branch"))
    dirty = current.get("dirty")
    if not isinstance(dirty, bool):
        raise ValueError("current_agent.dirty must be boolean")
    execution_context = (
        workspace_class in EXECUTION_WORKSPACE_CLASSES
        or dispatch_bound
        or branch.startswith("work/issue-")
        or branch.startswith("gwo/campaign/")
    )
    if not execution_context and relationship == "root" and workspace_class in STABLE_WORKSPACE_CLASSES:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "eligible",
            "route": "promote-current",
            "automatic_execution": True,
            "actions": [
                {
                    "action": "promote-current-agent",
                    "name": coordinator_name,
                    "labels": {
                        "repository": repository,
                        "role": "repository-coordinator",
                    },
                },
                {
                    "action": "rename-coordinator-home-workspace",
                    "title": repository_workspace_title,
                },
                {"action": "ensure-repository-room"},
                {"action": "replay-repository-room"},
            ],
            "ui_names": {
                "agent": coordinator_name,
                "workspace": repository_workspace_title,
            },
            "external_action_budget": 4,
            "integration_control_workspace_id": integration_workspace_id,
            "integration_ready": integration_workspace_id is not None,
            "home_branch": branch,
            "home_dirty": dirty,
            "blockers": [],
        }
    if execution_context:
        if len(dev_workspaces) != 1:
            return _protected("stable-dev-workspace-not-unique")
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "eligible",
            "route": "create-in-stable-dev-workspace",
            "automatic_execution": True,
            "actions": [
                {
                    "action": "create-repository-coordinator",
                    "name": coordinator_name,
                    "workspace_id": integration_workspace_id,
                    "workspace_title": repository_workspace_title,
                },
                {"action": "ensure-repository-room"},
                {"action": "replay-repository-room"},
            ],
            "ui_names": {
                "agent": coordinator_name,
                "workspace": repository_workspace_title,
            },
            "external_action_budget": 3,
            "integration_control_workspace_id": integration_workspace_id,
            "blockers": [],
        }
    return _protected("current-agent-not-promotable")


def wake_plan(snapshot: Any) -> dict[str, Any]:
    snapshot, repository = _validate_common(snapshot)
    coordinator = snapshot.get("coordinator")
    if not isinstance(coordinator, dict):
        raise ValueError("coordinator must be an object")
    coordinators, blockers = _coordinators([coordinator], repository)
    if blockers:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "protected",
            "automatic_execution": False,
            "action": "escalate",
            "prompt": None,
            "blockers": blockers,
        }
    coordinator = coordinators[0]
    signal_id = snapshot.get("request_signal_id")
    if not isinstance(signal_id, str) or not SIGNAL_RE.fullmatch(signal_id):
        raise ValueError("request_signal_id is invalid")
    status = _nonempty_text("coordinator.status", coordinator.get("status"))
    if status == "idle":
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "eligible",
            "automatic_execution": True,
            "action": "send-signal-only",
            "prompt": signal_id,
            "blockers": [],
        }
    if status in {"running", "initializing"}:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "eligible",
            "automatic_execution": True,
            "action": "do-not-disturb",
            "prompt": None,
            "blockers": [],
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "protected",
        "automatic_execution": False,
        "action": "escalate",
        "prompt": None,
        "blockers": ["coordinator-not-wakeable"],
    }


def _read_snapshot(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("entry-plan", "wake-plan"):
        child = subparsers.add_parser(command)
        child.add_argument("--snapshot", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        snapshot = _read_snapshot(args.snapshot)
        result = entry_plan(snapshot) if args.command == "entry-plan" else wake_plan(snapshot)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
