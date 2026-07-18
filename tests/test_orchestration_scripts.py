from __future__ import annotations

import argparse
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


ready_frontier = load_module("ready_frontier")
reconcile_issue_state = load_module("reconcile_issue_state")
validate_issue_state = load_module("validate_issue_state")


def issue(
    number: int,
    *,
    labels: tuple[str, ...] = ("ready-for-agent",),
    assignees: tuple[str, ...] = (),
    body: str = "",
    open_dependencies: tuple[int, ...] = (),
    dependencies_complete: bool = True,
):
    return {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://example.test/issues/{number}",
        "labels": [{"name": label} for label in labels],
        "assignees": [{"login": login} for login in assignees],
        "body": body,
        "open_dependencies": list(open_dependencies),
        "dependencies_complete": dependencies_complete,
    }


def v3_contract(*, architecture: str = "resolved") -> str:
    return f"""Execution-Contract: v3
Execution-Mode: paseo-agent
Agent-Role: implementation
Role-Category: impl
Integration-Branch: dev
Done-When: PR is green and accepted
Verification-Class: standard
Verification-Commands: python -m unittest discover -s tests -v
Manual-Evidence: none
Architecture-Decision: {architecture}
Review-Owner: orchestrator
"""


class ReadyFrontierTests(unittest.TestCase):
    def test_classifies_ready_claimed_blocked_and_invalid(self) -> None:
        issues = [
            issue(1, body=v3_contract()),
            issue(2, assignees=("worker",), body=v3_contract()),
            issue(3, body=v3_contract(), open_dependencies=(9,)),
            issue(4, labels=("ready-for-agent", "needs-info"), body=v3_contract()),
        ]
        result = ready_frontier.classify_frontier(issues)
        self.assertEqual([1], [item["number"] for item in result["ready"]])
        self.assertEqual([2], [item["number"] for item in result["claimed"]])
        self.assertEqual([3], [item["number"] for item in result["blocked"]])
        self.assertEqual([4], [item["number"] for item in result["invalid"]])

    def test_incomplete_native_dependency_readback_fails_closed(self) -> None:
        result = ready_frontier.classify_frontier(
            [issue(5, body=v3_contract(), dependencies_complete=False)]
        )
        self.assertEqual([5], [item["number"] for item in result["blocked"]])
        self.assertEqual(
            "native-graphql-incomplete", result["blocked"][0]["dependency_check"]
        )

    def test_expected_hotset_uses_strict_repository_relative_bullets(self) -> None:
        body = """## Expected hotset
- `src/api`
- `tests/api`

## Acceptance criteria
- done
"""
        self.assertEqual(["src/api", "tests/api"], ready_frontier.expected_hotset(body))
        self.assertIsNone(
            ready_frontier.expected_hotset("## Expected hotset\n- `../outside`")
        )

    def test_graphql_snapshot_exposes_dependency_ids_without_per_issue_rest(
        self,
    ) -> None:
        response = {
            "data": {
                "repository": {
                    "issues": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "number": 7,
                                "title": "Issue 7",
                                "url": "https://example.test/issues/7",
                                "state": "OPEN",
                                "body": v3_contract(),
                                "assignees": {"nodes": []},
                                "labels": {"nodes": [{"name": "ready-for-agent"}]},
                                "blockedBy": {
                                    "pageInfo": {"hasNextPage": False},
                                    "nodes": [{"number": 3, "state": "OPEN"}],
                                },
                            }
                        ],
                    }
                }
            }
        }
        calls = []

        def runner(arguments, cwd=None):
            calls.append(arguments)
            return __import__("json").dumps(response)

        issues = ready_frontier.fetch_issue_snapshot(
            "owner/repo", None, limit=100, runner=runner
        )

        self.assertEqual([3], issues[0]["open_dependencies"])
        self.assertEqual(1, len(calls))
        self.assertEqual(["api", "graphql"], calls[0][:2])


class ReconciliationParserTests(unittest.TestCase):
    def test_parse_exact_supports_empty_set(self) -> None:
        self.assertEqual((12, set()), reconcile_issue_state.parse_exact("12="))

    def test_dependency_rejects_self_edge(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            reconcile_issue_state.parse_edge("12=12")


class ExecutionContractParserTests(unittest.TestCase):
    def test_v3_ready_contract_is_accepted(self) -> None:
        candidate = issue(6, body=v3_contract())
        self.assertEqual(
            [], validate_issue_state.execution_contract_findings(candidate)
        )

    def test_v2_contract_is_rejected(self) -> None:
        candidate = issue(
            7,
            body=v3_contract().replace(
                "Execution-Contract: v3", "Execution-Contract: v2"
            ),
        )
        findings = validate_issue_state.execution_contract_findings(candidate)
        self.assertIn("invalid-execution-field", [item["code"] for item in findings])

    def test_missing_contract_is_a_migration_warning(self) -> None:
        findings = validate_issue_state.execution_contract_findings(issue(8))
        self.assertEqual(
            ["legacy-execution-contract"], [item["code"] for item in findings]
        )
        self.assertEqual("warning", findings[0]["severity"])

    def test_open_architecture_decision_is_not_ready(self) -> None:
        findings = validate_issue_state.execution_contract_findings(
            issue(9, body=v3_contract(architecture="discussion-required"))
        )
        self.assertIn("open-architecture-decision", [item["code"] for item in findings])

    def test_role_category_pair_must_match(self) -> None:
        findings = validate_issue_state.execution_contract_findings(
            issue(
                10,
                body=v3_contract().replace(
                    "Role-Category: impl", "Role-Category: audit"
                ),
            )
        )
        self.assertIn("role-category-mismatch", [item["code"] for item in findings])


if __name__ == "__main__":
    unittest.main()
