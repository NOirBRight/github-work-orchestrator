#!/usr/bin/env python3
"""Plan fail-closed delivery from a durable GWO Room event to one Agent."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = 1
REPOSITORY_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{5,63}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
MESSAGE_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
ROLES = {
    "repository-coordinator",
    "orchestrator",
    "intake",
    "implementation",
    "review",
    "monitor",
}
RECIPIENT_STATUSES = {"idle", "running", "initializing", "error", "closed"}
DELIVERY_STATES = {"pending", "wake-sent", "acknowledged"}
VISIBILITY_ONLY_EVENTS = {"PROGRESS", "HEARTBEAT"}
SOURCE_EVENT_TYPES = {
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
AUTHORITY_SCOPES = {
    "worker-dispatch",
    "review-dispatch",
    "campaign-control",
    "campaign-admission",
}


def _protected(*blockers: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "protected",
        "automatic_execution": False,
        "actions": [],
        "blockers": sorted(set(blockers)),
    }


def _require_text(name: str, value: Any, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{name} is invalid")
    return value


def _agent(value: Any, *, name: str, repository: str, campaign_id: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    agent_id = _require_text(f"{name}.agent_id", value.get("agent_id"), IDENTIFIER_RE)
    labels = value.get("labels")
    if not isinstance(labels, dict):
        raise ValueError(f"{name}.labels must be an object")
    role = labels.get("role")
    if labels.get("repository") != repository or role not in ROLES:
        raise ValueError(f"{name} repository/role evidence is invalid")
    if role != "repository-coordinator" and labels.get("campaign_id") != campaign_id:
        raise ValueError(f"{name} campaign evidence is invalid")
    if role == "repository-coordinator" and "campaign_id" in labels:
        raise ValueError(f"{name} root must not claim one campaign")
    if value.get("read_back") is not True:
        raise ValueError(f"{name} must be read back")
    if not isinstance(value.get("archived"), bool):
        raise ValueError(f"{name}.archived must be boolean")
    status = value.get("status")
    if status not in RECIPIENT_STATUSES:
        raise ValueError(f"{name}.status is invalid")
    relationship = value.get("relationship")
    parent_agent_id = value.get("parent_agent_id")
    if relationship == "root":
        if parent_agent_id is not None:
            raise ValueError(f"{name} root parent evidence is invalid")
    elif relationship == "subagent":
        _require_text(f"{name}.parent_agent_id", parent_agent_id, IDENTIFIER_RE)
    else:
        raise ValueError(f"{name}.relationship is invalid")
    return {
        "agent_id": agent_id,
        "status": status,
        "archived": value["archived"],
        "parent_agent_id": parent_agent_id,
        "relationship": relationship,
        "role": role,
    }


def _directly_related(sender: dict[str, Any], recipient: dict[str, Any]) -> bool:
    return bool(
        (
            sender["relationship"] == "subagent"
            and sender["parent_agent_id"] == recipient["agent_id"]
        )
        or (
            recipient["relationship"] == "subagent"
            and recipient["parent_agent_id"] == sender["agent_id"]
        )
    )


def _scope_matches_roles(
    authority_scope: str, sender: dict[str, Any], recipient: dict[str, Any]
) -> bool:
    roles = {sender["role"], recipient["role"]}
    if authority_scope == "worker-dispatch":
        return "orchestrator" in roles and bool(
            roles & {"implementation", "intake", "monitor"}
        )
    if authority_scope == "review-dispatch":
        return roles == {"orchestrator", "review"}
    return roles == {"repository-coordinator", "orchestrator"}


def _delivery_id(delivery: dict[str, Any]) -> str:
    identity = {
        field: delivery.get(field)
        for field in (
            "room",
            "message_id",
            "signal_id",
            "sender_agent_id",
            "recipient_agent_id",
            "authority_scope",
        )
    }
    digest = hashlib.sha256(
        json.dumps(identity, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"delivery-{digest[:24]}"


def _state_receipts_valid(delivery: dict[str, Any]) -> bool:
    state = delivery["state"]
    expected_delivery_id = _delivery_id(delivery)
    supplied_delivery_id = delivery.get("delivery_id")
    if supplied_delivery_id is not None and supplied_delivery_id != expected_delivery_id:
        return False
    wake_signal = delivery.get("wake_signal_id")
    wake_message = delivery.get("wake_message_id")
    ack_signal = delivery.get("ack_signal_id")
    ack_message = delivery.get("ack_message_id")
    wake_fields_present = wake_signal is not None or wake_message is not None
    ack_fields_present = ack_signal is not None or ack_message is not None
    wake_valid = bool(
        supplied_delivery_id == expected_delivery_id
        and wake_signal
        == f"delivery-wake-{expected_delivery_id.removeprefix('delivery-')}"
        and isinstance(wake_message, str)
        and MESSAGE_ID_RE.fullmatch(wake_message)
    )
    ack_valid = bool(
        supplied_delivery_id == expected_delivery_id
        and ack_signal
        == f"delivery-ack-{expected_delivery_id.removeprefix('delivery-')}"
        and isinstance(ack_message, str)
        and MESSAGE_ID_RE.fullmatch(ack_message)
    )
    if state == "pending":
        return not wake_fields_present and not ack_fields_present
    if state == "wake-sent":
        return wake_valid and not ack_fields_present
    return ack_valid and (not wake_fields_present or wake_valid)


def delivery_plan(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("snapshot schema_version must be 1")
    repository = _require_text("repository", snapshot.get("repository"), REPOSITORY_RE)
    campaign_id = _require_text("campaign_id", snapshot.get("campaign_id"), CAMPAIGN_RE)
    delivery = snapshot.get("delivery")
    if not isinstance(delivery, dict):
        raise ValueError("delivery must be an object")
    state = delivery.get("state")
    if state not in DELIVERY_STATES:
        raise ValueError("delivery.state is invalid")
    event_type = delivery.get("event_type")
    if event_type not in SOURCE_EVENT_TYPES:
        raise ValueError("delivery.event_type is invalid")
    authority_scope = delivery.get("authority_scope")
    if authority_scope not in AUTHORITY_SCOPES:
        raise ValueError("delivery.authority_scope is invalid")
    room = _require_text("delivery.room", delivery.get("room"), IDENTIFIER_RE)
    if room != f"gwo-{campaign_id}":
        raise ValueError("delivery room does not match campaign_id")
    if event_type in VISIBILITY_ONLY_EVENTS:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "not-required",
            "automatic_execution": False,
            "actions": [],
            "blockers": [],
        }
    signal_id = _require_text(
        "delivery.signal_id", delivery.get("signal_id"), IDENTIFIER_RE
    )
    message_id = _require_text(
        "delivery.message_id", delivery.get("message_id"), MESSAGE_ID_RE
    )
    sender_id = _require_text(
        "delivery.sender_agent_id", delivery.get("sender_agent_id"), IDENTIFIER_RE
    )
    recipient_id = _require_text(
        "delivery.recipient_agent_id",
        delivery.get("recipient_agent_id"),
        IDENTIFIER_RE,
    )
    _require_text(
        "delivery.dispatch_id", delivery.get("dispatch_id"), IDENTIFIER_RE
    )
    issue = delivery.get("issue")
    if not isinstance(issue, str) or not issue.strip():
        raise ValueError("delivery.issue is invalid")
    if not _state_receipts_valid(delivery):
        return _protected("delivery-state-receipt-invalid")
    if delivery.get("identity_verified") is not True:
        return _protected("source-event-identity-not-verified")
    sender = _agent(
        snapshot.get("sender"),
        name="sender",
        repository=repository,
        campaign_id=campaign_id,
    )
    recipient = _agent(
        snapshot.get("recipient"),
        name="recipient",
        repository=repository,
        campaign_id=campaign_id,
    )
    blockers: list[str] = []
    if sender_id != sender["agent_id"]:
        blockers.append("sender-readback-mismatch")
    if recipient_id != recipient["agent_id"]:
        blockers.append("recipient-readback-mismatch")
    if sender_id == recipient_id:
        blockers.append("self-delivery-forbidden")
    if not _directly_related(sender, recipient):
        blockers.append("delivery-target-not-direct-relative")
    if not _scope_matches_roles(authority_scope, sender, recipient):
        blockers.append("delivery-authority-scope-mismatch")
    if blockers:
        return _protected(*blockers)
    if state == "acknowledged":
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "delivered",
            "automatic_execution": False,
            "actions": [],
            "blockers": [],
        }
    if sender["archived"] or recipient["archived"]:
        return _protected("delivery-agent-archived")
    if recipient["status"] in {"error", "closed"}:
        return _protected("recipient-not-wakeable")
    if state == "wake-sent":
        if recipient["status"] == "idle":
            return _protected("wake-unacknowledged-recipient-idle")
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "awaiting-ack",
            "automatic_execution": True,
            "actions": [
                {
                    "action": "wait-for-delivery-ack",
                    "room": room,
                    "signal_id": signal_id,
                    "timeout": "60s",
                }
            ],
            "blockers": [],
        }
    if recipient["status"] == "idle":
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "wake-required",
            "automatic_execution": True,
            "actions": [
                {
                    "action": "send-signal-only",
                    "agent_id": recipient_id,
                    "prompt": (
                        f"GWO_WAKE room={room} signal={signal_id} message={message_id}"
                    ),
                }
            ],
            "blockers": [],
        }
    if recipient["status"] in {"running", "initializing"}:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "awaiting-ack",
            "automatic_execution": True,
            "actions": [
                {
                    "action": "wait-for-delivery-ack",
                    "room": room,
                    "signal_id": signal_id,
                    "timeout": "60s",
                }
            ],
            "blockers": [],
        }
    return _protected("recipient-not-wakeable")


def ack_event_plan(snapshot: Any) -> dict[str, Any]:
    validation = delivery_plan(snapshot)
    if validation["status"] == "protected":
        return validation
    if validation["status"] == "not-required":
        return _protected("delivery-ack-not-required")
    delivery = snapshot["delivery"]
    issue = delivery.get("issue")
    if not isinstance(issue, str) or not issue.strip():
        raise ValueError("delivery.issue is invalid")
    dispatch_id = _require_text(
        "delivery.dispatch_id", delivery.get("dispatch_id"), IDENTIFIER_RE
    )
    next_sequence = snapshot.get("next_sequence")
    if (
        not isinstance(next_sequence, int)
        or isinstance(next_sequence, bool)
        or next_sequence < 0
    ):
        raise ValueError("next_sequence is invalid")
    delivery_id = _delivery_id(delivery)
    event = {
        "schema_version": 1,
        "signal_id": f"delivery-ack-{delivery_id.removeprefix('delivery-')}",
        "campaign_id": snapshot["campaign_id"],
        "dispatch_id": dispatch_id,
        "sequence": next_sequence,
        "event_type": "DELIVERY_ACK",
        "issue": issue,
        "sender_agent_id": delivery["recipient_agent_id"],
        "recipient_agent_id": delivery["sender_agent_id"],
        "in_reply_to": delivery["signal_id"],
        "evidence": {
            "delivery_id": delivery_id,
            "source_message_id": delivery["message_id"],
            "source_signal_id": delivery["signal_id"],
            "source_sender_agent_id": delivery["sender_agent_id"],
            "source_recipient_agent_id": delivery["recipient_agent_id"],
            "authority_scope": delivery["authority_scope"],
        },
        "next_action": "reconcile acknowledged source event",
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "eligible",
        "automatic_execution": True,
        "event": event,
        "blockers": [],
    }


def wake_receipt_event_plan(snapshot: Any) -> dict[str, Any]:
    validation = delivery_plan(snapshot)
    if validation["status"] == "protected":
        return validation
    if validation["status"] != "wake-required":
        return _protected("wake-not-authorized")
    delivery = snapshot["delivery"]
    wake_result = snapshot.get("wake_result")
    if not isinstance(wake_result, dict):
        raise ValueError("wake_result must be an object")
    if (
        wake_result.get("accepted") is not True
        or wake_result.get("agent_id") != delivery["recipient_agent_id"]
    ):
        return _protected("wake-send-receipt-invalid")
    issue = delivery.get("issue")
    if not isinstance(issue, str) or not issue.strip():
        raise ValueError("delivery.issue is invalid")
    dispatch_id = _require_text(
        "delivery.dispatch_id", delivery.get("dispatch_id"), IDENTIFIER_RE
    )
    next_sequence = snapshot.get("next_sequence")
    if (
        not isinstance(next_sequence, int)
        or isinstance(next_sequence, bool)
        or next_sequence < 0
    ):
        raise ValueError("next_sequence is invalid")
    delivery_id = _delivery_id(delivery)
    event = {
        "schema_version": 1,
        "signal_id": f"delivery-wake-{delivery_id.removeprefix('delivery-')}",
        "campaign_id": snapshot["campaign_id"],
        "dispatch_id": dispatch_id,
        "sequence": next_sequence,
        "event_type": "DELIVERY_WAKE",
        "issue": issue,
        "sender_agent_id": delivery["sender_agent_id"],
        "recipient_agent_id": delivery["recipient_agent_id"],
        "in_reply_to": delivery["signal_id"],
        "evidence": {
            "delivery_id": delivery_id,
            "source_message_id": delivery["message_id"],
            "source_signal_id": delivery["signal_id"],
            "source_sender_agent_id": delivery["sender_agent_id"],
            "source_recipient_agent_id": delivery["recipient_agent_id"],
            "authority_scope": delivery["authority_scope"],
            "outcome": "sent",
        },
        "next_action": "wait for delivery ack",
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "eligible",
        "automatic_execution": True,
        "event": event,
        "blockers": [],
    }


def _read_snapshot(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("delivery-plan")
    plan.add_argument("--snapshot", type=Path, required=True)
    ack = subparsers.add_parser("ack-plan")
    ack.add_argument("--snapshot", type=Path, required=True)
    wake_receipt = subparsers.add_parser("wake-receipt-plan")
    wake_receipt.add_argument("--snapshot", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        snapshot = _read_snapshot(arguments.snapshot)
        planners = {
            "delivery-plan": delivery_plan,
            "ack-plan": ack_event_plan,
            "wake-receipt-plan": wake_receipt_event_plan,
        }
        result = planners[arguments.command](snapshot)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
