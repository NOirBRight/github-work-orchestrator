from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "github-work-orchestrator" / "scripts"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_store():
    return load_module("gwo_store", SCRIPT_DIR / "gwo_store.py")


def load_mailbox():
    return load_module("gwo_mailbox", SCRIPT_DIR / "gwo_mailbox.py")


def load_status():
    return load_module("gwo_status", SCRIPT_DIR / "gwo_status.py")


class MailboxFixture:
    """Open a store with coordinator claimed and a ready task+dispatch pair."""

    def __init__(self, test_case: unittest.TestCase, *, repo: str = "owner/repo"):
        self.test_case = test_case
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._saved_env = {
            "GWO_HOME": os.environ.get("GWO_HOME"),
            "GWO_AGENT_ID": os.environ.get("GWO_AGENT_ID"),
        }
        os.environ["GWO_HOME"] = str(self.home)
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.store_mod = load_store()
        self.mailbox_mod = load_mailbox()
        self.status_mod = load_status()
        self.repo = repo
        self.store = self.store_mod.Store.connect(self.home, repo)
        self.store.claim_coordinator()
        self.task = self.store.create_task(
            issue=42, group_label="g-42", risk="standard"
        )
        self.store.update_task(task_id=self.task["task_id"], status="ready")
        self.dispatch = self.store.create_dispatch(
            task_id=self.task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )

    def cleanup(self) -> None:
        self.store.close()
        self.tmp.cleanup()
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def as_agent(self, agent_id: str) -> "AgentContext":
        return AgentContext(self, agent_id)


class AgentContext:
    """Context manager that sets GWO_AGENT_ID and opens a fresh store."""

    def __init__(self, fixture: MailboxFixture, agent_id: str) -> None:
        self.fixture = fixture
        self.agent_id = agent_id
        self._saved = os.environ.get("GWO_AGENT_ID")

    def __enter__(self) -> "AgentContext":
        os.environ["GWO_AGENT_ID"] = self.agent_id
        return self

    def __exit__(self, *exc) -> None:
        if self._saved is None:
            os.environ.pop("GWO_AGENT_ID", None)
        else:
            os.environ["GWO_AGENT_ID"] = self._saved


class EventTypeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.mailbox_mod = self.fixture.mailbox_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_exactly_eight_event_types_accepted(self) -> None:
        expected = {
            "status",
            "ask",
            "reply",
            "worker_done",
            "review_result",
            "escalation",
            "decision_gate",
            "heartbeat",
        }
        self.assertEqual(expected, set(self.mailbox_mod.EVENT_TYPES))

    def test_rejects_unknown_event_type(self) -> None:
        with self.assertRaises(self.mailbox_mod.MailboxError):
            self.store.send(
                to_agent="coordinator-001",
                event_type="PROGRESS",
                payload={},
                signal_id="sig-abc1234567890",
            )


class RoleEntitlementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.mailbox_mod = self.fixture.mailbox_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_worker_can_send_status_to_coordinator(self) -> None:
        with self.fixture.as_agent("worker-001"):
            msg = self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={"phase": "running"},
                signal_id="sig-status-aaaaaaaaaa",
            )
        self.assertEqual("status", msg["type"])
        self.assertEqual("worker-001", msg["from_agent"])
        self.assertEqual("coordinator-001", msg["to_agent"])

    def test_worker_can_send_worker_done_to_coordinator(self) -> None:
        with self.fixture.as_agent("worker-001"):
            msg = self.store.send(
                to_agent="coordinator-001",
                event_type="worker_done",
                payload={"dispatch_id": self.fixture.dispatch["dispatch_id"]},
                signal_id="sig-done-aaaaaaaaaa",
            )
        self.assertEqual("worker_done", msg["type"])

    def test_worker_cannot_send_review_result(self) -> None:
        with self.fixture.as_agent("worker-001"):
            with self.assertRaises(self.mailbox_mod.MailboxError) as ctx:
                self.store.send(
                    to_agent="coordinator-001",
                    event_type="review_result",
                    payload={},
                    signal_id="sig-rev-aaaaaaaaaa",
                )
        self.assertIn("not entitled", str(ctx.exception).lower())

    def test_coordinator_cannot_send_worker_done(self) -> None:
        with self.assertRaises(self.mailbox_mod.MailboxError) as ctx:
            self.store.send(
                to_agent="worker-001",
                event_type="worker_done",
                payload={},
                signal_id="sig-done-bbbbbbbbbb",
            )
        self.assertIn("not entitled", str(ctx.exception).lower())

    def test_coordinator_can_send_reply_to_worker(self) -> None:
        with self.fixture.as_agent("worker-001"):
            self.store.send(
                to_agent="coordinator-001",
                event_type="ask",
                payload={"question": "scope?"},
                signal_id="sig-ask-aaaaaaaaaa",
            )
        msg = self.store.send(
            to_agent="worker-001",
            event_type="reply",
            payload={"answer": "yes"},
            in_reply_to="sig-ask-aaaaaaaaaa",
            signal_id="sig-reply-bbbbbbbbbb",
        )
        self.assertEqual("reply", msg["type"])

    def test_reviewer_can_send_review_result(self) -> None:
        self.store.register_agent(
            agent_id="reviewer-001",
            adapter="paseo",
            runtime_ref="rev-ref-001",
            role="reviewer",
            group_label="g-42",
        )
        with self.fixture.as_agent("reviewer-001"):
            msg = self.store.send(
                to_agent="coordinator-001",
                event_type="review_result",
                payload={"axis": "spec", "verdict": "pass"},
                signal_id="sig-review-aaaaaaaaa",
            )
        self.assertEqual("review_result", msg["type"])

    def test_heartbeat_only_for_implementation_role(self) -> None:
        with self.fixture.as_agent("worker-001"):
            msg = self.store.send(
                to_agent="coordinator-001",
                event_type="heartbeat",
                payload={},
                signal_id="sig-hb-aaaaaaaaaaa",
            )
        self.assertEqual("heartbeat", msg["type"])

    def test_decision_gate_only_for_coordinator(self) -> None:
        with self.fixture.as_agent("worker-001"):
            with self.assertRaises(self.mailbox_mod.MailboxError):
                self.store.send(
                    to_agent="coordinator-001",
                    event_type="decision_gate",
                    payload={},
                    signal_id="sig-gate-aaaaaaaaa",
                )
        msg = self.store.send(
            to_agent="worker-001",
            event_type="decision_gate",
            payload={"gate_url": "https://example/g/1"},
            signal_id="sig-gate-bbbbbbbbbb",
        )
        self.assertEqual("decision_gate", msg["type"])

    def test_impersonation_rejected_at_write_time(self) -> None:
        with self.fixture.as_agent("worker-002"):
            with self.assertRaises(self.mailbox_mod.MailboxError) as ctx:
                self.store.send(
                    to_agent="coordinator-001",
                    event_type="worker_done",
                    payload={
                        "dispatch_id": self.fixture.dispatch["dispatch_id"]
                    },
                    signal_id="sig-imp-aaaaaaaaaa",
                )
        msg = str(ctx.exception).lower()
        self.assertTrue("not entitled" in msg or "no registered role" in msg, msg)


class SequenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.mailbox_mod = self.fixture.mailbox_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_per_sender_sequence_monotonic(self) -> None:
        with self.fixture.as_agent("worker-001"):
            m1 = self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={},
                signal_id="sig-seq-aaaaaaaaaa",
            )
            m2 = self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={},
                signal_id="sig-seq-bbbbbbbbbb",
            )
        self.assertEqual(1, m1["seq"])
        self.assertEqual(2, m2["seq"])

    def test_sequence_per_sender_not_global(self) -> None:
        with self.fixture.as_agent("worker-001"):
            m1 = self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={},
                signal_id="sig-seq-aaaaaaaaaa",
            )
        msg = self.store.send(
            to_agent="worker-001",
            event_type="status",
            payload={},
            signal_id="sig-seq-bbbbbbbbbb",
        )
        self.assertEqual(1, m1["seq"])
        self.assertEqual(1, msg["seq"], "coordinator's own sequence starts at 1")


class AckOnReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.mailbox_mod = self.fixture.mailbox_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_inbox_returns_unacked_messages(self) -> None:
        with self.fixture.as_agent("worker-001"):
            self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={},
                signal_id="sig-inbox-aaaaaaaa",
            )
        msgs = self.store.inbox(agent_id="coordinator-001")
        self.assertEqual(1, len(msgs))
        self.assertIsNone(msgs[0]["acked_at"])

    def test_ack_on_read_records_ack(self) -> None:
        with self.fixture.as_agent("worker-001"):
            self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={},
                signal_id="sig-ack-aaaaaaaaaa",
            )
        msgs = self.store.inbox(agent_id="coordinator-001", ack_on_read=True)
        self.assertIsNotNone(msgs[0]["acked_at"])
        self.assertEqual("coordinator-001", msgs[0]["acked_by"])
        msgs_after = self.store.inbox(agent_id="coordinator-001")
        self.assertEqual(0, len(msgs_after))

    def test_inbox_filters_by_dispatch_scope(self) -> None:
        with self.fixture.as_agent("worker-001"):
            self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={},
                signal_id="sig-scope-aaaaaaaa",
            )
        with self.fixture.as_agent("worker-001"):
            scoped = self.store.inbox(
                agent_id="worker-001",
                dispatch_id=self.fixture.dispatch["dispatch_id"],
            )
        self.assertEqual(0, len(scoped), "worker inbox sees messages to worker")


class SignalIdIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.mailbox_mod = self.fixture.mailbox_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_exact_retry_deduplicates(self) -> None:
        with self.fixture.as_agent("worker-001"):
            m1 = self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={"phase": "running"},
                signal_id="sig-dedup-aaaaaaaa",
            )
            m2 = self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={"phase": "running"},
                signal_id="sig-dedup-aaaaaaaa",
            )
        self.assertEqual(m1["msg_id"], m2["msg_id"])
        msgs = self.store.inbox(agent_id="coordinator-001")
        self.assertEqual(1, len(msgs))

    def test_conflicting_retry_rejected(self) -> None:
        with self.fixture.as_agent("worker-001"):
            self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={"phase": "running"},
                signal_id="sig-conf-aaaaaaaaaa",
            )
            with self.assertRaises(self.mailbox_mod.MailboxError) as ctx:
                self.store.send(
                    to_agent="coordinator-001",
                    event_type="status",
                    payload={"phase": "done"},
                    signal_id="sig-conf-aaaaaaaaaa",
                )
        self.assertIn("conflict", str(ctx.exception).lower())

    def test_conflicting_retry_leaves_original_intact(self) -> None:
        with self.fixture.as_agent("worker-001"):
            self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={"phase": "running"},
                signal_id="sig-conf2-aaaaaaaa",
            )
            with self.assertRaises(self.mailbox_mod.MailboxError):
                self.store.send(
                    to_agent="coordinator-001",
                    event_type="ask",
                    payload={"q": "different"},
                    signal_id="sig-conf2-aaaaaaaa",
                )
        msgs = self.store.inbox(agent_id="coordinator-001")
        self.assertEqual(1, len(msgs))
        self.assertEqual("status", msgs[0]["type"])
        self.assertEqual({"phase": "running"}, msgs[0]["payload"])


class TwoAgentConversationTests(unittest.TestCase):
    """The acceptance scenario: two distinct GWO_AGENT_ID values demonstrate
    send -> ack-on-read -> done with recorded sender, recipient, ACK, and
    terminal evidence.
    """

    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.mailbox_mod = self.fixture.mailbox_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_send_ack_done_full_lifecycle(self) -> None:
        with self.fixture.as_agent("worker-001"):
            sent = self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={"phase": "running"},
                signal_id="sig-life-aaaaaaaaaa",
            )
        self.assertEqual("worker-001", sent["from_agent"])
        self.assertEqual("coordinator-001", sent["to_agent"])
        self.assertIsNone(sent["acked_at"])

        msgs = self.store.inbox(agent_id="coordinator-001", ack_on_read=True)
        self.assertEqual(1, len(msgs))
        self.assertEqual(sent["msg_id"], msgs[0]["msg_id"])
        self.assertIsNotNone(msgs[0]["acked_at"])
        self.assertEqual("coordinator-001", msgs[0]["acked_by"])

        with self.fixture.as_agent("worker-001"):
            result = self.store.mark_done(
                task_id=self.fixture.task["task_id"],
                dispatch_id=self.fixture.dispatch["dispatch_id"],
                status="done",
            )
        self.assertEqual("done", result["status"])
        tasks = self.store.list_tasks()
        self.assertEqual("done", tasks[0]["status"])


class ConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.mailbox_mod = self.fixture.mailbox_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_concurrent_send_serializes_without_duplicate_seq(self) -> None:
        with self.fixture.as_agent("worker-001"):
            other = store_mod_connect_other(self.fixture, "worker-001")
            try:
                m1 = self.store.send(
                    to_agent="coordinator-001",
                    event_type="status",
                    payload={},
                    signal_id="sig-race-aaaaaaaaaa",
                )
                m2 = other.send(
                    to_agent="coordinator-001",
                    event_type="status",
                    payload={},
                    signal_id="sig-race-bbbbbbbbbb",
                )
            finally:
                other.close()
        self.assertNotEqual(m1["msg_id"], m2["msg_id"])
        seqs = sorted([m1["seq"], m2["seq"]])
        self.assertEqual([1, 2], seqs)

    def test_concurrent_ack_race_serializes(self) -> None:
        with self.fixture.as_agent("worker-001"):
            self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={},
                signal_id="sig-ackrace-aaaaaaaaa",
            )
        other = store_mod_connect_other(self.fixture, "coordinator-001")
        try:
            msgs_a = self.store.inbox(agent_id="coordinator-001", ack_on_read=True)
            msgs_b = other.inbox(agent_id="coordinator-001", ack_on_read=True)
        finally:
            other.close()
        self.assertEqual(1, len(msgs_a))
        self.assertEqual(0, len(msgs_b), "second ACK must find the message already acked")


def store_mod_connect_other(fixture: MailboxFixture, agent_id: str):
    """Open a second Store connection under a given GWO_AGENT_ID."""
    saved = os.environ.get("GWO_AGENT_ID")
    os.environ["GWO_AGENT_ID"] = agent_id
    try:
        other = fixture.store_mod.Store.connect(fixture.home, fixture.repo)
    finally:
        if saved is None:
            os.environ.pop("GWO_AGENT_ID", None)
        else:
            os.environ["GWO_AGENT_ID"] = saved
    return other


class AskReplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.mailbox_mod = self.fixture.mailbox_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_ask_requires_in_reply_to_for_reply(self) -> None:
        with self.fixture.as_agent("worker-001"):
            ask = self.store.send(
                to_agent="coordinator-001",
                event_type="ask",
                payload={"question": "scope?"},
                signal_id="sig-ask2-aaaaaaaaaa",
            )
        reply = self.store.send(
            to_agent="worker-001",
            event_type="reply",
            payload={"answer": "yes"},
            in_reply_to="sig-ask2-aaaaaaaaaa",
            signal_id="sig-reply2-bbbbbbb",
        )
        self.assertEqual("sig-ask2-aaaaaaaaaa", reply["in_reply_to"])

    def test_reply_without_in_reply_to_rejected(self) -> None:
        with self.assertRaises(self.mailbox_mod.MailboxError):
            self.store.send(
                to_agent="worker-001",
                event_type="reply",
                payload={"answer": "yes"},
                signal_id="sig-reply3-bbbbbbb",
            )


class EscalationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.mailbox_mod = self.fixture.mailbox_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_worker_can_send_escalation(self) -> None:
        with self.fixture.as_agent("worker-001"):
            msg = self.store.send(
                to_agent="coordinator-001",
                event_type="escalation",
                payload={"reason": "blocked"},
                signal_id="sig-esc-aaaaaaaaaaa",
            )
        self.assertEqual("escalation", msg["type"])


class AgentStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.status_mod = self.fixture.status_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_agent_status_unknown_returns_exited(self) -> None:
        status = self.store.agent_status("never-spawned")
        self.assertIn(status["state"], ("exited", "unknown"))

    def test_agent_status_records_registered_agent(self) -> None:
        self.store.register_agent(
            agent_id="worker-001",
            adapter="paseo",
            runtime_ref="agent-ref-001",
            role="worker",
            group_label="g-42",
        )
        status = self.store.agent_status("worker-001")
        self.assertEqual("worker-001", status["agent_id"])
        self.assertIn(status["state"], ("running", "idle", "stalled", "exited"))


class ConfigCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.status_mod = self.fixture.status_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_config_check_returns_structure(self) -> None:
        result = self.store.config_check()
        self.assertIn("valid", result)
        self.assertIn("errors", result)


class DoctorRebuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.status_mod = self.fixture.status_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_rebuild_surfaces_ambiguity_without_destructive_inference(self) -> None:
        result = self.store.doctor_rebuild(
            github_snapshot={"issues": [], "agents": [], "worktrees": []},
            adapter_listing=[],
            git_worktrees=[],
        )
        self.assertIn("rebuilt", result)
        self.assertIn("ambiguities", result)
        self.assertIsInstance(result["ambiguities"], list)


if __name__ == "__main__":
    unittest.main()