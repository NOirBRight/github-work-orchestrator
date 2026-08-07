from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from gwo_v8._canonical import digest_value
from gwo_v8.batch_integrator import (
    BatchDeliveryAction,
    BatchDeliveryObservation,
    BatchDeliveryRequest,
)
from gwo_v8.candidate_gate import (
    AcceptedCandidateReceipt,
    CandidateGateParent,
    CandidateGateResult,
    CandidateIdentity,
    PlanInvalidationEvidence,
    RepairPacket,
)
from gwo_v8.execution_kernel import (
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
