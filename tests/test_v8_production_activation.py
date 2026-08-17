from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8.transition import CurrentWriter  # noqa: E402
from gwo_v8.production_activation import (  # noqa: E402
    ProductionActivationAuthorization,
    ProductionActivationError,
    ProductionActivationFacade,
    ProductionActivationRequest,
)
from tests.cutover_guard_test_support import activation_fixture  # noqa: E402


def _authorization(fixture, tmp_path):
    return ProductionActivationAuthorization(
        run_id="task-8-run",
        repository=fixture.repository,
        source_main_sha=fixture.subject.source_commit,
        source_main_tree="c" * 40,
        target_writer_generation=fixture.subject.target_writer_generation,
        evidence_root=str(tmp_path / "evidence"),
    )


def _request(fixture, authorization):
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None
    canary = replace(fixture.accepted_canary, repository=fixture.repository)
    return ProductionActivationRequest(
        authorization=authorization,
        source_main_sha=authorization.source_main_sha,
        source_main_tree=authorization.source_main_tree,
        compiled_plan=fixture.compiled_plan,
        canary=canary,
        guard_subject=fixture.subject,
        guard_receipt=report.receipt,
        worker_capacity=8,
        coordinator_capacity=1,
    )


@pytest.mark.parametrize(
    "field",
    (
        "run_id",
        "repository",
        "source_main_sha",
        "source_main_tree",
        "target_writer_generation",
        "evidence_root",
    ),
)
def test_authorization_rejects_empty_identity_fields(field, tmp_path):
    values = {
        "run_id": "run",
        "repository": "owner/repository",
        "source_main_sha": "a" * 40,
        "source_main_tree": "b" * 64,
        "target_writer_generation": "v8",
        "evidence_root": str(tmp_path / "evidence"),
    }
    values[field] = ""

    with pytest.raises(ValueError):
        ProductionActivationAuthorization(**values)


def test_execute_rejects_authorization_request_mismatch_before_cutover(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = _request(fixture, authorization)
    mismatched = replace(authorization, run_id="different-run")

    with pytest.raises(ProductionActivationError) as raised:
        ProductionActivationFacade(fixture.controller).execute(
            request,
            authorization=mismatched,
        )

    assert raised.value.code == "AUTHORIZATION_REQUEST_MISMATCH"
    assert fixture.mutation_calls() == ()
    assert fixture.transitions.history(fixture.repository) == ()
    assert fixture.publication.read_active(fixture.repository) is None


def test_preflight_rejects_request_source_identity_mismatch(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = replace(
        _request(fixture, authorization),
        source_main_tree="d" * 40,
    )

    with pytest.raises(ProductionActivationError) as raised:
        ProductionActivationFacade(fixture.controller).preflight(request)

    assert raised.value.code == "AUTHORIZATION_IDENTITY_MISMATCH"
    assert fixture.mutation_calls() == ()
    assert fixture.transitions.history(fixture.repository) == ()


def test_preflight_rejects_invalid_compiled_plan_without_writes(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = replace(
        _request(fixture, authorization),
        compiled_plan=replace(fixture.compiled_plan, digest="0" * 64),
    )

    with pytest.raises(ProductionActivationError) as raised:
        ProductionActivationFacade(fixture.controller).preflight(request)

    assert raised.value.code == "COMPILED_PLAN_DIGEST_MISMATCH"
    assert fixture.mutation_calls() == ()
    assert fixture.transitions.history(fixture.repository) == ()
    assert fixture.publication.read_active(fixture.repository) is None


def test_preflight_rejects_canary_not_accepted_without_writes(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = replace(
        _request(fixture, authorization),
        canary=replace(fixture.accepted_canary, repository=fixture.repository, accepted=False),
    )

    with pytest.raises(ProductionActivationError) as raised:
        ProductionActivationFacade(fixture.controller).preflight(request)

    assert raised.value.code == "CANARY_NOT_ACCEPTED"
    assert fixture.mutation_calls() == ()
    assert fixture.transitions.history(fixture.repository) == ()
    assert fixture.publication.read_active(fixture.repository) is None


def test_preflight_rejects_invalid_guard_receipt_without_writes(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = _request(fixture, authorization)
    fixture.writer_readback.value = replace(
        fixture.writer_readback.value,
        control_ref_digest="d" * 64,
    )

    with pytest.raises(ProductionActivationError) as raised:
        ProductionActivationFacade(fixture.controller).preflight(request)

    assert raised.value.code == "GUARD_RECEIPT_INVALID"
    assert fixture.mutation_calls() == ()
    assert fixture.transitions.history(fixture.repository) == ()
    assert fixture.publication.read_active(fixture.repository) is None


def test_preflight_rejects_non_v61_current_writer_without_writes(tmp_path, monkeypatch):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = _request(fixture, authorization)

    monkeypatch.setattr(
        fixture.transitions,
        "read_current",
        lambda repository: CurrentWriter(repository, "v7", "writer-record:v7"),
    )

    with pytest.raises(ProductionActivationError) as raised:
        ProductionActivationFacade(fixture.controller).preflight(request)

    assert raised.value.code == "SOURCE_WRITER_INVALID"
    assert fixture.mutation_calls() == ()
    assert fixture.transitions.history(fixture.repository) == ()
    assert fixture.publication.read_active(fixture.repository) is None


def test_preflight_rejects_capacity_other_than_8_1_without_writes(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = replace(_request(fixture, authorization), worker_capacity=4)

    with pytest.raises(ProductionActivationError) as raised:
        ProductionActivationFacade(fixture.controller).preflight(request)

    assert raised.value.code == "CUTOVER_CAPACITY_INVALID"
    assert fixture.mutation_calls() == ()
    assert fixture.transitions.history(fixture.repository) == ()
    assert fixture.publication.read_active(fixture.repository) is None


def test_execute_uses_in_memory_controls_and_repeat_is_readback_idempotent(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = _request(fixture, authorization)
    facade = ProductionActivationFacade(fixture.controller)
    preflight = facade.preflight(request)

    first = facade.execute(request, authorization=authorization, preflight=preflight)
    calls_after_first = fixture.mutation_calls()
    second = facade.execute(request, authorization=authorization, preflight=preflight)

    assert first.status == "cut_over"
    assert second == first
    assert fixture.mutation_calls() == calls_after_first
    assert len(fixture.transitions.history(fixture.repository)) == 2
    active = fixture.publication.read_active(fixture.repository)
    assert active is not None
    assert active.plan_digest == fixture.compiled_plan.digest
    assert active.writer_generation == authorization.target_writer_generation
    receipt = fixture.publication.durable.read_current_activation(fixture.repository)
    assert receipt is not None
    assert receipt.activation_id == first.activation_id
    assert fixture.transitions.allows_new_work(
        fixture.repository,
        authorization.target_writer_generation,
        first.activation_id,
    )
    assert fixture.transitions.capacity_limits(
        fixture.repository,
        authorization.target_writer_generation,
        first.activation_id,
    ) == (8, 1)


def test_execute_without_preflight_repeats_from_exact_activation_readback(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = _request(fixture, authorization)
    facade = ProductionActivationFacade(fixture.controller)

    first = facade.execute(request, authorization=authorization)
    calls_after_first = fixture.mutation_calls()
    second = facade.execute(request, authorization=authorization)

    assert second == first
    assert fixture.mutation_calls() == calls_after_first


def test_execute_rechecks_stale_preflight_before_cutover(tmp_path, monkeypatch):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = _request(fixture, authorization)
    facade = ProductionActivationFacade(fixture.controller)
    preflight = facade.preflight(request)
    monkeypatch.setattr(
        fixture.transitions,
        "read_current",
        lambda repository: CurrentWriter(repository, "v7", "writer-record:v7"),
    )

    with pytest.raises(ProductionActivationError) as raised:
        facade.execute(
            request,
            authorization=authorization,
            preflight=preflight,
        )

    assert raised.value.code == "SOURCE_WRITER_INVALID"
    assert fixture.mutation_calls() == ()
