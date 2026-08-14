from __future__ import annotations

import ast
from dataclasses import replace
import os
import shutil
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
import gwo_v8.activation as activation_module  # noqa: E402
import gwo_v8.github_snapshot as github_snapshot_module  # noqa: E402
import gwo_v8.plan_control_host as plan_control_host_module  # noqa: E402
import gwo_v8.runtime as runtime_module  # noqa: E402
import gwo_v8.runtime_gateway as runtime_gateway_module  # noqa: E402
import gwo_v8.transition as transition_module  # noqa: E402
import sync_orchestrator as sync_module  # noqa: E402


_ALLOWED_REPLAY_IMPORTS = {
    "__future__": {"annotations"},
    "dataclasses": {"dataclass"},
    "beta3_bootstrap_model": {
        "AttestedCutoverBundle",
        "BootstrapError",
        "FrozenReadPort",
        "digest_value",
    },
    "gwo_v8.cutover_guard": {
        "CutoverBlocker",
        "CutoverGuardReceipt",
        "CutoverGuardReport",
        "CutoverGuardSources",
        "CutoverReadbackBundle",
        "CutoverSubject",
        "GuardCheck",
    },
    "gwo_v8.plan_control_host": {"install_cutover_guard"},
}
_FORBIDDEN_REPLAY_SURFACES = (
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
_REQUIRED_DYNAMIC_TRIPWIRES = {
    "subprocess.run",
    "subprocess.Popen",
    "socket.socket",
    "socket.create_connection",
    "sqlite3.connect",
    "github.issue_read",
    "github.content_read",
    "paseo.read",
    "runtime.gateway.construct",
    "runtime.gateway.planning_preflight",
    "runtime.gateway.progress",
    "runtime.provider.prepare",
    "runtime.provider.command",
    "runtime.provider.events",
    "artifact.construct",
    "artifact.put",
    "host.construct",
    "install.github",
    "install.production",
    "install.package",
    "copy.tree",
    "replace.path",
    "transition.cutover",
    "transition.rollback",
    "cas.content",
    "cas.ref",
    "activation.guard",
    "activation.publish",
    "activation.start",
}


def _dotted(value):
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        parent = _dotted(value.value)
        return f"{parent}.{value.attr}" if parent else value.attr
    return ""


def _assert_replay_surface(source: str, *, filename: str) -> None:
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in {"dataclasses", "typing"}
                assert not any(
                    token in name
                    for token in _FORBIDDEN_REPLAY_SURFACES
                    for name in (alias.name, alias.asname or "")
                )
        elif isinstance(node, ast.ImportFrom):
            allowed_symbols = _ALLOWED_REPLAY_IMPORTS.get(node.module)
            assert allowed_symbols is not None
            for alias in node.names:
                assert alias.name in allowed_symbols
                assert not any(
                    token in name
                    for token in _FORBIDDEN_REPLAY_SURFACES
                    for name in (alias.name, alias.asname or "")
                )
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name == "install_cutover_guard":
                continue
            assert not any(
                token in name for token in _FORBIDDEN_REPLAY_SURFACES
            ), name


@pytest.fixture
def tripwires(monkeypatch):
    class Tripwires:
        def __init__(self):
            self.external_calls = []
            self.targets = {}

    tripwires = Tripwires()

    def forbidden(name):
        def record(*_args, **_kwargs):
            tripwires.external_calls.append(name)
            raise AssertionError(f"unexpected external call: {name}")

        return record

    def arm(owner, attribute, name):
        recorder = forbidden(name)
        monkeypatch.setattr(owner, attribute, recorder)
        tripwires.targets[name] = (owner, attribute, recorder)

    for owner, attribute, name in (
        (subprocess, "run", "subprocess.run"),
        (subprocess, "Popen", "subprocess.Popen"),
        (socket, "socket", "socket.socket"),
        (socket, "create_connection", "socket.create_connection"),
        (sqlite3, "connect", "sqlite3.connect"),
        (
            github_snapshot_module.GitHubCliIssueReadClient,
            "_run",
            "github.issue_read",
        ),
        (
            activation_module.GitHubCliContentClient,
            "read",
            "github.content_read",
        ),
        (runtime_module.PaseoCliClient, "_run", "paseo.read"),
        (
            runtime_gateway_module.RuntimeGateway,
            "__init__",
            "runtime.gateway.construct",
        ),
        (
            runtime_gateway_module.RuntimeGateway,
            "planning_preflight",
            "runtime.gateway.planning_preflight",
        ),
        (
            runtime_gateway_module.RuntimeGateway,
            "progress",
            "runtime.gateway.progress",
        ),
        (
            runtime_gateway_module._PaseoRuntimeProviderAdapter,
            "prepare",
            "runtime.provider.prepare",
        ),
        (
            runtime_gateway_module._PaseoRuntimeProviderAdapter,
            "command",
            "runtime.provider.command",
        ),
        (
            runtime_gateway_module._PaseoRuntimeProviderAdapter,
            "events",
            "runtime.provider.events",
        ),
        (
            runtime_gateway_module.ArtifactStore,
            "__init__",
            "artifact.construct",
        ),
        (runtime_gateway_module.ArtifactStore, "put", "artifact.put"),
        (
            plan_control_host_module.ProductionPlanControlStartHost,
            "__init__",
            "host.construct",
        ),
        (
            plan_control_host_module,
            "install_github_plan_control_start",
            "install.github",
        ),
        (
            plan_control_host_module,
            "install_production_start_host",
            "install.production",
        ),
        (sync_module, "install_atomic", "install.package"),
        (shutil, "copytree", "copy.tree"),
        (os, "replace", "replace.path"),
        (
            transition_module.WriterCutoverController,
            "cutover",
            "transition.cutover",
        ),
        (
            transition_module.WriterCutoverController,
            "rollback",
            "transition.rollback",
        ),
        (
            activation_module.GitHubCliContentClient,
            "compare_and_swap",
            "cas.content",
        ),
        (
            activation_module.GitHubCliContentClient,
            "compare_and_swap_ref",
            "cas.ref",
        ),
        (
            plan_control_host_module.ProductionCutoverGuardHost,
            "validate_activation",
            "activation.guard",
        ),
        (
            activation_module.LocalPlanPublication,
            "publish_and_activate",
            "activation.publish",
        ),
        (
            plan_control_host_module.ProductionPlanControlStartHost,
            "start",
            "activation.start",
        ),
    ):
        arm(owner, attribute, name)
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


def test_required_dynamic_tripwires_are_armed_on_real_surfaces(tripwires):
    assert set(tripwires.targets) == _REQUIRED_DYNAMIC_TRIPWIRES
    for name in sorted(tripwires.targets):
        owner, attribute, recorder = tripwires.targets[name]
        assert getattr(owner, attribute) is recorder
        with pytest.raises(AssertionError):
            getattr(owner, attribute)()
    assert tripwires.external_calls == sorted(_REQUIRED_DYNAMIC_TRIPWIRES)


@pytest.mark.parametrize(
    "malicious",
    (
        "from gwo_v8.cutover_guard import RuntimeGateway as Harmless\n",
        "from gwo_v8.cutover_guard import CutoverGuardReport as ArtifactStore\n",
    ),
)
def test_static_gate_rejects_forbidden_symbol_from_allowlisted_module(malicious):
    with pytest.raises(AssertionError):
        _assert_replay_surface(malicious, filename="malicious_replay.py")


def test_replay_module_has_only_the_frozen_guard_call_surface():
    path = REPO_ROOT / "scripts" / "beta3_replay_guard.py"
    _assert_replay_surface(
        path.read_text(encoding="utf-8"),
        filename=str(path),
    )
