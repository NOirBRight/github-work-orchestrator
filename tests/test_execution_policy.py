from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_SCRIPTS = ROOT / "skills" / "github-work-orchestrator" / "scripts"
WORKER_SCRIPTS = ROOT / "skills" / "github-issue-worker" / "scripts"


def load_module(name: str, directory: Path = ORCHESTRATOR_SCRIPTS):
    path = directory / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def load_archive_policy():
    return load_module("archive_policy")


CONTRACT = load_module("validate_execution_contract")
POLICY = load_module("execution_policy")
PROVIDERS = load_module("provider_policy")
ROOM = load_module("paseo_room")
PREFLIGHT = load_module("preflight", WORKER_SCRIPTS)


def contract(**overrides):
    payload = {
        "execution_contract": "v3",
        "issue": "#7",
        "repository": "owner/repo",
        "base_branch": "dev",
        "base_sha": "a" * 40,
        "feature_branch": "work/issue-7-example",
        "pr_target": "dev",
        "campaign_id": "campaign-20260718",
        "dispatch_id": "dispatch-issue-7",
        "room": "gwo-campaign-20260718",
        "agent_role": "implementation",
        "role_category": "impl",
        "execution_mode": "paseo-agent",
        "relationship": "subagent",
        "parent_agent_id": "agent-orchestrator",
        "notify_on_finish": True,
        "runtime_mode_id": "provider-full-access-mode",
        "done_when": "PR is green and accepted",
        "verification_class": "standard",
        "verification_commands": ["python -m unittest discover -s tests -v"],
        "manual_evidence": "none",
        "hotset": ["skills/github-work-orchestrator"],
        "architecture_decision": "resolved",
        "review_owner": "orchestrator",
        "permission_profile": {
            "filesystem": "workspace-write",
            "network": "github",
            "approval": "never",
            "unexpected_request_fallback": "parent",
        },
    }
    payload.update(overrides)
    return payload


def event(**overrides):
    payload = {
        "schema_version": 1,
        "signal_id": "worker-7-ready-1",
        "campaign_id": "campaign-20260718",
        "dispatch_id": "dispatch-issue-7",
        "sequence": 1,
        "event_type": "AGENT_READY",
        "issue": "#7",
        "sender_agent_id": "agent-worker-7",
        "recipient_agent_id": "agent-orchestrator",
        "evidence": "room preflight passed",
        "next_action": "wait for START",
    }
    payload.update(overrides)
    return payload


_DEFAULT_CLEANUP_TARGET = object()


def cleanup_report(
    root: Path,
    *,
    event: str = "stopped",
    seconds_since_event: int = 0,
    execution_mode: str = "paseo-agent",
    actor_overrides: dict | None = None,
    target=_DEFAULT_CLEANUP_TARGET,
    target_overrides: dict | None = None,
    execution_overrides: dict | None = None,
    protected_control_worktree: str | None = None,
):
    actor = {
        "agent_id": "agent-orchestrator",
        "role": "orchestrator",
        "worktree": str(root / "control"),
    }
    actor.update(actor_overrides or {})
    if target is _DEFAULT_CLEANUP_TARGET:
        target = {
            "agent_id": "agent-worker-7",
            "parent_agent_id": "agent-orchestrator",
            "relationship": "subagent",
            "role": "implementation",
            "idle": True,
            "archived": False,
        }
    if target is not None:
        target = {**target, **(target_overrides or {})}
    execution = {
        "worktree": str(root / "issue-7"),
        "branch": "work/issue-7-example",
        "clean": True,
        "durable": True,
        "bound_agent_ids": ["agent-worker-7"],
        "branch_merged": False,
    }
    execution.update(execution_overrides or {})
    return POLICY.cleanup_plan(
        event=event,
        seconds_since_event=seconds_since_event,
        execution_mode=execution_mode,
        protected_control_worktree=(
            protected_control_worktree or str(root / "control")
        ),
        actor=actor,
        target=target,
        execution=execution,
    )


class ExecutionContractTests(unittest.TestCase):
    def test_valid_v3_contract_is_dispatchable(self) -> None:
        report = CONTRACT.validate_contract(contract())
        self.assertTrue(report["dispatchable"])
        self.assertEqual("paseo-agent", report["execution_mode"])
        self.assertEqual("impl", report["role_category"])

    def test_v2_and_provider_specific_fields_fail_closed(self) -> None:
        report = CONTRACT.validate_contract(
            contract(
                execution_contract="v2",
                model_binding="fixed/model",
                callback_task="task-id",
            )
        )
        self.assertFalse(report["dispatchable"])
        self.assertIn("execution-contract-must-be-v3", report["errors"])
        self.assertIn(
            "provider-specific-field-forbidden:model_binding", report["errors"]
        )
        self.assertIn(
            "provider-specific-field-forbidden:callback_task", report["errors"]
        )

    def test_role_room_and_dev_flow_are_enforced(self) -> None:
        report = CONTRACT.validate_contract(
            contract(
                role_category="audit",
                room="gwo-other-campaign",
                base_branch="main",
                pr_target="main",
                feature_branch="codex/issue-7-example",
            )
        )
        for error in (
            "role-category-mismatch",
            "room-must-match-campaign",
            "base-branch-must-be-dev",
            "pr-target-must-be-dev",
            "invalid-feature-branch",
        ):
            self.assertIn(error, report["errors"])

    def test_open_architecture_and_missing_permissions_fail_closed(self) -> None:
        report = CONTRACT.validate_contract(
            contract(
                architecture_decision="discussion-required",
                permission_profile={"filesystem": "workspace-write"},
            )
        )
        self.assertIn("architecture-decision-open", report["errors"])
        self.assertIn("missing-permission:network", report["errors"])
        self.assertIn("missing-permission:approval", report["errors"])
        self.assertIn("approval-must-be-never", report["errors"])
        self.assertIn("permission-fallback-must-be-parent", report["errors"])

    def test_paseo_parentage_and_notifications_are_required(self) -> None:
        report = CONTRACT.validate_contract(
            contract(relationship="detached", parent_agent_id="", notify_on_finish=False)
        )
        self.assertIn("relationship-must-be-subagent", report["errors"])
        self.assertIn("missing-or-empty:parent_agent_id", report["errors"])
        self.assertIn("notify-on-finish-must-be-true", report["errors"])

    def test_campaign_orchestrator_is_a_subagent_of_the_repository_coordinator(self) -> None:
        report = CONTRACT.validate_contract(
            contract(
                agent_role="orchestrator",
                role_category="planning",
                parent_agent_id="agent-repository-coordinator",
            )
        )

        self.assertTrue(report["dispatchable"])
        self.assertEqual("orchestrator", report["agent_role"])
        self.assertEqual("planning", report["role_category"])

    def test_candidate_and_review_fix_plans_preserve_single_review(self) -> None:
        candidate = CONTRACT.verification_plan("strict", manual_evidence="browser")
        review_fix = CONTRACT.verification_plan(
            "strict", phase="review-fix", boundary_changed=False
        )
        self.assertTrue(candidate["local_full_suite"])
        self.assertTrue(candidate["manual_evidence"])
        self.assertEqual(1, candidate["formal_review_round_limit"])
        self.assertFalse(review_fix["local_full_suite"])
        self.assertEqual("delta-only", review_fix["orchestrator_review"])


class ExecutionPolicyTests(unittest.TestCase):
    def test_small_same_boundary_work_is_inline(self) -> None:
        report = POLICY.classify_execution_mode(expected_minutes=10, same_boundary=True)
        self.assertEqual("inline", report["execution_mode"])
        self.assertFalse(report["room_required"])

    def test_delegated_work_uses_paseo_agent(self) -> None:
        report = POLICY.classify_execution_mode(expected_minutes=30, same_boundary=True)
        self.assertEqual("paseo-agent", report["execution_mode"])
        self.assertTrue(report["room_required"])

    def test_capacity_is_provider_neutral_and_bounded(self) -> None:
        full = POLICY.capacity_report(
            campaign_id="campaign-full",
            repository_coordinators=1,
            campaign_orchestrators_for_campaign=1,
            active_agents_for_campaign=4,
            active_agents_global=5,
            max_active_agents_global=6,
        )
        conflict = POLICY.capacity_report(
            campaign_id="campaign-conflict",
            repository_coordinators=2,
            campaign_orchestrators_for_campaign=0,
            active_agents_for_campaign=0,
            active_agents_global=2,
            max_active_agents_global=6,
        )

        self.assertFalse(full["can_add_campaign_agent"])
        self.assertEqual(0, full["campaign_slots_remaining"])
        self.assertTrue(conflict["repository_coordinator_conflict"])
        self.assertFalse(conflict["can_add_campaign_orchestrator"])

    def test_distinct_campaigns_can_each_admit_an_orchestrator(self) -> None:
        campaign_a = POLICY.capacity_report(
            campaign_id="campaign-a",
            repository_coordinators=1,
            campaign_orchestrators_for_campaign=0,
            active_agents_for_campaign=0,
            active_agents_global=1,
            max_active_agents_global=6,
        )
        campaign_b = POLICY.capacity_report(
            campaign_id="campaign-b",
            repository_coordinators=1,
            campaign_orchestrators_for_campaign=0,
            active_agents_for_campaign=0,
            active_agents_global=2,
            max_active_agents_global=6,
        )

        self.assertTrue(campaign_a["can_add_campaign_orchestrator"])
        self.assertTrue(campaign_b["can_add_campaign_orchestrator"])

    def test_duplicate_orchestrators_for_one_campaign_are_a_conflict(self) -> None:
        report = POLICY.capacity_report(
            campaign_id="campaign-a",
            repository_coordinators=1,
            campaign_orchestrators_for_campaign=2,
            active_agents_for_campaign=2,
            active_agents_global=3,
            max_active_agents_global=6,
        )

        self.assertTrue(report["campaign_orchestrator_conflict"])
        self.assertFalse(report["can_add_campaign_orchestrator"])
        self.assertFalse(report["can_add_campaign_agent"])

    def test_repository_coordinator_admission_respects_global_capacity(self) -> None:
        report = POLICY.capacity_report(
            campaign_id="campaign-a",
            repository_coordinators=0,
            campaign_orchestrators_for_campaign=0,
            active_agents_for_campaign=0,
            active_agents_global=6,
            max_active_agents_global=6,
        )

        self.assertFalse(report["can_add_repository_coordinator"])
        self.assertEqual(0, report["global_slots_remaining"])

    def test_capacity_rejects_contradictory_or_over_limit_counts(self) -> None:
        defaults = {
            "campaign_id": "campaign-a",
            "repository_coordinators": 1,
            "campaign_orchestrators_for_campaign": 1,
            "active_agents_for_campaign": 1,
            "active_agents_global": 2,
            "max_active_agents_global": 6,
        }
        cases = (
            ({"active_agents_for_campaign": 3, "active_agents_global": 2}, "campaign count"),
            ({"active_agents_for_campaign": 1, "active_agents_global": 1}, "combined count"),
            ({"campaign_orchestrators_for_campaign": 2, "active_agents_for_campaign": 1}, "orchestrator count"),
            ({"repository_coordinators": 3, "active_agents_global": 2}, "coordinator count"),
            ({"active_agents_global": 7}, "global limit"),
            ({"active_agents_for_campaign": 5}, "campaign limit"),
            (
                {
                    "campaign_orchestrators_for_campaign": 0,
                    "active_agents_for_campaign": 1,
                },
                "missing campaign orchestrator",
            ),
        )
        for overrides, label in cases:
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    POLICY.capacity_report(**{**defaults, **overrides})

    def test_disjoint_campaigns_execute_in_parallel_but_merge_serially(self) -> None:
        current_dev = "a" * 40
        campaign_a = POLICY.campaign_concurrency_report(
            campaign_id="campaign-a",
            requested_hotset=["src/campaign-a"],
            active_hotsets={"campaign-b": ["src/campaign-b"]},
            integration_lease_holder="campaign-a",
            pinned_dev_sha=current_dev,
            current_dev_sha=current_dev,
        )
        campaign_b = POLICY.campaign_concurrency_report(
            campaign_id="campaign-b",
            requested_hotset=["src/campaign-b"],
            active_hotsets={"campaign-a": ["src/campaign-a"]},
            integration_lease_holder="campaign-a",
            pinned_dev_sha=current_dev,
            current_dev_sha=current_dev,
        )

        self.assertTrue(campaign_a["can_execute"])
        self.assertTrue(campaign_b["can_execute"])
        self.assertTrue(campaign_a["can_merge_dev"])
        self.assertFalse(campaign_b["can_merge_dev"])
        self.assertIn("integration-lease-held-by-other", campaign_b["blockers"])

    def test_overlapping_hotsets_block_the_later_campaign(self) -> None:
        dev_sha = "a" * 40
        report = POLICY.campaign_concurrency_report(
            campaign_id="campaign-b",
            requested_hotset=["src/shared/api"],
            active_hotsets={"campaign-a": ["src/shared"]},
            integration_lease_holder=None,
            pinned_dev_sha=dev_sha,
            current_dev_sha=dev_sha,
        )

        self.assertFalse(report["can_execute"])
        self.assertFalse(report["can_merge_dev"])
        self.assertEqual(["campaign-a"], report["conflicting_campaigns"])
        self.assertIn("hotset-conflict", report["blockers"])

    def test_hotsets_reject_noncanonical_repository_paths(self) -> None:
        dev_sha = "a" * 40
        invalid_entries = (
            "src/other/../shared",
            "src/./shared",
            "src//shared",
            "/src/shared",
            "C:/repo/src/shared",
            "src/shared/",
        )
        for entry in invalid_entries:
            with self.subTest(entry=entry):
                with self.assertRaisesRegex(
                    ValueError, "canonical repository-relative path"
                ):
                    POLICY.campaign_concurrency_report(
                        campaign_id="campaign-b",
                        requested_hotset=[entry],
                        active_hotsets={"campaign-a": ["src/shared"]},
                        integration_lease_holder="campaign-b",
                        pinned_dev_sha=dev_sha,
                        current_dev_sha=dev_sha,
                    )

    def test_hotset_traversal_cannot_bypass_an_existing_claim(self) -> None:
        dev_sha = "a" * 40
        with self.assertRaisesRegex(
            ValueError, "canonical repository-relative path"
        ):
            POLICY.campaign_concurrency_report(
                campaign_id="campaign-b",
                requested_hotset=["src/shared"],
                active_hotsets={"campaign-a": ["src/other/../shared"]},
                integration_lease_holder="campaign-b",
                pinned_dev_sha=dev_sha,
                current_dev_sha=dev_sha,
            )

    def test_dev_advancing_requires_base_refresh_before_merge(self) -> None:
        report = POLICY.campaign_concurrency_report(
            campaign_id="campaign-b",
            requested_hotset=["src/campaign-b"],
            active_hotsets={},
            integration_lease_holder="campaign-b",
            pinned_dev_sha="a" * 40,
            current_dev_sha="b" * 40,
        )

        self.assertTrue(report["can_execute"])
        self.assertFalse(report["can_merge_dev"])
        self.assertTrue(report["requires_base_refresh"])
        self.assertIn("dev-advanced", report["blockers"])

    def test_campaign_must_hold_the_integration_lease_before_merge(self) -> None:
        dev_sha = "a" * 40
        report = POLICY.campaign_concurrency_report(
            campaign_id="campaign-b",
            requested_hotset=["src/campaign-b"],
            active_hotsets={},
            integration_lease_holder=None,
            pinned_dev_sha=dev_sha,
            current_dev_sha=dev_sha,
        )

        self.assertTrue(report["can_execute"])
        self.assertFalse(report["can_merge_dev"])
        self.assertFalse(report["integration_lease_held"])
        self.assertIn("integration-lease-not-held", report["blockers"])

    def test_cleanup_protects_the_root_coordinator_and_control_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                actor_overrides={"role": "repository-coordinator", "worktree": str(root)},
                target_overrides={
                    "agent_id": "agent-orchestrator",
                    "parent_agent_id": None,
                    "role": "orchestrator",
                },
                execution_overrides={
                    "worktree": str(root),
                    "branch": "dev",
                    "bound_agent_ids": ["agent-orchestrator"],
                },
                protected_control_worktree=str(root),
            )

        self.assertEqual("protected", report["status"])
        self.assertEqual([], report["actions"])
        for blocker in (
            "self-archive-forbidden",
            "root-archive-requires-supervisor",
            "control-worktree-protected",
            "integration-branch-protected",
        ):
            self.assertIn(blocker, report["blockers"])

    def test_cleanup_allows_an_owned_idle_delegated_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="merged",
                seconds_since_event=20,
                execution_overrides={"branch_merged": True},
            )

        self.assertEqual("eligible", report["status"])
        self.assertFalse(report["automatic_execution"])
        self.assertEqual(
            ["archive-paseo-agent"],
            [item["action"] for item in report["actions"]],
        )
        self.assertEqual(
            "target-agent-archived-and-worktree-unbound",
            report["next_required_readback"],
        )

    def test_delegated_cleanup_waits_for_archive_readback_before_worktree_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="merged",
                target_overrides={"archived": True},
                execution_overrides={"bound_agent_ids": [], "branch_merged": True},
            )

        self.assertEqual("eligible", report["status"])
        self.assertEqual(
            ["archive-paseo-worktree", "delete-merged-remote-branch"],
            [item["action"] for item in report["actions"]],
        )
        self.assertIsNone(report["next_required_readback"])

    def test_repository_coordinator_can_cleanup_a_terminal_campaign_orchestrator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="merged",
                actor_overrides={
                    "agent_id": "agent-repository-coordinator",
                    "role": "repository-coordinator",
                },
                target_overrides={
                    "agent_id": "agent-campaign-a",
                    "parent_agent_id": "agent-repository-coordinator",
                    "role": "orchestrator",
                },
                execution_overrides={
                    "worktree": str(root / "campaign-a"),
                    "branch": "work/issue-14-campaign-a",
                    "bound_agent_ids": ["agent-campaign-a"],
                    "branch_merged": True,
                },
            )

        self.assertEqual("eligible", report["status"])
        self.assertEqual(
            "archive-paseo-agent", report["actions"][0]["action"]
        )

    def test_cleanup_rejects_a_sibling_or_foreign_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                target_overrides={
                    "agent_id": "agent-foreign",
                    "parent_agent_id": "another-orchestrator",
                },
                execution_overrides={
                    "worktree": str(root / "foreign"),
                    "branch": "work/issue-8-foreign",
                    "bound_agent_ids": ["agent-foreign"],
                },
            )

        self.assertEqual("protected", report["status"])
        self.assertEqual([], report["actions"])
        self.assertIn("target-not-direct-subagent", report["blockers"])

    def test_cleanup_requires_an_orchestrator_actor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                actor_overrides={
                    "agent_id": "agent-worker-parent",
                    "role": "implementation",
                },
                target_overrides={
                    "agent_id": "agent-worker-child",
                    "parent_agent_id": "agent-worker-parent",
                },
                execution_overrides={
                    "worktree": str(root / "issue-12"),
                    "branch": "work/issue-12-example",
                    "bound_agent_ids": ["agent-worker-child"],
                },
            )

        self.assertEqual("protected", report["status"])
        self.assertEqual([], report["actions"])
        self.assertIn("actor-role-not-cleanup-owner", report["blockers"])

    def test_cleanup_rejects_a_non_campaign_target_role(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                target_overrides={
                    "agent_id": "agent-planner",
                    "role": "planning",
                },
                execution_overrides={
                    "worktree": str(root / "planner"),
                    "branch": "work/issue-13-planner",
                    "bound_agent_ids": ["agent-planner"],
                },
            )

        self.assertEqual("protected", report["status"])
        self.assertEqual([], report["actions"])
        self.assertIn("target-role-not-cleanable", report["blockers"])

    def test_cleanup_only_targets_a_work_issue_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="merged",
                execution_mode="inline",
                target=None,
                execution_overrides={
                    "worktree": str(root / "release"),
                    "branch": "main",
                    "bound_agent_ids": [],
                    "branch_merged": True,
                },
            )

        self.assertEqual("protected", report["status"])
        self.assertEqual([], report["actions"])
        self.assertIn("execution-branch-not-work-issue", report["blockers"])

    def test_campaign_orchestrator_cannot_delete_repository_control_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            control_worktree = str(root / "repository-control")
            report = cleanup_report(
                root,
                execution_mode="inline",
                protected_control_worktree=control_worktree,
                actor_overrides={
                    "agent_id": "agent-campaign-a",
                    "worktree": str(root / "campaign-a"),
                },
                target=None,
                execution_overrides={
                    "worktree": control_worktree,
                    "branch": "work/issue-15-disguised-control",
                    "bound_agent_ids": [],
                },
            )

        self.assertEqual("protected", report["status"])
        self.assertEqual([], report["actions"])
        self.assertIn("control-worktree-protected", report["blockers"])

    def test_cleanup_rejects_non_boolean_safety_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            cases = (
                ({"idle": "false"}, {}, "target.idle"),
                ({"archived": "false"}, {}, "target.archived"),
                ({}, {"clean": "false"}, "execution.clean"),
                ({}, {"durable": "false"}, "execution.durable"),
                ({}, {"branch_merged": "false"}, "execution.branch_merged"),
            )
            for target_overrides, execution_overrides, field in cases:
                with self.subTest(field=field):
                    with self.assertRaisesRegex(ValueError, f"{field} must be boolean"):
                        cleanup_report(
                            root,
                            target_overrides=target_overrides,
                            execution_overrides=execution_overrides,
                        )

    def test_cleanup_rejects_a_detached_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                target_overrides={
                    "agent_id": "agent-handoff",
                    "relationship": "detached",
                },
                execution_overrides={
                    "worktree": str(root / "handoff"),
                    "branch": "work/issue-9-handoff",
                    "bound_agent_ids": ["agent-handoff"],
                },
            )

        self.assertEqual("protected", report["status"])
        self.assertEqual([], report["actions"])
        self.assertIn("target-relationship-not-subagent", report["blockers"])

    def test_cleanup_rejects_a_shared_execution_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="merged",
                execution_overrides={
                    "bound_agent_ids": ["agent-worker-7", "agent-review-7"],
                    "branch_merged": True,
                },
            )

        self.assertEqual("protected", report["status"])
        self.assertEqual([], report["actions"])
        self.assertIn("worktree-in-use", report["blockers"])

    def test_cleanup_preserves_a_busy_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                seconds_since_event=400,
                target_overrides={"idle": False},
            )

        self.assertEqual("protected", report["status"])
        self.assertTrue(report["overdue"])
        self.assertEqual([], report["actions"])
        self.assertIn("agent-not-idle", report["blockers"])

    def test_inline_cleanup_rejects_an_agent_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="merged",
                execution_mode="inline",
                target_overrides={
                    "agent_id": "agent-orchestrator",
                    "parent_agent_id": None,
                    "role": "orchestrator",
                },
                execution_overrides={
                    "worktree": str(root / "issue-10"),
                    "branch": "work/issue-10-inline",
                    "bound_agent_ids": ["agent-orchestrator"],
                    "branch_merged": True,
                },
            )

        self.assertEqual("protected", report["status"])
        self.assertEqual([], report["actions"])
        self.assertIn("inline-agent-target-forbidden", report["blockers"])

    def test_inline_cleanup_only_targets_the_isolated_execution_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="merged",
                execution_mode="inline",
                target=None,
                execution_overrides={
                    "worktree": str(root / "issue-10"),
                    "branch": "work/issue-10-inline",
                    "bound_agent_ids": [],
                    "branch_merged": True,
                },
            )

        self.assertEqual("eligible", report["status"])
        self.assertEqual(
            ["archive-paseo-worktree", "delete-merged-remote-branch"],
            [item["action"] for item in report["actions"]],
        )

    def test_delegated_cleanup_requires_an_exact_agent_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                target=None,
                execution_overrides={
                    "worktree": str(root / "unknown"),
                    "branch": "work/issue-11-unknown",
                    "bound_agent_ids": [],
                },
            )

        self.assertEqual("protected", report["status"])
        self.assertEqual([], report["actions"])
        self.assertIn("delegated-agent-target-required", report["blockers"])

    def test_cleanup_is_event_triggered_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            protected = cleanup_report(
                root,
                seconds_since_event=400,
                target_overrides={"idle": False},
                execution_overrides={"clean": False, "durable": False},
            )
            self.assertEqual("protected", protected["status"])
            self.assertTrue(protected["overdue"])
            self.assertEqual([], protected["actions"])
            for blocker in (
                "agent-not-idle",
                "worktree-not-clean",
                "work-not-durable",
            ):
                self.assertIn(blocker, protected["blockers"])

    def test_v3_cleanup_cli_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ORCHESTRATOR_SCRIPTS / "execution_policy.py"),
                    "cleanup-plan",
                    "--event",
                    "stopped",
                    "--seconds-since-event",
                    "0",
                    "--worktree",
                    str(Path(temporary).resolve()),
                    "--agent-id",
                    "agent-worker-7",
                    "--agent-idle",
                    "--worktree-clean",
                    "--durable",
                    "--ownership-unambiguous",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(2, result.returncode)
        self.assertNotIn("Traceback", result.stderr)

    def test_v4_cleanup_cli_accepts_explicit_actor_and_target_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            result = subprocess.run(
                [
                    sys.executable,
                    str(ORCHESTRATOR_SCRIPTS / "execution_policy.py"),
                    "cleanup-plan",
                    "--event",
                    "merged",
                    "--seconds-since-event",
                    "20",
                    "--execution-mode",
                    "paseo-agent",
                    "--actor-agent-id",
                    "agent-orchestrator",
                    "--actor-role",
                    "orchestrator",
                    "--actor-worktree",
                    str(root / "control"),
                    "--protected-control-worktree",
                    str(root / "control"),
                    "--target-agent-id",
                    "agent-worker-7",
                    "--target-parent-agent-id",
                    "agent-orchestrator",
                    "--target-relationship",
                    "subagent",
                    "--target-role",
                    "implementation",
                    "--target-agent-idle",
                    "--target-worktree",
                    str(root / "issue-7"),
                    "--branch",
                    "work/issue-7-example",
                    "--worktree-agent-id",
                    "agent-worker-7",
                    "--worktree-clean",
                    "--work-durable",
                    "--branch-merged",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual("eligible", payload["policy"]["status"])

    def test_concurrency_cli_reports_the_repository_merge_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hotsets = Path(temporary) / "active-hotsets.json"
            hotsets.write_text(
                json.dumps({"campaign-a": ["src/campaign-a"]}), encoding="utf-8"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(ORCHESTRATOR_SCRIPTS / "execution_policy.py"),
                    "concurrency",
                    "--campaign-id",
                    "campaign-b",
                    "--requested-hotset",
                    "src/campaign-b",
                    "--active-hotsets-json",
                    str(hotsets),
                    "--integration-lease-holder",
                    "campaign-a",
                    "--pinned-dev-sha",
                    "a" * 40,
                    "--current-dev-sha",
                    "a" * 40,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["policy"]["can_execute"])
        self.assertFalse(payload["policy"]["can_merge_dev"])
        self.assertIn(
            "integration-lease-held-by-other", payload["policy"]["blockers"]
        )

    def test_capacity_cli_admits_an_orchestrator_for_a_second_campaign(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ORCHESTRATOR_SCRIPTS / "execution_policy.py"),
                "capacity",
                "--campaign-id",
                "campaign-b",
                "--repository-coordinators",
                "1",
                "--campaign-orchestrators",
                "0",
                "--campaign-active-agents",
                "0",
                "--global-active-agents",
                "2",
                "--global-max-active-agents",
                "6",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["policy"]["can_add_campaign_orchestrator"])


class ArchiveAuthorizationTests(unittest.TestCase):
    def test_agent_cannot_archive_itself(self) -> None:
        policy = load_archive_policy()
        decision = policy.authorize_agent_archive(
            actor_kind="agent",
            actor_agent_id="agent-orchestrator",
            target_agent_id="agent-orchestrator",
            target_parent_agent_id=None,
            target_idle=True,
            force=False,
        )

        self.assertFalse(decision["authorized"])
        self.assertEqual("SELF_ARCHIVE_FORBIDDEN", decision["error"])

    def test_agent_cannot_archive_a_root_agent(self) -> None:
        policy = load_archive_policy()
        decision = policy.authorize_agent_archive(
            actor_kind="agent",
            actor_agent_id="agent-orchestrator",
            target_agent_id="agent-root",
            target_parent_agent_id=None,
            target_idle=True,
            force=False,
        )

        self.assertFalse(decision["authorized"])
        self.assertEqual("ROOT_ARCHIVE_REQUIRES_SUPERVISOR", decision["error"])

    def test_agent_cannot_archive_a_sibling_agent(self) -> None:
        policy = load_archive_policy()
        decision = policy.authorize_agent_archive(
            actor_kind="agent",
            actor_agent_id="agent-orchestrator",
            target_agent_id="agent-sibling",
            target_parent_agent_id="another-orchestrator",
            target_idle=True,
            force=False,
        )

        self.assertFalse(decision["authorized"])
        self.assertEqual("ARCHIVE_TARGET_NOT_DIRECT_CHILD", decision["error"])

    def test_agent_cannot_force_archive_a_running_child(self) -> None:
        policy = load_archive_policy()
        decision = policy.authorize_agent_archive(
            actor_kind="agent",
            actor_agent_id="agent-orchestrator",
            target_agent_id="agent-worker-7",
            target_parent_agent_id="agent-orchestrator",
            target_idle=False,
            force=True,
        )

        self.assertFalse(decision["authorized"])
        self.assertEqual("FORCE_REQUIRES_SUPERVISOR", decision["error"])

    def test_agent_can_only_archive_an_idle_child(self) -> None:
        policy = load_archive_policy()
        decision = policy.authorize_agent_archive(
            actor_kind="agent",
            actor_agent_id="agent-orchestrator",
            target_agent_id="agent-worker-7",
            target_parent_agent_id="agent-orchestrator",
            target_idle=False,
            force=False,
        )

        self.assertFalse(decision["authorized"])
        self.assertEqual("AGENT_NOT_IDLE", decision["error"])

    def test_agent_can_archive_its_direct_idle_child(self) -> None:
        policy = load_archive_policy()
        decision = policy.authorize_agent_archive(
            actor_kind="agent",
            actor_agent_id="agent-orchestrator",
            target_agent_id="agent-worker-7",
            target_parent_agent_id="agent-orchestrator",
            target_idle=True,
            force=False,
        )

        self.assertTrue(decision["authorized"])
        self.assertIsNone(decision["error"])

    def test_supervisor_can_explicitly_force_archive_a_root_agent(self) -> None:
        policy = load_archive_policy()
        decision = policy.authorize_agent_archive(
            actor_kind="supervisor",
            actor_agent_id=None,
            target_agent_id="agent-root",
            target_parent_agent_id=None,
            target_idle=False,
            force=True,
        )

        self.assertTrue(decision["authorized"])
        self.assertIsNone(decision["error"])

    def test_worktree_with_an_unarchived_agent_is_never_archivable(self) -> None:
        policy = load_archive_policy()
        decision = policy.authorize_worktree_archive(
            actor_kind="supervisor",
            actor_agent_id=None,
            actor_worktree=None,
            protected_control_worktree=r"C:\worktrees\dev-control",
            target_worktree=r"C:\worktrees\issue-7",
            bound_agent_ids=["agent-worker-7"],
        )

        self.assertFalse(decision["authorized"])
        self.assertEqual("WORKTREE_IN_USE", decision["error"])

    def test_agent_cannot_archive_its_control_worktree(self) -> None:
        policy = load_archive_policy()
        worktree = r"C:\worktrees\dev-control"
        decision = policy.authorize_worktree_archive(
            actor_kind="agent",
            actor_agent_id="agent-orchestrator",
            actor_worktree=worktree,
            protected_control_worktree=worktree,
            target_worktree=worktree,
            bound_agent_ids=[],
        )

        self.assertFalse(decision["authorized"])
        self.assertEqual("CONTROL_WORKTREE_PROTECTED", decision["error"])

    def test_agent_can_archive_an_unbound_execution_worktree(self) -> None:
        policy = load_archive_policy()
        decision = policy.authorize_worktree_archive(
            actor_kind="agent",
            actor_agent_id="agent-orchestrator",
            actor_worktree=r"C:\worktrees\dev-control",
            protected_control_worktree=r"C:\worktrees\dev-control",
            target_worktree=r"C:\worktrees\issue-7",
            bound_agent_ids=[],
        )

        self.assertTrue(decision["authorized"])
        self.assertIsNone(decision["error"])


class ProviderPolicyTests(unittest.TestCase):
    def test_highest_permission_mode_uses_advertised_capabilities(self) -> None:
        result = PROVIDERS.resolve_highest_permission_mode(
            [
                {"id": "plan-x", "label": "Plan", "description": "read only"},
                {"id": "ask-x", "label": "Always Ask", "description": "prompts for permission"},
                {"id": "auto-x", "label": "Auto mode", "description": "automatic approval"},
                {"id": "power-x", "label": "Unrestricted", "description": "skip all permission prompts"},
            ]
        )
        self.assertEqual("power-x", result["runtime_mode_id"])
        self.assertEqual("advertised-unattended-full-access", result["evidence"])

    def test_missing_unattended_mode_fails_closed(self) -> None:
        with self.assertRaisesRegex(PROVIDERS.ProviderPolicyError, "unattended"):
            PROVIDERS.resolve_highest_permission_mode(
                [{"id": "ask", "label": "Always Ask", "description": "prompts for permission"}]
            )

    def test_role_preference_is_resolved_without_hardcoded_provider(self) -> None:
        report = PROVIDERS.resolve_provider(
            role_category="impl",
            preferences={"providers": {"impl": "provider-a/namespace/model-x"}},
            available_providers={"provider-a", "provider-b"},
        )
        self.assertEqual("provider-a/namespace/model-x", report["selector"])
        self.assertEqual("orchestration-preferences", report["source"])

    def test_campaign_orchestrators_can_use_different_provider_bindings(self) -> None:
        preferences = {"providers": {"planning": "provider-a/default"}}
        available = {"provider-a", "provider-b"}
        campaign_a = PROVIDERS.resolve_campaign_orchestrator_provider(
            campaign_id="campaign-a",
            preferences=preferences,
            available_providers=available,
            explicit_override="provider-a/model-x",
        )
        campaign_b = PROVIDERS.resolve_campaign_orchestrator_provider(
            campaign_id="campaign-b",
            preferences=preferences,
            available_providers=available,
            explicit_override="provider-b/model-y",
        )

        self.assertEqual("provider-a/model-x", campaign_a["selector"])
        self.assertEqual("provider-b/model-y", campaign_b["selector"])
        self.assertEqual("campaign-a", campaign_a["campaign_id"])
        self.assertEqual("campaign-b", campaign_b["campaign_id"])

    def test_provider_cli_returns_a_campaign_binding_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            preferences = Path(temporary) / "preferences.json"
            preferences.write_text(
                json.dumps({"providers": {"planning": "provider-a/default"}}),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(ORCHESTRATOR_SCRIPTS / "provider_policy.py"),
                    "--role-category",
                    "planning",
                    "--campaign-id",
                    "campaign-b",
                    "--preferences",
                    str(preferences),
                    "--available-provider",
                    "provider-b",
                    "--explicit",
                    "provider-b/model-y",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("campaign-b", payload["provider"]["campaign_id"])
        self.assertEqual("provider-b/model-y", payload["provider"]["selector"])

    def test_explicit_override_wins_and_unavailable_provider_fails(self) -> None:
        report = PROVIDERS.resolve_provider(
            role_category="audit",
            preferences={"providers": {"audit": "provider-a/model-x"}},
            available_providers={"provider-a", "provider-b"},
            explicit_override="provider-b/model-y",
        )
        self.assertEqual("explicit-override", report["source"])
        with self.assertRaisesRegex(PROVIDERS.ProviderPolicyError, "unavailable"):
            PROVIDERS.resolve_provider(
                role_category="audit",
                preferences={"providers": {"audit": "missing/model"}},
                available_providers={"provider-a"},
            )

    def test_missing_role_preference_fails_closed(self) -> None:
        with self.assertRaisesRegex(PROVIDERS.ProviderPolicyError, "no provider"):
            PROVIDERS.resolve_provider(
                role_category="research",
                preferences={"providers": {}},
                available_providers={"provider-a"},
            )

    def test_malformed_selectors_fail_closed(self) -> None:
        malformed = [
            "provider-a",
            "provider-a/",
            "provider-a/model x",
            "provider-a//model-x",
            "provider-a/ns/model-x/",
        ]
        for selector in malformed:
            with self.subTest(selector=selector):
                with self.assertRaisesRegex(PROVIDERS.ProviderPolicyError, "must be"):
                    PROVIDERS.resolve_provider(
                        role_category="impl",
                        preferences={"providers": {"impl": selector}},
                        available_providers={"provider-a"},
                    )


class FakeRoomRunner:
    def __init__(self):
        self.messages: list[dict[str, str]] = []
        self.calls: list[list[str]] = []

    def __call__(self, arguments):
        args = list(arguments)
        self.calls.append(args)
        action = args[1]
        if action == "post":
            message = {
                "id": f"message-{len(self.messages) + 1}",
                "body": args[3],
                "author": "agent-worker-7",
            }
            self.messages.append(message)
            payload = message
        elif action == "read":
            payload = self.messages
        elif action == "wait":
            payload = []
        elif action == "inspect":
            payload = {"name": args[2], "messageCount": len(self.messages)}
        elif action == "create":
            payload = {"id": "room-1", "name": args[2]}
        elif action == "delete":
            payload = {"id": "room-1", "name": args[2]}
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")


class PaseoRoomProtocolTests(unittest.TestCase):
    def test_windows_cmd_wrapper_preserves_quoted_arguments(self) -> None:
        command = ROOM._windows_cmd_command(
            r"C:\Program Files\Paseo\paseo.cmd",
            ["chat", "create", "gwo-test-campaign", "--purpose", "two words"],
        )
        self.assertEqual(["/d", "/c"], command[1:3])
        self.assertEqual(r"C:\Program Files\Paseo\paseo.cmd", command[3])
        self.assertEqual("two words", command[-1])

    def setUp(self) -> None:
        self.runner = FakeRoomRunner()
        self.protocol = ROOM.PaseoRoom(self.runner)

    def test_create_preflight_and_post_return_receipts(self) -> None:
        created = self.protocol.create("campaign-20260718", "Issue campaign")
        self.assertEqual("gwo-campaign-20260718", created["room"])
        with mock.patch.dict(os.environ, {"PASEO_AGENT_ID": "agent-worker-7"}):
            preflight = self.protocol.preflight(
                created["room"], require_agent_identity=True
            )
            receipt = self.protocol.post(created["room"], event())
        self.assertEqual("agent-worker-7", preflight["agent_id"])
        self.assertEqual("message-1", receipt["message_id"])

    def test_sender_identity_mismatch_fails_before_publish(self) -> None:
        with mock.patch.dict(os.environ, {"PASEO_AGENT_ID": "other-agent"}):
            with self.assertRaisesRegex(ROOM.RoomProtocolError, "does not match"):
                self.protocol.post("gwo-campaign-20260718", event())
        self.assertEqual([], self.runner.messages)

    def test_replay_deduplicates_and_rejects_conflicts(self) -> None:
        original = event()
        duplicate = event()
        conflict = event(evidence="different evidence")
        invalid = {"id": "message-4", "body": "human note", "author": "manual"}
        for index, payload in enumerate((original, duplicate, conflict), start=1):
            self.runner.messages.append(
                {"id": f"message-{index}", "body": json.dumps(payload), "author": "a"}
            )
        self.runner.messages.append(invalid)
        replay = self.protocol.replay("gwo-campaign-20260718")
        self.assertEqual(1, len(replay["events"]))
        self.assertEqual(
            ["duplicate-signal-conflict", "event-must-be-object"],
            [item["reason"] for item in replay["rejected"]],
        )

    def test_wait_always_replays_room_after_wakeup(self) -> None:
        self.runner.messages.append(
            {"id": "message-1", "body": json.dumps(event()), "author": "a"}
        )
        result = self.protocol.wait("gwo-campaign-20260718", timeout="10s")
        self.assertEqual(1, len(result["events"]))
        self.assertEqual(["wait", "read"], [call[1] for call in self.runner.calls])

    def test_missing_agent_identity_fails_preflight(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ROOM.RoomProtocolError, "PASEO_AGENT_ID"):
                self.protocol.preflight(
                    "gwo-campaign-20260718", require_agent_identity=True
                )


class WorkerPreflightTests(unittest.TestCase):
    def test_permission_and_exact_base_guards_remain(self) -> None:
        sha = "a" * 40
        report = PREFLIGHT.evaluate_preflight(
            expected_base=sha,
            expected_branch="work/issue-7-example",
            filesystem="unrestricted",
            network="enabled",
            approval="never",
            observed={
                "head": sha,
                "integration_head": sha,
                "branch": "work/issue-7-example",
                "status": "",
            },
            require_github=False,
        )
        self.assertEqual("passed", report["status"])


if __name__ == "__main__":
    unittest.main()
