"""Strict V3 snapshot, Plan Intent, and PlanSpec schemas."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from ._v3_canonical import (
    digest as _digest,
    strict_json_bytes as _strict_json_bytes,
    strict_json_decode as _strict_json_decode,
)
from ._v3_types import (
    DIGEST_PATTERN as _DIGEST,
    CampaignHandle,
    PlanControlError,
    PlanRevision as _PlanRevision,
)


_TRIAGE_LABELS = frozenset(
    {"needs-triage", "needs-info", "ready-for-agent", "ready-for-human", "wontfix"}
)
_POLICY_ROLES = ("campaign", "worker", "recovery_worker", "review")
_OPERATION_ROOTS = frozenset(
    {"artifact", "ci", "git", "github", "repository", "workspace"}
)
_RESOURCE_ROOTS = frozenset(
    {
        "artifact",
        "campaign",
        "candidate",
        "repository",
        "review",
        "target",
        "work-run",
    }
)
_VERSIONED_IDENTIFIER = re.compile(
    r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\.v[1-9][0-9]*$"
)
_CAPABILITY_ID = re.compile(
    r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9][a-z0-9_-]*)*$"
)


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PlanControlError("SNAPSHOT_INVALID", f"{label} must be a string")
    return value


def _require_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise PlanControlError(
            "SNAPSHOT_INVALID", f"{label} must be a string list"
        )
    if len(set(value)) != len(value):
        raise PlanControlError("SNAPSHOT_INVALID", f"{label} contains duplicates")
    return sorted(value)


def _frozen_ref(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"ref", "digest"}:
        raise PlanControlError(
            "SNAPSHOT_INVALID", f"{label} must contain ref and digest"
        )
    return {
        "ref": _require_string(value["ref"], f"{label} ref"),
        "digest": _require_string(value["digest"], f"{label} digest"),
    }


def _versioned_identifier(value: str) -> bool:
    return bool(_VERSIONED_IDENTIFIER.fullmatch(value)) and not value.startswith("-")


def _authority_id(value: Any, *, label: str, roots: frozenset[str]) -> str:
    if not isinstance(value, str) or not value:
        raise PlanControlError(
            "POLICY_WITNESS_INVALID", f"{label} must be a non-empty string"
        )
    identifier = value
    if not _versioned_identifier(identifier):
        raise PlanControlError(
            "POLICY_WITNESS_INVALID", f"{label} is not a versioned identifier"
        )
    root = identifier.split(".", 1)[0]
    if root not in roots:
        raise PlanControlError(
            "POLICY_WITNESS_INVALID", f"{label} uses an unknown root"
        )
    return identifier


def _canonical_grants(value: Any, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise PlanControlError(
            "POLICY_WITNESS_INVALID", f"{label} must be a list"
        )
    grants: list[dict[str, str]] = []
    for grant in value:
        if not isinstance(grant, dict) or set(grant) != {
            "operation_id",
            "resource_id",
        }:
            raise PlanControlError(
                "POLICY_WITNESS_INVALID", f"{label} contains an invalid grant"
            )
        grants.append(
            {
                "operation_id": _authority_id(
                    grant["operation_id"],
                    label="operation_id",
                    roots=_OPERATION_ROOTS,
                ),
                "resource_id": _authority_id(
                    grant["resource_id"],
                    label="resource_id",
                    roots=_RESOURCE_ROOTS,
                ),
            }
        )
    identities = {
        (grant["operation_id"], grant["resource_id"]) for grant in grants
    }
    if len(identities) != len(grants):
        raise PlanControlError(
            "POLICY_WITNESS_INVALID", f"{label} repeats a grant"
        )
    return sorted(
        grants, key=lambda item: (item["operation_id"], item["resource_id"])
    )


def _normalize_policy(value: Any) -> dict[str, Any]:
    expected = {
        "schema_version",
        "ref",
        "digest",
        "authority_grants",
        "allowed_capabilities",
        "exclusive_resources",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise PlanControlError(
            "POLICY_WITNESS_INVALID",
            "Policy Witness contains unsupported or missing fields",
        )
    if value["schema_version"] != 1:
        raise PlanControlError(
            "POLICY_WITNESS_INVALID", "Policy Witness schema_version must be 1"
        )
    raw_grants = value["authority_grants"]
    if not isinstance(raw_grants, dict) or set(raw_grants) != set(_POLICY_ROLES):
        raise PlanControlError(
            "POLICY_WITNESS_INVALID",
            "Policy Witness authority roles are incomplete",
        )
    ref = value["ref"]
    if not isinstance(ref, str) or not ref or ref.startswith("-"):
        raise PlanControlError(
            "POLICY_WITNESS_INVALID", "Policy Witness ref is invalid"
        )
    core = {
        "schema_version": 1,
        "ref": ref,
        "authority_grants": {
            role: _canonical_grants(raw_grants[role], f"{role} grants")
            for role in _POLICY_ROLES
        },
        "allowed_capabilities": _canonical_capabilities(
            value["allowed_capabilities"],
            "allowed_capabilities",
        ),
        "exclusive_resources": _canonical_policy_facts(
            value["exclusive_resources"],
            "exclusive_resources",
            _RESOURCE_ROOTS,
        ),
    }
    witness_digest = value["digest"]
    if (
        not isinstance(witness_digest, str)
        or not _DIGEST.fullmatch(witness_digest)
        or witness_digest != _digest(_strict_json_bytes(core))
    ):
        raise PlanControlError(
            "POLICY_WITNESS_INVALID",
            "Policy Witness digest does not bind its canonical facts",
        )
    return {**core, "digest": witness_digest}


def _canonical_policy_facts(
    value: Any,
    label: str,
    roots: frozenset[str],
) -> list[str]:
    if not isinstance(value, list):
        raise PlanControlError(
            "POLICY_WITNESS_INVALID", f"{label} must be a list"
        )
    facts = [
        _authority_id(item, label=label, roots=roots)
        for item in value
    ]
    if len(set(facts)) != len(facts):
        raise PlanControlError(
            "POLICY_WITNESS_INVALID", f"{label} contains duplicates"
        )
    return sorted(facts)


def _canonical_capabilities(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(
            not isinstance(item, str)
            or not _CAPABILITY_ID.fullmatch(item)
            for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise PlanControlError(
            "POLICY_WITNESS_INVALID",
            f"{label} must contain unique provider-neutral capability IDs",
        )
    return sorted(value)


def _normalize_ticket(value: Any) -> dict[str, Any]:
    expected = {"key", "labels", "source", "contract", "native_blockers"}
    if not isinstance(value, dict) or set(value) != expected:
        raise PlanControlError(
            "SNAPSHOT_INVALID",
            "Ticket snapshot contains unsupported or missing fields",
        )
    key = _require_string(value["key"], "Ticket key")
    labels = _require_string_list(value["labels"], "Ticket labels")
    label_set = set(labels)
    if "ready-for-agent" not in label_set or label_set.intersection(
        _TRIAGE_LABELS - {"ready-for-agent"}
    ):
        raise PlanControlError(
            "TICKET_LABEL_INVALID", f"Ticket {key} is not ready-for-agent"
        )
    contract = value["contract"]
    if not isinstance(contract, dict) or set(contract) != {"title", "body"}:
        raise PlanControlError(
            "TICKET_CONTRACT_MISSING",
            f"Ticket {key} must freeze exact title and body",
        )
    normalized_contract = {
        "title": _require_string(contract["title"], "Ticket title"),
        "body": _require_string(contract["body"], "Ticket body"),
    }
    blockers = value["native_blockers"]
    if not isinstance(blockers, list):
        raise PlanControlError(
            "SNAPSHOT_INVALID", "native_blockers must be a list"
        )
    normalized_blockers: list[dict[str, str]] = []
    for blocker in blockers:
        if not isinstance(blocker, dict) or set(blocker) != {"key", "state"}:
            raise PlanControlError(
                "SNAPSHOT_INVALID",
                "native blocker must contain key and state",
            )
        state = _require_string(blocker["state"], "blocker state").lower()
        if state not in {"open", "closed"}:
            raise PlanControlError(
                "SNAPSHOT_INVALID", "blocker state must be open or closed"
            )
        normalized_blockers.append(
            {
                "key": _require_string(blocker["key"], "blocker key"),
                "state": state,
            }
        )
    if len({blocker["key"] for blocker in normalized_blockers}) != len(
        normalized_blockers
    ):
        raise PlanControlError(
            "SNAPSHOT_INVALID", "native blockers repeat a Ticket key"
        )
    return {
        "key": key,
        "labels": labels,
        "source": _frozen_ref(value["source"], "Ticket source"),
        "contract": normalized_contract,
        "native_blockers": sorted(
            normalized_blockers, key=lambda item: (item["key"], item["state"])
        ),
    }


def _normalize_snapshot(
    value: Any, repository: str, ready_refs: tuple[str, ...]
) -> dict[str, Any]:
    # Reject non-JSON caller objects (including NaN/Infinity) before semantic
    # validation so no lossy normalization can conceal them.
    value = _strict_json_decode(_strict_json_bytes(value))
    expected = {
        "repository",
        "target_branch",
        "campaign_source",
        "policy",
        "tickets",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise PlanControlError(
            "SNAPSHOT_INVALID",
            "Campaign snapshot contains unsupported or missing fields",
        )
    if value["repository"] != repository:
        raise PlanControlError(
            "SNAPSHOT_REPOSITORY_MISMATCH", "snapshot repository differs"
        )
    raw_tickets = value["tickets"]
    if not isinstance(raw_tickets, list):
        raise PlanControlError("SNAPSHOT_INVALID", "tickets must be a list")
    tickets = sorted(
        (_normalize_ticket(ticket) for ticket in raw_tickets),
        key=lambda item: item["key"],
    )
    keys = [ticket["key"] for ticket in tickets]
    if len(set(keys)) != len(keys) or set(keys) != set(ready_refs):
        raise PlanControlError(
            "SNAPSHOT_OMISSION",
            "snapshot must contain exactly every selected Ticket",
        )
    dependencies = {
        ticket["key"]: {
            blocker["key"]
            for blocker in ticket["native_blockers"]
            if blocker["key"] in keys
        }
        for ticket in tickets
    }
    _assert_acyclic(dependencies)
    snapshot = {
        "schema_version": 1,
        "repository": repository,
        "target_branch": _require_string(
            value["target_branch"], "target_branch"
        ),
        "campaign_source": _frozen_ref(
            value["campaign_source"], "Campaign source"
        ),
        "policy": _normalize_policy(value["policy"]),
        "tickets": tickets,
    }
    # Strict encoding here copies every caller-owned object.
    return _strict_json_decode(_strict_json_bytes(snapshot))


def _assert_acyclic(dependencies: Mapping[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise PlanControlError(
                "DEPENDENCY_CYCLE", "canonical Ticket blockers contain a cycle"
            )
        if key in visited:
            return
        visiting.add(key)
        for dependency in sorted(dependencies[key]):
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in sorted(dependencies):
        visit(key)


def _ready_refs(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PlanControlError(
            "READY_REFS_INVALID", "ready_refs must be a sequence"
        )
    refs = tuple(value)
    if not refs or any(not isinstance(item, str) or not item for item in refs):
        raise PlanControlError(
            "READY_REFS_INVALID", "ready_refs must be non-empty strings"
        )
    if len(set(refs)) != len(refs):
        raise PlanControlError(
            "READY_REFS_INVALID", "ready_refs must not repeat a Ticket"
        )
    return tuple(sorted(refs))


def _validate_options_for_tickets(
    options: Any, ticket_keys: set[str]
) -> None:
    for ticket_key, _role, _profile in options.runtime_profile_overrides:
        if ticket_key not in ticket_keys:
            raise PlanControlError(
                "START_OPTIONS_INVALID",
                f"Runtime override names unselected Ticket {ticket_key}",
            )


def _normalize_intent(value: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "admitted_work",
        "dependency_additions",
        "exclusive_resources",
        "capability_requirements",
        "decision_requirements",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise PlanControlError(
            "PLAN_INTENT_INVALID",
            "Planning output contains unsupported or missing fields",
        )
    selected = {ticket["key"] for ticket in snapshot["tickets"]}
    admitted = value["admitted_work"]
    if not isinstance(admitted, list) or set(admitted) != selected or len(
        admitted
    ) != len(selected):
        raise PlanControlError(
            "PLAN_INTENT_OMISSION",
            "Planning output must account for every selected Ticket",
        )
    dependencies = {
        ticket["key"]: {
            blocker["key"]
            for blocker in ticket["native_blockers"]
            if blocker["key"] in selected
        }
        for ticket in snapshot["tickets"]
    }
    raw_additions = value["dependency_additions"]
    if not isinstance(raw_additions, list):
        raise PlanControlError(
            "PLAN_INTENT_INVALID", "dependency_additions must be a list"
        )
    additions: list[dict[str, str]] = []
    for item in raw_additions:
        if not isinstance(item, dict) or set(item) != {"from", "to", "reason"}:
            raise PlanControlError(
                "PLAN_INTENT_INVALID", "dependency addition is invalid"
            )
        source = _require_string(item["from"], "dependency source")
        target = _require_string(item["to"], "dependency target")
        reason = _require_string(item["reason"], "dependency reason")
        if source not in selected or target not in selected or source == target:
            raise PlanControlError(
                "PLAN_INTENT_INVALID",
                "dependency addition must stay within selected work",
            )
        dependencies[source].add(target)
        additions.append({"from": source, "to": target, "reason": reason})
    if len({(item["from"], item["to"]) for item in additions}) != len(additions):
        raise PlanControlError(
            "PLAN_INTENT_INVALID", "dependency additions repeat an edge"
        )
    _assert_acyclic(dependencies)
    policy = snapshot["policy"]
    exclusive = _per_ticket_facts(
        value["exclusive_resources"], selected, "exclusive_resources"
    )
    if any(
        fact not in policy["exclusive_resources"]
        for facts in exclusive.values()
        for fact in facts
    ):
        raise PlanControlError(
            "EXCLUSIVE_RESOURCE_INVALID",
            "Planning output names an unknown Exclusive Resource",
        )
    capabilities = _per_ticket_facts(
        value["capability_requirements"], selected, "capability_requirements"
    )
    if any(
        fact not in policy["allowed_capabilities"]
        for facts in capabilities.values()
        for fact in facts
    ):
        raise PlanControlError(
            "CAPABILITY_INVALID",
            "Planning output names an unknown capability",
        )
    raw_findings = value["decision_requirements"]
    if not isinstance(raw_findings, list):
        raise PlanControlError(
            "PLAN_INTENT_INVALID", "decision_requirements must be a list"
        )
    findings: list[dict[str, Any]] = []
    for finding in raw_findings:
        if not isinstance(finding, dict) or set(finding) not in (
            {"code", "detail"},
            {"code", "detail", "ticket_key"},
        ):
            raise PlanControlError(
                "PLAN_INTENT_INVALID", "Decision finding is invalid"
            )
        ticket_key = finding.get("ticket_key")
        if ticket_key is not None and ticket_key not in selected:
            raise PlanControlError(
                "PLAN_INTENT_INVALID",
                "Decision finding names unselected work",
            )
        findings.append(
            {
                "code": _require_string(finding["code"], "Decision code"),
                "detail": _require_string(finding["detail"], "Decision detail"),
                "ticket_key": ticket_key,
            }
        )
    if len(
        {
            (item["code"], item["detail"], item["ticket_key"])
            for item in findings
        }
    ) != len(findings):
        raise PlanControlError(
            "PLAN_INTENT_INVALID", "Decision findings contain duplicates"
        )
    normalized = {
        "admitted_work": sorted(admitted),
        "dependency_additions": sorted(
            additions, key=lambda item: (item["from"], item["to"], item["reason"])
        ),
        "exclusive_resources": {
            key: exclusive[key] for key in sorted(exclusive)
        },
        "capability_requirements": {
            key: capabilities[key] for key in sorted(capabilities)
        },
        "decision_requirements": sorted(
            findings,
            key=lambda item: (
                item["code"],
                item["ticket_key"] or "",
                item["detail"],
            ),
        ),
    }
    return _strict_json_decode(_strict_json_bytes(normalized))


def _per_ticket_facts(
    value: Any, selected: set[str], label: str
) -> dict[str, list[str]]:
    if not isinstance(value, dict) or not set(value).issubset(selected):
        raise PlanControlError(
            "PLAN_INTENT_INVALID", f"{label} names unselected work"
        )
    result: dict[str, list[str]] = {}
    for key in selected:
        raw = value.get(key, [])
        if not isinstance(raw, list) or any(
            not isinstance(item, str) or not item for item in raw
        ):
            raise PlanControlError(
                "PLAN_INTENT_INVALID", f"{label} values must be string lists"
            )
        if len(set(raw)) != len(raw):
            raise PlanControlError(
                "PLAN_INTENT_INVALID", f"{label} repeats a value"
            )
        result[key] = sorted(raw)
    return result


def _authority_subtree(
    policy_digest: str, grants: list[dict[str, str]]
) -> dict[str, Any]:
    core = {
        "policy_witness_digest": policy_digest,
        "grants": grants,
    }
    return {**core, "subtree_digest": _digest(_strict_json_bytes(core))}


def _compile_plan(
    *,
    snapshot_bytes: bytes,
    snapshot_digest: str,
    intent_bytes: bytes,
    intent_digest: str,
    handle: CampaignHandle,
) -> _PlanRevision:
    if _digest(snapshot_bytes) != snapshot_digest:
        raise PlanControlError(
            "SNAPSHOT_DIGEST_MISMATCH", "persisted snapshot digest changed"
        )
    if _digest(intent_bytes) != intent_digest:
        raise PlanControlError(
            "PLAN_INTENT_DIGEST_MISMATCH", "persisted Plan Intent digest changed"
        )
    snapshot = _strict_json_decode(snapshot_bytes)
    intent = _normalize_intent(_strict_json_decode(intent_bytes), snapshot)
    if _strict_json_bytes(intent) != intent_bytes:
        raise PlanControlError(
            "PLAN_INTENT_READBACK_MISMATCH",
            "validated Plan Intent bytes changed before compilation",
        )
    policy = snapshot["policy"]
    policy_digest = policy["digest"]
    dependencies = {
        ticket["key"]: {blocker["key"] for blocker in ticket["native_blockers"]}
        for ticket in snapshot["tickets"]
    }
    for addition in intent["dependency_additions"]:
        dependencies[addition["from"]].add(addition["to"])
    work = []
    for ticket in snapshot["tickets"]:
        key = ticket["key"]
        work.append(
            {
                "key": key,
                "source": ticket["source"],
                "contract": ticket["contract"],
                "depends_on": sorted(dependencies[key]),
                "exclusive_resources": intent["exclusive_resources"][key],
                "capabilities": intent["capability_requirements"][key],
                "authority": {
                    "policy_witness_digest": policy_digest,
                    "worker": _authority_subtree(
                        policy_digest,
                        policy["authority_grants"]["worker"],
                    ),
                    "recovery_worker": _authority_subtree(
                        policy_digest,
                        policy["authority_grants"]["recovery_worker"],
                    ),
                    "review": _authority_subtree(
                        policy_digest,
                        policy["authority_grants"]["review"],
                    ),
                },
            }
        )
    plan_spec = {
        "schema_version": 3,
        "repository": snapshot["repository"],
        "target_branch": snapshot["target_branch"],
        "campaign": {
            "key": handle.campaign_key,
            "source": snapshot["campaign_source"],
            "authority": _authority_subtree(
                policy_digest, policy["authority_grants"]["campaign"]
            ),
        },
        "policy": {
            "schema_version": policy["schema_version"],
            "ref": policy["ref"],
            "digest": policy_digest,
            "allowed_capabilities": policy["allowed_capabilities"],
            "exclusive_resources": policy["exclusive_resources"],
        },
        "work": work,
    }
    canonical = _strict_json_bytes(plan_spec)
    _validate_plan_spec(canonical)
    return _PlanRevision(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        snapshot_digest=snapshot_digest,
        canonical_bytes=canonical,
        digest=_digest(canonical),
    )


def _validate_plan_spec(canonical: bytes) -> None:
    value = _strict_json_decode(canonical)
    top_fields = {
        "schema_version",
        "repository",
        "target_branch",
        "campaign",
        "policy",
        "work",
    }
    if (
        not isinstance(value, dict)
        or set(value) != top_fields
        or value["schema_version"] != 3
        or not isinstance(value["repository"], str)
        or not value["repository"]
        or not isinstance(value["target_branch"], str)
        or not value["target_branch"]
        or not isinstance(value["campaign"], dict)
        or not isinstance(value["policy"], dict)
        or not isinstance(value["work"], list)
        or not value["work"]
    ):
        raise PlanControlError(
            "PLANSPEC_V3_INVALID", "PlanSpec top-level schema is invalid"
        )
    campaign = value["campaign"]
    if (
        set(campaign) != {"key", "source", "authority"}
        or not isinstance(campaign["key"], str)
        or not campaign["key"]
        or not _valid_plan_ref(campaign["source"])
    ):
        raise PlanControlError(
            "PLANSPEC_V3_INVALID", "Campaign manifest schema is invalid"
        )
    policy = value["policy"]
    if set(policy) != {
        "schema_version",
        "ref",
        "digest",
        "allowed_capabilities",
        "exclusive_resources",
    } or (
        policy["schema_version"] != 1
        or not isinstance(policy["ref"], str)
        or not policy["ref"]
        or not isinstance(policy["digest"], str)
        or not _DIGEST.fullmatch(policy["digest"])
    ):
        raise PlanControlError(
            "PLANSPEC_V3_INVALID", "Policy Witness projection is invalid"
        )
    allowed_capabilities = _canonical_capabilities(
        policy["allowed_capabilities"],
        "PlanSpec allowed_capabilities",
    )
    exclusive_resources = _canonical_policy_facts(
        policy["exclusive_resources"],
        "PlanSpec exclusive_resources",
        _RESOURCE_ROOTS,
    )
    if (
        allowed_capabilities != policy["allowed_capabilities"]
        or exclusive_resources != policy["exclusive_resources"]
    ):
        raise PlanControlError(
            "PLANSPEC_V3_INVALID",
            "Policy Witness facts are not canonical",
        )
    work_keys: list[str] = []
    for work in value["work"]:
        if not isinstance(work, dict) or set(work) != {
            "key",
            "source",
            "contract",
            "depends_on",
            "exclusive_resources",
            "capabilities",
            "authority",
        } or (
            not isinstance(work["key"], str)
            or not work["key"]
            or not _valid_plan_ref(work["source"])
            or not isinstance(work["contract"], dict)
            or set(work["contract"]) != {"title", "body"}
            or any(
                not isinstance(work["contract"][field], str)
                or not work["contract"][field]
                for field in ("title", "body")
            )
            or not _canonical_plan_string_list(work["depends_on"])
            or not _canonical_plan_string_list(work["exclusive_resources"])
            or not _canonical_plan_string_list(work["capabilities"])
            or not set(work["exclusive_resources"]).issubset(
                exclusive_resources
            )
            or not set(work["capabilities"]).issubset(
                allowed_capabilities
            )
        ):
            raise PlanControlError(
                "PLANSPEC_V3_INVALID", "work manifest schema is invalid"
            )
        work_keys.append(work["key"])
    if work_keys != sorted(set(work_keys)):
        raise PlanControlError(
            "PLANSPEC_V3_INVALID", "work manifests are not uniquely ordered"
        )
    dependencies = {
        work["key"]: {
            dependency
            for dependency in work["depends_on"]
            if dependency in work_keys
        }
        for work in value["work"]
    }
    _assert_acyclic(dependencies)
    policy_digest = policy["digest"]
    _validate_authority(campaign["authority"], policy_digest)
    for work in value["work"]:
        authority = work["authority"]
        if not isinstance(authority, dict) or set(authority) != {
            "policy_witness_digest",
            "worker",
            "recovery_worker",
            "review",
        } or authority["policy_witness_digest"] != policy_digest:
            raise PlanControlError(
                "AUTHORITY_SUBTREE_INVALID", "work authority envelope is invalid"
            )
        for role in ("worker", "recovery_worker", "review"):
            _validate_authority(authority[role], policy_digest)


def _valid_plan_ref(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"ref", "digest"}
        and isinstance(value["ref"], str)
        and bool(value["ref"])
        and isinstance(value["digest"], str)
        and bool(value["digest"])
    )


def _canonical_plan_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and item for item in value)
        and value == sorted(set(value))
    )


def _validate_authority(value: Any, policy_digest: str) -> None:
    if not isinstance(value, dict) or set(value) != {
        "policy_witness_digest",
        "grants",
        "subtree_digest",
    }:
        raise PlanControlError(
            "AUTHORITY_SUBTREE_INVALID", "authority subtree schema is invalid"
        )
    core = {
        "policy_witness_digest": value["policy_witness_digest"],
        "grants": value["grants"],
    }
    if (
        value["policy_witness_digest"] != policy_digest
        or not isinstance(value["subtree_digest"], str)
        or not _DIGEST.fullmatch(value["subtree_digest"])
        or value["subtree_digest"] != _digest(_strict_json_bytes(core))
    ):
        raise PlanControlError(
            "AUTHORITY_SUBTREE_INVALID", "authority subtree digest is invalid"
        )
    if _canonical_grants(value["grants"], "PlanSpec grants") != value["grants"]:
        raise PlanControlError(
            "AUTHORITY_SUBTREE_INVALID",
            "authority grants are not canonical",
        )
