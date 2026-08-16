from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from test_orchestrator_v8_phase4bc import _compiled  # noqa: E402
from gwo_v8.transition import (  # noqa: E402
    InMemoryV8OwnershipControl,
    V8OwnershipReadback,
)
from tests.cutover_guard_test_support import activation_fixture  # noqa: E402


class _CurrentActivationOverride:
    def __init__(self, durable, current):
        self._durable = durable
        self._current = current

    def read_current_activation(self, repository):
        del repository
        return self._current

    def __getattr__(self, name):
        return getattr(self._durable, name)


def _cutover(fixture, receipt):
    return fixture.controller.cutover(
        fixture.compiled_plan,
        canary=fixture.accepted_canary,
        guard_subject=fixture.subject,
        guard_receipt=receipt,
        writer_generation="v8",
        worker_capacity=8,
        coordinator_capacity=1,
    )


def test_pending_retry_without_durable_receipt_validates_and_completes(tmp_path):
    fail_after = {"pending_reserved"}
    fixture = activation_fixture(tmp_path, fail_after=fail_after)
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None

    with pytest.raises(RuntimeError, match="isolated rehearsal failure"):
        _cutover(fixture, report.receipt)

    fail_after.clear()
    outcome = _cutover(fixture, report.receipt)

    assert outcome.status == "cut_over"
    assert fixture.publication.durable.read_current_activation(
        fixture.repository
    ) is not None


@pytest.mark.parametrize("tamper_target", ("column", "receipt_json"))
def test_pending_retry_rejects_expected_previous_digest_mismatch_before_stop(
    tmp_path,
    tamper_target,
):
    fail_after = {"receipt_read_back"}
    fixture = activation_fixture(tmp_path, fail_after=fail_after)
    fail_after.clear()
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
    fail_after.add("receipt_read_back")
    with pytest.raises(RuntimeError, match="isolated rehearsal failure"):
        _cutover(fixture, report.receipt)
    fail_after.clear()

    target_receipt = fixture.publication.durable.read_current_activation(
        fixture.repository
    )
    assert target_receipt is not None
    assert target_receipt.expected_previous_digest == historical_receipt.plan_digest

    with sqlite3.connect(tmp_path / "v8.sqlite3") as connection:
        row = connection.execute(
            """
            SELECT receipt_json FROM v8_pending_activations
            WHERE repository = ?
            """,
            (fixture.repository,),
        ).fetchone()
        assert row is not None
        if tamper_target == "column":
            connection.execute(
                """
                UPDATE v8_pending_activations
                SET expected_previous_digest = ?
                WHERE repository = ?
                """,
                (
                    "0" * 64,
                    fixture.repository,
                ),
            )
        else:
            receipt_json = json.loads(row[0])
            receipt_json["expected_previous_digest"] = "0" * 64
            connection.execute(
                """
                UPDATE v8_pending_activations
                SET receipt_json = ?
                WHERE repository = ?
                """,
                (
                    json.dumps(receipt_json, separators=(",", ":"), sort_keys=True),
                    fixture.repository,
                ),
            )

    fixture.calls.clear()
    outcome = _cutover(fixture, report.receipt)

    assert outcome.status == "blocked"
    assert outcome.blockers == ("CUTOVER_LOCAL_DURABLE_IDENTITY_MISMATCH",)
    assert "legacy.stop" not in fixture.mutation_calls()
    assert "publication.publish_and_activate" not in fixture.mutation_calls()
    assert fixture.publication.durable.read_current_activation(
        fixture.repository
    ) == target_receipt
    assert fixture.publication.durable.read_activation(
        fixture.repository,
        historical_receipt.activation_id,
    ) == historical_receipt


def test_pending_retry_after_durable_receipt_readback_rolls_forward(tmp_path):
    fail_after = {"receipt_read_back"}
    fixture = activation_fixture(tmp_path, fail_after=fail_after)
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None

    with pytest.raises(RuntimeError, match="isolated rehearsal failure"):
        _cutover(fixture, report.receipt)

    target_receipt = fixture.publication.durable.read_current_activation(
        fixture.repository
    )
    assert target_receipt is not None
    fail_after.clear()

    outcome = _cutover(fixture, report.receipt)

    assert outcome.status == "cut_over"
    assert outcome.activation_id == target_receipt.activation_id


def test_pending_transition_retry_before_local_reservation_rolls_forward(tmp_path):
    fixture = activation_fixture(tmp_path)
    historical_plan = _compiled(count=4)
    fixture.publication.publish_and_activate(
        historical_plan,
        expected_active_digest=None,
        writer_generation="v8",
    )

    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None
    original_publish_and_activate = fixture.publication.publish_and_activate

    def crash_before_reservation(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("isolated rehearsal failure before reservation")

    fixture.publication.publish_and_activate = crash_before_reservation
    with pytest.raises(
        RuntimeError,
        match="isolated rehearsal failure before reservation",
    ):
        _cutover(fixture, report.receipt)

    fixture.publication.publish_and_activate = original_publish_and_activate
    fixture.calls.clear()
    outcome = _cutover(fixture, report.receipt)

    assert outcome.status == "cut_over"
    assert fixture.publication.durable.read_current_activation(
        fixture.repository
    ) is not None


def test_rollback_after_pending_transition_before_local_reservation_completes(
    tmp_path,
):
    fixture = activation_fixture(tmp_path)
    historical_plan = _compiled(count=4)
    fixture.publication.publish_and_activate(
        historical_plan,
        expected_active_digest=None,
        writer_generation="v8",
    )
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None
    original_publish_and_activate = fixture.publication.publish_and_activate

    def crash_before_reservation(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("isolated rehearsal failure before reservation")

    fixture.publication.publish_and_activate = crash_before_reservation
    with pytest.raises(
        RuntimeError,
        match="isolated rehearsal failure before reservation",
    ):
        _cutover(fixture, report.receipt)

    fixture.publication.publish_and_activate = original_publish_and_activate
    outcome = fixture.controller.rollback(
        repository=fixture.repository,
        ownership=InMemoryV8OwnershipControl(
            V8OwnershipReadback(
                active_admissions=(),
                active_attempts=(),
                integration_lease=False,
                runtime_resources=(),
            )
        ),
        restore_writer_generation="v6.1",
        reason="abort before local reservation",
    )

    assert outcome.status == "rolled_back"
    assert fixture.legacy.readback(fixture.repository).stopped is False


def test_rollback_with_predecessor_cleans_receipt_free_target_reservation(
    tmp_path,
):
    fail_after = {"pending_reserved"}
    fixture = activation_fixture(tmp_path, fail_after=fail_after)
    fail_after.clear()
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
    fail_after.add("pending_reserved")

    with pytest.raises(RuntimeError, match="isolated rehearsal failure"):
        _cutover(fixture, report.receipt)

    fail_after.clear()
    outcome = fixture.controller.rollback(
        repository=fixture.repository,
        ownership=InMemoryV8OwnershipControl(
            V8OwnershipReadback(
                active_admissions=(),
                active_attempts=(),
                integration_lease=False,
                runtime_resources=(),
            )
        ),
        restore_writer_generation="v6.1",
        reason="abort target reservation",
    )

    assert outcome.status == "rolled_back"
    assert fixture.legacy.readback(fixture.repository).stopped is False
    assert not fixture.publication.has_pending_activation(fixture.repository)
    assert fixture.publication.durable.read_current_activation(
        fixture.repository
    ) == historical_receipt


def test_rollback_rolls_forward_receipt_backed_pending_with_predecessor(
    tmp_path,
):
    fail_after = {"receipt_read_back"}
    fixture = activation_fixture(tmp_path, fail_after=fail_after)
    fail_after.clear()
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
    fail_after.add("receipt_read_back")

    with pytest.raises(RuntimeError, match="isolated rehearsal failure"):
        _cutover(fixture, report.receipt)

    target_receipt = fixture.publication.durable.read_current_activation(
        fixture.repository
    )
    assert target_receipt is not None
    assert target_receipt.expected_previous_digest == historical_receipt.plan_digest
    fail_after.clear()

    outcome = fixture.controller.rollback(
        repository=fixture.repository,
        ownership=InMemoryV8OwnershipControl(
            V8OwnershipReadback(
                active_admissions=(),
                active_attempts=(),
                integration_lease=False,
                runtime_resources=(),
            )
        ),
        restore_writer_generation="v6.1",
        reason="compensate receipt-backed target",
    )

    assert outcome.status == "rolled_back"
    assert fixture.legacy.readback(fixture.repository).stopped is False
    assert not fixture.publication.has_pending_activation(fixture.repository)
    assert fixture.publication.durable.read_current_activation(
        fixture.repository
    ) == target_receipt


def test_pending_retry_blocks_when_target_receipt_current_readback_disappears(
    tmp_path,
):
    fail_after = {"receipt_read_back"}
    fixture = activation_fixture(tmp_path, fail_after=fail_after)
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None

    with pytest.raises(RuntimeError, match="isolated rehearsal failure"):
        _cutover(fixture, report.receipt)

    target_receipt = fixture.publication.durable.read_current_activation(
        fixture.repository
    )
    assert target_receipt is not None
    fail_after.clear()
    fixture.publication.durable = _CurrentActivationOverride(
        fixture.publication.durable,
        None,
    )
    fixture.calls.clear()

    outcome = _cutover(fixture, report.receipt)

    assert outcome.status == "blocked"
    assert outcome.blockers == ("CUTOVER_LOCAL_DURABLE_IDENTITY_MISMATCH",)
    assert "legacy.stop" not in fixture.mutation_calls()
    assert "publication.publish_and_activate" not in fixture.mutation_calls()
    assert fixture.publication.durable.read_activation(
        fixture.repository,
        target_receipt.activation_id,
    ) == target_receipt


def test_pending_retry_blocks_when_durable_current_reverts_to_predecessor(
    tmp_path,
):
    fail_after = {"receipt_read_back"}
    fixture = activation_fixture(tmp_path, fail_after=fail_after)
    fail_after.clear()
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
    fail_after.add("receipt_read_back")
    with pytest.raises(RuntimeError, match="isolated rehearsal failure"):
        _cutover(fixture, report.receipt)

    target_receipt = fixture.publication.durable.read_current_activation(
        fixture.repository
    )
    assert target_receipt is not None
    fail_after.clear()
    fixture.publication.durable = _CurrentActivationOverride(
        fixture.publication.durable,
        historical_receipt,
    )
    fixture.calls.clear()

    outcome = _cutover(fixture, report.receipt)

    assert outcome.status == "blocked"
    assert outcome.blockers == ("CUTOVER_LOCAL_DURABLE_IDENTITY_MISMATCH",)
    assert "legacy.stop" not in fixture.mutation_calls()
    assert "publication.publish_and_activate" not in fixture.mutation_calls()
    assert fixture.publication.durable.read_activation(
        fixture.repository,
        target_receipt.activation_id,
    ) == target_receipt
