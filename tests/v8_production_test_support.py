from __future__ import annotations

from dataclasses import dataclass, field, replace
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
from typing import Sequence
import sqlite3

import pytest

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
from gwo_v8.campaign_watchdog import (
    CampaignWatchdog,
    WatchdogCampaignSnapshot,
    WatchdogWake,
    WatchdogWakePage,
)
from gwo_v8.candidate_gate import (
    AcceptedCandidateReceipt,
    AssuranceMode,
    AssuranceRequirement,
    CandidateDiffRecordV1,
    CandidateGateParent,
    CandidateGateResult,
    CandidateGateStatus,
    CandidateIdentity,
    CandidateReceipt,
    InteractionClassification,
    InteractionKey,
    PlanInvalidationEvidence,
    RepairPacket,
    ReviewSubject,
)
from gwo_v8.execution_kernel import (
    CampaignOutcome,
    Diagnostics,
    ExecutionKernel,
    ExecutionKernelConfiguration,
    ResultIntegrityProof,
    StaleBindingObservation,
    StaleDiagnosisObservation,
    WorkRunAction,
    WorkRunEffects,
    WorkRunObservation,
    WorkRunSummary,
)
from gwo_v8.plan_control import (
    ActivePlanReadback,
    CampaignHandle,
    InMemoryPlanRepository,
    frozen_ticket_contract_digest,
)
from gwo_v8.plan_control_host import ProductionPlanControlStartHost, install_plan_control_start
from gwo_v8.campaign_watchdog import (
    CampaignWatchdog,
    WatchdogCampaignSnapshot,
    WatchdogWake,
    WatchdogWakePage,
)
from gwo_v8.runtime_gateway import (
    CapabilityPolicy,
    CapabilityPolicyProof,
    PlanInvalidationReport,
    PlanInvalidationReceipt,
    ProfileMapping,
    PlanningReceipt,
    RuntimeConfiguration,
    RuntimeGateway,
    PlanningPreflightReceipt,
    RuntimeProgressReceipt,
    RuntimeRepositoryContext,
    WorkRunPurpose,
    WorkRunSubject,
)
from gwo_v8.runtime_profile import RuntimeProfile
from gwo_v8.production_host import (
    PlanningContinuation,
    ProductionCompositionError,
    ProductionGwoHost,
    ProductionHostConfiguration,
)
from gwo_v8.production_effects import (
    BatchIntegratorPort,
    CandidateGateParentSource,
    CandidateGatePort,
    CandidateReferenceReader,
    ProductionWorkRunEffects,
    RuntimeGatewayFactory,
    RuntimeStaleReadbackPort,
    WorkRunSubjectSource,
)
from v8_successor_test_support import (
    _StaticPlanReader,
    _minimal_active_campaign,
    active_plan_spec,
    three_ticket_source_snapshot,
)


def make_candidate_receipt(action: WorkRunAction) -> CandidateReceipt:
    diff_record = CandidateDiffRecordV1(
        schema_version="CandidateDiffRecordV1",
        repository_object_format="sha1",
        base_commit_oid="2" * 40,
        base_tree_oid="3" * 40,
        candidate_commit_oid="4" * 40,
        candidate_tree_oid="5" * 40,
        entries=(),
    )
    return CandidateReceipt(
        parent_digest="1" * 64,
        repository=action.repository,
        campaign_key=action.campaign_key,
        campaign_handle=action.campaign_key,
        plan_revision_digest=action.plan_revision_digest,
        work_run_key=action.work_run_key or f"work-run:{action.ticket_key}",
        ticket_key=action.ticket_key,
        reported_reference="refs/heads/candidate",
        base_commit_oid="2" * 40,
        base_tree_oid="3" * 40,
        candidate_commit_oid="4" * 40,
        candidate_tree_oid="5" * 40,
        diff_schema_version="CandidateDiffRecordV1",
        diff_record_digest=diff_record.digest,
        authority_subtree_digest="7" * 64,
        runtime_subject_digest=action.work_subject_digest or "8" * 64,
    )


def _candidate_gate_identity(
    candidate: CandidateReceipt,
) -> tuple[CandidateDiffRecordV1, AssuranceRequirement, ReviewSubject]:
    record = CandidateDiffRecordV1(
        schema_version="CandidateDiffRecordV1",
        repository_object_format="sha1",
        base_commit_oid=candidate.base_commit_oid,
        base_tree_oid=candidate.base_tree_oid,
        candidate_commit_oid=candidate.candidate_commit_oid,
        candidate_tree_oid=candidate.candidate_tree_oid,
        entries=(),
        record_digest=candidate.diff_record_digest,
    )
    requirement = AssuranceRequirement(
        policy_id="test-policy",
        policy_version="v1",
        mode=AssuranceMode.STANDARD,
        required_check_ids=("candidate-check",),
        standards=(),
    )
    subject = ReviewSubject(
        parent_digest=candidate.parent_digest,
        candidate_receipt_digest=candidate.digest,
        runtime_subject_digest=candidate.runtime_subject_digest,
        candidate_digest="a" * 64,
        candidate_audit_digest="b" * 64,
        ticket_contract_digest="c" * 64,
        policy_witness_digest="9" * 64,
        base_commit_oid=candidate.base_commit_oid,
        base_tree_oid=candidate.base_tree_oid,
        candidate_commit_oid=candidate.candidate_commit_oid,
        candidate_tree_oid=candidate.candidate_tree_oid,
        diff_schema_version=candidate.diff_schema_version,
        diff_record_digest=record.digest,
        standards=(),
        check_evidence_digests=(),
        assurance_requirement_digest=requirement.digest,
    )
    return record, requirement, subject


def make_accepted_candidate_receipt(
    action: WorkRunAction,
    candidate: CandidateReceipt | None = None,
) -> AcceptedCandidateReceipt:
    candidate = candidate or make_candidate_receipt(action)
    record, requirement, subject = _candidate_gate_identity(candidate)
    return AcceptedCandidateReceipt(
        repository=action.repository,
        campaign_key=action.campaign_key,
        plan_revision_digest=action.plan_revision_digest,
        target_branch="main",
        ticket_key=action.ticket_key,
        work_run_key=candidate.work_run_key,
        integration_node_key=f"integration:{action.ticket_key}",
        accepted_sequence=1,
        base_sha=candidate.base_commit_oid,
        base_tree_oid=candidate.base_tree_oid,
        candidate_sha=candidate.candidate_commit_oid,
        candidate_tree_oid=candidate.candidate_tree_oid,
        candidate_receipt_digest=candidate.digest,
        diff_schema_version=candidate.diff_schema_version,
        diff_record_digest=record.digest,
        authority_subtree_digest=candidate.authority_subtree_digest,
        policy_witness_digest="9" * 64,
        review_subject_digest=subject.digest,
        assurance="standard",
        assurance_requirement_digest=requirement.digest,
        check_environment_digest="c" * 64,
        delivery_identity_digest="d" * 64,
        interaction_keys=(
            InteractionKey(
                "candidate-path",
                "src/main.py",
                InteractionClassification.ORDINARY,
            ),
        ),
        protected_surfaces=(),
        gitlink_change=False,
        evidence_digests=("e" * 64,),
        review_finding_ledger_digest="f" * 64,
    )


def accepted_candidate_result(action: WorkRunAction) -> CandidateGateResult:
    candidate = make_candidate_receipt(action)
    record, requirement, subject = _candidate_gate_identity(candidate)
    return CandidateGateResult(
        status=CandidateGateStatus.REVIEW_ACCEPTED,
        evidence=(),
        candidate_receipt=candidate,
        candidate_diff_record=record,
        assurance_requirement=requirement,
        review_subject=subject,
        accepted_candidate_receipt=make_accepted_candidate_receipt(action, candidate),
    )


@dataclass
class OneCandidateOnlyEffects:
    observations: dict[
        str,
        WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation,
    ] = field(default_factory=dict)

    def readback(
        self,
        action: WorkRunAction,
    ) -> WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation | None:
        return self.observations.get(action.stable_action_id)

    def bind_batch_delivery_request_digest(self, action: WorkRunAction) -> str:
        if action.kind != "batch_delivery":
            raise AssertionError(
                "parent Batch request binding is only valid for Batch delivery"
            )
        return "0" * 64

    def execute(
        self,
        action: WorkRunAction,
    ) -> WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation:
        if action.kind not in {"semantic_execution", "batch_delivery"}:
            raise AssertionError(f"OneCandidateOnlyEffects received {action.kind}")
        result = accepted_candidate_result(action)
        observation = WorkRunObservation(
            phase="accepted_awaiting_delivery",
            stable_action_id=action.stable_action_id,
            runtime_binding_id="binding:test",
            receipt_digest=result.candidate_receipt.digest,
            candidate_receipt=result.candidate_receipt,
            accepted_candidate_receipt_digest=result.accepted_candidate_receipt.digest,
            candidate_diff_record_digest=result.candidate_receipt.diff_record_digest,
            result_integrity=None,
            result_digest=None,
        )
        self.observations[action.stable_action_id] = observation
        return observation


def make_result_integrity_proof(
    action: WorkRunAction,
    accepted: AcceptedCandidateReceipt,
    *,
    target_contains_batch_sha: bool,
) -> ResultIntegrityProof:
    request_digest = action.batch_delivery_request_digest
    if type(request_digest) is not str:
        raise AssertionError(
            "test Batch delivery proof requires a bound parent request digest"
        )
    delivery = BatchDeliveryProof.create(
        delivery_stable_action_id=action.stable_action_id,
        delivery_request_digest=request_digest,
        batch_id="2" * 64,
        batch_sha="2" * 40,
        member_ticket_keys=(action.ticket_key,),
        local_check_receipt_digest="3" * 64,
        publication_receipt_digest="4" * 64,
        pull_request_number=17,
        pull_request_head_sha="2" * 40,
        hosted_result_receipt_digest="5" * 64,
        integration_lease_digest="6" * 64,
        target_branch="main",
        target_head_sha="7" * 40,
        target_readback_digest="8" * 64,
        target_contains_batch_sha=target_contains_batch_sha,
        pull_request_merge_target_sha="7" * 40,
        merge_method="merge",
    )
    proof = ResultIntegrityProof(
        accepted_candidate_receipt_digest=accepted.digest,
        candidate_commit_oid=accepted.candidate_sha,
        candidate_tree_oid=accepted.candidate_tree_oid,
        candidate_diff_record_digest=accepted.diff_record_digest,
        batch_delivery_receipt_digest="1" * 64,
        batch_delivery_stable_action_id=action.stable_action_id,
        batch_delivery_request_digest=request_digest,
        batch_delivery_batch_id=delivery.batch_id,
        batch_delivery_batch_sha=delivery.batch_sha,
        batch_delivery_proof_digest=delivery.proof_digest,
        delivery_stable_action_id=delivery.delivery_stable_action_id,
        delivery_request_digest=delivery.delivery_request_digest,
        batch_id=delivery.batch_id,
        batch_sha=delivery.batch_sha,
        delivery_member_ticket_keys=delivery.member_ticket_keys,
        local_check_receipt_digest=delivery.local_check_receipt_digest,
        publication_receipt_digest=delivery.publication_receipt_digest,
        pull_request_number=delivery.pull_request_number,
        pull_request_head_sha=delivery.pull_request_head_sha,
        hosted_result_receipt_digest=delivery.hosted_result_receipt_digest,
        integration_lease_digest=delivery.integration_lease_digest,
        target_branch=delivery.target_branch,
        target_head_sha=delivery.target_head_sha,
        target_readback_digest=delivery.target_readback_digest,
        target_contains_batch_sha=delivery.target_contains_batch_sha,
        pull_request_merge_target_sha=delivery.pull_request_merge_target_sha,
        merge_method=delivery.merge_method,
        result_digest="",
        evidence_digests=accepted.evidence_digests,
    )
    return replace(proof, result_digest=proof.expected_result_digest())


def make_completed_observation(
    action: WorkRunAction,
    *,
    target_contains_batch_sha: bool = True,
    evidence_digests: tuple[str, ...] | None = None,
) -> WorkRunObservation:
    candidate = make_candidate_receipt(action)
    accepted = make_accepted_candidate_receipt(action, candidate)
    proof = make_result_integrity_proof(
        action,
        accepted,
        target_contains_batch_sha=target_contains_batch_sha,
    )
    if evidence_digests is not None:
        proof = replace(proof, evidence_digests=tuple(evidence_digests))
        proof = replace(proof, result_digest=proof.expected_result_digest())
    return WorkRunObservation(
        phase="completed",
        stable_action_id=action.stable_action_id,
        runtime_binding_id=action.runtime_binding_id,
        receipt_digest="9" * 64,
        candidate_receipt=candidate,
        accepted_candidate_receipt_digest=accepted.digest,
        candidate_diff_record_digest=accepted.diff_record_digest,
        delivery_receipt_digest=proof.batch_delivery_receipt_digest,
        result_digest=proof.result_digest,
        evidence_digests=proof.evidence_digests,
        result_integrity=proof,
    )


@dataclass
class TamperedDeliveryEffects(OneCandidateOnlyEffects):
    def execute(
        self,
        action: WorkRunAction,
    ) -> WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation:
        if action.kind != "batch_delivery":
            return super().execute(action)
        candidate = make_candidate_receipt(action)
        accepted = make_accepted_candidate_receipt(action, candidate)
        proof = make_result_integrity_proof(
            action,
            accepted,
            target_contains_batch_sha=False,
        )
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


@dataclass
class NoopRunningEffects:
    observations: dict[str, WorkRunObservation] = field(default_factory=dict)

    def readback(
        self,
        action: WorkRunAction,
    ) -> WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation | None:
        return self.observations.get(action.stable_action_id)

    def execute(
        self,
        action: WorkRunAction,
    ) -> WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation:
        if action.kind not in {"semantic_execution", "semantic_resume"}:
            raise AssertionError(f"unexpected kernel test action: {action.kind}")
        observation = WorkRunObservation(
            phase="running",
            stable_action_id=action.stable_action_id,
            runtime_binding_id="binding:test",
            receipt_digest=digest_value(
                {"kind": "test-running", "action": action.stable_action_id}
            ),
        )
        self.observations[action.stable_action_id] = observation
        return observation


@pytest.fixture
def handle() -> CampaignHandle:
    _active, handle = _minimal_active_campaign(("issue:109",))
    return handle


@pytest.fixture
def active_plan() -> ActivePlanReadback:
    from gwo_v8._canonical import canonical_bytes, digest_bytes, load_canonical_json

    active, handle = _minimal_active_campaign(("issue:109",))
    plan = load_canonical_json(active.plan_spec_bytes)
    plan["campaign"]["key"] = handle.campaign_key
    payload = canonical_bytes(plan)
    revision = digest_bytes(payload)
    return replace(
        active,
        current_revision_digest=revision,
        plan_spec_bytes=payload,
        activation_receipt=replace(
            active.activation_receipt,
            revision_digest=revision,
        ),
        claim_proofs=tuple(
            replace(proof, plan_revision_digest=revision)
            for proof in active.claim_proofs
        ),
    )


@pytest.fixture
def make_kernel():
    def build(
        store_path: Path,
        active: ActivePlanReadback,
        *,
        effects: WorkRunEffects | None = None,
    ):
        from gwo_v8.execution_kernel import ExecutionKernel

        return ExecutionKernel(
            store_path=Path(store_path),
            plan_control=_StaticPlanReader(active),
            effects=effects or NoopRunningEffects(),
        )

    return build


@dataclass
class RecordingRuntimeGateway:
    receipt: RuntimeProgressReceipt | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)
    auto_complete: bool = False
    artifacts: object | None = None

    def planning_preflight(self, subject) -> PlanningPreflightReceipt:
        return PlanningPreflightReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            receipt_digest=digest_value(
                {
                    "kind": "recording-runtime-preflight.v1",
                    "stable_action_id": subject.stable_action_id,
                }
            ),
        )

    def progress(
        self,
        subject,
        preflight: PlanningPreflightReceipt | None = None,
        *,
        wake_cursor: str | None = None,
    ) -> RuntimeProgressReceipt:
        if preflight is not None:
            if self.artifacts is None:
                raise AssertionError("RecordingRuntimeGateway.artifacts must be configured")
            payload = {
                "admitted_work": ["issue:109"],
                "dependency_additions": [],
                "exclusive_resources": {"issue:109": []},
                "capability_requirements": {
                    "issue:109": ["git", "local_check"]
                },
                "decision_requirements": [],
            }
            output = self.artifacts.put_canonical(
                {
                    "schema_version": "gwo.runtime.output.v1",
                    "subject_digest": subject.digest,
                    "stable_action_id": subject.stable_action_id,
                    "authority_digest": subject.authority_digest,
                    "payload": payload,
                }
            )
            return PlanningReceipt(
                subject_digest=subject.digest,
                stable_action_id=subject.stable_action_id,
                status="completed",
                receipt_digest=digest_value(
                    {
                        "kind": "recording-runtime-planning-receipt.v1",
                        "stable_action_id": subject.stable_action_id,
                    }
                ),
                output_artifact_digest=output.digest,
                planning_output_artifact_digest=output.digest,
            )
        self.calls.append(("progress", subject.stable_action_id))
        if self.receipt is None:
            if not self.auto_complete:
                raise AssertionError("RecordingRuntimeGateway.receipt must be configured")
            receipt = RuntimeProgressReceipt(
                subject_digest=subject.digest,
                stable_action_id=subject.stable_action_id,
                status="completed",
                receipt_digest=digest_value(
                    {
                        "kind": "recording-runtime-receipt.v1",
                        "stable_action_id": subject.stable_action_id,
                    }
                ),
                output_artifact_digest=digest_value(
                    {
                        "kind": "recording-runtime-output.v1",
                        "stable_action_id": subject.stable_action_id,
                    }
                ),
            )
            # The composition fixture's clock is intentionally independent of
            # the host clock.  Give the accepted-candidate recovery path a
            # deterministic due time that the E2E watchdog invocation can
            # consume after a simulated lost callback.
            object.__setattr__(receipt, "next_check_at", "2026-08-03T10:00:00+00:00")
            return receipt
        return self.receipt


@dataclass
class RecordingRuntimeGatewayFactory:
    store_path: Path
    provider_command: str
    repository_root: Path
    gateway: RecordingRuntimeGateway = field(default_factory=RecordingRuntimeGateway)

    def __post_init__(self) -> None:
        self.runtime_configuration = make_recording_runtime_configuration(
            self.provider_command
        )
        self.repository_contexts = make_recording_repository_contexts(
            self.repository_root
        )

    def for_campaign(self, handle: CampaignHandle) -> RecordingRuntimeGateway:
        return self.gateway

    def build(self, **_kwargs: object) -> RecordingRuntimeGateway:
        self.gateway.artifacts = _kwargs.get("artifacts")
        return self.gateway


@dataclass
class RecordingRuntimeStaleReadback:
    observation: StaleBindingObservation | StaleDiagnosisObservation | None = None
    calls: list[str] = field(default_factory=list)

    def read_stale(
        self,
        action: WorkRunAction,
    ) -> StaleBindingObservation | StaleDiagnosisObservation:
        self.calls.append(action.stable_action_id)
        if self.observation is None:
            raise AssertionError(
                "RecordingRuntimeStaleReadback.observation must be configured"
            )
        return self.observation


@dataclass
class RecordingSubjectSource:
    subject: WorkRunSubject | None = None
    calls: list[str] = field(default_factory=list)
    auto_generate: bool = False
    actions: dict[str, WorkRunAction] | None = None

    def for_action(self, action: WorkRunAction) -> WorkRunSubject:
        self.calls.append(action.stable_action_id)
        if self.actions is not None:
            self.actions[action.stable_action_id] = action
        if self.subject is None:
            if not self.auto_generate:
                raise AssertionError("RecordingSubjectSource.subject must be configured")
            return make_test_subject(action)
        if self.subject.stable_action_id == action.stable_action_id:
            return self.subject
        return replace(self.subject, stable_action_id=action.stable_action_id)


@dataclass
class RecordingCandidateReferenceReader:
    reference: str | None = None
    auto_generate: bool = False
    calls: list[str] = field(default_factory=list)

    def read(self, output_artifact_digest: str, *, subject: WorkRunSubject) -> str:
        self.calls.append(output_artifact_digest)
        if self.reference is None:
            if not self.auto_generate:
                raise AssertionError(
                    "RecordingCandidateReferenceReader.reference must be configured"
                )
            return "refs/heads/candidate"
        return self.reference


@dataclass
class RecordingCandidateParentSource:
    parent: CandidateGateParent | None = None
    auto_generate: bool = False
    actions: dict[str, WorkRunAction] | None = None

    def for_action(
        self,
        action: WorkRunAction,
        subject: WorkRunSubject,
    ) -> CandidateGateParent:
        if self.actions is not None:
            self.actions[action.stable_action_id] = action
        if self.parent is None:
            if not self.auto_generate:
                raise AssertionError(
                    "RecordingCandidateParentSource.parent must be configured"
                )
            return CandidateGateParent(
                runtime_subject=subject,
                ticket_contract_digest="c" * 64,
                policy_witness_digest="d" * 64,
                workspace_identity="workspace:production-composition",
            )
        if self.parent.runtime_subject == subject:
            return self.parent
        return replace(
            self.parent,
            runtime_subject=subject,
            parent_digest=None,
        )


@dataclass
class RecordingCandidateGate:
    result: CandidateGateResult | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)
    auto_accept: bool = False
    actions: dict[str, WorkRunAction] | None = None

    def _result(
        self,
        operation: str,
        stable_action_id: str,
        subject: WorkRunSubject | None = None,
    ) -> CandidateGateResult:
        self.calls.append((stable_action_id, operation))
        if self.result is None:
            if not self.auto_accept:
                raise AssertionError("RecordingCandidateGate.result must be configured")
            action = (self.actions or {}).get(stable_action_id) or WorkRunAction(
                stable_action_id=stable_action_id,
                repository=subject.repository if subject is not None else "owner/repository",
                campaign_key=subject.campaign_key if subject is not None else "campaign:successor-kernel",
                plan_revision_digest=subject.plan_revision_digest if subject is not None else "a" * 64,
                ticket_key=subject.ticket_key if subject is not None else "issue:109",
                kind="semantic_execution",
                semantic_action_id=f"semantic:{subject.ticket_key}" if subject is not None else "semantic:109",
                work_run_key=subject.work_run_key if subject is not None else "work-run:issue:109",
                work_subject_digest=subject.digest if subject is not None else "b" * 64,
                runtime_binding_id=None,
                wake_ref=None,
                accepted_candidate_receipt_digest=None,
            )
            return accepted_candidate_result(action)
        return self.result

    def gate_candidate(
        self,
        parent: CandidateGateParent,
        reported_reference: str,
    ) -> CandidateGateResult:
        return self._result(
            "gate_candidate",
            parent.runtime_subject.stable_action_id,
            parent.runtime_subject,
        )

    def verify_repair(
        self,
        parent: CandidateGateParent,
        packet: RepairPacket,
        candidate: CandidateIdentity,
    ) -> CandidateGateResult:
        return self._result(
            "verify_repair",
            parent.runtime_subject.stable_action_id,
            parent.runtime_subject,
        )

    def replay_plan_invalidation(
        self,
        parent: CandidateGateParent,
        evidence: PlanInvalidationEvidence,
        report: PlanInvalidationReport,
    ) -> CandidateGateResult:
        return self._result(
            "replay_plan_invalidation",
            parent.runtime_subject.stable_action_id,
            parent.runtime_subject,
        )


@dataclass
class RecordingBatchRequestSource:
    target_path: Path
    runtime_factory: RecordingRuntimeGatewayFactory
    request: BatchDeliveryRequest | None = None
    auto_generate: bool = False

    def for_action(
        self,
        action: WorkRunAction,
        subject: WorkRunSubject,
        accepted_candidates: tuple[AcceptedCandidateReceipt, ...],
    ) -> BatchDeliveryRequest:
        if self.request is None:
            if not self.auto_generate:
                raise AssertionError("RecordingBatchRequestSource.request must be configured")
            return make_batch_delivery_request(action)
        return self.request


@dataclass
class RecordingBatchIntegrator:
    store_path: Path
    target_path: Path
    action: BatchDeliveryAction | None = None
    observation: BatchDeliveryObservation | None = None
    prepare_calls: int = 0
    readback_calls: int = 0
    execute_calls: int = 0
    target_integration_calls: int = 0
    suppress_callbacks: bool = False

    def prepare(self, request: BatchDeliveryRequest) -> BatchDeliveryAction:
        self.prepare_calls += 1
        if self.action is None:
            raise AssertionError("RecordingBatchIntegrator.action must be configured")
        return self.action

    def readback(self, action: BatchDeliveryAction) -> BatchDeliveryObservation | None:
        self.readback_calls += 1
        if self.execute_calls == 0:
            return None
        return self.observation

    def execute(self, action: BatchDeliveryAction) -> BatchDeliveryObservation:
        self.execute_calls += 1
        self.target_integration_calls += 1
        if self.observation is None:
            raise AssertionError(
                "RecordingBatchIntegrator.observation must be configured"
            )
        return self.observation


def make_recording_runtime_configuration(
    provider_command: str,
) -> RuntimeConfiguration:
    profile = RuntimeProfile(
        name="recording",
        provider="recording",
        model="recording-model",
        thinking="standard",
        mode="test",
        features={"provider_command": provider_command},
    )
    profile_digest = digest_value(profile.canonical())
    mapping = ProfileMapping(profile_digest)
    return RuntimeConfiguration(
        profiles={profile_digest: profile},
        host_mappings={"worker": mapping, "coordinator": mapping},
        repository_mappings={
            "owner/isolated-composition": {
                "worker": mapping,
                "coordinator": mapping,
            }
        },
        campaign_assertions={},
    )


def make_recording_repository_contexts(
    root: Path,
) -> dict[str, RuntimeRepositoryContext]:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return {
        "owner/isolated-composition": RuntimeRepositoryContext(
            root,
            "refs/heads/main",
        )
    }


def make_test_subject(action: WorkRunAction) -> WorkRunSubject:
    return WorkRunSubject(
        repository=action.repository,
        campaign_key=action.campaign_key,
        campaign_handle=f"{action.repository}:{action.campaign_key}",
        plan_revision_digest=action.plan_revision_digest,
        work_run_key=action.work_run_key or f"work-run:{action.ticket_key}",
        ticket_key=action.ticket_key,
        purpose=WorkRunPurpose.implementation(),
        prompt_artifact_digest="1" * 64,
        authority_subtree_digest="2" * 64,
        stable_action_id=action.stable_action_id,
    )


def make_batch_delivery_request(action: WorkRunAction) -> BatchDeliveryRequest:
    accepted = make_accepted_candidate_receipt(action)
    return BatchDeliveryRequest(
        stable_action_id=action.stable_action_id,
        repository=action.repository,
        campaign_key=action.campaign_key,
        plan_revision_digest=action.plan_revision_digest,
        target=BatchTarget(
            repository=action.repository,
            target_branch="main",
            target_head_sha="5" * 40,
            target_tree_oid="6" * 40,
            target_facts_digest="7" * 64,
        ),
        accepted_candidates=(accepted,),
        local_suite=LocalSuiteDefinition(
            suite_id="local:production",
            definition_digest="8" * 64,
            command=("py", "-3.13", "-m", "pytest", "-q"),
        ),
        hosted_suites=(
            HostedSuiteDefinition(
                suite_id="hosted:ci",
                hosted_name="GWO CI",
                definition_digest="9" * 64,
            ),
        ),
        writer_generation="v6.1",
        activation_id="activation:test",
    )


@dataclass
class ProductionEffectsSupport:
    root: Path

    def __post_init__(self) -> None:
        self.runtime = RecordingRuntimeGateway()
        self.runtime_factory = RecordingRuntimeGatewayFactory(
            store_path=self.root / "runtime.sqlite3",
            provider_command="recording-provider --no-dispatch",
            repository_root=self.root,
            gateway=self.runtime,
        )
        self.runtime_stale = RecordingRuntimeStaleReadback()
        self.subjects = RecordingSubjectSource()
        self.references = RecordingCandidateReferenceReader(
            reference="refs/heads/candidate"
        )
        self.parents = RecordingCandidateParentSource()
        self.candidate = RecordingCandidateGate()
        self.batch_requests = RecordingBatchRequestSource(
            target_path=self.root / "target",
            runtime_factory=self.runtime_factory,
        )
        self.batch = RecordingBatchIntegrator(
            store_path=self.root / "batch.sqlite3",
            target_path=self.root / "target",
        )

    @property
    def all_calls(self) -> list[object]:
        return [
            *self.runtime.calls,
            *self.runtime_stale.calls,
            *self.subjects.calls,
            *self.references.calls,
            *self.candidate.calls,
        ]

    def runtime_completed_receipt(
        self,
        action: WorkRunAction,
    ) -> RuntimeProgressReceipt:
        subject = make_test_subject(action)
        receipt = RuntimeProgressReceipt(
            subject_digest=subject.digest,
            stable_action_id=action.stable_action_id,
            status="completed",
            receipt_digest="3" * 64,
            output_artifact_digest="4" * 64,
        )
        return receipt

    def accepted_candidate_result(self, action: WorkRunAction) -> CandidateGateResult:
        return accepted_candidate_result(action)

    def batch_delivery_request(
        self,
        action: WorkRunAction,
    ) -> BatchDeliveryRequest:
        return make_batch_delivery_request(action)

    def complete_batch_observation(
        self,
        action: WorkRunAction,
    ) -> BatchDeliveryObservation:
        candidate = make_candidate_receipt(action)
        accepted = make_accepted_candidate_receipt(action, candidate)
        request = make_batch_delivery_request(action)
        batch_action = BatchDeliveryAction(
            stable_action_id=action.stable_action_id,
            request_digest=request.request_digest,
            batch_id="1" * 64,
            batch_sha=accepted.candidate_sha,
            member_ticket_keys=(accepted.ticket_key,),
        )
        members = (
            MemberDeliveryObservation(
                ticket_key=accepted.ticket_key,
                work_run_key=accepted.work_run_key,
                candidate_sha=accepted.candidate_sha,
                status="integrated",
                evidence_digests=accepted.evidence_digests,
            ),
        )
        delivery_proof = BatchDeliveryProof.create(
            delivery_stable_action_id=batch_action.stable_action_id,
            delivery_request_digest=batch_action.request_digest,
            batch_id=batch_action.batch_id,
            batch_sha=batch_action.batch_sha,
            member_ticket_keys=batch_action.member_ticket_keys,
            local_check_receipt_digest="a" * 64,
            publication_receipt_digest="b" * 64,
            pull_request_number=19,
            pull_request_head_sha=batch_action.batch_sha,
            hosted_result_receipt_digest="c" * 64,
            integration_lease_digest="d" * 64,
            target_branch=request.target.target_branch,
            target_head_sha="e" * 40,
            target_readback_digest="f" * 64,
            target_contains_batch_sha=True,
            pull_request_merge_target_sha="e" * 40,
            merge_method="merge",
        )
        observation_body = {
            "stable_action_id": batch_action.stable_action_id,
            "batch_id": batch_action.batch_id,
            "batch_sha": batch_action.batch_sha,
            "phase": "complete",
            "reason": "target integrated",
            "retry_count": 0,
            "fallback_generation": 0,
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
            "delivery_proofs": [delivery_proof.canonical()],
        }
        observation = BatchDeliveryObservation(
            stable_action_id=batch_action.stable_action_id,
            batch_id=batch_action.batch_id,
            batch_sha=batch_action.batch_sha,
            phase="complete",
            reason="target integrated",
            receipt_digest=digest_value(
                {"kind": "batch-observation.v1", **observation_body}
            ),
            retry_count=0,
            fallback_generation=0,
            members=members,
            delivery_proofs=(delivery_proof,),
        )
        self.batch_requests.request = request
        self.batch.action = batch_action
        self.batch.observation = observation
        return observation

    def plan_invalidation_result(self, action: WorkRunAction) -> CandidateGateResult:
        subject = make_test_subject(action)
        evidence = PlanInvalidationEvidence(
            runtime_subject=subject,
            parent_digest="c" * 64,
            candidate_digest="d" * 64,
            source_kind="scope_audit",
            source_evidence_digest="e" * 64,
            invalidated_obligation="ticket scope",
            required_effects=("read tracker",),
            workspace_identity="workspace:test",
            discovered_facts=("scope=outside",),
            reproduction="deterministic scope escape",
        )
        report = PlanInvalidationReport(
            repository=action.repository,
            campaign_key=action.campaign_key,
            plan_revision_digest=action.plan_revision_digest,
            ticket_key=action.ticket_key,
            work_run_key=action.work_run_key or f"work-run:{action.ticket_key}",
            runtime_binding_id=action.stable_action_id,
            authority_subtree_digest="2" * 64,
            reporter_role="worker",
            evidence_digest=evidence.digest,
            dedup_identity="invalidation:test",
            invalidated_obligation="ticket scope",
            required_effects=("read tracker",),
            workspace_identity="workspace:test",
        )
        proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="f" * 64,
        )
        receipt = PlanInvalidationReceipt(
            report_digest=report.digest,
            receipt_digest="0" * 64,
            capability_policy_proof=proof,
            observation={
                "kind": "plan_invalidation_observation.v1",
                "repository": report.repository,
                "campaign_key": report.campaign_key,
                "plan_revision_digest": report.plan_revision_digest,
                "ticket_key": report.ticket_key,
                "work_run_key": report.work_run_key,
                "runtime_binding_id": report.runtime_binding_id,
                "authority_subtree_digest": report.authority_subtree_digest,
                "reporter_role": report.reporter_role,
                "report_digest": report.digest,
                "evidence_digest": report.evidence_digest,
                "dedup_identity": report.dedup_identity,
                "invalidated_obligation": report.invalidated_obligation,
                "required_effects": list(report.required_effects),
                "workspace_identity": report.workspace_identity,
            },
        )
        return CandidateGateResult(
            status=CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
            evidence=(evidence,),
            plan_invalidation_receipt=receipt,
            plan_invalidation_report=report,
        )


@pytest.fixture
def action() -> WorkRunAction:
    return WorkRunAction(
        stable_action_id="action:109",
        repository="owner/repository",
        campaign_key="campaign:successor-kernel",
        plan_revision_digest="a" * 64,
        ticket_key="issue:109",
        kind="semantic_execution",
        semantic_action_id="semantic:109",
        work_run_key="work-run:issue:109",
        work_subject_digest="b" * 64,
        runtime_binding_id=None,
        wake_ref="runtime:initial",
        accepted_candidate_receipt_digest=None,
    )


@pytest.fixture
def delivery_action(action: WorkRunAction) -> WorkRunAction:
    delivery = WorkRunAction(
        stable_action_id="action:109:batch",
        repository=action.repository,
        campaign_key=action.campaign_key,
        plan_revision_digest=action.plan_revision_digest,
        ticket_key=action.ticket_key,
        kind="batch_delivery",
        semantic_action_id=action.semantic_action_id,
        work_run_key=action.work_run_key,
        work_subject_digest=action.work_subject_digest,
        runtime_binding_id="binding:test",
        wake_ref="candidate:accepted",
        accepted_candidate_receipt_digest=make_accepted_candidate_receipt(action).digest,
    )
    request = make_batch_delivery_request(delivery)
    return replace(delivery, batch_delivery_request_digest=request.request_digest)


@pytest.fixture
def support(tmp_path: Path) -> ProductionEffectsSupport:
    return ProductionEffectsSupport(tmp_path)


def make_production_effects(
    tmp_path: Path,
    support: ProductionEffectsSupport,
):
    from gwo_v8.production_effects import ProductionWorkRunEffects

    support.subjects.subject = make_test_subject(
        WorkRunAction(
            stable_action_id="subject:seed",
            repository="owner/repository",
            campaign_key="campaign:successor-kernel",
            plan_revision_digest="a" * 64,
            ticket_key="issue:109",
            kind="semantic_execution",
            semantic_action_id="semantic:109",
            work_run_key="work-run:issue:109",
            work_subject_digest="b" * 64,
            wake_ref=None,
            accepted_candidate_receipt_digest=None,
        )
    )
    support.parents.parent = CandidateGateParent(
        runtime_subject=support.subjects.subject,
        ticket_contract_digest="c" * 64,
        policy_witness_digest="d" * 64,
        workspace_identity="workspace:test",
    )
    return ProductionWorkRunEffects(
        store_path=tmp_path / "effects.sqlite3",
        runtime_gateways=support.runtime_factory,
        runtime_stale_readbacks=support.runtime_stale,
        work_run_subjects=support.subjects,
        candidate_references=support.references,
        candidate_parents=support.parents,
        candidate_gate=support.candidate,
        batch_requests=support.batch_requests,
        batch_integrator=support.batch,
    )


@dataclass(frozen=True)
class PlanningWriterGenerationReader:
    generation: str = "v6.1"

    def read(self) -> str:
        return self.generation


@dataclass
class DelayedPlanningStartHost:
    root: Path

    def __post_init__(self) -> None:
        self._continuations: dict[CampaignHandle, PlanningContinuation] = {}
        self._active: dict[CampaignHandle, ActivePlanReadback] = {}
        self._planning_action_ids: list[str] = []
        self._planning_passes = 0
        self._planning_store = self.root / "planning-continuation.json"
        self._planning_gateway_calls = 0
        if self._planning_store.exists():
            persisted = json.loads(
                self._planning_store.read_text(encoding="utf-8")
            )
            campaign = persisted["campaign"]
            handle = CampaignHandle(
                campaign["repository"], campaign["campaign_key"]
            )
            continuation = PlanningContinuation(
                campaign=handle,
                ready_refs=tuple(persisted["ready_refs"]),
                expected_previous_revision_digest=persisted[
                    "expected_previous_revision_digest"
                ],
                snapshot_artifact_digest=persisted["snapshot_artifact_digest"],
                planning_request_artifact_digest=persisted[
                    "planning_request_artifact_digest"
                ],
                stable_action_id=persisted["stable_action_id"],
                compilation_record_artifact_digest=persisted[
                    "compilation_record_artifact_digest"
                ],
            )
            self._continuations[handle] = continuation

    def start(
        self,
        repository: str,
        ready_refs: tuple[str, ...],
        options: object = None,
    ) -> CampaignHandle:
        handle = CampaignHandle(repository, "campaign:successor-kernel")
        continuation = PlanningContinuation(
            campaign=handle,
            ready_refs=tuple(ready_refs),
            expected_previous_revision_digest=None,
            snapshot_artifact_digest="1" * 64,
            planning_request_artifact_digest="2" * 64,
            stable_action_id="planning:campaign:planning",
            compilation_record_artifact_digest=None,
        )
        self._continuations[handle] = continuation
        self._planning_store.write_text(
            json.dumps(
                {
                    "campaign": {
                        "repository": handle.repository,
                        "campaign_key": handle.campaign_key,
                    },
                    "ready_refs": list(continuation.ready_refs),
                    "expected_previous_revision_digest": continuation.expected_previous_revision_digest,
                    "snapshot_artifact_digest": continuation.snapshot_artifact_digest,
                    "planning_request_artifact_digest": continuation.planning_request_artifact_digest,
                    "stable_action_id": continuation.stable_action_id,
                    "compilation_record_artifact_digest": continuation.compilation_record_artifact_digest,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return handle

    def read_planning_continuation(
        self,
        handle: CampaignHandle,
    ) -> PlanningContinuation | None:
        return self._continuations.get(handle)

    def read_active_or_none(
        self,
        handle: CampaignHandle,
    ) -> ActivePlanReadback | None:
        return self._active.get(handle)

    def read_active(self, handle: CampaignHandle) -> ActivePlanReadback:
        active = self._active.get(handle)
        if active is None:
            raise AssertionError("active Plan was read before planning continuation")
        return active

    def continue_start(
        self,
        handle: CampaignHandle,
        ready_refs: tuple[str, ...],
    ) -> CampaignHandle:
        continuation = self._continuations[handle]
        if tuple(ready_refs) != continuation.ready_refs:
            raise AssertionError("planning wake changed ready refs")
        self._planning_gateway_calls += 1
        self._planning_passes += 1
        self._planning_action_ids.append(continuation.stable_action_id)
        active, _old_handle = _minimal_active_campaign(continuation.ready_refs)
        plan = json.loads(active.plan_spec_bytes)
        plan["campaign"]["key"] = handle.campaign_key
        from gwo_v8._canonical import canonical_bytes, digest_bytes

        plan_bytes = canonical_bytes(plan)
        revision_digest = digest_bytes(plan_bytes)
        active = replace(
            active,
            current_revision_digest=revision_digest,
            plan_spec_bytes=plan_bytes,
            activation_receipt=replace(
                active.activation_receipt,
                revision_digest=revision_digest,
                ready_refs=continuation.ready_refs,
                ticket_keys=continuation.ready_refs,
            ),
            claim_proofs=tuple(
                replace(proof, plan_revision_digest=revision_digest)
                for proof in active.claim_proofs
            ),
        )
        self._active[handle] = active
        self._continuations.pop(handle)
        self._planning_store.unlink(missing_ok=True)
        return handle

    def install_execution_kernel(
        self,
        *,
        store_path: Path,
        effects: WorkRunEffects,
        configuration: ExecutionKernelConfiguration | None,
    ) -> ExecutionKernel:
        return ExecutionKernel(
            store_path=store_path,
            plan_control=self,
            effects=effects,
            configuration=configuration,
        )

    def planning_gateway_calls(self) -> int:
        return self._planning_gateway_calls

    def planning_action_ids(self) -> list[str]:
        return list(self._planning_action_ids)

    def planning_pass_count(self) -> int:
        return self._planning_passes


@dataclass
class PlanningWatchdog:
    def run_once(self, now: str) -> tuple[CampaignOutcome, ...]:
        return ()


@dataclass
class PlanningHostFixture:
    root: Path
    start_host: DelayedPlanningStartHost
    host: ProductionGwoHost
    arguments: dict[str, object]

    def start(self, repository: str, ready_refs: tuple[str, ...]) -> CampaignHandle:
        return self.host.start(repository, ready_refs)

    def advance(
        self,
        handle: CampaignHandle,
        wake_ref: str | None = None,
    ) -> CampaignOutcome:
        return self.host.advance(handle, wake_ref)

    def inspect(self, handle: CampaignHandle) -> Diagnostics:
        return self.host.inspect(handle)

    def planning_gateway_calls(self) -> int:
        return self.start_host.planning_gateway_calls()

    def planning_action_ids(self) -> list[str]:
        return self.start_host.planning_action_ids()

    def planning_pass_count(self) -> int:
        return self.start_host.planning_pass_count()

    def store_bytes(self) -> bytes:
        return Path(self.arguments["store_path"]).read_bytes()

    def install_arguments(self) -> dict[str, object]:
        return dict(self.arguments)

    def reinstall(self, root: Path) -> "PlanningHostFixture":
        start_host = DelayedPlanningStartHost(root)
        arguments = dict(self.arguments)
        arguments["start_host"] = start_host
        host = ProductionGwoHost.install(**arguments)
        return PlanningHostFixture(root, start_host, host, arguments)


def make_pending_planning_host(root: Path) -> PlanningHostFixture:
    target = root / "isolated-target"
    target.mkdir(parents=True, exist_ok=True)
    start_host = DelayedPlanningStartHost(root)
    store_path = root / "execution-kernel.sqlite3"
    effects = NoopRunningEffects()
    configuration = ProductionHostConfiguration(
        preview_mode="beta2_isolated_preview",
        target_isolation_root=root,
        writer_activation_enabled=False,
    )
    arguments: dict[str, object] = {
        "start_host": start_host,
        "store_path": store_path,
        "effects": effects,
        "configuration": None,
        "host_configuration": configuration,
        "target_path": target,
        "watchdog_store_path": root / "watchdog.sqlite3",
        "watchdog": PlanningWatchdog(),
        "writer_generation_reader": PlanningWriterGenerationReader(),
    }
    host = ProductionGwoHost.install(**arguments)
    return PlanningHostFixture(root, start_host, host, arguments)


@pytest.fixture
def planning_host(tmp_path: Path) -> PlanningHostFixture:
    return make_pending_planning_host(tmp_path)


def reinstall_production_host(
    root: Path,
    planning_host: PlanningHostFixture,
) -> PlanningHostFixture:
    return planning_host.reinstall(root)


@dataclass
class ReopenedRuntimeGateway:
    calls: list[str] = field(default_factory=list)

    def progress(
        self,
        subject: WorkRunSubject,
        *,
        wake_cursor: str | None,
    ) -> RuntimeProgressReceipt:
        self.calls.append(subject.stable_action_id)
        return RuntimeProgressReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            status="completed",
            receipt_digest=digest_value(
                {
                    "kind": "reopened-137-runtime.v1",
                    "subject_digest": subject.digest,
                    "wake_cursor": wake_cursor,
                }
            ),
            output_artifact_digest="4" * 64,
        )


def _reopened_invalidation_receipt(
    report: PlanInvalidationReport,
    source_evidence_digests: tuple[str, ...],
) -> PlanInvalidationReceipt:
    proof = CapabilityPolicyProof(
        capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
        authority_record_digest="f" * 64,
    )
    observation = {
        "kind": "plan_invalidation_observation.v1",
        "repository": report.repository,
        "campaign_key": report.campaign_key,
        "plan_revision_digest": report.plan_revision_digest,
        "ticket_key": report.ticket_key,
        "work_run_key": report.work_run_key,
        "runtime_binding_id": report.runtime_binding_id,
        "authority_subtree_digest": report.authority_subtree_digest,
        "reporter_role": report.reporter_role,
        "report_digest": report.digest,
        "evidence_digest": report.evidence_digest,
        "dedup_identity": report.dedup_identity,
        "invalidated_obligation": report.invalidated_obligation,
        "required_effects": list(report.required_effects),
        "workspace_identity": report.workspace_identity,
        "source_evidence_digests": list(source_evidence_digests),
    }
    return PlanInvalidationReceipt(
        report_digest=report.digest,
        receipt_digest=digest_value(observation),
        capability_policy_proof=proof,
        observation=observation,
    )


def make_reopened_plan_invalidation_result(
    subject: WorkRunSubject,
    *,
    source_kind: str,
    reproduction: str,
) -> CandidateGateResult:
    if source_kind not in {
        "scope_audit",
        "formal_review",
        "repair_verification",
    }:
        raise AssertionError(f"unsupported reopened #137 source: {source_kind}")
    source_digest = digest_value(
        {
            "kind": "reopened-137-source.v1",
            "ticket_key": subject.ticket_key,
            "source_kind": source_kind,
            "reproduction": reproduction,
        }
    )
    evidence = PlanInvalidationEvidence(
        runtime_subject=subject,
        parent_digest="c" * 64,
        candidate_digest="d" * 64,
        source_kind=source_kind,
        source_evidence_digest=source_digest,
        invalidated_obligation="ticket scope",
        required_effects=("read tracker",),
        workspace_identity=f"workspace:{subject.ticket_key}",
        discovered_facts=("scope=outside",),
        reproduction=reproduction,
    )
    report = PlanInvalidationReport(
        repository=subject.repository,
        campaign_key=subject.campaign_key,
        plan_revision_digest=subject.plan_revision_digest,
        ticket_key=subject.ticket_key,
        work_run_key=subject.work_run_key,
        runtime_binding_id=subject.stable_action_id,
        authority_subtree_digest=subject.authority_subtree_digest,
        reporter_role="worker",
        evidence_digest=evidence.digest,
        dedup_identity=f"reopened:137:{subject.ticket_key}:{source_kind}",
        invalidated_obligation=evidence.invalidated_obligation,
        required_effects=evidence.required_effects,
        workspace_identity=evidence.workspace_identity,
    )
    return CandidateGateResult(
        status=CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
        evidence=(evidence,),
        plan_invalidation_receipt=_reopened_invalidation_receipt(
            report,
            evidence.source_evidence_digests,
        ),
        plan_invalidation_report=report,
    )


@dataclass
class ReopenedCandidateGate:
    queued_cases: dict[str, tuple[str, str]] = field(default_factory=dict)
    results: dict[str, CandidateGateResult] = field(default_factory=dict)
    candidate_calls_by_ticket: dict[str, int] = field(default_factory=dict)
    reviewer_calls: int = 0
    formal_review_calls: int = 0
    repair_verification_calls: int = 0
    reporter_calls: int = 0

    def queue(
        self,
        ticket_key: str,
        *,
        mode: str,
        reported_reference: str = "refs/heads/candidate",
    ) -> None:
        if mode not in {
            "deterministic",
            "formal_review",
            "repair",
            "ordinary",
        }:
            raise AssertionError(f"unsupported reopened #137 mode: {mode}")
        if ticket_key in self.queued_cases:
            raise AssertionError(f"case already queued for {ticket_key}")
        self.queued_cases[ticket_key] = (mode, reported_reference)

    def _invalidation(
        self,
        subject: WorkRunSubject,
        *,
        source_kind: str,
        reproduction: str,
    ) -> CandidateGateResult:
        self.reporter_calls += 1
        return make_reopened_plan_invalidation_result(
            subject,
            source_kind=source_kind,
            reproduction=reproduction,
        )

    def _record(
        self,
        ticket_key: str,
        result: CandidateGateResult,
    ) -> CandidateGateResult:
        existing = self.results.get(ticket_key)
        if existing is not None and existing != result:
            raise AssertionError(f"changed CandidateGate result for {ticket_key}")
        self.results[ticket_key] = result
        return result

    def gate_candidate(
        self,
        parent: CandidateGateParent,
        reported_reference: str,
    ) -> CandidateGateResult:
        ticket_key = parent.runtime_subject.ticket_key
        self.candidate_calls_by_ticket[ticket_key] = (
            self.candidate_calls_by_ticket.get(ticket_key, 0) + 1
        )
        mode, expected_reference = self.queued_cases.pop(
            ticket_key,
            ("ordinary", reported_reference),
        )
        if reported_reference != expected_reference:
            raise AssertionError(
                f"candidate reference changed for {ticket_key}: "
                f"{reported_reference!r}"
            )
        if mode == "ordinary":
            result = CandidateGateResult(
                status=CandidateGateStatus.ORDINARY_REJECTED,
                evidence=(),
            )
        elif mode == "deterministic":
            result = self._invalidation(
                parent.runtime_subject,
                source_kind="scope_audit",
                reproduction="deterministic scope escape",
            )
        elif mode == "formal_review":
            self.reviewer_calls += 1
            self.formal_review_calls += 1
            result = self._invalidation(
                parent.runtime_subject,
                source_kind="formal_review",
                reproduction="formal Review scope escape",
            )
        else:
            self.repair_verification_calls += 1
            result = self._invalidation(
                parent.runtime_subject,
                source_kind="repair_verification",
                reproduction="bounded Repair Verification scope escape",
            )
        return self._record(ticket_key, result)

    def verify_repair(
        self,
        parent: CandidateGateParent,
        packet: RepairPacket,
        candidate: CandidateIdentity,
    ) -> CandidateGateResult:
        self.repair_verification_calls += 1
        result = self._invalidation(
            parent.runtime_subject,
            source_kind="repair_verification",
            reproduction=(
                "bounded Repair Verification scope escape for "
                f"{packet.digest}:{candidate.digest}"
            ),
        )
        return self._record(parent.runtime_subject.ticket_key, result)

    def replay_plan_invalidation(
        self,
        parent: CandidateGateParent,
        evidence: PlanInvalidationEvidence,
        report: PlanInvalidationReport,
    ) -> CandidateGateResult:
        if evidence.runtime_subject.digest != parent.runtime_subject.digest:
            raise AssertionError("replayed invalidation changed Runtime Subject")
        if report.evidence_digest != evidence.digest:
            raise AssertionError("replayed invalidation changed Evidence lineage")
        self.reporter_calls += 1
        result = CandidateGateResult(
            status=CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
            evidence=(evidence,),
            plan_invalidation_receipt=_reopened_invalidation_receipt(
                report,
                evidence.source_evidence_digests,
            ),
            plan_invalidation_report=report,
        )
        return self._record(parent.runtime_subject.ticket_key, result)

    def result_for(self, ticket_key: str) -> CandidateGateResult:
        try:
            return self.results[ticket_key]
        except KeyError as error:
            raise AssertionError(
                f"CandidateGate has no result for {ticket_key}"
            ) from error


@dataclass
class ReopenedSubjectSource:
    authority_subtree_digests: dict[str, str] = field(default_factory=dict)

    def for_action(self, action: WorkRunAction) -> WorkRunSubject:
        subject = make_test_subject(action)
        authority_digest = self.authority_subtree_digests.get(action.ticket_key)
        if authority_digest is not None:
            subject = replace(
                subject,
                authority_subtree_digest=authority_digest,
            )
        return subject


@dataclass
class ReopenedParentSource:
    def for_action(
        self,
        action: WorkRunAction,
        subject: WorkRunSubject,
    ) -> CandidateGateParent:
        return CandidateGateParent(
            runtime_subject=subject,
            ticket_contract_digest="c" * 64,
            policy_witness_digest="d" * 64,
            workspace_identity=f"workspace:{action.ticket_key}",
        )


@dataclass
class ReopenedReferenceReader:
    def read(self, output_artifact_digest: str, *, subject: WorkRunSubject) -> str:
        return "refs/heads/candidate"


class ReopenedNoDeliveryBatch:
    def prepare(self, request: BatchDeliveryRequest) -> BatchDeliveryAction:
        raise AssertionError("scope-escape revalidation must not prepare a Batch")

    def readback(self, action: BatchDeliveryAction) -> BatchDeliveryObservation | None:
        raise AssertionError("scope-escape revalidation must not read a Batch")

    def execute(self, action: BatchDeliveryAction) -> BatchDeliveryObservation:
        raise AssertionError("scope-escape revalidation must not execute a Batch")


@dataclass
class ReopenedStartHost:
    active: ActivePlanReadback

    def start(
        self,
        repository: str,
        ready_refs: tuple[str, ...],
        options: object = None,
    ) -> CampaignHandle:
        return self.active.handle

    def read_planning_continuation(self, handle: CampaignHandle) -> None:
        return None

    def read_active_or_none(self, handle: CampaignHandle) -> ActivePlanReadback:
        if handle != self.active.handle:
            raise AssertionError(handle)
        return self.active

    def read_active(self, handle: CampaignHandle) -> ActivePlanReadback:
        return self.read_active_or_none(handle)

    def install_execution_kernel(
        self,
        *,
        store_path: Path,
        effects: WorkRunEffects,
        configuration: ExecutionKernelConfiguration | None,
    ) -> ExecutionKernel:
        return ExecutionKernel(
            store_path=store_path,
            plan_control=self,
            effects=effects,
            configuration=configuration,
        )


@dataclass
class ReopenedWatchdog:
    def run_once(self, now: str) -> tuple[CampaignOutcome, ...]:
        return ()


@dataclass
class Reopened137HostFixture:
    host: ProductionGwoHost
    handle: CampaignHandle
    start_host: ReopenedStartHost
    gate: ReopenedCandidateGate
    root: Path

    def candidate_calls_for(self, ticket_key: str) -> int:
        return self.gate.candidate_calls_by_ticket.get(ticket_key, 0)

    @property
    def reviewer_calls(self) -> int:
        return self.gate.reviewer_calls

    @property
    def formal_review_calls(self) -> int:
        return self.gate.formal_review_calls

    @property
    def repair_verification_calls(self) -> int:
        return self.gate.repair_verification_calls

    @property
    def reporter_calls(self) -> int:
        return self.gate.reporter_calls

    def submit_candidate(
        self,
        ticket_key: str,
        reported_reference: str,
    ) -> None:
        self.gate.queue(
            ticket_key,
            mode="deterministic",
            reported_reference=reported_reference,
        )

    def submit_formal_review_scope_escape(self, ticket_key: str) -> None:
        self.gate.queue(ticket_key, mode="formal_review")

    def submit_repair_scope_escape(self, ticket_key: str) -> None:
        self.gate.queue(ticket_key, mode="repair")

    def submit_ordinary_unauthorized_candidate(self, ticket_key: str) -> None:
        self.gate.queue(ticket_key, mode="ordinary")

    def result_for(self, ticket_key: str) -> CandidateGateResult:
        return self.gate.result_for(ticket_key)

    def run_for(self, ticket_key: str) -> WorkRunSummary:
        matches = tuple(
            run
            for run in self.host.inspect(self.handle).work_runs
            if run.ticket_key == ticket_key
        )
        if len(matches) != 1:
            raise AssertionError(
                f"expected one Work Run for {ticket_key}, found {len(matches)}"
            )
        return matches[0]

    def advance(
        self,
        handle: CampaignHandle,
        wake_ref: str | None = None,
    ) -> CampaignOutcome:
        return self.host.advance(handle, wake_ref)

    def inspect(self, handle: CampaignHandle) -> Diagnostics:
        return self.host.inspect(handle)

    def restart(self) -> "Reopened137HostFixture":
        return install_reopened_137_host(
            root=self.root,
            start_host=self.start_host,
            gate=self.gate,
            handle=self.handle,
        )


def make_reopened_137_effects(
    root: Path,
    gate: ReopenedCandidateGate,
    authority_subtree_digests: dict[str, str],
) -> ProductionWorkRunEffects:
    runtime_factory = RecordingRuntimeGatewayFactory(
        store_path=root / "runtime.sqlite3",
        provider_command="recording-provider --no-dispatch",
        repository_root=root,
        gateway=ReopenedRuntimeGateway(),
    )
    return ProductionWorkRunEffects(
        store_path=root / "production-effects.sqlite3",
        runtime_gateways=runtime_factory,
        runtime_stale_readbacks=RecordingRuntimeStaleReadback(),
        work_run_subjects=ReopenedSubjectSource(authority_subtree_digests),
        candidate_references=ReopenedReferenceReader(),
        candidate_parents=ReopenedParentSource(),
        candidate_gate=gate,
        batch_requests=RecordingBatchRequestSource(
            target_path=root / "target",
            runtime_factory=runtime_factory,
        ),
        batch_integrator=ReopenedNoDeliveryBatch(),
    )


def install_reopened_137_host(
    *,
    root: Path,
    start_host: ReopenedStartHost,
    gate: ReopenedCandidateGate,
    handle: CampaignHandle,
) -> Reopened137HostFixture:
    target = root / "target"
    target.mkdir(parents=True, exist_ok=True)
    from gwo_v8._canonical import load_canonical_json

    plan = load_canonical_json(start_host.active.plan_spec_bytes)
    authority_subtree_digests = {
        item["key"]: item["authority"]["worker"]["subtree_digest"]
        for item in plan["work"]
    }
    host = ProductionGwoHost.install(
        start_host=start_host,
        store_path=root / "execution.sqlite3",
        effects=make_reopened_137_effects(
            root,
            gate,
            authority_subtree_digests,
        ),
        configuration=None,
        host_configuration=ProductionHostConfiguration(
            preview_mode="beta2_isolated_preview",
            target_isolation_root=root,
            writer_activation_enabled=False,
        ),
        target_path=target,
        watchdog_store_path=root / "watchdog.sqlite3",
        watchdog=ReopenedWatchdog(),
        writer_generation_reader=PlanningWriterGenerationReader(),
    )
    return Reopened137HostFixture(host, handle, start_host, gate, root)


def make_reopened_137_host(root: Path) -> Reopened137HostFixture:
    active, handle = _minimal_active_campaign(("issue:108", "issue:109"))
    from gwo_v8._canonical import canonical_bytes, digest_bytes, load_canonical_json

    plan = load_canonical_json(active.plan_spec_bytes)
    plan["campaign"]["key"] = handle.campaign_key
    plan_bytes = canonical_bytes(plan)
    revision_digest = digest_bytes(plan_bytes)
    active = replace(
        active,
        current_revision_digest=revision_digest,
        plan_spec_bytes=plan_bytes,
        activation_receipt=replace(
            active.activation_receipt,
            revision_digest=revision_digest,
        ),
        claim_proofs=tuple(
            replace(proof, plan_revision_digest=revision_digest)
            for proof in active.claim_proofs
        ),
    )
    return install_reopened_137_host(
        root=root,
        start_host=ReopenedStartHost(active),
        gate=ReopenedCandidateGate(),
        handle=handle,
    )


@pytest.fixture
def reopened_137_host(tmp_path: Path) -> Reopened137HostFixture:
    return make_reopened_137_host(tmp_path)


# --- C2 Task 7 Child 7 composition support ---

class RecordingCampaignSnapshotSource:
    def canonical_ready_refs(
        self,
        repository: str,
        ready_refs: Sequence[str],
    ) -> tuple[str, ...]:
        return tuple(sorted(set(ready_refs)))

    def snapshot(
        self,
        repository: str,
        ready_refs: Sequence[str],
    ) -> dict[str, object]:
        source = three_ticket_source_snapshot()
        selected = set(ready_refs)
        rebound_tickets = []
        repository_identity = {
            "full_name": repository,
            "url": f"https://api.github.com/repos/{repository}",
        }
        for original in source["tickets"]:
            if original["key"] not in selected:
                continue
            ticket = deepcopy(original)
            ticket["contract"]["repository"] = deepcopy(repository_identity)
            for blocker in ticket["native_blockers"]:
                blocker["repository"] = deepcopy(repository_identity)
                blocker["source"]["digest"] = digest_value(
                    {
                        "key": blocker["key"],
                        "state": blocker["state"],
                        "repository": repository_identity,
                    }
                )
            ticket["source"]["digest"] = frozen_ticket_contract_digest(
                key=ticket["key"],
                contract=ticket["contract"],
                labels=ticket["labels"],
                native_blockers=ticket["native_blockers"],
            )
            rebound_tickets.append(ticket)
        campaign_source = deepcopy(source["campaign_source"])
        campaign_source["repository"] = repository
        campaign_source["digest"] = digest_value(
            {
                key: campaign_source[key]
                for key in (
                    "repository",
                    "input_ref",
                    "resolved_commit_oid",
                    "tree_oid",
                )
            }
        )
        return {
            "repository": repository,
            "target_branch": source["target_branch"],
            "campaign_source": campaign_source,
            "policy": source["policy"],
            "tickets": rebound_tickets,
        }


class RecordingPlanControlRepository(InMemoryPlanRepository):
    def __init__(self, *, repository: str) -> None:
        self.repository = repository
        super().__init__(writer_generation="writer:v6.1")


_REPARSE_POINT_ATTRIBUTE = 0x0400


def _is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def _has_reparse_point(path: Path) -> bool:
    current = path.absolute()
    while True:
        if _is_reparse_point(current):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _git_metadata_path(target: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = target / path
    return path.resolve(strict=True)


def assert_isolated_e2e_target(target: Path, root: Path) -> None:
    target = Path(target)
    root = Path(root)
    if _has_reparse_point(target) or _has_reparse_point(root):
        raise ProductionCompositionError(
            "REAL_E2E_TARGET_NOT_ISOLATED",
            "the E2E target and explicit isolation root cannot use reparse paths",
        )
    try:
        target_resolved = target.resolve(strict=True)
        root_resolved = root.resolve(strict=True)
    except OSError as exc:
        raise ProductionCompositionError(
            "REAL_E2E_TARGET_NOT_ISOLATED",
            "the E2E target and explicit isolation root must exist",
        ) from exc
    if target_resolved == root_resolved or root_resolved not in target_resolved.parents:
        raise ProductionCompositionError(
            "REAL_E2E_TARGET_NOT_ISOLATED",
            "the E2E target must be a strict child of the explicit isolation root",
        )
    git_entry = target_resolved / ".git"
    if not target_resolved.is_dir() or not git_entry.is_dir():
        raise ProductionCompositionError(
            "REAL_E2E_TARGET_NOT_ISOLATED",
            "the isolated E2E target must have its own .git directory",
        )
    if _has_reparse_point(git_entry):
        raise ProductionCompositionError(
            "REAL_E2E_TARGET_NOT_ISOLATED",
            "the isolated E2E target .git directory cannot be a reparse path",
        )
    try:
        git_dir = subprocess.run(
            ["git", "-C", str(target_resolved), "rev-parse", "--git-dir"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        git_common_dir = subprocess.run(
            [
                "git",
                "-C",
                str(target_resolved),
                "rev-parse",
                "--git-common-dir",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        expected_git_dir = git_entry.resolve(strict=True)
        resolved_git_dir = _git_metadata_path(target_resolved, git_dir)
        resolved_common_dir = _git_metadata_path(target_resolved, git_common_dir)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise ProductionCompositionError(
            "REAL_E2E_TARGET_NOT_ISOLATED",
            "the isolated E2E target Git metadata could not be verified",
        ) from exc
    if (
        resolved_git_dir != expected_git_dir
        or resolved_common_dir != expected_git_dir
        or _has_reparse_point(resolved_git_dir)
        or _has_reparse_point(resolved_common_dir)
    ):
        raise ProductionCompositionError(
            "REAL_E2E_TARGET_NOT_ISOLATED",
            "the isolated E2E target must not share a Git common directory",
        )


def _safe_isolation_root(root: Path) -> Path:
    root = Path(root)
    if _has_reparse_point(root):
        raise ProductionCompositionError(
            "REAL_E2E_TARGET_NOT_ISOLATED",
            "the explicit isolation root cannot use reparse paths",
        )
    if root.exists() and not root.is_dir():
        raise ProductionCompositionError(
            "REAL_E2E_TARGET_NOT_ISOLATED",
            "the explicit isolation root must be a directory",
        )
    git_entry = root / ".git"
    if git_entry.exists() or git_entry.is_symlink():
        raise ProductionCompositionError(
            "REAL_E2E_TARGET_NOT_ISOLATED",
            "the explicit isolation root cannot itself be a Git worktree",
        )
    return root.resolve()


def create_temporary_target(root: Path) -> Path:
    root = _safe_isolation_root(root)
    root.mkdir(parents=True, exist_ok=True)
    target = Path(
        tempfile.mkdtemp(prefix="gwo-v8-real-provider-", dir=str(root))
    ).resolve()
    subprocess.run(
        ["git", "init", "--initial-branch", "main", str(target)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (target / "README.md").write_text(
        "isolated GWO V8 target\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "GWO V8 isolated test",
        "GIT_AUTHOR_EMAIL": "gwo-v8-isolated@example.invalid",
        "GIT_COMMITTER_NAME": "GWO V8 isolated test",
        "GIT_COMMITTER_EMAIL": "gwo-v8-isolated@example.invalid",
    }
    subprocess.run(
        ["git", "-C", str(target), "add", "README.md"],
        check=True,
        env=environment,
    )
    subprocess.run(
        ["git", "-C", str(target), "commit", "-m", "create isolated target"],
        check=True,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert_isolated_e2e_target(target, root)
    return target


def install_real_provider_composition(
    target: Path,
    *,
    root: Path,
    evidence_dir: Path,
) -> ProductionCompositionHarness:
    assert_isolated_e2e_target(target, root)
    if os.environ.get("GWO_V8_REAL_PROVIDER_E2E") != "1":
        raise ProductionCompositionError(
            "REAL_PROVIDER_E2E_NOT_ENABLED",
            "real-provider composition requires GWO_V8_REAL_PROVIDER_E2E=1",
        )
    command = os.environ.get("GWO_V8_REAL_PROVIDER_COMMAND")
    if not command or not command.strip():
        raise ProductionCompositionError(
            "REAL_PROVIDER_COMMAND_MISSING",
            "real-provider composition requires GWO_V8_REAL_PROVIDER_COMMAND",
        )
    raise ProductionCompositionError(
        "REAL_PROVIDER_UNSUPPORTED",
        "no safe real-provider subprocess adapter is available; opt-in fails closed",
    )


_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_ISSUE_137_REVALIDATION_KEYS = {
    "open_approval_digest",
    "open_readback_digest",
    "candidate_route_digest",
    "formal_review_route_digest",
    "repair_route_digest",
    "ordinary_rejection_digest",
    "replay_restart_digest",
    "close_approval_digest",
    "closed_readback_digest",
}


_BETA2_COMPOSITION_FIXTURE_FILENAME = "beta2-composition-fixture.json"


def write_beta2_evidence_bundle(
    root: Path,
    *,
    subject: dict[str, object],
    issue_states: dict[str, str],
    campaign_handle: str,
    plan_revision_digest: str,
    writer_generation_before: str,
    writer_generation_after: str,
    result_integrity_digests: tuple[str, ...],
    batch_delivery_proof_digests: tuple[str, ...],
    issue_137_revalidation: dict[str, object],
    local_verification_manifest_digest: str,
    workflow_count: int,
) -> Path:
    """Write a diagnostic partial fixture, never repository-wide GO evidence."""
    if (
        type(subject) is not dict
        or set(subject) != {"sha", "tree", "parents"}
        or type(subject.get("sha")) is not str
        or _HEX40.fullmatch(subject["sha"]) is None
        or type(subject.get("tree")) is not str
        or _HEX40.fullmatch(subject["tree"]) is None
        or type(subject.get("parents")) is not list
        or any(
            type(parent) is not str or _HEX40.fullmatch(parent) is None
            for parent in subject["parents"]
        )
    ):
        raise ProductionCompositionError(
            "BETA2_EVIDENCE_INVALID",
            "subject must contain lowercase 40-hex sha, tree, and parents",
        )
    if (
        type(plan_revision_digest) is not str
        or _HEX64.fullmatch(plan_revision_digest) is None
    ):
        raise ProductionCompositionError(
            "BETA2_EVIDENCE_INVALID",
            "plan_revision_digest is not a lowercase SHA-256 digest",
        )
    if type(campaign_handle) is not str or not campaign_handle.strip():
        raise ProductionCompositionError(
            "BETA2_EVIDENCE_INVALID",
            "campaign_handle is required",
        )
    if (
        type(writer_generation_before) is not str
        or type(writer_generation_after) is not str
        or writer_generation_before != writer_generation_after
    ):
        raise ProductionCompositionError(
            "BETA2_WRITER_CHANGED",
            "writer generation changed during isolated evidence",
        )
    expected_issue_states = {
        str(number): "CLOSED" for number in (113, 114, 115, 116, 117, 136, 137)
    }
    if issue_states != expected_issue_states:
        raise ProductionCompositionError(
            "BETA2_EVIDENCE_INVALID",
            "issue state readback is not the Beta2 closed set",
        )
    if (
        type(result_integrity_digests) is not tuple
        or not result_integrity_digests
        or any(
            type(digest) is not str or _HEX64.fullmatch(digest) is None
            for digest in result_integrity_digests
        )
    ):
        raise ProductionCompositionError(
            "BETA2_EVIDENCE_INVALID",
            "Result integrity digests are invalid",
        )
    if (
        type(batch_delivery_proof_digests) is not tuple
        or len(batch_delivery_proof_digests) != len(result_integrity_digests)
        or any(
            type(digest) is not str or _HEX64.fullmatch(digest) is None
            for digest in batch_delivery_proof_digests
        )
    ):
        raise ProductionCompositionError(
            "BETA2_EVIDENCE_INVALID",
            "Batch delivery proof digests are invalid",
        )
    if (
        type(issue_137_revalidation) is not dict
        or set(issue_137_revalidation) != _ISSUE_137_REVALIDATION_KEYS
        or any(
            type(digest) is not str or _HEX64.fullmatch(digest) is None
            for digest in issue_137_revalidation.values()
        )
    ):
        raise ProductionCompositionError(
            "BETA2_EVIDENCE_INVALID",
            "#137 revalidation evidence has an unknown or invalid field",
        )
    if (
        type(local_verification_manifest_digest) is not str
        or _HEX64.fullmatch(local_verification_manifest_digest) is None
    ):
        raise ProductionCompositionError(
            "BETA2_EVIDENCE_INVALID",
            "local verification manifest digest is invalid",
        )
    if type(workflow_count) is not int or workflow_count != 0:
        raise ProductionCompositionError(
            "BETA2_EVIDENCE_INVALID",
            "workflow_count must be zero for Local Verification Only",
        )

    manifest = {
        "schema_version": "gwo-v8-beta2-composition-evidence.v2",
        "verification_mode": "Local Verification Only",
        "preview_mode": "beta2_isolated_preview",
        "subject": deepcopy(subject),
        "issue_states": dict(issue_states),
        "campaign_handle": campaign_handle,
        "plan_revision_digest": plan_revision_digest,
        "writer_generation_before": writer_generation_before,
        "writer_generation_after": writer_generation_after,
        "writer_activation_enabled": False,
        "result_integrity_digests": list(result_integrity_digests),
        "batch_delivery_proof_digests": list(batch_delivery_proof_digests),
        "issue_137_revalidation": dict(issue_137_revalidation),
        "local_verification_manifest_digest": local_verification_manifest_digest,
        "workflow_count": 0,
        "full_gate": {
            "pytest": {"status": "passed"},
            "quick_validate": {"status": "passed"},
            "package_sync": {"status": "passed"},
            "diff_check": {"status": "passed"},
            "clean_status": {"status": "passed", "output": ""},
        },
        "target_isolation": True,
    }
    rendered = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"
    temporary_path: Path | None = None
    try:
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=".beta2-evidence-",
            suffix=".tmp",
            dir=str(root),
        )
        os.close(fd)
        temporary_path = Path(temporary_name)
        temporary_path.write_text(rendered, encoding="utf-8", newline="\n")
        temporary_bytes = temporary_path.read_bytes()
        if json.loads(temporary_bytes.decode("utf-8")) != manifest:
            raise ProductionCompositionError(
                "BETA2_EVIDENCE_INVALID",
                "evidence is not canonical JSON",
            )
        temporary_digest = hashlib.sha256(temporary_bytes).hexdigest()
        final_path = root / _BETA2_COMPOSITION_FIXTURE_FILENAME
        os.replace(temporary_path, final_path)
        temporary_path = None
        final_bytes = final_path.read_bytes()
        if (
            json.loads(final_bytes.decode("utf-8")) != manifest
            or hashlib.sha256(final_bytes).hexdigest() != temporary_digest
        ):
            raise ProductionCompositionError(
                "BETA2_EVIDENCE_INVALID",
                "evidence readback changed after write",
            )
        return final_path
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@dataclass
class RecordingWriterGenerationReader:
    generation: str

    def read(self) -> str:
        return self.generation

@dataclass
class ForwardingWatchdogAdvancer:
    host: ProductionGwoHost | None = None
    calls: list[tuple[CampaignHandle, str | None]] = field(default_factory=list)

    def advance(
        self,
        handle: CampaignHandle,
        wake_ref: str | None = None,
    ) -> CampaignOutcome:
        if self.host is None:
            raise AssertionError("watchdog advancer was used before host binding")
        self.calls.append((handle, wake_ref))
        return self.host.advance(handle, wake_ref)


@dataclass
class DeferredProductionCampaignSource:
    host: ProductionGwoHost | None = None
    handle: CampaignHandle | None = None

    def active_campaigns(self) -> tuple[CampaignHandle, ...]:
        if self.host is None or self.handle is None:
            return ()
        return (self.handle,)

    def watchdog_snapshot(
        self,
        handle: CampaignHandle,
    ) -> WatchdogCampaignSnapshot:
        if self.host is None or self.handle != handle:
            raise AssertionError("campaign source was used before host binding")
        return self.host.watchdog_snapshot(handle)


def make_recording_plan_control_start_host(
    *,
    root: Path,
    runtime_factory: RecordingRuntimeGatewayFactory,
) -> ProductionPlanControlStartHost:
    source = RecordingCampaignSnapshotSource()
    repository = RecordingPlanControlRepository(repository="owner/isolated-composition")
    return install_plan_control_start(
        source=source,
        repository=repository,
        runtime_configuration=runtime_factory.runtime_configuration,
        repository_contexts=runtime_factory.repository_contexts,
        gateway_store_path=root / "runtime-gateway.sqlite3",
        artifact_root=root / "artifacts",
        _gateway_builder=runtime_factory.build,
    )

@dataclass
class RecordingWakeSource:
    pages: list[WatchdogWakePage]
    calls: list[str | None] = field(default_factory=list)

    def read(self, after_cursor: str | None) -> WatchdogWakePage:
        self.calls.append(after_cursor)
        if self.pages:
            return self.pages.pop(0)
        return WatchdogWakePage((), after_cursor)


@dataclass
class ProductionCompositionHarness:
    host: ProductionGwoHost
    start_host: ProductionPlanControlStartHost
    store_path: Path
    effects: ProductionWorkRunEffects
    kernel_configuration: ExecutionKernelConfiguration | None
    host_configuration: ProductionHostConfiguration
    watchdog_store_path: Path
    watchdog: CampaignWatchdog
    writer_generation_reader: WriterGenerationReader
    repository: str
    ready_refs: tuple[str, ...]
    handle: CampaignHandle
    target: Path
    batch: RecordingBatchIntegrator
    advance_calls: list[tuple[CampaignHandle, str | None]]
    runtime_wake_source: RecordingWakeSource
    hosted_check_source: RecordingWakeSource
    watchdog_advancer: "ForwardingWatchdogAdvancer"
    evidence_dir: Path
    provider_command: str
    crashes: "CrashController"

    def publish_runtime_wake(self, cursor: str, stable_action_id: str) -> None:
        self.runtime_wake_source.pages.append(
            WatchdogWakePage(
                events=(
                    WatchdogWake(
                        cursor=cursor,
                        campaign=self.handle,
                        source="runtime",
                        source_identity=stable_action_id,
                    ),
                ),
                next_cursor=cursor,
            )
        )

    def advance_to_accepted_candidate(self) -> None:
        self.batch.suppress_callbacks = True
        try:
            for wake_ref in ("runtime:initial", "runtime:completed"):
                self.host.advance(self.handle, wake_ref)
        except BatchCallbackSuppressed:
            pass
        phase = self.host.inspect(self.handle).work_runs[0].phase
        if phase != "accepted_awaiting_delivery":
            raise AssertionError(
                "the deterministic composition fixture did not reach "
                "accepted_awaiting_delivery"
            )

    def kill_before_batch_callback(self) -> None:
        self.batch.suppress_callbacks = True

    def arm_crash(self, point: str) -> None:
        self.crashes.arm(point)

    def effect_ledger_row_count(self) -> int:
        return count_durable_effect_rows(self.effects._store_path)

    def advance_to_batch_delivery(self) -> None:
        self.advance_to_accepted_candidate()
        self.batch.suppress_callbacks = False

    def restart(self) -> "ProductionCompositionHarness":
        return type(self).from_task7_dependencies(
            target_path=self.target,
            evidence_dir=self.evidence_dir,
            provider_command=self.provider_command,
            store_path=self.store_path,
            watchdog_store_path=self.watchdog_store_path,
            existing_handle=self.handle,
            existing_start_host=self.start_host,
        )

    def install_arguments(self) -> dict[str, object]:
        return {
            "start_host": self.start_host,
            "store_path": self.store_path,
            "effects": self.effects,
            "configuration": self.kernel_configuration,
            "host_configuration": self.host_configuration,
            "target_path": self.target,
            "watchdog_store_path": self.watchdog_store_path,
            "watchdog": self.watchdog,
            "writer_generation_reader": self.writer_generation_reader,
        }

    @classmethod
    def from_task7_dependencies(
        cls,
        *,
        target_path: Path,
        evidence_dir: Path,
        provider_command: str,
        store_path: Path | None = None,
        watchdog_store_path: Path | None = None,
        existing_handle: CampaignHandle | None = None,
        existing_start_host: ProductionPlanControlStartHost | None = None,
    ) -> "ProductionCompositionHarness":
        target_path = target_path.resolve()
        evidence_dir = evidence_dir.resolve()
        evidence_dir.mkdir(parents=True, exist_ok=True)
        resolved_store_path = store_path or evidence_dir / "execution-kernel.sqlite3"
        resolved_watchdog_store_path = (
            watchdog_store_path or evidence_dir / "campaign-watchdog.sqlite3"
        )
        runtime_wake_source = RecordingWakeSource([])
        hosted_check_source = RecordingWakeSource([])
        watchdog_advancer = ForwardingWatchdogAdvancer()
        crashes = CrashController()
        runtime_factory = RecordingRuntimeGatewayFactory(
            store_path=evidence_dir / "runtime-gateway.sqlite3",
            provider_command=provider_command,
            repository_root=target_path,
        )
        runtime_factory.gateway.auto_complete = True
        runtime_stale_readbacks = RecordingRuntimeStaleReadback()
        action_book: dict[str, WorkRunAction] = {}
        work_run_subjects = RecordingSubjectSource(
            auto_generate=True,
            actions=action_book,
        )
        candidate_references = RecordingCandidateReferenceReader(auto_generate=True)
        candidate_parents = RecordingCandidateParentSource(
            auto_generate=True,
            actions=action_book,
        )
        candidate_gate = RecordingCandidateGate(
            auto_accept=True,
            actions=action_book,
        )
        batch = RecordingBatchIntegrator(
            store_path=evidence_dir / "batch-integrator.sqlite3",
            target_path=target_path,
        )
        effects = CrashInjectingProductionWorkRunEffects(
            crash_controller=crashes,
            store_path=evidence_dir / "production-effects.sqlite3",
            runtime_gateways=runtime_factory,
            runtime_stale_readbacks=runtime_stale_readbacks,
            work_run_subjects=work_run_subjects,
            candidate_references=candidate_references,
            candidate_parents=candidate_parents,
            candidate_gate=candidate_gate,
            batch_requests=RecordingBatchRequestSource(
                target_path=target_path,
                runtime_factory=runtime_factory,
                auto_generate=True,
            ),
            batch_integrator=CrashReadbackBatchIntegrator(batch, crashes),
        )
        start_host = existing_start_host or make_recording_plan_control_start_host(
            root=evidence_dir,
            runtime_factory=runtime_factory,
        )
        campaign_source = DeferredProductionCampaignSource()
        writer_generation_reader = RecordingWriterGenerationReader("v6.1")
        watchdog = CampaignWatchdog(
            store_path=resolved_watchdog_store_path,
            event_sources={
                "runtime_gateway": runtime_wake_source,
                "hosted_check": hosted_check_source,
            },
            campaign_source=campaign_source,
            advancer=watchdog_advancer,
        )
        host_configuration = ProductionHostConfiguration(
            target_isolation_root=target_path.parent,
            writer_activation_enabled=False,
        )
        host = ProductionGwoHost.install(
            start_host=start_host,
            store_path=resolved_store_path,
            effects=effects,
            configuration=None,
            host_configuration=host_configuration,
            target_path=target_path,
            watchdog_store_path=resolved_watchdog_store_path,
            watchdog=watchdog,
            writer_generation_reader=writer_generation_reader,
        )
        watchdog_advancer.host = host
        advance_calls: list[tuple[CampaignHandle, str | None]] = []
        public_advance = host.advance

        def record_public_advance(
            campaign_handle: CampaignHandle,
            wake_ref: str | None = None,
        ) -> CampaignOutcome:
            advance_calls.append((campaign_handle, wake_ref))
            return public_advance(campaign_handle, wake_ref)

        host.advance = record_public_advance  # type: ignore[method-assign]
        campaign_source.host = host
        repository = "owner/isolated-composition"
        ready_refs = ("issue:109",)
        handle = existing_handle or host.start(repository, ready_refs)
        campaign_source.handle = handle
        return cls(
            host=host,
            start_host=start_host,
            store_path=resolved_store_path,
            effects=effects,
            kernel_configuration=None,
            host_configuration=host_configuration,
            watchdog_store_path=resolved_watchdog_store_path,
            watchdog=watchdog,
            writer_generation_reader=writer_generation_reader,
            repository=repository,
            ready_refs=ready_refs,
            handle=handle,
            target=target_path,
            batch=batch,
            advance_calls=advance_calls,
            runtime_wake_source=runtime_wake_source,
            hosted_check_source=hosted_check_source,
            watchdog_advancer=watchdog_advancer,
            evidence_dir=evidence_dir,
            provider_command=provider_command,
            crashes=crashes,
        )

class CompositionCrash(RuntimeError):
    def __init__(self, point: str) -> None:
        super().__init__(f"injected production-composition crash: {point}")
        self.point = point


class BatchCallbackSuppressed(RuntimeError):
    """Test-only interruption for a lost callback before Batch execution."""


@dataclass
class CrashController:
    armed: set[str] = field(default_factory=set)

    def arm(self, point: str) -> None:
        if point not in {
            "after_effect_ledger_write",
            "after_batch_terminal_readback",
        }:
            raise AssertionError(f"unknown composition crash point: {point}")
        self.armed.add(point)

    def hit(self, point: str) -> None:
        if point in self.armed:
            self.armed.remove(point)
            raise CompositionCrash(point)


@dataclass
class RecordingBatchIntegrator:
    store_path: Path
    target_path: Path
    action: BatchDeliveryAction | None = None
    observation: BatchDeliveryObservation | None = None
    suppress_callbacks: bool = False

    def __post_init__(self) -> None:
        self.store_path = Path(self.store_path)
        self.target_path = Path(self.target_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.store_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS v8_recording_batch_terminal(
                    stable_action_id TEXT PRIMARY KEY,
                    action_json TEXT NOT NULL,
                    observation_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS v8_recording_batch_counters(
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    prepare_calls INTEGER NOT NULL,
                    execute_calls INTEGER NOT NULL,
                    target_integration_calls INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO v8_recording_batch_counters(
                    singleton, prepare_calls, execute_calls,
                    target_integration_calls
                ) VALUES (1, 0, 0, 0)
                ON CONFLICT(singleton) DO NOTHING
                """
            )

    def _action_json(self, action: BatchDeliveryAction) -> str:
        return json.dumps(
            {
                "stable_action_id": action.stable_action_id,
                "request_digest": action.request_digest,
                "batch_id": action.batch_id,
                "batch_sha": action.batch_sha,
                "member_ticket_keys": list(action.member_ticket_keys),
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    def _observation_json(self, observation: BatchDeliveryObservation) -> str:
        return json.dumps(
            {
                "stable_action_id": observation.stable_action_id,
                "batch_id": observation.batch_id,
                "batch_sha": observation.batch_sha,
                "phase": observation.phase,
                "reason": observation.reason,
                "receipt_digest": observation.receipt_digest,
                "retry_count": observation.retry_count,
                "fallback_generation": observation.fallback_generation,
                "members": [
                    {
                        "ticket_key": member.ticket_key,
                        "work_run_key": member.work_run_key,
                        "candidate_sha": member.candidate_sha,
                        "status": member.status,
                        "evidence_digests": list(member.evidence_digests),
                        "resume_reason": member.resume_reason,
                    }
                    for member in observation.members
                ],
                "delivery_proofs": [
                    proof.canonical() for proof in observation.delivery_proofs
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    def _decode_observation(self, rendered: str) -> BatchDeliveryObservation:
        value = json.loads(rendered)
        expected = {
            "stable_action_id",
            "batch_id",
            "batch_sha",
            "phase",
            "reason",
            "receipt_digest",
            "retry_count",
            "fallback_generation",
            "members",
            "delivery_proofs",
        }
        if type(value) is not dict or set(value) != expected:
            raise AssertionError("recording Batch observation fields changed")
        members = tuple(
            MemberDeliveryObservation(
                ticket_key=item["ticket_key"],
                work_run_key=item["work_run_key"],
                candidate_sha=item["candidate_sha"],
                status=item["status"],
                evidence_digests=tuple(item["evidence_digests"]),
                resume_reason=item["resume_reason"],
            )
            for item in value["members"]
        )
        delivery_proofs = tuple(
            BatchDeliveryProof(
                **{
                    **item,
                    "member_ticket_keys": tuple(item["member_ticket_keys"]),
                }
            )
            for item in value["delivery_proofs"]
        )
        return BatchDeliveryObservation(
            stable_action_id=value["stable_action_id"],
            batch_id=value["batch_id"],
            batch_sha=value["batch_sha"],
            phase=value["phase"],
            reason=value["reason"],
            receipt_digest=value["receipt_digest"],
            retry_count=value["retry_count"],
            fallback_generation=value["fallback_generation"],
            members=members,
            delivery_proofs=delivery_proofs,
        )

    def _bump(self, column: str) -> None:
        if column not in {
            "prepare_calls",
            "execute_calls",
            "target_integration_calls",
        }:
            raise AssertionError(column)
        with sqlite3.connect(self.store_path) as connection:
            connection.execute(
                f"UPDATE v8_recording_batch_counters "
                f"SET {column} = {column} + 1 WHERE singleton = 1"
            )

    def _counter(self, column: str) -> int:
        with sqlite3.connect(self.store_path) as connection:
            row = connection.execute(
                f"SELECT {column} FROM v8_recording_batch_counters "
                "WHERE singleton = 1"
            ).fetchone()
        if row is None:
            raise AssertionError("recording Batch counters disappeared")
        return int(row[0])

    @property
    def prepare_calls(self) -> int:
        return self._counter("prepare_calls")

    @property
    def execute_calls(self) -> int:
        return self._counter("execute_calls")

    @property
    def target_integration_calls(self) -> int:
        return self._counter("target_integration_calls")

    @property
    def persisted_observation(self) -> BatchDeliveryObservation | None:
        if self.action is None:
            return None
        return self.readback(self.action)

    def prepare(self, request: BatchDeliveryRequest) -> BatchDeliveryAction:
        self._bump("prepare_calls")
        if not request.accepted_candidates:
            raise AssertionError("recording Batch requires an accepted Candidate")
        expected_action = self.action or BatchDeliveryAction(
            stable_action_id=request.stable_action_id,
            request_digest=request.request_digest,
            batch_id=digest_value(
                {
                    "kind": "recording-batch.v1",
                    "request_digest": request.request_digest,
                }
            ),
            batch_sha=request.accepted_candidates[-1].candidate_sha,
            member_ticket_keys=tuple(
                item.ticket_key for item in request.accepted_candidates
            ),
        )
        if (
            expected_action.stable_action_id != request.stable_action_id
            or expected_action.request_digest != request.request_digest
            or expected_action.member_ticket_keys
            != tuple(item.ticket_key for item in request.accepted_candidates)
        ):
            raise AssertionError("recording Batch action identity changed")
        self.action = expected_action
        if self.observation is not None:
            if (
                self.observation.stable_action_id != expected_action.stable_action_id
                or self.observation.batch_id != expected_action.batch_id
                or self.observation.batch_sha != expected_action.batch_sha
                or tuple(
                    member.ticket_key for member in self.observation.members
                )
                != expected_action.member_ticket_keys
            ):
                raise AssertionError("recording Batch observation identity changed")
            return expected_action
        members = tuple(
            MemberDeliveryObservation(
                ticket_key=item.ticket_key,
                work_run_key=item.work_run_key,
                candidate_sha=item.candidate_sha,
                status="integrated",
                evidence_digests=tuple(sorted(item.evidence_digests)),
                resume_reason=None,
            )
            for item in request.accepted_candidates
        )
        delivery_proof = BatchDeliveryProof.create(
            delivery_stable_action_id=expected_action.stable_action_id,
            delivery_request_digest=expected_action.request_digest,
            batch_id=expected_action.batch_id,
            batch_sha=expected_action.batch_sha,
            member_ticket_keys=expected_action.member_ticket_keys,
            local_check_receipt_digest=digest_value(
                {"kind": "recording-local-check.v1", "batch_sha": expected_action.batch_sha}
            ),
            publication_receipt_digest=digest_value(
                {"kind": "recording-publication.v1", "batch_sha": expected_action.batch_sha}
            ),
            pull_request_number=1,
            pull_request_head_sha=expected_action.batch_sha,
            hosted_result_receipt_digest=digest_value(
                {"kind": "recording-hosted-result.v1", "batch_sha": expected_action.batch_sha}
            ),
            integration_lease_digest=digest_value(
                {"kind": "recording-integration-lease.v1", "batch_sha": expected_action.batch_sha}
            ),
            target_branch=request.target.target_branch,
            target_head_sha=expected_action.batch_sha,
            target_readback_digest=digest_value(
                {"kind": "recording-target-readback.v1", "batch_sha": expected_action.batch_sha}
            ),
            target_contains_batch_sha=True,
            pull_request_merge_target_sha=expected_action.batch_sha,
            merge_method="merge",
        )
        receipt_body = {
            "stable_action_id": expected_action.stable_action_id,
            "batch_id": expected_action.batch_id,
            "batch_sha": expected_action.batch_sha,
            "phase": "complete",
            "reason": "exact isolated target read-back",
            "retry_count": 0,
            "fallback_generation": 0,
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
            "delivery_proofs": [delivery_proof.canonical()],
        }
        receipt_digest = digest_value(
            {"kind": "batch-observation.v1", **receipt_body}
        )
        expected_observation = BatchDeliveryObservation(
            stable_action_id=expected_action.stable_action_id,
            batch_id=expected_action.batch_id,
            batch_sha=expected_action.batch_sha,
            phase="complete",
            reason="exact isolated target read-back",
            receipt_digest=receipt_digest,
            retry_count=0,
            fallback_generation=0,
            members=members,
            delivery_proofs=(delivery_proof,),
        )
        self.observation = expected_observation
        return expected_action

    def readback(
        self,
        action: BatchDeliveryAction,
    ) -> BatchDeliveryObservation | None:
        with sqlite3.connect(self.store_path) as connection:
            row = connection.execute(
                """
                SELECT action_json, observation_json
                  FROM v8_recording_batch_terminal
                 WHERE stable_action_id = ?
                """,
                (action.stable_action_id,),
            ).fetchone()
        if row is None:
            return None
        if row[0] != self._action_json(action):
            raise AssertionError("recording Batch durable action changed")
        observation = self._decode_observation(row[1])
        if (
            observation.stable_action_id != action.stable_action_id
            or observation.batch_id != action.batch_id
            or observation.batch_sha != action.batch_sha
            or observation.phase not in {"complete", "decision", "blocked"}
        ):
            raise AssertionError("recording Batch terminal readback changed")
        return observation

    def execute(self, action: BatchDeliveryAction) -> BatchDeliveryObservation:
        persisted = self.readback(action)
        if persisted is not None:
            return persisted
        if self.action != action or self.observation is None:
            raise AssertionError("prepare must configure the recording Batch")
        self._bump("execute_calls")
        self._bump("target_integration_calls")
        with sqlite3.connect(self.store_path) as connection:
            connection.execute(
                """
                INSERT INTO v8_recording_batch_terminal(
                    stable_action_id, action_json, observation_json
                ) VALUES (?, ?, ?)
                ON CONFLICT(stable_action_id) DO NOTHING
                """,
                (
                    action.stable_action_id,
                    self._action_json(action),
                    self._observation_json(self.observation),
                ),
            )
        saved = self.readback(action)
        if saved != self.observation:
            raise AssertionError("recording Batch exact readback differs from execute")
        return saved


@dataclass
class CrashReadbackBatchIntegrator:
    inner: RecordingBatchIntegrator
    crashes: CrashController

    def prepare(self, request: BatchDeliveryRequest) -> BatchDeliveryAction:
        if self.inner.suppress_callbacks:
            raise BatchCallbackSuppressed()
        return self.inner.prepare(request)

    def readback(
        self,
        action: BatchDeliveryAction,
    ) -> BatchDeliveryObservation | None:
        observation = self.inner.readback(action)
        if observation is not None and observation.phase in {
            "complete",
            "decision",
            "blocked",
        }:
            self.crashes.hit("after_batch_terminal_readback")
        return observation

    def execute(self, action: BatchDeliveryAction) -> BatchDeliveryObservation:
        return self.inner.execute(action)


class CrashInjectingProductionWorkRunEffects(ProductionWorkRunEffects):
    def __init__(
        self,
        *,
        crash_controller: CrashController,
        store_path: Path,
        runtime_gateways: RuntimeGatewayFactory,
        runtime_stale_readbacks: RuntimeStaleReadbackPort,
        work_run_subjects: WorkRunSubjectSource,
        candidate_references: CandidateReferenceReader,
        candidate_parents: CandidateGateParentSource,
        candidate_gate: CandidateGatePort,
        batch_requests: BatchRequestSource,
        batch_integrator: BatchIntegratorPort,
    ) -> None:
        self._crash_controller = crash_controller
        super().__init__(
            store_path=store_path,
            runtime_gateways=runtime_gateways,
            runtime_stale_readbacks=runtime_stale_readbacks,
            work_run_subjects=work_run_subjects,
            candidate_references=candidate_references,
            candidate_parents=candidate_parents,
            candidate_gate=candidate_gate,
            batch_requests=batch_requests,
            batch_integrator=batch_integrator,
        )

    def _record(
        self,
        action: WorkRunAction,
        observation: WorkRunEffectObservation,
        *,
        accepted_candidate: AcceptedCandidateReceipt | None = None,
    ) -> WorkRunEffectObservation:
        saved = super()._record(
            action,
            observation,
            accepted_candidate=accepted_candidate,
        )
        self._crash_controller.hit("after_effect_ledger_write")
        return saved


def count_durable_effect_rows(store_path: Path) -> int:
    with sqlite3.connect(store_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM v8_production_effect_receipts"
        ).fetchone()
    if row is None:
        raise AssertionError("effect-ledger count readback disappeared")
    return int(row[0])

@pytest.fixture
def composition_harness(tmp_path: Path) -> ProductionCompositionHarness:
    target = tmp_path / "isolated-target"
    target.mkdir(parents=True, exist_ok=False)
    evidence_dir = tmp_path / "composition-evidence"
    return ProductionCompositionHarness.from_task7_dependencies(
        target_path=target,
        evidence_dir=evidence_dir,
        provider_command="recording-provider --no-dispatch",
    )
