#!/usr/bin/env python3
"""gwo mailbox: event delivery layer for the GWO V7 kernel (Phase 1).

Implements the 8-event mailbox model with CLI-enforced role entitlement,
per-sender monotonic sequence, ack-on-read delivery, Signal-ID idempotency,
and rejection of conflicting retries. Every send/ack transition is contained
in one explicit SQLite transaction so concurrent writers serialize without
duplicate effects.

The eight accepted event types: ``status``, ``ask``, ``reply``,
``worker_done``, ``review_result``, ``escalation``, ``decision_gate``,
``heartbeat``. Role entitlement is enforced at write time from the
spawn-injected ``GWO_AGENT_ID`` and the registered agent role.

See docs/design/gwo-v7-architecture.md and ADRs 0007-0009.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from typing import Any


EVENT_TYPES = frozenset({
    "status",
    "ask",
    "reply",
    "worker_done",
    "review_result",
    "escalation",
    "decision_gate",
    "heartbeat",
})

# Role entitlement per event type. A sender role not listed for an event type
# is rejected at write time. The coordinator role is the repository coordinator;
# worker is an implementation-role dispatched agent; reviewer covers the
# review-role agents (spec/quality); monitor is an observability-only role.
ROLE_ENTITLEMENT: dict[str, frozenset[str]] = {
    "status": frozenset({"coordinator", "worker", "reviewer", "monitor"}),
    "ask": frozenset({"coordinator", "worker", "reviewer", "monitor"}),
    "reply": frozenset({"coordinator", "worker", "reviewer", "monitor"}),
    "worker_done": frozenset({"worker"}),
    "review_result": frozenset({"reviewer"}),
    "escalation": frozenset({"coordinator", "worker", "reviewer", "monitor"}),
    "decision_gate": frozenset({"coordinator"}),
    "heartbeat": frozenset({"worker"}),
}

# Events that require an in_reply_to field pointing at a prior signal_id.
REQUIRES_IN_REPLY_TO = frozenset({"reply"})

SIGNAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
ROLE_VALUES = frozenset({"coordinator", "worker", "reviewer", "monitor"})


class MailboxError(RuntimeError):
    """Base class for mailbox delivery errors."""


class EntitlementError(MailboxError):
    """Raised when a caller is not entitled to write an event type."""


class SignalIdError(MailboxError):
    """Raised when a Signal-ID retry conflicts with prior content."""


class DeliveryError(MailboxError):
    """Raised when a delivery or ACK transition is invalid."""


def _now() -> float:
    return time.time()


def _new_msg_id() -> str:
    return f"m-{uuid.uuid4().hex[:24]}"


def _validate_signal_id(signal_id: str) -> str:
    if not isinstance(signal_id, str) or not SIGNAL_ID_RE.fullmatch(signal_id):
        raise MailboxError("signal_id is invalid")
    return signal_id


def _validate_agent_id(agent_id: str, field: str = "agent_id") -> str:
    if not isinstance(agent_id, str) or not AGENT_ID_RE.fullmatch(agent_id):
        raise MailboxError(f"{field} is invalid")
    return agent_id


def _validate_payload(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise MailboxError("payload must be a JSON object")
    return payload


def _content_fingerprint(
    from_agent: str,
    to_agent: str,
    event_type: str,
    payload: dict[str, Any],
    in_reply_to: str | None,
) -> str:
    """Stable canonical fingerprint of a message's content for conflict checks."""
    canonical = json.dumps(
        {
            "from_agent": from_agent,
            "to_agent": to_agent,
            "type": event_type,
            "payload": payload,
            "in_reply_to": in_reply_to,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    import hashlib
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def register_agent(
    store: Any,
    *,
    agent_id: str,
    adapter: str,
    runtime_ref: str | None,
    role: str,
    group_label: str | None = None,
    session_id: str | None = None,
    pid: int | None = None,
) -> dict[str, Any]:
    """Register or refresh an agent row in the store.

    The caller must be the coordinator. The agent_id is the spawn-injected
    identity; the role records what event types that agent is entitled to
    write.
    """
    _validate_agent_id(agent_id, "agent_id")
    if role not in ROLE_VALUES:
        raise MailboxError(f"invalid role {role}")
    caller = store._caller()
    db = store.db
    db.execute("BEGIN IMMEDIATE")
    try:
        store._require_coordinator_claim(caller)
        existing = db.execute(
            "SELECT agent_id FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        now = _now()
        if existing is None:
            db.execute(
                "INSERT INTO agents (agent_id, adapter, runtime_ref, session_id, "
                "pid, role, group_label, created_at, archived_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (agent_id, adapter, runtime_ref, session_id, pid,
                 role, group_label, now),
            )
        else:
            db.execute(
                "UPDATE agents SET adapter = ?, runtime_ref = ?, session_id = ?, "
                "pid = ?, role = ?, group_label = ?, archived_at = NULL "
                "WHERE agent_id = ?",
                (adapter, runtime_ref, session_id, pid, role, group_label, agent_id),
            )
        db.execute("COMMIT")
    except BaseException:
        try:
            db.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise
    return _agent_row(db, agent_id)


def _agent_row(db: sqlite3.Connection, agent_id: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
    ).fetchone()
    if row is None:
        raise MailboxError(f"unknown agent {agent_id}")
    return {
        "agent_id": row["agent_id"],
        "adapter": row["adapter"],
        "runtime_ref": row["runtime_ref"],
        "session_id": row["session_id"],
        "pid": row["pid"],
        "role": row["role"],
        "group_label": row["group_label"],
        "created_at": row["created_at"],
        "archived_at": row["archived_at"],
    }


def _resolve_role(store: Any, agent_id: str) -> str:
    """Resolve the caller's role from the agents table.

    Falls back to inferring from dispatch context: if the caller is the
    dispatched agent of an active dispatch, the role is ``worker``. If the
    caller holds the coordinator claim, the role is ``coordinator``. This
    lets a freshly-spawned worker send events before an explicit
    register_agent call, while still enforcing entitlement.
    """
    db = store.db
    row = db.execute(
        "SELECT role, archived_at FROM agents WHERE agent_id = ?", (agent_id,)
    ).fetchone()
    if row is not None:
        if row["archived_at"] is not None:
            raise EntitlementError(f"agent {agent_id} is archived")
        return str(row["role"])
    # Fallback: coordinator claim holder is the coordinator.
    coord = db.execute(
        "SELECT agent_id FROM coordinator WHERE repo = ? AND released_at IS NULL",
        (store.repo,),
    ).fetchone()
    if coord is not None and str(coord["agent_id"]) == agent_id:
        return "coordinator"
    # Fallback: an agent with an active dispatch is a worker.
    dispatch = db.execute(
        "SELECT agent_id FROM dispatches WHERE agent_id = ? AND status = 'active'",
        (agent_id,),
    ).fetchone()
    if dispatch is not None:
        return "worker"
    raise EntitlementError(
        f"agent {agent_id} has no registered role and no dispatch context"
    )


def send(
    store: Any,
    *,
    to_agent: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    signal_id: str,
    in_reply_to: str | None = None,
) -> dict[str, Any]:
    """Post one mailbox event with identity, entitlement, and idempotency checks.

    Identity comes from the spawn-injected ``GWO_AGENT_ID``. The sender role
    is resolved from the agents table (or dispatch context fallback) and
    must be entitled to write ``event_type``. Per-sender sequence is monotonic.
    A retry with the same ``signal_id`` and identical content deduplicates
    cleanly; a retry with conflicting content is rejected.
    """
    _validate_signal_id(signal_id)
    _validate_agent_id(to_agent, "to_agent")
    if event_type not in EVENT_TYPES:
        raise MailboxError(f"unknown event type {event_type}")
    body = _validate_payload(payload)
    if in_reply_to is not None:
        _validate_signal_id(in_reply_to)
    if event_type in REQUIRES_IN_REPLY_TO and in_reply_to is None:
        raise MailboxError(f"{event_type} requires in_reply_to")

    caller = store._caller()
    _validate_agent_id(caller, "from_agent")
    db = store.db
    db.execute("BEGIN IMMEDIATE")
    try:
        # Reply binding: verify the referenced ask exists, the reply author
        # is that ask's intended recipient, and the reply recipient is the
        # ask author. This prevents adversarial replies from unrelated agents.
        # This check runs before role resolution because the reply authority
        # comes from being the ask's intended recipient, not just the role.
        if event_type == "reply" and in_reply_to is not None:
            ask_row = db.execute(
                "SELECT from_agent, to_agent, type FROM messages "
                "WHERE signal_id = ? AND type = 'ask'",
                (in_reply_to,),
            ).fetchone()
            if ask_row is None:
                raise MailboxError(
                    f"reply references unknown or non-ask signal_id {in_reply_to}"
                )
            ask_author = str(ask_row["from_agent"])
            ask_recipient = str(ask_row["to_agent"])
            if caller != ask_recipient:
                raise EntitlementError(
                    f"reply author {caller} is not the intended recipient "
                    f"{ask_recipient} of ask {in_reply_to}"
                )
            if to_agent != ask_author:
                raise MailboxError(
                    f"reply recipient {to_agent} is not the ask author "
                    f"{ask_author} of ask {in_reply_to}"
                )
        role = _resolve_role(store, caller)
        entitled = ROLE_ENTITLEMENT.get(event_type, frozenset())
        if role not in entitled:
            raise EntitlementError(
                f"role {role} is not entitled to send {event_type}"
            )
        # Per-sender monotonic sequence under the write lock.
        seq_row = db.execute(
            "SELECT MAX(seq) AS max_seq FROM messages WHERE from_agent = ?",
            (caller,),
        ).fetchone()
        next_seq = 1 if seq_row["max_seq"] is None else int(seq_row["max_seq"]) + 1

        # Signal-ID idempotency: check for an existing row with this signal_id.
        existing = db.execute(
            "SELECT msg_id, from_agent, to_agent, type, payload_json, "
            "in_reply_to IS NOT NULL AS has_reply, in_reply_to "
            "FROM messages WHERE signal_id = ?",
            (signal_id,),
        ).fetchone()
        if existing is not None:
            # Exact retry: same content -> deduplicate by returning the row.
            prior_payload = json.loads(existing["payload_json"])
            prior_fingerprint = _content_fingerprint(
                existing["from_agent"],
                existing["to_agent"],
                existing["type"],
                prior_payload,
                existing["in_reply_to"] if existing["has_reply"] else None,
            )
            new_fingerprint = _content_fingerprint(
                caller, to_agent, event_type, body, in_reply_to,
            )
            if prior_fingerprint != new_fingerprint:
                raise SignalIdError(
                    f"signal_id {signal_id} conflicts with prior content"
                )
            db.execute("ROLLBACK")
            return _message_row(db, existing["msg_id"])

        msg_id = _new_msg_id()
        payload_json = json.dumps(body, sort_keys=True)
        db.execute(
            "INSERT INTO messages (msg_id, signal_id, seq, from_agent, to_agent, "
            "type, payload_json, in_reply_to, created_at, acked_at, acked_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            (
                msg_id, signal_id, next_seq, caller, to_agent, event_type,
                payload_json, in_reply_to, _now(),
            ),
        )
        db.execute("COMMIT")
        return _message_row(db, msg_id)
    except BaseException:
        try:
            db.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise


def _message_row(db: sqlite3.Connection, msg_id: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT msg_id, signal_id, seq, from_agent, to_agent, type, "
        "payload_json, in_reply_to, created_at, acked_at, acked_by "
        "FROM messages WHERE msg_id = ?",
        (msg_id,),
    ).fetchone()
    if row is None:
        raise MailboxError(f"unknown message {msg_id}")
    return _message_from_row(row)


def _message_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "msg_id": row["msg_id"],
        "signal_id": row["signal_id"],
        "seq": row["seq"],
        "from_agent": row["from_agent"],
        "to_agent": row["to_agent"],
        "type": row["type"],
        "payload": json.loads(row["payload_json"]),
        "in_reply_to": row["in_reply_to"],
        "created_at": row["created_at"],
        "acked_at": row["acked_at"],
        "acked_by": row["acked_by"],
    }


def inbox(
    store: Any,
    *,
    agent_id: str,
    ack_on_read: bool = False,
    dispatch_id: str | None = None,
    wait: float | None = None,
) -> list[dict[str, Any]]:
    """Read (and optionally acknowledge) messages addressed to ``agent_id``.

    With ``ack_on_read=True``, each unacked message is acknowledged in the same
    transaction so a concurrent reader cannot double-ack. With
    ``dispatch_id``, messages are filtered to the caller's dispatch scope
    (messages from the dispatched agent or to the dispatched agent).

    With ``wait`` set, when no messages are immediately available the call
    polls up to ``wait`` seconds for at least one message to arrive, then
    returns whatever is available (which may be empty if the wait elapses).

    The caller-supplied ``agent_id`` must equal the live ``GWO_AGENT_ID`` so
    an injected identity cannot read or ACK another recipient's messages.
    The only exception is a dispatch-scoped read where the caller is the
    coordinator reading the dispatch's agent traffic, or the dispatched agent
    reading its own traffic.
    """
    _validate_agent_id(agent_id, "agent_id")
    caller = store._caller()
    db = store.db
    poll_interval = 0.05
    deadline = _now() + wait if wait is not None else None
    while True:
        db.execute("BEGIN IMMEDIATE")
        try:
            dispatched_agent: str | None = None
            if dispatch_id is not None:
                dispatch = db.execute(
                    "SELECT agent_id FROM dispatches WHERE dispatch_id = ?",
                    (dispatch_id,),
                ).fetchone()
                if dispatch is None:
                    raise DeliveryError(f"unknown dispatch {dispatch_id}")
                dispatched_agent = str(dispatch["agent_id"])
                caller_is_dispatched = caller == dispatched_agent
                caller_is_coord = _is_coordinator(store, caller)
                if not (caller_is_dispatched or caller_is_coord):
                    raise DeliveryError(
                        "only the dispatched agent or coordinator may read a "
                        "dispatch-scoped inbox"
                    )
                # Bind agent_id to the live caller: the dispatched agent reads
                # its own mailbox (agent_id == caller == dispatched_agent); the
                # coordinator reads the dispatched agent's mailbox
                # (agent_id == dispatched_agent). This prevents an injected
                # identity from reading another recipient's dispatch traffic.
                if caller_is_dispatched and agent_id != caller:
                    raise DeliveryError(
                        "dispatch-scoped inbox agent_id must equal the caller "
                        "(dispatched agent)"
                    )
                if caller_is_coord and agent_id != dispatched_agent:
                    raise DeliveryError(
                        "coordinator dispatch-scoped inbox agent_id must equal "
                        "the dispatched agent"
                    )
                # Return only deliveries the caller may read: messages addressed
                # TO the mailbox owner (agent_id). This excludes the owner's
                # outbound traffic, which the owner already authored.
                rows = db.execute(
                    "SELECT msg_id, signal_id, seq, from_agent, to_agent, type, "
                    "payload_json, in_reply_to, created_at, acked_at, acked_by "
                    "FROM messages WHERE to_agent = ? AND acked_at IS NULL "
                    "ORDER BY seq",
                    (agent_id,),
                ).fetchall()
            else:
                if agent_id != caller:
                    raise DeliveryError(
                        "agent_id must equal the caller's GWO_AGENT_ID to read "
                        "a non-scoped inbox"
                    )
                rows = db.execute(
                    "SELECT msg_id, signal_id, seq, from_agent, to_agent, type, "
                    "payload_json, in_reply_to, created_at, acked_at, acked_by "
                    "FROM messages WHERE to_agent = ? AND acked_at IS NULL "
                    "ORDER BY seq",
                    (agent_id,),
                ).fetchall()
            messages = [_message_from_row(r) for r in rows]
            if messages:
                if ack_on_read:
                    now = _now()
                    for msg in messages:
                        if msg["acked_at"] is None:
                            # Only the intended recipient (to_agent == caller)
                            # may ACK. A coordinator reading dispatch traffic
                            # must not ACK messages addressed to the worker.
                            if msg["to_agent"] == caller:
                                db.execute(
                                    "UPDATE messages SET acked_at = ?, acked_by = ? "
                                    "WHERE msg_id = ? AND acked_at IS NULL",
                                    (now, caller, msg["msg_id"]),
                                )
                    messages = [_message_row(db, m["msg_id"]) for m in messages]
                db.execute("COMMIT")
                return messages
            # No messages found. Release the write lock and decide whether to wait.
            db.execute("COMMIT")
            if deadline is None or _now() >= deadline:
                return []
            time.sleep(poll_interval)
        except BaseException:
            try:
                db.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise


def _is_coordinator(store: Any, agent_id: str) -> bool:
    """Return True if agent_id holds the active coordinator claim for this repo."""
    row = store.db.execute(
        "SELECT agent_id FROM coordinator WHERE repo = ? AND released_at IS NULL",
        (store.repo,),
    ).fetchone()
    return row is not None and str(row["agent_id"]) == agent_id


def ask(
    store: Any,
    *,
    to_agent: str,
    payload: dict[str, Any] | None = None,
    signal_id: str,
    timeout: float = 30.0,
    poll_interval: float = 0.05,
) -> dict[str, Any]:
    """Send an ask event and block until the correlated reply arrives.

    Sends an ``ask`` event to ``to_agent``, then polls the caller's inbox for a
    ``reply`` event whose ``in_reply_to`` matches ``signal_id``. Blocks up to
    ``timeout`` seconds. Raises ``MailboxError`` on timeout. The reply event is
    returned and not auto-acked (the caller decides when to ack).
    """
    ask_msg = send(
        store,
        to_agent=to_agent,
        event_type="ask",
        payload=payload,
        signal_id=signal_id,
    )
    caller = ask_msg["from_agent"]
    deadline = _now() + timeout
    while _now() < deadline:
        rows = store.db.execute(
            "SELECT msg_id, signal_id, seq, from_agent, to_agent, type, "
            "payload_json, in_reply_to, created_at, acked_at, acked_by "
            "FROM messages WHERE to_agent = ? AND type = 'reply' "
            "AND in_reply_to = ? ORDER BY seq",
            (caller, signal_id),
        ).fetchall()
        if rows:
            return _message_from_row(rows[0])
        time.sleep(poll_interval)
    raise MailboxError(
        f"ask {signal_id} timed out after {timeout}s waiting for reply"
    )


def agent_status(store: Any, agent_id: str) -> dict[str, Any]:
    """Return the runtime status of one agent (delegates to gwo_status)."""
    import gwo_status
    return gwo_status.agent_status(store, agent_id)


def config_check(store: Any, *, gwo_home: str | None = None) -> dict[str, Any]:
    """Validate the GWO configuration (delegates to gwo_status)."""
    import gwo_status
    return gwo_status.config_check(store, gwo_home=gwo_home)


def doctor_rebuild(
    store: Any,
    *,
    github_snapshot: dict[str, Any],
    adapter_listing: list[dict[str, Any]],
    git_worktrees: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rebuild the store from GitHub + adapter readback (delegates to gwo_status)."""
    import gwo_status
    return gwo_status.doctor_rebuild(
        store,
        github_snapshot=github_snapshot,
        adapter_listing=adapter_listing,
        git_worktrees=git_worktrees,
    )