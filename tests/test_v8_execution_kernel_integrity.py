from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass, replace
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

pytest_plugins = ("v8_production_test_support",)

from gwo_v8._canonical import digest_value
from gwo_v8.batch_integrator import (
    BatchDeliveryAction,
    BatchDeliveryObservation,
    BatchDeliveryProof,
    BatchDeliveryRequest,
    BatchTarget,
    HostedSuiteDefinition,
    LocalSuiteDefinition,
    MemberDeliveryObservation,
)
from gwo_v8.execution_kernel import (
    CampaignStatus,
    ExecutionKernelError,
    PlanInvalidationObservation,
    ResultIntegrityProof,
    StaleBindingObservation,
    StaleDiagnosisDisposition,
    StaleDiagnosisObservation,
    StaleReadbackState,
    WorkRunAction,
    WorkRunObservation,
)
from v8_production_test_support import (
    OneCandidateOnlyEffects,
    TamperedDeliveryEffects,
    make_accepted_candidate_receipt,
    make_candidate_receipt,
    make_result_integrity_proof,
)


def _dataclass_field_names(value_type: type[object]) -> tuple[str, ...]:
    return tuple(item.name for item in fields(value_type))


@dataclass
class CompletedWithoutProofEffects:
    candidate_only: bool
    observations: dict[str, WorkRunObservation] = field(default_factory=dict)

    def readback(self, action: WorkRunAction):
        return self.observations.get(action.stable_action_id)

    def execute(self, action: WorkRunAction):
        if self.candidate_only:
            candidate = make_candidate_receipt(action)
            accepted = make_accepted_candidate_receipt(action, candidate)
            observation = WorkRunObservation(
                phase="completed",
                stable_action_id=action.stable_action_id,
                runtime_binding_id=action.stable_action_id,
                receipt_digest=candidate.digest,
                candidate_receipt=candidate,
                accepted_candidate_receipt_digest=accepted.digest,
                candidate_diff_record_digest=accepted.diff_record_digest,
            )
        else:
            observation = WorkRunObservation(
                phase="completed",
                stable_action_id=action.stable_action_id,
                receipt_digest="a" * 64,
                result_digest="b" * 64,
                evidence_digests=("c" * 64,),
            )
        self.observations[action.stable_action_id] = observation
        return observation


@dataclass
class PlanInvalidationReadbackEffects:
    authority_subtree_digest: str
    observations: dict[str, WorkRunObservation] = field(default_factory=dict)
    batch_delivery_actions: list[str] = field(default_factory=list)

    def readback(self, action: WorkRunAction):
        return self.observations.get(action.stable_action_id)

    def execute(self, action: WorkRunAction):
        if action.kind == "batch_delivery":
            self.batch_delivery_actions.append(action.stable_action_id)
            observation = WorkRunObservation(
                phase="wait",
                stable_action_id=action.stable_action_id,
                runtime_binding_id=action.runtime_binding_id,
                receipt_digest="d" * 64,
            )
        else:
            candidate = make_candidate_receipt(action)
            accepted = make_accepted_candidate_receipt(action, candidate)
            invalidation = PlanInvalidationObservation(
                repository=action.repository,
                campaign_key=action.campaign_key,
                plan_revision_digest=action.plan_revision_digest,
                ticket_key=action.ticket_key,
                work_run_key=action.work_run_key,
                runtime_binding_id=action.stable_action_id,
                authority_subtree_digest=self.authority_subtree_digest,
                reporter_role="worker",
                report_digest="e" * 64,
                evidence_digest="f" * 64,
                dedup_identity="effect-invalidation:one",
                invalidated_obligation="the Candidate scope is no longer valid",
                required_effects=("workspace.write.v1",),
                workspace_identity="workspace:invalidation",
            )
            observation = WorkRunObservation(
                phase="accepted_awaiting_delivery",
                stable_action_id=action.stable_action_id,
                runtime_binding_id=action.stable_action_id,
                receipt_digest=candidate.digest,
                candidate_receipt=candidate,
                accepted_candidate_receipt_digest=accepted.digest,
                candidate_diff_record_digest=accepted.diff_record_digest,
                plan_invalidation=invalidation,
            )
        self.observations[action.stable_action_id] = observation
        return observation


@dataclass
class BatchRequestTamperEffects(OneCandidateOnlyEffects):
    emit_tampered_batch: bool = False

    def execute(self, action: WorkRunAction):
        if action.kind != "batch_delivery" or not self.emit_tampered_batch:
            return super().execute(action)
        candidate = make_candidate_receipt(action)
        accepted = make_accepted_candidate_receipt(action, candidate)
        proof = make_result_integrity_proof(
            action,
            accepted,
            target_contains_batch_sha=True,
        )
        proof = replace(
            proof,
            batch_delivery_request_digest="f" * 64,
        )
        proof = replace(proof, result_digest=proof.expected_result_digest())
        observation = WorkRunObservation(
            phase="completed",
            stable_action_id=action.stable_action_id,
            runtime_binding_id=action.runtime_binding_id,
            receipt_digest="9" * 64,
            candidate_receipt=candidate,
            accepted_candidate_receipt_digest=accepted.digest,
            candidate_diff_record_digest=accepted.diff_record_digest,
            delivery_receipt_digest="1" * 64,
            result_digest=proof.result_digest,
            result_integrity=proof,
        )
        self.observations[action.stable_action_id] = observation
        return observation


def test_observation_serializers_preserve_merged_dataclass_fields():
    for value_type in (
        WorkRunObservation,
        StaleBindingObservation,
        StaleDiagnosisObservation,
    ):
        assert is_dataclass(value_type)
        params = getattr(value_type, "__dataclass_params__", None)
        assert params is not None
        assert params.frozen is True

    assert "__post_init__" in WorkRunObservation.__dict__
    assert "running" in WorkRunObservation.__dict__

    assert _dataclass_field_names(WorkRunObservation) == (
        "phase",
        "stable_action_id",
        "receipt_digest",
        "reason",
        "next_check_at",
        "binding_established",
        "candidate_identity",
        "result_digest",
        "evidence_digests",
        "candidate_receipt",
        "runtime_binding_id",
        "accepted_candidate_receipt_digest",
        "candidate_diff_record_digest",
        "delivery_receipt_digest",
        "result_integrity",
        "plan_invalidation",
    )
    assert _dataclass_field_names(StaleBindingObservation) == (
        "stable_action_id",
        "runtime_binding_id",
        "state",
        "runtime_readback_digest",
        "process_readback_digest",
        "workspace_readback_digest",
        "campaign_readback_digest",
        "receipt_digest",
    )
    assert _dataclass_field_names(StaleDiagnosisObservation) == (
        "stable_action_id",
        "runtime_binding_id",
        "disposition",
        "receipt_digest",
    )


def test_observation_serializers_round_trip_merged_union_members():
    work_run = WorkRunObservation(
        phase="running",
        stable_action_id="action:running",
        receipt_digest="a" * 64,
        runtime_binding_id="binding:test",
    )
    stale_binding = StaleBindingObservation(
        stable_action_id="action:stale-readback",
        runtime_binding_id="binding:test",
        state=StaleReadbackState.IDLE,
        runtime_readback_digest="b" * 64,
        process_readback_digest="c" * 64,
        workspace_readback_digest="d" * 64,
        campaign_readback_digest="e" * 64,
        receipt_digest="f" * 64,
    )
    stale_diagnosis = StaleDiagnosisObservation(
        stable_action_id="action:stale-diagnosis",
        runtime_binding_id="binding:test",
        disposition=StaleDiagnosisDisposition.CONTINUE,
        receipt_digest="1" * 64,
    )

    assert WorkRunObservation.from_canonical(work_run.canonical()) == work_run
    assert StaleBindingObservation.from_canonical(
        stale_binding.canonical()
    ) == stale_binding
    assert StaleDiagnosisObservation.from_canonical(
        stale_diagnosis.canonical()
    ) == stale_diagnosis


def test_completed_observation_without_integrity_proof_is_rejected():
    with pytest.raises(ExecutionKernelError) as raised:
        WorkRunObservation(
            phase="completed",
            stable_action_id="action:completed",
            receipt_digest="a" * 64,
            result_digest="b" * 64,
        )
    assert raised.value.code == "RESULT_INTEGRITY_REQUIRED"


@pytest.mark.parametrize("candidate_only", (False, True))
def test_completed_non_proof_observation_cannot_publicly_complete(
    tmp_path,
    handle,
    active_plan,
    make_kernel,
    candidate_only,
):
    kernel = make_kernel(
        tmp_path / "kernel.sqlite3",
        active_plan,
        effects=CompletedWithoutProofEffects(candidate_only),
    )

    with pytest.raises(ExecutionKernelError) as raised:
        kernel.advance(handle)

    assert raised.value.code == "RESULT_INTEGRITY_REQUIRED"
    assert kernel.inspect(handle).status is not CampaignStatus.COMPLETE
    assert kernel._load(handle)["accepted_results"] == []


def test_plan_invalidation_readback_quiesces_before_candidate_or_batch(
    tmp_path,
    handle,
    active_plan,
    make_kernel,
):
    from gwo_v8._canonical import load_canonical_json

    plan = load_canonical_json(active_plan.plan_spec_bytes)
    authority_digest = plan["work"][0]["authority"]["worker"]["subtree_digest"]
    effects = PlanInvalidationReadbackEffects(authority_digest)
    kernel = make_kernel(tmp_path / "kernel.sqlite3", active_plan, effects=effects)

    outcome = kernel.advance(handle)

    summary = kernel.inspect(handle).work_runs[0]
    state = kernel._load(handle)
    run = state["runs"][summary.ticket_key]
    assert outcome.status is CampaignStatus.DECISION
    assert summary.phase == "quiescent"
    assert summary.claim_state == "released"
    assert summary.plan_invalidation is not None
    assert run["candidate_receipt"] is None
    assert run["accepted_candidate_receipt_digest"] is None
    assert state["accepted_results"] == []
    assert effects.batch_delivery_actions == []


def test_accepted_candidate_receipt_alone_cannot_create_a_code_result(
    tmp_path,
    handle,
    active_plan,
    make_kernel,
):
    effects = OneCandidateOnlyEffects()
    kernel = make_kernel(tmp_path / "kernel.sqlite3", active_plan, effects=effects)
    kernel.advance(handle)
    diagnostics = kernel.inspect(handle)
    run = diagnostics.work_runs[0]
    assert run.phase == "accepted_awaiting_delivery"
    assert run.result_digest is None
    assert not any(
        item["ticket_key"] == run.ticket_key
        for item in kernel._load(handle)["accepted_results"]
    )


def test_kernel_batch_ingestion_binds_parent_request_digest(
    tmp_path,
    handle,
    active_plan,
    make_kernel,
):
    effects = BatchRequestTamperEffects(emit_tampered_batch=True)
    kernel = make_kernel(tmp_path / "kernel.sqlite3", active_plan, effects=effects)

    with pytest.raises(ExecutionKernelError) as raised:
        kernel.advance(handle)

    assert raised.value.code == "RESULT_INTEGRITY_INVALID"
    run = next(iter(kernel._load(handle)["runs"].values()))
    assert run["batch_delivery_request_digest"] == "0" * 64
    assert run["batch_delivery_request_digest"] != "f" * 64


def test_nested_result_integrity_unknown_key_is_typed_rejection():
    action = WorkRunAction(
        stable_action_id="action:nested-proof",
        repository="owner/repository",
        campaign_key="campaign:nested-proof",
        plan_revision_digest="a" * 64,
        ticket_key="issue:nested-proof",
        kind="batch_delivery",
        semantic_action_id="semantic:nested-proof",
        work_run_key="work-run:nested-proof",
        work_subject_digest="b" * 64,
        runtime_binding_id="binding:nested-proof",
        wake_ref="candidate:accepted",
        accepted_candidate_receipt_digest=None,
        batch_delivery_request_digest="0" * 64,
    )
    candidate = make_candidate_receipt(action)
    accepted = make_accepted_candidate_receipt(action, candidate)
    proof = make_result_integrity_proof(
        action,
        accepted,
        target_contains_batch_sha=True,
    )
    observation = WorkRunObservation(
        phase="completed",
        stable_action_id=action.stable_action_id,
        receipt_digest="9" * 64,
        candidate_receipt=candidate,
        accepted_candidate_receipt_digest=accepted.digest,
        candidate_diff_record_digest=accepted.diff_record_digest,
        delivery_receipt_digest="1" * 64,
        result_digest=proof.result_digest,
        result_integrity=proof,
    )
    encoded = observation.canonical()
    encoded["result_integrity"]["unexpected"] = True

    with pytest.raises(ExecutionKernelError) as raised:
        WorkRunObservation.from_canonical(encoded)

    assert raised.value.code == "WORK_RUN_OBSERVATION_INVALID"


def test_completed_result_requires_exact_batch_and_target_readback(
    tmp_path,
    handle,
    active_plan,
    make_kernel,
):
    effects = TamperedDeliveryEffects()
    kernel = make_kernel(tmp_path / "kernel.sqlite3", active_plan, effects=effects)
    with pytest.raises(ExecutionKernelError) as raised:
        kernel.advance(handle)
    assert raised.value.code == "RESULT_INTEGRITY_INVALID"


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    (
        ("batch_delivery_proof_digest", "f" * 64),
        ("delivery_stable_action_id", "action:changed"),
        ("delivery_request_digest", "f" * 64),
        ("batch_id", "batch:changed"),
        ("batch_sha", "f" * 40),
        ("delivery_member_ticket_keys", ("issue:changed",)),
        ("local_check_receipt_digest", "f" * 64),
        ("publication_receipt_digest", "f" * 64),
        ("pull_request_number", 99),
        ("pull_request_head_sha", "f" * 40),
        ("hosted_result_receipt_digest", "f" * 64),
        ("integration_lease_digest", "f" * 64),
        ("target_branch", "release"),
        ("target_head_sha", "f" * 40),
        ("target_readback_digest", "f" * 64),
        ("target_contains_batch_sha", False),
        ("pull_request_merge_target_sha", "f" * 40),
        ("merge_method", "squash"),
    ),
)
def test_any_exact_delivery_proof_field_tamper_fails_closed(
    field_name,
    tampered_value,
):
    action = WorkRunAction(
        stable_action_id="action:integrity",
        repository="owner/repository",
        campaign_key="campaign:integrity",
        plan_revision_digest="a" * 64,
        ticket_key="issue:integrity",
        kind="batch_delivery",
        semantic_action_id="semantic:integrity",
        work_run_key="work-run:integrity",
        work_subject_digest="b" * 64,
        runtime_binding_id="binding:integrity",
        wake_ref="candidate:accepted",
        accepted_candidate_receipt_digest=None,
        batch_delivery_request_digest="0" * 64,
    )
    candidate = make_candidate_receipt(action)
    accepted = make_accepted_candidate_receipt(action, candidate)
    proof = make_result_integrity_proof(
        action,
        accepted,
        target_contains_batch_sha=True,
    )
    proof.validate_for(action, "main")

    with pytest.raises(ExecutionKernelError) as raised:
        replace(proof, **{field_name: tampered_value}).validate_for(action, "main")

    assert raised.value.code == "RESULT_INTEGRITY_INVALID"


def test_fallback_result_selects_exact_singleton_proof_and_keeps_parent_receipt():
    work_action = WorkRunAction(
        stable_action_id="work-action:1",
        repository="owner/repository",
        campaign_key="campaign:fallback",
        plan_revision_digest="a" * 64,
        ticket_key="issue:1",
        kind="batch_delivery",
        semantic_action_id="semantic:1",
        work_run_key="work-run:1",
        work_subject_digest="b" * 64,
        runtime_binding_id="binding:1",
        wake_ref="candidate:accepted",
        accepted_candidate_receipt_digest=None,
    )
    candidate = make_candidate_receipt(work_action)
    first = make_accepted_candidate_receipt(work_action, candidate)
    second = replace(
        first,
        ticket_key="issue:2",
        work_run_key="work-run:2",
        integration_node_key="integration:issue:2",
        accepted_sequence=2,
        candidate_sha="c" * 40,
        candidate_tree_oid="d" * 40,
        candidate_receipt_digest="e" * 64,
        diff_record_digest="f" * 64,
        evidence_digests=("2" * 64,),
    )
    request = BatchDeliveryRequest(
        stable_action_id="parent-delivery:fallback",
        repository=work_action.repository,
        campaign_key=work_action.campaign_key,
        plan_revision_digest=work_action.plan_revision_digest,
        target=BatchTarget(
            repository=work_action.repository,
            target_branch="main",
            target_head_sha="9" * 40,
            target_tree_oid="8" * 40,
            target_facts_digest="7" * 64,
        ),
        accepted_candidates=(first, second),
        local_suite=LocalSuiteDefinition(
            suite_id="local:fallback",
            definition_digest="6" * 64,
            command=("py", "-3.13", "-m", "pytest", "-q"),
        ),
        hosted_suites=(
            HostedSuiteDefinition(
                suite_id="hosted:fallback",
                hosted_name="GWO CI",
                definition_digest="5" * 64,
            ),
        ),
        writer_generation="v6.1",
        activation_id="activation:fallback",
    )
    parent_action = BatchDeliveryAction(
        stable_action_id=request.stable_action_id,
        request_digest=request.request_digest,
        batch_id="4" * 64,
        batch_sha="4" * 40,
        member_ticket_keys=(first.ticket_key, second.ticket_key),
    )
    first_delivery = BatchDeliveryProof.create(
        delivery_stable_action_id="singleton-delivery:1",
        delivery_request_digest="1" * 64,
        batch_id="1" * 64,
        batch_sha="1" * 40,
        member_ticket_keys=(first.ticket_key,),
        local_check_receipt_digest="a" * 64,
        publication_receipt_digest="b" * 64,
        pull_request_number=31,
        pull_request_head_sha="1" * 40,
        hosted_result_receipt_digest="c" * 64,
        integration_lease_digest="d" * 64,
        target_branch="main",
        target_head_sha="3" * 40,
        target_readback_digest="e" * 64,
        target_contains_batch_sha=True,
        pull_request_merge_target_sha="3" * 40,
        merge_method="merge",
    )
    second_delivery = BatchDeliveryProof.create(
        delivery_stable_action_id="singleton-delivery:2",
        delivery_request_digest="2" * 64,
        batch_id="2" * 64,
        batch_sha="2" * 40,
        member_ticket_keys=(second.ticket_key,),
        local_check_receipt_digest="b" * 64,
        publication_receipt_digest="c" * 64,
        pull_request_number=32,
        pull_request_head_sha="2" * 40,
        hosted_result_receipt_digest="d" * 64,
        integration_lease_digest="e" * 64,
        target_branch="main",
        target_head_sha="4" * 40,
        target_readback_digest="f" * 64,
        target_contains_batch_sha=True,
        pull_request_merge_target_sha="4" * 40,
        merge_method="merge",
    )
    members = (
        MemberDeliveryObservation(
            ticket_key=first.ticket_key,
            work_run_key=first.work_run_key,
            candidate_sha=first.candidate_sha,
            status="integrated",
            evidence_digests=first.evidence_digests,
        ),
        MemberDeliveryObservation(
            ticket_key=second.ticket_key,
            work_run_key=second.work_run_key,
            candidate_sha=second.candidate_sha,
            status="integrated",
            evidence_digests=second.evidence_digests,
        ),
    )
    observation_body = {
        "stable_action_id": parent_action.stable_action_id,
        "batch_id": parent_action.batch_id,
        "batch_sha": parent_action.batch_sha,
        "phase": "complete",
        "reason": "SingletonFallbackComplete",
        "retry_count": 0,
        "fallback_generation": 1,
        "members": [
            {
                "ticket_key": member.ticket_key,
                "work_run_key": member.work_run_key,
                "candidate_sha": member.candidate_sha,
                "status": member.status,
                "evidence_digests": list(member.evidence_digests),
                "resume_reason": member.resume_reason,
            }
            for member in members
        ],
        "delivery_proofs": [
            first_delivery.canonical(),
            second_delivery.canonical(),
        ],
    }
    observation = BatchDeliveryObservation(
        stable_action_id=parent_action.stable_action_id,
        batch_id=parent_action.batch_id,
        batch_sha=parent_action.batch_sha,
        phase="complete",
        reason="SingletonFallbackComplete",
        receipt_digest=digest_value(
            {"kind": "batch-observation.v1", **observation_body}
        ),
        retry_count=0,
        fallback_generation=1,
        members=members,
        delivery_proofs=(first_delivery, second_delivery),
    )

    first_result = ResultIntegrityProof.from_batch_observation(
        parent_action, request, observation, first
    )
    second_result = ResultIntegrityProof.from_batch_observation(
        parent_action, request, observation, second
    )

    assert first_result.batch_delivery_receipt_digest == observation.receipt_digest
    assert second_result.batch_delivery_receipt_digest == observation.receipt_digest
    assert first_result.delivery_proof_body() == first_delivery.body()
    assert second_result.delivery_proof_body() == second_delivery.body()
    assert first_result.batch_sha != parent_action.batch_sha
    assert second_result.batch_sha != parent_action.batch_sha


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
