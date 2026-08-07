"""Host-private ports for composing the V8 production effect boundary."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

from .batch_integrator import (
    BatchDeliveryAction,
    BatchDeliveryObservation,
    BatchDeliveryRequest,
)
from .candidate_gate import (
    AcceptedCandidateReceipt,
    CandidateGateParent,
    CandidateGateResult,
    CandidateIdentity,
    PlanInvalidationEvidence,
    RepairPacket,
)
from .execution_kernel import (
    StaleBindingObservation,
    StaleDiagnosisObservation,
    WorkRunAction,
    WorkRunObservation,
)
from .plan_control import CampaignHandle
from .runtime_gateway import (
    PlanInvalidationReport,
    RuntimeGateway,
    RuntimeProgressReceipt,
    WorkRunSubject,
)


class ProductionCompositionError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class RuntimeGatewayFactory(Protocol):
    def for_campaign(self, handle: CampaignHandle) -> RuntimeGateway: ...


class WorkRunSubjectSource(Protocol):
    def for_action(self, action: WorkRunAction) -> WorkRunSubject: ...


class CandidateReferenceReader(Protocol):
    def read(
        self,
        output_artifact_digest: str,
        *,
        subject: WorkRunSubject,
    ) -> str: ...


class RuntimeStaleReadbackPort(Protocol):
    def read_stale(
        self,
        action: WorkRunAction,
    ) -> StaleBindingObservation | StaleDiagnosisObservation: ...


class CandidateGatePort(Protocol):
    def gate_candidate(
        self,
        parent: CandidateGateParent,
        reported_reference: str,
    ) -> CandidateGateResult: ...

    def verify_repair(
        self,
        parent: CandidateGateParent,
        packet: RepairPacket,
        candidate: CandidateIdentity,
    ) -> CandidateGateResult: ...

    def replay_plan_invalidation(
        self,
        parent: CandidateGateParent,
        evidence: PlanInvalidationEvidence,
        report: PlanInvalidationReport,
    ) -> CandidateGateResult: ...


class CandidateGateParentSource(Protocol):
    def for_action(
        self,
        action: WorkRunAction,
        subject: WorkRunSubject,
    ) -> CandidateGateParent: ...


class BatchIntegratorPort(Protocol):
    def prepare(self, request: BatchDeliveryRequest) -> BatchDeliveryAction: ...

    def readback(
        self,
        action: BatchDeliveryAction,
    ) -> BatchDeliveryObservation | None: ...

    def execute(self, action: BatchDeliveryAction) -> BatchDeliveryObservation: ...


class BatchRequestSource(Protocol):
    def for_action(
        self,
        action: WorkRunAction,
        subject: WorkRunSubject,
        accepted_candidates: tuple[AcceptedCandidateReceipt, ...],
    ) -> BatchDeliveryRequest: ...


WorkRunEffectObservation = (
    WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation
)


class ProductionWorkRunEffects:
    def __init__(
        self,
        *,
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
        required = (
            ("runtime_gateways", runtime_gateways, ("for_campaign",)),
            ("runtime_stale_readbacks", runtime_stale_readbacks, ("read_stale",)),
            ("work_run_subjects", work_run_subjects, ("for_action",)),
            ("candidate_references", candidate_references, ("read",)),
            ("candidate_parents", candidate_parents, ("for_action",)),
            (
                "candidate_gate",
                candidate_gate,
                ("gate_candidate", "verify_repair", "replay_plan_invalidation"),
            ),
            ("batch_requests", batch_requests, ("for_action",)),
            ("batch_integrator", batch_integrator, ("prepare", "readback", "execute")),
        )
        if any(
            any(not callable(getattr(port, method, None)) for method in methods)
            for _name, port, methods in required
        ):
            raise ProductionCompositionError(
                "PRODUCTION_COMPOSITION_INPUT_INVALID",
                "every merged Runtime/Candidate/Batch port must expose its exact methods",
            )
        self._store_path = Path(store_path)
        self._runtime_gateways = runtime_gateways
        self._runtime_stale_readbacks = runtime_stale_readbacks
        self._work_run_subjects = work_run_subjects
        self._candidate_references = candidate_references
        self._candidate_parents = candidate_parents
        self._candidate_gate = candidate_gate
        self._batch_requests = batch_requests
        self._batch_integrator = batch_integrator
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._store_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS v8_production_effect_receipts(
                    stable_action_id TEXT PRIMARY KEY,
                    action_json TEXT NOT NULL,
                    observation_json TEXT NOT NULL,
                    observation_digest TEXT NOT NULL,
                    accepted_candidate_receipt_json TEXT
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(v8_production_effect_receipts)"
                )
            }
            if "accepted_candidate_receipt_json" not in columns:
                connection.execute(
                    "ALTER TABLE v8_production_effect_receipts "
                    "ADD COLUMN accepted_candidate_receipt_json TEXT"
                )

    def readback(
        self,
        action: WorkRunAction,
    ) -> WorkRunEffectObservation | None:
        if type(action) is not WorkRunAction or not action.stable_action_id:
            raise ProductionCompositionError(
                "PRODUCTION_EFFECT_ACTION_INVALID",
                "readback requires one exact non-empty WorkRunAction identity",
            )
        with sqlite3.connect(self._store_path) as connection:
            row = connection.execute(
                "SELECT observation_json FROM v8_production_effect_receipts "
                "WHERE stable_action_id = ?",
                (action.stable_action_id,),
            ).fetchone()
        if row is None:
            return None
        raise ProductionCompositionError(
            "PRODUCTION_EFFECT_READBACK_REQUIRES_UNION_DECODER",
            "a persisted effect row requires the canonical closed-union decoder installed by Task 4",
        )

    def execute(self, action: WorkRunAction) -> WorkRunEffectObservation:
        cached = self.readback(action)
        if cached is not None:
            return cached
        raise ProductionCompositionError(
            "PRODUCTION_EFFECT_EXECUTION_REQUIRES_UNION_ADAPTER",
            "a missing effect row is executable only through the Task 4 readback-first adapter",
        )
