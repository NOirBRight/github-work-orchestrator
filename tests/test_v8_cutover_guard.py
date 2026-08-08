from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8._canonical import canonical_bytes, digest_value
from gwo_v8.cutover_guard import (
    CutoverGuard,
    CutoverGuardError,
    CutoverReadbackBundle,
    JsonCutoverReadPorts,
    READBACK_BUNDLE_SCHEMA,
)
from tests.cutover_guard_test_support import GuardHarness


def _with_readback_digest(value):
    body = value.canonical()
    body.pop("readback_digest")
    return replace(value, readback_digest=digest_value(body))


def _bundle_canonical(harness):
    return CutoverReadbackBundle(
        schema=READBACK_BUNDLE_SCHEMA,
        subject=harness.subject,
        legacy=harness.legacy.value,
        durable_state=harness.durable.value,
        writer_fence=harness.writer.value,
        ownership=harness.ownership.value,
        compatibility=harness.compatibility.value,
        runtime=harness.runtime.value,
        packages=harness.packages.value,
    ).canonical()


def test_guard_success_returns_digest_bound_read_only_receipt_without_writes():
    harness = GuardHarness.valid()

    report = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert report.decision == "GO"
    assert report.blockers == ()
    assert report.receipt is not None
    assert report.receipt.subject_digest == digest_value(harness.subject.canonical())
    assert report.receipt.readback_digest == report.readback_digest
    assert report.receipt.source_writer_generation == "v6.1"
    assert report.receipt.target_writer_generation == "v8"
    assert report.receipt.receipt_digest == digest_value(
        report.receipt.canonical_without_digest()
    )
    assert harness.mutation_calls() == ()
    assert all(call_count > 0 for call_count in harness.read_call_counts().values())


def test_guard_collects_named_blockers_without_short_circuiting_or_writing():
    harness = GuardHarness.valid()
    harness.legacy.value = _with_readback_digest(
        replace(
            harness.legacy.value,
            active_dispatches=("dispatch:running",),
        )
    )
    harness.writer.value = _with_readback_digest(
        replace(
            harness.writer.value,
            writer_generation="unexpected-writer",
        )
    )
    harness.runtime.value = _with_readback_digest(
        replace(
            harness.runtime.value,
            selectors=(),
        )
    )

    report = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert report.decision == "NO_GO"
    assert {blocker.code for blocker in report.blockers} == {
        "CUTOVER_LEGACY_NOT_QUIESCENT",
        "CUTOVER_SOURCE_WRITER_INVALID",
        "CUTOVER_RUNTIME_CONFIGURATION_INVALID",
    }
    assert harness.mutation_calls() == ()
    assert all(call_count > 0 for call_count in harness.read_call_counts().values())


@pytest.mark.parametrize(
    ("state", "accepted"),
    (("none", True), ("terminal", True), ("quiescent_read_only", True), ("running", False)),
)
def test_guard_accepts_only_terminal_or_quiescent_v2_readback(state, accepted):
    harness = GuardHarness.valid()
    harness.legacy.value = _with_readback_digest(
        replace(
            harness.legacy.value,
            v2_execution_refs=("v2:one",),
            v2_execution_state=state,
        )
    )

    report = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert (report.decision == "GO") is accepted
    if not accepted:
        assert "CUTOVER_V2_ACTIVE" in {blocker.code for blocker in report.blockers}


def test_reader_exception_becomes_named_blocker_and_other_reads_continue():
    harness = GuardHarness.valid()
    harness.ownership.raise_error = RuntimeError("lease read unavailable")

    report = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert report.decision == "NO_GO"
    assert "CUTOVER_OWNERSHIP_READBACK_INVALID" in {
        blocker.code for blocker in report.blockers
    }
    assert all(call_count > 0 for call_count in harness.read_call_counts().values())


def test_malformed_typed_readback_becomes_named_blocker_and_other_reads_continue():
    harness = GuardHarness.valid()
    harness.ownership.value = object()

    report = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert report.decision == "NO_GO"
    assert "CUTOVER_OWNERSHIP_READBACK_INVALID" in {
        blocker.code for blocker in report.blockers
    }
    assert all(call_count > 0 for call_count in harness.read_call_counts().values())


def test_guard_go_and_no_go_never_call_repository_sqlite_github_process_or_runtime_writers():
    harness = GuardHarness.valid()

    go_report = CutoverGuard(harness.sources).evaluate(harness.subject)
    assert go_report.decision == "GO"
    harness.legacy.value = _with_readback_digest(
        replace(
            harness.legacy.value,
            active_workers=("worker:running",),
        )
    )

    no_go_report = CutoverGuard(harness.sources).evaluate(harness.subject)
    assert no_go_report.decision == "NO_GO"
    assert harness.external_writes == {
        "repository": 0,
        "sqlite": 0,
        "github": 0,
        "process": 0,
        "runtime": 0,
    }
    assert harness.mutation_calls() == ()


def test_guard_receipt_digest_changes_when_any_readback_changes():
    harness = GuardHarness.valid()
    first = CutoverGuard(harness.sources).evaluate(harness.subject)
    changed = replace(
        harness.writer.value,
        record_id="writer-record:changed",
    )
    harness.writer.value = _with_readback_digest(changed)

    second = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert first.decision == second.decision == "GO"
    assert first.receipt is not None and second.receipt is not None
    assert first.readback_digest != second.readback_digest
    assert first.receipt.receipt_digest != second.receipt.receipt_digest


def test_guard_rejects_nonempty_active_plan_digests():
    harness = GuardHarness.valid()
    harness.durable.value = _with_readback_digest(
        replace(
            harness.durable.value,
            active_plan_digests=("plan:active",),
        )
    )

    report = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert report.decision == "NO_GO"
    assert "CUTOVER_DURABLE_STATE_INVALID" in {
        blocker.code for blocker in report.blockers
    }
    assert all(call_count > 0 for call_count in harness.read_call_counts().values())


@pytest.mark.parametrize(
    ("port_name", "blocker_code"),
    (
        ("legacy", "CUTOVER_LEGACY_READBACK_INVALID"),
        ("durable", "CUTOVER_DURABLE_STATE_READBACK_INVALID"),
        ("writer", "CUTOVER_WRITER_FENCE_READBACK_INVALID"),
        ("ownership", "CUTOVER_OWNERSHIP_READBACK_INVALID"),
        ("compatibility", "CUTOVER_COMPATIBILITY_READBACK_INVALID"),
        ("runtime", "CUTOVER_RUNTIME_READBACK_INVALID"),
        ("packages", "CUTOVER_PACKAGES_READBACK_INVALID"),
    ),
)
def test_readback_digest_mismatch_is_named_no_go_and_all_reads_continue(
    port_name, blocker_code
):
    harness = GuardHarness.valid()
    port = getattr(harness, port_name)
    port.value = replace(port.value, readback_digest="0" * 64)

    report = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert report.decision == "NO_GO"
    assert blocker_code in {blocker.code for blocker in report.blockers}
    assert all(call_count > 0 for call_count in harness.read_call_counts().values())


def test_invalid_runtime_selector_enum_is_named_no_go_and_all_reads_continue():
    harness = GuardHarness.valid()
    selector = replace(
        harness.runtime.value.selectors[0],
        configuration_source="untrusted",
    )
    harness.runtime.value = _with_readback_digest(
        replace(
            harness.runtime.value,
            selectors=(selector,) + harness.runtime.value.selectors[1:],
        )
    )

    report = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert report.decision == "NO_GO"
    assert "CUTOVER_RUNTIME_READBACK_INVALID" in {
        blocker.code for blocker in report.blockers
    }
    assert all(call_count > 0 for call_count in harness.read_call_counts().values())


def test_invalid_nested_package_identity_is_named_no_go_and_all_reads_continue():
    harness = GuardHarness.valid()
    object.__setattr__(
        harness.packages.value.source_packages[0],
        "content_digest",
        object(),
    )

    report = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert report.decision == "NO_GO"
    assert "CUTOVER_PACKAGES_READBACK_INVALID" in {
        blocker.code for blocker in report.blockers
    }
    assert all(call_count > 0 for call_count in harness.read_call_counts().values())


def test_offline_bundle_rejects_readback_digest_mismatch_with_named_error(tmp_path):
    harness = GuardHarness.valid()
    value = _bundle_canonical(harness)
    value["readbacks"]["durable_state"]["readback_digest"] = "0" * 64
    path = tmp_path / "malformed-bundle.json"
    path.write_bytes(canonical_bytes(value))

    with pytest.raises(CutoverGuardError) as error:
        JsonCutoverReadPorts.load(path)

    assert error.value.code == "CUTOVER_DURABLE_STATE_READBACK_INVALID"


def test_offline_bundle_rejects_invalid_runtime_selector_enum_with_named_error(tmp_path):
    harness = GuardHarness.valid()
    value = _bundle_canonical(harness)
    value["readbacks"]["runtime"]["selectors"][0][
        "configuration_source"
    ] = "untrusted"
    path = tmp_path / "malformed-runtime-bundle.json"
    path.write_bytes(canonical_bytes(value))

    with pytest.raises(CutoverGuardError) as error:
        JsonCutoverReadPorts.load(path)

    assert error.value.code == "CUTOVER_RUNTIME_READBACK_INVALID"


def test_activation_token_validation_re_reads_prerequisites_and_rejects_stale_readback():
    harness = GuardHarness.valid()
    first = CutoverGuard(harness.sources).evaluate(harness.subject)
    assert first.receipt is not None
    harness.writer.value = _with_readback_digest(
        replace(
            harness.writer.value,
            control_ref_digest="c" * 64,
        )
    )

    with pytest.raises(CutoverGuardError) as error:
        CutoverGuard(harness.sources).validate_activation_token(
            harness.subject,
            first.receipt,
        )

    assert error.value.code == "CUTOVER_GUARD_TOKEN_STALE"
    assert harness.mutation_calls() == ()
    assert all(call_count >= 2 for call_count in harness.read_call_counts().values())
