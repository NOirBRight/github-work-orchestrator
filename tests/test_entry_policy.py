from __future__ import annotations

import importlib.util
import json
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


entry_policy = load_module("entry_policy")


def coordinator(status: str = "idle") -> dict:
    return {
        "agent_id": "22222222-2222-4222-8222-222222222222",
        "repository": "owner/repo",
        "status": status,
        "role": "repository-coordinator",
        "relationship": "root",
        "parent_agent_id": None,
        "labels": {
            "repository": "owner/repo",
            "role": "repository-coordinator",
        },
        "read_back": True,
    }


def action_names(report: dict) -> list[str]:
    return [item["action"] for item in report["actions"]]


def base_snapshot() -> dict:
    return {
        "schema_version": 1,
        "repository": "owner/repo",
        "current_agent": {
            "agent_id": "11111111-1111-4111-8111-111111111111",
            "relationship": "root",
            "workspace_class": "stable-repository",
            "repository_readback": True,
            "dispatch_bound": False,
            "branch": "topic/local-notes",
            "dirty": True,
        },
        "repository_coordinators": [],
        "stable_dev_workspaces": [
            {
                "workspace_id": "dev-control",
                "repository": "owner/repo",
                "branch": "dev",
                "stable": True,
            }
        ],
    }


class EntryPolicyTests(unittest.TestCase):
    def test_sanitized_slow_trace_is_bounded_by_v43_relay_contract(self) -> None:
        trace = json.loads(
            (ROOT / "tests" / "fixtures" / "relay_slow_trace.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(54, trace["tool_calls_before_first_forward"])
        self.assertAlmostEqual(799.412, trace["elapsed_to_first_forward_seconds"])
        self.assertGreater(trace["non_tool_ratio"], 0.95)
        self.assertEqual(5, entry_policy.MAX_RELAY_EXTERNAL_ACTIONS)
        self.assertEqual(60, trace["v43_local_forward_target_seconds"])

    def test_stable_dirty_non_dev_root_is_promoted_in_place(self) -> None:
        result = entry_policy.entry_plan(base_snapshot())

        self.assertEqual("eligible", result["status"])
        self.assertEqual("promote-current", result["route"])
        self.assertTrue(result["automatic_execution"])
        self.assertEqual("dev-control", result["integration_control_workspace_id"])
        self.assertIn("promote-current-agent", action_names(result))
        self.assertEqual("Repo · owner/repo · dev", result["ui_names"]["workspace"])

    def test_execution_worktree_is_not_promoted(self) -> None:
        snapshot = base_snapshot()
        snapshot["current_agent"].update(
            {
                "workspace_class": "issue-worktree",
                "dispatch_bound": True,
                "branch": "work/issue-151-a2",
            }
        )

        result = entry_policy.entry_plan(snapshot)

        self.assertEqual("eligible", result["status"])
        self.assertEqual("create-in-stable-dev-workspace", result["route"])
        self.assertNotIn("promote-current-agent", action_names(result))

    def test_execution_worktree_without_unique_dev_workspace_fails_closed(self) -> None:
        snapshot = base_snapshot()
        snapshot["current_agent"].update(
            {
                "workspace_class": "issue-worktree",
                "dispatch_bound": True,
                "branch": "work/issue-151-a2",
            }
        )
        snapshot["stable_dev_workspaces"] = []

        result = entry_policy.entry_plan(snapshot)

        self.assertEqual("protected", result["status"])
        self.assertFalse(result["automatic_execution"])
        self.assertEqual([], result["actions"])
        self.assertIn("stable-dev-workspace-not-unique", result["blockers"])

    def test_existing_coordinator_routes_current_task_to_bounded_relay(self) -> None:
        snapshot = base_snapshot()
        snapshot["repository_coordinators"] = [coordinator()]
        snapshot["request"] = {
            "signal_id": "repo-request-53d395a4-793d-43d3-80f0-0f3b53acd94d",
            "sequence": 1,
            "summary": "Please reconcile issue 151.",
            "original_message_sha256": "a" * 64,
        }

        result = entry_policy.entry_plan(snapshot)

        self.assertEqual("relay", result["status"])
        self.assertEqual("relay-existing", result["route"])
        self.assertEqual(
            [
                "rename-current-agent-as-relay",
                "post-operator-request",
                "read-coordinator-status-once",
            ],
            action_names(result),
        )
        self.assertLessEqual(result["external_action_budget"], 5)
        self.assertNotIn("read-github-frontier", action_names(result))
        self.assertNotIn("list-worktrees", action_names(result))
        self.assertEqual("Relay · owner/repo → Coordinator", result["ui_names"]["agent"])

    def test_duplicate_coordinator_fails_closed_without_relay(self) -> None:
        snapshot = base_snapshot()
        second = coordinator()
        second["agent_id"] = "33333333-3333-4333-8333-333333333333"
        snapshot["repository_coordinators"] = [coordinator(), second]

        result = entry_policy.entry_plan(snapshot)

        self.assertEqual("protected", result["status"])
        self.assertEqual([], result["actions"])
        self.assertIn("repository-coordinator-conflict", result["blockers"])

    def test_sensitive_or_absolute_request_summary_is_rejected(self) -> None:
        snapshot = base_snapshot()
        snapshot["repository_coordinators"] = [coordinator()]
        snapshot["request"] = {
            "signal_id": "repo-request-53d395a4-793d-43d3-80f0-0f3b53acd94d",
            "sequence": 1,
            "summary": r"Use token sk-secret from C:\\Users\\operator\\notes.txt",
            "original_message_sha256": "b" * 64,
        }

        result = entry_policy.entry_plan(snapshot)

        self.assertEqual("protected", result["status"])
        self.assertEqual([], result["actions"])
        self.assertIn("request-summary-sensitive", result["blockers"])

    def test_delimiter_bypass_paths_and_additional_secret_shapes_are_rejected(self) -> None:
        for summary in (
            r"file=C:\Users\operator\notes.txt",
            "file=C://Users/operator/notes.txt",
            "path=/home/operator/notes.txt",
            "path=/opt/company/repo/file.py",
            "path=/workspace/repo/file.py",
            "url=https://example.com,/home/operator/notes.txt",
            "url=https://example.com|C://Users/operator/notes.txt",
            r"url=https://example.com;file=C:\Users\operator\notes.txt",
            r"share=\\server\private\notes.txt",
            "authorization=Bearer abc.def.ghi",
            "token=github_pat_example123",
            "key=AKIAABCDEFGHIJKLMNOP",
            "-----BEGIN PRIVATE KEY-----",
        ):
            with self.subTest(summary=summary):
                snapshot = base_snapshot()
                snapshot["repository_coordinators"] = [coordinator()]
                snapshot["request"] = {
                    "signal_id": "repo-request-53d395a4-793d-43d3-80f0-0f3b53acd94d",
                    "sequence": 1,
                    "summary": summary,
                    "original_message_sha256": "b" * 64,
                }
                result = entry_policy.entry_plan(snapshot)
                self.assertIn("request-summary-sensitive", result["blockers"])

    def test_https_url_is_not_misclassified_as_a_windows_drive_path(self) -> None:
        snapshot = base_snapshot()
        snapshot["repository_coordinators"] = [coordinator()]
        snapshot["request"] = {
            "signal_id": "repo-request-53d395a4-793d-43d3-80f0-0f3b53acd94d",
            "sequence": 1,
            "summary": "Please inspect https://github.com/owner/repo/issues/151",
            "original_message_sha256": "b" * 64,
        }

        result = entry_policy.entry_plan(snapshot)

        self.assertEqual("relay", result["status"])
        self.assertEqual([], result["blockers"])

    def test_foreign_or_mislabeled_coordinator_is_not_a_relay_target(self) -> None:
        snapshot = base_snapshot()
        invalid = coordinator()
        invalid["relationship"] = "subagent"
        invalid["parent_agent_id"] = "foreign-parent"
        snapshot["repository_coordinators"] = [invalid]

        result = entry_policy.entry_plan(snapshot)

        self.assertEqual("protected", result["status"])
        self.assertIn("repository-coordinator-evidence-invalid", result["blockers"])


class WakePolicyTests(unittest.TestCase):
    def snapshot(self, status: str) -> dict:
        return {
            "schema_version": 1,
            "repository": "owner/repo",
            "request_signal_id": "repo-request-53d395a4-793d-43d3-80f0-0f3b53acd94d",
            "coordinator": coordinator(status),
        }

    def test_idle_coordinator_receives_signal_id_only(self) -> None:
        result = entry_policy.wake_plan(self.snapshot("idle"))

        self.assertEqual("send-signal-only", result["action"])
        self.assertEqual(
            "repo-request-53d395a4-793d-43d3-80f0-0f3b53acd94d",
            result["prompt"],
        )

    def test_running_coordinator_is_not_interrupted(self) -> None:
        result = entry_policy.wake_plan(self.snapshot("running"))

        self.assertEqual("do-not-disturb", result["action"])
        self.assertIsNone(result["prompt"])

    def test_error_coordinator_requires_operator(self) -> None:
        result = entry_policy.wake_plan(self.snapshot("error"))

        self.assertEqual("protected", result["status"])
        self.assertEqual("escalate", result["action"])
        self.assertFalse(result["automatic_execution"])


if __name__ == "__main__":
    unittest.main()
