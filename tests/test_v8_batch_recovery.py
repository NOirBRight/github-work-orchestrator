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


