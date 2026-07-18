from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "github-work-orchestrator" / "scripts"


def load_module(name: str):
    path = SCRIPT_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


SCHEDULER = load_module("campaign_scheduler")
LOOP = load_module("coordinator_loop")


def candidate(issue: int, **overrides):
    payload = {
        "issue": issue,
        "rank": issue,
        "lifecycle": "ready-for-agent",
        "assignees": [],
        "open_dependencies": [],
        "hotset": [f"src/issue-{issue}"],
        "verification_class": "fast",
        "attempt": 1,
        "contract_valid": True,
        "slug": f"issue-{issue}",
    }
    payload.update(overrides)
    attempt = payload["attempt"]
    payload.setdefault(
        "dispatch_readback",
        {
            "dispatch_id": f"dispatch-issue-{issue}-a{attempt}",
            "active_matches": 0,
            "archived_matches": 0,
            "read_back": True,
        },
    )
    if attempt > 1:
        payload.setdefault(
            "previous_dispatch",
            {
                "dispatch_id": f"dispatch-issue-{issue}-a{attempt - 1}",
                "attempt": attempt - 1,
                "agent_id": f"agent-issue-{issue}-a{attempt - 1}",
                "terminal_event": "STOPPED",
                "terminal_signal_id": f"stopped-issue-{issue}-a{attempt - 1}",
                "terminal_sender_agent_id": f"agent-issue-{issue}-a{attempt - 1}",
                "terminal_read_back": True,
                "agent_status": "archived",
                "agent_reconciled": True,
                "ownership_unambiguous": True,
                "wip_durable": True,
            },
        )
    return payload


def review_agent(axis: str, *, exists: bool, reusable: bool) -> dict:
    return {
        "axis": axis,
        "exists": exists,
        "reusable": reusable,
        "agent_id": f"agent-{axis}-reviewer" if exists else None,
        "parent_agent_id": "agent-campaign" if exists else None,
        "relationship": "subagent" if exists else None,
        "labels": (
            {
                "repository": "owner/repo",
                "campaign_id": "campaign-20260718",
                "role": "review",
                "review_axis": axis,
            }
            if exists
            else None
        ),
        "read_back": True,
    }


def snapshot(*candidates, **overrides):
    payload = {
        "schema_version": 1,
        "repository": "owner/repo",
        "campaign_id": "campaign-20260718",
        "campaign_agent_id": "agent-campaign",
        "campaign_hotset": ["src"],
        "case_sensitive_paths": True,
        "control_plane": {
            "repository_coordinators": 1,
            "campaign_orchestrators": 1,
            "scope_readback": True,
            "provider_binding_readback": True,
        },
        "capacity": {
            "campaign_active_agents": 1,
            "campaign_agent_limit": 6,
            "campaign_active_workers": 0,
            "campaign_worker_limit": 3,
            "campaign_active_reviewers": 0,
            "campaign_review_limit": 2,
            "global_active_agents": 2,
            "global_agent_limit": 13,
        },
        "review_agents": {
            "spec": review_agent("spec", exists=False, reusable=False),
            "quality": review_agent("quality", exists=False, reusable=False),
        },
        "candidates": list(candidates),
        "active_dispatches": [],
        "active_external_hotsets": {},
        "max_dispatch_attempts": 3,
    }
    payload.update(overrides)
    return payload


class CampaignSchedulerTests(unittest.TestCase):
    def test_fast_wave_fills_three_worker_slots(self) -> None:
        report = SCHEDULER.plan_wave(snapshot(candidate(3), candidate(1), candidate(2)))

        self.assertEqual("eligible", report["status"])
        self.assertTrue(report["automatic_execution"])
        self.assertEqual([1, 2, 3], [item["issue"] for item in report["dispatches"]])
        self.assertEqual(3, report["slots"]["dispatch_slots"])
        self.assertEqual("Worker · #1 · a1", report["dispatches"][0]["agent_name"])
        self.assertEqual("WT · #1 · issue-1", report["dispatches"][0]["workspace_title"])
        self.assertEqual("agent-campaign", report["dispatches"][0]["parent_agent_id"])

    def test_standard_wave_keeps_all_three_worker_slots(self) -> None:
        report = SCHEDULER.plan_wave(
            snapshot(
                candidate(1, verification_class="standard"),
                candidate(2, verification_class="standard"),
                candidate(3, verification_class="standard"),
            )
        )

        self.assertEqual([1, 2, 3], [item["issue"] for item in report["dispatches"]])
        self.assertFalse(report["slots"]["review_slot_reserved"])
        self.assertTrue(report["slots"]["review_pair_needed"])
        self.assertEqual(2, report["slots"]["review_slots_available"])

    def test_four_worker_slots_or_nonpair_review_capacity_fails_closed(self) -> None:
        four_workers = SCHEDULER.plan_wave(
            snapshot(
                candidate(1),
                capacity={
                    "campaign_active_agents": 1,
                    "campaign_agent_limit": 7,
                    "campaign_active_workers": 0,
                    "campaign_worker_limit": 4,
                    "campaign_active_reviewers": 0,
                    "campaign_review_limit": 2,
                    "global_active_agents": 2,
                    "global_agent_limit": 13,
                },
            )
        )
        one_reviewer = SCHEDULER.plan_wave(
            snapshot(
                candidate(1),
                capacity={
                    "campaign_active_agents": 1,
                    "campaign_agent_limit": 5,
                    "campaign_active_workers": 0,
                    "campaign_worker_limit": 3,
                    "campaign_active_reviewers": 0,
                    "campaign_review_limit": 1,
                    "global_active_agents": 2,
                    "global_agent_limit": 13,
                },
            )
        )

        self.assertEqual([], four_workers["dispatches"])
        self.assertIn("worker-slot-limit-exceeded", four_workers["global_blockers"])
        self.assertEqual([], one_reviewer["dispatches"])
        self.assertIn("review-slot-limit-invalid", one_reviewer["global_blockers"])

    def test_nonreusable_review_pair_member_blocks_instead_of_admitting_a_second(
        self,
    ) -> None:
        report = SCHEDULER.plan_wave(
            snapshot(
                candidate(1, verification_class="standard"),
                candidate(2, verification_class="standard"),
                review_agents={
                    "spec": review_agent("spec", exists=True, reusable=False),
                    "quality": review_agent("quality", exists=True, reusable=True),
                },
                capacity={
                    "campaign_active_agents": 3,
                    "campaign_agent_limit": 6,
                    "campaign_active_workers": 0,
                    "campaign_worker_limit": 3,
                    "campaign_active_reviewers": 2,
                    "campaign_review_limit": 2,
                    "global_active_agents": 4,
                    "global_agent_limit": 13,
                },
            )
        )

        self.assertEqual([], report["dispatches"])
        self.assertFalse(report["slots"]["review_slot_reserved"])
        self.assertIn("spec-reviewer-not-reusable", report["global_blockers"])

    def test_review_agent_identity_must_be_read_back_for_this_campaign(self) -> None:
        spec = review_agent("spec", exists=True, reusable=True)
        spec["labels"]["campaign_id"] = "campaign-foreign"
        report = SCHEDULER.plan_wave(
            snapshot(
                candidate(1, verification_class="standard"),
                review_agents={
                    "spec": spec,
                    "quality": review_agent("quality", exists=False, reusable=False),
                },
                capacity={
                    "campaign_active_agents": 2,
                    "campaign_agent_limit": 6,
                    "campaign_active_workers": 0,
                    "campaign_worker_limit": 3,
                    "campaign_active_reviewers": 1,
                    "campaign_review_limit": 2,
                    "global_active_agents": 3,
                    "global_agent_limit": 13,
                },
            )
        )

        self.assertEqual([], report["dispatches"])
        self.assertIn("spec-review-agent-identity-invalid", report["global_blockers"])

    def test_dependency_and_hotset_conflicts_defer_only_affected_issues(self) -> None:
        report = SCHEDULER.plan_wave(
            snapshot(
                candidate(112, rank=30, open_dependencies=[143]),
                candidate(143, rank=10, hotset=["src/alpha"]),
                candidate(150, rank=20, hotset=["src/beta"]),
            )
        )

        self.assertEqual([143, 150], [item["issue"] for item in report["dispatches"]])
        blocked = next(item for item in report["deferred"] if item["issue"] == 112)
        self.assertEqual(["open-dependencies"], blocked["blockers"])

    def test_unknown_hotset_is_repository_exclusive(self) -> None:
        report = SCHEDULER.plan_wave(
            snapshot(candidate(1, hotset=[]), candidate(2, hotset=["src/other"]))
        )

        self.assertEqual([1], [item["issue"] for item in report["dispatches"]])
        self.assertEqual("repository", report["dispatches"][0]["exclusive_scope"])
        self.assertIn(
            "hotset-conflict",
            next(item for item in report["deferred"] if item["issue"] == 2)["blockers"],
        )

    def test_hotset_comparison_uses_explicit_worktree_case_semantics(self) -> None:
        insensitive = SCHEDULER.plan_wave(
            snapshot(
                candidate(1, hotset=["Src/API.py"]),
                candidate(2, hotset=["src/api.py"]),
                campaign_hotset=["src"],
                case_sensitive_paths=False,
            )
        )
        sensitive = SCHEDULER.plan_wave(
            snapshot(
                candidate(1, hotset=["Src/API.py"]),
                candidate(2, hotset=["src/api.py"]),
                campaign_hotset=["Src", "src"],
                case_sensitive_paths=True,
            )
        )

        self.assertEqual([1], [item["issue"] for item in insensitive["dispatches"]])
        self.assertEqual([1, 2], [item["issue"] for item in sensitive["dispatches"]])

        missing = snapshot(candidate(3))
        missing.pop("case_sensitive_paths")
        with self.assertRaisesRegex(ValueError, "readback"):
            SCHEDULER.plan_wave(missing)

    def test_duplicate_active_dispatch_is_a_global_blocker(self) -> None:
        active = [
            {
                "issue": 7,
                "dispatch_id": "dispatch-issue-7-a1",
                "hotset": ["src/issue-7"],
                "verification_class": "fast",
            },
            {
                "issue": 7,
                "dispatch_id": "dispatch-issue-7-a2",
                "hotset": ["src/issue-7"],
                "verification_class": "fast",
            },
        ]
        report = SCHEDULER.plan_wave(snapshot(candidate(8), active_dispatches=active))

        self.assertEqual("blocked", report["status"])
        self.assertFalse(report["automatic_execution"])
        self.assertEqual([], report["dispatches"])
        self.assertIn("duplicate-active-dispatch", report["global_blockers"])

    def test_duplicate_dispatch_identity_across_issues_is_a_global_blocker(
        self,
    ) -> None:
        active = [
            {
                "issue": 7,
                "dispatch_id": "dispatch-shared-a1",
                "hotset": ["src/issue-7"],
                "verification_class": "fast",
            },
            {
                "issue": 8,
                "dispatch_id": "dispatch-shared-a1",
                "hotset": ["src/issue-8"],
                "verification_class": "fast",
            },
        ]
        report = SCHEDULER.plan_wave(snapshot(candidate(9), active_dispatches=active))

        self.assertEqual([], report["dispatches"])
        self.assertIn("duplicate-active-dispatch-id", report["global_blockers"])

    def test_dispatch_ids_are_stable_and_retry_limit_fails_closed(self) -> None:
        first = SCHEDULER.plan_wave(snapshot(candidate(7, attempt=2)))
        second = SCHEDULER.plan_wave(snapshot(candidate(7, attempt=2)))
        exhausted = SCHEDULER.plan_wave(snapshot(candidate(7, attempt=4)))

        self.assertEqual(first, second)
        self.assertEqual("dispatch-issue-7-a2", first["dispatches"][0]["dispatch_id"])
        self.assertIn("retry-limit-exhausted", exhausted["deferred"][0]["blockers"])
        self.assertEqual(
            "post-escalation-and-set-ready-for-human",
            exhausted["deferred"][0]["next_action"],
        )

    def test_retry_requires_exact_terminal_predecessor_proof(self) -> None:
        missing = SCHEDULER.plan_wave(
            snapshot(candidate(7, attempt=2, previous_dispatch=None))
        )
        mismatched = SCHEDULER.plan_wave(
            snapshot(
                candidate(
                    7,
                    attempt=2,
                    previous_dispatch={
                        "dispatch_id": "dispatch-issue-7-a1",
                        "attempt": 1,
                        "agent_id": "agent-issue-7-a1",
                        "terminal_event": "STOPPED",
                        "terminal_signal_id": "stopped-7-a1",
                        "terminal_sender_agent_id": "agent-issue-7-a1",
                        "terminal_read_back": True,
                        "agent_status": "archived",
                        "agent_reconciled": True,
                        "ownership_unambiguous": False,
                        "wip_durable": True,
                    },
                )
            )
        )

        self.assertIn(
            "terminal-predecessor-proof-missing", missing["deferred"][0]["blockers"]
        )
        self.assertIn(
            "terminal-predecessor-proof-invalid",
            mismatched["deferred"][0]["blockers"],
        )
        self.assertFalse(missing["automatic_execution"])
        self.assertFalse(mismatched["automatic_execution"])

    def test_blocked_escalated_or_idle_predecessor_cannot_spawn_a_successor(
        self,
    ) -> None:
        blocked = candidate(7, attempt=2)
        blocked["previous_dispatch"]["terminal_event"] = "BLOCKED"
        idle = candidate(8, attempt=2)
        idle["previous_dispatch"]["agent_status"] = "idle"

        blocked_report = SCHEDULER.plan_wave(snapshot(blocked))
        idle_report = SCHEDULER.plan_wave(snapshot(idle))

        for report in (blocked_report, idle_report):
            self.assertEqual([], report["dispatches"])
            self.assertIn(
                "terminal-predecessor-proof-invalid",
                report["deferred"][0]["blockers"],
            )

    def test_attempt_limit_cannot_be_configured_above_three(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not exceed 3"):
            SCHEDULER.plan_wave(
                snapshot(candidate(7, attempt=4), max_dispatch_attempts=4)
            )

    def test_existing_active_or_archived_dispatch_identity_is_reconciled(self) -> None:
        existing = candidate(7)
        existing["dispatch_readback"]["archived_matches"] = 1

        report = SCHEDULER.plan_wave(snapshot(existing))

        self.assertEqual([], report["dispatches"])
        self.assertIn(
            "dispatch-identity-already-exists", report["deferred"][0]["blockers"]
        )

    def test_external_campaign_hotset_conflict_blocks_the_campaign(self) -> None:
        report = SCHEDULER.plan_wave(
            snapshot(
                candidate(1, hotset=["src/shared/api"]),
                candidate(2, hotset=["src/isolated"]),
                active_external_hotsets={"campaign-other": ["src/shared"]},
                capacity={
                    "campaign_active_agents": 1,
                    "campaign_agent_limit": 6,
                    "campaign_active_workers": 0,
                    "campaign_worker_limit": 3,
                    "campaign_active_reviewers": 0,
                    "campaign_review_limit": 2,
                    "global_active_agents": 3,
                    "global_agent_limit": 13,
                },
            )
        )

        self.assertEqual([], report["dispatches"])
        self.assertIn("campaign-hotset-conflict", report["global_blockers"])

    def test_disjoint_external_campaign_does_not_consume_worker_slots(self) -> None:
        report = SCHEDULER.plan_wave(
            snapshot(
                candidate(1, hotset=["src/issue-1"]),
                active_external_hotsets={"campaign-other": ["docs"]},
                capacity={
                    "campaign_active_agents": 1,
                    "campaign_agent_limit": 6,
                    "campaign_active_workers": 0,
                    "campaign_worker_limit": 3,
                    "campaign_active_reviewers": 0,
                    "campaign_review_limit": 2,
                    "global_active_agents": 3,
                    "global_agent_limit": 13,
                },
            )
        )

        self.assertEqual([1], [item["issue"] for item in report["dispatches"]])

    def test_control_plane_or_count_contradiction_returns_zero_actions(self) -> None:
        duplicate = SCHEDULER.plan_wave(
            snapshot(
                candidate(1),
                control_plane={
                    "repository_coordinators": 2,
                    "campaign_orchestrators": 1,
                    "scope_readback": True,
                    "provider_binding_readback": True,
                },
            )
        )
        missing_review_count = SCHEDULER.plan_wave(
            snapshot(
                candidate(1),
                review_agents={
                    "spec": review_agent("spec", exists=True, reusable=True),
                    "quality": review_agent("quality", exists=False, reusable=False),
                },
            )
        )

        self.assertEqual([], duplicate["dispatches"])
        self.assertIn("repository-coordinator-conflict", duplicate["global_blockers"])
        self.assertEqual([], missing_review_count["dispatches"])
        self.assertIn(
            "review-agent-count-contradicts-capacity",
            missing_review_count["global_blockers"],
        )
        self.assertIn(
            "global:repository-coordinator-conflict",
            duplicate["deferred"][0]["blockers"],
        )

    def test_active_dispatch_and_review_agent_must_both_be_counted(self) -> None:
        report = SCHEDULER.plan_wave(
            snapshot(
                candidate(8),
                review_agents={
                    "spec": review_agent("spec", exists=True, reusable=True),
                    "quality": review_agent("quality", exists=True, reusable=True),
                },
                active_dispatches=[
                    {
                        "issue": 7,
                        "dispatch_id": "dispatch-issue-7-a1",
                        "hotset": ["src/issue-7"],
                        "verification_class": "standard",
                    }
                ],
                capacity={
                    "campaign_active_agents": 4,
                    "campaign_agent_limit": 6,
                    "campaign_active_workers": 0,
                    "campaign_worker_limit": 3,
                    "campaign_active_reviewers": 2,
                    "campaign_review_limit": 2,
                    "global_active_agents": 5,
                    "global_agent_limit": 13,
                },
            )
        )

        self.assertIn(
            "active-dispatch-count-contradicts-capacity", report["global_blockers"]
        )
        self.assertEqual([], report["dispatches"])

    def test_foreign_active_agents_reduce_actual_global_wave_capacity(self) -> None:
        report = SCHEDULER.plan_wave(
            snapshot(
                candidate(1),
                candidate(2),
                candidate(3),
                capacity={
                    "campaign_active_agents": 1,
                    "campaign_agent_limit": 6,
                    "campaign_active_workers": 0,
                    "campaign_worker_limit": 3,
                    "campaign_active_reviewers": 0,
                    "campaign_review_limit": 2,
                    "global_active_agents": 12,
                    "global_agent_limit": 13,
                },
            )
        )

        self.assertEqual([1], [item["issue"] for item in report["dispatches"]])
        self.assertEqual(1, report["slots"]["global_slots"])

    def test_standard_wave_preserves_global_capacity_for_missing_review_pair(self) -> None:
        report = SCHEDULER.plan_wave(
            snapshot(
                candidate(1, verification_class="standard"),
                candidate(2, verification_class="standard"),
                candidate(3, verification_class="standard"),
                capacity={
                    "campaign_active_agents": 1,
                    "campaign_agent_limit": 6,
                    "campaign_active_workers": 0,
                    "campaign_worker_limit": 3,
                    "campaign_active_reviewers": 0,
                    "campaign_review_limit": 2,
                    "global_active_agents": 10,
                    "global_agent_limit": 13,
                },
            )
        )

        self.assertEqual([1], [item["issue"] for item in report["dispatches"]])
        self.assertEqual(2, report["slots"]["review_global_slots_reserved"])
        self.assertEqual(1, report["slots"]["dispatch_slots"])

    def test_two_full_campaigns_plus_coordinator_exactly_fill_global_default(self) -> None:
        report = SCHEDULER.plan_wave(
            snapshot(
                candidate(7),
                capacity={
                    "campaign_active_agents": 6,
                    "campaign_agent_limit": 6,
                    "campaign_active_workers": 3,
                    "campaign_worker_limit": 3,
                    "campaign_active_reviewers": 2,
                    "campaign_review_limit": 2,
                    "global_active_agents": 13,
                    "global_agent_limit": 13,
                },
                active_dispatches=[
                    {
                        "issue": issue,
                        "dispatch_id": f"dispatch-issue-{issue}-a1",
                        "hotset": [f"src/issue-{issue}"],
                        "verification_class": "standard",
                    }
                    for issue in (1, 2, 3)
                ],
                review_agents={
                    "spec": review_agent("spec", exists=True, reusable=True),
                    "quality": review_agent("quality", exists=True, reusable=True),
                },
            )
        )

        self.assertEqual([], report["dispatches"])
        self.assertEqual(0, report["slots"]["dispatch_slots"])

    def test_cli_reads_one_snapshot_and_emits_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json"
            path.write_text(json.dumps(snapshot(candidate(7))), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "campaign_scheduler.py"),
                    "plan-wave",
                    "--snapshot",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(7, json.loads(result.stdout)["plan"]["dispatches"][0]["issue"])


class CoordinatorLoopPolicyTests(unittest.TestCase):
    def test_phase_boundary_and_elapsed_target_request_heartbeat(self) -> None:
        phase = LOOP.heartbeat_plan(
            phase_boundary=True,
            material_progress=False,
            safe_to_post=True,
            terminal=False,
            seconds_since_runtime_signal=10,
        )
        elapsed = LOOP.heartbeat_plan(
            phase_boundary=False,
            material_progress=False,
            safe_to_post=True,
            terminal=False,
            seconds_since_runtime_signal=300,
        )

        self.assertEqual("post-heartbeat", phase["action"])
        self.assertEqual("post-heartbeat", elapsed["action"])

    def test_long_blocking_command_does_not_require_a_heartbeat(self) -> None:
        report = LOOP.heartbeat_plan(
            phase_boundary=True,
            material_progress=False,
            safe_to_post=False,
            terminal=False,
            seconds_since_runtime_signal=1200,
        )

        self.assertEqual("continue-without-signal", report["action"])

    def test_material_progress_and_terminal_state_take_precedence(self) -> None:
        progress = LOOP.heartbeat_plan(
            phase_boundary=True,
            material_progress=True,
            safe_to_post=True,
            terminal=False,
            seconds_since_runtime_signal=300,
        )
        terminal = LOOP.heartbeat_plan(
            phase_boundary=True,
            material_progress=True,
            safe_to_post=True,
            terminal=True,
            seconds_since_runtime_signal=300,
        )

        self.assertEqual("post-progress", progress["action"])
        self.assertEqual("no-signal-after-terminal", terminal["action"])

    def test_stale_running_agent_is_never_prompted_or_replaced(self) -> None:
        active = LOOP.stale_recovery_plan(
            seconds_since_runtime_signal=900,
            seconds_since_last_inspection=900,
            agent_status="running",
            timeline_active=True,
            identity_matches=True,
            permission_pending=False,
            terminal_event=False,
            recovery_prompt_sent=False,
        )
        quiet = LOOP.stale_recovery_plan(
            seconds_since_runtime_signal=900,
            seconds_since_last_inspection=900,
            agent_status="running",
            timeline_active=False,
            identity_matches=True,
            permission_pending=False,
            terminal_event=False,
            recovery_prompt_sent=False,
        )

        self.assertEqual([], active["actions"])
        self.assertEqual(["record-suspected-stalled-checkpoint"], quiet["actions"])
        for report in (active, quiet):
            self.assertFalse(report["replacement_authorized"])
            self.assertFalse(report["cancellation_authorized"])
            self.assertFalse(report["archive_authorized"])

    def test_idle_agent_receives_only_one_recovery_prompt(self) -> None:
        first = LOOP.stale_recovery_plan(
            seconds_since_runtime_signal=900,
            seconds_since_last_inspection=900,
            agent_status="idle",
            timeline_active=False,
            identity_matches=True,
            permission_pending=False,
            terminal_event=False,
            recovery_prompt_sent=False,
        )
        repeated = LOOP.stale_recovery_plan(
            seconds_since_runtime_signal=1800,
            seconds_since_last_inspection=900,
            agent_status="idle",
            timeline_active=False,
            identity_matches=True,
            permission_pending=False,
            terminal_event=False,
            recovery_prompt_sent=True,
        )

        self.assertEqual(["send-one-recovery-prompt"], first["actions"])
        self.assertEqual([], repeated["actions"])

    def test_silence_below_threshold_or_cooldown_only_waits(self) -> None:
        fresh = LOOP.stale_recovery_plan(
            seconds_since_runtime_signal=899,
            seconds_since_last_inspection=900,
            agent_status="idle",
            timeline_active=False,
            identity_matches=True,
            permission_pending=False,
            terminal_event=False,
            recovery_prompt_sent=False,
        )
        cooldown = LOOP.stale_recovery_plan(
            seconds_since_runtime_signal=1800,
            seconds_since_last_inspection=899,
            agent_status="idle",
            timeline_active=False,
            identity_matches=True,
            permission_pending=False,
            terminal_event=False,
            recovery_prompt_sent=False,
        )

        self.assertEqual("wait", fresh["status"])
        self.assertEqual("wait", cooldown["status"])

    def test_recovery_matrix_preserves_wip_and_escalates_ambiguity(self) -> None:
        common = {
            "seconds_since_runtime_signal": 900,
            "seconds_since_last_inspection": 900,
            "timeline_active": False,
            "permission_pending": False,
            "terminal_event": False,
            "recovery_prompt_sent": False,
        }
        identity = LOOP.stale_recovery_plan(
            **common, agent_status="idle", identity_matches=False
        )
        errored = LOOP.stale_recovery_plan(
            **common, agent_status="error", identity_matches=True
        )
        missing = LOOP.stale_recovery_plan(
            **common, agent_status="missing", identity_matches=True
        )

        self.assertEqual(["post-blocked-identity-mismatch"], identity["actions"])
        self.assertEqual(["preserve-wip-and-evaluate-successor"], errored["actions"])
        self.assertEqual(["post-escalation-agent-missing"], missing["actions"])
        for report in (identity, errored, missing):
            self.assertFalse(report["replacement_authorized"])
            self.assertFalse(report["archive_authorized"])

    def test_identity_mismatch_takes_precedence_over_claimed_terminal_event(
        self,
    ) -> None:
        report = LOOP.stale_recovery_plan(
            seconds_since_runtime_signal=900,
            seconds_since_last_inspection=900,
            agent_status="closed",
            timeline_active=False,
            identity_matches=False,
            permission_pending=False,
            terminal_event=True,
            recovery_prompt_sent=False,
        )

        self.assertEqual("blocked", report["status"])
        self.assertEqual(["post-blocked-identity-mismatch"], report["actions"])

    def test_invalid_configuration_blocks_new_dispatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "worker_stale_after_seconds"):
            LOOP.resolve_orchestration_config(
                {
                    "orchestration": {
                        "worker_heartbeat_target_seconds": 300,
                        "worker_stale_after_seconds": 200,
                    }
                }
            )

        with self.assertRaisesRegex(ValueError, "stale_recheck_cooldown_seconds"):
            LOOP.resolve_orchestration_config(
                {"orchestration": {"stale_recheck_cooldown_seconds": 899}}
            )

        with self.assertRaisesRegex(ValueError, "max_dispatch_attempts_per_issue"):
            LOOP.resolve_orchestration_config(
                {"orchestration": {"max_dispatch_attempts_per_issue": 4}}
            )

        with self.assertRaisesRegex(ValueError, "stale_after_seconds"):
            LOOP.stale_recovery_plan(
                seconds_since_runtime_signal=900,
                seconds_since_last_inspection=900,
                agent_status="idle",
                timeline_active=False,
                identity_matches=True,
                permission_pending=False,
                terminal_event=False,
                recovery_prompt_sent=False,
                stale_after_seconds=899,
            )

    def test_config_cli_resolves_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            preferences = Path(temporary) / "preferences.json"
            preferences.write_text("{}", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "coordinator_loop.py"),
                    "resolve-config",
                    "--preferences",
                    str(preferences),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        policy = json.loads(result.stdout)["policy"]
        self.assertEqual(60, policy["wait_timeout_seconds"])
        self.assertEqual(300, policy["worker_heartbeat_target_seconds"])
        self.assertEqual(900, policy["worker_stale_after_seconds"])
        self.assertEqual(6, policy["max_active_agents_per_campaign"])
        self.assertEqual(3, policy["max_worker_slots_per_campaign"])
        self.assertEqual(2, policy["max_review_slots_per_campaign"])
        self.assertEqual(13, policy["max_active_agents_global"])

    def test_capacity_config_must_fit_campaign_and_repository_control_planes(self) -> None:
        with self.assertRaisesRegex(ValueError, "campaign capacity"):
            LOOP.resolve_orchestration_config(
                {
                    "orchestration": {
                        "max_active_agents_per_campaign": 5,
                        "max_worker_slots_per_campaign": 3,
                        "max_review_slots_per_campaign": 2,
                    }
                }
            )
        with self.assertRaisesRegex(ValueError, "global capacity"):
            LOOP.resolve_orchestration_config(
                {
                    "orchestration": {
                        "max_active_agents_per_campaign": 6,
                        "max_active_agents_global": 6,
                    }
                }
            )
        with self.assertRaisesRegex(ValueError, "must not exceed 3"):
            LOOP.resolve_orchestration_config(
                {
                    "orchestration": {
                        "max_active_agents_per_campaign": 7,
                        "max_worker_slots_per_campaign": 4,
                        "max_review_slots_per_campaign": 2,
                    }
                }
            )
        with self.assertRaisesRegex(ValueError, "must equal 2"):
            LOOP.resolve_orchestration_config(
                {
                    "orchestration": {
                        "max_active_agents_per_campaign": 5,
                        "max_worker_slots_per_campaign": 3,
                        "max_review_slots_per_campaign": 1,
                    }
                }
            )


if __name__ == "__main__":
    unittest.main()
