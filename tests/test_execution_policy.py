from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
