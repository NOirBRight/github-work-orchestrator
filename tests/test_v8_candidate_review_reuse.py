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
    CandidateAcceptanceFacts,
    CandidateCheckEvidence,
    CandidateDiffEntryV1,
    CandidateDiffRecordV1,
    CandidateGate,
    CandidateGateError,
    CandidateGateParent,
    CandidateIdentity,
    CandidateReadback,
    FormalReviewResult,
)
from gwo_v8.runtime_gateway import (  # noqa: E402
    CapabilityPolicy,
    CapabilityPolicyProof,
    WorkRunPurpose,
    WorkRunSubject,
)


class _Reporter:
    def report_plan_invalidation(self, _subject, _evidence, _report):
        raise AssertionError("these fixtures do not report Plan Invalidation")


class _Reader:
    def __init__(self, readback):
        self.readback = readback
        self.calls = 0

    def read_candidate(self, _repository, _reported_reference):
        self.calls += 1
        return self.readback


class _Reviewer:
    capability_policy_proof = CapabilityPolicyProof(
        capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
        authority_record_digest="9" * 64,
    )

    def __init__(self):
        self.calls = 0
        self.gate = None

    def review(self, action):
        self.calls += 1
        if self.gate is not None:
            self.gate.reviewer_calls += 1
        return FormalReviewResult(subject_digest=action.subject.digest)


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
                failure=None,
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


class _DiffStore:
    def __init__(self, records):
        self.records = {record.digest: record for record in records}
        self.reads = 0

    def put(self, record):
        self.records[record.digest] = record
        return record.digest

    def read(self, digest):
        self.reads += 1
        return self.records.get(digest)

    def corrupt(self, digest):
        self.records[digest] = None


def _record(*, candidate_tree_oid="d" * 40):
    return CandidateDiffRecordV1(
        schema_version="CandidateDiffRecordV1",
        repository_object_format="sha1",
        base_commit_oid="a" * 40,
        base_tree_oid="b" * 40,
        candidate_commit_oid="c" * 40,
        candidate_tree_oid=candidate_tree_oid,
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


def _subject_fixture():
    runtime_subject = WorkRunSubject(
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
        runtime_subject=runtime_subject,
        ticket_contract_digest="4" * 64,
        policy_witness_digest="5" * 64,
        workspace_identity="workspace:one",
    )
    record = _record()
    alternate_record = _record(candidate_tree_oid="f" * 40)
    candidate = CandidateIdentity(
        reported_reference="refs/heads/candidate",
        base_commit_oid=record.base_commit_oid,
        base_tree_oid=record.base_tree_oid,
        candidate_commit_oid=record.candidate_commit_oid,
        candidate_tree_oid=record.candidate_tree_oid,
        changed_path_tokens=record.changed_path_tokens,
    )
    readback = CandidateReadback(
        repository=runtime_subject.repository,
        candidate=candidate,
        diff_record=record,
    )
    return parent, readback, record, alternate_record


@pytest.fixture
def review_reuse_gate():
    parent, readback, record, alternate_record = _subject_fixture()
    store = _DiffStore((record, alternate_record))
    reviewer = _Reviewer()
    gate = CandidateGate(
        invalidation_reporter=_Reporter(),
        candidate_reader=_Reader(readback),
        formal_reviewer=reviewer,
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
        diff_artifacts=store,
    )
    gate.reviewer_calls = 0
    reviewer.gate = gate
    result = gate.gate_candidate(parent, "refs/heads/candidate")
    assert result.review_subject is not None
    assert result.candidate_diff_record == record
    subject = result.review_subject
    store.reads = 0
    gate.reviewer_calls = 0
    return gate, subject, result, store


def replace_subject_field(subject, field):
    if field == "candidate_tree_oid":
        value = "f" * 40
        return replace(subject, candidate_tree_oid=value, subject_digest=None)
    if field == "diff_record_digest":
        value = _record(candidate_tree_oid="f" * 40).digest
        return replace(subject, diff_record_digest=value, subject_digest=None)
    if field == "policy_witness_digest":
        return replace(subject, policy_witness_digest="8" * 64, subject_digest=None)
    if field == "assurance_requirement_digest":
        return replace(subject, assurance_requirement_digest="8" * 64, subject_digest=None)
    if field == "protocol_version":
        return replace(subject, protocol_version="gwo.formal-review.v2", subject_digest=None)
    if field == "action_kind":
        return replace(
            subject,
            action_kind="repair_verify",
            prior_review_subject_digest="a" * 64,
            repair_packet_digest="b" * 64,
            repair_delta_digest="c" * 64,
            subject_digest=None,
        )
    raise AssertionError(f"unsupported ReviewSubject field: {field}")


def test_reuse_requires_identical_subject_and_revalidated_diff(review_reuse_gate):
    gate, subject, result, store = review_reuse_gate
    assert gate.reuse_formal_review(subject=subject, result=result) == result
    assert store.reads == 1


@pytest.mark.parametrize(
    "field",
    [
        "candidate_tree_oid",
        "diff_record_digest",
        "policy_witness_digest",
        "assurance_requirement_digest",
        "protocol_version",
        "action_kind",
    ],
)
def test_changed_subject_fails_closed_before_reviewer(review_reuse_gate, field):
    gate, subject, result, _store = review_reuse_gate
    with pytest.raises(CandidateGateError) as raised:
        gate.reuse_formal_review(
            subject=replace_subject_field(subject, field),
            result=result,
        )
    assert raised.value.code == "CANDIDATE_GATE_REVIEW_REUSE_INVALID"


def test_missing_or_changed_diff_fails_before_reviewer(review_reuse_gate):
    gate, subject, result, store = review_reuse_gate
    store.corrupt(subject.diff_record_digest)
    with pytest.raises(CandidateGateError) as raised:
        gate.reuse_formal_review(subject=subject, result=result)
    assert raised.value.code == "CANDIDATE_GATE_DIFF_ARTIFACT_INVALID"
    assert gate.reviewer_calls == 0
