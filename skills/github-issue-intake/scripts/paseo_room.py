#!/usr/bin/env python3
"""Validated Paseo campaign-room protocol with replay and wait compensation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Callable, Sequence


SCHEMA_VERSION = 1
DEFAULT_REPLAY_LIMIT = 500
EVENT_TYPES = {
    "CAMPAIGN_OPENED",
    "AGENT_READY",
    "START",
    "PROGRESS",
    "HEARTBEAT",
    "ASK",
    "REPLY",
    "DECISION_GATE",
    "DISCUSSION_REQUIRED",
    "BLOCKED",
    "PR_OPENED",
    "READY_FOR_REVIEW",
    "REVIEW_RESULT",
    "COMPLETED",
    "WORKER_DONE",
    "ESCALATION",
    "STOPPED",
    "CHECKPOINT",
    "CAMPAIGN_CLOSED",
}
CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{5,63}$")
REPOSITORY_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TIMEOUT_RE = re.compile(r"^([1-9][0-9]*)(ms|s|m)$")
HEARTBEAT_PHASES = {"analysis", "implementation", "verification", "review-fix"}
REVIEW_AXES = {"spec", "quality"}
WORKER_TERMINAL_EVENTS = {"WORKER_DONE", "BLOCKED", "STOPPED"}
AGENT_ROLES = {
    "repository-coordinator",
    "orchestrator",
    "intake",
    "implementation",
    "review",
    "monitor",
}
WORKER_ROLES = {"intake", "implementation", "review", "monitor"}
IMPLEMENTATION_WORKER_ROLES = {"implementation"}
COORDINATOR_ROLES = {"repository-coordinator", "orchestrator"}
EVENT_ALLOWED_ROLES = {
    "CAMPAIGN_OPENED": COORDINATOR_ROLES,
    "AGENT_READY": WORKER_ROLES,
    "START": COORDINATOR_ROLES,
    "PROGRESS": WORKER_ROLES,
    "HEARTBEAT": IMPLEMENTATION_WORKER_ROLES,
    "ASK": IMPLEMENTATION_WORKER_ROLES,
    "REPLY": COORDINATOR_ROLES,
    "DECISION_GATE": COORDINATOR_ROLES,
    "DISCUSSION_REQUIRED": AGENT_ROLES,
    "BLOCKED": AGENT_ROLES,
    "PR_OPENED": IMPLEMENTATION_WORKER_ROLES,
    "READY_FOR_REVIEW": {"orchestrator"},
    "REVIEW_RESULT": {"review"},
    "COMPLETED": {"orchestrator", "intake"},
    "WORKER_DONE": IMPLEMENTATION_WORKER_ROLES,
    "ESCALATION": COORDINATOR_ROLES,
    "STOPPED": AGENT_ROLES,
    "CHECKPOINT": COORDINATOR_ROLES,
    "CAMPAIGN_CLOSED": {"orchestrator"},
}
CAMPAIGN_CONTROL_EVENTS = {"CAMPAIGN_OPENED", "CHECKPOINT", "CAMPAIGN_CLOSED"}


class RoomProtocolError(RuntimeError):
    pass


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _windows_cmd_command(executable: str, arguments: Sequence[str]) -> list[str]:
    # Passing each argument separately lets subprocess apply Windows quoting.
    # `/s` must not be used: it strips the first/last quotes from the command
    # string and can truncate a quoted chat purpose or JSON message.
    return [
        os.environ.get("COMSPEC", "cmd.exe"),
        "/d",
        "/c",
        executable,
        *arguments,
    ]


def room_name(campaign_id: str) -> str:
    if not CAMPAIGN_RE.fullmatch(campaign_id):
        raise RoomProtocolError("invalid campaign_id")
    return f"gwo-{campaign_id}"


def validate_event(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["event-must-be-object"]
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("invalid-schema-version")
    for field in (
        "signal_id",
        "campaign_id",
        "dispatch_id",
        "event_type",
        "issue",
        "sender_agent_id",
        "next_action",
    ):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            errors.append(f"missing-or-empty:{field}")
    evidence = payload.get("evidence")
    if not (
        (isinstance(evidence, str) and bool(evidence.strip()))
        or (isinstance(evidence, dict) and bool(evidence))
    ):
        errors.append("missing-or-empty:evidence")
    if isinstance(payload.get("campaign_id"), str) and not CAMPAIGN_RE.fullmatch(
        payload["campaign_id"]
    ):
        errors.append("invalid-campaign-id")
    for field in ("signal_id", "dispatch_id", "sender_agent_id"):
        value = payload.get(field)
        if isinstance(value, str) and value and not IDENTIFIER_RE.fullmatch(value):
            errors.append(f"invalid-{field.replace('_', '-')}")
    recipient = payload.get("recipient_agent_id")
    if recipient is not None and (
        not isinstance(recipient, str) or not IDENTIFIER_RE.fullmatch(recipient)
    ):
        errors.append("invalid-recipient-agent-id")
    if payload.get("event_type") not in EVENT_TYPES:
        errors.append("invalid-event-type")
    sequence = payload.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        errors.append("invalid-sequence")
    in_reply_to = payload.get("in_reply_to")
    if in_reply_to is not None and (
        not isinstance(in_reply_to, str) or not IDENTIFIER_RE.fullmatch(in_reply_to)
    ):
        errors.append("invalid-in-reply-to")

    event_type = payload.get("event_type")
    if event_type == "HEARTBEAT":
        if not isinstance(evidence, dict):
            errors.append("invalid-heartbeat-evidence")
        else:
            if evidence.get("phase") not in HEARTBEAT_PHASES:
                errors.append("invalid-heartbeat-phase")
            for field in ("last_completed_step", "next_step"):
                if (
                    not isinstance(evidence.get(field), str)
                    or not evidence[field].strip()
                ):
                    errors.append(f"invalid-heartbeat-{field.replace('_', '-')}")
            head_sha = evidence.get("head_sha")
            if head_sha is not None and (
                not isinstance(head_sha, str) or not SHA_RE.fullmatch(head_sha)
            ):
                errors.append("invalid-heartbeat-head-sha")
            if not isinstance(evidence.get("worktree_dirty"), bool):
                errors.append("invalid-heartbeat-worktree-dirty")
            if evidence.get("blocking") is not False:
                errors.append("invalid-heartbeat-blocking")
    elif event_type == "ASK":
        if (
            not isinstance(evidence, dict)
            or not isinstance(evidence.get("question"), str)
            or not evidence["question"].strip()
            or evidence.get("blocking") is not True
        ):
            errors.append("invalid-ask-evidence")
        if recipient is None:
            errors.append("ask-requires-recipient")
    elif event_type == "REPLY":
        if not isinstance(in_reply_to, str):
            errors.append("reply-requires-in-reply-to")
        if recipient is None:
            errors.append("reply-requires-recipient")
        if (
            not isinstance(evidence, dict)
            or not isinstance(evidence.get("answer"), str)
            or not evidence["answer"].strip()
        ):
            errors.append("invalid-reply-evidence")
    elif event_type == "DECISION_GATE":
        if (
            not isinstance(evidence, dict)
            or not isinstance(evidence.get("decision"), str)
            or not evidence["decision"].strip()
            or evidence.get("github_state") != "ready-for-human"
            or not isinstance(evidence.get("github_url"), str)
            or not evidence["github_url"].strip()
        ):
            errors.append("invalid-decision-gate-evidence")
    elif event_type == "WORKER_DONE":
        if not isinstance(evidence, dict):
            errors.append("invalid-worker-done-evidence")
        else:
            if not isinstance(evidence.get("head_sha"), str) or not SHA_RE.fullmatch(
                evidence["head_sha"]
            ):
                errors.append("invalid-worker-done-head-sha")
            for field in ("verification", "changed_paths"):
                value = evidence.get(field)
                if (
                    not isinstance(value, list)
                    or not value
                    or any(
                        not isinstance(item, str) or not item.strip() for item in value
                    )
                ):
                    errors.append(f"invalid-worker-done-{field.replace('_', '-')}")
            if not isinstance(evidence.get("pr"), str) or not evidence["pr"].strip():
                errors.append("invalid-worker-done-pr")
    elif event_type == "REVIEW_RESULT":
        valid = isinstance(evidence, dict) and set(evidence) == {
            "axis",
            "candidate_sha",
            "base_sha",
            "diff_sha256",
            "acceptance_sha256",
            "review_round",
            "scope",
            "previous_candidate_sha",
            "verdict",
            "findings",
        }
        if valid:
            review_round = evidence.get("review_round")
            scope = evidence.get("scope")
            previous_sha = evidence.get("previous_candidate_sha")
            findings = evidence.get("findings")
            valid = bool(
                evidence.get("axis") in REVIEW_AXES
                and isinstance(evidence.get("candidate_sha"), str)
                and SHA_RE.fullmatch(evidence["candidate_sha"])
                and isinstance(evidence.get("base_sha"), str)
                and SHA_RE.fullmatch(evidence["base_sha"])
                and isinstance(evidence.get("diff_sha256"), str)
                and SHA256_RE.fullmatch(evidence["diff_sha256"])
                and isinstance(evidence.get("acceptance_sha256"), str)
                and SHA256_RE.fullmatch(evidence["acceptance_sha256"])
                and isinstance(review_round, int)
                and not isinstance(review_round, bool)
                and review_round >= 1
                and scope in {"full", "delta"}
                and evidence.get("verdict") in {"pass", "fail"}
                and isinstance(findings, list)
                and all(
                    isinstance(finding, str) and bool(finding.strip())
                    for finding in findings
                )
            )
            if valid and scope == "full":
                valid = review_round == 1 and previous_sha is None
            elif valid and scope == "delta":
                valid = bool(
                    review_round >= 2
                    and isinstance(previous_sha, str)
                    and SHA_RE.fullmatch(previous_sha)
                    and previous_sha != evidence["candidate_sha"]
                )
        if not valid:
            errors.append("invalid-review-result-evidence")
    elif event_type == "ESCALATION":
        if (
            not isinstance(evidence, dict)
            or not isinstance(evidence.get("reason"), str)
            or not evidence["reason"].strip()
            or not isinstance(evidence.get("attempts"), int)
            or isinstance(evidence.get("attempts"), bool)
            or evidence["attempts"] < 1
        ):
            errors.append("invalid-escalation-evidence")
    return sorted(set(errors))


def _timeout_seconds(value: str) -> float:
    match = TIMEOUT_RE.fullmatch(value.strip())
    if not match:
        raise RoomProtocolError("timeout must use ms, s, or m")
    amount = int(match.group(1))
    unit = match.group(2)
    seconds = amount / 1000 if unit == "ms" else amount * (60 if unit == "m" else 1)
    if seconds > 60:
        raise RoomProtocolError("chat wait timeout must not exceed 60 seconds")
    return seconds


def _default_runner(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("paseo")
    if not executable:
        raise RoomProtocolError("paseo CLI is not available on PATH")
    command = [executable, *arguments]
    if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        command = _windows_cmd_command(executable, arguments)
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=65,
        )
    except subprocess.TimeoutExpired as error:
        raise RoomProtocolError("Paseo CLI exceeded the bounded wait") from error


def _json_output(completed: subprocess.CompletedProcess[str]) -> Any:
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RoomProtocolError(f"Paseo CLI failed: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RoomProtocolError("Paseo CLI returned invalid JSON") from error


def _identity_receipt_key(payload: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(payload.get("agent_id", "")),
        str(payload.get("campaign_id", "")),
        str(payload.get("dispatch_id", "")),
    )


def _identity_receipt_lookup(
    receipts: Sequence[dict[str, Any]] | None,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    if receipts is None:
        return {}
    if isinstance(receipts, (str, bytes)) or not isinstance(receipts, Sequence):
        raise RoomProtocolError("identity receipts must be a JSON array")
    lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, dict):
            raise RoomProtocolError(f"identity receipt {index} must be an object")
        key = _identity_receipt_key(receipt)
        if not all(key):
            raise RoomProtocolError(
                f"identity receipt {index} requires agent_id, campaign_id, and dispatch_id"
            )
        if key in lookup:
            raise RoomProtocolError("duplicate identity receipt")
        lookup[key] = receipt
    return lookup


IDENTITY_AUTHORITY_SCOPES = {
    "worker-dispatch",
    "review-dispatch",
    "campaign-control",
    "campaign-admission",
}
REVIEW_ASSIGNMENT_LOCK_FIELDS = {
    "dispatch_id",
    "candidate_sha",
    "base_sha",
    "diff_sha256",
    "acceptance_sha256",
    "review_round",
    "scope",
    "previous_candidate_sha",
}


def _campaign_parent_is_read_back(
    child: dict[str, Any],
    readbacks: dict[str, dict[str, Any]],
    *,
    repository: str,
    campaign_id: str,
) -> bool:
    parent = readbacks.get(child.get("parent_agent_id"))
    return bool(
        child.get("relationship") == "subagent"
        and isinstance(parent, dict)
        and parent["labels"].get("repository") == repository
        and parent["labels"].get("campaign_id") == campaign_id
        and parent["labels"].get("role") == "orchestrator"
        and parent["relationship"] == "subagent"
    )


def _repository_coordinator_parent_is_read_back(
    campaign: dict[str, Any],
    readbacks: dict[str, dict[str, Any]],
    *,
    repository: str,
) -> bool:
    coordinator = readbacks.get(campaign.get("parent_agent_id"))
    return bool(
        campaign.get("relationship") == "subagent"
        and isinstance(coordinator, dict)
        and coordinator["labels"].get("repository") == repository
        and coordinator["labels"].get("role") == "repository-coordinator"
        and coordinator["relationship"] == "root"
        and coordinator["parent_agent_id"] is None
    )


def _review_assignment_map(
    value: Any,
    *,
    authority_scope: str,
    repository: str,
    campaign_id: str,
    dispatch_id: str,
    readbacks: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if authority_scope != "review-dispatch":
        if value not in (None, []):
            raise RoomProtocolError(
                "review_assignments are valid only for review-dispatch scope"
            )
        return {}
    if not isinstance(value, list):
        raise RoomProtocolError("review-dispatch requires review_assignments")
    result: dict[str, dict[str, Any]] = {}
    seen_axes: set[str] = set()
    required_fields = {
        "agent_id",
        "campaign_id",
        "review_axis",
        "campaign_parent_agent_id",
        "lock",
        "review_lock_read_back",
        "read_back",
    }
    for index, assignment in enumerate(value):
        if not isinstance(assignment, dict) or set(assignment) != required_fields:
            raise RoomProtocolError(f"review assignment {index} has invalid fields")
        agent_id = assignment.get("agent_id")
        axis = assignment.get("review_axis")
        lock = assignment.get("lock")
        readback = readbacks.get(agent_id) if isinstance(agent_id, str) else None
        valid = bool(
            isinstance(agent_id, str)
            and IDENTIFIER_RE.fullmatch(agent_id)
            and agent_id not in result
            and axis in REVIEW_AXES
            and axis not in seen_axes
            and assignment.get("campaign_id") == campaign_id
            and assignment.get("read_back") is True
            and assignment.get("review_lock_read_back") is True
            and isinstance(lock, dict)
            and set(lock) == REVIEW_ASSIGNMENT_LOCK_FIELDS
            and lock.get("dispatch_id") == dispatch_id
            and isinstance(lock.get("candidate_sha"), str)
            and SHA_RE.fullmatch(lock["candidate_sha"])
            and isinstance(lock.get("base_sha"), str)
            and SHA_RE.fullmatch(lock["base_sha"])
            and isinstance(lock.get("diff_sha256"), str)
            and SHA256_RE.fullmatch(lock["diff_sha256"])
            and isinstance(lock.get("acceptance_sha256"), str)
            and SHA256_RE.fullmatch(lock["acceptance_sha256"])
            and isinstance(lock.get("review_round"), int)
            and not isinstance(lock.get("review_round"), bool)
            and lock["review_round"] >= 1
            and lock.get("scope") in {"full", "delta"}
            and isinstance(readback, dict)
            and _campaign_parent_is_read_back(
                readback,
                readbacks,
                repository=repository,
                campaign_id=campaign_id,
            )
            and assignment.get("campaign_parent_agent_id")
            == readback["parent_agent_id"]
            and readback["labels"].get("repository") == repository
            and readback["labels"].get("campaign_id") == campaign_id
            and readback["labels"].get("role") == "review"
            and readback["labels"].get("review_axis") == axis
            and "dispatch_id" not in readback["labels"]
        )
        previous_sha = lock.get("previous_candidate_sha") if isinstance(lock, dict) else None
        if valid and lock["scope"] == "full":
            valid = lock["review_round"] == 1 and previous_sha is None
        elif valid and lock["scope"] == "delta":
            valid = bool(
                lock["review_round"] >= 2
                and isinstance(previous_sha, str)
                and SHA_RE.fullmatch(previous_sha)
                and previous_sha != lock["candidate_sha"]
            )
        if not valid:
            raise RoomProtocolError(f"review assignment {index} is invalid")
        result[agent_id] = assignment
        seen_axes.add(axis)
    if seen_axes != REVIEW_AXES:
        raise RoomProtocolError("review-dispatch requires the fixed review pair")
    return result


def identity_receipt_plan(snapshot: Any) -> dict[str, Any]:
    """Compile exact replay receipts from normalized Paseo Agent readbacks."""
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise RoomProtocolError("identity plan snapshot schema_version must be 1")
    repository = snapshot.get("repository")
    campaign_id = snapshot.get("campaign_id")
    dispatch_id = snapshot.get("dispatch_id")
    authority_scope = snapshot.get("authority_scope")
    if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository):
        raise RoomProtocolError("identity plan repository is invalid")
    if not isinstance(campaign_id, str) or not CAMPAIGN_RE.fullmatch(campaign_id):
        raise RoomProtocolError("identity plan campaign_id is invalid")
    if not isinstance(dispatch_id, str) or not IDENTIFIER_RE.fullmatch(dispatch_id):
        raise RoomProtocolError("identity plan dispatch_id is invalid")
    if authority_scope not in IDENTITY_AUTHORITY_SCOPES:
        raise RoomProtocolError("identity plan authority_scope is invalid")
    raw_readbacks = snapshot.get("agent_readbacks")
    if not isinstance(raw_readbacks, list) or not raw_readbacks:
        raise RoomProtocolError("identity plan requires agent_readbacks")

    readbacks: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_readbacks):
        if isinstance(raw, dict) and isinstance(raw.get("snapshot"), dict):
            raw = raw["snapshot"]
        if not isinstance(raw, dict) or raw.get("read_back") is not True:
            raise RoomProtocolError(f"agent readback {index} is not read back")
        agent_id = raw.get("agent_id", raw.get("id"))
        labels = raw.get("labels")
        parent_agent_id = raw.get("parent_agent_id")
        if parent_agent_id is None and isinstance(labels, dict):
            parent_agent_id = labels.get("paseo.parent-agent-id")
        relationship = raw.get("relationship")
        if relationship is None:
            relationship = "subagent" if parent_agent_id else "root"
        if (
            not isinstance(agent_id, str)
            or not IDENTIFIER_RE.fullmatch(agent_id)
            or agent_id in readbacks
            or not isinstance(labels, dict)
            or labels.get("repository") != repository
            or labels.get("role") not in AGENT_ROLES
            or relationship not in {"root", "subagent"}
            or (relationship == "root" and parent_agent_id is not None)
            or (
                relationship == "subagent"
                and (not isinstance(parent_agent_id, str) or not parent_agent_id)
            )
        ):
            raise RoomProtocolError(f"agent readback {index} identity is invalid")
        readbacks[agent_id] = {
            "agent_id": agent_id,
            "parent_agent_id": parent_agent_id,
            "relationship": relationship,
            "labels": labels,
        }

    assignments = _review_assignment_map(
        snapshot.get("review_assignments"),
        authority_scope=authority_scope,
        repository=repository,
        campaign_id=campaign_id,
        dispatch_id=dispatch_id,
        readbacks=readbacks,
    )

    receipts: list[dict[str, Any]] = []
    for agent_id, readback in sorted(readbacks.items()):
        labels = readback["labels"]
        role = labels["role"]
        assignment: dict[str, Any] | None = None
        if role == "review":
            if authority_scope != "review-dispatch" or agent_id not in assignments:
                continue
            assignment = assignments[agent_id]
            authority = {
                "kind": "reusable-reviewer",
                "campaign_id": campaign_id,
                "dispatch_id": dispatch_id,
                "subject_agent_id": agent_id,
                "campaign_parent_agent_id": readback["parent_agent_id"],
                "review_axis": labels.get("review_axis"),
                "assignment": assignment,
                "read_back": True,
            }
        elif role in WORKER_ROLES:
            if authority_scope != "worker-dispatch" or (
                labels.get("campaign_id") != campaign_id
                or labels.get("dispatch_id") != dispatch_id
            ):
                continue
            if not _campaign_parent_is_read_back(
                readback,
                readbacks,
                repository=repository,
                campaign_id=campaign_id,
            ):
                raise RoomProtocolError(
                    "worker dispatch authority requires a read-back Campaign parent"
                )
            authority = {
                "kind": "dispatch-owner",
                "campaign_id": campaign_id,
                "dispatch_id": dispatch_id,
                "subject_agent_id": agent_id,
                "read_back": True,
            }
        elif role == "orchestrator":
            if labels.get("campaign_id") != campaign_id:
                continue
            if authority_scope == "campaign-control":
                if not _repository_coordinator_parent_is_read_back(
                    readback, readbacks, repository=repository
                ):
                    raise RoomProtocolError(
                        "campaign-control authority requires a read-back root Coordinator parent"
                    )
                authority = {
                    "kind": "campaign-control",
                    "campaign_id": campaign_id,
                    "dispatch_id": dispatch_id,
                    "subject_agent_id": agent_id,
                    "read_back": True,
                }
            elif authority_scope in {"worker-dispatch", "review-dispatch"}:
                if authority_scope == "worker-dispatch":
                    children = [
                        child
                        for child in readbacks.values()
                        if child["parent_agent_id"] == agent_id
                        and child["relationship"] == "subagent"
                        and child["labels"].get("campaign_id") == campaign_id
                        and child["labels"].get("dispatch_id") == dispatch_id
                        and child["labels"].get("role") in WORKER_ROLES - {"review"}
                    ]
                else:
                    children = [
                        child
                        for child in readbacks.values()
                        if child["agent_id"] in assignments
                        and child["parent_agent_id"] == agent_id
                        and child["relationship"] == "subagent"
                    ]
                if not children:
                    raise RoomProtocolError(
                        "orchestrator dispatch authority requires read-back children"
                    )
                subjects = [
                    {
                        "agent_id": child["agent_id"],
                        "parent_agent_id": agent_id,
                        "relationship": "subagent",
                        "labels": child["labels"],
                        "assignment": assignments.get(child["agent_id"]),
                    }
                    for child in sorted(children, key=lambda item: item["agent_id"])
                ]
                authority = {
                    "kind": "direct-child-dispatch",
                    "campaign_id": campaign_id,
                    "dispatch_id": dispatch_id,
                    "subjects": subjects,
                    "read_back": True,
                }
            else:
                continue
        elif role == "repository-coordinator":
            if authority_scope not in {"campaign-admission", "campaign-control"}:
                continue
            children = [
                child
                for child in readbacks.values()
                if child["parent_agent_id"] == agent_id
                and child["relationship"] == "subagent"
                and child["labels"].get("campaign_id") == campaign_id
                and child["labels"].get("role") == "orchestrator"
            ]
            if len(children) != 1:
                raise RoomProtocolError(
                    "coordinator authority requires one read-back Campaign child"
                )
            child = children[0]
            authority = {
                "kind": "admitted-campaign",
                "campaign_id": campaign_id,
                "dispatch_id": dispatch_id,
                "subject_agent_id": child["agent_id"],
                "subject_parent_agent_id": agent_id,
                "subject_relationship": "subagent",
                "subject_labels": child["labels"],
                "read_back": True,
            }
        else:
            continue
        receipts.append(
            {
                "agent_id": agent_id,
                "campaign_id": campaign_id,
                "dispatch_id": dispatch_id,
                "role": role,
                "parent_agent_id": readback["parent_agent_id"],
                "relationship": readback["relationship"],
                "labels": labels,
                "authority": authority,
                **({"assignment": assignment} if assignment is not None else {}),
                "read_back": True,
            }
        )
    if not receipts:
        raise RoomProtocolError("identity plan produced no receipts")
    return {
        "schema_version": 1,
        "repository": repository,
        "campaign_id": campaign_id,
        "dispatch_id": dispatch_id,
        "authority_scope": authority_scope,
        "receipts": receipts,
    }


REVIEW_LOCK_FIELDS = (
    "campaign_id",
    "dispatch_id",
    "candidate_sha",
    "base_sha",
    "diff_sha256",
    "acceptance_sha256",
    "review_round",
    "scope",
    "previous_candidate_sha",
    "previous_review_round",
    "previous_lock_read_back",
    "source",
    "read_back",
)


def _review_lock_lookup(
    receipts: Sequence[dict[str, Any]] | None,
) -> dict[tuple[str, str, int], dict[str, Any]]:
    if receipts is None:
        return {}
    if isinstance(receipts, (str, bytes)) or not isinstance(receipts, Sequence):
        raise RoomProtocolError("review locks must be a JSON array")
    lookup: dict[tuple[str, str, int], dict[str, Any]] = {}
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, dict) or set(receipt) != set(REVIEW_LOCK_FIELDS):
            raise RoomProtocolError(f"review lock {index} has invalid fields")
        campaign_id = receipt.get("campaign_id")
        dispatch_id = receipt.get("dispatch_id")
        review_round = receipt.get("review_round")
        scope = receipt.get("scope")
        previous_sha = receipt.get("previous_candidate_sha")
        previous_round = receipt.get("previous_review_round")
        valid = bool(
            isinstance(campaign_id, str)
            and CAMPAIGN_RE.fullmatch(campaign_id)
            and isinstance(dispatch_id, str)
            and IDENTIFIER_RE.fullmatch(dispatch_id)
            and isinstance(receipt.get("candidate_sha"), str)
            and SHA_RE.fullmatch(receipt["candidate_sha"])
            and isinstance(receipt.get("base_sha"), str)
            and SHA_RE.fullmatch(receipt["base_sha"])
            and isinstance(receipt.get("diff_sha256"), str)
            and SHA256_RE.fullmatch(receipt["diff_sha256"])
            and isinstance(receipt.get("acceptance_sha256"), str)
            and SHA256_RE.fullmatch(receipt["acceptance_sha256"])
            and isinstance(review_round, int)
            and not isinstance(review_round, bool)
            and review_round >= 1
            and receipt.get("source") == "campaign-verified-candidate"
            and receipt.get("read_back") is True
            and receipt.get("previous_lock_read_back") is True
        )
        if valid and scope == "full":
            valid = review_round == 1 and previous_sha is None and previous_round is None
        elif valid and scope == "delta":
            valid = bool(
                review_round >= 2
                and isinstance(previous_sha, str)
                and SHA_RE.fullmatch(previous_sha)
                and previous_sha != receipt["candidate_sha"]
                and previous_round == review_round - 1
            )
        else:
            valid = False
        if not valid:
            raise RoomProtocolError(f"review lock {index} is invalid")
        key = (campaign_id, dispatch_id, review_round)
        if key in lookup:
            raise RoomProtocolError("duplicate review lock")
        lookup[key] = receipt
    for key, receipt in lookup.items():
        if receipt["scope"] != "delta":
            continue
        previous = lookup.get((key[0], key[1], receipt["previous_review_round"]))
        if previous is None or previous["candidate_sha"] != receipt["previous_candidate_sha"]:
            raise RoomProtocolError("delta review lock lineage is not read back")
    return lookup


def _review_lock_from_event(event: dict[str, Any]) -> dict[str, Any]:
    evidence = event["evidence"]
    return {
        "campaign_id": event["campaign_id"],
        "dispatch_id": event["dispatch_id"],
        **{
            field: evidence[field]
            for field in (
                "candidate_sha",
                "base_sha",
                "diff_sha256",
                "acceptance_sha256",
                "review_round",
                "scope",
                "previous_candidate_sha",
            )
        },
    }


def _identity_receipt_errors(
    event: dict[str, Any],
    message_author: Any,
    receipt: dict[str, Any] | None,
    receipt_lookup: dict[tuple[str, str, str], dict[str, Any]],
) -> list[str]:
    if message_author != event["sender_agent_id"]:
        return ["message-author-mismatch"]
    if receipt is None:
        return ["identity-receipt-missing"]

    errors: list[str] = []
    for field, expected in (
        ("agent_id", event["sender_agent_id"]),
        ("campaign_id", event["campaign_id"]),
        ("dispatch_id", event["dispatch_id"]),
    ):
        if receipt.get(field) != expected:
            errors.append(f"identity-receipt-{field.replace('_', '-')}-mismatch")
    role = receipt.get("role")
    relationship = receipt.get("relationship")
    parent_agent_id = receipt.get("parent_agent_id")
    if role not in AGENT_ROLES:
        errors.append("identity-receipt-role-invalid")
    if role == "repository-coordinator":
        if relationship != "root" or parent_agent_id is not None:
            errors.append("identity-receipt-parentage-invalid")
    elif (
        relationship != "subagent"
        or not isinstance(parent_agent_id, str)
        or not parent_agent_id
    ):
        errors.append("identity-receipt-parentage-invalid")
    labels = receipt.get("labels")
    if not isinstance(labels, dict):
        errors.append("identity-receipt-labels-missing")
    else:
        expected_labels: list[tuple[str, Any]] = [("role", role)]
        if role in WORKER_ROLES - {"review"}:
            expected_labels.extend(
                [
                    ("campaign_id", event["campaign_id"]),
                    ("dispatch_id", event["dispatch_id"]),
                ]
            )
        elif role == "review":
            expected_labels.append(("campaign_id", event["campaign_id"]))
        elif role == "orchestrator":
            expected_labels.append(("campaign_id", event["campaign_id"]))
        for field, expected in expected_labels:
            if labels.get(field) != expected:
                errors.append(
                    f"identity-receipt-label-{field.replace('_', '-')}-mismatch"
                )
        if role == "review" and event["event_type"] == "REVIEW_RESULT":
            review_axis = (
                event.get("evidence", {}).get("axis")
                if isinstance(event.get("evidence"), dict)
                else None
            )
            if labels.get("review_axis") != review_axis:
                errors.append("identity-receipt-review-axis-mismatch")
        if role == "review" and "dispatch_id" in labels:
            errors.append("identity-receipt-reviewer-static-dispatch-forbidden")
        repository = labels.get("repository")
        if not isinstance(repository, str) or not repository.strip():
            errors.append("identity-receipt-label-repository-missing")

    authority = receipt.get("authority")
    if not isinstance(authority, dict):
        errors.append("identity-receipt-authority-missing")
    else:
        for field, expected in (
            ("campaign_id", event["campaign_id"]),
            ("dispatch_id", event["dispatch_id"]),
        ):
            if authority.get(field) != expected:
                errors.append(
                    f"identity-receipt-authority-{field.replace('_', '-')}-mismatch"
                )
        if role == "review":
            expected_authority = "reusable-reviewer"
        elif role in WORKER_ROLES:
            expected_authority = "dispatch-owner"
        elif role == "orchestrator" and event["event_type"] in CAMPAIGN_CONTROL_EVENTS:
            expected_authority = "campaign-control"
        elif role == "orchestrator":
            expected_authority = "direct-child-dispatch"
        else:
            expected_authority = "admitted-campaign"
        if authority.get("kind") != expected_authority:
            errors.append("identity-receipt-authority-kind-mismatch")
        subject_agent_id = authority.get("subject_agent_id")
        if expected_authority in {
            "dispatch-owner",
            "campaign-control",
            "reusable-reviewer",
        }:
            if subject_agent_id != event["sender_agent_id"]:
                errors.append("identity-receipt-authority-subject-mismatch")
            if expected_authority == "reusable-reviewer":
                assignment = receipt.get("assignment")
                authority_assignment = authority.get("assignment")
                lock = assignment.get("lock") if isinstance(assignment, dict) else None
                evidence = event.get("evidence")
                assignment_valid = bool(
                    isinstance(assignment, dict)
                    and authority_assignment == assignment
                    and assignment.get("agent_id") == event["sender_agent_id"]
                    and assignment.get("campaign_id") == event["campaign_id"]
                    and assignment.get("review_axis") == labels.get("review_axis")
                    and assignment.get("campaign_parent_agent_id") == parent_agent_id
                    and assignment.get("read_back") is True
                    and assignment.get("review_lock_read_back") is True
                    and isinstance(lock, dict)
                    and set(lock) == REVIEW_ASSIGNMENT_LOCK_FIELDS
                    and lock.get("dispatch_id") == event["dispatch_id"]
                    and authority.get("campaign_parent_agent_id") == parent_agent_id
                    and authority.get("review_axis") == labels.get("review_axis")
                )
                if assignment_valid and event["event_type"] == "REVIEW_RESULT":
                    assignment_valid = isinstance(evidence, dict) and all(
                        evidence.get(field) == lock.get(field)
                        for field in REVIEW_ASSIGNMENT_LOCK_FIELDS - {"dispatch_id"}
                    )
                if not assignment_valid:
                    errors.append("identity-receipt-review-assignment-invalid")
                parent_receipt = receipt_lookup.get(
                    (parent_agent_id, event["campaign_id"], event["dispatch_id"])
                )
                parent_authority = (
                    parent_receipt.get("authority")
                    if isinstance(parent_receipt, dict)
                    else None
                )
                parent_subjects = (
                    parent_authority.get("subjects")
                    if isinstance(parent_authority, dict)
                    else None
                )
                parent_valid = bool(
                    isinstance(parent_receipt, dict)
                    and parent_receipt.get("role") == "orchestrator"
                    and parent_receipt.get("read_back") is True
                    and isinstance(parent_receipt.get("labels"), dict)
                    and parent_receipt["labels"].get("repository")
                    == labels.get("repository")
                    and parent_receipt["labels"].get("campaign_id")
                    == event["campaign_id"]
                    and isinstance(parent_authority, dict)
                    and parent_authority.get("kind") == "direct-child-dispatch"
                    and parent_authority.get("read_back") is True
                    and isinstance(parent_subjects, list)
                    and any(
                        isinstance(subject, dict)
                        and subject.get("agent_id") == event["sender_agent_id"]
                        and subject.get("parent_agent_id") == parent_agent_id
                        and subject.get("relationship") == "subagent"
                        and subject.get("labels") == labels
                        and subject.get("assignment") == assignment
                        for subject in parent_subjects
                    )
                )
                if not parent_valid:
                    errors.append("identity-receipt-review-parent-authority-missing")
        elif expected_authority == "direct-child-dispatch":
            subjects = authority.get("subjects")
            if not isinstance(subjects, list):
                subjects = [
                    {
                        "agent_id": subject_agent_id,
                        "parent_agent_id": authority.get("subject_parent_agent_id"),
                        "relationship": authority.get("subject_relationship"),
                        "labels": authority.get("subject_labels"),
                        "assignment": None,
                    }
                ]
            valid_subject_ids: list[str] = []
            seen_subject_ids: set[str] = set()
            for subject in subjects:
                if not isinstance(subject, dict):
                    errors.append("identity-receipt-authority-subject-invalid")
                    continue
                child_id = subject.get("agent_id")
                child_labels = subject.get("labels")
                basic_valid = bool(
                    isinstance(child_id, str)
                    and child_id
                    and child_id != event["sender_agent_id"]
                    and child_id not in seen_subject_ids
                    and subject.get("parent_agent_id") == event["sender_agent_id"]
                    and subject.get("relationship") == "subagent"
                    and isinstance(child_labels, dict)
                    and child_labels.get("repository") == labels.get("repository")
                    and child_labels.get("campaign_id") == event["campaign_id"]
                    and child_labels.get("role") in WORKER_ROLES
                )
                if basic_valid and child_labels.get("role") == "review":
                    child_assignment = subject.get("assignment")
                    basic_valid = bool(
                        "dispatch_id" not in child_labels
                        and child_labels.get("review_axis") in REVIEW_AXES
                        and isinstance(child_assignment, dict)
                        and child_assignment.get("agent_id") == child_id
                        and child_assignment.get("campaign_id") == event["campaign_id"]
                        and child_assignment.get("review_axis")
                        == child_labels.get("review_axis")
                        and child_assignment.get("campaign_parent_agent_id")
                        == event["sender_agent_id"]
                        and child_assignment.get("read_back") is True
                        and child_assignment.get("review_lock_read_back") is True
                        and isinstance(child_assignment.get("lock"), dict)
                        and child_assignment["lock"].get("dispatch_id")
                        == event["dispatch_id"]
                    )
                elif basic_valid:
                    basic_valid = child_labels.get("dispatch_id") == event["dispatch_id"]
                if not basic_valid:
                    errors.append("identity-receipt-authority-subject-invalid")
                    continue
                seen_subject_ids.add(child_id)
                valid_subject_ids.append(child_id)
            if not valid_subject_ids:
                errors.append("identity-receipt-authority-subject-mismatch")
            if (
                event["event_type"] == "START"
                and event.get("recipient_agent_id") not in valid_subject_ids
            ):
                errors.append("identity-receipt-authority-recipient-mismatch")
        else:
            subject_labels = authority.get("subject_labels")
            if (
                not isinstance(subject_agent_id, str)
                or not subject_agent_id
                or subject_agent_id == event["sender_agent_id"]
                or authority.get("subject_parent_agent_id") != event["sender_agent_id"]
                or authority.get("subject_relationship") != "subagent"
            ):
                errors.append("identity-receipt-authority-subject-mismatch")
            if (
                event["event_type"] == "START"
                and event.get("recipient_agent_id") != subject_agent_id
            ):
                errors.append("identity-receipt-authority-recipient-mismatch")
            if not isinstance(subject_labels, dict):
                errors.append("identity-receipt-authority-subject-labels-missing")
            else:
                expected_subject_labels = {
                    "campaign_id": event["campaign_id"],
                    "repository": labels.get("repository")
                    if isinstance(labels, dict)
                    else None,
                }
                expected_subject_labels["role"] = "orchestrator"
                for field, expected in expected_subject_labels.items():
                    if subject_labels.get(field) != expected:
                        errors.append(
                            "identity-receipt-authority-subject-label-"
                            f"{field.replace('_', '-')}-mismatch"
                        )
        if authority.get("read_back") is not True:
            errors.append("identity-receipt-authority-not-read-back")
    if receipt.get("read_back") is not True:
        errors.append("identity-receipt-not-read-back")
    return sorted(set(errors))


def _event_authority_errors(
    event: dict[str, Any], receipt: dict[str, Any]
) -> list[str]:
    role = receipt.get("role")
    allowed = EVENT_ALLOWED_ROLES.get(event["event_type"], set())
    return [] if role in allowed else ["event-role-not-authorized"]


def _rejection(
    message_id: Any,
    reason: str,
    event: dict[str, Any] | None = None,
) -> dict[str, str]:
    result = {"message_id": str(message_id), "reason": reason}
    if isinstance(event, dict):
        for field in ("campaign_id", "dispatch_id", "signal_id"):
            value = event.get(field)
            if isinstance(value, str) and value:
                result[field] = value
    return result


class PaseoRoom:
    def __init__(self, runner: Runner = _default_runner):
        self._runner = runner

    def create(self, campaign_id: str, purpose: str) -> dict[str, Any]:
        room = room_name(campaign_id)
        if not purpose.strip():
            raise RoomProtocolError("room purpose is required")
        payload = _json_output(
            self._runner(["chat", "create", room, "--purpose", purpose, "--json"])
        )
        return {"room": room, "receipt": payload}

    def preflight(self, room: str, *, require_agent_identity: bool) -> dict[str, Any]:
        agent_id = os.environ.get("PASEO_AGENT_ID", "").strip()
        if require_agent_identity and not agent_id:
            raise RoomProtocolError(
                "PASEO_AGENT_ID is required for Agent-authored events"
            )
        payload = _json_output(self._runner(["chat", "inspect", room, "--json"]))
        return {"room": room, "agent_id": agent_id or None, "inspect": payload}

    def post(self, room: str, event: dict[str, Any]) -> dict[str, Any]:
        errors = validate_event(event)
        if errors:
            raise RoomProtocolError("invalid room event: " + ", ".join(errors))
        expected_room = room_name(event["campaign_id"])
        if room != expected_room:
            raise RoomProtocolError("room does not match campaign_id")
        runtime_agent_id = os.environ.get("PASEO_AGENT_ID", "").strip()
        if not runtime_agent_id:
            raise RoomProtocolError(
                "PASEO_AGENT_ID is required for Agent-authored events"
            )
        if event["sender_agent_id"] != runtime_agent_id:
            raise RoomProtocolError("sender_agent_id does not match PASEO_AGENT_ID")
        body = json.dumps(event, separators=(",", ":"), sort_keys=True)
        payload = _json_output(self._runner(["chat", "post", room, body, "--json"]))
        message_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise RoomProtocolError("chat post did not return a message UUID")
        return {"room": room, "message_id": message_id, "signal_id": event["signal_id"]}

    def replay(
        self,
        room: str,
        *,
        identity_receipts: Sequence[dict[str, Any]] | None = None,
        review_locks: Sequence[dict[str, Any]] | None = None,
        dispatch_id: str | None = None,
        consumer_role: str = "campaign",
        limit: int = DEFAULT_REPLAY_LIMIT,
    ) -> dict[str, Any]:
        if limit <= 0:
            raise RoomProtocolError("replay limit must be positive")
        if dispatch_id is not None and (
            not isinstance(dispatch_id, str) or not IDENTIFIER_RE.fullmatch(dispatch_id)
        ):
            raise RoomProtocolError("dispatch_id scope is invalid")
        if consumer_role not in {"campaign", "worker"}:
            raise RoomProtocolError("consumer_role must be campaign or worker")
        if consumer_role == "worker" and dispatch_id is None:
            raise RoomProtocolError("worker replay requires dispatch_id scope")
        receipt_lookup = _identity_receipt_lookup(identity_receipts)
        review_lock_lookup = _review_lock_lookup(review_locks)
        payload = _json_output(
            self._runner(["chat", "read", room, "--limit", str(limit), "--json"])
        )
        if not isinstance(payload, list):
            raise RoomProtocolError("chat read must return a JSON array")

        events: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        seen: dict[str, str] = {}
        seen_dispatches: dict[str, str] = {}
        last_sequence: dict[tuple[str, str], int] = {}
        terminal_dispatches: set[str] = set()
        blocked_dispatches: set[str] = set()
        asks: dict[str, dict[str, Any]] = {}
        review_rounds: dict[tuple[str, int], dict[str, Any]] = {}
        for message in payload:
            message_id = (
                message.get("id", "unknown") if isinstance(message, dict) else "unknown"
            )
            body = message.get("body") if isinstance(message, dict) else None
            try:
                event = json.loads(body) if isinstance(body, str) else None
            except json.JSONDecodeError:
                event = None
            if (
                dispatch_id is not None
                and isinstance(event, dict)
                and event.get("dispatch_id") != dispatch_id
            ):
                continue
            if (
                consumer_role == "worker"
                and isinstance(event, dict)
                and event.get("event_type") == "REVIEW_RESULT"
            ):
                continue
            errors = validate_event(event)
            if errors:
                rejected.append(_rejection(message_id, ",".join(errors), event))
                continue
            if room != room_name(event["campaign_id"]):
                rejected.append(_rejection(message_id, "room-campaign-mismatch", event))
                continue
            receipt_key = (
                event["sender_agent_id"],
                event["campaign_id"],
                event["dispatch_id"],
            )
            identity_errors = _identity_receipt_errors(
                event,
                message.get("author") if isinstance(message, dict) else None,
                receipt_lookup.get(receipt_key),
                receipt_lookup,
            )
            receipt = receipt_lookup.get(receipt_key)
            if not identity_errors and receipt is not None:
                identity_errors.extend(_event_authority_errors(event, receipt))
            if identity_errors:
                blocked_dispatches.add(event["dispatch_id"])
                rejected.append(
                    _rejection(message_id, ",".join(identity_errors), event)
                )
                continue
            canonical = json.dumps(event, separators=(",", ":"), sort_keys=True)
            signal_id = event["signal_id"]
            if signal_id in seen:
                if seen[signal_id] != canonical:
                    blocked_dispatches.update(
                        {event["dispatch_id"], seen_dispatches[signal_id]}
                    )
                    rejected.append(
                        _rejection(message_id, "duplicate-signal-conflict", event)
                    )
                continue
            sequence_key = (event["sender_agent_id"], event["dispatch_id"])
            if event["sequence"] <= last_sequence.get(sequence_key, -1):
                blocked_dispatches.add(event["dispatch_id"])
                rejected.append(_rejection(message_id, "nonmonotonic-sequence", event))
                continue
            if (
                event["event_type"] == "HEARTBEAT"
                and event["dispatch_id"] in terminal_dispatches
            ):
                rejected.append(
                    _rejection(message_id, "heartbeat-after-terminal", event)
                )
                continue
            if event["event_type"] == "REPLY":
                ask = asks.get(event["in_reply_to"])
                if (
                    ask is None
                    or ask["campaign_id"] != event["campaign_id"]
                    or ask["dispatch_id"] != event["dispatch_id"]
                    or ask.get("recipient_agent_id") != event["sender_agent_id"]
                    or event.get("recipient_agent_id") != ask["sender_agent_id"]
                ):
                    blocked_dispatches.add(event["dispatch_id"])
                    rejected.append(
                        _rejection(message_id, "reply-correlation-invalid", event)
                    )
                    continue
            if event["event_type"] == "REVIEW_RESULT":
                evidence = event["evidence"]
                authorized = review_lock_lookup.get(
                    (
                        event["campaign_id"],
                        event["dispatch_id"],
                        evidence["review_round"],
                    )
                )
                if authorized is None:
                    blocked_dispatches.add(event["dispatch_id"])
                    rejected.append(
                        _rejection(message_id, "review-lock-receipt-missing", event)
                    )
                    continue
                expected_lock = {
                    field: authorized[field]
                    for field in _review_lock_from_event(event)
                }
                if _review_lock_from_event(event) != expected_lock:
                    blocked_dispatches.add(event["dispatch_id"])
                    rejected.append(
                        _rejection(message_id, "review-lock-receipt-mismatch", event)
                    )
                    continue
                review_key = (event["dispatch_id"], evidence["review_round"])
                lock = {
                    field: evidence[field]
                    for field in (
                        "candidate_sha",
                        "base_sha",
                        "diff_sha256",
                        "acceptance_sha256",
                        "review_round",
                        "scope",
                        "previous_candidate_sha",
                    )
                }
                review_round = review_rounds.setdefault(
                    review_key, {"lock": lock, "axes": {}}
                )
                if review_round["lock"] != lock:
                    blocked_dispatches.add(event["dispatch_id"])
                    rejected.append(
                        _rejection(message_id, "review-pair-lock-mismatch", event)
                    )
                    continue
                axis = evidence["axis"]
                if axis in review_round["axes"]:
                    blocked_dispatches.add(event["dispatch_id"])
                    rejected.append(
                        _rejection(message_id, "duplicate-review-axis", event)
                    )
                    continue
                review_round["axes"][axis] = evidence
            seen[signal_id] = canonical
            seen_dispatches[signal_id] = event["dispatch_id"]
            last_sequence[sequence_key] = event["sequence"]
            if event["event_type"] in WORKER_TERMINAL_EVENTS:
                terminal_dispatches.add(event["dispatch_id"])
            if event["event_type"] == "ASK":
                asks[signal_id] = event
            events.append(
                event | {"message_id": str(message_id), "identity_verified": True}
            )
        actionable_events = [
            event for event in events if event["dispatch_id"] not in blocked_dispatches
        ]
        review_pairs: dict[str, dict[str, Any]] = {}
        for (dispatch_id, review_round), state in sorted(review_rounds.items()):
            previous = review_pairs.get(dispatch_id)
            if previous is not None and previous["review_round"] > review_round:
                continue
            axes = state["axes"]
            lock = state["lock"]
            complete = REVIEW_AXES.issubset(axes)
            review_pairs[dispatch_id] = {
                **lock,
                "status": (
                    "blocked"
                    if dispatch_id in blocked_dispatches
                    else "complete" if complete else "incomplete"
                ),
                "axes": sorted(axes),
                "verdict": (
                    "pass"
                    if complete
                    and all(axes[axis]["verdict"] == "pass" for axis in REVIEW_AXES)
                    else "fail" if complete else None
                ),
            }
        return {
            "room": room,
            "events": actionable_events,
            "rejected": rejected,
            "blocked_dispatches": sorted(blocked_dispatches),
            "review_pairs": review_pairs,
        }

    def wait(
        self,
        room: str,
        *,
        timeout: str,
        identity_receipts: Sequence[dict[str, Any]] | None = None,
        review_locks: Sequence[dict[str, Any]] | None = None,
        dispatch_id: str | None = None,
        consumer_role: str = "campaign",
        limit: int = DEFAULT_REPLAY_LIMIT,
    ) -> dict[str, Any]:
        _timeout_seconds(timeout)
        _json_output(
            self._runner(["chat", "wait", room, "--timeout", timeout, "--json"])
        )
        return self.replay(
            room,
            identity_receipts=identity_receipts,
            review_locks=review_locks,
            dispatch_id=dispatch_id,
            consumer_role=consumer_role,
            limit=limit,
        )

    def close(self, room: str) -> dict[str, Any]:
        payload = _json_output(self._runner(["chat", "delete", room, "--json"]))
        return {"room": room, "receipt": payload}


def _read_event(path: Path | None) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8") if path else sys.stdin.read()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RoomProtocolError("event input must be a JSON object")
    return payload


def _read_identity_receipts(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or any(
        not isinstance(item, dict) for item in payload
    ):
        raise RoomProtocolError("identity receipts must be a JSON array of objects")
    return payload


def _read_review_locks(path: Path | None) -> list[dict[str, Any]] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or any(
        not isinstance(item, dict) for item in payload
    ):
        raise RoomProtocolError("review locks must be a JSON array of objects")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--campaign-id", required=True)
    create.add_argument("--purpose", required=True)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--room", required=True)
    preflight.add_argument("--require-agent-identity", action="store_true")
    post = subparsers.add_parser("post")
    post.add_argument("--room", required=True)
    post.add_argument("--input", type=Path)
    identity_plan = subparsers.add_parser("identity-plan")
    identity_plan.add_argument("--snapshot", type=Path, required=True)
    identity_plan.add_argument("--receipts-output", type=Path, required=True)
    replay = subparsers.add_parser("replay")
    replay.add_argument("--room", required=True)
    replay.add_argument("--identity-receipts", type=Path, required=True)
    replay.add_argument("--review-locks", type=Path)
    replay.add_argument("--dispatch-id")
    replay.add_argument(
        "--consumer-role", choices=("campaign", "worker"), default="campaign"
    )
    replay.add_argument("--limit", type=int, default=DEFAULT_REPLAY_LIMIT)
    wait = subparsers.add_parser("wait")
    wait.add_argument("--room", required=True)
    wait.add_argument("--timeout", required=True)
    wait.add_argument("--identity-receipts", type=Path, required=True)
    wait.add_argument("--review-locks", type=Path)
    wait.add_argument("--dispatch-id")
    wait.add_argument(
        "--consumer-role", choices=("campaign", "worker"), default="campaign"
    )
    wait.add_argument("--limit", type=int, default=DEFAULT_REPLAY_LIMIT)
    close = subparsers.add_parser("close")
    close.add_argument("--room", required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    protocol = PaseoRoom()
    try:
        if arguments.command == "create":
            result = protocol.create(arguments.campaign_id, arguments.purpose)
        elif arguments.command == "preflight":
            result = protocol.preflight(
                arguments.room,
                require_agent_identity=arguments.require_agent_identity,
            )
        elif arguments.command == "post":
            result = protocol.post(arguments.room, _read_event(arguments.input))
        elif arguments.command == "identity-plan":
            result = identity_receipt_plan(
                json.loads(arguments.snapshot.read_text(encoding="utf-8"))
            )
            arguments.receipts_output.write_text(
                json.dumps(result["receipts"], indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        elif arguments.command == "replay":
            result = protocol.replay(
                arguments.room,
                identity_receipts=_read_identity_receipts(arguments.identity_receipts),
                review_locks=_read_review_locks(arguments.review_locks),
                dispatch_id=arguments.dispatch_id,
                consumer_role=arguments.consumer_role,
                limit=arguments.limit,
            )
        elif arguments.command == "wait":
            result = protocol.wait(
                arguments.room,
                timeout=arguments.timeout,
                identity_receipts=_read_identity_receipts(arguments.identity_receipts),
                review_locks=_read_review_locks(arguments.review_locks),
                dispatch_id=arguments.dispatch_id,
                consumer_role=arguments.consumer_role,
                limit=arguments.limit,
            )
        else:
            result = protocol.close(arguments.room)
    except (OSError, json.JSONDecodeError, RoomProtocolError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
