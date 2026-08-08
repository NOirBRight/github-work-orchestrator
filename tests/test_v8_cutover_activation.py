from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8.activation import ActivationError  # noqa: E402
from tests.cutover_guard_test_support import activation_fixture  # noqa: E402


def test_cutover_without_guard_token_returns_blocked_before_any_mutation(tmp_path):
    fixture = activation_fixture(tmp_path)

    outcome = fixture.controller.cutover(
        fixture.compiled_plan,
        canary=fixture.accepted_canary,
        guard_subject=fixture.subject,
        guard_receipt=None,
        writer_generation="v8",
        worker_capacity=8,
        coordinator_capacity=1,
    )

    assert outcome.status == "blocked"
    assert outcome.blockers == ("CUTOVER_GUARD_REQUIRED",)
    assert fixture.mutation_calls() == ()
    assert fixture.transitions.history(fixture.repository) == ()
    assert fixture.publication.read_active(fixture.repository) is None
    assert fixture.publication.durable.read_current_activation(fixture.repository) is None


def test_stale_guard_token_returns_blocked_before_v61_stop_or_activation(tmp_path):
    fixture = activation_fixture(tmp_path)
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None
    fixture.writer_readback.value = replace(
        fixture.writer_readback.value,
        control_ref_digest="d" * 64,
    )

    outcome = fixture.controller.cutover(
        fixture.compiled_plan,
        canary=fixture.accepted_canary,
        guard_subject=fixture.subject,
        guard_receipt=report.receipt,
        writer_generation="v8",
        worker_capacity=8,
        coordinator_capacity=1,
    )

    assert outcome.status == "blocked"
    assert outcome.blockers == ("CUTOVER_GUARD_TOKEN_STALE",)
    assert fixture.mutation_calls() == ()
    assert fixture.legacy.readback(fixture.repository).authority_state == "authoritative_quiescent"


def test_fresh_guard_allows_existing_activation_receipt_commit_and_readback(tmp_path):
    fixture = activation_fixture(tmp_path)
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None

    outcome = fixture.controller.cutover(
        fixture.compiled_plan,
        canary=fixture.accepted_canary,
        guard_subject=fixture.subject,
        guard_receipt=report.receipt,
        writer_generation="v8",
        worker_capacity=8,
        coordinator_capacity=1,
    )

    assert outcome.status == "cut_over"
    receipt = fixture.publication.durable.read_current_activation(fixture.repository)
    assert receipt is not None
    assert receipt.repository == fixture.repository
    assert receipt.writer_generation == "v8"
    assert receipt.plan_digest == fixture.compiled_plan.digest
    assert fixture.transitions.allows_new_work(
        fixture.repository,
        "v8",
        receipt.activation_id,
    )


def test_pending_activation_cannot_admit_work_before_activation_receipt(tmp_path):
    fixture = activation_fixture(tmp_path, fail_after={"publish_activation"})
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None

    with pytest.raises(ActivationError) as error:
        fixture.controller.cutover(
            fixture.compiled_plan,
            canary=fixture.accepted_canary,
            guard_subject=fixture.subject,
            guard_receipt=report.receipt,
            writer_generation="v8",
            worker_capacity=8,
            coordinator_capacity=1,
        )

    assert error.value.code == "DURABLE_STATE_AMBIGUOUS"
    assert fixture.publication.read_active(fixture.repository) is None
    assert fixture.transitions.capacity_limits(
        fixture.repository,
        "v8",
        "unread-back-activation",
    ) == (0, 0)


def test_receipt_backed_rollback_is_new_compensating_record_and_preserves_receipt(tmp_path):
    fixture = activation_fixture(tmp_path)
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None
    fixture.controller.cutover(
        fixture.compiled_plan,
        canary=fixture.accepted_canary,
        guard_subject=fixture.subject,
        guard_receipt=report.receipt,
        writer_generation="v8",
        worker_capacity=8,
        coordinator_capacity=1,
    )
    activation = fixture.publication.durable.read_current_activation(fixture.repository)
    assert activation is not None

    rollback = fixture.controller.rollback(
        repository=fixture.repository,
        ownership=fixture.ownership,
        restore_writer_generation="v6.1",
        reason="beta3 rehearsal rollback",
    )

    assert rollback.status == "rolled_back"
    assert fixture.publication.durable.read_activation(
        fixture.repository,
        activation.activation_id,
    ) == activation
    assert fixture.transitions.history(fixture.repository)[-1].kind == "rollback"
    assert fixture.legacy.readback(fixture.repository).authority_state == "authoritative_quiescent"
