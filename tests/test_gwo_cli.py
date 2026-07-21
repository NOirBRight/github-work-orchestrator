from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GWO_PY = ROOT / "skills" / "github-work-orchestrator" / "scripts" / "gwo.py"
SCRIPT_DIR = str(GWO_PY.parent)


class CliFixture:
    """Run gwo.py against an isolated temporary GWO_HOME."""

    def __init__(self, repo: str = "owner/repo", *, claim: bool = False) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.repo = repo
        self._saved_env = {
            "GWO_HOME": os.environ.get("GWO_HOME"),
            "GWO_AGENT_ID": os.environ.get("GWO_AGENT_ID"),
            "PYTHONPATH": os.environ.get("PYTHONPATH"),
        }
        os.environ["GWO_HOME"] = str(self.home)
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        os.environ["PYTHONPATH"] = SCRIPT_DIR + os.pathsep + os.environ.get("PYTHONPATH", "")
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


class CliCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = CliFixture()

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_coordinator_claim_succeeds(self) -> None:
        result = self.fixture.run("coordinator", "claim")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("coordinator-001", result.stdout)

    def test_coordinator_second_claim_is_rejected(self) -> None:
        self.fixture.run("coordinator", "claim")
        result = self.fixture.run("coordinator", "claim")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("coordinator-001", result.stderr)

    def test_coordinator_release_succeeds(self) -> None:
        self.fixture.run("coordinator", "claim")
        result = self.fixture.run("coordinator", "release")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_coordinator_release_without_claim_fails(self) -> None:
        result = self.fixture.run("coordinator", "release")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("no active", result.stderr.lower())


class CliTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = CliFixture(claim=True)

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_task_create_returns_json(self) -> None:
        result = self.fixture.run(
            "task", "create", "--issue", "42", "--group", "g-42", "--risk", "standard"
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("pending", payload["status"])
        self.assertEqual(42, payload["issue"])
        self.assertEqual("coordinator-001", payload["created_by"])

    def test_task_list_returns_json_array(self) -> None:
        self.fixture.run("task", "create", "--issue", "42", "--group", "g-42", "--risk", "standard")
        self.fixture.run("task", "create", "--issue", "43", "--group", "g-43", "--risk", "fast")
        result = self.fixture.run("task", "list")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        tasks = json.loads(result.stdout)
        self.assertEqual(2, len(tasks))

    def test_task_update_status(self) -> None:
        create = self.fixture.run(
            "task", "create", "--issue", "42", "--group", "g-42", "--risk", "standard"
        )
        task_id = json.loads(create.stdout)["task_id"]
        result = self.fixture.run("task", "update", task_id, "--status", "ready")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("ready", json.loads(result.stdout)["status"])

    def test_task_update_rejects_invalid_transition(self) -> None:
        create = self.fixture.run(
            "task", "create", "--issue", "42", "--group", "g-42", "--risk", "standard"
        )
        task_id = json.loads(create.stdout)["task_id"]
        result = self.fixture.run("task", "update", task_id, "--status", "done")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("invalid", result.stderr.lower())


class CliDispatchDoneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = CliFixture(claim=True)

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_dispatch_and_done_lifecycle(self) -> None:
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
        self.assertEqual(0, dispatch.returncode, dispatch.stdout + dispatch.stderr)
        dispatch_id = json.loads(dispatch.stdout)["dispatch_id"]
        self.assertEqual("active", json.loads(dispatch.stdout)["status"])

        os.environ["GWO_AGENT_ID"] = "worker-001"
        done = self.fixture.run("done", "--task-id", task_id, "--dispatch-id", dispatch_id, "--status", "done")
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual("done", json.loads(done.stdout)["status"])

    def test_dispatch_rejects_non_ready_task(self) -> None:
        create = self.fixture.run(
            "task", "create", "--issue", "42", "--group", "g-42", "--risk", "standard"
        )
        task_id = json.loads(create.stdout)["task_id"]
        result = self.fixture.run(
            "dispatch", "create",
            "--task-id", task_id,
            "--agent-id", "worker-001",
            "--worktree", "/tmp/wt-42",
            "--branch", "work/issue-42",
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("pending", result.stderr.lower())

    def test_done_rejects_wrong_agent(self) -> None:
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
        dispatch_id = json.loads(dispatch.stdout)["dispatch_id"]
        os.environ["GWO_AGENT_ID"] = "worker-002"
        result = self.fixture.run("done", "--task-id", task_id, "--dispatch-id", dispatch_id, "--status", "done")
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertNotEqual(0, result.returncode)
        self.assertIn("agent", result.stderr.lower())


class CliIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = CliFixture(claim=True)

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_write_without_agent_id_fails(self) -> None:
        saved = os.environ.pop("GWO_AGENT_ID")
        try:
            result = self.fixture.run(
                "task", "create", "--issue", "42", "--group", "g-42", "--risk", "standard"
            )
        finally:
            os.environ["GWO_AGENT_ID"] = saved
        self.assertNotEqual(0, result.returncode)
        self.assertIn("GWO_AGENT_ID", result.stderr)

    def test_write_rejects_caller_supplied_identity(self) -> None:
        result = self.fixture.run(
            "task", "create", "--issue", "42", "--group", "g-42", "--risk", "standard",
            "--created-by", "attacker",
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("cannot be caller-supplied", result.stderr.lower())

    def test_every_write_fails_without_agent_id(self) -> None:
        commands = [
            ("coordinator", "claim"),
            ("task", "create", "--issue", "42", "--group", "g-42", "--risk", "standard"),
            ("task", "update", "t-x", "--status", "ready"),
            ("dispatch", "create", "--task-id", "t-x", "--agent-id", "w",
             "--worktree", "/x", "--branch", "b"),
            ("done", "--task-id", "t-x", "--dispatch-id", "d-x", "--status", "done"),
            ("send", "--to", "coordinator-001", "--type", "status",
             "--signal-id", "sig-x-aaaaaaaaaaa"),
            ("inbox", "--agent-id", "coordinator-001"),
            ("agent", "register", "--agent-id", "w", "--adapter", "paseo",
             "--runtime-ref", "r", "--role", "worker"),
            ("config", "check"),
        ]
        for args in commands:
            with self.subTest(cmd=args[0]):
                saved = os.environ.pop("GWO_AGENT_ID")
                try:
                    result = self.fixture.run(*args)
                finally:
                    os.environ["GWO_AGENT_ID"] = saved
                self.assertNotEqual(0, result.returncode, args)
                self.assertIn("GWO_AGENT_ID", result.stderr)

    def test_dispatch_rejects_caller_supplied_identity(self) -> None:
        create = self.fixture.run(
            "task", "create", "--issue", "42", "--group", "g-42", "--risk", "standard"
        )
        task_id = json.loads(create.stdout)["task_id"]
        self.fixture.run("task", "update", task_id, "--status", "ready")
        result = self.fixture.run(
            "dispatch", "create",
            "--task-id", task_id,
            "--agent-id", "worker-001",
            "--worktree", "/tmp/wt-42",
            "--branch", "work/issue-42",
            "--dispatched-by", "attacker",
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("cannot be caller-supplied", result.stderr.lower())

    def test_done_rejects_caller_supplied_identity(self) -> None:
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
        dispatch_id = json.loads(dispatch.stdout)["dispatch_id"]
        os.environ["GWO_AGENT_ID"] = "worker-001"
        result = self.fixture.run(
            "done", "--task-id", task_id, "--dispatch-id", dispatch_id,
            "--status", "done", "--actor", "attacker",
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertNotEqual(0, result.returncode)
        self.assertIn("cannot be caller-supplied", result.stderr.lower())


class CliPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = CliFixture()

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_state_persists_across_invocations(self) -> None:
        self.fixture.run("coordinator", "claim")
        result = self.fixture.run("coordinator", "claim")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("coordinator-001", result.stderr)


class CliHelpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = CliFixture()

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_help_lists_subcommands(self) -> None:
        result = self.fixture.run("--help")
        self.assertEqual(0, result.returncode)
        for command in ("coordinator", "task", "dispatch", "done"):
            self.assertIn(command, result.stdout)


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


if __name__ == "__main__":
    unittest.main()