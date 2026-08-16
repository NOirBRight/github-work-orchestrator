from __future__ import annotations

from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from test_orchestrator_v8_phase4bc import _compiled  # noqa: E402
from tests.cutover_guard_test_support import activation_fixture  # noqa: E402


def test_cutover_uses_historical_activation_receipt_as_expected_previous_digest(
    tmp_path,
):
    fixture = activation_fixture(tmp_path)
    historical_plan = _compiled(count=4)
    fixture.publication.publish_and_activate(
        historical_plan,
        expected_active_digest=None,
        writer_generation="v8",
    )
    historical_receipt = fixture.publication.durable.read_current_activation(
        fixture.repository
    )
    assert historical_receipt is not None

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
    active_receipt = fixture.publication.durable.read_current_activation(
        fixture.repository
    )
    assert active_receipt is not None
    assert active_receipt.expected_previous_digest == historical_receipt.plan_digest
    assert fixture.publication.durable.read_activation(
        fixture.repository,
        historical_receipt.activation_id,
    ) == historical_receipt


def test_cutover_retry_after_receipt_commit_remains_idempotent(tmp_path):
    fail_after = {"receipt_read_back"}
    fixture = activation_fixture(tmp_path, fail_after=fail_after)
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None

    with pytest.raises(RuntimeError, match="isolated rehearsal failure"):
        fixture.controller.cutover(
            fixture.compiled_plan,
            canary=fixture.accepted_canary,
            guard_subject=fixture.subject,
            guard_receipt=report.receipt,
            writer_generation="v8",
            worker_capacity=8,
            coordinator_capacity=1,
        )

    durable_receipt = fixture.publication.durable.read_current_activation(
        fixture.repository
    )
    assert durable_receipt is not None
    fail_after.clear()

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
    assert outcome.activation_id == durable_receipt.activation_id
    assert fixture.publication.durable.activation_count(fixture.repository) == 1
