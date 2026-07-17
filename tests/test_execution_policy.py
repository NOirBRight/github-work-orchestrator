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
    spec.loader.exec_module(module)
    return module


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
        report = POLICY.capacity_report(
            orchestrators_for_activity=0, active_agents=3, max_active_agents=4
        )
        self.assertTrue(report["can_add_orchestrator"])
        self.assertTrue(report["can_add_agent"])
        self.assertEqual(1, report["agent_slots_remaining"])
        full = POLICY.capacity_report(
            orchestrators_for_activity=1, active_agents=4, max_active_agents=4
        )
        self.assertFalse(full["can_add_orchestrator"])
        self.assertFalse(full["can_add_agent"])

    def test_cleanup_is_event_triggered_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            eligible = POLICY.cleanup_plan(
                event="merged",
                seconds_since_event=20,
                worktree=str(Path(temporary).resolve()),
                branch="work/issue-7-example",
                agent_id="agent-worker-7",
                agent_idle=True,
                worktree_clean=True,
                durable=True,
                ownership_unambiguous=True,
                branch_merged=True,
            )
            self.assertEqual("eligible", eligible["status"])
            self.assertEqual(
                [
                    "archive-paseo-agent",
                    "archive-paseo-worktree",
                    "delete-merged-remote-branch",
                ],
                [item["action"] for item in eligible["actions"]],
            )
            protected = POLICY.cleanup_plan(
                event="stopped",
                seconds_since_event=400,
                worktree=str(Path(temporary).resolve()),
                branch=None,
                agent_id="agent-worker-7",
                agent_idle=False,
                worktree_clean=False,
                durable=False,
                ownership_unambiguous=False,
                branch_merged=False,
            )
            self.assertEqual("protected", protected["status"])
            self.assertTrue(protected["overdue"])
            self.assertEqual([], protected["actions"])


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
