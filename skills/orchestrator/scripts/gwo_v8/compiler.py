"""Deterministic PlanSpec v2 compilation for the V8 walking skeleton."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from ._canonical import canonical_bytes, digest_bytes, digest_value
from ._effects import EffectContractError, authorized_file_changes


WORKFLOW_SKILLS = {"implement", "implement-gwo", "orchestrator"}
PLAN_NODE_FIELDS = {
    "goal_key",
    "work_item_key",
    "kind",
    "inputs",
    "output_contract",
    "effect_contract",
    "resource_claims",
    "runtime_requirements",
    "difficulty",
    "risk",
    "recovery_policy",
    "skill_reference",
}


class CompileError(ValueError):
    """A deterministic rejection of non-executable Plan Intent."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CompiledPlan:
    """The sole canonical PlanSpec representation consumed downstream."""

    repository: str
    canonical_bytes: bytes
    digest: str
    compilation_record: dict[str, Any]

    def has_valid_digest(self) -> bool:
        """Validate Compiler-owned bytes without creating another plan identity."""

        return digest_bytes(self.canonical_bytes) == self.digest


def _semantic_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    snapshot = json.loads(canonical_bytes(value))
    snapshot["semantic_digest"] = digest_value(value)
    return snapshot


def _node(value: dict[str, Any]) -> dict[str, Any]:
    contract_digest = digest_value(value)
    return {
        **json.loads(canonical_bytes(value)),
        "contract_digest": contract_digest,
        "node_key": f"node:{contract_digest[:24]}",
    }


def _skill_name(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CompileError("SKILL_REFERENCE_INVALID", "Skill Reference must be a name")
    return value.strip().lstrip("/$").casefold()


def _validate_effect_contract(proposal: dict[str, Any]) -> None:
    try:
        authorized_file_changes(proposal)
    except EffectContractError as error:
        raise CompileError("EFFECT_CONTRACT_VIOLATION", str(error)) from error


class PlanCompiler:
    """Compile one Ready Work Item into the minimal executable V8 graph."""

    def compile(
        self,
        plan_intent: dict[str, Any],
        source_snapshot: dict[str, Any],
        policy_snapshot: dict[str, Any],
    ) -> CompiledPlan:
        if not all(
            isinstance(value, dict)
            for value in (plan_intent, source_snapshot, policy_snapshot)
        ):
            raise CompileError("COMPILE_INPUT_INVALID", "compiler inputs must be objects")

        repository = source_snapshot.get("repository")
        work_items = source_snapshot.get("work_items")
        goals = plan_intent.get("goals")
        proposed_nodes = plan_intent.get("nodes")
        if not isinstance(repository, str) or "/" not in repository:
            raise CompileError("REPOSITORY_INVALID", "repository identity is invalid")
        if not isinstance(work_items, list) or len(work_items) != 1:
            raise CompileError(
                "WORK_ITEM_SET_INVALID", "Phase 1 requires one Work Item"
            )
        if not isinstance(goals, list) or len(goals) != 1:
            raise CompileError("GOAL_SET_INVALID", "Phase 1 requires one Goal")
        if not isinstance(proposed_nodes, list) or len(proposed_nodes) != 1:
            raise CompileError("NODE_SET_INVALID", "Phase 1 requires one work node")

        work_item = work_items[0]
        if (
            not isinstance(work_item, dict)
            or work_item.get("tracker_state") != "ready-for-agent"
        ):
            raise CompileError(
                "WORK_ITEM_NOT_READY",
                "only ready-for-agent Work Items may compile into executable work",
            )
        goal = goals[0]
        proposal = proposed_nodes[0]
        if not isinstance(goal, dict) or not isinstance(proposal, dict):
            raise CompileError("COMPILE_INPUT_INVALID", "semantic entries must be objects")
        unknown_fields = set(proposal) - PLAN_NODE_FIELDS
        if unknown_fields:
            raise CompileError(
                "PLAN_NODE_FIELD_INVALID",
                f"Plan Node contains unsupported fields: {sorted(unknown_fields)}",
            )
        if proposal.get("kind") != "work":
            raise CompileError("NODE_KIND_INVALID", "Phase 1 requires a work node")
        _validate_effect_contract(proposal)

        skill_reference = _skill_name(proposal.get("skill_reference"))
        if skill_reference in WORKFLOW_SKILLS:
            raise CompileError(
                "WORKFLOW_SKILL_RECURSION",
                f"workflow entry Skill cannot bind to a Plan Node: {skill_reference}",
            )

        work_item_key = work_item.get("work_item_key")
        goal_key = goal.get("goal_key")
        if (
            not isinstance(work_item_key, str)
            or not work_item_key
            or not isinstance(goal_key, str)
            or not goal_key
            or proposal.get("work_item_key") != work_item_key
            or proposal.get("goal_key") != goal_key
        ):
            raise CompileError(
                "PLAN_RELATION_INVALID", "Goal, Work Item, and node links must agree"
            )

        work_node = _node(
            {
                **proposal,
                "skill_reference": skill_reference,
            }
        )
        integration_node = _node(
            {
                "goal_key": goal_key,
                "work_item_key": work_item_key,
                "kind": "integration",
                "inputs": {"candidate_from": work_node["node_key"]},
                "output_contract": {
                    "required_evidence": [{"kind": "integration"}]
                },
                "effect_contract": {
                    "write_scopes": [],
                    "external_effects": ["git:integrate"],
                },
                "resource_claims": [f"integration_lease:{repository}"],
                "runtime_requirements": {"capabilities": []},
                "difficulty": "light",
                "risk": "low",
                "recovery_policy": {"semantic_attempts": 1, "repair_rounds": 0},
                "skill_reference": None,
            }
        )
        nodes = sorted(
            [work_node, integration_node],
            key=lambda node: (node["kind"], node["node_key"]),
        )
        edges = [
            {
                "from_node": work_node["node_key"],
                "to_node": integration_node["node_key"],
                "type": "result_required",
            }
        ]
        plan_spec = {
            "schema_version": 2,
            "repository": repository,
            "parent_plan_digest": plan_intent.get("parent_plan_digest"),
            "goals": [_semantic_snapshot(goal)],
            "work_items": [_semantic_snapshot(work_item)],
            "nodes": nodes,
            "edges": edges,
        }
        canonical = canonical_bytes(plan_spec)
        digest = digest_bytes(canonical)
        compilation_record = {
            "source_digest": digest_value(source_snapshot),
            "policy_digest": digest_value(policy_snapshot),
            "edge_provenance": [
                {
                    **edges[0],
                    "source": "compiler:serial-integration",
                }
            ],
        }
        return CompiledPlan(
            repository=repository,
            canonical_bytes=canonical,
            digest=digest,
            compilation_record=compilation_record,
        )
