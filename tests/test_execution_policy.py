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
MATERIAL = load_module("material_delivery")
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


def identity_receipt(
    *,
    agent_id: str = "agent-worker-7",
    campaign_id: str = "campaign-20260718",
    dispatch_id: str = "dispatch-issue-7",
    role: str = "implementation",
    parent_agent_id: str | None = "agent-orchestrator",
    relationship: str = "subagent",
    authority_kind: str | None = None,
    authority_subject_agent_id: str | None = None,
    review_axis: str | None = None,
):
    labels = {
        "repository": "owner/repo",
        "role": role,
    }
    assignment = None
    if role == "review":
        if review_axis is None:
            raise ValueError("review_axis is required for review receipts")
        labels.update({"campaign_id": campaign_id, "review_axis": review_axis})
        assignment = {
            "agent_id": agent_id,
            "campaign_id": campaign_id,
            "review_axis": review_axis,
            "campaign_parent_agent_id": parent_agent_id,
            "lock": {
                "dispatch_id": dispatch_id,
                "candidate_sha": "b" * 40,
                "base_sha": "a" * 40,
                "diff_sha256": "c" * 64,
                "acceptance_sha256": "d" * 64,
                "review_round": 1,
                "scope": "full",
                "previous_candidate_sha": None,
            },
            "review_lock_read_back": True,
            "read_back": True,
        }
        authority_kind = authority_kind or "reusable-reviewer"
        subject_agent_id = agent_id
        subject_parent_agent_id = parent_agent_id
        subject_relationship = relationship
        subject_labels = labels
    elif role in ROOM.WORKER_ROLES:
        labels.update({"campaign_id": campaign_id, "dispatch_id": dispatch_id})
        authority_kind = authority_kind or "dispatch-owner"
        subject_agent_id = agent_id
        subject_parent_agent_id = parent_agent_id
        subject_relationship = relationship
        subject_labels = labels
    elif role == "orchestrator":
        labels["campaign_id"] = campaign_id
        authority_kind = authority_kind or "direct-child-dispatch"
        if authority_kind == "campaign-control":
            subject_agent_id = agent_id
            subject_parent_agent_id = parent_agent_id
            subject_relationship = relationship
            subject_labels = labels
        else:
            subject_agent_id = authority_subject_agent_id or "agent-worker-7"
            subject_parent_agent_id = agent_id
            subject_relationship = "subagent"
            subject_labels = {
                "repository": "owner/repo",
                "campaign_id": campaign_id,
                "dispatch_id": dispatch_id,
                "role": "implementation",
            }
    else:
        authority_kind = authority_kind or "admitted-campaign"
        subject_agent_id = "agent-orchestrator"
        subject_parent_agent_id = agent_id
        subject_relationship = "subagent"
        subject_labels = {
            "repository": "owner/repo",
            "campaign_id": campaign_id,
            "role": "orchestrator",
        }
    receipt = {
        "agent_id": agent_id,
        "campaign_id": campaign_id,
        "dispatch_id": dispatch_id,
        "role": role,
        "parent_agent_id": parent_agent_id,
        "relationship": relationship,
        "labels": labels,
        "authority": {
            "kind": authority_kind,
            "campaign_id": campaign_id,
            "dispatch_id": dispatch_id,
            "subject_agent_id": subject_agent_id,
            "subject_parent_agent_id": subject_parent_agent_id,
            "subject_relationship": subject_relationship,
            "subject_labels": subject_labels,
            "read_back": True,
        },
        "read_back": True,
    }
    if role == "review":
        receipt["assignment"] = assignment
        receipt["authority"].update(
            {
                "campaign_parent_agent_id": parent_agent_id,
                "review_axis": review_axis,
                "assignment": assignment,
            }
        )
    return receipt


def review_lock_receipt(
    *,
    dispatch_id: str = "dispatch-issue-7",
    candidate_sha: str = "b" * 40,
    base_sha: str = "a" * 40,
    diff_sha256: str = "c" * 64,
    acceptance_sha256: str = "d" * 64,
    review_round: int = 1,
    scope: str = "full",
    previous_candidate_sha: str | None = None,
) -> dict:
    return {
        "campaign_id": "campaign-20260718",
        "dispatch_id": dispatch_id,
        "candidate_sha": candidate_sha,
        "base_sha": base_sha,
        "diff_sha256": diff_sha256,
        "acceptance_sha256": acceptance_sha256,
        "review_round": review_round,
        "scope": scope,
        "previous_candidate_sha": previous_candidate_sha,
        "previous_review_round": review_round - 1 if scope == "delta" else None,
        "previous_lock_read_back": scope != "delta" or review_round > 1,
        "source": "campaign-verified-candidate",
        "read_back": True,
    }


def review_identity_receipts(*axes: str | tuple[str, str]) -> list[dict]:
    reviewers = [
        identity_receipt(
            agent_id=(item[0] if isinstance(item, tuple) else f"agent-{item}-reviewer"),
            role="review",
            review_axis=(item[1] if isinstance(item, tuple) else item),
        )
        for item in axes
    ]
    parent = identity_receipt(
        agent_id="agent-orchestrator",
        role="orchestrator",
        parent_agent_id="agent-repository-coordinator",
    )
    parent["authority"] = {
        "kind": "direct-child-dispatch",
        "campaign_id": "campaign-20260718",
        "dispatch_id": "dispatch-issue-7",
        "subjects": [
            {
                "agent_id": receipt["agent_id"],
                "parent_agent_id": "agent-orchestrator",
                "relationship": "subagent",
                "labels": receipt["labels"],
                "assignment": receipt["assignment"],
            }
            for receipt in reviewers
        ],
        "read_back": True,
    }
    return [*reviewers, parent]


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
    target_kind: str | None = None,
    resource_kind: str | None = None,
):
    actor = {
        "agent_id": "agent-orchestrator",
        "role": "orchestrator",
        "worktree": str(root / "control"),
        "repository": "owner/repo",
        "campaign_id": "campaign-20260718",
        "dispatch_id": "dispatch-issue-7",
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
            "repository": "owner/repo",
            "campaign_id": "campaign-20260718",
            "dispatch_id": "dispatch-issue-7",
            "campaign_control_expected": False,
            "campaign_generation": None,
            "campaign_generation_read_back": False,
            "labels": {"repository": "owner/repo"},
            "labels_read_back": True,
            "result_captured": False,
            "result_captured_read_back": False,
        }
    if target is not None:
        target = {**target, **(target_overrides or {})}
        if target.get("role") == "orchestrator" and target.get("campaign_generation") is None:
            target["campaign_generation"] = (
                "v4.3" if target.get("campaign_control_expected") else "legacy-v4.2"
            )
            target["campaign_generation_read_back"] = True
    if target is not None and target.get("role") == "orchestrator":
        terminal_event = "CAMPAIGN_CLOSED"
        terminal_sender = target["agent_id"]
    elif event == "stopped":
        terminal_event = "STOPPED"
        terminal_sender = (
            target["agent_id"] if target is not None else actor["agent_id"]
        )
    else:
        terminal_event = "COMPLETED"
        terminal_sender = actor["agent_id"]
    execution = {
        "worktree": str(root / "issue-7"),
        "branch": "work/issue-7-example",
        "clean": True,
        "durable": True,
        "bound_agent_ids": ["agent-worker-7"],
        "branch_merged": False,
        "agent_only": False,
        "unique_commits": 0,
        "branch_local_only": False,
        "remaining_child_agent_ids": [],
        "children_read_back": True,
        "children_repository": "owner/repo",
        "children_campaign_id": "campaign-20260718",
        "children_scope": "direct-subagent",
        "no_worktree_read_back": False,
        "resource_identity_read_back": False,
        "worktree_slug": None,
        "resource_archived": False,
        "branch_deleted": False,
        "repository": "owner/repo",
        "campaign_id": "campaign-20260718",
        "dispatch_id": "dispatch-issue-7",
        "terminal_receipt": {
            "event_type": terminal_event,
            "signal_id": "terminal-issue-7",
            "sender_agent_id": terminal_sender,
            "repository": "owner/repo",
            "campaign_id": "campaign-20260718",
            "dispatch_id": "dispatch-issue-7",
            "read_back": True,
        },
    }
    execution.update(execution_overrides or {})
    if target_kind is None:
        target_kind = (
            "campaign"
            if target is not None and target.get("role") == "orchestrator"
            else "worker"
        )
    if resource_kind is None:
        if target_kind == "campaign":
            resource_kind = "none" if execution.get("agent_only") else "campaign-control"
        elif execution.get("agent_only"):
            resource_kind = "none"
        else:
            resource_kind = "issue-worktree"
    if resource_kind == "campaign-control":
        if "resource_identity_read_back" not in (execution_overrides or {}):
            execution["resource_identity_read_back"] = True
        if "worktree_slug" not in (execution_overrides or {}):
            execution["worktree_slug"] = Path(execution["worktree"]).name
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
        target_kind=target_kind,
        resource_kind=resource_kind,
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
            contract(
                relationship="detached", parent_agent_id="", notify_on_finish=False
            )
        )
        self.assertIn("relationship-must-be-subagent", report["errors"])
        self.assertIn("missing-or-empty:parent_agent_id", report["errors"])
        self.assertIn("notify-on-finish-must-be-true", report["errors"])

    def test_campaign_orchestrator_is_a_subagent_of_the_repository_coordinator(
        self,
    ) -> None:
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
            active_agents_for_campaign=6,
            active_agents_global=7,
            max_active_agents_global=7,
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
            (
                {"active_agents_for_campaign": 3, "active_agents_global": 2},
                "campaign count",
            ),
            (
                {"active_agents_for_campaign": 1, "active_agents_global": 1},
                "combined count",
            ),
            (
                {
                    "campaign_orchestrators_for_campaign": 2,
                    "active_agents_for_campaign": 1,
                },
                "orchestrator count",
            ),
            (
                {"repository_coordinators": 3, "active_agents_global": 2},
                "coordinator count",
            ),
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
            case_sensitive_paths=True,
            integration_control_available=True,
            integration_control_clean=True,
        )
        campaign_b = POLICY.campaign_concurrency_report(
            campaign_id="campaign-b",
            requested_hotset=["src/campaign-b"],
            active_hotsets={"campaign-a": ["src/campaign-a"]},
            integration_lease_holder="campaign-a",
            pinned_dev_sha=current_dev,
            current_dev_sha=current_dev,
            case_sensitive_paths=True,
            integration_control_available=True,
            integration_control_clean=True,
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
            case_sensitive_paths=True,
            integration_control_available=True,
            integration_control_clean=True,
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
                        case_sensitive_paths=True,
                        integration_control_available=True,
                        integration_control_clean=True,
                    )

    def test_hotsets_preserve_case_on_case_sensitive_repositories(self) -> None:
        self.assertEqual("Src/API.py", POLICY.normalize_hotset_entry("Src/API.py"))
        report = POLICY.campaign_concurrency_report(
            campaign_id="campaign-a",
            requested_hotset=["Src/API.py"],
            active_hotsets={"campaign-b": ["src/api.py"]},
            integration_lease_holder="campaign-a",
            pinned_dev_sha="a" * 40,
            current_dev_sha="a" * 40,
            case_sensitive_paths=True,
            integration_control_available=True,
            integration_control_clean=True,
        )
        self.assertTrue(report["can_execute"])
        insensitive = POLICY.campaign_concurrency_report(
            campaign_id="campaign-a",
            requested_hotset=["Src/API.py"],
            active_hotsets={"campaign-b": ["src/api.py"]},
            integration_lease_holder="campaign-a",
            pinned_dev_sha="a" * 40,
            current_dev_sha="a" * 40,
            case_sensitive_paths=False,
            integration_control_available=True,
            integration_control_clean=True,
        )
        self.assertFalse(insensitive["can_execute"])

        with self.assertRaisesRegex(ValueError, "readback"):
            POLICY.campaign_concurrency_report(
                campaign_id="campaign-a",
                requested_hotset=["Src/API.py"],
                active_hotsets={},
                integration_lease_holder="campaign-a",
                pinned_dev_sha="a" * 40,
                current_dev_sha="a" * 40,
            )

    def test_hotset_traversal_cannot_bypass_an_existing_claim(self) -> None:
        dev_sha = "a" * 40
        with self.assertRaisesRegex(ValueError, "canonical repository-relative path"):
            POLICY.campaign_concurrency_report(
                campaign_id="campaign-b",
                requested_hotset=["src/shared"],
                active_hotsets={"campaign-a": ["src/other/../shared"]},
                integration_lease_holder="campaign-b",
                pinned_dev_sha=dev_sha,
                current_dev_sha=dev_sha,
                case_sensitive_paths=True,
                integration_control_available=True,
                integration_control_clean=True,
            )

    def test_dev_advancing_requires_base_refresh_before_merge(self) -> None:
        report = POLICY.campaign_concurrency_report(
            campaign_id="campaign-b",
            requested_hotset=["src/campaign-b"],
            active_hotsets={},
            integration_lease_holder="campaign-b",
            pinned_dev_sha="a" * 40,
            current_dev_sha="b" * 40,
            case_sensitive_paths=True,
            integration_control_available=True,
            integration_control_clean=True,
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
            case_sensitive_paths=True,
            integration_control_available=True,
            integration_control_clean=True,
        )

        self.assertTrue(report["can_execute"])
        self.assertFalse(report["can_merge_dev"])
        self.assertFalse(report["integration_lease_held"])
        self.assertIn("integration-lease-not-held", report["blockers"])

    def test_dirty_or_missing_integration_control_waits_without_mutating_user_wip(self) -> None:
        dev_sha = "a" * 40
        dirty = POLICY.campaign_concurrency_report(
            campaign_id="campaign-b",
            requested_hotset=["src/campaign-b"],
            active_hotsets={},
            integration_lease_holder="campaign-b",
            pinned_dev_sha=dev_sha,
            current_dev_sha=dev_sha,
            case_sensitive_paths=True,
            integration_control_available=True,
            integration_control_clean=False,
        )
        missing = POLICY.campaign_concurrency_report(
            campaign_id="campaign-b",
            requested_hotset=["src/campaign-b"],
            active_hotsets={},
            integration_lease_holder="campaign-b",
            pinned_dev_sha=dev_sha,
            current_dev_sha=dev_sha,
            case_sensitive_paths=True,
            integration_control_available=False,
            integration_control_clean=False,
        )

        for report in (dirty, missing):
            self.assertTrue(report["can_execute"])
            self.assertFalse(report["can_merge_dev"])
            self.assertEqual("WAITING_INTEGRATION", report["candidate_state"])
            self.assertEqual([], report["recovery_actions"])
            self.assertTrue(report["preserve_user_wip"])
        self.assertIn("integration-control-worktree-dirty", dirty["blockers"])
        self.assertIn("integration-control-worktree-unavailable", missing["blockers"])

    def test_cleanup_protects_the_root_coordinator_and_control_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                actor_overrides={
                    "role": "repository-coordinator",
                    "worktree": str(root),
                },
                target_overrides={
                    "agent_id": "agent-orchestrator",
                    "parent_agent_id": None,
                    "role": "orchestrator",
                },
                execution_overrides={
                    "worktree": str(root),
                    "branch": "dev",
                    "bound_agent_ids": ["agent-orchestrator"],
                    "branch_merged": True,
                },
                protected_control_worktree=str(root),
            )

        self.assertEqual("protected", report["status"])
        self.assertFalse(report["automatic_execution"])
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
        self.assertTrue(report["automatic_execution"])
        self.assertEqual(
            ["archive-paseo-agent"],
            [item["action"] for item in report["actions"]],
        )
        self.assertEqual(
            "target-agent-archived-and-worktree-unbound",
            report["next_required_readback"],
        )

    def test_cleanup_requires_exact_identity_and_terminal_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            missing = cleanup_report(
                root,
                execution_overrides={"terminal_receipt": None},
            )
            mismatch = cleanup_report(
                root,
                target_overrides={"campaign_id": "campaign-other"},
            )
            invalid_terminal_reports = []
            for event_type in ("HEARTBEAT", "DELIVERY_WAKE", "DELIVERY_ACK"):
                invalid_terminal_reports.append(
                    cleanup_report(
                        root,
                        execution_overrides={
                            "terminal_receipt": {
                                "event_type": event_type,
                                "signal_id": f"{event_type.lower()}-7",
                                "sender_agent_id": "agent-worker-7",
                                "repository": "owner/repo",
                                "campaign_id": "campaign-20260718",
                                "dispatch_id": "dispatch-issue-7",
                                "read_back": True,
                            }
                        },
                    )
                )

        self.assertIn("terminal-receipt-missing", missing["blockers"])
        self.assertIn("cleanup-identity-mismatch", mismatch["blockers"])
        for report in invalid_terminal_reports:
            self.assertIn("terminal-receipt-invalid", report["blockers"])
        for report in (missing, mismatch, *invalid_terminal_reports):
            self.assertEqual("protected", report["status"])
            self.assertEqual([], report["actions"])

    def test_merged_event_requires_branch_merge_readback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = cleanup_report(
                Path(temporary).resolve(),
                event="merged",
                execution_overrides={"branch_merged": False},
            )

        self.assertEqual("protected", report["status"])
        self.assertIn("merged-event-without-merged-branch", report["blockers"])

    def test_delegated_cleanup_waits_for_archive_readback_before_worktree_cleanup(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="merged",
                target_overrides={"archived": True},
                execution_overrides={"bound_agent_ids": [], "branch_merged": True},
            )

        self.assertEqual("eligible", report["status"])
        self.assertTrue(report["automatic_execution"])
        self.assertEqual(
            ["archive-paseo-worktree", "delete-merged-remote-branch"],
            [item["action"] for item in report["actions"]],
        )
        self.assertIsNone(report["next_required_readback"])

    def test_repository_coordinator_can_cleanup_a_terminal_campaign_orchestrator(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="campaign-closed",
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
                    "worktree": None,
                    "branch": None,
                    "bound_agent_ids": [],
                    "agent_only": True,
                },
            )

        self.assertEqual("eligible", report["status"])
        self.assertTrue(report["automatic_execution"])
        self.assertEqual("archive-paseo-agent", report["actions"][0]["action"])
        self.assertEqual("target-agent-archived", report["next_required_readback"])

    def test_campaign_orchestrator_cleanup_completes_without_a_fake_worktree(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="campaign-closed",
                actor_overrides={
                    "agent_id": "agent-repository-coordinator",
                    "role": "repository-coordinator",
                },
                target_overrides={
                    "agent_id": "agent-campaign-a",
                    "parent_agent_id": "agent-repository-coordinator",
                    "role": "orchestrator",
                    "archived": True,
                },
                execution_overrides={
                    "worktree": None,
                    "branch": None,
                    "bound_agent_ids": [],
                    "agent_only": True,
                },
            )

        self.assertEqual("eligible", report["status"])
        self.assertTrue(report["cleanup_complete"])
        self.assertFalse(report["automatic_execution"])
        self.assertEqual([], report["actions"])

    def test_new_campaign_cleanup_archives_agent_before_control_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="campaign-closed",
                target_kind="campaign",
                resource_kind="campaign-control",
                actor_overrides={
                    "agent_id": "agent-repository-coordinator",
                    "role": "repository-coordinator",
                },
                target_overrides={
                    "agent_id": "agent-campaign-a",
                    "parent_agent_id": "agent-repository-coordinator",
                    "role": "orchestrator",
                    "campaign_control_expected": True,
                },
                execution_overrides={
                    "worktree": str(root / "campaign-campaign-20260718"),
                    "branch": "gwo/campaign/campaign-20260718",
                    "bound_agent_ids": ["agent-campaign-a"],
                    "agent_only": False,
                    "clean": True,
                    "unique_commits": 0,
                    "branch_local_only": True,
                    "remaining_child_agent_ids": [],
                },
            )

        self.assertEqual("eligible", report["status"])
        self.assertEqual("campaign", report["target_kind"])
        self.assertEqual("campaign-control", report["resource_kind"])
        self.assertEqual(["archive-paseo-agent"], [item["action"] for item in report["actions"]])
        self.assertEqual(
            "target-agent-archived-and-worktree-unbound",
            report["next_required_readback"],
        )

    def test_archived_campaign_agent_only_authorizes_control_worktree_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="campaign-closed",
                target_kind="campaign",
                resource_kind="campaign-control",
                actor_overrides={
                    "agent_id": "agent-repository-coordinator",
                    "role": "repository-coordinator",
                },
                target_overrides={
                    "agent_id": "agent-campaign-a",
                    "parent_agent_id": "agent-repository-coordinator",
                    "role": "orchestrator",
                    "archived": True,
                    "campaign_control_expected": True,
                },
                execution_overrides={
                    "worktree": str(root / "campaign-campaign-20260718"),
                    "branch": "gwo/campaign/campaign-20260718",
                    "bound_agent_ids": [],
                    "agent_only": False,
                    "clean": True,
                    "unique_commits": 0,
                    "branch_local_only": True,
                    "remaining_child_agent_ids": [],
                },
            )

        self.assertEqual(
            ["archive-paseo-worktree"],
            [item["action"] for item in report["actions"]],
        )
        self.assertEqual(
            "campaign-control-worktree-absent",
            report["next_required_readback"],
        )

    def test_absent_campaign_control_worktree_then_authorizes_local_branch_delete(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="campaign-closed",
                target_kind="campaign",
                resource_kind="campaign-control",
                actor_overrides={
                    "agent_id": "agent-repository-coordinator",
                    "role": "repository-coordinator",
                },
                target_overrides={
                    "agent_id": "agent-campaign-a",
                    "parent_agent_id": "agent-repository-coordinator",
                    "role": "orchestrator",
                    "archived": True,
                    "campaign_control_expected": True,
                },
                execution_overrides={
                    "worktree": str(root / "campaign-campaign-20260718"),
                    "branch": "gwo/campaign/campaign-20260718",
                    "bound_agent_ids": [],
                    "agent_only": False,
                    "clean": True,
                    "unique_commits": 0,
                    "branch_local_only": True,
                    "resource_archived": True,
                    "branch_deleted": False,
                },
            )

        self.assertEqual(
            ["delete-local-control-branch"],
            [item["action"] for item in report["actions"]],
        )
        self.assertEqual("campaign-control-branch-absent", report["next_required_readback"])

    def test_campaign_control_cleanup_fails_closed_on_children_changes_or_commits(self) -> None:
        cases = (
            ({"remaining_child_agent_ids": ["agent-spec-reviewer"]}, "campaign-children-not-cleaned"),
            ({"children_read_back": False}, "campaign-children-not-read-back"),
            ({"clean": False}, "worktree-not-clean"),
            ({"unique_commits": 1}, "campaign-control-has-unique-commits"),
            ({"branch_local_only": False}, "campaign-control-branch-not-local-only"),
        )
        for overrides, blocker in cases:
            with self.subTest(blocker=blocker), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                report = cleanup_report(
                    root,
                    event="campaign-closed",
                    target_kind="campaign",
                    resource_kind="campaign-control",
                    actor_overrides={
                        "agent_id": "agent-repository-coordinator",
                        "role": "repository-coordinator",
                    },
                    target_overrides={
                        "agent_id": "agent-campaign-a",
                        "parent_agent_id": "agent-repository-coordinator",
                        "role": "orchestrator",
                        "campaign_control_expected": True,
                    },
                    execution_overrides={
                        "worktree": str(root / "campaign-campaign-20260718"),
                        "branch": "gwo/campaign/campaign-20260718",
                        "bound_agent_ids": ["agent-campaign-a"],
                        "agent_only": False,
                        "clean": True,
                        "unique_commits": 0,
                        "branch_local_only": True,
                        "remaining_child_agent_ids": [],
                        **overrides,
                    },
                )
            self.assertEqual("protected", report["status"])
            self.assertIn(blocker, report["blockers"])

    def test_campaign_control_resource_identity_must_match_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="campaign-closed",
                target_kind="campaign",
                resource_kind="campaign-control",
                actor_overrides={
                    "agent_id": "agent-repository-coordinator",
                    "role": "repository-coordinator",
                },
                target_overrides={
                    "agent_id": "agent-campaign-a",
                    "parent_agent_id": "agent-repository-coordinator",
                    "role": "orchestrator",
                    "campaign_control_expected": True,
                },
                execution_overrides={
                    "worktree": str(root / "campaign-campaign-other"),
                    "worktree_slug": "campaign-campaign-other",
                    "branch": "gwo/campaign/campaign-other",
                    "bound_agent_ids": ["agent-campaign-a"],
                    "agent_only": False,
                    "clean": True,
                    "unique_commits": 0,
                    "branch_local_only": True,
                    "resource_identity_read_back": True,
                },
            )

        self.assertEqual("protected", report["status"])
        self.assertIn("campaign-control-branch-invalid", report["blockers"])
        self.assertIn("campaign-control-worktree-identity-invalid", report["blockers"])

    def test_campaign_generation_readback_prevents_new_campaign_legacy_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="campaign-closed",
                target_kind="campaign",
                resource_kind="none",
                actor_overrides={
                    "agent_id": "agent-repository-coordinator",
                    "role": "repository-coordinator",
                },
                target_overrides={
                    "agent_id": "agent-campaign-a",
                    "parent_agent_id": "agent-repository-coordinator",
                    "role": "orchestrator",
                    "campaign_control_expected": False,
                    "campaign_generation": "v4.3",
                    "campaign_generation_read_back": True,
                },
                execution_overrides={
                    "worktree": None,
                    "branch": None,
                    "bound_agent_ids": [],
                    "agent_only": True,
                },
            )

        self.assertEqual("protected", report["status"])
        self.assertIn("campaign-generation-resource-mismatch", report["blockers"])

    def test_review_agent_uses_agent_only_worker_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="stopped",
                target_kind="worker",
                resource_kind="none",
                target_overrides={"role": "review"},
                execution_overrides={
                    "worktree": None,
                    "branch": None,
                    "bound_agent_ids": [],
                    "agent_only": True,
                },
            )

        self.assertEqual("eligible", report["status"])
        self.assertEqual(["archive-paseo-agent"], [item["action"] for item in report["actions"]])

    def test_ephemeral_probe_requires_explicit_lifecycle_and_captured_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            valid = cleanup_report(
                root,
                event="stopped",
                target_kind="ephemeral",
                resource_kind="none",
                target_overrides={
                    "role": "monitor",
                    "labels": {
                        "repository": "owner/repo",
                        "gwo.lifecycle": "ephemeral",
                    },
                    "result_captured": True,
                    "result_captured_read_back": True,
                },
                execution_overrides={
                    "worktree": None,
                    "branch": None,
                    "bound_agent_ids": [],
                    "agent_only": True,
                    "no_worktree_read_back": True,
                },
            )
            missing_label = cleanup_report(
                root,
                event="stopped",
                target_kind="ephemeral",
                resource_kind="none",
                target_overrides={
                    "role": "monitor",
                    "labels": {"repository": "owner/repo"},
                    "result_captured": True,
                    "result_captured_read_back": True,
                },
                execution_overrides={
                    "worktree": None,
                    "branch": None,
                    "bound_agent_ids": [],
                    "agent_only": True,
                    "no_worktree_read_back": True,
                },
            )
            missing_workspace_proof = cleanup_report(
                root,
                event="stopped",
                target_kind="ephemeral",
                resource_kind="none",
                target_overrides={
                    "role": "monitor",
                    "labels": {
                        "repository": "owner/repo",
                        "gwo.lifecycle": "ephemeral",
                    },
                    "result_captured": True,
                    "result_captured_read_back": True,
                },
                execution_overrides={
                    "worktree": None,
                    "branch": None,
                    "bound_agent_ids": [],
                    "agent_only": True,
                    "no_worktree_read_back": False,
                },
            )

        self.assertEqual("eligible", valid["status"])
        self.assertIn("ephemeral-lifecycle-label-missing", missing_label["blockers"])
        self.assertIn(
            "ephemeral-no-worktree-not-read-back", missing_workspace_proof["blockers"]
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

    def test_repository_coordinator_cannot_bypass_campaign_to_clean_a_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                target_kind="worker",
                actor_overrides={
                    "agent_id": "agent-repository-coordinator",
                    "role": "repository-coordinator",
                },
                target_overrides={
                    "parent_agent_id": "agent-repository-coordinator",
                },
            )

        self.assertEqual("protected", report["status"])
        self.assertIn("actor-role-not-cleanup-owner", report["blockers"])

    def test_campaign_orchestrator_cannot_be_disguised_as_ephemeral(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="stopped",
                target_kind="ephemeral",
                resource_kind="none",
                actor_overrides={
                    "agent_id": "agent-repository-coordinator",
                    "role": "repository-coordinator",
                },
                target_overrides={
                    "agent_id": "agent-campaign-a",
                    "parent_agent_id": "agent-repository-coordinator",
                    "role": "orchestrator",
                    "labels": {
                        "repository": "owner/repo",
                        "gwo.lifecycle": "ephemeral",
                    },
                    "result_captured": True,
                    "result_captured_read_back": True,
                },
                execution_overrides={
                    "worktree": None,
                    "branch": None,
                    "bound_agent_ids": [],
                    "agent_only": True,
                    "no_worktree_read_back": True,
                },
            )

        self.assertEqual("protected", report["status"])
        self.assertIn("ephemeral-target-role-invalid", report["blockers"])
        self.assertIn("target-role-not-cleanable", report["blockers"])

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

    def test_campaign_orchestrator_cannot_delete_repository_control_worktree(
        self,
    ) -> None:
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
        self.assertTrue(report["automatic_execution"])
        self.assertEqual(
            ["archive-paseo-worktree", "delete-merged-remote-branch"],
            [item["action"] for item in report["actions"]],
        )

    def test_stopped_inline_cleanup_accepts_a_stopped_actor_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            report = cleanup_report(
                root,
                event="stopped",
                execution_mode="inline",
                target=None,
                execution_overrides={
                    "worktree": str(root / "issue-10"),
                    "branch": "work/issue-10-inline",
                    "bound_agent_ids": [],
                    "branch_merged": False,
                },
            )

        self.assertEqual("eligible", report["status"])
        self.assertEqual(
            ["archive-paseo-worktree"], [a["action"] for a in report["actions"]]
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
                    "--target-kind",
                    "worker",
                    "--resource-kind",
                    "issue-worktree",
                    "--actor-agent-id",
                    "agent-orchestrator",
                    "--actor-role",
                    "orchestrator",
                    "--actor-worktree",
                    str(root / "control"),
                    "--actor-repository",
                    "owner/repo",
                    "--actor-campaign-id",
                    "campaign-20260718",
                    "--actor-dispatch-id",
                    "dispatch-issue-7",
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
                    "--target-repository",
                    "owner/repo",
                    "--target-campaign-id",
                    "campaign-20260718",
                    "--target-dispatch-id",
                    "dispatch-issue-7",
                    "--target-agent-idle",
                    "--target-worktree",
                    str(root / "issue-7"),
                    "--branch",
                    "work/issue-7-example",
                    "--execution-repository",
                    "owner/repo",
                    "--execution-campaign-id",
                    "campaign-20260718",
                    "--execution-dispatch-id",
                    "dispatch-issue-7",
                    "--terminal-event",
                    "COMPLETED",
                    "--terminal-signal-id",
                    "terminal-issue-7",
                    "--terminal-sender-agent-id",
                    "agent-orchestrator",
                    "--terminal-repository",
                    "owner/repo",
                    "--terminal-campaign-id",
                    "campaign-20260718",
                    "--terminal-dispatch-id",
                    "dispatch-issue-7",
                    "--terminal-read-back",
                    "--worktree-agent-id",
                    "agent-worker-7",
                    "--worktree-clean",
                    "--work-durable",
                    "--branch-merged",
                    "--unique-commits",
                    "0",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual("eligible", payload["policy"]["status"])
        self.assertTrue(payload["policy"]["automatic_execution"])

    def test_cleanup_cli_retires_campaign_agent_without_feature_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            result = subprocess.run(
                [
                    sys.executable,
                    str(ORCHESTRATOR_SCRIPTS / "execution_policy.py"),
                    "cleanup-plan",
                    "--event",
                    "campaign-closed",
                    "--seconds-since-event",
                    "20",
                    "--execution-mode",
                    "paseo-agent",
                    "--target-kind",
                    "campaign",
                    "--resource-kind",
                    "none",
                    "--actor-agent-id",
                    "agent-repository-coordinator",
                    "--actor-role",
                    "repository-coordinator",
                    "--actor-worktree",
                    str(root / "control"),
                    "--actor-repository",
                    "owner/repo",
                    "--actor-campaign-id",
                    "campaign-20260718",
                    "--actor-dispatch-id",
                    "campaign-control-1",
                    "--protected-control-worktree",
                    str(root / "control"),
                    "--target-agent-id",
                    "agent-campaign",
                    "--target-parent-agent-id",
                    "agent-repository-coordinator",
                    "--target-relationship",
                    "subagent",
                    "--target-role",
                    "orchestrator",
                    "--target-repository",
                    "owner/repo",
                    "--target-campaign-id",
                    "campaign-20260718",
                    "--target-dispatch-id",
                    "campaign-control-1",
                    "--target-agent-idle",
                    "--campaign-generation",
                    "legacy-v4.2",
                    "--campaign-generation-read-back",
                    "--execution-repository",
                    "owner/repo",
                    "--execution-campaign-id",
                    "campaign-20260718",
                    "--execution-dispatch-id",
                    "campaign-control-1",
                    "--terminal-event",
                    "CAMPAIGN_CLOSED",
                    "--terminal-signal-id",
                    "campaign-closed-1",
                    "--terminal-sender-agent-id",
                    "agent-campaign",
                    "--terminal-repository",
                    "owner/repo",
                    "--terminal-campaign-id",
                    "campaign-20260718",
                    "--terminal-dispatch-id",
                    "campaign-control-1",
                    "--terminal-read-back",
                    "--work-durable",
                    "--agent-only",
                    "--children-read-back",
                    "--children-repository",
                    "owner/repo",
                    "--children-campaign-id",
                    "campaign-20260718",
                    "--children-scope",
                    "direct-subagent",
                    "--unique-commits",
                    "0",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            [{"action": "archive-paseo-agent", "target": "agent-campaign"}],
            payload["policy"]["actions"],
        )
        self.assertEqual(
            "target-agent-archived", payload["policy"]["next_required_readback"]
        )

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
                    "--case-sensitive-paths",
                    "--integration-control-available",
                    "--integration-control-clean",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["policy"]["can_execute"])
        self.assertFalse(payload["policy"]["can_merge_dev"])
        self.assertIn("integration-lease-held-by-other", payload["policy"]["blockers"])

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
                {
                    "id": "ask-x",
                    "label": "Always Ask",
                    "description": "prompts for permission",
                },
                {
                    "id": "power-x",
                    "label": "Unrestricted",
                    "description": "skip all permission prompts",
                    "isUnattended": True,
                },
            ]
        )
        self.assertEqual("power-x", result["runtime_mode_id"])
        self.assertEqual("advertised-is-unattended", result["evidence"])

    def test_generic_execution_description_is_not_unattended_evidence(self) -> None:
        with self.assertRaisesRegex(PROVIDERS.ProviderPolicyError, "unattended"):
            PROVIDERS.resolve_highest_permission_mode(
                [
                    {
                        "id": "build",
                        "label": "Build",
                        "description": "Tools require confirmation for every action",
                    }
                ]
            )

    def test_configured_unattended_mode_must_exist_and_wins(self) -> None:
        modes = [
            {"id": "auto", "label": "Default Permissions"},
            {"id": "full-access", "label": "Full Access"},
        ]
        selected = PROVIDERS.resolve_highest_permission_mode(
            modes, configured_mode_id="full-access"
        )
        self.assertEqual("full-access", selected["runtime_mode_id"])
        self.assertEqual("configured-unattended-mode", selected["evidence"])
        with self.assertRaisesRegex(PROVIDERS.ProviderPolicyError, "unavailable"):
            PROVIDERS.resolve_highest_permission_mode(
                modes, configured_mode_id="missing"
            )

    def test_configured_interactive_mode_is_still_rejected(self) -> None:
        with self.assertRaisesRegex(PROVIDERS.ProviderPolicyError, "interactive"):
            PROVIDERS.resolve_highest_permission_mode(
                [
                    {
                        "id": "ask",
                        "label": "Always Ask",
                        "description": "prompts for permission",
                    }
                ],
                configured_mode_id="ask",
            )

    def test_duplicate_mode_ids_fail_closed(self) -> None:
        with self.assertRaisesRegex(PROVIDERS.ProviderPolicyError, "duplicate"):
            PROVIDERS.resolve_highest_permission_mode(
                [
                    {"id": "full", "isUnattended": True},
                    {"id": "full", "isUnattended": True},
                ]
            )

    def test_missing_unattended_mode_fails_closed(self) -> None:
        with self.assertRaisesRegex(PROVIDERS.ProviderPolicyError, "unattended"):
            PROVIDERS.resolve_highest_permission_mode(
                [
                    {
                        "id": "ask",
                        "label": "Always Ask",
                        "description": "prompts for permission",
                    }
                ]
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

    def test_post_material_returns_delivery_snapshot_from_publish_receipt(self) -> None:
        material = event(
            event_type="WORKER_DONE",
            signal_id="worker-done-material-post-1",
            evidence={
                "head_sha": "a" * 40,
                "verification": ["pytest: passed"],
                "changed_paths": ["src/policy.py"],
                "pr": "https://example.test/pr/7",
            },
            next_action="return candidate",
        )

        with mock.patch.dict(os.environ, {"PASEO_AGENT_ID": "agent-worker-7"}):
            receipt = self.protocol.post_material(
                "gwo-campaign-20260718",
                material,
                authority_scope="worker-dispatch",
                identity_receipts=[
                    identity_receipt(),
                    identity_receipt(
                        agent_id="agent-orchestrator",
                        role="orchestrator",
                        parent_agent_id="agent-repository-coordinator",
                    ),
                ],
            )

        self.assertEqual("message-1", receipt["message_id"])
        self.assertEqual(
            {
                "state": "pending",
                "room": "gwo-campaign-20260718",
                "event_type": "WORKER_DONE",
                "signal_id": "worker-done-material-post-1",
                "message_id": "message-1",
                "dispatch_id": "dispatch-issue-7",
                "issue": "#7",
                "sender_agent_id": "agent-worker-7",
                "recipient_agent_id": "agent-orchestrator",
                "authority_scope": "worker-dispatch",
                "identity_verified": True,
            },
            receipt["delivery"],
        )

    def test_post_material_requires_compiled_identity_receipts_before_publish(self) -> None:
        material = event()

        with mock.patch.dict(os.environ, {"PASEO_AGENT_ID": "agent-worker-7"}):
            with self.assertRaisesRegex(ROOM.RoomProtocolError, "identity receipts"):
                self.protocol.post_material(
                    "gwo-campaign-20260718",
                    material,
                    authority_scope="worker-dispatch",
                )

        self.assertEqual([], self.runner.messages)

    def test_post_material_requires_runtime_agent_identity_before_publish(self) -> None:
        receipts = [
            identity_receipt(),
            identity_receipt(
                agent_id="agent-orchestrator",
                role="orchestrator",
                parent_agent_id="agent-repository-coordinator",
            ),
        ]

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ROOM.RoomProtocolError, "PASEO_AGENT_ID"):
                self.protocol.post_material(
                    "gwo-campaign-20260718",
                    event(),
                    authority_scope="worker-dispatch",
                    identity_receipts=receipts,
                )

        self.assertEqual([], self.runner.messages)

    def test_campaign_completed_material_uses_control_authority_to_root(self) -> None:
        campaign = identity_receipt(
            agent_id="agent-orchestrator",
            role="orchestrator",
            parent_agent_id="agent-repository-coordinator",
            authority_kind="campaign-control",
        )
        root = identity_receipt(
            agent_id="agent-repository-coordinator",
            role="repository-coordinator",
            parent_agent_id=None,
            relationship="root",
        )
        completed = event(
            signal_id="campaign-completed-upward-1",
            event_type="COMPLETED",
            sender_agent_id="agent-orchestrator",
            recipient_agent_id="agent-repository-coordinator",
            evidence="candidate verified and ready for integration",
            next_action="request integration lease",
        )

        with mock.patch.dict(os.environ, {"PASEO_AGENT_ID": "agent-orchestrator"}):
            receipt = self.protocol.post_material(
                "gwo-campaign-20260718",
                completed,
                authority_scope="campaign-control",
                identity_receipts=[campaign, root],
            )

        self.assertEqual("campaign-control", receipt["delivery"]["authority_scope"])
        self.assertEqual(
            "agent-repository-coordinator",
            receipt["delivery"]["recipient_agent_id"],
        )

    def test_post_material_rejects_a_non_relative_recipient_before_publish(self) -> None:
        material = event(recipient_agent_id="agent-sibling")
        receipts = [
            identity_receipt(),
            identity_receipt(
                agent_id="agent-orchestrator",
                role="orchestrator",
                parent_agent_id="agent-repository-coordinator",
            ),
        ]

        with mock.patch.dict(os.environ, {"PASEO_AGENT_ID": "agent-worker-7"}):
            with self.assertRaisesRegex(ROOM.RoomProtocolError, "recipient"):
                self.protocol.post_material(
                    "gwo-campaign-20260718",
                    material,
                    authority_scope="worker-dispatch",
                    identity_receipts=receipts,
                )

        self.assertEqual([], self.runner.messages)

    def test_post_material_rejects_visibility_and_recipientless_events(self) -> None:
        heartbeat = event(
            event_type="HEARTBEAT",
            evidence={
                "phase": "implementation",
                "last_completed_step": "updated policy",
                "next_step": "run tests",
                "head_sha": None,
                "worktree_dirty": True,
                "blocking": False,
            },
        )
        recipientless = event(recipient_agent_id=None)

        with mock.patch.dict(os.environ, {"PASEO_AGENT_ID": "agent-worker-7"}):
            with self.assertRaisesRegex(ROOM.RoomProtocolError, "does not require"):
                self.protocol.post_material(
                    "gwo-campaign-20260718",
                    heartbeat,
                    authority_scope="worker-dispatch",
                )
            with self.assertRaisesRegex(ROOM.RoomProtocolError, "requires recipient"):
                self.protocol.post_material(
                    "gwo-campaign-20260718",
                    recipientless,
                    authority_scope="worker-dispatch",
                )
        self.assertEqual([], self.runner.messages)

    def test_sender_identity_mismatch_fails_before_publish(self) -> None:
        with mock.patch.dict(os.environ, {"PASEO_AGENT_ID": "other-agent"}):
            with self.assertRaisesRegex(ROOM.RoomProtocolError, "does not match"):
                self.protocol.post("gwo-campaign-20260718", event())
        self.assertEqual([], self.runner.messages)

    def test_material_delivery_replay_separates_business_event_and_ack_state(self) -> None:
        source_message_id = "33333333-3333-4333-8333-333333333331"
        source = event(
            signal_id="worker-done-delivery-1",
            sequence=1,
            event_type="WORKER_DONE",
            evidence={
                "head_sha": "a" * 40,
                "verification": ["pytest: passed"],
                "changed_paths": ["src/policy.py"],
                "pr": "https://example.test/pr/7",
            },
            next_action="return candidate",
        )
        snapshot = {
            "schema_version": 1,
            "repository": "owner/repo",
            "campaign_id": "campaign-20260718",
            "delivery": {
                "state": "pending",
                "room": "gwo-campaign-20260718",
                "event_type": "WORKER_DONE",
                "signal_id": source["signal_id"],
                "message_id": source_message_id,
                "dispatch_id": source["dispatch_id"],
                "issue": source["issue"],
                "sender_agent_id": source["sender_agent_id"],
                "recipient_agent_id": source["recipient_agent_id"],
                "authority_scope": "worker-dispatch",
                "identity_verified": True,
            },
            "sender": {
                "agent_id": "agent-worker-7",
                "status": "running",
                "archived": False,
                "parent_agent_id": "agent-orchestrator",
                "relationship": "subagent",
                "labels": {
                    "repository": "owner/repo",
                    "campaign_id": "campaign-20260718",
                    "dispatch_id": "dispatch-issue-7",
                    "role": "implementation",
                },
                "read_back": True,
            },
            "recipient": {
                "agent_id": "agent-orchestrator",
                "status": "idle",
                "archived": False,
                "parent_agent_id": "agent-repository-coordinator",
                "relationship": "subagent",
                "labels": {
                    "repository": "owner/repo",
                    "campaign_id": "campaign-20260718",
                    "role": "orchestrator",
                },
                "read_back": True,
            },
        }
        snapshot["next_sequence"] = 2
        snapshot["wake_result"] = {
            "agent_id": "agent-orchestrator",
            "accepted": True,
        }
        wake = MATERIAL.wake_receipt_event_plan(snapshot)["event"]
        snapshot["next_sequence"] = 1
        ack = MATERIAL.ack_event_plan(snapshot)["event"]
        for message_id, payload, author in (
            (source_message_id, source, "agent-worker-7"),
            ("33333333-3333-4333-8333-333333333332", wake, "agent-worker-7"),
            ("33333333-3333-4333-8333-333333333333", ack, "agent-orchestrator"),
        ):
            self.runner.messages.append(
                {"id": message_id, "body": json.dumps(payload), "author": author}
            )

        replay = self.protocol.replay(
            "gwo-campaign-20260718",
            identity_receipts=[
                identity_receipt(),
                identity_receipt(
                    agent_id="agent-orchestrator",
                    role="orchestrator",
                    parent_agent_id="agent-repository-coordinator",
                ),
            ],
        )

        self.assertEqual(["WORKER_DONE"], [item["event_type"] for item in replay["events"]])
        self.assertEqual(
            ["DELIVERY_WAKE", "DELIVERY_ACK"],
            [item["event_type"] for item in replay["delivery_events"]],
        )
        self.assertEqual("acknowledged", replay["deliveries"][0]["state"])
        self.assertEqual(source_message_id, replay["deliveries"][0]["message_id"])
        self.assertEqual([], replay["blocked_dispatches"])

    def test_repository_coordinator_start_delivery_is_acked_by_campaign(self) -> None:
        root_id = "agent-repository-coordinator"
        campaign_id = "agent-orchestrator"
        source_message_id = "44444444-4444-4444-8444-444444444441"
        source = event(
            signal_id="campaign-start-delivery-1",
            sequence=1,
            event_type="START",
            sender_agent_id=root_id,
            recipient_agent_id=campaign_id,
            evidence="campaign admission read back",
            next_action="start campaign reconciliation",
        )
        snapshot = {
            "schema_version": 1,
            "repository": "owner/repo",
            "campaign_id": "campaign-20260718",
            "delivery": {
                "state": "pending",
                "room": "gwo-campaign-20260718",
                "event_type": "START",
                "signal_id": source["signal_id"],
                "message_id": source_message_id,
                "dispatch_id": source["dispatch_id"],
                "issue": source["issue"],
                "sender_agent_id": root_id,
                "recipient_agent_id": campaign_id,
                "authority_scope": "campaign-admission",
                "identity_verified": True,
            },
            "sender": {
                "agent_id": root_id,
                "status": "running",
                "archived": False,
                "parent_agent_id": None,
                "relationship": "root",
                "labels": {
                    "repository": "owner/repo",
                    "role": "repository-coordinator",
                },
                "read_back": True,
            },
            "recipient": {
                "agent_id": campaign_id,
                "status": "idle",
                "archived": False,
                "parent_agent_id": root_id,
                "relationship": "subagent",
                "labels": {
                    "repository": "owner/repo",
                    "campaign_id": "campaign-20260718",
                    "role": "orchestrator",
                },
                "read_back": True,
            },
        }
        snapshot["next_sequence"] = 2
        snapshot["wake_result"] = {"agent_id": campaign_id, "accepted": True}
        wake = MATERIAL.wake_receipt_event_plan(snapshot)["event"]
        snapshot["next_sequence"] = 1
        ack = MATERIAL.ack_event_plan(snapshot)["event"]
        for message_id, payload, author in (
            (source_message_id, source, root_id),
            ("44444444-4444-4444-8444-444444444442", wake, root_id),
            ("44444444-4444-4444-8444-444444444443", ack, campaign_id),
        ):
            self.runner.messages.append(
                {"id": message_id, "body": json.dumps(payload), "author": author}
            )

        replay = self.protocol.replay(
            "gwo-campaign-20260718",
            identity_receipts=[
                identity_receipt(
                    agent_id=root_id,
                    role="repository-coordinator",
                    parent_agent_id=None,
                    relationship="root",
                ),
                identity_receipt(
                    agent_id=campaign_id,
                    role="orchestrator",
                    parent_agent_id=root_id,
                    authority_kind="campaign-control",
                ),
            ],
        )

        self.assertEqual("acknowledged", replay["deliveries"][0]["state"])
        self.assertEqual([], replay["blocked_dispatches"])
        self.assertEqual([], replay["rejected"])

    def test_invalid_delivery_receipt_does_not_poison_valid_business_event(self) -> None:
        source_message_id = "66666666-6666-4666-8666-666666666661"
        source = event(
            signal_id="worker-done-nonpoison-1",
            event_type="WORKER_DONE",
            evidence={
                "head_sha": "a" * 40,
                "verification": ["pytest: passed"],
                "changed_paths": ["src/policy.py"],
                "pr": "https://example.test/pr/7",
            },
        )
        snapshot = {
            "schema_version": 1,
            "repository": "owner/repo",
            "campaign_id": "campaign-20260718",
            "delivery": {
                "state": "pending",
                "room": "gwo-campaign-20260718",
                "event_type": "WORKER_DONE",
                "signal_id": source["signal_id"],
                "message_id": source_message_id,
                "dispatch_id": source["dispatch_id"],
                "issue": source["issue"],
                "sender_agent_id": source["sender_agent_id"],
                "recipient_agent_id": source["recipient_agent_id"],
                "authority_scope": "worker-dispatch",
                "identity_verified": True,
            },
            "sender": {
                "agent_id": "agent-worker-7",
                "status": "running",
                "archived": False,
                "parent_agent_id": "agent-orchestrator",
                "relationship": "subagent",
                "labels": {
                    "repository": "owner/repo",
                    "campaign_id": "campaign-20260718",
                    "dispatch_id": "dispatch-issue-7",
                    "role": "implementation",
                },
                "read_back": True,
            },
            "recipient": {
                "agent_id": "agent-orchestrator",
                "status": "idle",
                "archived": False,
                "parent_agent_id": "agent-repository-coordinator",
                "relationship": "subagent",
                "labels": {
                    "repository": "owner/repo",
                    "campaign_id": "campaign-20260718",
                    "role": "orchestrator",
                },
                "read_back": True,
            },
            "next_sequence": 1,
        }
        forged_ack = MATERIAL.ack_event_plan(snapshot)["event"]
        forged_ack["evidence"]["source_message_id"] = (
            "66666666-6666-4666-8666-666666666669"
        )
        for message_id, payload, author in (
            (source_message_id, source, "agent-worker-7"),
            ("66666666-6666-4666-8666-666666666662", forged_ack, "agent-orchestrator"),
        ):
            self.runner.messages.append(
                {"id": message_id, "body": json.dumps(payload), "author": author}
            )

        replay = self.protocol.replay(
            "gwo-campaign-20260718",
            identity_receipts=[
                identity_receipt(),
                identity_receipt(
                    agent_id="agent-orchestrator",
                    role="orchestrator",
                    parent_agent_id="agent-repository-coordinator",
                ),
            ],
        )

        self.assertEqual(["WORKER_DONE"], [item["event_type"] for item in replay["events"]])
        self.assertEqual("pending", replay["deliveries"][0]["state"])
        self.assertEqual([], replay["blocked_dispatches"])
        self.assertEqual("delivery-correlation-invalid", replay["rejected"][0]["reason"])

    def test_missing_runtime_agent_identity_fails_before_publish(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ROOM.RoomProtocolError, "PASEO_AGENT_ID"):
                self.protocol.post("gwo-campaign-20260718", event())
        self.assertEqual([], self.runner.messages)

    def test_replay_deduplicates_and_rejects_conflicts(self) -> None:
        original = event()
        duplicate = event()
        conflict = event(evidence="different evidence")
        invalid = {"id": "message-4", "body": "human note", "author": "manual"}
        for index, payload in enumerate((original, duplicate, conflict), start=1):
            self.runner.messages.append(
                {
                    "id": f"message-{index}",
                    "body": json.dumps(payload),
                    "author": "agent-worker-7",
                }
            )
        self.runner.messages.append(invalid)
        replay = self.protocol.replay(
            "gwo-campaign-20260718", identity_receipts=[identity_receipt()]
        )
        self.assertEqual([], replay["events"])
        self.assertEqual([], replay["deliveries"])
        self.assertEqual(["dispatch-issue-7"], replay["blocked_dispatches"])
        self.assertEqual(
            ["duplicate-signal-conflict", "event-must-be-object"],
            [item["reason"] for item in replay["rejected"]],
        )
        self.assertEqual("dispatch-issue-7", replay["rejected"][0]["dispatch_id"])

    def test_replay_rejects_a_foreign_campaign_event(self) -> None:
        self.runner.messages.append(
            {
                "id": "message-1",
                "body": json.dumps(
                    event(campaign_id="campaign-foreign", signal_id="foreign-1")
                ),
                "author": "agent-worker-7",
            }
        )

        replay = self.protocol.replay(
            "gwo-campaign-20260718", identity_receipts=[identity_receipt()]
        )

        self.assertEqual([], replay["events"])
        self.assertEqual("room-campaign-mismatch", replay["rejected"][0]["reason"])

    def test_replay_rejects_nonmonotonic_sender_dispatch_sequence(self) -> None:
        first = event(signal_id="progress-2", sequence=2, event_type="PROGRESS")
        stale = event(signal_id="progress-1", sequence=1, event_type="PROGRESS")
        for index, payload in enumerate((first, stale), start=1):
            self.runner.messages.append(
                {
                    "id": f"message-{index}",
                    "body": json.dumps(payload),
                    "author": "agent-worker-7",
                }
            )

        replay = self.protocol.replay(
            "gwo-campaign-20260718", identity_receipts=[identity_receipt()]
        )

        self.assertEqual([], replay["events"])
        self.assertEqual("nonmonotonic-sequence", replay["rejected"][0]["reason"])
        self.assertEqual(["dispatch-issue-7"], replay["blocked_dispatches"])

    def test_heartbeat_payload_and_terminal_order_are_enforced(self) -> None:
        heartbeat = event(
            signal_id="heartbeat-1",
            sequence=2,
            event_type="HEARTBEAT",
            evidence={
                "phase": "implementation",
                "last_completed_step": "updated policy",
                "next_step": "run tests",
                "head_sha": None,
                "worktree_dirty": True,
                "blocking": False,
            },
        )
        done = event(
            signal_id="done-1",
            sequence=3,
            event_type="WORKER_DONE",
            evidence={
                "head_sha": "a" * 40,
                "verification": ["pytest: passed"],
                "changed_paths": ["src/policy.py"],
                "pr": "https://example.test/pr/7",
            },
        )
        late = event(
            signal_id="heartbeat-2",
            sequence=4,
            event_type="HEARTBEAT",
            evidence=heartbeat["evidence"],
        )
        for index, payload in enumerate((heartbeat, done, late), start=1):
            self.runner.messages.append(
                {
                    "id": f"message-{index}",
                    "body": json.dumps(payload),
                    "author": "agent-worker-7",
                }
            )

        replay = self.protocol.replay(
            "gwo-campaign-20260718", identity_receipts=[identity_receipt()]
        )

        self.assertEqual(
            ["HEARTBEAT", "WORKER_DONE"], [e["event_type"] for e in replay["events"]]
        )
        self.assertEqual("heartbeat-after-terminal", replay["rejected"][0]["reason"])

    def test_invalid_heartbeat_and_uncorrelated_reply_fail_validation(self) -> None:
        heartbeat_errors = ROOM.validate_event(
            event(event_type="HEARTBEAT", evidence="still working")
        )
        reply_errors = ROOM.validate_event(
            event(event_type="REPLY", evidence={"answer": "continue"})
        )

        self.assertIn("invalid-heartbeat-evidence", heartbeat_errors)
        self.assertIn("reply-requires-in-reply-to", reply_errors)

    def review_result(
        self,
        *,
        axis: str,
        sender: str,
        signal_id: str,
        candidate_sha: str = "b" * 40,
        base_sha: str = "a" * 40,
        diff_sha256: str = "c" * 64,
        acceptance_sha256: str = "d" * 64,
        review_round: int = 1,
        scope: str = "full",
        previous_candidate_sha: str | None = None,
        sequence: int = 1,
    ) -> dict:
        return event(
            signal_id=signal_id,
            sequence=sequence,
            event_type="REVIEW_RESULT",
            sender_agent_id=sender,
            evidence={
                "axis": axis,
                "candidate_sha": candidate_sha,
                "base_sha": base_sha,
                "diff_sha256": diff_sha256,
                "acceptance_sha256": acceptance_sha256,
                "review_round": review_round,
                "scope": scope,
                "previous_candidate_sha": previous_candidate_sha,
                "verdict": "pass",
                "findings": [],
            },
            next_action="return verdict to Campaign",
        )

    def test_review_result_requires_locked_axis_sha_diff_and_acceptance(self) -> None:
        valid = self.review_result(
            axis="spec", sender="agent-spec-reviewer", signal_id="review-spec-1"
        )
        invalid = self.review_result(
            axis="spec", sender="agent-spec-reviewer", signal_id="review-spec-2"
        )
        invalid["evidence"].pop("acceptance_sha256")

        self.assertEqual([], ROOM.validate_event(valid))
        self.assertIn("invalid-review-result-evidence", ROOM.validate_event(invalid))

    def test_two_review_axes_complete_only_for_the_same_locked_candidate(self) -> None:
        spec = self.review_result(
            axis="spec", sender="agent-spec-reviewer", signal_id="review-spec-1"
        )
        quality = self.review_result(
            axis="quality",
            sender="agent-quality-reviewer",
            signal_id="review-quality-1",
        )
        for index, payload in enumerate((spec, quality), start=1):
            self.runner.messages.append(
                {
                    "id": f"message-{index}",
                    "body": json.dumps(payload),
                    "author": payload["sender_agent_id"],
                }
            )
        receipts = review_identity_receipts("spec", "quality")

        replay = self.protocol.replay(
            "gwo-campaign-20260718",
            identity_receipts=receipts,
            review_locks=[review_lock_receipt()],
        )

        self.assertEqual([], replay["blocked_dispatches"])
        self.assertEqual("complete", replay["review_pairs"]["dispatch-issue-7"]["status"])
        self.assertEqual("b" * 40, replay["review_pairs"]["dispatch-issue-7"]["candidate_sha"])

    def test_review_pair_mismatch_or_duplicate_axis_blocks_final_verdict(self) -> None:
        spec = self.review_result(
            axis="spec", sender="agent-spec-reviewer", signal_id="review-spec-1"
        )
        duplicate_spec = self.review_result(
            axis="spec",
            sender="agent-spec-reviewer",
            signal_id="review-spec-duplicate",
            sequence=2,
        )
        quality = self.review_result(
            axis="quality",
            sender="agent-quality-reviewer",
            signal_id="review-quality-1",
            candidate_sha="e" * 40,
        )
        for index, payload in enumerate((spec, duplicate_spec, quality), start=1):
            self.runner.messages.append(
                {
                    "id": f"message-{index}",
                    "body": json.dumps(payload),
                    "author": payload["sender_agent_id"],
                }
            )
        receipts = review_identity_receipts("spec", "quality")
        receipts[1]["assignment"]["lock"]["candidate_sha"] = "e" * 40
        replay = self.protocol.replay(
            "gwo-campaign-20260718",
            identity_receipts=receipts,
            review_locks=[review_lock_receipt()],
        )

        reasons = ",".join(item["reason"] for item in replay["rejected"])
        self.assertIn("duplicate-review-axis", reasons)
        self.assertIn("review-lock-receipt-mismatch", reasons)
        self.assertEqual(["dispatch-issue-7"], replay["blocked_dispatches"])

    def test_review_receipt_axis_must_match_runtime_label(self) -> None:
        spec = self.review_result(
            axis="spec", sender="agent-spec-reviewer", signal_id="review-spec-1"
        )
        self.runner.messages.append(
            {
                "id": "message-1",
                "body": json.dumps(spec),
                "author": "agent-spec-reviewer",
            }
        )

        replay = self.protocol.replay(
            "gwo-campaign-20260718",
            identity_receipts=review_identity_receipts(
                ("agent-spec-reviewer", "quality")
            ),
            review_locks=[review_lock_receipt()],
        )

        self.assertEqual([], replay["events"])
        self.assertIn("identity-receipt-review-axis-mismatch", replay["rejected"][0]["reason"])

    def test_review_pair_cannot_self_authorize_a_forged_shared_lock(self) -> None:
        spec = self.review_result(
            axis="spec",
            sender="agent-spec-reviewer",
            signal_id="review-spec-forged",
            candidate_sha="e" * 40,
        )
        quality = self.review_result(
            axis="quality",
            sender="agent-quality-reviewer",
            signal_id="review-quality-forged",
            candidate_sha="e" * 40,
        )
        for index, payload in enumerate((spec, quality), start=1):
            self.runner.messages.append(
                {
                    "id": f"message-{index}",
                    "body": json.dumps(payload),
                    "author": payload["sender_agent_id"],
                }
            )

        receipts = review_identity_receipts("spec", "quality")
        for receipt in receipts:
            if receipt["role"] != "review":
                continue
            receipt["assignment"]["lock"]["candidate_sha"] = "e" * 40
        replay = self.protocol.replay(
            "gwo-campaign-20260718",
            identity_receipts=receipts,
            review_locks=[review_lock_receipt(candidate_sha="b" * 40)],
        )

        self.assertEqual([], replay["events"])
        self.assertEqual(["dispatch-issue-7"], replay["blocked_dispatches"])
        self.assertTrue(
            all("review-lock-receipt-mismatch" in item["reason"] for item in replay["rejected"])
        )

    def test_delta_review_requires_prior_candidate_and_both_axes(self) -> None:
        invalid = self.review_result(
            axis="spec",
            sender="agent-spec-reviewer",
            signal_id="review-spec-delta",
            review_round=2,
            scope="delta",
        )
        valid = self.review_result(
            axis="spec",
            sender="agent-spec-reviewer",
            signal_id="review-spec-delta-valid",
            review_round=2,
            scope="delta",
            previous_candidate_sha="9" * 40,
        )

        self.assertIn("invalid-review-result-evidence", ROOM.validate_event(invalid))
        self.assertEqual([], ROOM.validate_event(valid))

    def test_delta_review_lock_requires_the_exact_prior_readback(self) -> None:
        current = review_lock_receipt(
            candidate_sha="e" * 40,
            review_round=2,
            scope="delta",
            previous_candidate_sha="b" * 40,
        )
        with self.assertRaisesRegex(ROOM.RoomProtocolError, "lineage"):
            ROOM._review_lock_lookup([current])

        lookup = ROOM._review_lock_lookup([review_lock_receipt(), current])

        self.assertEqual(2, len(lookup))

    def test_replay_requires_exact_runtime_identity_receipts(self) -> None:
        self.runner.messages.append(
            {
                "id": "message-1",
                "body": json.dumps(event()),
                "author": "agent-worker-7",
            }
        )

        missing = self.protocol.replay("gwo-campaign-20260718")
        mismatched = self.protocol.replay(
            "gwo-campaign-20260718",
            identity_receipts=[identity_receipt(dispatch_id="dispatch-other")],
        )

        self.assertEqual([], missing["events"])
        self.assertEqual("identity-receipt-missing", missing["rejected"][0]["reason"])
        self.assertEqual([], mismatched["events"])
        self.assertEqual(
            "identity-receipt-missing", mismatched["rejected"][0]["reason"]
        )

    def test_worker_scoped_replay_ignores_control_history_and_compiles_receipts(
        self,
    ) -> None:
        unrelated = event(
            signal_id="campaign-checkpoint-1",
            campaign_id="campaign-20260718",
            dispatch_id="campaign-20260718",
            event_type="CHECKPOINT",
            sender_agent_id="agent-repository-coordinator",
            sequence=1,
        )
        legacy_unrelated = {
            "campaign_id": "campaign-20260718",
            "dispatch_id": "campaign-20260718",
            "event_type": "CHECKPOINT",
            "sender_agent_id": "legacy-coordinator",
        }
        start = event(
            signal_id="start-worker-7",
            event_type="START",
            sender_agent_id="agent-orchestrator",
            recipient_agent_id="agent-worker-7",
            sequence=1,
        )
        review_result = self.review_result(
            axis="spec",
            sender="agent-spec-reviewer",
            signal_id="review-spec-worker-view",
        )
        review_fix = event(
            signal_id="start-review-fix-worker-7",
            event_type="START",
            sender_agent_id="agent-orchestrator",
            recipient_agent_id="agent-worker-7",
            sequence=2,
        )
        for index, payload in enumerate(
            (unrelated, legacy_unrelated, start, review_result, review_fix), start=1
        ):
            self.runner.messages.append(
                {
                    "id": f"message-{index}",
                    "body": json.dumps(payload),
                    "author": payload["sender_agent_id"],
                }
            )
        plan = ROOM.identity_receipt_plan(
            {
                "schema_version": 1,
                "repository": "owner/repo",
                "campaign_id": "campaign-20260718",
                "dispatch_id": "dispatch-issue-7",
                "authority_scope": "worker-dispatch",
                "agent_readbacks": [
                    {
                        "agent_id": "agent-orchestrator",
                        "parent_agent_id": "agent-repository-coordinator",
                        "relationship": "subagent",
                        "labels": {
                            "repository": "owner/repo",
                            "campaign_id": "campaign-20260718",
                            "role": "orchestrator",
                        },
                        "read_back": True,
                    },
                    {
                        "agent_id": "agent-worker-7",
                        "parent_agent_id": "agent-orchestrator",
                        "relationship": "subagent",
                        "labels": {
                            "repository": "owner/repo",
                            "campaign_id": "campaign-20260718",
                            "dispatch_id": "dispatch-issue-7",
                            "role": "implementation",
                        },
                        "read_back": True,
                    },
                ],
            }
        )

        replay = self.protocol.replay(
            "gwo-campaign-20260718",
            identity_receipts=plan["receipts"],
            dispatch_id="dispatch-issue-7",
            consumer_role="worker",
        )

        self.assertEqual(
            ["START", "START"], [item["event_type"] for item in replay["events"]]
        )
        self.assertEqual([], replay["rejected"])
        self.assertEqual([], replay["blocked_dispatches"])

        with self.assertRaisesRegex(ROOM.RoomProtocolError, "Campaign parent"):
            ROOM.identity_receipt_plan(
                {
                    "schema_version": 1,
                    "repository": "owner/repo",
                    "campaign_id": "campaign-20260718",
                    "dispatch_id": "dispatch-issue-7",
                    "authority_scope": "worker-dispatch",
                    "agent_readbacks": [
                        {
                            "agent_id": "agent-worker-7",
                            "parent_agent_id": "foreign-parent",
                            "relationship": "subagent",
                            "labels": {
                                "repository": "owner/repo",
                                "campaign_id": "campaign-20260718",
                                "dispatch_id": "dispatch-issue-7",
                                "role": "implementation",
                            },
                            "read_back": True,
                        }
                    ],
                }
            )

    def test_identity_plan_cli_writes_a_replay_ready_receipt_array(self) -> None:
        snapshot = {
            "schema_version": 1,
            "repository": "owner/repo",
            "campaign_id": "campaign-20260718",
            "dispatch_id": "dispatch-issue-7",
            "authority_scope": "worker-dispatch",
            "agent_readbacks": [
                {
                    "agent_id": "agent-orchestrator",
                    "parent_agent_id": "agent-repository-coordinator",
                    "relationship": "subagent",
                    "labels": {
                        "repository": "owner/repo",
                        "campaign_id": "campaign-20260718",
                        "role": "orchestrator",
                    },
                    "read_back": True,
                },
                {
                    "agent_id": "agent-worker-7",
                    "parent_agent_id": "agent-orchestrator",
                    "relationship": "subagent",
                    "labels": {
                        "repository": "owner/repo",
                        "campaign_id": "campaign-20260718",
                        "dispatch_id": "dispatch-issue-7",
                        "role": "implementation",
                    },
                    "read_back": True,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "readbacks.json"
            output = Path(temporary) / "receipts.json"
            source.write_text(json.dumps(snapshot), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ORCHESTRATOR_SCRIPTS / "paseo_room.py"),
                    "identity-plan",
                    "--snapshot",
                    str(source),
                    "--receipts-output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            receipts = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIsInstance(receipts, list)
        self.assertEqual(
            {"agent-orchestrator", "agent-worker-7"},
            {item["agent_id"] for item in receipts},
        )

    def test_identity_plan_supports_the_fixed_reusable_review_pair(self) -> None:
        lock = {
            "dispatch_id": "dispatch-issue-7",
            "candidate_sha": "b" * 40,
            "base_sha": "a" * 40,
            "diff_sha256": "c" * 64,
            "acceptance_sha256": "d" * 64,
            "review_round": 1,
            "scope": "full",
            "previous_candidate_sha": None,
        }
        readbacks = [
            {
                "agent_id": "agent-orchestrator",
                "parent_agent_id": "agent-repository-coordinator",
                "relationship": "subagent",
                "labels": {
                    "repository": "owner/repo",
                    "campaign_id": "campaign-20260718",
                    "role": "orchestrator",
                },
                "read_back": True,
            },
            *[
                {
                    "agent_id": f"agent-{axis}-reviewer",
                    "parent_agent_id": "agent-orchestrator",
                    "relationship": "subagent",
                    "labels": {
                        "repository": "owner/repo",
                        "campaign_id": "campaign-20260718",
                        "role": "review",
                        "review_axis": axis,
                    },
                    "read_back": True,
                }
                for axis in ("spec", "quality")
            ],
        ]
        assignments = [
            {
                "agent_id": f"agent-{axis}-reviewer",
                "campaign_id": "campaign-20260718",
                "review_axis": axis,
                "campaign_parent_agent_id": "agent-orchestrator",
                "lock": lock,
                "review_lock_read_back": True,
                "read_back": True,
            }
            for axis in ("spec", "quality")
        ]
        plan = ROOM.identity_receipt_plan(
            {
                "schema_version": 1,
                "repository": "owner/repo",
                "campaign_id": "campaign-20260718",
                "dispatch_id": "dispatch-issue-7",
                "authority_scope": "review-dispatch",
                "agent_readbacks": readbacks,
                "review_assignments": assignments,
            }
        )
        for axis in ("spec", "quality"):
            payload = self.review_result(
                axis=axis,
                sender=f"agent-{axis}-reviewer",
                signal_id=f"review-{axis}-identity-plan",
            )
            self.runner.messages.append(
                {
                    "id": f"message-{axis}",
                    "body": json.dumps(payload),
                    "author": payload["sender_agent_id"],
                }
            )

        replay = self.protocol.replay(
            "gwo-campaign-20260718",
            identity_receipts=plan["receipts"],
            review_locks=[review_lock_receipt()],
        )

        reviewer_receipts = [
            item for item in plan["receipts"] if item["role"] == "review"
        ]
        self.assertEqual(2, len(reviewer_receipts))
        self.assertTrue(
            all("dispatch_id" not in item["labels"] for item in reviewer_receipts)
        )
        self.assertEqual("complete", replay["review_pairs"]["dispatch-issue-7"]["status"])
        self.assertEqual([], replay["blocked_dispatches"])

        poisoned = {**assignments[0], "campaign_parent_agent_id": "foreign-parent"}
        with self.assertRaisesRegex(ROOM.RoomProtocolError, "review assignment"):
            ROOM.identity_receipt_plan(
                {
                    "schema_version": 1,
                    "repository": "owner/repo",
                    "campaign_id": "campaign-20260718",
                    "dispatch_id": "dispatch-issue-7",
                    "authority_scope": "review-dispatch",
                    "agent_readbacks": readbacks,
                    "review_assignments": [poisoned, assignments[1]],
                }
            )
        wrong_axis = {**assignments[0], "review_axis": "quality"}
        with self.assertRaisesRegex(ROOM.RoomProtocolError, "review assignment"):
            ROOM.identity_receipt_plan(
                {
                    "schema_version": 1,
                    "repository": "owner/repo",
                    "campaign_id": "campaign-20260718",
                    "dispatch_id": "dispatch-issue-7",
                    "authority_scope": "review-dispatch",
                    "agent_readbacks": readbacks,
                    "review_assignments": [wrong_axis, assignments[1]],
                }
            )
        orphan_readbacks = json.loads(json.dumps(readbacks[1:]))
        for item in orphan_readbacks:
            item["parent_agent_id"] = "foreign-parent"
        orphan_assignments = json.loads(json.dumps(assignments))
        for item in orphan_assignments:
            item["campaign_parent_agent_id"] = "foreign-parent"
        with self.assertRaisesRegex(ROOM.RoomProtocolError, "review assignment"):
            ROOM.identity_receipt_plan(
                {
                    "schema_version": 1,
                    "repository": "owner/repo",
                    "campaign_id": "campaign-20260718",
                    "dispatch_id": "dispatch-issue-7",
                    "authority_scope": "review-dispatch",
                    "agent_readbacks": orphan_readbacks,
                    "review_assignments": orphan_assignments,
                }
            )

    def test_identity_plan_uses_explicit_campaign_control_scope(self) -> None:
        plan = ROOM.identity_receipt_plan(
            {
                "schema_version": 1,
                "repository": "owner/repo",
                "campaign_id": "campaign-20260718",
                "dispatch_id": "campaign-control-1",
                "authority_scope": "campaign-control",
                "agent_readbacks": [
                    {
                        "agent_id": "agent-repository-coordinator",
                        "parent_agent_id": None,
                        "relationship": "root",
                        "labels": {
                            "repository": "owner/repo",
                            "role": "repository-coordinator",
                        },
                        "read_back": True,
                    },
                    {
                        "agent_id": "agent-orchestrator",
                        "parent_agent_id": "agent-repository-coordinator",
                        "relationship": "subagent",
                        "labels": {
                            "repository": "owner/repo",
                            "campaign_id": "campaign-20260718",
                            "role": "orchestrator",
                        },
                        "read_back": True,
                    },
                ],
            }
        )
        checkpoint = event(
            signal_id="campaign-checkpoint-control-1",
            dispatch_id="campaign-control-1",
            event_type="CHECKPOINT",
            sender_agent_id="agent-orchestrator",
        )
        self.runner.messages.append(
            {
                "id": "message-control",
                "body": json.dumps(checkpoint),
                "author": "agent-orchestrator",
            }
        )

        replay = self.protocol.replay(
            "gwo-campaign-20260718", identity_receipts=plan["receipts"]
        )

        orchestrator_receipt = next(
            item for item in plan["receipts"] if item["role"] == "orchestrator"
        )
        self.assertEqual("campaign-control", orchestrator_receipt["authority"]["kind"])
        self.assertEqual(["CHECKPOINT"], [item["event_type"] for item in replay["events"]])

        with self.assertRaisesRegex(ROOM.RoomProtocolError, "root Coordinator parent"):
            ROOM.identity_receipt_plan(
                {
                    "schema_version": 1,
                    "repository": "owner/repo",
                    "campaign_id": "campaign-20260718",
                    "dispatch_id": "campaign-control-1",
                    "authority_scope": "campaign-control",
                    "agent_readbacks": [
                        {
                            "agent_id": "agent-orchestrator",
                            "parent_agent_id": "foreign-parent",
                            "relationship": "subagent",
                            "labels": {
                                "repository": "owner/repo",
                                "campaign_id": "campaign-20260718",
                                "role": "orchestrator",
                            },
                            "read_back": True,
                        }
                    ],
                }
            )

    def test_replay_rejects_message_author_spoofing(self) -> None:
        self.runner.messages.append(
            {"id": "message-1", "body": json.dumps(event()), "author": "other-agent"}
        )

        replay = self.protocol.replay(
            "gwo-campaign-20260718", identity_receipts=[identity_receipt()]
        )

        self.assertEqual([], replay["events"])
        self.assertEqual("message-author-mismatch", replay["rejected"][0]["reason"])

    def test_reply_must_correlate_to_the_accepted_ask(self) -> None:
        ask = event(
            signal_id="ask-1",
            event_type="ASK",
            evidence={"question": "Which API?", "blocking": True},
        )
        reply = event(
            signal_id="reply-1",
            sender_agent_id="agent-orchestrator",
            recipient_agent_id="agent-worker-7",
            event_type="REPLY",
            in_reply_to="ask-1",
            evidence={"answer": "Use v2"},
        )
        for index, payload in enumerate((ask, reply), start=1):
            self.runner.messages.append(
                {
                    "id": f"message-{index}",
                    "body": json.dumps(payload),
                    "author": payload["sender_agent_id"],
                }
            )
        receipts = [
            identity_receipt(),
            identity_receipt(
                agent_id="agent-orchestrator",
                role="orchestrator",
                parent_agent_id="agent-repository-coordinator",
            ),
        ]

        replay = self.protocol.replay(
            "gwo-campaign-20260718", identity_receipts=receipts
        )

        self.assertEqual(
            ["ASK", "REPLY"], [item["event_type"] for item in replay["events"]]
        )
        self.assertEqual([], replay["blocked_dispatches"])

    def test_uncorrelated_reply_blocks_the_dispatch(self) -> None:
        reply = event(
            signal_id="reply-1",
            sender_agent_id="agent-orchestrator",
            recipient_agent_id="agent-worker-7",
            event_type="REPLY",
            in_reply_to="missing-ask",
            evidence={"answer": "Use v2"},
        )
        self.runner.messages.append(
            {
                "id": "message-1",
                "body": json.dumps(reply),
                "author": "agent-orchestrator",
            }
        )

        replay = self.protocol.replay(
            "gwo-campaign-20260718",
            identity_receipts=[
                identity_receipt(
                    agent_id="agent-orchestrator",
                    role="orchestrator",
                    parent_agent_id="agent-repository-coordinator",
                )
            ],
        )

        self.assertEqual([], replay["events"])
        self.assertEqual("reply-correlation-invalid", replay["rejected"][0]["reason"])
        self.assertEqual(["dispatch-issue-7"], replay["blocked_dispatches"])

    def test_room_event_authority_is_role_enforced(self) -> None:
        worker_checkpoint = event(
            signal_id="checkpoint-1",
            event_type="CHECKPOINT",
            evidence={"cursor": "message-1"},
        )
        coordinator_heartbeat = event(
            signal_id="heartbeat-1",
            sender_agent_id="agent-orchestrator",
            event_type="HEARTBEAT",
            evidence={
                "phase": "implementation",
                "last_completed_step": "reviewed status",
                "next_step": "wait",
                "head_sha": None,
                "worktree_dirty": False,
                "blocking": False,
            },
        )
        intake_heartbeat = event(
            signal_id="heartbeat-intake-1",
            sender_agent_id="agent-intake",
            event_type="HEARTBEAT",
            evidence={
                "phase": "analysis",
                "last_completed_step": "published intake",
                "next_step": "return result",
                "head_sha": None,
                "worktree_dirty": False,
                "blocking": False,
            },
        )
        for index, payload in enumerate(
            (worker_checkpoint, coordinator_heartbeat, intake_heartbeat), start=1
        ):
            self.runner.messages.append(
                {
                    "id": f"message-{index}",
                    "body": json.dumps(payload),
                    "author": payload["sender_agent_id"],
                }
            )

        replay = self.protocol.replay(
            "gwo-campaign-20260718",
            identity_receipts=[
                identity_receipt(),
                identity_receipt(
                    agent_id="agent-orchestrator",
                    role="orchestrator",
                    parent_agent_id="agent-repository-coordinator",
                ),
                identity_receipt(
                    agent_id="agent-intake",
                    role="intake",
                    parent_agent_id="agent-orchestrator",
                ),
            ],
        )

        self.assertEqual([], replay["events"])
        self.assertEqual(
            [
                "event-role-not-authorized",
                "event-role-not-authorized",
                "event-role-not-authorized",
            ],
            [item["reason"] for item in replay["rejected"]],
        )

    def test_orchestrator_identity_is_static_across_child_dispatches(self) -> None:
        start = event(
            signal_id="start-8",
            dispatch_id="dispatch-issue-8",
            issue="#8",
            sender_agent_id="agent-orchestrator",
            recipient_agent_id="agent-worker-8",
            event_type="START",
            evidence={"base_sha": "a" * 40},
        )
        self.runner.messages.append(
            {
                "id": "message-1",
                "body": json.dumps(start),
                "author": "agent-orchestrator",
            }
        )
        receipt = identity_receipt(
            agent_id="agent-orchestrator",
            dispatch_id="dispatch-issue-8",
            role="orchestrator",
            parent_agent_id="agent-repository-coordinator",
            authority_subject_agent_id="agent-worker-8",
        )
        self.assertNotIn("dispatch_id", receipt["labels"])

        replay = self.protocol.replay(
            "gwo-campaign-20260718", identity_receipts=[receipt]
        )

        self.assertEqual(["START"], [item["event_type"] for item in replay["events"]])
        self.assertEqual([], replay["blocked_dispatches"])

    def test_coordinator_control_authority_does_not_mutate_static_labels(self) -> None:
        opened = event(
            signal_id="campaign-opened-1",
            dispatch_id="campaign-control-1",
            issue="campaign",
            sender_agent_id="agent-repository-coordinator",
            recipient_agent_id="agent-orchestrator",
            event_type="CAMPAIGN_OPENED",
            evidence={"admission": "read-back"},
        )
        checkpoint = event(
            signal_id="checkpoint-1",
            dispatch_id="campaign-control-1",
            issue="campaign",
            sender_agent_id="agent-orchestrator",
            recipient_agent_id="agent-repository-coordinator",
            event_type="CHECKPOINT",
            evidence={"cursor": "campaign-opened-1"},
        )
        for index, payload in enumerate((opened, checkpoint), start=1):
            self.runner.messages.append(
                {
                    "id": f"message-{index}",
                    "body": json.dumps(payload),
                    "author": payload["sender_agent_id"],
                }
            )
        root_receipt = identity_receipt(
            agent_id="agent-repository-coordinator",
            dispatch_id="campaign-control-1",
            role="repository-coordinator",
            parent_agent_id=None,
            relationship="root",
        )
        orchestrator_receipt = identity_receipt(
            agent_id="agent-orchestrator",
            dispatch_id="campaign-control-1",
            role="orchestrator",
            parent_agent_id="agent-repository-coordinator",
            authority_kind="campaign-control",
        )
        self.assertNotIn("campaign_id", root_receipt["labels"])
        self.assertNotIn("dispatch_id", root_receipt["labels"])
        self.assertNotIn("dispatch_id", orchestrator_receipt["labels"])

        replay = self.protocol.replay(
            "gwo-campaign-20260718",
            identity_receipts=[root_receipt, orchestrator_receipt],
        )

        self.assertEqual(
            ["CAMPAIGN_OPENED", "CHECKPOINT"],
            [item["event_type"] for item in replay["events"]],
        )

    def test_repository_coordinator_cannot_start_a_campaign_worker(self) -> None:
        start = event(
            signal_id="root-start-worker-1",
            sender_agent_id="agent-repository-coordinator",
            recipient_agent_id="agent-worker-7",
            event_type="START",
            evidence={"base_sha": "a" * 40},
        )
        self.runner.messages.append(
            {
                "id": "message-1",
                "body": json.dumps(start),
                "author": "agent-repository-coordinator",
            }
        )

        replay = self.protocol.replay(
            "gwo-campaign-20260718",
            identity_receipts=[
                identity_receipt(
                    agent_id="agent-repository-coordinator",
                    role="repository-coordinator",
                    parent_agent_id=None,
                    relationship="root",
                )
            ],
        )

        self.assertEqual([], replay["events"])
        self.assertIn(
            "identity-receipt-authority-recipient-mismatch",
            replay["rejected"][0]["reason"],
        )

    def test_decision_gate_requires_durable_github_receipt(self) -> None:
        invalid = ROOM.validate_event(
            event(
                event_type="DECISION_GATE",
                evidence={
                    "decision": "choose option A",
                    "github_state": "ready-for-human",
                },
            )
        )
        valid = ROOM.validate_event(
            event(
                event_type="DECISION_GATE",
                evidence={
                    "decision": "choose option A",
                    "github_state": "ready-for-human",
                    "github_url": "https://github.com/owner/repo/issues/7#issuecomment-1",
                },
            )
        )

        self.assertIn("invalid-decision-gate-evidence", invalid)
        self.assertEqual([], valid)

    def test_wait_rejects_more_than_sixty_seconds(self) -> None:
        with self.assertRaisesRegex(ROOM.RoomProtocolError, "60 seconds"):
            self.protocol.wait("gwo-campaign-20260718", timeout="61s")

    def test_wait_always_replays_room_after_wakeup(self) -> None:
        self.runner.messages.append(
            {
                "id": "message-1",
                "body": json.dumps(event()),
                "author": "agent-worker-7",
            }
        )
        result = self.protocol.wait(
            "gwo-campaign-20260718",
            timeout="10s",
            identity_receipts=[identity_receipt()],
        )
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
