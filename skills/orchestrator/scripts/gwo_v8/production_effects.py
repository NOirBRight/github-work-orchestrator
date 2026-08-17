"""Host-private ports for composing the V8 production effect boundary."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Protocol

from ._canonical import digest_bytes, digest_value
from .batch_integrator import (
    BatchDeliveryAction,
    BatchDeliveryObservation,
    BatchDeliveryRequest,
    MemberDeliveryObservation,
)
from .candidate_gate import (
    AcceptedCandidateReceipt,
    CandidateGateParent,
    CandidateGateResult,
    CandidateGateStatus,
    CandidateIdentity,
    CandidateReceipt,
    InteractionClassification,
    InteractionKey,
    PlanInvalidationEvidence,
    RepairPacket,
)
from .execution_kernel import (
    PlanInvalidationObservation,
    ResultIntegrityProof,
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


class _ProductionReplayDeferred(RuntimeError):
    """Stop one fair scan after replaying a durable semantic effect."""


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

_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_EFFECT_CLAIM_WAIT_SECONDS = 10.0
_EFFECT_CLAIM_POLL_SECONDS = 0.01


@contextmanager
def _ledger_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


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
        self._public_advance_active = False
        self._replayed_effect: tuple[str, str] | None = None
        self._suppress_replay_tracking = False
        self._fault_proxy: object | None = None
        self._claim_owner_prefix = f"{id(self)}:{uuid.uuid4().hex}"
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        with _ledger_connection(self._store_path) as connection:
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS v8_production_effect_claims(
                    stable_action_id TEXT PRIMARY KEY,
                    action_json TEXT NOT NULL,
                    owner_token TEXT NOT NULL,
                    state TEXT NOT NULL,
                    claimed_at REAL NOT NULL,
                    completed_observation_digest TEXT,
                    provider_dispatched INTEGER
                )
                """
            )
            claim_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(v8_production_effect_claims)"
                )
            }
            if "provider_dispatched" not in claim_columns:
                connection.execute(
                    "ALTER TABLE v8_production_effect_claims "
                    "ADD COLUMN provider_dispatched INTEGER"
                )

    def bind_fault_proxy(self, proxy: object) -> None:
        """Install the named-Canary adapter on the real effect boundary."""

        if not callable(getattr(proxy, "execute", None)):
            raise ProductionCompositionError(
                "ROOT_CANARY_FAULT_CONFIGURATION_INVALID",
                "fault proxy must expose execute",
            )
        self._fault_proxy = proxy

    def _begin_public_advance(self) -> None:
        self._public_advance_active = True
        self._replayed_effect = None

    def _end_public_advance(self) -> None:
        self._public_advance_active = False
        self._replayed_effect = None

    def bind_batch_delivery_request_digest(self, action: WorkRunAction) -> str:
        """Read the exact parent Batch request identity without delivering it."""

        self._validate_action(action)
        if action.kind != "batch_delivery":
            raise ProductionCompositionError(
                "PRODUCTION_EFFECT_ACTION_INVALID",
                "Batch request binding requires a batch_delivery action",
            )
        expected_digest = action.batch_delivery_request_digest
        accepted_digest = action.accepted_candidate_receipt_digest
        if not accepted_digest:
            raise ProductionCompositionError(
                "BATCH_ACCEPTED_RECEIPT_MISSING",
                "batch delivery requires the exact accepted-Candidate digest",
            )
        accepted_candidate = self._read_accepted_candidate_receipt(
            action,
            accepted_digest,
        )
        subject = self._work_run_subjects.for_action(action)
        self._validate_subject(subject, action)
        request = self._batch_requests.for_action(
            action,
            subject,
            (accepted_candidate,),
        )
        self._validate_batch_request(
            request,
            action,
            accepted_candidate,
            require_request_digest=False,
        )
        request_digest = request.request_digest
        if expected_digest is not None and expected_digest != request_digest:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "Batch request identity changed for the stable action",
            )
        return request_digest

    def readback(
        self,
        action: WorkRunAction,
    ) -> WorkRunEffectObservation | None:
        self._validate_action(action)
        expected_action_json = self._action_json(action)
        try:
            with _ledger_connection(self._store_path) as connection:
                row = connection.execute(
                    """
                    SELECT action_json, observation_json, observation_digest,
                           accepted_candidate_receipt_json
                      FROM v8_production_effect_receipts
                     WHERE stable_action_id = ?
                    """,
                    (action.stable_action_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect ledger could not be read",
            ) from error
        if row is None:
            return None
        action_json, observation_json, observation_digest, accepted_json = row
        try:
            recorded_action = self._decode_recorded_action(action_json)
            requested_action = self._decode_recorded_action(expected_action_json)
        except ProductionCompositionError:
            raise
        except Exception as error:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect ledger action identity is not exact",
            ) from error
        recorded_action.pop("wake_ref", None)
        requested_action.pop("wake_ref", None)
        if recorded_action != requested_action:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect ledger action identity changed for the stable action",
            )
        if (
            type(observation_json) is not str
            or type(observation_digest) is not str
            or digest_bytes(observation_json.encode("utf-8")) != observation_digest
        ):
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect ledger observation digest changed",
            )
        try:
            payload = json.loads(observation_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect ledger observation is not JSON",
            ) from error
        if type(payload) is not dict:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect ledger observation is not a JSON object",
            )
        try:
            observation = self._decode_effect_observation(payload)
            self._validate_effect_observation(action, observation)
            if (
                action.kind in {"semantic_execution", "semantic_resume"}
                and type(observation) is WorkRunObservation
                and observation.accepted_candidate_receipt_digest is not None
                and accepted_json is None
            ):
                raise ProductionCompositionError(
                    "EFFECT_READBACK_INVALID",
                    "accepted Candidate observation omitted its persisted receipt",
                )
            if accepted_json is not None:
                self._validate_stored_accepted_receipt(
                    action,
                    observation,
                    accepted_json,
                )
            elif (
                action.kind == "batch_delivery"
                and type(observation) is WorkRunObservation
                and observation.accepted_candidate_receipt_digest is not None
            ):
                persisted = self._read_accepted_candidate_receipt(
                    action,
                    observation.accepted_candidate_receipt_digest,
                )
                if (
                    observation.candidate_receipt is not None
                    and persisted.candidate_receipt_digest
                    != observation.candidate_receipt.digest
                ) or (
                    observation.candidate_diff_record_digest
                    != persisted.diff_record_digest
                ):
                    raise ProductionCompositionError(
                        "EFFECT_READBACK_INVALID",
                        "Batch observation changed its persisted Candidate identity",
                    )
            if self._public_advance_active and not self._suppress_replay_tracking:
                self._replayed_effect = (action.stable_action_id, action.kind)
            return observation
        except ProductionCompositionError:
            raise
        except Exception as error:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect ledger row is not an exact closed-union observation",
            ) from error

    def _claim_token(self, action: WorkRunAction) -> str:
        return (
            f"{self._claim_owner_prefix}:{action.stable_action_id}:"
            f"{threading.get_ident()}:{uuid.uuid4().hex}"
        )

    def _claim_or_wait(
        self,
        action: WorkRunAction,
        owner: str,
    ) -> tuple[WorkRunEffectObservation | None, RuntimeProgressReceipt | None]:
        """Reserve an effect before any provider/deep-module call.

        A committed claim is the authoritative duplicate fence.  A competing
        caller waits for the owner to publish the effect receipt; it never
        speculatively enters RuntimeGateway, CandidateGate, or BatchIntegrator.
        An in-flight claim is never taken over on a timer: an ambiguous
        provider boundary must be recovered by readback, not by risking a
        second provider effect.
        """

        action_json = self._claim_action_json(action)
        deadline = time.monotonic() + _EFFECT_CLAIM_WAIT_SECONDS
        semantic_readback_attempted = False
        while True:
            now = time.time()
            stale_owner: str | None = None
            stale_claimed_at: float | None = None
            try:
                with _ledger_connection(self._store_path) as connection:
                    connection.execute(
                        """
                        INSERT INTO v8_production_effect_claims(
                            stable_action_id, action_json, owner_token,
                            state, claimed_at, completed_observation_digest,
                            provider_dispatched
                        ) VALUES (?, ?, ?, 'in_flight', ?, NULL, NULL)
                        ON CONFLICT(stable_action_id) DO NOTHING
                        """,
                        (
                            action.stable_action_id,
                            action_json,
                            owner,
                            now,
                        ),
                    )
                    row = connection.execute(
                        """
                        SELECT action_json, owner_token, state, claimed_at
                               , provider_dispatched
                          FROM v8_production_effect_claims
                         WHERE stable_action_id = ?
                        """,
                        (action.stable_action_id,),
                    ).fetchone()
                    if row is None:
                        raise ProductionCompositionError(
                            "EFFECT_READBACK_INVALID",
                            "durable effect claim disappeared during reservation",
                        )
                    (
                        recorded_action,
                        recorded_owner,
                        state,
                        claimed_at,
                        provider_dispatched,
                    ) = row
                    if recorded_action != action_json:
                        raise ProductionCompositionError(
                            "EFFECT_READBACK_INVALID",
                            "durable effect claim identity changed",
                        )
                    if (
                        state not in {"in_flight", "completed"}
                        or type(claimed_at) is not float
                        or provider_dispatched not in {None, 0, 1}
                    ):
                        raise ProductionCompositionError(
                            "EFFECT_READBACK_INVALID",
                            "durable effect claim state is malformed",
                        )
                    cached = self.readback(action)
                    if cached is not None:
                        return cached, None
                    if state == "completed":
                        raise ProductionCompositionError(
                            "EFFECT_READBACK_INVALID",
                            "completed effect claim has no effect receipt",
                        )
                    if recorded_owner == owner:
                        return None, None
                    if now - claimed_at >= _EFFECT_CLAIM_WAIT_SECONDS:
                        stale_owner = recorded_owner
                        stale_claimed_at = claimed_at
            except sqlite3.Error as error:
                raise ProductionCompositionError(
                    "EFFECT_READBACK_INVALID",
                    "effect claim could not be durably read or reserved",
                ) from error
            terminal_runtime: RuntimeProgressReceipt | None = None
            if (
                stale_owner is not None
                and stale_claimed_at is not None
                and action.kind in {"semantic_execution", "semantic_resume"}
                and provider_dispatched in {None, 1}
                and not semantic_readback_attempted
            ):
                semantic_readback_attempted = True
                terminal_runtime = self._read_terminal_runtime(action)
            if (
                stale_owner is not None
                and stale_claimed_at is not None
                and action.kind == "batch_delivery"
                and self._has_authoritative_batch_readback(action)
            ):
                try:
                    with _ledger_connection(self._store_path) as connection:
                        updated = connection.execute(
                            """
                            UPDATE v8_production_effect_claims
                               SET owner_token = ?, claimed_at = ?, state = 'in_flight'
                             WHERE stable_action_id = ?
                               AND action_json = ?
                               AND owner_token = ?
                               AND claimed_at = ?
                               AND state = 'in_flight'
                            """,
                            (
                                owner,
                                time.time(),
                                action.stable_action_id,
                                action_json,
                                stale_owner,
                                stale_claimed_at,
                            ),
                        )
                    if updated.rowcount == 1:
                        cached = self.readback(action)
                        if cached is not None:
                            return cached, None
                        return None, None
                except sqlite3.Error as error:
                    raise ProductionCompositionError(
                        "EFFECT_READBACK_INVALID",
                        "effect claim takeover could not be durably recorded",
                    ) from error
            if (
                stale_owner is not None
                and stale_claimed_at is not None
                and terminal_runtime is not None
            ):
                try:
                    with _ledger_connection(self._store_path) as connection:
                        updated = connection.execute(
                            """
                            UPDATE v8_production_effect_claims
                               SET owner_token = ?, claimed_at = ?, state = 'in_flight'
                             WHERE stable_action_id = ?
                               AND action_json = ?
                               AND owner_token = ?
                               AND claimed_at = ?
                               AND state = 'in_flight'
                            """,
                            (
                                owner,
                                time.time(),
                                action.stable_action_id,
                                action_json,
                                stale_owner,
                                stale_claimed_at,
                            ),
                        )
                    if updated.rowcount == 1:
                        cached = self.readback(action)
                        if cached is not None:
                            return cached, None
                        return None, terminal_runtime
                except sqlite3.Error as error:
                    raise ProductionCompositionError(
                        "EFFECT_READBACK_INVALID",
                        "effect claim takeover could not be durably recorded",
                    ) from error
            if time.monotonic() >= deadline:
                # A live owner that has not produced a receipt is ambiguous;
                # fail closed rather than entering the provider concurrently.
                raise ProductionCompositionError(
                    "EFFECT_EXECUTION_IN_PROGRESS",
                    "another owner has the authoritative effect claim",
                )
            time.sleep(_EFFECT_CLAIM_POLL_SECONDS)

    def _release_claim(self, action: WorkRunAction, owner: str) -> None:
        try:
            with _ledger_connection(self._store_path) as connection:
                connection.execute(
                    """
                    DELETE FROM v8_production_effect_claims
                     WHERE stable_action_id = ?
                       AND owner_token = ?
                       AND state = 'in_flight'
                    """,
                    (action.stable_action_id, owner),
                )
        except sqlite3.Error:
            # The original error is the useful failure.  If the claim cannot
            # be released, retaining it is the safe duplicate fence.
            return

    def _mark_claim_provider_dispatch(
        self,
        action: WorkRunAction,
        owner: str,
        dispatched: bool | None,
    ) -> None:
        try:
            with _ledger_connection(self._store_path) as connection:
                connection.execute(
                    """
                    UPDATE v8_production_effect_claims
                       SET provider_dispatched = ?
                     WHERE stable_action_id = ?
                       AND owner_token = ?
                       AND state = 'in_flight'
                    """,
                    (
                        None if dispatched is None else (1 if dispatched else 0),
                        action.stable_action_id,
                        owner,
                    ),
                )
        except sqlite3.Error:
            # Retain an unknown claim if its dispatch evidence cannot be
            # durably classified.  The duplicate fence is the safe outcome.
            return

    def _fault_after_record(
        self,
        action: WorkRunAction,
        observation: WorkRunEffectObservation,
        *,
        role: str,
        point: str,
        proxy_action_id: str | None = None,
    ) -> None:
        proxy = self._fault_proxy
        if proxy is None:
            return
        try:
            from scripts.v8_root_canary_fault_proxy import FaultRequest
        except ModuleNotFoundError:
            from v8_root_canary_fault_proxy import FaultRequest

        stable_action_id = proxy_action_id or action.stable_action_id
        payload_digest = digest_value(
            {
                "kind": "gwo.fault-payload.v1",
                "action": self._action_json(action),
                "observation": observation.canonical(),
            }
        )
        request = FaultRequest(
            role=role,
            point=point,
            stable_action_id=stable_action_id,
            payload_digest=payload_digest,
            command=(role, point, action.stable_action_id),
            plan_revision_digest=action.plan_revision_digest,
        )
        proxy.execute(
            request,
            run_command=lambda _command: observation.canonical(),
        )

    def campaign_proof_readback(
        self,
        campaign: CampaignHandle,
        plan_revision_digest: str,
    ) -> dict[str, object]:
        """Read the Task 3 proof projection from owner durable records."""

        if type(campaign) is not CampaignHandle or not _DIGEST_PATTERN.fullmatch(plan_revision_digest):
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "Campaign proof source identity is malformed",
            )
        semantic_ids: list[str] = []
        external_ids: list[str] = []
        batch_receipts: list[str] = []
        review_ledgers: list[str] = []
        try:
            with _ledger_connection(self._store_path) as connection:
                rows = connection.execute(
                    """
                    SELECT action_json, observation_json, observation_digest,
                           accepted_candidate_receipt_json
                      FROM v8_production_effect_receipts
                    """
                ).fetchall()
        except sqlite3.Error as error:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "Campaign proof effect ledger could not be read",
            ) from error
        for action_json, observation_json, observation_digest, accepted_json in rows:
            action_value = self._decode_recorded_action(action_json)
            if (
                action_value["repository"] != campaign.repository
                or action_value["campaign_key"] != campaign.campaign_key
                or action_value["plan_revision_digest"] != plan_revision_digest
            ):
                continue
            observation = self._decode_recorded_observation(
                observation_json,
                observation_digest,
            )
            stable_action_id = action_value["stable_action_id"]
            kind = action_value["kind"]
            if kind in {"semantic_execution", "semantic_resume"}:
                semantic_ids.append(stable_action_id)
            elif kind == "batch_delivery":
                external_ids.append(stable_action_id)
                if type(observation) is WorkRunObservation:
                    batch_receipts.append(observation.receipt_digest)
            if accepted_json is not None:
                if type(accepted_json) is not str:
                    raise ProductionCompositionError(
                        "EFFECT_READBACK_INVALID",
                        "Finding ledger receipt is malformed",
                    )
                try:
                    accepted = self._accepted_receipt_from_canonical(
                        json.loads(accepted_json)
                    )
                except (TypeError, json.JSONDecodeError) as error:
                    raise ProductionCompositionError(
                        "EFFECT_READBACK_INVALID",
                        "Finding ledger receipt is not canonical JSON",
                    ) from error
                review_ledgers.append(accepted.review_finding_ledger_digest)

        runtime = self._runtime_proof_readback(campaign, plan_revision_digest)
        return {
            "runtime_selector_digest": runtime["runtime_selector_digest"],
            "permission_binding_pairs": runtime["permission_binding_pairs"],
            "review_finding_ledger_digests": sorted(set(review_ledgers)),
            "batch_receipt_digests": sorted(set(batch_receipts)),
            "semantic_effect_ids": sorted(set(semantic_ids)),
            "external_effect_ids": sorted(set(external_ids)),
            "duplicate_effect_ids": [],
        }

    def _runtime_proof_readback(
        self,
        campaign: CampaignHandle,
        plan_revision_digest: str,
    ) -> dict[str, object]:
        gateway = self._runtime_gateways.for_campaign(campaign)
        reader = getattr(gateway, "campaign_proof_readback", None)
        if callable(reader):
            value = reader(campaign, plan_revision_digest)
            if type(value) is not dict:
                raise ProductionCompositionError(
                    "EFFECT_READBACK_INVALID",
                    "Runtime proof source is not a mapping",
                )
            return value
        refresh = getattr(gateway, "_refresh", None)
        if callable(refresh):
            refresh()
        data = getattr(gateway, "_data", None)
        if type(data) is not dict:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "Runtime Gateway has no authoritative durable proof source",
            )
        assignments: list[dict[str, object]] = []
        permission_pairs: list[tuple[str, str]] = []
        actions = data.get("actions")
        if type(actions) is not dict:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "Runtime Gateway action ledger is malformed",
            )
        for stable_action_id, record in sorted(actions.items()):
            if type(stable_action_id) is not str or type(record) is not dict:
                raise ProductionCompositionError(
                    "EFFECT_READBACK_INVALID",
                    "Runtime Gateway action ledger is malformed",
                )
            subject = record.get("subject")
            if type(subject) is not dict:
                raise ProductionCompositionError(
                    "EFFECT_READBACK_INVALID",
                    "Runtime Gateway action subject is malformed",
                )
            if (
                subject.get("repository") != campaign.repository
                or subject.get("campaign_key") != campaign.campaign_key
                or subject.get("plan_revision_digest") != plan_revision_digest
            ):
                continue
            fields = (
                "subject_digest",
                "selector",
                "configuration_source",
                "profile_digest",
                "availability_fallback_profile_digest",
                "fallback_selected",
                "assignment_digest",
            )
            if any(field not in record for field in fields):
                raise ProductionCompositionError(
                    "EFFECT_READBACK_INVALID",
                    "Runtime Gateway assignment record is incomplete",
                )
            assignments.append(
                {
                    "stable_action_id": stable_action_id,
                    **{field: record[field] for field in fields},
                }
            )
            observation = record.get("last_observation")
            if observation is None:
                continue
            if type(observation) is not dict:
                raise ProductionCompositionError(
                    "EFFECT_READBACK_INVALID",
                    "Runtime Gateway observation record is malformed",
                )
            completed = observation.get("completed_permission_response")
            if completed is None:
                continue
            if type(completed) is not dict:
                raise ProductionCompositionError(
                    "EFFECT_READBACK_INVALID",
                    "Runtime permission completion record is malformed",
                )
            request = completed.get("request")
            requested_binding = request.get("binding_ref") if type(request) is dict else None
            readback_binding = completed.get("binding_ref")
            if (
                type(requested_binding) is not str
                or not requested_binding
                or type(readback_binding) is not str
                or not readback_binding
            ):
                raise ProductionCompositionError(
                    "EFFECT_READBACK_INVALID",
                    "Runtime permission Binding readback is malformed",
                )
            permission_pairs.append((requested_binding, readback_binding))
        selector_digest = digest_value(
            {
                "kind": "gwo.runtime-selector-readback.v1",
                "repository": campaign.repository,
                "campaign_key": campaign.campaign_key,
                "plan_revision_digest": plan_revision_digest,
                "assignments": assignments,
            }
        )
        return {
            "runtime_selector_digest": selector_digest,
            "permission_binding_pairs": sorted(set(permission_pairs)),
        }

    def execute(self, action: WorkRunAction) -> WorkRunEffectObservation:
        self._validate_action(action)
        cached = self.readback(action)
        if cached is not None:
            return cached
        owner = self._claim_token(action)
        claimed, terminal_runtime = self._claim_or_wait(action, owner)
        if claimed is not None:
            return claimed
        return self._execute_claimed(
            action,
            owner,
            terminal_runtime=terminal_runtime,
        )

    def _execute_claimed(
        self,
        action: WorkRunAction,
        owner: str,
        *,
        terminal_runtime: RuntimeProgressReceipt | None = None,
    ) -> WorkRunEffectObservation:
        try:
            return self._execute_claimed_inner(
                action,
                owner,
                terminal_runtime=terminal_runtime,
            )
        except BaseException as error:
            # A deep module may explicitly prove that no provider effect was
            # dispatched.  Every other failure keeps the claim for
            # readback-first recovery and therefore cannot authorize a
            # duplicate provider call.
            if getattr(error, "provider_dispatched", None) is False:
                self._release_claim(action, owner)
            else:
                provider_dispatched = getattr(error, "provider_dispatched", None)
                self._mark_claim_provider_dispatch(
                    action,
                    owner,
                    True if provider_dispatched is True else None,
                )
            raise

    def _execute_claimed_inner(
        self,
        action: WorkRunAction,
        owner: str,
        *,
        terminal_runtime: RuntimeProgressReceipt | None = None,
    ) -> WorkRunEffectObservation:
        if action.kind in {"stale_readback", "stale_diagnosis"}:
            observation = self._runtime_stale_readbacks.read_stale(action)
            self._validate_effect_observation(action, observation)
            accepted_candidate = None
        elif action.kind in {"semantic_execution", "semantic_resume"}:
            observation, accepted_candidate = self._execute_semantic(
                action,
                terminal_runtime=terminal_runtime,
            )
        elif action.kind == "batch_delivery":
            observation = self._execute_batch(action)
            accepted_candidate = None
        else:
            raise ProductionCompositionError(
                "PRODUCTION_EFFECT_ACTION_INVALID",
                f"unsupported WorkRunAction kind: {action.kind}",
            )
        saved = self._record(
            action,
            observation,
            accepted_candidate=accepted_candidate,
            claim_owner=owner,
        )
        if action.kind in {"semantic_execution", "semantic_resume"}:
            if accepted_candidate is not None:
                self._fault_after_record(
                    action,
                    saved,
                    role="worker",
                    point="candidate_persisted_before_ack",
                )
                self._fault_after_record(
                    action,
                    saved,
                    role="review",
                    point="finding_ledger_persisted_before_ack",
                    proxy_action_id=f"{action.stable_action_id}:review",
                )
        elif action.kind == "batch_delivery":
            self._fault_after_record(
                action,
                saved,
                role="delivery",
                point="hosted_receipt_persisted_before_ack",
            )
        return saved

    @staticmethod
    def _validate_action(action: WorkRunAction) -> None:
        if (
            type(action) is not WorkRunAction
            or type(action.stable_action_id) is not str
            or not action.stable_action_id
        ):
            raise ProductionCompositionError(
                "PRODUCTION_EFFECT_ACTION_INVALID",
                "effect execution requires one exact non-empty WorkRunAction identity",
            )

    @staticmethod
    def _action_json(action: WorkRunAction) -> str:
        packet = action.stale_diagnosis_packet
        follow_up = action.stale_follow_up_kind
        return json.dumps(
            {
                "stable_action_id": action.stable_action_id,
                "repository": action.repository,
                "campaign_key": action.campaign_key,
                "plan_revision_digest": action.plan_revision_digest,
                "ticket_key": action.ticket_key,
                "kind": action.kind,
                "semantic_action_id": action.semantic_action_id,
                "work_run_key": action.work_run_key,
                "work_subject_digest": action.work_subject_digest,
                "runtime_binding_id": action.runtime_binding_id,
                "wake_ref": action.wake_ref,
                "accepted_candidate_receipt_digest": action.accepted_candidate_receipt_digest,
                "batch_delivery_request_digest": action.batch_delivery_request_digest,
                "stale_diagnosis_packet": (
                    None if packet is None else packet.canonical()
                ),
                "stale_follow_up_kind": (
                    None if follow_up is None else follow_up.value
                ),
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def _claim_action_json(cls, action: WorkRunAction) -> str:
        value = json.loads(cls._action_json(action))
        value["wake_ref"] = None
        return json.dumps(value, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _validate_subject(subject: WorkRunSubject, action: WorkRunAction) -> None:
        if (
            type(subject) is not WorkRunSubject
            or subject.repository != action.repository
            or subject.campaign_key != action.campaign_key
            or subject.plan_revision_digest != action.plan_revision_digest
            or subject.ticket_key != action.ticket_key
            or subject.work_run_key != action.work_run_key
            or subject.stable_action_id != action.stable_action_id
        ):
            raise ProductionCompositionError(
                "PRODUCTION_RUNTIME_RECEIPT_INVALID",
                "WorkRunSubject is not bound to the exact WorkRunAction",
            )

    @staticmethod
    def _validate_runtime_receipt(
        runtime: RuntimeProgressReceipt,
        subject: WorkRunSubject,
        action: WorkRunAction,
    ) -> None:
        if (
            type(runtime) is not RuntimeProgressReceipt
            or runtime.subject_digest != subject.digest
            or runtime.stable_action_id != action.stable_action_id
            or type(runtime.status) is not str
            or runtime.status not in {"running", "parked", "completed"}
            or type(runtime.receipt_digest) is not str
            or _DIGEST_PATTERN.fullmatch(runtime.receipt_digest) is None
        ):
            raise ProductionCompositionError(
                "PRODUCTION_RUNTIME_RECEIPT_INVALID",
                "RuntimeProgressReceipt is not bound to the exact subject/action",
            )
        if runtime.output_artifact_digest is not None and (
            type(runtime.output_artifact_digest) is not str
            or _DIGEST_PATTERN.fullmatch(runtime.output_artifact_digest) is None
        ):
            raise ProductionCompositionError(
                "PRODUCTION_RUNTIME_RECEIPT_INVALID",
                "Runtime output Artifact digest is invalid",
            )
        if runtime.status == "completed" and runtime.output_artifact_digest is None:
            raise ProductionCompositionError(
                "PRODUCTION_RUNTIME_RECEIPT_INVALID",
                "completed Runtime readback omitted its output Artifact digest",
            )

    @staticmethod
    def _runtime_binding_id(runtime: RuntimeProgressReceipt) -> str:
        binding_id = runtime.stable_action_id
        if type(binding_id) is not str or not binding_id:
            raise ProductionCompositionError(
                "PRODUCTION_RUNTIME_RECEIPT_INVALID",
                "trusted Runtime readback omitted its stable action identity",
            )
        return binding_id

    def _read_terminal_runtime(
        self,
        action: WorkRunAction,
    ) -> RuntimeProgressReceipt | None:
        """Read a terminal Runtime outcome without entering CandidateGate."""

        try:
            subject = self._work_run_subjects.for_action(action)
            self._validate_subject(subject, action)
            gateway = self._runtime_gateways.for_campaign(
                CampaignHandle(action.repository, action.campaign_key)
            )
            runtime = gateway.progress(subject, wake_cursor=action.wake_ref)
            self._validate_runtime_receipt(runtime, subject, action)
        except Exception:
            # A stale claim is still ambiguous unless the Gateway proves the
            # exact terminal outcome.  In particular, readback failures must
            # never turn into a second provider dispatch.
            return None
        if runtime.status != "completed" or runtime.output_artifact_digest is None:
            return None
        return runtime

    def _execute_semantic(
        self,
        action: WorkRunAction,
        *,
        terminal_runtime: RuntimeProgressReceipt | None = None,
    ) -> tuple[WorkRunObservation, AcceptedCandidateReceipt | None]:
        subject = self._work_run_subjects.for_action(action)
        self._validate_subject(subject, action)
        if terminal_runtime is None:
            gateway = self._runtime_gateways.for_campaign(
                CampaignHandle(action.repository, action.campaign_key)
            )
            runtime = gateway.progress(subject, wake_cursor=action.wake_ref)
        else:
            runtime = terminal_runtime
        self._validate_runtime_receipt(runtime, subject, action)
        if runtime.status in {"running", "parked"}:
            return self._observation_from_runtime(runtime, action), None
        assert runtime.output_artifact_digest is not None
        reported_reference = self._candidate_references.read(
            runtime.output_artifact_digest,
            subject=subject,
        )
        if type(reported_reference) is not str or not reported_reference:
            raise ProductionCompositionError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "Candidate reference readback is not exact non-empty text",
            )
        parent = self._candidate_parents.for_action(action, subject)
        if type(parent) is not CandidateGateParent or parent.runtime_subject != subject:
            raise ProductionCompositionError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "CandidateGate parent is not bound to the exact WorkRunSubject",
            )
        result = self._candidate_gate.gate_candidate(parent, reported_reference)
        if type(result) is not CandidateGateResult:
            raise ProductionCompositionError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "CandidateGate did not return an exact CandidateGateResult",
            )
        binding_id = self._runtime_binding_id(runtime)
        if result.status in {
            CandidateGateStatus.REVIEW_ACCEPTED,
            CandidateGateStatus.REPAIR_ACCEPTED,
        }:
            candidate = result.candidate_receipt
            accepted = result.accepted_candidate_receipt
            if candidate is None or accepted is None:
                raise ProductionCompositionError(
                    "CANDIDATE_GATE_READBACK_INVALID",
                    "an accepted CandidateGateResult lacks both exact receipt values",
                )
            self._validate_candidate_pair(action, candidate, accepted)
            return (
                WorkRunObservation(
                    phase="accepted_awaiting_delivery",
                    stable_action_id=action.stable_action_id,
                    runtime_binding_id=binding_id,
                    receipt_digest=candidate.digest,
                    candidate_receipt=candidate,
                    accepted_candidate_receipt_digest=accepted.digest,
                    candidate_diff_record_digest=accepted.diff_record_digest,
                    result_digest=None,
                    result_integrity=None,
                    next_check_at=getattr(runtime, "next_check_at", None),
                ),
                accepted,
            )
        if result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED:
            receipt = result.plan_invalidation_receipt
            if receipt is None:
                raise ProductionCompositionError(
                    "CANDIDATE_GATE_READBACK_INVALID",
                    "Plan Invalidation result lacks its authoritative receipt",
                )
            try:
                invalidation = PlanInvalidationObservation.from_receipt(receipt)
            except Exception as error:
                raise ProductionCompositionError(
                    "PLAN_INVALIDATION_READBACK_INVALID",
                    "CandidateGate Plan Invalidation receipt is not exact",
                ) from error
            self._validate_plan_invalidation(action, invalidation, binding_id)
            return (
                WorkRunObservation(
                    phase="quiescent",
                    stable_action_id=action.stable_action_id,
                    runtime_binding_id=binding_id,
                    receipt_digest=invalidation.digest,
                    plan_invalidation=invalidation,
                ),
                None,
            )
        phase = {
            CandidateGateStatus.REPAIR_REQUIRED: "repair",
            CandidateGateStatus.REPAIR_REJECTED: "decision",
            CandidateGateStatus.ORDINARY_REJECTED: "decision",
            CandidateGateStatus.DECISION_REQUIRED: "decision",
        }.get(result.status)
        if phase is None:
            raise ProductionCompositionError(
                "CANDIDATE_GATE_READBACK_INVALID",
                f"unknown CandidateGateResult status: {result.status}",
            )
        evidence_digests = tuple(
            sorted(
                digest
                for item in result.evidence
                for digest in (getattr(item, "digest", None),)
                if digest is not None
            )
        )
        return (
            WorkRunObservation(
                phase=phase,
                stable_action_id=action.stable_action_id,
                runtime_binding_id=binding_id,
                receipt_digest=digest_bytes(
                    json.dumps(
                        {
                            "action": action.stable_action_id,
                            "status": result.status.value,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ),
                evidence_digests=evidence_digests,
            ),
            None,
        )

    def _prepare_batch_action(
        self,
        action: WorkRunAction,
    ) -> tuple[AcceptedCandidateReceipt, BatchDeliveryRequest, BatchDeliveryAction]:
        accepted_digest = action.accepted_candidate_receipt_digest
        if not accepted_digest:
            raise ProductionCompositionError(
                "BATCH_ACCEPTED_RECEIPT_MISSING",
                "batch delivery requires the exact accepted-Candidate digest",
            )
        accepted_candidate = self._read_accepted_candidate_receipt(
            action,
            accepted_digest,
        )
        subject = self._work_run_subjects.for_action(action)
        self._validate_subject(subject, action)
        request = self._batch_requests.for_action(
            action,
            subject,
            (accepted_candidate,),
        )
        self._validate_batch_request(request, action, accepted_candidate)
        batch_action = self._batch_integrator.prepare(request)
        if (
            type(batch_action) is not BatchDeliveryAction
            or batch_action.stable_action_id != action.stable_action_id
            or batch_action.request_digest != request.request_digest
        ):
            raise ProductionCompositionError(
                "BATCH_READBACK_INVALID",
                "BatchIntegrator prepare returned a changed Batch action identity",
            )
        return accepted_candidate, request, batch_action

    def _has_authoritative_batch_readback(self, action: WorkRunAction) -> bool:
        """Probe the owner journal without entering the provider boundary."""

        try:
            _accepted_candidate, _request, batch_action = self._prepare_batch_action(action)
            batch_observation = self._batch_integrator.readback(batch_action)
            if batch_observation is None:
                return False
            self._validate_batch_observation(batch_action, batch_observation)
            return True
        except Exception:
            return False

    def _execute_batch(self, action: WorkRunAction) -> WorkRunObservation:
        if self._replayed_effect is not None and self._replayed_effect[1] in {
            "semantic_execution",
            "semantic_resume",
        }:
            raise _ProductionReplayDeferred()
        accepted_candidate, request, batch_action = self._prepare_batch_action(action)
        batch_observation = self._batch_integrator.readback(batch_action)
        if batch_observation is None:
            executed_observation = self._batch_integrator.execute(batch_action)
            batch_observation = self._batch_integrator.readback(batch_action)
            if (
                type(executed_observation) is not BatchDeliveryObservation
                or type(batch_observation) is not BatchDeliveryObservation
                or batch_observation != executed_observation
            ):
                raise ProductionCompositionError(
                    "BATCH_READBACK_INVALID",
                    "Batch terminal readback did not exactly match execute",
                )
        if type(batch_observation) is not BatchDeliveryObservation:
            raise ProductionCompositionError(
                "BATCH_READBACK_INVALID",
                "BatchIntegrator did not return an exact Batch observation",
            )
        self._validate_batch_observation(batch_action, batch_observation)
        if batch_observation.phase == "complete":
            try:
                proof = ResultIntegrityProof.from_batch_observation(
                    batch_action,
                    request,
                    batch_observation,
                    accepted_candidate,
                )
                proof.validate_for(action, request.target.target_branch)
            except Exception as error:
                raise ProductionCompositionError(
                    "RESULT_INTEGRITY_INVALID",
                    "complete Batch readback did not prove an exact Result",
                ) from error
            candidate = self._read_candidate_receipt(action)
            return WorkRunObservation(
                phase="completed",
                stable_action_id=action.stable_action_id,
                runtime_binding_id=action.runtime_binding_id,
                receipt_digest=batch_observation.receipt_digest,
                candidate_receipt=candidate,
                accepted_candidate_receipt_digest=accepted_candidate.digest,
                candidate_diff_record_digest=accepted_candidate.diff_record_digest,
                delivery_receipt_digest=batch_observation.receipt_digest,
                result_digest=proof.result_digest,
                evidence_digests=proof.evidence_digests,
                result_integrity=proof,
            )
        return self._observation_from_batch(batch_observation, action)

    @staticmethod
    def _validate_batch_request(
        request: BatchDeliveryRequest,
        action: WorkRunAction,
        accepted_candidate: AcceptedCandidateReceipt,
        *,
        require_request_digest: bool = True,
    ) -> None:
        if (
            type(request) is not BatchDeliveryRequest
            or request.stable_action_id != action.stable_action_id
            or request.repository != action.repository
            or request.campaign_key != action.campaign_key
            or request.plan_revision_digest != action.plan_revision_digest
            or accepted_candidate not in request.accepted_candidates
        ):
            raise ProductionCompositionError(
                "BATCH_READBACK_INVALID",
                "Batch request is not bound to the exact accepted Candidate and Work Run",
            )
        if require_request_digest and (
            type(action.batch_delivery_request_digest) is not str
            or action.batch_delivery_request_digest != request.request_digest
        ):
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "Batch request identity changed for the stable action",
            )

    @staticmethod
    def _validate_batch_observation(
        action: BatchDeliveryAction,
        observation: BatchDeliveryObservation,
    ) -> None:
        if (
            type(observation) is not BatchDeliveryObservation
            or observation.stable_action_id != action.stable_action_id
            or observation.batch_id != action.batch_id
            or observation.batch_sha != action.batch_sha
            or type(observation.members) is not tuple
            or any(type(member) is not MemberDeliveryObservation for member in observation.members)
            or tuple(member.ticket_key for member in observation.members)
            != tuple(action.member_ticket_keys)
        ):
            raise ProductionCompositionError(
                "BATCH_READBACK_INVALID",
                "Batch observation changed its exact action or member identity",
            )
        if observation.phase != "complete":
            try:
                observation.canonical()
            except Exception as error:
                raise ProductionCompositionError(
                    "BATCH_READBACK_INVALID",
                    "Batch observation is not an exact canonical readback",
                ) from error

    @staticmethod
    def _validate_candidate_pair(
        action: WorkRunAction,
        candidate: CandidateReceipt,
        accepted: AcceptedCandidateReceipt,
    ) -> None:
        if (
            type(candidate) is not CandidateReceipt
            or type(accepted) is not AcceptedCandidateReceipt
            or candidate.repository != action.repository
            or candidate.campaign_key != action.campaign_key
            or candidate.plan_revision_digest != action.plan_revision_digest
            or candidate.ticket_key != action.ticket_key
            or candidate.work_run_key != action.work_run_key
            or candidate.runtime_subject_digest != action.work_subject_digest
            or accepted.repository != action.repository
            or accepted.campaign_key != action.campaign_key
            or accepted.plan_revision_digest != action.plan_revision_digest
            or accepted.ticket_key != action.ticket_key
            or accepted.work_run_key != action.work_run_key
            or accepted.base_sha != candidate.base_commit_oid
            or accepted.base_tree_oid != candidate.base_tree_oid
            or accepted.candidate_sha != candidate.candidate_commit_oid
            or accepted.candidate_tree_oid != candidate.candidate_tree_oid
            or accepted.candidate_receipt_digest != candidate.digest
            or accepted.diff_schema_version != candidate.diff_schema_version
            or accepted.diff_record_digest != candidate.diff_record_digest
            or accepted.authority_subtree_digest != candidate.authority_subtree_digest
        ):
            raise ProductionCompositionError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "CandidateGate receipts are not bound to the exact Work Run",
            )

    @staticmethod
    def _validate_plan_invalidation(
        action: WorkRunAction,
        observation: PlanInvalidationObservation,
        binding_id: str,
    ) -> None:
        if (
            type(observation) is not PlanInvalidationObservation
            or observation.repository != action.repository
            or observation.campaign_key != action.campaign_key
            or observation.plan_revision_digest != action.plan_revision_digest
            or observation.ticket_key != action.ticket_key
            or observation.work_run_key != action.work_run_key
            or observation.runtime_binding_id != binding_id
        ):
            raise ProductionCompositionError(
                "PLAN_INVALIDATION_READBACK_INVALID",
                "Plan Invalidation observation is not bound to the exact Work Run",
            )

    @staticmethod
    def _observation_from_runtime(
        runtime: RuntimeProgressReceipt,
        action: WorkRunAction,
    ) -> WorkRunObservation:
        phase = {"running": "running", "parked": "parked"}.get(runtime.status)
        if phase is None:
            raise ProductionCompositionError(
                "PRODUCTION_RUNTIME_RECEIPT_INVALID",
                "non-terminal Runtime status is outside the closed mapping",
            )
        return WorkRunObservation(
            phase=phase,
            stable_action_id=action.stable_action_id,
            runtime_binding_id=ProductionWorkRunEffects._runtime_binding_id(runtime),
            receipt_digest=runtime.receipt_digest,
            next_check_at=getattr(runtime, "next_check_at", None),
        )

    @staticmethod
    def _observation_from_batch(
        batch_observation: BatchDeliveryObservation,
        action: WorkRunAction,
    ) -> WorkRunObservation:
        phase = {
            "running": "wait",
            "wait": "wait",
            "decision": "decision",
            "blocked": "blocked",
        }.get(batch_observation.phase)
        if phase is None:
            raise ProductionCompositionError(
                "BATCH_READBACK_INVALID",
                "Batch phase is not a non-terminal closed mapping",
            )
        return WorkRunObservation(
            phase=phase,
            stable_action_id=action.stable_action_id,
            runtime_binding_id=action.runtime_binding_id,
            receipt_digest=batch_observation.receipt_digest,
            reason=batch_observation.reason,
            evidence_digests=tuple(
                sorted(
                    digest
                    for member in batch_observation.members
                    for digest in member.evidence_digests
                )
            ),
        )

    @staticmethod
    def _decode_effect_observation(
        payload: dict[str, object],
    ) -> WorkRunEffectObservation:
        kind = payload.get("kind")
        if kind == "work_run_observation.v1":
            return WorkRunObservation.from_canonical(payload)
        if kind == "stale_binding_observation.v1":
            data = dict(payload)
            candidate = data.get("candidate_receipt")
            if isinstance(candidate, dict):
                data["candidate_receipt"] = CandidateReceipt.from_canonical(candidate)
            return StaleBindingObservation.from_canonical(data)
        if kind == "stale_diagnosis_observation.v1":
            return StaleDiagnosisObservation.from_canonical(payload)
        raise ProductionCompositionError(
            "EFFECT_READBACK_INVALID",
            "effect ledger row has no exact member of the closed #113 union",
        )

    @staticmethod
    def _validate_effect_observation(
        action: WorkRunAction,
        observation: WorkRunEffectObservation,
    ) -> None:
        expected_type: type[object] | None = {
            "stale_readback": StaleBindingObservation,
            "stale_diagnosis": StaleDiagnosisObservation,
            "semantic_execution": WorkRunObservation,
            "semantic_resume": WorkRunObservation,
            "batch_delivery": WorkRunObservation,
        }.get(action.kind)
        if expected_type is None or type(observation) is not expected_type:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect observation kind does not match the stable action kind",
            )
        if observation.stable_action_id != action.stable_action_id:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect observation changed its stable action identity",
            )
        observation_binding = getattr(observation, "runtime_binding_id", None)
        if action.runtime_binding_id is not None and observation_binding != action.runtime_binding_id:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect observation changed its Runtime Binding identity",
            )
        if action.kind == "batch_delivery":
            if (
                not action.accepted_candidate_receipt_digest
                or observation.accepted_candidate_receipt_digest
                != action.accepted_candidate_receipt_digest
            ):
                raise ProductionCompositionError(
                    "EFFECT_READBACK_INVALID",
                    "Batch observation is not bound to its accepted Candidate",
                )
        if action.kind in {"stale_readback", "stale_diagnosis"} and (
            not action.runtime_binding_id
            or observation_binding != action.runtime_binding_id
        ):
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "stale observation is not bound to its Runtime Binding",
            )
        if type(observation) is WorkRunObservation:
            allowed_phases = {
                "semantic_execution": {
                    "running",
                    "parked",
                    "repair",
                    "decision",
                    "quiescent",
                    "accepted_awaiting_delivery",
                },
                "semantic_resume": {
                    "running",
                    "parked",
                    "repair",
                    "decision",
                    "quiescent",
                    "accepted_awaiting_delivery",
                },
                "batch_delivery": {"wait", "decision", "blocked", "completed"},
            }[action.kind]
            if observation.phase not in allowed_phases:
                raise ProductionCompositionError(
                    "EFFECT_READBACK_INVALID",
                    "effect observation phase is outside its action mapping",
                )
            if observation.phase == "quiescent":
                if type(observation.plan_invalidation) is not PlanInvalidationObservation:
                    raise ProductionCompositionError(
                        "EFFECT_READBACK_INVALID",
                        "quiescent effect observation omitted its Plan Invalidation",
                    )
                ProductionWorkRunEffects._validate_plan_invalidation(
                    action,
                    observation.plan_invalidation,
                    observation_binding,
                )
            if observation.phase == "completed":
                proof = observation.result_integrity
                if proof is None or observation.result_digest != proof.result_digest:
                    raise ProductionCompositionError(
                        "EFFECT_READBACK_INVALID",
                        "completed effect observation is not bound to its Result proof",
                    )
                if (
                    observation.delivery_receipt_digest
                    != proof.batch_delivery_receipt_digest
                    or observation.accepted_candidate_receipt_digest
                    != action.accepted_candidate_receipt_digest
                ):
                    raise ProductionCompositionError(
                        "EFFECT_READBACK_INVALID",
                        "completed effect observation changed its delivery identity",
                    )
                try:
                    proof.validate_for(action, proof.target_branch)
                except Exception as error:
                    raise ProductionCompositionError(
                        "EFFECT_READBACK_INVALID",
                        "completed effect observation carries an invalid Result proof",
                    ) from error

    @staticmethod
    def _accepted_receipt_from_canonical(
        value: dict[str, object],
    ) -> AcceptedCandidateReceipt:
        expected = {
            "kind",
            "repository",
            "campaign_key",
            "plan_revision_digest",
            "target_branch",
            "ticket_key",
            "work_run_key",
            "integration_node_key",
            "accepted_sequence",
            "base_sha",
            "base_tree_oid",
            "candidate_sha",
            "candidate_tree_oid",
            "candidate_receipt_digest",
            "diff_schema_version",
            "diff_record_digest",
            "authority_subtree_digest",
            "policy_witness_digest",
            "review_subject_digest",
            "assurance",
            "assurance_requirement_digest",
            "check_environment_digest",
            "delivery_identity_digest",
            "interaction_keys",
            "protected_surfaces",
            "gitlink_change",
            "evidence_digests",
            "review_finding_ledger_digest",
            "receipt_digest",
        }
        if (
            type(value) is not dict
            or set(value) != expected
            or value.get("kind") != "accepted_candidate_receipt.v1"
            or type(value.get("interaction_keys")) is not list
            or type(value.get("protected_surfaces")) is not list
            or type(value.get("evidence_digests")) is not list
        ):
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "stored AcceptedCandidateReceipt has an unknown field set",
            )
        try:
            interaction_keys = tuple(
                InteractionKey(
                    item["namespace"],
                    item["value"],
                    InteractionClassification(item["classification"]),
                )
                for item in value["interaction_keys"]
                if type(item) is dict and set(item) == {
                    "namespace",
                    "value",
                    "classification",
                }
            )
            if len(interaction_keys) != len(value["interaction_keys"]):
                raise ValueError("interaction key schema is not exact")
            payload = dict(value)
            payload.pop("kind")
            stored_digest = payload.pop("receipt_digest")
            payload["interaction_keys"] = interaction_keys
            payload["protected_surfaces"] = tuple(payload["protected_surfaces"])
            payload["evidence_digests"] = tuple(payload["evidence_digests"])
            receipt = AcceptedCandidateReceipt(**payload)
        except Exception as error:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "stored AcceptedCandidateReceipt is not exact",
            ) from error
        if receipt.digest != stored_digest or receipt.canonical() != value:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "stored AcceptedCandidateReceipt digest changed",
            )
        return receipt

    def _validate_stored_accepted_receipt(
        self,
        action: WorkRunAction,
        observation: WorkRunEffectObservation,
        accepted_json: object,
    ) -> None:
        if type(accepted_json) is not str:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "stored AcceptedCandidateReceipt is not JSON",
            )
        try:
            payload = json.loads(accepted_json)
        except json.JSONDecodeError as error:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "stored AcceptedCandidateReceipt is not JSON",
            ) from error
        receipt = self._accepted_receipt_from_canonical(payload)
        if (
            type(observation) is not WorkRunObservation
            or observation.accepted_candidate_receipt_digest != receipt.digest
            or observation.candidate_receipt is None
            or observation.candidate_receipt.digest != receipt.candidate_receipt_digest
            or observation.candidate_diff_record_digest != receipt.diff_record_digest
            or receipt.repository != action.repository
            or receipt.campaign_key != action.campaign_key
            or receipt.plan_revision_digest != action.plan_revision_digest
            or receipt.ticket_key != action.ticket_key
            or receipt.work_run_key != action.work_run_key
        ):
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "stored AcceptedCandidateReceipt is not bound to the effect observation",
            )

    @staticmethod
    def _decode_recorded_action(action_json: object) -> dict[str, object]:
        expected = {
            "stable_action_id",
            "repository",
            "campaign_key",
            "plan_revision_digest",
            "ticket_key",
            "kind",
            "semantic_action_id",
            "work_run_key",
            "work_subject_digest",
            "runtime_binding_id",
            "wake_ref",
            "accepted_candidate_receipt_digest",
            "batch_delivery_request_digest",
            "stale_diagnosis_packet",
            "stale_follow_up_kind",
        }
        if type(action_json) is not str:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect ledger action identity is not JSON",
            )
        try:
            payload = json.loads(action_json)
        except json.JSONDecodeError as error:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect ledger action identity is not JSON",
            ) from error
        if (
            type(payload) is not dict
            or set(payload) != expected
            or json.dumps(payload, separators=(",", ":"), sort_keys=True)
            != action_json
        ):
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect ledger action identity is not exact",
            )
        return payload

    @staticmethod
    def _decode_recorded_observation(
        observation_json: object,
        observation_digest: object,
    ) -> WorkRunEffectObservation:
        if (
            type(observation_json) is not str
            or type(observation_digest) is not str
            or digest_bytes(observation_json.encode("utf-8")) != observation_digest
        ):
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect ledger observation digest changed",
            )
        try:
            payload = json.loads(observation_json)
        except json.JSONDecodeError as error:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect ledger observation is not JSON",
            ) from error
        if type(payload) is not dict:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect ledger observation is not a JSON object",
            )
        return ProductionWorkRunEffects._decode_effect_observation(payload)

    def _record(
        self,
        action: WorkRunAction,
        observation: WorkRunEffectObservation,
        *,
        accepted_candidate: AcceptedCandidateReceipt | None = None,
        claim_owner: str,
    ) -> WorkRunEffectObservation:
        self._validate_effect_observation(action, observation)
        if accepted_candidate is not None:
            if type(observation) is not WorkRunObservation:
                raise ProductionCompositionError(
                    "CANDIDATE_GATE_READBACK_INVALID",
                    "AcceptedCandidateReceipt cannot accompany a stale observation",
                )
            if (
                type(accepted_candidate) is not AcceptedCandidateReceipt
                or observation.accepted_candidate_receipt_digest
                != accepted_candidate.digest
            ):
                raise ProductionCompositionError(
                    "CANDIDATE_GATE_READBACK_INVALID",
                    "AcceptedCandidateReceipt is not bound to the observation",
                )
        action_json = self._action_json(action)
        claim_action_json = self._claim_action_json(action)
        observation_json = json.dumps(
            observation.canonical(),
            separators=(",", ":"),
            sort_keys=True,
        )
        observation_digest = digest_bytes(observation_json.encode("utf-8"))
        accepted_json = (
            None
            if accepted_candidate is None
            else json.dumps(
                accepted_candidate.canonical(),
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        try:
            with _ledger_connection(self._store_path) as connection:
                connection.execute(
                    """
                    INSERT INTO v8_production_effect_receipts(
                        stable_action_id, action_json, observation_json,
                        observation_digest, accepted_candidate_receipt_json
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(stable_action_id) DO NOTHING
                    """,
                    (
                        action.stable_action_id,
                        action_json,
                        observation_json,
                        observation_digest,
                        accepted_json,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT action_json, observation_json, observation_digest,
                           accepted_candidate_receipt_json
                      FROM v8_production_effect_receipts
                     WHERE stable_action_id = ?
                    """,
                    (action.stable_action_id,),
                ).fetchone()
                if (
                    row is None
                    or row[0] != action_json
                    or row[1] != observation_json
                    or row[2] != observation_digest
                    or row[3] != accepted_json
                ):
                    raise ProductionCompositionError(
                        "EFFECT_READBACK_INVALID",
                        "effect ledger duplicate observation changed its identity",
                    )
                updated = connection.execute(
                    """
                    UPDATE v8_production_effect_claims
                       SET state = 'completed',
                           owner_token = ?,
                           completed_observation_digest = ?
                     WHERE stable_action_id = ? AND action_json = ?
                       AND owner_token = ? AND state = 'in_flight'
                    """,
                    (
                        claim_owner,
                        observation_digest,
                        action.stable_action_id,
                        claim_action_json,
                        claim_owner,
                    ),
                )
                if updated.rowcount != 1:
                    raise ProductionCompositionError(
                        "EFFECT_READBACK_INVALID",
                        "effect ledger observation had no authoritative claim",
                    )
        except sqlite3.Error as error:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect ledger observation could not be durably recorded",
            ) from error
        previous_suppression = self._suppress_replay_tracking
        self._suppress_replay_tracking = True
        try:
            saved = self.readback(action)
        finally:
            self._suppress_replay_tracking = previous_suppression
        if saved is None:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "effect observation disappeared after its durable insert",
            )
        return saved

    def _read_accepted_candidate_receipt(
        self,
        action: WorkRunAction,
        expected_digest: str,
    ) -> AcceptedCandidateReceipt:
        try:
            with _ledger_connection(self._store_path) as connection:
                rows = connection.execute(
                    """
                    SELECT action_json, observation_json, observation_digest,
                           accepted_candidate_receipt_json
                      FROM v8_production_effect_receipts
                     WHERE accepted_candidate_receipt_json IS NOT NULL
                    """
                ).fetchall()
        except sqlite3.Error as error:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "accepted Candidate receipt ledger could not be read",
            ) from error
        matching: list[AcceptedCandidateReceipt] = []
        for action_json, observation_json, observation_digest, receipt_json in rows:
            recorded_action = self._decode_recorded_action(action_json)
            if (
                recorded_action.get("repository") != action.repository
                or recorded_action.get("campaign_key") != action.campaign_key
                or recorded_action.get("plan_revision_digest") != action.plan_revision_digest
                or recorded_action.get("ticket_key") != action.ticket_key
                or recorded_action.get("work_run_key") != action.work_run_key
                or recorded_action.get("kind")
                not in {"semantic_execution", "semantic_resume"}
            ):
                continue
            try:
                observation = self._decode_recorded_observation(
                    observation_json,
                    observation_digest,
                )
                if (
                    type(observation) is not WorkRunObservation
                    or observation.accepted_candidate_receipt_digest is None
                ):
                    raise ProductionCompositionError(
                        "EFFECT_READBACK_INVALID",
                        "stored AcceptedCandidateReceipt lacks its exact observation",
                    )
                receipt = self._accepted_receipt_from_canonical(
                    json.loads(receipt_json)
                )
                if (
                    observation.accepted_candidate_receipt_digest != receipt.digest
                    or observation.candidate_receipt is None
                    or receipt.candidate_receipt_digest
                    != observation.candidate_receipt.digest
                    or receipt.diff_record_digest
                    != observation.candidate_receipt.diff_record_digest
                ):
                    raise ProductionCompositionError(
                        "EFFECT_READBACK_INVALID",
                        "stored AcceptedCandidateReceipt is not bound to its Candidate",
                    )
            except (TypeError, json.JSONDecodeError) as error:
                raise ProductionCompositionError(
                    "EFFECT_READBACK_INVALID",
                    "stored AcceptedCandidateReceipt is not JSON",
                ) from error
            if receipt.digest != expected_digest:
                raise ProductionCompositionError(
                    "EFFECT_READBACK_INVALID",
                    "stored AcceptedCandidateReceipt digest differs from the action",
                )
            matching.append(receipt)
        if not matching:
            raise ProductionCompositionError(
                "BATCH_ACCEPTED_RECEIPT_MISSING",
                "no stored CandidateGate-owned AcceptedCandidateReceipt matches the batch",
            )
        if any(receipt != matching[0] for receipt in matching[1:]):
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "multiple stored AcceptedCandidateReceipts disagree for the Work Run",
            )
        return matching[0]

    def _read_candidate_receipt(self, action: WorkRunAction) -> CandidateReceipt:
        try:
            with _ledger_connection(self._store_path) as connection:
                rows = connection.execute(
                    """
                    SELECT action_json, observation_json, observation_digest
                      FROM v8_production_effect_receipts
                    """
                ).fetchall()
        except sqlite3.Error as error:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "Candidate receipt ledger could not be read",
            ) from error
        candidates: list[CandidateReceipt] = []
        for action_json, observation_json, observation_digest in rows:
            recorded_action = self._decode_recorded_action(action_json)
            if (
                recorded_action.get("repository") != action.repository
                or recorded_action.get("campaign_key") != action.campaign_key
                or recorded_action.get("plan_revision_digest") != action.plan_revision_digest
                or recorded_action.get("ticket_key") != action.ticket_key
                or recorded_action.get("work_run_key") != action.work_run_key
                or recorded_action.get("kind")
                not in {"semantic_execution", "semantic_resume"}
            ):
                continue
            try:
                observation = self._decode_recorded_observation(
                    observation_json,
                    observation_digest,
                )
                if type(observation) is not WorkRunObservation:
                    raise ProductionCompositionError(
                        "EFFECT_READBACK_INVALID",
                        "stored Candidate receipt observation is not exact",
                    )
            except Exception as error:
                raise ProductionCompositionError(
                    "EFFECT_READBACK_INVALID",
                    "stored Candidate receipt observation is not exact",
                ) from error
            if observation.candidate_receipt is not None:
                candidates.append(observation.candidate_receipt)
        if not candidates:
            raise ProductionCompositionError(
                "CANDIDATE_RECEIPT_MISSING",
                "batch delivery has no exact persisted shared CandidateReceipt",
            )
        if any(candidate != candidates[0] for candidate in candidates[1:]):
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "multiple persisted CandidateReceipts disagree for the Work Run",
            )
        return candidates[0]
