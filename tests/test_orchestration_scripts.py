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

    def reserve(self, *, owner: str = "owner-a", now: float = 0.0):
        return self.store().reserve(
            repository="owner/repo",
            issue=17,
            branch="codex/issue-17-example",
            owner_token=owner,
            ttl_seconds=10,
            now=now,
        )

    def assert_lease_error(self, code: str, operation) -> None:
        with self.assertRaises(task_creation_lease.LeaseError) as raised:
            operation()
        self.assertEqual(code, raised.exception.code)

    def test_host_singleflight_blocks_parallel_cross_project_creation(self) -> None:
        barrier = threading.Barrier(2)

        def reserve(repository, issue_number, branch, token):
            barrier.wait(timeout=5)
            try:
                result = self.store().reserve(
                    repository=repository,
                    issue=issue_number,
                    branch=branch,
                    owner_token=token,
                    now=100.0,
                )
                return ("ok", result)
            except task_creation_lease.LeaseError as error:
                return ("error", error.code)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda args: reserve(*args),
                    [
                        ("owner/one", 1, "codex/issue-1-one", "token-one"),
                        ("owner/two", 2, "codex/issue-2-two", "token-two"),
                    ],
                )
            )
        self.assertEqual(1, sum(status == "ok" for status, _ in results))
        self.assertEqual(
            ["ACTIVE_CREATION_EXISTS"],
            [value for status, value in results if status == "error"],
        )

    def test_new_record_uses_only_minimal_creation_states(self) -> None:
        lease = self.reserve()
        self.assertEqual(2, lease["schema_version"])
        self.assertEqual("creating", lease["state"])
        self.assertTrue(lease["creation_authorized"])
        for forbidden in (
            "bootstrap-ready",
            "preflight-ready",
            "activated",
            "worktree-creating",
        ):
            self.assertNotIn(forbidden, json.dumps(lease))

    def test_same_owner_and_key_is_idempotent_without_second_authorization(self) -> None:
        first = self.reserve()
        second = self.reserve(now=1.0)
        self.assertEqual(first["lease_id"], second["lease_id"])
        self.assertTrue(first["creation_authorized"])
        self.assertFalse(second["creation_authorized"])
        self.assertTrue(second["idempotent"])

    def test_real_task_identity_releases_creation_guard_immediately(self) -> None:
        store = self.store()
        self.reserve()
        store.record_request("owner-a", "client-17", now=1.0)
        released = store.release(
            "owner-a",
            outcome="task-materialized",
            task_id="019f0000-0000-7000-8000-000000000017",
            worktree_state="owned",
            evidence="exact-task-and-worktree-readback",
            now=2.0,
        )
        self.assertEqual("task-materialized", released["outcome"])
        self.assertIsNone(store.inspect())

    def test_exact_request_releases_materialized_task_after_owner_turn_loss(self) -> None:
        store = self.store()
        self.reserve()
        store.record_request("owner-a", "client-17", now=1.0)

        self.assert_lease_error(
            "REQUEST_ID_MISMATCH",
            lambda: store.release(
                None,
                request_id="wrong-client",
                outcome="task-materialized",
                task_id="019f0000-0000-7000-8000-000000000017",
                worktree_state="owned",
                evidence="exact-task-and-worktree-readback",
                now=20.0,
            ),
        )
        released = store.release(
            None,
            request_id="client-17",
            outcome="task-materialized",
            task_id="019f0000-0000-7000-8000-000000000017",
            worktree_state="owned",
            evidence="exact-task-and-worktree-readback",
            now=20.0,
        )
        self.assertEqual("task-materialized", released["outcome"])
        self.assertTrue(released["request_authenticated"])
        self.assertIsNone(store.inspect())

    def test_uncertain_creation_blocks_reentry_even_after_expiry(self) -> None:
        store = self.store()
        self.reserve()
        store.mark_uncertain("owner-a", request_id="client-17", now=1.0)
        self.assert_lease_error(
            "RECONCILIATION_REQUIRED",
            lambda: store.reserve(
                repository="other/repo",
                issue=18,
                branch="codex/issue-18-example",
                owner_token="owner-b",
                now=20.0,
            ),
        )

    def test_reconciliation_requires_restart_owner_and_exact_evidence(self) -> None:
        store = self.store()
        self.reserve()
        store.mark_uncertain("owner-a", request_id="client-17", now=1.0)
        kwargs = {
            "request_id": "client-17",
            "outcome": "task-materialized",
            "task_id": "019f0000-0000-7000-8000-000000000017",
            "worktree_state": "owned",
            "evidence": "exact-readback",
            "now": 20.0,
        }
        self.assert_lease_error(
            "RECONCILIATION_EVIDENCE_REQUIRED",
            lambda: store.reconcile("owner-a", host_restarted=False, **kwargs),
        )
        self.assert_lease_error(
            "REQUEST_ID_MISMATCH",
            lambda: store.reconcile(
                "owner-a",
                host_restarted=True,
                **(kwargs | {"request_id": "wrong-client"}),
            ),
        )
        result = store.reconcile("owner-a", host_restarted=True, **kwargs)
        self.assertEqual("task-materialized", result["outcome"])
        self.assertIsNone(store.inspect())

    def test_terminal_no_task_releases_only_with_safe_worktree(self) -> None:
        store = self.store()
        self.reserve()
        self.assert_lease_error(
            "RECONCILIATION_EVIDENCE_REQUIRED",
            lambda: store.release(
                "owner-a",
                outcome="terminal-no-task",
                task_id=None,
                worktree_state="dirty",
                evidence="native-terminal-readback",
                now=1.0,
            ),
        )
        result = store.release(
            "owner-a",
            outcome="terminal-no-task",
            task_id=None,
            worktree_state="clean-orphan",
            evidence="native-terminal-readback",
            now=1.0,
        )
        self.assertEqual("terminal-no-task", result["outcome"])

    def test_legacy_schema_is_readable_and_drainable(self) -> None:
        store = self.store()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "schema_version": 1,
            "lease_id": "legacy-lease",
            "idempotency_key": "f" * 64,
            "repository": "owner/repo",
            "issue": "17",
            "branch": "codex/issue-17-example",
            "owner_token_sha256": task_creation_lease._token_digest("owner-a"),
            "state": "preflight-ready",
            "revision": 4,
            "created_at": 0.0,
            "updated_at": 1.0,
            "expires_at": 2.0,
            "ttl_seconds": 10,
        }
        store.active_path.write_text(json.dumps(record), encoding="utf-8")
        self.assertTrue(store.inspect()["legacy"])
        result = store.reconcile(
            "owner-a",
            host_restarted=True,
            request_id=None,
            outcome="terminal-no-task",
            task_id=None,
            worktree_state="absent",
            evidence="full-native-and-worktree-readback",
            now=20.0,
        )
        self.assertTrue(result["legacy"])
        self.assertIsNone(store.inspect())

    def test_default_location_is_shared_across_codex_homes(self) -> None:
        common = {
            "LOCALAPPDATA": str(self.state_dir / "local-app-data"),
            "USERPROFILE": str(self.state_dir / "profile"),
            "HOME": str(self.state_dir / "profile"),
        }
        with mock.patch.dict(
            os.environ,
            common | {"CODEX_HOME": str(self.state_dir / "codex-a")},
            clear=True,
        ):
            first = task_creation_lease._default_state_dir()
        with mock.patch.dict(
            os.environ,
            common | {"CODEX_HOME": str(self.state_dir / "codex-b")},
            clear=True,
        ):
            second = task_creation_lease._default_state_dir()
        self.assertEqual(first, second)

    def test_malformed_state_fails_closed(self) -> None:
        store = self.store()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        store.active_path.write_text("{broken", encoding="utf-8")
        self.assert_lease_error("LEASE_STATE_UNREADABLE", store.inspect)

    def test_cli_round_trip_requires_caller_owned_token(self) -> None:
        script = SCRIPT_DIR / "task_creation_lease.py"
        state_arguments = ["--state-dir", str(self.state_dir / "cli")]
        reserve = subprocess.run(
            [
                sys.executable,
                str(script),
                *state_arguments,
                "reserve",
                "--repository",
                "owner/repo",
                "--issue",
                "50",
                "--branch",
                "codex/issue-50-example",
                "--owner-token",
                "owner-cli",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(reserve.stdout)
        self.assertTrue(payload["lease"]["creation_authorized"])
        self.assertNotIn("owner_token", payload["lease"])
        release = subprocess.run(
            [
                sys.executable,
                str(script),
                *state_arguments,
                "release",
                "--owner-token",
                "owner-cli",
                "--outcome",
                "cancelled-before-invoke",
                "--worktree-state",
                "absent",
                "--evidence",
                "no-native-call-made",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            "cancelled-before-invoke",
            json.loads(release.stdout)["lease"]["outcome"],
        )


if __name__ == "__main__":
    unittest.main()
