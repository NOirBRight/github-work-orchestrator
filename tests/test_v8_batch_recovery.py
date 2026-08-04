from __future__ import annotations

from dataclasses import replace
import sqlite3
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8._batch_integrator_store import SqliteBatchDeliveryJournal
from v8_batch_test_support import make_batch_action, make_hosted_result_receipt


def test_batch_journal_rebuilds_action_lease_and_hosted_receipt_after_restart(tmp_path):
    store_path = tmp_path / "v8.sqlite3"
    first = SqliteBatchDeliveryJournal(store_path)
    action = make_batch_action()
    record = first.create_action(action, action.request_digest)
    lease = first.acquire_integration_lease(
        "owner/repo", "action:one", "gen:1", "activation:1"
    )
    hosted = make_hosted_result_receipt()
    first.persist_hosted_result(hosted)

    second = SqliteBatchDeliveryJournal(store_path)

    assert second.read_action(action.stable_action_id) == record
    assert second.read_integration_lease("owner/repo") == lease
    assert (
        second.read_hosted_result(
            hosted.stable_action_id,
            hosted.batch_sha,
            hosted.suite_id,
            hosted.provider_check_id,
        )
        == hosted
    )
