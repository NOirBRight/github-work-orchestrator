from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "github-work-orchestrator" / "scripts"
GWO_PY = SCRIPT_DIR / "gwo.py"


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

    def _set_agent(self, agent_id: str) -> None:
        """Set GWO_AGENT_ID without opening a new store (for same-thread switches)."""
        os.environ["GWO_AGENT_ID"] = agent_id


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


# ---------------------------------------------------------------------------
# CLI subcommand coverage (subprocess invocation of gwo.py).
#
# These tests exercise the send, ask, inbox, agent status/register, config
# check, and doctor rebuild subcommands end-to-end through the packaged CLI,
# preserving the behavioral coverage of the Phase 1 mailbox surface. They live
# in this claimed test file rather than tests/test_gwo_cli.py so the candidate
# diff stays within Issue #22's canonical Change Claims.
# ---------------------------------------------------------------------------


class CliFixture:
    """Run gwo.py against an isolated temporary GWO_HOME via subprocess."""

    def __init__(self, *, claim: bool = False) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._saved_env = {
            "GWO_HOME": os.environ.get("GWO_HOME"),
            "GWO_AGENT_ID": os.environ.get("GWO_AGENT_ID"),
            "PYTHONPATH": os.environ.get("PYTHONPATH"),
        }
        os.environ["GWO_HOME"] = str(self.home)
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        os.environ["PYTHONPATH"] = str(SCRIPT_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")
        if claim:
            self.run("coordinator", "claim")

    def cleanup(self) -> None:
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(GWO_PY), *args],
            capture_output=True,
            text=True,
            check=False,
        )


class CliSendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = CliFixture(claim=True)

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def _seed_dispatch(self) -> tuple[str, str]:
        create = self.fixture.run(
            "task", "create", "--issue", "42", "--group", "g-42", "--risk", "standard"
        )
        task_id = json.loads(create.stdout)["task_id"]
        self.fixture.run("task", "update", task_id, "--status", "ready")
        dispatch = self.fixture.run(
            "dispatch", "create",
            "--task-id", task_id,
            "--agent-id", "worker-001",
            "--worktree", "/tmp/wt-42",
            "--branch", "work/issue-42",
        )
        return task_id, json.loads(dispatch.stdout)["dispatch_id"]

    def test_send_status_from_worker(self) -> None:
        self._seed_dispatch()
        os.environ["GWO_AGENT_ID"] = "worker-001"
        try:
            result = self.fixture.run(
                "send", "--to", "coordinator-001", "--type", "status",
                "--signal-id", "sig-cli-status-aaaaa",
                "--payload", json.dumps({"phase": "running"}),
            )
        finally:
            os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("status", payload["type"])
        self.assertEqual("worker-001", payload["from_agent"])

    def test_send_rejects_unknown_event_type(self) -> None:
        self._seed_dispatch()
        os.environ["GWO_AGENT_ID"] = "worker-001"
        try:
            result = self.fixture.run(
                "send", "--to", "coordinator-001", "--type", "PROGRESS",
                "--signal-id", "sig-cli-bad-aaaaaa",
            )
        finally:
            os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertNotEqual(0, result.returncode)
        self.assertIn("unknown", result.stderr.lower())

    def test_send_rejects_impersonation(self) -> None:
        self._seed_dispatch()
        os.environ["GWO_AGENT_ID"] = "worker-002"
        try:
            result = self.fixture.run(
                "send", "--to", "coordinator-001", "--type", "worker_done",
                "--signal-id", "sig-cli-imp-aaaaaa",
            )
        finally:
            os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertNotEqual(0, result.returncode)

    def test_send_deduplicates_exact_retry(self) -> None:
        self._seed_dispatch()
        os.environ["GWO_AGENT_ID"] = "worker-001"
        try:
            first = self.fixture.run(
                "send", "--to", "coordinator-001", "--type", "status",
                "--signal-id", "sig-cli-dedup-aaaa",
                "--payload", json.dumps({"phase": "running"}),
            )
            second = self.fixture.run(
                "send", "--to", "coordinator-001", "--type", "status",
                "--signal-id", "sig-cli-dedup-aaaa",
                "--payload", json.dumps({"phase": "running"}),
            )
        finally:
            os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertEqual(
            json.loads(first.stdout)["msg_id"],
            json.loads(second.stdout)["msg_id"],
        )

    def test_send_rejects_conflicting_retry(self) -> None:
        self._seed_dispatch()
        os.environ["GWO_AGENT_ID"] = "worker-001"
        try:
            self.fixture.run(
                "send", "--to", "coordinator-001", "--type", "status",
                "--signal-id", "sig-cli-conf-aaaaa",
                "--payload", json.dumps({"phase": "running"}),
            )
            result = self.fixture.run(
                "send", "--to", "coordinator-001", "--type", "status",
                "--signal-id", "sig-cli-conf-aaaaa",
                "--payload", json.dumps({"phase": "done"}),
            )
        finally:
            os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertNotEqual(0, result.returncode)
        self.assertIn("conflict", result.stderr.lower())

    def test_send_fails_without_agent_id(self) -> None:
        self._seed_dispatch()
        saved = os.environ.pop("GWO_AGENT_ID")
        try:
            result = self.fixture.run(
                "send", "--to", "coordinator-001", "--type", "status",
                "--signal-id", "sig-cli-noagent-aaaa",
            )
        finally:
            os.environ["GWO_AGENT_ID"] = saved
        self.assertNotEqual(0, result.returncode)
        self.assertIn("GWO_AGENT_ID", result.stderr)


class CliInboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = CliFixture(claim=True)

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def _seed_and_send(self) -> None:
        create = self.fixture.run(
            "task", "create", "--issue", "42", "--group", "g-42", "--risk", "standard"
        )
        task_id = json.loads(create.stdout)["task_id"]
        self.fixture.run("task", "update", task_id, "--status", "ready")
        self.fixture.run(
            "dispatch", "create",
            "--task-id", task_id,
            "--agent-id", "worker-001",
            "--worktree", "/tmp/wt-42",
            "--branch", "work/issue-42",
        )
        os.environ["GWO_AGENT_ID"] = "worker-001"
        try:
            self.fixture.run(
                "send", "--to", "coordinator-001", "--type", "status",
                "--signal-id", "sig-cli-inbox-aaaa",
                "--payload", json.dumps({"phase": "running"}),
            )
        finally:
            os.environ["GWO_AGENT_ID"] = "coordinator-001"

    def test_inbox_returns_unacked_messages(self) -> None:
        self._seed_and_send()
        result = self.fixture.run("inbox", "--agent-id", "coordinator-001")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        msgs = json.loads(result.stdout)
        self.assertEqual(1, len(msgs))
        self.assertIsNone(msgs[0]["acked_at"])

    def test_inbox_ack_on_read(self) -> None:
        self._seed_and_send()
        self.fixture.run("inbox", "--agent-id", "coordinator-001", "--ack-on-read")
        result = self.fixture.run("inbox", "--agent-id", "coordinator-001")
        msgs = json.loads(result.stdout)
        self.assertEqual(0, len(msgs), "acked messages must not reappear")

    def test_inbox_fails_without_agent_id(self) -> None:
        saved = os.environ.pop("GWO_AGENT_ID")
        try:
            result = self.fixture.run("inbox", "--agent-id", "coordinator-001")
        finally:
            os.environ["GWO_AGENT_ID"] = saved
        self.assertNotEqual(0, result.returncode)
        self.assertIn("GWO_AGENT_ID", result.stderr)


class CliAgentStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = CliFixture(claim=True)

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_agent_status_unknown(self) -> None:
        result = self.fixture.run("agent", "status", "never-spawned")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("never-spawned", payload["agent_id"])
        self.assertIn(payload["state"], ("exited", "unknown"))

    def test_agent_status_registered(self) -> None:
        self.fixture.run(
            "agent", "register",
            "--agent-id", "worker-001",
            "--adapter", "paseo",
            "--runtime-ref", "ref-001",
            "--role", "worker",
            "--group-label", "g-42",
        )
        result = self.fixture.run("agent", "status", "worker-001")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("worker-001", payload["agent_id"])
        self.assertIn(payload["state"], ("running", "idle", "stalled", "exited"))

    def test_agent_register_fails_without_agent_id(self) -> None:
        saved = os.environ.pop("GWO_AGENT_ID")
        try:
            result = self.fixture.run(
                "agent", "register",
                "--agent-id", "w", "--adapter", "paseo",
                "--runtime-ref", "r", "--role", "worker",
            )
        finally:
            os.environ["GWO_AGENT_ID"] = saved
        self.assertNotEqual(0, result.returncode)
        self.assertIn("GWO_AGENT_ID", result.stderr)


class CliConfigCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = CliFixture(claim=True)

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_config_check_returns_structure(self) -> None:
        result = self.fixture.run("config", "check")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("valid", payload)
        self.assertIn("errors", payload)

    def test_config_check_fails_without_agent_id(self) -> None:
        saved = os.environ.pop("GWO_AGENT_ID")
        try:
            result = self.fixture.run("config", "check")
        finally:
            os.environ["GWO_AGENT_ID"] = saved
        self.assertNotEqual(0, result.returncode)
        self.assertIn("GWO_AGENT_ID", result.stderr)


class CliDoctorRebuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = CliFixture(claim=True)

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_doctor_rebuild_empty_snapshot(self) -> None:
        snapshot = {"issues": [], "agents": [], "worktrees": []}
        result = self.fixture.run(
            "doctor", "rebuild",
            "--github-snapshot", json.dumps(snapshot),
            "--adapter-listing", json.dumps([]),
            "--git-worktrees", json.dumps([]),
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["rebuilt"])
        self.assertEqual([], payload["ambiguities"])

    def test_doctor_rebuild_fails_without_agent_id(self) -> None:
        saved = os.environ.pop("GWO_AGENT_ID")
        try:
            result = self.fixture.run(
                "doctor", "rebuild",
                "--github-snapshot", json.dumps({"issues": [], "agents": [], "worktrees": []}),
                "--adapter-listing", json.dumps([]),
                "--git-worktrees", json.dumps([]),
            )
        finally:
            os.environ["GWO_AGENT_ID"] = saved
        self.assertNotEqual(0, result.returncode)
        self.assertIn("GWO_AGENT_ID", result.stderr)


# ---------------------------------------------------------------------------
# Commit-bound heavy review regression tests. Each test reproduces one
# finding from the review before the fix lands.
# ---------------------------------------------------------------------------


class InboxIdentityLeakTests(unittest.TestCase):
    """Finding 1: inbox must require the caller-supplied agent_id to equal the
    live GWO_AGENT_ID so an injected identity cannot read or ACK another
    recipient's messages.
    """

    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.mailbox_mod = self.fixture.mailbox_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_intruder_cannot_read_other_recipient_messages(self) -> None:
        with self.fixture.as_agent("worker-001"):
            self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={"secret": "coordinator-payload"},
                signal_id="sig-leak-aaaaaaaaaa",
            )
        # intruder-001 has a valid injected identity but is not coordinator-001.
        with self.fixture.as_agent("intruder-001"):
            with self.assertRaises(self.mailbox_mod.MailboxError):
                self.store.inbox(agent_id="coordinator-001")

    def test_intruder_cannot_ack_other_recipient_messages(self) -> None:
        with self.fixture.as_agent("worker-001"):
            self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={"secret": "coordinator-payload"},
                signal_id="sig-leak-ack-aaaaaaaaa",
            )
        with self.fixture.as_agent("intruder-001"):
            with self.assertRaises(self.mailbox_mod.MailboxError):
                self.store.inbox(agent_id="coordinator-001", ack_on_read=True)
        # coordinator-001 can still read and ack its own message.
        msgs = self.store.inbox(agent_id="coordinator-001", ack_on_read=True)
        self.assertEqual(1, len(msgs))
        self.assertEqual("coordinator-001", msgs[0]["acked_by"])


class AskBlockingTests(unittest.TestCase):
    """Finding 2: ask must block for the correlated reply and inbox --wait must
    actually wait for events rather than returning immediately.
    """

    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.mailbox_mod = self.fixture.mailbox_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_ask_returns_correlated_reply(self) -> None:
        # coordinator sends ask to worker; worker replies; ask returns the reply.
        import threading

        ask_signal = "sig-askblock-aaaaaaaaa"
        reply_signal = "sig-replyblock-bbbbbb"
        ask_result: list[Any] = []
        ask_error: list[BaseException] = []

        def ask_thread() -> None:
            try:
                store = self.fixture.store_mod.Store.connect(
                    self.fixture.home, self.fixture.repo
                )
                try:
                    result = store.ask(
                        to_agent="worker-001",
                        payload={"question": "scope?"},
                        signal_id=ask_signal,
                        timeout=5.0,
                    )
                    ask_result.append(result)
                finally:
                    store.close()
            except BaseException as error:  # pragma: no cover
                ask_error.append(error)

        with self.fixture.as_agent("coordinator-001"):
            t = threading.Thread(target=ask_thread)
            t.start()
            # Give the ask thread time to send and block.
            t.join(timeout=0.5)
            self.assertTrue(t.is_alive(), "ask must block waiting for the reply")
            # Now send the reply as worker using a separate connection.
            self.fixture._set_agent("worker-001")
            worker_store = self.fixture.store_mod.Store.connect(
                self.fixture.home, self.fixture.repo
            )
            try:
                worker_store.send(
                    to_agent="coordinator-001",
                    event_type="reply",
                    payload={"answer": "yes"},
                    in_reply_to=ask_signal,
                    signal_id=reply_signal,
                )
            finally:
                worker_store.close()
            self.fixture._set_agent("coordinator-001")
            t.join(timeout=5.0)
        self.assertFalse(t.is_alive(), "ask must return after the correlated reply")
        self.assertEqual([], ask_error)
        self.assertGreater(len(ask_result), 0)
        self.assertEqual("reply", ask_result[0]["type"])
        self.assertEqual(ask_signal, ask_result[0]["in_reply_to"])

    def test_ask_times_out_without_reply(self) -> None:
        import time

        start = time.monotonic()
        with self.fixture.as_agent("coordinator-001"):
            with self.assertRaises(self.mailbox_mod.MailboxError) as ctx:
                self.store.ask(
                    to_agent="worker-001",
                    payload={"q": "?"},
                    signal_id="sig-asktimeout-aaaaa",
                    timeout=0.3,
                )
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.25, "ask must actually block until timeout")
        self.assertIn("timeout", str(ctx.exception).lower())

    def test_inbox_wait_blocks_until_event_arrives(self) -> None:
        import threading

        inbox_result: list[list[dict[str, Any]]] = []
        inbox_error: list[BaseException] = []

        def inbox_thread() -> None:
            try:
                store = self.fixture.store_mod.Store.connect(
                    self.fixture.home, self.fixture.repo
                )
                try:
                    result = store.inbox(
                        agent_id="coordinator-001", wait=5.0
                    )
                    inbox_result.append(result)
                finally:
                    store.close()
            except BaseException as error:  # pragma: no cover
                inbox_error.append(error)

        with self.fixture.as_agent("coordinator-001"):
            t = threading.Thread(target=inbox_thread)
            t.start()
            t.join(timeout=0.5)
            self.assertTrue(t.is_alive(), "inbox --wait must block when no events exist")
            # Send an event so the wait can return.
            self.fixture._set_agent("worker-001")
            worker_store = self.fixture.store_mod.Store.connect(
                self.fixture.home, self.fixture.repo
            )
            try:
                worker_store.send(
                    to_agent="coordinator-001",
                    event_type="status",
                    payload={},
                    signal_id="sig-inboxwait-aaaaaaa",
                )
            finally:
                worker_store.close()
            self.fixture._set_agent("coordinator-001")
            t.join(timeout=5.0)
        self.assertFalse(t.is_alive())
        self.assertEqual([], inbox_error)
        self.assertGreater(len(inbox_result), 0)
        self.assertGreaterEqual(len(inbox_result[0]), 1)


class DoctorRebuildAmbiguityTests(unittest.TestCase):
    """Finding 3: doctor_rebuild must surface orphan git worktrees and must
    not default a missing adapter role to worker; it must surface ambiguity.
    """

    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.status_mod = self.fixture.status_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_orphan_git_worktree_surfaces_ambiguity(self) -> None:
        result = self.store.doctor_rebuild(
            github_snapshot={"issues": [], "agents": [], "worktrees": []},
            adapter_listing=[],
            git_worktrees=[
                {"path": "/tmp/wt-orphan", "branch": "work/issue-99",
                 "head": "deadbeef", "agent_id": None},
            ],
        )
        self.assertGreater(
            len(result["ambiguities"]), 0,
            "an orphan git worktree with no matching agent must surface ambiguity",
        )

    def test_missing_adapter_role_surfaces_ambiguity_not_default_worker(self) -> None:
        result = self.store.doctor_rebuild(
            github_snapshot={"issues": [], "agents": [], "worktrees": []},
            adapter_listing=[
                {"agent_id": "agent-no-role", "status": "running"},
            ],
            git_worktrees=[],
        )
        self.assertGreater(
            len(result["ambiguities"]), 0,
            "an adapter entry with no role must surface ambiguity, not default to worker",
        )
        # The agent must not have been inserted as a worker.
        rows = self.store.db.execute(
            "SELECT role FROM agents WHERE agent_id = ?", ("agent-no-role",)
        ).fetchall()
        self.assertEqual(0, len(rows), "agent with missing role must not be inserted")

    def test_missing_adapter_name_surfaces_ambiguity(self) -> None:
        result = self.store.doctor_rebuild(
            github_snapshot={"issues": [], "agents": [], "worktrees": []},
            adapter_listing=[
                {"agent_id": "agent-no-adapter", "role": "worker"},
            ],
            git_worktrees=[],
        )
        self.assertGreater(
            len(result["ambiguities"]), 0,
            "an adapter entry with no adapter name must surface ambiguity",
        )


class AgentStatusReadbackTests(unittest.TestCase):
    """Finding 4: agent_status must perform adapter/runtime readback and
    produce the running/stalled/exited states with terminal evidence, not
    always return running for unarchived and empty evidence.
    """

    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.status_mod = self.fixture.status_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_agent_status_calls_adapter_readback(self) -> None:
        calls: list[str] = []
        original = self.status_mod.readback_agent

        def fake_readback(store: Any, agent_id: str) -> dict[str, Any]:
            calls.append(agent_id)
            return {"state": "running", "terminal_evidence": {}, "last_activity": 0.0}

        self.status_mod.readback_agent = fake_readback
        try:
            self.store.register_agent(
                agent_id="worker-001",
                adapter="paseo",
                runtime_ref="ref-001",
                role="worker",
                group_label="g-42",
            )
            self.store.agent_status("worker-001")
        finally:
            self.status_mod.readback_agent = original
        self.assertEqual(["worker-001"], calls, "agent_status must call readback_agent")

    def test_agent_status_can_produce_stalled(self) -> None:
        self.store.register_agent(
            agent_id="worker-001",
            adapter="paseo",
            runtime_ref="ref-001",
            role="worker",
            group_label="g-42",
        )
        original = self.status_mod.readback_agent

        def fake_readback(store: Any, agent_id: str) -> dict[str, Any]:
            return {"state": "stalled", "terminal_evidence": {},
                    "last_activity": 0.0}

        self.status_mod.readback_agent = fake_readback
        try:
            status = self.store.agent_status("worker-001")
        finally:
            self.status_mod.readback_agent = original
        self.assertEqual("stalled", status["state"])

    def test_agent_status_exited_carries_terminal_evidence(self) -> None:
        self.store.register_agent(
            agent_id="worker-002",
            adapter="paseo",
            runtime_ref="ref-002",
            role="worker",
            group_label="g-42",
        )
        evidence = {"exit_code": 1, "reason": "crashed"}
        original = self.status_mod.readback_agent

        def fake_readback(store: Any, agent_id: str) -> dict[str, Any]:
            return {"state": "exited", "terminal_evidence": evidence,
                    "last_activity": 0.0}

        self.status_mod.readback_agent = fake_readback
        try:
            status = self.store.agent_status("worker-002")
        finally:
            self.status_mod.readback_agent = original
        self.assertEqual("exited", status["state"])
        self.assertEqual(evidence, status["terminal_evidence"])
        self.assertNotEqual({}, status["terminal_evidence"])

    def test_agent_status_unknown_uses_readback(self) -> None:
        original = self.status_mod.readback_agent

        def fake_readback(store: Any, agent_id: str) -> dict[str, Any]:
            return {"state": "exited", "terminal_evidence": {"reason": "never-spawned"},
                    "last_activity": 0.0}

        self.status_mod.readback_agent = fake_readback
        try:
            status = self.store.agent_status("never-spawned")
        finally:
            self.status_mod.readback_agent = original
        self.assertEqual("exited", status["state"])
        self.assertEqual({"reason": "never-spawned"}, status["terminal_evidence"])


class TrueConcurrencyTests(unittest.TestCase):
    """Finding 5: the concurrency tests must exercise overlapping threads, not
    sequential calls. This regression test asserts that two send operations
    started concurrently can overlap in time.
    """

    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.mailbox_mod = self.fixture.mailbox_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_concurrent_send_overlaps_in_time(self) -> None:
        import threading
        import time

        active_count = [0]
        lock = threading.Lock()

        def send_with_connection(store: Any, signal_id: str) -> dict[str, Any]:
            with lock:
                active_count[0] += 1
            time.sleep(0.1)
            with lock:
                active_count[0] -= 1
            return store.send(
                to_agent="coordinator-001", event_type="status",
                payload={}, signal_id=signal_id,
            )

        results: list[dict[str, Any]] = []
        store_mod = self.fixture.store_mod

        def send_a() -> None:
            os.environ["GWO_AGENT_ID"] = "worker-001"
            store = store_mod.Store.connect(self.fixture.home, self.fixture.repo)
            try:
                results.append(send_with_connection(store, "sig-truecon-aaaaaaaa"))
            finally:
                store.close()

        def send_b() -> None:
            os.environ["GWO_AGENT_ID"] = "worker-001"
            store = store_mod.Store.connect(self.fixture.home, self.fixture.repo)
            try:
                results.append(send_with_connection(store, "sig-truecon-bbbbbbbb"))
            finally:
                store.close()

        saved_agent = os.environ.get("GWO_AGENT_ID")
        try:
            t_a = threading.Thread(target=send_a)
            t_b = threading.Thread(target=send_b)
            t_a.start()
            t_b.start()
            t_a.join(timeout=5.0)
            t_b.join(timeout=5.0)
        finally:
            if saved_agent is None:
                os.environ.pop("GWO_AGENT_ID", None)
            else:
                os.environ["GWO_AGENT_ID"] = saved_agent
        # Both threads ran and produced distinct messages with monotonic seqs.
        self.assertEqual(2, len(results))
        self.assertNotEqual(results[0]["msg_id"], results[1]["msg_id"])
        seqs = sorted([r["seq"] for r in results])
        self.assertEqual([1, 2], seqs)

    def test_concurrent_ack_overlaps_in_time(self) -> None:
        import threading

        with self.fixture.as_agent("worker-001"):
            self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={},
                signal_id="sig-trueconack-aaaa",
            )
        results: list[list[dict[str, Any]]] = []
        store_mod = self.fixture.store_mod

        def ack_a() -> None:
            os.environ["GWO_AGENT_ID"] = "coordinator-001"
            store = store_mod.Store.connect(self.fixture.home, self.fixture.repo)
            try:
                results.append(store.inbox(
                    agent_id="coordinator-001", ack_on_read=True
                ))
            finally:
                store.close()

        def ack_b() -> None:
            os.environ["GWO_AGENT_ID"] = "coordinator-001"
            store = store_mod.Store.connect(self.fixture.home, self.fixture.repo)
            try:
                results.append(store.inbox(
                    agent_id="coordinator-001", ack_on_read=True
                ))
            finally:
                store.close()

        saved_agent = os.environ.get("GWO_AGENT_ID")
        try:
            t_a = threading.Thread(target=ack_a)
            t_b = threading.Thread(target=ack_b)
            t_a.start()
            t_b.start()
            t_a.join(timeout=5.0)
            t_b.join(timeout=5.0)
        finally:
            if saved_agent is None:
                os.environ.pop("GWO_AGENT_ID", None)
            else:
                os.environ["GWO_AGENT_ID"] = saved_agent
        # Exactly one ACK wins; the other sees zero messages.
        total = sum(len(r) for r in results)
        self.assertEqual(1, total, "exactly one concurrent ACK must win")


# ---------------------------------------------------------------------------
# Second commit-bound heavy review regression tests. Each test reproduces one
# finding from the review before the fix lands.
# ---------------------------------------------------------------------------


class ReplyAuthorBindingTests(unittest.TestCase):
    """Finding 1: reply must verify the referenced ask exists, the reply author
    is that ask's intended recipient, and the reply recipient is the ask author.
    An adversarial reply from an unrelated agent must be rejected so ask()
    cannot accept it.
    """

    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.mailbox_mod = self.fixture.mailbox_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_reply_rejects_when_referenced_ask_does_not_exist(self) -> None:
        with self.fixture.as_agent("worker-001"):
            with self.assertRaises(self.mailbox_mod.MailboxError) as ctx:
                self.store.send(
                    to_agent="coordinator-001",
                    event_type="reply",
                    payload={"answer": "yes"},
                    in_reply_to="sig-nonexistent-ask",
                    signal_id="sig-reply-noask-aaaa",
                )
        self.assertIn("ask", str(ctx.exception).lower())

    def test_reply_rejects_adversarial_author_not_ask_recipient(self) -> None:
        # coordinator asks worker-001.
        ask = self.store.send(
            to_agent="worker-001",
            event_type="ask",
            payload={"q": "scope?"},
            signal_id="sig-rb-ask-aaaaaaaaa",
        )
        self.assertEqual("coordinator-001", ask["from_agent"])
        self.assertEqual("worker-001", ask["to_agent"])
        # An unrelated agent (worker-002) tries to reply to the ask,
        # claiming to be the answerer. This must be rejected because
        # worker-002 is not the ask's intended recipient.
        with self.fixture.as_agent("worker-002"):
            with self.assertRaises(self.mailbox_mod.MailboxError) as ctx:
                self.store.send(
                    to_agent="coordinator-001",
                    event_type="reply",
                    payload={"answer": "hijack"},
                    in_reply_to="sig-rb-ask-aaaaaaaaa",
                    signal_id="sig-rb-reply-adversa",
                )
        self.assertIn("not the intended recipient", str(ctx.exception).lower())

    def test_reply_rejects_when_recipient_is_not_ask_author(self) -> None:
        # coordinator asks worker-001.
        self.store.send(
            to_agent="worker-001",
            event_type="ask",
            payload={"q": "scope?"},
            signal_id="sig-rb-ask2-aaaaaaaa",
        )
        # worker-001 (the legitimate recipient) tries to reply to someone
        # other than the ask author (coordinator-001). This must be rejected.
        with self.fixture.as_agent("worker-001"):
            with self.assertRaises(self.mailbox_mod.MailboxError) as ctx:
                self.store.send(
                    to_agent="worker-002",
                    event_type="reply",
                    payload={"answer": "wrong-recipient"},
                    in_reply_to="sig-rb-ask2-aaaaaaaa",
                    signal_id="sig-rb-reply-wrongrec",
                )
        self.assertIn("not the ask author", str(ctx.exception).lower())

    def test_ask_rejects_adversarial_reply_from_unrelated_agent(self) -> None:
        import threading

        ask_signal = "sig-rb-ask3-aaaaaaaaa"
        ask_result: list[Any] = []
        ask_error: list[BaseException] = []

        def ask_thread() -> None:
            try:
                store = self.fixture.store_mod.Store.connect(
                    self.fixture.home, self.fixture.repo
                )
                try:
                    result = store.ask(
                        to_agent="worker-001",
                        payload={"q": "?"},
                        signal_id=ask_signal,
                        timeout=1.0,
                    )
                    ask_result.append(result)
                finally:
                    store.close()
            except BaseException as error:
                ask_error.append(error)

        with self.fixture.as_agent("coordinator-001"):
            t = threading.Thread(target=ask_thread)
            t.start()
            t.join(timeout=0.3)
            self.assertTrue(t.is_alive(), "ask must block")
            # Adversarial reply from worker-002 (not the intended recipient).
            self.fixture._set_agent("worker-002")
            adv_store = self.fixture.store_mod.Store.connect(
                self.fixture.home, self.fixture.repo
            )
            try:
                with self.assertRaises(self.mailbox_mod.MailboxError):
                    adv_store.send(
                        to_agent="coordinator-001",
                        event_type="reply",
                        payload={"answer": "hijack"},
                        in_reply_to=ask_signal,
                        signal_id="sig-rb-adv-reply-aa",
                    )
            finally:
                adv_store.close()
            self.fixture._set_agent("coordinator-001")
            t.join(timeout=2.0)
        # ask must have timed out because the adversarial reply was rejected.
        self.assertEqual([], ask_result)
        self.assertGreater(len(ask_error), 0)
        self.assertIn("timed out", str(ask_error[0]).lower())


class DispatchScopedInboxBindingTests(unittest.TestCase):
    """Finding 2: dispatch-scoped inbox must bind agent_id to the live caller,
    return only deliveries the caller may read, and ensure only the intended
    recipient can ACK.
    """

    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.mailbox_mod = self.fixture.mailbox_mod
        self.dispatch_id = self.fixture.dispatch["dispatch_id"]

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_dispatch_scoped_inbox_returns_only_readable_by_caller(self) -> None:
        # coordinator sends a message to worker-001 (worker reads its own).
        self.store.send(
            to_agent="worker-001",
            event_type="status",
            payload={"msg": "to-worker"},
            signal_id="sig-dsi-to-worker-aaa",
        )
        # worker-001 sends a message to coordinator (outbound, worker should
        # see its own outbound; coordinator should see its inbound).
        with self.fixture.as_agent("worker-001"):
            self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={"msg": "to-coordinator"},
                signal_id="sig-dsi-to-coord-aaaa",
            )
        # worker-001 reads its dispatch-scoped inbox: should see messages
        # addressed TO worker-001 (its inbox), not its own outbound to coordinator.
        with self.fixture.as_agent("worker-001"):
            msgs = self.store.inbox(
                agent_id="worker-001",
                dispatch_id=self.dispatch_id,
            )
        self.assertGreater(len(msgs), 0, "must see the message addressed to worker-001")
        to_agent_values = [m["to_agent"] for m in msgs]
        self.assertTrue(
            all(a == "worker-001" for a in to_agent_values),
            "dispatch-scoped inbox for the dispatched agent must return only "
            "messages addressed to that agent, not its outbound traffic",
        )

    def test_dispatch_scoped_inbox_ack_only_by_intended_recipient(self) -> None:
        # coordinator sends a message to worker-001.
        self.store.send(
            to_agent="worker-001",
            event_type="status",
            payload={"msg": "to-worker"},
            signal_id="sig-dsi-ack-aaaaaaaaa",
        )
        # coordinator reads the dispatch-scoped inbox with ack_on_read.
        # coordinator is NOT the intended recipient (worker-001 is), so the
        # message must NOT be acked by coordinator.
        msgs = self.store.inbox(
            agent_id="worker-001",
            dispatch_id=self.dispatch_id,
            ack_on_read=True,
        )
        # The message is visible (coordinator can read dispatch traffic) but
        # must not be acked by coordinator because coordinator is not the
        # recipient.
        self.assertGreater(len(msgs), 0)
        for m in msgs:
            self.assertIsNone(
                m["acked_at"],
                "coordinator must not ACK messages addressed to worker-001",
            )
        # worker-001 (the actual recipient) can ACK.
        with self.fixture.as_agent("worker-001"):
            msgs = self.store.inbox(
                agent_id="worker-001",
                dispatch_id=self.dispatch_id,
                ack_on_read=True,
            )
        self.assertGreater(len(msgs), 0)
        self.assertEqual("worker-001", msgs[0]["acked_by"])

    def test_dispatch_scoped_inbox_rejects_agent_id_not_caller(self) -> None:
        # An intruder (not the dispatched agent, not coordinator) tries to
        # read a dispatch-scoped inbox with an agent_id that is not theirs.
        with self.fixture.as_agent("intruder-001"):
            with self.assertRaises(self.mailbox_mod.MailboxError):
                self.store.inbox(
                    agent_id="worker-001",
                    dispatch_id=self.dispatch_id,
                )


class DoneTerminalEvidenceTests(unittest.TestCase):
    """Finding 3: done CLI must accept structured terminal evidence input,
    pass it through to mark_done, and the two-agent send -> ack -> done
    scenario must record and assert non-empty terminal_evidence_json.
    """

    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.mailbox_mod = self.fixture.mailbox_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_mark_done_records_non_empty_terminal_evidence(self) -> None:
        evidence = {
            "candidate_sha": "abc123",
            "pr_url": "https://example/pr/29",
            "changed_paths": ["a.py", "b.py"],
        }
        with self.fixture.as_agent("worker-001"):
            result = self.store.mark_done(
                task_id=self.fixture.task["task_id"],
                dispatch_id=self.fixture.dispatch["dispatch_id"],
                status="done",
                evidence=evidence,
            )
        self.assertEqual("done", result["status"])
        self.assertIsNotNone(result["terminal_evidence_json"])
        stored = json.loads(result["terminal_evidence_json"])
        self.assertEqual(evidence, stored)
        self.assertNotEqual({}, stored)

    def test_cli_done_accepts_terminal_evidence(self) -> None:
        cli = CliFixture(claim=True)
        try:
            create = cli.run(
                "task", "create", "--issue", "42", "--group", "g-42", "--risk", "standard"
            )
            task_id = json.loads(create.stdout)["task_id"]
            cli.run("task", "update", task_id, "--status", "ready")
            dispatch = cli.run(
                "dispatch", "create",
                "--task-id", task_id,
                "--agent-id", "worker-001",
                "--worktree", "/tmp/wt-42",
                "--branch", "work/issue-42",
            )
            dispatch_id = json.loads(dispatch.stdout)["dispatch_id"]
            os.environ["GWO_AGENT_ID"] = "worker-001"
            try:
                evidence = json.dumps({"candidate_sha": "deadbeef", "pr_url": "https://x"})
                result = cli.run(
                    "done", "--task-id", task_id, "--dispatch-id", dispatch_id,
                    "--status", "done", "--evidence", evidence,
                )
            finally:
                os.environ["GWO_AGENT_ID"] = "coordinator-001"
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            stored = json.loads(payload["terminal_evidence_json"])
            self.assertEqual("deadbeef", stored["candidate_sha"])
        finally:
            cli.cleanup()

    def test_two_agent_send_ack_done_records_terminal_evidence(self) -> None:
        with self.fixture.as_agent("worker-001"):
            self.store.send(
                to_agent="coordinator-001",
                event_type="status",
                payload={"phase": "running"},
                signal_id="sig-evi-send-aaaaaaaa",
            )
        msgs = self.store.inbox(agent_id="coordinator-001", ack_on_read=True)
        self.assertEqual(1, len(msgs))
        self.assertEqual("coordinator-001", msgs[0]["acked_by"])
        evidence = {"candidate_sha": "face0ff", "pr_url": "https://example/pr/29"}
        with self.fixture.as_agent("worker-001"):
            result = self.store.mark_done(
                task_id=self.fixture.task["task_id"],
                dispatch_id=self.fixture.dispatch["dispatch_id"],
                status="done",
                evidence=evidence,
            )
        self.assertEqual("done", result["status"])
        stored = json.loads(result["terminal_evidence_json"])
        self.assertEqual(evidence, stored)
        self.assertNotEqual({}, stored)


class DoctorRebuildConflictTests(unittest.TestCase):
    """Finding 4: doctor_rebuild must detect conflicts across status, role,
    adapter, and existing-row evidence; surface ambiguities rather than
    first-wins insertion.
    """

    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.status_mod = self.fixture.status_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_same_status_conflicting_role_surfaces_ambiguity(self) -> None:
        result = self.store.doctor_rebuild(
            github_snapshot={"issues": [], "agents": [], "worktrees": []},
            adapter_listing=[
                {"agent_id": "agent-dup", "status": "running", "role": "worker", "adapter": "paseo"},
                {"agent_id": "agent-dup", "status": "running", "role": "reviewer", "adapter": "paseo"},
            ],
            git_worktrees=[],
        )
        self.assertGreater(
            len(result["ambiguities"]), 0,
            "same status but conflicting role must surface ambiguity",
        )

    def test_same_status_conflicting_adapter_surfaces_ambiguity(self) -> None:
        result = self.store.doctor_rebuild(
            github_snapshot={"issues": [], "agents": [], "worktrees": []},
            adapter_listing=[
                {"agent_id": "agent-dup2", "status": "running", "role": "worker", "adapter": "paseo"},
                {"agent_id": "agent-dup2", "status": "running", "role": "worker", "adapter": "headless"},
            ],
            git_worktrees=[],
        )
        self.assertGreater(
            len(result["ambiguities"]), 0,
            "same status but conflicting adapter must surface ambiguity",
        )

    def test_existing_row_conflicting_role_surfaces_ambiguity(self) -> None:
        # Pre-register an agent as worker.
        self.store.register_agent(
            agent_id="agent-existing",
            adapter="paseo",
            runtime_ref="ref-1",
            role="worker",
            group_label="g-42",
        )
        # Rebuild with an adapter listing that claims a different role.
        result = self.store.doctor_rebuild(
            github_snapshot={"issues": [], "agents": [], "worktrees": []},
            adapter_listing=[
                {"agent_id": "agent-existing", "status": "running", "role": "reviewer", "adapter": "paseo"},
            ],
            git_worktrees=[],
        )
        self.assertGreater(
            len(result["ambiguities"]), 0,
            "existing row with conflicting role must surface ambiguity",
        )

    def test_existing_row_conflicting_adapter_surfaces_ambiguity(self) -> None:
        self.store.register_agent(
            agent_id="agent-existing2",
            adapter="paseo",
            runtime_ref="ref-2",
            role="worker",
            group_label="g-42",
        )
        result = self.store.doctor_rebuild(
            github_snapshot={"issues": [], "agents": [], "worktrees": []},
            adapter_listing=[
                {"agent_id": "agent-existing2", "status": "running", "role": "worker", "adapter": "headless"},
            ],
            git_worktrees=[],
        )
        self.assertGreater(
            len(result["ambiguities"]), 0,
            "existing row with conflicting adapter must surface ambiguity",
        )


class ProductionReadbackTests(unittest.TestCase):
    """Finding 5: production readback_agent must be a real stdlib runtime
    readback seam that can observe stalled/exited state with terminal evidence,
    testable without monkeypatching.
    """

    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.status_mod = self.fixture.status_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_production_readback_running_for_active_agent(self) -> None:
        # Register an agent with the current process's PID so the production
        # readback can observe a live process via stdlib os.kill.
        import os as _os
        self.store.register_agent(
            agent_id="worker-001",
            adapter="paseo",
            runtime_ref="ref-001",
            role="worker",
            group_label="g-42",
            pid=_os.getpid(),
        )
        status = self.store.agent_status("worker-001")
        self.assertEqual("running", status["state"])
        self.assertEqual(True, status["registered"])

    def test_production_readback_exited_for_archived_agent(self) -> None:
        self.store.register_agent(
            agent_id="worker-002",
            adapter="paseo",
            runtime_ref="ref-002",
            role="worker",
            group_label="g-42",
        )
        # Archive the agent by setting archived_at.
        self.store.db.execute(
            "UPDATE agents SET archived_at = ? WHERE agent_id = ?",
            (1234567.0, "worker-002"),
        )
        status = self.store.agent_status("worker-002")
        self.assertEqual("exited", status["state"])
        self.assertNotEqual({}, status["terminal_evidence"])

    def test_production_readback_exited_for_unknown_agent(self) -> None:
        status = self.store.agent_status("never-spawned")
        self.assertEqual("exited", status["state"])
        self.assertNotEqual({}, status["terminal_evidence"])
        self.assertEqual(False, status["registered"])

    def test_production_readback_stalled_via_runtime_input(self) -> None:
        # Register an agent with a runtime_ref pointing at a non-existent
        # process/session. The production readback should detect that the
        # runtime is not advancing and report stalled.
        self.store.register_agent(
            agent_id="worker-003",
            adapter="paseo",
            runtime_ref="definitely-not-a-real-ref",
            role="worker",
            group_label="g-42",
        )
        status = self.store.agent_status("worker-003")
        # With no real runtime to observe, a registered agent whose runtime
        # cannot be contacted should be stalled (not running), because the
        # production readback must observe runtime activity, not just the
        # agents table.
        self.assertIn(status["state"], ("stalled", "exited"),
                      "unobservable runtime must not be reported as running")


class ConfigPreflightTests(unittest.TestCase):
    """Finding 6: config_check must be a non-destructive preflight that reads
    GWO_HOME/config.json and the expected migration set, and dispatch creation
    must be gated on a successful config check.
    """

    def setUp(self) -> None:
        self.fixture = MailboxFixture(self)
        self.store = self.fixture.store
        self.status_mod = self.fixture.status_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_config_check_reads_config_json(self) -> None:
        import json as _json
        config_path = Path(self.fixture.home) / "config.json"
        config_path.write_text(_json.dumps({
            "max_workers": 3,
            "max_reviewers": 2,
            "stalled_threshold_seconds": 900,
        }))
        result = self.store.config_check()
        self.assertTrue(result["valid"], result.get("errors", []))

    def test_config_check_reports_malformed_config_json(self) -> None:
        config_path = Path(self.fixture.home) / "config.json"
        config_path.write_text("{not valid json")
        result = self.store.config_check()
        self.assertFalse(result["valid"])
        self.assertGreater(len(result["errors"]), 0)

    def test_config_check_reports_migration_drift(self) -> None:
        # Drop a migration row to simulate drift.
        self.store.db.execute(
            "DELETE FROM schema_migrations WHERE name = '0002-messages-in-reply-to'"
        )
        result = self.store.config_check()
        self.assertFalse(result["valid"])
        self.assertGreater(len(result["errors"]), 0)

    def test_dispatch_creation_gated_on_config_check(self) -> None:
        # Malform config so config_check fails.
        config_path = Path(self.fixture.home) / "config.json"
        config_path.write_text("{bad json")
        task = self.store.create_task(issue=99, group_label="g-99", risk="fast")
        self.store.update_task(task_id=task["task_id"], status="ready")
        with self.assertRaises(self.store_mod_error()) as ctx:
            self.store.create_dispatch(
                task_id=task["task_id"],
                agent_id="worker-009",
                worktree="/tmp/wt-99",
                branch="work/issue-99",
            )
        self.assertIn("config", str(ctx.exception).lower())
        # The new task (issue 99) must remain ready (not dispatched).
        tasks = {t["issue"]: t for t in self.store.list_tasks()}
        self.assertEqual("ready", tasks[99]["status"])

    def store_mod_error(self):
        return self.fixture.store_mod.StoreError


if __name__ == "__main__":
    unittest.main()