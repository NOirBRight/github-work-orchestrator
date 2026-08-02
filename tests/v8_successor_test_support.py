"""Independent, deterministic support for the approved-successor tests.

The values in the builders are deliberately assembled here rather than by
calling PlanControl's digest or contract helpers.  The support module is a
test authority: it must catch a production change that accidentally changes a
Ticket, source, policy, or execution identity.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Callable, Mapping

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _canonical_bytes(value: Any) -> bytes:
    """Encode the small JSON domain used by these fixed test facts."""

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_copy(value: Any) -> Any:
    return json.loads(_canonical_bytes(value).decode("utf-8"))


def _digest(value: Any) -> str:
    """Independent SHA-256 over the repository's canonical JSON spelling."""

    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _repository_identity() -> dict[str, str]:
    return {
        "full_name": "owner/repository",
        "url": "https://api.github.com/repos/owner/repository",
    }


def _policy() -> dict[str, Any]:
    core: dict[str, Any] = {
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
    return {**core, "digest": _digest(core)}


def _campaign_source() -> dict[str, Any]:
    value = {
        "repository": "owner/repository",
        "input_ref": "refs/heads/main",
        "resolved_commit_oid": "a" * 40,
        "tree_oid": "b" * 40,
    }
    return {**value, "digest": _digest(value)}


def _contract(
    number: int,
    *,
    body: str | None = None,
    state: str = "open",
) -> dict[str, Any]:
    return {
        "id": number,
        "node_id": f"ISSUE_{number}",
        "number": number,
        "title": f"Successor contract {number}",
        "body": body or f"Complete successor work {number}",
        "state": state,
        "state_reason": None,
        "type": None,
        "repository": _repository_identity(),
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


def _blocker(number: int, *, state: str = "closed") -> dict[str, Any]:
    key = f"issue:{number}"
    value = {
        "key": key,
        "state": state,
        "repository": _repository_identity(),
    }
    return {**value, "source": {"ref": key, "digest": _digest(value)}}


def _ticket(
    number: int,
    *,
    state: str = "open",
    body: str | None = None,
    native_blockers: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    key = f"issue:{number}"
    contract = _contract(number, body=body, state=state)
    labels = [label["name"] for label in contract["labels"]]
    blockers = sorted((deepcopy(item) for item in native_blockers), key=lambda item: item["key"])
    projection = {
        "number": number,
        "contract": contract,
        "labels": labels,
        "source_ref": key,
        "native_blockers": blockers,
    }
    return {
        "key": key,
        "labels": labels,
        "source": {"ref": key, "digest": _digest(projection)},
        "contract": contract,
        "native_blockers": blockers,
    }


def _authority(policy_digest: str, grants: list[dict[str, str]]) -> dict[str, Any]:
    core = {"policy_witness_digest": policy_digest, "grants": deepcopy(grants)}
    return {**core, "subtree_digest": _digest(core)}


def _tickets() -> list[dict[str, Any]]:
    external = _blocker(900, state="closed")
    return [
        _ticket(108),
        _ticket(109, native_blockers=(external,)),
        _ticket(110),
    ]


def active_plan_spec() -> dict[str, Any]:
    """Return the fixed, structurally valid predecessor PlanSpec v3."""

    policy = _policy()
    tickets = _tickets()
    work: list[dict[str, Any]] = []
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
                    "worker": _authority(
                        policy["digest"],
                        policy["authority_grants"]["worker"],
                    ),
                    "recovery_worker": _authority(
                        policy["digest"],
                        policy["authority_grants"]["recovery_worker"],
                    ),
                    "review": _authority(
                        policy["digest"],
                        policy["authority_grants"]["review"],
                    ),
                },
            }
        )
    return {
        "schema_version": 3,
        "repository": "owner/repository",
        "target_branch": "main",
        "campaign": {
            "key": "campaign:successor",
            "source": _campaign_source(),
            "authority": _authority(
                policy["digest"], policy["authority_grants"]["campaign"]
            ),
        },
        "policy": {"ref": policy["ref"], "digest": policy["digest"]},
        "work": work,
    }


def three_ticket_source_snapshot() -> dict[str, Any]:
    """Return the raw source facts for the canonical three-Ticket Campaign."""

    policy = _policy()
    tickets = _tickets()
    return {
        "repository": "owner/repository",
        "target_branch": "main",
        "campaign_source": _campaign_source(),
        "policy": deepcopy(policy),
        "policy_witness": deepcopy(policy),
        "tickets": tickets,
        "native_blocker_graph": [
            {"ticket_key": ticket["key"], "blockers": deepcopy(ticket["native_blockers"])}
            for ticket in tickets
        ],
        "external_dependencies": [
            {
                "key": "issue:900",
                "state": "closed",
                "repository": "owner/repository",
                "source": deepcopy(tickets[1]["native_blockers"][0]["source"]),
            }
        ],
        "approved_dependency_edges": [],
    }


def _target_facts_digest(plan: Mapping[str, Any]) -> str:
    return _digest(
        {
            "kind": "gwo.target-facts.v1",
            "repository": plan["repository"],
            "target_branch": plan["target_branch"],
            "campaign_source": plan["campaign"]["source"],
        }
    )


def _work_subject_digest(plan: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    return _digest(
        {
            "kind": "gwo.work-subject.v1",
            "repository": plan["repository"],
            "campaign_key": plan["campaign"]["key"],
            "target_branch": plan["target_branch"],
            "campaign_source": plan["campaign"]["source"],
            "campaign_authority": plan["campaign"]["authority"],
            "policy": plan["policy"],
            "ticket_key": item["key"],
            "source": item["source"],
            "contract": item["contract"],
            "depends_on": list(item["depends_on"]),
            "exclusive_resources": list(item["exclusive_resources"]),
            "capabilities": list(item["capabilities"]),
            "authority": item["authority"],
        }
    )


def _work_run_key(ticket_key: str, subject_digest: str) -> str:
    return "work-run:" + _digest(
        {
            "kind": "gwo.work-run-key.v1",
            "ticket_key": ticket_key,
            "work_subject_digest": subject_digest,
        }
    )


def _replanning_runs(plan: Mapping[str, Any], revision_digest: str) -> list[dict[str, Any]]:
    phases = {
        "issue:108": ("completed", False, None, "released", None, "7" * 64, ["8" * 64]),
        "issue:109": (
            "quiescent",
            False,
            "PlanInvalidation",
            "released",
            "candidate:r0:109",
            None,
            [],
        ),
        "issue:110": ("pending", False, None, "unclaimed", None, None, []),
    }
    result: list[dict[str, Any]] = []
    for item in plan["work"]:
        key = item["key"]
        phase, slot, reason, claim_state, candidate, result_digest, evidence = phases[key]
        subject_digest = _work_subject_digest(plan, item)
        result.append(
            {
                "ticket_key": key,
                "work_run_key": _work_run_key(key, subject_digest),
                "phase": phase,
                "slot_held": slot,
                "reason": reason,
                "next_check_at": None,
                "runtime_binding_id": f"binding:r0:{key.removeprefix('issue:')}",
                "claim_state": claim_state,
                "exclusive_resources": [],
                "work_subject_digest": subject_digest,
                "candidate_identity": candidate,
                "result_digest": result_digest,
                "evidence_digests": evidence,
            }
        )
    return result


def three_ticket_replanning_snapshot() -> dict[str, Any]:
    """Return one complete frozen replanning projection for Tasks 2-9."""

    source = three_ticket_source_snapshot()
    plan = active_plan_spec()
    revision_digest = _digest(plan)
    runs = _replanning_runs(plan, revision_digest)
    accepted = [
        {
            "kind": "accepted_result_binding.v1",
            "ticket_key": "issue:108",
            "result_digest": "7" * 64,
            "evidence_digests": ["8" * 64],
            "work_subject_digest": next(
                run["work_subject_digest"] for run in runs if run["ticket_key"] == "issue:108"
            ),
            "target_facts_digest": _target_facts_digest(plan),
        }
    ]
    snapshot: dict[str, Any] = {
        "schema_version": "gwo.plan.invalidation-snapshot.v1",
        "repository": source["repository"],
        "campaign_key": "campaign:successor",
        "target_branch": source["target_branch"],
        "campaign_source": deepcopy(source["campaign_source"]),
        "policy": deepcopy(source["policy"]),
        "policy_witness": deepcopy(source["policy_witness"]),
        "tickets": deepcopy(source["tickets"]),
        "native_blocker_graph": deepcopy(source["native_blocker_graph"]),
        "external_dependencies": deepcopy(source["external_dependencies"]),
        "active_plan_revision": {
            "digest": revision_digest,
            "plan_spec": plan,
            "expected_previous_revision_digest": None,
        },
        "plan_revision_digest": revision_digest,
        "work_runs": runs,
        "claims": [
            {
                "ticket_key": key,
                "repository": "owner/repository",
                "campaign_key": "campaign:successor",
                "plan_revision_digest": revision_digest,
            }
            for key in ("issue:108", "issue:109", "issue:110")
        ],
        "accepted_results": accepted,
        "pending_invalidations": [],
        "approved_dependency_edges": [],
    }
    snapshot["snapshot_digest"] = _digest(snapshot)
    return snapshot


def _refresh_ticket_source(item: dict[str, Any]) -> None:
    key = item["key"]
    contract = item["contract"]
    labels = [label["name"] for label in contract["labels"]]
    item["labels"] = labels
    item["source"] = {
        "ref": key,
        "digest": _digest(
            {
                "number": int(key.removeprefix("issue:")),
                "contract": contract,
                "labels": labels,
                "source_ref": key,
                "native_blockers": item.get("native_blockers", []),
            }
        ),
    }


def changed_plan_spec(field: str) -> dict[str, Any]:
    """Change exactly one allowed PlanSpec semantic fact, keeping it valid."""

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
        raise ValueError(f"unknown semantic PlanSpec field: {field}")
    plan = active_plan_spec()
    item = next(value for value in plan["work"] if value["key"] == "issue:108")
    policy = _policy()
    if field == "source":
        item["source"]["digest"] = _digest({"changed_source": "issue:108"})
    elif field == "contract":
        item["contract"]["body"] = "Changed contract fact"
        item["source"]["digest"] = _digest(
            {
                "number": 108,
                "contract": item["contract"],
                "labels": [label["name"] for label in item["contract"]["labels"]],
                "source_ref": "issue:108",
                "native_blockers": [],
            }
        )
    elif field == "depends_on":
        item["depends_on"] = ["issue:110"]
    elif field == "exclusive_resources":
        item["exclusive_resources"] = ["repository.target.v1"]
    elif field == "capabilities":
        item["capabilities"] = ["git"]
    elif field == "authority":
        grants = [{"operation_id": "workspace.write.v1", "resource_id": "work-run.workspace.v2"}]
        item["authority"]["worker"] = _authority(policy["digest"], grants)
    elif field == "campaign_authority":
        grants = [{"operation_id": "repository.read.v1", "resource_id": "campaign.snapshot.v2"}]
        plan["campaign"]["authority"] = _authority(policy["digest"], grants)
    elif field == "policy":
        changed_policy_digest = _digest({"policy": "changed"})
        plan["policy"] = {
            "ref": "policy:changed",
            "digest": changed_policy_digest,
        }
        for work_item in plan["work"]:
            for role in ("worker", "recovery_worker", "review"):
                grants = work_item["authority"][role]["grants"]
                work_item["authority"][role] = _authority(
                    changed_policy_digest,
                    grants,
                )
            work_item["authority"]["policy_witness_digest"] = changed_policy_digest
        plan["campaign"]["authority"] = _authority(
            changed_policy_digest,
            plan["campaign"]["authority"]["grants"],
        )
    elif field == "campaign_source":
        source = deepcopy(plan["campaign"]["source"])
        source["resolved_commit_oid"] = "c" * 40
        source["digest"] = _digest({key: source[key] for key in source if key != "digest"})
        plan["campaign"]["source"] = source
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


def _active_plan_value(host: object, handle: object) -> dict[str, Any]:
    from gwo_v8._canonical import load_canonical_json

    return load_canonical_json(host.read_active(handle).plan_spec_bytes)


def invalidation_receipt(harness: object, ticket_key: str):
    """Build one exact invalidation observation for the current Work Run."""

    from gwo_v8.execution_kernel import PlanInvalidationObservation

    handle = harness.handle
    active = harness.host.read_active(handle)
    plan = _active_plan_value(harness.host, handle)
    item = next(item for item in plan["work"] if item["key"] == ticket_key)
    run_key = f"work-run:{ticket_key}"
    binding = f"binding:{ticket_key}"
    kernel = getattr(harness, "_kernel", None)
    if kernel is not None and callable(getattr(kernel, "inspect", None)):
        try:
            run = next(run for run in kernel.inspect(handle).work_runs if run.ticket_key == ticket_key)
            run_key = run.work_run_key
            binding = run.runtime_binding_id or binding
        except Exception:
            pass
    # The default scripted replan output names this exact Evidence identity;
    # callers that need another Evidence digest provide their own payload.
    evidence = "9" * 64
    return PlanInvalidationObservation(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        plan_revision_digest=active.current_revision_digest,
        ticket_key=ticket_key,
        work_run_key=run_key,
        runtime_binding_id=binding,
        authority_subtree_digest=item["authority"]["worker"]["subtree_digest"],
        reporter_role="worker",
        report_digest=evidence,
        evidence_digest=evidence,
        dedup_identity=f"successor:{ticket_key}",
        invalidated_obligation=f"Ticket {ticket_key} discovered a PlanSpec obligation.",
        required_effects=("workspace.write.v1",),
        workspace_identity=f"workspace:{ticket_key}",
    )


class _ArtifactRef:
    def __init__(self, digest: str, byte_length: int):
        self.digest = digest
        self.byte_length = byte_length
        self.path = f"memory:{digest}"


class _Artifacts:
    def __init__(self):
        self.values: dict[str, Any] = {}

    def put_canonical(self, value: Any) -> _ArtifactRef:
        payload = _canonical_bytes(value)
        digest = hashlib.sha256(payload).hexdigest()
        self.values[digest] = _canonical_copy(value)
        return _ArtifactRef(digest, len(payload))

    def get(self, digest: str) -> _ArtifactRef:
        value = self.values[digest]
        return _ArtifactRef(digest, len(_canonical_bytes(value)))

    def read_json(self, digest: str) -> Any:
        return _canonical_copy(self.values[digest])


class _Source:
    def __init__(self, value: Mapping[str, Any]):
        self.value = deepcopy(dict(value))
        self.calls = 0
        self._mutate_after_snapshot: Callable[[], None] | None = None

    def snapshot(self, repository: str, ready_refs: tuple[str, ...]):
        assert repository == "owner/repository"
        assert tuple(sorted(ready_refs)) == (
            "issue:108",
            "issue:109",
            "issue:110",
        )
        self.calls += 1
        if self._mutate_after_snapshot is not None and self.calls > 1:
            mutation = self._mutate_after_snapshot
            self._mutate_after_snapshot = None
            mutation()
        return _canonical_copy(self.value)

    def mutate(self, field: str) -> None:
        if field == "contract":
            ticket = next(item for item in self.value["tickets"] if item["key"] == "issue:108")
            ticket["contract"]["body"] = "Authoritative contract changed"
            _refresh_ticket_source(ticket)
        elif field == "membership":
            self.value["tickets"] = [
                item for item in self.value["tickets"] if item["key"] != "issue:110"
            ]
        elif field == "campaign_source":
            source = deepcopy(self.value["campaign_source"])
            source["resolved_commit_oid"] = "c" * 40
            source["digest"] = _digest({key: source[key] for key in source if key != "digest"})
            self.value["campaign_source"] = source
        elif field == "target_branch":
            self.value["target_branch"] = "release"
        elif field == "policy":
            policy = deepcopy(self.value["policy"])
            policy["ref"] = "policy:changed"
            policy["digest"] = _digest({key: policy[key] for key in policy if key != "digest"})
            self.value["policy"] = policy
            if "policy_witness" in self.value:
                self.value["policy_witness"] = deepcopy(policy)
        else:
            raise ValueError(f"unknown source mutation: {field}")


class InjectedCrash(RuntimeError):
    """Named crash used only at a fixture durability seam."""

    def __init__(self, boundary: str):
        super().__init__(f"injected crash at {boundary}")
        self.boundary = boundary


class ScriptedPlanningGateway:
    """Deterministic Gateway double with separate initial/replan counters."""

    def __init__(self, artifacts: Any | None = None, payload: Mapping[str, Any] | None = None, *args, **kwargs):
        self.artifacts = artifacts or _Artifacts()
        self.payload = deepcopy(payload) if payload is not None else successor_payload(
            dependencies=(
                (
                    "issue:109",
                    "issue:110",
                    "The invalidated work consumes the existing owner's result.",
                ),
            )
        )
        self.planning_progresses = 0
        self.replan_progresses = 0
        self.preflights: list[Any] = []
        self.progresses: list[Any] = []
        self.outputs: list[str] = []

    def planning_preflight(self, subject):
        from gwo_v8.runtime_gateway import PlanningPreflightReceipt

        self.preflights.append(subject)
        prefix = "a" if subject.stable_action_id.startswith("replan:") else "5"
        return PlanningPreflightReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            receipt_digest=prefix * 64,
        )

    def _read_coordinator_capability(self, subject):
        from gwo_v8.runtime_gateway import CoordinatorCapabilityProof

        return CoordinatorCapabilityProof(
            subject_digest=subject.digest,
            repository_read_only=True,
            tracker_read_only=True,
            can_activate_plan_revision=False,
            can_edit_tracker=False,
            can_expand_authority=False,
            delegation_enabled=False,
        )

    def progress(self, subject, preflight):
        from gwo_v8.runtime_gateway import PlanningReceipt

        self.progresses.append(subject)
        successor = subject.stable_action_id.startswith("replan:")
        if successor:
            self.replan_progresses += 1
            payload = deepcopy(self.payload)
            receipt_prefix = "b"
        else:
            self.planning_progresses += 1
            payload = {
                "admitted_work": ["issue:108", "issue:109", "issue:110"],
                "dependency_additions": [],
                "exclusive_resources": {
                    "issue:108": [],
                    "issue:109": [],
                    "issue:110": [],
                },
                "capability_requirements": {
                    key: ["git", "local_check"]
                    for key in ("issue:108", "issue:109", "issue:110")
                },
                "decision_requirements": [],
            }
            receipt_prefix = "6"
        output = self.artifacts.put_canonical(
            {
                "schema_version": "gwo.runtime.output.v1",
                "subject_digest": subject.digest,
                "stable_action_id": subject.stable_action_id,
                "authority_digest": subject.authority_digest,
                "payload": payload,
            }
        )
        self.outputs.append(output.digest)
        return PlanningReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            status="completed",
            receipt_digest=receipt_prefix * 64,
            output_artifact_digest=output.digest,
            planning_output_artifact_digest=output.digest,
        )


class CrashBoundaryRepository:
    """Delegating repository double with named post-write crash seams."""

    def __init__(self, delegate: object | None = None):
        self._delegate = delegate
        self._armed_boundary: str | None = None
        self._crashed = False
        self._competing_winner: Any | None = None

    def __getattr__(self, name: str):
        if self._delegate is None:
            raise AttributeError(name)
        return getattr(self._delegate, name)

    def arm_crash(self, boundary: str) -> None:
        if boundary not in {"attempt_save", "revision_publish", "activation_ack", "kernel_migration"}:
            raise ValueError(f"unknown crash boundary: {boundary}")
        self._armed_boundary = boundary
        self._crashed = False

    def _crash_after(self, boundary: str) -> None:
        if self._armed_boundary == boundary and not self._crashed:
            self._crashed = True
            raise InjectedCrash(boundary)

    def save_attempt(self, attempt):
        if self._delegate is None:
            self._crash_after("attempt_save")
            return attempt
        result = self._delegate.save_attempt(attempt)
        if getattr(attempt, "planning_protocol_id", None) == "campaign.plan-invalidation-output.v1":
            self._crash_after("attempt_save")
        return result

    def publish_revision(self, revision):
        if self._delegate is None:
            self._crash_after("revision_publish")
            return None
        result = self._delegate.publish_revision(revision)
        self._crash_after("revision_publish")
        return result

    def activate(self, receipt):
        if self._delegate is None:
            self._crash_after("activation_ack")
            return None
        if self._competing_winner is not None:
            winner = self._competing_winner
            self._competing_winner = None
            self._install_winner(winner)
        result = self._delegate.activate(receipt)
        self._crash_after("activation_ack")
        return result

    def _install_winner(self, winner: Any) -> None:
        if self._delegate is None:
            return
        revision = getattr(winner, "_revision", None)
        if revision is not None and callable(getattr(self._delegate, "publish_revision", None)):
            self._delegate.publish_revision(revision)
        planning = getattr(winner, "_planning_reservation", None)
        if planning is not None and callable(getattr(self._delegate, "reserve_planning", None)):
            self._delegate.reserve_planning(planning)
        if callable(getattr(self._delegate, "reserve_claims", None)):
            self._delegate.reserve_claims(winner)
        self._delegate.activate(winner)
        if callable(getattr(self._delegate, "finalize_claims", None)):
            self._delegate.finalize_claims(winner)

    def install_competing_successor(self, handle: object | None = None):
        from gwo_v8._canonical import canonical_bytes, digest_bytes, load_canonical_json
        from gwo_v8.plan_control import ActivationReceipt, PlanRevision, PlanningReservation

        if self._delegate is None or handle is None:
            return None
        current = self._delegate.read_activation(handle)
        if current is None:
            return None
        current_revision = self._delegate.read_revision(current.revision_digest)
        if current_revision is None:
            return None
        plan = load_canonical_json(current_revision.canonical_bytes)
        plan["target_branch"] = "competing"
        payload = canonical_bytes(plan)
        revision = PlanRevision(
            repository=handle.repository,
            campaign_key=handle.campaign_key,
            snapshot_digest=current_revision.snapshot_digest,
            canonical_bytes=payload,
            digest=digest_bytes(payload),
        )
        winner = replace(
            current,
            revision_digest=revision.digest,
            expected_previous_revision_digest=current.revision_digest,
            planning_stable_action_id="replan:competitor",
        )
        object.__setattr__(winner, "_revision", revision)
        object.__setattr__(
            winner,
            "_planning_reservation",
            PlanningReservation(
                repository=handle.repository,
                campaign_key=handle.campaign_key,
                ticket_keys=current.ticket_keys,
                subject_digest=current.planning_subject_digest,
                stable_action_id="replan:competitor",
                preflight_receipt_digest=current.planning_preflight_receipt_digest,
                snapshot_artifact_digest=current_revision.snapshot_digest,
                policy_witness_digest="2" * 64,
                planning_request_artifact_digest=current.compilation_record_artifact_digest,
            ),
        )
        self._competing_winner = winner
        return winner


class RevisionBoundEffects:
    """Readback-first Work Run effects with revision-bound stale output."""

    def __init__(
        self,
        *,
        initial_revision_digest: str | None = None,
        complete_tickets: tuple[str, ...] = ("issue:108",),
        phase: str | None = None,
        result_digest: str | None = None,
        evidence_digests: tuple[str, ...] = (),
        **kwargs,
    ):
        self.initial_revision_digest = initial_revision_digest
        self.complete_tickets = set(complete_tickets)
        self.phase = phase
        self.result_digest = result_digest
        self.evidence_digests = tuple(evidence_digests)
        self.executed: list[Any] = []
        self._readbacks: dict[str, Any] = {}
        self._replay_ticket: str | None = None
        self._crash_boundary: str | None = None

    def arm_crash(self, boundary: str) -> None:
        if boundary not in {"attempt_save", "revision_publish", "activation_ack", "kernel_migration"}:
            raise ValueError(f"unknown crash boundary: {boundary}")
        self._crash_boundary = boundary

    def readback(self, action):
        return self._readbacks.get(action.stable_action_id)

    def _make_observation(self, action, phase: str, *, candidate: str | None = None):
        from gwo_v8.execution_kernel import WorkRunObservation

        receipt = _digest(
            {
                "kind": "successor-effect.v1",
                "action": action.stable_action_id,
                "phase": phase,
            }
        )
        values: dict[str, Any] = {
            "phase": phase,
            "stable_action_id": action.stable_action_id,
            "receipt_digest": receipt,
        }
        fields = getattr(WorkRunObservation, "__dataclass_fields__", {})
        if "candidate_identity" in fields:
            values["candidate_identity"] = candidate
        if "result_digest" in fields:
            values["result_digest"] = self.result_digest if phase == "completed" else None
        if "evidence_digests" in fields:
            values["evidence_digests"] = (
                self.evidence_digests if phase == "completed" else ()
            )
        return WorkRunObservation(**values)

    def execute(self, action):
        if (
            self._crash_boundary == "kernel_migration"
            and self.initial_revision_digest is not None
            and action.plan_revision_digest != self.initial_revision_digest
        ):
            self._crash_boundary = None
            raise InjectedCrash("kernel_migration")
        self.executed.append(action)
        ticket = action.ticket_key
        if self._replay_ticket == ticket:
            old = next(
                item for item in reversed(self.executed[:-1]) if item.ticket_key == ticket
            )
            observation = self._make_observation(
                old,
                "candidate_checks",
                candidate=f"candidate:r0:{ticket.removeprefix('issue:')}",
            )
            return replace(observation, stable_action_id=old.stable_action_id)
        if self.phase is not None:
            phase = self.phase
        elif ticket in self.complete_tickets or (
            ticket == "issue:110"
            and self.initial_revision_digest is not None
            and action.plan_revision_digest != self.initial_revision_digest
        ):
            phase = "completed"
        elif ticket == "issue:109" and (
            self.initial_revision_digest is None
            or action.plan_revision_digest == self.initial_revision_digest
        ):
            phase = "candidate_checks"
        else:
            phase = "running"
        candidate = (
            f"candidate:r0:{ticket.removeprefix('issue:')}"
            if ticket == "issue:109" and phase == "candidate_checks"
            else None
        )
        observation = self._make_observation(action, phase, candidate=candidate)
        self._readbacks[action.stable_action_id] = observation
        return observation

    def replay_predecessor_candidate(self, ticket_key: str):
        if not any(action.ticket_key == ticket_key for action in self.executed):
            raise AssertionError(f"no predecessor effect exists for {ticket_key}")
        self._replay_ticket = ticket_key
        return next(
            action for action in reversed(self.executed) if action.ticket_key == ticket_key
        ).stable_action_id


class _TestHost:
    def __init__(self, control: object):
        self.control = control
        self._tamper_field: str | None = None

    def read_active(self, handle):
        return self.control.read_active(handle)

    def start(self, repository, ready_refs, options=None):
        return self.control.start(repository, ready_refs, options, campaign_key="campaign:successor")

    def start_successor(self, handle, ready_refs, *, expected_previous_revision_digest):
        return self.control.start(
            handle.repository,
            ready_refs,
            campaign_key=handle.campaign_key,
            expected_previous_revision_digest=expected_previous_revision_digest,
        )

    def classify_plan_invalidations(self, handle, invalidations, execution_snapshot):
        return self.control.classify_plan_invalidations(handle, invalidations, execution_snapshot)

    def activate_successor(self, handle, classification):
        result = self.control.activate_successor(handle, classification)
        field = self._tamper_field
        self._tamper_field = None
        if field is None:
            return result
        from gwo_v8._canonical import canonical_bytes

        if field == "campaign":
            return replace(result, handle=replace(result.handle, campaign_key="campaign:tampered"))
        if field == "previous_revision":
            receipt = replace(result.activation_receipt, expected_previous_revision_digest="f" * 64)
            return replace(result, activation_receipt=receipt)
        if field == "revision_digest":
            return replace(result, current_revision_digest="e" * 64)
        if field == "plan_spec":
            return replace(result, plan_spec_bytes=canonical_bytes({"tampered": True}))
        if field == "claims":
            return replace(result, claim_proofs=())
        raise AssertionError(field)

    def arm_activation_readback_tamper(self, field: str) -> None:
        if field not in {"campaign", "previous_revision", "revision_digest", "plan_spec", "claims"}:
            raise ValueError(f"unknown activation readback field: {field}")
        self._tamper_field = field

    def install_execution_kernel(self, *, store_path: Path, effects: object, configuration=None):
        from gwo_v8.execution_kernel import ExecutionKernel

        return ExecutionKernel(
            store_path=store_path,
            plan_control=self,
            effects=effects,
            configuration=configuration,
        )


@dataclass
class SuccessorHarness:
    handle: object
    host: object
    repository: object
    source: object
    gateway: object
    effects: object
    initial_revision_digest: str
    _kernel: object | None = field(default=None, repr=False, compare=False)
    _reinstaller: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def invalidation_for(self, ticket_key: str):
        return invalidation_receipt(self, ticket_key)

    def active_plan(self) -> dict[str, object]:
        return _active_plan_value(self.host, self.handle)

    def ledger_snapshot(self) -> dict[str, object]:
        revisions = getattr(self.repository, "revisions", {})
        if isinstance(revisions, Mapping):
            revisions = tuple(sorted(revisions))
        else:
            revisions = tuple(sorted(revisions))
        read_claims = getattr(self.repository, "read_campaign_claim_proofs", None)
        claims = (
            tuple(read_claims(self.handle))
            if callable(read_claims)
            else ()
        )
        read_activation = getattr(self.repository, "read_activation", None)
        return {
            "activation": read_activation(self.handle) if callable(read_activation) else None,
            "claims": claims,
            "revisions": revisions,
            "effects": tuple(getattr(self.effects, "executed", ())),
        }

    def set_successor_payload(self, payload: Mapping[str, Any]) -> None:
        self.gateway.payload = deepcopy(dict(payload))

    def arm_crash(self, boundary: str) -> None:
        if boundary not in {"attempt_save", "revision_publish", "activation_ack", "kernel_migration"}:
            raise ValueError(f"unknown crash boundary: {boundary}")
        arm = getattr(self.repository, "arm_crash", None)
        if callable(arm):
            arm(boundary)
        arm = getattr(self.effects, "arm_crash", None)
        if callable(arm):
            arm(boundary)

    def arm_activation_readback_tamper(self, field: str) -> None:
        arm = getattr(self.host, "arm_activation_readback_tamper", None)
        if not callable(arm):
            raise AttributeError("host does not expose activation readback tampering")
        arm(field)

    def mutate_source(self, field: str) -> None:
        if field not in {"contract", "membership", "campaign_source", "target_branch", "policy"}:
            raise ValueError(f"unknown source mutation: {field}")
        mutate = getattr(self.source, "mutate", None)
        if callable(mutate):
            mutate(field)
            return
        raise AttributeError("source does not expose deterministic mutation")

    def install_competing_successor(self):
        install = getattr(self.repository, "install_competing_successor", None)
        if not callable(install):
            raise AttributeError("repository does not expose a competing-successor seam")
        return install(self.handle)

    def reinstall(self) -> None:
        if self._reinstaller is not None:
            self._reinstaller()
            return
        reinstall = getattr(self.host, "reinstall", None)
        if callable(reinstall):
            reinstall()


def _source_projection() -> dict[str, Any]:
    source = three_ticket_source_snapshot()
    return {
        key: deepcopy(source[key])
        for key in ("repository", "target_branch", "campaign_source", "policy", "tickets")
    }


def _execution_snapshot(active: object, *, invalidated_ticket: str = "issue:109") -> dict[str, Any]:
    claims = [
        {
            "ticket_key": proof.ticket_key,
            "repository": proof.repository,
            "campaign_key": proof.campaign_key,
            "plan_revision_digest": proof.plan_revision_digest,
        }
        for proof in active.claim_proofs
    ]
    return {
        "runs": [
            {
                "ticket_key": key,
                "work_run_key": f"work-run:{key}",
                "phase": "quiescent" if key == invalidated_ticket else "pending",
                "slot_held": False,
                "reason": "PlanInvalidation" if key == invalidated_ticket else None,
                "next_check_at": None,
                "runtime_binding_id": f"binding:{key}",
                "claim_state": "released",
                "exclusive_resources": [],
            }
            for key in ("issue:108", "issue:109", "issue:110")
        ],
        "claims": claims,
        "accepted_results": [],
    }


def _direct_setup(payload: Mapping[str, Any] | None = None):
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl

    artifacts = _Artifacts()
    base = InMemoryPlanRepository(writer_generation="writer:successor")
    repository = CrashBoundaryRepository(base)
    source = _Source(_source_projection())
    gateway = ScriptedPlanningGateway(artifacts, payload)
    control = PlanControl(
        source=source,
        artifacts=artifacts,
        gateway=gateway,
        repository=repository,
    )
    handle = control.start(
        "owner/repository",
        ["issue:108", "issue:109", "issue:110"],
        campaign_key="campaign:successor",
    )
    host = _TestHost(control)
    active = host.read_active(handle)
    harness = SuccessorHarness(
        handle=handle,
        host=host,
        repository=repository,
        source=source,
        gateway=gateway,
        effects=RevisionBoundEffects(initial_revision_digest=active.current_revision_digest),
        initial_revision_digest=active.current_revision_digest,
    )
    return control, repository, gateway, artifacts, source, host, handle, harness


def _classified_setup(payload: Mapping[str, Any] | None = None):
    control, repository, gateway, artifacts, source, host, handle, harness = _direct_setup(payload)
    active = host.read_active(handle)
    classification = control.classify_plan_invalidations(
        handle,
        (harness.invalidation_for("issue:109"),),
        _execution_snapshot(active),
    )
    return control, repository, gateway, artifacts, source, host, handle, harness, classification


class _StaticPlanReader:
    def __init__(self, active):
        self.active = active

    def read_active(self, handle):
        if handle != self.active.handle:
            raise AssertionError(handle)
        return self.active


def _minimal_active_campaign(ticket_keys: tuple[str, ...]):
    from gwo_v8._canonical import canonical_bytes, digest_bytes, digest_value
    from gwo_v8.plan_control import (
        ActivationReceipt,
        ActivePlanReadback,
        CampaignHandle,
        TicketClaimProof,
    )

    handle = CampaignHandle("owner/repository", "campaign:successor-kernel")
    full = active_plan_spec()
    full["work"] = [item for item in full["work"] if item["key"] in ticket_keys]
    payload = canonical_bytes(full)
    revision = digest_bytes(payload)
    receipt = ActivationReceipt(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        revision_digest=revision,
        expected_previous_revision_digest=None,
        writer_generation="writer:one",
        ready_refs=ticket_keys,
        ticket_keys=ticket_keys,
        planning_subject_digest="d" * 64,
        planning_stable_action_id="planning:one",
        planning_preflight_receipt_digest="e" * 64,
        compilation_record_artifact_digest="f" * 64,
        planning_receipt_digest="0" * 64,
        planning_output_artifact_digest="1" * 64,
    )
    return (
        ActivePlanReadback(
            handle=handle,
            current_revision_digest=revision,
            plan_spec_bytes=payload,
            activation_receipt=receipt,
            claim_proofs=tuple(
                TicketClaimProof(
                    ticket_key=key,
                    repository=handle.repository,
                    campaign_key=handle.campaign_key,
                    plan_revision_digest=revision,
                )
                for key in ticket_keys
            ),
        ),
        handle,
    )


@pytest.fixture
def successor_control():
    control, repository, gateway, _artifacts, _source, _host, handle, _harness, classification = _classified_setup()
    return control, repository, gateway, handle, classification


@pytest.fixture
def github_successor_repository():
    _control, repository, _gateway, _artifacts, _source, _host, handle, _harness, classification = _classified_setup()
    attempt = repository.read_attempt(handle, classification.plan_revision_digest)
    return repository, handle, classification, attempt


@pytest.fixture
def kernel_with_one_ticket(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    active, handle = _minimal_active_campaign(("issue:109",))
    effects = RevisionBoundEffects(complete_tickets=())
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_StaticPlanReader(active),
        effects=effects,
    )
    return kernel, effects, handle


@pytest.fixture
def kernel_with_completed_result(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    active, handle = _minimal_active_campaign(("issue:108",))
    effects = RevisionBoundEffects(
        complete_tickets=("issue:108",),
        result_digest="7" * 64,
        evidence_digests=("8" * 64,),
    )
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution-completed.sqlite3",
        plan_control=_StaticPlanReader(active),
        effects=effects,
    )
    kernel.advance(handle)
    return kernel, handle


@pytest.fixture
def successor_kernel(tmp_path):
    control, repository, gateway, _artifacts, source, host, handle, harness = _direct_setup()
    active = host.read_active(handle)
    effects = RevisionBoundEffects(initial_revision_digest=active.current_revision_digest)
    kernel = host.install_execution_kernel(
        store_path=tmp_path / "successor.sqlite3",
        effects=effects,
    )
    kernel.advance(handle)
    harness._kernel = kernel
    harness.effects = effects
    return kernel, control, effects, handle


@pytest.fixture
def successor_host():
    _control, _repository, gateway, _artifacts, _source, host, handle, harness, classification = _classified_setup()
    return host, gateway, handle, classification


def _public_harness(tmp_path, *, dependency: bool = True) -> SuccessorHarness:
    _control, repository, gateway, _artifacts, source, host, handle, harness = _direct_setup()
    from gwo_v8.execution_kernel import ExecutionKernel

    active = host.read_active(handle)
    effects = RevisionBoundEffects(initial_revision_digest=active.current_revision_digest)
    kernel = host.install_execution_kernel(
        store_path=tmp_path / ("public-dependency.sqlite3" if dependency else "public.sqlite3"),
        effects=effects,
    )
    kernel.advance(handle)
    harness.effects = effects
    harness._kernel = kernel
    harness._reinstaller = lambda: setattr(
        harness,
        "_kernel",
        host.install_execution_kernel(
            store_path=tmp_path / ("public-dependency.sqlite3" if dependency else "public.sqlite3"),
            effects=effects,
        ),
    )
    return harness


@pytest.fixture
def public_successor(tmp_path):
    return _public_harness(tmp_path, dependency=True)


@pytest.fixture
def public_dependency_successor(tmp_path):
    return _public_harness(tmp_path, dependency=True)
