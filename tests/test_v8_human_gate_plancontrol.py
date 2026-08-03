from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from v8_successor_test_support import _direct_setup, _execution_snapshot
from gwo_v8._canonical import canonical_bytes, digest_value, load_canonical_json


def _human_payload():
    return {
        "evidence_digests": ["9" * 64],
        "disposition": "require_human_decision",
        "reason": "The requested authority exceeds the frozen Policy Witness.",
        "successor": None,
        "decision": {
            "code": "HUMAN_DECISION_REQUIRED",
            "detail": "A human must approve the broader authority.",
            "required_change": "authority",
        },
    }


class _PendingSource:
    def __init__(self, result):
        self.result = result
        self.reads = 0

    def read(self, handle, decision, readback_ref):
        self.reads += 1
        assert handle == decision.campaign
        assert readback_ref == "workflow://approval/one"
        return self.result


class _ApprovedSource(_PendingSource):
    pass


class _SequencedSource(_PendingSource):
    def __init__(self, *results):
        self.results = list(results)
        self.reads = 0

    def read(self, handle, decision, readback_ref):
        self.reads += 1
        assert handle == decision.campaign
        assert readback_ref == "workflow://approval/one"
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


class _UnauthorizedReplaySource(_ApprovedSource):
    def read(self, handle, decision, readback_ref):
        from gwo_v8.human_gate import HumanGateError

        raise HumanGateError(
            "HUMAN_APPROVAL_UNAUTHORIZED",
            "the durable approval actor is no longer allowed",
        )


def _decision_setup():
    control, repository, gateway, artifacts, source, host, handle, harness = _direct_setup(
        _human_payload()
    )
    active = host.read_active(handle)
    classification = control.classify_plan_invalidations(
        handle,
        (harness.invalidation_for("issue:109"),),
        _execution_snapshot(active),
    )
    assert classification is not None
    assert classification.decision is not None
    return control, repository, gateway, artifacts, source, host, handle, harness, classification


def _pending_readback(decision_id: str):
    from gwo_v8.human_gate import HumanSourceReadback

    return HumanSourceReadback(
        decision_id=decision_id,
        state="pending",
        approval_record_bytes=None,
        tracker_source_bytes=None,
        policy_witness_bytes=None,
        approval_record_digest=None,
        tracker_source_digest=None,
        policy_witness_digest=None,
        source_change_digest=None,
        readback_digest=digest_value(
            {
                "decision_id": decision_id,
                "state": "pending",
                "approval_record_digest": None,
                "tracker_source_digest": None,
                "policy_witness_digest": None,
                "source_change_digest": None,
                "code": "HUMAN_SOURCE_READBACK_PENDING",
            }
        ),
        code="HUMAN_SOURCE_READBACK_PENDING",
    )


def _approved_readback(control, handle, decision):
    from gwo_v8._canonical import digest_bytes
    from gwo_v8.human_gate import HumanSourceReadback

    source = deepcopy(
        control._source.snapshot(
            handle.repository,
            ("issue:108", "issue:109", "issue:110"),
        )
    )
    source_core = {
        "kind": "gwo.human-tracker-source.v1",
        "repository": handle.repository,
        "campaign_key": handle.campaign_key,
        "target_branch": source["target_branch"],
        "campaign_source": source["campaign_source"],
        "membership": {
            "ticket_keys": [ticket["key"] for ticket in source["tickets"]],
            "digest": digest_value(
                {"ticket_keys": [ticket["key"] for ticket in source["tickets"]]}
            ),
        },
        "tickets": source["tickets"],
        "product_release": None,
    }
    tracker = {**source_core, "source_change_digest": digest_value(source_core)}
    policy = deepcopy(source["policy"])
    # The classification in this fixture requires an authority change.  Make
    # the synthetic authoritative readback a real policy-domain change rather
    # than an accidental no-op of the active Policy Witness.
    policy["ref"] = "policy:human-approved"
    policy["replan"] = {
        "successor_revision_limit": 1,
        "repeated_invalidation_limit": 1,
    }
    policy["digest"] = digest_value(
        {key: value for key, value in policy.items() if key != "digest"}
    )
    approval = {
        "kind": "gwo.human-approval.v1",
        "decision_id": decision.decision_id,
        "classification_action_id": decision.classification_action_id,
        "predecessor_revision_digest": decision.plan_revision_digest,
        "evidence_digests": list(decision.evidence_digests),
        "required_change": decision.required_change,
        "approval_state": "approved",
        "approval_record_ref": "workflow://approval/one",
        "approval_actor_ref": "workflow://gwo-human-gate",
        "source_change_digest": policy["digest"],
    }
    approval_bytes = canonical_bytes(approval)
    tracker_bytes = canonical_bytes(tracker)
    policy_bytes = canonical_bytes(policy)
    return HumanSourceReadback(
        decision_id=decision.decision_id,
        state="approved",
        approval_record_bytes=approval_bytes,
        tracker_source_bytes=tracker_bytes,
        policy_witness_bytes=policy_bytes,
        approval_record_digest=digest_bytes(approval_bytes),
        tracker_source_digest=digest_bytes(tracker_bytes),
        policy_witness_digest=digest_bytes(policy_bytes),
        source_change_digest=policy["digest"],
        readback_digest=digest_value(
            {
                "decision_id": decision.decision_id,
                "state": "approved",
                "approval_record_digest": digest_bytes(approval_bytes),
                "tracker_source_digest": digest_bytes(tracker_bytes),
                "policy_witness_digest": digest_bytes(policy_bytes),
                "source_change_digest": policy["digest"],
                "code": "HUMAN_SOURCE_APPROVED",
            }
        ),
        code="HUMAN_SOURCE_APPROVED",
    )


def _approved_readback_with_sources(
    readback,
    *,
    tracker=None,
    policy=None,
):
    from gwo_v8._canonical import digest_bytes
    from gwo_v8.human_gate import HumanSourceReadback

    approval = load_canonical_json(readback.approval_record_bytes)
    tracker = deepcopy(
        tracker if tracker is not None else load_canonical_json(readback.tracker_source_bytes)
    )
    policy = deepcopy(
        policy if policy is not None else load_canonical_json(readback.policy_witness_bytes)
    )
    approval["source_change_digest"] = policy["digest"] if approval.get("required_change") == "authority" else tracker["source_change_digest"]
    approval_bytes = canonical_bytes(approval)
    tracker_bytes = canonical_bytes(tracker)
    policy_bytes = canonical_bytes(policy)
    approval_digest = digest_bytes(approval_bytes)
    tracker_digest = digest_bytes(tracker_bytes)
    policy_digest = digest_bytes(policy_bytes)
    source_change_digest = (
        policy["digest"]
        if approval.get("required_change") == "authority"
        else tracker["source_change_digest"]
    )
    return HumanSourceReadback(
        decision_id=readback.decision_id,
        state="approved",
        approval_record_bytes=approval_bytes,
        tracker_source_bytes=tracker_bytes,
        policy_witness_bytes=policy_bytes,
        approval_record_digest=approval_digest,
        tracker_source_digest=tracker_digest,
        policy_witness_digest=policy_digest,
        source_change_digest=source_change_digest,
        readback_digest=digest_value(
            {
                "decision_id": readback.decision_id,
                "state": "approved",
                "approval_record_digest": approval_digest,
                "tracker_source_digest": tracker_digest,
                "policy_witness_digest": policy_digest,
                "source_change_digest": source_change_digest,
                "code": "HUMAN_SOURCE_APPROVED",
            }
        ),
        code="HUMAN_SOURCE_APPROVED",
    )


def _rebind_tracker_projection(tracker):
    tracker["tickets"] = sorted(tracker["tickets"], key=lambda item: item["key"])
    ticket_keys = [ticket["key"] for ticket in tracker["tickets"]]
    tracker["membership"] = {
        "ticket_keys": ticket_keys,
        "digest": digest_value({"ticket_keys": ticket_keys}),
    }
    source_core = {
        key: tracker[key]
        for key in (
            "kind",
            "repository",
            "campaign_key",
            "target_branch",
            "campaign_source",
            "membership",
            "tickets",
            "product_release",
        )
    }
    tracker["source_change_digest"] = digest_value(source_core)
    return tracker


def test_require_human_decision_derives_one_stable_record_and_exact_repository_readback():
    control, repository, gateway, _artifacts, _source, host, handle, _harness, classification = (
        _decision_setup()
    )

    first = control.require_human_decision(handle, classification)
    second = control.require_human_decision(handle, classification)

    assert first == second
    assert first.decision_id.startswith("decision:")
    assert repository.read_human_decision(handle, first.decision_id) == first

    changed = type(classification)(
        **{
            **classification.__dict__,
            "reason": "changed exact invalidation",
        }
    )
    with pytest.raises(Exception) as error:
        control.require_human_decision(handle, changed)
    assert error.value.code == "HUMAN_DECISION_CONFLICT"


def test_authority_decision_binds_predecessor_policy_domain_and_snapshot_separately():
    control, _repository, _gateway, artifacts, _source, host, handle, _harness, classification = (
        _decision_setup()
    )

    decision = control.require_human_decision(handle, classification)
    predecessor_snapshot = artifacts.read_json(classification.snapshot_digest)
    active_plan = load_canonical_json(host.read_active(handle).plan_spec_bytes)

    assert decision.required_source.source_kind == "policy"
    assert decision.required_source.predecessor_snapshot_digest == classification.snapshot_digest
    assert decision.required_source.predecessor_source_digest == active_plan["policy"]["digest"]
    assert decision.required_source.predecessor_source_digest != digest_value(
        {
            "kind": "gwo.human-tracker-source.v1",
            "repository": handle.repository,
            "campaign_key": handle.campaign_key,
            "target_branch": predecessor_snapshot["target_branch"],
            "campaign_source": predecessor_snapshot["campaign_source"],
            "membership": {
                "ticket_keys": [ticket["key"] for ticket in predecessor_snapshot["tickets"]],
                "digest": digest_value(
                    {"ticket_keys": [ticket["key"] for ticket in predecessor_snapshot["tickets"]]}
                ),
            },
            "tickets": predecessor_snapshot["tickets"],
            "product_release": predecessor_snapshot.get("product_release"),
        }
    )


def test_campaign_source_validation_accepts_canonical_projection_sets_without_hashing_sets():
    from gwo_v8.plan_control import _campaign_source
    from v8_successor_test_support import three_ticket_source_snapshot

    source = three_ticket_source_snapshot()
    assert _campaign_source(
        source["campaign_source"],
        source["repository"],
    ) == source["campaign_source"]


def test_successor_policy_validation_accepts_canonical_field_sets_without_hashing_sets():
    from gwo_v8.successor_plan import _require_policy
    from v8_successor_test_support import three_ticket_source_snapshot

    policy = three_ticket_source_snapshot()["policy_witness"]

    assert _require_policy(policy) == policy


def test_replan_budget_policy_reads_the_active_policy_witness_exactly():
    from gwo_v8.human_gate import ReplanBudgetPolicy

    control, _repository, _gateway, _artifacts, _source, host, handle, _harness, _classification = (
        _decision_setup()
    )

    policy = control.read_replan_budget_policy(handle)

    assert type(policy) is ReplanBudgetPolicy
    assert policy.successor_revision_limit == 1
    assert policy.repeated_invalidation_limit == 1
    active_plan = load_canonical_json(host.read_active(handle).plan_spec_bytes)
    assert policy.policy_witness_digest == active_plan["policy"]["digest"]


def test_pending_human_readback_is_saved_once_and_replayed_without_a_second_source_read():
    from gwo_v8.human_gate import HumanDecisionChoice

    control, repository, _gateway, _artifacts, _source, _host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    source = _PendingSource(_pending_readback(decision.decision_id))
    control._human_source = source
    choice = HumanDecisionChoice(
        decision_id=decision.decision_id,
        choice="approve",
        readback_ref="workflow://approval/one",
    )

    first = control.advance_human_decision(handle, decision, choice)
    second = control.advance_human_decision(handle, decision, choice)

    assert first == second
    assert source.reads == 1
    assert repository.read_human_gate_readback(handle, decision.decision_id) == first


def test_replaying_an_approved_readback_rechecks_authoritative_actor_before_planning():
    from gwo_v8.human_gate import HumanDecisionChoice
    from gwo_v8.plan_control import PlanControlError
    from v8_successor_test_support import successor_payload

    control, repository, gateway, _artifacts, _source, _host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    readback = _approved_readback(control, handle, decision)
    choice = HumanDecisionChoice(decision.decision_id, "approve", "workflow://approval/one")
    repository.save_human_decision(decision)
    repository.save_human_gate_readback(handle, decision, choice, readback)
    control._human_source = _UnauthorizedReplaySource(readback)
    gateway.payload = successor_payload()

    with pytest.raises(PlanControlError) as error:
        control.advance_human_decision(handle, decision, choice)

    assert error.value.code == "HUMAN_APPROVAL_UNAUTHORIZED"
    assert gateway.replan_progresses == 1


def test_rejected_choice_never_activates_even_when_existing_source_readback_is_approved():
    from gwo_v8.human_gate import HumanDecisionChoice
    from gwo_v8.plan_control import PlanControlError

    (
        control,
        repository,
        gateway,
        _artifacts,
        _source,
        host,
        handle,
        _harness,
        classification,
    ) = _decision_setup()
    decision = control.require_human_decision(handle, classification)
    readback = _approved_readback(control, handle, decision)
    control._human_source = _ApprovedSource(readback)
    reject = HumanDecisionChoice(
        decision.decision_id,
        "reject",
        "workflow://approval/one",
    )
    predecessor = host.read_active(handle)

    # Persist the authoritative source readback without allowing it to run a
    # successor.  The following public advance is the replay path under audit.
    rejected = control.read_human_decision_source(handle, decision, reject)
    assert rejected.state == "rejected"
    assert rejected.code == "HUMAN_SOURCE_REJECTED"
    assert repository.read_human_gate_readback(handle, decision.decision_id) == readback
    with pytest.raises(PlanControlError) as error:
        control.advance_human_decision(handle, decision, reject)

    assert error.value.code == "HUMAN_APPROVAL_INPUT_INVALID"
    assert gateway.replan_progresses == 1
    assert host.read_active(handle) == predecessor
    assert repository.read_attempt(handle, predecessor.current_revision_digest) is None


def test_human_decision_choice_conflict_does_not_replace_durable_source_readback():
    from gwo_v8.human_gate import HumanDecisionChoice

    control, repository, _gateway, _artifacts, _source, _host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    source = _PendingSource(_pending_readback(decision.decision_id))
    control._human_source = source
    approve = HumanDecisionChoice(
        decision.decision_id, "approve", "workflow://approval/one"
    )
    reject = HumanDecisionChoice(decision.decision_id, "reject", "workflow://two")

    control.advance_human_decision(handle, decision, approve)
    with pytest.raises(Exception) as error:
        control.advance_human_decision(handle, decision, reject)

    assert error.value.code == "HUMAN_APPROVAL_INPUT_INVALID"
    assert source.reads == 1


def test_approved_human_source_runs_one_tagged_successor_pass_and_activates_once():
    from gwo_v8.human_gate import HumanDecisionChoice

    (
        control,
        repository,
        gateway,
        _artifacts,
        _source,
        host,
        handle,
        harness,
        classification,
    ) = _decision_setup()
    decision = control.require_human_decision(handle, classification)
    readback = _approved_readback(control, handle, decision)
    control._human_source = _ApprovedSource(readback)
    from v8_successor_test_support import successor_payload

    gateway.payload = successor_payload(
        dependencies=(
            (
                "issue:109",
                "issue:110",
                "The approved authority permits the successor dependency.",
            ),
        )
    )
    choice = HumanDecisionChoice(
        decision.decision_id,
        "approve",
        "workflow://approval/one",
    )
    predecessor = host.read_active(handle)

    result = control.advance_human_decision(handle, decision, choice)
    successor = host.read_active(handle)

    assert result == readback
    assert successor.current_revision_digest != predecessor.current_revision_digest
    # One pass classified the invalidation; approval adds exactly one new
    # human-owned successor pass and never re-enters that pass.
    assert gateway.replan_progresses == 2
    attempt = repository.read_attempt(handle, predecessor.current_revision_digest)
    assert attempt is not None
    assert attempt.planning_protocol_id == "campaign.plan-invalidation-output.v1"
    expected_human_action_id = "replan:human:" + digest_value(
        {
            "decision_id": decision.decision_id,
            "source_readback_digest": readback.readback_digest,
            "previous_revision_digest": predecessor.current_revision_digest,
        }
    )[:24]
    assert attempt.subject.stable_action_id == (
        expected_human_action_id
    )
    assert repository.read_human_gate_readback(handle, decision.decision_id) == readback
    human_attempt = repository.read_human_gate_attempt(
        handle,
        decision.decision_id,
        readback.readback_digest,
    )
    assert human_attempt is not None
    assert human_attempt.state == "active_successor"
    assert human_attempt.compilation_record_artifact_digest is not None
    assert human_attempt.activation_receipt_digest == digest_value(
        host.read_active(handle).activation_receipt.__dict__
    )


def test_approved_human_source_snapshot_contains_the_complete_authoritative_projection():
    from gwo_v8.human_gate import HumanDecisionChoice

    (
        control,
        _repository,
        gateway,
        artifacts,
        _source,
        _host,
        handle,
        _harness,
        classification,
    ) = _decision_setup()
    decision = control.require_human_decision(handle, classification)
    readback = _approved_readback(control, handle, decision)
    control._human_source = _ApprovedSource(readback)
    from v8_successor_test_support import successor_payload

    gateway.payload = successor_payload(
        dependencies=(
            (
                "issue:109",
                "issue:110",
                "The approved authority permits the successor dependency.",
            ),
        )
    )

    control.advance_human_decision(
        handle,
        decision,
        HumanDecisionChoice(
            decision_id=decision.decision_id,
            choice="approve",
            readback_ref="workflow://approval/one",
        ),
    )

    tracker = load_canonical_json(readback.tracker_source_bytes)
    policy = load_canonical_json(readback.policy_witness_bytes)
    successor_subject = gateway.preflights[-1]
    snapshot = artifacts.read_json(successor_subject.snapshot_artifact_digest)
    assert snapshot["target_branch"] == tracker["target_branch"]
    assert snapshot["campaign_source"] == tracker["campaign_source"]
    assert snapshot["membership"] == tracker["membership"]
    assert snapshot["tickets"] == tracker["tickets"]
    assert snapshot["product_release"] == tracker["product_release"]
    assert snapshot["source_change_digest"] == tracker["source_change_digest"]
    assert snapshot["policy_witness"] == policy


def test_approved_source_adoption_compiles_new_ticket_and_contract_from_tracker_source():
    from gwo_v8.human_gate import HumanDecisionChoice
    from v8_successor_test_support import _ticket, successor_payload

    (
        control,
        _repository,
        gateway,
        _artifacts,
        _source,
        host,
        handle,
        _harness,
        classification,
    ) = _decision_setup()
    decision = control.require_human_decision(handle, classification)
    readback = _approved_readback(control, handle, decision)
    tracker = load_canonical_json(readback.tracker_source_bytes)
    new_ticket = _ticket(111, body="Approved new Ticket contract")
    tracker["tickets"].append(new_ticket)
    tracker["tickets"] = sorted(tracker["tickets"], key=lambda item: item["key"])
    tracker["membership"] = {
        "ticket_keys": [ticket["key"] for ticket in tracker["tickets"]],
        "digest": digest_value(
            {"ticket_keys": [ticket["key"] for ticket in tracker["tickets"]]}
        ),
    }
    source_core = {
        key: tracker[key]
        for key in (
            "kind",
            "repository",
            "campaign_key",
            "target_branch",
            "campaign_source",
            "membership",
            "tickets",
            "product_release",
        )
    }
    tracker["source_change_digest"] = digest_value(source_core)
    control._human_source = _ApprovedSource(
        _approved_readback_with_sources(readback, tracker=tracker)
    )
    gateway.payload = successor_payload(
        owners=("issue:111",),
        dependencies=(
            ("issue:111", "issue:110", "approved new Ticket dependency"),
        ),
    )

    control.advance_human_decision(
        handle,
        decision,
        HumanDecisionChoice(
            decision_id=decision.decision_id,
            choice="approve",
            readback_ref="workflow://approval/one",
        ),
    )

    plan = load_canonical_json(host.read_active(handle).plan_spec_bytes)
    work = {item["key"]: item for item in plan["work"]}
    assert work["issue:111"]["contract"] == new_ticket["contract"]
    assert work["issue:111"]["source"] == new_ticket["source"]
    assert sorted(work) == tracker["membership"]["ticket_keys"]


def test_approved_source_adoption_compiles_changed_acceptance_from_tracker_contract():
    from gwo_v8.human_gate import HumanDecisionChoice
    from v8_successor_test_support import _refresh_ticket_source, successor_payload

    control, _repository, gateway, _artifacts, _source, host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    readback = _approved_readback(control, handle, decision)
    tracker = load_canonical_json(readback.tracker_source_bytes)
    changed = next(ticket for ticket in tracker["tickets"] if ticket["key"] == "issue:109")
    changed["contract"]["body"] = "Approved changed acceptance contract"
    _refresh_ticket_source(changed)
    control._human_source = _ApprovedSource(
        _approved_readback_with_sources(
            readback,
            tracker=_rebind_tracker_projection(tracker),
        )
    )
    gateway.payload = successor_payload()

    control.advance_human_decision(
        handle,
        decision,
        HumanDecisionChoice(decision.decision_id, "approve", "workflow://approval/one"),
    )

    plan = load_canonical_json(host.read_active(handle).plan_spec_bytes)
    adopted = next(item for item in plan["work"] if item["key"] == "issue:109")
    assert adopted["contract"] == changed["contract"]
    assert adopted["source"] == changed["source"]


def test_approved_source_adoption_compiles_changed_campaign_membership_exactly():
    from gwo_v8.human_gate import HumanDecisionChoice
    from v8_successor_test_support import successor_payload

    control, _repository, gateway, _artifacts, _source, host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    readback = _approved_readback(control, handle, decision)
    tracker = load_canonical_json(readback.tracker_source_bytes)
    tracker["tickets"] = [
        ticket for ticket in tracker["tickets"] if ticket["key"] != "issue:108"
    ]
    tracker = _rebind_tracker_projection(tracker)
    control._human_source = _ApprovedSource(
        _approved_readback_with_sources(readback, tracker=tracker)
    )
    gateway.payload = successor_payload()

    control.advance_human_decision(
        handle,
        decision,
        HumanDecisionChoice(decision.decision_id, "approve", "workflow://approval/one"),
    )

    plan = load_canonical_json(host.read_active(handle).plan_spec_bytes)
    assert [item["key"] for item in plan["work"]] == tracker["membership"]["ticket_keys"]


def test_approved_source_adoption_compiles_product_release_projection_exactly():
    from gwo_v8.human_gate import HumanDecisionChoice
    from v8_successor_test_support import successor_payload

    control, _repository, gateway, _artifacts, _source, host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    readback = _approved_readback(control, handle, decision)
    tracker = load_canonical_json(readback.tracker_source_bytes)
    tracker["product_release"] = {
        "product": "gwo",
        "release": "2026.08",
    }
    tracker = _rebind_tracker_projection(tracker)
    control._human_source = _ApprovedSource(
        _approved_readback_with_sources(readback, tracker=tracker)
    )
    gateway.payload = successor_payload()

    control.advance_human_decision(
        handle,
        decision,
        HumanDecisionChoice(decision.decision_id, "approve", "workflow://approval/one"),
    )

    plan = load_canonical_json(host.read_active(handle).plan_spec_bytes)
    assert plan["campaign"]["product_release"] == tracker["product_release"]
    assert plan["campaign"]["source_change_digest"] == tracker["source_change_digest"]


def test_approved_source_adoption_compiles_authority_from_changed_policy_witness():
    from gwo_v8.human_gate import HumanDecisionChoice
    from v8_successor_test_support import successor_payload

    control, _repository, gateway, _artifacts, _source, host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    readback = _approved_readback(control, handle, decision)
    policy = load_canonical_json(readback.policy_witness_bytes)
    policy["ref"] = "policy:approved-authority"
    policy["kind"] = "gwo.policy-witness.v1"
    policy["replan"] = {
        "successor_revision_limit": 1,
        "repeated_invalidation_limit": 1,
    }
    policy["digest"] = digest_value(
        {key: value for key, value in policy.items() if key != "digest"}
    )
    control._human_source = _ApprovedSource(
        _approved_readback_with_sources(readback, policy=policy)
    )
    gateway.payload = successor_payload()

    control.advance_human_decision(
        handle,
        decision,
        HumanDecisionChoice(decision.decision_id, "approve", "workflow://approval/one"),
    )

    plan = load_canonical_json(host.read_active(handle).plan_spec_bytes)
    assert plan["policy"] == {"ref": policy["ref"], "digest": policy["digest"]}
    assert plan["campaign"]["authority"]["grants"] == policy["authority_grants"]["campaign"]
    assert all(
        item["authority"]["worker"]["grants"] == policy["authority_grants"]["worker"]
        and item["authority"]["policy_witness_digest"] == policy["digest"]
        for item in plan["work"]
    )


@pytest.mark.parametrize(
    ("successor_revision_limit", "repeated_invalidation_limit"),
    ((2, 2), (2, 4)),
)
def test_approved_successor_policy_cannot_change_original_replan_budget(
    successor_revision_limit,
    repeated_invalidation_limit,
):
    """Authority may change the witness identity, but never its Campaign budget."""

    from gwo_v8.human_gate import HumanDecisionChoice
    from gwo_v8.plan_control import PlanControlError

    control, repository, gateway, _artifacts, _source, host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    readback = _approved_readback(control, handle, decision)
    policy = load_canonical_json(readback.policy_witness_bytes)
    policy["ref"] = "policy:approved-budget-change"
    policy["replan"] = {
        "successor_revision_limit": successor_revision_limit,
        "repeated_invalidation_limit": repeated_invalidation_limit,
    }
    policy["digest"] = digest_value(
        {key: value for key, value in policy.items() if key != "digest"}
    )
    control._human_source = _ApprovedSource(
        _approved_readback_with_sources(readback, policy=policy)
    )
    from v8_successor_test_support import successor_payload

    gateway.payload = successor_payload(
        dependencies=(
            (
                "issue:109",
                "issue:110",
                "The invalidated work consumes the existing owner's result.",
            ),
        )
    )
    predecessor = host.read_active(handle)

    with pytest.raises(PlanControlError) as error:
        control.advance_human_decision(
            handle,
            decision,
            HumanDecisionChoice(
                decision.decision_id,
                "approve",
                "workflow://approval/one",
            ),
        )

    assert error.value.code == "REPLAN_BUDGET_POLICY_INVALID"
    assert host.read_active(handle) == predecessor
    assert repository.read_human_gate_readback(handle, decision.decision_id) is None


def test_automatic_successor_rejects_changed_budget_before_activation():
    """The automatic successor publication path validates budget before CAS."""

    from gwo_v8._canonical import canonical_bytes, digest_bytes
    from gwo_v8.plan_control import PlanRevision, PlanControlError, _authority
    from v8_successor_test_support import successor_payload

    control, repository, gateway, artifacts, _source, host, handle, harness = _direct_setup(
        successor_payload(
            dependencies=(
                (
                    "issue:109",
                    "issue:110",
                    "The invalidated work consumes the existing owner's result.",
                ),
            )
        )
    )
    active = host.read_active(handle)
    classification = control.classify_plan_invalidations(
        handle,
        (harness.invalidation_for("issue:109"),),
        _execution_snapshot(active),
    )
    assert classification is not None
    predecessor = host.read_active(handle)
    compile_successor = control._compile_successor_revision

    def changed_budget_compile(campaign_handle, attempt, intent_bytes):
        revision = compile_successor(campaign_handle, attempt, intent_bytes)
        plan = deepcopy(revision.plan_spec)
        predecessor_plan = load_canonical_json(predecessor.plan_spec_bytes)
        policy = deepcopy(artifacts.read_json(predecessor_plan["policy"]["digest"]))
        policy["ref"] = "policy:automatic-budget-change"
        policy["replan"] = {
            "successor_revision_limit": 2,
            "repeated_invalidation_limit": 2,
        }
        policy_without_digest = {
            key: value for key, value in policy.items() if key != "digest"
        }
        policy["digest"] = digest_value(policy_without_digest)
        policy_without_digest = {
            key: value for key, value in policy.items() if key != "digest"
        }
        artifacts.put_canonical(policy_without_digest)
        policy_digest = policy["digest"]
        plan["policy"] = {"ref": policy["ref"], "digest": policy_digest}
        plan["campaign"]["authority"] = _authority(
            policy_digest,
            plan["campaign"]["authority"]["grants"],
        )
        for item in plan["work"]:
            item["authority"]["policy_witness_digest"] = policy_digest
            for role in ("worker", "recovery_worker", "review"):
                item["authority"][role] = _authority(
                    policy_digest,
                    item["authority"][role]["grants"],
                )
        payload = canonical_bytes(plan)
        return PlanRevision(
            repository=revision.repository,
            campaign_key=revision.campaign_key,
            snapshot_digest=revision.snapshot_digest,
            canonical_bytes=payload,
            digest=digest_bytes(payload),
        )

    control._compile_successor_revision = changed_budget_compile
    with pytest.raises(PlanControlError) as error:
        control.activate_successor(handle, classification)

    assert error.value.code == "REPLAN_BUDGET_POLICY_INVALID"
    assert host.read_active(handle) == predecessor
    assert len(repository.revisions) == 1
    assert gateway.replan_progresses == 1


def test_invalid_policy_witness_is_rejected_before_human_readback_persistence():
    from gwo_v8.human_gate import HumanDecisionChoice
    from gwo_v8.plan_control import PlanControlError
    from v8_successor_test_support import successor_payload

    control, repository, gateway, _artifacts, _source, _host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    readback = _approved_readback(control, handle, decision)
    policy = load_canonical_json(readback.policy_witness_bytes)
    policy["authority_grants"]["worker"] = []
    policy["digest"] = digest_value(
        {key: value for key, value in policy.items() if key != "digest"}
    )
    control._human_source = _ApprovedSource(
        _approved_readback_with_sources(readback, policy=policy)
    )
    gateway.payload = successor_payload()

    with pytest.raises(PlanControlError) as error:
        control.advance_human_decision(
            handle,
            decision,
            HumanDecisionChoice(decision.decision_id, "approve", "workflow://approval/one"),
        )

    assert error.value.code in {"POLICY_WITNESS_INVALID", "REPLAN_BUDGET_POLICY_INVALID"}
    assert repository.read_human_gate_readback(handle, decision.decision_id) is None


def test_planning_to_activation_rechecks_human_source_cas_without_replanning():
    from gwo_v8.human_gate import HumanDecisionChoice
    from gwo_v8.plan_control import PlanControlError
    from v8_successor_test_support import successor_payload

    control, _repository, gateway, _artifacts, _source, host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    original = _approved_readback(control, handle, decision)
    tracker = load_canonical_json(original.tracker_source_bytes)
    tracker["tickets"].append(__import__("v8_successor_test_support", fromlist=["_ticket"])._ticket(111))
    approved_tracker = _rebind_tracker_projection(tracker)
    approved = _approved_readback_with_sources(original, tracker=approved_tracker)
    drift_tracker = deepcopy(approved_tracker)
    drift_tracker["campaign_source"]["resolved_commit_oid"] = "c" * 40
    drift_tracker["campaign_source"]["digest"] = digest_value(
        {
            key: drift_tracker["campaign_source"][key]
            for key in ("input_ref", "resolved_commit_oid", "tree_oid")
        }
    )
    drift = _approved_readback_with_sources(
        approved,
        tracker=_rebind_tracker_projection(drift_tracker),
    )
    source = _SequencedSource(approved, drift)
    control._human_source = source
    gateway.payload = successor_payload(
        owners=("issue:111",),
        dependencies=(("issue:111", "issue:110", "source CAS"),),
    )
    choice = HumanDecisionChoice(
        decision.decision_id,
        "approve",
        "workflow://approval/one",
    )
    predecessor = host.read_active(handle)

    with pytest.raises(PlanControlError) as error:
        control.advance_human_decision(handle, decision, choice)

    assert error.value.code == "HUMAN_SOURCE_CHANGED_DURING_READBACK"
    assert source.reads == 2
    assert gateway.replan_progresses == 2
    assert host.read_active(handle) == predecessor

    with pytest.raises(PlanControlError) as replay_error:
        control.advance_human_decision(handle, decision, choice)
    assert replay_error.value.code == "HUMAN_SOURCE_CHANGED_DURING_READBACK"
    assert source.reads == 3
    assert gateway.replan_progresses == 2
    assert host.read_active(handle) == predecessor


def test_incomplete_approved_policy_witness_never_falls_back_to_predecessor_policy():
    from gwo_v8.human_gate import HumanDecisionChoice
    from gwo_v8.plan_control import PlanControlError

    control, _repository, gateway, _artifacts, _source, host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    readback = _approved_readback(control, handle, decision)
    minimal_policy = {
        "kind": "gwo.policy-witness.v1",
        "replan": {
            "successor_revision_limit": 1,
            "repeated_invalidation_limit": 1,
        },
    }
    minimal_policy["digest"] = digest_value(
        {key: value for key, value in minimal_policy.items() if key != "digest"}
    )
    control._human_source = _ApprovedSource(
        _approved_readback_with_sources(readback, policy=minimal_policy)
    )
    predecessor = host.read_active(handle)

    with pytest.raises(PlanControlError) as error:
        control.advance_human_decision(
            handle,
            decision,
            HumanDecisionChoice(
                decision.decision_id,
                "approve",
                "workflow://approval/one",
            ),
        )

    assert error.value.code == "REPLAN_BUDGET_POLICY_INVALID"
    assert gateway.replan_progresses == 1
    assert host.read_active(handle) == predecessor


def test_inmemory_save_human_gate_attempt_requires_durable_approved_choice_and_source():
    from gwo_v8.human_gate import HumanDecisionChoice, HumanGateAttempt
    from gwo_v8.plan_control import CampaignHandle, InMemoryPlanRepository, PlanControlError
    from gwo_v8.planning_protocol import REPLANNING_OUTPUT_PROTOCOL_ID

    control, _repository, _gateway, _artifacts, _source, _host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    approved = _approved_readback(control, handle, decision)
    action_id = "replan:human:" + digest_value(
        {
            "decision_id": decision.decision_id,
            "source_readback_digest": approved.readback_digest,
            "previous_revision_digest": decision.plan_revision_digest,
        }
    )[:24]
    attempt = HumanGateAttempt(
        decision_id=decision.decision_id,
        campaign=handle,
        predecessor_revision_digest=decision.plan_revision_digest,
        source_readback_digest=approved.readback_digest,
        tracker_source_digest=approved.tracker_source_digest,
        policy_witness_digest=approved.policy_witness_digest,
        planning_action_id=action_id,
        planning_protocol_id=REPLANNING_OUTPUT_PROTOCOL_ID,
        state="planning_validated_successor",
        compilation_record_artifact_digest=None,
        activation_receipt_digest=None,
    )

    repository = InMemoryPlanRepository(writer_generation="writer:one")
    with pytest.raises(PlanControlError) as error:
        repository.save_human_gate_attempt(attempt)
    assert error.value.code == "HUMAN_GATE_ATTEMPT_READBACK_INVALID"

    repository.save_human_decision(decision)
    repository.save_human_gate_readback(
        handle,
        decision,
        HumanDecisionChoice(decision.decision_id, "approve", "workflow://approval/one"),
        _pending_readback(decision.decision_id),
    )
    with pytest.raises(PlanControlError) as error:
        repository.save_human_gate_attempt(attempt)
    assert error.value.code == "HUMAN_GATE_ATTEMPT_READBACK_INVALID"

    rejected_choice_repository = InMemoryPlanRepository(writer_generation="writer:one")
    rejected_choice_repository.save_human_decision(decision)
    rejected_choice_repository.save_human_gate_readback(
        handle,
        decision,
        HumanDecisionChoice(decision.decision_id, "reject", "workflow://approval/one"),
        approved,
    )
    with pytest.raises(PlanControlError) as error:
        rejected_choice_repository.save_human_gate_attempt(attempt)
    assert error.value.code == "HUMAN_GATE_ATTEMPT_READBACK_INVALID"


def test_inmemory_save_human_gate_attempt_requires_matching_generic_planning_attempt():
    from gwo_v8.human_gate import HumanDecisionChoice, HumanGateAttempt
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControlError
    from gwo_v8.planning_protocol import REPLANNING_OUTPUT_PROTOCOL_ID

    control, _repository, _gateway, _artifacts, _source, _host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    source = _approved_readback(control, handle, decision)
    attempt = HumanGateAttempt(
        decision_id=decision.decision_id,
        campaign=handle,
        predecessor_revision_digest=decision.plan_revision_digest,
        source_readback_digest=source.readback_digest,
        tracker_source_digest=source.tracker_source_digest,
        policy_witness_digest=source.policy_witness_digest,
        planning_action_id="replan:human:"
        + digest_value(
            {
                "decision_id": decision.decision_id,
                "source_readback_digest": source.readback_digest,
                "previous_revision_digest": decision.plan_revision_digest,
            }
        )[:24],
        planning_protocol_id=REPLANNING_OUTPUT_PROTOCOL_ID,
        state="planning_validated_successor",
        compilation_record_artifact_digest="f" * 64,
        activation_receipt_digest=None,
    )
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    repository.save_human_decision(decision)
    repository.save_human_gate_readback(
        handle,
        decision,
        HumanDecisionChoice(decision.decision_id, "approve", "workflow://approval/one"),
        source,
    )

    with pytest.raises(PlanControlError) as error:
        repository.save_human_gate_attempt(attempt)
    assert error.value.code == "HUMAN_GATE_ATTEMPT_READBACK_INVALID"
