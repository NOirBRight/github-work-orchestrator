from __future__ import annotations

import sqlite3
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8.transition import WriterCutoverController  # noqa: E402
from test_orchestrator_v8_phase4bc import _compiled  # noqa: E402
from tests.cutover_guard_test_support import (  # noqa: E402
    RecordingPublication,
    activation_fixture,
)


class _MissingCurrentReceipt:
    def __init__(self, durable):
        self._durable = durable

    def read_current_activation(self, repository):
        del repository
        return None

    def __getattr__(self, name):
        return getattr(self._durable, name)


def _active_row(path: Path, repository: str):
    with sqlite3.connect(path) as connection:
        return connection.execute(
            """
            SELECT plan_digest, writer_generation, activation_id
            FROM v8_active_plans
            WHERE repository = ?
            """,
            (repository,),
        ).fetchone()


def _cutover(controller, fixture, receipt):
    return controller.cutover(
        fixture.compiled_plan,
        canary=fixture.accepted_canary,
        guard_subject=fixture.subject,
        guard_receipt=receipt,
        writer_generation="v8",
        worker_capacity=8,
        coordinator_capacity=1,
    )


def test_pending_receipt_commit_then_fresh_store_retry_blocks_before_mutation(
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

    fail_once = True

    def checkpoint(name: str) -> None:
        nonlocal fail_once
        if name == "receipt_read_back" and fail_once:
            fail_once = False
            raise RuntimeError("isolated rehearsal failure")

    target_publication = RecordingPublication(
        tmp_path / "v8.sqlite3",
        durable=fixture.publication.durable,
        writer_authority=fixture.transitions,
        checkpoint=checkpoint,
        calls=fixture.calls,
    )
    target_controller = WriterCutoverController(
        legacy=fixture.legacy,
        transitions=fixture.transitions,
        publication=target_publication,
        guard=fixture.guard,
    )
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None

    with pytest.raises(RuntimeError, match="isolated rehearsal failure"):
        _cutover(target_controller, fixture, report.receipt)

    target_receipt = fixture.publication.durable.read_current_activation(
        fixture.repository
    )
    assert target_receipt is not None
    assert target_receipt.plan_digest == fixture.compiled_plan.digest
    assert target_receipt.expected_previous_digest == historical_receipt.plan_digest
    assert fixture.transitions.read_current(fixture.repository).record_id != (
        "initial-writer"
    )

    fixture.calls.clear()
    fresh_publication = RecordingPublication(
        tmp_path / "fresh-local.sqlite3",
        durable=fixture.publication.durable,
        writer_authority=fixture.transitions,
        calls=fixture.calls,
    )
    fresh_controller = WriterCutoverController(
        legacy=fixture.legacy,
        transitions=fixture.transitions,
        publication=fresh_publication,
        guard=fixture.guard,
    )

    outcome = _cutover(fresh_controller, fixture, report.receipt)

    assert outcome.status == "blocked"
    assert outcome.blockers == ("CUTOVER_LOCAL_DURABLE_IDENTITY_MISMATCH",)
    assert "legacy.stop" not in fixture.mutation_calls()
    assert "publication.publish_and_activate" not in fixture.mutation_calls()
    assert _active_row(tmp_path / "fresh-local.sqlite3", fixture.repository) is None
    assert fixture.publication.durable.read_current_activation(
        fixture.repository
    ) == target_receipt
    assert fixture.publication.durable.read_activation(
        fixture.repository,
        historical_receipt.activation_id,
    ) == historical_receipt
    blocked_record = fixture.transitions.read(fixture.repository, outcome.record_id)
    assert blocked_record is not None
    assert blocked_record.status == "blocked"


def test_missing_durable_receipt_with_local_active_blocks_before_mutation(tmp_path):
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
    local_active_before = _active_row(tmp_path / "v8.sqlite3", fixture.repository)
    assert local_active_before == (
        historical_receipt.plan_digest,
        historical_receipt.writer_generation,
        historical_receipt.activation_id,
    )

    durable = fixture.publication.durable
    fixture.publication.durable = _MissingCurrentReceipt(durable)
    fixture.calls.clear()
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None

    outcome = _cutover(fixture.controller, fixture, report.receipt)

    assert outcome.status == "blocked"
    assert outcome.blockers == ("CUTOVER_LOCAL_DURABLE_IDENTITY_MISMATCH",)
    assert "legacy.stop" not in fixture.mutation_calls()
    assert "publication.publish_and_activate" not in fixture.mutation_calls()
    assert _active_row(tmp_path / "v8.sqlite3", fixture.repository) == local_active_before
    assert durable.read_current_activation(fixture.repository) == historical_receipt
    assert durable.read_activation(
        fixture.repository,
        historical_receipt.activation_id,
    ) == historical_receipt
