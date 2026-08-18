from __future__ import annotations

import sqlite3
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8.transition import WriterCutoverController  # noqa: E402
from test_orchestrator_v8_phase4bc import _compiled  # noqa: E402
from tests.cutover_guard_test_support import (  # noqa: E402
    RecordingPublication,
    activation_fixture,
)


def test_fresh_local_store_reconstructs_from_durable_receipt_and_cuts_over(
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
    assert fixture.publication.read_authoritative_rollback_identity(
        fixture.repository
    )[0] == historical_receipt

    fresh_store = tmp_path / "fresh-local.sqlite3"
    fresh_publication = RecordingPublication(
        fresh_store,
        durable=fixture.publication.durable,
        writer_authority=fixture.transitions,
        calls=fixture.calls,
    )
    controller = WriterCutoverController(
        legacy=fixture.legacy,
        transitions=fixture.transitions,
        publication=fresh_publication,
        guard=fixture.guard,
    )
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None

    outcome = controller.cutover(
        fixture.compiled_plan,
        canary=fixture.accepted_canary,
        guard_subject=fixture.subject,
        guard_receipt=report.receipt,
        writer_generation="v8",
        worker_capacity=8,
        coordinator_capacity=1,
    )

    assert outcome.status == "cut_over"
    assert outcome.blockers == ()
    assert outcome.activation_id is not None
    assert "legacy.stop" in fixture.mutation_calls()
    assert fixture.legacy.readback(fixture.repository).stopped is True

    active = fresh_publication.read_active(fixture.repository)
    assert active is not None
    assert active.activation_id == outcome.activation_id
    assert active.plan_digest == fixture.compiled_plan.digest
    current_receipt = fixture.publication.durable.read_current_activation(
        fixture.repository
    )
    assert current_receipt is not None
    assert current_receipt.activation_id == outcome.activation_id
    assert current_receipt.plan_digest == fixture.compiled_plan.digest
    assert current_receipt.expected_previous_digest == historical_receipt.plan_digest
    assert current_receipt != historical_receipt
    assert fixture.publication.durable.read_activation(
        fixture.repository,
        historical_receipt.activation_id,
    ) == historical_receipt
    assert fixture.publication.durable.activation_count(fixture.repository) == 2

    record = fixture.transitions.read(fixture.repository, outcome.record_id)
    assert record is not None
    assert record.status == "cut_over"
    assert record.activation_id == outcome.activation_id
    assert fixture.transitions.history(fixture.repository)[-1] == record

    with sqlite3.connect(fresh_store) as connection:
        assert connection.execute(
            """
            SELECT repository, plan_digest, writer_generation, activation_id
            FROM v8_active_plans
            WHERE repository = ?
            """,
            (fixture.repository,),
        ).fetchall() == [
            (
                fixture.repository,
                fixture.compiled_plan.digest,
                "v8",
                outcome.activation_id,
            )
        ]
        assert connection.execute(
            "SELECT 1 FROM v8_pending_activations WHERE repository = ?",
            (fixture.repository,),
        ).fetchall() == []
        assert connection.execute(
            """
            SELECT writer_generation
            FROM v8_writer_generations
            WHERE repository = ?
            """,
            (fixture.repository,),
        ).fetchall() == [("v8",)]
