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
    AuditFailureKind,
    AuditFailureRoute,
    CandidateAuditReport,
    CandidateAcceptanceFacts,
    CandidateCheckEvidence,
    CandidateDiffEntryV1,
    CandidateDiffRecordV1,
    CandidateGate,
    CandidateGateError,
    CandidateGateParent,
    CandidateGateStatus,
    CandidateIdentity,
    CandidateReadback,
    CandidateReceipt,
    DeterministicAuditFailure,
    FormalReviewFinding,
    FormalReviewResult,
    PlanInvalidationEvidence,
    RepairPacket,
    RepairVerificationEvidence,
    RepairVerificationResult,
    ReviewFindingDisposition,
    ReviewSubject,
)
from gwo_v8.runtime_gateway import (  # noqa: E402
    CapabilityPolicy,
    CapabilityPolicyProof,
    PlanInvalidationReceipt,
    WorkRunPurpose,
    WorkRunSubject,
)


PROTOCOL_PATH = "c3JjL21haW4ucHk"
OUTSIDE_PATH = "c3JjL291dHNpZGUucHk"


class _Reporter:
    def __init__(self):
        self.calls = []

    def report_plan_invalidation(self, subject, evidence, report):
        self.calls.append((subject, evidence, report))
        proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="a" * 64,
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


class _DiffStore:
    def __init__(self, records=()):
        self.records = {record.digest: record for record in records}

    def put(self, record):
        self.records[record.digest] = record
        return record.digest

    def read(self, digest):
        return self.records.get(digest)


class _Reader:
    def __init__(self, readback):
        self.readback = readback
        self.calls = []

    def read_candidate(self, repository, reported_reference):
        self.calls.append((repository, reported_reference))
        return self.readback


class _Checks:
    def run(self, _parent, readback):
        check_id = "check:unit"
        return (
            CandidateCheckEvidence(
                check_id=check_id,
                candidate_tree_oid=readback.candidate.candidate_tree_oid,
                outcome="passed",
                definition_digest="a" * 64,
                observation_digest=digest_value(
                    {
                        "kind": "candidate_check_observation.v1",
                        "check_id": check_id,
                        "candidate_tree_oid": readback.candidate.candidate_tree_oid,
                        "diff_record_digest": readback.diff_record.digest,
                        "outcome": "passed",
                        "failure_digest": None,
                    }
                ),
            ),
        )


class _DuplicateChecks:
    def run(self, _parent, readback):
        passed = _Checks().run(_parent, readback)[0]
        failure = DeterministicAuditFailure(
            kind=AuditFailureKind.AFFECTED_CHECK,
            route=AuditFailureRoute.ORDINARY_UNAUTHORIZED,
            code="CHECK_FAILED",
            detail="the duplicate check deliberately failed",
        )
        failed = CandidateCheckEvidence(
            check_id=passed.check_id,
            candidate_tree_oid=passed.candidate_tree_oid,
            outcome="failed",
            definition_digest=passed.definition_digest,
            observation_digest=digest_value(
                {
                    "kind": "candidate_check_observation.v1",
                    "check_id": passed.check_id,
                    "candidate_tree_oid": passed.candidate_tree_oid,
                    "diff_record_digest": readback.diff_record.digest,
                    "outcome": "failed",
                    "failure_digest": failure.digest,
                }
            ),
            failure=failure,
        )
        # Put the failed item first so a dict collapse would mask it with the
        # later passing item.
        return (failed, passed)


class _Policy:
    def derive(self, _parent, _readback, _checks):
        return AssuranceRequirement(
            policy_id="policy:candidate-assurance",
            policy_version="1",
            mode=AssuranceMode.STANDARD,
            required_check_ids=("check:unit",),
            standards=("standard:repository",),
        )


class _Reviewer:
    capability_policy_proof = CapabilityPolicyProof(
        capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
        authority_record_digest="9" * 64,
    )

    def __init__(self):
        self.calls = 0

    def review(self, _action):
        self.calls += 1
        raise AssertionError("Repair Verification must not invoke Formal Review")


class _Verifier:
    capability_policy_proof = CapabilityPolicyProof(
        capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
        authority_record_digest="a" * 64,
    )

    def __init__(self):
        self.calls = self.requests = []

    def verify(self, request):
        self.requests.append(request)
        return RepairVerificationResult(
            request_digest=request.digest,
            accepted=True,
            details=("repair verifier accepted the bounded delta",),
        )


def _record(
    *,
    candidate_commit_oid,
    candidate_tree_oid,
    path_tokens,
    base_commit_oid="1" * 40,
    base_tree_oid="2" * 40,
):
    return CandidateDiffRecordV1(
        schema_version="CandidateDiffRecordV1",
        repository_object_format="sha1",
        base_commit_oid=base_commit_oid,
        base_tree_oid=base_tree_oid,
        candidate_commit_oid=candidate_commit_oid,
        candidate_tree_oid=candidate_tree_oid,
        entries=tuple(
            CandidateDiffEntryV1(
                old_path=None,
                new_path=path,
                change_kind="add",
                old_mode=None,
                new_mode="100644",
                old_object_type=None,
                new_object_type="blob",
                old_oid=None,
                new_oid=("3" if path == PROTOCOL_PATH else "4") * 40,
            )
            for path in path_tokens
        ),
    )


def _make_fixture(*, scope_escape=False, check_runner=None, base_mismatch=False):
    runtime_subject = WorkRunSubject(
        repository="owner/repository",
        campaign_key="campaign:one",
        campaign_handle="campaign-handle:one",
        plan_revision_digest="5" * 64,
        work_run_key="work-run:one",
        ticket_key="issue:114",
        purpose=WorkRunPurpose.implementation(),
        prompt_artifact_digest="6" * 64,
        authority_subtree_digest="7" * 64,
        stable_action_id="binding:one",
    )
    parent = CandidateGateParent(
        runtime_subject=runtime_subject,
        ticket_contract_digest="8" * 64,
        policy_witness_digest="9" * 64,
        workspace_identity="workspace:one",
    )
    requirement = _Policy().derive(parent, None, ())
    prior_record = _record(
        candidate_commit_oid="a" * 40,
        candidate_tree_oid="b" * 40,
        path_tokens=(PROTOCOL_PATH,),
    )
    prior_candidate = CandidateIdentity(
        reported_reference="refs/heads/repaired",
        base_commit_oid=prior_record.base_commit_oid,
        base_tree_oid=prior_record.base_tree_oid,
        candidate_commit_oid=prior_record.candidate_commit_oid,
        candidate_tree_oid=prior_record.candidate_tree_oid,
        changed_path_tokens=prior_record.changed_path_tokens,
    )
    prior_readback = CandidateReadback(
        repository=runtime_subject.repository,
        candidate=prior_candidate,
        diff_record=prior_record,
    )
    prior_receipt = CandidateReceipt.from_readback(
        parent=parent,
        reported_reference=prior_candidate.reported_reference,
        readback=prior_readback,
    )
    prior_check = _Checks().run(parent, prior_readback)[0]
    prior_audit = CandidateAuditReport(
        parent_digest=parent.digest,
        candidate=prior_candidate,
        diff_record=prior_record,
        standards=requirement.standards,
        check_evidence_digests=(prior_check.digest,),
        assurance_requirement=requirement.digest,
    )
    subject = ReviewSubject.from_assurance(
        parent=parent,
        candidate_receipt=prior_receipt,
        readback=prior_readback,
        audit=prior_audit,
        checks=(prior_check,),
        requirement=requirement,
    )
    finding = FormalReviewFinding(
        parent_digest=parent.digest,
        candidate_digest=prior_candidate.digest,
        review_subject_digest=subject.digest,
        finding_id="finding:repair",
        severity="hard",
        code="CHECK_REQUIRES_REPAIR",
        message="repair the bounded Candidate change",
    )
    review_result = FormalReviewResult(
        subject_digest=subject.digest,
        findings=(finding,),
    )
    packet = RepairPacket.from_review(
        parent=parent,
        candidate_receipt=prior_receipt,
        subject=subject,
        result=review_result,
        allowed_path_tokens=prior_record.changed_path_tokens,
        required_check_ids=requirement.required_check_ids,
        repair_instructions=("fix the named finding",),
    )
    complete_ledger = packet.finding_ledger.with_disposition(
        finding_id=finding.finding_id,
        disposition=ReviewFindingDisposition.FIXED,
        reason="the bounded repair is present",
    )
    complete_packet = replace(
        packet,
        finding_ledger=complete_ledger,
        packet_digest=None,
    )

    repaired_paths = (PROTOCOL_PATH, OUTSIDE_PATH) if scope_escape else (PROTOCOL_PATH,)
    repaired_record = _record(
        candidate_commit_oid="c" * 40,
        candidate_tree_oid="d" * 40,
        path_tokens=tuple(sorted(repaired_paths)),
        base_commit_oid=("e" * 40 if base_mismatch else prior_record.base_commit_oid),
        base_tree_oid=("f" * 40 if base_mismatch else prior_record.base_tree_oid),
    )
    repaired_candidate = CandidateIdentity(
        reported_reference="refs/heads/repaired",
        base_commit_oid=repaired_record.base_commit_oid,
        base_tree_oid=repaired_record.base_tree_oid,
        candidate_commit_oid=repaired_record.candidate_commit_oid,
        candidate_tree_oid=repaired_record.candidate_tree_oid,
        changed_path_tokens=repaired_record.changed_path_tokens,
    )
    repaired_readback = CandidateReadback(
        repository=runtime_subject.repository,
        candidate=repaired_candidate,
        diff_record=repaired_record,
    )
    verifier = _Verifier()
    formal_reviewer = _Reviewer()
    gate = CandidateGate(
        invalidation_reporter=_Reporter(),
        candidate_reader=_Reader(repaired_readback),
        formal_reviewer=formal_reviewer,
        repair_verifier=verifier,
        check_runner=_Checks() if check_runner is None else check_runner,
        assurance_policy=_Policy(),
        acceptance_facts=CandidateAcceptanceFacts(
            target_branch="main",
            integration_node_key="integration:issue:114",
            accepted_sequence=1,
            check_environment_digest="e" * 64,
            delivery_identity_digest="f" * 64,
            protected_surfaces=("protected/path",),
        ),
        diff_artifacts=_DiffStore((prior_record,)),
    )
    return gate, verifier, formal_reviewer, parent, packet, complete_packet, repaired_candidate


@pytest.fixture
def repair_gate():
    gate, verifier, _reviewer, parent, _packet, packet, candidate = _make_fixture()
    return gate, verifier, parent, packet, candidate


@pytest.fixture
def unresolved_repair():
    gate, verifier, _reviewer, parent, packet, _complete, candidate = _make_fixture()
    return gate, verifier, parent, packet, candidate


@pytest.fixture
def scope_escape_repair():
    gate, verifier, _reviewer, parent, _packet, packet, candidate = _make_fixture(
        scope_escape=True
    )
    return gate, verifier, parent, packet, candidate


def test_verify_repair_uses_repair_verify_not_formal_review(repair_gate):
    gate, verifier, parent, packet, candidate = repair_gate
    result = gate.verify_repair(parent, packet, candidate)
    assert result.status == CandidateGateStatus.REPAIR_ACCEPTED
    assert verifier.requests[0].review_subject.action_kind == "repair_verify"
    assert verifier.requests[0].review_subject.prior_review_subject_digest == (
        packet.prior_review_subject_digest
    )
    assert verifier.requests[0].review_subject.repair_packet_digest == packet.digest
    assert verifier.requests[0].review_subject.repair_delta_digest == (
        verifier.requests[0].repair_delta.digest
    )


def test_repair_requires_disposition_for_every_prior_finding(unresolved_repair):
    gate, _verifier, parent, packet, candidate = unresolved_repair
    with pytest.raises(CandidateGateError) as raised:
        gate.verify_repair(parent, packet, candidate)
    assert raised.value.code == "CANDIDATE_GATE_REPAIR_LEDGER_INVALID"


def test_repair_scope_escape_fails_before_verifier(scope_escape_repair):
    gate, repair_verifier, parent, packet, candidate = scope_escape_repair
    result = gate.verify_repair(parent, packet, candidate)
    repair_evidence = next(
        item
        for item in result.evidence
        if type(item) is RepairVerificationEvidence
    )
    plan_evidence = next(
        item for item in result.evidence if type(item) is PlanInvalidationEvidence
    )

    assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    assert repair_evidence.scope_escape_paths == (OUTSIDE_PATH,)
    assert plan_evidence.source_kind == "repair_verification"
    assert plan_evidence.source_evidence_digest == repair_evidence.digest
    assert result.plan_invalidation_receipt is not None
    assert result.plan_invalidation_report is not None
    assert (
        result.plan_invalidation_receipt.report_digest
        == result.plan_invalidation_report.digest
    )
    assert repair_verifier.calls == []


def test_repair_rejects_duplicate_check_ids_before_verifier():
    gate, verifier, _reviewer, parent, _packet, packet, candidate = _make_fixture(
        check_runner=_DuplicateChecks(),
    )
    with pytest.raises(CandidateGateError) as raised:
        gate.verify_repair(parent, packet, candidate)
    assert raised.value.code == "CANDIDATE_GATE_REPAIR_CHECK_INVALID"
    assert verifier.requests == []


def test_repair_rejects_changed_base_before_verifier():
    gate, verifier, _reviewer, parent, _packet, packet, candidate = _make_fixture(
        base_mismatch=True,
    )
    with pytest.raises(CandidateGateError) as raised:
        gate.verify_repair(parent, packet, candidate)
    assert raised.value.code == "CANDIDATE_GATE_REPAIR_BASE_INVALID"
    assert verifier.requests == []


def test_repair_request_rejects_parent_cross_binding(repair_gate):
    gate, verifier, parent, packet, candidate = repair_gate
    gate.verify_repair(parent, packet, candidate)
    request = verifier.requests[0]
    with pytest.raises(CandidateGateError) as raised:
        replace(request, parent_digest="a" * 64, request_digest=None)
    assert raised.value.code == "CANDIDATE_GATE_REPAIR_REQUEST_INVALID"


def test_repair_request_rejects_subject_candidate_cross_binding(repair_gate):
    gate, verifier, parent, packet, candidate = repair_gate
    gate.verify_repair(parent, packet, candidate)
    request = verifier.requests[0]
    subject = replace(
        request.review_subject,
        candidate_digest="e" * 64,
        subject_digest=None,
    )
    with pytest.raises(CandidateGateError) as raised:
        replace(request, review_subject=subject, request_digest=None)
    assert raised.value.code == "CANDIDATE_GATE_REPAIR_REQUEST_INVALID"


def test_repair_request_rejects_delta_candidate_cross_binding(repair_gate):
    gate, verifier, parent, packet, candidate = repair_gate
    gate.verify_repair(parent, packet, candidate)
    request = verifier.requests[0]
    delta = replace(
        request.repair_delta,
        repaired_candidate_tree_oid="e" * 40,
        delta_digest=None,
    )
    subject = replace(
        request.review_subject,
        repair_delta_digest=delta.digest,
        subject_digest=None,
    )
    with pytest.raises(CandidateGateError) as raised:
        replace(
            request,
            review_subject=subject,
            repair_delta=delta,
            request_digest=None,
        )
    assert raised.value.code == "CANDIDATE_GATE_REPAIR_REQUEST_INVALID"


def test_repair_request_rejects_receipt_base_and_reference_cross_binding(repair_gate):
    gate, verifier, parent, packet, candidate = repair_gate
    gate.verify_repair(parent, packet, candidate)
    request = verifier.requests[0]
    receipt = replace(
        request.candidate_receipt,
        base_commit_oid="e" * 40,
        reported_reference="refs/heads/other",
        receipt_digest=None,
    )
    subject = replace(
        request.review_subject,
        candidate_receipt_digest=receipt.digest,
        subject_digest=None,
    )
    with pytest.raises(CandidateGateError) as raised:
        replace(
            request,
            candidate_receipt=receipt,
            review_subject=subject,
            request_digest=None,
        )
    assert raised.value.code == "CANDIDATE_GATE_REPAIR_REQUEST_INVALID"


def test_repair_request_rejects_subject_base_cross_binding(repair_gate):
    gate, verifier, parent, packet, candidate = repair_gate
    gate.verify_repair(parent, packet, candidate)
    request = verifier.requests[0]
    subject = replace(
        request.review_subject,
        base_tree_oid="e" * 40,
        subject_digest=None,
    )
    with pytest.raises(CandidateGateError) as raised:
        replace(request, review_subject=subject, request_digest=None)
    assert raised.value.code == "CANDIDATE_GATE_REPAIR_REQUEST_INVALID"


def test_repair_request_rejects_check_evidence_digest_mismatch(repair_gate):
    gate, verifier, parent, packet, candidate = repair_gate
    gate.verify_repair(parent, packet, candidate)
    request = verifier.requests[0]
    subject = replace(
        request.review_subject,
        check_evidence_digests=("e" * 64,),
        subject_digest=None,
    )
    with pytest.raises(CandidateGateError) as raised:
        replace(request, review_subject=subject, request_digest=None)
    assert raised.value.code == "CANDIDATE_GATE_REPAIR_REQUEST_INVALID"


def test_repair_request_rejects_duplicate_check_evidence_digests(repair_gate):
    gate, verifier, parent, packet, candidate = repair_gate
    gate.verify_repair(parent, packet, candidate)
    request = verifier.requests[0]
    with pytest.raises(CandidateGateError) as raised:
        replace(
            request,
            required_check_evidence=(request.required_check_evidence[0],) * 2,
            request_digest=None,
        )
    assert raised.value.code == "CANDIDATE_GATE_REPAIR_REQUEST_INVALID"
