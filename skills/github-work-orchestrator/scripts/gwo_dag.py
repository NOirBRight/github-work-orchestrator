#!/usr/bin/env python3
"""DAG plan schema v1 and deterministic guard checks for GWO V7.

The DAG schema is intentionally separate from V6 campaign_scheduler.py so the
V6 planner stays packaged and behaviorally unchanged until Phase 3. This module
reuses V6 wave/Hotset/capacity semantics (hotset_policy, contract_schema) but
does not mutate lifecycle state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from hotset_policy import hotsets_overlap, normalize_hotset
from contract_schema import VERIFICATION_CLASSES


DAG_SCHEMA_VERSION = 1
REVIEW_AXES = {"spec", "quality"}
REQUIRED_NODE_FIELDS = {"id", "kind"}
ISSUE_REQUIRED = {"issue", "group", "risk"}
REVIEW_REQUIRED = {"target", "axis"}
INTEGRATION_REQUIRED = {"target"}


class DagValidationError(ValueError):
    """Raised when a plan fails a deterministic guard check."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _nonempty_text(name: str, value: Any, *, code: str | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DagValidationError(code or "invalid-text", f"{name} must be nonempty text")
    return value.strip()


def _positive_integer(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DagValidationError("invalid-positive-integer", f"{name} must be a positive integer")
    return value


def _nonnegative_integer(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DagValidationError("invalid-nonnegative-integer", f"{name} must be a nonnegative integer")
    return value


def _validate_structure(plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise DagValidationError("plan-not-object", "plan must be an object")
    if plan.get("schema_version") != DAG_SCHEMA_VERSION:
        raise DagValidationError("schema-version-mismatch", "schema_version must be 1")
    repository = _nonempty_text("repository", plan.get("repository"), code="repository-missing")
    nodes = plan.get("nodes")
    if not isinstance(nodes, list):
        raise DagValidationError("nodes-not-list", "nodes must be a list")
    edges = plan.get("edges")
    if not isinstance(edges, list):
        raise DagValidationError("edges-not-list", "edges must be a list")
    for index, edge in enumerate(edges):
        if not isinstance(edge, list) or len(edge) != 2 or not all(isinstance(item, str) for item in edge):
            raise DagValidationError("edge-shape-invalid", f"edges[{index}] must be a [source, target] text pair")
    capacity = plan.get("capacity")
    if not isinstance(capacity, dict):
        raise DagValidationError("capacity-not-object", "capacity must be an object")
    github_dependencies = plan.get("github_dependencies", {})
    if not isinstance(github_dependencies, dict):
        raise DagValidationError("github-dependencies-not-object", "github_dependencies must be an object")
    contract_dependencies = plan.get("contract_dependencies", {})
    if not isinstance(contract_dependencies, dict):
        raise DagValidationError("contract-dependencies-not-object", "contract_dependencies must be an object")
    return {
        "repository": repository,
        "nodes": nodes,
        "edges": edges,
        "capacity": capacity,
        "github_dependencies": github_dependencies,
        "contract_dependencies": contract_dependencies,
    }


def _validate_nodes(nodes: list[Any]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(nodes):
        if not isinstance(raw, dict):
            raise DagValidationError("node-not-object", f"nodes[{index}] must be an object")
        missing = REQUIRED_NODE_FIELDS - set(raw)
        if missing:
            raise DagValidationError("node-fields-missing", f"nodes[{index}] missing required fields: {sorted(missing)}")
        node_id = _nonempty_text(f"nodes[{index}].id", raw.get("id"), code="node-id-invalid")
        if node_id in by_id:
            raise DagValidationError("node-id-duplicate", f"duplicate node id: {node_id}")
        kind = _nonempty_text(f"nodes[{index}].kind", raw.get("kind"), code="node-kind-invalid")
        if kind not in {"issue", "review", "integration"}:
            raise DagValidationError("node-kind-invalid", f"nodes[{index}] has invalid kind")
        node: dict[str, Any] = {"id": node_id, "kind": kind}
        if kind == "issue":
            missing = ISSUE_REQUIRED - set(raw)
            if missing:
                raise DagValidationError("issue-fields-missing", f"nodes[{index}] missing issue fields: {sorted(missing)}")
            issue = _positive_integer(f"nodes[{index}].issue", raw.get("issue"))
            group = _nonempty_text(f"nodes[{index}].group", raw.get("group"), code="issue-group-invalid")
            risk = _nonempty_text(f"nodes[{index}].risk", raw.get("risk"), code="issue-risk-invalid")
            if risk not in VERIFICATION_CLASSES:
                raise DagValidationError("issue-risk-invalid", f"nodes[{index}] has invalid risk")
            hotset = raw.get("hotset")
            try:
                normalized_hotset = _normalize_issue_hotset(hotset)
            except ValueError as error:
                raise DagValidationError("issue-hotset-invalid", f"nodes[{index}] hotset invalid: {error}")
            node.update({
                "issue": issue,
                "group": group,
                "risk": risk,
                "hotset": normalized_hotset,
                "repository_wide": not normalized_hotset,
            })
        elif kind == "review":
            target = raw.get("target")
            axis = _nonempty_text(f"nodes[{index}].axis", raw.get("axis"), code="review-axis-invalid")
            if axis not in {"combined", "spec", "quality"}:
                raise DagValidationError("review-axis-invalid", f"nodes[{index}] has invalid review axis")
            if not isinstance(target, str) or not target.strip():
                raise DagValidationError("review-target-missing", f"review {node_id} missing target")
            node.update({"target": target.strip(), "axis": axis})
        elif kind == "integration":
            target = raw.get("target")
            if not isinstance(target, str) or not target.strip():
                raise DagValidationError("integration-target-missing", f"integration {node_id} missing target")
            node.update({"target": target.strip()})
        by_id[node_id] = node
    return by_id


def _validate_edges(edges: list[list[str]], nodes: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for edge in edges:
        source, target = edge
        if source not in nodes:
            raise DagValidationError("edge-unknown-node", f"edge source unknown: {source}")
        if target not in nodes:
            raise DagValidationError("edge-unknown-node", f"edge target unknown: {target}")
        if source == target:
            raise DagValidationError("dag-cycle", f"self-loop on {source}")
        adjacency[source].add(target)
    return adjacency


def _detect_cycle(nodes: dict[str, dict[str, Any]], adjacency: dict[str, set[str]]) -> None:
    """Raise DagValidationError if the directed graph contains a cycle.

    Iterative DFS avoids RecursionError on long chains (e.g. 1500 nodes).
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {node_id: WHITE for node_id in nodes}
    stack: list[str] = []
    path: list[str] = []

    def _emit_cycle(start_index: int, node_id: str) -> None:
        cycle = " -> ".join(path[start_index:] + [node_id])
        raise DagValidationError("dag-cycle", f"cycle detected: {cycle}")

    for start in sorted(nodes):
        if color[start] != WHITE:
            continue
        stack.append((start, iter(sorted(adjacency[start]))))
        color[start] = GRAY
        path.append(start)
        while stack:
            node_id, neighbors = stack[-1]
            try:
                neighbor = next(neighbors)
            except StopIteration:
                color[node_id] = BLACK
                stack.pop()
                path.pop()
                continue
            if color[neighbor] == GRAY:
                _emit_cycle(path.index(neighbor), neighbor)
            if color[neighbor] == WHITE:
                color[neighbor] = GRAY
                stack.append((neighbor, iter(sorted(adjacency[neighbor]))))
                path.append(neighbor)


def _dependency_consistency(
    nodes: dict[str, dict[str, Any]],
    adjacency: dict[str, set[str]],
    github_dependencies: dict[str, list[int]],
    contract_dependencies: dict[str, list[int]],
) -> None:
    """Validate that graph edges exactly match GitHub native and v3 contract deps.

    Edge semantics: an edge ``dep -> node`` means ``node`` depends on ``dep``,
    matching GitHub ``blockedBy`` / contract ``dispatch_after``. Therefore the
    dependency maps for a node list the *predecessors* (incoming edges), not its
    successors.
    """
    issue_numbers = {node["issue"] for node in nodes.values() if node["kind"] == "issue"}
    issue_number_to_id = {node["issue"]: node_id for node_id, node in nodes.items() if node["kind"] == "issue"}

    def _predecessors(node_id: str) -> set[str]:
        return {source for source, targets in adjacency.items() if node_id in targets and nodes[source]["kind"] == "issue"}

    def _expected_predecessors(deps: list[int]) -> set[str]:
        return {issue_number_to_id[dep] for dep in deps if isinstance(dep, int) and dep > 0 and dep in issue_numbers}

    for node_id, deps in github_dependencies.items():
        if node_id not in nodes:
            raise DagValidationError("github-dependency-unknown-node", f"github_dependencies references unknown node: {node_id}")
        node = nodes[node_id]
        if node["kind"] != "issue":
            continue
        expected = _expected_predecessors(deps)
        actual = _predecessors(node_id)
        if expected != actual:
            raise DagValidationError(
                "github-dependency-mismatch",
                f"node {node_id} GitHub dependency mismatch: predecessors={sorted(actual)} expected={sorted(expected)}",
            )
    for node_id, deps in contract_dependencies.items():
        if node_id not in nodes:
            raise DagValidationError("contract-dependency-unknown-node", f"contract_dependencies references unknown node: {node_id}")
        node = nodes[node_id]
        if node["kind"] != "issue":
            continue
        expected = _expected_predecessors(deps)
        actual = _predecessors(node_id)
        if expected != actual:
            raise DagValidationError(
                "contract-dependency-mismatch",
                f"node {node_id} contract dependency mismatch: predecessors={sorted(actual)} expected={sorted(expected)}",
            )
    # Every edge between issue nodes must be reflected in both dep maps as
    # predecessor entries on the dependent (target) node.
    for source, targets in adjacency.items():
        if nodes[source]["kind"] != "issue":
            continue
        source_issue = nodes[source]["issue"]
        for target in targets:
            if nodes[target]["kind"] != "issue":
                continue
            if source_issue not in github_dependencies.get(target, []):
                raise DagValidationError(
                    "github-dependency-mismatch",
                    f"node {target} edge from {source} not reflected in github_dependencies",
                )
            if source_issue not in contract_dependencies.get(target, []):
                raise DagValidationError(
                    "contract-dependency-mismatch",
                    f"node {target} edge from {source} not reflected in contract_dependencies",
                )


def _validate_capacity(capacity: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    global_limit_value = capacity.get("global_agent_limit")
    if not isinstance(global_limit_value, int) or isinstance(global_limit_value, bool) or global_limit_value <= 0:
        raise DagValidationError("capacity-global-limit-infeasible", "global_agent_limit must be a positive integer")
    global_limit = global_limit_value
    global_active = _nonnegative_integer("capacity.global_active_agents", capacity.get("global_active_agents"))
    if global_active > global_limit:
        raise DagValidationError("capacity-global-already-exceeded", "global_active_agents exceeds global_agent_limit")
    group_limits = capacity.get("group_limits", {})
    if not isinstance(group_limits, dict):
        raise DagValidationError("capacity-group-limits-not-object", "capacity.group_limits must be an object")
    normalized_groups: dict[str, dict[str, int]] = {}
    active_groups = {node["group"] for node in nodes.values() if node["kind"] == "issue"}
    for group, setting in group_limits.items():
        if not isinstance(setting, dict):
            raise DagValidationError("capacity-group-setting-invalid", f"capacity.group_limits[{group}] must be an object")
        limit_value = setting.get("limit")
        active_value = setting.get("active")
        if not isinstance(limit_value, int) or isinstance(limit_value, bool) or limit_value <= 0:
            raise DagValidationError(
                "capacity-group-limit-infeasible",
                f"group {group} limit must be a positive integer",
            )
        limit = limit_value
        active = _nonnegative_integer(f"capacity.group_limits[{group}].active", active_value)
        if active > limit:
            raise DagValidationError("capacity-group-already-exceeded", f"group {group} active exceeds limit")
        normalized_groups[group] = {"limit": limit, "active": active}
    for group in active_groups:
        if group not in normalized_groups:
            raise DagValidationError("capacity-group-missing", f"group {group} has no capacity entry")
    return {
        "global_agent_limit": global_limit,
        "global_active_agents": global_active,
        "group_limits": normalized_groups,
    }


def _validate_reviews(nodes: dict[str, dict[str, Any]], adjacency: dict[str, set[str]]) -> None:
    """Require risk-appropriate review nodes for each issue node."""
    review_nodes_by_target: dict[str, list[dict[str, Any]]] = {}
    for node in nodes.values():
        if node["kind"] == "review":
            review_nodes_by_target.setdefault(node["target"], []).append(node)
    # Validate every review node's target existence/kind first, regardless of
    # whether any issue node claims it, so invalid-target errors are reported
    # before risk-completeness errors.
    for node in nodes.values():
        if node["kind"] != "review":
            continue
        target = nodes.get(node["target"])
        if target is None:
            raise DagValidationError("review-target-missing", f"review {node['id']} target {node['target']} missing")
        if target["kind"] != "issue":
            raise DagValidationError("review-target-not-issue", f"review {node['id']} target is not an issue")
    for node in nodes.values():
        if node["kind"] != "issue":
            continue
        node_id = node["id"]
        reviews = review_nodes_by_target.get(node_id, [])
        for review in reviews:
            if review["id"] not in adjacency[node_id]:
                raise DagValidationError("review-not-successor", f"review {review['id']} is not a successor of {node_id}")
        axes = {review["axis"] for review in reviews}
        if node["risk"] == "fast":
            if reviews:
                raise DagValidationError("review-unexpected-for-fast", f"fast issue {node_id} must not have review nodes")
        elif node["risk"] == "standard":
            if axes != {"combined"}:
                raise DagValidationError("review-missing-standard", f"standard issue {node_id} requires one combined review node")
        elif node["risk"] == "strict":
            if axes != REVIEW_AXES:
                missing = sorted(REVIEW_AXES - axes)
                raise DagValidationError(f"review-missing-strict-{missing[0]}", f"strict issue {node_id} requires independent spec and quality review nodes")


def _validate_integration_chain(nodes: dict[str, dict[str, Any]], adjacency: dict[str, set[str]]) -> None:
    """Require all integration nodes to form one serial chain."""
    integration_nodes = [node for node in nodes.values() if node["kind"] == "integration"]
    if not integration_nodes:
        return
    integration_ids = {node["id"] for node in integration_nodes}

    # Verify each integration targets an issue and is a successor of that issue.
    for node in integration_nodes:
        target = nodes.get(node["target"])
        if target is None:
            raise DagValidationError("integration-target-missing", f"integration {node['id']} target missing")
        if target["kind"] != "issue":
            raise DagValidationError("integration-target-not-issue", f"integration {node['id']} target is not an issue")
        if node["id"] not in adjacency[target["id"]]:
            raise DagValidationError("integration-not-successor", f"integration {node['id']} is not a successor of {target['id']}")

    # Build the subgraph restricted to integration nodes.
    integration_adjacency: dict[str, set[str]] = {node_id: set() for node_id in integration_ids}
    for source in integration_ids:
        for target in adjacency[source]:
            if target in integration_ids:
                integration_adjacency[source].add(target)

    # Serial chain: every node has at most one incoming and one outgoing edge
    # and the graph is connected as one path.
    in_degree: dict[str, int] = {node_id: 0 for node_id in integration_ids}
    for source, targets in integration_adjacency.items():
        for target in targets:
            in_degree[target] += 1
    if any(degree > 1 for degree in in_degree.values()):
        raise DagValidationError("integration-chain-branching", "integration chain has a node with multiple incoming edges")
    if any(len(targets) > 1 for targets in integration_adjacency.values()):
        raise DagValidationError("integration-chain-branching", "integration chain has a node with multiple outgoing edges")

    # Exactly one head and one tail; all nodes reachable from head.
    heads = [node_id for node_id in integration_ids if in_degree[node_id] == 0]
    tails = [node_id for node_id in integration_ids if not integration_adjacency[node_id]]
    if len(heads) != 1 or len(tails) != 1:
        raise DagValidationError("integration-chain-not-serial", "integration nodes do not form exactly one serial chain")

    visited: set[str] = set()
    current = heads[0]
    while current in integration_ids:
        if current in visited:
            raise DagValidationError("dag-cycle", "cycle inside integration chain")
        visited.add(current)
        next_nodes = sorted(integration_adjacency[current])
        if not next_nodes:
            break
        current = next_nodes[0]
    if len(visited) != len(integration_ids):
        raise DagValidationError("integration-chain-not-serial", "integration chain is disconnected")


def _compute_reachability(adjacency: dict[str, set[str]], start_nodes: set[str]) -> dict[str, set[str]]:
    """Iteratively compute forward reachability for each start node.

    Avoids recursion so a 1500-node chain does not hit Python's recursion limit.
    """
    reachable: dict[str, set[str]] = {node_id: set() for node_id in start_nodes}
    for start in sorted(start_nodes):
        stack = list(sorted(adjacency[start]))
        seen = reachable[start]
        seen.update(adjacency[start])
        while stack:
            current = stack.pop()
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        # A node cannot reach itself for ordering comparisons.
        seen.discard(start)
    return reachable


def _validate_hotsets(nodes: dict[str, dict[str, Any]], adjacency: dict[str, set[str]], case_sensitive_paths: bool = True) -> None:
    """Reject concurrently runnable issue nodes with overlapping Hotsets."""
    # Two issue nodes are concurrently runnable if there is no path from either
    # to the other in either direction. Repository-wide exclusive nodes (empty
    # hotset) conflict with any other issue node.
    issue_nodes = [node for node in nodes.values() if node["kind"] == "issue"]
    issue_ids = {node["id"] for node in issue_nodes}
    reachable = _compute_reachability(adjacency, issue_ids)

    for left in issue_nodes:
        for right in issue_nodes:
            if left["id"] >= right["id"]:
                continue
            if right["id"] in reachable[left["id"]] or left["id"] in reachable[right["id"]]:
                continue
            if left["repository_wide"] or right["repository_wide"]:
                raise DagValidationError(
                    "hotset-conflict",
                    f"issue {left['id']} and {right['id']} are concurrent and one is repository-wide exclusive",
                )
            if hotsets_overlap(left["hotset"], right["hotset"], case_sensitive=case_sensitive_paths):
                raise DagValidationError(
                    "hotset-conflict",
                    f"issue {left['id']} and {right['id']} have overlapping hotsets",
                )


def _normalize_issue_hotset(value: Any) -> list[str]:
    """Normalize an issue hotset, treating missing/None as repository-exclusive."""
    if value is None:
        return []
    if not value:
        return []
    return normalize_hotset(value, allow_empty=True)


def _ready_frontier(nodes: dict[str, dict[str, Any]], adjacency: dict[str, set[str]], capacity: dict[str, Any]) -> list[str]:
    """Return the DAG ready frontier subject to available capacity."""
    in_degree: dict[str, int] = {node_id: 0 for node_id in nodes}
    for targets in adjacency.values():
        for target in targets:
            in_degree[target] += 1

    # Issue nodes are ready when all predecessors are satisfied. Review and
    # integration nodes run only after their target issue is done, but the
    # ready frontier here is about runnable work: issue nodes first.
    ready = sorted(node_id for node_id, node in nodes.items() if node["kind"] == "issue" and in_degree[node_id] == 0)

    # Apply capacity. Global cap is approximate at the plan level: the number of
    # concurrently runnable issue nodes cannot exceed remaining global slots or
    # remaining group slots. This mirrors V6 plan-wave semantics.
    global_remaining = max(0, capacity["global_agent_limit"] - capacity["global_active_agents"])
    selected: list[str] = []
    group_counts: dict[str, int] = {}
    for node_id in ready:
        node = nodes[node_id]
        group = node["group"]
        group_setting = capacity["group_limits"].get(group, {"limit": 0, "active": 0})
        group_remaining = max(0, group_setting["limit"] - group_setting["active"] - group_counts.get(group, 0))
        if len(selected) >= global_remaining or group_remaining <= 0:
            continue
        selected.append(node_id)
        group_counts[group] = group_counts.get(group, 0) + 1
    return selected


def check_dag(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate a DAG plan and return a deterministic structured result.

    Fail-fast: the first guard failure is returned as a deterministic
    ``rejection_codes`` list containing exactly that code. The list shape is
    kept for backward compatibility with callers that expect a collection.
    """
    try:
        validated = _validate_structure(plan)
        nodes = _validate_nodes(validated["nodes"])
        adjacency = _validate_edges(validated["edges"], nodes)
        _detect_cycle(nodes, adjacency)
        _dependency_consistency(
            nodes,
            adjacency,
            validated["github_dependencies"],
            validated["contract_dependencies"],
        )
        capacity = _validate_capacity(validated["capacity"], nodes)
        _validate_reviews(nodes, adjacency)
        _validate_integration_chain(nodes, adjacency)
        _validate_hotsets(nodes, adjacency)
        frontier = _ready_frontier(nodes, adjacency, capacity)
        return {
            "ok": True,
            "schema_version": DAG_SCHEMA_VERSION,
            "repository": validated["repository"],
            "ready_frontier": frontier,
            "rejection_codes": [],
        }
    except DagValidationError as error:
        return {
            "ok": False,
            "schema_version": DAG_SCHEMA_VERSION,
            "rejection_codes": [error.code],
            "error": error.message,
        }


def _build_parser() -> "argparse.ArgumentParser":
    import argparse
    parser = argparse.ArgumentParser(
        prog="gwo_dag.py",
        description="GWO V7 DAG plan guard (stdlib-only).",
    )
    parser.add_argument(
        "--plan",
        required=True,
        help="path to JSON plan or '-' for stdin",
    )
    return parser


def _read_plan_text(args: Any) -> str:
    """Read plan text from file path or stdin."""
    if args.plan == "-":
        import sys
        return sys.stdin.read()
    return Path(args.plan).read_text(encoding="utf-8")


def main(args: Any) -> int:
    """Entry point for `gwo guard check-dag` and direct python execution."""
    if args is None:
        args = _build_parser().parse_args()
    try:
        plan_text = _read_plan_text(args)
    except FileNotFoundError as error:
        print(json.dumps({
            "ok": False,
            "schema_version": DAG_SCHEMA_VERSION,
            "rejection_codes": ["plan-file-not-found"],
            "error": str(error),
        }, sort_keys=True))
        return 2
    except OSError as error:
        print(json.dumps({
            "ok": False,
            "schema_version": DAG_SCHEMA_VERSION,
            "rejection_codes": ["plan-read-error"],
            "error": str(error),
        }, sort_keys=True))
        return 2
    try:
        plan = json.loads(plan_text)
    except json.JSONDecodeError as error:
        print(json.dumps({"ok": False, "schema_version": DAG_SCHEMA_VERSION, "rejection_codes": ["plan-json-invalid"], "error": str(error)}, sort_keys=True))
        return 2
    result = check_dag(plan)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(None))
