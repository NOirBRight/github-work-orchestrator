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
    "DISCUSSION_REQUIRED",
    "BLOCKED",
    "PR_OPENED",
    "READY_FOR_REVIEW",
    "REVIEW_RESULT",
    "COMPLETED",
    "STOPPED",
    "CHECKPOINT",
    "CAMPAIGN_CLOSED",
}
CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{5,63}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


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
        "evidence",
        "next_action",
    ):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            errors.append(f"missing-or-empty:{field}")
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
    return sorted(set(errors))


def _default_runner(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("paseo")
    if not executable:
        raise RoomProtocolError("paseo CLI is not available on PATH")
    command = [executable, *arguments]
    if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        command = _windows_cmd_command(executable, arguments)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def _json_output(completed: subprocess.CompletedProcess[str]) -> Any:
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RoomProtocolError(f"Paseo CLI failed: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RoomProtocolError("Paseo CLI returned invalid JSON") from error


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
            raise RoomProtocolError("PASEO_AGENT_ID is required for Agent-authored events")
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
        if runtime_agent_id and event["sender_agent_id"] != runtime_agent_id:
            raise RoomProtocolError("sender_agent_id does not match PASEO_AGENT_ID")
        body = json.dumps(event, separators=(",", ":"), sort_keys=True)
        payload = _json_output(
            self._runner(["chat", "post", room, body, "--json"])
        )
        message_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise RoomProtocolError("chat post did not return a message UUID")
        return {"room": room, "message_id": message_id, "signal_id": event["signal_id"]}

    def replay(self, room: str, *, limit: int = DEFAULT_REPLAY_LIMIT) -> dict[str, Any]:
        if limit <= 0:
            raise RoomProtocolError("replay limit must be positive")
        payload = _json_output(
            self._runner(["chat", "read", room, "--limit", str(limit), "--json"])
        )
        if not isinstance(payload, list):
            raise RoomProtocolError("chat read must return a JSON array")

        events: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        seen: dict[str, str] = {}
        for message in payload:
            message_id = message.get("id", "unknown") if isinstance(message, dict) else "unknown"
            body = message.get("body") if isinstance(message, dict) else None
            try:
                event = json.loads(body) if isinstance(body, str) else None
            except json.JSONDecodeError:
                event = None
            errors = validate_event(event)
            if errors:
                rejected.append({"message_id": str(message_id), "reason": ",".join(errors)})
                continue
            canonical = json.dumps(event, separators=(",", ":"), sort_keys=True)
            signal_id = event["signal_id"]
            if signal_id in seen:
                if seen[signal_id] != canonical:
                    rejected.append(
                        {"message_id": str(message_id), "reason": "duplicate-signal-conflict"}
                    )
                continue
            seen[signal_id] = canonical
            events.append(event | {"message_id": str(message_id)})
        return {"room": room, "events": events, "rejected": rejected}

    def wait(
        self,
        room: str,
        *,
        timeout: str,
        limit: int = DEFAULT_REPLAY_LIMIT,
    ) -> dict[str, Any]:
        if not timeout.strip():
            raise RoomProtocolError("timeout is required")
        _json_output(self._runner(["chat", "wait", room, "--timeout", timeout, "--json"]))
        return self.replay(room, limit=limit)

    def close(self, room: str) -> dict[str, Any]:
        payload = _json_output(self._runner(["chat", "delete", room, "--json"]))
        return {"room": room, "receipt": payload}


def _read_event(path: Path | None) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8") if path else sys.stdin.read()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RoomProtocolError("event input must be a JSON object")
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
    replay.add_argument("--limit", type=int, default=DEFAULT_REPLAY_LIMIT)
    wait = subparsers.add_parser("wait")
    wait.add_argument("--room", required=True)
    wait.add_argument("--timeout", required=True)
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
            result = protocol.replay(arguments.room, limit=arguments.limit)
        elif arguments.command == "wait":
            result = protocol.wait(
                arguments.room, timeout=arguments.timeout, limit=arguments.limit
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
