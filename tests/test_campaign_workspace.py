from __future__ import annotations

import importlib.util
import sys
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


CAMPAIGN = load_module("campaign_workspace")


def create_snapshot(**overrides) -> dict:
    payload = {
        "schema_version": 1,
        "repository": "owner/repo",
        "campaign_id": "c-017-lifecycle",
        "purpose": "lifecycle",
        "repository_coordinator": {
            "agent_id": "agent-repository-coordinator",
            "repository": "owner/repo",
            "role": "repository-coordinator",
            "relationship": "root",
            "parent_agent_id": None,
            "labels": {
                "repository": "owner/repo",
                "role": "repository-coordinator",
            },
            "read_back": True,
        },
        "base": {
            "repository": "owner/repo",
            "branch": "dev",
            "sha": "a" * 40,
            "read_back": True,
        },
        "provider_binding": {
            "provider": "opencode",
            "model": "codexhub-kimi/k3",
            "mode": "build",
            "read_back": True,
        },
        "existing_campaign_agents": 0,
        "existing_control_workspaces": 0,
    }
    payload.update(overrides)
    return payload


class CampaignWorkspaceTests(unittest.TestCase):
    def test_new_campaign_plan_uses_dedicated_control_worktree_and_subagent(self) -> None:
        report = CAMPAIGN.plan_create(create_snapshot())

        self.assertEqual("eligible", report["status"])
        self.assertTrue(report["automatic_execution"])
        self.assertEqual("gwo/campaign/c-017-lifecycle", report["branch"])
        self.assertEqual("campaign-c-017-lifecycle", report["worktree_slug"])
        self.assertEqual("Campaign · c-017-lifecycle · lifecycle", report["workspace_title"])
        create_agent = next(
            item for item in report["actions"] if item["action"] == "create-campaign-agent"
        )
        self.assertEqual("subagent", create_agent["relationship"])
        self.assertEqual("agent-repository-coordinator", create_agent["parent_agent_id"])
        self.assertEqual("create/worktree", create_agent["workspace"])
        self.assertEqual("Campaign · c-017-lifecycle · lifecycle", create_agent["name"])

    def test_duplicate_campaign_agent_or_workspace_fails_closed(self) -> None:
        for field in ("existing_campaign_agents", "existing_control_workspaces"):
            with self.subTest(field=field):
                report = CAMPAIGN.plan_create(create_snapshot(**{field: 1}))
                self.assertEqual("protected", report["status"])
                self.assertEqual([], report["actions"])

    def test_base_must_be_read_back_dev(self) -> None:
        report = CAMPAIGN.plan_create(
            create_snapshot(
                base={
                    "repository": "owner/repo",
                    "branch": "main",
                    "sha": "a" * 40,
                    "read_back": True,
                }
            )
        )

        self.assertIn("campaign-base-not-dev", report["blockers"])
        self.assertEqual([], report["actions"])

    def test_exact_readback_admits_campaign(self) -> None:
        report = CAMPAIGN.validate_readback(
            {
                "schema_version": 1,
                "repository": "owner/repo",
                "campaign_id": "c-017-lifecycle",
                "purpose": "lifecycle",
                **create_snapshot(
                    existing_campaign_agents=1,
                    existing_control_workspaces=1,
                ),
                "observed": {
                    "agent_id": "agent-campaign",
                    "parent_agent_id": "agent-repository-coordinator",
                    "relationship": "subagent",
                    "agent_title": "Campaign · c-017-lifecycle · lifecycle",
                    "workspace_title": "Campaign · c-017-lifecycle · lifecycle",
                    "workspace_kind": "worktree",
                    "worktree_slug": "campaign-c-017-lifecycle",
                    "branch": "gwo/campaign/c-017-lifecycle",
                    "head_sha": "a" * 40,
                    "provider": "opencode",
                    "model": "codexhub-kimi/k3",
                    "mode": "build",
                    "labels": {
                        "repository": "owner/repo",
                        "campaign_id": "c-017-lifecycle",
                        "role": "orchestrator",
                        "gwo.version": "4.3",
                    },
                    "tracked_changes": False,
                    "unique_commits": 0,
                    "branch_local_only": True,
                    "read_back": True,
                },
            }
        )

        self.assertTrue(report["admitted"])
        self.assertTrue(report["dispatch_allowed"])

    def test_control_workspace_pollution_stops_dispatch_and_preserves_scene(self) -> None:
        base_observed = {
            "agent_id": "agent-campaign",
            "parent_agent_id": "agent-repository-coordinator",
            "relationship": "subagent",
            "agent_title": "Campaign · c-017-lifecycle · lifecycle",
            "workspace_title": "Campaign · c-017-lifecycle · lifecycle",
            "workspace_kind": "worktree",
            "worktree_slug": "campaign-c-017-lifecycle",
            "branch": "gwo/campaign/c-017-lifecycle",
            "head_sha": "a" * 40,
            "provider": "opencode",
            "model": "codexhub-kimi/k3",
            "mode": "build",
            "labels": {
                "repository": "owner/repo",
                "campaign_id": "c-017-lifecycle",
                "role": "orchestrator",
                "gwo.version": "4.3",
            },
            "tracked_changes": False,
            "unique_commits": 0,
            "branch_local_only": True,
            "read_back": True,
        }
        for changes, blocker in (
            ({"tracked_changes": True}, "campaign-control-tracked-changes"),
            ({"unique_commits": 1}, "campaign-control-unique-commits"),
            ({"branch_local_only": False}, "campaign-control-branch-published"),
        ):
            with self.subTest(blocker=blocker):
                report = CAMPAIGN.validate_readback(
                    {
                        "schema_version": 1,
                        "repository": "owner/repo",
                        "campaign_id": "c-017-lifecycle",
                        "purpose": "lifecycle",
                        **create_snapshot(
                            existing_campaign_agents=1,
                            existing_control_workspaces=1,
                        ),
                        "observed": {**base_observed, **changes},
                    }
                )
                self.assertFalse(report["dispatch_allowed"])
                self.assertTrue(report["preserve_scene"])
                self.assertIn(blocker, report["blockers"])

    def test_poisoned_expected_cannot_override_authoritative_admission_evidence(self) -> None:
        payload = create_snapshot(
            existing_campaign_agents=1,
            existing_control_workspaces=1,
        )
        payload["expected"] = {
            "parent_agent_id": "foreign-parent",
            "branch": "gwo/campaign/foreign-campaign",
            "head_sha": "f" * 40,
        }
        payload["observed"] = {
            "agent_id": "agent-campaign",
            "parent_agent_id": "foreign-parent",
            "relationship": "subagent",
            "agent_title": "Campaign · c-017-lifecycle · lifecycle",
            "workspace_title": "Campaign · c-017-lifecycle · lifecycle",
            "workspace_kind": "worktree",
            "worktree_slug": "campaign-c-017-lifecycle",
            "branch": "gwo/campaign/foreign-campaign",
            "head_sha": "f" * 40,
            "provider": "opencode",
            "model": "codexhub-kimi/k3",
            "mode": "build",
            "labels": {
                "repository": "owner/repo",
                "campaign_id": "c-017-lifecycle",
                "role": "orchestrator",
                "gwo.version": "4.3",
            },
            "tracked_changes": False,
            "unique_commits": 0,
            "branch_local_only": True,
            "read_back": True,
        }

        report = CAMPAIGN.validate_readback(payload)

        self.assertFalse(report["admitted"])
        self.assertIn("campaign-readback-parent-agent-id-mismatch", report["blockers"])
        self.assertIn("campaign-readback-branch-mismatch", report["blockers"])

    def test_poisoned_coordinator_or_base_repository_fails_closed(self) -> None:
        poisoned = create_snapshot()
        poisoned["repository_coordinator"]["relationship"] = "subagent"
        poisoned["repository_coordinator"]["parent_agent_id"] = "foreign-parent"
        wrong_base = create_snapshot()
        wrong_base["base"]["repository"] = "other/repo"

        coordinator_report = CAMPAIGN.plan_create(poisoned)
        base_report = CAMPAIGN.plan_create(wrong_base)

        self.assertIn(
            "repository-coordinator-parentage-invalid", coordinator_report["blockers"]
        )
        self.assertIn("campaign-base-repository-mismatch", base_report["blockers"])


if __name__ == "__main__":
    unittest.main()
