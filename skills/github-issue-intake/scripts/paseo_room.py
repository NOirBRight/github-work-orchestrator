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
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TIMEOUT_RE = re.compile(r"^([1-9][0-9]*)(ms|s|m)$")
HEARTBEAT_PHASES = {"analysis", "implementation", "verification", "review-fix"}
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


def _identity_receipt_errors(
    event: dict[str, Any],
    message_author: Any,
    receipt: dict[str, Any] | None,
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
        if role in WORKER_ROLES:
            expected_labels.extend(
                [
                    ("campaign_id", event["campaign_id"]),
                    ("dispatch_id", event["dispatch_id"]),
                ]
            )
        elif role == "orchestrator":
            expected_labels.append(("campaign_id", event["campaign_id"]))
        for field, expected in expected_labels:
            if labels.get(field) != expected:
                errors.append(
                    f"identity-receipt-label-{field.replace('_', '-')}-mismatch"
                )
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
        if role in WORKER_ROLES:
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
        if expected_authority in {"dispatch-owner", "campaign-control"}:
            if subject_agent_id != event["sender_agent_id"]:
                errors.append("identity-receipt-authority-subject-mismatch")
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
                if expected_authority == "direct-child-dispatch":
                    expected_subject_labels["dispatch_id"] = event["dispatch_id"]
                else:
                    expected_subject_labels["role"] = "orchestrator"
                for field, expected in expected_subject_labels.items():
                    if subject_labels.get(field) != expected:
                        errors.append(
                            "identity-receipt-authority-subject-label-"
                            f"{field.replace('_', '-')}-mismatch"
                        )
                if (
                    expected_authority == "direct-child-dispatch"
                    and subject_labels.get("role") not in WORKER_ROLES
                ):
                    errors.append(
                        "identity-receipt-authority-subject-label-role-mismatch"
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
        limit: int = DEFAULT_REPLAY_LIMIT,
    ) -> dict[str, Any]:
        if limit <= 0:
            raise RoomProtocolError("replay limit must be positive")
        receipt_lookup = _identity_receipt_lookup(identity_receipts)
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
        for message in payload:
            message_id = (
                message.get("id", "unknown") if isinstance(message, dict) else "unknown"
            )
            body = message.get("body") if isinstance(message, dict) else None
            try:
                event = json.loads(body) if isinstance(body, str) else None
            except json.JSONDecodeError:
                event = None
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
        return {
            "room": room,
            "events": actionable_events,
            "rejected": rejected,
            "blocked_dispatches": sorted(blocked_dispatches),
        }

    def wait(
        self,
        room: str,
        *,
        timeout: str,
        identity_receipts: Sequence[dict[str, Any]] | None = None,
        limit: int = DEFAULT_REPLAY_LIMIT,
    ) -> dict[str, Any]:
        _timeout_seconds(timeout)
        _json_output(
            self._runner(["chat", "wait", room, "--timeout", timeout, "--json"])
        )
        return self.replay(room, identity_receipts=identity_receipts, limit=limit)

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
    replay = subparsers.add_parser("replay")
    replay.add_argument("--room", required=True)
    replay.add_argument("--identity-receipts", type=Path, required=True)
    replay.add_argument("--limit", type=int, default=DEFAULT_REPLAY_LIMIT)
    wait = subparsers.add_parser("wait")
    wait.add_argument("--room", required=True)
    wait.add_argument("--timeout", required=True)
    wait.add_argument("--identity-receipts", type=Path, required=True)
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
        elif arguments.command == "replay":
            result = protocol.replay(
                arguments.room,
                identity_receipts=_read_identity_receipts(arguments.identity_receipts),
                limit=arguments.limit,
            )
        elif arguments.command == "wait":
            result = protocol.wait(
                arguments.room,
                timeout=arguments.timeout,
                identity_receipts=_read_identity_receipts(arguments.identity_receipts),
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
