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


REVIEW = load_module("review_policy")


def reviewer(axis: str, *, exists: bool, status: str = "missing") -> dict:
    return {
        "axis": axis,
        "exists": exists,
        "agent_id": f"agent-{axis}-reviewer" if exists else None,
        "status": status,
        "relationship": "subagent" if exists else None,
        "parent_agent_id": "agent-campaign" if exists else None,
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


def candidate(issue: int, *, ready: str, verification_class: str = "standard", **overrides) -> dict:
    payload = {
        "issue": issue,
        "dispatch_id": f"dispatch-issue-{issue}-a1",
        "verified_ready_at": ready,
        "verification_class": verification_class,
        "candidate_sha": f"{issue % 10}" * 40,
        "base_sha": "a" * 40,
        "diff_sha256": "b" * 64,
        "acceptance_sha256": "c" * 64,
        "review_round": 1,
        "scope": "full",
        "previous_candidate_sha": None,
    }
    payload.update(overrides)
    return payload


def lock(value: dict) -> dict:
    return {
        field: value[field]
        for field in (
            "dispatch_id",
            "candidate_sha",
            "base_sha",
            "diff_sha256",
            "acceptance_sha256",
            "review_round",
            "scope",
            "previous_candidate_sha",
        )
    }


def snapshot(
    *candidates: dict,
    reviewers: dict | None = None,
    active_review=None,
    capacity: dict | None = None,
) -> dict:
    reviewer_evidence = reviewers or {
        "spec": reviewer("spec", exists=False),
        "quality": reviewer("quality", exists=False),
    }
    reviewer_count = sum(1 for item in reviewer_evidence.values() if item["exists"])
    return {
        "schema_version": 1,
        "repository": "owner/repo",
        "campaign_id": "campaign-20260718",
        "campaign_agent_id": "agent-campaign",
        "reviewers": reviewer_evidence,
        "capacity": capacity
        or {
            "campaign_active_agents": 1 + reviewer_count,
            "campaign_agent_limit": 6,
            "campaign_active_reviewers": reviewer_count,
            "campaign_review_limit": 2,
            "global_active_agents": 2 + reviewer_count,
            "global_agent_limit": 13,
            "read_back": True,
        },
        "active_review": active_review,
        "candidates": list(candidates),
    }


class ReviewPolicyTests(unittest.TestCase):
    def test_fast_candidate_is_reviewed_inline_without_review_agents(self) -> None:
        report = REVIEW.plan_review(
            snapshot(
                candidate(
                    7,
                    ready="2026-07-18T10:00:00Z",
                    verification_class="fast",
                )
            )
        )

        self.assertEqual("campaign-inline-dual-axis-review", report["actions"][0]["action"])
        self.assertEqual([], report["reviewer_creation_actions"])

    def test_standard_candidate_lazily_creates_exactly_two_paseo_reviewers(self) -> None:
        report = REVIEW.plan_review(
            snapshot(candidate(7, ready="2026-07-18T10:00:00Z"))
        )

        self.assertEqual(
            ["quality", "spec"],
            sorted(item["axis"] for item in report["reviewer_creation_actions"]),
        )
        for action in report["reviewer_creation_actions"]:
            self.assertEqual("create-paseo-reviewer", action["action"])
            self.assertEqual("subagent", action["relationship"])
            self.assertEqual("agent-campaign", action["parent_agent_id"])
            self.assertIn(action["name"], {"Spec Reviewer", "Quality Reviewer"})
            self.assertEqual(action["axis"], action["labels"]["review_axis"])
        self.assertEqual([], report["review_dispatch_actions"])

    def test_partial_pair_creation_preserves_success_and_only_builds_missing_axis(self) -> None:
        report = REVIEW.plan_review(
            snapshot(
                candidate(7, ready="2026-07-18T10:00:00Z"),
                reviewers={
                    "spec": reviewer("spec", exists=True, status="idle"),
                    "quality": reviewer("quality", exists=False),
                },
            )
        )

        self.assertEqual(
            ["quality"],
            [item["axis"] for item in report["reviewer_creation_actions"]],
        )
        self.assertNotIn("archive-paseo-agent", [item["action"] for item in report["actions"]])

    def test_reviewer_creation_rechecks_global_capacity_and_can_build_one_axis(self) -> None:
        one_slot = REVIEW.plan_review(
            snapshot(
                candidate(7, ready="2026-07-18T10:00:00Z"),
                capacity={
                    "campaign_active_agents": 1,
                    "campaign_agent_limit": 6,
                    "campaign_active_reviewers": 0,
                    "campaign_review_limit": 2,
                    "global_active_agents": 12,
                    "global_agent_limit": 13,
                    "read_back": True,
                },
            )
        )
        no_slots = REVIEW.plan_review(
            snapshot(
                candidate(7, ready="2026-07-18T10:00:00Z"),
                capacity={
                    "campaign_active_agents": 1,
                    "campaign_agent_limit": 6,
                    "campaign_active_reviewers": 0,
                    "campaign_review_limit": 2,
                    "global_active_agents": 13,
                    "global_agent_limit": 13,
                    "read_back": True,
                },
            )
        )

        self.assertEqual(
            ["spec"], [item["axis"] for item in one_slot["reviewer_creation_actions"]]
        )
        self.assertEqual([], no_slots["actions"])
        self.assertIn("review-capacity-insufficient", no_slots["blockers"])

    def test_reviewer_identity_labels_are_read_back_exactly(self) -> None:
        spec = reviewer("spec", exists=True, status="idle")
        spec["labels"]["campaign_id"] = "campaign-foreign"
        report = REVIEW.plan_review(
            snapshot(
                candidate(7, ready="2026-07-18T10:00:00Z"),
                reviewers={
                    "spec": spec,
                    "quality": reviewer("quality", exists=True, status="idle"),
                },
            )
        )

        self.assertEqual([], report["actions"])
        self.assertIn("spec-reviewer-labels-invalid", report["blockers"])

    def test_ready_pair_dispatches_both_axes_with_identical_locks(self) -> None:
        candidate_7 = candidate(7, ready="2026-07-18T10:00:00Z")
        report = REVIEW.plan_review(
            snapshot(
                candidate_7,
                reviewers={
                    "spec": reviewer("spec", exists=True, status="idle"),
                    "quality": reviewer("quality", exists=True, status="idle"),
                },
            )
        )

        actions = report["review_dispatch_actions"]
        self.assertEqual(["quality", "spec"], sorted(item["axis"] for item in actions))
        locks = {
            json.dumps(item["lock"], sort_keys=True)
            for item in actions
        }
        self.assertEqual(1, len(locks))
        self.assertEqual(candidate_7["candidate_sha"], actions[0]["lock"]["candidate_sha"])

    def test_busy_pair_queues_by_verified_time_then_issue(self) -> None:
        report = REVIEW.plan_review(
            snapshot(
                candidate(9, ready="2026-07-18T10:01:00Z"),
                candidate(8, ready="2026-07-18T10:00:00Z"),
                candidate(7, ready="2026-07-18T10:00:00Z"),
                reviewers={
                    "spec": reviewer("spec", exists=True, status="running"),
                    "quality": reviewer("quality", exists=True, status="running"),
                },
                active_review={
                    "lock": lock(candidate(6, ready="2026-07-18T09:00:00Z")),
                    "reviewer_agent_ids": {
                        "spec": "agent-spec-reviewer",
                        "quality": "agent-quality-reviewer",
                    },
                    "read_back": True,
                },
            )
        )

        self.assertEqual([7, 8, 9], [item["issue"] for item in report["queue"]])
        self.assertEqual([], report["review_dispatch_actions"])

    def test_queue_orders_instants_not_iso_offset_spelling(self) -> None:
        report = REVIEW.plan_review(
            snapshot(
                candidate(8, ready="2026-07-18T09:00:00Z"),
                candidate(7, ready="2026-07-18T10:00:00+02:00"),
                reviewers={
                    "spec": reviewer("spec", exists=True, status="running"),
                    "quality": reviewer("quality", exists=True, status="running"),
                },
                active_review={
                    "lock": lock(candidate(6, ready="2026-07-18T09:00:00Z")),
                    "reviewer_agent_ids": {
                        "spec": "agent-spec-reviewer",
                        "quality": "agent-quality-reviewer",
                    },
                    "read_back": True,
                },
            )
        )

        self.assertEqual([7, 8], [item["issue"] for item in report["queue"]])

    def test_idle_reusable_pair_selects_next_candidate_without_new_agents(self) -> None:
        report = REVIEW.plan_review(
            snapshot(
                candidate(8, ready="2026-07-18T10:01:00Z"),
                reviewers={
                    "spec": reviewer("spec", exists=True, status="idle"),
                    "quality": reviewer("quality", exists=True, status="idle"),
                },
            )
        )

        self.assertEqual([], report["reviewer_creation_actions"])
        self.assertEqual(
            {"agent-spec-reviewer", "agent-quality-reviewer"},
            {item["agent_id"] for item in report["review_dispatch_actions"]},
        )

    def test_active_review_recovery_compares_the_complete_lock_and_reviewer_ids(self) -> None:
        current = candidate(7, ready="2026-07-18T10:00:00Z")
        stale = lock(current)
        stale["diff_sha256"] = "e" * 64
        report = REVIEW.plan_review(
            snapshot(
                current,
                reviewers={
                    "spec": reviewer("spec", exists=True, status="running"),
                    "quality": reviewer("quality", exists=True, status="running"),
                },
                active_review={
                    "lock": stale,
                    "reviewer_agent_ids": {
                        "spec": "agent-spec-reviewer",
                        "quality": "agent-quality-reviewer",
                    },
                    "read_back": True,
                },
            )
        )

        self.assertEqual([], report["actions"])
        self.assertIn("active-review-lock-mismatch", report["blockers"])

    def test_delta_round_requires_previous_candidate_sha(self) -> None:
        with self.assertRaisesRegex(ValueError, "previous_candidate_sha"):
            REVIEW.plan_review(
                snapshot(
                    candidate(
                        7,
                        ready="2026-07-18T10:00:00Z",
                        review_round=2,
                        scope="delta",
                    )
                )
            )

    def test_cli_emits_deterministic_plan(self) -> None:
        payload = snapshot(candidate(7, ready="2026-07-18T10:00:00Z"))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "review_policy.py"),
                    "plan-review",
                    "--snapshot",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["plan"]["automatic_execution"])


if __name__ == "__main__":
    unittest.main()
