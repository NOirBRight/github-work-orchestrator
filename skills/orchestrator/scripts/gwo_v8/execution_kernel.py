"""The V8 Campaign execution state machine.

``ExecutionKernel`` is deliberately the only post-activation workflow driver.
It consumes PlanControl's read-only active Campaign proof, persists an intent
for each bounded effect, and asks an owning deep module to read that exact
effect back before it is executed or retried.  It owns neither Ticket claims
nor Runtime/provider policy.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
from pathlib import Path
import re
import sqlite3
import threading
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol

if TYPE_CHECKING:
    from .campaign_watchdog import WatchdogCampaignSnapshot

from ._canonical import (
    CanonicalJsonError,
    canonical_bytes,
    digest_bytes,
    digest_value,
    load_canonical_json,
)
from .plan_control import (
    ActivePlanReadback,
    ActivationReceipt,
    CampaignHandle,
    PlanInvalidationDecision,
    PlanInvalidationClassification,
    PlanInvalidationDisposition,
    TicketClaimProof,
)
from .candidate_gate import CandidateGateError, CandidateReceipt
from .human_gate import (
    HumanDecisionChoice,
    HumanDecisionRecord,
    HumanGateSummary,
    HumanSourceReadback,
    RequiredDurableSourceChange,
    _human_planning_action_id as _derive_human_planning_action_id,
)
from .planning_protocol import REPLANNING_OUTPUT_PROTOCOL_ID


_DIGEST_LENGTH = 64
_DEFAULT_SUCCESSOR_REVISION_LIMIT = 1
_DEFAULT_REPEATED_INVALIDATION_LIMIT = 1
_HUMAN_SUCCESSOR_ACTION_PREFIX = "replan:human:"
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StaleReadbackState(str, Enum):
    TERMINAL = "terminal"
    IDLE = "idle"
    PERMISSION_WAITING = "permission_waiting"
    CANDIDATE_RECEIVED = "candidate_received"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    AMBIGUOUS_RUNNING = "ambiguous_running"


class StaleDiagnosisDisposition(str, Enum):
    CONTINUE = "continue"
    GUIDE_SAME_WORKER = "guide_same_worker"
    RECOVER_SAME_BINDING = "recover_same_binding"
    DECISION = "decision"


_STALE_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_STALE_MAX_TEXT = 256
_STALE_MAX_STATE_TEXT = 64


def _validate_stale_digest(value: object, label: str) -> None:
    if type(value) is not str or _STALE_DIGEST_PATTERN.fullmatch(value) is None:
        raise ExecutionKernelError(
            "EFFECT_READBACK_INVALID",
            f"{label} must be a SHA-256 digest",
        )


def _validate_binding_id(value: object, label: str) -> None:
    if type(value) is not str or not value:
        raise ExecutionKernelError(
            "EFFECT_READBACK_INVALID",
            f"{label} must be non-empty exact text",
        )


def _validate_bounded_text(
    value: object,
    label: str,
    *,
    maximum: int = _STALE_MAX_TEXT,
) -> None:
    if (
        type(value) is not str
        or not value
        or "\x00" in value
        or "\r" in value
        or "\n" in value
        or len(value) > maximum
    ):
        raise ExecutionKernelError(
            "EFFECT_READBACK_INVALID",
            f"{label} must be bounded exact text",
        )


@dataclass(frozen=True)
class TerminalBindingEvidence:
    """Read-backed proof that one established Runtime Binding is terminal."""

    prior_action_id: str
    prior_runtime_binding_id: str
    agent_id: str
    session_id: str
    workspace_id: str
    terminal_state: str
    fence_digest: str
    checkpoint_digest: str
    evidence_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self) is not TerminalBindingEvidence:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "terminal binding Evidence must be an exact value",
            )
        for value, label in (
            (self.prior_action_id, "terminal prior action identity"),
            (self.prior_runtime_binding_id, "terminal prior Runtime Binding"),
            (self.agent_id, "terminal Agent identity"),
            (self.session_id, "terminal session identity"),
            (self.workspace_id, "terminal workspace identity"),
            (self.terminal_state, "terminal state"),
        ):
            _validate_bounded_text(value, label, maximum=_STALE_MAX_STATE_TEXT)
        if self.terminal_state != "terminal":
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "terminal binding Evidence does not prove a terminal state",
            )
        _validate_stale_digest(self.fence_digest, "terminal binding fence")
        _validate_stale_digest(self.checkpoint_digest, "terminal binding checkpoint")
        expected = digest_value(self._body())
        if self.evidence_digest is None:
            object.__setattr__(self, "evidence_digest", expected)
        elif self.evidence_digest != expected:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "terminal binding Evidence digest changed",
            )

    def _body(self) -> dict[str, str]:
        return {
            "kind": "terminal-binding-evidence.v1",
            "prior_action_id": self.prior_action_id,
            "prior_runtime_binding_id": self.prior_runtime_binding_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "terminal_state": self.terminal_state,
            "fence_digest": self.fence_digest,
            "checkpoint_digest": self.checkpoint_digest,
        }

    @property
    def digest(self) -> str:
        assert self.evidence_digest is not None
        return self.evidence_digest

    def canonical(self) -> dict[str, str]:
        return {**self._body(), "evidence_digest": self.digest}

    @classmethod
    def from_canonical(cls, value: Mapping[str, Any]) -> "TerminalBindingEvidence":
        expected = {
            "kind",
            "prior_action_id",
            "prior_runtime_binding_id",
            "agent_id",
            "session_id",
            "workspace_id",
            "terminal_state",
            "fence_digest",
            "checkpoint_digest",
            "evidence_digest",
        }
        if type(value) is not dict or set(value) != expected or value.get("kind") != "terminal-binding-evidence.v1":
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "terminal binding Evidence canonical schema is not exact",
            )
        try:
            return cls(
                prior_action_id=value["prior_action_id"],
                prior_runtime_binding_id=value["prior_runtime_binding_id"],
                agent_id=value["agent_id"],
                session_id=value["session_id"],
                workspace_id=value["workspace_id"],
                terminal_state=value["terminal_state"],
                fence_digest=value["fence_digest"],
                checkpoint_digest=value["checkpoint_digest"],
                evidence_digest=value["evidence_digest"],
            )
        except ExecutionKernelError:
            raise
        except Exception as error:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "terminal binding Evidence canonical value is invalid",
            ) from error


class StaleFollowUpKind(str, Enum):
    GUIDANCE = "guidance"
    SAME_BINDING_RECOVERY = "same_binding_recovery"


@dataclass(frozen=True)
class StaleDiagnosisPacket:
    """Bounded, non-transcript stale context sent to one Coordinator action."""

    MAX_TRANSCRIPT_ITEMS = 8
    MAX_TRANSCRIPT_ITEM_BYTES = 512
    MAX_TRANSCRIPT_BYTES = 2048
    MAX_CANDIDATE_IDENTITIES = 3

    repository: str
    campaign_key: str
    plan_revision_digest: str
    ticket_key: str
    work_run_key: str
    work_subject_digest: str
    ticket_contract_digest: str
    authority_subtree_digest: str
    policy_witness_digest: str
    runtime_binding_id: str
    candidate_count: int
    binding_count: int
    candidate_identities: tuple[str, ...]
    lifecycle_state: str
    process_state: str
    workspace_state: str
    check_state: str
    transcript_tail: tuple[str, ...] = ()
    packet_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self) is not StaleDiagnosisPacket:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale diagnosis packet must be an exact value",
            )
        for value, label in (
            (self.repository, "stale packet repository"),
            (self.campaign_key, "stale packet Campaign"),
            (self.ticket_key, "stale packet Ticket"),
            (self.work_run_key, "stale packet Work Run"),
            (self.runtime_binding_id, "stale packet Runtime Binding"),
        ):
            _validate_bounded_text(value, label)
        for value, label in (
            (self.plan_revision_digest, "stale packet Plan Revision"),
            (self.work_subject_digest, "stale packet Work Subject"),
            (self.ticket_contract_digest, "stale packet Ticket contract"),
            (self.authority_subtree_digest, "stale packet authority"),
            (self.policy_witness_digest, "stale packet Policy Witness"),
        ):
            _validate_stale_digest(value, label)
        for value, label in (
            (self.lifecycle_state, "stale packet lifecycle state"),
            (self.process_state, "stale packet process state"),
            (self.workspace_state, "stale packet workspace state"),
            (self.check_state, "stale packet check state"),
        ):
            _validate_bounded_text(value, label, maximum=_STALE_MAX_STATE_TEXT)
        for value, label, maximum in (
            (self.candidate_count, "stale packet Candidate count", 3),
            (self.binding_count, "stale packet binding count", 2),
        ):
            if type(value) is not int or isinstance(value, bool) or not 0 <= value <= maximum:
                raise ExecutionKernelError(
                    "EFFECT_READBACK_INVALID",
                    f"{label} is outside its bound",
                )
        if self.binding_count < 1:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale packet binding count must include the current binding",
            )
        if type(self.candidate_identities) is not tuple or len(self.candidate_identities) > self.MAX_CANDIDATE_IDENTITIES:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale packet Candidate identities exceed their bound",
            )
        if self.candidate_identities != tuple(sorted(set(self.candidate_identities))):
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale packet Candidate identities are not canonical",
            )
        for value in self.candidate_identities:
            _validate_bounded_text(value, "stale packet Candidate identity")
        if type(self.transcript_tail) is not tuple or len(self.transcript_tail) > self.MAX_TRANSCRIPT_ITEMS:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale packet transcript tail exceeds its item bound",
            )
        total_bytes = 0
        for value in self.transcript_tail:
            _validate_bounded_text(
                value,
                "stale packet transcript item",
                maximum=self.MAX_TRANSCRIPT_ITEM_BYTES,
            )
            total_bytes += len(value.encode("utf-8"))
        if total_bytes > self.MAX_TRANSCRIPT_BYTES:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale packet transcript tail exceeds its byte bound",
            )
        expected = digest_value(self._body())
        if self.packet_digest is None:
            object.__setattr__(self, "packet_digest", expected)
        elif self.packet_digest != expected:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale diagnosis packet digest changed",
            )

    def _body(self) -> dict[str, Any]:
        return {
            "kind": "stale-diagnosis-packet.v1",
            "repository": self.repository,
            "campaign_key": self.campaign_key,
            "plan_revision_digest": self.plan_revision_digest,
            "ticket_key": self.ticket_key,
            "work_run_key": self.work_run_key,
            "work_subject_digest": self.work_subject_digest,
            "ticket_contract_digest": self.ticket_contract_digest,
            "authority_subtree_digest": self.authority_subtree_digest,
            "policy_witness_digest": self.policy_witness_digest,
            "runtime_binding_id": self.runtime_binding_id,
            "candidate_count": self.candidate_count,
            "binding_count": self.binding_count,
            "candidate_identities": list(self.candidate_identities),
            "lifecycle_state": self.lifecycle_state,
            "process_state": self.process_state,
            "workspace_state": self.workspace_state,
            "check_state": self.check_state,
            "transcript_tail": list(self.transcript_tail),
        }

    @property
    def digest(self) -> str:
        assert self.packet_digest is not None
        return self.packet_digest

    @property
    def identity(self) -> str:
        return f"stale-diagnosis-packet:{self.digest[:32]}"

    def canonical(self) -> dict[str, Any]:
        return {
            **self._body(),
            "packet_digest": self.digest,
            "packet_identity": self.identity,
        }

    @classmethod
    def from_canonical(cls, value: Mapping[str, Any]) -> "StaleDiagnosisPacket":
        expected = {
            "kind",
            "repository",
            "campaign_key",
            "plan_revision_digest",
            "ticket_key",
            "work_run_key",
            "work_subject_digest",
            "ticket_contract_digest",
            "authority_subtree_digest",
            "policy_witness_digest",
            "runtime_binding_id",
            "candidate_count",
            "binding_count",
            "candidate_identities",
            "lifecycle_state",
            "process_state",
            "workspace_state",
            "check_state",
            "transcript_tail",
            "packet_digest",
            "packet_identity",
        }
        if type(value) is not dict or set(value) != expected or value.get("kind") != "stale-diagnosis-packet.v1":
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "stale diagnosis packet canonical schema is not exact",
            )
        if (
            type(value["candidate_identities"]) is not list
            or type(value["transcript_tail"]) is not list
        ):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "stale diagnosis packet arrays are not exact JSON arrays",
            )
        try:
            packet = cls(
                repository=value["repository"],
                campaign_key=value["campaign_key"],
                plan_revision_digest=value["plan_revision_digest"],
                ticket_key=value["ticket_key"],
                work_run_key=value["work_run_key"],
                work_subject_digest=value["work_subject_digest"],
                ticket_contract_digest=value["ticket_contract_digest"],
                authority_subtree_digest=value["authority_subtree_digest"],
                policy_witness_digest=value["policy_witness_digest"],
                runtime_binding_id=value["runtime_binding_id"],
                candidate_count=value["candidate_count"],
                binding_count=value["binding_count"],
                candidate_identities=tuple(value["candidate_identities"]),
                lifecycle_state=value["lifecycle_state"],
                process_state=value["process_state"],
                workspace_state=value["workspace_state"],
                check_state=value["check_state"],
                transcript_tail=tuple(value["transcript_tail"]),
                packet_digest=value["packet_digest"],
            )
        except ExecutionKernelError:
            raise
        except Exception as error:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "stale diagnosis packet canonical value is invalid",
            ) from error
        if value["packet_identity"] != packet.identity:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "stale diagnosis packet identity changed",
            )
        return packet


@dataclass(frozen=True)
class StaleBindingObservation:
    stable_action_id: str
    runtime_binding_id: str
    state: StaleReadbackState
    runtime_readback_digest: str
    process_readback_digest: str
    workspace_readback_digest: str
    campaign_readback_digest: str
    receipt_digest: str
    candidate_receipt: CandidateReceipt | None = None

    def __post_init__(self) -> None:
        if type(self) is not StaleBindingObservation:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale binding observations must be exact values",
            )
        _validate_binding_id(self.stable_action_id, "stale readback action identity")
        _validate_binding_id(self.runtime_binding_id, "stale runtime binding identity")
        if type(self.state) is not StaleReadbackState:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale readback state is not closed",
            )
        for value, label in (
            (self.runtime_readback_digest, "runtime readback"),
            (self.process_readback_digest, "process readback"),
            (self.workspace_readback_digest, "workspace readback"),
            (self.campaign_readback_digest, "Campaign readback"),
            (self.receipt_digest, "stale readback receipt"),
        ):
            _validate_stale_digest(value, label)
        if self.candidate_receipt is not None and type(self.candidate_receipt) is not CandidateReceipt:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale Candidate progress is not an exact CandidateReceipt",
            )
        if (
            self.state is StaleReadbackState.CANDIDATE_RECEIVED
            and self.candidate_receipt is not None
            and self.receipt_digest != self.candidate_receipt.digest
        ):
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale Candidate readback receipt does not bind CandidateReceipt",
            )


@dataclass(frozen=True)
class StaleDiagnosisObservation:
    stable_action_id: str
    runtime_binding_id: str
    disposition: StaleDiagnosisDisposition
    receipt_digest: str

    def __post_init__(self) -> None:
        if type(self) is not StaleDiagnosisObservation:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale diagnosis observations must be exact values",
            )
        _validate_binding_id(self.stable_action_id, "stale diagnosis action identity")
        _validate_binding_id(self.runtime_binding_id, "stale diagnosis runtime binding identity")
        if type(self.disposition) is not StaleDiagnosisDisposition:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale diagnosis disposition is not closed",
            )
        _validate_stale_digest(self.receipt_digest, "stale diagnosis receipt")


@dataclass(frozen=True)
class StaleFollowUpObservation:
    stable_action_id: str
    runtime_binding_id: str
    kind: StaleFollowUpKind
    receipt_digest: str

    def __post_init__(self) -> None:
        if type(self) is not StaleFollowUpObservation:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale follow-up observations must be exact values",
            )
        _validate_binding_id(self.stable_action_id, "stale follow-up action identity")
        _validate_binding_id(self.runtime_binding_id, "stale follow-up Runtime Binding")
        if type(self.kind) is not StaleFollowUpKind:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale follow-up kind is not closed",
            )
        _validate_stale_digest(self.receipt_digest, "stale follow-up receipt")


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
    last_wake_ref: str | None = None


@dataclass(frozen=True)
class RevisionLineageSummary:
    """Inspect-only summary of a predecessor Plan Revision's retained facts."""

    plan_revision_digest: str
    activation_receipt_digest: str
    classification_action_id: str
    work_run_keys: tuple[str, ...]
    workspace_identities: tuple[str, ...]
    candidate_identities: tuple[str, ...]
    result_digests: tuple[str, ...]
    evidence_digests: tuple[str, ...]
    source_evidence_digests: tuple[str, ...] = ()


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
    revision_lineage: tuple[RevisionLineageSummary, ...] = ()
    human_gate: HumanGateSummary | None = None

    @property
    def plan_invalidation_classification(self) -> PlanInvalidationClassification | None:
        """Compatibility spelling for the Campaign-level readback."""

        return self.invalidation_classification


@dataclass(frozen=True)
class ExecutionKernelConfiguration:
    """Host-owned capacity configuration; it is intentionally outside PlanSpec."""

    host_worker_slots: int = 4
    repository_worker_slots: dict[str, int] | None = None
    host_stale_after_seconds: int = 1800
    repository_stale_after_seconds: dict[str, int] | None = None

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
        if (
            type(self.host_stale_after_seconds) is not int
            or isinstance(self.host_stale_after_seconds, bool)
            or self.host_stale_after_seconds <= 0
        ):
            raise ExecutionKernelError(
                "STALE_CONFIGURATION_INVALID",
                "host stale threshold must be a positive exact integer",
            )
        stale_overrides = self.repository_stale_after_seconds
        if stale_overrides is not None and type(stale_overrides) is not dict:
            raise ExecutionKernelError(
                "STALE_CONFIGURATION_INVALID",
                "repository stale thresholds must be an exact mapping",
            )
        if stale_overrides is not None:
            copied: dict[str, int] = {}
            for repository, seconds in stale_overrides.items():
                if type(repository) is not str or not repository.strip():
                    raise ExecutionKernelError(
                        "STALE_CONFIGURATION_INVALID",
                        "repository stale threshold keys must be non-empty exact text",
                    )
                if (
                    type(seconds) is not int
                    or isinstance(seconds, bool)
                    or seconds <= 0
                ):
                    raise ExecutionKernelError(
                        "STALE_CONFIGURATION_INVALID",
                        "repository stale thresholds must be positive exact integers",
                    )
                copied[repository] = seconds
            object.__setattr__(
                self,
                "repository_stale_after_seconds",
                MappingProxyType(copied),
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

    def stale_after_seconds_for(self, repository: str) -> int:
        if type(repository) is not str or not repository.strip():
            raise ExecutionKernelError(
                "STALE_CONFIGURATION_INVALID",
                "repository must be non-empty exact text",
            )
        configured = (self.repository_stale_after_seconds or {}).get(
            repository, self.host_stale_after_seconds
        )
        if (
            type(configured) is not int
            or isinstance(configured, bool)
            or configured <= 0
        ):
            raise ExecutionKernelError(
                "STALE_CONFIGURATION_INVALID",
                "stale threshold must be a positive exact integer",
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
    runtime_binding_id: str | None = None
    stale_diagnosis_packet: StaleDiagnosisPacket | None = None
    stale_follow_up_kind: StaleFollowUpKind | None = None

    def __post_init__(self) -> None:
        if self.stale_diagnosis_packet is not None:
            if type(self.stale_diagnosis_packet) is not StaleDiagnosisPacket or self.kind != "stale_diagnosis":
                raise ExecutionKernelError(
                    "EFFECT_READBACK_INVALID",
                    "stale diagnosis packet is bound to the wrong action kind",
                )
        if self.stale_follow_up_kind is not None:
            expected = {
                StaleFollowUpKind.GUIDANCE: "stale_guidance",
                StaleFollowUpKind.SAME_BINDING_RECOVERY: "stale_same_binding_recovery",
            }
            if type(self.stale_follow_up_kind) is not StaleFollowUpKind or expected.get(self.stale_follow_up_kind) != self.kind:
                raise ExecutionKernelError(
                    "EFFECT_READBACK_INVALID",
                    "stale follow-up kind is bound to the wrong action kind",
                )


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
    candidate_receipt: CandidateReceipt | None = None
    runtime_binding_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    workspace_id: str | None = None
    terminal_binding_evidence: TerminalBindingEvidence | None = None

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
        if self.runtime_binding_id is not None:
            _validate_binding_id(self.runtime_binding_id, "runtime binding identity")
        identity_values = (self.agent_id, self.session_id, self.workspace_id)
        if any(value is not None for value in identity_values):
            if self.runtime_binding_id is None or any(value is None for value in identity_values):
                raise ExecutionKernelError(
                    "WORK_RUN_OBSERVATION_INVALID",
                    "Agent, session, and workspace identities must be complete",
                )
            for value, label in zip(
                identity_values,
                ("Agent identity", "session identity", "workspace identity"),
            ):
                _validate_bounded_text(value, label)
        if self.terminal_binding_evidence is not None and type(
            self.terminal_binding_evidence
        ) is not TerminalBindingEvidence:
            raise ExecutionKernelError(
                "WORK_RUN_OBSERVATION_INVALID",
                "terminal binding Evidence is not typed",
            )
        if self.terminal_binding_evidence is not None and self.runtime_binding_id is None:
            raise ExecutionKernelError(
                "WORK_RUN_OBSERVATION_INVALID",
                "terminal binding Evidence has no replacement binding",
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
        if self.candidate_receipt is not None and type(
            self.candidate_receipt
        ) is not CandidateReceipt:
            raise ExecutionKernelError(
                "WORK_RUN_OBSERVATION_INVALID",
                "candidate_receipt is not an exact CandidateReceipt",
            )
        if (
            self.candidate_receipt is not None
            and self.receipt_digest != self.candidate_receipt.digest
        ):
            raise ExecutionKernelError(
                "WORK_RUN_OBSERVATION_INVALID",
                "effect receipt digest does not bind CandidateReceipt",
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
    source_evidence_digests: tuple[str, ...] | None = None

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
        if self.source_evidence_digests is not None:
            if type(self.source_evidence_digests) is not tuple:
                raise ExecutionKernelError(
                    "PLAN_INVALIDATION_OBSERVATION_INVALID",
                    "Plan Invalidation source Evidence must be a tuple",
                )
            if (
                not self.source_evidence_digests
                or any(
                    type(digest) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                    for digest in self.source_evidence_digests
                )
                or self.source_evidence_digests
                != tuple(sorted(set(self.source_evidence_digests)))
            ):
                raise ExecutionKernelError(
                    "PLAN_INVALIDATION_OBSERVATION_INVALID",
                    "Plan Invalidation source Evidence is not canonical",
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
        value = {
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
        if self.source_evidence_digests is not None:
            value["source_evidence_digests"] = list(self.source_evidence_digests)
        return value

    @classmethod
    def from_canonical(cls, value: Mapping[str, Any]) -> "PlanInvalidationObservation":
        """Decode the Gateway receipt's closed observation projection."""

        legacy_expected = {
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
        expected_with_source_lineage = legacy_expected | {
            "source_evidence_digests"
        }
        if (
            not isinstance(value, Mapping)
            or set(value) not in (legacy_expected, expected_with_source_lineage)
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
        has_source_lineage = "source_evidence_digests" in value
        source_digests = value.get("source_evidence_digests")
        if has_source_lineage and type(source_digests) is not list:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_OBSERVATION_INVALID",
                "Gateway Plan Invalidation receipt source Evidence is not a list",
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
                source_evidence_digests=(
                    None if not has_source_lineage else tuple(source_digests)
                ),
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
    source_evidence_digests: tuple[str, ...] | None = None


class ActivePlanReader(Protocol):
    def read_active(self, handle: CampaignHandle) -> ActivePlanReadback: ...


class SuccessorPlanActivator(Protocol):
    """Private PlanControl port for one exact approved successor transition."""

    def activate_successor(
        self,
        handle: CampaignHandle,
        classification: PlanInvalidationClassification,
    ) -> ActivePlanReadback: ...


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

    def readback(
        self, action: WorkRunAction
    ) -> WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation | StaleFollowUpObservation | None: ...

    def execute(
        self, action: WorkRunAction
    ) -> WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation | StaleFollowUpObservation: ...


class ExecutionKernel:
    """Persist and advance one Campaign without Coordinator continuation."""

    def __init__(
        self,
        *,
        store_path: Path,
        plan_control: ActivePlanReader,
        effects: WorkRunEffects,
        configuration: ExecutionKernelConfiguration | None = None,
        _clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._store_path = Path(store_path)
        self._plan_control = plan_control
        self._effects = effects
        self._configuration = configuration or ExecutionKernelConfiguration()
        self._campaign_row_versions: dict[tuple[str, str], int | None] = {}
        if not callable(_clock):
            raise ExecutionKernelError(
                "STALE_CONFIGURATION_INVALID",
                "ExecutionKernel clock must be callable",
            )
        self._clock = _clock
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS v8_execution_kernel_campaigns (
                    repository TEXT NOT NULL,
                    campaign_key TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    state_version INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (repository, campaign_key)
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(v8_execution_kernel_campaigns)"
                ).fetchall()
            }
            if "state_version" not in columns:
                connection.execute(
                    "ALTER TABLE v8_execution_kernel_campaigns "
                    "ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0"
                )

    def advance(
        self,
        campaign_handle: CampaignHandle,
        wake_ref: str | None = None,
        *,
        plan_invalidation: object | None = None,
        human_decision: HumanDecisionChoice | None = None,
    ) -> CampaignOutcome:
        """Read back authority, perform all currently due effects, derive one status."""

        with self._campaign_lock(campaign_handle):
            active, work = self._authoritative_active(campaign_handle)
            state = self._load_initialize_or_reconcile_successor(active, work)
            self._reconcile_plan_invalidations(active, state, work)
            if plan_invalidation is not None:
                observation = self._coerce_plan_invalidation(plan_invalidation)
                if self._is_historical_plan_invalidation_replay(
                    active,
                    state,
                    observation,
                ):
                    # A receipt from an archived predecessor is accepted only
                    # when the complete observation is already present in
                    # revision lineage.  Returning before classification,
                    # successor activation, wake handling, or effect admission
                    # makes an exact replay observationally idempotent.
                    return self._outcome(active.handle, state)
                self._apply_plan_invalidation(active, state, work, observation)
            self._classify_plan_invalidations_if_needed(active, state, work)
            if self._human_successor_requires_resume(active, state):
                active, work, state = self._resume_human_successor(
                    active,
                    work,
                    state,
                )
            elif human_decision is not None:
                active, work, state = self._advance_human_gate(
                    active,
                    state,
                    human_decision,
                )
            classification = self._current_classification(
                state,
                active.current_revision_digest,
            )
            active, work, state = self._activate_successor_if_due(
                active,
                work,
                state,
                classification,
            )
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
                progressed = self._perform_due_effect(
                    active, state, due, wake_ref=wake_ref
                )
                state = self._load(active.handle)
                if not progressed:
                    # A durable intent may exist without an authoritative
                    # readback (for example after a provider timeout).  The
                    # next advance may read that exact identity again, but it
                    # must not execute it a second time.  A semantic wake
                    # readback with no lifecycle change is nevertheless
                    # consumed for this wake, so continue the fair scan and
                    # let released capacity refill pending Tickets.
                    current_run = state.get("runs", {}).get(due)
                    if (
                        wake_ref is not None
                        and type(current_run) is dict
                        and current_run.get("last_wake_ref") == wake_ref
                    ):
                        continue
                    break

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
        migration_due = state.get("plan_revision_digest") != active.current_revision_digest
        if migration_due:
            if not self._human_successor_activation_crossed(active, state):
                self._validate_successor_state_match(active, state)
            outcome = CampaignOutcome(CampaignStatus.WAIT, "SuccessorMigrationDue")
        else:
            # Reuse the same identity backfill as advance, but do not admit or
            # execute an effect.  Historical inspect must not expose an empty or
            # Ticket-shaped Work Run identity after an upgrade.
            state = self._load_or_initialize(active, work)
            outcome = self._outcome(active.handle, state)
        classification = self._current_classification(
            state,
            state["plan_revision_digest"],
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
            revision_lineage=self._revision_lineage_summaries(state),
            human_gate=self._human_gate_summary(state),
        )

    def active_campaigns(self) -> tuple[CampaignHandle, ...]:
        """Return persisted non-terminal Campaigns without hydrating a Plan."""

        try:
            with self._connect_read_only() as connection:
                rows = connection.execute(
                    """
                    SELECT repository, campaign_key
                    FROM v8_execution_kernel_campaigns
                    ORDER BY repository, campaign_key
                    """
                ).fetchall()
        except ExecutionKernelError:
            raise
        except sqlite3.Error as error:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Execution store schema is unreadable",
            ) from error
        active: list[CampaignHandle] = []
        for row in rows:
            handle = CampaignHandle(row["repository"], row["campaign_key"])
            state = self._read_persisted_campaign_without_migration(handle)
            if self._status_from_persisted_state(state) is not CampaignStatus.COMPLETE:
                active.append(handle)
        return tuple(active)

    def watchdog_snapshot(self, handle: CampaignHandle) -> "WatchdogCampaignSnapshot":
        state = self._read_persisted_campaign_without_migration(handle)
        from .campaign_watchdog import WatchdogCampaignSnapshot

        return WatchdogCampaignSnapshot(
            campaign=handle,
            status=self._status_from_persisted_state(state),
            trusted_progress_digest=self._trusted_progress_digest(state, handle),
            next_check_at=self._snapshot_next_check_at(state),
            active_binding_ids=self._snapshot_active_binding_ids(state),
            diagnosed_binding_ids=self._snapshot_diagnosed_binding_ids(state),
            candidate_receipt_digests=tuple(
                sorted(
                    receipt.digest
                    for _ticket_key, receipt in self._candidate_receipt_records(
                        state, handle
                    )
                )
            ),
            last_wake_refs=tuple(sorted(state.get("last_wake_refs", ()))),
        )

    def read_candidate_receipt(
        self,
        campaign_handle: CampaignHandle,
        ticket_key: str,
    ) -> CandidateReceipt | None:
        state = self._load(campaign_handle)
        if state is None:
            return None
        runs = state.get("runs")
        if type(runs) is not dict or type(ticket_key) is not str:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "ExecutionKernel run state is not a ticket-keyed mapping",
            )
        run = runs.get(ticket_key)
        if run is None:
            return None
        if type(run) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "ExecutionKernel Work Run state is not a mapping",
            )
        stored = run.get("candidate_receipt")
        if stored is None:
            return None
        try:
            receipt = CandidateReceipt.from_canonical(stored)
        except CandidateGateError as error:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "stored CandidateReceipt failed canonical readback",
            ) from error
        if (
            receipt.repository != campaign_handle.repository
            or receipt.campaign_key != campaign_handle.campaign_key
            or receipt.campaign_handle != campaign_handle.campaign_key
            or receipt.plan_revision_digest != state.get("plan_revision_digest")
            or receipt.ticket_key != ticket_key
            or receipt.work_run_key != run.get("work_run_key")
        ):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "stored CandidateReceipt is bound to another Campaign or Work Run",
            )
        if run.get("candidate_receipt_digest") != receipt.digest:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "stored CandidateReceipt digest changed during readback",
            )
        return receipt

    def read_candidate_receipts(
        self,
        campaign_handle: CampaignHandle,
    ) -> tuple[tuple[str, CandidateReceipt], ...]:
        state = self._load(campaign_handle)
        if state is None:
            return ()
        runs = state.get("runs")
        if type(runs) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "ExecutionKernel run state is not a mapping",
            )
        values: list[tuple[str, CandidateReceipt]] = []
        for ticket_key in sorted(runs):
            receipt = self.read_candidate_receipt(campaign_handle, ticket_key)
            if receipt is not None:
                values.append((ticket_key, receipt))
        return tuple(values)

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
                source_evidence_digests=(
                    tuple(invalidation_record["source_evidence_digests"])
                    if type(invalidation_record.get("source_evidence_digests")) is list
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
            runtime_binding_id=run.get("runtime_binding_id") or run.get("semantic_action_id"),
            claim_state=run.get("claim_state", "unclaimed"),
            exclusive_resources=tuple(run.get("exclusive_resources", ())),
            work_subject_digest=run.get("work_subject_digest", ""),
            candidate_identity=run.get("candidate_identity"),
            result_digest=run.get("result_digest"),
            evidence_digests=tuple(run.get("evidence_digests", ())),
            last_wake_ref=run.get("last_wake_ref"),
        )

    def _replan_budget_defaults(
        self,
        active: ActivePlanReadback,
        plan: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Build the durable budget envelope from the Policy Witness seam."""

        policy = plan.get("policy")
        if policy is None:
            # The pre-#136 execution fixtures (and persisted campaigns made
            # by them) deliberately contain no Policy Witness projection.
            # They predate the human gate and therefore have no budget
            # contract to initialize.  Do not invent a digest or limits for
            # that legacy state; the human-gate path below still fails closed
            # if it is ever asked to persist a Decision without this envelope.
            return None
        if type(policy) is not dict:
            raise ExecutionKernelError(
                "REPLAN_BUDGET_POLICY_INVALID",
                "active PlanSpec omitted its Policy Witness projection",
            )
        policy_digest = policy.get("digest")
        if (
            type(policy_digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", policy_digest) is None
        ):
            raise ExecutionKernelError(
                "REPLAN_BUDGET_POLICY_INVALID",
                "active PlanSpec Policy Witness digest is invalid",
            )
        reader = getattr(self._plan_control, "read_replan_budget_policy", None)
        if not callable(reader):
            # #135 hosts do not expose the #136 read-only policy seam.  Keep
            # those already-supported campaigns free of a synthetic budget;
            # any human-gate writer later fails closed when it needs one.
            return None
        try:
            from .human_gate import ReplanBudgetPolicy

            budget = reader(active.handle)
        except ExecutionKernelError:
            raise
        except Exception as error:
            # A PlanSpec produced before #136 may carry the historical
            # ``{ref, digest}`` Policy Witness projection while its witness
            # has no ``replan`` object.  Keep that #135/#134 execution path
            # compatible: it must not receive a synthetic budget, but a later
            # human-gate transition will still fail closed because it requires
            # the budget-bearing seam.  A malformed present budget remains a
            # hard failure.
            if (
                getattr(error, "code", None) == "REPLAN_BUDGET_POLICY_INVALID"
                and "omitted its replan budget" in getattr(error, "detail", "")
            ):
                return None
            raise ExecutionKernelError(
                "REPLAN_BUDGET_POLICY_INVALID",
                "PolicyControl replan budget readback failed",
            ) from error
        if type(budget) is not ReplanBudgetPolicy:
            raise ExecutionKernelError(
                "REPLAN_BUDGET_POLICY_INVALID",
                "PlanControl returned an untyped replan budget policy",
            )
        if budget.policy_witness_digest != policy_digest:
            raise ExecutionKernelError(
                "REPLAN_BUDGET_READBACK_INVALID",
                "replan budget policy is bound to another active Policy Witness",
            )
        return {
            "policy_witness_digest": policy_digest,
            "successor_revisions_used": 0,
            "successor_revision_limit": budget.successor_revision_limit,
            "invalidation_limit": budget.repeated_invalidation_limit,
            "obligations": {},
        }

    @staticmethod
    def _validate_replan_budgets(
        value: object,
        expected: Mapping[str, Any],
    ) -> None:
        if type(value) is not dict or set(value) != {
            "policy_witness_digest",
            "successor_revisions_used",
            "successor_revision_limit",
            "invalidation_limit",
            "obligations",
        }:
            raise ExecutionKernelError(
                "REPLAN_BUDGET_READBACK_INVALID",
                "persisted replan budget schema is not closed",
            )
        if (
            value["policy_witness_digest"]
            != expected["policy_witness_digest"]
            or type(value["successor_revisions_used"]) is not int
            or isinstance(value["successor_revisions_used"], bool)
            or value["successor_revisions_used"] < 0
            or value["successor_revision_limit"]
            != expected["successor_revision_limit"]
            or value["invalidation_limit"] != expected["invalidation_limit"]
            or type(value["obligations"]) is not dict
        ):
            raise ExecutionKernelError(
                "REPLAN_BUDGET_READBACK_INVALID",
                "persisted replan budget facts changed",
            )
        for obligation_key, record in value["obligations"].items():
            if (
                type(obligation_key) is not str
                or re.fullmatch(r"[0-9a-f]{64}", obligation_key) is None
                or type(record) is not dict
                or set(record) != {"evidence_digests"}
                or type(record["evidence_digests"]) is not list
                or tuple(record["evidence_digests"])
                != tuple(sorted(set(record["evidence_digests"])))
                or any(
                    type(digest) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                    for digest in record["evidence_digests"]
                )
            ):
                raise ExecutionKernelError(
                    "REPLAN_BUDGET_READBACK_INVALID",
                    "persisted replan obligation evidence is invalid",
                )

    @staticmethod
    def _obligation_key(
        observation: PlanInvalidationObservation,
        run: Mapping[str, Any],
    ) -> str:
        return digest_value(
            {
                "ticket_key": observation.ticket_key,
                "invalidated_obligation": observation.invalidated_obligation,
                "work_subject_digest": run["work_subject_digest"],
            }
        )

    def _record_replan_budget_evidence(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        observation: PlanInvalidationObservation,
        run: Mapping[str, Any],
    ) -> None:
        budgets = state.get("replan_budgets")
        if budgets is None:
            # Legacy #135/#110 PlanSpecs have no Policy Witness projection
            # and consequently no human-gate budget contract.  Preserve
            # their existing invalidation behavior; a human Decision can only
            # be persisted by the budget-bearing path above.
            return
        if type(budgets) is not dict or type(budgets.get("obligations")) is not dict:
            raise ExecutionKernelError(
                "REPLAN_BUDGET_READBACK_INVALID",
                "Plan Invalidation has no durable obligation budget",
            )
        obligation_key = self._obligation_key(observation, run)
        entry = budgets["obligations"].setdefault(
            obligation_key,
            {"evidence_digests": []},
        )
        if type(entry) is not dict or set(entry) != {"evidence_digests"}:
            raise ExecutionKernelError(
                "REPLAN_BUDGET_READBACK_INVALID",
                "Plan Invalidation obligation budget is malformed",
            )
        evidence_digests = entry["evidence_digests"]
        if type(evidence_digests) is not list:
            raise ExecutionKernelError(
                "REPLAN_BUDGET_READBACK_INVALID",
                "Plan Invalidation obligation Evidence is malformed",
            )
        if observation.evidence_digest in evidence_digests:
            return
        evidence_digests.append(observation.evidence_digest)
        evidence_digests.sort()
        limit = budgets.get("invalidation_limit")
        if type(limit) is not int or len(evidence_digests) <= limit:
            return
        self._persist_budget_exhaustion(
            active,
            state,
            self._current_classification(
                state,
                active.current_revision_digest,
            ),
            obligation_key,
            tuple(evidence_digests),
        )

    def _persist_budget_exhaustion(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        classification: PlanInvalidationClassification | None,
        obligation_key: str,
        evidence_digests: tuple[str, ...],
        *,
        detail: str | None = None,
        repeated_invalidations: int | None = None,
    ) -> None:
        existing = self._human_gate_summary(state)
        if existing is not None and existing.phase == "budget_exhausted":
            return
        evidence_digests = tuple(sorted(set(evidence_digests)))
        if not evidence_digests:
            raise ExecutionKernelError(
                "REPLAN_BUDGET_READBACK_INVALID",
                "budget exhaustion omitted its Evidence lineage",
            )
        if classification is None:
            classification_action_id = self._replanning_action_id(
                active,
                evidence_digests,
            )
            snapshot_digest = digest_value(
                {
                    "kind": "gwo.replan-budget-snapshot.v1",
                    "repository": active.handle.repository,
                    "campaign_key": active.handle.campaign_key,
                    "plan_revision_digest": active.current_revision_digest,
                    "evidence_digests": list(evidence_digests),
                    "obligation_key": obligation_key,
                }
            )
        else:
            classification_action_id = classification.action_id
            snapshot_digest = classification.snapshot_digest
        if detail is None:
            detail = (
                "Repeated Plan Invalidation Evidence exhausted the bound for "
                + obligation_key
            )
        identity = {
            "kind": "gwo.replan-budget-decision-id.v1",
            "repository": active.handle.repository,
            "campaign_key": active.handle.campaign_key,
            "plan_revision_digest": active.current_revision_digest,
            "classification_action_id": classification_action_id,
            "snapshot_digest": snapshot_digest,
            "obligation_key": obligation_key,
            "evidence_digests": list(evidence_digests),
        }
        decision_id = "decision:" + digest_value(identity)[:24]
        action_id = "replan:budget:" + digest_value(identity)
        decision = PlanInvalidationDecision(
            code="REPLAN_BUDGET_EXHAUSTED",
            detail=detail,
            required_change="replan_budget",
        )
        record = HumanDecisionRecord(
            decision_id=decision_id,
            campaign=active.handle,
            classification_action_id=action_id,
            plan_revision_digest=active.current_revision_digest,
            evidence_digests=evidence_digests,
            required_change=decision.required_change,
            detail=decision.detail,
            required_source=RequiredDurableSourceChange(
                required_change=decision.required_change,
                source_kind=RequiredDurableSourceChange.source_kind_for(
                    decision.required_change
                ),
                predecessor_source_digest=snapshot_digest,
                required_subject=obligation_key,
                detail=decision.detail,
            ),
        )
        self._persist_human_gate_state(
            active,
            state,
            record,
            phase="budget_exhausted",
            reason_code=decision.code,
            repeated_invalidations=(
                len(evidence_digests)
                if repeated_invalidations is None
                else repeated_invalidations
            ),
        )

    @staticmethod
    def _successor_budget_obligation_key(
        active: ActivePlanReadback,
        classification: PlanInvalidationClassification | None = None,
        *,
        evidence_digests: tuple[str, ...] = (),
    ) -> str:
        if classification is None:
            evidence_digests = tuple(sorted(set(evidence_digests)))
            if not evidence_digests:
                raise ExecutionKernelError(
                    "REPLAN_BUDGET_READBACK_INVALID",
                    "successor budget exhaustion omitted its Evidence lineage",
                )
            classification_action_id = ExecutionKernel._replanning_action_id(
                active,
                evidence_digests,
            )
        else:
            classification_action_id = classification.action_id
            evidence_digests = classification.evidence_digests
        return digest_value(
            {
                "kind": "gwo.replan-successor-budget-obligation.v1",
                "plan_revision_digest": active.current_revision_digest,
                "classification_action_id": classification_action_id,
                "evidence_digests": list(evidence_digests),
            }
        )

    @staticmethod
    def _successor_revision_budget_exhausted(
        state: Mapping[str, Any],
    ) -> bool:
        budgets = state.get("replan_budgets")
        if budgets is None:
            # Historical #135 state has no #136 Policy Witness budget and must
            # retain its pre-budget successor behavior.
            return False
        if type(budgets) is not dict:
            raise ExecutionKernelError(
                "REPLAN_BUDGET_READBACK_INVALID",
                "successor activation has no durable replan budget envelope",
            )
        used = budgets.get("successor_revisions_used")
        limit = budgets.get("successor_revision_limit")
        if (
            type(used) is not int
            or isinstance(used, bool)
            or type(limit) is not int
            or isinstance(limit, bool)
            or used < 0
            or limit < 1
        ):
            raise ExecutionKernelError(
                "REPLAN_BUDGET_READBACK_INVALID",
                "successor activation budget counters are invalid",
            )
        return used >= limit

    def _persist_budget_exhaustion_readback(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
    ) -> None:
        self._save(active.handle, state)
        persisted = self._load(active.handle)
        if persisted is None or persisted.get("human_gate") != state.get("human_gate"):
            raise ExecutionKernelError(
                "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                "budget exhaustion Decision did not read back exactly",
            )
        state.clear()
        state.update(persisted)

    def _exhaust_successor_revision_budget(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        classification: PlanInvalidationClassification,
    ) -> None:
        budgets = state.get("replan_budgets")
        if type(budgets) is not dict:
            raise ExecutionKernelError(
                "REPLAN_BUDGET_READBACK_INVALID",
                "successor activation has no durable replan budget envelope",
            )
        self._persist_budget_exhaustion(
            active,
            state,
            classification,
            self._successor_budget_obligation_key(active, classification),
            classification.evidence_digests,
            detail=(
                "Successor Plan Revision limit exhausted before activation: "
                f"{budgets['successor_revisions_used']} of "
                f"{budgets['successor_revision_limit']} revisions used"
            ),
            repeated_invalidations=0,
        )
        self._persist_budget_exhaustion_readback(active, state)

    @staticmethod
    def _human_planning_action_id(
        decision_id: str,
        source_readback_digest: str,
        predecessor_revision_digest: str,
    ) -> str:
        try:
            return _derive_human_planning_action_id(
                decision_id,
                source_readback_digest,
                predecessor_revision_digest,
            )
        except Exception as error:
            raise ExecutionKernelError(
                "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                "human Decision cannot bind a complete successor action",
            ) from error

    @staticmethod
    def _human_gate_summary(
        state: Mapping[str, Any],
    ) -> HumanGateSummary | None:
        raw = state.get("human_gate")
        if raw is None:
            return None
        if type(raw) is not dict or type(raw.get("summary")) is not dict:
            raise ExecutionKernelError(
                "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                "human gate state omitted its exact inspect summary",
            )
        try:
            return HumanGateSummary.from_canonical(raw["summary"])
        except Exception as error:
            if isinstance(error, ExecutionKernelError):
                raise
            raise ExecutionKernelError(
                "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                "human gate inspect summary did not read back",
            ) from error

    def _persist_human_gate_state(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        record: HumanDecisionRecord,
        *,
        phase: str,
        reason_code: str,
        choice: HumanDecisionChoice | None = None,
        source_readback: HumanSourceReadback | None = None,
        planning_action_id: str | None = None,
        successor_revision_digest: str | None = None,
        repeated_invalidations: int = 0,
    ) -> None:
        budgets = state.get("replan_budgets")
        if budgets is None:
            raise ExecutionKernelError(
                "REPLAN_BUDGET_POLICY_INVALID",
                "human gate requires a durable replan budget policy",
            )
        if type(budgets) is not dict:
            raise ExecutionKernelError(
                "REPLAN_BUDGET_READBACK_INVALID",
                "human gate budget envelope is malformed",
            )
        successor_used = budgets.get("successor_revisions_used")
        successor_limit = budgets.get("successor_revision_limit")
        invalidation_limit = budgets.get("invalidation_limit")
        if (
            type(successor_used) is not int
            or type(successor_limit) is not int
            or type(invalidation_limit) is not int
        ):
            raise ExecutionKernelError(
                "REPLAN_BUDGET_READBACK_INVALID",
                "human gate budget counters are invalid",
            )
        summary = HumanGateSummary(
            phase=phase,
            decision_id=record.decision_id,
            classification_action_id=record.classification_action_id,
            required_change=record.required_change,
            evidence_digests=record.evidence_digests,
            required_source_kind=record.required_source.source_kind,
            reason_code=reason_code,
            source_readback_digest=(
                None if source_readback is None else source_readback.readback_digest
            ),
            planning_action_id=planning_action_id,
            predecessor_revision_digest=record.plan_revision_digest,
            successor_revision_digest=successor_revision_digest,
            successor_revisions_used=successor_used,
            successor_revision_limit=successor_limit,
            repeated_invalidations=repeated_invalidations,
            repeated_invalidation_limit=invalidation_limit,
        )
        state["human_gate"] = {
            "decision": record.canonical(),
            "decision_digest": record.digest,
            "choice": None if choice is None else choice.canonical(),
            "source_readback": (
                None
                if source_readback is None
                else source_readback.canonical()
            ),
            "summary": summary.canonical(),
        }
        from ._canonical import canonical_bytes

        if canonical_bytes(record.canonical()) != canonical_bytes(
            HumanDecisionRecord.from_canonical(record.canonical()).canonical()
        ):
            raise ExecutionKernelError(
                "HUMAN_DECISION_RECORD_INVALID",
                "human Decision did not round-trip canonically",
            )

    def _persist_human_gate_attempt(
        self,
        *,
        campaign: CampaignHandle,
        decision: HumanDecisionRecord,
        source_readback: HumanSourceReadback,
        predecessor_revision_digest: str,
        state: str,
        compilation_record_artifact_digest: str | None = None,
        activation_receipt_digest: str | None = None,
    ) -> None:
        """Persist the human attempt before/after each external commit point."""

        from .human_gate import HumanGateAttempt

        if (
            type(decision) is not HumanDecisionRecord
            or decision.campaign != campaign
            or type(source_readback) is not HumanSourceReadback
            or not source_readback.approved
        ):
            raise ExecutionKernelError(
                "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                "human attempt is not bound to an approved source readback",
            )
        reader = getattr(self._plan_control, "read_human_gate_attempt", None)
        saver = getattr(self._plan_control, "save_human_gate_attempt", None)
        if not callable(reader) or not callable(saver):
            raise ExecutionKernelError(
                "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                "PlanControl omitted durable human attempt readback",
            )
        planning_action_id = self._human_planning_action_id(
            decision.decision_id,
            source_readback.readback_digest,
            predecessor_revision_digest,
        )
        existing = reader(
            campaign,
            decision.decision_id,
            source_readback.readback_digest,
        )
        if existing is not None:
            if type(existing) is not HumanGateAttempt:
                raise ExecutionKernelError(
                    "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                    "durable human attempt is not typed",
                )
            if (
                existing.decision_id != decision.decision_id
                or existing.campaign != campaign
                or existing.predecessor_revision_digest
                != predecessor_revision_digest
                or existing.source_readback_digest
                != source_readback.readback_digest
                or existing.tracker_source_digest
                != source_readback.tracker_source_digest
                or existing.policy_witness_digest
                != source_readback.policy_witness_digest
                or existing.planning_action_id != planning_action_id
                or existing.planning_protocol_id != REPLANNING_OUTPUT_PROTOCOL_ID
            ):
                raise ExecutionKernelError(
                    "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                    "durable human attempt identity changed",
                )
            if existing.state == "active_successor":
                return
            if compilation_record_artifact_digest is None:
                compilation_record_artifact_digest = (
                    existing.compilation_record_artifact_digest
                )
            if activation_receipt_digest is None:
                activation_receipt_digest = existing.activation_receipt_digest
        attempt = HumanGateAttempt(
            decision_id=decision.decision_id,
            campaign=campaign,
            predecessor_revision_digest=predecessor_revision_digest,
            source_readback_digest=source_readback.readback_digest,
            tracker_source_digest=source_readback.tracker_source_digest,
            policy_witness_digest=source_readback.policy_witness_digest,
            planning_action_id=planning_action_id,
            planning_protocol_id=REPLANNING_OUTPUT_PROTOCOL_ID,
            state=state,
            compilation_record_artifact_digest=compilation_record_artifact_digest,
            activation_receipt_digest=activation_receipt_digest,
        )
        try:
            saved = saver(attempt)
            observed = reader(
                campaign,
                decision.decision_id,
                source_readback.readback_digest,
            )
        except ExecutionKernelError:
            raise
        except Exception as error:
            raise ExecutionKernelError(
                "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                "human attempt could not be persisted",
            ) from error
        if observed != saved or observed != attempt:
            raise ExecutionKernelError(
                "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                "human attempt did not read back exactly",
            )

    def _ensure_human_gate(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        classification: PlanInvalidationClassification,
    ) -> None:
        """Persist one stable Decision and its quiescent inspect projection."""

        existing = state.get("human_gate")
        if existing is not None:
            if type(existing) is not dict:
                raise ExecutionKernelError(
                    "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                    "human gate state is not an object",
                )
            summary = self._human_gate_summary(state)
            if (
                summary is None
                or summary.classification_action_id != classification.action_id
                or summary.predecessor_revision_digest
                != active.current_revision_digest
                or summary.evidence_digests != classification.evidence_digests
            ):
                raise ExecutionKernelError(
                    "HUMAN_DECISION_CONFLICT",
                    "persisted human Decision is bound to another invalidation",
                )
            return
        decision = classification.decision
        if decision is None:
            raise ExecutionKernelError(
                "PLAN_INVALIDATION_DECISION_INVALID",
                "human classification omitted its named Decision",
            )
        require = getattr(self._plan_control, "require_human_decision", None)
        if callable(require):
            try:
                record = require(active.handle, classification)
            except ExecutionKernelError:
                raise
            except Exception as error:
                raise ExecutionKernelError(
                    "HUMAN_DECISION_READBACK_INVALID",
                    "PlanControl did not durably save the human Decision",
                ) from error
            if type(record) is not HumanDecisionRecord:
                raise ExecutionKernelError(
                    "HUMAN_DECISION_READBACK_INVALID",
                    "PlanControl returned an untyped human Decision",
                )
        else:
            # Compatibility for the narrow #134 reader double.  Production
            # host composition always exposes the durable PlanControl seam;
            # this fallback is deliberately never used by a writer.
            identity = {
                "kind": "gwo.human-decision-id.v1",
                "repository": active.handle.repository,
                "campaign_key": active.handle.campaign_key,
                "classification_action_id": classification.action_id,
                "plan_revision_digest": active.current_revision_digest,
                "evidence_digests": list(classification.evidence_digests),
                "decision": decision.canonical(),
            }
            decision_id = "decision:" + digest_value(identity)[:24]
            source_kind = RequiredDurableSourceChange.source_kind_for(
                decision.required_change
            )
            required_source = RequiredDurableSourceChange(
                required_change=decision.required_change,
                source_kind=source_kind,
                predecessor_source_digest=classification.snapshot_digest,
                required_subject=(
                    f"{active.handle.campaign_key}:{decision.required_change}"
                ),
                detail=decision.detail,
            )
            record = HumanDecisionRecord(
                decision_id=decision_id,
                campaign=active.handle,
                classification_action_id=classification.action_id,
                plan_revision_digest=active.current_revision_digest,
                evidence_digests=classification.evidence_digests,
                required_change=decision.required_change,
                detail=decision.detail,
                required_source=required_source,
            )
        self._persist_human_gate_state(
            active,
            state,
            record,
            phase="awaiting_human_choice",
            reason_code=decision.code,
        )

    def _advance_human_gate(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        choice: HumanDecisionChoice,
    ) -> tuple[
        ActivePlanReadback,
        dict[str, dict[str, Any]],
        dict[str, Any],
    ]:
        if type(choice) is not HumanDecisionChoice:
            raise ExecutionKernelError(
                "HUMAN_APPROVAL_INPUT_INVALID",
                "advance requires a typed HumanDecisionChoice",
            )
        gate = state.get("human_gate")
        if type(gate) is not dict or type(gate.get("decision")) is not dict:
            raise ExecutionKernelError(
                "HUMAN_DECISION_READBACK_INVALID",
                "human approval has no durable Decision to continue",
            )
        try:
            decision = HumanDecisionRecord.from_canonical(gate["decision"])
        except Exception as error:
            raise ExecutionKernelError(
                "HUMAN_DECISION_READBACK_INVALID",
                "persisted human Decision cannot be hydrated",
            ) from error
        if decision.campaign != active.handle or choice.decision_id != decision.decision_id:
            raise ExecutionKernelError(
                "HUMAN_APPROVAL_INPUT_INVALID",
                "human approval is bound to another Campaign Decision",
            )

        summary = self._human_gate_summary(state)
        if summary is None:
            raise ExecutionKernelError(
                "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                "human approval has no inspect summary",
            )
        if summary.phase in {"active_successor", "budget_exhausted", "rejected_change"}:
            return active, self._authoritative_active(active.handle)[1], state

        if self._successor_revision_budget_exhausted(state):
            classification = self._current_classification(
                state,
                active.current_revision_digest,
            )
            if (
                classification is None
                or classification.disposition
                is not PlanInvalidationDisposition.REQUIRE_HUMAN_DECISION
            ):
                raise ExecutionKernelError(
                    "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                    "human successor budget lost its human classification",
                )
            self._exhaust_successor_revision_budget(active, state, classification)
            return active, self._authoritative_active(active.handle)[1], state

        readback = self._read_human_decision_source(active, decision, choice)
        if type(readback) is not HumanSourceReadback:
            raise ExecutionKernelError(
                "HUMAN_SOURCE_READBACK_INVALID",
                "PlanControl returned an untyped human source readback",
            )
        if readback.decision_id != decision.decision_id:
            raise ExecutionKernelError(
                "HUMAN_SOURCE_READBACK_INVALID",
                "human source readback names another Decision",
            )

        if readback.state == "pending":
            phase = "awaiting_durable_tracker_policy_readback"
        elif readback.state == "approved":
            phase = "planning_validated_successor"
        elif readback.state == "rejected":
            phase = "rejected_change"
        else:
            phase = "awaiting_durable_tracker_policy_readback"

        if readback.approved:
            classification = self._current_classification(
                state,
                active.current_revision_digest,
            )
            if (
                classification is None
                or classification.disposition
                is not PlanInvalidationDisposition.REQUIRE_HUMAN_DECISION
                or classification.decision is None
                or classification.decision.required_change != decision.required_change
            ):
                raise ExecutionKernelError(
                    "HUMAN_SUCCESSOR_TRANSITION_READBACK_INVALID",
                    "approved human source lost its original human classification",
                )
            intent = self._human_successor_transition_intent(
                active,
                classification,
                decision,
                readback,
            )
            state["human_successor_transition"] = intent
            self._persist_human_gate_state(
                active,
                state,
                decision,
                phase="planning_validated_successor",
                reason_code=readback.reason_code,
                choice=choice,
                source_readback=readback,
                planning_action_id=intent["classification_action_id"],
                repeated_invalidations=summary.repeated_invalidations,
            )
            self._persist_human_gate_attempt(
                campaign=active.handle,
                decision=decision,
                source_readback=readback,
                predecessor_revision_digest=active.current_revision_digest,
                state="planning_validated_successor",
            )
        else:
            self._persist_human_gate_state(
                active,
                state,
                decision,
                phase=phase,
                reason_code=readback.reason_code,
                choice=choice,
                source_readback=readback,
                repeated_invalidations=summary.repeated_invalidations,
            )
        self._save(active.handle, state)
        persisted = self._load(active.handle)
        if persisted is None or persisted.get("human_gate") != state.get("human_gate"):
            raise ExecutionKernelError(
                "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                "human source readback did not persist exactly",
        )
        state.clear()
        state.update(persisted)

        if not readback.approved:
            return active, self._authoritative_active(active.handle)[1], state

        continuation = getattr(self._plan_control, "advance_human_decision", None)
        if not callable(continuation):
            raise ExecutionKernelError(
                "HUMAN_APPROVAL_UNAUTHORIZED",
                "PlanControl omitted the approved human activation seam",
            )
        try:
            activated_readback = continuation(active.handle, decision, choice)
        except ExecutionKernelError:
            raise
        except Exception as error:
            raise ExecutionKernelError(
                "HUMAN_SUCCESSOR_ACTIVATION_FAILED",
                "PlanControl approved human successor activation did not complete",
            ) from error
        if type(activated_readback) is not HumanSourceReadback or activated_readback != readback:
            raise ExecutionKernelError(
                "HUMAN_SUCCESSOR_ACTIVATION_READBACK_INVALID",
                "approved human activation did not return the durable source readback",
            )
        return self._finish_human_successor(active, state)

    def _read_human_decision_source(
        self,
        active: ActivePlanReadback,
        decision: HumanDecisionRecord,
        choice: HumanDecisionChoice,
    ) -> HumanSourceReadback:
        reader = getattr(self._plan_control, "read_human_decision_source", None)
        try:
            if callable(reader):
                readback = reader(active.handle, decision, choice)
            else:
                continuation = getattr(
                    self._plan_control,
                    "advance_human_decision",
                    None,
                )
                if not callable(continuation):
                    raise ExecutionKernelError(
                        "HUMAN_APPROVAL_UNAUTHORIZED",
                        "PlanControl omitted the read-only human source seam",
                    )
                try:
                    readback = continuation(
                        active.handle,
                        decision,
                        choice,
                        _read_only=True,
                    )
                except TypeError as error:
                    raise ExecutionKernelError(
                        "HUMAN_APPROVAL_UNAUTHORIZED",
                        "PlanControl host must expose read_human_decision_source",
                    ) from error
        except ExecutionKernelError:
            raise
        except Exception as error:
            raise ExecutionKernelError(
                "HUMAN_SOURCE_READBACK_INVALID",
                "PlanControl human source readback did not complete",
            ) from error
        if type(readback) is not HumanSourceReadback:
            raise ExecutionKernelError(
                "HUMAN_SOURCE_READBACK_INVALID",
                "PlanControl returned an untyped human source readback",
            )
        return readback

    @staticmethod
    def _human_successor_transition_intent(
        active: ActivePlanReadback,
        classification: PlanInvalidationClassification,
        decision: HumanDecisionRecord,
        readback: HumanSourceReadback,
    ) -> dict[str, Any]:
        return {
            "kind": "human_successor_transition.v1",
            "decision_id": decision.decision_id,
            "classification_action_id": ExecutionKernel._human_planning_action_id(
                decision.decision_id,
                readback.readback_digest,
                active.current_revision_digest,
            ),
            "classification_digest": None,
            "snapshot_digest": classification.snapshot_digest,
            "previous_revision_digest": active.current_revision_digest,
            "evidence_digests": list(decision.evidence_digests),
            "source_readback_digest": readback.readback_digest,
            "state": "activation_due",
        }

    def _finish_human_successor(
        self,
        predecessor: ActivePlanReadback,
        state: dict[str, Any],
        *,
        predecessor_revision_digest: str | None = None,
    ) -> tuple[
        ActivePlanReadback,
        dict[str, dict[str, Any]],
        dict[str, Any],
    ]:
        """Read back the human activation and commit its Kernel transition."""

        previous_digest = (
            predecessor.current_revision_digest
            if predecessor_revision_digest is None
            else predecessor_revision_digest
        )
        fresh, successor_work = self._authoritative_active(predecessor.handle)
        transition = state.get("human_successor_transition")
        if type(transition) is not dict:
            raise ExecutionKernelError(
                "HUMAN_SUCCESSOR_TRANSITION_READBACK_INVALID",
                "approved human successor omitted its durable transition intent",
            )
        if (
            fresh.current_revision_digest == previous_digest
            or fresh.activation_receipt.expected_previous_revision_digest
            != previous_digest
            or fresh.activation_receipt.planning_stable_action_id
            != transition.get("classification_action_id")
        ):
            raise ExecutionKernelError(
                "HUMAN_SUCCESSOR_ACTIVATION_READBACK_INVALID",
                "approved human successor Activation Receipt is not human-bound",
            )
        decision = HumanDecisionRecord.from_canonical(
            state["human_gate"]["decision"]
        )
        source = HumanSourceReadback.from_canonical(
            state["human_gate"]["source_readback"]
        )
        classification = self._human_successor_classification(
            state,
            transition,
            decision,
            source,
            fresh,
        )
        transition = {
            **transition,
            "classification_digest": classification.digest,
            "state": "activated",
        }
        state["human_successor_transition"] = transition
        state["human_successor_classification"] = classification.canonical()
        choice = HumanDecisionChoice.from_canonical(state["human_gate"]["choice"])
        summary = self._human_gate_summary(state)
        assert summary is not None  # guarded by the durable gate write
        self._persist_human_gate_state(
            predecessor,
            state,
            decision,
            phase="active_successor",
            reason_code=source.reason_code,
            choice=choice,
            source_readback=source,
            planning_action_id=transition["classification_action_id"],
            successor_revision_digest=fresh.current_revision_digest,
            repeated_invalidations=summary.repeated_invalidations,
        )
        self._save(predecessor.handle, state)
        persisted = self._load(predecessor.handle)
        if persisted is None or persisted.get("human_successor_transition") != transition:
            raise ExecutionKernelError(
                "HUMAN_SUCCESSOR_TRANSITION_READBACK_INVALID",
                "human successor transition did not read back exactly",
            )
        state.clear()
        state.update(persisted)
        try:
            plan = load_canonical_json(fresh.plan_spec_bytes)
        except CanonicalJsonError as error:
            raise ExecutionKernelError(
                "SUCCESSOR_ACTIVATION_READBACK_INVALID",
                "human successor PlanSpec is not canonical",
            ) from error
        reconciled = self._reconcile_successor_revision(
            fresh,
            state,
            successor_work,
            plan,
        )
        self._persist_human_gate_attempt(
            campaign=predecessor.handle,
            decision=decision,
            source_readback=source,
            predecessor_revision_digest=previous_digest,
            state="active_successor",
            compilation_record_artifact_digest=(
                fresh.activation_receipt.compilation_record_artifact_digest
            ),
            activation_receipt_digest=digest_value(
                fresh.activation_receipt.__dict__
            ),
        )
        return (
            fresh,
            successor_work,
            reconciled,
        )

    @staticmethod
    def _human_successor_requires_resume(
        active: ActivePlanReadback,
        state: Mapping[str, Any],
    ) -> bool:
        transition = state.get("human_successor_transition")
        if type(transition) is not dict:
            return False
        if transition.get("state") not in {"activation_due", "activated"}:
            return False
        return (
            transition.get("state") == "activation_due"
            or state.get("plan_revision_digest") != active.current_revision_digest
        )

    @staticmethod
    def _human_successor_activation_crossed(
        active: ActivePlanReadback,
        state: Mapping[str, Any],
    ) -> bool:
        """Recognize the recoverable receipt window before human finalization."""

        transition = state.get("human_successor_transition")
        if type(transition) is not dict or transition.get("state") != "activation_due":
            return False
        previous_digest = transition.get("previous_revision_digest")
        action_id = transition.get("classification_action_id")
        return (
            type(previous_digest) is str
            and state.get("plan_revision_digest") == previous_digest
            and active.current_revision_digest != previous_digest
            and active.activation_receipt.expected_previous_revision_digest
            == previous_digest
            and active.activation_receipt.planning_stable_action_id == action_id
        )

    def _resume_human_successor(
        self,
        active: ActivePlanReadback,
        work: dict[str, dict[str, Any]],
        state: dict[str, Any],
    ) -> tuple[
        ActivePlanReadback,
        dict[str, dict[str, Any]],
        dict[str, Any],
    ]:
        transition = state.get("human_successor_transition")
        if type(transition) is not dict:
            raise ExecutionKernelError(
                "HUMAN_SUCCESSOR_TRANSITION_READBACK_INVALID",
                "human successor restart omitted its transition intent",
            )
        previous_digest = transition.get("previous_revision_digest")
        if type(previous_digest) is not str:
            raise ExecutionKernelError(
                "HUMAN_SUCCESSOR_TRANSITION_READBACK_INVALID",
                "human successor transition omitted its predecessor revision",
            )
        try:
            decision = HumanDecisionRecord.from_canonical(
                state["human_gate"]["decision"]
            )
            source = HumanSourceReadback.from_canonical(
                state["human_gate"]["source_readback"]
            )
        except Exception as error:
            raise ExecutionKernelError(
                "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                "human successor restart cannot hydrate its durable source lineage",
            ) from error
        reader = getattr(self._plan_control, "read_human_gate_attempt", None)
        if not callable(reader):
            raise ExecutionKernelError(
                "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                "PlanControl omitted durable human attempt readback",
            )
        try:
            durable_attempt = reader(
                active.handle,
                decision.decision_id,
                source.readback_digest,
            )
        except ExecutionKernelError:
            raise
        except Exception as error:
            raise ExecutionKernelError(
                "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                "durable human attempt could not be read during restart",
            ) from error
        from .human_gate import HumanGateAttempt

        expected_action_id = self._human_planning_action_id(
            decision.decision_id,
            source.readback_digest,
            previous_digest,
        )
        if type(durable_attempt) is not HumanGateAttempt or (
            durable_attempt.campaign != active.handle
            or durable_attempt.predecessor_revision_digest != previous_digest
            or durable_attempt.source_readback_digest != source.readback_digest
            or durable_attempt.tracker_source_digest != source.tracker_source_digest
            or durable_attempt.policy_witness_digest != source.policy_witness_digest
            or durable_attempt.planning_action_id != expected_action_id
            or durable_attempt.state
            not in {"planning_validated_successor", "active_successor"}
        ):
            raise ExecutionKernelError(
                "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                "human successor restart lacks the exact durable attempt lineage",
            )
        if active.current_revision_digest != previous_digest:
            return self._finish_human_successor(
                active,
                state,
                predecessor_revision_digest=previous_digest,
            )
        try:
            choice = HumanDecisionChoice.from_canonical(
                state["human_gate"]["choice"]
            )
        except Exception as error:
            raise ExecutionKernelError(
                "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                "human successor restart cannot hydrate its durable choice",
            ) from error
        if self._successor_revision_budget_exhausted(state):
            classification = self._current_classification(
                state,
                previous_digest,
            )
            if (
                classification is None
                or classification.disposition
                is not PlanInvalidationDisposition.REQUIRE_HUMAN_DECISION
            ):
                raise ExecutionKernelError(
                    "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                    "human successor restart lost its human classification",
                )
            self._exhaust_successor_revision_budget(active, state, classification)
            return active, work, state
        continuation = getattr(self._plan_control, "advance_human_decision", None)
        if not callable(continuation):
            raise ExecutionKernelError(
                "HUMAN_APPROVAL_UNAUTHORIZED",
                "PlanControl omitted the approved human activation seam",
            )
        try:
            readback = continuation(active.handle, decision, choice)
        except ExecutionKernelError:
            raise
        except Exception as error:
            raise ExecutionKernelError(
                "HUMAN_SUCCESSOR_ACTIVATION_FAILED",
                "human successor restart activation did not complete",
            ) from error
        expected = HumanSourceReadback.from_canonical(
            state["human_gate"]["source_readback"]
        )
        if type(readback) is not HumanSourceReadback or readback != expected:
            raise ExecutionKernelError(
                "HUMAN_SUCCESSOR_ACTIVATION_READBACK_INVALID",
                "human successor restart returned a different source readback",
            )
        return self._finish_human_successor(active, state)

    def _human_successor_classification(
        self,
        state: Mapping[str, Any],
        transition: Mapping[str, Any],
        decision: HumanDecisionRecord,
        source: HumanSourceReadback,
        fresh: ActivePlanReadback,
    ) -> PlanInvalidationClassification:
        raw = state.get("human_successor_classification")
        if raw is not None:
            classification = self._decode_classification(raw)
            if (
                classification.action_id != transition.get("classification_action_id")
                or classification.disposition
                is not PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR
            ):
                raise ExecutionKernelError(
                    "HUMAN_SUCCESSOR_TRANSITION_READBACK_INVALID",
                    "human successor classification is not an approved human action",
                )
            return classification
        original = self._current_classification(
            state,
            transition["previous_revision_digest"],
        )
        if (
            original is None
            or original.disposition
            is not PlanInvalidationDisposition.REQUIRE_HUMAN_DECISION
            or original.decision is None
            or original.decision.required_change != decision.required_change
        ):
            raise ExecutionKernelError(
                "HUMAN_SUCCESSOR_TRANSITION_READBACK_INVALID",
                "human successor lost its original human Decision classification",
            )
        return PlanInvalidationClassification(
            action_id=transition["classification_action_id"],
            snapshot_digest=transition["snapshot_digest"],
            plan_revision_digest=transition["previous_revision_digest"],
            evidence_digests=decision.evidence_digests,
            disposition=PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR,
            reason=(
                "approved human source "
                + source.source_change_digest
                if source.source_change_digest is not None
                else "approved human source"
            ),
            capability_proof_digest=original.capability_proof_digest,
            successor_ticket_keys=tuple(sorted(item.ticket_key for item in fresh.claim_proofs)),
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
        budget_defaults = self._replan_budget_defaults(active, plan)
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
                    "runtime_binding_id": None,
                    "resume_ordinal": 0,
                    "next_check_at": None,
                    "last_trusted_progress_at": None,
                    "stale_due_at": None,
                    "stale_readback_action_id": None,
                     "stale_diagnosis_action_id": None,
                     "stale_disposition": None,
                     "diagnosed_binding_ids": [],
                     "stale_slot_release_pending": False,
                     "binding_replacement_ordinal": 0,
                     "terminal_binding_evidence": None,
                     "runtime_agent_id": None,
                     "runtime_session_id": None,
                     "runtime_workspace_id": None,
                     "candidate_submission_count": 0,
                     "process_state": "unknown",
                     "workspace_state": "unknown",
                     "check_state": "unknown",
                     "transcript_tail": [],
                     "stale_diagnosis_packet": None,
                     "stale_diagnosis_packet_digest": None,
                     "stale_diagnosis_packet_identity": None,
                     "stale_follow_up_action_id": None,
                     "stale_follow_up_kind": None,
                     "stale_follow_up_completed": True,
                    "work_subject_digest": subject_digest,
                    "work_run_key": (
                        work_run_key(key, subject_digest)
                        if _has_revision_identity_facts(plan, work[key])
                        else f"work-run:{key}"
                    ),
                    "exclusive_resources": list(work[key].get("exclusive_resources", [])),
                    "claim_state": "unclaimed",
                    "candidate_identity": None,
                    "candidate_receipt": None,
                    "candidate_receipt_digest": None,
                    "candidate_commit_oids": [],
                    "candidate_receipt_digests": [],
                    "trusted_progress_revision": 0,
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
                "last_wake_refs": [],
                "trusted_progress_revision": 0,
                "diagnosed_binding_ids": [],
                "normalized_permission_receipts": [],
                "candidate_receipts": [],
                "delivery_receipts": [],
                "plan_invalidation": {},
                "plan_invalidation_resolutions": {},
                "plan_invalidation_classifications": {},
                "accepted_results": [],
                "revision_lineage": [],
            }
            if budget_defaults is not None:
                state["replan_budgets"] = budget_defaults
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
        dirty = False
        for field, default in (
            ("plan_invalidation", {}),
            ("plan_invalidation_resolutions", {}),
            ("plan_invalidation_classifications", {}),
            ("accepted_results", []),
            ("revision_lineage", []),
            ("last_wake_refs", []),
            ("trusted_progress_revision", 0),
            ("diagnosed_binding_ids", []),
            ("normalized_permission_receipts", []),
            ("candidate_receipts", []),
            ("delivery_receipts", []),
        ):
            if field not in state:
                state[field] = default
                dirty = True
        if type(state["revision_lineage"]) is not list:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "ExecutionKernel revision lineage is not a list",
            )
        state.setdefault("effects", {})
        if type(state["effects"]) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID", "ExecutionKernel effects are not a mapping"
            )
        if budget_defaults is None:
            if "replan_budgets" in state:
                raise ExecutionKernelError(
                    "REPLAN_BUDGET_READBACK_INVALID",
                    "persisted replan budgets have no active Policy Witness",
                )
        elif "replan_budgets" not in state:
            state["replan_budgets"] = budget_defaults
            dirty = True
        else:
            self._validate_replan_budgets(
                state["replan_budgets"],
                budget_defaults,
            )
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
            if "candidate_receipt" not in run:
                run.setdefault("candidate_receipt", None)
                dirty = True
            if "candidate_receipt_digest" not in run:
                run["candidate_receipt_digest"] = None
                dirty = True
            if "candidate_commit_oids" not in run:
                run.setdefault("candidate_commit_oids", [])
                dirty = True
            if "candidate_receipt_digests" not in run:
                run.setdefault("candidate_receipt_digests", [])
                dirty = True
            if "trusted_progress_revision" not in run:
                run["trusted_progress_revision"] = 0
                dirty = True
            for field, default in (
                ("runtime_binding_id", None),
                ("last_trusted_progress_at", None),
                ("stale_due_at", None),
                ("stale_readback_action_id", None),
                ("stale_diagnosis_action_id", None),
                ("stale_disposition", None),
                ("diagnosed_binding_ids", []),
                ("stale_slot_release_pending", False),
                ("binding_replacement_ordinal", 0),
                ("terminal_binding_evidence", None),
                ("runtime_agent_id", None),
                ("runtime_session_id", None),
                ("runtime_workspace_id", None),
                ("candidate_submission_count", 0),
                ("process_state", "unknown"),
                ("workspace_state", "unknown"),
                ("check_state", "unknown"),
                ("transcript_tail", []),
                ("stale_diagnosis_packet", None),
                ("stale_diagnosis_packet_digest", None),
                ("stale_diagnosis_packet_identity", None),
                ("stale_follow_up_action_id", None),
                ("stale_follow_up_kind", None),
                ("stale_follow_up_completed", True),
            ):
                if field not in run:
                    run[field] = default.copy() if isinstance(default, list) else default
                    dirty = True
            if run.get("phase") in _SLOT_PHASES:
                last_progress = run.get("last_trusted_progress_at")
                stale_due = run.get("stale_due_at")
                if last_progress is None and stale_due is None:
                    now = self._clock_value()
                    run["last_trusted_progress_at"] = self._timestamp(now)
                    run["stale_due_at"] = self._timestamp(
                        now
                        + timedelta(
                            seconds=self._configuration.stale_after_seconds_for(
                                active.handle.repository
                            )
                        )
                    )
                    dirty = True
                elif (
                    (last_progress is None) != (stale_due is None)
                    and not (
                        last_progress is not None
                        and stale_due is None
                        and run.get("stale_disposition") is not None
                    )
                ):
                    raise ExecutionKernelError(
                        "EXECUTION_STORE_INVALID",
                        "stale progress timestamp pair is incomplete",
                    )
                for timestamp, label in (
                    (run.get("last_trusted_progress_at"), "last trusted progress time"),
                    (run.get("stale_due_at"), "stale due time"),
                ):
                    if timestamp is None:
                        continue
                    if type(timestamp) is not str:
                        raise ExecutionKernelError(
                            "EXECUTION_STORE_INVALID",
                            f"{label} is not canonical text",
                        )
                    try:
                        parsed_timestamp = datetime.fromisoformat(timestamp)
                    except ValueError as error:
                        raise ExecutionKernelError(
                            "EXECUTION_STORE_INVALID",
                            f"{label} is unreadable",
                        ) from error
                    if (
                        parsed_timestamp.tzinfo is None
                        or parsed_timestamp.utcoffset() != timedelta(0)
                        or parsed_timestamp.isoformat() != timestamp
                    ):
                        raise ExecutionKernelError(
                            "EXECUTION_STORE_INVALID",
                            f"{label} is not canonical UTC text",
                        )
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

        if state["candidate_receipts"] == []:
            existing_receipts = [
                run["candidate_receipt"]
                for _ticket_key, run in sorted(state["runs"].items())
                if type(run) is dict and run.get("candidate_receipt") is not None
            ]
            if existing_receipts:
                state["candidate_receipts"] = existing_receipts
                dirty = True
        if state["last_wake_refs"] == []:
            existing_wakes = sorted(
                {
                    run.get("last_wake_ref")
                    for run in state["runs"].values()
                    if type(run) is dict and run.get("last_wake_ref") is not None
                }
            )
            if existing_wakes:
                state["last_wake_refs"] = existing_wakes
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
            # Typed stale effects already carry their own stable identity.
            # They are not legacy semantic effects and must never be inferred
            # as a resume merely because their identity differs from the
            # semantic binding.  Keeping the key also preserves readback
            # recovery after a restart.
            if type(effect.get("kind")) is str:
                revision_bound_action_id = legacy_action_id
            else:
                revision_bound_action_id = None
            # A successful resume increments ``resume_ordinal`` before the
            # next advance persists the run.  Therefore ``last_action_id``
            # can legitimately be the resume identity for the immediately
            # preceding ordinal, not the current one.  Treat both identities
            # as already revision-bound; otherwise this migration would
            # rewrite a completed resume receipt as the next resume intent
            # and suppress the next real replacement attempt.
            resume_action_ids = {resume_action_id}
            resume_ordinal = run.get("resume_ordinal")
            if type(resume_ordinal) is int and resume_ordinal > 0:
                previous_resume_run = dict(run)
                previous_resume_run["resume_ordinal"] = resume_ordinal - 1
                resume_action_ids.add(
                    self._effect_action_id(
                        active,
                        ticket_key,
                        previous_resume_run,
                        resuming=True,
                    )
                )
            if revision_bound_action_id is None:
                if legacy_action_id == execution_action_id or legacy_action_id in resume_action_ids:
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

    def _load_initialize_or_reconcile_successor(
        self,
        active: ActivePlanReadback,
        work: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Load R0, or reconcile exactly one durably committed successor."""

        state = self._load(active.handle)
        if state is None:
            return self._load_or_initialize(active, work)
        if state.get("plan_revision_digest") == active.current_revision_digest:
            return self._load_or_initialize(active, work)

        if self._human_successor_activation_crossed(active, state):
            # PlanControl may have crossed its durable activation receipt just
            # before the Kernel could persist the human-bound classification.
            # Leave the predecessor row intact so advance() can finish the
            # transition from this intent without treating REQUIRE_HUMAN as a
            # normal approved-successor classification.
            return state

        classification = self._validate_successor_state_match(active, state)
        transition = state["successor_transition"]
        previous_writer_generation = state.get(
            "successor_previous_writer_generation"
        )
        if (
            type(previous_writer_generation) is not str
            or not previous_writer_generation
        ):
            raise ExecutionKernelError(
                "SUCCESSOR_ACTIVATION_READBACK_INVALID",
                "successor transition omitted its predecessor writer generation",
            )
        # A crash can leave the durable predecessor row behind after
        # PlanControl has already switched the active authority.  Do not let
        # that recovery path trust only the transition envelope: the active
        # readback must pass the same closed receipt/PlanSpec/claim fence as
        # the original activation before migration or effects are admitted.
        self._validate_successor_readback(
            active.handle,
            transition,
            classification,
            active,
            active,
            expected_writer_generation=previous_writer_generation,
        )
        try:
            plan = load_canonical_json(active.plan_spec_bytes)
        except CanonicalJsonError as error:
            raise ExecutionKernelError(
                "ACTIVE_PLAN_INVALID", "successor PlanSpec bytes are not canonical"
            ) from error
        if type(plan) is not dict:
            raise ExecutionKernelError(
                "ACTIVE_PLAN_INVALID", "successor PlanSpec is not an object"
            )
        return self._reconcile_successor_revision(active, state, work, plan)

    def _validate_successor_state_match(
        self,
        active: ActivePlanReadback,
        state: Mapping[str, Any],
    ) -> PlanInvalidationClassification:
        """Prove that an active revision is the one recorded successor."""

        try:
            if state.get("human_successor_transition") is not None:
                return self._validate_human_successor_state_match(active, state)
            transition = state.get("successor_transition")
            previous_digest = state.get("plan_revision_digest")
            if (
                type(transition) is not dict
                or set(transition)
                != {
                    "kind",
                    "classification_action_id",
                    "classification_digest",
                    "snapshot_digest",
                    "previous_revision_digest",
                    "evidence_digests",
                    "state",
                }
                or transition.get("kind") != "successor_transition.v1"
                or transition.get("state") not in {"activation_due", "activated"}
                or transition.get("previous_revision_digest") != previous_digest
                or type(previous_digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", previous_digest) is None
                or active.current_revision_digest == previous_digest
                or active.activation_receipt.expected_previous_revision_digest
                != previous_digest
                or active.activation_receipt.planning_stable_action_id
                != transition.get("classification_action_id")
            ):
                raise ValueError("successor transition does not match active receipt")
            classifications = state.get("plan_invalidation_classifications")
            if type(classifications) is not dict:
                raise ValueError("successor classification map is missing")
            classification = self._decode_classification(
                classifications.get(transition["classification_action_id"])
            )
            if (
                classification.action_id != transition["classification_action_id"]
                or classification.digest != transition["classification_digest"]
                or classification.snapshot_digest != transition["snapshot_digest"]
                or classification.plan_revision_digest != previous_digest
                or list(classification.evidence_digests)
                != transition["evidence_digests"]
                or classification.action_id
                != "replan:"
                + digest_value(
                    {
                        "repository": active.handle.repository,
                        "campaign_key": active.handle.campaign_key,
                        "plan_revision_digest": previous_digest,
                        "evidence_digests": list(classification.evidence_digests),
                    }
                )
                or classification.disposition
                is not PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR
            ):
                raise ValueError("successor classification does not match transition")
            return classification
        except Exception as error:
            if isinstance(error, ExecutionKernelError) and error.code == "CAMPAIGN_REVISION_CHANGED":
                raise
            raise ExecutionKernelError(
                "CAMPAIGN_REVISION_CHANGED",
                "active revision changed without one exact durable successor transition",
            ) from error

    def _validate_human_successor_state_match(
        self,
        active: ActivePlanReadback,
        state: Mapping[str, Any],
    ) -> PlanInvalidationClassification:
        transition = state.get("human_successor_transition")
        try:
            if (
                type(transition) is not dict
                or set(transition)
                != {
                    "kind",
                    "decision_id",
                    "classification_action_id",
                    "classification_digest",
                    "snapshot_digest",
                    "previous_revision_digest",
                    "evidence_digests",
                    "source_readback_digest",
                    "state",
                }
                or transition["kind"] != "human_successor_transition.v1"
                or transition["state"] not in {"activation_due", "activated"}
                or transition["previous_revision_digest"]
                != state.get("plan_revision_digest")
                or active.current_revision_digest
                == transition["previous_revision_digest"]
                or active.activation_receipt.expected_previous_revision_digest
                != transition["previous_revision_digest"]
                or active.activation_receipt.planning_stable_action_id
                != transition["classification_action_id"]
                or not transition["classification_action_id"].startswith(
                    _HUMAN_SUCCESSOR_ACTION_PREFIX
                )
                or type(transition["classification_digest"]) is not str
            ):
                raise ValueError("human successor transition does not match receipt")
            classification = self._decode_classification(
                state.get("human_successor_classification")
            )
            if (
                classification.action_id != transition["classification_action_id"]
                or classification.digest != transition["classification_digest"]
                or classification.snapshot_digest != transition["snapshot_digest"]
                or classification.plan_revision_digest
                != transition["previous_revision_digest"]
                or list(classification.evidence_digests)
                != transition["evidence_digests"]
                or classification.disposition
                is not PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR
                or classification.decision is not None
            ):
                raise ValueError("human successor classification does not match transition")
            gate = self._human_gate_summary(state)
            if (
                gate is None
                or gate.phase not in {"planning_validated_successor", "active_successor"}
                or gate.decision_id != transition["decision_id"]
                or gate.planning_action_id != transition["classification_action_id"]
                or gate.source_readback_digest
                != transition["source_readback_digest"]
            ):
                raise ValueError("human successor gate summary does not match transition")
            return classification
        except Exception as error:
            if isinstance(error, ExecutionKernelError) and error.code == "CAMPAIGN_REVISION_CHANGED":
                raise
            raise ExecutionKernelError(
                "CAMPAIGN_REVISION_CHANGED",
                "active revision changed without one exact durable human successor transition",
            ) from error

    def _activate_successor_if_due(
        self,
        active: ActivePlanReadback,
        work: dict[str, dict[str, Any]],
        state: dict[str, Any],
        classification: PlanInvalidationClassification | None,
    ) -> tuple[ActivePlanReadback, dict[str, dict[str, Any]], dict[str, Any]]:
        """Commit and reconcile one approved successor, if the private port exists."""

        if (
            classification is None
            or classification.disposition
            is not PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR
        ):
            return active, work, state
        if (
            classification.plan_revision_digest != active.current_revision_digest
            or classification.action_id
            != self._replanning_action_id(active, classification.evidence_digests)
        ):
            raise ExecutionKernelError(
                "SUCCESSOR_TRANSITION_READBACK_INVALID",
                "successor classification action is not bound to the predecessor revision",
            )
        activator = getattr(self._plan_control, "activate_successor", None)
        if not callable(activator):
            # The #134 classifier double intentionally has no successor port;
            # it remains quiescent rather than gaining a second public route.
            return active, work, state

        if self._successor_revision_budget_exhausted(state):
            self._exhaust_successor_revision_budget(
                active,
                state,
                classification,
            )
            return active, work, state

        transition = {
            "kind": "successor_transition.v1",
            "classification_action_id": classification.action_id,
            "classification_digest": classification.digest,
            "snapshot_digest": classification.snapshot_digest,
            "previous_revision_digest": active.current_revision_digest,
            "evidence_digests": list(classification.evidence_digests),
            "state": "activation_due",
        }
        existing = state.get("successor_transition")
        if (
            type(existing) is dict
            and existing.get("previous_revision_digest") == active.current_revision_digest
            and existing != transition
        ):
            raise ExecutionKernelError(
                "SUCCESSOR_TRANSITION_READBACK_INVALID",
                "successor activation intent conflicts with the current classification",
            )
        state["successor_transition"] = load_canonical_json(canonical_bytes(transition))
        state["successor_previous_writer_generation"] = (
            active.activation_receipt.writer_generation
        )
        self._save(active.handle, state)
        persisted = self._load(active.handle)
        if persisted is None or persisted.get("successor_transition") != transition:
            raise ExecutionKernelError(
                "SUCCESSOR_TRANSITION_READBACK_INVALID",
                "successor activation intent did not read back exactly",
            )
        state.clear()
        state.update(persisted)

        try:
            candidate = activator(active.handle, classification)
        except Exception as error:
            raise ExecutionKernelError(
                "SUCCESSOR_ACTIVATION_FAILED",
                "PlanControl successor activation did not complete",
            ) from error
        try:
            fresh = self._plan_control.read_active(active.handle)
        except Exception as error:
            raise ExecutionKernelError(
                "SUCCESSOR_ACTIVATION_READBACK_INVALID",
                "successor active authority could not be read back",
            ) from error
        self._validate_successor_readback(
            active.handle,
            transition,
            classification,
            candidate,
            fresh,
            expected_writer_generation=active.activation_receipt.writer_generation,
        )
        successor_plan = load_canonical_json(candidate.plan_spec_bytes)
        if type(successor_plan) is not dict:  # pragma: no cover - validated above
            raise ExecutionKernelError(
                "SUCCESSOR_ACTIVATION_READBACK_INVALID",
                "successor PlanSpec is not an object",
            )
        successor_work = {
            item["key"]: item for item in successor_plan["work"]
        }
        migrated = self._reconcile_successor_revision(
            candidate,
            state,
            successor_work,
            successor_plan,
        )
        return candidate, successor_work, migrated

    @staticmethod
    def _validate_successor_readback(
        handle: CampaignHandle,
        transition: Mapping[str, Any],
        classification: PlanInvalidationClassification,
        candidate: object,
        fresh: object,
        *,
        expected_writer_generation: str | None = None,
    ) -> ActivePlanReadback:
        """Require one exact receipt/PlanSpec/claim readback before migration."""

        try:
            if (
                type(classification) is not PlanInvalidationClassification
                or type(candidate) is not ActivePlanReadback
                or type(fresh) is not ActivePlanReadback
                or candidate != fresh
                or type(candidate.activation_receipt) is not ActivationReceipt
                or candidate.handle != handle
                or candidate.activation_receipt.repository != handle.repository
                or candidate.activation_receipt.campaign_key != handle.campaign_key
                or type(candidate.plan_spec_bytes) is not bytes
            ):
                raise ValueError("successor authority readback is untyped or inconsistent")
            plan = load_canonical_json(candidate.plan_spec_bytes)
            if type(plan) is not dict or canonical_bytes(plan) != candidate.plan_spec_bytes:
                raise ValueError("successor PlanSpec is not canonical")
            # PlanControl owns the compiler, but the ExecutionKernel owns the
            # readback fence.  Reuse its closed V3 validator here so a hostile
            # or stale activator cannot smuggle a partial PlanSpec into the
            # migration path (which would otherwise fall back to legacy
            # identity derivation for missing authority facts).
            from .plan_control import _validate_plan_spec

            _validate_plan_spec(candidate.plan_spec_bytes)
            if (
                plan["repository"] != handle.repository
                or plan["campaign"]["key"] != handle.campaign_key
                or type(plan["target_branch"]) is not str
                or not plan["target_branch"]
            ):
                raise ValueError("successor PlanSpec identity is malformed")
            plan_keys: list[str] = []
            for item in plan["work"]:
                if (
                    type(item) is not dict
                    or type(item.get("key")) is not str
                    or not item["key"]
                    or type(item.get("depends_on")) is not list
                    or type(item.get("exclusive_resources")) is not list
                    or any(
                        type(value) is not str or not value
                        for value in item.get("depends_on", [])
                        + item.get("exclusive_resources", [])
                    )
                ):
                    raise ValueError("successor PlanSpec work item is invalid")
                plan_keys.append(item["key"])
            if len(set(plan_keys)) != len(plan_keys):
                raise ValueError("successor PlanSpec work keys are not unique")
            receipt = candidate.activation_receipt
            claim_proofs = candidate.claim_proofs
            ready_refs = tuple(
                sorted(item["source"]["ref"] for item in plan["work"])
            )
            if (
                type(receipt.writer_generation) is not str
                or not receipt.writer_generation
                or (
                    expected_writer_generation is not None
                    and receipt.writer_generation != expected_writer_generation
                )
                or type(receipt.ready_refs) is not tuple
                or receipt.ready_refs != ready_refs
                or not receipt.ready_refs
                or len(set(receipt.ready_refs)) != len(receipt.ready_refs)
                or any(type(ref) is not str or not ref for ref in receipt.ready_refs)
                or type(receipt.ticket_keys) is not tuple
                or any(type(key) is not str or not key for key in receipt.ticket_keys)
                or tuple(sorted(set(receipt.ticket_keys))) != receipt.ticket_keys
                or type(receipt.expected_previous_revision_digest) is not str
                or re.fullmatch(
                    r"[0-9a-f]{64}", receipt.expected_previous_revision_digest
                ) is None
                or type(receipt.planning_stable_action_id) is not str
                or not receipt.planning_stable_action_id
                or type(receipt.planning_subject_digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", receipt.planning_subject_digest)
                is None
                or type(receipt.planning_preflight_receipt_digest) is not str
                or re.fullmatch(
                    r"[0-9a-f]{64}", receipt.planning_preflight_receipt_digest
                )
                is None
                or type(receipt.compilation_record_artifact_digest) is not str
                or re.fullmatch(
                    r"[0-9a-f]{64}", receipt.compilation_record_artifact_digest
                )
                is None
                or type(receipt.planning_receipt_digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", receipt.planning_receipt_digest)
                is None
                or type(receipt.planning_output_artifact_digest) is not str
                or re.fullmatch(
                    r"[0-9a-f]{64}", receipt.planning_output_artifact_digest
                )
                is None
                or type(candidate.current_revision_digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", candidate.current_revision_digest)
                is None
            ):
                raise ValueError("successor Activation Receipt fields are invalid")
            if type(claim_proofs) is not tuple:
                raise ValueError("successor claims are not a tuple")
            if any(type(proof) is not TicketClaimProof for proof in claim_proofs):
                raise ValueError("successor claims are untyped")
            if any(
                type(proof.ticket_key) is not str
                or not proof.ticket_key
                or type(proof.repository) is not str
                or type(proof.campaign_key) is not str
                or type(proof.plan_revision_digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", proof.plan_revision_digest) is None
                for proof in claim_proofs
            ):
                raise ValueError("successor claim fields are invalid")
            claim_keys = tuple(proof.ticket_key for proof in claim_proofs)
            if (
                candidate.current_revision_digest != receipt.revision_digest
                or candidate.current_revision_digest
                == transition["previous_revision_digest"]
                or receipt.expected_previous_revision_digest
                != transition["previous_revision_digest"]
                or receipt.planning_stable_action_id
                != classification.action_id
                or digest_bytes(candidate.plan_spec_bytes)
                != candidate.current_revision_digest
                or tuple(plan_keys) != receipt.ticket_keys
                or claim_keys != receipt.ticket_keys
                or tuple(sorted(set(receipt.ticket_keys))) != receipt.ticket_keys
                or len(claim_proofs) != len(plan_keys)
                or any(
                    proof.repository != handle.repository
                    or proof.campaign_key != handle.campaign_key
                    or proof.plan_revision_digest != candidate.current_revision_digest
                    for proof in claim_proofs
                )
            ):
                raise ValueError("successor authority did not read back exactly")
            return candidate
        except Exception as error:
            raise ExecutionKernelError(
                "SUCCESSOR_ACTIVATION_READBACK_INVALID",
                "successor authority did not read back exactly",
            ) from error

    def _reconcile_successor_revision(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        work: dict[str, dict[str, Any]],
        plan: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Atomically replace R0 execution state with exact R1 state."""

        classification = self._validate_successor_state_match(active, state)
        old_runs = state.get("runs")
        accepted_values = state.get("accepted_results")
        old_lineage = state.get("revision_lineage", [])
        if type(old_runs) is not dict or type(accepted_values) is not list:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "predecessor execution state is not migratable",
            )
        if type(old_lineage) is not list:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "predecessor revision lineage is not a list",
            )

        lineage = load_canonical_json(
            canonical_bytes(
                {
                    "kind": "revision_lineage.v1",
                    "plan_revision_digest": state["plan_revision_digest"],
                    "activation_receipt_digest": state["activation_receipt_digest"],
                    "classification_action_id": classification.action_id,
                    "runs": old_runs,
                    "accepted_results": accepted_values,
                    "plan_invalidation": state.get("plan_invalidation", {}),
                    "plan_invalidation_classifications": state.get(
                        "plan_invalidation_classifications", {}
                    ),
                }
            )
        )
        if type(lineage) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "successor lineage could not be canonicalized",
            )
        if lineage in old_lineage:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "successor lineage was already archived in predecessor state",
            )

        bindings: dict[str, AcceptedResultBinding] = {}
        for value in accepted_values:
            if type(value) is not dict:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "predecessor accepted Result is not an object",
                )
            if set(value) == {"ticket_key", "result_digest"}:
                continue
            try:
                binding = AcceptedResultBinding.from_canonical(value)
            except Exception as error:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "predecessor accepted Result binding is invalid",
                ) from error
            if binding.ticket_key in bindings:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "predecessor contains duplicate accepted Results",
                )
            bindings[binding.ticket_key] = binding

        successor_accepted: list[dict[str, Any]] = []
        successor_runs: dict[str, dict[str, Any]] = {}
        successor_target_digest = _target_facts_digest_for_kernel(plan)
        for ticket_key in sorted(work):
            item = work[ticket_key]
            run = self._new_run_state(plan, ticket_key, item)
            binding = bindings.get(ticket_key)
            predecessor_run = old_runs.get(ticket_key)
            if binding is not None and can_preserve_result(
                binding,
                run["work_subject_digest"],
                successor_target_digest,
            ):
                if type(predecessor_run) is not dict:
                    raise ExecutionKernelError(
                        "EXECUTION_STORE_INVALID",
                        "accepted Result has no predecessor Work Run",
                    )
                if predecessor_run.get("phase") not in {None, "completed"}:
                    raise ExecutionKernelError(
                        "EXECUTION_STORE_INVALID",
                        "accepted Result is not backed by a completed predecessor Work Run",
                    )
                if (
                    predecessor_run.get("result_digest") not in {None, binding.result_digest}
                    or (
                        predecessor_run.get("evidence_digests") is not None
                        and predecessor_run.get("evidence_digests")
                        != list(binding.evidence_digests)
                    )
                ):
                    raise ExecutionKernelError(
                        "EXECUTION_STORE_INVALID",
                        "accepted Result binding disagrees with its predecessor Work Run",
                    )
                run.update(
                    {
                        "phase": "completed",
                        "result_digest": binding.result_digest,
                        "evidence_digests": list(binding.evidence_digests),
                    }
                )
                successor_accepted.append(binding.canonical())
            successor_runs[ticket_key] = run

        transition_key = (
            "human_successor_transition"
            if "human_successor_transition" in state
            else "successor_transition"
        )
        transition = load_canonical_json(
            canonical_bytes(state[transition_key])
        )
        budgets = state.get("replan_budgets")
        if budgets is None:
            # Historical #135 campaigns have no #136 Policy Witness budget.
            # Preserve their successor behavior without manufacturing a
            # counter; any human-gated successor is initialized only from the
            # exact budget-bearing policy seam above.
            next_budgets = None
        else:
            if type(budgets) is not dict:
                raise ExecutionKernelError(
                    "REPLAN_BUDGET_READBACK_INVALID",
                    "successor migration omitted its replan budget envelope",
                )
            successor_budget_defaults = self._replan_budget_defaults(active, plan)
            if successor_budget_defaults is None:
                raise ExecutionKernelError(
                    "REPLAN_BUDGET_READBACK_INVALID",
                    "successor migration omitted its active Policy Witness budget",
                )
            if set(budgets) != {
                "policy_witness_digest",
                "successor_revisions_used",
                "successor_revision_limit",
                "invalidation_limit",
                "obligations",
            }:
                raise ExecutionKernelError(
                    "REPLAN_BUDGET_READBACK_INVALID",
                    "successor migration budget schema is not closed",
                )
            self._validate_replan_budgets(
                budgets,
                {
                    "policy_witness_digest": budgets["policy_witness_digest"],
                    "successor_revision_limit": budgets["successor_revision_limit"],
                    "invalidation_limit": budgets["invalidation_limit"],
                },
            )
            if (
                budgets["successor_revision_limit"]
                != successor_budget_defaults["successor_revision_limit"]
                or budgets["invalidation_limit"]
                != successor_budget_defaults["invalidation_limit"]
            ):
                raise ExecutionKernelError(
                    "REPLAN_BUDGET_READBACK_INVALID",
                    "successor Policy Witness changed the Campaign replan budget",
                )
            next_budgets = load_canonical_json(canonical_bytes(budgets))
            # The approved successor may carry a new authoritative Policy
            # Witness (the human-gate authority-change path), but only its
            # digest may change.  The original Campaign limits, counters, and
            # obligation Evidence remain durable across the revision.
            next_budgets["policy_witness_digest"] = successor_budget_defaults[
                "policy_witness_digest"
            ]
            next_budgets["successor_revisions_used"] += 1
            if (
                next_budgets["successor_revisions_used"]
                > next_budgets["successor_revision_limit"]
            ):
                raise ExecutionKernelError(
                    "REPLAN_BUDGET_READBACK_INVALID",
                    "successor revision budget was exceeded before migration",
                )
        new_state: dict[str, Any] = {
            "plan_revision_digest": active.current_revision_digest,
            "activation_receipt_digest": digest_value(
                active.activation_receipt.__dict__
            ),
            "runs": successor_runs,
            "effects": {},
            "wake_refs": [],
            "last_wake_refs": [],
            "trusted_progress_revision": 0,
            "diagnosed_binding_ids": [],
            "normalized_permission_receipts": [],
            "candidate_receipts": [],
            "delivery_receipts": [],
            "plan_invalidation": {},
            "plan_invalidation_resolutions": {},
            "plan_invalidation_classifications": {},
            "accepted_results": sorted(
                successor_accepted,
                key=lambda value: value["ticket_key"],
            ),
            "revision_lineage": [*old_lineage, lineage],
        }
        if next_budgets is not None:
            new_state["replan_budgets"] = next_budgets
        new_state[transition_key] = transition
        if transition_key == "human_successor_transition":
            new_state["human_successor_classification"] = state.get(
                "human_successor_classification"
            )
            gate = state.get("human_gate")
            if type(gate) is not dict:
                raise ExecutionKernelError(
                    "HUMAN_GATE_ATTEMPT_READBACK_INVALID",
                    "human successor migration omitted its gate state",
                )
            summary = HumanGateSummary.from_canonical(gate["summary"])
            new_gate = dict(gate)
            new_gate["summary"] = replace(
                summary,
                successor_revisions_used=next_budgets["successor_revisions_used"],
                successor_revision_digest=active.current_revision_digest,
                phase="active_successor",
            ).canonical()
            new_state["human_gate"] = new_gate
        self._save(active.handle, new_state)
        readback = self._load(active.handle)
        if readback != new_state:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "successor execution state did not read back exactly",
            )
        state.clear()
        state.update(readback)
        return state

    @staticmethod
    def _new_run_state(
        plan: Mapping[str, Any],
        ticket_key: str,
        work_item: Mapping[str, Any],
    ) -> dict[str, Any]:
        subject_digest = _work_subject_digest_for_kernel(plan, work_item)
        return {
            "phase": "pending",
            "slot_held": False,
            "reason": None,
            "last_action_id": None,
            "semantic_action_id": None,
            "runtime_binding_id": None,
            "resume_ordinal": 0,
            "next_check_at": None,
            "last_trusted_progress_at": None,
            "stale_due_at": None,
            "stale_readback_action_id": None,
            "stale_diagnosis_action_id": None,
            "stale_disposition": None,
            "diagnosed_binding_ids": [],
            "stale_slot_release_pending": False,
            "binding_replacement_ordinal": 0,
            "terminal_binding_evidence": None,
            "runtime_agent_id": None,
            "runtime_session_id": None,
            "runtime_workspace_id": None,
            "candidate_submission_count": 0,
            "process_state": "unknown",
            "workspace_state": "unknown",
            "check_state": "unknown",
            "transcript_tail": [],
            "stale_diagnosis_packet": None,
            "stale_diagnosis_packet_digest": None,
            "stale_diagnosis_packet_identity": None,
            "stale_follow_up_action_id": None,
            "stale_follow_up_kind": None,
            "stale_follow_up_completed": True,
            "work_subject_digest": subject_digest,
            "work_run_key": _work_run_key_for_kernel(plan, work_item, subject_digest),
            "exclusive_resources": list(work_item.get("exclusive_resources", [])),
            "claim_state": "unclaimed",
            "candidate_identity": None,
            "candidate_receipt": None,
            "candidate_receipt_digest": None,
            "candidate_commit_oids": [],
            "candidate_receipt_digests": [],
            "trusted_progress_revision": 0,
            "result_digest": None,
            "evidence_digests": [],
            "plan_invalidation": None,
            "plan_invalidation_resolution": None,
            "resume_after_invalidation": False,
        }

    @staticmethod
    def _revision_lineage_summaries(
        state: Mapping[str, Any],
    ) -> tuple[RevisionLineageSummary, ...]:
        records = state.get("revision_lineage", [])
        if type(records) is not list:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "ExecutionKernel revision lineage is not a list",
            )
        summaries: list[RevisionLineageSummary] = []
        for record in records:
            if type(record) is not dict or set(record) != {
                "kind",
                "plan_revision_digest",
                "activation_receipt_digest",
                "classification_action_id",
                "runs",
                "accepted_results",
                "plan_invalidation",
                "plan_invalidation_classifications",
            } or record.get("kind") != "revision_lineage.v1":
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "revision lineage record schema is not exact",
                )
            work_run_keys: set[str] = set()
            workspace_identities: set[str] = set()
            candidate_identities: set[str] = set()
            result_digests: set[str] = set()
            evidence_digests: set[str] = set()
            source_evidence_digests: set[str] = set()
            runs = record["runs"]
            if type(runs) is not dict:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "revision lineage runs are not a mapping",
                )
            for run in runs.values():
                if type(run) is not dict:
                    raise ExecutionKernelError(
                        "EXECUTION_STORE_INVALID",
                        "revision lineage Work Run is not an object",
                    )
                for value, target in (
                    (run.get("work_run_key"), work_run_keys),
                    (run.get("workspace_identity"), workspace_identities),
                    (run.get("candidate_identity"), candidate_identities),
                    (run.get("result_digest"), result_digests),
                ):
                    if type(value) is str and value:
                        target.add(value)
                evidence = run.get("evidence_digests", [])
                if type(evidence) is list:
                    evidence_digests.update(
                        value for value in evidence if type(value) is str and value
                    )
                invalidation = run.get("plan_invalidation")
                if type(invalidation) is dict:
                    workspace = invalidation.get("workspace_identity")
                    if type(workspace) is str and workspace:
                        workspace_identities.add(workspace)
                    source = invalidation.get("source_evidence_digests")
                    if type(source) is list:
                        source_evidence_digests.update(
                            value for value in source
                            if type(value) is str and value
                        )
            invalidations = record["plan_invalidation"]
            if type(invalidations) is not dict:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "revision lineage invalidations are not a mapping",
                )
            for invalidation in invalidations.values():
                if type(invalidation) is dict:
                    workspace = invalidation.get("workspace_identity")
                    if type(workspace) is str and workspace:
                        workspace_identities.add(workspace)
                    source = invalidation.get("source_evidence_digests")
                    if type(source) is list:
                        source_evidence_digests.update(
                            value for value in source
                            if type(value) is str and value
                        )
            accepted = record["accepted_results"]
            if type(accepted) is not list:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "revision lineage accepted Results are not a list",
                )
            for value in accepted:
                if type(value) is not dict:
                    continue
                if set(value) == {"ticket_key", "result_digest"}:
                    if type(value.get("result_digest")) is str:
                        result_digests.add(value["result_digest"])
                    continue
                try:
                    binding = AcceptedResultBinding.from_canonical(value)
                except Exception as error:
                    raise ExecutionKernelError(
                        "EXECUTION_STORE_INVALID",
                        "revision lineage accepted Result is invalid",
                    ) from error
                result_digests.add(binding.result_digest)
                evidence_digests.update(binding.evidence_digests)
            summaries.append(
                RevisionLineageSummary(
                    plan_revision_digest=record["plan_revision_digest"],
                    activation_receipt_digest=record["activation_receipt_digest"],
                    classification_action_id=record["classification_action_id"],
                    work_run_keys=tuple(sorted(work_run_keys)),
                    workspace_identities=tuple(sorted(workspace_identities)),
                    candidate_identities=tuple(sorted(candidate_identities)),
                    result_digests=tuple(sorted(result_digests)),
                    evidence_digests=tuple(sorted(evidence_digests)),
                    source_evidence_digests=tuple(sorted(source_evidence_digests)),
                )
            )
        return tuple(
            sorted(
                summaries,
                key=lambda summary: (
                    summary.plan_revision_digest,
                    summary.activation_receipt_digest,
                    summary.classification_action_id,
                ),
            )
        )

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

    @staticmethod
    def _trusted_lifecycle_projection(run: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            run.get("phase"),
            run.get("reason"),
            run.get("next_check_at"),
            run.get("slot_held"),
            run.get("claim_state"),
            run.get("candidate_identity"),
            run.get("result_digest"),
            tuple(run.get("evidence_digests", ())),
            run.get("runtime_binding_id"),
        )

    def _clock_value(self) -> datetime:
        try:
            value = self._clock()
        except Exception as error:
            raise ExecutionKernelError(
                "STALE_CONFIGURATION_INVALID",
                "ExecutionKernel clock did not return a timestamp",
            ) from error
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ExecutionKernelError(
                "STALE_CONFIGURATION_INVALID",
                "ExecutionKernel clock must return an aware UTC datetime",
            )
        return value

    @staticmethod
    def _timestamp(value: datetime) -> str:
        rendered = value.isoformat()
        try:
            parsed = datetime.fromisoformat(rendered)
        except ValueError as error:  # pragma: no cover - datetime.isoformat is valid
            raise ExecutionKernelError(
                "STALE_CONFIGURATION_INVALID",
                "ExecutionKernel clock produced a noncanonical timestamp",
            ) from error
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0) or parsed.isoformat() != rendered:
            raise ExecutionKernelError(
                "STALE_CONFIGURATION_INVALID",
                "ExecutionKernel clock produced a noncanonical UTC timestamp",
            )
        return rendered

    def _record_trusted_progress(
        self,
        state: dict[str, Any],
        run: dict[str, Any],
        *,
        repository: str,
    ) -> None:
        state_revision = state.get("trusted_progress_revision", 0)
        run_revision = run.get("trusted_progress_revision", 0)
        if (
            type(state_revision) is not int
            or isinstance(state_revision, bool)
            or state_revision < 0
            or type(run_revision) is not int
            or isinstance(run_revision, bool)
            or run_revision < 0
        ):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "trusted progress revision is not a non-negative integer",
            )
        state["trusted_progress_revision"] = state_revision + 1
        run["trusted_progress_revision"] = run_revision + 1
        now = self._clock_value()
        run["last_trusted_progress_at"] = self._timestamp(now)
        run["stale_due_at"] = self._timestamp(
            now + timedelta(seconds=self._configuration.stale_after_seconds_for(repository))
        )

    @staticmethod
    def _stale_binding_id(run: Mapping[str, Any]) -> str:
        binding = run.get("runtime_binding_id") or run.get("semantic_action_id")
        _validate_binding_id(binding, "active runtime binding identity")
        return binding

    @staticmethod
    def _stale_action_identity(
        active: ActivePlanReadback,
        ticket_key: str,
        run: Mapping[str, Any],
        *,
        binding_id: str,
        trusted_progress_digest: str,
    ) -> str:
        return (
            f"stale-readback:{active.handle.campaign_key}:{run['work_run_key']}"
            f":{binding_id}:{trusted_progress_digest}"
        )

    @staticmethod
    def _stale_diagnosis_identity(
        active: ActivePlanReadback,
        run: Mapping[str, Any],
        *,
        binding_id: str,
    ) -> str:
        return f"stale-diagnosis:{active.handle.campaign_key}:{run['work_run_key']}:{binding_id}"

    @staticmethod
    def _stale_follow_up_identity(
        active: ActivePlanReadback,
        run: Mapping[str, Any],
        *,
        binding_id: str,
        kind: StaleFollowUpKind,
    ) -> str:
        return (
            f"stale-follow-up:{kind.value}:{active.handle.campaign_key}:"
            f"{run['work_run_key']}:{binding_id}"
        )

    def _persist_action_intent(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        action: WorkRunAction,
    ) -> None:
        effect_identity = {
            "kind": action.kind,
            "ticket_key": action.ticket_key,
            "runtime_binding_id": action.runtime_binding_id,
            "plan_revision_digest": action.plan_revision_digest,
            "work_run_key": action.work_run_key,
            "work_subject_digest": action.work_subject_digest,
        }
        if action.stale_diagnosis_packet is not None:
            effect_identity.update(
                {
                    "packet_digest": action.stale_diagnosis_packet.digest,
                    "packet_identity": action.stale_diagnosis_packet.identity,
                }
            )
        if action.stale_follow_up_kind is not None:
            effect_identity["follow_up_kind"] = action.stale_follow_up_kind.value
        effects = state.setdefault("effects", {})
        if type(effects) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "ExecutionKernel effects are not a mapping",
            )
        prior = effects.get(action.stable_action_id)
        if prior is not None:
            if type(prior) is not dict or any(
                field in prior and prior[field] != value
                for field, value in effect_identity.items()
            ):
                raise ExecutionKernelError(
                    "EFFECT_READBACK_INVALID",
                    "stale effect intent is not bound to its exact action identity",
                )
            if prior.get("state") not in {"intent", "read_back"}:
                raise ExecutionKernelError(
                    "EFFECT_READBACK_INVALID",
                    "stale effect intent has an unknown durable state",
                )
            return
        effects[action.stable_action_id] = {
            "state": "intent",
            "execute_attempted": False,
            **effect_identity,
        }
        self._save(active.handle, state)

    def _persist_effect_readback(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        action: WorkRunAction,
        observation: StaleBindingObservation | StaleDiagnosisObservation | StaleFollowUpObservation,
    ) -> None:
        effect = state["effects"].get(action.stable_action_id)
        if type(effect) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "stale effect intent disappeared before readback",
            )
        receipt_digest = observation.receipt_digest
        if effect.get("state") == "read_back" and effect.get("receipt_digest") != receipt_digest:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale effect receipt changed during readback",
            )
        effect.update({"state": "read_back", "receipt_digest": receipt_digest})
        self._save(active.handle, state)

    def _read_or_execute_once(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        action: WorkRunAction,
        *,
        fence_execute: bool = False,
    ) -> object | None:
        prior = state.get("effects", {}).get(action.stable_action_id)
        self._persist_action_intent(active, state, action)
        readback = self._effects.readback(action)
        if readback is not None:
            return readback
        if type(prior) is dict and prior.get("state") == "read_back":
            return None
        if fence_execute and type(prior) is dict and prior.get("execute_attempted") is True:
            return None
        if fence_execute:
            effect = state["effects"].get(action.stable_action_id)
            if type(effect) is not dict:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "effect intent disappeared before execution fence",
                )
            effect["execute_attempted"] = True
            self._save(active.handle, state)
        return self._effects.execute(action)

    @staticmethod
    def _validate_stale_readback(
        action: WorkRunAction,
        observation: object,
        binding_id: str,
    ) -> StaleBindingObservation:
        if (
            type(observation) is not StaleBindingObservation
            or observation.stable_action_id != action.stable_action_id
            or observation.runtime_binding_id != binding_id
            or action.runtime_binding_id != binding_id
        ):
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale readback is not bound to its exact action and Runtime Binding",
            )
        return observation

    @staticmethod
    def _validate_stale_diagnosis(
        action: WorkRunAction,
        observation: object,
        binding_id: str,
    ) -> StaleDiagnosisObservation:
        if (
            type(observation) is not StaleDiagnosisObservation
            or observation.stable_action_id != action.stable_action_id
            or observation.runtime_binding_id != binding_id
            or action.runtime_binding_id != binding_id
            or type(observation.disposition) is not StaleDiagnosisDisposition
        ):
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale diagnosis is not bound to its exact action and Runtime Binding",
            )
        return observation

    @staticmethod
    def _validate_stale_follow_up(
        action: WorkRunAction,
        observation: object,
        binding_id: str,
        kind: StaleFollowUpKind,
    ) -> StaleFollowUpObservation:
        if (
            type(observation) is not StaleFollowUpObservation
            or observation.stable_action_id != action.stable_action_id
            or observation.runtime_binding_id != binding_id
            or action.runtime_binding_id != binding_id
            or observation.kind is not kind
        ):
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "stale follow-up is not bound to its exact action and Runtime Binding",
            )
        return observation

    @staticmethod
    def _record_diagnosed_binding(
        state: dict[str, Any],
        run: dict[str, Any],
        binding_id: str,
    ) -> None:
        for target in (state.setdefault("diagnosed_binding_ids", []), run.setdefault("diagnosed_binding_ids", [])):
            if type(target) is not list:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "diagnosed Runtime Binding identities are not a list",
                )
            if binding_id not in target:
                target.append(binding_id)
                target.sort()

    def _stale_diagnosis_packet(
        self,
        active: ActivePlanReadback,
        state: Mapping[str, Any],
        run: Mapping[str, Any],
        ticket_key: str,
        binding_id: str,
    ) -> StaleDiagnosisPacket:
        plan = load_canonical_json(active.plan_spec_bytes)
        if type(plan) is not dict or type(plan.get("work")) is not list:
            raise ExecutionKernelError(
                "ACTIVE_PLAN_INVALID",
                "stale diagnosis packet has no frozen Ticket plan",
            )
        work_item = next(
            (item for item in plan["work"] if type(item) is dict and item.get("key") == ticket_key),
            None,
        )
        if work_item is None:
            raise ExecutionKernelError(
                "ACTIVE_PLAN_INVALID",
                "stale diagnosis packet Ticket is not in the active Plan Revision",
            )
        authority = work_item.get("authority")
        worker_authority = authority.get("worker") if type(authority) is dict else None
        authority_subtree_digest = (
            worker_authority.get("subtree_digest")
            if type(worker_authority) is dict
            else None
        )
        if type(authority_subtree_digest) is not str:
            authority_subtree_digest = digest_value(authority)
        policy = plan.get("policy")
        policy_witness_digest = (
            policy.get("digest") if type(policy) is dict else None
        )
        if type(policy_witness_digest) is not str:
            policy_witness_digest = digest_value(policy)
        candidate_count = run.get("candidate_submission_count", 0)
        if run.get("candidate_receipt") is not None and candidate_count == 0:
            candidate_count = 1
        candidate_identity = run.get("candidate_identity")
        candidate_identities = (candidate_identity,) if type(candidate_identity) is str else ()
        transcript_tail = run.get("transcript_tail", [])
        if type(transcript_tail) is not list:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "stale diagnosis transcript tail is not a list",
            )
        return StaleDiagnosisPacket(
            repository=active.handle.repository,
            campaign_key=active.handle.campaign_key,
            plan_revision_digest=active.current_revision_digest,
            ticket_key=ticket_key,
            work_run_key=run["work_run_key"],
            work_subject_digest=run["work_subject_digest"],
            ticket_contract_digest=digest_value(work_item.get("contract")),
            authority_subtree_digest=authority_subtree_digest,
            policy_witness_digest=policy_witness_digest,
            runtime_binding_id=binding_id,
            candidate_count=candidate_count,
            binding_count=1 + run.get("binding_replacement_ordinal", 0),
            candidate_identities=candidate_identities,
            lifecycle_state=run.get("phase", "unknown"),
            process_state=run.get("process_state", "unknown"),
            workspace_state=run.get("workspace_state", "unknown"),
            check_state=run.get("check_state", "unknown"),
            transcript_tail=tuple(transcript_tail),
        )

    @staticmethod
    def _candidate_history(
        run: dict[str, Any],
    ) -> tuple[list[str], list[str]]:
        candidate_commit_oids = run.setdefault("candidate_commit_oids", [])
        candidate_receipt_digests = run.setdefault("candidate_receipt_digests", [])
        if type(candidate_commit_oids) is not list or any(
            type(oid) is not str
            or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", oid) is None
            for oid in candidate_commit_oids
        ):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Candidate commit OID history is malformed",
            )
        if len(candidate_commit_oids) != len(set(candidate_commit_oids)):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Candidate commit OID history contains duplicates",
            )
        if type(candidate_receipt_digests) is not list or any(
            type(digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for digest in candidate_receipt_digests
        ):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Candidate receipt digest history is malformed",
            )
        if len(candidate_receipt_digests) != len(set(candidate_receipt_digests)):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Candidate receipt digest history contains duplicates",
            )
        return candidate_commit_oids, candidate_receipt_digests

    def _record_candidate_history(
        self,
        run: dict[str, Any],
        receipt: CandidateReceipt,
    ) -> tuple[bool, bool]:
        candidate_commit_oids, candidate_receipt_digests = self._candidate_history(run)
        history_changed = False
        if receipt.digest not in candidate_receipt_digests:
            candidate_receipt_digests.append(receipt.digest)
            history_changed = True
        if receipt.candidate_commit_oid not in candidate_commit_oids:
            candidate_commit_oids.append(receipt.candidate_commit_oid)
            history_changed = True
        return history_changed, len(candidate_commit_oids) > 3

    @staticmethod
    def _mark_candidate_budget_exhausted(
        run: dict[str, Any],
        ticket_key: str,
    ) -> None:
        run["phase"] = "decision"
        run["reason"] = f"CandidateBudgetExhausted:{ticket_key}"
        run["next_check_at"] = None
        run["slot_held"] = False
        run["claim_state"] = "released"

    def _persist_candidate_receipt(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        run: dict[str, Any],
        ticket_key: str,
        receipt: CandidateReceipt,
    ) -> tuple[bool, bool]:
        if (
            type(receipt) is not CandidateReceipt
            or receipt.repository != active.handle.repository
            or receipt.campaign_key != active.handle.campaign_key
            or receipt.campaign_handle != active.handle.campaign_key
            or receipt.plan_revision_digest != active.current_revision_digest
            or receipt.work_run_key != run["work_run_key"]
            or receipt.ticket_key != ticket_key
            or receipt.runtime_subject_digest != run["work_subject_digest"]
        ):
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "CandidateReceipt is not bound to the current Campaign, Ticket, Work Run, or subject",
            )
        receipt_canonical = receipt.canonical()
        changed = run.get("candidate_receipt") != receipt_canonical
        run["candidate_receipt"] = receipt_canonical
        run["candidate_receipt_digest"] = receipt.digest
        run["candidate_identity"] = f"candidate:{receipt.candidate_commit_oid}"
        history_changed, budget_exhausted = self._record_candidate_history(run, receipt)
        if changed or history_changed:
            count = run.get("candidate_submission_count", 0)
            if type(count) is not int or isinstance(count, bool) or count < 0:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Candidate submission count is invalid",
                )
            run["candidate_submission_count"] = min(3, count + 1)
            self._record_trusted_progress(
                state,
                run,
                repository=active.handle.repository,
            )
        state["candidate_receipts"] = [
            value["candidate_receipt"]
            for _key, value in sorted(state["runs"].items())
            if type(value) is dict and value.get("candidate_receipt") is not None
        ]
        self._save(active.handle, state)
        persisted = self._load(active.handle)
        if persisted is None:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "CandidateReceipt disappeared during persistence",
            )
        persisted_run = persisted["runs"].get(ticket_key)
        try:
            persisted_receipt = CandidateReceipt.from_canonical(
                persisted_run["candidate_receipt"]
            )
        except (CandidateGateError, TypeError, KeyError) as error:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "persisted CandidateReceipt failed canonical readback",
            ) from error
        if persisted_receipt != receipt:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "persisted CandidateReceipt changed during readback",
            )
        state.clear()
        state.update(persisted)
        return changed, budget_exhausted

    def _apply_mechanical_stale_readback(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        run: dict[str, Any],
        observation: StaleBindingObservation,
        *,
        progress_already_recorded: bool = False,
    ) -> None:
        phase_by_state = {
            StaleReadbackState.TERMINAL: ("completed", "RuntimeTerminal"),
            StaleReadbackState.IDLE: ("wait", "RuntimeIdle"),
            StaleReadbackState.PERMISSION_WAITING: ("wait", "PermissionWaiting"),
            StaleReadbackState.CANDIDATE_RECEIVED: ("candidate_checks", "CandidateReceiptReceived"),
            StaleReadbackState.PROVIDER_UNAVAILABLE: ("runtime_unavailable", "ProviderUnavailable"),
        }
        if observation.state not in phase_by_state:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "unclassified stale readback must use the diagnosis path",
            )
        phase, reason = phase_by_state[observation.state]
        run["phase"] = phase
        run["reason"] = reason
        run["stale_slot_release_pending"] = observation.state in {
            StaleReadbackState.PERMISSION_WAITING,
            StaleReadbackState.PROVIDER_UNAVAILABLE,
        }
        run["slot_held"] = (
            phase in _SLOT_PHASES or run["stale_slot_release_pending"]
        )
        run["claim_state"] = "held" if run["slot_held"] else "released"
        run["stale_due_at"] = None
        run["stale_disposition"] = observation.state.value
        if not progress_already_recorded:
            self._record_trusted_progress(
                state,
                run,
                repository=active.handle.repository,
            )
        run["stale_due_at"] = None
        self._save(active.handle, state)

    def _apply_stale_diagnosis(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        run: dict[str, Any],
        observation: StaleDiagnosisObservation,
    ) -> None:
        dispositions = {
            StaleDiagnosisDisposition.CONTINUE: ("running", "StaleSuppressed", True),
            StaleDiagnosisDisposition.GUIDE_SAME_WORKER: (
                "running",
                "StaleWorkerGuidance",
                True,
            ),
            StaleDiagnosisDisposition.RECOVER_SAME_BINDING: (
                "running",
                "StaleBindingRecovery",
                True,
            ),
            StaleDiagnosisDisposition.DECISION: ("decision", "RuntimeBindingStale", False),
        }
        if observation.disposition not in dispositions:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "unknown stale diagnosis disposition",
            )
        phase, reason, slot_held = dispositions[observation.disposition]
        run["phase"] = phase
        run["reason"] = reason
        run["slot_held"] = slot_held
        run["claim_state"] = "held" if slot_held else "released"
        run["stale_slot_release_pending"] = False
        run["stale_due_at"] = None
        run["stale_disposition"] = observation.disposition.value
        if observation.disposition is StaleDiagnosisDisposition.GUIDE_SAME_WORKER:
            follow_up_kind = StaleFollowUpKind.GUIDANCE
        elif observation.disposition is StaleDiagnosisDisposition.RECOVER_SAME_BINDING:
            follow_up_kind = StaleFollowUpKind.SAME_BINDING_RECOVERY
        else:
            follow_up_kind = None
        if follow_up_kind is None:
            run["stale_follow_up_action_id"] = None
            run["stale_follow_up_kind"] = None
            run["stale_follow_up_completed"] = True
        else:
            run["stale_follow_up_action_id"] = self._stale_follow_up_identity(
                active,
                run,
                binding_id=self._stale_binding_id(run),
                kind=follow_up_kind,
            )
            run["stale_follow_up_kind"] = follow_up_kind.value
            run["stale_follow_up_completed"] = False
        self._record_trusted_progress(
            state,
            run,
            repository=active.handle.repository,
        )
        run["stale_due_at"] = None
        self._save(active.handle, state)

    def _perform_stale_effect(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        ticket_key: str,
    ) -> bool:
        run = state["runs"][ticket_key]
        binding_id = self._stale_binding_id(run)
        trusted_progress_digest = self._trusted_progress_digest(state, active.handle)
        readback_id = run.get("stale_readback_action_id")
        if readback_id is None:
            readback_id = self._stale_action_identity(
                active,
                ticket_key,
                run,
                binding_id=binding_id,
                trusted_progress_digest=trusted_progress_digest,
            )
            run["stale_readback_action_id"] = readback_id
        action = WorkRunAction(
            stable_action_id=readback_id,
            repository=active.handle.repository,
            campaign_key=active.handle.campaign_key,
            plan_revision_digest=active.current_revision_digest,
            ticket_key=ticket_key,
            kind="stale_readback",
            semantic_action_id=run.get("semantic_action_id") or binding_id,
            work_run_key=run["work_run_key"],
            work_subject_digest=run["work_subject_digest"],
            runtime_binding_id=binding_id,
        )
        observation = self._read_or_execute_once(active, state, action)
        if observation is None:
            return False
        stale_readback = self._validate_stale_readback(action, observation, binding_id)
        if (
            stale_readback.state is StaleReadbackState.CANDIDATE_RECEIVED
            and stale_readback.candidate_receipt is None
        ):
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "CANDIDATE_RECEIVED stale readback lacks an exact CandidateReceipt",
            )
        candidate_progress_recorded = False
        candidate_budget_exhausted = False
        if stale_readback.candidate_receipt is not None:
            if stale_readback.state is not StaleReadbackState.CANDIDATE_RECEIVED:
                raise ExecutionKernelError(
                    "EFFECT_READBACK_INVALID",
                    "CandidateReceipt is attached to a non-Candidate stale state",
                )
            candidate_progress_recorded, candidate_budget_exhausted = self._persist_candidate_receipt(
                active,
                state,
                state["runs"][ticket_key],
                ticket_key,
                stale_readback.candidate_receipt,
            )
            run = state["runs"][ticket_key]
        self._persist_effect_readback(active, state, action, stale_readback)
        run = state["runs"][ticket_key]
        run["last_action_id"] = action.stable_action_id
        if candidate_budget_exhausted:
            self._mark_candidate_budget_exhausted(run, ticket_key)
            run["stale_due_at"] = None
            run["stale_disposition"] = stale_readback.state.value
            self._save(active.handle, state)
            return True
        if stale_readback.state in {
            StaleReadbackState.TERMINAL,
            StaleReadbackState.IDLE,
            StaleReadbackState.PERMISSION_WAITING,
            StaleReadbackState.CANDIDATE_RECEIVED,
            StaleReadbackState.PROVIDER_UNAVAILABLE,
        }:
            self._apply_mechanical_stale_readback(
                active,
                state,
                run,
                stale_readback,
                progress_already_recorded=candidate_progress_recorded,
            )
            return True
        if stale_readback.state is not StaleReadbackState.AMBIGUOUS_RUNNING:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "unknown stale readback state",
            )
        diagnosis_id = run.get("stale_diagnosis_action_id")
        if diagnosis_id is None:
            diagnosis_id = self._stale_diagnosis_identity(
                active,
                run,
                binding_id=binding_id,
            )
            run["stale_diagnosis_action_id"] = diagnosis_id
        self._record_diagnosed_binding(state, run, binding_id)
        packet_record = run.get("stale_diagnosis_packet")
        if packet_record is None:
            packet = self._stale_diagnosis_packet(
                active,
                state,
                run,
                ticket_key,
                binding_id,
            )
            run["stale_diagnosis_packet"] = packet.canonical()
            run["stale_diagnosis_packet_digest"] = packet.digest
            run["stale_diagnosis_packet_identity"] = packet.identity
        else:
            packet = StaleDiagnosisPacket.from_canonical(packet_record)
            if (
                packet.runtime_binding_id != binding_id
                or run.get("stale_diagnosis_packet_digest") != packet.digest
                or run.get("stale_diagnosis_packet_identity") != packet.identity
            ):
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "persisted stale diagnosis packet is not bound to the current Runtime Binding",
                )
        diagnosis = WorkRunAction(
            stable_action_id=diagnosis_id,
            repository=active.handle.repository,
            campaign_key=active.handle.campaign_key,
            plan_revision_digest=active.current_revision_digest,
            ticket_key=ticket_key,
            kind="stale_diagnosis",
            semantic_action_id=run.get("semantic_action_id") or binding_id,
            work_run_key=run["work_run_key"],
            work_subject_digest=run["work_subject_digest"],
            runtime_binding_id=binding_id,
            stale_diagnosis_packet=packet,
        )
        diagnosis_observation = self._read_or_execute_once(
            active,
            state,
            diagnosis,
            fence_execute=True,
        )
        if diagnosis_observation is None:
            self._save(active.handle, state)
            return False
        stale_diagnosis = self._validate_stale_diagnosis(
            diagnosis,
            diagnosis_observation,
            binding_id,
        )
        self._persist_effect_readback(active, state, diagnosis, stale_diagnosis)
        run["last_action_id"] = diagnosis.stable_action_id
        self._apply_stale_diagnosis(active, state, run, stale_diagnosis)
        return True

    def _perform_stale_follow_up(
        self,
        active: ActivePlanReadback,
        state: dict[str, Any],
        ticket_key: str,
    ) -> bool:
        run = state["runs"][ticket_key]
        if run.get("stale_follow_up_completed") is True:
            return True
        binding_id = self._stale_binding_id(run)
        try:
            kind = StaleFollowUpKind(run["stale_follow_up_kind"])
        except (KeyError, TypeError, ValueError) as error:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "stale follow-up kind is not closed",
            ) from error
        action_kind = {
            StaleFollowUpKind.GUIDANCE: "stale_guidance",
            StaleFollowUpKind.SAME_BINDING_RECOVERY: "stale_same_binding_recovery",
        }[kind]
        action_id = run.get("stale_follow_up_action_id")
        expected_id = self._stale_follow_up_identity(
            active,
            run,
            binding_id=binding_id,
            kind=kind,
        )
        if action_id != expected_id:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "stale follow-up identity changed",
            )
        action = WorkRunAction(
            stable_action_id=action_id,
            repository=active.handle.repository,
            campaign_key=active.handle.campaign_key,
            plan_revision_digest=active.current_revision_digest,
            ticket_key=ticket_key,
            kind=action_kind,
            semantic_action_id=run.get("semantic_action_id") or binding_id,
            work_run_key=run["work_run_key"],
            work_subject_digest=run["work_subject_digest"],
            runtime_binding_id=binding_id,
            stale_follow_up_kind=kind,
        )
        observation = self._read_or_execute_once(
            active,
            state,
            action,
            fence_execute=True,
        )
        if observation is None:
            return False
        follow_up = self._validate_stale_follow_up(
            action,
            observation,
            binding_id,
            kind,
        )
        self._persist_effect_readback(active, state, action, follow_up)
        run["last_action_id"] = action.stable_action_id
        run["stale_follow_up_completed"] = True
        self._record_trusted_progress(
            state,
            run,
            repository=active.handle.repository,
        )
        run["stale_due_at"] = None
        self._save(active.handle, state)
        return True

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
        # Follow-up effects are durable deterministic work and outrank every
        # semantic action, including a wake hint.
        for ticket_key in sorted(work):
            run = state["runs"][ticket_key]
            if (
                run.get("stale_follow_up_action_id") is not None
                and run.get("stale_follow_up_completed") is not True
            ):
                return ticket_key

        # A due stale binding always receives zero-LLM readback before a wake
        # can select ordinary semantic work.  The timer and wake paths share
        # this exact deadline gate.
        now = self._clock_value()
        for ticket_key in sorted(work):
            run = state["runs"][ticket_key]
            if run["phase"] not in _SLOT_PHASES:
                continue
            due_at = run.get("stale_due_at")
            if due_at is None:
                continue
            if type(due_at) is not str:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "stale due time is not canonical text",
                )
            try:
                due_value = datetime.fromisoformat(due_at)
            except ValueError as error:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "stale due time is unreadable",
                ) from error
            if (
                due_value.tzinfo is None
                or due_value.utcoffset() != timedelta(0)
                or due_value.isoformat() != due_at
            ):
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "stale due time is not canonical UTC text",
                )
            if due_value > now:
                continue
            binding_id = self._stale_binding_id(run)
            diagnosed = state.get("diagnosed_binding_ids", [])
            if type(diagnosed) is not list:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "diagnosed Runtime Binding identities are not a list",
                )
            if binding_id in diagnosed and run.get("stale_disposition") is not None:
                continue
            return ticket_key

        # A wake never starts a second semantic action: it grants one bounded
        # authoritative readback of each already-active Work Run.  No-wake
        # calls are admission/refill only, so they cannot become LLM polling.
        if wake_ref is not None:
            for ticket_key in sorted(work):
                run = state["runs"][ticket_key]
                if (
                    (run["phase"] in _SLOT_PHASES or run.get("slot_held") is True)
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

    @staticmethod
    def _plan_invalidation_record(
        observation: PlanInvalidationObservation,
    ) -> dict[str, Any]:
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
        if observation.source_evidence_digests is not None:
            record["source_evidence_digests"] = list(
                observation.source_evidence_digests
            )
        return record

    def _is_historical_plan_invalidation_replay(
        self,
        active: ActivePlanReadback,
        state: Mapping[str, Any],
        observation: PlanInvalidationObservation,
    ) -> bool:
        """Recognize only an exact receipt archived by successor migration."""

        if observation.plan_revision_digest == active.current_revision_digest:
            return False
        lineage = state.get("revision_lineage", [])
        if type(lineage) is not list:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "ExecutionKernel revision lineage is not a list",
            )
        expected = self._plan_invalidation_record(observation)
        for predecessor in lineage:
            if type(predecessor) is not dict:
                continue
            if predecessor.get("plan_revision_digest") != observation.plan_revision_digest:
                continue
            invalidations = predecessor.get("plan_invalidation")
            if type(invalidations) is not dict:
                continue
            if any(record == expected for record in invalidations.values()):
                return True
        return False

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
        record = self._plan_invalidation_record(observation)
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
        self._record_replan_budget_evidence(
            active,
            state,
            observation,
            run,
        )
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
        gate = self._human_gate_summary(state)
        if gate is not None and gate.phase == "budget_exhausted":
            return
        classifier = getattr(self._plan_control, "classify_plan_invalidations", None)
        if not callable(classifier):
            return
        observations, evidence_digests = self._pending_invalidation_observations(state)
        if not observations:
            return
        if self._successor_revision_budget_exhausted(state):
            budgets = state["replan_budgets"]
            self._persist_budget_exhaustion(
                active,
                state,
                None,
                self._successor_budget_obligation_key(
                    active,
                    evidence_digests=evidence_digests,
                ),
                evidence_digests,
                detail=(
                    "Successor Plan Revision limit exhausted before classification: "
                    f"{budgets['successor_revisions_used']} of "
                    f"{budgets['successor_revision_limit']} revisions used"
                ),
                repeated_invalidations=0,
            )
            self._persist_budget_exhaustion_readback(active, state)
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
            if any(
                item.from_ticket not in work or item.to_ticket not in work
                for item in classification.dependency_additions
            ):
                raise ExecutionKernelError(
                    "PLAN_INVALIDATION_DEPENDENCY_UNPROVED",
                    "classification successor names a Ticket outside the active Campaign",
                )
        if classification.disposition in {
            PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR,
            PlanInvalidationDisposition.REQUIRE_HUMAN_DECISION,
        }:
            # The affected Work Runs remain quiescent.  #135/#136 own the
            # later successor activation or tracker/authority gate.
            if (
                classification.disposition
                is PlanInvalidationDisposition.REQUIRE_HUMAN_DECISION
            ):
                self._ensure_human_gate(active, state, classification)
                self._save(active.handle, state)
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
            legacy_required = {
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
            required_with_source_lineage = legacy_required | {
                "source_evidence_digests"
            }
            if set(record) not in (legacy_required, required_with_source_lineage):
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Campaign Plan Invalidation record schema is not closed",
                )
            source_evidence_digests = record.get("source_evidence_digests")
            if source_evidence_digests is not None and type(source_evidence_digests) is not list:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Campaign Plan Invalidation source Evidence is not a list",
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
                    source_evidence_digests=(
                        None
                        if source_evidence_digests is None
                        else tuple(source_evidence_digests)
                    ),
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
    ) -> bool:
        run = state["runs"][ticket_key]
        candidate_commit_oids = run.setdefault("candidate_commit_oids", [])
        candidate_receipt_digests = run.setdefault("candidate_receipt_digests", [])
        if type(candidate_commit_oids) is not list or any(
            type(oid) is not str
            or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", oid) is None
            for oid in candidate_commit_oids
        ):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Candidate commit OID history is malformed",
            )
        if len(candidate_commit_oids) != len(set(candidate_commit_oids)):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Candidate commit OID history contains duplicates",
            )
        if type(candidate_receipt_digests) is not list or any(
            type(digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for digest in candidate_receipt_digests
        ):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Candidate receipt digest history is malformed",
            )
        if len(candidate_receipt_digests) != len(set(candidate_receipt_digests)):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Candidate receipt digest history contains duplicates",
            )

        stored_receipt = run.get("candidate_receipt")
        history_changed = False
        if stored_receipt is not None:
            try:
                receipt = CandidateReceipt.from_canonical(stored_receipt)
            except CandidateGateError as error:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Candidate budget input failed canonical receipt readback",
                ) from error
            if (
                receipt.repository != active.handle.repository
                or receipt.campaign_key != active.handle.campaign_key
                or receipt.campaign_handle != active.handle.campaign_key
                or receipt.plan_revision_digest != active.current_revision_digest
                or receipt.ticket_key != ticket_key
                or receipt.work_run_key != run["work_run_key"]
                or receipt.runtime_subject_digest != run["work_subject_digest"]
            ):
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Candidate budget receipt is bound to another Work Run",
                )
            if receipt.digest not in candidate_receipt_digests:
                candidate_receipt_digests.append(receipt.digest)
                history_changed = True
            if receipt.candidate_commit_oid not in candidate_commit_oids:
                candidate_commit_oids.append(receipt.candidate_commit_oid)
                history_changed = True
            if len(candidate_commit_oids) > 3:
                self._mark_candidate_budget_exhausted(run, ticket_key)
                self._save(active.handle, state)
                return

        if history_changed:
            self._save(active.handle, state)

        prior_action_id = run.get("last_action_id")
        if (
            run.get("stale_follow_up_action_id") is not None
            and run.get("stale_follow_up_completed") is not True
        ):
            return self._perform_stale_follow_up(active, state, ticket_key)
        if (
            run["phase"] in _SLOT_PHASES
            and run.get("stale_due_at") is not None
            and run.get("stale_disposition") is None
        ):
            due_at = run["stale_due_at"]
            if type(due_at) is not str:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "stale due time is not canonical text",
                )
            try:
                due_value = datetime.fromisoformat(due_at)
            except ValueError as error:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "stale due time is unreadable",
                ) from error
            if due_value <= self._clock_value():
                return self._perform_stale_effect(active, state, ticket_key)
        trusted_before = self._trusted_lifecycle_projection(run)
        trusted_progress_recorded = False
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
            runtime_binding_id=run.get("runtime_binding_id"),
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
                if wake_ref is not None:
                    state.setdefault("last_wake_refs", [])
                    if wake_ref not in state["last_wake_refs"]:
                        state["last_wake_refs"].append(wake_ref)
                        state["last_wake_refs"].sort()
                self._save(active.handle, state)
                return False
            observation = self._effects.execute(action)
        if type(observation) is not WorkRunObservation or observation.stable_action_id != action_id:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "effect result does not bind its stable action identity",
            )
        if observation.runtime_binding_id is not None:
            binding_changed = (
                run.get("runtime_binding_id") is not None
                and run.get("runtime_binding_id") != observation.runtime_binding_id
            )
            if binding_changed:
                evidence = observation.terminal_binding_evidence
                ordinal = run.get("binding_replacement_ordinal", 0)
                if (
                    action.kind != "semantic_resume"
                    or run.get("phase") != "parked"
                    or type(ordinal) is not int
                    or isinstance(ordinal, bool)
                    or ordinal >= 1
                    or type(evidence) is not TerminalBindingEvidence
                    or type(prior_action_id) is not str
                    or not prior_action_id
                    or evidence.prior_action_id != prior_action_id
                    or evidence.prior_runtime_binding_id != run.get("runtime_binding_id")
                    or evidence.agent_id != run.get("runtime_agent_id")
                    or evidence.session_id != run.get("runtime_session_id")
                    or evidence.workspace_id != run.get("runtime_workspace_id")
                    or observation.agent_id is None
                    or observation.session_id is None
                    or observation.workspace_id is None
                ):
                    raise ExecutionKernelError(
                        "EFFECT_READBACK_INVALID",
                        "Runtime Binding replacement lacks one terminal-binding Evidence and allowance",
                    )
                run["binding_replacement_ordinal"] = ordinal + 1
                run["terminal_binding_evidence"] = evidence.canonical()
                run["runtime_agent_id"] = observation.agent_id
                run["runtime_session_id"] = observation.session_id
                run["runtime_workspace_id"] = observation.workspace_id
                run["stale_readback_action_id"] = None
                run["stale_diagnosis_action_id"] = None
                run["stale_diagnosis_packet"] = None
                run["stale_diagnosis_packet_digest"] = None
                run["stale_diagnosis_packet_identity"] = None
                run["stale_disposition"] = None
                run["stale_follow_up_action_id"] = None
                run["stale_follow_up_kind"] = None
                run["stale_follow_up_completed"] = True
            elif (
                action.runtime_binding_id is not None
                and action.runtime_binding_id != observation.runtime_binding_id
            ):
                raise ExecutionKernelError(
                    "EFFECT_READBACK_INVALID",
                    "Work Run observation is not bound to its action Runtime Binding",
                )
            if observation.agent_id is not None:
                for field, value in (
                    ("runtime_agent_id", observation.agent_id),
                    ("runtime_session_id", observation.session_id),
                    ("runtime_workspace_id", observation.workspace_id),
                ):
                    existing = run.get(field)
                    if existing is not None and existing != value:
                        raise ExecutionKernelError(
                            "EFFECT_READBACK_INVALID",
                            "Runtime identity changed without a Runtime Binding replacement",
                        )
                    run[field] = value
            run["runtime_binding_id"] = observation.runtime_binding_id
        run.setdefault("candidate_receipt", None)
        receipt = observation.candidate_receipt
        candidate_budget_exhausted = False
        if receipt is not None:
            if (
                receipt.repository != active.handle.repository
                or receipt.campaign_key != active.handle.campaign_key
                or receipt.campaign_handle != active.handle.campaign_key
                or receipt.plan_revision_digest != active.current_revision_digest
                or receipt.work_run_key != run["work_run_key"]
                or receipt.ticket_key != ticket_key
                or receipt.runtime_subject_digest != run["work_subject_digest"]
            ):
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "CandidateReceipt is bound to another Campaign or Work Run",
                )
            receipt_canonical = receipt.canonical()
            receipt_changed = run.get("candidate_receipt") != receipt_canonical
            run["candidate_receipt"] = receipt_canonical
            run["candidate_receipt_digest"] = receipt.digest
            _history_changed, candidate_budget_exhausted = self._record_candidate_history(
                run,
                receipt,
            )
            state["candidate_receipts"] = [
                value["candidate_receipt"]
                for _key, value in sorted(state["runs"].items())
                if type(value) is dict and value.get("candidate_receipt") is not None
            ]
            if receipt_changed:
                self._record_trusted_progress(
                    state,
                    run,
                    repository=active.handle.repository,
                )
                trusted_progress_recorded = True
            self._save(active.handle, state)
            persisted_state = self._load(active.handle)
            if persisted_state is None:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Campaign state disappeared after CandidateReceipt persistence",
                )
            persisted_run = persisted_state.get("runs", {}).get(ticket_key)
            if type(persisted_run) is not dict:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "CandidateReceipt Work Run disappeared during readback",
                )
            try:
                persisted_receipt = CandidateReceipt.from_canonical(
                    persisted_run.get("candidate_receipt")
                )
            except CandidateGateError as error:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "persisted CandidateReceipt failed canonical readback",
                ) from error
            if persisted_receipt != receipt:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "persisted CandidateReceipt changed during readback",
                )
            state.clear()
            state.update(persisted_state)
            run = state["runs"][ticket_key]
        if candidate_budget_exhausted:
            self._mark_candidate_budget_exhausted(run, ticket_key)
        else:
            run["phase"] = observation.phase
            run["reason"] = observation.reason
            run["next_check_at"] = observation.next_check_at
            run["slot_held"] = observation.phase in _SLOT_PHASES or (
                observation.phase == "wait"
                and observation.binding_established
                and observation.runtime_binding_id is not None
            )
            run["claim_state"] = "held" if run["slot_held"] else "released"
        run["last_wake_ref"] = wake_ref
        if wake_ref is not None:
            state.setdefault("last_wake_refs", [])
            if wake_ref not in state["last_wake_refs"]:
                state["last_wake_refs"].append(wake_ref)
                state["last_wake_refs"].sort()
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
        if not candidate_budget_exhausted and observation.phase == "completed":
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
        if (
            not trusted_progress_recorded
            and (
            prior_effect is None
            or prior_effect.get("state") != "read_back"
            or self._trusted_lifecycle_projection(run) != trusted_before
            )
        ):
            self._record_trusted_progress(
                state,
                run,
                repository=active.handle.repository,
            )
        self._save(active.handle, state)
        return True

    def _outcome(self, handle: CampaignHandle, state: dict[str, Any] | None) -> CampaignOutcome:
        if state is None:
            return CampaignOutcome(CampaignStatus.BLOCKED, "CampaignNotAdvanced")
        gate = self._human_gate_summary(state)
        if gate is not None:
            if gate.phase in {
                "awaiting_human_choice",
                "rejected_change",
                "budget_exhausted",
            }:
                return CampaignOutcome(CampaignStatus.DECISION, gate.reason_code)
            if gate.phase == "awaiting_durable_tracker_policy_readback":
                return CampaignOutcome(CampaignStatus.WAIT, gate.reason_code)
            if gate.phase == "planning_validated_successor":
                return CampaignOutcome(CampaignStatus.RUNNING, gate.reason_code)
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

    def _connect_read_only(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                f"{self._store_path.resolve().as_uri()}?mode=ro",
                uri=True,
            )
        except sqlite3.Error as error:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Execution store is not readable",
            ) from error
        connection.row_factory = sqlite3.Row
        return connection

    def _read_persisted_campaign_without_migration(
        self, handle: CampaignHandle
    ) -> dict[str, Any]:
        if type(handle) is not CampaignHandle:
            raise ExecutionKernelError(
                "CAMPAIGN_HANDLE_INVALID",
                "Campaign handle must be an exact CampaignHandle",
            )
        state = self._load_read_only(handle)
        if state is None:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Campaign state does not exist",
            )
        return state

    def _load_read_only(self, handle: CampaignHandle) -> dict[str, Any] | None:
        try:
            with self._connect_read_only() as connection:
                row = connection.execute(
                    """
                    SELECT state_json FROM v8_execution_kernel_campaigns
                    WHERE repository = ? AND campaign_key = ?
                    """,
                    (handle.repository, handle.campaign_key),
                ).fetchone()
        except ExecutionKernelError:
            raise
        except sqlite3.Error as error:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Execution store schema is unreadable",
            ) from error
        if row is None:
            return None
        try:
            value = json.loads(row["state_json"])
        except json.JSONDecodeError as error:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID", "Campaign state is unreadable"
            ) from error
        if type(value) is not dict:
            raise ExecutionKernelError("EXECUTION_STORE_INVALID", "Campaign state is invalid")
        return value

    def _status_from_persisted_state(self, state: Mapping[str, Any]) -> CampaignStatus:
        if type(state) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Campaign state is not an object",
            )
        try:
            return self._outcome(CampaignHandle("", ""), state).status
        except ExecutionKernelError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Campaign state cannot derive its status",
            ) from error

    def _trusted_progress_digest(
        self,
        state: Mapping[str, Any],
        handle: CampaignHandle | None = None,
    ) -> str:
        if handle is None:
            candidate_receipts: object = state.get("candidate_receipts", [])
        else:
            candidate_receipts = [
                receipt.canonical()
                for _ticket_key, receipt in self._candidate_receipt_records(state, handle)
            ]
        return digest_value(
            {
                "kernel_transition_revision": state.get("trusted_progress_revision", 0),
                "permission_receipts": state.get("normalized_permission_receipts", []),
                "candidate_receipts": candidate_receipts,
                "delivery_receipts": state.get("delivery_receipts", []),
            }
        )

    def _candidate_receipt_records(
        self,
        state: Mapping[str, Any],
        handle: CampaignHandle,
    ) -> tuple[tuple[str, CandidateReceipt], ...]:
        runs = state.get("runs")
        if type(runs) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "ExecutionKernel Work Runs are not a mapping",
            )
        records: list[tuple[str, CandidateReceipt]] = []
        for ticket_key in sorted(runs):
            run = runs[ticket_key]
            if type(run) is not dict:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "ExecutionKernel Work Run is not a mapping",
                )
            stored = run.get("candidate_receipt")
            if stored is None:
                continue
            try:
                receipt = CandidateReceipt.from_canonical(stored)
            except Exception as error:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "persisted CandidateReceipt failed canonical readback",
                ) from error
            if (
                receipt.repository != handle.repository
                or receipt.campaign_key != handle.campaign_key
                or receipt.campaign_handle != handle.campaign_key
                or receipt.plan_revision_digest != state.get("plan_revision_digest")
                or receipt.ticket_key != ticket_key
                or receipt.work_run_key != run.get("work_run_key")
                or receipt.runtime_subject_digest != run.get("work_subject_digest")
            ):
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "CandidateReceipt is bound to another Campaign or Work Run",
                )
            stored_digest = run.get("candidate_receipt_digest")
            if stored_digest != receipt.digest:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "CandidateReceipt digest changed during readback",
                )
            records.append((ticket_key, receipt))
        canonical_records = [receipt.canonical() for _ticket_key, receipt in records]
        if "candidate_receipts" in state:
            stored_records = state["candidate_receipts"]
            if type(stored_records) is not list or stored_records != canonical_records:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "CandidateReceipt projection changed during readback",
                )
        return tuple(records)

    @staticmethod
    def _snapshot_next_check_at(state: Mapping[str, Any]) -> str | None:
        value = state.get("next_check_at")
        if value is not None:
            if type(value) is not str or not value:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_INVALID",
                    "Campaign next_check_at is invalid",
                )
            return value
        values: list[object] = []
        for run in state.get("runs", {}).values():
            if type(run) is not dict:
                continue
            if run.get("next_check_at") is not None:
                values.append(run.get("next_check_at"))
            if run.get("phase") in _SLOT_PHASES and run.get("stale_due_at") is not None:
                values.append(run.get("stale_due_at"))
        if any(type(item) is not str or not item for item in values):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Work Run next_check_at is invalid",
            )
        return min(values) if values else None

    @staticmethod
    def _snapshot_text_tuple(
        value: object,
        label: str,
    ) -> tuple[str, ...]:
        if type(value) not in {list, tuple}:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                f"{label} is not a string collection",
            )
        if any(type(item) is not str or not item for item in value):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                f"{label} contains invalid text",
            )
        return tuple(sorted(set(value)))

    def _snapshot_active_binding_ids(
        self, state: Mapping[str, Any]
    ) -> tuple[str, ...]:
        if "active_binding_ids" in state:
            return self._snapshot_text_tuple(
                state["active_binding_ids"], "active binding identities"
            )
        values: list[str] = []
        for run in state.get("runs", {}).values():
            if type(run) is not dict:
                continue
            if not (run.get("slot_held") or run.get("phase") in _SLOT_PHASES):
                continue
            binding = run.get("runtime_binding_id") or run.get("semantic_action_id")
            if binding is not None:
                values.append(binding)
        return self._snapshot_text_tuple(values, "active binding identities")

    def _snapshot_diagnosed_binding_ids(
        self, state: Mapping[str, Any]
    ) -> tuple[str, ...]:
        if "diagnosed_binding_ids" in state:
            return self._snapshot_text_tuple(
                state["diagnosed_binding_ids"], "diagnosed binding identities"
            )
        values: list[str] = []
        for run in state.get("runs", {}).values():
            if type(run) is dict and type(run.get("diagnosed_binding_ids")) is list:
                values.extend(run["diagnosed_binding_ids"])
        return self._snapshot_text_tuple(values, "diagnosed binding identities")

    def _campaign_lock(self, handle: CampaignHandle) -> threading.RLock:
        key = f"{self._store_path.resolve()}::{handle.repository}::{handle.campaign_key}"
        with _KERNEL_LOCKS_GUARD:
            return _KERNEL_LOCKS.setdefault(key, threading.RLock())

    def _load(self, handle: CampaignHandle) -> dict[str, Any] | None:
        key = (handle.repository, handle.campaign_key)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state_json, state_version FROM v8_execution_kernel_campaigns
                WHERE repository = ? AND campaign_key = ?
                """,
                (handle.repository, handle.campaign_key),
            ).fetchone()
        if row is None:
            self._campaign_row_versions[key] = None
            return None
        version = row["state_version"]
        if type(version) is not int or isinstance(version, bool) or version < 0:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "Campaign durable state version is invalid",
            )
        self._campaign_row_versions[key] = version
        try:
            value = json.loads(row["state_json"])
        except json.JSONDecodeError as error:
            raise ExecutionKernelError("EXECUTION_STORE_INVALID", "Campaign state is unreadable") from error
        if type(value) is not dict:
            raise ExecutionKernelError("EXECUTION_STORE_INVALID", "Campaign state is invalid")
        return value

    def _save(self, handle: CampaignHandle, state: dict[str, Any]) -> None:
        rendered = json.dumps(state, separators=(",", ":"), sort_keys=True)
        key = (handle.repository, handle.campaign_key)
        expected = self._campaign_row_versions.get(key)
        with self._connect() as connection:
            try:
                if expected is None:
                    connection.execute(
                        """
                        INSERT INTO v8_execution_kernel_campaigns
                            (repository, campaign_key, state_json, state_version)
                        VALUES (?, ?, ?, 0)
                        """,
                        (handle.repository, handle.campaign_key, rendered),
                    )
                    next_version = 0
                else:
                    cursor = connection.execute(
                        """
                        UPDATE v8_execution_kernel_campaigns
                        SET state_json = ?, state_version = state_version + 1
                        WHERE repository = ? AND campaign_key = ? AND state_version = ?
                        """,
                        (rendered, handle.repository, handle.campaign_key, expected),
                    )
                    if cursor.rowcount != 1:
                        raise ExecutionKernelError(
                            "EXECUTION_STORE_CONFLICT",
                            "Campaign state changed concurrently",
                        )
                    next_version = expected + 1
            except ExecutionKernelError:
                raise
            except sqlite3.IntegrityError as error:
                raise ExecutionKernelError(
                    "EXECUTION_STORE_CONFLICT",
                    "Campaign state changed concurrently",
                ) from error
        self._campaign_row_versions[key] = next_version


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
    human_decision: HumanDecisionChoice | None = None,
) -> CampaignOutcome:
    """Advance the installed V3 Campaign state machine once."""

    return _installed_execution_kernel().advance(
        campaign_handle,
        wake_ref,
        plan_invalidation=plan_invalidation,
        human_decision=human_decision,
    )


def inspect(campaign_handle: CampaignHandle) -> Diagnostics:
    """Read the installed V3 Campaign diagnostics without an external effect."""

    return _installed_execution_kernel().inspect(campaign_handle)
