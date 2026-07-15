from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "github-work-orchestrator" / "scripts"


def load_module(name: str):
    path = SCRIPT_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ready_frontier = load_module("ready_frontier")
reconcile_issue_state = load_module("reconcile_issue_state")
validate_issue_state = load_module("validate_issue_state")
task_creation_lease = load_module("task_creation_lease")


def issue(
    number: int,
    *,
    labels: tuple[str, ...] = ("ready-for-agent",),
    assignees: tuple[str, ...] = (),
    body: str = "",
):
    return {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://example.test/issues/{number}",
        "labels": [{"name": label} for label in labels],
        "assignees": [{"login": login} for login in assignees],
        "body": body,
    }


class ReadyFrontierTests(unittest.TestCase):
    def test_classifies_ready_claimed_blocked_and_invalid(self) -> None:
        issues = [
            issue(1),
            issue(2, assignees=("worker",)),
            issue(3, body="Blocked by: #9"),
            issue(4, labels=("ready-for-agent", "needs-info")),
        ]
        result = ready_frontier.classify_frontier(
            issues,
            {9: "OPEN"},
            {1: 0, 2: 0, 3: None, 4: 0},
        )

        self.assertEqual([1], [item["number"] for item in result["ready"]])
        self.assertEqual([2], [item["number"] for item in result["claimed"]])
        self.assertEqual([3], [item["number"] for item in result["blocked"]])
        self.assertEqual([4], [item["number"] for item in result["invalid"]])

    def test_closed_textual_blocker_does_not_block_fallback(self) -> None:
        result = ready_frontier.classify_frontier(
            [issue(5, body="Blocked by: #9")],
            {9: "CLOSED"},
            {5: None},
        )
        self.assertEqual([5], [item["number"] for item in result["ready"]])


class ReconciliationParserTests(unittest.TestCase):
    def test_parse_exact_supports_empty_set(self) -> None:
        self.assertEqual((12, set()), reconcile_issue_state.parse_exact("12="))

    def test_dependency_rejects_self_edge(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            reconcile_issue_state.parse_edge("12=12")


class ExecutionContractParserTests(unittest.TestCase):
    def test_v2_ready_contract_is_accepted(self) -> None:
        candidate = issue(
            6,
            body="""Execution-Contract: v2
Verification-Class: fast
Verification-Commands: python -m pytest tests/unit -q
Manual-Evidence: none
Architecture-Decision: resolved
Review-Owner: orchestrator
""",
        )
        self.assertEqual(
            [], validate_issue_state.execution_contract_findings(candidate)
        )

    def test_legacy_contract_is_a_migration_warning(self) -> None:
        findings = validate_issue_state.execution_contract_findings(issue(7))
        self.assertEqual(["legacy-execution-contract"], [f["code"] for f in findings])
        self.assertEqual("warning", findings[0]["severity"])

    def test_open_architecture_decision_is_not_ready(self) -> None:
        candidate = issue(
            8,
            body="""Execution-Contract: v2
Verification-Class: strict
Verification-Commands: python -m pytest -q
Manual-Evidence: none
Architecture-Decision: discussion-required
Review-Owner: orchestrator
""",
        )
        findings = validate_issue_state.execution_contract_findings(candidate)
        self.assertIn("open-architecture-decision", [f["code"] for f in findings])


class TaskCreationLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state_dir = Path(self.temporary.name)

    def store(self):
        return task_creation_lease.LeaseStore(self.state_dir)

    def assert_lease_error(self, code: str, operation) -> None:
        with self.assertRaises(task_creation_lease.LeaseError) as raised:
            operation()
        self.assertEqual(code, raised.exception.code)

    def test_host_singleflight_blocks_parallel_cross_project_creation(self) -> None:
        barrier = threading.Barrier(2)

        def reserve(repository: str, issue_number: int, owner: str):
            barrier.wait()
            try:
                lease = self.store().reserve(
                    repository=repository,
                    issue=issue_number,
                    branch=f"codex/issue-{issue_number}",
                    owner_token=owner,
                    ttl_seconds=30,
                )
                return ("reserved", lease["idempotency_key"])
            except task_creation_lease.LeaseError as error:
                return (error.code, None)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda arguments: reserve(*arguments),
                    (
                        ("NOirBRight/CodexHub", 140, "owner-codexhub"),
                        ("NOirBRight/AYASpace2", 9, "owner-ayaspace"),
                    ),
                )
            )

        self.assertEqual(1, sum(result[0] == "reserved" for result in results))
        self.assertEqual(
            1,
            sum(
                result[0]
                in {"ACTIVE_CREATION_EXISTS", "HOST_SINGLEFLIGHT_BUSY"}
                for result in results
            ),
        )

    def test_two_processes_receive_one_creation_authorization(self) -> None:
        script = SCRIPT_DIR / "task_creation_lease.py"
        gate = self.state_dir / "start-gate"
        ready = (self.state_dir / "ready-one", self.state_dir / "ready-two")
        barrier_wrapper = """
import os
import subprocess
import sys
import time

ready_path, gate_path, *command = sys.argv[1:]
open(ready_path, "x", encoding="utf-8").close()
deadline = time.monotonic() + 10
while not os.path.exists(gate_path):
    if time.monotonic() >= deadline:
        raise SystemExit(124)
    time.sleep(0.005)
completed = subprocess.run(command, capture_output=True, text=True)
sys.stdout.write(completed.stdout)
sys.stderr.write(completed.stderr)
raise SystemExit(completed.returncode)
"""

        def command(repository: str, issue_number: int, owner: str) -> list[str]:
            return [
                sys.executable,
                str(script),
                "--state-dir",
                str(self.state_dir),
                "reserve",
                "--repository",
                repository,
                "--issue",
                str(issue_number),
                "--branch",
                f"codex/issue-{issue_number}",
                "--owner-token",
                owner,
            ]

        first = subprocess.Popen(
            [
                sys.executable,
                "-c",
                barrier_wrapper,
                str(ready[0]),
                str(gate),
                *command("NOirBRight/CodexHub", 140, "owner-codexhub"),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ | {"CODEX_HOME": "C:/isolated-a"},
        )
        second = subprocess.Popen(
            [
                sys.executable,
                "-c",
                barrier_wrapper,
                str(ready[1]),
                str(gate),
                *command("NOirBRight/AYASpace2", 9, "owner-ayaspace"),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ | {"CODEX_HOME": "C:/isolated-b"},
        )
        processes = (first, second)
        deadline = time.monotonic() + 10
        while not all(path.is_file() for path in ready):
            if time.monotonic() >= deadline:
                for process in processes:
                    process.kill()
                self.fail("subprocess creation race did not reach the start barrier")
            time.sleep(0.005)
        gate.touch()
        outputs = [process.communicate(timeout=10) for process in processes]

        self.assertEqual([0, 2], sorted(process.returncode for process in processes))
        authorized = [
            json.loads(stdout)["lease"]
            for process, (stdout, _stderr) in zip(processes, outputs)
            if process.returncode == 0
        ]
        refused = [
            json.loads(stderr)
            for process, (_stdout, stderr) in zip(processes, outputs)
            if process.returncode == 2
        ]
        self.assertEqual([True], [lease["creation_authorized"] for lease in authorized])
        self.assertIn(
            refused[0]["error"],
            {"ACTIVE_CREATION_EXISTS", "HOST_SINGLEFLIGHT_BUSY"},
        )

    def test_default_lease_location_is_shared_across_codex_homes(self) -> None:
        local_state = self.state_dir / "host-local-state"
        state_environment = {}
        if os.name == "nt":
            state_environment["LOCALAPPDATA"] = str(local_state)
        elif sys.platform != "darwin":
            state_environment["XDG_STATE_HOME"] = str(local_state)
        with mock.patch.dict(
            os.environ,
            state_environment | {"CODEX_HOME": "C:/isolated-a"},
            clear=False,
        ):
            first = task_creation_lease._default_state_dir()
        with mock.patch.dict(
            os.environ,
            state_environment | {"CODEX_HOME": "C:/isolated-b"},
            clear=False,
        ):
            second = task_creation_lease._default_state_dir()
        self.assertEqual(first, second)
        if state_environment:
            self.assertTrue(first.is_relative_to(local_state))

    def test_same_owner_and_idempotency_key_reuses_one_lease(self) -> None:
        first = self.store().reserve(
            repository="NOirBRight/CodexHub",
            issue=140,
            branch="codex/issue-140-native-responses-tools",
            owner_token="owner-one",
        )
        second = self.store().reserve(
            repository="noirbright/codexhub",
            issue="140",
            branch="codex/issue-140-native-responses-tools",
            owner_token="owner-one",
        )
        self.assertEqual(first["lease_id"], second["lease_id"])
        self.assertTrue(first["creation_authorized"])
        self.assertFalse(second["creation_authorized"])
        self.assertTrue(second["idempotent"])

        self.assert_lease_error(
            "ACTIVE_CREATION_EXISTS",
            lambda: self.store().reserve(
                repository="NOirBRight/CodexHub",
                issue=140,
                branch="codex/issue-140-native-responses-tools",
                owner_token="owner-two",
            ),
        )

    def test_creation_unknown_and_expiry_never_allow_automatic_steal(self) -> None:
        store = self.store()
        store.reserve(
            repository="NOirBRight/CodexHub",
            issue=140,
            branch="codex/issue-140-native-responses-tools",
            owner_token="owner-one",
            ttl_seconds=5,
            now=100,
        )
        store.transition("owner-one", "invoking", now=100.5)
        store.transition(
            "owner-one", "queued", request_id="queued-request-one", now=101
        )
        store.transition("owner-one", "creation-unknown", now=102)

        self.assert_lease_error(
            "EXPIRED_CREATION_REQUIRES_RECONCILIATION",
            lambda: store.reserve(
                repository="NOirBRight/AYASpace2",
                issue=9,
                branch="codex/issue-9",
                owner_token="owner-two",
                now=1000,
            ),
        )
        self.assert_lease_error(
            "EXPIRED_CREATION_REQUIRES_RECONCILIATION",
            lambda: store.release("owner-one", now=1000),
        )

    def test_expired_owner_cannot_mutate_or_release_without_reconciliation(self) -> None:
        store = self.store()
        store.reserve(
            repository="NOirBRight/CodexHub",
            issue=140,
            branch="codex/issue-140-native-responses-tools",
            owner_token="owner-one",
            ttl_seconds=5,
            now=100,
        )
        store.transition("owner-one", "invoking", now=100.5)
        store.transition(
            "owner-one", "queued", request_id="queued-request-one", now=101
        )
        self.assert_lease_error(
            "EXPIRED_CREATION_REQUIRES_RECONCILIATION",
            lambda: store.transition("owner-one", "failed", now=106),
        )
        self.assert_lease_error(
            "EXPIRED_CREATION_REQUIRES_RECONCILIATION",
            lambda: store.release("owner-one", now=106),
        )
        reconciled = store.reconcile(
            "owner-one",
            host_restarted=True,
            request_id="queued-request-one",
            request_state="cancelled",
            task_state="absent",
            worktree_state="absent",
            evidence="readback-after-restart",
            now=107,
        )
        self.assertEqual("cancelled", reconciled["state"])
        store.release("owner-one", now=107)

    def test_expired_terminal_lease_can_only_be_released_by_its_owner(self) -> None:
        store = self.store()
        store.reserve(
            repository="NOirBRight/CodexHub",
            issue=140,
            branch="codex/issue-140-native-responses-tools",
            owner_token="owner-one",
            ttl_seconds=5,
            now=100,
        )
        store.transition("owner-one", "failed", now=101)
        self.assert_lease_error(
            "RECONCILIATION_NOT_REQUIRED",
            lambda: store.reconcile(
                "owner-one",
                host_restarted=True,
                request_id=None,
                request_state="no-receipt-terminal",
                task_state="absent",
                worktree_state="absent",
                evidence="terminal-record",
                now=1000,
            ),
        )
        self.assert_lease_error(
            "TERMINAL_LEASE_REQUIRES_OWNER_RELEASE",
            lambda: store.reserve(
                repository="NOirBRight/AYASpace2",
                issue=9,
                branch="codex/issue-9",
                owner_token="owner-two",
                now=1000,
            ),
        )
        self.assert_lease_error(
            "OWNER_MISMATCH", lambda: store.release("owner-two", now=1000)
        )
        released = store.release("owner-one", now=1000)
        self.assertEqual("failed", released["state"])

    def test_observed_progress_renews_the_bounded_lease_interval(self) -> None:
        store = self.store()
        store.reserve(
            repository="NOirBRight/CodexHub",
            issue=140,
            branch="codex/issue-140-native-responses-tools",
            owner_token="owner-one",
            ttl_seconds=5,
            now=100,
        )
        store.transition("owner-one", "invoking", now=102)
        queued = store.transition(
            "owner-one", "queued", request_id="queued-request-one", now=104
        )
        self.assertEqual(109, queued["expires_at"])
        materialized = store.transition("owner-one", "task-materialized", now=108)
        self.assertEqual(113, materialized["expires_at"])
        store.transition("owner-one", "bootstrap-ready", now=112)

    def test_malformed_persistent_state_fails_closed(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / task_creation_lease.ACTIVE_FILE).write_text(
            '{"schema_version": 1, "state": "reserved"}', encoding="utf-8"
        )
        self.assert_lease_error(
            "LEASE_STATE_UNREADABLE", lambda: self.store().inspect()
        )

    def test_reconciliation_requires_restart_task_and_worktree_evidence(self) -> None:
        store = self.store()
        store.reserve(
            repository="NOirBRight/CodexHub",
            issue=140,
            branch="codex/issue-140-native-responses-tools",
            owner_token="owner-one",
            now=100,
        )
        store.transition("owner-one", "invoking", now=100.5)
        store.transition(
            "owner-one", "queued", request_id="queued-request-one", now=101
        )
        store.transition("owner-one", "creation-unknown", now=102)

        self.assert_lease_error(
            "OWNER_MISMATCH",
            lambda: store.reconcile(
                "owner-two",
                host_restarted=True,
                request_id="queued-request-one",
                request_state="cancelled",
                task_state="absent",
                worktree_state="clean-orphan",
                evidence="readback-1",
                now=103,
            ),
        )
        self.assert_lease_error(
            "RECONCILIATION_EVIDENCE_REQUIRED",
            lambda: store.reconcile(
                "owner-one",
                host_restarted=False,
                request_id="queued-request-one",
                request_state="cancelled",
                task_state="absent",
                worktree_state="clean-orphan",
                evidence="readback-1",
                now=103,
            ),
        )
        reconciled = store.reconcile(
            "owner-one",
            host_restarted=True,
            request_id="queued-request-one",
            request_state="cancelled",
            task_state="absent",
            worktree_state="clean-orphan",
            evidence="readback-1",
            now=104,
        )
        self.assertEqual("cancelled", reconciled["state"])
        store.release("owner-one", now=105)
        self.assertIsNone(store.inspect())

    def test_queued_and_reconciliation_require_the_exact_request_identity(self) -> None:
        store = self.store()
        store.reserve(
            repository="NOirBRight/CodexHub",
            issue=140,
            branch="codex/issue-140-native-responses-tools",
            owner_token="owner-one",
            now=100,
        )
        self.assert_lease_error(
            "REQUEST_ID_REQUIRED",
            lambda: store.transition("owner-one", "queued", now=101),
        )
        store.transition("owner-one", "invoking", now=100.5)
        store.transition(
            "owner-one", "queued", request_id="queued-request-one", now=101
        )
        store.transition("owner-one", "creation-unknown", now=102)
        self.assert_lease_error(
            "REQUEST_ID_MISMATCH",
            lambda: store.reconcile(
                "owner-one",
                host_restarted=True,
                request_id="different-request",
                request_state="cancelled",
                task_state="absent",
                worktree_state="absent",
                evidence="readback-2",
                now=103,
            ),
        )

    def test_crash_during_native_create_reconciles_without_a_request_receipt(self) -> None:
        store = self.store()
        store.reserve(
            repository="NOirBRight/CodexHub",
            issue=140,
            branch="codex/issue-140-native-responses-tools",
            owner_token="owner-one",
            now=100,
        )
        invoking = store.transition("owner-one", "invoking", now=101)
        self.assertEqual("invoking", invoking["state"])
        reconciled = store.reconcile(
            "owner-one",
            host_restarted=True,
            request_id=None,
            request_state="no-receipt-terminal",
            task_state="absent",
            worktree_state="absent",
            evidence="post-restart-full-inventory",
            now=102,
        )
        self.assertEqual("failed", reconciled["state"])
        store.release("owner-one", now=103)

    def test_expired_reserved_lease_cannot_adopt_a_materialized_task(self) -> None:
        store = self.store()
        store.reserve(
            repository="NOirBRight/CodexHub",
            issue=140,
            branch="codex/issue-140-native-responses-tools",
            owner_token="owner-one",
            ttl_seconds=5,
            now=100,
        )
        self.assert_lease_error(
            "RECONCILIATION_EVIDENCE_REQUIRED",
            lambda: store.reconcile(
                "owner-one",
                host_restarted=True,
                request_id=None,
                request_state="no-receipt-materialized",
                task_state="materialized",
                worktree_state="owned",
                evidence="post-restart-full-inventory",
                now=106,
            ),
        )

    def test_admitted_no_receipt_creation_can_adopt_its_materialized_task(self) -> None:
        for uncertain_state in ("invoking", "creation-unknown"):
            with self.subTest(uncertain_state=uncertain_state):
                store = task_creation_lease.LeaseStore(
                    self.state_dir / uncertain_state
                )
                store.reserve(
                    repository="NOirBRight/CodexHub",
                    issue=140,
                    branch="codex/issue-140-native-responses-tools",
                    owner_token="owner-one",
                    now=100,
                )
                store.transition("owner-one", "invoking", now=101)
                if uncertain_state == "creation-unknown":
                    store.transition("owner-one", "creation-unknown", now=102)
                reconciled = store.reconcile(
                    "owner-one",
                    host_restarted=True,
                    request_id=None,
                    request_state="no-receipt-materialized",
                    task_state="materialized",
                    worktree_state="owned",
                    evidence="post-restart-full-inventory",
                    now=103,
                )
                self.assertEqual("task-materialized", reconciled["state"])

    def test_task_materialized_recovery_path_is_explicit(self) -> None:
        store = self.store()
        store.reserve(
            repository="NOirBRight/CodexHub",
            issue=140,
            branch="codex/issue-140-native-responses-tools",
            owner_token="owner-one",
        )
        store.transition("owner-one", "invoking")
        store.transition(
            "owner-one", "queued", request_id="queued-request-one"
        )
        store.transition("owner-one", "task-materialized")
        self.assert_lease_error(
            "INVALID_TRANSITION",
            lambda: store.transition("owner-one", "preflight-ready"),
        )
        recovered = store.transition(
            "owner-one", "preflight-ready", recovery_path=True
        )
        self.assertTrue(recovered["recovery_path"])
        store.transition("owner-one", "activated")
        store.release("owner-one")

    def test_release_requires_owner_and_terminal_state(self) -> None:
        store = self.store()
        store.reserve(
            repository="NOirBRight/CodexHub",
            issue=140,
            branch="codex/issue-140-native-responses-tools",
            owner_token="owner-one",
        )
        for state in (
            "invoking",
            "queued",
            "worktree-creating",
            "task-materialized",
            "bootstrap-ready",
            "preflight-ready",
            "activated",
        ):
            store.transition(
                "owner-one",
                state,
                request_id="queued-request-one" if state == "queued" else None,
            )

        self.assert_lease_error(
            "OWNER_MISMATCH", lambda: store.release("owner-two")
        )
        released = store.release("owner-one")
        self.assertEqual("activated", released["state"])
        self.assertIsNone(store.inspect())

    def test_cli_reserve_transition_and_release_round_trip(self) -> None:
        script = SCRIPT_DIR / "task_creation_lease.py"
        state_arguments = ["--state-dir", str(self.state_dir)]
        reserved = subprocess.run(
            [
                sys.executable,
                str(script),
                *state_arguments,
                "reserve",
                "--repository",
                "NOirBRight/CodexHub",
                "--issue",
                "140",
                "--branch",
                "codex/issue-140-native-responses-tools",
                "--owner-token",
                "owner-cli",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual("reserved", json.loads(reserved.stdout)["lease"]["state"])

        for command in (
            ("transition", "--state", "failed", "--owner-token", "owner-cli"),
            ("release", "--owner-token", "owner-cli"),
        ):
            completed = subprocess.run(
                [sys.executable, str(script), *state_arguments, *command],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(json.loads(completed.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
