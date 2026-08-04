from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.candidate_gate import (  # noqa: E402
    CandidateGateError,
    CandidateGateParent,
    CandidateReceipt,
    FormalReviewResult,
    RepairPacket,
    ReviewFinding,
    ReviewSubject,
)
from gwo_v8.runtime_gateway import WorkRunPurpose, WorkRunSubject  # noqa: E402


def _rejected_fixture():
    runtime_subject = WorkRunSubject(
        repository="owner/repository",
        campaign_key="campaign:one",
        campaign_handle="campaign-handle:one",
        plan_revision_digest="4" * 64,
        work_run_key="work-run:one",
        ticket_key="issue:115",
        purpose=WorkRunPurpose.implementation(),
        prompt_artifact_digest="5" * 64,
        authority_subtree_digest="6" * 64,
        stable_action_id="binding:one",
    )
    parent = CandidateGateParent(
        runtime_subject=runtime_subject,
        ticket_contract_digest="7" * 64,
        policy_witness_digest="8" * 64,
        workspace_identity="workspace:one",
    )
    candidate_receipt = CandidateReceipt(
        parent_digest=parent.digest,
        repository=runtime_subject.repository,
        campaign_key=runtime_subject.campaign_key,
        campaign_handle=runtime_subject.campaign_handle,
        plan_revision_digest=runtime_subject.plan_revision_digest,
        work_run_key=runtime_subject.work_run_key,
        ticket_key=runtime_subject.ticket_key,
        reported_reference="refs/heads/candidate",
        base_commit_oid="a" * 40,
        base_tree_oid="b" * 40,
        candidate_commit_oid="c" * 40,
        candidate_tree_oid="d" * 40,
        diff_schema_version="CandidateDiffRecordV1",
        diff_record_digest="9" * 64,
        authority_subtree_digest=runtime_subject.authority_subtree_digest,
        runtime_subject_digest=runtime_subject.digest,
    )
    subject = ReviewSubject(
        parent_digest=parent.digest,
        candidate_receipt_digest=candidate_receipt.digest,
        runtime_subject_digest=runtime_subject.digest,
        candidate_digest="a" * 64,
        candidate_audit_digest="b" * 64,
        ticket_contract_digest=parent.ticket_contract_digest,
        policy_witness_digest=parent.policy_witness_digest,
        base_commit_oid=candidate_receipt.base_commit_oid,
        base_tree_oid=candidate_receipt.base_tree_oid,
        candidate_commit_oid=candidate_receipt.candidate_commit_oid,
        candidate_tree_oid=candidate_receipt.candidate_tree_oid,
        diff_schema_version=candidate_receipt.diff_schema_version,
        diff_record_digest=candidate_receipt.diff_record_digest,
        standards=("standard:repository",),
        check_evidence_digests=("e" * 64,),
        assurance_requirement_digest="f" * 64,
    )
    result = FormalReviewResult(
        subject_digest=subject.digest,
        findings=(
            ReviewFinding(
                parent_digest=parent.digest,
                candidate_digest=subject.candidate_digest,
                review_subject_digest=subject.digest,
                finding_id="finding:authority",
                severity="hard",
                code="AUTHORITY",
                message="repair the authority boundary",
                required_effects=("authority.persist.v1",),
            ),
            ReviewFinding(
                parent_digest=parent.digest,
                candidate_digest=subject.candidate_digest,
                review_subject_digest=subject.digest,
                finding_id="finding:test",
                severity="advisory",
                code="TEST",
                message="add the missing regression test",
            ),
        ),
    )
    return SimpleNamespace(
        parent=parent,
        candidate_receipt=candidate_receipt,
        subject=subject,
        result=result,
    )


@pytest.fixture
def rejected():
    return _rejected_fixture()


def make_repair_packet(rejected):
    return RepairPacket.from_review(
        parent=rejected.parent,
        candidate_receipt=rejected.candidate_receipt,
        subject=rejected.subject,
        result=rejected.result,
        allowed_path_tokens=("c3JjL21haW4ucHk",),
        required_check_ids=("unit", "typecheck"),
        repair_instructions=("fix named findings only",),
    )


def test_packet_contains_ledger_scope_checks_protocol_and_instructions(rejected):
    packet = make_repair_packet(rejected)

    assert packet.finding_ledger.entries
    assert packet.required_disposition_ids == (
        "finding:authority",
        "finding:test",
    )
    assert packet.allowed_path_tokens == ("c3JjL21haW4ucHk",)
    assert packet.required_check_ids == ("typecheck", "unit")
    assert packet.protocol_version == "gwo.formal-review.v1"
    assert packet.repair_instructions == ("fix named findings only",)


def test_packet_rejects_truncated_ledger(rejected):
    packet = make_repair_packet(rejected)

    with pytest.raises(CandidateGateError) as raised:
        packet.with_ledger(packet.finding_ledger.entries[:1])

    assert raised.value.code == "CANDIDATE_GATE_REPAIR_PACKET_INVALID"
