from __future__ import annotations

import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "github-work-orchestrator" / "scripts"
GWO_PY = SCRIPT_DIR / "gwo.py"

# Imported via importlib in the fixture so tests can reload after source changes.

def load_store():
    import importlib.util
    sys.path.insert(0, str(SCRIPT_DIR))
    spec = importlib.util.spec_from_file_location("gwo_store", SCRIPT_DIR / "gwo_store.py")
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load gwo_store")
    module = importlib.util.module_from_spec(spec)
    sys.modules["gwo_store"] = module
    spec.loader.exec_module(module)
    return module


class CliFixture:
    """Run gwo.py against an isolated temporary GWO_HOME."""

    def __init__(self, repo: str = "owner/repo", *, claim: bool = False) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.repo = repo
        self._saved_env = {
            "GWO_HOME": os.environ.get("GWO_HOME"),
            "GWO_REPOSITORY": os.environ.get("GWO_REPOSITORY"),
            "GWO_AGENT_ID": os.environ.get("GWO_AGENT_ID"),
            "PYTHONPATH": os.environ.get("PYTHONPATH"),
        }
        os.environ["GWO_HOME"] = str(self.home)
        os.environ["GWO_REPOSITORY"] = repo
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

    def run(self, *args: str):
        import subprocess
        return subprocess.run(
            [sys.executable, str(GWO_PY), "--repository", self.repo, *args],
            capture_output=True,
            text=True,
            check=False,
        )


class StoreFixture:
    """Open a store against an isolated temporary GWO_HOME."""

    def __init__(self, test_case, *, repo: str = "owner/repo", claim: bool = True):
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
        self.repo = repo
        self.store = self.store_mod.Store.connect(self.home, repo, caller_agent_id="coordinator-001")
        if claim:
            self.store.claim_coordinator()

    def cleanup(self) -> None:
        self.store.close()
        self.tmp.cleanup()
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class ReviewRoundCliTests(unittest.TestCase):
    """Red tests for gwo_review CLI review-round issue."""

    def setUp(self) -> None:
        self.fixture = CliFixture(claim=True)

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def _seed_task_and_dispatch(self) -> tuple[str, str]:
        result = self.fixture.run(
            "task", "create", "--issue", "24", "--group", "g-24", "--risk", "strict"
        )
        self.assertEqual(0, result.returncode, result.stderr)
        task_id = result.stdout.strip()
        import json
        task_id = json.loads(task_id)["task_id"]
        self.fixture.run("task", "update", task_id, "--status", "ready")
        dispatch = self.fixture.run(
            "dispatch", "create",
            "--task-id", task_id,
            "--agent-id", "worker-24",
            "--worktree", "/tmp/wt-24",
            "--branch", "work/issue-24",
        )
        self.assertEqual(0, dispatch.returncode, dispatch.stderr)
        dispatch_id = json.loads(dispatch.stdout)["dispatch_id"]
        # Register the reviewers that strict tier requires.
        for reviewer in ("reviewer-spec", "reviewer-quality"):
            reg = self.fixture.run(
                "agent", "register",
                "--agent-id", reviewer,
                "--adapter", "paseo",
                "--role", "reviewer",
                "--group-label", "g-24",
            )
            self.assertEqual(0, reg.returncode, reg.stderr)
        return task_id, dispatch_id

    def test_cli_has_review_subcommand(self) -> None:
        help_result = self.fixture.run("--help")
        self.assertIn("review", help_result.stdout)

    def _strict_assignments(self) -> dict[str, str]:
        import json
        return json.dumps({"spec": "reviewer-spec", "quality": "reviewer-quality"})

    def test_review_round_create_issues_identity(self) -> None:
        task_id, dispatch_id = self._seed_task_and_dispatch()
        result = self.fixture.run(
            "review", "round-create",
            "--dispatch-id", dispatch_id,
            "--round", "1",
            "--candidate-sha", "a" * 40,
            "--base-sha", "b" * 40,
            "--diff-digest", "c" * 64,
            "--acceptance-digest", "d" * 64,
            "--scope", "full",
            "--assignments", self._strict_assignments(),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        import json
        payload = json.loads(result.stdout)
        self.assertEqual(dispatch_id, payload["dispatch_id"])
        self.assertEqual(1, payload["round"])
        self.assertEqual("a" * 40, payload["candidate_sha"])
        self.assertEqual("coordinator-001", payload["issued_by"])
        self.assertRegex(payload["round_id"], r"^rr-[0-9a-f]+")

    def test_review_round_create_rejects_supplied_identity(self) -> None:
        task_id, dispatch_id = self._seed_task_and_dispatch()
        result = self.fixture.run(
            "review", "round-create",
            "--dispatch-id", dispatch_id,
            "--round", "1",
            "--candidate-sha", "a" * 40,
            "--base-sha", "b" * 40,
            "--diff-digest", "c" * 64,
            "--acceptance-digest", "d" * 64,
            "--scope", "full",
            "--assignments", self._strict_assignments(),
            "--issued-by", "attacker",
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("cannot be caller-supplied", result.stderr.lower())

    def test_review_round_requires_coordinator_claim(self) -> None:
        task_id, dispatch_id = self._seed_task_and_dispatch()
        self.fixture.run("coordinator", "release")
        result = self.fixture.run(
            "review", "round-create",
            "--dispatch-id", dispatch_id,
            "--round", "1",
            "--candidate-sha", "a" * 40,
            "--base-sha", "b" * 40,
            "--diff-digest", "c" * 64,
            "--acceptance-digest", "d" * 64,
            "--scope", "full",
            "--assignments", self._strict_assignments(),
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("coordinator", result.stderr.lower())

    def test_review_round_rejects_forger_supplied_round_id(self) -> None:
        task_id, dispatch_id = self._seed_task_and_dispatch()
        result = self.fixture.run(
            "review", "round-create",
            "--dispatch-id", dispatch_id,
            "--round", "1",
            "--candidate-sha", "a" * 40,
            "--base-sha", "b" * 40,
            "--diff-digest", "c" * 64,
            "--acceptance-digest", "d" * 64,
            "--scope", "full",
            "--assignments", self._strict_assignments(),
            "--round-id", "rr-forged",
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("cannot be caller-supplied", result.stderr.lower())

    def test_review_result_records_reference_only(self) -> None:
        task_id, dispatch_id = self._seed_task_and_dispatch()
        create = self.fixture.run(
            "review", "round-create",
            "--dispatch-id", dispatch_id,
            "--round", "1",
            "--candidate-sha", "a" * 40,
            "--base-sha", "b" * 40,
            "--diff-digest", "c" * 64,
            "--acceptance-digest", "d" * 64,
            "--scope", "full",
            "--assignments", self._strict_assignments(),
        )
        import json
        round_id = json.loads(create.stdout)["round_id"]
        os.environ["GWO_AGENT_ID"] = "reviewer-spec"
        result = self.fixture.run(
            "review", "result-create",
            "--round-id", round_id,
            "--axis", "spec",
            "--verdict", "approved",
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(round_id, payload["round_id"])
        self.assertEqual("reviewer-spec", payload["agent_id"])

    def test_review_result_rejects_forging_lock_fields(self) -> None:
        task_id, dispatch_id = self._seed_task_and_dispatch()
        create = self.fixture.run(
            "review", "round-create",
            "--dispatch-id", dispatch_id,
            "--round", "1",
            "--candidate-sha", "a" * 40,
            "--base-sha", "b" * 40,
            "--diff-digest", "c" * 64,
            "--acceptance-digest", "d" * 64,
            "--scope", "full",
            "--assignments", self._strict_assignments(),
        )
        import json
        round_id = json.loads(create.stdout)["round_id"]
        os.environ["GWO_AGENT_ID"] = "reviewer-spec"
        result = self.fixture.run(
            "review", "result-create",
            "--round-id", round_id,
            "--axis", "spec",
            "--verdict", "approved",
            "--candidate-sha", "z" * 40,
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertNotEqual(0, result.returncode)
        self.assertIn("candidate_sha", result.stderr.lower())

    def test_delta_round_requires_prior_round_id(self) -> None:
        task_id, dispatch_id = self._seed_task_and_dispatch()
        result = self.fixture.run(
            "review", "round-create",
            "--dispatch-id", dispatch_id,
            "--round", "2",
            "--candidate-sha", "a" * 40,
            "--base-sha", "b" * 40,
            "--diff-digest", "c" * 64,
            "--acceptance-digest", "d" * 64,
            "--scope", "delta",
            "--assignments", self._strict_assignments(),
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("prior_round_id", result.stderr.lower())

    def test_delta_round_requires_same_dispatch_prior(self) -> None:
        task_id, dispatch_id = self._seed_task_and_dispatch()
        import json
        first = self.fixture.run(
            "review", "round-create",
            "--dispatch-id", dispatch_id,
            "--round", "1",
            "--candidate-sha", "a" * 40,
            "--base-sha", "b" * 40,
            "--diff-digest", "c" * 64,
            "--acceptance-digest", "d" * 64,
            "--scope", "full",
            "--assignments", self._strict_assignments(),
        )
        prior = json.loads(first.stdout)["round_id"]
        task2 = self.fixture.run(
            "task", "create", "--issue", "25", "--group", "g-25", "--risk", "strict"
        )
        t2 = json.loads(task2.stdout)["task_id"]
        self.fixture.run("task", "update", t2, "--status", "ready")
        d2 = self.fixture.run(
            "dispatch", "create",
            "--task-id", t2,
            "--agent-id", "worker-25",
            "--worktree", "/tmp/wt-25",
            "--branch", "work/issue-25",
        )
        # Register reviewers for the second strict dispatch.
        for reviewer in ("reviewer-25-spec", "reviewer-25-quality"):
            reg = self.fixture.run(
                "agent", "register",
                "--agent-id", reviewer,
                "--adapter", "paseo",
                "--role", "reviewer",
                "--group-label", "g-25",
            )
            self.assertEqual(0, reg.returncode, reg.stderr)
        d2_id = json.loads(d2.stdout)["dispatch_id"]
        result = self.fixture.run(
            "review", "round-create",
            "--dispatch-id", d2_id,
            "--round", "2",
            "--candidate-sha", "e" * 40,
            "--base-sha", "f" * 40,
            "--diff-digest", "9" * 64,
            "--acceptance-digest", "0" * 64,
            "--scope", "delta",
            "--prior-round-id", prior,
            "--assignments", json.dumps({"spec": "reviewer-25-spec", "quality": "reviewer-25-quality"}),
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("same dispatch", result.stderr.lower())


class LeaseCliTests(unittest.TestCase):
    """Red tests for gwo_lease CLI integration lease."""

    def setUp(self) -> None:
        self.fixture = CliFixture(claim=True)

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_cli_has_lease_subcommand(self) -> None:
        help_result = self.fixture.run("--help")
        self.assertIn("lease", help_result.stdout)

    def test_lease_acquire_serializes_integration(self) -> None:
        result = self.fixture.run(
            "lease", "acquire",
            "--scope", "repo:owner/repo:integration",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        import json
        payload = json.loads(result.stdout)
        self.assertEqual("repo:owner/repo:integration", payload["scope"])
        self.assertEqual("coordinator-001", payload["holder_agent"])

    def test_lease_second_acquire_is_rejected(self) -> None:
        self.fixture.run(
            "lease", "acquire",
            "--scope", "repo:owner/repo:integration",
        )
        # Second acquire by the same coordinator is rejected because the lease
        # is already held.
        result = self.fixture.run(
            "lease", "acquire",
            "--scope", "repo:owner/repo:integration",
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("coordinator-001", result.stderr)

    def test_lease_release_by_non_coordinator_rejected(self) -> None:
        self.fixture.run(
            "lease", "acquire",
            "--scope", "repo:owner/repo:integration",
        )
        os.environ["GWO_AGENT_ID"] = "integrator-002"
        result = self.fixture.run(
            "lease", "release",
            "--scope", "repo:owner/repo:integration",
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertNotEqual(0, result.returncode)
        self.assertIn("coordinator", result.stderr.lower())

    def test_lease_release_then_reacquire(self) -> None:
        self.fixture.run(
            "lease", "acquire",
            "--scope", "repo:owner/repo:integration",
        )
        release = self.fixture.run(
            "lease", "release",
            "--scope", "repo:owner/repo:integration",
        )
        self.assertEqual(0, release.returncode, release.stderr)
        result = self.fixture.run(
            "lease", "acquire",
            "--scope", "repo:owner/repo:integration",
        )
        self.assertEqual(0, result.returncode, result.stderr)


class ReviewRoundStoreTests(unittest.TestCase):
    """Direct store-level red tests for review-round primitives."""

    def setUp(self) -> None:
        os.environ["PYTHONPATH"] = str(SCRIPT_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")
        self.fixture = StoreFixture(self)
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def _seed_dispatch(self, risk: str = "strict"):
        task = self.store.create_task(issue=24, group_label="g-24", risk=risk)
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-24",
            worktree="/tmp/wt-24",
            branch="work/issue-24",
        )
        return task, dispatch

    def _register_reviewer(self, agent_id: str):
        self.store.register_agent(
            agent_id=agent_id,
            adapter="paseo",
            runtime_ref=f"ref-{agent_id}",
            role="reviewer",
            group_label="g-24",
        )

    def test_store_has_issue_review_round(self) -> None:
        self.assertTrue(hasattr(self.store, "issue_review_round"))

    def test_issue_review_round_creates_identity(self) -> None:
        task, dispatch = self._seed_dispatch("standard")
        self._register_reviewer("reviewer-combined")
        row = self.store.issue_review_round(
            dispatch_id=dispatch["dispatch_id"],
            round=1,
            candidate_sha="a" * 40,
            base_sha="b" * 40,
            diff_digest="c" * 64,
            acceptance_digest="d" * 64,
            scope="full",
            assignments={"combined": "reviewer-combined"},
        )
        self.assertEqual(dispatch["dispatch_id"], row["dispatch_id"])
        self.assertEqual("coordinator-001", row["issued_by"])

    def test_store_has_submit_review_result(self) -> None:
        self.assertTrue(hasattr(self.store, "submit_review_result"))

    def test_submit_review_result_rejects_forger_supplied_candidate(self) -> None:
        task, dispatch = self._seed_dispatch("standard")
        self._register_reviewer("reviewer-combined")
        row = self.store.issue_review_round(
            dispatch_id=dispatch["dispatch_id"],
            round=1,
            candidate_sha="a" * 40,
            base_sha="b" * 40,
            diff_digest="c" * 64,
            acceptance_digest="d" * 64,
            scope="full",
            assignments={"combined": "reviewer-combined"},
        )
        os.environ["GWO_AGENT_ID"] = "reviewer-combined"
        with self.assertRaises(self.store_mod.IdentityError) as ctx:
            self.store.submit_review_result(
                round_id=row["round_id"],
                axis="combined",
                verdict="approved",
                candidate_sha="z" * 40,
            )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertIn("candidate_sha", str(ctx.exception).lower())

    def test_store_has_acquire_integration_lease(self) -> None:
        self.assertTrue(hasattr(self.store, "acquire_integration_lease"))

    def test_store_has_release_integration_lease(self) -> None:
        self.assertTrue(hasattr(self.store, "release_integration_lease"))

    def test_acquired_lease_serializes(self) -> None:
        lease = self.store.acquire_integration_lease(scope="repo:owner/repo:integration")
        self.assertEqual("coordinator-001", lease["holder_agent"])
        holder = self.store.integration_lease_holder("repo:owner/repo:integration")
        self.assertEqual("coordinator-001", holder)


class ReviewAuthorityTests(unittest.TestCase):
    """Issue #37: only the assigned registered non-archived Reviewer may submit
    a result for the assigned round and axis.
    """

    def setUp(self) -> None:
        os.environ["PYTHONPATH"] = str(SCRIPT_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")
        self.fixture = StoreFixture(self)
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def _seed_strict_dispatch(self):
        task = self.store.create_task(issue=37, group_label="g-37", risk="strict")
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-37",
            worktree="/tmp/wt-37",
            branch="work/issue-37",
        )
        return task, dispatch

    def _register_reviewer(self, agent_id: str, axis: str):
        self.store.register_agent(
            agent_id=agent_id,
            adapter="paseo",
            runtime_ref=f"ref-{agent_id}",
            role="reviewer",
            group_label="g-37",
        )
        # assignment is part of round creation in V7
        return agent_id, axis

    def test_unregistered_reviewer_cannot_submit_result(self) -> None:
        task, dispatch = self._seed_strict_dispatch()
        self._register_reviewer("reviewer-spec", "spec")
        self._register_reviewer("reviewer-quality", "quality")
        rr = self.store.issue_review_round(
            dispatch_id=dispatch["dispatch_id"],
            round=1,
            candidate_sha="a" * 40,
            base_sha="b" * 40,
            diff_digest="c" * 64,
            acceptance_digest="d" * 64,
            scope="full",
            assignments={"spec": "reviewer-spec", "quality": "reviewer-quality"},
        )
        os.environ["GWO_AGENT_ID"] = "reviewer-unregistered"
        with self.assertRaises(self.store_mod.IdentityError) as ctx:
            self.store.submit_review_result(
                round_id=rr["round_id"], axis="spec", verdict="approved"
            )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertIn("registered", str(ctx.exception).lower())

    def test_archived_reviewer_cannot_submit_result(self) -> None:
        task, dispatch = self._seed_strict_dispatch()
        self._register_reviewer("reviewer-archived", "spec")
        self._register_reviewer("reviewer-quality", "quality")
        rr = self.store.issue_review_round(
            dispatch_id=dispatch["dispatch_id"],
            round=1,
            candidate_sha="a" * 40,
            base_sha="b" * 40,
            diff_digest="c" * 64,
            acceptance_digest="d" * 64,
            scope="full",
            assignments={"spec": "reviewer-archived", "quality": "reviewer-quality"},
        )
        self.store.db.execute(
            "UPDATE agents SET archived_at = ? WHERE agent_id = ?",
            (time.time(), "reviewer-archived"),
        )
        os.environ["GWO_AGENT_ID"] = "reviewer-archived"
        with self.assertRaises(self.store_mod.IdentityError) as ctx:
            self.store.submit_review_result(
                round_id=rr["round_id"], axis="spec", verdict="approved"
            )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertIn("archived", str(ctx.exception).lower())

    def test_reviewer_without_axis_assignment_rejected(self) -> None:
        task, dispatch = self._seed_strict_dispatch()
        self._register_reviewer("reviewer-spec", "spec")
        self._register_reviewer("reviewer-quality", "quality")
        rr = self.store.issue_review_round(
            dispatch_id=dispatch["dispatch_id"],
            round=1,
            candidate_sha="a" * 40,
            base_sha="b" * 40,
            diff_digest="c" * 64,
            acceptance_digest="d" * 64,
            scope="full",
            assignments={"spec": "reviewer-spec", "quality": "reviewer-quality"},
        )
        self.store.register_agent(
            agent_id="reviewer-unassigned",
            adapter="paseo",
            runtime_ref="ref-unassigned",
            role="reviewer",
            group_label="g-37",
        )
        os.environ["GWO_AGENT_ID"] = "reviewer-unassigned"
        with self.assertRaises(self.store_mod.IdentityError) as ctx:
            self.store.submit_review_result(
                round_id=rr["round_id"], axis="spec", verdict="approved"
            )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertIn("assigned", str(ctx.exception).lower())


class ReviewRoundUniquenessTests(unittest.TestCase):
    """Issue #37: round numbers are unique per dispatch; delta lineage is a
    single superseding chain; stale/forked/same-candidate/superseded evidence is
    rejected.
    """

    def setUp(self) -> None:
        os.environ["PYTHONPATH"] = str(SCRIPT_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")
        self.fixture = StoreFixture(self)
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def _seed_dispatch(self, issue: int = 37):
        task = self.store.create_task(issue=issue, group_label=f"g-{issue}", risk="strict")
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id=f"worker-{issue}",
            worktree=f"/tmp/wt-{issue}",
            branch=f"work/issue-{issue}",
        )
        return task, dispatch

    def _strict_assignments(self, issue: int = 37):
        return {
            "spec": f"reviewer-{issue}-spec",
            "quality": f"reviewer-{issue}-quality",
        }

    def _register_strict_reviewers(self, issue: int = 37):
        for axis, agent_id in self._strict_assignments(issue).items():
            self.store.register_agent(
                agent_id=agent_id,
                adapter="paseo",
                runtime_ref=f"ref-{agent_id}",
                role="reviewer",
                group_label=f"g-{issue}",
            )

    def test_duplicate_round_one_rejected(self) -> None:
        task, dispatch = self._seed_dispatch()
        self._register_strict_reviewers()
        self.store.issue_review_round(
            dispatch_id=dispatch["dispatch_id"],
            round=1,
            candidate_sha="a" * 40,
            base_sha="b" * 40,
            diff_digest="c" * 64,
            acceptance_digest="d" * 64,
            scope="full",
            assignments=self._strict_assignments(),
        )
        with self.assertRaises(self.store_mod.TransitionError) as ctx:
            self.store.issue_review_round(
                dispatch_id=dispatch["dispatch_id"],
                round=1,
                candidate_sha="e" * 40,
                base_sha="f" * 40,
                diff_digest="1" * 64,
                acceptance_digest="2" * 64,
                scope="full",
                assignments=self._strict_assignments(),
            )
        self.assertIn("round", str(ctx.exception).lower())

    def test_delta_round_requires_prior_plus_one(self) -> None:
        task, dispatch = self._seed_dispatch()
        self._register_strict_reviewers()
        first = self.store.issue_review_round(
            dispatch_id=dispatch["dispatch_id"],
            round=1,
            candidate_sha="a" * 40,
            base_sha="b" * 40,
            diff_digest="c" * 64,
            acceptance_digest="d" * 64,
            scope="full",
            assignments=self._strict_assignments(),
        )
        with self.assertRaises(self.store_mod.TransitionError) as ctx:
            self.store.issue_review_round(
                dispatch_id=dispatch["dispatch_id"],
                round=3,
                candidate_sha="e" * 40,
                base_sha="f" * 40,
                diff_digest="1" * 64,
                acceptance_digest="2" * 64,
                scope="delta",
                prior_round_id=first["round_id"],
                assignments=self._strict_assignments(),
            )
        self.assertIn("round", str(ctx.exception).lower())

    def test_delta_round_requires_different_candidate(self) -> None:
        task, dispatch = self._seed_dispatch()
        self._register_strict_reviewers()
        first = self.store.issue_review_round(
            dispatch_id=dispatch["dispatch_id"],
            round=1,
            candidate_sha="a" * 40,
            base_sha="b" * 40,
            diff_digest="c" * 64,
            acceptance_digest="d" * 64,
            scope="full",
            assignments=self._strict_assignments(),
        )
        with self.assertRaises(self.store_mod.TransitionError) as ctx:
            self.store.issue_review_round(
                dispatch_id=dispatch["dispatch_id"],
                round=2,
                candidate_sha="a" * 40,
                base_sha="f" * 40,
                diff_digest="1" * 64,
                acceptance_digest="2" * 64,
                scope="delta",
                prior_round_id=first["round_id"],
                assignments=self._strict_assignments(),
            )
        self.assertIn("candidate", str(ctx.exception).lower())

    def test_stale_round_result_rejected(self) -> None:
        task, dispatch = self._seed_dispatch()
        self._register_strict_reviewers()
        first = self.store.issue_review_round(
            dispatch_id=dispatch["dispatch_id"],
            round=1,
            candidate_sha="a" * 40,
            base_sha="b" * 40,
            diff_digest="c" * 64,
            acceptance_digest="d" * 64,
            scope="full",
            assignments=self._strict_assignments(),
        )
        self.store.issue_review_round(
            dispatch_id=dispatch["dispatch_id"],
            round=2,
            candidate_sha="e" * 40,
            base_sha="f" * 40,
            diff_digest="1" * 64,
            acceptance_digest="2" * 64,
            scope="delta",
            prior_round_id=first["round_id"],
            assignments=self._strict_assignments(),
        )
        os.environ["GWO_AGENT_ID"] = "reviewer-37-spec"
        with self.assertRaises(self.store_mod.TransitionError) as ctx:
            self.store.submit_review_result(
                round_id=first["round_id"], axis="spec", verdict="approved"
            )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertIn("stale", str(ctx.exception).lower())


class ReviewGateTests(unittest.TestCase):
    """Issue #37: review gate accepts only the latest round at the current
    candidate and fails closed for incomplete or rejected evidence.
    """

    def setUp(self) -> None:
        os.environ["PYTHONPATH"] = str(SCRIPT_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")
        self.fixture = StoreFixture(self)
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def _seed_standard_dispatch(self):
        task = self.store.create_task(issue=37, group_label="g-37", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-37",
            worktree="/tmp/wt-37",
            branch="work/issue-37",
        )
        return task, dispatch

    def _seed_strict_dispatch(self):
        task = self.store.create_task(issue=37, group_label="g-37", risk="strict")
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-37",
            worktree="/tmp/wt-37",
            branch="work/issue-37",
        )
        return task, dispatch

    def _register_and_assign(self, agent_id: str, axis: str):
        self.store.register_agent(
            agent_id=agent_id,
            adapter="paseo",
            runtime_ref=f"ref-{agent_id}",
            role="reviewer",
            group_label="g-37",
        )
        return agent_id

    def test_gate_has_check_review(self) -> None:
        self.assertTrue(hasattr(self.store, "check_review_gate"))

    def test_standard_gate_approved_after_combined(self) -> None:
        task, dispatch = self._seed_standard_dispatch()
        self._register_and_assign("reviewer-c", "combined")
        rr = self.store.issue_review_round(
            dispatch_id=dispatch["dispatch_id"],
            round=1,
            candidate_sha="a" * 40,
            base_sha="b" * 40,
            diff_digest="c" * 64,
            acceptance_digest="d" * 64,
            scope="full",
            assignments={"combined": "reviewer-c"},
        )
        os.environ["GWO_AGENT_ID"] = "reviewer-c"
        self.store.submit_review_result(
            round_id=rr["round_id"], axis="combined", verdict="approved"
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        gate = self.store.check_review_gate(
            dispatch_id=dispatch["dispatch_id"], candidate_sha="a" * 40
        )
        self.assertTrue(gate["approved"])

    def test_standard_gate_rejected_fails_closed(self) -> None:
        task, dispatch = self._seed_standard_dispatch()
        self._register_and_assign("reviewer-c", "combined")
        rr = self.store.issue_review_round(
            dispatch_id=dispatch["dispatch_id"],
            round=1,
            candidate_sha="a" * 40,
            base_sha="b" * 40,
            diff_digest="c" * 64,
            acceptance_digest="d" * 64,
            scope="full",
            assignments={"combined": "reviewer-c"},
        )
        os.environ["GWO_AGENT_ID"] = "reviewer-c"
        self.store.submit_review_result(
            round_id=rr["round_id"], axis="combined", verdict="rejected"
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        gate = self.store.check_review_gate(
            dispatch_id=dispatch["dispatch_id"], candidate_sha="a" * 40
        )
        self.assertFalse(gate["approved"])

    def test_strict_gate_requires_two_distinct_agents(self) -> None:
        task, dispatch = self._seed_strict_dispatch()
        self._register_and_assign("reviewer-spec", "spec")
        self._register_and_assign("reviewer-quality", "quality")
        rr = self.store.issue_review_round(
            dispatch_id=dispatch["dispatch_id"],
            round=1,
            candidate_sha="a" * 40,
            base_sha="b" * 40,
            diff_digest="c" * 64,
            acceptance_digest="d" * 64,
            scope="full",
            assignments={"spec": "reviewer-spec", "quality": "reviewer-quality"},
        )
        os.environ["GWO_AGENT_ID"] = "reviewer-spec"
        self.store.submit_review_result(
            round_id=rr["round_id"], axis="spec", verdict="approved"
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        # Only one axis approved: gate must not open.
        gate = self.store.check_review_gate(
            dispatch_id=dispatch["dispatch_id"], candidate_sha="a" * 40
        )
        self.assertFalse(gate["approved"])

    def test_strict_gate_opens_with_two_distinct_agents(self) -> None:
        task, dispatch = self._seed_strict_dispatch()
        self._register_and_assign("reviewer-spec", "spec")
        self._register_and_assign("reviewer-quality", "quality")
        rr = self.store.issue_review_round(
            dispatch_id=dispatch["dispatch_id"],
            round=1,
            candidate_sha="a" * 40,
            base_sha="b" * 40,
            diff_digest="c" * 64,
            acceptance_digest="d" * 64,
            scope="full",
            assignments={"spec": "reviewer-spec", "quality": "reviewer-quality"},
        )
        os.environ["GWO_AGENT_ID"] = "reviewer-spec"
        self.store.submit_review_result(
            round_id=rr["round_id"], axis="spec", verdict="approved"
        )
        os.environ["GWO_AGENT_ID"] = "reviewer-quality"
        self.store.submit_review_result(
            round_id=rr["round_id"], axis="quality", verdict="approved"
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        gate = self.store.check_review_gate(
            dispatch_id=dispatch["dispatch_id"], candidate_sha="a" * 40
        )
        self.assertTrue(gate["approved"])

    def test_same_agent_dual_axes_strict_rejected(self) -> None:
        task, dispatch = self._seed_strict_dispatch()
        self._register_and_assign("reviewer-both", "spec")
        self._register_and_assign("reviewer-both", "quality")
        with self.assertRaises(self.store_mod.TransitionError) as ctx:
            self.store.issue_review_round(
                dispatch_id=dispatch["dispatch_id"],
                round=1,
                candidate_sha="a" * 40,
                base_sha="b" * 40,
                diff_digest="c" * 64,
                acceptance_digest="d" * 64,
                scope="full",
                assignments={"spec": "reviewer-both", "quality": "reviewer-both"},
            )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertIn("different reviewers", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
