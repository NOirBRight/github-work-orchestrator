#!/usr/bin/env python3
"""Validated repository-level mailbox protocol for GWO Operator Relays."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Callable, Sequence

from request_safety import text_is_sensitive


SCHEMA_VERSION = 1
DEFAULT_REPLAY_LIMIT = 200
MAX_SUMMARY_CHARS = 500
EVENT_TYPES = {"OPERATOR_REQUEST", "REQUEST_ACCEPTED", "REQUEST_REJECTED"}
EVENT_ALLOWED_ROLES = {
    "OPERATOR_REQUEST": {"operator-relay"},
    "REQUEST_ACCEPTED": {"repository-coordinator"},
    "REQUEST_REJECTED": {"repository-coordinator"},
}
TOP_LEVEL_FIELDS = {
    "schema_version",
    "repository",
    "signal_id",
    "sequence",
    "event_type",
    "sender_agent_id",
    "sender_role",
    "in_reply_to",
    "payload",
}
REPOSITORY_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
SIGNAL_RE = re.compile(r"^repo-(?:request|response)-[A-Za-z0-9][A-Za-z0-9-]{7,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RoomProtocolError(RuntimeError):
    pass


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _windows_cmd_command(executable: str, arguments: Sequence[str]) -> list[str]:
    return [
        os.environ.get("COMSPEC", "cmd.exe"),
        "/d",
        "/c",
        executable,
        *arguments,
    ]


def _repository(value: Any) -> str:
    if not isinstance(value, str) or not REPOSITORY_RE.fullmatch(value.strip()):
        raise RoomProtocolError("repository must be owner/repo")
    return value.strip()


def room_name(repository: str) -> str:
    repository = _repository(repository).lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", repository).strip("-")
    digest = hashlib.sha256(repository.encode("utf-8")).hexdigest()[:12]
    return f"gwo-repo-{slug[:80].rstrip('-')}-{digest}"


def _text_is_sensitive(value: str) -> bool:
    return text_is_sensitive(value)


def validate_event(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["event-must-be-object"]
    errors: list[str] = []
    if set(value) != TOP_LEVEL_FIELDS:
        errors.append("event-fields-invalid")
    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append("invalid-schema-version")
    repository = value.get("repository")
    if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository):
        errors.append("invalid-repository")
    signal_id = value.get("signal_id")
    if not isinstance(signal_id, str) or not SIGNAL_RE.fullmatch(signal_id):
        errors.append("invalid-signal-id")
    sequence = value.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        errors.append("invalid-sequence")
    event_type = value.get("event_type")
    if event_type not in EVENT_TYPES:
        errors.append("invalid-event-type")
    sender_agent_id = value.get("sender_agent_id")
    if not isinstance(sender_agent_id, str) or not IDENTIFIER_RE.fullmatch(sender_agent_id):
        errors.append("invalid-sender-agent-id")
    sender_role = value.get("sender_role")
    if sender_role not in {"operator-relay", "repository-coordinator"}:
        errors.append("invalid-sender-role")
    elif event_type in EVENT_TYPES and sender_role not in EVENT_ALLOWED_ROLES[event_type]:
        errors.append("event-role-not-authorized")
    in_reply_to = value.get("in_reply_to")
    if in_reply_to is not None and (
        not isinstance(in_reply_to, str) or not SIGNAL_RE.fullmatch(in_reply_to)
    ):
        errors.append("invalid-in-reply-to")
    payload = value.get("payload")
    if not isinstance(payload, dict):
        errors.append("payload-must-be-object")
        return sorted(set(errors))

    if event_type == "OPERATOR_REQUEST":
        if in_reply_to is not None:
            errors.append("operator-request-must-not-reply")
        if set(payload) != {"summary", "original_message_sha256"}:
            errors.append("operator-request-payload-fields-invalid")
        summary = payload.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            errors.append("operator-request-summary-invalid")
        elif len(summary.strip()) > MAX_SUMMARY_CHARS:
            errors.append("operator-request-summary-too-long")
        elif _text_is_sensitive(summary):
            errors.append("operator-request-payload-sensitive")
        digest = payload.get("original_message_sha256")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            errors.append("operator-request-digest-invalid")
    elif event_type == "REQUEST_ACCEPTED":
        if not isinstance(in_reply_to, str):
            errors.append("repository-response-requires-in-reply-to")
        if set(payload) != {"disposition"}:
            errors.append("request-accepted-payload-fields-invalid")
        disposition = payload.get("disposition")
        if disposition not in {"queued", "duplicate", "already-active"}:
            errors.append("request-accepted-disposition-invalid")
    elif event_type == "REQUEST_REJECTED":
        if not isinstance(in_reply_to, str):
            errors.append("repository-response-requires-in-reply-to")
        if set(payload) != {"reason"}:
            errors.append("request-rejected-payload-fields-invalid")
        reason = payload.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 200:
            errors.append("request-rejected-reason-invalid")
        elif _text_is_sensitive(reason):
            errors.append("request-rejected-payload-sensitive")
    return sorted(set(errors))


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


def _receipt_lookup(receipts: Sequence[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    if receipts is None or isinstance(receipts, (str, bytes)) or not isinstance(receipts, Sequence):
        raise RoomProtocolError("identity receipts must be a JSON array")
    result: dict[str, dict[str, Any]] = {}
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, dict):
            raise RoomProtocolError(f"identity receipt {index} must be an object")
        agent_id = receipt.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            raise RoomProtocolError(f"identity receipt {index} requires agent_id")
        if agent_id in result:
            raise RoomProtocolError("duplicate identity receipt")
        result[agent_id] = receipt
    return result


def _identity_errors(
    event: dict[str, Any], message_author: Any, receipt: dict[str, Any] | None
) -> list[str]:
    if message_author != event["sender_agent_id"]:
        return ["message-author-mismatch"]
    if receipt is None:
        return ["identity-receipt-missing"]
    errors: list[str] = []
    for field in ("agent_id", "repository", "role"):
        expected = event["sender_agent_id"] if field == "agent_id" else event[field if field != "role" else "sender_role"]
        if receipt.get(field) != expected:
            errors.append(f"identity-receipt-{field}-mismatch")
    labels = receipt.get("labels")
    if not isinstance(labels, dict):
        errors.append("identity-receipt-labels-missing")
    else:
        for field, expected in (
            ("repository", event["repository"]),
            ("role", event["sender_role"]),
        ):
            if labels.get(field) != expected:
                errors.append(f"identity-receipt-label-{field}-mismatch")
    if receipt.get("read_back") is not True:
        errors.append("identity-receipt-not-read-back")
    relationship = receipt.get("relationship")
    parent_agent_id = receipt.get("parent_agent_id")
    if event["sender_role"] == "repository-coordinator":
        if relationship != "root" or parent_agent_id is not None:
            errors.append("identity-receipt-coordinator-parentage-invalid")
    elif relationship not in {"root", "subagent"}:
        errors.append("identity-receipt-relationship-invalid")
    elif relationship == "subagent" and (
        not isinstance(parent_agent_id, str) or not parent_agent_id
    ):
        errors.append("identity-receipt-parent-missing")
    return sorted(set(errors))


def _rejection(message_id: Any, reason: str, event: Any = None) -> dict[str, str]:
    result = {"message_id": str(message_id), "reason": reason}
    if isinstance(event, dict):
        for field in ("repository", "signal_id", "sender_agent_id"):
            if isinstance(event.get(field), str):
                result[field] = event[field]
    return result


class RepositoryRoom:
    def __init__(self, runner: Runner = _default_runner):
        self._runner = runner

    def create(self, repository: str, purpose: str) -> dict[str, Any]:
        room = room_name(repository)
        if not isinstance(purpose, str) or not purpose.strip():
            raise RoomProtocolError("room purpose is required")
        receipt = _json_output(
            self._runner(["chat", "create", room, "--purpose", purpose, "--json"])
        )
        return {"room": room, "receipt": receipt}

    def post(self, room: str, event: dict[str, Any]) -> dict[str, Any]:
        errors = validate_event(event)
        if errors:
            raise RoomProtocolError("invalid repository event: " + ", ".join(errors))
        if room != room_name(event["repository"]):
            raise RoomProtocolError("room does not match repository")
        runtime_agent_id = os.environ.get("PASEO_AGENT_ID", "").strip()
        if not runtime_agent_id:
            raise RoomProtocolError("PASEO_AGENT_ID is required for Agent-authored events")
        if runtime_agent_id != event["sender_agent_id"]:
            raise RoomProtocolError("sender_agent_id does not match PASEO_AGENT_ID")
        body = json.dumps(event, separators=(",", ":"), sort_keys=True)
        receipt = _json_output(self._runner(["chat", "post", room, body, "--json"]))
        message_id = receipt.get("id") if isinstance(receipt, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise RoomProtocolError("chat post did not return a message UUID")
        return {"room": room, "message_id": message_id, "signal_id": event["signal_id"]}

    def replay(
        self,
        room: str,
        *,
        identity_receipts: Sequence[dict[str, Any]],
        limit: int = DEFAULT_REPLAY_LIMIT,
    ) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise RoomProtocolError("replay limit must be positive")
        receipts = _receipt_lookup(identity_receipts)
        messages = _json_output(
            self._runner(["chat", "read", room, "--limit", str(limit), "--json"])
        )
        if not isinstance(messages, list):
            raise RoomProtocolError("chat read must return a JSON array")
        events: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        seen: dict[str, str] = {}
        seen_senders: dict[str, str] = {}
        last_sequence: dict[str, int] = {}
        blocked_senders: set[str] = set()
        poisoned_requests: set[str] = set()
        requests: dict[str, dict[str, Any]] = {}
        for message in messages:
            message_id = message.get("id", "unknown") if isinstance(message, dict) else "unknown"
            body = message.get("body") if isinstance(message, dict) else None
            try:
                event = json.loads(body) if isinstance(body, str) else None
            except json.JSONDecodeError:
                event = None
            errors = validate_event(event)
            if errors:
                if isinstance(event, dict) and event.get("event_type") == "OPERATOR_REQUEST":
                    signal_id = event.get("signal_id")
                    if isinstance(signal_id, str):
                        poisoned_requests.add(signal_id)
                rejected.append(_rejection(message_id, ",".join(errors), event))
                continue
            assert isinstance(event, dict)
            if room != room_name(event["repository"]):
                rejected.append(_rejection(message_id, "room-repository-mismatch", event))
                continue
            identity_errors = _identity_errors(
                event,
                message.get("author") if isinstance(message, dict) else None,
                receipts.get(event["sender_agent_id"]),
            )
            if identity_errors:
                blocked_senders.add(event["sender_agent_id"])
                if event["event_type"] == "OPERATOR_REQUEST":
                    poisoned_requests.add(event["signal_id"])
                rejected.append(_rejection(message_id, ",".join(identity_errors), event))
                continue
            canonical = json.dumps(event, separators=(",", ":"), sort_keys=True)
            signal_id = event["signal_id"]
            if signal_id in seen:
                if seen[signal_id] != canonical:
                    blocked_senders.update({event["sender_agent_id"], seen_senders[signal_id]})
                    if (
                        event["event_type"] == "OPERATOR_REQUEST"
                        or signal_id in requests
                    ):
                        poisoned_requests.add(signal_id)
                    rejected.append(_rejection(message_id, "duplicate-signal-conflict", event))
                continue
            sender = event["sender_agent_id"]
            if event["sequence"] <= last_sequence.get(sender, 0):
                blocked_senders.add(sender)
                if event["event_type"] == "OPERATOR_REQUEST":
                    poisoned_requests.add(signal_id)
                rejected.append(_rejection(message_id, "nonmonotonic-sequence", event))
                continue
            if event["event_type"] in {"REQUEST_ACCEPTED", "REQUEST_REJECTED"}:
                if event["in_reply_to"] in poisoned_requests:
                    rejected.append(_rejection(message_id, "repository-request-poisoned", event))
                    continue
                request = requests.get(event["in_reply_to"])
                if request is None or request["repository"] != event["repository"]:
                    blocked_senders.add(sender)
                    rejected.append(_rejection(message_id, "repository-response-correlation-invalid", event))
                    continue
            seen[signal_id] = canonical
            seen_senders[signal_id] = sender
            last_sequence[sender] = event["sequence"]
            if event["event_type"] == "OPERATOR_REQUEST":
                requests[signal_id] = event
            events.append(event | {"message_id": str(message_id), "identity_verified": True})
        actionable: list[dict[str, Any]] = []
        for event in events:
            if event["sender_agent_id"] in blocked_senders:
                continue
            if (
                event["event_type"] in {"REQUEST_ACCEPTED", "REQUEST_REJECTED"}
                and event["in_reply_to"] in poisoned_requests
            ):
                rejected.append(
                    _rejection(event["message_id"], "repository-request-poisoned", event)
                )
                continue
            if event["event_type"] == "OPERATOR_REQUEST" and event["signal_id"] in poisoned_requests:
                continue
            actionable.append(event)
        return {
            "room": room,
            "events": actionable,
            "rejected": rejected,
            "blocked_senders": sorted(blocked_senders),
            "blocked_requests": sorted(poisoned_requests),
        }

    def wait(
        self,
        room: str,
        *,
        timeout: str,
        identity_receipts: Sequence[dict[str, Any]],
        limit: int = DEFAULT_REPLAY_LIMIT,
    ) -> dict[str, Any]:
        if timeout not in {f"{seconds}s" for seconds in range(1, 61)}:
            raise RoomProtocolError("wait timeout must be 1s through 60s")
        _json_output(self._runner(["chat", "wait", room, "--timeout", timeout, "--json"]))
        return self.replay(room, identity_receipts=identity_receipts, limit=limit)


def _read_json(path: Path | None) -> Any:
    raw = path.read_text(encoding="utf-8") if path else sys.stdin.read()
    return json.loads(raw)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--repository", required=True)
    create.add_argument("--purpose", required=True)
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
    return parser


def main() -> int:
    args = _parser().parse_args()
    protocol = RepositoryRoom()
    try:
        if args.command == "create":
            result = protocol.create(args.repository, args.purpose)
        elif args.command == "post":
            result = protocol.post(args.room, _read_json(args.input))
        elif args.command == "replay":
            result = protocol.replay(
                args.room,
                identity_receipts=_read_json(args.identity_receipts),
                limit=args.limit,
            )
        else:
            result = protocol.wait(
                args.room,
                timeout=args.timeout,
                identity_receipts=_read_json(args.identity_receipts),
                limit=args.limit,
            )
    except (OSError, json.JSONDecodeError, RoomProtocolError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
