from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8._canonical import digest_value
from gwo_v8.cutover_guard import CutoverGuardError, REQUIRED_RUNTIME_SELECTORS
from gwo_v8.plan_control_host import (
    PlanControlError,
    ProductionCutoverReadAdapterResolver,
    RuntimeConfigurationReader,
    install_cutover_guard,
)
from gwo_v8.runtime_gateway import RuntimeGateway
from tests.cutover_guard_test_support import (
    GuardHarness,
    forbidden_runtime_gateway_constructor,
    forbidden_sqlite_connect,
    runtime_configuration_without,
    valid_production_start_host,
    valid_runtime_configuration,
    MutatingLegacyReader,
)


def test_runtime_configuration_reader_resolves_exact_required_selectors_without_gateway_or_store(monkeypatch):
    configuration = valid_runtime_configuration()
    reader = RuntimeConfigurationReader(configuration)
    monkeypatch.setattr(
        RuntimeGateway,
        "__init__",
        forbidden_runtime_gateway_constructor,
    )
    monkeypatch.setattr(sqlite3, "connect", forbidden_sqlite_connect)

    readback = reader.read("owner/repo", REQUIRED_RUNTIME_SELECTORS)

    assert tuple(item.selector for item in readback.selectors) == REQUIRED_RUNTIME_SELECTORS
    assert readback.provider_action_refs == ()
    assert readback.persistence_write_refs == ()
    from gwo_v8.runtime_gateway import _runtime_configuration_canonical

    assert readback.configuration_digest == digest_value(
        _runtime_configuration_canonical(configuration)
    )


def test_runtime_configuration_reader_fails_closed_on_missing_review_strong_mapping():
    configuration = runtime_configuration_without("review_strong")

    with pytest.raises(CutoverGuardError) as error:
        RuntimeConfigurationReader(configuration).read(
            "owner/repo",
            REQUIRED_RUNTIME_SELECTORS,
        )

    assert error.value.code == "CUTOVER_RUNTIME_CONFIGURATION_INVALID"


def test_host_check_is_read_only_and_does_not_expose_activation():
    harness = GuardHarness.valid()
    host = install_cutover_guard(sources=harness.sources)

    report = host.check(harness.subject)

    assert report.decision == "GO"
    assert callable(host.validate_activation)
    assert not hasattr(host, "activate")
    assert not hasattr(host, "publish_activation")
    assert harness.mutation_calls() == ()


def test_existing_start_host_can_install_guard_without_replacing_v3_public_reader(tmp_path):
    start_host = valid_production_start_host(tmp_path)
    harness = GuardHarness.valid()

    guard_host = start_host.install_cutover_guard(sources=harness.sources)

    assert guard_host.check(harness.subject).decision == "GO"
    assert start_host.read_active is not None
    assert start_host.start is not None


def test_guard_host_rejects_a_source_object_with_mutating_surfaces():
    harness = GuardHarness.valid()
    harness.sources = replace(
        harness.sources,
        legacy=MutatingLegacyReader(),
    )

    with pytest.raises(PlanControlError) as error:
        install_cutover_guard(sources=harness.sources)

    assert error.value.code == "CUTOVER_GUARD_COMPOSITION_INVALID"


def test_guard_resolver_rejects_a_mutating_read_adapter_instead_of_wrapping_it():
    harness = GuardHarness.valid()

    with pytest.raises(PlanControlError) as error:
        ProductionCutoverReadAdapterResolver(
            legacy=MutatingLegacyReader(),
            durable_state=harness.durable,
            writer_fence=harness.writer,
            ownership=harness.ownership,
            runtime_configuration=valid_runtime_configuration(),
        )

    assert error.value.code == "CUTOVER_GUARD_COMPOSITION_INVALID"
    assert harness.mutation_calls() == ()
