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


if __name__ == "__main__":
    unittest.main()