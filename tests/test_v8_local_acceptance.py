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


class _NoCleanupTemporaryDirectory:
    def __init__(self, root: Path):
        self.root = str(root)

    def __enter__(self) -> str:
        return self.root

    def __exit__(self, *_args: object) -> None:
        return None


def _patch_runner_root_lifecycle(
    runner, root: Path, monkeypatch: pytest.MonkeyPatch
):
    original = runner.tempfile.TemporaryDirectory

    def temporary_directory(*args, **kwargs):
        directory = kwargs.get("dir")
        if directory is not None and Path(directory).resolve() == root.resolve():
            return _NoCleanupTemporaryDirectory(root)
        return original(*args, **kwargs)

    monkeypatch.setattr(runner.tempfile, "TemporaryDirectory", temporary_directory)


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
    assert (
        proof["batch_delivery_receipt_digest"]
        == facts["batch"]["delivery_receipt_digest"]
    )
    assert proof["result_digest"] == facts["result"]["result_digest"]
    assert proof["evidence_digests"] == facts["evidence"]["digests"]

    assert (
        runner.digest_value(
            {key: value for key, value in candidate.items() if key != "receipt_digest"}
        )
        == candidate["receipt_digest"]
    )
    assert (
        runner.digest_value(
            {key: value for key, value in accepted.items() if key != "receipt_digest"}
        )
        == accepted["receipt_digest"]
    )
    diff_body = {key: value for key, value in diff.items() if key != "record_digest"}
    assert (
        hashlib.sha256(
            b"gwo.candidate-diff-record.v1\x00" + runner.canonical_bytes(diff_body)
        ).hexdigest()
        == diff["record_digest"]
    )
    delivery_body = {
        key: value for key, value in delivery.items() if key != "proof_digest"
    }
    assert (
        runner.digest_value({"kind": "batch-delivery-proof.v1", **delivery_body})
        == delivery["proof_digest"]
    )
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
    assert (
        proof_value.expected_batch_delivery_proof_digest() == delivery["proof_digest"]
    )
    assert proof_value.expected_result_digest() == proof["result_digest"]

    for observation_group in readback["observations"].values():
        for observation in observation_group:
            assert (
                runner.WorkRunObservation.from_canonical(observation).canonical()
                == observation
            )

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


def test_root_canary_covers_four_work_runs_review_repair_rejection_and_batches(
    tmp_path: Path,
):
    runner = _load_runner()

    record = runner.run_local_acceptance(
        root=tmp_path,
        run_id="root-run",
        scenario="root",
    )

    assert record["gate"] == "LOCAL_ROOT_CANARY_GO"
    assert record["status"] == "Complete"
    assert record["public_status"] == "Complete"

    standard = ("issue:101", "issue:102", "issue:103")
    strict = "issue:104"
    facts = record["facts"]
    assert [item["ticket_key"] for item in facts["work_runs"]] == [
        *standard,
        strict,
    ]
    assert facts["concurrency"] == {
        "worker_slot_limit": 4,
        "max_held": 4,
        "final_held": 0,
        "final_available": 4,
        "work_run_count": 4,
    }
    assert facts["exclusive_resources"] == {
        "issue:101": [],
        "issue:102": [],
        "issue:103": [],
        "issue:104": ["repository.target.v1"],
    }

    gate = facts["candidate_gate"]
    assert gate["reviewed"] == [*standard, strict]
    assert gate["repair_required"] == ["issue:102"]
    assert gate["rejected"] == ["issue:103"]
    assert gate["strict_specialist_review"] == [strict]
    assert gate["accepted"] == [*standard, strict]
    gate_events = {item["ticket_key"]: item for item in gate["events"]}
    assert gate_events["issue:102"]["repair"] == "repair_verify"
    assert gate_events["issue:103"]["repair"] == "replacement_candidate"
    assert (
        gate_events["issue:103"]["rejected_candidate_receipt_digest"]
        != gate_events["issue:103"]["candidate_receipt_digest"]
    )
    assert gate_events[strict]["specialist_review"] == "accepted"

    batches = facts["batches"]
    assert len(batches) == 2
    assert batches[0]["member_ticket_keys"] == list(standard)
    assert batches[0]["singleton"] is False
    assert batches[1]["member_ticket_keys"] == [strict]
    assert batches[1]["singleton"] is True
    assert batches[0]["batch_id"] != batches[1]["batch_id"]
    assert batches[0]["batch_sha"] != batches[1]["batch_sha"]

    work_runs = {item["ticket_key"]: item for item in facts["work_runs"]}
    for ticket_key in (*standard, strict):
        run = work_runs[ticket_key]
        assert run["phase"] == "completed"
        assert run["candidate_identity"]
        assert run["accepted_candidate_receipt_digest"]
        assert run["candidate_diff_record_digest"]
        assert run["batch_id"]
        assert run["batch_sha"]
        assert run["result_digest"]
        assert run["evidence_digests"]
        assert run["git_readback"]["target_contains_batch_sha"] is True

    readback = facts["readback"]
    assert len(readback["candidate_receipts"]) == 4
    assert len(readback["candidate_diffs"]) == 4
    assert len(readback["accepted_candidate_receipts"]) == 4
    assert len(readback["delivery_proofs"]) == 4
    assert len(readback["result_integrities"]) == 4
    assert readback["git_readback"]["target_branch"] == "main"
    assert all(
        item["target_contains_batch_sha"]
        for item in readback["git_readback"]["batches"]
    )

    candidates = {item["ticket_key"]: item for item in readback["candidate_receipts"]}
    diffs = {
        ticket_key: item
        for ticket_key, item in zip((*standard, strict), readback["candidate_diffs"])
    }
    accepted = {
        item["ticket_key"]: item for item in readback["accepted_candidate_receipts"]
    }
    result_proofs = {
        item["accepted_candidate_receipt_digest"]: item
        for item in readback["result_integrities"]
    }
    deliveries = {
        item["delivery_stable_action_id"]: item for item in readback["delivery_proofs"]
    }
    for ticket_key in (*standard, strict):
        candidate = candidates[ticket_key]
        diff = diffs[ticket_key]
        accepted_candidate = accepted[ticket_key]
        run = work_runs[ticket_key]
        result = result_proofs[accepted_candidate["receipt_digest"]]
        delivery = deliveries[result["delivery_stable_action_id"]]

        assert run["candidate_receipt_digest"] == candidate["receipt_digest"]
        assert candidate["diff_record_digest"] == diff["record_digest"]
        assert (
            accepted_candidate["candidate_receipt_digest"]
            == candidate["receipt_digest"]
        )
        assert accepted_candidate["diff_record_digest"] == diff["record_digest"]
        assert accepted_candidate["candidate_sha"] == candidate["candidate_commit_oid"]
        assert (
            result["accepted_candidate_receipt_digest"]
            == accepted_candidate["receipt_digest"]
        )
        assert result["candidate_commit_oid"] == candidate["candidate_commit_oid"]
        assert result["candidate_diff_record_digest"] == diff["record_digest"]
        assert result["batch_delivery_receipt_digest"] == run["delivery_receipt_digest"]
        assert result["result_digest"] == run["result_digest"]
        assert result["evidence_digests"] == run["evidence_digests"]
        assert delivery["proof_digest"] == result["batch_delivery_proof_digest"]
        assert delivery["batch_id"] == run["batch_id"]
        assert delivery["batch_sha"] == run["batch_sha"]


def test_root_canary_persists_authoritative_candidate_gate_transitions(
    tmp_path: Path,
):
    runner = _load_runner()

    record = runner.run_local_acceptance(
        root=tmp_path,
        run_id="root-candidate-transitions",
        scenario="root",
    )

    transitions = record["facts"]["candidate_gate"]["transitions"]
    by_ticket = {}
    for transition in transitions:
        by_ticket.setdefault(transition["ticket_key"], []).append(transition)

    assert [item["status"] for item in by_ticket["issue:101"]] == ["review_accepted"]
    assert [item["status"] for item in by_ticket["issue:102"]] == [
        "repair_required",
        "repair_accepted",
    ]
    assert [item["status"] for item in by_ticket["issue:103"]] == [
        "ordinary_rejected",
        "review_accepted",
    ]
    assert [item["status"] for item in by_ticket["issue:104"]] == ["review_accepted"]
    assert (
        by_ticket["issue:103"][0]["candidate_receipt"]["candidate_commit_oid"]
        != (by_ticket["issue:103"][1]["candidate_receipt"]["candidate_commit_oid"])
    )
    assert all(transition["persisted"] is True for transition in transitions)


def test_root_canary_uses_public_advance_for_watchdog_lost_wake_duplicate_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _load_runner()
    calls: list[tuple[str, str | None]] = []

    original_advance = runner.gwo_v8.advance

    def traced_advance(handle, wake_ref=None, **kwargs):
        calls.append(("advance", wake_ref))
        return original_advance(handle, wake_ref, **kwargs)

    monkeypatch.setattr(runner.gwo_v8, "advance", traced_advance)

    record = runner.run_local_acceptance(
        root=tmp_path,
        run_id="root-replay",
        scenario="root",
    )

    replay = record["replay"]
    assert replay["watchdog_progressed"] is True
    assert replay["lost_wake"]["status"] == "Complete"
    assert replay["duplicate_callback"]["status"] == "Complete"
    assert replay["restart_advance"]["status"] == "Complete"
    assert replay["readback_unchanged"] is True
    assert replay["idempotent_effects"] is True
    assert replay["semantic_execute_calls_before_restart"] == 4
    assert replay["semantic_execute_calls_after_replay"] == 4
    assert replay["delivery_execute_calls_before_restart"] == 4
    assert replay["delivery_execute_calls_after_replay"] == 4
    assert any(
        name == "advance" and wake_ref == replay["callback_emitted"]
        for name, wake_ref in calls
    )
    assert replay["duplicate_callback_ref"].startswith("watchdog:runtime:")
    assert any(
        name == "advance" and wake_ref == replay["duplicate_callback_ref"]
        for name, wake_ref in calls
    )


def test_root_cli_output_is_canonical_and_deterministic(tmp_path: Path):
    runner = _load_runner()
    command = [
        sys.executable,
        str(RUNNER),
        "--root",
        "PLACEHOLDER_ROOT",
        "--run-id",
        "root-cli-run",
        "--scenario",
        "root",
    ]
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

    first = subprocess.run(
        [
            str(value).replace("PLACEHOLDER_ROOT", str(tmp_path / "first-root"))
            for value in command
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    second = subprocess.run(
        [
            str(value).replace("PLACEHOLDER_ROOT", str(tmp_path / "second-root"))
            for value in command
        ],
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
    assert record["gate"] == "LOCAL_ROOT_CANARY_GO"
    assert record["status"] == "Complete"


def test_root_candidate_readback_uses_real_git_commit_tree_and_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runner = _load_runner()
    _patch_runner_root_lifecycle(runner, tmp_path, monkeypatch)
    record = runner.run_local_acceptance(root=tmp_path, run_id="task2-git-readback", scenario="root")
    repository = tmp_path / "repository"
    candidates = record["facts"]["git_readback"]["candidate_objects"]
    assert len(candidates) == 4
    for candidate in candidates:
        commit = subprocess.run(
            ["git", "rev-parse", f"{candidate['reference']}^{{commit}}"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "rev-parse", f"{candidate['reference']}^{{tree}}"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert (commit, tree) == (candidate["commit_sha"], candidate["tree_sha"])
        assert candidate["diff_record_digest"] in {
            diff["record_digest"] for diff in record["facts"]["readback"]["candidate_diffs"]
        }


def test_root_batch_delivery_uses_real_batch_integrator_and_git_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runner = _load_runner()
    _patch_runner_root_lifecycle(runner, tmp_path, monkeypatch)
    record = runner.run_local_acceptance(root=tmp_path, run_id="task2-batch-readback", scenario="root")
    repository = tmp_path / "repository"
    batches = record["facts"]["git_readback"]["batches"]
    assert [batch["member_ticket_keys"] for batch in batches] == [
        ["issue:101", "issue:102", "issue:103"],
        ["issue:104"],
    ]
    assert all(batch["batch_ref_sha"] == batch["batch_sha"] for batch in batches)
    assert all(batch["target_contains_batch_sha"] for batch in batches)
    for batch in batches:
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", batch["batch_sha"], batch["target_head_sha"]],
            cwd=repository,
            check=False,
        )
        assert ancestry.returncode == 0


def test_root_watchdog_callback_lost_wake_duplicate_and_restart_are_public_advance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runner = _load_runner()
    calls: list[str | None] = []
    original = runner.gwo_v8.advance

    def traced(handle, wake_ref=None, **kwargs):
        calls.append(wake_ref)
        return original(handle, wake_ref, **kwargs)

    monkeypatch.setattr(runner.gwo_v8, "advance", traced)
    record = runner.run_local_acceptance(root=tmp_path, run_id="task2-watchdog", scenario="root")
    replay = record["replay"]
    assert replay["watchdog_progressed"] is True
    assert replay["lost_wake"]["status"] == "Complete"
    assert replay["duplicate_callback"]["status"] == "Complete"
    assert replay["restart_advance"]["status"] == "Complete"
    assert any(wake == replay["callback_emitted"] for wake in calls)
    assert replay["idempotent_effects"] is True


def test_root_worker_slots_release_and_strict_resource_is_exclusive(tmp_path: Path):
    runner = _load_runner()
    record = runner.run_local_acceptance(root=tmp_path, run_id="task2-resources", scenario="root")
    concurrency = record["facts"]["concurrency"]
    resources = record["facts"]["exclusive_resources"]
    assert concurrency["worker_slot_limit"] == 4
    assert concurrency["max_held"] == 4
    assert concurrency["final_held"] == 0
    assert concurrency["final_available"] == 4
    assert resources["issue:101"] == []
    assert resources["issue:102"] == []
    assert resources["issue:103"] == []
    assert resources["issue:104"] == ["repository.target.v1"]


def test_root_acceptance_is_canonical_across_independent_roots(tmp_path: Path):
    runner = _load_runner()
    first = runner.run_local_acceptance(
        root=tmp_path / "first-root", run_id="task2-deterministic", scenario="root"
    )
    second = runner.run_local_acceptance(
        root=tmp_path / "second-root", run_id="task2-deterministic", scenario="root"
    )
    assert first["record_digest"] == second["record_digest"]
    assert runner.canonical_json(first) == runner.canonical_json(second)
    assert len(first["facts"]["git_readback"]["candidate_objects"]) == 4
