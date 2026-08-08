from __future__ import annotations

from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.candidate_gate import (  # noqa: E402
    AuditFailureKind,
    AuditFailureRoute,
    CandidateDiffEntryV1,
    CandidateDiffRecordV1,
    CandidateGate,
    CandidateGateParent,
    CandidateGateStatus,
    CandidateIdentity,
    CandidateReadback,
    CandidateGateError,
    DeterministicAuditFailure,
    FormalReviewResult,
)
from gwo_v8.runtime_gateway import (  # noqa: E402
    CapabilityPolicy,
    CapabilityPolicyProof,
    WorkRunPurpose,
    WorkRunSubject,
)
from gwo_v8._canonical import digest_value  # noqa: E402


class _Reporter:
    def report_plan_invalidation(self, _subject, _evidence, _report):
        raise AssertionError("these fixtures do not report Plan Invalidation")


class _Reader:
    def __init__(self, readback):
        self.readback = readback
        self.calls = []

    def read_candidate(self, repository, reported_reference):
        self.calls.append((repository, reported_reference))
        return self.readback


class _Reviewer:
    capability_policy_proof = CapabilityPolicyProof(
        capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
        authority_record_digest="9" * 64,
    )

    def __init__(self):
        self.actions = []

    def review(self, action):
        self.actions.append(action)
        return FormalReviewResult(subject_digest=action.subject.digest)


class _Checks:
    def __init__(
        self,
        failed=False,
        check_ids=("check:unit",),
        empty=False,
        tampered_observation=False,
    ):
        self.failed = failed
        self.check_ids = check_ids
        self.empty = empty
        self.tampered_observation = tampered_observation

    def run(self, _parent, readback):
        if self.empty:
            return ()
        failure = None
        outcome = "passed"
        if self.failed:
            outcome = "failed"
            failure = DeterministicAuditFailure(
                kind=AuditFailureKind.AFFECTED_CHECK,
                route=AuditFailureRoute.ORDINARY_UNAUTHORIZED,
                code="CHECK_FAILED",
                detail="the affected deterministic check failed",
            )
        from gwo_v8.candidate_gate import CandidateCheckEvidence

        values = []
        for check_id in self.check_ids:
            observation_digest = digest_value(
                {
                    "kind": "candidate_check_observation.v1",
                    "check_id": check_id,
                    "candidate_tree_oid": readback.candidate.candidate_tree_oid,
                    "diff_record_digest": readback.diff_record.digest,
                    "outcome": outcome,
                    "failure_digest": None if failure is None else failure.digest,
                }
            )
            values.append(
                CandidateCheckEvidence(
                    check_id=check_id,
                    candidate_tree_oid=readback.candidate.candidate_tree_oid,
                    outcome=outcome,
                    definition_digest="a" * 64,
                    observation_digest=(
                        "f" * 64
                        if self.tampered_observation
                        else observation_digest
                    ),
                    failure=failure,
                )
            )
        return tuple(values)


class _Policy:
    def __init__(self, mode, required_check_ids=("check:unit",)):
        self.mode = mode
        self.required_check_ids = required_check_ids

    def derive(self, _parent, _readback, _checks):
        from gwo_v8.candidate_gate import AssuranceRequirement

        return AssuranceRequirement(
            policy_id="policy:candidate-assurance",
            policy_version="1",
            mode=self.mode,
            required_check_ids=self.required_check_ids,
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


def _gate(
    *,
    mode,
    failed=False,
    reviewer=None,
    required_check_ids=("check:unit",),
    check_ids=("check:unit",),
    empty_checks=False,
    tampered_observation=False,
):
    parent, readback = _parent_and_readback()
    reader = _Reader(readback)
    reviewer = _Reviewer() if reviewer is None else reviewer
    from gwo_v8.candidate_gate import AssuranceMode, CandidateAcceptanceFacts

    facts = CandidateAcceptanceFacts(
        target_branch="main",
        integration_node_key="integration:issue:114",
        accepted_sequence=1,
        check_environment_digest="6" * 64,
        delivery_identity_digest="7" * 64,
        protected_surfaces=("protected/path",),
    )
    gate = CandidateGate(
        invalidation_reporter=_Reporter(),
        candidate_reader=reader,
        formal_reviewer=reviewer,
        check_runner=_Checks(
            failed=failed,
            check_ids=check_ids,
            empty=empty_checks,
            tampered_observation=tampered_observation,
        ),
        assurance_policy=_Policy(mode, required_check_ids),
        acceptance_facts=facts,
    )
    return gate, reader, reviewer, parent


@pytest.fixture
def gate_with_standard():
    from gwo_v8.candidate_gate import AssuranceMode

    return _gate(mode=AssuranceMode.STANDARD)


@pytest.fixture
def gate_with_failed_check():
    from gwo_v8.candidate_gate import AssuranceMode

    gate, reader, reviewer, parent = _gate(
        mode=AssuranceMode.STANDARD,
        failed=True,
    )
    return gate, reviewer, parent


@pytest.fixture
def no_review_gate():
    from gwo_v8.candidate_gate import AssuranceMode

    gate, _reader, reviewer, parent = _gate(mode=AssuranceMode.NO_REVIEW)
    return gate, reviewer, parent


@pytest.fixture
def accepted_candidate_result(gate_with_standard):
    gate, _reader, _reviewer, parent = gate_with_standard
    return gate.gate_candidate(parent, "refs/heads/candidate")


def test_standard_gate_reads_once_and_runs_one_primary_review(gate_with_standard):
    gate, reader, reviewer, parent = gate_with_standard
    result = gate.gate_candidate(parent, "refs/heads/candidate")
    assert result.status == CandidateGateStatus.REVIEW_ACCEPTED
    assert reader.calls == [(parent.runtime_subject.repository, "refs/heads/candidate")]
    assert [action.kind for action in reviewer.actions] == ["formal_review"]
    assert result.candidate_receipt is not None
    assert result.accepted_candidate_receipt.candidate_receipt_digest == (
        result.candidate_receipt.digest
    )


def test_deterministic_failure_stops_before_reviewer(gate_with_failed_check):
    gate, reviewer, parent = gate_with_failed_check
    result = gate.gate_candidate(parent, "refs/heads/candidate")
    assert result.status == CandidateGateStatus.ORDINARY_REJECTED
    assert reviewer.actions == []


def test_no_review_allowlist_uses_zero_calls(no_review_gate):
    from gwo_v8.candidate_gate import ReviewFindingLedger

    gate, reviewer, parent = no_review_gate
    result = gate.gate_candidate(parent, "refs/heads/candidate")
    assert result.status == CandidateGateStatus.REVIEW_ACCEPTED
    assert reviewer.actions == []
    assert result.review_finding_ledger_digest == ReviewFindingLedger(entries=()).digest
    assert result.accepted_candidate_receipt.assurance == "no_review"


def test_missing_required_check_fails_before_reviewer():
    from gwo_v8.candidate_gate import AssuranceMode

    gate, _reader, reviewer, parent = _gate(
        mode=AssuranceMode.STANDARD,
        required_check_ids=("check:unit", "check:required"),
    )
    with pytest.raises(CandidateGateError) as raised:
        gate.gate_candidate(parent, "refs/heads/candidate")
    assert raised.value.code in {
        "CANDIDATE_GATE_CHECK_INVALID",
        "CANDIDATE_GATE_ASSURANCE_INVALID",
    }
    assert reviewer.actions == []


def test_duplicate_check_id_fails_before_reviewer():
    from gwo_v8.candidate_gate import AssuranceMode

    gate, _reader, reviewer, parent = _gate(
        mode=AssuranceMode.STANDARD,
        check_ids=("check:unit", "check:unit"),
    )
    with pytest.raises(CandidateGateError) as raised:
        gate.gate_candidate(parent, "refs/heads/candidate")
    assert raised.value.code in {
        "CANDIDATE_GATE_CHECK_INVALID",
        "CANDIDATE_GATE_ASSURANCE_INVALID",
    }
    assert reviewer.actions == []


def test_unexpected_check_id_fails_before_reviewer():
    from gwo_v8.candidate_gate import AssuranceMode

    gate, _reader, reviewer, parent = _gate(
        mode=AssuranceMode.STANDARD,
        check_ids=("check:unexpected",),
    )
    with pytest.raises(CandidateGateError) as raised:
        gate.gate_candidate(parent, "refs/heads/candidate")
    assert raised.value.code in {
        "CANDIDATE_GATE_CHECK_INVALID",
        "CANDIDATE_GATE_ASSURANCE_INVALID",
    }
    assert reviewer.actions == []


def test_no_required_check_evidence_fails_before_reviewer():
    from gwo_v8.candidate_gate import AssuranceMode

    gate, _reader, reviewer, parent = _gate(
        mode=AssuranceMode.STANDARD,
        empty_checks=True,
    )
    with pytest.raises(CandidateGateError) as raised:
        gate.gate_candidate(parent, "refs/heads/candidate")
    assert raised.value.code in {
        "CANDIDATE_GATE_CHECK_INVALID",
        "CANDIDATE_GATE_ASSURANCE_INVALID",
    }
    assert reviewer.actions == []


@pytest.mark.parametrize(
    "required_check_ids",
    [(), ("check:unit", "check:unit"), ("check:unit", "check:required")],
)
def test_assurance_requirement_rejects_noncanonical_required_checks(
    required_check_ids,
):
    from gwo_v8.candidate_gate import AssuranceMode

    gate, _reader, reviewer, parent = _gate(
        mode=AssuranceMode.STANDARD,
        required_check_ids=required_check_ids,
    )
    with pytest.raises(CandidateGateError) as raised:
        gate.gate_candidate(parent, "refs/heads/candidate")
    assert raised.value.code == "CANDIDATE_GATE_ASSURANCE_INVALID"
    assert reviewer.actions == []


def test_tampered_check_observation_digest_fails_before_reviewer():
    from gwo_v8.candidate_gate import AssuranceMode

    gate, _reader, reviewer, parent = _gate(
        mode=AssuranceMode.STANDARD,
        tampered_observation=True,
    )
    with pytest.raises(CandidateGateError) as raised:
        gate.gate_candidate(parent, "refs/heads/candidate")
    assert raised.value.code == "CANDIDATE_GATE_CHECK_INVALID"
    assert reviewer.actions == []


def test_accepted_candidate_receipt_matches_batch_handoff_fields(
    accepted_candidate_result,
):
    receipt = accepted_candidate_result.accepted_candidate_receipt
    assert set(receipt.canonical()) == {
        "kind",
        "repository",
        "campaign_key",
        "plan_revision_digest",
        "target_branch",
        "ticket_key",
        "work_run_key",
        "integration_node_key",
        "accepted_sequence",
        "base_sha",
        "base_tree_oid",
        "candidate_sha",
        "candidate_tree_oid",
        "candidate_receipt_digest",
        "diff_schema_version",
        "diff_record_digest",
        "authority_subtree_digest",
        "policy_witness_digest",
        "review_subject_digest",
        "assurance",
        "assurance_requirement_digest",
        "check_environment_digest",
        "delivery_identity_digest",
        "interaction_keys",
        "protected_surfaces",
        "gitlink_change",
        "evidence_digests",
        "review_finding_ledger_digest",
        "receipt_digest",
    }
    assert "result_digest" not in receipt.canonical()
    assert receipt.base_sha == accepted_candidate_result.candidate_receipt.base_commit_oid
    assert receipt.candidate_sha == accepted_candidate_result.candidate_receipt.candidate_commit_oid
    assert receipt.candidate_receipt_digest == accepted_candidate_result.candidate_receipt.digest


def test_interaction_keys_are_concrete_and_derived_from_candidate_diff(
    accepted_candidate_result,
):
    from gwo_v8.candidate_gate import InteractionKey

    keys = accepted_candidate_result.accepted_candidate_receipt.interaction_keys
    assert all(type(key) is InteractionKey for key in keys)
    assert tuple(key.value for key in keys) == tuple(
        sorted(accepted_candidate_result.candidate_diff_record.changed_path_tokens)
    )
    assert all(key.namespace == "candidate-path" for key in keys)
    assert all(
        key.canonical()["classification"]
        in {"ordinary", "protected", "high_coupling", "non_decomposable"}
        for key in keys
    )


def test_gate_candidate_never_writes_kernel_state(gate_with_standard):
    gate, _reader, _reviewer, parent = gate_with_standard
    gate.gate_candidate(parent, "refs/heads/candidate")
    assert not hasattr(gate, "advance")
    assert not hasattr(gate, "persist_candidate_receipt")
