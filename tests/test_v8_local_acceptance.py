from __future__ import annotations

import ast
from dataclasses import fields
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


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
    assert record["public_status"] == "Complete"
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
    assert record["replay"]["idempotent_effects"] is True
    assert record["replay"]["readback_unchanged"] is True


def test_repeated_runs_isolate_run_id_and_scenario_and_release_resources(
    tmp_path: Path,
):
    runner = _load_runner()
    root = tmp_path / "caller-root"
    root.mkdir()
    caller_file = root / "caller-owned.txt"
    caller_file.write_text("leave this file alone", encoding="utf-8")

    complete = runner.run_local_acceptance(
        root=root,
        run_id="fixed-run",
        scenario="single",
    )
    failure = runner.run_local_acceptance(
        root=root,
        run_id="fixed-run",
        scenario="failure",
    )
    repeated_complete = runner.run_local_acceptance(
        root=root,
        run_id="fixed-run",
        scenario="single",
    )

    assert complete["status"] == "Complete"
    assert failure["status"] == "Failure"
    assert failure["public_status"] == "Running"
    assert complete["campaign"]["campaign_key"] != failure["campaign"]["campaign_key"]
    assert repeated_complete == complete
    assert caller_file.read_text(encoding="utf-8") == "leave this file alone"
    assert sorted(path.name for path in root.iterdir()) == [caller_file.name]


@pytest.mark.parametrize(
    ("scenario", "expected_status", "expected_public_status"),
    (
        ("wait", "Wait", "Wait"),
        ("blocked", "Blocked", "Blocked"),
        ("failure", "Failure", "Running"),
    ),
)
def test_state_branches_restart_advance_wake_and_replay_through_public_api(
    tmp_path: Path,
    scenario: str,
    expected_status: str,
    expected_public_status: str,
):
    runner = _load_runner()

    record = runner.run_local_acceptance(
        root=tmp_path,
        run_id=f"{scenario}-run",
        scenario=scenario,
    )

    assert record["status"] == expected_status
    assert record["public_status"] == expected_public_status
    assert record["public_reason"] == record["replay"]["final_inspect"]["reason"]
    assert record["replay"]["restart_inspect"]["status"] == expected_public_status
    assert record["replay"]["restart_advance"]["status"] == expected_status
    assert record["replay"]["repeated_restart_advance"]["status"] == expected_status
    assert record["replay"]["readback_unchanged"] is True
    assert record["replay"]["idempotent_effects"] is True
    if scenario == "wait":
        assert record["facts"]["worker"]["phase"] == "wait"
        assert record["replay"]["restart_inspect"]["work_runs"][0]["next_check_at"]
    elif scenario == "blocked":
        assert record["facts"]["worker"]["phase"] == "blocked"
    else:
        assert record["failure"]["type"] == "LocalAcceptanceFailure"
        assert record["replay"]["restart_inspect"]["status"] == "Running"
        assert record["replay"]["restart_advance"]["type"] == "LocalAcceptanceFailure"


def test_public_api_boundary_is_traced_without_restricting_harness_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _load_runner()
    calls: list[tuple[str, str | None]] = []

    for name in ("start", "advance", "inspect"):
        original = getattr(runner.gwo_v8, name)

        def traced(*args, _name=name, _original=original, **kwargs):
            wake_ref = args[1] if _name == "advance" and len(args) > 1 else None
            calls.append((_name, wake_ref))
            return _original(*args, **kwargs)

        monkeypatch.setattr(runner.gwo_v8, name, traced)

    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "reconcile_once"
        for node in ast.walk(tree)
    )

    runner.run_local_acceptance(
        root=tmp_path,
        run_id="public-boundary",
        scenario="wait",
    )

    assert calls[0][0] == "start"
    assert any(name == "advance" and wake == "local:initial" for name, wake in calls)
    assert any(name == "advance" and wake == "local:restart" for name, wake in calls)
    assert {name for name, _wake in calls} <= {"start", "advance", "inspect"}


def test_complete_readback_is_canonical_linked_and_idempotent_after_replay(
    tmp_path: Path,
):
    runner = _load_runner()
    record = runner.run_local_acceptance(
        root=tmp_path,
        run_id="linked-run",
        scenario="single",
    )

    facts = record["facts"]
    readback = facts["readback"]
    candidate = readback["candidate_receipt"]
    diff = readback["candidate_diff"]
    accepted = readback["accepted_candidate_receipt"]
    delivery = readback["delivery_proof"]
    proof = readback["result_integrity"]

    assert candidate is not None
    assert diff is not None
    assert accepted is not None
    assert delivery is not None
    assert proof is not None
    assert candidate["campaign_key"] == record["campaign"]["campaign_key"]
    assert candidate["plan_revision_digest"] == facts["plan_revision"]["digest"]
    assert candidate["work_run_key"] == facts["worker"]["work_run_key"]
    assert candidate["ticket_key"] == facts["ticket"]["key"]
    assert candidate["diff_record_digest"] == diff["record_digest"]
    assert accepted["candidate_receipt_digest"] == candidate["receipt_digest"]
    assert accepted["diff_record_digest"] == diff["record_digest"]
    assert accepted["candidate_sha"] == candidate["candidate_commit_oid"]
    assert accepted["candidate_tree_oid"] == candidate["candidate_tree_oid"]
    assert accepted["work_run_key"] == candidate["work_run_key"]
    assert delivery["delivery_stable_action_id"] == proof["delivery_stable_action_id"]
    assert delivery["delivery_request_digest"] == proof["delivery_request_digest"]
    assert delivery["batch_id"] == proof["batch_id"]
    assert delivery["batch_sha"] == proof["batch_sha"]
    assert delivery["pull_request_head_sha"] == proof["pull_request_head_sha"]
    assert delivery["target_contains_batch_sha"] is True
    assert proof["accepted_candidate_receipt_digest"] == accepted["receipt_digest"]
    assert proof["candidate_diff_record_digest"] == diff["record_digest"]
    assert proof["batch_delivery_receipt_digest"] == facts["batch"]["delivery_receipt_digest"]
    assert proof["result_digest"] == facts["result"]["result_digest"]
    assert proof["evidence_digests"] == facts["evidence"]["digests"]

    assert runner.digest_value(
        {key: value for key, value in candidate.items() if key != "receipt_digest"}
    ) == candidate["receipt_digest"]
    assert runner.digest_value(
        {key: value for key, value in accepted.items() if key != "receipt_digest"}
    ) == accepted["receipt_digest"]
    diff_body = {key: value for key, value in diff.items() if key != "record_digest"}
    assert hashlib.sha256(
        b"gwo.candidate-diff-record.v1\x00" + runner.canonical_bytes(diff_body)
    ).hexdigest() == diff["record_digest"]
    delivery_body = {
        key: value for key, value in delivery.items() if key != "proof_digest"
    }
    assert runner.digest_value(
        {"kind": "batch-delivery-proof.v1", **delivery_body}
    ) == delivery["proof_digest"]
    proof_value = runner.ResultIntegrityProof(
        **{
            field.name: (
                tuple(proof[field.name])
                if field.name in {"delivery_member_ticket_keys", "evidence_digests"}
                else proof[field.name]
            )
            for field in fields(runner.ResultIntegrityProof)
        }
    )
    assert proof_value.expected_batch_delivery_proof_digest() == delivery["proof_digest"]
    assert proof_value.expected_result_digest() == proof["result_digest"]

    for observation_group in readback["observations"].values():
        for observation in observation_group:
            assert runner.WorkRunObservation.from_canonical(observation).canonical() == observation

    assert record["record_digest"] == runner.digest_value(
        {key: value for key, value in record.items() if key != "record_digest"}
    )
    assert record["replay"]["semantic_execute_calls_before_restart"] == 1
    assert record["replay"]["semantic_execute_calls_after_replay"] == 1
    assert record["replay"]["delivery_execute_calls_before_restart"] == 1
    assert record["replay"]["delivery_execute_calls_after_replay"] == 1


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


@pytest.mark.parametrize(
    ("scenario", "expected_status", "expected_public_status"),
    (
        ("single", "Complete", "Complete"),
        ("wait", "Wait", "Wait"),
        ("blocked", "Blocked", "Blocked"),
        ("failure", "Failure", "Running"),
    ),
)
def test_cli_emits_canonical_deterministic_output_for_every_status(
    tmp_path: Path,
    scenario: str,
    expected_status: str,
    expected_public_status: str,
):
    runner = _load_runner()
    command = [
        sys.executable,
        str(RUNNER),
        "--root",
        str(tmp_path / "caller-root"),
        "--run-id",
        "cli-fixed-run",
        "--scenario",
        scenario,
    ]
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

    first = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    second = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stderr == ""
    assert first.stdout == second.stdout
    record = json.loads(first.stdout)
    assert first.stdout == runner.canonical_json(record) + "\n"
    assert record["status"] == expected_status
    assert record["public_status"] == expected_public_status
    assert runner.load_canonical_json(first.stdout.rstrip("\n")) == record
