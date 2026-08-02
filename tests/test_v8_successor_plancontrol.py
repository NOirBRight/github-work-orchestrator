from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _policy() -> dict[str, object]:
    from gwo_v8._canonical import digest_value

    core = {
        "schema_version": 1,
        "ref": "policy:successor",
        "authority_grants": {
            "campaign": [{"operation_id": "repository.read.v1", "resource_id": "campaign.snapshot.v1"}],
            "worker": [{"operation_id": "workspace.write.v1", "resource_id": "work-run.workspace.v1"}],
            "recovery_worker": [{"operation_id": "workspace.write.v1", "resource_id": "work-run.workspace.v1"}],
            "review": [{"operation_id": "repository.read.v1", "resource_id": "review.subject.v1"}],
        },
        "allowed_capabilities": ["git", "local_check"],
        "exclusive_resources": ["repository.target.v1"],
    }
    return {**core, "digest": digest_value(core)}


def _ticket(number: int) -> dict[str, object]:
    from gwo_v8.plan_control import frozen_ticket_contract_digest

    key = f"issue:{number}"
    labels = ["ready-for-agent"]
    contract = {
        "id": number,
        "node_id": f"ISSUE_{number}",
        "number": number,
        "title": f"Ticket {number}",
        "body": f"Frozen contract {number}",
        "state": "open",
        "state_reason": None,
        "type": None,
        "repository": {
            "full_name": "owner/repository",
            "url": "https://api.github.com/repos/owner/repository",
        },
        "labels": [{
            "id": 1,
            "node_id": "LABEL_READY",
            "url": "https://api.github.com/repos/owner/repository/labels/ready-for-agent",
            "name": "ready-for-agent",
            "color": "0052cc",
            "default": False,
            "description": "ready",
        }],
        "comments": [],
        "updated_at": "2026-08-02T00:00:00Z",
    }
    blockers: list[dict[str, object]] = []
    return {
        "key": key,
        "labels": labels,
        "source": {
            "ref": key,
            "digest": frozen_ticket_contract_digest(
                key=key,
                contract=contract,
                labels=labels,
                native_blockers=blockers,
            ),
        },
        "contract": contract,
        "native_blockers": blockers,
    }


def _source_snapshot() -> dict[str, object]:
    from gwo_v8._canonical import digest_value

    campaign_source = {
        "repository": "owner/repository",
        "input_ref": "refs/heads/main",
        "resolved_commit_oid": "a" * 40,
        "tree_oid": "b" * 40,
    }
    return {
        "repository": "owner/repository",
        "target_branch": "main",
        "campaign_source": {**campaign_source, "digest": digest_value(campaign_source)},
        "policy": _policy(),
        "tickets": [_ticket(number) for number in (108, 109, 110)],
    }


class _Artifacts:
    def __init__(self):
        self.values: dict[str, object] = {}

    def put_canonical(self, value):
        from gwo_v8._canonical import canonical_bytes, digest_bytes, load_canonical_json

        payload = canonical_bytes(value)
        digest = digest_bytes(payload)
        self.values[digest] = load_canonical_json(payload)
        return type("ArtifactRef", (), {"digest": digest})()

    def get(self, digest):
        if digest not in self.values:
            raise KeyError(digest)
        return type("ArtifactRef", (), {"digest": digest})()

    def read_json(self, digest):
        from gwo_v8._canonical import canonical_bytes, load_canonical_json

        return load_canonical_json(canonical_bytes(self.values[digest]))


class _Source:
    def __init__(self, value):
        self.value = deepcopy(value)

    def snapshot(self, repository, ready_refs):
        from gwo_v8._canonical import canonical_bytes, load_canonical_json

        assert repository == "owner/repository"
        assert tuple(sorted(ready_refs)) == ("issue:108", "issue:109", "issue:110")
        return load_canonical_json(canonical_bytes(self.value))


class _Gateway:
    def __init__(self, artifacts, payload=None):
        self.artifacts = artifacts
        self.payload = payload
        self.planning_progresses = 0
        self.replan_progresses = 0
        self.preflights = []

    def planning_preflight(self, subject):
        from gwo_v8.runtime_gateway import PlanningPreflightReceipt

        self.preflights.append(subject)
        return PlanningPreflightReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            receipt_digest=("a" if subject.stable_action_id.startswith("replan:") else "5") * 64,
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

        if subject.stable_action_id.startswith("replan:"):
            self.replan_progresses += 1
            payload = self.payload
        else:
            self.planning_progresses += 1
            payload = _initial_intent()
        output = self.artifacts.put_canonical({
            "schema_version": "gwo.runtime.output.v1",
            "subject_digest": subject.digest,
            "stable_action_id": subject.stable_action_id,
            "authority_digest": subject.authority_digest,
            "payload": payload,
        })
        return PlanningReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            status="completed",
            receipt_digest=("b" if subject.stable_action_id.startswith("replan:") else "6") * 64,
            output_artifact_digest=output.digest,
            planning_output_artifact_digest=output.digest,
        )


def _initial_intent() -> dict[str, object]:
    return {
        "admitted_work": ["issue:108", "issue:109", "issue:110"],
        "dependency_additions": [],
        "exclusive_resources": {
            "issue:108": [],
            "issue:109": [],
            "issue:110": [],
        },
        "capability_requirements": {
            "issue:108": ["git", "local_check"],
            "issue:109": ["git", "local_check"],
            "issue:110": ["git", "local_check"],
        },
        "decision_requirements": [],
    }


def _initial_control():
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl

    artifacts = _Artifacts()
    repository = InMemoryPlanRepository(writer_generation="writer:successor")
    gateway = _Gateway(artifacts)
    source = _Source(_source_snapshot())
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
    return control, repository, gateway, artifacts, source, handle


def _successor_payload(evidence_digest: str = "9" * 64) -> dict[str, object]:
    return {
        "evidence_digests": [evidence_digest],
        "disposition": "use_approved_successor",
        "reason": "The approved owner needs one successor dependency.",
        "successor": {
            "approved_ticket_keys": ["issue:109"],
            "dependency_additions": [{
                "from": "issue:109",
                "to": "issue:110",
                "reason": "The invalidated work consumes the existing owner's result.",
            }],
            "exclusive_resource_additions": [],
        },
        "decision": None,
    }


def _execution_snapshot(active):
    return {
        "runs": [
            {
                "ticket_key": ticket_key,
                "work_run_key": f"work-run:{ticket_key}",
                "phase": "quiescent",
                "slot_held": False,
                "reason": "PlanInvalidation",
                "next_check_at": None,
                "runtime_binding_id": f"binding:{ticket_key}",
                "claim_state": "released",
                "exclusive_resources": [],
            }
            for ticket_key in ("issue:108", "issue:109", "issue:110")
        ],
        "claims": [
            {
                "ticket_key": proof.ticket_key,
                "repository": proof.repository,
                "campaign_key": proof.campaign_key,
                "plan_revision_digest": proof.plan_revision_digest,
            }
            for proof in active.claim_proofs
        ],
        "accepted_results": [],
    }


def _invalidation(control, handle, evidence_digest: str = "9" * 64):
    from gwo_v8._canonical import load_canonical_json
    from gwo_v8.execution_kernel import PlanInvalidationObservation

    active = control.read_active(handle)
    plan = load_canonical_json(active.plan_spec_bytes)
    authority = next(
        item["authority"]["worker"]["subtree_digest"]
        for item in plan["work"]
        if item["key"] == "issue:109"
    )
    return PlanInvalidationObservation(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        plan_revision_digest=active.current_revision_digest,
        ticket_key="issue:109",
        work_run_key="work-run:issue:109",
        runtime_binding_id="binding:issue:109",
        authority_subtree_digest=authority,
        reporter_role="worker",
        report_digest=evidence_digest,
        evidence_digest=evidence_digest,
        dedup_identity="successor:one",
        invalidated_obligation="The owner result is required by the invalidated work.",
        required_effects=("workspace.write.v1",),
        workspace_identity="workspace:issue:109",
    )


def _classified_control(payload=None):
    control, repository, gateway, artifacts, source, handle = _initial_control()
    gateway.payload = _successor_payload() if payload is None else payload
    active = control.read_active(handle)
    classification = control.classify_plan_invalidations(
        handle,
        (_invalidation(control, handle),),
        _execution_snapshot(active),
    )
    assert classification is not None
    return control, repository, gateway, artifacts, source, handle, classification


def _replanning_snapshot(control, repository, handle):
    from gwo_v8._canonical import load_canonical_json

    active = control.read_active(handle)
    predecessor_attempt = repository.read_attempt(handle, None)
    source = load_canonical_json(predecessor_attempt.snapshot_bytes)
    plan = load_canonical_json(active.plan_spec_bytes)
    return {
        "schema_version": "gwo.plan.invalidation-snapshot.v1",
        "repository": handle.repository,
        "campaign_key": handle.campaign_key,
        "target_branch": source["target_branch"],
        "campaign_source": source["campaign_source"],
        "active_plan_revision": {
            "digest": active.current_revision_digest,
            "plan_spec": plan,
            "expected_previous_revision_digest": active.activation_receipt.expected_previous_revision_digest,
        },
        "tickets": source["tickets"],
        "native_blocker_graph": [
            {"ticket_key": ticket["key"], "blockers": ticket["native_blockers"]}
            for ticket in source["tickets"]
        ],
        "external_dependencies": [],
        "work_runs": [],
        "claims": [
            {
                "ticket_key": proof.ticket_key,
                "repository": proof.repository,
                "campaign_key": proof.campaign_key,
                "plan_revision_digest": proof.plan_revision_digest,
            }
            for proof in active.claim_proofs
        ],
        "accepted_results": [],
        "pending_invalidations": [],
        "approved_dependency_edges": [],
        "policy_witness": source["policy"],
    }


def _successor_classification(*, snapshot_digest, plan_revision_digest, action_id="replan:seed"):
    from gwo_v8.plan_control import (
        PlanInvalidationClassification,
        PlanInvalidationDependency,
    )

    return PlanInvalidationClassification(
        action_id=action_id,
        snapshot_digest=snapshot_digest,
        plan_revision_digest=plan_revision_digest,
        evidence_digests=("9" * 64,),
        disposition=__import__(
            "gwo_v8.plan_control", fromlist=["PlanInvalidationDisposition"]
        ).PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR,
        reason="The approved owner needs one successor dependency.",
        capability_proof_digest="0" * 64,
        successor_ticket_keys=("issue:109",),
        dependency_additions=(
            PlanInvalidationDependency(
                from_ticket="issue:109",
                to_ticket="issue:110",
                reason="The invalidated work consumes the existing owner's result.",
            ),
        ),
    )


def _seed_completed_successor(control, repository, gateway, artifacts, handle):
    from gwo_v8._canonical import canonical_bytes, digest_bytes, digest_value, load_canonical_json
    from gwo_v8.plan_control import (
        _PlanningAttempt,
        PlanningReservation,
    )
    from gwo_v8.planning_protocol import replanning_prompt, REPLANNING_OUTPUT_PROTOCOL_ID
    from gwo_v8.runtime_gateway import (
        CampaignPlanningSubject,
        CoordinatorCapabilityProof,
        PlanningPreflightReceipt,
        PlanningReceipt,
    )

    active = control.read_active(handle)
    snapshot = _replanning_snapshot(control, repository, handle)
    snapshot_bytes = canonical_bytes(snapshot)
    snapshot_digest = artifacts.put_canonical(snapshot).digest
    policy_digest = snapshot["policy_witness"]["digest"]
    provisional = CampaignPlanningSubject(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        campaign_handle="campaign-handle:" + digest_value(handle.__dict__),
        expected_previous_plan_revision_digest=active.current_revision_digest,
        snapshot_artifact_digest=snapshot_digest,
        policy_witness_digest=policy_digest,
        planning_request_artifact_digest="0" * 64,
        stable_action_id="replan:seed",
    )
    request_digest = artifacts.put_canonical(
        replanning_prompt(
            subject_digest=provisional.prompt_binding_digest,
            authority_digest=policy_digest,
            snapshot_artifact_digest=snapshot_digest,
            policy_witness_artifact_digest=policy_digest,
        )
    ).digest
    subject = replace(provisional, planning_request_artifact_digest=request_digest)
    proof = CoordinatorCapabilityProof(
        subject_digest=subject.digest,
        repository_read_only=True,
        tracker_read_only=True,
        can_activate_plan_revision=False,
        can_edit_tracker=False,
        can_expand_authority=False,
        delegation_enabled=False,
    )
    classification = replace(
        _successor_classification(
            snapshot_digest=snapshot_digest,
            plan_revision_digest=active.current_revision_digest,
        ),
        capability_proof_digest=proof.digest,
    )
    output = artifacts.put_canonical({
        "schema_version": "gwo.runtime.output.v1",
        "subject_digest": subject.digest,
        "stable_action_id": subject.stable_action_id,
        "authority_digest": subject.authority_digest,
        "payload": _successor_payload(),
    })
    preflight = PlanningPreflightReceipt(
        subject_digest=subject.digest,
        stable_action_id=subject.stable_action_id,
        receipt_digest="a" * 64,
    )
    planning = PlanningReceipt(
        subject_digest=subject.digest,
        stable_action_id=subject.stable_action_id,
        status="completed",
        receipt_digest="b" * 64,
        output_artifact_digest=output.digest,
        planning_output_artifact_digest=output.digest,
    )
    from gwo_v8.successor_plan import derive_successor_plan_intent

    normalized = derive_successor_plan_intent(snapshot, classification.canonical())
    record = {
        "schema_version": "gwo.plan.successor-compilation.v1",
        "subject": subject.canonical(),
        "subject_digest": subject.digest,
        "snapshot_artifact_digest": snapshot_digest,
        "policy_witness_digest": policy_digest,
        "planning_request_artifact_digest": request_digest,
        "stable_action_id": subject.stable_action_id,
        "preflight_receipt": {
            "subject_digest": preflight.subject_digest,
            "stable_action_id": preflight.stable_action_id,
            "receipt_digest": preflight.receipt_digest,
        },
        "planning_receipt": {
            "subject_digest": planning.subject_digest,
            "stable_action_id": planning.stable_action_id,
            "status": planning.status,
            "receipt_digest": planning.receipt_digest,
            "command": None,
            "wake_cursor": planning.wake_cursor,
            "wake_hints": list(planning.wake_hints),
            "output_artifact_digest": planning.output_artifact_digest,
            "planning_output_artifact_digest": planning.planning_output_artifact_digest,
        },
        "output_artifact_digest": output.digest,
        "planning_output": artifacts.read_json(output.digest),
        "coordinator_capability_proof": proof.canonical(),
        "coordinator_capability_proof_digest": proof.digest,
        "classification": classification.canonical(),
        "classification_digest": classification.digest,
        "normalized_intent": normalized,
        "normalized_intent_digest": digest_value(normalized),
    }
    record_bytes = canonical_bytes(record)
    record_digest = artifacts.put_canonical(record).digest
    attempt = _PlanningAttempt(
        handle=handle,
        ready_refs=active.activation_receipt.ready_refs,
        ticket_keys=active.activation_receipt.ticket_keys,
        expected_previous_revision_digest=active.current_revision_digest,
        snapshot_bytes=snapshot_bytes,
        snapshot_artifact_digest=snapshot_digest,
        policy_witness_digest=policy_digest,
        planning_request_artifact_digest=request_digest,
        subject=subject,
        planning_protocol_id=REPLANNING_OUTPUT_PROTOCOL_ID,
        compilation_record_artifact_digest=record_digest,
        compilation_record_bytes=record_bytes,
    )
    repository.save_attempt(attempt)
    repository.reserve_planning(
        PlanningReservation(
            repository=handle.repository,
            campaign_key=handle.campaign_key,
            ticket_keys=attempt.ticket_keys,
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            preflight_receipt_digest=preflight.receipt_digest,
            snapshot_artifact_digest=snapshot_digest,
            policy_witness_digest=policy_digest,
            planning_request_artifact_digest=request_digest,
        )
    )
    repository.save_invalidation_classification(handle, classification)
    return classification


def _durable_state(repository, handle):
    return (
        repository.active_receipt(handle),
        dict(repository.claims),
        dict(repository.revisions),
        dict(repository.activation_receipts),
        dict(repository.pending_reservations),
        dict(repository.planning_reservations),
    )


def _rewrite_successor_record_classification(
    repository,
    artifacts,
    handle,
    classification,
):
    from gwo_v8._canonical import canonical_bytes

    attempt = repository.read_attempt(handle, classification.plan_revision_digest)
    assert attempt is not None
    record = deepcopy(artifacts.values[attempt.compilation_record_artifact_digest])
    embedded = replace(
        classification,
        reason="A different durable classification must not activate this successor.",
    )
    output_digest = record["output_artifact_digest"]
    output = deepcopy(artifacts.values[output_digest])
    output["payload"]["reason"] = embedded.reason
    output_ref = artifacts.put_canonical(output)
    record["planning_receipt"]["output_artifact_digest"] = output_ref.digest
    record["planning_receipt"]["planning_output_artifact_digest"] = output_ref.digest
    record["output_artifact_digest"] = output_ref.digest
    record["planning_output"] = artifacts.read_json(output_ref.digest)
    record["classification"] = embedded.canonical()
    record["classification_digest"] = embedded.digest
    record_ref = artifacts.put_canonical(record)
    repository.attempts[
        (handle.repository, handle.campaign_key, classification.plan_revision_digest)
    ] = replace(
        attempt,
        compilation_record_artifact_digest=record_ref.digest,
        compilation_record_bytes=canonical_bytes(record),
    )


def _canonical_accepted_result_binding():
    return {
        "kind": "accepted_result_binding.v1",
        "ticket_key": "issue:108",
        "result_digest": "1" * 64,
        "evidence_digests": ["2" * 64, "3" * 64],
        "work_subject_digest": "4" * 64,
        "target_facts_digest": "5" * 64,
    }


def test_activate_successor_never_calls_gateway_again():
    control, repository, gateway, artifacts, _source, handle = _initial_control()
    classification = _seed_completed_successor(
        control, repository, gateway, artifacts, handle
    )

    readback = control.activate_successor(handle, classification)
    replay = control.activate_successor(handle, classification)

    assert gateway.replan_progresses == 0
    assert replay == readback
    assert readback.activation_receipt.planning_stable_action_id == classification.action_id
    assert readback.activation_receipt.expected_previous_revision_digest == (
        classification.plan_revision_digest
    )
    assert repository.read_planning_reservation(
        handle, classification.action_id
    ) is None


def test_classification_persists_one_successor_attempt_before_result():
    _control, repository, _gateway, _artifacts, _source, handle, classification = (
        _classified_control()
    )
    attempt = repository.read_attempt(handle, classification.plan_revision_digest)

    assert attempt is not None
    assert attempt.planning_protocol_id == "campaign.plan-invalidation-output.v1"
    assert attempt.subject.stable_action_id == classification.action_id
    assert attempt.compilation_record_artifact_digest is not None
    reservation = repository.read_planning_reservation(
        handle,
        classification.action_id,
    )
    assert reservation is not None
    assert reservation.subject_digest == attempt.subject.digest
    assert reservation.ticket_keys == attempt.ticket_keys


def test_successor_classification_replay_keeps_one_attempt_and_reservation():
    control, repository, gateway, _artifacts, _source, handle, classification = (
        _classified_control()
    )
    replay = control.classify_plan_invalidations(
        handle,
        (_invalidation(control, handle),),
        _execution_snapshot(control.read_active(handle)),
    )

    assert replay == classification
    assert gateway.replan_progresses == 1
    attempt = repository.read_attempt(handle, classification.plan_revision_digest)
    assert attempt is not None
    assert repository.read_planning_reservation(
        handle,
        classification.action_id,
    ) is not None


def test_successor_classification_recovers_after_attempt_commit_crash():
    from gwo_v8.plan_control import PlanControlError

    control, repository, gateway, _artifacts, _source, handle = _initial_control()
    gateway.payload = _successor_payload()
    active = control.read_active(handle)
    original_save = repository.save_invalidation_classification
    crashed = False

    def crash_once(classification_handle, classification):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise PlanControlError(
                "DURABLE_STATE_AMBIGUOUS",
                "synthetic crash after the successor attempt commit",
            )
        return original_save(classification_handle, classification)

    repository.save_invalidation_classification = crash_once
    with pytest.raises(PlanControlError) as raised:
        control.classify_plan_invalidations(
            handle,
            (_invalidation(control, handle),),
            _execution_snapshot(active),
        )
    assert raised.value.code == "DURABLE_STATE_AMBIGUOUS"
    assert gateway.replan_progresses == 1

    repository.save_invalidation_classification = original_save
    replay = control.classify_plan_invalidations(
        handle,
        (_invalidation(control, handle),),
        _execution_snapshot(active),
    )

    assert replay is not None
    assert replay.disposition.value == "use_approved_successor"
    assert gateway.replan_progresses == 1
    assert repository.read_attempt(
        handle,
        replay.plan_revision_digest,
    ) is not None


def test_successor_classification_readback_fails_if_attempt_is_missing():
    from gwo_v8.plan_control import PlanControlError

    control, repository, _gateway, _artifacts, _source, handle, classification = (
        _classified_control()
    )
    repository.attempts.pop(
        (handle.repository, handle.campaign_key, classification.plan_revision_digest)
    )

    with pytest.raises(PlanControlError) as raised:
        control.classify_plan_invalidations(
            handle,
            (_invalidation(control, handle),),
            _execution_snapshot(control.read_active(handle)),
        )

    assert raised.value.code == "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID"


def test_new_approved_dependency_reaches_compiled_plan():
    from gwo_v8._canonical import load_canonical_json

    control, _repository, _gateway, _artifacts, _source, handle, classification = (
        _classified_control()
    )
    readback = control.activate_successor(handle, classification)
    work = {
        item["key"]: item
        for item in load_canonical_json(readback.plan_spec_bytes)["work"]
    }

    assert work["issue:109"]["depends_on"] == ["issue:110"]
    assert work["issue:110"]["depends_on"] == []


def test_successor_replay_returns_the_same_exact_readback():
    control, _repository, gateway, _artifacts, _source, handle, classification = (
        _classified_control()
    )
    first = control.activate_successor(handle, classification)
    second = control.activate_successor(handle, classification)

    assert gateway.replan_progresses == 1
    assert second == first
    assert second.activation_receipt == first.activation_receipt
    assert second.plan_spec_bytes == first.plan_spec_bytes
    assert second.claim_proofs == first.claim_proofs


def test_source_or_policy_drift_stops_before_publication():
    from gwo_v8.plan_control import PlanControlError

    control, repository, _gateway, _artifacts, source, handle, classification = (
        _classified_control()
    )
    predecessor = repository.active_receipt(handle)
    source.value["target_branch"] = "release"

    with pytest.raises(PlanControlError) as raised:
        control.activate_successor(handle, classification)

    assert raised.value.code == "REPLAN_SOURCE_CHANGED"
    assert repository.active_receipt(handle) == predecessor
    assert len(repository.revisions) == 1
    assert repository.claims == {
        (handle.repository, ticket_key): predecessor.revision_digest
        for ticket_key in classification.successor_ticket_keys
    } | {
        (handle.repository, "issue:108"): predecessor.revision_digest,
        (handle.repository, "issue:110"): predecessor.revision_digest,
    }


def test_two_successors_have_one_cas_winner_and_no_partial_loser():
    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.plan_control import ActivationReceipt, PlanningReservation

    control, repository, _gateway, _artifacts, _source, handle, first = (
        _classified_control()
    )
    first_attempt = repository.read_attempt(handle, first.plan_revision_digest)
    assert first_attempt is not None

    class CompetingRepository:
        writer_generation = repository.writer_generation

        def __init__(self):
            self.competitor = None

        def __getattr__(self, name):
            return getattr(repository, name)

        def activate(self, receipt):
            if self.competitor is None:
                self.competitor = ActivationReceipt(
                    repository=receipt.repository,
                    campaign_key=receipt.campaign_key,
                    revision_digest="c" * 64,
                    expected_previous_revision_digest=receipt.expected_previous_revision_digest,
                    writer_generation=receipt.writer_generation,
                    ready_refs=receipt.ready_refs,
                    ticket_keys=receipt.ticket_keys,
                    planning_subject_digest="d" * 64,
                    planning_stable_action_id="replan:competitor",
                    planning_preflight_receipt_digest="e" * 64,
                    compilation_record_artifact_digest="f" * 64,
                    planning_receipt_digest="a" * 64,
                    planning_output_artifact_digest="b" * 64,
                )
                repository.reserve_planning(
                    PlanningReservation(
                        repository=receipt.repository,
                        campaign_key=receipt.campaign_key,
                        ticket_keys=receipt.ticket_keys,
                        subject_digest=self.competitor.planning_subject_digest,
                        stable_action_id=self.competitor.planning_stable_action_id,
                        preflight_receipt_digest=self.competitor.planning_preflight_receipt_digest,
                        snapshot_artifact_digest=first_attempt.snapshot_artifact_digest,
                        policy_witness_digest=first_attempt.policy_witness_digest,
                        planning_request_artifact_digest=first_attempt.planning_request_artifact_digest,
                    )
                )
                repository.reserve_claims(self.competitor)
                repository.activate(self.competitor)
            return repository.activate(receipt)

    competing = CompetingRepository()
    control._repository = competing

    with pytest.raises(PlanControlError) as raised:
        control.activate_successor(handle, first)

    assert raised.value.code == "ACTIVATION_CAS_CONFLICT"
    assert repository.active_receipt(handle) == competing.competitor
    assert all(
        digest == first.plan_revision_digest
        for digest in repository.claims.values()
    )


def test_successor_replays_after_claim_reservation_crash():
    from gwo_v8.plan_control import PlanControlError

    control, repository, _gateway, _artifacts, _source, handle, classification = (
        _classified_control()
    )

    class CrashAfterReserve:
        writer_generation = repository.writer_generation

        def __init__(self):
            self.crashed = False

        def __getattr__(self, name):
            return getattr(repository, name)

        def reserve_claims(self, receipt):
            result = repository.reserve_claims(receipt)
            if not self.crashed:
                self.crashed = True
                raise PlanControlError(
                    "DURABLE_STATE_AMBIGUOUS",
                    "synthetic crash after successor claim reservation",
                )
            return result

    crash_repository = CrashAfterReserve()
    control._repository = crash_repository
    with pytest.raises(PlanControlError) as crashed:
        control.activate_successor(handle, classification)
    assert crashed.value.code == "DURABLE_STATE_AMBIGUOUS"

    replay = control.activate_successor(handle, classification)
    assert replay.activation_receipt.planning_stable_action_id == classification.action_id
    assert repository.read_planning_reservation(handle, classification.action_id) is None


def test_successor_compilation_record_tamper_fails_closed():
    from gwo_v8.plan_control import PlanControlError

    control, repository, _gateway, artifacts, _source, handle, classification = (
        _classified_control()
    )
    attempt = repository.read_attempt(handle, classification.plan_revision_digest)
    assert attempt is not None
    record = artifacts.values[attempt.compilation_record_artifact_digest]
    record["normalized_intent_digest"] = "0" * 64
    before = _durable_state(repository, handle)

    with pytest.raises(PlanControlError) as raised:
        control.activate_successor(handle, classification)

    assert raised.value.code == "COMPILATION_RECORD_INVALID"
    assert _durable_state(repository, handle) == before


@pytest.mark.parametrize(
    ("artifact", "field", "expected_code"),
    (
        ("snapshot", "target_branch", "SNAPSHOT_READBACK_INVALID"),
        ("policy", "ref", "POLICY_WITNESS_INVALID"),
        ("request", "authority_digest", "PLANNING_REQUEST_INVALID"),
    ),
)
def test_successor_immutable_input_tamper_fails_before_any_effect(
    artifact,
    field,
    expected_code,
):
    from gwo_v8.plan_control import PlanControlError

    control, repository, _gateway, artifacts, _source, handle, classification = (
        _classified_control()
    )
    attempt = repository.read_attempt(handle, classification.plan_revision_digest)
    assert attempt is not None
    digests = {
        "snapshot": attempt.snapshot_artifact_digest,
        "policy": attempt.policy_witness_digest,
        "request": attempt.planning_request_artifact_digest,
    }
    artifacts.values[digests[artifact]][field] = "tampered"
    before = _durable_state(repository, handle)

    with pytest.raises(PlanControlError) as raised:
        control.activate_successor(handle, classification)

    assert raised.value.code == expected_code
    assert _durable_state(repository, handle) == before


def test_successor_snapshot_bytes_tamper_fails_before_any_effect():
    from gwo_v8._canonical import canonical_bytes, load_canonical_json
    from gwo_v8.plan_control import PlanControlError

    control, repository, _gateway, _artifacts, _source, handle, classification = (
        _classified_control()
    )
    attempt = repository.read_attempt(handle, classification.plan_revision_digest)
    assert attempt is not None
    snapshot = load_canonical_json(attempt.snapshot_bytes)
    snapshot["target_branch"] = "release"
    repository.attempts[
        (handle.repository, handle.campaign_key, classification.plan_revision_digest)
    ] = replace(attempt, snapshot_bytes=canonical_bytes(snapshot))
    before = _durable_state(repository, handle)

    with pytest.raises(PlanControlError) as raised:
        control.activate_successor(handle, classification)

    assert raised.value.code == "SNAPSHOT_DIGEST_MISMATCH"
    assert _durable_state(repository, handle) == before


def test_successor_compilation_record_bytes_are_required():
    from gwo_v8.plan_control import PlanControlError

    control, repository, _gateway, _artifacts, _source, handle, classification = (
        _classified_control()
    )
    attempt = repository.read_attempt(handle, classification.plan_revision_digest)
    assert attempt is not None
    repository.attempts[
        (handle.repository, handle.campaign_key, classification.plan_revision_digest)
    ] = replace(attempt, compilation_record_bytes=None)
    before = _durable_state(repository, handle)

    with pytest.raises(PlanControlError) as raised:
        control.activate_successor(handle, classification)

    assert raised.value.code == "COMPILATION_RECORD_INVALID"
    assert _durable_state(repository, handle) == before


def test_successor_embedded_classification_must_match_durable_classification():
    from gwo_v8.plan_control import PlanControlError

    control, repository, _gateway, artifacts, _source, handle, classification = (
        _classified_control()
    )
    _rewrite_successor_record_classification(
        repository,
        artifacts,
        handle,
        classification,
    )
    before = _durable_state(repository, handle)

    with pytest.raises(PlanControlError) as raised:
        control.activate_successor(handle, classification)

    assert raised.value.code == "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID"
    assert _durable_state(repository, handle) == before


def test_start_dispatches_successor_recovery_without_a_second_planning_pass():
    control, repository, gateway, _artifacts, _source, handle, classification = (
        _classified_control()
    )
    predecessor = repository.active_receipt(handle)

    result = control.start(
        handle.repository,
        ["issue:108", "issue:109", "issue:110"],
        campaign_key=handle.campaign_key,
        expected_previous_revision_digest=classification.plan_revision_digest,
    )

    assert result == handle
    assert gateway.replan_progresses == 1
    assert repository.active_receipt(handle) != predecessor


def test_start_successor_recovery_missing_classification_is_named_and_fail_closed():
    from gwo_v8.plan_control import PlanControlError

    control, repository, _gateway, _artifacts, _source, handle, classification = (
        _classified_control()
    )
    repository.invalidation_classifications.pop(
        (handle.repository, handle.campaign_key, classification.action_id)
    )
    before = _durable_state(repository, handle)

    with pytest.raises(PlanControlError) as raised:
        control.start(
            handle.repository,
            ["issue:108", "issue:109", "issue:110"],
            campaign_key=handle.campaign_key,
            expected_previous_revision_digest=classification.plan_revision_digest,
        )

    assert raised.value.code == "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID"
    assert _durable_state(repository, handle) == before


def test_execution_snapshot_retains_canonical_accepted_result_binding():
    control, _repository, gateway, artifacts, _source, handle = _initial_control()
    gateway.payload = _successor_payload()
    active = control.read_active(handle)
    execution = _execution_snapshot(active)
    binding = _canonical_accepted_result_binding()
    execution["accepted_results"] = [binding]

    classification = control.classify_plan_invalidations(
        handle,
        (_invalidation(control, handle),),
        execution,
    )

    assert classification is not None
    snapshot = artifacts.read_json(classification.snapshot_digest)
    assert snapshot["accepted_results"] == [binding]


def test_execution_snapshot_accepts_explicit_legacy_accepted_result_record():
    control, _repository, gateway, artifacts, _source, handle = _initial_control()
    gateway.payload = _successor_payload()
    active = control.read_active(handle)
    execution = _execution_snapshot(active)
    legacy = {"ticket_key": "issue:108", "result_digest": "6" * 64}
    execution["accepted_results"] = [legacy]

    classification = control.classify_plan_invalidations(
        handle,
        (_invalidation(control, handle),),
        execution,
    )

    assert classification is not None
    snapshot = artifacts.read_json(classification.snapshot_digest)
    assert snapshot["accepted_results"] == [legacy]


def test_allowed_exclusive_resource_delta_reaches_compiled_plan():
    from gwo_v8._canonical import load_canonical_json

    payload = _successor_payload()
    payload["successor"]["dependency_additions"] = []
    payload["successor"]["exclusive_resource_additions"] = [{
        "ticket_key": "issue:109",
        "resource_id": "repository.target.v1",
        "reason": "The owner serializes the shared target.",
    }]
    control, _repository, _gateway, _artifacts, _source, handle, classification = (
        _classified_control(payload)
    )
    readback = control.activate_successor(handle, classification)
    plan = load_canonical_json(readback.plan_spec_bytes)
    work = {item["key"]: item for item in plan["work"]}

    assert work["issue:109"]["exclusive_resources"] == [
        "repository.target.v1"
    ]
