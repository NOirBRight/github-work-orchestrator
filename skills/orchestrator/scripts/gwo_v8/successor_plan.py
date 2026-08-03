"""Pure derivation and compilation of one approved successor PlanSpec.

The invalidation Coordinator is allowed to name approved owners and justify a
small dependency or Exclusive Resource delta.  This module turns those facts
into a complete Campaign PlanSpec without consulting a repository, Runtime
Gateway, or mutable source.  PlanControl owns the durable boundary around
these functions; keeping the compiler pure makes that boundary replayable.
"""

from __future__ import annotations

from copy import deepcopy
import heapq
from typing import Any, Mapping

from ._canonical import (
    CanonicalJsonError,
    canonical_bytes,
    digest_value,
    load_canonical_json,
)


_DIGEST_LENGTH = 64
_MAX_DEPENDENCY_NODES = 8_192
_MAX_DEPENDENCY_EDGES = 65_536

_SNAPSHOT_FIELDS = {
    "schema_version",
    "repository",
    "campaign_key",
    "target_branch",
    "campaign_source",
    "membership",
    "policy",
    "policy_witness",
    "tickets",
    "product_release",
    "source_change_digest",
    "native_blocker_graph",
    "external_dependencies",
    "work_runs",
    "claims",
    "accepted_results",
    "pending_invalidations",
    "approved_dependency_edges",
    "active_plan_revision",
    "plan_revision_digest",
    "snapshot_digest",
}


class SuccessorPlanError(RuntimeError):
    """A named fail-closed successor derivation or compilation outcome."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _fail(code: str, detail: str) -> None:
    raise SuccessorPlanError(code, detail)


def _canonical(value: Any, *, code: str = "SUCCESSOR_PLAN_INVALID") -> Any:
    try:
        return load_canonical_json(canonical_bytes(value))
    except (CanonicalJsonError, TypeError, ValueError) as error:
        raise SuccessorPlanError(code, "value is outside canonical JSON") from error


def _exact_mapping(
    value: Any,
    fields: set[str],
    *,
    code: str,
    label: str,
) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code, f"{label} contains unsupported or missing fields")
    return value


def _text(value: Any, label: str, *, code: str = "SUCCESSOR_PLAN_INVALID") -> str:
    if type(value) is not str or not value:
        _fail(code, f"{label} must be non-empty exact text")
    return value


def _digest(value: Any, label: str) -> str:
    value = _text(value, label)
    if len(value) != _DIGEST_LENGTH or any(
        character not in "0123456789abcdef" for character in value
    ):
        _fail("SUCCESSOR_PLAN_INVALID", f"{label} must be a SHA-256 digest")
    return value


def _sorted_unique_texts(value: Any, label: str) -> list[str]:
    if type(value) is not list or any(type(item) is not str or not item for item in value):
        _fail("SUCCESSOR_PLAN_INVALID", f"{label} must be a list of text")
    if value != sorted(set(value)):
        _fail("SUCCESSOR_PLAN_INVALID", f"{label} must be sorted and unique")
    return list(value)


def _require_policy(policy: Any) -> dict[str, Any]:
    if type(policy) is not dict:
        _fail("SUCCESSOR_PLAN_INVALID", "replanning snapshot omitted its Policy Witness")
    required = {"schema_version", "ref", "digest", "authority_grants", "allowed_capabilities", "exclusive_resources"}
    optional = {"replan", "kind"}
    if set(policy) != required and set(policy) not in (
        required | {"replan"},
        required | {"kind"},
        required | optional,
    ):
        _fail("SUCCESSOR_PLAN_INVALID", "Policy Witness schema is not closed")
    if policy.get("schema_version") != 1:
        _fail("SUCCESSOR_PLAN_INVALID", "Policy Witness schema version is invalid")
    if "kind" in policy and policy["kind"] != "gwo.policy-witness.v1":
        _fail("SUCCESSOR_PLAN_INVALID", "Policy Witness kind is invalid")
    _digest(policy["digest"], "Policy Witness digest")
    _text(policy["ref"], "Policy Witness ref")
    _sorted_unique_texts(policy["allowed_capabilities"], "Policy capabilities")
    _sorted_unique_texts(policy["exclusive_resources"], "Policy Exclusive Resources")
    if "replan" in policy:
        replan = policy["replan"]
        if (
            type(replan) is not dict
            or set(replan)
            != {"successor_revision_limit", "repeated_invalidation_limit"}
            or any(
                type(replan[field]) is not int
                or isinstance(replan[field], bool)
                or replan[field] < 1
                for field in (
                    "successor_revision_limit",
                    "repeated_invalidation_limit",
                )
            )
        ):
            _fail("SUCCESSOR_PLAN_INVALID", "Policy Witness replan budget is invalid")
    if (
        type(policy["authority_grants"]) is not dict
        or set(policy["authority_grants"])
        != {"campaign", "worker", "recovery_worker", "review"}
        or any(
            type(grants) is not list
            or any(
                type(grant) is not dict
                or set(grant) != {"operation_id", "resource_id"}
                or any(type(value) is not str or not value for value in grant.values())
                for grant in grants
            )
            for grants in policy["authority_grants"].values()
        )
    ):
        _fail("SUCCESSOR_PLAN_INVALID", "Policy Witness authority grants are invalid")
    core = {key: value for key, value in policy.items() if key != "digest"}
    if digest_value(core) != policy["digest"]:
        _fail("SUCCESSOR_PLAN_INVALID", "Policy Witness digest does not bind its facts")
    return policy


def _require_work_item(value: Any) -> dict[str, Any]:
    fields = {
        "key",
        "source",
        "contract",
        "depends_on",
        "exclusive_resources",
        "capabilities",
        "authority",
    }
    item = _exact_mapping(
        value,
        fields,
        code="SUCCESSOR_PLAN_INVALID",
        label="active PlanSpec work item",
    )
    _text(item["key"], "PlanSpec work key")
    for field in ("depends_on", "exclusive_resources", "capabilities"):
        _sorted_unique_texts(item[field], f"PlanSpec work {field}")
    if type(item["source"]) is not dict or type(item["contract"]) is not dict or type(item["authority"]) is not dict:
        _fail("SUCCESSOR_PLAN_INVALID", "active PlanSpec work item contains malformed frozen facts")
    return item


def _require_active_plan(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    active = snapshot.get("active_plan_revision")
    active_fields = {"digest", "plan_spec"}
    if type(active) is dict and "expected_previous_revision_digest" in active:
        active_fields.add("expected_previous_revision_digest")
    active = _exact_mapping(
        active,
        active_fields,
        code="SUCCESSOR_PLAN_INVALID",
        label="active Plan Revision projection",
    )
    plan = _exact_mapping(
        active["plan_spec"],
        {"schema_version", "repository", "target_branch", "campaign", "policy", "work"},
        code="SUCCESSOR_PLAN_INVALID",
        label="active PlanSpec",
    )
    if plan["schema_version"] != 3:
        _fail("SUCCESSOR_PLAN_INVALID", "active PlanSpec is not v3")
    work = plan["work"]
    if type(work) is not list or not work:
        _fail("SUCCESSOR_PLAN_INVALID", "active PlanSpec has no work manifest")
    normalized_work = [_require_work_item(item) for item in work]
    keys = [item["key"] for item in normalized_work]
    if keys != sorted(set(keys)):
        _fail("SUCCESSOR_PLAN_INVALID", "active PlanSpec work keys are not canonical")
    _digest(active["digest"], "active Plan Revision digest")
    if active["digest"] != digest_value(plan):
        _fail("SUCCESSOR_PLAN_INVALID", "active Plan Revision digest does not bind its PlanSpec")
    if plan["repository"] != snapshot["repository"]:
        _fail("SUCCESSOR_PLAN_INVALID", "active PlanSpec belongs to another repository")
    if type(plan["policy"]) is not dict or set(plan["policy"]) != {"ref", "digest"}:
        _fail("SUCCESSOR_PLAN_INVALID", "active PlanSpec Policy projection is invalid")
    return plan, normalized_work


def _require_snapshot(value: Any) -> dict[str, Any]:
    snapshot = _canonical(value)
    if type(snapshot) is not dict or not {
        "repository",
        "target_branch",
        "campaign_source",
        "policy_witness",
        "tickets",
        "active_plan_revision",
    }.issubset(snapshot):
        _fail("SUCCESSOR_PLAN_INVALID", "replanning snapshot is missing required authority facts")
    if not set(snapshot).issubset(_SNAPSHOT_FIELDS):
        _fail("SUCCESSOR_PLAN_INVALID", "replanning snapshot contains unsupported fields")
    _text(snapshot["repository"], "snapshot repository")
    _text(snapshot["target_branch"], "snapshot target branch")
    if type(snapshot["campaign_source"]) is not dict:
        _fail("SUCCESSOR_PLAN_INVALID", "snapshot Campaign source is invalid")
    policy = _require_policy(snapshot["policy_witness"])
    if "policy" in snapshot and "policy_witness" in snapshot and snapshot["policy"] != policy:
        _fail("SUCCESSOR_PLAN_INVALID", "snapshot Policy projections disagree")
    tickets = snapshot["tickets"]
    if type(tickets) is not list or any(type(item) is not dict for item in tickets):
        _fail("SUCCESSOR_PLAN_INVALID", "snapshot Tickets are invalid")
    ticket_keys = [item.get("key") for item in tickets]
    if any(type(key) is not str or not key for key in ticket_keys) or ticket_keys != sorted(set(ticket_keys)):
        _fail("SUCCESSOR_PLAN_INVALID", "snapshot Ticket membership is not canonical")
    adoption_fields = {"membership", "product_release", "source_change_digest"}
    present_adoption_fields = adoption_fields.intersection(snapshot)
    if present_adoption_fields and present_adoption_fields != adoption_fields:
        _fail("SUCCESSOR_PLAN_INVALID", "approved source projection is incomplete")
    if present_adoption_fields:
        membership = _exact_mapping(
            snapshot["membership"],
            {"ticket_keys", "digest"},
            code="SUCCESSOR_PLAN_INVALID",
            label="approved source membership",
        )
        _sorted_unique_texts(membership["ticket_keys"], "approved source membership keys")
        if membership["ticket_keys"] != ticket_keys:
            _fail("SUCCESSOR_PLAN_INVALID", "approved source membership does not equal its Tickets")
        _digest(membership["digest"], "approved source membership digest")
        if membership["digest"] != digest_value({"ticket_keys": ticket_keys}):
            _fail("SUCCESSOR_PLAN_INVALID", "approved source membership digest changed")
        _digest(snapshot["source_change_digest"], "approved source change digest")
        if type(snapshot["product_release"]) not in (dict, list, str, int, float, bool, type(None)):
            _fail("SUCCESSOR_PLAN_INVALID", "approved product/release projection is not canonical")
    plan, work = _require_active_plan(snapshot)
    work_keys = [item["key"] for item in work]
    source_adoption = "source_change_digest" in snapshot
    if not source_adoption and ticket_keys != work_keys:
        _fail("SUCCESSOR_PLAN_INVALID", "snapshot Tickets do not equal complete active membership")
    if not source_adoption and plan["policy"]["digest"] != policy["digest"]:
        _fail("SUCCESSOR_PLAN_INVALID", "active PlanSpec is bound to another Policy Witness")
    if (
        "plan_revision_digest" in snapshot
        and (
            _digest(snapshot["plan_revision_digest"], "snapshot Plan Revision digest")
            != snapshot["active_plan_revision"]["digest"]
        )
    ):
        _fail("SUCCESSOR_PLAN_INVALID", "snapshot Plan Revision identity is inconsistent")
    graph = {item["key"]: set(item["depends_on"]) for item in work}
    _assert_acyclic(graph)
    if "external_dependencies" in snapshot and type(snapshot["external_dependencies"]) is not list:
        _fail("SUCCESSOR_PLAN_INVALID", "snapshot external dependencies are invalid")
    return snapshot


def _snapshot_identity(snapshot: dict[str, Any]) -> str:
    if "snapshot_digest" not in snapshot:
        return digest_value(snapshot)
    declared = snapshot["snapshot_digest"]
    _digest(declared, "snapshot digest")
    content = dict(snapshot)
    del content["snapshot_digest"]
    if digest_value(content) != declared:
        _fail("SUCCESSOR_PLAN_INVALID", "snapshot digest does not bind its facts")
    return declared


def _classification_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        canonical = getattr(value, "canonical", None)
        if not callable(canonical):
            _fail("SUCCESSOR_PLAN_INVALID", "successor classification is not a canonical mapping")
        value = canonical()
    return _canonical(value, code="SUCCESSOR_PLAN_INVALID")


def _require_classification(
    value: Any,
    *,
    snapshot_digest: str,
    plan_revision_digest: str,
) -> dict[str, Any]:
    classification = _classification_mapping(value)
    expected = {
        "kind",
        "action_id",
        "snapshot_digest",
        "plan_revision_digest",
        "evidence_digests",
        "disposition",
        "reason",
        "successor",
        "decision",
        "capability_proof_digest",
    }
    _exact_mapping(
        classification,
        expected,
        code="SUCCESSOR_PLAN_INVALID",
        label="successor classification",
    )
    if classification["kind"] != "plan_invalidation_classification.v1":
        _fail("SUCCESSOR_PLAN_INVALID", "successor classification kind is invalid")
    _text(classification["action_id"], "classification action")
    _digest(classification["snapshot_digest"], "classification snapshot digest")
    _digest(classification["plan_revision_digest"], "classification Plan Revision digest")
    if (
        classification["snapshot_digest"] != snapshot_digest
        or classification["plan_revision_digest"] != plan_revision_digest
    ):
        _fail(
            "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
            "successor classification is bound to another snapshot or Plan Revision",
        )
    _digest(classification["capability_proof_digest"], "classification capability proof digest")
    _sorted_unique_texts(classification["evidence_digests"], "classification Evidence digests")
    _text(classification["reason"], "classification reason")
    if classification["disposition"] != "use_approved_successor" or classification["decision"] is not None:
        _fail("SUCCESSOR_PLAN_INVALID", "successor classification must name only an approved successor")
    successor = _exact_mapping(
        classification["successor"],
        {"approved_ticket_keys", "dependency_additions", "exclusive_resource_additions"},
        code="SUCCESSOR_PLAN_INVALID",
        label="successor classification facts",
    )
    _sorted_unique_texts(successor["approved_ticket_keys"], "successor owners")
    raw_dependencies = successor["dependency_additions"]
    if type(raw_dependencies) is not list:
        _fail("SUCCESSOR_PLAN_INVALID", "successor dependency additions are not a list")
    for item in raw_dependencies:
        dependency = _exact_mapping(
            item,
            {"from", "to", "reason"},
            code="PLAN_INVALIDATION_DEPENDENCY_INVALID",
            label="successor dependency addition",
        )
        _text(dependency["from"], "dependency source", code="PLAN_INVALIDATION_DEPENDENCY_INVALID")
        _text(dependency["to"], "dependency target", code="PLAN_INVALIDATION_DEPENDENCY_INVALID")
        _text(dependency["reason"], "dependency reason", code="PLAN_INVALIDATION_DEPENDENCY_INVALID")
    if [
        (item["from"], item["to"], item["reason"])
        for item in raw_dependencies
    ] != sorted(
        (item["from"], item["to"], item["reason"])
        for item in raw_dependencies
    ):
        _fail("PLAN_INVALIDATION_DEPENDENCY_INVALID", "successor dependencies are not canonical")
    raw_resources = successor["exclusive_resource_additions"]
    if type(raw_resources) is not list:
        _fail("SUCCESSOR_PLAN_INVALID", "successor Exclusive Resources are not a list")
    for item in raw_resources:
        resource = _exact_mapping(
            item,
            {"ticket_key", "resource_id", "reason"},
            code="EXCLUSIVE_RESOURCE_INVALID",
            label="successor Exclusive Resource addition",
        )
        _text(resource["ticket_key"], "Exclusive Resource Ticket", code="EXCLUSIVE_RESOURCE_INVALID")
        _text(resource["resource_id"], "Exclusive Resource ID", code="EXCLUSIVE_RESOURCE_INVALID")
        _text(resource["reason"], "Exclusive Resource reason", code="EXCLUSIVE_RESOURCE_INVALID")
    if [
        (item["ticket_key"], item["resource_id"], item["reason"])
        for item in raw_resources
    ] != sorted(
        (item["ticket_key"], item["resource_id"], item["reason"])
        for item in raw_resources
    ):
        _fail("EXCLUSIVE_RESOURCE_INVALID", "successor Exclusive Resources are not canonical")
    return classification


def _assert_acyclic(graph: Mapping[str, set[str]]) -> None:
    if len(graph) > _MAX_DEPENDENCY_NODES:
        _fail("DEPENDENCY_STRUCTURE_LIMIT", "successor dependency graph is too large")
    keys = set(graph)
    if any(type(key) is not str or not key for key in keys):
        _fail("DEPENDENCY_INVALID", "successor dependency graph has invalid nodes")
    if any(type(values) is not set or not values.issubset(keys) for values in graph.values()):
        _fail("DEPENDENCY_INVALID", "successor dependency graph names unselected work")
    edge_count = sum(len(values) for values in graph.values())
    if edge_count > _MAX_DEPENDENCY_EDGES:
        _fail("DEPENDENCY_STRUCTURE_LIMIT", "successor dependency graph has too many edges")
    # Kahn's algorithm is iterative and bounded; edge direction is dependent
    # -> prerequisite, but cycles are direction-independent.
    incoming = {key: 0 for key in keys}
    outgoing = {key: set() for key in keys}
    for source, prerequisites in graph.items():
        for prerequisite in prerequisites:
            outgoing[source].add(prerequisite)
            incoming[prerequisite] += 1
    ready = [key for key, count in incoming.items() if count == 0]
    heapq.heapify(ready)
    visited = 0
    while ready:
        node = heapq.heappop(ready)
        visited += 1
        for prerequisite in sorted(outgoing[node]):
            incoming[prerequisite] -= 1
            if incoming[prerequisite] == 0:
                heapq.heappush(ready, prerequisite)
    if visited != len(keys):
        _fail("DEPENDENCY_CYCLE", "successor dependency graph contains a cycle")


def _active_edges(work: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {
        (item["key"], dependency)
        for item in work
        for dependency in item["depends_on"]
    }


def _source_adoption_is_unchanged(
    snapshot: dict[str, Any],
    plan: dict[str, Any],
    work: list[dict[str, Any]],
) -> bool:
    """Tell both compiler entry points when source adoption is a no-op."""

    if "source_change_digest" not in snapshot:
        return False
    active_campaign = plan["campaign"]
    active_source_digest = active_campaign.get("source_change_digest")
    same_tracker_projection = (
        type(active_source_digest) is str
        and active_source_digest == snapshot["source_change_digest"]
    )
    same_policy_projection = (
        plan["policy"].get("digest") == snapshot["policy_witness"]["digest"]
    )
    source_tickets = {item["key"]: item for item in snapshot["tickets"]}
    current_by_key = {item["key"]: item for item in work}
    same_frozen_work = (
        set(source_tickets) == set(current_by_key)
        and all(
            source_tickets[key].get("source") == current_by_key[key].get("source")
            and source_tickets[key].get("contract")
            == current_by_key[key].get("contract")
            for key in source_tickets
        )
    )
    same_campaign_projection = (
        snapshot["target_branch"] == plan["target_branch"]
        and snapshot["campaign_source"] == active_campaign["source"]
        and snapshot.get("product_release") == active_campaign.get("product_release")
    )
    return (
        same_tracker_projection
        and same_policy_projection
        and same_frozen_work
        and same_campaign_projection
    )


def _derive_values(
    snapshot: dict[str, Any], classification: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    plan, work = _require_active_plan(snapshot)
    source_adoption = "source_change_digest" in snapshot
    source_tickets = {
        item["key"]: item for item in snapshot["tickets"]
    }
    if _source_adoption_is_unchanged(snapshot, plan, work):
        _fail(
            "SUCCESSOR_PLAN_UNCHANGED",
            "approved source projection is identical to the active Plan Revision",
        )
    selected = tuple(source_tickets) if source_adoption else tuple(item["key"] for item in work)
    selected_set = set(selected)
    successor = classification["successor"]
    owners = successor["approved_ticket_keys"]
    if not owners or not set(owners).issubset(selected_set):
        _fail(
            "PLAN_INVALIDATION_TICKET_INVALID",
            "successor owners must be approved Campaign Tickets",
        )

    current_edges = _active_edges(work)
    current_by_key = {item["key"]: item for item in work}
    graph = {
        key: {
            dependency
            for dependency in current_by_key[key]["depends_on"]
            if dependency in selected_set
        }
        for key in selected
        if key in current_by_key
    }
    if source_adoption:
        for key, ticket in source_tickets.items():
            graph.setdefault(key, set())
            graph[key].update(
                blocker["key"]
                for blocker in ticket.get("native_blockers", [])
                if (
                    type(blocker) is dict
                    and blocker.get("state") == "open"
                    and blocker.get("key") in selected_set
                )
            )
    additions: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str]] = set()
    for raw in successor["dependency_additions"]:
        source, target = raw["from"], raw["to"]
        edge = (source, target)
        if source not in selected_set or target not in selected_set or source == target:
            _fail(
                "PLAN_INVALIDATION_DEPENDENCY_UNPROVED",
                "successor dependencies must remain inside approved Campaign Tickets",
            )
        if edge in seen_edges:
            _fail("PLAN_INVALIDATION_DEPENDENCY_INVALID", "successor dependencies repeat an edge")
        if edge in current_edges:
            _fail("PLAN_INVALIDATION_DEPENDENCY_DUPLICATE", "successor repeats an active dependency edge")
        seen_edges.add(edge)
        graph[source].add(target)
        additions.append(
            {"from": source, "to": target, "reason": raw["reason"]}
        )
    _assert_acyclic(graph)
    additions.sort(key=lambda item: (item["from"], item["to"], item["reason"]))

    policy = _require_policy(snapshot.get("policy_witness", snapshot.get("policy")))
    allowed_resources = set(policy["exclusive_resources"])
    resources = {}
    capabilities = {}
    for key in selected:
        current = current_by_key.get(key)
        resources[key] = (
            [value for value in current["exclusive_resources"] if value in allowed_resources]
            if current is not None
            else []
        )
        allowed_capabilities = set(policy["allowed_capabilities"])
        capabilities[key] = (
            [value for value in current["capabilities"] if value in allowed_capabilities]
            if current is not None
            else []
        )
    active_resources = {
        (item["key"], resource)
        for item in work
        for resource in item["exclusive_resources"]
    }
    seen_resources: set[tuple[str, str]] = set()
    for raw in successor["exclusive_resource_additions"]:
        ticket, resource = raw["ticket_key"], raw["resource_id"]
        identity = (ticket, resource)
        if ticket not in selected_set or resource not in allowed_resources:
            _fail(
                "EXCLUSIVE_RESOURCE_INVALID",
                "successor names an unapproved Ticket or policy-unknown Exclusive Resource",
            )
        if identity in seen_resources:
            _fail("EXCLUSIVE_RESOURCE_DUPLICATE", "successor Exclusive Resources repeat a resource")
        if identity in active_resources:
            _fail("EXCLUSIVE_RESOURCE_DUPLICATE", "successor repeats an active Exclusive Resource")
        seen_resources.add(identity)
        resources[ticket].append(resource)
    for ticket in resources:
        resources[ticket] = sorted(set(resources[ticket]))

    intent = {
        "admitted_work": list(selected),
        "dependency_additions": additions,
        "exclusive_resources": {
            key: resources[key] for key in sorted(resources)
        },
        "capability_requirements": {
            key: capabilities[key] for key in sorted(capabilities)
        },
        "decision_requirements": [],
    }
    changed = source_adoption or bool(additions or seen_resources)
    return intent, changed


def derive_successor_plan_intent(
    snapshot: Mapping[str, Any],
    classification: Mapping[str, Any] | Any,
) -> dict[str, Any]:
    """Derive a canonical full-Campaign successor intent from frozen facts."""

    frozen = _require_snapshot(snapshot)
    normalized_classification = _require_classification(
        classification,
        snapshot_digest=_snapshot_identity(frozen),
        plan_revision_digest=frozen["active_plan_revision"]["digest"],
    )
    intent, changed = _derive_values(frozen, normalized_classification)
    if not changed:
        _fail(
            "SUCCESSOR_PLAN_UNCHANGED",
            "an approved owner without a dependency or resource delta is not a successor",
        )
    return _canonical(intent)


def _require_normalized_intent(
    snapshot: dict[str, Any], value: Any
) -> dict[str, Any]:
    intent = _canonical(value, code="SUCCESSOR_PLAN_INVALID")
    expected = {
        "admitted_work",
        "dependency_additions",
        "exclusive_resources",
        "capability_requirements",
        "decision_requirements",
    }
    _exact_mapping(
        intent,
        expected,
        code="SUCCESSOR_PLAN_INVALID",
        label="successor normalized intent",
    )
    plan, work = _require_active_plan(snapshot)
    source_adoption = "source_change_digest" in snapshot
    if _source_adoption_is_unchanged(snapshot, plan, work):
        _fail(
            "SUCCESSOR_PLAN_UNCHANGED",
            "approved source projection is identical to the active Plan Revision",
        )
    selected = [
        item["key"] for item in snapshot["tickets"]
    ] if source_adoption else [item["key"] for item in work]
    if intent["admitted_work"] != selected:
        _fail("PLAN_INVALIDATION_TICKET_INVALID", "successor intent must retain complete Campaign membership")
    if intent["decision_requirements"] != []:
        _fail("PLAN_INVALIDATION_DECISION_INVALID", "successor intent cannot carry a Decision")

    current_by_key = {item["key"]: item for item in work}
    source_by_key = {
        item["key"]: item for item in snapshot["tickets"]
    }
    current_edges = _active_edges(work)
    graph = {
        key: {
            dependency
            for dependency in current_by_key[key]["depends_on"]
            if dependency in set(selected)
        }
        for key in selected
        if key in current_by_key
    }
    if source_adoption:
        for key, ticket in source_by_key.items():
            graph.setdefault(key, set())
            graph[key].update(
                blocker["key"]
                for blocker in ticket.get("native_blockers", [])
                if (
                    type(blocker) is dict
                    and blocker.get("state") == "open"
                    and blocker.get("key") in set(selected)
                )
            )
    seen_edges: set[tuple[str, str]] = set()
    additions = intent["dependency_additions"]
    if type(additions) is not list:
        _fail("PLAN_INVALIDATION_DEPENDENCY_INVALID", "successor intent dependencies are not a list")
    for item in additions:
        item = _exact_mapping(
            item,
            {"from", "to", "reason"},
            code="PLAN_INVALIDATION_DEPENDENCY_INVALID",
            label="successor intent dependency",
        )
        source, target = item["from"], item["to"]
        _text(source, "dependency source", code="PLAN_INVALIDATION_DEPENDENCY_INVALID")
        _text(target, "dependency target", code="PLAN_INVALIDATION_DEPENDENCY_INVALID")
        _text(item["reason"], "dependency reason", code="PLAN_INVALIDATION_DEPENDENCY_INVALID")
        edge = (source, target)
        if source not in graph or target not in graph or source == target:
            _fail("PLAN_INVALIDATION_DEPENDENCY_UNPROVED", "successor intent dependency leaves the Campaign")
        if edge in current_edges or edge in seen_edges:
            _fail("PLAN_INVALIDATION_DEPENDENCY_DUPLICATE", "successor intent repeats a dependency")
        seen_edges.add(edge)
        graph[source].add(target)
    if [
        (item["from"], item["to"], item["reason"]) for item in additions
    ] != sorted(
        (item["from"], item["to"], item["reason"]) for item in additions
    ):
        _fail("PLAN_INVALIDATION_DEPENDENCY_INVALID", "successor intent dependencies are not canonical")
    _assert_acyclic(graph)

    policy = _require_policy(snapshot.get("policy_witness", snapshot.get("policy")))
    selected_set = set(selected)
    resources = intent["exclusive_resources"]
    if type(resources) is not dict or set(resources) != selected_set:
        _fail("PLAN_INVALIDATION_RESOURCE_INVALID", "successor intent must account for every Ticket resource set")
    changed = False
    for key in selected:
        item = current_by_key.get(key)
        values = _sorted_unique_texts(resources[key], "successor intent Exclusive Resources")
        if item is not None and not source_adoption and not set(item["exclusive_resources"]).issubset(values):
            _fail("EXCLUSIVE_RESOURCE_INVALID", "successor intent cannot remove an active Exclusive Resource")
        if any(value not in policy["exclusive_resources"] for value in values):
            _fail("EXCLUSIVE_RESOURCE_INVALID", "successor intent names a policy-unknown Exclusive Resource")
        if item is None or values != item["exclusive_resources"]:
            changed = True
    capabilities = intent["capability_requirements"]
    if type(capabilities) is not dict or set(capabilities) != selected_set:
        _fail("CAPABILITY_INVALID", "successor intent must account for every Ticket capability set")
    for key in selected:
        item = current_by_key.get(key)
        values = _sorted_unique_texts(capabilities[key], "successor intent capabilities")
        if item is not None and not source_adoption and values != item["capabilities"]:
            _fail("CAPABILITY_INVALID", "successor cannot change Ticket capabilities")
        if any(value not in policy["allowed_capabilities"] for value in values):
            _fail("CAPABILITY_INVALID", "successor intent names a policy-unknown capability")
    if seen_edges:
        changed = True
    if not changed and not source_adoption:
        _fail("SUCCESSOR_PLAN_UNCHANGED", "successor intent has no dependency or resource delta")
    return intent


def _authority(policy_digest: str, grants: list[dict[str, Any]]) -> dict[str, Any]:
    core = {
        "policy_witness_digest": policy_digest,
        "grants": deepcopy(grants),
    }
    return {**core, "subtree_digest": digest_value(core)}


def compile_successor_plan_spec(
    snapshot: Mapping[str, Any],
    normalized_intent: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile a complete PlanSpec while changing only successor deltas."""

    frozen = _require_snapshot(snapshot)
    intent = _require_normalized_intent(frozen, normalized_intent)
    active, work = _require_active_plan(frozen)
    source_adoption = "source_change_digest" in frozen
    additions = {
        (item["from"], item["to"])
        for item in intent["dependency_additions"]
    }
    source_by_key = {
        item["key"]: item for item in frozen["tickets"]
    }
    current_by_key = {item["key"]: item for item in work}
    policy = _require_policy(frozen.get("policy_witness", frozen.get("policy")))
    compiled_work: list[dict[str, Any]] = []
    work_keys = intent["admitted_work"] if source_adoption else [item["key"] for item in work]
    for key in work_keys:
        current = current_by_key.get(key)
        source_ticket = source_by_key.get(key)
        if source_adoption and source_ticket is None:
            _fail("PLAN_INVALIDATION_TICKET_INVALID", "successor intent names a Ticket outside approved source membership")
        if current is None and not source_adoption:
            _fail("PLAN_INVALIDATION_TICKET_INVALID", "successor intent names a Ticket outside active membership")
        dependencies = {
            dependency
            for dependency in (current["depends_on"] if current is not None else ())
            if dependency in set(work_keys)
        }
        if source_adoption and source_ticket is not None:
            dependencies.update(
                blocker["key"]
                for blocker in source_ticket.get("native_blockers", [])
                if (
                    type(blocker) is dict
                    and blocker.get("state") == "open"
                    and blocker.get("key") in set(work_keys)
                )
            )
        dependencies.update(
            target for source, target in additions if source == key
        )
        if source_adoption:
            capabilities = deepcopy(intent["capability_requirements"][key])
            authority = {
                "policy_witness_digest": policy["digest"],
                "worker": _authority(policy["digest"], policy["authority_grants"]["worker"]),
                "recovery_worker": _authority(policy["digest"], policy["authority_grants"]["recovery_worker"]),
                "review": _authority(policy["digest"], policy["authority_grants"]["review"]),
            }
            source = deepcopy(source_ticket["source"])
            contract = deepcopy(source_ticket["contract"])
        else:
            capabilities = deepcopy(current["capabilities"])
            authority = deepcopy(current["authority"])
            source = deepcopy(current["source"])
            contract = deepcopy(current["contract"])
        compiled_work.append(
            {
                "key": key,
                "source": source,
                "contract": contract,
                "depends_on": sorted(dependencies),
                "exclusive_resources": deepcopy(intent["exclusive_resources"][key]),
                "capabilities": capabilities,
                "authority": authority,
            }
        )
    graph = {
        item["key"]: set(item["depends_on"]) for item in compiled_work
    }
    _assert_acyclic(graph)
    if source_adoption:
        campaign = {
            "key": active["campaign"]["key"],
            "source": deepcopy(frozen["campaign_source"]),
            "authority": _authority(
                policy["digest"],
                policy["authority_grants"]["campaign"],
            ),
            "product_release": deepcopy(frozen["product_release"]),
            "source_change_digest": frozen["source_change_digest"],
        }
        plan_policy = {"ref": policy["ref"], "digest": policy["digest"]}
    else:
        campaign = deepcopy(active["campaign"])
        plan_policy = deepcopy(active["policy"])
    result = {
        "schema_version": active["schema_version"],
        "repository": active["repository"],
        "target_branch": deepcopy(frozen["target_branch"] if source_adoption else active["target_branch"]),
        "campaign": campaign,
        "policy": plan_policy,
        "work": compiled_work,
    }
    return _canonical(result)


def _source_projection(
    snapshot: dict[str, Any], *, observed: bool = False
) -> dict[str, Any]:
    return {
        "target_branch": snapshot.get("target_branch"),
        "campaign_source": snapshot.get("campaign_source"),
        "membership": snapshot.get("membership"),
        "tickets": snapshot.get("tickets"),
        "product_release": snapshot.get("product_release"),
        "source_change_digest": snapshot.get("source_change_digest"),
        "external_dependencies": snapshot.get("external_dependencies", []),
        "policy": snapshot.get("policy") if observed else snapshot["policy_witness"],
    }


def validate_fresh_successor_source(
    snapshot: Mapping[str, Any], fresh_source: Mapping[str, Any]
) -> None:
    """Reject authoritative source drift before a successor is published."""

    frozen = _require_snapshot(snapshot)
    observed = _canonical(fresh_source, code="REPLAN_SOURCE_CHANGED")
    if type(observed) is not dict:
        _fail("REPLAN_SOURCE_CHANGED", "fresh authoritative source is not an object")
    expected_projection = _source_projection(frozen)
    observed_projection = _source_projection(observed, observed=True)
    source_fields = (
        "target_branch",
        "campaign_source",
        "membership",
        "tickets",
        "product_release",
        "source_change_digest",
        "external_dependencies",
    )
    if any(
        observed_projection[field] != expected_projection[field]
        for field in source_fields
    ):
        _fail(
            "REPLAN_SOURCE_CHANGED",
            "authoritative source no longer equals the validated replanning snapshot",
        )
    if observed_projection["policy"] != expected_projection["policy"]:
        _fail(
            "REPLAN_POLICY_CHANGED",
            "Policy Witness no longer equals the validated replanning snapshot",
        )
