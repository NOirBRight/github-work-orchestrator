from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CONTRACT = load_module(
    "validate_execution_contract",
    ROOT
    / "skills"
    / "github-work-orchestrator"
    / "scripts"
    / "validate_execution_contract.py",
)
PREFLIGHT = load_module(
    "worker_preflight",
    ROOT / "skills" / "github-issue-worker" / "scripts" / "preflight.py",
)
WORKER_SIGNAL = load_module(
    "worker_signal",
    ROOT / "skills" / "github-issue-worker" / "scripts" / "worker_signal.py",
)
INTAKE_SIGNAL = load_module(
    "intake_signal",
    ROOT / "skills" / "github-issue-intake" / "scripts" / "intake_signal.py",
)


def contract(**overrides):
    value = {
        "execution_contract": "v2",
        "issue": "https://example.test/issues/7",
        "repository": "owner/repo",
        "verification_class": "standard",
        "verification_commands": ["python -m pytest tests/unit -q"],
        "manual_evidence": "none",
        "architecture_decision": "resolved",
        "review_owner": "orchestrator",
        "base_branch": "dev",
        "base_sha": "a" * 40,
        "feature_branch": "codex/issue-7-example",
        "hotset": ["src/example.py"],
        "model_profile": "standard",
        "model_binding": "gpt-5.6-luna / high",
        "model_binding_status": "verified",
        "model_binding_evidence": "native-runtime-readback:example",
        "permission_profile": {
            "filesystem": "unrestricted",
            "network": "enabled",
            "approval": "never",
        },
        "callback_task": "private-callback",
        "pr_target": "dev",
    }
    value.update(overrides)
    return value


class ExecutionContractTests(unittest.TestCase):
    def test_valid_v2_contract_is_dispatchable(self) -> None:
        report = CONTRACT.validate_contract(contract())
        self.assertEqual("valid", report["status"])
        self.assertTrue(report["dispatchable"])
        self.assertTrue(report["verification_plan"]["local_full_suite"])
        self.assertEqual(0, report["verification_plan"]["worker_review_runs"])

    def test_open_architecture_decision_fails_closed(self) -> None:
        report = CONTRACT.validate_contract(
            contract(architecture_decision="discussion-required")
        )
        self.assertFalse(report["dispatchable"])
        self.assertIn("architecture-decision-open", report["errors"])

    def test_missing_review_owner_and_commands_fail_closed(self) -> None:
        report = CONTRACT.validate_contract(
            contract(review_owner="worker", verification_commands=[])
        )
        self.assertFalse(report["dispatchable"])
        self.assertIn("review-owner-must-be-orchestrator", report["errors"])
        self.assertIn(
            "verification-commands-must-be-nonempty-list", report["errors"]
        )

    def test_fast_candidate_skips_full_suite_and_review_agents(self) -> None:
        plan = CONTRACT.verification_plan("fast")
        self.assertFalse(plan["local_full_suite"])
        self.assertEqual("direct", plan["orchestrator_review"])
        self.assertEqual(0, plan["worker_review_runs"])

    def test_standard_and_strict_have_one_formal_review(self) -> None:
        for verification_class in ("standard", "strict"):
            with self.subTest(verification_class=verification_class):
                plan = CONTRACT.verification_plan(verification_class)
                self.assertTrue(plan["local_full_suite"])
                self.assertEqual("standards-spec", plan["orchestrator_review"])
                self.assertEqual(1, plan["formal_review_round_limit"])

    def test_candidate_pipeline_is_local_first_single_ci_and_parallel(self) -> None:
        plan = CONTRACT.verification_plan(
            "strict", manual_evidence="one packaged behavior check"
        )
        self.assertTrue(plan["local_green_before_ci"])
        self.assertEqual(
            "one-per-locally-green-candidate", plan["ci_run_mode"]
        )
        self.assertEqual("parallel", plan["integration_gates"])
        self.assertEqual("pre-merge", plan["manual_evidence_timing"])
        self.assertEqual(
            "tree-delta-or-repository-requirement-only",
            plan["post_merge_rebuild"],
        )

    def test_same_boundary_delta_preserves_suite_and_parallel_pipeline(self) -> None:
        plan = CONTRACT.verification_plan("strict", phase="review-fix")
        self.assertFalse(plan["local_full_suite"])
        self.assertEqual("delta-only", plan["orchestrator_review"])
        self.assertTrue(plan["local_green_before_ci"])
        self.assertEqual("parallel", plan["integration_gates"])
        self.assertEqual("pre-merge-if-affected", plan["manual_evidence_timing"])
        self.assertEqual(15, plan["candidate_target_minutes"])

    def test_manual_evidence_comes_only_from_contract_field(self) -> None:
        strict_none = CONTRACT.validate_contract(
            contract(verification_class="strict", manual_evidence="none")
        )
        standard_explicit = CONTRACT.validate_contract(
            contract(manual_evidence="one isolated Desktop replay")
        )
        self.assertFalse(strict_none["verification_plan"]["manual_evidence"])
        self.assertTrue(
            standard_explicit["verification_plan"]["manual_evidence"]
        )

    def test_unverified_model_binding_fails_closed(self) -> None:
        report = CONTRACT.validate_contract(
            contract(model_binding_status="unverified")
        )
        self.assertFalse(report["dispatchable"])
        self.assertIn("model-binding-must-be-verified", report["errors"])

    def test_missing_model_binding_evidence_fails_closed(self) -> None:
        report = CONTRACT.validate_contract(contract(model_binding_evidence=""))
        self.assertFalse(report["dispatchable"])
        self.assertIn("missing-or-empty:model_binding_evidence", report["errors"])

    def test_review_fix_is_delta_only_unless_boundary_changes(self) -> None:
        ordinary = CONTRACT.verification_plan("strict", phase="review-fix")
        boundary = CONTRACT.verification_plan(
            "strict", phase="review-fix", boundary_changed=True
        )
        self.assertFalse(ordinary["local_full_suite"])
        self.assertTrue(boundary["local_full_suite"])
        self.assertEqual("delta-only", ordinary["orchestrator_review"])


class WorkerPreflightTests(unittest.TestCase):
    def test_full_access_clean_exact_base_passes(self) -> None:
        report = PREFLIGHT.evaluate_preflight(
            expected_base="b" * 40,
            expected_branch="codex/issue-7-example",
            filesystem="danger-full-access",
            network="enabled",
            approval="never",
            observed={
                "head": "b" * 40,
                "integration_head": "b" * 40,
                "branch": "codex/issue-7-example",
                "status": "",
                "github_login": "worker",
                "github_repository": "owner/repo",
            },
            require_github=True,
        )
        self.assertEqual("passed", report["status"])
        self.assertEqual([], report["failures"])

    def test_narrow_permission_or_wrong_base_fails_closed(self) -> None:
        report = PREFLIGHT.evaluate_preflight(
            expected_base="c" * 40,
            expected_branch=None,
            filesystem="workspace-write",
            network="restricted",
            approval="managed",
            observed={
                "head": "d" * 40,
                "integration_head": "c" * 40,
                "branch": "",
                "status": "",
            },
            require_github=False,
        )
        self.assertEqual("failed", report["status"])
        self.assertIn("filesystem_unrestricted", report["failures"])
        self.assertIn("network_enabled", report["failures"])
        self.assertIn("approval_never", report["failures"])
        self.assertIn("head_matches_base", report["failures"])

    def test_command_timeout_is_bounded_and_classified(self) -> None:
        with patch.object(
            PREFLIGHT.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["gh", "api", "user"], 0.1),
        ):
            with self.assertRaises(PREFLIGHT.CommandError) as raised:
                PREFLIGHT.run_command(
                    ROOT,
                    "github-identity",
                    ["gh", "api", "user"],
                    timeout_seconds=0.1,
                )
        self.assertEqual(124, raised.exception.returncode)
        self.assertEqual("timed-out", raised.exception.reason)


class SignalFormatterTests(unittest.TestCase):
    def worker_payload(self):
        return {
            "state": "READY_FOR_REVIEW",
            "issue": "#7",
            "branch": "codex/issue-7-example",
            "commit": "a" * 40,
            "pr": "https://example.test/pull/8",
            "verification_class": "standard",
            "verification": "passed",
            "phase_timings": {
                "plan": "2m",
                "implementation": "12m",
                "verification": "4m",
                "waiting": "1m",
            },
            "full_suite_runs": 1,
            "review_runs": 0,
            "scope_delta": "none",
            "hotset": ["src/example.py"],
            "blocker_next_action": "Orchestrator review",
        }

    def test_worker_signal_is_stable_and_rejects_worker_review(self) -> None:
        payload = self.worker_payload()
        first = WORKER_SIGNAL.render_signal(payload)
        self.assertEqual(first, WORKER_SIGNAL.render_signal(payload))
        self.assertIn("Review-Runs: 0", first)
        payload["review_runs"] = 1
        self.assertIn(
            "worker-review-runs-must-be-zero",
            WORKER_SIGNAL.validate_signal(payload),
        )

    def test_intake_signal_is_stable(self) -> None:
        payload = {
            "state": "ISSUE_READY",
            "issue_topic": "#9",
            "repository": "owner/repo",
            "evidence": "readback passed",
            "next_action": "reconcile frontier",
        }
        first = INTAKE_SIGNAL.render_signal(payload)
        self.assertEqual(first, INTAKE_SIGNAL.render_signal(payload))
        self.assertIn("INTAKE_SIGNAL", first)


if __name__ == "__main__":
    unittest.main()
