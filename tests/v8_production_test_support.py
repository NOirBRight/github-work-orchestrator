from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from gwo_v8._canonical import digest_value
from gwo_v8.batch_integrator import (
    BatchDeliveryAction,
    BatchDeliveryObservation,
    BatchDeliveryProof,
    BatchDeliveryRequest,
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
    ResultIntegrityProof,
    StaleBindingObservation,
    StaleDiagnosisObservation,
    WorkRunAction,
    WorkRunEffects,
    WorkRunObservation,
)
from gwo_v8.plan_control import ActivePlanReadback, CampaignHandle
from gwo_v8.runtime_gateway import (
    PlanInvalidationReport,
    ProfileMapping,
    RuntimeConfiguration,
    RuntimeGateway,
    RuntimeProgressReceipt,
    RuntimeRepositoryContext,
    WorkRunSubject,
)
from gwo_v8.runtime_profile import RuntimeProfile
from v8_successor_test_support import _StaticPlanReader, _minimal_active_campaign


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

    def progress(
        self,
        subject: WorkRunSubject,
        *,
        wake_cursor: str | None,
    ) -> RuntimeProgressReceipt:
        self.calls.append(("progress", subject.work_run_key))
        if self.receipt is None:
            raise AssertionError("RecordingRuntimeGateway.receipt must be configured")
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

    def for_action(self, action: WorkRunAction) -> WorkRunSubject:
        self.calls.append(action.stable_action_id)
        if self.subject is None:
            raise AssertionError("RecordingSubjectSource.subject must be configured")
        return self.subject


@dataclass
class RecordingCandidateReferenceReader:
    reference: str | None = None
    calls: list[str] = field(default_factory=list)

    def read(self, output_artifact_digest: str, *, subject: WorkRunSubject) -> str:
        self.calls.append(output_artifact_digest)
        if self.reference is None:
            raise AssertionError(
                "RecordingCandidateReferenceReader.reference must be configured"
            )
        return self.reference


@dataclass
class RecordingCandidateParentSource:
    parent: CandidateGateParent | None = None

    def for_action(
        self,
        action: WorkRunAction,
        subject: WorkRunSubject,
    ) -> CandidateGateParent:
        if self.parent is None:
            raise AssertionError(
                "RecordingCandidateParentSource.parent must be configured"
            )
        return self.parent


@dataclass
class RecordingCandidateGate:
    result: CandidateGateResult | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)

    def _result(self, operation: str, stable_action_id: str) -> CandidateGateResult:
        self.calls.append((stable_action_id, operation))
        if self.result is None:
            raise AssertionError("RecordingCandidateGate.result must be configured")
        return self.result

    def gate_candidate(
        self,
        parent: CandidateGateParent,
        reported_reference: str,
    ) -> CandidateGateResult:
        return self._result("gate_candidate", parent.runtime_subject.stable_action_id)

    def verify_repair(
        self,
        parent: CandidateGateParent,
        packet: RepairPacket,
        candidate: CandidateIdentity,
    ) -> CandidateGateResult:
        return self._result("verify_repair", parent.runtime_subject.stable_action_id)

    def replay_plan_invalidation(
        self,
        parent: CandidateGateParent,
        evidence: PlanInvalidationEvidence,
        report: PlanInvalidationReport,
    ) -> CandidateGateResult:
        return self._result(
            "replay_plan_invalidation",
            parent.runtime_subject.stable_action_id,
        )


@dataclass
class RecordingBatchRequestSource:
    target_path: Path
    runtime_factory: RecordingRuntimeGatewayFactory
    request: BatchDeliveryRequest | None = None

    def for_action(
        self,
        action: WorkRunAction,
        subject: WorkRunSubject,
        accepted_candidates: tuple[AcceptedCandidateReceipt, ...],
    ) -> BatchDeliveryRequest:
        if self.request is None:
            raise AssertionError("RecordingBatchRequestSource.request must be configured")
        return self.request


@dataclass
class RecordingBatchIntegrator:
    store_path: Path
    target_path: Path
    action: BatchDeliveryAction | None = None
    observation: BatchDeliveryObservation | None = None
    prepare_calls: int = 0
    execute_calls: int = 0
    target_integration_calls: int = 0
    suppress_callbacks: bool = False

    def prepare(self, request: BatchDeliveryRequest) -> BatchDeliveryAction:
        self.prepare_calls += 1
        if self.action is None:
            raise AssertionError("RecordingBatchIntegrator.action must be configured")
        return self.action

    def readback(self, action: BatchDeliveryAction) -> BatchDeliveryObservation | None:
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
