from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
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
POLICY = load_module(
    "execution_policy",
    ROOT
    / "skills"
    / "github-work-orchestrator"
    / "scripts"
    / "execution_policy.py",
)
AGENT_INSTALLER = load_module(
    "install_worker_agent",
    ROOT
    / "skills"
    / "github-work-orchestrator"
    / "scripts"
    / "install_worker_agent.py",
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
        "execution_lane": "subagent",
        "model_profile": "standard",
        "model_binding": "ollama-cloud/glm-5.2",
        "model_reasoning_effort": "max",
        "model_binding_requirement": "best-effort",
        "model_binding_status": "request-accepted",
        "model_binding_evidence": "native-request-accepted:no-readback",
        "permission_profile": {
            "filesystem": "unrestricted",
            "network": "enabled",
            "approval": "never",
        },
        "callback_task": "019f0000-0000-7000-8000-000000000001",
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

    def test_rejected_model_binding_fails_closed(self) -> None:
        report = CONTRACT.validate_contract(
            contract(model_binding_status="rejected")
        )
        self.assertFalse(report["dispatchable"])
        self.assertIn("model-binding-rejected", report["errors"])

    def test_exact_runtime_requires_runtime_verified_binding(self) -> None:
        report = CONTRACT.validate_contract(
            contract(model_binding_requirement="exact-runtime")
        )
        self.assertFalse(report["dispatchable"])
        self.assertIn("exact-runtime-binding-not-verified", report["errors"])

    def test_implementation_worker_cannot_silently_fall_back_to_gpt(self) -> None:
        report = CONTRACT.validate_contract(
            contract(
                model_binding="gpt-5.6-terra",
                model_reasoning_effort="high",
            )
        )
        self.assertFalse(report["dispatchable"])
        self.assertIn("implementation-worker-must-use-glm-5.2", report["errors"])

    def test_glm_reasoning_must_be_explicit_max(self) -> None:
        omitted = contract()
        omitted.pop("model_reasoning_effort")
        explicit_none = contract(model_reasoning_effort="none")
        explicit_max = contract(model_reasoning_effort="max")
        omitted_report = CONTRACT.validate_contract(omitted)
        self.assertFalse(omitted_report["dispatchable"])
        self.assertIn("glm-reasoning-must-be-max", omitted_report["errors"])
        self.assertFalse(
            CONTRACT.validate_contract(explicit_none)["dispatchable"]
        )
        self.assertTrue(CONTRACT.validate_contract(explicit_max)["dispatchable"])

    def test_inline_lane_keeps_the_orchestrator_gpt_binding(self) -> None:
        report = CONTRACT.validate_contract(
            contract(
                execution_lane="inline",
                model_profile="orchestrator",
                model_binding="gpt-5.6-terra",
                model_reasoning_effort="high",
                model_binding_status="runtime-verified",
            )
        )
        self.assertTrue(report["dispatchable"], report["errors"])

    def test_lane_profile_and_visible_callback_must_match_policy(self) -> None:
        wrong_worker_profile = CONTRACT.validate_contract(
            contract(model_profile="orchestrator")
        )
        wrong_inline_profile = CONTRACT.validate_contract(
            contract(
                execution_lane="inline",
                model_profile="architecture",
                model_binding="gpt-5.6-sol",
                model_reasoning_effort="max",
                model_binding_status="runtime-verified",
            )
        )
        invalid_callback = CONTRACT.validate_contract(
            contract(execution_lane="visible-worker", callback_task="not-a-task-id")
        )
        self.assertIn(
            "invalid-implementation-worker-profile",
            wrong_worker_profile["errors"],
        )
        self.assertIn("inline-lane-must-use-orchestrator-profile", wrong_inline_profile["errors"])
        self.assertIn("inline-lane-must-keep-orchestrator-gpt", wrong_inline_profile["errors"])
        self.assertIn(
            "inline-lane-must-keep-orchestrator-reasoning",
            wrong_inline_profile["errors"],
        )
        self.assertIn("visible-worker-callback-task-invalid", invalid_callback["errors"])

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


class ExecutionLanePolicyTests(unittest.TestCase):
    def test_small_same_boundary_change_routes_inline(self) -> None:
        report = POLICY.classify_execution_lane(
            expected_minutes=15,
            same_boundary=True,
        )
        self.assertEqual("inline", report["lane"])

    def test_bounded_implementation_defaults_to_subagent(self) -> None:
        report = POLICY.classify_execution_lane(
            expected_minutes=45,
            same_boundary=False,
        )
        self.assertEqual("subagent", report["lane"])

    def test_persistent_or_human_work_routes_visible(self) -> None:
        cases = (
            {"restart_persistence": True},
            {"manual_ui_or_login": True},
            {"prolonged_observation": True},
            {"independent_visible_context": True},
        )
        for requirement in cases:
            with self.subTest(requirement=requirement):
                report = POLICY.classify_execution_lane(
                    expected_minutes=5,
                    same_boundary=True,
                    **requirement,
                )
                self.assertEqual("visible-worker", report["lane"])

    def test_capacity_enforces_one_three_four_and_host_slots(self) -> None:
        report = POLICY.capacity_report(
            visible_orchestrators_for_activity=1,
            visible_workers_global=3,
            active_subagents=2,
            host_subagent_slots=3,
        )
        self.assertFalse(report["can_add_orchestrator"])
        self.assertFalse(report["can_add_visible_worker"])
        self.assertTrue(report["can_add_subagent"])
        self.assertEqual(1, report["subagent_slots_remaining"])
        capped = POLICY.capacity_report(
            visible_orchestrators_for_activity=0,
            visible_workers_global=0,
            active_subagents=4,
            host_subagent_slots=20,
        )
        self.assertFalse(capped["can_add_subagent"])
        self.assertEqual(4, capped["effective_subagent_limit"])

    def test_cleanup_is_event_triggered_and_fail_closed(self) -> None:
        worktree = str((ROOT / "task-worktree").resolve())
        task_id = "019f0000-0000-7000-8000-000000000007"
        eligible = POLICY.cleanup_plan(
            event="merged",
            seconds_since_event=120,
            worktree=worktree,
            branch="codex/issue-7-example",
            visible_task_id=task_id,
            worktree_clean=True,
            durable=True,
            ownership_unambiguous=True,
            active_editor=False,
            branch_merged=True,
            visible_worker=True,
        )
        self.assertEqual("eligible", eligible["status"])
        self.assertEqual(300, eligible["deadline_seconds"])
        self.assertEqual(
            [
                {"action": "remove-worktree", "target": worktree},
                {
                    "action": "delete-merged-local-branch",
                    "target": "codex/issue-7-example",
                },
                {
                    "action": "request-human-visible-task-archive",
                    "target": task_id,
                },
            ],
            eligible["actions"],
        )
        self.assertEqual(
            eligible,
            POLICY.cleanup_plan(
                event="merged",
                seconds_since_event=120,
                worktree=worktree,
                branch="codex/issue-7-example",
                visible_task_id=task_id,
                worktree_clean=True,
                durable=True,
                ownership_unambiguous=True,
                active_editor=False,
                branch_merged=True,
                visible_worker=True,
            ),
        )
        self.assertNotIn(
            "archive-visible-task",
            [action["action"] for action in eligible["actions"]],
        )
        self.assertFalse(eligible["automatic_task_archive"])
        protected = POLICY.cleanup_plan(
            event="stopped",
            seconds_since_event=301,
            worktree=worktree,
            branch=None,
            visible_task_id=task_id,
            worktree_clean=False,
            durable=False,
            ownership_unambiguous=True,
            active_editor=False,
            branch_merged=False,
            visible_worker=True,
        )
        self.assertEqual("protected", protected["status"])
        self.assertIn("worktree-not-clean", protected["blockers"])
        self.assertIn("work-not-durable", protected["blockers"])
        with self.assertRaisesRegex(ValueError, "absolute path"):
            POLICY.cleanup_plan(
                event="merged",
                seconds_since_event=0,
                worktree="relative-worktree",
                branch=None,
                visible_task_id=None,
                worktree_clean=True,
                durable=True,
                ownership_unambiguous=True,
                active_editor=False,
                branch_merged=False,
                visible_worker=False,
            )
        with self.assertRaisesRegex(ValueError, "exact Task ID"):
            POLICY.cleanup_plan(
                event="merged",
                seconds_since_event=0,
                worktree=worktree,
                branch=None,
                visible_task_id="not-a-task-id",
                worktree_clean=True,
                durable=True,
                ownership_unambiguous=True,
                active_editor=False,
                branch_merged=False,
                visible_worker=True,
            )


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


class WorkerAgentInstallerTests(unittest.TestCase):
    def test_template_installs_idempotently_with_qualified_max(self) -> None:
        report = AGENT_INSTALLER.validate_agent(AGENT_INSTALLER.template_path())
        self.assertEqual("worker", report["name"])
        self.assertEqual("ollama-cloud/glm-5.2", report["model"])
        self.assertEqual("max", report["reasoning"])
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "agents"
            installed = AGENT_INSTALLER.install_agent(
                AGENT_INSTALLER.template_path(), target
            )
            repeated = AGENT_INSTALLER.install_agent(
                AGENT_INSTALLER.template_path(), target
            )
            self.assertEqual("installed", installed["status"])
            self.assertEqual("already-current", repeated["status"])

    def test_existing_different_worker_requires_explicit_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "agents"
            target.mkdir()
            (target / "worker.toml").write_text("name = 'different'\n", encoding="utf-8")
            with self.assertRaises(AGENT_INSTALLER.AgentConfigError):
                AGENT_INSTALLER.install_agent(
                    AGENT_INSTALLER.template_path(), target
                )


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

    def test_activation_signals_are_supported_with_exact_task_ids(self) -> None:
        payload = {
            "state": "WORKER_BOOTED",
            "issue": "#7",
            "task_id": "019f0000-0000-7000-8000-000000000007",
            "callback_task": "019f0000-0000-7000-8000-000000000001",
            "evidence": "native Task identity read back",
        }
        rendered = WORKER_SIGNAL.render_signal(payload)
        self.assertIn("State: WORKER_BOOTED", rendered)
        self.assertEqual([], WORKER_SIGNAL.validate_signal(payload))
        payload["state"] = "PREFLIGHT_READY"
        payload["evidence"] = "read-only preflight passed"
        self.assertIn("State: PREFLIGHT_READY", WORKER_SIGNAL.render_signal(payload))
        payload["callback_task"] = "not-a-task-id"
        self.assertIn("invalid-callback-task", WORKER_SIGNAL.validate_signal(payload))

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
