from __future__ import annotations

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


def test_fresh_local_store_fails_closed_before_legacy_stop_and_preserves_receipt(
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

    fresh_publication = RecordingPublication(
        tmp_path / "fresh-local.sqlite3",
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

    assert outcome.status == "blocked"
    assert outcome.blockers == ("CUTOVER_LOCAL_DURABLE_IDENTITY_MISMATCH",)
    assert "legacy.stop" not in fixture.mutation_calls()
    assert fixture.legacy.readback(fixture.repository).stopped is False
    assert fixture.publication.durable.read_current_activation(
        fixture.repository
    ) == historical_receipt
    assert fixture.publication.durable.read_activation(
        fixture.repository,
        historical_receipt.activation_id,
    ) == historical_receipt
