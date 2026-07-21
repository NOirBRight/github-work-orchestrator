#!/usr/bin/env python3
"""TDD tests for the GWO V7 DAG plan schema v1 and `gwo guard check-dag`."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "github-work-orchestrator" / "scripts" / "gwo.py"
DAG_SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "github-work-orchestrator" / "scripts" / "gwo_dag.py"


def _plan(**kwargs: Any) -> dict[str, Any]:
    """Build a minimal valid DAG plan; kwargs override defaults."""
    plan: dict[str, Any] = {
        "schema_version": 1,
        "repository": "owner/repo",
        "nodes": [
            {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "fast", "hotset": ["src/auth"]},
        ],
        "edges": [],
        "capacity": {
            "global_agent_limit": 13,
            "global_active_agents": 1,
            "group_limits": {"g-auth": {"limit": 6, "active": 1}},
        },
        "github_dependencies": {},
        "contract_dependencies": {},
    }
    plan.update(kwargs)
    return plan


def _run_check_dag(plan: dict[str, Any]) -> dict[str, Any]:
    """Invoke `gwo guard check-dag --plan -` and parse JSON stdout."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repository", "owner/repo", "guard", "check-dag", "--plan", "-"],
        input=json.dumps(plan),
        capture_output=True,
        text=True,
        cwd=str(SCRIPT.parent),
    )
    payload = json.loads(result.stdout) if result.stdout.strip() else {}
    return {"returncode": result.returncode, **payload}


def test_dag_script_compiles() -> None:
    import py_compile
    py_compile.compile(str(DAG_SCRIPT), doraise=True)


def test_guard_check_dag_accepts_valid_plan() -> None:
    result = _run_check_dag(_plan())
    assert result["returncode"] == 0
    assert result.get("ok") is True
    assert result.get("ready_frontier") == ["t-1"]
    assert result.get("rejection_codes") == []


def test_guard_rejects_cycle() -> None:
    plan = _plan(
        nodes=[
            {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "fast", "hotset": []},
            {"id": "t-2", "kind": "issue", "issue": 2, "group": "g-auth", "risk": "fast", "hotset": []},
        ],
        edges=[["t-1", "t-2"], ["t-2", "t-1"]],
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 1
    assert result.get("ok") is False
    assert "dag-cycle" in result.get("rejection_codes", [])


def test_guard_rejects_missing_native_dependency() -> None:
    plan = _plan(
        nodes=[
            {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "fast", "hotset": ["src/a"]},
            {"id": "t-2", "kind": "issue", "issue": 2, "group": "g-auth", "risk": "fast", "hotset": ["src/b"]},
        ],
        edges=[["t-1", "t-2"]],
        github_dependencies={"t-2": [1]},
        contract_dependencies={"t-2": [1]},
    )
    # Remove the native dependency to trigger the GitHub mismatch.
    plan["github_dependencies"] = {}
    result = _run_check_dag(plan)
    assert result["returncode"] == 1
    assert "github-dependency-mismatch" in result.get("rejection_codes", [])


def test_guard_rejects_missing_contract_dependency() -> None:
    plan = _plan(
        nodes=[
            {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "fast", "hotset": ["src/a"]},
            {"id": "t-2", "kind": "issue", "issue": 2, "group": "g-auth", "risk": "fast", "hotset": ["src/b"]},
        ],
        edges=[["t-1", "t-2"]],
        github_dependencies={"t-2": [1]},
        contract_dependencies={"t-2": [1]},
    )
    # Remove the contract dependency to trigger the contract mismatch.
    plan["contract_dependencies"] = {}
    result = _run_check_dag(plan)
    assert result["returncode"] == 1
    assert "contract-dependency-mismatch" in result.get("rejection_codes", [])


def test_guard_rejects_concurrent_hotset_overlap() -> None:
    plan = _plan(
        nodes=[
                {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "fast", "hotset": ["src/auth"]},
                {"id": "t-2", "kind": "issue", "issue": 2, "group": "g-auth", "risk": "fast", "hotset": ["src/auth/login.py"]},

        ],
        edges=[],
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 1
    assert "hotset-conflict" in result.get("rejection_codes", [])


def test_missing_hotset_is_repository_exclusive() -> None:
    plan = _plan(
        nodes=[
                {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "fast"},
                {"id": "t-2", "kind": "issue", "issue": 2, "group": "g-auth", "risk": "fast", "hotset": ["src/auth"]},

        ],
        edges=[],
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 1
    assert "hotset-conflict" in result.get("rejection_codes", [])


def test_guard_rejects_infeasible_global_capacity() -> None:
    plan = _plan(capacity={"global_agent_limit": 0, "global_active_agents": 0, "group_limits": {}})
    result = _run_check_dag(plan)
    assert result["returncode"] == 1
    assert "capacity-global-limit-infeasible" in result.get("rejection_codes", [])


def test_guard_rejects_infeasible_group_capacity() -> None:
    plan = _plan(
        capacity={
            "global_agent_limit": 13,
            "global_active_agents": 1,
            "group_limits": {"g-auth": {"limit": 0, "active": 0}},
        }
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 1
    assert "capacity-group-limit-infeasible" in result.get("rejection_codes", [])


def test_guard_rejects_overactive_group_capacity() -> None:
    plan = _plan(
        capacity={
            "global_agent_limit": 13,
            "global_active_agents": 1,
            "group_limits": {"g-auth": {"limit": 1, "active": 2}},
        }
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 1
    assert "capacity-group-already-exceeded" in result.get("rejection_codes", [])


def test_guard_rejects_missing_standard_review() -> None:
    plan = _plan(
        nodes=[
            {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "standard", "hotset": []},
        ],
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 1
    assert "review-missing-standard" in result.get("rejection_codes", [])


def test_guard_accepts_standard_with_review_node() -> None:
    plan = _plan(
        nodes=[
            {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "standard", "hotset": []},
            {"id": "r-1", "kind": "review", "target": "t-1", "axis": "combined"},
        ],
        edges=[["t-1", "r-1"]],
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 0
    assert result.get("ok") is True


def test_guard_rejects_missing_strict_review_pair() -> None:
    plan = _plan(
        nodes=[
            {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "strict", "hotset": []},
            {"id": "r-1-q", "kind": "review", "target": "t-1", "axis": "quality"},
        ],
        edges=[["t-1", "r-1-q"]],
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 1
    assert "review-missing-strict-spec" in result.get("rejection_codes", [])


def test_guard_accepts_strict_with_independent_review_pair() -> None:
    plan = _plan(
        nodes=[
            {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "strict", "hotset": []},
            {"id": "r-1-s", "kind": "review", "target": "t-1", "axis": "spec"},
            {"id": "r-1-q", "kind": "review", "target": "t-1", "axis": "quality"},
        ],
        edges=[["t-1", "r-1-s"], ["t-1", "r-1-q"]],
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 0
    assert result.get("ok") is True


def test_guard_rejects_non_serial_integration_chain() -> None:
    plan = _plan(
        nodes=[
            {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "fast", "hotset": []},
            {"id": "t-2", "kind": "issue", "issue": 2, "group": "g-auth", "risk": "fast", "hotset": []},
            {"id": "i-1", "kind": "integration", "target": "t-1"},
            {"id": "i-2", "kind": "integration", "target": "t-2"},
        ],
        edges=[["t-1", "i-1"], ["t-2", "i-2"]],
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 1
    assert "integration-chain-not-serial" in result.get("rejection_codes", [])


def test_guard_accepts_serial_integration_chain() -> None:
    plan = _plan(
        nodes=[
            {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "fast", "hotset": ["src/a"]},
            {"id": "t-2", "kind": "issue", "issue": 2, "group": "g-auth", "risk": "fast", "hotset": ["src/b"]},
            {"id": "i-1", "kind": "integration", "target": "t-1"},
            {"id": "i-2", "kind": "integration", "target": "t-2"},
        ],
        edges=[["t-1", "i-1"], ["i-1", "i-2"], ["t-2", "i-2"]],
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 0
    assert result.get("ok") is True


def test_guard_computes_ready_frontier_with_dependencies() -> None:
    plan = _plan(
        nodes=[
            {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "fast", "hotset": ["src/a"]},
            {"id": "t-2", "kind": "issue", "issue": 2, "group": "g-auth", "risk": "fast", "hotset": ["src/b"]},
        ],
        edges=[["t-1", "t-2"]],
        github_dependencies={"t-2": [1]},
        contract_dependencies={"t-2": [1]},
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 0
    assert result.get("ready_frontier") == ["t-1"]


def test_guard_ready_frontier_excludes_blocked_by_capacity() -> None:
    plan = _plan(
        nodes=[
            {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "fast", "hotset": ["src/a"]},
            {"id": "t-2", "kind": "issue", "issue": 2, "group": "g-auth", "risk": "fast", "hotset": ["src/b"]},
        ],
        edges=[],
        capacity={
            "global_agent_limit": 13,
            "global_active_agents": 13,
            "group_limits": {"g-auth": {"limit": 6, "active": 6}},
        },
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 0
    assert result.get("ok") is True
    assert result.get("ready_frontier") == []


def test_guard_rejects_unknown_node_reference_in_edge() -> None:
    plan = _plan(edges=[["t-1", "t-missing"]])
    result = _run_check_dag(plan)
    assert result["returncode"] == 1
    assert "edge-unknown-node" in result.get("rejection_codes", [])


def test_guard_rejects_review_without_target() -> None:
    plan = _plan(
        nodes=[
            {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "standard", "hotset": []},
            {"id": "r-1", "kind": "review", "axis": "combined"},
        ],
        edges=[["t-1", "r-1"]],
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 1
    assert "review-target-missing" in result.get("rejection_codes", [])


def test_guard_rejects_review_targeting_non_issue() -> None:
    plan = _plan(
        nodes=[
            {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "standard", "hotset": []},
            {"id": "t-2", "kind": "issue", "issue": 2, "group": "g-auth", "risk": "fast", "hotset": ["src/b"]},
            {"id": "r-1", "kind": "review", "target": "r-1", "axis": "combined"},
        ],
        edges=[["t-1", "r-1"]],
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 1
    assert "review-target-not-issue" in result.get("rejection_codes", [])


def test_guard_rejects_integration_without_target() -> None:
    plan = _plan(
        nodes=[
            {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": "fast", "hotset": []},
            {"id": "i-1", "kind": "integration"},
        ],
        edges=[["t-1", "i-1"]],
    )
    result = _run_check_dag(plan)
    assert result["returncode"] == 1
    assert "integration-target-missing" in result.get("rejection_codes", [])


@pytest.mark.parametrize(
    "risk,review_axes,should_pass",
    [
        ("fast", [], True),
        ("standard", ["combined"], True),
        ("standard", [], False),
        ("strict", ["spec", "quality"], True),
        ("strict", ["combined"], False),
        ("strict", ["spec"], False),
    ],
)
def test_risk_review_combinations(risk: str, review_axes: list[str], should_pass: bool) -> None:
    nodes: list[dict[str, Any]] = [
        {"id": "t-1", "kind": "issue", "issue": 1, "group": "g-auth", "risk": risk, "hotset": []},
    ]
    edges: list[list[str]] = []
    for index, axis in enumerate(review_axes, start=1):
        node_id = f"r-{index}"
        nodes.append({"id": node_id, "kind": "review", "target": "t-1", "axis": axis})
        edges.append(["t-1", node_id])
    result = _run_check_dag(_plan(nodes=nodes, edges=edges))
    assert result["returncode"] == (0 if should_pass else 1)


def _v6_snapshot(**kwargs: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "repository": "owner/repo",
        "campaign_id": "c-test01",
        "campaign_agent_id": "campaign-test",
        "case_sensitive_paths": True,
        "max_dispatch_attempts": 3,
        "campaign_hotset": [],
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
            "spec": {"exists": False, "reusable": True, "axis": "spec", "read_back": True},
            "quality": {"exists": False, "reusable": True, "axis": "quality", "read_back": True},
        },
        "active_dispatches": [],
        "active_external_hotsets": {},
        "control_plane": {
            "repository_coordinators": 1,
            "campaign_orchestrators": 1,
            "scope_readback": True,
            "provider_binding_readback": True,
        },
        "candidates": [],
    }
    snapshot.update(kwargs)
    return snapshot


def _normalize_v6_hotset(value: Any) -> list[str]:
    """Strip trailing slashes from V6 hotset entries so they pass V7 normalization."""
    if not value:
        return []
    return [entry.rstrip("/") for entry in value if isinstance(entry, str)]


def _v6_to_dag(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Convert a V6 plan-wave snapshot into a V7 DAG plan for compatibility testing."""
    group = snapshot["campaign_id"]
    nodes: list[dict[str, Any]] = []
    edges: list[list[str]] = []
    github_dependencies: dict[str, list[int]] = {}
    contract_dependencies: dict[str, list[int]] = {}
    for item in snapshot["candidates"]:
        node_id = f"t-{item['issue']}"
        nodes.append(
            {
                "id": node_id,
                "kind": "issue",
                "issue": item["issue"],
                "group": group,
                "risk": item["verification_class"],
                "hotset": _normalize_v6_hotset(item.get("hotset", [])),
            }
        )
        deps = item.get("open_dependencies", [])
        if deps:
            # V6 open_dependencies are issues that must complete *before* this
            # node, so the edge direction in the DAG is dep -> node.
            github_dependencies[node_id] = list(deps)
            contract_dependencies[node_id] = list(deps)
            for dep in deps:
                edges.append([f"t-{dep}", node_id])
    group_limits = {group: {"limit": snapshot["capacity"]["campaign_agent_limit"], "active": snapshot["capacity"]["campaign_active_agents"]}}
    return {
        "schema_version": 1,
        "repository": snapshot["repository"],
        "nodes": nodes,
        "edges": edges,
        "capacity": {
            "global_agent_limit": snapshot["capacity"]["global_agent_limit"],
            "global_active_agents": snapshot["capacity"]["global_active_agents"],
            "group_limits": group_limits,
        },
        "github_dependencies": github_dependencies,
        "contract_dependencies": contract_dependencies,
    }


def test_v6_compatibility_single_ready_wave() -> None:
    """Every V6 plan-wave accepted wave is accepted as a V7 DAG ready frontier."""
    snapshot = _v6_snapshot(
        candidates=[
            {
                    "issue": 10,
                    "rank": 10,
                    "assignees": [],
                    "open_dependencies": [],
                    "hotset": ["src/a"],
                    "verification_class": "fast",
                    "attempt": 1,
                    "slug": "auth",
                    "lifecycle": "ready-for-agent",
                    "contract_valid": True,
                    "dispatch_readback": {
                        "dispatch_id": "dispatch-issue-10-a1",
                        "active_matches": 0,
                        "archived_matches": 0,
                        "read_back": True,
                    },
                },
                {
                    "issue": 11,
                    "rank": 11,
                    "assignees": [],
                    "open_dependencies": [],
                    "hotset": ["src/b"],
                    "verification_class": "fast",
                    "attempt": 1,
                    "slug": "billing",
                    "lifecycle": "ready-for-agent",
                    "contract_valid": True,
                    "dispatch_readback": {
                        "dispatch_id": "dispatch-issue-11-a1",
                        "active_matches": 0,
                        "archived_matches": 0,
                        "read_back": True,
                    },
                },
            ]
        )
    import json
    import subprocess
    import sys
    from pathlib import Path
    scheduler = Path(__file__).resolve().parents[1] / "skills" / "github-work-orchestrator" / "scripts" / "campaign_scheduler.py"
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as handle:
        json.dump(snapshot, handle)
        snapshot_path = handle.name
    try:
        v6_result = subprocess.run(
            [sys.executable, str(scheduler), "plan-wave", "--snapshot", snapshot_path],
            capture_output=True,
            text=True,
            cwd=str(scheduler.parent),
        )
    finally:
        Path(snapshot_path).unlink(missing_ok=True)
    v6_plan = json.loads(v6_result.stdout)
    assert v6_plan.get("ok") is True
    assert v6_plan["plan"]["automatic_execution"] is True
    dag_plan = _v6_to_dag(snapshot)
    dag_result = _run_check_dag(dag_plan)
    assert dag_result["returncode"] == 0
    assert dag_result.get("ok") is True
    accepted_issues = {item["issue"] for item in v6_plan["plan"]["dispatches"]}
    frontier_issues = {int(node_id.split("-", 1)[1]) for node_id in dag_result.get("ready_frontier", [])}
    assert accepted_issues == frontier_issues


def test_v6_compatibility_rejected_hotset_conflict_becomes_dag_rejection() -> None:
    snapshot = _v6_snapshot(
        candidates=[
            {
                "issue": 10,
                "rank": 10,
                "assignees": [],
                "open_dependencies": [],
                "hotset": ["src/auth"],
                "verification_class": "fast",
                "attempt": 1,
                "slug": "auth",
                "lifecycle": "ready-for-agent",
                "contract_valid": True,
                "dispatch_readback": {
                    "dispatch_id": "dispatch-issue-10-a1",
                    "active_matches": 0,
                    "archived_matches": 0,
                    "read_back": True,
                },
            },
            {
                "issue": 11,
                "rank": 11,
                "assignees": [],
                "open_dependencies": [],
                "hotset": ["src/auth/login.py"],
                "verification_class": "fast",
                "attempt": 1,
                "slug": "login",
                "lifecycle": "ready-for-agent",
                "contract_valid": True,
                "dispatch_readback": {
                    "dispatch_id": "dispatch-issue-11-a1",
                    "active_matches": 0,
                    "archived_matches": 0,
                    "read_back": True,
                },
            },
        ]
    )
    scheduler = Path(__file__).resolve().parents[1] / "skills" / "github-work-orchestrator" / "scripts" / "campaign_scheduler.py"
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as handle:
        json.dump(snapshot, handle)
        snapshot_path = handle.name
    try:
        v6_result = subprocess.run(
            [sys.executable, str(scheduler), "plan-wave", "--snapshot", snapshot_path],
            capture_output=True,
            text=True,
            cwd=str(scheduler.parent),
        )
    finally:
        Path(snapshot_path).unlink(missing_ok=True)
    v6_plan = json.loads(v6_result.stdout)
    assert v6_plan.get("ok") is True

    accepted = {item["issue"] for item in v6_plan["plan"]["dispatches"]}
    assert len(accepted) == 1
    dag_plan = _v6_to_dag(snapshot)
    dag_result = _run_check_dag(dag_plan)
    # V6 defers the overlapping issue; V7 DAG rejects the plan because
    # concurrently runnable nodes must have disjoint hotsets.
    assert dag_result["returncode"] == 1
    assert "hotset-conflict" in dag_result.get("rejection_codes", [])
    assert dag_result.get("ok") is False


def test_v6_compatibility_dependency_deferral() -> None:
    snapshot = _v6_snapshot(
        candidates=[
            {
                "issue": 10,
                "rank": 10,
                "assignees": [],
                "open_dependencies": [],
                "hotset": ["src/a"],
                "verification_class": "fast",
                "attempt": 1,
                "slug": "base",
                "lifecycle": "ready-for-agent",
                "contract_valid": True,
                "dispatch_readback": {
                    "dispatch_id": "dispatch-issue-10-a1",
                    "active_matches": 0,
                    "archived_matches": 0,
                    "read_back": True,
                },
            },
            {
                "issue": 11,
                "rank": 11,
                "assignees": [],
                "open_dependencies": [10],
                "hotset": ["src/b"],
                "verification_class": "fast",
                "attempt": 1,
                "slug": "depend",
                "lifecycle": "ready-for-agent",
                "contract_valid": True,
                "dispatch_readback": {
                    "dispatch_id": "dispatch-issue-11-a1",
                    "active_matches": 0,
                    "archived_matches": 0,
                    "read_back": True,
                },
            },
        ]
    )
    dag_plan = _v6_to_dag(snapshot)
    dag_result = _run_check_dag(dag_plan)
    assert dag_result["returncode"] == 0
    assert dag_result.get("ready_frontier") == ["t-10"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
