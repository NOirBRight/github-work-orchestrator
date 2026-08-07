from __future__ import annotations

from dataclasses import replace
import json
import sqlite3
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.batch_integrator import (
    BatchDeliveryAction,
    BatchIntegratorError,
    BatchTarget,
    DeliveryAttributionAmbiguous,
    DeliveryIdentityMismatch,
)
from v8_batch_test_support import (
    BatchRecoveryHarness,
    CrashInjected,
    make_batch_request,
    make_accepted_candidate_receipt,
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


def test_pending_hosted_result_resumes_with_a_fresh_read(tmp_path):
    integrator, drivers = make_integrator(
        tmp_path, hosted_outcomes=("pending", "passed")
    )
    action = integrator.prepare(
        make_batch_request(accepted_candidates=make_three_standard_receipts())
    )

    pending = integrator.execute(action)
    complete = integrator.execute(action)

    assert pending.phase == "wait"
    assert pending.reason == "HostedResultPending"
    assert complete.phase == "complete"
    assert drivers.hosted.hosted_read_calls == 2


def test_failed_direct_singleton_is_durable_decision_without_unchanged_retry(
    tmp_path,
):
    strict = make_accepted_candidate_receipt(
        ticket_key="issue:strict", assurance="strict"
    )
    integrator, drivers = make_integrator(tmp_path, hosted_outcomes=("code_failure",))
    action = integrator.prepare(make_batch_request(accepted_candidates=(strict,)))

    failed = integrator.execute(action)

    assert failed.phase == "decision"
    assert failed.reason == "WorkerResumeRequired"
    assert (
        integrator.journal.read_terminal_hosted_result(
            action.stable_action_id, action.batch_sha, "hosted"
        )
        is not None
    )

    restarted, restarted_drivers = make_integrator(tmp_path, hosted_outcomes=("passed",))
    adopted = restarted.execute(action)

    assert adopted.phase == "decision"
    assert adopted.reason == "WorkerResumeRequired"
    assert drivers.hosted.hosted_read_calls == 1
    assert restarted_drivers.hosted.hosted_read_calls == 0
    assert restarted_drivers.hosted.integrated_shas == []


def test_partial_parent_selection_does_not_dissolve_unselected_candidates(tmp_path):
    strict = make_accepted_candidate_receipt(
        ticket_key="issue:1", assurance="strict", accepted_sequence=1
    )
    other = make_three_standard_receipts()[1:]
    integrator, drivers = make_integrator(tmp_path, hosted_outcomes=("code_failure",))
    action = integrator.prepare(
        make_batch_request(accepted_candidates=(strict, *other))
    )

    failed = integrator.execute(action)

    assert failed.phase == "decision"
    assert failed.fallback_generation == 0
    assert drivers.created_batch_member_sets == [("issue:1",)]


def test_supported_composition_failure_routes_to_one_singleton_fallback(tmp_path):
    integrator, drivers = make_integrator(tmp_path)
    compose = drivers.git.compose_batch
    failed_once = True

    def fail_first_multi_member_compose(batch_id, target, members):
        nonlocal failed_once
        if failed_once and len(members) > 1:
            failed_once = False
            raise BatchIntegratorError(
                "BATCH_COMPOSITION_CONFLICT", "test composition conflict"
            )
        return compose(batch_id, target, members)

    drivers.git.compose_batch = fail_first_multi_member_compose
    action = integrator.prepare(
        make_batch_request(accepted_candidates=make_three_standard_receipts())
    )

    observations = []
    for _ in range(8):
        observation = integrator.execute(action)
        observations.append(observation)
        if observation.phase in {"complete", "decision", "blocked"}:
            break

    assert observations[0].fallback_generation == 1
    assert observations[-1].phase == "complete"
    assert [
        member_set for member_set in drivers.created_batch_member_sets if len(member_set) == 1
    ] == [("issue:1",), ("issue:2",), ("issue:3",)]


def test_singleton_fallback_construction_crash_does_not_complete_empty_queue(tmp_path):
    integrator, drivers = make_integrator(
        tmp_path, hosted_outcomes=("code_failure", "passed", "passed", "passed")
    )
    action = integrator.prepare(
        make_batch_request(accepted_candidates=make_three_standard_receipts())
    )
    original_cas = integrator.journal.compare_and_swap_action
    crashed = False

    def crash_after_parent_fallback_persisted(*args, **kwargs):
        nonlocal crashed
        record = kwargs.get("next_record")
        result = original_cas(*args, **kwargs)
        if not crashed and record is not None and record.fallback_generation == 1:
            crashed = True
            raise RuntimeError("fallback construction crash")
        return result

    integrator.journal.compare_and_swap_action = crash_after_parent_fallback_persisted
    with pytest.raises(RuntimeError, match="fallback construction crash"):
        integrator.execute(action)

    durable = integrator.journal.read_action(action.stable_action_id)
    assert durable is not None
    durable_state = json.loads(durable.state_json)
    assert durable.fallback_generation == 1
    assert durable_state["singleton_queue"] == []
    assert durable_state["singleton_materialization_complete"] is False

    restarted, restarted_drivers = make_integrator(tmp_path)
    observations = []
    for _ in range(8):
        observation = restarted.execute(action)
        observations.append(observation)
        if observation.phase in {"complete", "decision", "blocked"}:
            break

    assert observations[-1].phase == "complete"
    assert [
        member_set
        for member_set in restarted_drivers.created_batch_member_sets
        if len(member_set) == 1
    ] == [("issue:1",), ("issue:2",), ("issue:3",)]
    assert drivers.created_batch_member_sets == [("issue:1", "issue:2", "issue:3")]


def test_retry_intent_is_persisted_before_external_retry_crash(tmp_path):
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
    retry = drivers.hosted.retry_hosted_idempotent
    crashed = False

    def crash_after_retry_request(*args, **kwargs):
        nonlocal crashed
        result = retry(*args, **kwargs)
        if not crashed:
            crashed = True
            raise RuntimeError("retry crash window")
        return result

    drivers.hosted.retry_hosted_idempotent = crash_after_retry_request
    integrator.execute(action)
    with pytest.raises(RuntimeError, match="retry crash window"):
        integrator.execute(action)

    durable = integrator.journal.read_action(action.stable_action_id)
    assert durable is not None
    state = json.loads(durable.state_json)
    assert durable.retry_count == 1
    assert state["retry_intent"]["batch_sha"] == action.batch_sha
    assert state["retry_intent"]["idempotency_key"]

    drivers.hosted.retry_hosted_idempotent = retry
    observations = []
    for _ in range(4):
        observation = integrator.execute(action)
        observations.append(observation)
        if observation.phase == "blocked":
            break

    assert observations[-1].phase == "blocked"
    assert durable.batch_sha == action.batch_sha
    assert integrator.journal.read_action(action.stable_action_id).retry_count == 2
    assert len(drivers.hosted.retry_shas) == 2
    assert len(set(drivers.hosted.retry_shas)) == 1


def test_infrastructure_retry_passes_durable_idempotency_key_to_declared_driver(
    tmp_path,
):
    integrator, drivers = make_integrator(
        tmp_path,
        hosted_outcomes=("infrastructure_failure", "infrastructure_failure"),
    )
    action = integrator.prepare(
        make_batch_request(accepted_candidates=make_three_standard_receipts())
    )
    retry_keys = []

    def retry_with_key(repository, batch_sha, provider_check_id, idempotency_key):
        retry_keys.append((repository, batch_sha, provider_check_id, idempotency_key))

    drivers.hosted.retry_hosted_idempotent = None
    drivers.hosted.retry_hosted = retry_with_key

    waiting = integrator.execute(action)
    assert waiting.phase == "wait"
    resumed = integrator.execute(action)

    assert resumed.phase == "wait"
    assert len(retry_keys) == 1
    repository, batch_sha, provider_check_id, idempotency_key = retry_keys[0]
    assert repository == "owner/repo"
    assert batch_sha == action.batch_sha
    assert provider_check_id == "check:1"
    durable = integrator.journal.read_action(action.stable_action_id)
    assert durable is not None
    state = json.loads(durable.state_json)
    assert idempotency_key == state["retry_idempotency_key"]


def test_singleton_fallback_materializes_against_authoritative_advanced_target(
    tmp_path,
):
    integrator, drivers = make_integrator(
        tmp_path,
        hosted_outcomes=("code_failure", "passed", "passed", "passed"),
    )
    request = make_batch_request(accepted_candidates=make_three_standard_receipts())
    action = integrator.prepare(request)
    advanced_target = replace(
        request.target,
        target_head_sha="c" * 40,
        target_tree_oid="d" * 40,
    )
    read_count = 0

    def read_target_with_advance(target):
        nonlocal read_count
        read_count += 1
        return advanced_target

    drivers.git.read_target = read_target_with_advance

    observation = integrator.execute(action)

    assert observation.fallback_generation == 1
    parent = integrator.journal.read_action(action.stable_action_id)
    assert parent is not None
    state = json.loads(parent.state_json)
    assert read_count >= 1
    assert [
        entry["target"]["target_head_sha"] for entry in state["singleton_queue"]
    ] == [advanced_target.target_head_sha] * 3
    assert drivers.git.clean_base_advance_calls[-3:] == [
        "issue:1",
        "issue:2",
        "issue:3",
    ]


def test_singleton_child_action_binds_actual_child_request_digest(tmp_path):
    integrator, _drivers = make_integrator(
        tmp_path, hosted_outcomes=("code_failure", "passed", "passed", "passed")
    )
    request = make_batch_request(accepted_candidates=make_three_standard_receipts())
    action = integrator.prepare(request)
    integrator.execute(action)

    parent = integrator.journal.read_action(action.stable_action_id)
    assert parent is not None
    state = json.loads(parent.state_json)
    candidates = {candidate.ticket_key: candidate for candidate in request.accepted_candidates}
    for entry in state["singleton_queue"]:
        child_action = BatchDeliveryAction(**entry["action"])
        child_request = replace(
            request,
            stable_action_id=child_action.stable_action_id,
            target=BatchTarget(**entry["target"]),
            accepted_candidates=(candidates[entry["member"]["ticket_key"]],),
        )
        assert child_action.request_digest == child_request.request_digest
        assert integrator.journal.read_action(child_action.stable_action_id).request_digest == (
            child_request.request_digest
        )
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


def test_mismatched_durable_hosted_receipt_fails_before_provider_read(tmp_path):
    integrator, _drivers = make_integrator(tmp_path)
    action = integrator.prepare(
        make_batch_request(accepted_candidates=make_three_standard_receipts())
    )
    integrator.journal.persist_hosted_result(
        make_hosted_result_receipt(
            stable_action_id=action.stable_action_id,
            batch_sha="b" * 40,
        )
    )

    restarted, restarted_drivers = make_integrator(tmp_path, hosted_outcomes=("passed",))
    with pytest.raises(DeliveryIdentityMismatch):
        restarted.execute(action)

    assert restarted_drivers.hosted.hosted_read_calls == 0


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
    assert passed.phase == "decision"
    assert passed.reason == "WorkerResumeRequired"
    assert drivers.created_batch_member_sets == [("issue:strict",)]
    assert drivers.hosted.hosted_read_calls == 1


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
