from __future__ import annotations

from dataclasses import replace
import sqlite3
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.batch_integrator import (
    DeliveryAttributionAmbiguous,
    DeliveryIdentityMismatch,
)
from v8_batch_test_support import (
    BatchRecoveryHarness,
    CrashInjected,
    make_batch_request,
    make_hosted_result_receipt,
    make_integrator,
    make_three_standard_receipts,
)


@pytest.fixture
def batch_harness(tmp_path):
    return BatchRecoveryHarness(tmp_path)


def test_infrastructure_retry_keeps_one_batch_sha(batch_harness):
    observations = batch_harness.run_outcomes(
        "infrastructure_failure",
        "infrastructure_failure",
        "infrastructure_failure",
    )

    assert observations[-1].phase == "blocked"
    assert len(set(batch_harness.retry_shas)) == 1
    assert len(batch_harness.retry_shas) == 2


def test_infrastructure_failure_retries_same_batch_sha_at_most_twice(tmp_path):
    integrator, drivers = make_integrator(
        tmp_path,
        hosted_outcomes=(
            "infrastructure_failure",
            "infrastructure_failure",
            "infrastructure_failure",
        ),
    )
    action = integrator.prepare(
        make_batch_request(accepted_candidates=make_three_standard_receipts())
    )

    first = integrator.execute(action)
    second = integrator.execute(action)
    third = integrator.execute(action)

    assert first.phase == second.phase == "wait"
    assert third.phase == "blocked"
    assert drivers.hosted.retry_shas == [action.batch_sha, action.batch_sha]
    assert drivers.hosted.hosted_read_shas == [
        action.batch_sha,
        action.batch_sha,
        action.batch_sha,
    ]
    assert all(sha == action.batch_sha for sha in drivers.hosted.retry_shas)


def test_terminal_hosted_receipt_is_adopted_after_restart_without_provider_reread(
    tmp_path,
):
    integrator, drivers = make_integrator(
        tmp_path,
        hosted_outcomes=("passed",),
        crash_after="hosted_receipt_persisted",
    )
    action = integrator.prepare(
        make_batch_request(accepted_candidates=make_three_standard_receipts())
    )

    with pytest.raises(CrashInjected, match="hosted_receipt_persisted"):
        integrator.execute(action)

    assert drivers.hosted.hosted_read_shas == [action.batch_sha]
    assert drivers.hosted.integrated_shas == []
    assert (
        integrator.journal.read_hosted_result(
            action.stable_action_id, action.batch_sha, "hosted", "check:1"
        )
        is not None
    )

    restarted, restarted_drivers = make_integrator(tmp_path)
    observation = restarted.execute(action)

    assert observation.phase == "complete"
    assert restarted_drivers.hosted.hosted_read_calls == 0
    assert restarted_drivers.hosted.integrated_shas == [action.batch_sha]
    assert drivers.hosted.hosted_read_shas == [action.batch_sha]


def test_restart_rejects_persisted_hosted_receipt_with_wrong_action_or_observation_digest(
    tmp_path,
):
    integrator, _drivers = make_integrator(tmp_path)
    action = integrator.prepare(
        make_batch_request(accepted_candidates=make_three_standard_receipts())
    )
    receipt = make_hosted_result_receipt(
        stable_action_id=action.stable_action_id,
        batch_sha=action.batch_sha,
    )
    integrator.journal.persist_hosted_result(receipt)
    store_path = tmp_path / "v8.sqlite3"
    with sqlite3.connect(store_path) as connection:
        connection.execute(
            """
            UPDATE v8_batch_hosted_receipts
            SET observation_digest = ?
            WHERE stable_action_id = ?
              AND batch_sha = ?
              AND suite_id = ?
              AND provider_check_id = ?
            """,
            (
                "f" * 64,
                receipt.stable_action_id,
                receipt.batch_sha,
                receipt.suite_id,
                receipt.provider_check_id,
            ),
        )

    restarted, _restarted_drivers = make_integrator(tmp_path)
    with pytest.raises(DeliveryIdentityMismatch):
        restarted.execute(action)


def test_multi_member_code_failure_dissolves_once_into_singletons(tmp_path):
    integrator, drivers = make_integrator(
        tmp_path,
        hosted_outcomes=("code_failure", "passed", "passed", "passed"),
    )
    action = integrator.prepare(
        make_batch_request(accepted_candidates=make_three_standard_receipts())
    )

    first = integrator.execute(action)
    second = integrator.execute(action)
    third = integrator.execute(action)
    fourth = integrator.execute(action)

    assert first.fallback_generation == 1
    assert [item for item in drivers.created_batch_member_sets if len(item) == 1] == [
        ("issue:1",),
        ("issue:2",),
        ("issue:3",),
    ]
    assert fourth.phase == "complete"
    assert tuple(proof.member_ticket_keys for proof in fourth.delivery_proofs) == (
        ("issue:1",),
        ("issue:2",),
        ("issue:3",),
    )
    assert all(
        proof.delivery_stable_action_id != action.stable_action_id
        for proof in fourth.delivery_proofs
    )
    assert [proof.batch_sha for proof in fourth.delivery_proofs] == (
        drivers.hosted.integrated_shas
    )
    assert drivers.formation_calls == 1
    assert drivers.composition_calls == 4

    with pytest.raises(DeliveryIdentityMismatch):
        replace(fourth, delivery_proofs=fourth.delivery_proofs[:-1])


def test_fallback_has_one_singleton_proof_per_member(batch_harness):
    result = batch_harness.run_successful_singleton_fallback()

    assert result.fallback_generation == 1
    assert tuple(len(proof.member_ticket_keys) for proof in result.delivery_proofs) == (
        1,
        1,
        1,
    )


def test_singleton_fallback_reuses_member_candidate_and_evidence_without_review(
    tmp_path,
):
    integrator, drivers = make_integrator(
        tmp_path,
        hosted_outcomes=("code_failure", "passed", "passed", "passed"),
    )
    candidates = make_three_standard_receipts()
    action = integrator.prepare(make_batch_request(accepted_candidates=candidates))

    for _ in range(4):
        integrator.execute(action)

    assert drivers.candidategate_calls == 0
    assert drivers.review_calls == 0
    assert drivers.singleton_member_candidate_shas == [
        item.candidate_sha for item in candidates
    ]
    assert drivers.singleton_member_evidence_digests == [
        item.evidence_digests for item in candidates
    ]


def test_only_failing_singleton_requests_worker_resume_with_review_ledger_context(
    tmp_path,
):
    integrator, drivers = make_integrator(
        tmp_path,
        hosted_outcomes=("code_failure", "code_failure", "passed", "passed"),
    )
    candidates = make_three_standard_receipts()
    action = integrator.prepare(make_batch_request(accepted_candidates=candidates))

    observations = [integrator.execute(action) for _ in range(4)]
    resume_members = [
        member
        for observation in observations
        for member in observation.members
        if member.status == "resume_required"
    ]

    assert [(member.ticket_key, member.work_run_key) for member in resume_members] == [
        ("issue:1", "work-run:1")
    ]
    assert drivers.resume_directives == [
        ("work-run:1", candidates[0].review_finding_ledger_digest)
    ]


def test_singleton_failure_never_recursively_splits_or_regroups(tmp_path):
    integrator, drivers = make_integrator(
        tmp_path,
        hosted_outcomes=(
            "code_failure",
            "code_failure",
            "code_failure",
            "code_failure",
        ),
    )
    action = integrator.prepare(
        make_batch_request(accepted_candidates=make_three_standard_receipts())
    )

    for _ in range(8):
        integrator.execute(action)

    assert drivers.created_batch_member_sets.count(("issue:1",)) == 1
    assert all(len(member_set) <= 1 for member_set in drivers.created_batch_member_sets[1:])
    assert drivers.formation_calls == 1
    assert drivers.composition_calls == 4


def test_strict_candidate_is_singleton_on_initial_delivery_and_recovery(tmp_path):
    from v8_batch_test_support import make_accepted_candidate_receipt

    strict = make_accepted_candidate_receipt(ticket_key="issue:strict", assurance="strict")
    integrator, drivers = make_integrator(
        tmp_path, hosted_outcomes=("code_failure", "passed")
    )
    action = integrator.prepare(make_batch_request(accepted_candidates=(strict,)))

    failed = integrator.execute(action)
    passed = integrator.execute(action)

    assert failed.fallback_generation == 0
    assert passed.phase == "complete"
    assert drivers.created_batch_member_sets == [("issue:strict",)]


@pytest.mark.parametrize(
    ("failure_mode", "expected_error"),
    [
        ("wrong_batch_sha", DeliveryIdentityMismatch),
        ("ambiguous_provider", DeliveryAttributionAmbiguous),
    ],
)
def test_identity_mismatch_and_ambiguous_attribution_preserve_evidence_and_forbid_fallback_or_resume(
    tmp_path, failure_mode, expected_error
):
    integrator, drivers = make_integrator(
        tmp_path / failure_mode, delivery_failure=failure_mode
    )
    candidates = make_three_standard_receipts()
    action = integrator.prepare(make_batch_request(accepted_candidates=candidates))

    with pytest.raises(expected_error):
        integrator.execute(action)

    assert drivers.created_batch_member_sets == [
        ("issue:1", "issue:2", "issue:3")
    ]
    assert drivers.resume_directives == []
    assert drivers.preserved_evidence_digests == [
        item.evidence_digests for item in candidates
    ]
