from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8.transition import (  # noqa: E402
    CurrentWriter,
    WriterTransitionRecord,
)
from gwo_v8._canonical import canonical_bytes, digest_bytes, digest_value  # noqa: E402
from gwo_v8.production_activation import (  # noqa: E402
    ProductionActivationAuthorization,
    ProductionActivationAuthorizationReceipt,
    ProductionActivationError,
    ProductionActivationFacade,
    ProductionActivationRequest,
)
from tests.cutover_guard_test_support import activation_fixture  # noqa: E402


def _authorization(fixture, tmp_path):
    return ProductionActivationAuthorization(
        run_id="task-8-run",
        repository=fixture.repository,
        merged_main_sha=fixture.subject.source_commit,
        merged_main_git_tree="c" * 40,
        release_subject_digest="d" * 64,
        evidence_root=str(tmp_path / "evidence"),
        target_repository=fixture.repository,
        writer_transition="v6.1 -> v8",
        target_writer_generation=fixture.subject.target_writer_generation,
    )


class _AuthorizationSource:
    def __init__(self, receipt):
        self.receipt = receipt

    def read(self, authorization):
        del authorization
        return self.receipt


def _authorization_receipt(authorization):
    identity = {
        **authorization.canonical_without_receipt_fields(),
        "approval_ref": (
            f"github://{authorization.repository}/owner-approval/{authorization.run_id}"
        ),
    }
    return ProductionActivationAuthorizationReceipt(
        **identity,
        receipt_digest=digest_value(identity),
    )


class _CanaryControl:
    def __init__(
        self,
        delegate,
        *,
        manifest_bytes=None,
        missing_ref=None,
    ):
        self.delegate = delegate
        self.manifest_bytes = manifest_bytes
        self.missing_ref = missing_ref

    def read(self, source_ref):
        if source_ref == self.missing_ref:
            return None
        return self.delegate.read(source_ref)

    def read_manifest(self, manifest_ref):
        if self.manifest_bytes is not None:
            return self.manifest_bytes
        return self.delegate.read_manifest(manifest_ref)


def _facade(fixture, authorization, *, canary=True, canary_control=None):
    arguments = {
        "authorization_source": _AuthorizationSource(
            _authorization_receipt(authorization),
        ),
    }
    if canary:
        arguments["canary_evidence_control"] = (
            fixture.canary_evidence_control
            if canary_control is None
            else canary_control
        )
    return ProductionActivationFacade(fixture.controller, **arguments)


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


def _publish_pending(fixture, request, *, manifest_ref=None, plan_digest=None):
    authorization = request.authorization
    canary = request.canary
    fixture.transitions.publish(
        WriterTransitionRecord(
            record_id="writer-transition:pending",
            repository=authorization.target_repository,
            kind="cutover_pending",
            status="pending",
            previous_writer_generation="v6.1",
            writer_generation=authorization.target_writer_generation,
            activation_id=None,
            plan_digest=(
                request.compiled_plan.digest
                if plan_digest is None
                else plan_digest
            ),
            canary_evidence_digest=canary.evidence_package_digest,
            canary_evidence_refs=canary.evidence_refs,
            canary_manifest_ref=(
                canary.manifest_ref if manifest_ref is None else manifest_ref
            ),
            worker_capacity=0,
            coordinator_capacity=0,
            reason=None,
            created_at="2026-08-17T00:00:00+00:00",
        ),
    )
    fixture.calls.clear()


@pytest.mark.parametrize(
    "field",
    (
        "run_id",
        "repository",
        "merged_main_sha",
        "merged_main_git_tree",
        "release_subject_digest",
        "target_repository",
        "writer_transition",
        "target_writer_generation",
        "evidence_root",
    ),
)
def test_authorization_rejects_empty_identity_fields(field, tmp_path):
    values = {
        "run_id": "run",
        "repository": "owner/repository",
        "merged_main_sha": "a" * 40,
        "merged_main_git_tree": "b" * 40,
        "release_subject_digest": "c" * 64,
        "target_repository": "owner/repository",
        "writer_transition": "v6.1 -> v8",
        "target_writer_generation": "v8",
        "evidence_root": str(tmp_path / "evidence"),
    }
    values[field] = ""

    with pytest.raises(ValueError):
        ProductionActivationAuthorization(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("merged_main_sha", "A" * 40),
        ("merged_main_sha", "a" * 39),
        ("merged_main_git_tree", "b" * 64),
        ("merged_main_git_tree", "g" * 40),
    ),
)
def test_authorization_rejects_noncanonical_main_commit_or_tree(field, value, tmp_path):
    values = {
        "run_id": "run",
        "repository": "owner/repository",
        "merged_main_sha": "a" * 40,
        "merged_main_git_tree": "b" * 40,
        "release_subject_digest": "c" * 64,
        "target_repository": "owner/repository",
        "writer_transition": "v6.1 -> v8",
        "target_writer_generation": "v8",
        "evidence_root": str(tmp_path / "evidence"),
    }
    values[field] = value

    with pytest.raises(ValueError):
        ProductionActivationAuthorization(**values)


def test_preflight_requires_authoritative_authorization_provenance_readback(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = _request(fixture, authorization)

    with pytest.raises(ProductionActivationError) as raised:
        ProductionActivationFacade(fixture.controller).preflight(request)

    assert raised.value.code == "AUTHORIZATION_PROVENANCE_REQUIRED"
    assert fixture.mutation_calls() == ()
    assert fixture.transitions.history(fixture.repository) == ()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("run_id", "different-run"),
        ("repository", "other/repository"),
        ("merged_main_sha", "d" * 40),
        ("merged_main_git_tree", "e" * 40),
        ("target_writer_generation", "v8-other"),
        ("evidence_root", "other-evidence-root"),
    ),
)
def test_preflight_rejects_authorization_provenance_readback_mismatch(
    field,
    value,
    tmp_path,
):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = _request(fixture, authorization)
    changed = replace(authorization, **{field: value})
    facade = ProductionActivationFacade(
        fixture.controller,
        authorization_source=_AuthorizationSource(
            _authorization_receipt(changed),
        ),
    )

    with pytest.raises(ProductionActivationError) as raised:
        facade.preflight(request)

    assert raised.value.code == "AUTHORIZATION_PROVENANCE_INVALID"
    assert fixture.mutation_calls() == ()


def test_source_main_tree_is_not_guard_subject_source_tree_digest(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    assert authorization.source_main_tree != fixture.subject.source_tree_digest

    preflight = _facade(fixture, authorization).preflight(
        _request(fixture, authorization),
    )

    assert preflight.authorization_receipt.source_main_tree == (
        authorization.source_main_tree
    )


def test_preflight_requires_durable_canary_evidence_readback_dependency(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)

    with pytest.raises(ProductionActivationError) as raised:
        _facade(fixture, authorization, canary=False).preflight(
            _request(fixture, authorization),
        )

    assert raised.value.code == "CANARY_VERIFIER_REQUIRED"
    assert fixture.mutation_calls() == ()


def test_preflight_revalidates_canary_manifest_package_digest(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    control = _CanaryControl(
        fixture.canary_evidence_control,
        manifest_bytes=b"tampered",
    )

    with pytest.raises(ProductionActivationError) as raised:
        _facade(
            fixture,
            authorization,
            canary_control=control,
        ).preflight(_request(fixture, authorization))

    assert raised.value.code == "CANARY_EVIDENCE_READBACK_INVALID"
    assert fixture.mutation_calls() == ()


def test_preflight_revalidates_canary_manifest_repository(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    manifest_ref = fixture.accepted_canary.manifest_ref
    assert manifest_ref is not None
    original = fixture.canary_evidence_control.read_manifest(manifest_ref)
    assert original is not None
    package = json.loads(original)
    package["repository"] = "other/repository"
    modified = canonical_bytes(package)
    canary = replace(
        fixture.accepted_canary,
        evidence_package_digest=digest_bytes(modified),
    )
    control = _CanaryControl(
        fixture.canary_evidence_control,
        manifest_bytes=modified,
    )

    with pytest.raises(ProductionActivationError) as raised:
        _facade(
            fixture,
            authorization,
            canary_control=control,
        ).preflight(
            replace(_request(fixture, authorization), canary=canary),
        )

    assert raised.value.code == "CANARY_EVIDENCE_READBACK_INVALID"
    assert fixture.mutation_calls() == ()


@pytest.mark.parametrize(
    ("manifest_ref", "evidence_ref"),
    (
        ("memory://manifest", None),
        (None, "synthetic://evidence"),
        (None, "memory://evidence"),
    ),
)
def test_preflight_rejects_non_durable_canary_references(
    manifest_ref,
    evidence_ref,
    tmp_path,
):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    canary = fixture.accepted_canary
    if manifest_ref is not None:
        canary = replace(canary, manifest_ref=manifest_ref)
    else:
        canary = replace(
            canary,
            evidence_refs=(evidence_ref, *canary.evidence_refs[1:]),
        )

    with pytest.raises(ProductionActivationError) as raised:
        _facade(
            fixture,
            authorization,
        ).preflight(
            replace(_request(fixture, authorization), canary=canary),
        )

    assert raised.value.code == "CANARY_EVIDENCE_READBACK_INVALID"
    assert fixture.mutation_calls() == ()


def test_preflight_rejects_missing_canary_evidence_readback(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    missing_ref = fixture.accepted_canary.evidence_refs[0]
    control = _CanaryControl(
        fixture.canary_evidence_control,
        missing_ref=missing_ref,
    )

    with pytest.raises(ProductionActivationError) as raised:
        _facade(
            fixture,
            authorization,
            canary_control=control,
        ).preflight(_request(fixture, authorization))

    assert raised.value.code == "CANARY_EVIDENCE_READBACK_INVALID"
    assert fixture.mutation_calls() == ()


def test_execute_resumes_an_exact_pending_cutover_via_controller(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = _request(fixture, authorization)
    _publish_pending(fixture, request)

    outcome = _facade(fixture, authorization).execute(
        request,
        authorization=authorization,
    )

    assert outcome.status == "cut_over"
    assert outcome.writer_generation == authorization.target_writer_generation
    assert "legacy.stop" in fixture.mutation_calls()
    assert fixture.transitions.history(fixture.repository)[-1].status == "cut_over"


def test_execute_rejects_a_nonmatching_pending_cutover_without_resuming(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = _request(fixture, authorization)
    _publish_pending(
        fixture,
        request,
        manifest_ref="github://different-manifest",
    )

    with pytest.raises(ProductionActivationError) as raised:
        _facade(fixture, authorization).execute(
            request,
            authorization=authorization,
        )

    assert raised.value.code == "ACTIVATION_READBACK_INVALID"
    assert fixture.mutation_calls() == ()
    assert len(fixture.transitions.history(fixture.repository)) == 1


def test_execute_rejects_compilation_record_mutation_after_preflight(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = _request(fixture, authorization)
    facade = _facade(fixture, authorization)
    preflight = facade.preflight(request)
    request.compiled_plan.compilation_record["source_digest"] = "0" * 64

    with pytest.raises(ProductionActivationError) as raised:
        facade.execute(
            request,
            authorization=authorization,
            preflight=preflight,
        )

    assert raised.value.code == "COMPILED_PLAN_IDENTITY_CHANGED"
    assert fixture.mutation_calls() == ()
    assert fixture.transitions.history(fixture.repository) == ()


def test_execute_uses_immutable_plan_snapshot_at_controller_boundary(
    tmp_path,
    monkeypatch,
):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = _request(fixture, authorization)
    facade = _facade(fixture, authorization)
    preflight = facade.preflight(request)
    expected_record = json.loads(canonical_bytes(request.compiled_plan.compilation_record))
    observed_plans = []
    original_cutover = fixture.controller.cutover

    def cutover(compiled_plan, **kwargs):
        request.compiled_plan.compilation_record["source_digest"] = "0" * 64
        observed_plans.append(compiled_plan)
        return original_cutover(compiled_plan, **kwargs)

    monkeypatch.setattr(fixture.controller, "cutover", cutover)

    outcome = facade.execute(
        request,
        authorization=authorization,
        preflight=preflight,
    )

    assert outcome.status == "cut_over"
    assert len(observed_plans) == 1
    assert observed_plans[0] is not request.compiled_plan
    assert observed_plans[0].compilation_record == expected_record
    active = fixture.publication.read_active(fixture.repository)
    assert active is not None
    assert active.compilation_record == expected_record


def test_execute_rejects_authorization_request_mismatch_before_cutover(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = _request(fixture, authorization)
    mismatched = replace(authorization, run_id="different-run")

    with pytest.raises(ProductionActivationError) as raised:
        _facade(fixture, authorization).execute(
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
        _facade(fixture, authorization).preflight(request)

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
        _facade(fixture, authorization).preflight(request)

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
        _facade(fixture, authorization).preflight(request)

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
        _facade(fixture, authorization).preflight(request)

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
        _facade(fixture, authorization).preflight(request)

    assert raised.value.code == "SOURCE_WRITER_INVALID"
    assert fixture.mutation_calls() == ()
    assert fixture.transitions.history(fixture.repository) == ()
    assert fixture.publication.read_active(fixture.repository) is None


def test_preflight_rejects_capacity_other_than_8_1_without_writes(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = replace(_request(fixture, authorization), worker_capacity=4)

    with pytest.raises(ProductionActivationError) as raised:
        _facade(fixture, authorization).preflight(request)

    assert raised.value.code == "CUTOVER_CAPACITY_INVALID"
    assert fixture.mutation_calls() == ()
    assert fixture.transitions.history(fixture.repository) == ()
    assert fixture.publication.read_active(fixture.repository) is None


def test_execute_uses_in_memory_controls_and_repeat_is_readback_idempotent(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = _request(fixture, authorization)
    facade = _facade(fixture, authorization)
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
    facade = _facade(fixture, authorization)

    first = facade.execute(request, authorization=authorization)
    calls_after_first = fixture.mutation_calls()
    second = facade.execute(request, authorization=authorization)

    assert second == first
    assert fixture.mutation_calls() == calls_after_first


def test_execute_rechecks_stale_preflight_before_cutover(tmp_path, monkeypatch):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    request = _request(fixture, authorization)
    facade = _facade(fixture, authorization)
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
