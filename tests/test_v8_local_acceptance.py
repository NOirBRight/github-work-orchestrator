from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_v8_local_acceptance.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_v8_local_acceptance", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_single_acceptance_uses_public_api_and_reaches_complete(tmp_path: Path):
    runner = _load_runner()

    record = runner.run_local_acceptance(
        root=tmp_path,
        run_id="fixed-run",
        scenario="single",
    )

    assert record["gate"] == "PUBLIC_API_SINGLE_NODE_GO"
    assert record["status"] == "Complete"
    assert record["run_id"] == "fixed-run"


def test_single_acceptance_records_the_complete_domain_chain_and_idempotent_restart(
    tmp_path: Path,
):
    runner = _load_runner()

    record = runner.run_local_acceptance(
        root=tmp_path,
        run_id="fixed-run",
        scenario="single",
    )

    facts = record["facts"]
    assert facts["ticket"]["key"] == "issue:1"
    assert facts["campaign"]["campaign_key"] == record["campaign"]["campaign_key"]
    assert facts["plan_revision"]["digest"]
    assert facts["worker"]["phase"] == "completed"
    assert facts["candidate_gate"]["candidate_identity"]
    assert facts["candidate_gate"]["accepted_candidate_receipt_digest"]
    assert facts["review"]["status"] == "accepted"
    assert facts["batch"]["delivery_receipt_digest"]
    assert facts["result"]["result_digest"]
    assert facts["evidence"]["digests"]
    assert record["replay"]["idempotent_delivery"] is True
    assert record["replay"]["restart_advance"]["status"] == "Complete"


def test_state_branches_are_observable_through_public_outcomes(tmp_path: Path):
    runner = _load_runner()

    wait = runner.run_local_acceptance(
        root=tmp_path / "wait",
        run_id="wait-run",
        scenario="wait",
    )
    blocked = runner.run_local_acceptance(
        root=tmp_path / "blocked",
        run_id="blocked-run",
        scenario="blocked",
    )
    failure = runner.run_local_acceptance(
        root=tmp_path / "failure",
        run_id="failure-run",
        scenario="failure",
    )

    assert wait["status"] == "Wait"
    assert wait["facts"]["worker"]["phase"] == "wait"
    assert blocked["status"] == "Blocked"
    assert blocked["facts"]["worker"]["phase"] == "blocked"
    assert failure["status"] == "Failure"
    assert failure["failure"]["type"] == "LocalAcceptanceFailure"


def test_acceptance_record_is_canonical_json(tmp_path: Path):
    runner = _load_runner()
    record = runner.run_local_acceptance(
        root=tmp_path,
        run_id="canonical-run",
        scenario="single",
    )

    rendered = runner.canonical_json(record)
    assert rendered == json.dumps(
        record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert json.loads(rendered)["record_digest"] == record["record_digest"]
