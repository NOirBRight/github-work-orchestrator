from __future__ import annotations

import ast
from dataclasses import replace
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (
    REPO_ROOT / "scripts",
    REPO_ROOT / "skills" / "orchestrator" / "scripts",
    REPO_ROOT / "tests",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from beta3_bootstrap_model import (  # noqa: E402
    AttestedCutoverBundle,
    AttemptIdentity,
    BootstrapError,
    ComponentObservation,
    FieldBinding,
    SourceRecord,
)
import beta3_replay_guard as replay_module  # noqa: E402
from beta3_replay_guard import (  # noqa: E402
    ReplayResult,
    evaluate_attested_bundle,
)
from cutover_guard_test_support import (  # noqa: E402
    EXPECTED_CHECK_IDS,
    GuardHarness,
)
from gwo_v8._canonical import digest_value  # noqa: E402
from gwo_v8.cutover_guard import (  # noqa: E402
    CutoverGuardReport,
    CutoverReadbackBundle,
)


@pytest.fixture
def tripwires(monkeypatch):
    class Tripwires:
        external_calls: list[str] = []

    tripwires = Tripwires()

    def forbidden(name):
        def record(*_args, **_kwargs):
            tripwires.external_calls.append(name)
            raise AssertionError(f"unexpected external call: {name}")

        return record

    monkeypatch.setattr(subprocess, "run", forbidden("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", forbidden("subprocess.Popen"))
    monkeypatch.setattr(socket, "socket", forbidden("socket.socket"))
    monkeypatch.setattr(
        socket,
        "create_connection",
        forbidden("socket.create_connection"),
    )
    monkeypatch.setattr(sqlite3, "connect", forbidden("sqlite3.connect"))
    return tripwires


def _attested_bundle(*, active: bool = False) -> AttestedCutoverBundle:
    harness = GuardHarness.valid()
    if active:
        legacy = replace(
            harness.legacy.value,
            v2_execution_refs=("v2:running",),
            v2_execution_state="running",
        )
        legacy_body = legacy.canonical()
        legacy_body.pop("readback_digest")
        harness.legacy.value = replace(
            legacy,
            readback_digest=digest_value(legacy_body),
        )

    readbacks = (
        ("legacy", harness.legacy.value),
        ("durable_state", harness.durable.value),
        ("writer_fence", harness.writer.value),
        ("ownership", harness.ownership.value),
        ("compatibility", harness.compatibility.value),
        ("runtime", harness.runtime.value),
        ("packages", harness.packages.value),
    )
    attempt = AttemptIdentity.create(
        run_id="beta3-replay-test",
        repository=harness.subject.repository,
        evidence_root=r"D:\evidence",
        cutover_subject_digest=digest_value(harness.subject.canonical()),
        runner_sha256="1" * 64,
        attestor_sha256="2" * 64,
        nonce_factory=lambda size: "3" * (size * 2),
    )
    source_record = SourceRecord(
        role="fixture.replay",
        locator="fixture://beta3/replay",
        repository=harness.subject.repository,
        read_mode="FIXTURE",
        identity=(("fixture_id", "replay"),),
        content_sha256="4" * 64,
        readback_digest=None,
        producer_sha256=attempt.attestor_sha256,
    )
    source_digest = source_record.digest
    targets = tuple(
        f"{name}.{field}"
        for name, value in (("subject", harness.subject), *readbacks)
        for field in value.canonical()
    )
    bindings = tuple(
        FieldBinding(
            target=target,
            source_record_digests=(source_digest,),
            derivation="fixture.replay",
        )
        for target in targets
    )
    component = ComponentObservation(
        readbacks=readbacks,
        source_records=(source_record,),
        field_bindings=bindings,
    )
    return AttestedCutoverBundle.create(
        attempt=attempt,
        subject=harness.subject,
        components=(component,),
    )


@pytest.fixture
def valid_attested_bundle():
    return _attested_bundle()


@pytest.fixture
def active_bundle():
    return _attested_bundle(active=True)


def test_replay_go_uses_exact_bundle_and_zero_external_calls(
    valid_attested_bundle,
    tripwires,
):
    result = evaluate_attested_bundle(valid_attested_bundle)

    assert type(result) is ReplayResult
    assert type(result.report) is CutoverGuardReport
    assert result.report.decision == "GO"
    assert tuple(check.check_id for check in result.report.checks) == EXPECTED_CHECK_IDS
    assert result.report.receipt is not None
    assert result.subject is valid_attested_bundle.subject
    assert result.attestation_digest == valid_attested_bundle.attestation_digest
    assert tripwires.external_calls == []


def test_replay_no_go_collects_blocker_and_still_has_zero_external_calls(
    active_bundle,
    tripwires,
):
    result = evaluate_attested_bundle(active_bundle)

    assert result.report.decision == "NO_GO"
    assert result.report.receipt is None
    assert {blocker.code for blocker in result.report.blockers} == {
        "CUTOVER_V2_ACTIVE"
    }
    assert tripwires.external_calls == []


def test_replay_cross_validates_report_readback_and_receipt(valid_attested_bundle):
    result = evaluate_attested_bundle(valid_attested_bundle)
    readbacks = (
        ("legacy", valid_attested_bundle.legacy),
        ("durable_state", valid_attested_bundle.durable_state),
        ("writer_fence", valid_attested_bundle.writer_fence),
        ("ownership", valid_attested_bundle.ownership),
        ("compatibility", valid_attested_bundle.compatibility),
        ("runtime", valid_attested_bundle.runtime),
        ("packages", valid_attested_bundle.packages),
    )
    expected_readback_digest = digest_value(
        {name: value.canonical() for name, value in readbacks}
    )
    observed_by_check = {
        "source_writer": valid_attested_bundle.writer_fence,
        "legacy_quiescence": valid_attested_bundle.legacy,
        "durable_state": valid_attested_bundle.durable_state,
        "writer_and_lease": valid_attested_bundle.ownership,
        "production_paths": valid_attested_bundle.compatibility,
        "runtime_configuration": valid_attested_bundle.runtime,
        "package_installation": valid_attested_bundle.packages,
    }

    assert type(result.readback_bundle) is CutoverReadbackBundle
    assert result.report.schema == "gwo.cutover-guard.v1"
    assert result.report.repository == valid_attested_bundle.subject.repository
    assert result.report.subject_digest == digest_value(
        valid_attested_bundle.subject.canonical()
    )
    assert result.report.readback_digest == expected_readback_digest
    assert tuple(
        check.observed_digest for check in result.report.checks
    ) == tuple(
        digest_value(observed_by_check[check_id].canonical())
        for check_id in EXPECTED_CHECK_IDS
    )
    assert result.report.receipt is not None
    assert result.report.receipt.readback_digest == expected_readback_digest
    assert result.report.receipt.receipt_digest == digest_value(
        result.report.receipt.canonical_without_digest()
    )


def test_replay_rejects_guard_report_mismatch(valid_attested_bundle, monkeypatch):
    original_install = replay_module.install_cutover_guard

    def install_with_mismatched_report(*, sources):
        host = original_install(sources=sources)

        class MismatchedHost:
            def check(self, subject):
                return replace(host.check(subject), readback_digest="0" * 64)

        return MismatchedHost()

    monkeypatch.setattr(
        replay_module,
        "install_cutover_guard",
        install_with_mismatched_report,
    )

    with pytest.raises(BootstrapError) as error:
        evaluate_attested_bundle(valid_attested_bundle)

    assert error.value.code == "LIVE_GUARD_INVALID"


def test_replay_module_has_only_the_frozen_guard_call_surface():
    path = REPO_ROOT / "scripts" / "beta3_replay_guard.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    allowed_modules = {
        "dataclasses",
        "beta3_bootstrap_model",
        "gwo_v8.cutover_guard",
        "gwo_v8.plan_control_host",
    }
    forbidden = (
        "subprocess",
        "socket",
        "sqlite3",
        "requests",
        "urllib",
        "github",
        "paseo",
        "provider",
        "RuntimeGateway",
        "ProductionPlanControlStartHost",
        "ArtifactStore",
        "install_github_plan_control_start",
        "transition",
        "activation",
        "compare_and_swap",
        "publish",
        "put",
        "write",
        "start",
        "stop",
        "restore",
        "drain",
    )

    def dotted(value):
        if isinstance(value, ast.Name):
            return value.id
        if isinstance(value, ast.Attribute):
            parent = dotted(value.value)
            return f"{parent}.{value.attr}" if parent else value.attr
        return ""

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
            assert all(name in {"dataclasses"} for name in imported)
        elif isinstance(node, ast.ImportFrom):
            assert node.module in allowed_modules or node.module == "__future__"
        elif isinstance(node, ast.Call):
            name = dotted(node.func)
            if name == "install_cutover_guard":
                continue
            assert not any(token in name for token in forbidden), name
