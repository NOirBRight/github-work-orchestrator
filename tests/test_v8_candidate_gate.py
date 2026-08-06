from __future__ import annotations

from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def test_ticket_unsatisfiable_audit_reports_typed_invalidation_before_review():
    from gwo_v8.candidate_gate import (
        AuditFailureKind,
        AuditFailureRoute,
        CandidateAuditReport,
        CandidateGate,
        CandidateGateParent,
        CandidateIdentity,
        DeterministicAuditFailure,
    )
    from gwo_v8._canonical import digest_value
    from gwo_v8.runtime_gateway import WorkRunPurpose, WorkRunSubject
    from gwo_v8.runtime_gateway import (
        CapabilityPolicy,
        CapabilityPolicyProof,
        PlanInvalidationReceipt,
    )

    parent = CandidateGateParent(
        runtime_subject=WorkRunSubject(
            repository="owner/repository",
            campaign_key="campaign:one",
            campaign_handle="campaign-handle:one",
            plan_revision_digest="a" * 64,
            work_run_key="work-run:one",
            ticket_key="issue:one",
            purpose=WorkRunPurpose.implementation(),
            prompt_artifact_digest="b" * 64,
            authority_subtree_digest="c" * 64,
            stable_action_id="binding:one",
        ),
        ticket_contract_digest="d" * 64,
        policy_witness_digest="e" * 64,
        workspace_identity="workspace:one",
    )
    audit = CandidateAuditReport(
        parent_digest=parent.digest,
        candidate=CandidateIdentity(
            reported_reference="refs/heads/candidate",
            base_commit_oid="1" * 40,
            base_tree_oid="2" * 40,
            candidate_commit_oid="3" * 40,
            candidate_tree_oid="4" * 40,
            changed_paths=("src/protocol.py",),
        ),
        failures=(
            DeterministicAuditFailure(
                kind=AuditFailureKind.PROTECTED_EFFECT,
                route=AuditFailureRoute.TICKET_UNSATISFIABLE,
                code="PERSISTENT_PROTOCOL_REQUIRED",
                detail="The frozen Ticket cannot safely satisfy the required protocol.",
                invalidated_obligation="issue:one acceptance",
                required_effects=("protocol.persist.v1",),
            ),
        ),
    )

    class InvalidationPort:
        def report_plan_invalidation(self, subject, evidence, report):
            self.calls = getattr(self, "calls", 0) + 1
            self.last = (subject, evidence, report)
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

    class Reviewer:
        def __init__(self):
            self.calls = 0

        def review(self, request):
            self.calls += 1
            raise AssertionError("deterministic invalidation must not invoke review")

    port = InvalidationPort()
    reviewer = Reviewer()
    result = CandidateGate(
        invalidation_reporter=port,
        formal_reviewer=reviewer,
    ).audit_candidate(parent, audit)

    assert result.plan_invalidation_receipt is not None
    assert result.plan_invalidation_receipt.__class__.__name__ == "PlanInvalidationReceipt"
    assert result.classification is None
    assert reviewer.calls == 0
    assert port.calls == 1
    assert result.plan_invalidation_report == port.last[2]
    assert result.evidence[0].kind == "candidate_audit"
    assert result.evidence[1].kind == "plan_invalidation"


def _parent(*, purpose=None):
    from gwo_v8.candidate_gate import CandidateGateParent
    from gwo_v8.runtime_gateway import WorkRunPurpose, WorkRunSubject

    if purpose is None:
        purpose = WorkRunPurpose.implementation()
    return CandidateGateParent(
        runtime_subject=WorkRunSubject(
            repository="owner/repository",
            campaign_key="campaign:one",
            campaign_handle="campaign-handle:one",
            plan_revision_digest="a" * 64,
            work_run_key="work-run:one",
            ticket_key="issue:one",
            purpose=purpose,
            prompt_artifact_digest="b" * 64,
            authority_subtree_digest="c" * 64,
            stable_action_id="binding:one",
        ),
        ticket_contract_digest="d" * 64,
        policy_witness_digest="e" * 64,
        workspace_identity="workspace:one",
    )


def _clean_audit(parent):
    from gwo_v8.candidate_gate import CandidateAuditReport, CandidateIdentity

    return CandidateAuditReport(
        parent_digest=parent.digest,
        candidate=CandidateIdentity(
            reported_reference="refs/heads/candidate",
            base_commit_oid="1" * 40,
            base_tree_oid="2" * 40,
            candidate_commit_oid="3" * 40,
            candidate_tree_oid="4" * 40,
            changed_paths=("src/protocol.py",),
        ),
    )


def _repair_reader(parent, candidate):
    from gwo_v8.candidate_gate import (
        CandidateDiffEntryV1,
        CandidateDiffRecordV1,
        CandidateReadback,
    )

    diff = CandidateDiffRecordV1(
        repository=parent.runtime_subject.repository,
        object_format="sha1",
        base_commit_oid=candidate.base_commit_oid,
        base_tree_oid=candidate.base_tree_oid,
        candidate_commit_oid=candidate.candidate_commit_oid,
        candidate_tree_oid=candidate.candidate_tree_oid,
        entries=tuple(
            CandidateDiffEntryV1(
                side="candidate",
                path=path,
                mode="100644",
                object_type="blob",
                object_oid=("a" if index % 2 else "b") * 40,
            )
            for index, path in enumerate(candidate.changed_paths)
        ),
    )
    readback = CandidateReadback(
        repository=parent.runtime_subject.repository,
        candidate=candidate,
        diff_record=diff,
    )

    class Reader:
        def read_candidate(self, _repository, reference):
            assert reference == candidate.reported_reference
            return readback

    return Reader()


class _RecordingPort:
    def __init__(self):
        self.calls = 0
        self.reports = []

    def report_plan_invalidation(self, subject, evidence, report):
        from gwo_v8._canonical import digest_value
        from gwo_v8.runtime_gateway import (
            CapabilityPolicy,
            CapabilityPolicyProof,
            PlanInvalidationReceipt,
        )

        self.calls += 1
        self.reports.append(report)
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


def test_unproven_formal_reviewer_capability_fails_before_review_call():
    from gwo_v8.candidate_gate import CandidateGate, CandidateGateError
    from gwo_v8.runtime_gateway import CapabilityPolicy, CapabilityPolicyProof

    class InvalidationPort:
        def report_plan_invalidation(self, _subject, _evidence, _report):
            raise AssertionError("clean Candidate must not report invalidation")

    class Reviewer:
        capability_policy_proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=True),
            authority_record_digest="9" * 64,
        )

        def __init__(self):
            self.calls = 0

        def review(self, _request):
            self.calls += 1
            raise AssertionError("capability failure must precede review")

    reviewer = Reviewer()
    with pytest.raises(CandidateGateError) as raised:
        CandidateGate(
            invalidation_reporter=InvalidationPort(),
            formal_reviewer=reviewer,
        ).audit_candidate(_parent(), _clean_audit(_parent()))

    assert raised.value.code == "CANDIDATE_GATE_CAPABILITY_PROOF_INVALID"
    assert reviewer.calls == 0


def test_ordinary_unauthorized_candidate_is_rejected_without_replanning_or_review():
    from gwo_v8.candidate_gate import (
        AuditFailureKind,
        AuditFailureRoute,
        CandidateGate,
        CandidateGateStatus,
        CandidateAuditReport,
        DeterministicAuditFailure,
    )

    parent = _parent()
    audit = CandidateAuditReport(
        parent_digest=parent.digest,
        candidate=_clean_audit(parent).candidate,
        failures=(
            DeterministicAuditFailure(
                kind=AuditFailureKind.SCOPE,
                route=AuditFailureRoute.ORDINARY_UNAUTHORIZED,
                code="OUTSIDE_TICKET_SCOPE",
                detail="Candidate changed an unapproved path.",
            ),
        ),
    )

    class Reviewer:
        capability_policy_proof = None

        def review(self, _request):
            raise AssertionError("ordinary rejection must stop before Review")

    port = _RecordingPort()
    result = CandidateGate(
        invalidation_reporter=port,
        formal_reviewer=Reviewer(),
    ).audit_candidate(parent, audit)

    assert result.status is CandidateGateStatus.ORDINARY_REJECTED
    assert result.plan_invalidation_receipt is None
    assert result.evidence[0].kind == "candidate_audit"
    assert port.calls == 0


def test_formal_review_scope_finding_is_preserved_and_routed_without_repair():
    from gwo_v8.candidate_gate import (
        CandidateGate,
        CandidateGateStatus,
        FormalReviewFinding,
        FormalReviewResult,
    )
    from gwo_v8.runtime_gateway import CapabilityPolicy, CapabilityPolicyProof

    parent = _parent()
    audit = _clean_audit(parent)

    class Reviewer:
        capability_policy_proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="9" * 64,
        )

        def __init__(self):
            self.calls = 0

        def review(self, request):
            self.calls += 1
            return FormalReviewResult(
                subject_digest=request.digest,
                findings=(
                    FormalReviewFinding(
                        parent_digest=request.parent_digest,
                        candidate_digest=request.candidate_digest,
                        review_subject_digest=request.digest,
                        finding_id="finding:protocol-owner",
                        severity="hard",
                        code="OWNER_OUTSIDE_TICKET",
                        message="The required persistent protocol has another owner.",
                        scope_escape=True,
                        invalidated_obligation="issue:one protocol ownership",
                        required_effects=("protocol.persist.v1",),
                    ),
                ),
            )

    port = _RecordingPort()
    reviewer = Reviewer()
    result = CandidateGate(
        invalidation_reporter=port,
        formal_reviewer=reviewer,
    ).audit_candidate(parent, audit)

    assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    assert [item.kind for item in result.evidence] == [
        "candidate_audit",
        "formal_review_finding",
        "plan_invalidation",
    ]
    assert result.repair_packet is None
    assert result.classification is None
    assert reviewer.calls == 1
    assert port.calls == 1
    finding = result.evidence[1]
    assert result.evidence[2].source_evidence_digest == finding.digest


def test_repair_scope_escape_is_evidence_and_never_reopens_formal_review():
    from gwo_v8.candidate_gate import (
        CandidateGate,
        CandidateGateStatus,
        CandidateIdentity,
        RepairPacket,
        RepairVerificationResult,
    )
    from gwo_v8.runtime_gateway import (
        CapabilityPolicy,
        CapabilityPolicyProof,
        WorkRunPurpose,
    )

    parent = _parent(purpose=WorkRunPurpose.formal_review())
    packet = RepairPacket(
        parent_digest=parent.digest,
        rejected_candidate_digest="1" * 64,
        prior_review_subject_digest="2" * 64,
        finding_digests=("3" * 64,),
        allowed_paths=("src/protocol.py",),
    )
    repaired = CandidateIdentity(
        reported_reference="refs/heads/repaired",
        base_commit_oid="5" * 40,
        base_tree_oid="6" * 40,
        candidate_commit_oid="7" * 40,
        candidate_tree_oid="8" * 40,
        changed_paths=("src/new_owner.py", "src/protocol.py"),
    )

    class Reviewer:
        capability_policy_proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="9" * 64,
        )

        def review(self, _request):
            raise AssertionError("Repair scope escape must not reopen Formal Review")

    class Verifier:
        capability_policy_proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="9" * 64,
        )

        def __init__(self):
            self.calls = 0

        def verify(self, request):
            self.calls += 1
            return RepairVerificationResult(
                request_digest=request.digest,
                accepted=False,
                scope_escape_paths=("src/new_owner.py",),
                details=("repair escaped its allowed packet scope",),
                invalidated_obligation="repair packet allowed scope",
                required_effects=("protocol.persist.v1",),
            )

    port = _RecordingPort()
    verifier = Verifier()
    result = CandidateGate(
        invalidation_reporter=port,
        formal_reviewer=Reviewer(),
        candidate_reader=_repair_reader(parent, repaired),
        repair_verifier=verifier,
    ).verify_repair(parent, packet, repaired)

    assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    assert [item.kind for item in result.evidence] == [
        "repair_scope_escape",
        "plan_invalidation",
    ]
    assert result.plan_invalidation_report.reporter_role == "review"
    assert verifier.calls == 1
    assert port.calls == 1


def test_stale_candidate_audit_is_rejected_before_any_external_effect():
    from gwo_v8.candidate_gate import CandidateGate, CandidateGateError

    parent = _parent()
    audit = _clean_audit(parent)
    object.__setattr__(audit, "parent_digest", "f" * 64)
    port = _RecordingPort()

    with pytest.raises(CandidateGateError) as raised:
        CandidateGate(invalidation_reporter=port).audit_candidate(parent, audit)

    assert raised.value.code == "CANDIDATE_GATE_EVIDENCE_STALE"
    assert port.calls == 0


def test_duplicate_plan_invalidation_replay_reads_back_the_same_receipt():
    from gwo_v8.candidate_gate import (
        AuditFailureKind,
        AuditFailureRoute,
        CandidateAuditReport,
        CandidateGate,
        CandidateIdentity,
        DeterministicAuditFailure,
    )

    parent = _parent()
    audit = CandidateAuditReport(
        parent_digest=parent.digest,
        candidate=CandidateIdentity(
            reported_reference="refs/heads/candidate",
            base_commit_oid="1" * 40,
            base_tree_oid="2" * 40,
            candidate_commit_oid="3" * 40,
            candidate_tree_oid="4" * 40,
            changed_paths=("src/protocol.py",),
        ),
        failures=(
            DeterministicAuditFailure(
                kind=AuditFailureKind.AUTHORITY,
                route=AuditFailureRoute.TICKET_UNSATISFIABLE,
                code="AUTHORITY_CHANGE_REQUIRED",
                detail="The frozen authority is insufficient.",
                invalidated_obligation="issue:one authority",
                required_effects=("authority.grant.v1",),
            ),
        ),
    )
    port = _RecordingPort()
    gate = CandidateGate(invalidation_reporter=port)
    first = gate.audit_candidate(parent, audit)
    replay = gate.replay_plan_invalidation(
        parent,
        first.evidence[-1],
        first.plan_invalidation_report,
    )

    assert replay.plan_invalidation_receipt.receipt_digest == (
        first.plan_invalidation_receipt.receipt_digest
    )
    assert replay.plan_invalidation_report == first.plan_invalidation_report
    assert port.calls == 2


def test_candidate_identity_rejects_noncanonical_changed_path_order():
    from gwo_v8.candidate_gate import CandidateGateError, CandidateIdentity

    with pytest.raises(CandidateGateError) as raised:
        CandidateIdentity(
            reported_reference="refs/heads/candidate",
            base_commit_oid="1" * 40,
            base_tree_oid="2" * 40,
            candidate_commit_oid="3" * 40,
            candidate_tree_oid="4" * 40,
            changed_paths=("z.py", "a.py"),
        )

    assert raised.value.code == "CANDIDATE_GATE_EVIDENCE_INVALID"


def test_exact_diff_record_sorts_add_before_delete_when_old_path_is_absent():
    from gwo_v8.candidate_gate import CandidateDiffRecordV1

    record = CandidateDiffRecordV1.from_tree_entries(
        repository_object_format="sha1",
        base_commit_oid="1" * 40,
        base_tree_oid="2" * 40,
        candidate_commit_oid="3" * 40,
        candidate_tree_oid="4" * 40,
        base_entries={b"b": ("100644", "blob", "5" * 40)},
        candidate_entries={b"a": ("100644", "blob", "6" * 40)},
    )

    assert [
        (entry.change_kind, entry.old_path, entry.new_path)
        for entry in record.entries
    ] == [
        ("add", None, "YQ"),
        ("delete", "Yg", None),
    ]


@pytest.mark.parametrize(
    ("object_format", "valid_width", "invalid_width"),
    [("sha1", 40, 64), ("sha256", 64, 40)],
)
@pytest.mark.parametrize(
    "field_name",
    [
        "base_commit_oid",
        "base_tree_oid",
        "candidate_commit_oid",
        "candidate_tree_oid",
    ],
)
def test_exact_diff_record_rejects_record_oid_width_mismatch(
    object_format, valid_width, invalid_width, field_name
):
    from gwo_v8.candidate_gate import CandidateDiffEntryV1, CandidateDiffRecordV1

    values = {
        "base_commit_oid": "1" * valid_width,
        "base_tree_oid": "2" * valid_width,
        "candidate_commit_oid": "3" * valid_width,
        "candidate_tree_oid": "4" * valid_width,
    }
    values[field_name] = "f" * invalid_width
    entry = CandidateDiffEntryV1(
        old_path=None,
        new_path="YQ",
        change_kind="add",
        old_mode=None,
        new_mode="100644",
        old_object_type=None,
        new_object_type="blob",
        old_oid=None,
        new_oid="5" * valid_width,
    )

    with pytest.raises(Exception) as raised:
        CandidateDiffRecordV1(
            schema_version="CandidateDiffRecordV1",
            repository_object_format=object_format,
            entries=(entry,),
            **values,
        )

    assert raised.value.code == "CANDIDATE_GATE_DIFF_INVALID"


@pytest.mark.parametrize(
    ("object_format", "valid_width", "invalid_width"),
    [("sha1", 40, 64), ("sha256", 64, 40)],
)
def test_exact_diff_record_rejects_entry_oid_width_mismatch(
    object_format, valid_width, invalid_width
):
    from gwo_v8.candidate_gate import CandidateDiffEntryV1, CandidateDiffRecordV1

    entry = CandidateDiffEntryV1(
        old_path=None,
        new_path="YQ",
        change_kind="add",
        old_mode=None,
        new_mode="100644",
        old_object_type=None,
        new_object_type="blob",
        old_oid=None,
        new_oid="5" * invalid_width,
    )

    with pytest.raises(Exception) as raised:
        CandidateDiffRecordV1(
            schema_version="CandidateDiffRecordV1",
            repository_object_format=object_format,
            base_commit_oid="1" * valid_width,
            base_tree_oid="2" * valid_width,
            candidate_commit_oid="3" * valid_width,
            candidate_tree_oid="4" * valid_width,
            entries=(entry,),
        )

    assert raised.value.code == "CANDIDATE_GATE_DIFF_INVALID"


@pytest.mark.parametrize(
    "failure_kind, code",
    [
        ("scope", "SCOPE_ESCAPE"),
        ("protected_effect", "PROTECTED_EFFECT"),
        ("authority", "AUTHORITY_GAP"),
        ("affected_check", "CHECK_TARGET_GAP"),
    ],
)
def test_each_deterministic_audit_failure_stops_before_review_or_repair(
    failure_kind, code
):
    from gwo_v8.candidate_gate import (
        AuditFailureKind,
        AuditFailureRoute,
        CandidateAuditReport,
        CandidateGate,
        CandidateGateStatus,
        DeterministicAuditFailure,
    )

    parent = _parent()
    audit = CandidateAuditReport(
        parent_digest=parent.digest,
        candidate=_clean_audit(parent).candidate,
        failures=(
            DeterministicAuditFailure(
                kind=AuditFailureKind(failure_kind),
                route=AuditFailureRoute.TICKET_UNSATISFIABLE,
                code=code,
                detail=f"{failure_kind} proves the frozen Ticket is unsafe.",
                invalidated_obligation="issue:one frozen obligation",
                required_effects=(f"{failure_kind}.required.v1",),
            ),
        ),
    )

    class NeverReview:
        capability_policy_proof = None

        def review(self, _request):
            raise AssertionError("deterministic audit must stop before Formal Review")

    class NeverRepair:
        capability_policy_proof = None

        def verify(self, _request):
            raise AssertionError("deterministic audit must stop before Repair Verification")

    port = _RecordingPort()
    result = CandidateGate(
        invalidation_reporter=port,
        formal_reviewer=NeverReview(),
        repair_verifier=NeverRepair(),
    ).audit_candidate(parent, audit)

    assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    assert result.plan_invalidation_receipt is not None
    assert result.repair_packet is None
    assert port.calls == 1


def test_candidate_gate_remains_a_deep_module_not_a_new_public_workflow_export():
    import gwo_v8

    from gwo_v8.candidate_gate import CandidateGate, CandidateGateResult

    assert CandidateGate.__module__ == "gwo_v8.candidate_gate"
    assert CandidateGateResult.__module__ == "gwo_v8.candidate_gate"
    assert not hasattr(gwo_v8, "CandidateGate")
    assert not hasattr(gwo_v8, "CandidateGateResult")


def test_stale_formal_review_finding_is_rejected_before_plan_invalidation():
    from gwo_v8.candidate_gate import (
        CandidateGate,
        CandidateGateError,
        FormalReviewFinding,
        FormalReviewResult,
    )
    from gwo_v8.runtime_gateway import CapabilityPolicy, CapabilityPolicyProof

    parent = _parent()
    audit = _clean_audit(parent)

    class Reviewer:
        capability_policy_proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="9" * 64,
        )

        def review(self, request):
            finding = FormalReviewFinding(
                parent_digest=request.parent_digest,
                candidate_digest=request.candidate_digest,
                review_subject_digest=request.digest,
                finding_id="finding:stale",
                severity="hard",
                code="STALE",
                message="stale review finding",
            )
            object.__setattr__(finding, "parent_digest", "f" * 64)
            return FormalReviewResult(
                subject_digest=request.digest,
                findings=(finding,),
            )

    port = _RecordingPort()
    with pytest.raises(CandidateGateError) as raised:
        CandidateGate(
            invalidation_reporter=port,
            formal_reviewer=Reviewer(),
        ).audit_candidate(parent, audit)

    assert raised.value.code == "CANDIDATE_GATE_EVIDENCE_STALE"
    assert port.calls == 0


def test_runtime_gateway_adapter_stores_exact_evidence_before_private_report(tmp_path):
    from gwo_v8._canonical import digest_value
    from gwo_v8.candidate_gate import (
        AuditFailureKind,
        AuditFailureRoute,
        CandidateAuditReport,
        CandidateGate,
        DeterministicAuditFailure,
        RuntimeGatewayPlanInvalidationAdapter,
    )
    from gwo_v8.runtime_gateway import (
        ArtifactStore,
        CapabilityPolicy,
        CapabilityPolicyProof,
        PlanInvalidationReceipt,
    )

    parent = _parent()
    audit = CandidateAuditReport(
        parent_digest=parent.digest,
        candidate=_clean_audit(parent).candidate,
        failures=(
            DeterministicAuditFailure(
                kind=AuditFailureKind.SCOPE,
                route=AuditFailureRoute.TICKET_UNSATISFIABLE,
                code="PERSISTENT_SCOPE",
                detail="scope requires a persistent owner",
                invalidated_obligation="issue:one owner",
                required_effects=("owner.persist.v1",),
            ),
        ),
    )

    class Gateway:
        def __init__(self):
            self._artifacts = ArtifactStore(tmp_path / "artifacts")
            self.calls = 0

        def _report_plan_invalidation(self, subject, report):
            self.calls += 1
            self.observed = self._artifacts.read_json(report.evidence_digest)
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
                "source_evidence_digests": self.observed["source_evidence_digests"],
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

    gateway = Gateway()
    result = CandidateGate(
        invalidation_reporter=RuntimeGatewayPlanInvalidationAdapter(gateway)
    ).audit_candidate(parent, audit)

    assert gateway.calls == 1
    assert gateway.observed["kind"] == "plan_invalidation"
    assert gateway.observed["subject"] == parent.runtime_subject.canonical()
    assert result.plan_invalidation_receipt is not None


def test_candidate_gate_rejects_a_gateway_receipt_without_source_lineage():
    from gwo_v8.candidate_gate import (
        AuditFailureKind,
        AuditFailureRoute,
        CandidateAuditReport,
        CandidateGate,
        CandidateGateError,
        DeterministicAuditFailure,
    )

    parent = _parent()
    audit = CandidateAuditReport(
        parent_digest=parent.digest,
        candidate=_clean_audit(parent).candidate,
        failures=(
            DeterministicAuditFailure(
                kind=AuditFailureKind.SCOPE,
                route=AuditFailureRoute.TICKET_UNSATISFIABLE,
                code="PERSISTENT_SCOPE",
                detail="scope requires a persistent owner",
                invalidated_obligation="issue:one owner",
                required_effects=("owner.persist.v1",),
            ),
        ),
    )

    class LegacyPort(_RecordingPort):
        def report_plan_invalidation(self, subject, evidence, report):
            receipt = super().report_plan_invalidation(subject, evidence, report)
            from gwo_v8.runtime_gateway import PlanInvalidationReceipt

            observation = dict(receipt.observation)
            observation.pop("source_evidence_digests", None)
            return PlanInvalidationReceipt(
                report_digest=receipt.report_digest,
                receipt_digest=receipt.receipt_digest,
                capability_policy_proof=receipt.capability_policy_proof,
                observation=observation,
            )

    with pytest.raises(CandidateGateError) as raised:
        CandidateGate(invalidation_reporter=LegacyPort()).audit_candidate(
            parent, audit
        )

    assert raised.value.code == "CANDIDATE_GATE_RECEIPT_INVALID"


def test_formal_review_scope_escape_preserves_complete_source_evidence_digest_tuple():
    """Every scope-escape Finding must remain a source of the invalidation."""

    from gwo_v8.candidate_gate import (
        CandidateAuditReport,
        CandidateGate,
        FormalReviewFinding,
        FormalReviewResult,
    )
    from gwo_v8.runtime_gateway import CapabilityPolicy, CapabilityPolicyProof

    parent = _parent()
    audit = _clean_audit(parent)

    class Reviewer:
        capability_policy_proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="9" * 64,
        )

        def review(self, request):
            findings = (
                FormalReviewFinding(
                    parent_digest=request.parent_digest,
                    candidate_digest=request.candidate_digest,
                    review_subject_digest=request.digest,
                    finding_id="finding:deep-owner",
                    severity="hard",
                    code="OWNER_OUTSIDE_TICKET",
                    message="The deep module owner is outside the frozen Ticket.",
                    scope_escape=True,
                    invalidated_obligation="issue:one deep-module owner",
                    required_effects=("owner.persist.v1",),
                ),
                FormalReviewFinding(
                    parent_digest=request.parent_digest,
                    candidate_digest=request.candidate_digest,
                    review_subject_digest=request.digest,
                    finding_id="finding:campaign-dependency",
                    severity="hard",
                    code="DEPENDENCY_OUTSIDE_TICKET",
                    message="The Campaign dependency is outside the frozen Ticket.",
                    scope_escape=True,
                    invalidated_obligation="issue:one Campaign dependency",
                    required_effects=("dependency.graph.v1",),
                ),
            )
            return FormalReviewResult(
                subject_digest=request.digest,
                findings=findings,
            )

    result = CandidateGate(
        invalidation_reporter=_RecordingPort(),
        formal_reviewer=Reviewer(),
    ).audit_candidate(parent, CandidateAuditReport(
        parent_digest=audit.parent_digest,
        candidate=audit.candidate,
    ))

    source_digests = tuple(
        sorted(item.digest for item in result.evidence if item.kind == "formal_review_finding")
    )
    plan_evidence = result.evidence[-1]

    assert plan_evidence.source_evidence_digests == source_digests
    assert plan_evidence.canonical()["source_evidence_digests"] == list(source_digests)


def test_formal_review_invalidation_carries_the_complete_finding_and_candidate_lineage():
    from gwo_v8.candidate_gate import (
        CandidateAuditReport,
        CandidateGate,
        FormalReviewFinding,
        FormalReviewResult,
    )
    from gwo_v8.runtime_gateway import CapabilityPolicy, CapabilityPolicyProof

    parent = _parent()
    audit = _clean_audit(parent)

    class Reviewer:
        capability_policy_proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="9" * 64,
        )

        def review(self, request):
            findings = (
                FormalReviewFinding(
                    parent_digest=request.parent_digest,
                    candidate_digest=request.candidate_digest,
                    review_subject_digest=request.digest,
                    finding_id="finding:scope",
                    severity="hard",
                    code="OWNER_OUTSIDE_TICKET",
                    message="The persistent owner is outside the frozen Ticket.",
                    scope_escape=True,
                    invalidated_obligation="issue:one owner",
                    required_effects=("owner.persist.v1",),
                ),
                FormalReviewFinding(
                    parent_digest=request.parent_digest,
                    candidate_digest=request.candidate_digest,
                    review_subject_digest=request.digest,
                    finding_id="finding:ordinary",
                    severity="hard",
                    code="CHECK_REQUIRES_REPAIR",
                    message="The local check needs an ordinary repair.",
                    required_effects=("check.repair.v1",),
                ),
            )
            return FormalReviewResult(subject_digest=request.digest, findings=findings)

    result = CandidateGate(
        invalidation_reporter=_RecordingPort(),
        formal_reviewer=Reviewer(),
    ).audit_candidate(parent, audit)
    plan_evidence = result.evidence[-1]

    assert len(plan_evidence.source_evidence_digests) == 2
    assert plan_evidence.lineage_artifacts
    assert any(
        item.get("kind") == "formal_review_finding.v1"
        and item.get("finding_id") == "finding:ordinary"
        for item in plan_evidence.lineage_artifacts
    )
    assert any(
        item.get("kind") == "candidate_audit.v1"
        for item in plan_evidence.lineage_artifacts
    )


@pytest.mark.parametrize(
    "source_kind",
    ("candidate_audit", "formal_review", "repair_verification"),
)
def test_candidate_source_evidence_has_complete_lineage_to_report_and_receipt(source_kind):
    """Candidate, Review, and Repair sources all bind through one receipt."""

    from gwo_v8.candidate_gate import (
        AuditFailureKind,
        AuditFailureRoute,
        CandidateAuditReport,
        CandidateGate,
        CandidateIdentity,
        DeterministicAuditFailure,
        FormalReviewFinding,
        FormalReviewResult,
        RepairPacket,
        RepairVerificationResult,
    )
    from gwo_v8.runtime_gateway import (
        CapabilityPolicy,
        CapabilityPolicyProof,
        WorkRunPurpose,
    )

    parent = _parent(
        purpose=(
            WorkRunPurpose.formal_review()
            if source_kind == "repair_verification"
            else WorkRunPurpose.implementation()
        )
    )
    port = _RecordingPort()
    source_digests = None

    if source_kind == "candidate_audit":
        audit = CandidateAuditReport(
            parent_digest=parent.digest,
            candidate=_clean_audit(parent).candidate,
            failures=(
                DeterministicAuditFailure(
                    kind=AuditFailureKind.SCOPE,
                    route=AuditFailureRoute.TICKET_UNSATISFIABLE,
                    code="PERSISTENT_SCOPE",
                    detail="The frozen Ticket needs a persistent owner.",
                    invalidated_obligation="issue:one persistent owner",
                    required_effects=("owner.persist.v1",),
                ),
            ),
        )
        result = CandidateGate(invalidation_reporter=port).audit_candidate(parent, audit)
        source_digests = (result.evidence[0].digest,)

    elif source_kind == "formal_review":
        audit = CandidateAuditReport(
            parent_digest=parent.digest,
            candidate=_clean_audit(parent).candidate,
        )

        class Reviewer:
            capability_policy_proof = CapabilityPolicyProof(
                capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
                authority_record_digest="9" * 64,
            )

            def review(self, request):
                finding = FormalReviewFinding(
                    parent_digest=request.parent_digest,
                    candidate_digest=request.candidate_digest,
                    review_subject_digest=request.digest,
                    finding_id="finding:owner",
                    severity="hard",
                    code="OWNER_OUTSIDE_TICKET",
                    message="The persistent owner is outside the frozen Ticket.",
                    scope_escape=True,
                    invalidated_obligation="issue:one persistent owner",
                    required_effects=("owner.persist.v1",),
                )
                return FormalReviewResult(
                    subject_digest=request.digest,
                    findings=(finding,),
                )

        result = CandidateGate(
            invalidation_reporter=port,
            formal_reviewer=Reviewer(),
        ).audit_candidate(parent, audit)
        source_digests = (result.evidence[1].digest,)

    else:
        packet = RepairPacket(
            parent_digest=parent.digest,
            rejected_candidate_digest="1" * 64,
            prior_review_subject_digest="2" * 64,
            finding_digests=("3" * 64,),
            allowed_paths=("src/protocol.py",),
        )
        repaired = CandidateIdentity(
            reported_reference="refs/heads/repaired",
            base_commit_oid="5" * 40,
            base_tree_oid="6" * 40,
            candidate_commit_oid="7" * 40,
            candidate_tree_oid="8" * 40,
            changed_paths=("src/new_owner.py", "src/protocol.py"),
        )

        class Verifier:
            capability_policy_proof = CapabilityPolicyProof(
                capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
                authority_record_digest="9" * 64,
            )

            def verify(self, request):
                return RepairVerificationResult(
                    request_digest=request.digest,
                    accepted=False,
                    scope_escape_paths=("src/new_owner.py",),
                    details=("repair escaped its allowed packet scope",),
                    invalidated_obligation="repair packet allowed scope",
                    required_effects=("owner.persist.v1",),
                )

        result = CandidateGate(
            invalidation_reporter=port,
            candidate_reader=_repair_reader(parent, repaired),
            repair_verifier=Verifier(),
        ).verify_repair(parent, packet, repaired)
        source_digests = (result.evidence[0].digest,)

    assert source_digests is not None
    plan_evidence = result.evidence[-1]
    report = port.reports[-1]
    receipt = result.plan_invalidation_receipt

    assert plan_evidence.source_evidence_digests == source_digests
    assert plan_evidence.canonical()["source_evidence_digests"] == list(source_digests)
    assert report.evidence_digest == plan_evidence.digest
    assert receipt is not None
    assert receipt.report_digest == report.digest
    assert receipt.observation["report_digest"] == report.digest
    assert receipt.observation["evidence_digest"] == plan_evidence.digest


def test_repair_verification_does_not_route_an_allowed_path_as_scope_escape():
    """CandidateGate independently checks repair paths against the packet."""

    from gwo_v8.candidate_gate import (
        CandidateGate,
        CandidateGateStatus,
        CandidateIdentity,
        RepairPacket,
        RepairVerificationResult,
    )
    from gwo_v8.runtime_gateway import CapabilityPolicy, CapabilityPolicyProof

    parent = _parent()
    packet = RepairPacket(
        parent_digest=parent.digest,
        rejected_candidate_digest="1" * 64,
        prior_review_subject_digest="2" * 64,
        finding_digests=("3" * 64,),
        allowed_paths=("src/protocol.py",),
    )
    candidate = CandidateIdentity(
        reported_reference="refs/heads/repaired",
        base_commit_oid="4" * 40,
        base_tree_oid="5" * 40,
        candidate_commit_oid="6" * 40,
        candidate_tree_oid="7" * 40,
        changed_paths=("src/protocol.py",),
    )

    class Verifier:
        capability_policy_proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="8" * 64,
        )

        def verify(self, request):
            return RepairVerificationResult(
                request_digest=request.digest,
                accepted=False,
                scope_escape_paths=("src/protocol.py",),
                details=("the packet path was touched",),
                invalidated_obligation="repair packet allowed scope",
                required_effects=("protocol.persist.v1",),
            )

    reporter = _RecordingPort()
    with pytest.raises(Exception) as error:
        CandidateGate(
            invalidation_reporter=reporter,
            candidate_reader=_repair_reader(parent, candidate),
            repair_verifier=Verifier(),
        ).verify_repair(parent, packet, candidate)

    assert error.value.code == "CANDIDATE_GATE_REPAIR_SCOPE_INVALID"
    assert reporter.calls == 0


def test_repair_verification_detects_candidate_path_outside_packet_even_without_verifier_claim():
    """The exact repaired Candidate, not a verifier claim, proves escape."""

    from gwo_v8.candidate_gate import (
        CandidateGate,
        CandidateGateStatus,
        CandidateIdentity,
        RepairPacket,
        RepairVerificationResult,
    )
    from gwo_v8.runtime_gateway import CapabilityPolicy, CapabilityPolicyProof

    parent = _parent()
    packet = RepairPacket(
        parent_digest=parent.digest,
        rejected_candidate_digest="1" * 64,
        prior_review_subject_digest="2" * 64,
        finding_digests=("3" * 64,),
        allowed_paths=("src/protocol.py",),
    )
    candidate = CandidateIdentity(
        reported_reference="refs/heads/repaired",
        base_commit_oid="4" * 40,
        base_tree_oid="5" * 40,
        candidate_commit_oid="6" * 40,
        candidate_tree_oid="7" * 40,
        changed_paths=("src/new_owner.py", "src/protocol.py"),
    )

    class Verifier:
        capability_policy_proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="8" * 64,
        )

        def verify(self, request):
            return RepairVerificationResult(
                request_digest=request.digest,
                accepted=False,
                details=("verifier did not classify the new path",),
                invalidated_obligation="repair packet allowed scope",
                required_effects=("protocol.persist.v1",),
            )

    reporter = _RecordingPort()
    result = CandidateGate(
        invalidation_reporter=reporter,
        candidate_reader=_repair_reader(parent, candidate),
        repair_verifier=Verifier(),
    ).verify_repair(parent, packet, candidate)

    assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    assert result.plan_invalidation_receipt is not None
    assert reporter.calls == 1
    assert "escaped_path=src/new_owner.py" in result.evidence[-1].discovered_facts


def test_repair_verification_reads_authoritative_candidate_before_scope_calculation():
    """A caller cannot hide a repaired path from the exact-reference readback."""

    from gwo_v8.candidate_gate import (
        CandidateDiffEntryV1,
        CandidateDiffRecordV1,
        CandidateGate,
        CandidateGateStatus,
        CandidateIdentity,
        CandidateReadback,
        RepairPacket,
        RepairVerificationResult,
    )
    from gwo_v8.runtime_gateway import CapabilityPolicy, CapabilityPolicyProof

    parent = _parent()
    packet = RepairPacket(
        parent_digest=parent.digest,
        rejected_candidate_digest="1" * 64,
        prior_review_subject_digest="2" * 64,
        finding_digests=("3" * 64,),
        allowed_paths=("src/protocol.py",),
    )
    # The caller reports only the allowed path.  The authoritative reference
    # contains one additional path outside the Repair Packet boundary.
    reported = CandidateIdentity(
        reported_reference="refs/heads/repaired",
        base_commit_oid="4" * 40,
        base_tree_oid="5" * 40,
        candidate_commit_oid="6" * 40,
        candidate_tree_oid="7" * 40,
        changed_paths=("src/protocol.py",),
    )
    authoritative = CandidateIdentity(
        reported_reference=reported.reported_reference,
        base_commit_oid=reported.base_commit_oid,
        base_tree_oid=reported.base_tree_oid,
        candidate_commit_oid=reported.candidate_commit_oid,
        candidate_tree_oid=reported.candidate_tree_oid,
        changed_paths=("src/new_owner.py", "src/protocol.py"),
    )
    diff = CandidateDiffRecordV1(
        repository=parent.runtime_subject.repository,
        object_format="sha1",
        base_commit_oid=authoritative.base_commit_oid,
        base_tree_oid=authoritative.base_tree_oid,
        candidate_commit_oid=authoritative.candidate_commit_oid,
        candidate_tree_oid=authoritative.candidate_tree_oid,
        entries=(
            CandidateDiffEntryV1(
                side="candidate",
                path="src/new_owner.py",
                mode="100644",
                object_type="blob",
                object_oid="8" * 40,
            ),
            CandidateDiffEntryV1(
                side="candidate",
                path="src/protocol.py",
                mode="100644",
                object_type="blob",
                object_oid="9" * 40,
            ),
        ),
    )
    readback = CandidateReadback(
        repository=parent.runtime_subject.repository,
        candidate=authoritative,
        diff_record=diff,
    )

    class Reader:
        def __init__(self):
            self.calls = 0

        def read_candidate(self, repository, reference):
            self.calls += 1
            assert repository == parent.runtime_subject.repository
            assert reference == reported.reported_reference
            return readback

    class Verifier:
        capability_policy_proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="a" * 64,
        )

        def verify(self, request):
            assert request.candidate == authoritative
            return RepairVerificationResult(
                request_digest=request.digest,
                accepted=True,
                details=("repair verifier did not classify the extra path",),
                invalidated_obligation="repair packet allowed scope",
                required_effects=("protocol.persist.v1",),
            )

    reader = Reader()
    result = CandidateGate(
        invalidation_reporter=_RecordingPort(),
        candidate_reader=reader,
        repair_verifier=Verifier(),
    ).verify_repair(parent, packet, reported)

    assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    assert reader.calls == 1
    assert result.evidence[-1].source_kind == "repair_verification"
    assert any(
        item.get("kind") == "candidate_diff_record.v1"
        for item in result.evidence[-1].lineage_artifacts
    )


def test_repair_verification_fails_closed_when_authoritative_immutable_identity_differs():
    from gwo_v8.candidate_gate import (
        CandidateDiffEntryV1,
        CandidateDiffRecordV1,
        CandidateGate,
        CandidateGateError,
        CandidateIdentity,
        CandidateReadback,
        RepairPacket,
        RepairVerificationResult,
    )
    from gwo_v8.runtime_gateway import CapabilityPolicy, CapabilityPolicyProof

    parent = _parent()
    packet = RepairPacket(
        parent_digest=parent.digest,
        rejected_candidate_digest="1" * 64,
        prior_review_subject_digest="2" * 64,
        finding_digests=("3" * 64,),
        allowed_paths=("src/protocol.py",),
    )
    reported = CandidateIdentity(
        reported_reference="refs/heads/repaired",
        base_commit_oid="4" * 40,
        base_tree_oid="5" * 40,
        candidate_commit_oid="6" * 40,
        candidate_tree_oid="7" * 40,
        changed_paths=("src/protocol.py",),
    )
    authoritative = CandidateIdentity(
        reported_reference=reported.reported_reference,
        base_commit_oid=reported.base_commit_oid,
        base_tree_oid=reported.base_tree_oid,
        candidate_commit_oid="8" * 40,
        candidate_tree_oid=reported.candidate_tree_oid,
        changed_paths=("src/protocol.py",),
    )
    readback = CandidateReadback(
        repository=parent.runtime_subject.repository,
        candidate=authoritative,
        diff_record=CandidateDiffRecordV1(
            repository=parent.runtime_subject.repository,
            object_format="sha1",
            base_commit_oid=authoritative.base_commit_oid,
            base_tree_oid=authoritative.base_tree_oid,
            candidate_commit_oid=authoritative.candidate_commit_oid,
            candidate_tree_oid=authoritative.candidate_tree_oid,
            entries=(
                CandidateDiffEntryV1(
                    side="candidate",
                    path="src/protocol.py",
                    mode="100644",
                    object_type="blob",
                    object_oid="9" * 40,
                ),
            ),
        ),
    )

    class Reader:
        def read_candidate(self, _repository, _reference):
            return readback

    class Verifier:
        capability_policy_proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="a" * 64,
        )

        def verify(self, _request):
            return RepairVerificationResult(
                request_digest="b" * 64,
                accepted=True,
            )

    reporter = _RecordingPort()
    with pytest.raises(CandidateGateError) as error:
        CandidateGate(
            invalidation_reporter=reporter,
            candidate_reader=Reader(),
            repair_verifier=Verifier(),
        ).verify_repair(parent, packet, reported)

    assert error.value.code == "CANDIDATE_GATE_EVIDENCE_STALE"
    assert reporter.calls == 0


def test_candidate_gate_uses_authoritative_candidate_readback_and_complete_diff_subject():
    from gwo_v8.candidate_gate import (
        CandidateAuditReport,
        CandidateDiffEntryV1,
        CandidateDiffRecordV1,
        CandidateGate,
        CandidateGateParent,
        CandidateIdentity,
        CandidateReadback,
        FormalReviewResult,
    )
    from gwo_v8.runtime_gateway import CapabilityPolicy, CapabilityPolicyProof

    parent = _parent()
    path_token = "c3JjL3Byb3RvY29sLnB5"
    candidate = CandidateIdentity(
        reported_reference="refs/heads/candidate",
        base_commit_oid="1" * 40,
        base_tree_oid="2" * 40,
        candidate_commit_oid="3" * 40,
        candidate_tree_oid="4" * 40,
        changed_path_tokens=(path_token,),
    )
    diff = CandidateDiffRecordV1(
        schema_version="CandidateDiffRecordV1",
        repository_object_format="sha1",
        base_commit_oid=candidate.base_commit_oid,
        base_tree_oid=candidate.base_tree_oid,
        candidate_commit_oid=candidate.candidate_commit_oid,
        candidate_tree_oid=candidate.candidate_tree_oid,
        entries=(
            CandidateDiffEntryV1(
                old_path=None,
                new_path=path_token,
                change_kind="add",
                old_mode=None,
                new_mode="100644",
                old_object_type=None,
                new_object_type="blob",
                old_oid=None,
                new_oid="5" * 40,
            ),
        ),
    )
    readback = CandidateReadback(
        repository=parent.runtime_subject.repository,
        candidate=candidate,
        diff_record=diff,
    )

    class Reader:
        def __init__(self):
            self.calls = []

        def read_candidate(self, repository, reference):
            self.calls.append((repository, reference))
            return readback

    class Reviewer:
        capability_policy_proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="9" * 64,
        )

        def review(self, request):
            assert request.base_commit_oid == candidate.base_commit_oid
            assert request.candidate_tree_oid == candidate.candidate_tree_oid
            assert request.diff_schema_version == "CandidateDiffRecordV1"
            assert request.diff_digest == diff.digest
            return FormalReviewResult(subject_digest=request.digest)

    reader = Reader()
    result = CandidateGate(
        invalidation_reporter=_RecordingPort(),
        candidate_reader=reader,
        formal_reviewer=Reviewer(),
    ).audit_candidate(
        parent,
        CandidateAuditReport(parent_digest=parent.digest, candidate=candidate),
    )

    assert result.status.value == "review_accepted"
    assert reader.calls == [(parent.runtime_subject.repository, candidate.reported_reference)]
