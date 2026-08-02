"""The V8 Campaign execution state machine.

``ExecutionKernel`` is deliberately the only post-activation workflow driver.
It consumes PlanControl's read-only active Campaign proof, persists an intent
for each bounded effect, and asks an owning deep module to read that exact
effect back before it is executed or retried.  It owns neither Ticket claims
nor Runtime/provider policy.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Mapping, Protocol

from ._canonical import CanonicalJsonError, digest_bytes, digest_value, load_canonical_json
from .plan_control import (
    ActivePlanReadback,
    CampaignHandle,
    PlanInvalidationClassification,
    PlanInvalidationDisposition,
)


_DIGEST_LENGTH = 64
_TERMINAL_PHASES = frozenset({"completed"})
_SLOT_PHASES = frozenset({"running", "candidate_checks", "formal_review", "repair"})
# A quiescent Work Run has been authoritatively stopped by Plan Invalidation.
# It retains diagnostic identity and performs no further Worker/Candidate/
# Review/Repair/delivery effect, but it does not hold a Worker Slot.
_QUIESCENT_PHASES = frozenset({"quiescent"})
_KERNEL_LOCKS_GUARD = threading.Lock()
_KERNEL_LOCKS: dict[str, threading.RLock] = {}


def _has_revision_identity_facts(
    plan: Mapping[str, Any], work_item: Mapping[str, Any]
) -> bool:
    """Return whether this PlanSpec has the Task 3 semantic identity shape."""

    campaign = plan.get("campaign")
    return (
        type(campaign) is dict
        and "source" in campaign
        and "authority" in campaign
        and "target_branch" in plan
        and "policy" in plan
        and all(
            field in work_item
            for field in (
                "source",
                "contract",
                "capabilities",
                "authority",
            )
        )
    )


def _legacy_work_subject_digest(
    plan: Mapping[str, Any], work_item: Mapping[str, Any]
) -> str:
    campaign = plan.get("campaign")
    if type(campaign) is not dict:
        campaign = {}
    return digest_value(
        {
            "kind": "gwo.work-subject.v1",
            "repository": plan["repository"],
            "campaign_key": campaign.get("key"),
            "target_branch": plan.get("target_branch"),
            "campaign_source": campaign.get("source"),
            "campaign_authority": campaign.get("authority"),
            "policy": plan.get("policy"),
            "ticket_key": work_item["key"],
            "source": work_item.get("source"),
            "contract": work_item.get("contract"),
            "depends_on": list(work_item.get("depends_on", ())),
            "exclusive_resources": list(work_item.get("exclusive_resources", ())),
            "capabilities": list(work_item.get("capabilities", ())),
            "authority": work_item.get("authority"),
        }
    )


def _work_subject_digest_for_kernel(
    plan: Mapping[str, Any], work_item: Mapping[str, Any]
) -> str:
    if not _has_revision_identity_facts(plan, work_item):
        return _legacy_work_subject_digest(plan, work_item)
    return work_subject_digest(plan, work_item)


def _target_facts_digest_for_kernel(plan: Mapping[str, Any]) -> str:
    campaign = plan.get("campaign")
    if type(campaign) is not dict or "source" not in campaign:
        return digest_value(
            {
                "kind": "gwo.target-facts.v1",
                "repository": plan["repository"],
                "target_branch": plan.get("target_branch"),
                "campaign_source": campaign.get("source")
                if type(campaign) is dict
                else None,
            }
        )
    return target_facts_digest(plan)


def _work_run_key_for_kernel(
    plan: Mapping[str, Any], work_item: Mapping[str, Any], subject_digest: str
) -> str:
    if _has_revision_identity_facts(plan, work_item):
        return work_run_key(work_item["key"], subject_digest)
    return f"work-run:{work_item['key']}"


class ExecutionKernelError(RuntimeError):
    """A named fail-closed ExecutionKernel outcome."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


from .revision_identity import (
    AcceptedResultBinding,
    can_preserve_result,
    target_facts_digest,
    work_run_key,
    work_subject_digest,
)

class CampaignStatus(str, Enum):
    COMPLETE = "Complete"
    RUNNING = "Running"
    DECISION = "Decision"
    WAIT = "Wait"
    BLOCKED = "Blocked"


@dataclass(frozen=True)
class CampaignOutcome:
    status: CampaignStatus
    reason: str


@dataclass(frozen=True)
class WorkRunSummary:
    ticket_key: str
    phase: str
    slot_held: bool
    reason: str | None
    next_check_at: str | None
    plan_invalidation: PlanInvalidationDiagnostic | None = None
    work_run_key: str = ""
    runtime_binding_id: str | None = None
    claim_state: str = "unclaimed"
    exclusive_resources: tuple[str, ...] = ()
    work_subject_digest: str = ""
    candidate_identity: str | None = None
    result_digest: str | None = None
    evidence_digests: tuple[str, ...] = ()


@dataclass(frozen=True)
class Diagnostics:
    status: CampaignStatus
    reason: str
    campaign: CampaignHandle
    plan_revision_digest: str
    worker_slots: dict[str, int]
    work_runs: tuple[WorkRunSummary, ...]
    outstanding_effect_ids: tuple[str, ...]
    invalidation_classification: PlanInvalidationClassification | None = None

    @property
    def plan_invalidation_classification(self) -> PlanInvalidationClassification | None:
        """Compatibility spelling for the Campaign-level readback."""

        return self.invalidation_classification


@dataclass(frozen=True)
class ExecutionKernelConfiguration:
    """Host-owned capacity configuration; it is intentionally outside PlanSpec."""

    host_worker_slots: int = 4
    repository_worker_slots: dict[str, int] | None = None

    def __post_init__(self) -> None:
        overrides = self.repository_worker_slots
        if overrides is not None and type(overrides) is not dict:
            raise ExecutionKernelError(
                "WORKER_SLOT_CONFIGURATION_INVALID",
                "repository Worker Slot overrides must be an exact mapping",
            )
        # The boundary owns a snapshot rather than a caller-retained mapping.
        object.__setattr__(
            self,
            "repository_worker_slots",
            None if overrides is None else dict(overrides),
        )

    def worker_slots_for(self, repository: str) -> int:
        configured = (self.repository_worker_slots or {}).get(
            repository, self.host_worker_slots
        )
        if (
            type(configured) is not int
            or isinstance(configured, bool)
            or configured < 1
        ):
            raise ExecutionKernelError(
                "WORKER_SLOT_CONFIGURATION_INVALID",
                "Worker Slot capacity must be a positive exact integer",
            )
        return configured


@dataclass(frozen=True)
class WorkRunAction:
    """One bounded external effect addressed by a stable Campaign identity."""

    stable_action_id: str
    repository: str
    campaign_key: str
    plan_revision_digest: str
    ticket_key: str
    kind: str
    semantic_action_id: str
    work_run_key: str = ""
    work_subject_digest: str = ""


@dataclass(frozen=True)
class WorkRunObservation:
    """A durable readback emitted by the deep module owning an effect.

    #112 may emit ``runtime_unavailable`` or ``parked``.  The Kernel consumes
    those typed facts but deliberately does not classify provider failures or
    decide permission policy itself.
    """

    phase: str
    stable_action_id: str
    receipt_digest: str
    reason: str | None = None
    next_check_at: str | None = None
    binding_established: bool = True
    candidate_identity: str | None = None
    result_digest: str | None = None
    evidence_digests: tuple[str, ...] = ()

    _PHASES = frozenset(
        {
            "running",
            "candidate_checks",
            "formal_review",
            "repair",
            "accepted_awaiting_delivery",
            "parked",
            "completed",
            "decision",
            "wait",
            "blocked",
            "runtime_unavailable",
            "quiescent",
        }
    )

    def __post_init__(self) -> None:
        if self.phase not in self._PHASES:
            raise ExecutionKernelError(
                "WORK_RUN_OBSERVATION_INVALID", "Work Run phase is not recognized"
            )
        if type(self.stable_action_id) is not str or not self.stable_action_id:
            raise ExecutionKernelError(
                "WORK_RUN_OBSERVATION_INVALID", "Work Run action identity is missing"
            )
        if (
            type(self.receipt_digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.receipt_digest) is None
        ):
            raise ExecutionKernelError(
                "WORK_RUN_OBSERVATION_INVALID", "Work Run receipt digest is invalid"
            )
        if self.next_check_at is not None and type(self.next_check_at) is not str:
            raise ExecutionKernelError(
                "WORK_RUN_OBSERVATION_INVALID", "Work Run due time is invalid"
            )
        if self.candidate_identity is not None and (
            type(self.candidate_identity) is not str or not self.candidate_identity
        ):
            raise ExecutionKernelError(
                "WORK_RUN_OBSERVATION_INVALID",
                "Candidate identity must be non-empty text when present",
            )
        if type(self.evidence_digests) is not tuple:
            raise ExecutionKernelError(
                "WORK_RUN_OBSERVATION_INVALID",
                "Evidence identities must be a tuple",
            )
        for digest, label in (
            (self.result_digest, "Result"),
            *(
                (evidence_digest, "Evidence")
                for evidence_digest in self.evidence_digests
            ),
        ):
            if digest is not None and (
                type(digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise ExecutionKernelError(
                    "WORK_RUN_OBSERVATION_INVALID",
                    f"{label} digest is invalid",
                )
        if self.evidence_digests != tuple(sorted(set(self.evidence_digests))):
            raise ExecutionKernelError(
                "WORK_RUN_OBSERVATION_INVALID",
                "Evidence identities are not canonical",
            )

    @classmethod
    def running(cls, stable_action_id: str) -> "WorkRunObservation":
        return cls("running", stable_action_id, digest_value({"action": stable_action_id, "phase": "running"}))


@dataclass(frozen=True)
class PlanInvalidationObservation:
    """One authoritative Plan Invalidation report bound to exact identities.

    The RuntimeGateway publishes this typed observation after it reads the
    Artifact-backed report and proves the reporting role's capability policy.
    ExecutionKernel persists it under ``dedup_identity`` and transitions only
    the affected Work Run to ``quiescent``.  It is Evidence of plan
    invalidation, not a replacement plan and not authority to widen a
    Candidate.  It cannot mutate Issues, blockers, Campaign membership,
    authority, merge state, or the global route.
    """

    repository: str
    campaign_key: str
    plan_revision_digest: str
    ticket_key: str
    work_run_key: str
    runtime_binding_id: str
    authority_subtree_digest: str
    reporter_role: str
    report_digest: str
    evidence_digest: str
    dedup_identity: str
    invalidated_obligation: str
    required_effects: tuple[str, ...]
    workspace_identity: str

    def __post_init__(self) -> None:
        for field_name in (
            "repository",
            "campaign_key",
            "ticket_key",
            "work_run_key",
            "runtime_binding_id",
            "reporter_role",
            "dedup_identity",
            "invalidated_obligation",
            "workspace_identity",
        ):
            if type(getattr(self, field_name)) is not str or not getattr(self, field_name):
                raise ExecutionKernelError(
                    "PLAN_INVALIDATION_OBSERVATION_INVALID",
                    f"Plan Invalidation {field_name} is missing",
                )
        for digest_field in (
            "plan_revision_digest",
            "authority_subtree_digest",
            "report_digest",
            "evidence_digest",
        ):
            value = getattr(self, digest_field)
            if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ExecutionKernelError(
                    "PLAN_INVALIDATION_OBSERVATION_INVALID",
                    f"Plan Invalidation {digest_field} is not a SHA-256 digest",
                )
        if type(self.required_effects) is not tuple or any(
            type(effect) is not str or not effect for effect in self.required_effects
        ):
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_OBSERVATION_INVALID",
                "Plan Invalidation required effects must be a tuple of non-empty strings",
            )
        if self.reporter_role not in {"worker", "recovery_worker", "review"}:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_OBSERVATION_INVALID",
                "Plan Invalidation reporter role is outside the closed authority union",
            )

    @property
    def digest(self) -> str:
        return digest_value(self.canonical())

    def canonical(self) -> dict[str, Any]:
        return {
            "kind": "plan_invalidation_observation.v1",
            "repository": self.repository,
            "campaign_key": self.campaign_key,
            "plan_revision_digest": self.plan_revision_digest,
            "ticket_key": self.ticket_key,
            "work_run_key": self.work_run_key,
            "runtime_binding_id": self.runtime_binding_id,
            "authority_subtree_digest": self.authority_subtree_digest,
            "reporter_role": self.reporter_role,
            "report_digest": self.report_digest,
            "evidence_digest": self.evidence_digest,
            "dedup_identity": self.dedup_identity,
            "invalidated_obligation": self.invalidated_obligation,
            "required_effects": list(self.required_effects),
            "workspace_identity": self.workspace_identity,
        }

    @classmethod
    def from_canonical(cls, value: Mapping[str, Any]) -> "PlanInvalidationObservation":
        """Decode the Gateway receipt's closed observation projection."""

        expected = {
            "kind",
            "repository",
            "campaign_key",
            "plan_revision_digest",
            "ticket_key",
            "work_run_key",
            "runtime_binding_id",
            "authority_subtree_digest",
            "reporter_role",
            "report_digest",
            "evidence_digest",
            "dedup_identity",
            "invalidated_obligation",
            "required_effects",
            "workspace_identity",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != expected
            or value.get("kind") != "plan_invalidation_observation.v1"
        ):
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_OBSERVATION_INVALID",
                "Gateway Plan Invalidation receipt has an unknown observation schema",
            )
        effects = value.get("required_effects")
        if type(effects) is not list:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_OBSERVATION_INVALID",
                "Gateway Plan Invalidation receipt effects are not a list",
            )
        try:
            return cls(
                repository=value["repository"],
                campaign_key=value["campaign_key"],
                plan_revision_digest=value["plan_revision_digest"],
                ticket_key=value["ticket_key"],
                work_run_key=value["work_run_key"],
                runtime_binding_id=value["runtime_binding_id"],
                authority_subtree_digest=value["authority_subtree_digest"],
                reporter_role=value["reporter_role"],
                report_digest=value["report_digest"],
                evidence_digest=value["evidence_digest"],
                dedup_identity=value["dedup_identity"],
                invalidated_obligation=value["invalidated_obligation"],
                required_effects=tuple(effects),
                workspace_identity=value["workspace_identity"],
            )
        except KeyError as error:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_OBSERVATION_INVALID",
                "Gateway Plan Invalidation receipt is incomplete",
            ) from error

    @classmethod
    def from_receipt(cls, value: object) -> "PlanInvalidationObservation":
        """Convert a RuntimeGateway receipt without importing RuntimeGateway."""

        observation = getattr(value, "observation", None)
        report_digest = getattr(value, "report_digest", None)
        if observation is None or type(report_digest) is not str:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_OBSERVATION_INVALID",
                "plan_invalidation requires a Gateway receipt with a readback observation",
            )
        decoded = cls.from_canonical(observation)
        if decoded.report_digest != report_digest:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_OBSERVATION_INVALID",
                "Gateway receipt report identity does not match its observation",
            )
        receipt_digest = getattr(value, "receipt_digest", None)
        if type(receipt_digest) is not str or re.fullmatch(r"[0-9a-f]{64}", receipt_digest) is None:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_OBSERVATION_INVALID",
                "Gateway receipt digest is invalid",
            )
        return decoded


@dataclass(frozen=True)
class PlanInvalidationDiagnostic:
    """The inspect-facing diagnostic for one quiescent Work Run.

    It names the invalidated obligation, Evidence identity, retained
    diagnostic identity, and the exact continuation condition without a
    model transcript.  It is read-only Evidence; it never carries a
    replacement plan or authority to resume.
    """

    report_digest: str
    evidence_digest: str
    invalidated_obligation: str
    required_effects: tuple[str, ...]
    workspace_identity: str
    continuation_condition: str
    work_run_key: str = ""
    runtime_binding_id: str | None = None
    authority_subtree_digest: str | None = None
    dedup_identity: str | None = None
    claim_state: str = "released"
    exclusive_resources: tuple[str, ...] = ()
    classification_action_id: str | None = None
    classification_disposition: str | None = None


class ActivePlanReader(Protocol):
    def read_active(self, handle: CampaignHandle) -> ActivePlanReadback: ...


class PlanInvalidationClassifier(Protocol):
    """Private PlanControl seam used only after a Work Run is quiescent."""

    def classify_plan_invalidations(
        self,
        handle: CampaignHandle,
        invalidations: tuple[PlanInvalidationObservation, ...],
        execution_snapshot: Mapping[str, Any],
    ) -> PlanInvalidationClassification | None: ...


class WorkRunEffects(Protocol):
    """The readback-first seam for RuntimeGateway/CandidateGate/BatchIntegrator."""

    def readback(self, action: WorkRunAction) -> WorkRunObservation | None: ...

    def execute(self, action: WorkRunAction) -> WorkRunObservation: ...


class ExecutionKernel:
    """Persist and advance one Campaign without Coordinator continuation."""

    def __init__(
        self,
        *,
        store_path: Path,
        plan_control: ActivePlanReader,
        effects: WorkRunEffects,
        configuration: ExecutionKernelConfiguration | None = None,
    ) -> None:
        self._store_path = Path(store_path)
        self._plan_control = plan_control
        self._effects = effects
        self._configuration = configuration or ExecutionKernelConfiguration()
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS v8_execution_kernel_campaigns (
                    repository TEXT NOT NULL,
                    campaign_key TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    PRIMARY KEY (repository, campaign_key)
                )
                """
            )

    def advance(
        self,
        campaign_handle: CampaignHandle,
        wake_ref: str | None = None,
        *,
        plan_invalidation: object | None = None,
    ) -> CampaignOutcome:
        """Read back authority, perform all currently due effects, derive one status."""

        with self._campaign_lock(campaign_handle):
            active, work = self._authoritative_active(campaign_handle)
            state = self._load_or_initialize(active, work)
            self._reconcile_plan_invalidations(active, state, work)
            if plan_invalidation is not None:
                observation = self._coerce_plan_invalidation(plan_invalidation)
                self._apply_plan_invalidation(active, state, work, observation)
            self._classify_plan_invalidations_if_needed(active, state, work)
            is_new_wake = False
            if wake_ref is not None:
                if type(wake_ref) is not str or not wake_ref:
                    raise ExecutionKernelError("WAKE_REFERENCE_INVALID", "wake_ref must be non-empty text")
                seen = set(state["wake_refs"])
                if wake_ref not in seen:
                    state["wake_refs"].append(wake_ref)
                    is_new_wake = True
                    self._save(active.handle, state)

            # A bounded full fair scan gives every currently eligible Ticket
            # the same chance, then repeats after releases so refill needs no
            # caller.  The lock is an in-process Campaign serialization aid,
            # not a persistence transaction; no SQLite transaction crosses
            # the external readback/execute boundary.
            while True:
                due = self._next_due_run(
                    state,
                    work,
                    active,
                    wake_ref=wake_ref if is_new_wake else None,
                )
                if due is None:
                    break
                self._perform_due_effect(active, state, due, wake_ref=wake_ref)
                state = self._load(active.handle)

            return self._outcome(active.handle, state)

    def inspect(self, campaign_handle: CampaignHandle) -> Diagnostics:
        """Mechanically explain the durable Campaign state; it sends no effect."""

        active, work = self._authoritative_active(campaign_handle)
        state = self._load(active.handle)
        if state is None:
            return Diagnostics(
                status=CampaignStatus.BLOCKED,
                reason="CampaignNotAdvanced",
                campaign=active.handle,
                plan_revision_digest=active.current_revision_digest,
                worker_slots={
                    "limit": self._configuration.worker_slots_for(active.handle.repository),
                    "held": 0,
                    "available": self._configuration.worker_slots_for(active.handle.repository),
                },
                work_runs=(),
                outstanding_effect_ids=(),
            )
        # Reuse the same identity backfill as advance, but do not admit or
        # execute an effect.  Historical inspect must not expose an empty or
        # Ticket-shaped Work Run identity after an upgrade.
        state = self._load_or_initialize(active, work)
        outcome = self._outcome(active.handle, state)
        classification = self._current_classification(
            state,
            active.current_revision_digest,
        )
        runs = tuple(
            self._run_summary(key, run)
            for key, run in sorted(state["runs"].items())
        )
        held = sum(1 for run in state["runs"].values() if run["slot_held"])
        outstanding = tuple(
            action_id
            for action_id, effect in sorted(state["effects"].items())
            if effect["state"] == "intent"
        )
        limit = self._configuration.worker_slots_for(active.handle.repository)
        return Diagnostics(
            status=outcome.status,
            reason=outcome.reason,
            campaign=active.handle,
            plan_revision_digest=state["plan_revision_digest"],
            worker_slots={"limit": limit, "held": held, "available": limit - held},
            work_runs=runs,
            outstanding_effect_ids=outstanding,
            invalidation_classification=classification,
        )

    @staticmethod
    def _run_summary(
        ticket_key: str,
        run: dict[str, Any],
    ) -> WorkRunSummary:
        invalidation_record = run.get("plan_invalidation")
        diagnostic: PlanInvalidationDiagnostic | None = None
        if invalidation_record is not None:
            diagnostic = PlanInvalidationDiagnostic(
                report_digest=invalidation_record["report_digest"],
                evidence_digest=invalidation_record["evidence_digest"],
                invalidated_obligation=invalidation_record["invalidated_obligation"],
                required_effects=tuple(invalidation_record.get("required_effects", ())),
                workspace_identity=invalidation_record["workspace_identity"],
                continuation_condition="PlanControlReplanRequired",
                work_run_key=invalidation_record.get("work_run_key", run.get("work_run_key", "")),
                runtime_binding_id=invalidation_record.get("runtime_binding_id"),
                authority_subtree_digest=invalidation_record.get("authority_subtree_digest"),
                dedup_identity=invalidation_record.get("dedup_identity"),
                claim_state=run.get("claim_state", "released"),
                exclusive_resources=tuple(run.get("exclusive_resources", ())),
                classification_action_id=(
                    run.get("plan_invalidation_resolution", {}).get("action_id")
                    if type(run.get("plan_invalidation_resolution")) is dict
                    else None
                ),
                classification_disposition=(
                    run.get("plan_invalidation_resolution", {}).get("disposition")
                    if type(run.get("plan_invalidation_resolution")) is dict
                    else None
                ),
            )
        return WorkRunSummary(
            ticket_key=ticket_key,
            phase=run["phase"],
            slot_held=bool(run["slot_held"]),
            reason=run.get("reason"),
            next_check_at=run.get("next_check_at"),
            plan_invalidation=diagnostic,
            work_run_key=run.get("work_run_key", ""),
            runtime_binding_id=run.get("semantic_action_id"),
            claim_state=run.get("claim_state", "unclaimed"),
            exclusive_resources=tuple(run.get("exclusive_resources", ())),
            work_subject_digest=run.get("work_subject_digest", ""),
            candidate_identity=run.get("candidate_identity"),
            result_digest=run.get("result_digest"),
            evidence_digests=tuple(run.get("evidence_digests", ())),
        )

    @staticmethod
    def _coerce_plan_invalidation(value: object) -> PlanInvalidationObservation:
        if type(value) is PlanInvalidationObservation:
            return value
        try:
            return PlanInvalidationObservation.from_receipt(value)
        except ExecutionKernelError:
            raise
        except Exception as error:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_OBSERVATION_INVALID",
                "plan_invalidation is not a typed observation or Gateway receipt",
            ) from error

    def _authoritative_active(
        self, handle: CampaignHandle
    ) -> tuple[ActivePlanReadback, dict[str, dict[str, Any]]]:
        if type(handle) is not CampaignHandle:
            raise ExecutionKernelError(
                "CAMPAIGN_HANDLE_INVALID", "advance requires the exact CampaignHandle"
            )
        try:
            active = self._plan_control.read_active(handle)
        except Exception as error:
            raise ExecutionKernelError(
                "ACTIVATION_READBACK_FAILED",
                "#109 Activation Receipt and Ticket claims did not read back",
            ) from error
        if (
            type(active) is not ActivePlanReadback
            or active.handle != handle
            or active.current_revision_digest != active.activation_receipt.revision_digest
            or active.activation_receipt.repository != handle.repository
            or active.activation_receipt.campaign_key != handle.campaign_key
        ):
            raise ExecutionKernelError(
                "ACTIVATION_READBACK_INVALID",
                "Activation Receipt is not bound to this Campaign",
            )
        try:
            plan = load_canonical_json(active.plan_spec_bytes)
        except CanonicalJsonError as error:
            raise ExecutionKernelError("ACTIVE_PLAN_INVALID", "PlanSpec bytes are not canonical") from error
        if (
            digest_bytes(active.plan_spec_bytes) != active.current_revision_digest
            or type(plan) is not dict
            or plan.get("schema_version") != 3
            or plan.get("repository") != handle.repository
            or type(plan.get("campaign")) is not dict
            or plan["campaign"].get("key") != handle.campaign_key
            or type(plan.get("work")) is not list
        ):
            raise ExecutionKernelError("ACTIVE_PLAN_INVALID", "active PlanSpec is not exact")
        work: dict[str, dict[str, Any]] = {}
        for item in plan["work"]:
            if type(item) is not dict or type(item.get("key")) is not str:
                raise ExecutionKernelError("ACTIVE_PLAN_INVALID", "PlanSpec work item is invalid")
            key = item["key"]
            if key in work:
                raise ExecutionKernelError("ACTIVE_PLAN_INVALID", "PlanSpec work keys are not unique")
            dependencies = item.get("depends_on")
            resources = item.get("exclusive_resources")
            if (
                type(dependencies) is not list
                or type(resources) is not list
                or any(type(value) is not str or not value for value in dependencies + resources)
            ):
                raise ExecutionKernelError("ACTIVE_PLAN_INVALID", "PlanSpec admission facts are invalid")
            work[key] = item
        expected_claims = tuple(active.activation_receipt.ticket_keys)
        observed_claims = tuple(proof.ticket_key for proof in active.claim_proofs)
        if (
            tuple(sorted(work)) != expected_claims
            or observed_claims != expected_claims
            or len(set(observed_claims)) != len(observed_claims)
            or any(
                proof.repository != handle.repository
                or proof.campaign_key != handle.campaign_key
                or proof.plan_revision_digest != active.current_revision_digest
                for proof in active.claim_proofs
            )
        ):
            raise ExecutionKernelError(
                "TICKET_CLAIM_READBACK_INVALID",
                "Ticket claims are missing, foreign, stale, or unbound",
            )
        return active, work

    def _load_or_initialize(
        self, active: ActivePlanReadback, work: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        try:
            plan = load_canonical_json(active.plan_spec_bytes)
        except CanonicalJsonError as error:  # pragma: no cover - validated earlier
            raise ExecutionKernelError(
                "ACTIVE_PLAN_INVALID", "PlanSpec bytes are not canonical"
            ) from error
        if type(plan) is not dict:
            raise ExecutionKernelError("ACTIVE_PLAN_INVALID", "active PlanSpec is not an object")
        state = self._load(active.handle)
        if state is None:
            runs: dict[str, dict[str, Any]] = {}
            for key in sorted(work):
                subject_digest = _work_subject_digest_for_kernel(plan, work[key])
                runs[key] = {
                    "phase": "pending",
                    "slot_held": False,
                    "reason": None,
                    "last_action_id": None,
                    "semantic_action_id": None,
                    "resume_ordinal": 0,
                    "next_check_at": None,
                    "work_subject_digest": subject_digest,
                    "work_run_key": (
                        work_run_key(key, subject_digest)
                        if _has_revision_identity_facts(plan, work[key])
                        else f"work-run:{key}"
                    ),
                    "exclusive_resources": list(work[key].get("exclusive_resources", [])),
                    "claim_state": "unclaimed",
                    "candidate_identity": None,
                    "result_digest": None,
                    "evidence_digests": [],
                    "plan_invalidation": None,
                    "plan_invalidation_resolution": None,
                    "resume_after_invalidation": False,
                }
            state = {
                "plan_revision_digest": active.current_revision_digest,
                "activation_receipt_digest": digest_value(active.activation_receipt.__dict__),
                "runs": runs,
                "effects": {},
                "wake_refs": [],
                "plan_invalidation": {},
                "plan_invalidation_resolutions": {},
                "plan_invalidation_classifications": {},
                "accepted_results": [],
            }
            self._save(active.handle, state)
            return state
        if (
            state.get("plan_revision_digest") != active.current_revision_digest
            or state.get("activation_receipt_digest")
            != digest_value(active.activation_receipt.__dict__)
            or tuple(sorted(state.get("runs", {}))) != tuple(sorted(work))
        ):
            raise ExecutionKernelError(
                "CAMPAIGN_REVISION_CHANGED",
                "a successor Plan Revision requires its own durable execution state",
            )
        state.setdefault("plan_invalidation", {})
        state.setdefault("plan_invalidation_resolutions", {})
        state.setdefault("plan_invalidation_classifications", {})
        state.setdefault("accepted_results", [])
        state.setdefault("effects", {})
        if type(state["effects"]) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID", "ExecutionKernel effects are not a mapping"
            )
        dirty = False
        # Backfill only a missing historical Work Run identity.  An existing
        # key is already bound state: preserve the legacy Ticket-shaped key so
        # same-revision invalidation records remain exact.  Only successor
        # activation may rekey that historical identity.
        for ticket_key, run in state.get("runs", {}).items():
            if type(run) is not dict:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID", "ExecutionKernel Work Run is not an object"
                )
            subject_digest = _work_subject_digest_for_kernel(plan, work[ticket_key])
            expected_work_run_key = _work_run_key_for_kernel(
                plan, work[ticket_key], subject_digest
            )
            existing_subject_digest = run.get("work_subject_digest")
            if (
                existing_subject_digest is not None
                and existing_subject_digest != subject_digest
            ):
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "historical Work Subject identity is not bound to the active Plan Revision",
                )
            if existing_subject_digest != subject_digest:
                run["work_subject_digest"] = subject_digest
                dirty = True
            existing_work_run_key = run.get("work_run_key")
            legacy_work_run_key = f"work-run:{ticket_key}"
            if existing_work_run_key not in {
                None,
                legacy_work_run_key,
                expected_work_run_key,
            }:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "historical Work Run identity is not bound to the active Plan Revision",
                )
            if existing_work_run_key is None:
                run["work_run_key"] = legacy_work_run_key
                dirty = True
            if "plan_invalidation" not in run:
                run["plan_invalidation"] = None
                dirty = True
            if "exclusive_resources" not in run:
                run["exclusive_resources"] = list(work[ticket_key].get("exclusive_resources", []))
                dirty = True
            if "claim_state" not in run:
                run["claim_state"] = (
                    "held" if run.get("slot_held") else
                    "unclaimed" if run.get("phase") == "pending" else
                    "released"
                )
                dirty = True
            if "candidate_identity" not in run:
                run["candidate_identity"] = None
                dirty = True
            if "result_digest" not in run:
                run["result_digest"] = None
                dirty = True
            if "evidence_digests" not in run:
                run["evidence_digests"] = []
                dirty = True
            if "plan_invalidation_resolution" not in run:
                run["plan_invalidation_resolution"] = None
                dirty = True
            if "resume_after_invalidation" not in run:
                run["resume_after_invalidation"] = False
                dirty = True

        # Re-key any historical effect referenced by the Work Run's last
        # action.  The migration is deterministic and idempotent: after the
        # first readback only the revision-bound key remains.
        for ticket_key, run in state["runs"].items():
            legacy_action_id = run.get("last_action_id")
            if type(legacy_action_id) is not str:
                continue
            effect = state["effects"].get(legacy_action_id)
            if type(effect) is not dict:
                continue
            if effect.get("ticket_key") != ticket_key:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "historical effect is not bound to its Work Run",
                )
            execution_action_id = self._effect_action_id(
                active, ticket_key, run, resuming=False
            )
            resume_action_id = self._effect_action_id(
                active, ticket_key, run, resuming=True
            )
            if legacy_action_id in {execution_action_id, resume_action_id}:
                revision_bound_action_id = legacy_action_id
            else:
                resuming = legacy_action_id != run.get("semantic_action_id")
                revision_bound_action_id = self._effect_action_id(
                    active, ticket_key, run, resuming=resuming
                )
            effect_identity = {
                "plan_revision_digest": active.current_revision_digest,
                "work_run_key": run["work_run_key"],
                "work_subject_digest": run["work_subject_digest"],
            }
            migrated_effect = dict(effect)
            for field, value in effect_identity.items():
                if field in migrated_effect and migrated_effect[field] != value:
                    raise ExecutionKernelError(
                        "EXECUTION_STORE_INVALID",
                        "historical effect identity is not bound to the active Plan Revision",
                    )
                migrated_effect[field] = value
            if revision_bound_action_id != legacy_action_id:
                existing_effect = state["effects"].get(revision_bound_action_id)
                if existing_effect is not None and existing_effect != migrated_effect:
                    raise ExecutionKernelError(
                        "EXECUTION_STORE_INVALID",
                        "historical effect migration conflicts with a revision-bound effect",
                    )
                state["effects"].pop(legacy_action_id, None)
                state["effects"][revision_bound_action_id] = migrated_effect
                run["last_action_id"] = revision_bound_action_id
                dirty = True
            elif state["effects"].get(revision_bound_action_id) != migrated_effect:
                state["effects"][revision_bound_action_id] = migrated_effect
                dirty = True
        if dirty:
            self._save(active.handle, state)
        return state

    @staticmethod
    def _effect_action_id(
        active: ActivePlanReadback,
        ticket_key: str,
        run: Mapping[str, Any],
        *,
        resuming: bool,
    ) -> str:
        kind = "semantic_resume" if resuming else "semantic_execution"
        return digest_value(
            {
                "kind": f"work-run.{kind}.v1",
                "repository": active.handle.repository,
                "campaign_key": active.handle.campaign_key,
                "plan_revision_digest": active.current_revision_digest,
                "ticket_key": ticket_key,
                "work_run_key": run["work_run_key"],
                "work_subject_digest": run["work_subject_digest"],
                "ordinal": run["resume_ordinal"] if resuming else 0,
            }
        )

    def _next_due_run(
        self,
        state: dict[str, Any],
        work: dict[str, dict[str, Any]],
        active: ActivePlanReadback,
        *,
        wake_ref: str | None,
    ) -> str | None:
        capacity = self._configuration.worker_slots_for(active.handle.repository)
        held = sum(1 for run in state["runs"].values() if run["slot_held"])
        # A wake never starts a second semantic action: it grants one bounded
        # authoritative readback of each already-active Work Run.  No-wake
        # calls are admission/refill only, so they cannot become LLM polling.
        if wake_ref is not None:
            for ticket_key in sorted(work):
                run = state["runs"][ticket_key]
                if (
                    run["phase"] in _SLOT_PHASES
                    and run.get("last_wake_ref") != wake_ref
                ):
                    return ticket_key
            # #112 alone proves a parked binding.  Once it has, this Kernel
            # deterministically reacquires a Worker Slot before it asks the
            # effect owner to resume that same semantic action.
            for ticket_key in sorted(work):
                run = state["runs"][ticket_key]
                if run["phase"] != "parked" or run.get("last_wake_ref") == wake_ref:
                    continue
                if held >= capacity:
                    run["reason"] = "WorkerSlotCapacity"
                    continue
                run["reason"] = None
                return ticket_key
        for ticket_key in sorted(work):
            run = state["runs"][ticket_key]
            # A durable invalidation record fences this Work Run even if the
            # process crashed between the record save and the quiescent save.
            # Reconciliation repairs that window before any external effect.
            if self._has_pending_plan_invalidation(state, ticket_key):
                continue
            if run["phase"] != "pending":
                continue
            dependencies = work[ticket_key]["depends_on"]
            if any(
                dependency not in state["runs"]
                or state["runs"][dependency]["phase"] not in _TERMINAL_PHASES
                for dependency in dependencies
            ):
                run["reason"] = "TicketDependency"
                continue
            claimed_elsewhere = {
                resource
                for other_key, other in state["runs"].items()
                # A PlanSpec declaration becomes an actual Exclusive Resource
                # claim only after this Kernel has admitted its Work Run.
                # Pending Tickets never reserve each other merely by naming
                # the same resource.  A quiescent Work Run holds no resource.
                if other_key != ticket_key
                and other["phase"] not in {"pending", *_TERMINAL_PHASES, *_QUIESCENT_PHASES}
                for resource in work[other_key].get("exclusive_resources", [])
            }
            if any(resource in claimed_elsewhere for resource in work[ticket_key]["exclusive_resources"]):
                run["reason"] = "ExclusiveResource"
                continue
            if held >= capacity:
                run["reason"] = "WorkerSlotCapacity"
                continue
            run["reason"] = None
            return ticket_key
        self._save(active.handle, state)
        return None

    def _apply_plan_invalidation(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        work: dict[str, dict[str, Any]],
        observation: PlanInvalidationObservation,
    ) -> None:
        """Persist one report, then reconcile its quiescent Work Run.

        The report record is the first durable write.  That ordering fences the
        affected Work Run even if the process dies before the quiescent state or
        slot release is persisted.  ``_reconcile_plan_invalidations`` is safe to
        run again after a crash and never issues an external effect.
        """

        if type(observation) is not PlanInvalidationObservation:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_OBSERVATION_INVALID",
                "plan_invalidation requires an exact PlanInvalidationObservation",
            )
        if (
            observation.repository != active.handle.repository
            or observation.campaign_key != active.handle.campaign_key
            or observation.plan_revision_digest != active.current_revision_digest
        ):
            raise ExecutionKernelError(
                "INVALIDATION_IDENTITY_MISMATCH",
                "Plan Invalidation observation is not bound to this Campaign or Plan Revision",
            )
        run = state["runs"].get(observation.ticket_key)
        if run is None:
            raise ExecutionKernelError(
                "INVALIDATION_IDENTITY_MISMATCH",
                "Plan Invalidation observation names a Ticket that is not admitted",
            )
        if run.get("work_run_key") != observation.work_run_key:
            # #133 callers may still report the historical Ticket-shaped key.
            # Rebind that compatibility spelling to the already persisted
            # semantic Work Run before recording the observation; any other
            # mismatch remains fail-closed.
            if observation.work_run_key != f"work-run:{observation.ticket_key}":
                raise ExecutionKernelError(
                    "INVALIDATION_IDENTITY_MISMATCH",
                    "Plan Invalidation observation is not bound to this Work Run",
                )
            observation = replace(
                observation,
                work_run_key=run["work_run_key"],
            )
        # A report is only meaningful after the Runtime Binding exists.  A
        # pending/unbound run must not be quiesced by a forged identity.
        if (
            type(run.get("semantic_action_id")) is not str
            or not run.get("semantic_action_id")
            or run["semantic_action_id"] != observation.runtime_binding_id
        ):
            raise ExecutionKernelError(
                "INVALIDATION_IDENTITY_MISMATCH",
                "Plan Invalidation observation is not bound to this Runtime Binding",
            )
        # The authority subtree digest must match the PlanSpec authority for
        # this Ticket's reporting role.  A foreign authority cannot stop work.
        # The Kernel fails closed when the frozen authority structure is
        # missing: it never accepts an invalidation whose authority boundary
        # it cannot independently prove.
        work_item = work.get(observation.ticket_key)
        if type(work_item) is not dict:
            raise ExecutionKernelError(
                "INVALIDATION_IDENTITY_MISMATCH",
                "Plan Invalidation observation names a Ticket that is not in the active PlanSpec",
            )
        expected_role = self._expected_reporter_role(work_item)
        if observation.reporter_role != expected_role:
            raise ExecutionKernelError(
                "INVALIDATION_AUTHORITY_ROLE_MISMATCH",
                "Plan Invalidation reporter role does not match the Work Run purpose",
            )
        authority = work_item.get("authority")
        if type(authority) is not dict:
            raise ExecutionKernelError(
                "INVALIDATION_IDENTITY_MISMATCH",
                "Plan Invalidation requires a frozen authority record for the Ticket",
            )
        role_authority = authority.get(observation.reporter_role)
        if type(role_authority) is not dict:
            raise ExecutionKernelError(
                "INVALIDATION_IDENTITY_MISMATCH",
                "Plan Invalidation requires a frozen authority record for the reporter role",
            )
        subtree_digest = role_authority.get("subtree_digest")
        if type(subtree_digest) is not str or subtree_digest != observation.authority_subtree_digest:
            raise ExecutionKernelError(
                "INVALIDATION_IDENTITY_MISMATCH",
                "Plan Invalidation observation is not bound to this authority subtree",
            )
        invalidations = state.setdefault("plan_invalidation", {})
        dedup_key = self._scoped_dedup_key(observation)
        record = {
            "repository": observation.repository,
            "campaign_key": observation.campaign_key,
            "plan_revision_digest": observation.plan_revision_digest,
            "ticket_key": observation.ticket_key,
            "work_run_key": observation.work_run_key,
            "runtime_binding_id": observation.runtime_binding_id,
            "authority_subtree_digest": observation.authority_subtree_digest,
            "reporter_role": observation.reporter_role,
            "report_digest": observation.report_digest,
            "evidence_digest": observation.evidence_digest,
            "dedup_identity": observation.dedup_identity,
            "invalidated_obligation": observation.invalidated_obligation,
            "required_effects": list(observation.required_effects),
            "workspace_identity": observation.workspace_identity,
            "observation_digest": observation.digest,
        }
        existing = invalidations.get(dedup_key)
        if existing is not None:
            if existing != record:
                raise ExecutionKernelError(
                    "INVALIDATION_DEDUP_CONFLICT",
                    "Plan Invalidation deduplication identity is bound to a different observation",
                )
            self._reconcile_plan_invalidations(active, state, work)
            return
        if run["phase"] not in {
            *_SLOT_PHASES,
            "accepted_awaiting_delivery",
            "parked",
            "runtime_unavailable",
            "quiescent",
        }:
            raise ExecutionKernelError(
                "INVALIDATION_PHASE_INVALID",
                "Plan Invalidation may only stop an active bound Work Run",
            )
        invalidations[dedup_key] = record
        # Persist the observation before changing any Work Run field.
        self._save(active.handle, state)
        self._reconcile_plan_invalidations(active, state, work)

    @staticmethod
    def _decode_classification(value: object) -> PlanInvalidationClassification:
        """Decode one exact persisted Coordinator result or fail closed."""

        if type(value) is not dict:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                "persisted invalidation classification is not an object",
            )
        try:
            classification = PlanInvalidationClassification.from_canonical(value)
        except Exception as error:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                "persisted invalidation classification cannot be decoded",
            ) from error
        if classification.canonical() != value:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                "persisted invalidation classification is not canonical",
            )
        return classification

    @staticmethod
    def _replanning_action_id(
        active: ActivePlanReadback,
        evidence_digests: tuple[str, ...],
    ) -> str:
        return "replan:" + digest_value(
            {
                "repository": active.handle.repository,
                "campaign_key": active.handle.campaign_key,
                "plan_revision_digest": active.current_revision_digest,
                "evidence_digests": list(evidence_digests),
            }
        )

    @staticmethod
    def _pending_invalidation_observations(
        state: Mapping[str, Any],
    ) -> tuple[tuple[PlanInvalidationObservation, ...], tuple[str, ...]]:
        records = state.get("plan_invalidation", {})
        resolutions = state.get("plan_invalidation_resolutions", {})
        if type(records) is not dict or type(resolutions) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Campaign Plan Invalidation records are invalid",
            )
        observations: list[PlanInvalidationObservation] = []
        for dedup_key, record in sorted(records.items()):
            if dedup_key in resolutions:
                continue
            if type(record) is not dict:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Campaign Plan Invalidation record is invalid",
                )
            raw = {
                "kind": "plan_invalidation_observation.v1",
                **{
                    key: value
                    for key, value in record.items()
                    if key != "observation_digest"
                },
            }
            try:
                observation = PlanInvalidationObservation.from_canonical(raw)
            except Exception as error:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Campaign Plan Invalidation record cannot be decoded",
                ) from error
            if record.get("observation_digest") != observation.digest:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Campaign Plan Invalidation observation digest changed",
                )
            observations.append(observation)
        evidence_digests = tuple(sorted({item.evidence_digest for item in observations}))
        return tuple(observations), evidence_digests

    @staticmethod
    def _execution_snapshot(
        active: ActivePlanReadback,
        state: Mapping[str, Any],
        work: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        runs = state.get("runs")
        if type(runs) is not dict or tuple(sorted(runs)) != tuple(sorted(work)):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "ExecutionKernel Work Run set does not match the active PlanSpec",
            )
        run_facts: list[dict[str, Any]] = []
        for ticket_key in sorted(work):
            run = runs[ticket_key]
            if type(run) is not dict:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "ExecutionKernel Work Run is not an object",
                )
            run_facts.append(
                {
                    "ticket_key": ticket_key,
                    "work_run_key": run.get("work_run_key"),
                    "phase": run.get("phase"),
                    "slot_held": run.get("slot_held"),
                    "reason": run.get("reason"),
                    "next_check_at": run.get("next_check_at"),
                    "runtime_binding_id": run.get("semantic_action_id"),
                    "claim_state": run.get("claim_state"),
                    "exclusive_resources": list(
                        work[ticket_key].get("exclusive_resources", [])
                    ),
                }
            )
        claims = [
            {
                "ticket_key": proof.ticket_key,
                "repository": proof.repository,
                "campaign_key": proof.campaign_key,
                "plan_revision_digest": proof.plan_revision_digest,
            }
            for proof in active.claim_proofs
        ]
        accepted_results = state.get("accepted_results", [])
        if type(accepted_results) is not list:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "ExecutionKernel accepted Results are not a list",
            )
        snapshot_results: list[dict[str, Any]] = []
        for result in accepted_results:
            if type(result) is not dict:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "ExecutionKernel accepted Result is not an object",
                )
            if set(result) == {"ticket_key", "result_digest"}:
                snapshot_results.append(
                    {
                        "ticket_key": result["ticket_key"],
                        "result_digest": result["result_digest"],
                    }
                )
                continue
            try:
                binding = AcceptedResultBinding.from_canonical(result)
            except Exception as error:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "ExecutionKernel accepted Result binding is invalid",
                ) from error
            snapshot_results.append(binding.canonical())
        return {
            "runs": run_facts,
            "claims": sorted(claims, key=lambda item: item["ticket_key"]),
            "accepted_results": sorted(
                snapshot_results, key=lambda item: item["ticket_key"]
            ),
        }

    def _current_classification(
        self,
        state: Mapping[str, Any],
        plan_revision_digest: str,
    ) -> PlanInvalidationClassification | None:
        records = state.get("plan_invalidation_classifications", {})
        if type(records) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Campaign invalidation classifications are not a mapping",
            )
        decoded = [self._decode_classification(value) for value in records.values()]
        if any(item.plan_revision_digest != plan_revision_digest for item in decoded):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Campaign invalidation classification belongs to another Plan Revision",
            )
        if len(decoded) > 1:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_CLASSIFICATION_CONFLICT",
                "one active Plan Revision has more than one classification",
            )
        return decoded[0] if decoded else None

    def _classify_plan_invalidations_if_needed(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        work: Mapping[str, Mapping[str, Any]],
    ) -> None:
        classifier = getattr(self._plan_control, "classify_plan_invalidations", None)
        if not callable(classifier):
            return
        observations, evidence_digests = self._pending_invalidation_observations(state)
        if not observations:
            return
        expected_action_id = self._replanning_action_id(active, evidence_digests)
        existing = self._current_classification(state, active.current_revision_digest)
        if existing is not None:
            if (
                existing.action_id != expected_action_id
                or existing.evidence_digests != evidence_digests
            ):
                raise ExecutionKernelError(
                    "PLAN_INVALIDATION_CLASSIFICATION_CONFLICT",
                    "new same-revision Evidence appeared after classification",
                )
            self._apply_invalidation_classification(
                active,
                state,
                work,
                existing,
                evidence_digests,
            )
            return
        execution_snapshot = self._execution_snapshot(active, state, work)
        try:
            classification = classifier(
                active.handle,
                observations,
                execution_snapshot,
            )
        except ExecutionKernelError:
            raise
        except Exception as error:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_CLASSIFICATION_FAILED",
                "PlanControl invalidation classification did not complete",
            ) from error
        if classification is None:
            return
        if type(classification) is not PlanInvalidationClassification:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                "PlanControl returned an untyped invalidation classification",
            )
        if (
            classification.action_id != expected_action_id
            or classification.plan_revision_digest != active.current_revision_digest
            or classification.evidence_digests != evidence_digests
        ):
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                "PlanControl classification is bound to another Evidence set",
            )
        state["plan_invalidation_classifications"] = {
            classification.action_id: classification.canonical()
        }
        self._save(active.handle, state)
        readback = self._load(active.handle)
        if readback is None:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Campaign state disappeared after classification persistence",
            )
        state.clear()
        state.update(readback)
        persisted = self._current_classification(
            state,
            active.current_revision_digest,
        )
        if persisted != classification:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                "PlanControl classification did not read back exactly",
            )
        self._apply_invalidation_classification(
            active,
            state,
            work,
            persisted,
            evidence_digests,
        )

    def _apply_invalidation_classification(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        work: Mapping[str, Mapping[str, Any]],
        classification: PlanInvalidationClassification,
        evidence_digests: tuple[str, ...],
    ) -> None:
        if (
            classification.plan_revision_digest != active.current_revision_digest
            or classification.evidence_digests != evidence_digests
        ):
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                "classification Evidence does not cover all pending invalidations",
            )
        if classification.disposition is PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR:
            if not set(classification.successor_ticket_keys).issubset(work):
                raise ExecutionKernelError(
                    "PLAN_INVALIDATION_TICKET_INVALID",
                    "classification successor names a Ticket outside the active Campaign",
                )
            approved_edges = {
                (dependency, ticket_key)
                for ticket_key, item in work.items()
                for dependency in item.get("depends_on", [])
                if dependency in work
            }
            if any(
                (item.from_ticket, item.to_ticket) not in approved_edges
                for item in classification.dependency_additions
            ):
                raise ExecutionKernelError(
                    "PLAN_INVALIDATION_DEPENDENCY_UNPROVED",
                    "classification successor names an unproved dependency",
                )
        if classification.disposition in {
            PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR,
            PlanInvalidationDisposition.REQUIRE_HUMAN_DECISION,
        }:
            # The affected Work Runs remain quiescent.  #135/#136 own the
            # later successor activation or tracker/authority gate.
            return
        records = state.get("plan_invalidation", {})
        resolutions = state.setdefault("plan_invalidation_resolutions", {})
        if type(records) is not dict or type(resolutions) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Campaign Plan Invalidation resolution records are invalid",
            )
        for dedup_key, record in records.items():
            if dedup_key in resolutions:
                continue
            if type(record) is not dict or record.get("evidence_digest") not in evidence_digests:
                raise ExecutionKernelError(
                    "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                    "classification omitted a pending invalidation record",
                )
            ticket_key = record.get("ticket_key")
            run = state.get("runs", {}).get(ticket_key)
            if type(run) is not dict or ticket_key not in work:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Plan Invalidation resolution names an unknown Work Run",
                )
            resolutions[dedup_key] = classification.action_id
            run["phase"] = "pending"
            run["slot_held"] = False
            run["reason"] = None
            run["next_check_at"] = None
            run["last_action_id"] = None
            run["claim_state"] = "unclaimed"
            run["resume_ordinal"] = int(run.get("resume_ordinal", 0)) + 1
            run["resume_after_invalidation"] = True
            run["plan_invalidation_resolution"] = {
                "action_id": classification.action_id,
                "disposition": classification.disposition.value,
            }
        self._save(active.handle, state)
        readback = self._load(active.handle)
        if readback is None:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Campaign state disappeared after invalidation classification application",
            )
        state.clear()
        state.update(readback)
        for ticket_key, run in state.get("runs", {}).items():
            if self._has_pending_plan_invalidation(state, ticket_key):
                continue
            resolution = run.get("plan_invalidation_resolution")
            if type(resolution) is not dict or resolution.get("action_id") != classification.action_id:
                continue
            if run.get("phase") != "pending" or run.get("slot_held"):
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "resumed Work Run was not read back before admission",
                )

    @staticmethod
    def _scoped_dedup_key(observation: PlanInvalidationObservation) -> str:
        """Namespace caller-provided dedup identities by Campaign/Work Run."""

        return digest_value(
            {
                "repository": observation.repository,
                "campaign_key": observation.campaign_key,
                "plan_revision_digest": observation.plan_revision_digest,
                "work_run_key": observation.work_run_key,
                "dedup_identity": observation.dedup_identity,
            }
        )

    @staticmethod
    def _has_pending_plan_invalidation(
        state: Mapping[str, Any],
        ticket_key: str,
    ) -> bool:
        records = state.get("plan_invalidation", {})
        resolutions = state.get("plan_invalidation_resolutions", {})
        if type(records) is not dict or type(resolutions) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Campaign Plan Invalidation resolution records are invalid",
            )
        return any(
            type(record) is dict
            and record.get("ticket_key") == ticket_key
            and dedup_key not in resolutions
            for dedup_key, record in records.items()
        )

    @staticmethod
    def _expected_reporter_role(work_item: Mapping[str, Any]) -> str:
        """Resolve the closed PlanControl role for one Work Run."""

        explicit = work_item.get("reporter_role")
        if type(explicit) is str and explicit in {"worker", "recovery_worker", "review"}:
            return explicit
        purpose = work_item.get("purpose")
        if type(purpose) is dict:
            purpose = purpose.get("kind")
        if purpose is None and type(work_item.get("contract")) is dict:
            purpose = work_item["contract"].get("purpose")
            if type(purpose) is dict:
                purpose = purpose.get("kind")
        if purpose in {"formal_review", "invalid_review_payload_retry", "specialist_review", "review"}:
            return "review"
        if purpose in {"terminal_recovery_implementation", "recovery_worker"}:
            return "recovery_worker"
        return "worker"

    def _reconcile_plan_invalidations(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        work: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """Repair report/quiescence/slot-release crash windows idempotently."""

        invalidations = state.setdefault("plan_invalidation", {})
        if type(invalidations) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Campaign Plan Invalidation records are invalid",
            )
        resolutions = state.setdefault("plan_invalidation_resolutions", {})
        classifications = state.setdefault("plan_invalidation_classifications", {})
        if type(resolutions) is not dict or type(classifications) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Campaign Plan Invalidation readback lineage is invalid",
            )
        for dedup_key, record in list(invalidations.items()):
            if type(record) is not dict:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Campaign Plan Invalidation record is invalid",
                )
            required = {
                "repository",
                "campaign_key",
                "plan_revision_digest",
                "ticket_key",
                "work_run_key",
                "runtime_binding_id",
                "authority_subtree_digest",
                "reporter_role",
                "report_digest",
                "evidence_digest",
                "dedup_identity",
                "invalidated_obligation",
                "required_effects",
                "workspace_identity",
                "observation_digest",
            }
            if set(record) != required:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Campaign Plan Invalidation record schema is not closed",
                )
            try:
                observation = PlanInvalidationObservation(
                    repository=record["repository"],
                    campaign_key=record["campaign_key"],
                    plan_revision_digest=record["plan_revision_digest"],
                    ticket_key=record["ticket_key"],
                    work_run_key=record["work_run_key"],
                    runtime_binding_id=record["runtime_binding_id"],
                    authority_subtree_digest=record["authority_subtree_digest"],
                    reporter_role=record["reporter_role"],
                    report_digest=record["report_digest"],
                    evidence_digest=record["evidence_digest"],
                    dedup_identity=record["dedup_identity"],
                    invalidated_obligation=record["invalidated_obligation"],
                    required_effects=tuple(record["required_effects"]),
                    workspace_identity=record["workspace_identity"],
                )
            except (KeyError, TypeError, ExecutionKernelError) as error:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Campaign Plan Invalidation record cannot be decoded",
                ) from error
            if (
                dedup_key != self._scoped_dedup_key(observation)
                or record["observation_digest"] != observation.digest
                or observation.repository != active.handle.repository
                or observation.campaign_key != active.handle.campaign_key
                or observation.plan_revision_digest != active.current_revision_digest
            ):
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Campaign Plan Invalidation record is stale or misbound",
                )
            run = state["runs"].get(observation.ticket_key)
            if type(run) is not dict or run.get("work_run_key") != observation.work_run_key:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Campaign Plan Invalidation record names an unknown Work Run",
                )
            work_item = work.get(observation.ticket_key)
            if type(work_item) is not dict:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Campaign Plan Invalidation record names an unknown PlanSpec item",
                )
            if (
                run.get("semantic_action_id") != observation.runtime_binding_id
                or observation.reporter_role != self._expected_reporter_role(work_item)
                or type(work_item.get("authority")) is not dict
                or type(work_item["authority"].get(observation.reporter_role)) is not dict
                or work_item["authority"][observation.reporter_role].get("subtree_digest")
                != observation.authority_subtree_digest
            ):
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Campaign Plan Invalidation record is not bound to the current authority",
                )
            resolution_action_id = resolutions.get(dedup_key)
            if resolution_action_id is not None:
                if type(resolution_action_id) is not str or not resolution_action_id:
                    raise ExecutionKernelError(
                        "EXECUTION_STORE_INVALID",
                        "Plan Invalidation resolution identity is invalid",
                    )
                classification = self._decode_classification(
                    classifications.get(resolution_action_id)
                )
                if (
                    classification.action_id != resolution_action_id
                    or classification.plan_revision_digest
                    != active.current_revision_digest
                    or observation.evidence_digest
                    not in classification.evidence_digests
                    or classification.disposition
                    not in {
                        PlanInvalidationDisposition.RESUME_UNCHANGED,
                        PlanInvalidationDisposition.DEFER_NON_BLOCKING,
                        PlanInvalidationDisposition.REJECT_INVALID_EVIDENCE,
                    }
                ):
                    raise ExecutionKernelError(
                        "EXECUTION_STORE_INVALID",
                        "Plan Invalidation resolution is not a valid resume classification",
                    )
                # The observation remains diagnostic lineage, but it no longer
                # fences the Work Run after the exact classification readback.
                continue
            if run.get("phase") not in {
                *_SLOT_PHASES,
                "accepted_awaiting_delivery",
                "parked",
                "runtime_unavailable",
                "quiescent",
            }:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Campaign Plan Invalidation record retroactively targets a terminal or pending run",
                )
            if run.get("plan_invalidation") != record or run.get("phase") != "quiescent":
                # Keep the slot held until this quiescent state is durable.
                run["phase"] = "quiescent"
                run["reason"] = "PlanInvalidation"
                run["plan_invalidation"] = record
                run["last_action_id"] = None
                run["next_check_at"] = None
                run["claim_state"] = "held" if run.get("slot_held") else "released"
                self._save(active.handle, state)
                readback = self._load(active.handle)
                if readback is None:
                    raise ExecutionKernelError(
                        "EXECUTION_STORE_INVALID",
                        "Campaign state disappeared after Plan Invalidation persistence",
                    )
                readback_run = readback["runs"].get(observation.ticket_key)
                if (
                    type(readback_run) is not dict
                    or readback_run.get("phase") != "quiescent"
                    or readback_run.get("plan_invalidation") != record
                ):
                    raise ExecutionKernelError(
                        "EXECUTION_STORE_INVALID",
                        "Quiescent Work Run state was not read back before slot release",
                    )
                state.clear()
                state.update(readback)
                invalidations = state["plan_invalidation"]
                run = state["runs"][observation.ticket_key]
            if run.get("slot_held"):
                run["slot_held"] = False
                run["claim_state"] = "released"
                self._save(active.handle, state)

    def _perform_due_effect(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        ticket_key: str,
        *,
        wake_ref: str | None,
    ) -> None:
        run = state["runs"][ticket_key]
        resuming = run["phase"] == "parked" or bool(
            run.get("resume_after_invalidation")
        )
        kind = "semantic_resume" if resuming else "semantic_execution"
        action_id = self._effect_action_id(
            active, ticket_key, run, resuming=resuming
        )
        semantic_action_id = run.get("semantic_action_id") or action_id
        action = WorkRunAction(
            stable_action_id=action_id,
            repository=active.handle.repository,
            campaign_key=active.handle.campaign_key,
            plan_revision_digest=active.current_revision_digest,
            ticket_key=ticket_key,
            kind=kind,
            semantic_action_id=semantic_action_id,
            work_run_key=run["work_run_key"],
            work_subject_digest=run["work_subject_digest"],
        )
        # The intent becomes durable before the external boundary.  A restart
        # observes this same identity through the effect owner before retry.
        prior_effect = state["effects"].get(action_id)
        effect_identity = {
            "plan_revision_digest": active.current_revision_digest,
            "work_run_key": run["work_run_key"],
            "work_subject_digest": run["work_subject_digest"],
        }
        if prior_effect is not None and any(
            field in prior_effect and prior_effect[field] != value
            for field, value in effect_identity.items()
        ):
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "durable effect intent is not bound to the current revision and Work Run",
            )
        state["effects"].setdefault(
            action_id,
            {
                "state": "intent",
                "ticket_key": ticket_key,
                **effect_identity,
            },
        )
        run["last_action_id"] = action_id
        run["semantic_action_id"] = semantic_action_id
        run["slot_held"] = True
        run["claim_state"] = "held"
        self._save(active.handle, state)
        observation = self._effects.readback(action)
        if observation is None:
            if prior_effect is not None and prior_effect.get("state") == "read_back":
                # A wake hint with no authoritative state change is consumed.
                # Reissuing the semantic effect here would create polling and
                # would undermine the stable-action recovery contract.
                run["last_wake_ref"] = wake_ref
                self._save(active.handle, state)
                return
            observation = self._effects.execute(action)
        if type(observation) is not WorkRunObservation or observation.stable_action_id != action_id:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "effect result does not bind its stable action identity",
            )
        run["phase"] = observation.phase
        run["reason"] = observation.reason
        run["next_check_at"] = observation.next_check_at
        run["slot_held"] = observation.phase in _SLOT_PHASES
        run["claim_state"] = "held" if run["slot_held"] else "released"
        run["last_wake_ref"] = wake_ref
        run["resume_after_invalidation"] = False
        if observation.candidate_identity is not None:
            run["candidate_identity"] = observation.candidate_identity
        if observation.phase == "runtime_unavailable" and observation.binding_established:
            # A live unavailable binding retains the Slot until #112 proves a
            # park/terminal transition.  The phase itself is a durable Wait.
            run["slot_held"] = True
            run["claim_state"] = "held"
        if resuming:
            run["resume_ordinal"] += 1
        state["effects"][action_id] = {
            "state": "read_back",
            "ticket_key": ticket_key,
            "receipt_digest": observation.receipt_digest,
            **effect_identity,
        }
        if observation.phase == "completed":
            try:
                plan = load_canonical_json(active.plan_spec_bytes)
            except CanonicalJsonError as error:  # pragma: no cover - validated earlier
                raise ExecutionKernelError(
                    "ACTIVE_PLAN_INVALID", "PlanSpec bytes are not canonical"
                ) from error
            binding = AcceptedResultBinding(
                ticket_key=ticket_key,
                result_digest=observation.result_digest or observation.receipt_digest,
                evidence_digests=observation.evidence_digests,
                work_subject_digest=run["work_subject_digest"],
                target_facts_digest=_target_facts_digest_for_kernel(plan),
            )
            state["accepted_results"] = [
                value
                for value in state["accepted_results"]
                if value["ticket_key"] != ticket_key
            ] + [binding.canonical()]
            state["accepted_results"].sort(key=lambda value: value["ticket_key"])
            run["result_digest"] = binding.result_digest
            run["evidence_digests"] = list(binding.evidence_digests)
        self._save(active.handle, state)

    def _outcome(self, handle: CampaignHandle, state: dict[str, Any] | None) -> CampaignOutcome:
        if state is None:
            return CampaignOutcome(CampaignStatus.BLOCKED, "CampaignNotAdvanced")
        runs = state["runs"].values()
        if runs and all(run["phase"] in _TERMINAL_PHASES for run in runs):
            return CampaignOutcome(CampaignStatus.COMPLETE, "AllRequiredWorkComplete")
        active = next(
            (
                ticket_key
                for ticket_key, run in sorted(state["runs"].items())
                if run["phase"] in _SLOT_PHASES
            ),
            None,
        )
        if active is not None:
            return CampaignOutcome(CampaignStatus.RUNNING, f"WorkRunActive:{active}")
        due = next(
            (
                ticket_key
                for ticket_key, run in sorted(state["runs"].items())
                if run["phase"] == "pending" and run.get("reason") is None
            ),
            None,
        )
        if due is not None:
            return CampaignOutcome(CampaignStatus.RUNNING, f"AdmissionDue:{due}")
        decision = next(
            (
                (ticket_key, run)
                for ticket_key, run in sorted(state["runs"].items())
                if run["phase"] == "decision"
            ),
            None,
        )
        if decision is not None:
            ticket_key, run = decision
            return CampaignOutcome(
                CampaignStatus.DECISION,
                run.get("reason") or f"DecisionRequired:{ticket_key}",
            )
        # A quiescent Work Run is an explicit, named Decision until PlanControl
        # (#134) wires the bounded Coordinator replanning path.  No sixth public
        # status is introduced: the Campaign-level outcome is Decision, and the
        # Work Run phase remains private Kernel-internal state.
        quiescent = next(
            (
                (ticket_key, run)
                for ticket_key, run in sorted(state["runs"].items())
                if run["phase"] == "quiescent"
            ),
            None,
        )
        if quiescent is not None:
            ticket_key, run = quiescent
            obligation = (
                run.get("plan_invalidation", {}).get("invalidated_obligation")
                if run.get("plan_invalidation")
                else None
            )
            detail = (
                f"PlanInvalidation:{ticket_key}:{obligation}"
                if obligation is not None
                else f"PlanInvalidation:{ticket_key}"
            )
            return CampaignOutcome(CampaignStatus.DECISION, detail)
        waiting = next(
            (
                (ticket_key, run)
                for ticket_key, run in sorted(state["runs"].items())
                if run["phase"] in {"parked", "wait", "runtime_unavailable", "pending"}
            ),
            None,
        )
        if waiting is not None:
            ticket_key, run = waiting
            default_reason = {
                "parked": "RuntimeParked",
                "wait": "ObservableContinuationRequired",
                "runtime_unavailable": "RuntimeUnavailable",
                "pending": "AdmissionBlocked",
            }[run["phase"]]
            return CampaignOutcome(
                CampaignStatus.WAIT,
                run.get("reason") or f"{default_reason}:{ticket_key}",
            )
        blocked = next(
            (
                (ticket_key, run)
                for ticket_key, run in sorted(state["runs"].items())
                if run["phase"] == "blocked"
            ),
            None,
        )
        if blocked is not None:
            ticket_key, run = blocked
            return CampaignOutcome(
                CampaignStatus.BLOCKED,
                run.get("reason") or f"NoAuthorizedContinuation:{ticket_key}",
            )
        return CampaignOutcome(CampaignStatus.BLOCKED, "NoAuthorizedContinuation")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._store_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _campaign_lock(self, handle: CampaignHandle) -> threading.RLock:
        key = f"{self._store_path.resolve()}::{handle.repository}::{handle.campaign_key}"
        with _KERNEL_LOCKS_GUARD:
            return _KERNEL_LOCKS.setdefault(key, threading.RLock())

    def _load(self, handle: CampaignHandle) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state_json FROM v8_execution_kernel_campaigns
                WHERE repository = ? AND campaign_key = ?
                """,
                (handle.repository, handle.campaign_key),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["state_json"])
        except json.JSONDecodeError as error:
            raise ExecutionKernelError("EXECUTION_STORE_INVALID", "Campaign state is unreadable") from error
        if type(value) is not dict:
            raise ExecutionKernelError("EXECUTION_STORE_INVALID", "Campaign state is invalid")
        return value

    def _save(self, handle: CampaignHandle, state: dict[str, Any]) -> None:
        rendered = json.dumps(state, separators=(",", ":"), sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO v8_execution_kernel_campaigns (repository, campaign_key, state_json)
                VALUES (?, ?, ?)
                ON CONFLICT(repository, campaign_key) DO UPDATE SET state_json = excluded.state_json
                """,
                (handle.repository, handle.campaign_key, rendered),
            )


_default_execution_kernel: ExecutionKernel | None = None


def install_execution_kernel(
    *,
    store_path: Path,
    plan_control: ActivePlanReader,
    effects: WorkRunEffects,
    configuration: ExecutionKernelConfiguration | None = None,
) -> ExecutionKernel:
    """Install the one V3 ``advance``/``inspect`` host composition.

    The supplied ``plan_control`` surface is intentionally limited to
    ``read_active``.  It prevents this normal path from reaching V2's legacy
    driver or from gaining PlanControl's claim/publication operations.
    """

    global _default_execution_kernel
    _default_execution_kernel = ExecutionKernel(
        store_path=store_path,
        plan_control=plan_control,
        effects=effects,
        configuration=configuration,
    )
    return _default_execution_kernel


def _installed_execution_kernel() -> ExecutionKernel:
    if _default_execution_kernel is None:
        raise ExecutionKernelError(
            "EXECUTION_KERNEL_UNINSTALLED",
            "install_execution_kernel must compose the V3 active reader first",
        )
    return _default_execution_kernel


def advance(
    campaign_handle: CampaignHandle,
    wake_ref: str | None = None,
    *,
    plan_invalidation: object | None = None,
) -> CampaignOutcome:
    """Advance the installed V3 Campaign state machine once."""

    return _installed_execution_kernel().advance(
        campaign_handle,
        wake_ref,
        plan_invalidation=plan_invalidation,
    )


def inspect(campaign_handle: CampaignHandle) -> Diagnostics:
    """Read the installed V3 Campaign diagnostics without an external effect."""

    return _installed_execution_kernel().inspect(campaign_handle)
