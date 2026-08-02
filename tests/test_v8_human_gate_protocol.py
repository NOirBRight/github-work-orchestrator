from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


from gwo_v8._canonical import canonical_bytes, digest_bytes, digest_value
from gwo_v8.plan_control import CampaignHandle


def _required_source(required_change: str, *, source_kind: str | None = None):
    from gwo_v8.human_gate import RequiredDurableSourceChange

    return RequiredDurableSourceChange(
        required_change=required_change,
        source_kind=source_kind or RequiredDurableSourceChange.source_kind_for(required_change),
        predecessor_source_digest="a" * 64,
        required_subject=f"campaign:one:{required_change}",
        detail="Read the exact authoritative source before continuing.",
    )


def _decision(*, required_change: str = "authority"):
    from gwo_v8.human_gate import HumanDecisionRecord

    return HumanDecisionRecord(
        decision_id="decision:" + "1" * 24,
        campaign=CampaignHandle("owner/repository", "campaign:one"),
        classification_action_id="replan:classification:one",
        plan_revision_digest="b" * 64,
        evidence_digests=("c" * 64, "d" * 64),
        required_change=required_change,
        detail="The frozen authority is insufficient for the required effect.",
        required_source=_required_source(required_change),
    )


def _approved_source(*, decision_id: str = "decision:" + "1" * 24):
    from gwo_v8.human_gate import HumanSourceReadback

    approval = canonical_bytes(
        {
            "kind": "gwo.human-approval.v1",
            "decision_id": decision_id,
            "approval_state": "approved",
        }
    )
    tracker = canonical_bytes(
        {
            "kind": "gwo.human-tracker-source.v1",
            "campaign_key": "campaign:one",
            "membership": {"ticket_keys": ["issue:one"]},
        }
    )
    policy = canonical_bytes(
        {
            "kind": "gwo.policy-witness.v1",
            "replan": {
                "successor_revision_limit": 2,
                "repeated_invalidation_limit": 3,
            },
        }
    )
    return HumanSourceReadback(
        decision_id=decision_id,
        state="approved",
        approval_record_bytes=approval,
        tracker_source_bytes=tracker,
        policy_witness_bytes=policy,
        approval_record_digest=digest_bytes(approval),
        tracker_source_digest=digest_bytes(tracker),
        policy_witness_digest=digest_bytes(policy),
        source_change_digest="e" * 64,
        readback_digest="f" * 64,
        code="HUMAN_SOURCE_APPROVED",
    )


def _summary(phase: str):
    from gwo_v8.human_gate import HumanGateSummary

    return HumanGateSummary(
        phase=phase,
        decision_id="decision:" + "1" * 24,
        classification_action_id="replan:classification:one",
        required_change="authority",
        evidence_digests=("c" * 64,),
        required_source_kind="policy",
        reason_code="HUMAN_DECISION_REQUIRED",
        source_readback_digest=None,
        planning_action_id=None,
        predecessor_revision_digest="b" * 64,
        successor_revision_digest=None,
        successor_revisions_used=0,
        successor_revision_limit=2,
        repeated_invalidations=0,
        repeated_invalidation_limit=3,
    )


@pytest.mark.parametrize(
    ("required_change", "source_kind"),
    (
        ("new_ticket", "tracker"),
        ("acceptance", "tracker"),
        ("campaign_membership", "tracker"),
        ("product", "tracker"),
        ("authority", "policy"),
        ("replan_budget", "none"),
    ),
)
def test_required_change_has_one_closed_authoritative_source_kind(
    required_change: str, source_kind: str
):
    from gwo_v8.human_gate import RequiredDurableSourceChange

    source = _required_source(required_change)
    assert source.source_kind == source_kind
    assert RequiredDurableSourceChange.source_kind_for(required_change) == source_kind

    with pytest.raises(Exception) as error:
        _required_source(required_change, source_kind="tracker" if source_kind != "tracker" else "policy")
    assert error.value.code == "HUMAN_DECISION_RECORD_INVALID"


def test_decision_digest_binds_every_evidence_and_required_source_field():
    record = _decision()
    assert record.canonical()["kind"] == "gwo.human-decision.v1"
    assert record.digest == digest_value(record.canonical())
    assert type(record.canonical()["evidence_digests"]) is list
    assert type(record.canonical()["required_source"]) is dict

    assert replace(record, evidence_digests=("c" * 64,)).digest != record.digest
    assert replace(record, detail="different").digest != record.digest
    assert replace(record, required_source=replace(record.required_source, detail="different")).digest != record.digest
    assert replace(record, required_source=replace(record.required_source, predecessor_source_digest="f" * 64)).digest != record.digest

    restored = type(record).from_canonical(record.canonical())
    assert restored == record
    assert restored.canonical() == record.canonical()


def test_decision_rejects_duplicate_evidence_and_unknown_or_missing_fields():
    from gwo_v8.human_gate import HumanDecisionRecord

    canonical = _decision().canonical()
    duplicate = {**canonical, "evidence_digests": ["c" * 64, "c" * 64]}
    with pytest.raises(Exception) as error:
        HumanDecisionRecord.from_canonical(duplicate)
    assert error.value.code == "HUMAN_DECISION_RECORD_INVALID"

    with pytest.raises(Exception) as error:
        HumanDecisionRecord.from_canonical({**canonical, "unexpected": True})
    assert error.value.code == "HUMAN_DECISION_RECORD_INVALID"

    missing = dict(canonical)
    del missing["required_source"]
    with pytest.raises(Exception) as error:
        HumanDecisionRecord.from_canonical(missing)
    assert error.value.code == "HUMAN_DECISION_RECORD_INVALID"


def test_choice_is_opaque_and_has_no_source_actor_or_authority_payload():
    from gwo_v8.human_gate import HumanDecisionChoice

    choice = HumanDecisionChoice(
        decision_id="decision:" + "1" * 24,
        choice="approve",
        readback_ref="opaque://workflow/record/one",
    )
    assert choice.canonical() == {
        "kind": "gwo.human-decision-choice.v1",
        "decision_id": "decision:" + "1" * 24,
        "choice": "approve",
        "readback_ref": "opaque://workflow/record/one",
    }
    assert HumanDecisionChoice.from_canonical(choice.canonical()) == choice

    for value in (
        {**choice.canonical(), "actor": "human"},
        {**choice.canonical(), "source_bytes": "{}"},
        {**choice.canonical(), "choice": "yes"},
        {**choice.canonical(), "readback_ref": ""},
    ):
        with pytest.raises(Exception) as error:
            HumanDecisionChoice.from_canonical(value)
        assert error.value.code == "HUMAN_APPROVAL_INPUT_INVALID"


def test_approved_readback_requires_all_canonical_bytes_and_matching_digests():
    from gwo_v8.human_gate import HumanSourceReadback

    source = _approved_source()
    assert source.approved is True
    assert source.canonical()["kind"] == "gwo.human-source-readback.v1"
    assert HumanSourceReadback.from_canonical(source.canonical()) == source

    for field in (
        "approval_record_bytes",
        "tracker_source_bytes",
        "policy_witness_bytes",
        "approval_record_digest",
        "tracker_source_digest",
        "policy_witness_digest",
        "source_change_digest",
        "readback_digest",
    ):
        with pytest.raises(Exception) as error:
            HumanSourceReadback(**{**source.__dict__, field: None})
        assert error.value.code == "HUMAN_SOURCE_READBACK_INVALID"

    with pytest.raises(Exception) as error:
        HumanSourceReadback(**{**source.__dict__, "tracker_source_digest": "0" * 64})
    assert error.value.code == "HUMAN_SOURCE_READBACK_INVALID"

    with pytest.raises(Exception) as error:
        HumanSourceReadback(**{**source.__dict__, "policy_witness_bytes": b'{"not": "canonical"}'})
    assert error.value.code == "HUMAN_SOURCE_READBACK_INVALID"


def test_source_readback_maps_non_approved_states_without_approved_bytes():
    from gwo_v8.human_gate import HumanSourceReadback

    for state, code in (
        ("pending", "HUMAN_SOURCE_READBACK_PENDING"),
        ("rejected", "HUMAN_SOURCE_REJECTED"),
        ("incomplete", "HUMAN_SOURCE_READBACK_INCOMPLETE"),
        ("ambiguous", "HUMAN_SOURCE_AMBIGUOUS"),
        ("reverted", "HUMAN_SOURCE_REVERTED"),
        ("out_of_policy", "HUMAN_SOURCE_OUT_OF_POLICY"),
    ):
        source = HumanSourceReadback(
            decision_id="decision:" + "1" * 24,
            state=state,
            approval_record_bytes=None,
            tracker_source_bytes=None,
            policy_witness_bytes=None,
            approval_record_digest=None,
            tracker_source_digest=None,
            policy_witness_digest=None,
            source_change_digest=None,
            readback_digest="f" * 64,
            code=code,
        )
        assert source.approved is False
        assert source.reason_code == code


def test_replan_budget_policy_accepts_only_positive_exact_limits():
    from gwo_v8.human_gate import ReplanBudgetPolicy

    policy_core = {
        "kind": "gwo.policy-witness.v1",
        "replan": {
            "successor_revision_limit": 2,
            "repeated_invalidation_limit": 3,
        },
    }
    policy = {**policy_core, "digest": digest_value(policy_core)}
    budget = ReplanBudgetPolicy.from_policy(policy)
    assert budget.successor_revision_limit == 2
    assert budget.repeated_invalidation_limit == 3
    assert budget.policy_witness_digest == policy["digest"]
    assert ReplanBudgetPolicy.from_canonical(budget.canonical()) == budget

    for successor, repeated in ((0, 1), (1, 0), (True, 1), (1, False), (1.0, 2)):
        invalid = {
            **policy,
            "replan": {
                "successor_revision_limit": successor,
                "repeated_invalidation_limit": repeated,
            },
        }
        with pytest.raises(Exception) as error:
            ReplanBudgetPolicy.from_policy(invalid)
        assert error.value.code == "REPLAN_BUDGET_POLICY_INVALID"


def test_human_gate_summary_accepts_only_closed_inspect_phases():
    from gwo_v8.human_gate import HUMAN_GATE_PHASES, HumanGateSummary

    assert set(HUMAN_GATE_PHASES) == {
        "awaiting_human_choice",
        "awaiting_durable_tracker_policy_readback",
        "planning_validated_successor",
        "active_successor",
        "rejected_change",
        "budget_exhausted",
    }
    for phase in HUMAN_GATE_PHASES:
        summary = _summary(phase)
        assert HumanGateSummary.from_canonical(summary.canonical()) == summary

    with pytest.raises(Exception) as error:
        _summary("running")
    assert error.value.code == "HUMAN_GATE_SUMMARY_INVALID"


def test_attempt_binds_the_replanning_protocol_and_plan_readback_is_closed():
    from gwo_v8.human_gate import (
        HumanGateAttempt,
        HumanGateError,
        HumanGatePlanReadback,
    )
    from gwo_v8.planning_protocol import REPLANNING_OUTPUT_PROTOCOL_ID

    attempt = HumanGateAttempt(
        decision_id="decision:" + "1" * 24,
        campaign=CampaignHandle("owner/repository", "campaign:one"),
        predecessor_revision_digest="b" * 64,
        source_readback_digest="c" * 64,
        tracker_source_digest="d" * 64,
        policy_witness_digest="e" * 64,
        planning_action_id="replan:human:111111111111111111111111",
        planning_protocol_id=REPLANNING_OUTPUT_PROTOCOL_ID,
        state="planning_validated_successor",
        compilation_record_artifact_digest=None,
        activation_receipt_digest=None,
    )
    assert HumanGateAttempt.from_canonical(attempt.canonical()) == attempt
    with pytest.raises(HumanGateError) as error:
        replace(attempt, planning_protocol_id="campaign.planning-output.v1")
    assert error.value.code == "HUMAN_GATE_ATTEMPT_INVALID"

    readback = HumanGatePlanReadback(summary=_summary("awaiting_human_choice"))
    assert HumanGatePlanReadback.from_canonical(readback.canonical()) == readback
