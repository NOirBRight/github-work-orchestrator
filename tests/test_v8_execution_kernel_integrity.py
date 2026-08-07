from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

pytest_plugins = ("v8_production_test_support",)

from gwo_v8.execution_kernel import ExecutionKernelError


def test_sqlite_campaign_state_rejects_stale_writer_without_overwriting(
    tmp_path,
    handle,
    active_plan,
    make_kernel,
):
    first = make_kernel(tmp_path / "kernel.sqlite3", active_plan)
    second = make_kernel(tmp_path / "kernel.sqlite3", active_plan)
    first.advance(handle)
    left = first._read_state(handle)
    right = second._read_state(handle)
    left_state = dict(left.state)
    left_state["test_marker"] = "left"
    first._save(handle, left_state, expected_version=left.version)
    right_state = dict(right.state)
    right_state["test_marker"] = "right"
    with pytest.raises(ExecutionKernelError) as raised:
        second._save(handle, right_state, expected_version=right.version)
    assert raised.value.code == "EXECUTION_STORE_CAS_CONFLICT"
    assert first._load(handle)["test_marker"] == "left"


def test_inspect_does_not_write_or_migrate_campaign_state(
    tmp_path,
    handle,
    active_plan,
    make_kernel,
    monkeypatch,
):
    kernel = make_kernel(tmp_path / "kernel.sqlite3", active_plan)
    kernel.advance(handle)
    before = (tmp_path / "kernel.sqlite3").read_bytes()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("inspect attempted a state migration or write")

    monkeypatch.setattr(kernel, "_load_or_initialize", forbidden)
    monkeypatch.setattr(kernel, "_save", forbidden)
    diagnostics = kernel.inspect(handle)
    after = (tmp_path / "kernel.sqlite3").read_bytes()
    assert diagnostics.campaign == handle
    assert after == before


def test_inspect_projects_missing_historical_diagnostic_fields_without_writing(
    tmp_path,
    handle,
    active_plan,
    make_kernel,
):
    kernel = make_kernel(tmp_path / "kernel.sqlite3", active_plan)
    kernel.advance(handle)
    readback = kernel._read_state(handle)
    historical = dict(readback.state)
    historical.pop("effects")
    run = historical["runs"][next(iter(historical["runs"]))]
    for field in (
        "phase",
        "slot_held",
        "work_subject_digest",
        "work_run_key",
        "exclusive_resources",
        "claim_state",
        "candidate_identity",
        "result_digest",
        "evidence_digests",
    ):
        run.pop(field, None)
    kernel._save(handle, historical, expected_version=readback.version)

    before = (tmp_path / "kernel.sqlite3").read_bytes()
    diagnostics = kernel.inspect(handle)
    after = (tmp_path / "kernel.sqlite3").read_bytes()

    assert diagnostics.campaign == handle
    assert diagnostics.work_runs
    assert after == before


def test_raw_wake_cas_does_not_advance_trusted_progress_or_reset_staleness(
    tmp_path,
    handle,
    active_plan,
    make_kernel,
):
    kernel = make_kernel(tmp_path / "kernel.sqlite3", active_plan)
    kernel.advance(handle)
    before = kernel._read_state(handle)
    run = before.state["runs"][next(iter(before.state["runs"]))]
    trusted = (
        run["trusted_progress_revision"],
        run["last_trusted_progress_at"],
        run["stale_due_at"],
    )
    state = dict(before.state)
    state["last_wake_ref"] = "watchdog:raw:41"
    after_version = kernel._save(handle, state, expected_version=before.version)
    after = kernel._read_state(handle)
    updated_run = after.state["runs"][next(iter(after.state["runs"]))]
    assert after_version == before.version + 1
    assert after.version == before.version + 1
    assert (
        updated_run["trusted_progress_revision"],
        updated_run["last_trusted_progress_at"],
        updated_run["stale_due_at"],
    ) == trusted
