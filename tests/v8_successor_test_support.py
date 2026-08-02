"""Deterministic fixtures and doubles shared by the #135 successor tests.

The builders in this module deliberately do not call successor production
code.  They are the frozen, independent input projections used to exercise
the pure compiler and identity modules before the integrated harness is
assembled.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _digest(value: Any) -> str:
    from gwo_v8._canonical import digest_value

    return digest_value(value)


def _policy() -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": 1,
        "ref": "policy:successor",
        "authority_grants": {
            "campaign": [
                {
                    "operation_id": "repository.read.v1",
                    "resource_id": "campaign.snapshot.v1",
                }
            ],
            "worker": [
                {
                    "operation_id": "workspace.write.v1",
                    "resource_id": "work-run.workspace.v1",
                }
            ],
            "recovery_worker": [
                {
                    "operation_id": "workspace.write.v1",
                    "resource_id": "work-run.workspace.v1",
                }
            ],
            "review": [
                {
                    "operation_id": "repository.read.v1",
                    "resource_id": "review.subject.v1",
                }
            ],
        },
        "allowed_capabilities": ["git", "local_check"],
        "exclusive_resources": ["repository.target.v1"],
    }
    value["digest"] = _digest(value)
    return value


def _campaign_source() -> dict[str, Any]:
    value = {
        "repository": "owner/repository",
        "input_ref": "refs/heads/main",
        "resolved_commit_oid": "a" * 40,
        "tree_oid": "b" * 40,
    }
    return {**value, "digest": _digest(value)}


def _contract(number: int, *, body: str | None = None, state: str = "open") -> dict[str, Any]:
    return {
        "id": number,
        "node_id": f"ISSUE_{number}",
        "number": number,
        "title": f"Successor contract {number}",
        "body": body or f"Complete successor work {number}",
        "state": state,
        "state_reason": None,
        "type": None,
        "repository": {
            "full_name": "owner/repository",
            "url": "https://api.github.com/repos/owner/repository",
        },
        "labels": [
            {
                "id": 1,
                "node_id": "LABEL_READY",
                "url": "https://api.github.com/repos/owner/repository/labels/ready-for-agent",
                "name": "ready-for-agent",
                "color": "0052cc",
                "default": False,
                "description": "ready",
            }
        ],
        "comments": [],
        "updated_at": "2026-07-30T00:00:00Z",
    }


def _ticket(number: int, *, state: str = "open", body: str | None = None) -> dict[str, Any]:
    from gwo_v8.plan_control import frozen_ticket_contract_digest

    key = f"issue:{number}"
    contract = _contract(number, body=body, state=state)
    blockers: list[dict[str, Any]] = []
    return {
        "key": key,
        "labels": ["ready-for-agent"],
        "source": {
            "ref": key,
            "digest": frozen_ticket_contract_digest(
                key=key,
                contract=contract,
                labels=["ready-for-agent"],
                native_blockers=blockers,
            ),
        },
        "contract": contract,
        "native_blockers": blockers,
    }


def _authority(policy_digest: str, grants: list[dict[str, str]]) -> dict[str, Any]:
    core = {"policy_witness_digest": policy_digest, "grants": deepcopy(grants)}
    return {**core, "subtree_digest": _digest(core)}


def active_plan_spec() -> dict[str, Any]:
    """Return an independent canonical three-ticket predecessor PlanSpec."""

    policy = _policy()
    campaign_source = _campaign_source()
    tickets = [_ticket(number) for number in (108, 109, 110)]
    work = []
    for ticket in tickets:
        work.append(
            {
                "key": ticket["key"],
                "source": deepcopy(ticket["source"]),
                "contract": deepcopy(ticket["contract"]),
                "depends_on": [],
                "exclusive_resources": [],
                "capabilities": ["git", "local_check"],
                "authority": {
                    "policy_witness_digest": policy["digest"],
                    "worker": _authority(policy["digest"], policy["authority_grants"]["worker"]),
                    "recovery_worker": _authority(
                        policy["digest"], policy["authority_grants"]["recovery_worker"]
                    ),
                    "review": _authority(policy["digest"], policy["authority_grants"]["review"]),
                },
            }
        )
    return {
        "schema_version": 3,
        "repository": "owner/repository",
        "target_branch": "main",
        "campaign": {
            "key": "campaign:successor",
            "source": campaign_source,
            "authority": _authority(policy["digest"], policy["authority_grants"]["campaign"]),
        },
        "policy": {"ref": policy["ref"], "digest": policy["digest"]},
        "work": work,
    }


def three_ticket_source_snapshot() -> dict[str, Any]:
    policy = _policy()
    tickets = [_ticket(number) for number in (108, 109, 110)]
    return {
        "repository": "owner/repository",
        "target_branch": "main",
        "campaign_source": _campaign_source(),
        "policy": deepcopy(policy),
        "policy_witness": deepcopy(policy),
        "tickets": tickets,
        "external_dependencies": [
            {
                "key": "issue:900",
                "state": "closed",
                "repository": "owner/repository",
            }
        ],
        "approved_dependency_edges": [],
    }


def three_ticket_replanning_snapshot() -> dict[str, Any]:
    source = three_ticket_source_snapshot()
    plan = active_plan_spec()
    source["active_plan_revision"] = {
        "digest": _digest(plan),
        "plan_spec": plan,
    }
    source["plan_revision_digest"] = source["active_plan_revision"]["digest"]
    source["snapshot_digest"] = _digest(source)
    return source


def changed_plan_spec(field: str) -> dict[str, Any]:
    allowed = {
        "source",
        "contract",
        "depends_on",
        "exclusive_resources",
        "capabilities",
        "authority",
        "campaign_authority",
        "policy",
        "campaign_source",
        "target_branch",
    }
    if field not in allowed:
        raise AssertionError(f"unknown semantic PlanSpec field: {field}")
    plan = active_plan_spec()
    item = next(value for value in plan["work"] if value["key"] == "issue:108")
    if field == "source":
        item["source"]["digest"] = "c" * 64
    elif field == "contract":
        item["contract"]["body"] = "Changed contract fact"
    elif field == "depends_on":
        item["depends_on"] = ["issue:110"]
    elif field == "exclusive_resources":
        item["exclusive_resources"] = ["repository.target.v1"]
    elif field == "capabilities":
        item["capabilities"] = ["git"]
    elif field == "authority":
        item["authority"]["worker"]["grants"][0]["operation_id"] = "repository.read.v1"
    elif field == "campaign_authority":
        plan["campaign"]["authority"]["grants"][0]["operation_id"] = "repository.write.v1"
    elif field == "policy":
        plan["policy"]["digest"] = "d" * 64
    elif field == "campaign_source":
        plan["campaign"]["source"]["resolved_commit_oid"] = "e" * 40
    elif field == "target_branch":
        plan["target_branch"] = "release"
    return plan


def successor_payload(
    *,
    owners: tuple[str, ...] = ("issue:110",),
    dependencies: tuple[tuple[str, str, str], ...] = (),
    resources: tuple[tuple[str, str, str], ...] = (),
) -> dict[str, Any]:
    return {
        "evidence_digests": ["9" * 64],
        "disposition": "use_approved_successor",
        "reason": "Approved Campaign work owns the discovered obligation.",
        "successor": {
            "approved_ticket_keys": list(owners),
            "dependency_additions": [
                {"from": source, "to": target, "reason": reason}
                for source, target, reason in dependencies
            ],
            "exclusive_resource_additions": [
                {"ticket_key": ticket, "resource_id": resource, "reason": reason}
                for ticket, resource, reason in resources
            ],
        },
        "decision": None,
    }


def successor_classification_value(
    *,
    owners: tuple[str, ...] = ("issue:110",),
    dependencies: tuple[tuple[str, str, str], ...] = (),
    resources: tuple[tuple[str, str, str], ...] = (),
):
    from gwo_v8.plan_control import (
        PlanInvalidationClassification,
        PlanInvalidationDependency,
        PlanInvalidationDisposition,
        PlanInvalidationExclusiveResource,
    )

    return PlanInvalidationClassification(
        action_id="replan:successor",
        snapshot_digest="1" * 64,
        plan_revision_digest="2" * 64,
        evidence_digests=("9" * 64,),
        disposition=PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR,
        reason="Approved Campaign work owns the discovered obligation.",
        capability_proof_digest="4" * 64,
        successor_ticket_keys=tuple(sorted(owners)),
        dependency_additions=tuple(
            sorted(
                (
                    PlanInvalidationDependency(source, target, reason)
                    for source, target, reason in dependencies
                ),
                key=lambda item: (item.from_ticket, item.to_ticket, item.reason),
            )
        ),
        exclusive_resource_additions=tuple(
            sorted(
                (
                    PlanInvalidationExclusiveResource(ticket, resource, reason)
                    for ticket, resource, reason in resources
                ),
                key=lambda item: (item.ticket_key, item.resource_id, item.reason),
            )
        ),
    )


def invalidation_receipt(harness: object, ticket_key: str):
    """Build one deterministic Gateway observation for a harness Work Run."""

    from gwo_v8.execution_kernel import PlanInvalidationObservation

    handle = harness.handle
    active = harness.host.read_active(handle)
    plan = active_plan_spec()
    try:
        from gwo_v8._canonical import load_canonical_json

        plan = load_canonical_json(active.plan_spec_bytes)
    except Exception:
        pass
    item = next(item for item in plan["work"] if item["key"] == ticket_key)
    authority = item["authority"]["worker"]["subtree_digest"]
    evidence = _digest(
        {
            "kind": "successor-invalidation.v1",
            "campaign": handle.campaign_key,
            "ticket_key": ticket_key,
        }
    )
    return PlanInvalidationObservation(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        plan_revision_digest=active.current_revision_digest,
        ticket_key=ticket_key,
        work_run_key=f"work-run:{ticket_key}",
        runtime_binding_id=f"binding:{ticket_key}",
        authority_subtree_digest=authority,
        reporter_role="worker",
        report_digest=evidence,
        evidence_digest=evidence,
        dedup_identity=f"successor:{ticket_key}",
        invalidated_obligation=f"Ticket {ticket_key} discovered a PlanSpec obligation.",
        required_effects=("workspace.write.v1",),
        workspace_identity=f"workspace:{ticket_key}",
    )


# These names are intentionally defined now so later integration tasks can
# extend the same fixture module without changing pytest plugin wiring.
@dataclass
class SuccessorHarness:
    handle: object
    host: object
    repository: object
    source: object
    gateway: object
    effects: object
    initial_revision_digest: str


class InjectedCrash(RuntimeError):
    pass


class ScriptedPlanningGateway:
    """Protocol counter surface used by integrated tasks.

    Task 4/7 extend this double's runtime behavior in their own test scope;
    keeping the counters here makes the fixture API stable for all modules.
    """

    def __init__(self, *args, **kwargs):
        self.planning_progresses = 0
        self.replan_progresses = 0
        self.preflights = []
        self.progresses = []


class RevisionBoundEffects:
    def __init__(self, *args, **kwargs):
        self.executed: list[Any] = []

    def replay_predecessor_candidate(self, ticket_key: str):
        raise NotImplementedError("integration fixture not installed yet")


class CrashBoundaryRepository:
    pass
