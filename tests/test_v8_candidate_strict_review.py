from __future__ import annotations

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
    CandidateGateStatus,
    CandidateReadback,
    CandidateIdentity,
    FormalReviewFinding,
    FormalReviewResult,
)
from gwo_v8.runtime_gateway import (  # noqa: E402
    CapabilityPolicy,
    CapabilityPolicyProof,
    WorkRunPurpose,
    WorkRunSubject,
)


class _Reader:
    def __init__(self, readback):
        self.readback = readback
        self.calls = []

    def read_candidate(self, repository, reported_reference):
        self.calls.append((repository, reported_reference))
        return self.readback


class _Reporter:
    def report_plan_invalidation(self, _subject, _evidence, _report):
        raise AssertionError("strict review fixtures do not report invalidation")


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


class _Policy:
    def __init__(self, mode, specialist_policy_id=None):
        self.mode = mode
        self.specialist_policy_id = specialist_policy_id

    def derive(self, _parent, _readback, _checks):
        return AssuranceRequirement(
            policy_id="policy:candidate-assurance",
            policy_version="1",
            mode=self.mode,
            required_check_ids=("check:unit",),
            standards=("standard:repository",),
            specialist_policy_id=self.specialist_policy_id,
        )


class _Reviewer:
    capability_policy_proof = CapabilityPolicyProof(
        capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
        authority_record_digest="9" * 64,
    )

    def __init__(self, *, invalid_transport=False, reject=False):
        self.invalid_transport = invalid_transport
        self.reject = reject
        self.actions = []

    def review(self, action):
        self.actions.append(action)
        if self.invalid_transport and len(self.actions) == 1:
            from gwo_v8.candidate_gate import InvalidReviewTransport

            raise InvalidReviewTransport("review payload was not typed")
        if self.reject:
            finding = FormalReviewFinding(
                parent_digest=action.subject.parent_digest,
                candidate_digest=action.subject.candidate_digest,
                review_subject_digest=action.subject.digest,
                finding_id="finding:rejected",
                severity="hard",
                code="REVIEW_REJECTED",
                message="The unchanged Candidate is rejected by Formal Review.",
            )
            return FormalReviewResult(
                subject_digest=action.subject.digest,
                findings=(finding,),
            )
        return FormalReviewResult(subject_digest=action.subject.digest)


class _StrictRetryBudgetReviewer(_Reviewer):
    def review(self, action):
        self.actions.append(action)
        from gwo_v8.candidate_gate import InvalidReviewTransport

        if action.kind in {"formal_review", "specialist_review"}:
            raise InvalidReviewTransport("review payload was not typed")
        return FormalReviewResult(subject_digest=action.subject.digest)


def _parent_and_readback():
    subject = WorkRunSubject(
        repository="owner/repository",
        campaign_key="campaign:one",
        campaign_handle="campaign-handle:one",
        plan_revision_digest="1" * 64,
        work_run_key="work-run:one",
        ticket_key="issue:115",
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


def _gate(*, mode, specialist_policy_id=None, reviewer=None):
    parent, readback = _parent_and_readback()
    reviewer = _Reviewer() if reviewer is None else reviewer
    gate = CandidateGate(
        invalidation_reporter=_Reporter(),
        candidate_reader=_Reader(readback),
        formal_reviewer=reviewer,
        check_runner=_Checks(),
        assurance_policy=_Policy(mode, specialist_policy_id),
        acceptance_facts=CandidateAcceptanceFacts(
            target_branch="main",
            integration_node_key="integration:issue:115",
            accepted_sequence=1,
            check_environment_digest="6" * 64,
            delivery_identity_digest="7" * 64,
            protected_surfaces=("protected/path",),
        ),
    )
    return gate, reviewer, parent


@pytest.fixture
def strict_gate():
    return _gate(
        mode=AssuranceMode.STRICT,
        specialist_policy_id="security",
    )


@pytest.fixture
def strict_decision_gate():
    return _gate(mode=AssuranceMode.STRICT)


@pytest.fixture
def invalid_transport_gate():
    return _gate(
        mode=AssuranceMode.STANDARD,
        reviewer=_Reviewer(invalid_transport=True),
    )


@pytest.fixture
def strict_retry_budget_gate():
    return _gate(
        mode=AssuranceMode.STRICT,
        specialist_policy_id="security",
        reviewer=_StrictRetryBudgetReviewer(),
    )


@pytest.fixture
def rejected_gate():
    return _gate(
        mode=AssuranceMode.STANDARD,
        reviewer=_Reviewer(reject=True),
    )


def test_strict_uses_primary_then_at_most_one_specialist(strict_gate):
    gate, reviewer, parent = strict_gate
    result = gate.gate_candidate(parent, "refs/heads/candidate")
    assert result.status == CandidateGateStatus.REVIEW_ACCEPTED
    assert [action.kind for action in reviewer.actions] == [
        "formal_review",
        "specialist_review",
    ]
    assert reviewer.actions[0].purpose == WorkRunPurpose.formal_review()
    assert reviewer.actions[1].purpose == WorkRunPurpose.specialist_review(
        "security"
    )
    assert reviewer.actions[0].subject.digest == reviewer.actions[1].subject.digest


def test_strict_without_specialist_returns_typed_decision(strict_decision_gate):
    gate, reviewer, parent = strict_decision_gate
    result = gate.gate_candidate(parent, "refs/heads/candidate")
    assert result.status == CandidateGateStatus.DECISION_REQUIRED
    assert result.review_subject is not None
    assert reviewer.actions == []


def test_invalid_transport_retries_same_subject_as_review_strong(
    invalid_transport_gate,
):
    gate, reviewer, parent = invalid_transport_gate
    gate.gate_candidate(parent, "refs/heads/candidate")
    assert [action.kind for action in reviewer.actions] == [
        "formal_review",
        "review_strong",
    ]
    assert reviewer.actions[0].purpose == WorkRunPurpose.formal_review()
    assert reviewer.actions[1].purpose == WorkRunPurpose.invalid_review_payload_retry()
    assert reviewer.actions[0].subject.digest == reviewer.actions[1].subject.digest


def test_strict_transport_retry_budget_is_shared_by_subject(
    strict_retry_budget_gate,
):
    gate, reviewer, parent = strict_retry_budget_gate
    caught = None
    try:
        gate.gate_candidate(parent, "refs/heads/candidate")
    except Exception as error:  # noqa: BLE001 - assert the typed boundary below
        caught = error

    assert [action.kind for action in reviewer.actions] == [
        "formal_review",
        "review_strong",
        "specialist_review",
    ]
    assert len({action.subject.digest for action in reviewer.actions}) == 1
    assert isinstance(caught, CandidateGateError)
    assert caught.code == "CANDIDATE_GATE_REVIEW_TRANSPORT_RETRY_EXHAUSTED"
    assert caught.detail == (
        "Review transport retry budget was already consumed for this ReviewSubject"
    )


def test_valid_rejection_does_not_repeat_unchanged_subject(rejected_gate):
    gate, reviewer, parent = rejected_gate
    first = gate.gate_candidate(parent, "refs/heads/candidate")
    second = gate.gate_candidate(parent, "refs/heads/candidate")
    assert first.status == CandidateGateStatus.REPAIR_REQUIRED
    assert second.status == CandidateGateStatus.REPAIR_REQUIRED
    assert len(reviewer.actions) == 1
