from __future__ import annotations

from base64 import b64encode
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent.parent.parent / "scripts"))

from gwo_v8._canonical import digest_value  # noqa: E402
from gwo_v8.production_activation import (  # noqa: E402
    ProductionActivationAuthorization,
    ProductionActivationAuthorizationReceipt,
    ProductionActivationComposition,
    ProductionActivationError,
    ProductionActivationRequest,
)
from gwo_v8.transition import CanaryAcceptance  # noqa: E402
import run_v8_production_activation as activation_cli  # noqa: E402
from run_v8_production_activation import (  # noqa: E402
    build_activation_bundle,
    run_production_activation,
)
from tests.cutover_guard_test_support import activation_fixture  # noqa: E402


RELEASE_SUBJECT_DIGEST = "d" * 64


def _authorization(fixture, tmp_path):
    return ProductionActivationAuthorization(
        run_id="phase5-production-activation-test",
        repository="NOirBRight/github-work-orchestrator",
        merged_main_sha=fixture.subject.source_commit,
        merged_main_git_tree="c" * 40,
        release_subject_digest=RELEASE_SUBJECT_DIGEST,
        evidence_root=str(tmp_path / "evidence"),
        target_repository=fixture.repository,
        writer_transition="v6.1 -> v8",
        target_writer_generation=fixture.subject.target_writer_generation,
    )


def _authorization_receipt(authorization):
    body = {
        **authorization.canonical_without_receipt_fields(),
        "approval_ref": (
            f"github://{authorization.repository}/owner-approval/{authorization.run_id}"
        ),
    }
    return ProductionActivationAuthorizationReceipt(
        **body,
        receipt_digest=digest_value(body),
    )


def _request(fixture, authorization):
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None
    canary = replace(fixture.accepted_canary, repository=fixture.repository)
    return ProductionActivationRequest(
        authorization=authorization,
        source_main_sha=authorization.merged_main_sha,
        source_main_tree=authorization.merged_main_git_tree,
        compiled_plan=fixture.compiled_plan,
        canary=canary,
        guard_subject=fixture.subject,
        guard_receipt=report.receipt,
        worker_capacity=8,
        coordinator_capacity=1,
    )


def _payload(fixture, authorization, receipt):
    request = _request(fixture, authorization)
    return {
        "authorization": authorization.canonical(),
        "authorization_receipt": receipt.canonical(),
        "compiled_plan": {
            "repository": request.compiled_plan.repository,
            "canonical_bytes_base64": b64encode(
                request.compiled_plan.canonical_bytes
            ).decode("ascii"),
            "digest": request.compiled_plan.digest,
            "compilation_record": request.compiled_plan.compilation_record,
        },
        "canary": asdict(request.canary),
        "guard_subject": request.guard_subject.canonical(),
        "guard_receipt": request.guard_receipt.canonical(),
        "worker_capacity": request.worker_capacity,
        "coordinator_capacity": request.coordinator_capacity,
    }


class _Factory:
    def __init__(self, fixture):
        self.fixture = fixture
        self.calls: list[dict[str, object]] = []

    def compose(self, **values: object) -> ProductionActivationComposition:
        self.calls.append(values)
        return ProductionActivationComposition(
            controller=self.fixture.controller,
            canary_evidence_control=self.fixture.canary_evidence_control,
        )


def test_authorization_binds_exact_release_and_writer_transition_identity(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)

    assert authorization.merged_main_sha == fixture.subject.source_commit
    assert authorization.merged_main_git_tree == "c" * 40
    assert authorization.source_main_sha == authorization.merged_main_sha
    assert authorization.source_main_tree == authorization.merged_main_git_tree
    assert authorization.release_subject_digest == RELEASE_SUBJECT_DIGEST
    assert authorization.target_repository == fixture.repository
    assert authorization.writer_transition == "v6.1 -> v8"


def test_activation_bundle_builds_all_typed_inputs_and_controller_wiring(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    receipt = _authorization_receipt(authorization)
    bundle = build_activation_bundle(_payload(fixture, authorization, receipt))
    factory = _Factory(fixture)

    result = run_production_activation(bundle, factory=factory, execute=True)

    assert result.outcome is not None
    assert result.outcome.status == "cut_over"
    assert len(factory.calls) == 1
    call = factory.calls[0]
    assert type(call["authorization"]) is ProductionActivationAuthorization
    assert type(call["compiled_plan"]).__name__ == "CompiledPlan"
    assert type(call["canary"]) is CanaryAcceptance
    assert type(call["guard_subject"]).__name__ == "CutoverSubject"
    assert type(call["guard_receipt"]).__name__ == "CutoverGuardReceipt"
    assert type(result.composition.controller).__name__ == "WriterCutoverController"


def test_activation_bundle_rejects_tampered_owner_identity_before_factory(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    receipt = _authorization_receipt(authorization)
    payload = _payload(fixture, authorization, receipt)
    payload["authorization"] = {
        **payload["authorization"],
        "target_repository": "other/repository",
    }

    with pytest.raises(ProductionActivationError) as raised:
        build_activation_bundle(payload)

    assert raised.value.code == "AUTHORIZATION_IDENTITY_INVALID"


def test_preflight_mode_does_not_call_controller_mutation(tmp_path):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    receipt = _authorization_receipt(authorization)
    bundle = build_activation_bundle(_payload(fixture, authorization, receipt))
    factory = _Factory(fixture)

    result = run_production_activation(bundle, factory=factory, execute=False)

    assert result.outcome is None
    assert result.preflight.plan_digest == fixture.compiled_plan.digest
    assert fixture.mutation_calls() == ()
    assert fixture.transitions.history(fixture.repository) == ()


def test_cli_main_reads_pretty_json_and_writes_preflight_readback(tmp_path, monkeypatch):
    fixture = activation_fixture(tmp_path)
    authorization = _authorization(fixture, tmp_path)
    receipt = _authorization_receipt(authorization)
    input_path = tmp_path / "activation-input.json"
    output_path = tmp_path / "activation-preflight.json"
    input_path.write_text(
        json.dumps(_payload(fixture, authorization, receipt), indent=2),
        encoding="utf-8",
    )
    factory = _Factory(fixture)
    monkeypatch.setattr(activation_cli, "_load_factory", lambda _spec: factory)

    exit_code = activation_cli.main(
        [
            "--input",
            str(input_path),
            "--composition-factory",
            "unused:factory",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert output["schema"] == "gwo.v8.production-activation.v1"
    assert output["mode"] == "preflight"
    assert output["authorization"]["release_subject_digest"] == RELEASE_SUBJECT_DIGEST
    assert output["outcome"] is None
    assert fixture.mutation_calls() == ()
