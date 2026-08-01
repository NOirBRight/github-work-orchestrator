"""The V8 Campaign execution state machine.

``ExecutionKernel`` is deliberately the only post-activation workflow driver.
It consumes PlanControl's read-only active Campaign proof, persists an intent
for each bounded effect, and asks an owning deep module to read that exact
effect back before it is executed or retried.  It owns neither Ticket claims
nor Runtime/provider policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Protocol

from ._canonical import CanonicalJsonError, digest_bytes, digest_value, load_canonical_json
from .plan_control import ActivePlanReadback, CampaignHandle


_DIGEST_LENGTH = 64
_TERMINAL_PHASES = frozenset({"completed"})
_SLOT_PHASES = frozenset({"running", "candidate_checks", "formal_review", "repair"})
_KERNEL_LOCKS_GUARD = threading.Lock()
_KERNEL_LOCKS: dict[str, threading.RLock] = {}


class ExecutionKernelError(RuntimeError):
    """A named fail-closed ExecutionKernel outcome."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


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


@dataclass(frozen=True)
class Diagnostics:
    status: CampaignStatus
    reason: str
    campaign: CampaignHandle
    plan_revision_digest: str
    worker_slots: dict[str, int]
    work_runs: tuple[WorkRunSummary, ...]
    outstanding_effect_ids: tuple[str, ...]


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

    @classmethod
    def running(cls, stable_action_id: str) -> "WorkRunObservation":
        return cls("running", stable_action_id, digest_value({"action": stable_action_id, "phase": "running"}))


class ActivePlanReader(Protocol):
    def read_active(self, handle: CampaignHandle) -> ActivePlanReadback: ...


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
        self, campaign_handle: CampaignHandle, wake_ref: str | None = None
    ) -> CampaignOutcome:
        """Read back authority, perform all currently due effects, derive one status."""

        with self._campaign_lock(campaign_handle):
            active, work = self._authoritative_active(campaign_handle)
            state = self._load_or_initialize(active, work)
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

        active, _work = self._authoritative_active(campaign_handle)
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
        outcome = self._outcome(active.handle, state)
        runs = tuple(
            WorkRunSummary(
                ticket_key=key,
                phase=run["phase"],
                slot_held=bool(run["slot_held"]),
                reason=run.get("reason"),
                next_check_at=run.get("next_check_at"),
            )
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
        )

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
        state = self._load(active.handle)
        if state is None:
            state = {
                "plan_revision_digest": active.current_revision_digest,
                "activation_receipt_digest": digest_value(active.activation_receipt.__dict__),
                "runs": {
                    key: {
                        "phase": "pending",
                        "slot_held": False,
                        "reason": None,
                        "last_action_id": None,
                        "semantic_action_id": None,
                        "resume_ordinal": 0,
                        "next_check_at": None,
                    }
                    for key in sorted(work)
                },
                "effects": {},
                "wake_refs": [],
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
        return state

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
                # the same resource.
                if other_key != ticket_key
                and other["phase"] not in {"pending", *_TERMINAL_PHASES}
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

    def _perform_due_effect(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        ticket_key: str,
        *,
        wake_ref: str | None,
    ) -> None:
        run = state["runs"][ticket_key]
        resuming = run["phase"] == "parked"
        kind = "semantic_resume" if resuming else "semantic_execution"
        action_id = digest_value(
            {
                "kind": f"work-run.{kind}.v1",
                "repository": active.handle.repository,
                "campaign_key": active.handle.campaign_key,
                "plan_revision_digest": active.current_revision_digest,
                "ticket_key": ticket_key,
                "ordinal": run["resume_ordinal"] if resuming else 0,
            }
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
        )
        # The intent becomes durable before the external boundary.  A restart
        # observes this same identity through the effect owner before retry.
        prior_effect = state["effects"].get(action_id)
        state["effects"].setdefault(action_id, {"state": "intent", "ticket_key": ticket_key})
        run["last_action_id"] = action_id
        run["semantic_action_id"] = semantic_action_id
        run["slot_held"] = True
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
        run["last_wake_ref"] = wake_ref
        if observation.phase == "runtime_unavailable" and observation.binding_established:
            # A live unavailable binding retains the Slot until #112 proves a
            # park/terminal transition.  The phase itself is a durable Wait.
            run["slot_held"] = True
        if resuming:
            run["resume_ordinal"] += 1
        state["effects"][action_id] = {
            "state": "read_back",
            "ticket_key": ticket_key,
            "receipt_digest": observation.receipt_digest,
        }
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
    campaign_handle: CampaignHandle, wake_ref: str | None = None
) -> CampaignOutcome:
    """Advance the installed V3 Campaign state machine once."""

    return _installed_execution_kernel().advance(campaign_handle, wake_ref)


def inspect(campaign_handle: CampaignHandle) -> Diagnostics:
    """Read the installed V3 Campaign diagnostics without an external effect."""

    return _installed_execution_kernel().inspect(campaign_handle)
