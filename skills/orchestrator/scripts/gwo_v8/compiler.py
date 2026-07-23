"""Deterministic PlanSpec v2 compilation for the V8 walking skeleton."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import json
import re
from typing import Any

from ._canonical import canonical_bytes, digest_bytes, digest_value
from ._effects import EffectContractError, authorized_file_changes


WORKFLOW_SKILLS = {"implement", "implement-gwo", "orchestrator"}
DIFFICULTIES = {"routine", "standard", "complex", "frontier"}
RISKS = {"low", "standard", "strict"}
RISK_ORDER = {"low": 0, "standard": 1, "strict": 2}
PLAN_INTENT_FIELDS = {"parent_plan_digest", "goals", "nodes", "edges"}
SOURCE_SNAPSHOT_FIELDS = {"repository", "work_items"}
GOAL_FIELDS = {"goal_key", "objective", "acceptance"}
WORK_ITEM_FIELDS = {
    "work_item_key",
    "tracker_state",
    "source_ref",
    "title",
    "outcome_contract",
}
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
INPUT_FIELDS = {"file_changes"}
FILE_CHANGE_FIELDS = {"path", "content"}
OUTPUT_CONTRACT_FIELDS = {"required_evidence", "checks"}
EVIDENCE_REQUIREMENT_FIELDS = {"kind", "check_id"}
CHECK_FIELDS = {"check_id", "command"}
CHECK_DEFINITION_FIELDS = {
    "check_id",
    "version",
    "command",
    "hosted_name",
    "environment_requirements",
    "input_selector",
    "base_sensitive",
    "risk",
    "hosted_only",
    "suite",
}
EFFECT_CONTRACT_FIELDS = {"write_scopes", "external_effects"}
RUNTIME_REQUIREMENT_FIELDS = {"capabilities"}
RECOVERY_POLICY_FIELDS = {"semantic_attempts", "repair_rounds"}
OUTCOME_CONTRACT_FIELDS = {"path", "content"}


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


def _require_object(
    value: Any,
    *,
    fields: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CompileError("COMPILE_INPUT_INVALID", f"{label} must be an object")
    unknown = set(value) - fields
    if unknown:
        raise CompileError(
            "PLAN_FIELD_INVALID",
            f"{label} contains unsupported fields: {sorted(unknown)}",
        )
    return value


def _require_string_list(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise CompileError("PLAN_FIELD_INVALID", f"{label} must be string list")
    return value


def _require_string(
    value: Any,
    *,
    label: str,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise CompileError("PLAN_FIELD_INVALID", f"{label} must be a string")
    return value


def _require_nonnegative_int(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CompileError(
            "PLAN_FIELD_INVALID", f"{label} must be a non-negative integer"
        )
    return value


def _validate_plan_fields(
    plan_intent: dict[str, Any],
    source_snapshot: dict[str, Any],
    goal: dict[str, Any],
    work_item: dict[str, Any],
    proposal: dict[str, Any],
) -> None:
    _require_object(goal, fields=GOAL_FIELDS, label="Goal")
    _require_object(work_item, fields=WORK_ITEM_FIELDS, label="Work Item")
    _require_object(proposal, fields=PLAN_NODE_FIELDS, label="Plan Node")

    parent_digest = plan_intent.get("parent_plan_digest")
    if parent_digest is not None and (
        not isinstance(parent_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", parent_digest) is None
    ):
        raise CompileError(
            "PLAN_FIELD_INVALID",
            "parent_plan_digest must be null or a 64-hex digest",
        )
    _require_string(goal.get("goal_key"), label="Goal key")
    _require_string(goal.get("objective"), label="Goal objective")
    _require_string_list(goal.get("acceptance"), label="Goal acceptance")
    _require_string(work_item.get("work_item_key"), label="Work Item key")
    _require_string(work_item.get("tracker_state"), label="tracker state")
    _require_string(work_item.get("source_ref"), label="source reference")
    _require_string(work_item.get("title"), label="Work Item title")
    outcome_contract = _require_object(
        work_item.get("outcome_contract"),
        fields=OUTCOME_CONTRACT_FIELDS,
        label="Work Item outcome contract",
    )
    _require_string(outcome_contract.get("path"), label="outcome path")
    _require_string(
        outcome_contract.get("content"),
        label="outcome content",
        allow_empty=True,
    )
    _require_string(proposal.get("goal_key"), label="Plan Node Goal key")
    _require_string(proposal.get("work_item_key"), label="Plan Node Work Item key")
    kind = _require_string(proposal.get("kind"), label="Plan Node kind")
    if kind != "work":
        raise CompileError("PLAN_FIELD_INVALID", "Phase 1 Plan Node kind must be work")
    difficulty = _require_string(proposal.get("difficulty"), label="Worker difficulty")
    if difficulty not in DIFFICULTIES:
        raise CompileError("PLAN_FIELD_INVALID", "Worker difficulty is invalid")
    risk = _require_string(proposal.get("risk"), label="risk")
    if risk not in RISKS:
        raise CompileError("PLAN_FIELD_INVALID", "risk is invalid")
    inputs = _require_object(
        proposal.get("inputs"), fields=INPUT_FIELDS, label="Plan Node inputs"
    )
    changes = inputs.get("file_changes")
    if not isinstance(changes, list) or not changes:
        raise CompileError(
            "COMPILE_INPUT_INVALID", "file_changes must be a non-empty list"
        )
    for change in changes:
        checked_change = _require_object(
            change, fields=FILE_CHANGE_FIELDS, label="file change"
        )
        _require_string(checked_change.get("path"), label="file change path")
        _require_string(
            checked_change.get("content"),
            label="file change content",
            allow_empty=True,
        )
    expected_change = {
        "path": outcome_contract.get("path"),
        "content": outcome_contract.get("content"),
    }
    if changes != [expected_change]:
        raise CompileError(
            "PLAN_RELATION_INVALID",
            "Plan Node work must match the Ready Work Item outcome contract",
        )

    output_contract = _require_object(
        proposal.get("output_contract"),
        fields=OUTPUT_CONTRACT_FIELDS,
        label="output contract",
    )
    requirements = output_contract.get("required_evidence")
    checks = output_contract.get("checks")
    if not isinstance(requirements, list) or not isinstance(checks, list):
        raise CompileError(
            "COMPILE_INPUT_INVALID", "Evidence requirements and checks must be lists"
        )
    for requirement in requirements:
        checked_requirement = _require_object(
            requirement,
            fields=EVIDENCE_REQUIREMENT_FIELDS,
            label="Evidence requirement",
        )
        evidence_kind = _require_string(
            checked_requirement.get("kind"), label="Evidence kind"
        )
        if evidence_kind not in {"candidate", "check"}:
            raise CompileError("PLAN_FIELD_INVALID", "work Evidence kind is invalid")
        if evidence_kind == "check":
            _require_string(
                checked_requirement.get("check_id"), label="Evidence check ID"
            )
        elif checked_requirement.get("check_id") is not None:
            raise CompileError(
                "PLAN_FIELD_INVALID",
                "candidate Evidence cannot name a check",
            )
    for check in checks:
        checked = _require_object(check, fields=CHECK_FIELDS, label="check")
        _require_string(checked.get("check_id"), label="check ID")
        _require_string_list(checked.get("command"), label="check command")

    effect_contract = _require_object(
        proposal.get("effect_contract"),
        fields=EFFECT_CONTRACT_FIELDS,
        label="Effect Contract",
    )
    _require_string_list(effect_contract.get("write_scopes"), label="Write Scopes")
    _require_string_list(
        effect_contract.get("external_effects"), label="external effects"
    )
    runtime_requirements = _require_object(
        proposal.get("runtime_requirements"),
        fields=RUNTIME_REQUIREMENT_FIELDS,
        label="Runtime Requirements",
    )
    _require_string_list(
        runtime_requirements.get("capabilities"),
        label="Runtime capabilities",
    )
    recovery = _require_object(
        proposal.get("recovery_policy"),
        fields=RECOVERY_POLICY_FIELDS,
        label="recovery policy",
    )
    _require_nonnegative_int(
        recovery.get("semantic_attempts"), label="semantic attempts"
    )
    _require_nonnegative_int(recovery.get("repair_rounds"), label="repair rounds")
    _require_string_list(proposal.get("resource_claims"), label="Resource Claims")
    if plan_intent.get("edges") != []:
        raise CompileError(
            "PLAN_FIELD_INVALID",
            "Phase 1 accepts no proposed edges; the Compiler owns Integration edges",
        )


def _policy_version(policy_snapshot: dict[str, Any]) -> int:
    version = policy_snapshot.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise CompileError(
            "POLICY_SNAPSHOT_INVALID",
            "policy version must be a positive integer",
        )
    return version


def _compiled_check_definitions(
    proposal: dict[str, Any],
    policy_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    requested = (proposal.get("output_contract") or {}).get("checks") or []
    policy_version = _policy_version(policy_snapshot)
    if policy_version < 3:
        definitions = [
            {
                "check_id": check["check_id"],
                "version": 1,
                "command": list(check["command"]),
                "hosted_name": None,
                "environment_requirements": [],
                "input_selector": list(
                    (proposal.get("effect_contract") or {}).get("write_scopes") or ()
                ),
                "base_sensitive": False,
                "risk": proposal["risk"],
                "hosted_only": False,
                "suite": "repository",
            }
            for check in requested
        ]
    else:
        raw_definitions = policy_snapshot.get("check_definitions")
        if not isinstance(raw_definitions, list):
            raise CompileError(
                "CHECK_DEFINITIONS_MISSING",
                "version 3 policy must define repository checks",
            )
        by_id: dict[str, dict[str, Any]] = {}
        for raw in raw_definitions:
            definition = _require_object(
                raw,
                fields=CHECK_DEFINITION_FIELDS,
                label="Check Definition",
            )
            check_id = _require_string(
                definition.get("check_id"),
                label="Check Definition ID",
            )
            if check_id in by_id:
                raise CompileError(
                    "CHECK_DEFINITION_DUPLICATE",
                    f"duplicate Check Definition: {check_id}",
                )
            version = definition.get("version")
            if not isinstance(version, int) or isinstance(version, bool) or version < 1:
                raise CompileError(
                    "CHECK_DEFINITION_INVALID",
                    f"Check Definition version is invalid: {check_id}",
                )
            command = _require_string_list(
                definition.get("command"),
                label=f"Check Definition command: {check_id}",
            )
            hosted_name = definition.get("hosted_name")
            if hosted_name is not None:
                _require_string(hosted_name, label=f"hosted check name: {check_id}")
            environment_requirements = _require_string_list(
                definition.get("environment_requirements"),
                label=f"environment requirements: {check_id}",
            )
            input_selector = _require_string_list(
                definition.get("input_selector"),
                label=f"input selector: {check_id}",
            )
            if not input_selector:
                raise CompileError(
                    "CHECK_DEFINITION_INVALID",
                    f"Check Definition has no input selector: {check_id}",
                )
            base_sensitive = definition.get("base_sensitive")
            hosted_only = definition.get("hosted_only")
            if not isinstance(base_sensitive, bool) or not isinstance(
                hosted_only, bool
            ):
                raise CompileError(
                    "CHECK_DEFINITION_INVALID",
                    f"Check Definition flags are invalid: {check_id}",
                )
            risk = _require_string(
                definition.get("risk"),
                label=f"Check Definition risk: {check_id}",
            )
            if risk not in RISKS:
                raise CompileError(
                    "CHECK_DEFINITION_INVALID",
                    f"Check Definition risk is invalid: {check_id}",
                )
            suite = _require_string(
                definition.get("suite"),
                label=f"Check Definition suite: {check_id}",
            )
            if suite not in {"affected", "repository", "hosted"}:
                raise CompileError(
                    "CHECK_DEFINITION_INVALID",
                    f"Check Definition suite is invalid: {check_id}",
                )
            if hosted_only and hosted_name is None:
                raise CompileError(
                    "CHECK_DEFINITION_INVALID",
                    f"hosted-only Check Definition has no hosted name: {check_id}",
                )
            if hosted_only != (suite == "hosted"):
                raise CompileError(
                    "CHECK_DEFINITION_INVALID",
                    (
                        "hosted-only and hosted suite must agree for "
                        f"Check Definition: {check_id}"
                    ),
                )
            by_id[check_id] = {
                "check_id": check_id,
                "version": version,
                "command": list(command),
                "hosted_name": hosted_name,
                "environment_requirements": list(environment_requirements),
                "input_selector": list(input_selector),
                "base_sensitive": base_sensitive,
                "risk": risk,
                "hosted_only": hosted_only,
                "suite": suite,
            }
        selected_ids: set[str] = set()
        for check in requested:
            check_id = check["check_id"]
            definition = by_id.get(check_id)
            if definition is None:
                raise CompileError(
                    "CHECK_DEFINITION_MISSING",
                    f"required repository Check Definition is missing: {check_id}",
                )
            if definition["command"] != check["command"]:
                raise CompileError(
                    "CHECK_DEFINITION_MISMATCH",
                    f"Plan check command differs from repository policy: {check_id}",
                )
            selected_ids.add(check_id)
        changed_paths = {
            str(change.get("path"))
            for change in (proposal.get("inputs") or {}).get("file_changes") or ()
            if isinstance(change, dict)
        }
        proposal_risk = str(proposal["risk"])
        selected_ids.update(
            check_id
            for check_id, definition in by_id.items()
            if RISK_ORDER[str(definition["risk"])] <= RISK_ORDER[proposal_risk]
            and any(
                fnmatchcase(path, selector)
                for path in changed_paths
                for selector in definition["input_selector"]
            )
        )
        definitions = [by_id[check_id] for check_id in sorted(selected_ids)]

    compiled: list[dict[str, Any]] = []
    for definition in definitions:
        body = json.loads(canonical_bytes(definition))
        compiled.append(
            {
                **body,
                "definition_digest": digest_value(body),
            }
        )
    return compiled


def _matches_low_risk_allowlist(
    proposal: dict[str, Any],
    policy_snapshot: dict[str, Any],
) -> bool:
    patterns = policy_snapshot.get("low_risk_allowlist")
    if not isinstance(patterns, list) or any(
        not isinstance(pattern, str) or not pattern for pattern in patterns
    ):
        raise CompileError(
            "LOW_RISK_ALLOWLIST_MISSING",
            "version 3 policy must define the deterministic low-risk allowlist",
        )
    paths = [
        str(change["path"])
        for change in (proposal.get("inputs") or {}).get("file_changes") or ()
    ]
    return bool(paths) and all(
        any(fnmatchcase(path, pattern) for pattern in patterns) for path in paths
    )


def _compiled_review_requirement(
    proposal: dict[str, Any],
    policy_snapshot: dict[str, Any],
) -> dict[str, Any]:
    risk = proposal["risk"]
    if risk == "low":
        if _policy_version(policy_snapshot) >= 3 and not _matches_low_risk_allowlist(
            proposal,
            policy_snapshot,
        ):
            raise CompileError(
                "LOW_RISK_NOT_ALLOWED",
                "low risk is not allowed for the proposed change surface",
            )
        return {
            "mode": "none",
            "axes": [],
            "specialist_requirements": [],
            "human_decision_required": False,
        }
    if risk == "standard":
        return {
            "mode": "dual_axis",
            "axes": ["standards", "spec"],
            "specialist_requirements": [],
            "human_decision_required": False,
        }
    strict = policy_snapshot.get("strict_review")
    if not isinstance(strict, dict):
        raise CompileError(
            "STRICT_REVIEW_POLICY_MISSING",
            "strict work requires a concrete specialist or human policy",
        )
    specialists = _require_string_list(
        strict.get("specialist_requirements"),
        label="strict specialist requirements",
    )
    if any(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", specialist) is None
        for specialist in specialists
    ):
        raise CompileError(
            "STRICT_REVIEW_POLICY_INVALID",
            "strict specialist IDs must be stable repository-policy names",
        )
    human_required = strict.get("human_decision_required")
    if not isinstance(human_required, bool) or (not specialists and not human_required):
        raise CompileError(
            "STRICT_REVIEW_POLICY_INVALID",
            "strict work requires a specialist or human decision",
        )
    return {
        "mode": "strict",
        "axes": ["standards", "spec"],
        "specialist_requirements": list(specialists),
        "human_decision_required": human_required,
    }


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
            raise CompileError(
                "COMPILE_INPUT_INVALID", "compiler inputs must be objects"
            )
        _require_object(plan_intent, fields=PLAN_INTENT_FIELDS, label="Plan Intent")
        _require_object(
            source_snapshot,
            fields=SOURCE_SNAPSHOT_FIELDS,
            label="source snapshot",
        )

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
            raise CompileError(
                "COMPILE_INPUT_INVALID", "semantic entries must be objects"
            )
        _validate_plan_fields(
            plan_intent,
            source_snapshot,
            goal,
            work_item,
            proposal,
        )
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

        if proposal["risk"] != "low" and not goal.get("acceptance"):
            raise CompileError(
                "SPEC_INPUT_MISSING",
                "reviewed executable work requires canonical acceptance input",
            )
        review_requirement = _compiled_review_requirement(
            proposal,
            policy_snapshot,
        )
        compiled_checks = _compiled_check_definitions(
            proposal,
            policy_snapshot,
        )
        if _policy_version(policy_snapshot) >= 3:
            local_suites = {
                str(check.get("suite"))
                for check in compiled_checks
                if check.get("hosted_only") is not True
            }
            if not any(check.get("hosted_only") is True for check in compiled_checks):
                raise CompileError(
                    "HOSTED_CHECK_MISSING",
                    (
                        "V8 executable work requires at least one applicable "
                        "typed hosted Check Definition"
                    ),
                )
            if "repository" not in local_suites:
                raise CompileError(
                    "REPOSITORY_CHECK_MISSING",
                    (
                        "V8 executable work requires one repository-equivalent "
                        "local Check Definition"
                    ),
                )
            if proposal["risk"] != "low" and "affected" not in local_suites:
                raise CompileError(
                    "AFFECTED_CHECK_MISSING",
                    ("reviewed V8 work requires a cheap affected Check before Review"),
                )
        hosted_only_ids = {
            check["check_id"]
            for check in compiled_checks
            if check.get("hosted_only") is True
        }
        required_evidence = [
            requirement
            for requirement in (
                (proposal.get("output_contract") or {}).get("required_evidence") or ()
            )
            if not (
                requirement.get("kind") == "check"
                and requirement.get("check_id") in hosted_only_ids
            )
        ]
        required_check_ids = {
            requirement.get("check_id")
            for requirement in required_evidence
            if requirement.get("kind") == "check"
        }
        for check in compiled_checks:
            if (
                check.get("hosted_only") is not True
                and check["check_id"] not in required_check_ids
            ):
                required_evidence.append(
                    {"kind": "check", "check_id": check["check_id"]}
                )
                required_check_ids.add(check["check_id"])
        if review_requirement["mode"] != "none":
            required_evidence.append({"kind": "review"})
        compiled_output_contract = {
            "required_evidence": required_evidence,
            "checks": compiled_checks,
            "review_requirement": review_requirement,
            "delivery_required": _policy_version(policy_snapshot) >= 3,
        }
        work_node = _node(
            {
                **proposal,
                "output_contract": compiled_output_contract,
                "skill_reference": skill_reference,
            }
        )
        decision_node = (
            _node(
                {
                    "goal_key": goal_key,
                    "work_item_key": work_item_key,
                    "kind": "decision",
                    "inputs": {
                        "candidate_from": work_node["node_key"],
                        "decision_kind": "strict_review_human",
                    },
                    "output_contract": {"required_evidence": [{"kind": "decision"}]},
                    "effect_contract": {
                        "write_scopes": [],
                        "external_effects": [],
                    },
                    "resource_claims": [],
                    "runtime_requirements": {"capabilities": []},
                    "difficulty": "routine",
                    "risk": "strict",
                    "recovery_policy": {
                        "semantic_attempts": 1,
                        "repair_rounds": 0,
                    },
                    "skill_reference": None,
                }
            )
            if review_requirement["human_decision_required"]
            else None
        )
        integration_node = _node(
            {
                "goal_key": goal_key,
                "work_item_key": work_item_key,
                "kind": "integration",
                "inputs": {"candidate_from": work_node["node_key"]},
                "output_contract": {"required_evidence": [{"kind": "integration"}]},
                "effect_contract": {
                    "write_scopes": [],
                    "external_effects": ["git:integrate"],
                },
                "resource_claims": [f"integration_lease:{repository}"],
                "runtime_requirements": {"capabilities": []},
                "difficulty": "routine",
                "risk": "low",
                "recovery_policy": {"semantic_attempts": 1, "repair_rounds": 0},
                "skill_reference": None,
            }
        )
        nodes = sorted(
            [
                work_node,
                integration_node,
                *([] if decision_node is None else [decision_node]),
            ],
            key=lambda node: (node["kind"], node["node_key"]),
        )
        if decision_node is None:
            edges = [
                {
                    "from_node": work_node["node_key"],
                    "to_node": integration_node["node_key"],
                    "type": "result_required",
                }
            ]
        else:
            edges = [
                {
                    "from_node": work_node["node_key"],
                    "to_node": decision_node["node_key"],
                    "type": "result_required",
                },
                {
                    "from_node": decision_node["node_key"],
                    "to_node": integration_node["node_key"],
                    "type": "decision_required",
                },
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
                    **edge,
                    "source": (
                        "compiler:strict-human-decision"
                        if decision_node is not None
                        else "compiler:serial-integration"
                    ),
                }
                for edge in edges
            ],
        }
        return CompiledPlan(
            repository=repository,
            canonical_bytes=canonical,
            digest=digest,
            compilation_record=compilation_record,
        )
