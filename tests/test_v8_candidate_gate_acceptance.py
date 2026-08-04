from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8._canonical import digest_value  # noqa: E402
from gwo_v8.candidate_gate import (  # noqa: E402
    AssuranceMode,
    AssuranceRequirement,
    CandidateAuditReport,
    CandidateCheckEvidence,
    CandidateDiffEntryV1,
    CandidateDiffRecordV1,
    CandidateAcceptanceFacts,
    CandidateGate,
    CandidateGateError,
    CandidateGateParent,
    CandidateGateResult,
    CandidateGateStatus,
    CandidateIdentity,
    CandidateReadback,
    FormalReviewFinding,
    FormalReviewResult,
)
from gwo_v8.runtime_gateway import (  # noqa: E402
    CapabilityPolicy,
    CapabilityPolicyProof,
    PlanInvalidationReceipt,
    WorkRunPurpose,
    WorkRunSubject,
)


class _Reader:
    def __init__(self, readback):
        self.readback = readback

    def read_candidate(self, _repository, _reported_reference):
        return self.readback


class _Reporter:
    def report_plan_invalidation(self, _subject, _evidence, _report):
        raise AssertionError("accepted Candidate fixtures do not report invalidation")


class _RecordingReporter:
    def __init__(self):
        self.calls = 0

    def report_plan_invalidation(self, subject, evidence, report):
        self.calls += 1
        proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="9" * 64,
        )
        observation = {
            "kind": "plan_invalidation_observation.v1",
            "repository": report.repository,
            "campaign_key": report.campaign_key,
            "plan_revision_digest": report.plan_revision_digest,
            "ticket_key": report.ticket_key,
            "work_run_key": report.work_run_key,
            "runtime_binding_id": report.runtime_binding_id,
            "authority_subtree_digest": report.authority_subtree_digest,
            "reporter_role": report.reporter_role,
            "report_digest": report.digest,
            "evidence_digest": report.evidence_digest,
            "dedup_identity": report.dedup_identity,
            "invalidated_obligation": report.invalidated_obligation,
            "required_effects": list(report.required_effects),
            "workspace_identity": report.workspace_identity,
            "source_evidence_digests": list(evidence.source_evidence_digests),
        }
        return PlanInvalidationReceipt(
            report_digest=report.digest,
            receipt_digest=digest_value(
                {
                    "kind": "plan_invalidation_receipt.v1",
                    "report_digest": report.digest,
                    "subject_digest": subject.digest,
                    "authority_record_digest": proof.authority_record_digest,
                }
            ),
            capability_policy_proof=proof,
            observation=observation,
        )


class _Reviewer:
    capability_policy_proof = CapabilityPolicyProof(
        capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
        authority_record_digest="9" * 64,
    )

    def __init__(self, *, scope_escape=False):
        self.scope_escape = scope_escape

    def review(self, request):
        subject = getattr(request, "subject", request)
        if not self.scope_escape:
            return FormalReviewResult(subject_digest=subject.digest)
        finding = FormalReviewFinding(
            parent_digest=subject.parent_digest,
            candidate_digest=subject.candidate_digest,
            review_subject_digest=subject.digest,
            finding_id="finding:scope",
            severity="hard",
            code="OWNER_OUTSIDE_TICKET",
            message="The required owner is outside the frozen Ticket.",
            scope_escape=True,
            invalidated_obligation="issue:114 owner",
            required_effects=("owner.persist.v1",),
        )
        return FormalReviewResult(
            subject_digest=subject.digest,
            findings=(finding,),
        )


class _Checks:
    def run(self, _parent, readback):
        check_id = "check:unit"
        outcome = "passed"
        return (
            CandidateCheckEvidence(
                check_id=check_id,
                candidate_tree_oid=readback.candidate.candidate_tree_oid,
                outcome=outcome,
                definition_digest="a" * 64,
                observation_digest=digest_value(
                    {
                        "kind": "candidate_check_observation.v1",
                        "check_id": check_id,
                        "candidate_tree_oid": readback.candidate.candidate_tree_oid,
                        "diff_record_digest": readback.diff_record.digest,
                        "outcome": outcome,
                        "failure_digest": None,
                    }
                ),
            ),
        )


class _Policy:
    def derive(self, _parent, _readback, _checks):
        return AssuranceRequirement(
            policy_id="policy:candidate-assurance",
            policy_version="1",
            mode=AssuranceMode.STANDARD,
            required_check_ids=("check:unit",),
            standards=("standard:repository",),
        )


def _parent_and_readback():
    subject = WorkRunSubject(
        repository="owner/repository",
        campaign_key="campaign:one",
        campaign_handle="campaign-handle:one",
        plan_revision_digest="1" * 64,
        work_run_key="work-run:one",
        ticket_key="issue:114",
        purpose=WorkRunPurpose.implementation(),
        prompt_artifact_digest="2" * 64,
        authority_subtree_digest="3" * 64,
        stable_action_id="binding:one",
    )
    parent = CandidateGateParent(
        runtime_subject=subject,
        ticket_contract_digest="4" * 64,
        policy_witness_digest="5" * 64,
        workspace_identity="workspace:one",
    )
    record = CandidateDiffRecordV1(
        schema_version="CandidateDiffRecordV1",
        repository_object_format="sha1",
        base_commit_oid="a" * 40,
        base_tree_oid="b" * 40,
        candidate_commit_oid="c" * 40,
        candidate_tree_oid="d" * 40,
        entries=(
            CandidateDiffEntryV1(
                old_path=None,
                new_path="c3JjL21haW4ucHk",
                change_kind="add",
                old_mode=None,
                new_mode="100644",
                old_object_type=None,
                new_object_type="blob",
                old_oid=None,
                new_oid="e" * 40,
            ),
        ),
    )
    candidate = CandidateIdentity(
        reported_reference="refs/heads/candidate",
        base_commit_oid=record.base_commit_oid,
        base_tree_oid=record.base_tree_oid,
        candidate_commit_oid=record.candidate_commit_oid,
        candidate_tree_oid=record.candidate_tree_oid,
        changed_path_tokens=record.changed_path_tokens,
    )
    return parent, CandidateReadback(
        repository=subject.repository,
        candidate=candidate,
        diff_record=record,
    )


def _gate(readback, *, reviewer=None):
    parent, _ = _parent_and_readback()
    gate = CandidateGate(
        invalidation_reporter=_Reporter(),
        candidate_reader=_Reader(readback),
        formal_reviewer=_Reviewer() if reviewer is None else reviewer,
        check_runner=_Checks(),
        assurance_policy=_Policy(),
        acceptance_facts=CandidateAcceptanceFacts(
            target_branch="main",
            integration_node_key="integration:issue:114",
            accepted_sequence=1,
            check_environment_digest="6" * 64,
            delivery_identity_digest="7" * 64,
            protected_surfaces=("protected/path",),
        ),
    )
    return gate, parent


@pytest.fixture
def accepted_candidate_result():
    parent, readback = _parent_and_readback()
    gate, _ = _gate(readback)
    return gate.gate_candidate(parent, "refs/heads/candidate")


@pytest.fixture
def scope_escape_result():
    parent, readback = _parent_and_readback()
    reporter = _RecordingReporter()
    audit = CandidateAuditReport(
        parent_digest=parent.digest,
        candidate=readback.candidate,
    )
    result = CandidateGate(
        invalidation_reporter=reporter,
        formal_reviewer=_Reviewer(scope_escape=True),
    ).audit_candidate(parent, audit)
    return result


@pytest.fixture
def gate_with_mismatch():
    parent, readback = _parent_and_readback()
    mismatched_candidate = replace(
        readback.candidate,
        reported_reference="refs/heads/authoritative",
        candidate_digest=None,
    )
    mismatched_readback = CandidateReadback(
        repository=readback.repository,
        candidate=mismatched_candidate,
        diff_record=readback.diff_record,
    )
    return _gate(mismatched_readback)


def test_candidate_result_distinguishes_private_and_accepted_receipts(
    accepted_candidate_result,
):
    assert accepted_candidate_result.candidate_receipt is not None
    assert accepted_candidate_result.accepted_candidate_receipt is not None
    assert accepted_candidate_result.accepted_candidate_receipt.candidate_receipt_digest == (
        accepted_candidate_result.candidate_receipt.digest
    )
    assert accepted_candidate_result.candidate_diff_record is not None
    assert accepted_candidate_result.assurance_requirement is not None
    assert accepted_candidate_result.review_subject is not None


def test_scope_escape_routes_plan_invalidation_without_classification(
    scope_escape_result,
):
    assert scope_escape_result.status == CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    assert scope_escape_result.classification is None
    assert scope_escape_result.plan_invalidation_report is not None
    assert scope_escape_result.review_subject is None
    assert scope_escape_result.accepted_candidate_receipt is None


def test_mismatched_authoritative_readback_is_rejected(gate_with_mismatch):
    gate, parent = gate_with_mismatch
    with pytest.raises(CandidateGateError) as raised:
        gate.gate_candidate(parent, "refs/heads/candidate")
    assert raised.value.code == "CANDIDATE_GATE_EVIDENCE_STALE"


def test_plan_invalidation_requires_the_complete_readback_pair():
    with pytest.raises(CandidateGateError) as raised:
        CandidateGateResult(
            status=CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
            evidence=(),
        )
    assert raised.value.code == "CANDIDATE_GATE_EVIDENCE_INVALID"


def test_public_acceptance_requires_the_accepted_candidate_receipt(
    accepted_candidate_result,
):
    with pytest.raises(CandidateGateError) as raised:
        replace(accepted_candidate_result, accepted_candidate_receipt=None)
    assert raised.value.code == "CANDIDATE_GATE_ACCEPTANCE_INVALID"


def test_accepted_candidate_receipt_must_bind_every_delivery_identity(
    accepted_candidate_result,
):
    receipt = replace(
        accepted_candidate_result.accepted_candidate_receipt,
        candidate_receipt_digest="0" * 64,
    )
    with pytest.raises(CandidateGateError) as raised:
        replace(accepted_candidate_result, accepted_candidate_receipt=receipt)
    assert raised.value.code == "CANDIDATE_GATE_ACCEPTANCE_INVALID"
