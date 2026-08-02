"""PlanControl: compile and activate one immutable PlanSpec v3 revision.

This module owns source readback, one Campaign Planning Pass, immutable
PlanSpec compilation, Ticket claims, and activation readback.  RuntimeGateway
owns every Runtime concern.  In particular, this module sees only its planning
subject plus opaque preflight/progress receipts; it has no provider, Profile,
session, Workspace, or command seam.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from functools import wraps
import re
import threading
from typing import Any, Mapping, Protocol, Sequence

from ._canonical import CanonicalJsonError, canonical_bytes, digest_bytes, digest_value, load_canonical_json
from .planning_protocol import (
    PLANNING_OUTPUT_PROTOCOL_ID,
    REPLANNING_OUTPUT_PROTOCOL_ID,
    planning_prompt,
    replanning_prompt,
)
from .runtime_gateway import (
    CampaignPlanningSubject,
    CoordinatorCapabilityProof,
    PlanningPreflightReceipt,
    PlanningReceipt,
)
from .revision_identity import AcceptedResultBinding


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_VERSIONED_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\.v[1-9][0-9]*$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9][a-z0-9_-]*)*$")
_AUTO_PREVIOUS = object()
_TRIAGE = frozenset({"needs-triage", "needs-info", "ready-for-agent", "ready-for-human", "wontfix"})
_POLICY_ROLES = ("campaign", "worker", "recovery_worker", "review")
_ROLE_AUTHORITY_GRANTS = {
    "campaign": (("repository.read.v1", "campaign.snapshot.v1"),),
    "worker": (("workspace.write.v1", "work-run.workspace.v1"),),
    "recovery_worker": (("workspace.write.v1", "work-run.workspace.v1"),),
    "review": (("repository.read.v1", "review.subject.v1"),),
}
_OPERATION_ROOTS = frozenset({"artifact", "ci", "git", "github", "repository", "workspace"})
_RESOURCE_ROOTS = frozenset({"artifact", "campaign", "candidate", "repository", "review", "target", "work-run"})
_MAX_DEPENDENCY_NODES = 8_192
_MAX_DEPENDENCY_EDGES = 65_536


class PlanControlError(RuntimeError):
    """A named fail-closed PlanControl outcome."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _repository_locked(method):
    """Serialize the in-memory durable-double's complete CAS surface."""

    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return guarded


@dataclass(frozen=True)
class CampaignHandle:
    """The stable opaque Campaign identity, independent of a Plan Revision."""

    repository: str
    campaign_key: str


@dataclass(frozen=True)
class DecisionFinding:
    code: str
    detail: str
    ticket_key: str | None = None


class PlanInvalidationDisposition(str, Enum):
    """The closed legal result of one Campaign invalidation classification."""

    RESUME_UNCHANGED = "resume_unchanged"
    DEFER_NON_BLOCKING = "defer_non_blocking"
    USE_APPROVED_SUCCESSOR = "use_approved_successor"
    REQUIRE_HUMAN_DECISION = "require_human_decision"
    REJECT_INVALID_EVIDENCE = "reject_invalid_evidence"


@dataclass(frozen=True)
class PlanInvalidationDependency:
    """One Coordinator-justified edge between already approved Tickets."""

    from_ticket: str
    to_ticket: str
    reason: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.from_ticket, "dependency source"),
            (self.to_ticket, "dependency target"),
            (self.reason, "dependency reason"),
        ):
            _text(value, label)
        if self.from_ticket == self.to_ticket:
            raise PlanControlError(
                "PLAN_INVALIDATION_DEPENDENCY_INVALID",
                "Plan Invalidation dependency cannot point to itself",
            )

    def canonical(self) -> dict[str, str]:
        return {
            "from": self.from_ticket,
            "to": self.to_ticket,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PlanInvalidationExclusiveResource:
    ticket_key: str
    resource_id: str
    reason: str

    def __post_init__(self) -> None:
        _text(self.ticket_key, "Exclusive Resource Ticket")
        _text(self.resource_id, "Exclusive Resource ID")
        _text(self.reason, "Exclusive Resource reason")

    def canonical(self) -> dict[str, str]:
        return {
            "ticket_key": self.ticket_key,
            "resource_id": self.resource_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PlanInvalidationDecision:
    """A named human choice required before the Campaign can change."""

    code: str
    detail: str
    required_change: str

    _CHANGES = frozenset(
        {
            "new_ticket",
            "acceptance",
            "campaign_membership",
            "authority",
            "product",
            "replan_budget",
        }
    )

    def __post_init__(self) -> None:
        _text(self.code, "Decision code")
        _text(self.detail, "Decision detail")
        if type(self.required_change) is not str or self.required_change not in self._CHANGES:
            raise PlanControlError(
                "PLAN_INVALIDATION_DECISION_INVALID",
                "Decision required_change is outside the closed union",
            )

    def canonical(self) -> dict[str, str]:
        return {
            "code": self.code,
            "detail": self.detail,
            "required_change": self.required_change,
        }


@dataclass(frozen=True)
class PlanInvalidationClassification:
    """Typed, read-only Coordinator output for one active revision."""

    action_id: str
    snapshot_digest: str
    plan_revision_digest: str
    evidence_digests: tuple[str, ...]
    disposition: PlanInvalidationDisposition
    reason: str
    capability_proof_digest: str
    successor_ticket_keys: tuple[str, ...] = ()
    dependency_additions: tuple[PlanInvalidationDependency, ...] = ()
    exclusive_resource_additions: tuple[PlanInvalidationExclusiveResource, ...] = ()
    decision: PlanInvalidationDecision | None = None

    def __post_init__(self) -> None:
        _text(self.action_id, "classification action_id")
        _digest(self.snapshot_digest, "classification snapshot digest")
        _digest(self.plan_revision_digest, "classification Plan Revision digest")
        _digest(self.capability_proof_digest, "classification capability proof digest")
        if type(self.evidence_digests) is not tuple or not self.evidence_digests:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "classification must account for at least one Evidence digest",
            )
        if any(
            type(value) is not str or _DIGEST.fullmatch(value) is None
            for value in self.evidence_digests
        ):
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "classification Evidence identities must be SHA-256 digests",
            )
        if tuple(sorted(set(self.evidence_digests))) != self.evidence_digests:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "classification Evidence identities must be sorted and unique",
            )
        if type(self.disposition) is not PlanInvalidationDisposition:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "classification disposition is outside the closed union",
            )
        _text(self.reason, "classification reason")
        if type(self.successor_ticket_keys) is not tuple or any(
            type(key) is not str or not key for key in self.successor_ticket_keys
        ):
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "successor Ticket keys are invalid",
            )
        if tuple(sorted(set(self.successor_ticket_keys))) != self.successor_ticket_keys:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "successor Ticket keys must be sorted and unique",
            )
        if type(self.dependency_additions) is not tuple or any(
            type(item) is not PlanInvalidationDependency
            for item in self.dependency_additions
        ):
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "successor dependency additions are invalid",
            )
        if tuple(
            sorted(
                self.dependency_additions,
                key=lambda item: (item.from_ticket, item.to_ticket, item.reason),
            )
        ) != self.dependency_additions:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "successor dependency additions must be canonical",
            )
        if type(self.exclusive_resource_additions) is not tuple or any(
            type(item) is not PlanInvalidationExclusiveResource
            for item in self.exclusive_resource_additions
        ):
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "successor exclusive resource additions are invalid",
            )
        if tuple(
            sorted(
                self.exclusive_resource_additions,
                key=lambda item: (item.ticket_key, item.resource_id, item.reason),
            )
        ) != self.exclusive_resource_additions:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "successor exclusive resource additions must be canonical",
            )
        if len(
            {
                (item.ticket_key, item.resource_id)
                for item in self.exclusive_resource_additions
            }
        ) != len(self.exclusive_resource_additions):
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "successor exclusive resource additions repeat a resource",
            )
        if self.decision is not None and type(self.decision) is not PlanInvalidationDecision:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "classification Decision is invalid",
            )
        if self.disposition is PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR:
            if not self.successor_ticket_keys or self.decision is not None:
                raise PlanControlError(
                    "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                    "approved successor classification must name approved work only",
                )
        elif self.disposition is PlanInvalidationDisposition.REQUIRE_HUMAN_DECISION:
            if (
                self.decision is None
                or self.successor_ticket_keys
                or self.dependency_additions
                or self.exclusive_resource_additions
            ):
                raise PlanControlError(
                    "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                    "human Decision classification cannot carry successor work",
                )
        elif (
            self.successor_ticket_keys
            or self.dependency_additions
            or self.exclusive_resource_additions
            or self.decision is not None
        ):
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "resume/defer/reject classification cannot carry plan or Decision changes",
            )

    def canonical(self) -> dict[str, Any]:
        successor = None
        if self.disposition is PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR:
            successor = {
                "approved_ticket_keys": list(self.successor_ticket_keys),
                "dependency_additions": [
                    item.canonical() for item in self.dependency_additions
                ],
                "exclusive_resource_additions": [
                    item.canonical() for item in self.exclusive_resource_additions
                ],
            }
        return {
            "kind": "plan_invalidation_classification.v1",
            "action_id": self.action_id,
            "snapshot_digest": self.snapshot_digest,
            "plan_revision_digest": self.plan_revision_digest,
            "evidence_digests": list(self.evidence_digests),
            "disposition": self.disposition.value,
            "reason": self.reason,
            "successor": successor,
            "decision": None if self.decision is None else self.decision.canonical(),
            "capability_proof_digest": self.capability_proof_digest,
        }

    @property
    def digest(self) -> str:
        return digest_value(self.canonical())

    @classmethod
    def from_canonical(cls, value: Mapping[str, Any]) -> "PlanInvalidationClassification":
        expected = {
            "kind",
            "action_id",
            "snapshot_digest",
            "plan_revision_digest",
            "evidence_digests",
            "disposition",
            "reason",
            "successor",
            "decision",
            "capability_proof_digest",
        }
        if type(value) is not dict or set(value) != expected or value["kind"] != "plan_invalidation_classification.v1":
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "classification record schema is not closed",
            )
        evidence = value["evidence_digests"]
        if type(evidence) is not list or any(type(item) is not str for item in evidence):
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "classification Evidence identities are not a list",
            )
        try:
            disposition = PlanInvalidationDisposition(value["disposition"])
            successor = value["successor"]
            if successor is None:
                successor_keys: tuple[str, ...] = ()
                dependencies: tuple[PlanInvalidationDependency, ...] = ()
                resources: tuple[PlanInvalidationExclusiveResource, ...] = ()
            else:
                if type(successor) is not dict or set(successor) != {
                    "approved_ticket_keys",
                    "dependency_additions",
                    "exclusive_resource_additions",
                }:
                    raise PlanControlError(
                        "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                        "classification successor schema is invalid",
                    )
                raw_successor_keys = successor["approved_ticket_keys"]
                raw_dependencies = successor["dependency_additions"]
                raw_resources = successor["exclusive_resource_additions"]
                if (
                    type(raw_successor_keys) is not list
                    or any(type(item) is not str for item in raw_successor_keys)
                    or type(raw_dependencies) is not list
                    or any(
                        type(item) is not dict
                        or set(item) != {"from", "to", "reason"}
                        for item in raw_dependencies
                    )
                    or type(raw_resources) is not list
                    or any(
                        type(item) is not dict
                        or set(item) != {"ticket_key", "resource_id", "reason"}
                        for item in raw_resources
                    )
                ):
                    raise PlanControlError(
                        "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                        "classification successor values are invalid",
                    )
                successor_keys = tuple(raw_successor_keys)
                dependencies = tuple(
                    PlanInvalidationDependency(
                        from_ticket=item["from"],
                        to_ticket=item["to"],
                        reason=item["reason"],
                    )
                    for item in raw_dependencies
                )
                resources = tuple(
                    PlanInvalidationExclusiveResource(
                        ticket_key=item["ticket_key"],
                        resource_id=item["resource_id"],
                        reason=item["reason"],
                    )
                    for item in raw_resources
                )
            if (
                disposition is not PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR
                and successor is not None
            ):
                raise PlanControlError(
                    "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                    "non-successor classification cannot carry successor values",
                )
            decision_value = value["decision"]
            if decision_value is not None and (
                type(decision_value) is not dict
                or set(decision_value) != {"code", "detail", "required_change"}
            ):
                raise PlanControlError(
                    "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                    "classification Decision values are invalid",
                )
            decision = (
                None
                if decision_value is None
                else PlanInvalidationDecision(**decision_value)
            )
            return cls(
                action_id=value["action_id"],
                snapshot_digest=value["snapshot_digest"],
                plan_revision_digest=value["plan_revision_digest"],
                evidence_digests=tuple(evidence),
                disposition=disposition,
                reason=value["reason"],
                capability_proof_digest=value["capability_proof_digest"],
                successor_ticket_keys=successor_keys,
                dependency_additions=dependencies,
                exclusive_resource_additions=resources,
                decision=decision,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "classification record cannot be decoded",
            ) from error


class PlanControlDecision(PlanControlError):
    """A Planning Pass named a durable human Decision instead of a Plan."""

    def __init__(self, snapshot_digest: str, findings: tuple[DecisionFinding, ...]):
        super().__init__("PLAN_CONTROL_DECISION_REQUIRED", "Campaign Planning requires a named Decision")
        self.snapshot_digest = snapshot_digest
        self.findings = findings
        self.decision_digest = digest_value(
            {
                "snapshot_digest": snapshot_digest,
                "findings": [finding.__dict__ for finding in findings],
            }
        )


class SplitCampaignDecision(PlanControlError):
    """A durable named Decision to split an input that cannot be planned safely."""

    def __init__(
        self,
        *,
        handle: CampaignHandle,
        snapshot_digest: str,
        snapshot_byte_length: int,
        maximum_snapshot_bytes: int,
        decision_digest: str,
    ):
        super().__init__(
            "SPLIT_CAMPAIGN_REQUIRED",
            "Campaign snapshot exceeds the configured Planning input bound",
        )
        self.handle = handle
        self.snapshot_digest = snapshot_digest
        self.snapshot_byte_length = snapshot_byte_length
        self.maximum_snapshot_bytes = maximum_snapshot_bytes
        self.decision_digest = decision_digest


@dataclass(frozen=True)
class PlanRevision:
    repository: str
    campaign_key: str
    snapshot_digest: str
    canonical_bytes: bytes
    digest: str

    @property
    def plan_spec(self) -> dict[str, Any]:
        value = load_canonical_json(self.canonical_bytes)
        assert type(value) is dict
        return value


@dataclass(frozen=True)
class ActivationReceipt:
    repository: str
    campaign_key: str
    revision_digest: str
    expected_previous_revision_digest: str | None
    writer_generation: str
    ready_refs: tuple[str, ...]
    ticket_keys: tuple[str, ...]
    planning_subject_digest: str
    planning_stable_action_id: str
    planning_preflight_receipt_digest: str
    # The activation commit point binds the entire one-pass authority, not
    # merely the preflight.  These immutable identities prevent a durable
    # state from re-addressing an otherwise valid revision to another #111
    # receipt or output after activation.
    compilation_record_artifact_digest: str
    planning_receipt_digest: str
    planning_output_artifact_digest: str


@dataclass(frozen=True)
class PlanningReservation:
    """Repository-global non-executable claim made after exact preflight."""

    repository: str
    campaign_key: str
    ticket_keys: tuple[str, ...]
    subject_digest: str
    stable_action_id: str
    preflight_receipt_digest: str
    # Invalidation classification keeps its immutable bounded input stable
    # while an unrelated Work Run continues during a pending Coordinator
    # readback.  Initial Planning reservations leave these optional fields
    # unset for backwards-compatible V8 state.
    snapshot_artifact_digest: str | None = None
    policy_witness_digest: str | None = None
    planning_request_artifact_digest: str | None = None


@dataclass(frozen=True)
class TicketClaimProof:
    """Read-only proof of one repository-global Ticket claim."""

    ticket_key: str
    repository: str
    campaign_key: str
    plan_revision_digest: str


@dataclass(frozen=True)
class ActivePlanReadback:
    """Internal #110 seam reconstructed only from activated immutable facts."""

    handle: CampaignHandle
    current_revision_digest: str
    plan_spec_bytes: bytes
    activation_receipt: ActivationReceipt
    claim_proofs: tuple[TicketClaimProof, ...]


@dataclass(frozen=True)
class _ActiveAuthorityEnvelope:
    """The one immutable authority root consumed by active/recovery paths.

    This deliberately collects every value whose independent readback could
    otherwise be mixed before claims are finalized.  It has no public surface:
    callers still receive only ``ActivePlanReadback``.
    """

    handle: CampaignHandle
    receipt: ActivationReceipt
    attempt: _PlanningAttempt
    revision: PlanRevision
    snapshot: Mapping[str, Any]
    compilation_record: Mapping[str, Any]
    claim_proofs: tuple[TicketClaimProof, ...]


@dataclass(frozen=True)
class _SplitCampaignDecisionRecord:
    handle: CampaignHandle
    ready_refs: tuple[str, ...]
    ticket_keys: tuple[str, ...]
    expected_previous_revision_digest: str | None
    canonical_bytes: bytes
    digest: str


@dataclass(frozen=True)
class _PlanningAttempt:
    handle: CampaignHandle
    ready_refs: tuple[str, ...]
    ticket_keys: tuple[str, ...]
    expected_previous_revision_digest: str | None
    snapshot_bytes: bytes
    snapshot_artifact_digest: str
    policy_witness_digest: str
    planning_request_artifact_digest: str
    subject: CampaignPlanningSubject
    planning_protocol_id: str = PLANNING_OUTPUT_PROTOCOL_ID
    compilation_record_artifact_digest: str | None = None
    revision: PlanRevision | None = None
    # The governed production repository persists this immutable copy so a
    # fresh host can reconstruct the Artifact-backed record before any active
    # Plan readback.  It is absent only while Planning is still incomplete.
    compilation_record_bytes: bytes | None = None

    def __post_init__(self) -> None:
        if type(self.planning_protocol_id) is not str or self.planning_protocol_id not in {
            PLANNING_OUTPUT_PROTOCOL_ID,
            REPLANNING_OUTPUT_PROTOCOL_ID,
        }:
            raise PlanControlError(
                "PLANNING_ATTEMPT_PROTOCOL_INVALID",
                "Planning attempt protocol is outside the closed protocol union",
            )


class CampaignSnapshotSource(Protocol):
    """The GitHub/Ticket adapter, deliberately outside PlanControl's policy."""

    def snapshot(self, repository: str, ready_refs: tuple[str, ...]) -> Mapping[str, Any]: ...


class PlanningArtifacts(Protocol):
    """Digest-addressed host Artifact port shared with RuntimeGateway."""

    def put_canonical(self, value: Any) -> Any: ...

    def get(self, digest: str) -> Any: ...

    def read_json(self, digest: str) -> Any: ...


class CampaignPlanningGateway(Protocol):
    """The intentionally tiny #111 caller surface used by PlanControl."""

    def planning_preflight(self, subject: CampaignPlanningSubject) -> PlanningPreflightReceipt: ...

    def progress(self, subject: CampaignPlanningSubject, preflight: PlanningPreflightReceipt) -> PlanningReceipt: ...

    def _read_coordinator_capability(
        self,
        subject: CampaignPlanningSubject,
    ) -> CoordinatorCapabilityProof: ...


class PlanControlRepository(Protocol):
    """Durable facts required by PlanControl's claim and activation boundary."""

    def active_receipt(self, handle: CampaignHandle) -> ActivationReceipt | None: ...

    def read_attempt(self, handle: CampaignHandle, expected_previous_revision_digest: str | None) -> _PlanningAttempt | None: ...

    def save_attempt(self, attempt: _PlanningAttempt) -> _PlanningAttempt: ...

    def read_split_decision(
        self,
        handle: CampaignHandle,
        expected_previous_revision_digest: str | None,
    ) -> _SplitCampaignDecisionRecord | None: ...

    def save_split_decision(
        self,
        decision: _SplitCampaignDecisionRecord,
    ) -> _SplitCampaignDecisionRecord: ...

    def reserve_planning(self, reservation: PlanningReservation) -> None: ...

    def release_planning(self, reservation: PlanningReservation) -> None: ...

    def read_planning_reservation(
        self,
        handle: CampaignHandle,
        stable_action_id: str,
    ) -> PlanningReservation | None: ...

    def reserve_claims(self, receipt: ActivationReceipt) -> None: ...

    def publish_revision(self, revision: PlanRevision) -> None: ...

    def read_revision(self, digest: str) -> PlanRevision | None: ...

    def activate(self, receipt: ActivationReceipt) -> None: ...

    def finalize_claims(self, receipt: ActivationReceipt) -> None: ...

    def read_pending_reservation(
        self,
        receipt: ActivationReceipt,
    ) -> ActivationReceipt | None: ...

    def read_activation(self, handle: CampaignHandle) -> ActivationReceipt | None: ...

    def read_claim_proofs(
        self,
        handle: CampaignHandle,
        revision_digest: str,
    ) -> tuple[TicketClaimProof, ...]: ...

    def read_campaign_claim_proofs(
        self,
        handle: CampaignHandle,
    ) -> tuple[TicketClaimProof, ...]: ...

    def read_invalidation_classification(
        self,
        handle: CampaignHandle,
        action_id: str,
    ) -> PlanInvalidationClassification | None: ...

    def save_invalidation_classification(
        self,
        handle: CampaignHandle,
        classification: PlanInvalidationClassification,
    ) -> PlanInvalidationClassification: ...


class InMemoryPlanRepository:
    """Deterministic repository double with the same CAS/readback semantics."""

    def __init__(self, *, writer_generation: str):
        _text(writer_generation, "writer_generation")
        self.writer_generation = writer_generation
        self._lock = threading.RLock()
        self.attempts: dict[tuple[str, str, str | None], _PlanningAttempt] = {}
        self.claims: dict[tuple[str, str], str] = {}
        self._claim_campaigns: dict[tuple[str, str], str] = {}
        self.pending_reservations: dict[
            tuple[str, str, str], ActivationReceipt
        ] = {}
        self.planning_reservations: dict[
            tuple[str, str, str], PlanningReservation
        ] = {}
        self.split_decisions: dict[
            tuple[str, str, str | None], _SplitCampaignDecisionRecord
        ] = {}
        self.revisions: dict[str, PlanRevision] = {}
        self.activations: dict[tuple[str, str], ActivationReceipt] = {}
        # ``activations`` is the mutable current pointer only.  Retain every
        # published receipt separately so a successor cannot erase audit
        # evidence for its predecessor.
        self.activation_receipts: dict[
            tuple[str, str, str, str], ActivationReceipt
        ] = {}
        self.invalidation_classifications: dict[
            tuple[str, str, str], PlanInvalidationClassification
        ] = {}

    @staticmethod
    def _attempt_key(handle: CampaignHandle, previous: str | None) -> tuple[str, str, str | None]:
        return (handle.repository, handle.campaign_key, previous)

    @_repository_locked
    def active_receipt(self, handle: CampaignHandle) -> ActivationReceipt | None:
        return self.activations.get((handle.repository, handle.campaign_key))

    @_repository_locked
    def read_attempt(self, handle: CampaignHandle, expected_previous_revision_digest: str | None) -> _PlanningAttempt | None:
        return self.attempts.get(self._attempt_key(handle, expected_previous_revision_digest))

    @_repository_locked
    def save_attempt(self, attempt: _PlanningAttempt) -> _PlanningAttempt:
        key = self._attempt_key(attempt.handle, attempt.expected_previous_revision_digest)
        existing = self.attempts.get(key)
        if existing is not None:
            immutable_existing = replace(
                existing,
                compilation_record_artifact_digest=None,
                revision=None,
                compilation_record_bytes=None,
            )
            immutable_attempt = replace(
                attempt,
                compilation_record_artifact_digest=None,
                revision=None,
                compilation_record_bytes=None,
            )
            if immutable_existing != immutable_attempt:
                raise PlanControlError(
                    "PLANNING_ATTEMPT_IDENTITY_CONFLICT",
                    "Campaign attempt changed its immutable subject or snapshot",
                )
            if (
                existing.compilation_record_artifact_digest is not None
                and existing.compilation_record_artifact_digest
                != attempt.compilation_record_artifact_digest
            ):
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Campaign attempt replaced its bound compilation record",
                )
            if existing.revision is not None and existing.revision != attempt.revision:
                raise PlanControlError(
                    "PLAN_PUBLICATION_CONFLICT",
                    "Campaign attempt replaced its compiled Plan Revision",
                )
            if (
                existing.compilation_record_bytes is not None
                and existing.compilation_record_bytes
                != attempt.compilation_record_bytes
            ):
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Campaign attempt replaced its durable compilation record",
                )
        self.attempts[key] = attempt
        return attempt

    @_repository_locked
    def read_split_decision(
        self,
        handle: CampaignHandle,
        expected_previous_revision_digest: str | None,
    ) -> _SplitCampaignDecisionRecord | None:
        return self.split_decisions.get(
            self._attempt_key(handle, expected_previous_revision_digest)
        )

    @_repository_locked
    def save_split_decision(
        self,
        decision: _SplitCampaignDecisionRecord,
    ) -> _SplitCampaignDecisionRecord:
        key = self._attempt_key(
            decision.handle,
            decision.expected_previous_revision_digest,
        )
        existing = self.split_decisions.get(key)
        if existing is not None and existing != decision:
            raise PlanControlError(
                "SPLIT_CAMPAIGN_DECISION_CONFLICT",
                "Split-Campaign Decision changed its immutable identity",
            )
        self.split_decisions[key] = decision
        return decision

    @staticmethod
    def _reservation_key(receipt: ActivationReceipt) -> tuple[str, str, str]:
        return (
            receipt.repository,
            receipt.campaign_key,
            receipt.revision_digest,
        )

    @staticmethod
    def _planning_reservation_key(
        reservation: PlanningReservation,
    ) -> tuple[str, str, str]:
        return (
            reservation.repository,
            reservation.campaign_key,
            reservation.stable_action_id,
        )

    @_repository_locked
    def reserve_planning(self, reservation: PlanningReservation) -> None:
        if (
            type(reservation) is not PlanningReservation
            or type(reservation.repository) is not str
            or not reservation.repository
            or type(reservation.campaign_key) is not str
            or not reservation.campaign_key
            or type(reservation.ticket_keys) is not tuple
            or not reservation.ticket_keys
            or any(
                type(ticket_key) is not str or not ticket_key
                for ticket_key in reservation.ticket_keys
            )
            or len(set(reservation.ticket_keys)) != len(
                reservation.ticket_keys
            )
            or type(reservation.subject_digest) is not str
            or _DIGEST.fullmatch(reservation.subject_digest) is None
            or type(reservation.stable_action_id) is not str
            or not reservation.stable_action_id
            or type(reservation.preflight_receipt_digest) is not str
            or _DIGEST.fullmatch(reservation.preflight_receipt_digest) is None
            or any(
                value is not None
                and (type(value) is not str or _DIGEST.fullmatch(value) is None)
                for value in (
                    reservation.snapshot_artifact_digest,
                    reservation.policy_witness_digest,
                    reservation.planning_request_artifact_digest,
                )
            )
            or (
                reservation.stable_action_id.startswith("replan:")
                and (
                    reservation.snapshot_artifact_digest is None
                    or reservation.policy_witness_digest is None
                    or reservation.planning_request_artifact_digest is None
                )
            )
        ):
            raise PlanControlError(
                "PLANNING_RESERVATION_INVALID",
                "Planning reservation must be one exact non-executable claim",
            )
        key = self._planning_reservation_key(reservation)
        with self._lock:
            existing = self.planning_reservations.get(key)
            if existing is not None:
                if existing != reservation:
                    raise PlanControlError(
                        "PLANNING_RESERVATION_CONFLICT",
                        "Planning reservation changed its exact preflight binding",
                    )
                return
            conflicts = {
                ticket_key
                for ticket_key in reservation.ticket_keys
                if (
                    (reservation.repository, ticket_key)
                    in self._claim_campaigns
                    and self._claim_campaigns[
                        (reservation.repository, ticket_key)
                    ]
                    != reservation.campaign_key
                )
            }
            for pending in self.pending_reservations.values():
                if (
                    pending.repository == reservation.repository
                    and pending.campaign_key != reservation.campaign_key
                ):
                    conflicts.update(
                        set(pending.ticket_keys).intersection(
                            reservation.ticket_keys
                        )
                    )
            for pending in self.planning_reservations.values():
                if (
                    pending.repository == reservation.repository
                    and pending.campaign_key != reservation.campaign_key
                ):
                    conflicts.update(
                        set(pending.ticket_keys).intersection(
                            reservation.ticket_keys
                        )
                    )
            if conflicts:
                raise PlanControlError(
                    "TICKET_CLAIM_CONFLICT",
                    "Ticket claims overlap: " + ", ".join(sorted(conflicts)),
                )
            self.planning_reservations[key] = reservation

    @_repository_locked
    def release_planning(self, reservation: PlanningReservation) -> None:
        if type(reservation) is not PlanningReservation:
            raise PlanControlError(
                "PLANNING_RESERVATION_INVALID",
                "Planning reservation release requires its exact receipt",
            )
        key = self._planning_reservation_key(reservation)
        with self._lock:
            existing = self.planning_reservations.get(key)
            if existing is None:
                return
            if existing != reservation:
                raise PlanControlError(
                    "PLANNING_RESERVATION_CONFLICT",
                    "Planning reservation release changed its identity",
                )
            self.planning_reservations.pop(key)

    @_repository_locked
    def read_planning_reservation(
        self,
        handle: CampaignHandle,
        stable_action_id: str,
    ) -> PlanningReservation | None:
        if type(handle) is not CampaignHandle or type(stable_action_id) is not str:
            raise PlanControlError(
                "PLANNING_RESERVATION_INVALID",
                "Planning reservation readback identity is invalid",
            )
        return self.planning_reservations.get(
            (handle.repository, handle.campaign_key, stable_action_id)
        )

    @_repository_locked
    def reserve_claims(self, receipt: ActivationReceipt) -> None:
        if (
            type(receipt) is not ActivationReceipt
            or type(receipt.repository) is not str
            or not receipt.repository
            or type(receipt.campaign_key) is not str
            or not receipt.campaign_key
            or type(receipt.revision_digest) is not str
            or _DIGEST.fullmatch(receipt.revision_digest) is None
            or (
                receipt.expected_previous_revision_digest is not None
                and (
                    type(receipt.expected_previous_revision_digest) is not str
                    or _DIGEST.fullmatch(
                        receipt.expected_previous_revision_digest
                    )
                    is None
                )
            )
            or type(receipt.writer_generation) is not str
            or not receipt.writer_generation
            or receipt.writer_generation != self.writer_generation
            or type(receipt.ready_refs) is not tuple
            or type(receipt.ticket_keys) is not tuple
            or not receipt.ready_refs
            or not receipt.ticket_keys
            or any(type(item) is not str or not item for item in receipt.ready_refs)
            or any(type(item) is not str or not item for item in receipt.ticket_keys)
            or len(set(receipt.ready_refs)) != len(receipt.ready_refs)
            or len(set(receipt.ticket_keys)) != len(receipt.ticket_keys)
            or type(receipt.planning_subject_digest) is not str
            or _DIGEST.fullmatch(receipt.planning_subject_digest) is None
            or type(receipt.planning_stable_action_id) is not str
            or not receipt.planning_stable_action_id
            or type(receipt.planning_preflight_receipt_digest) is not str
            or _DIGEST.fullmatch(
                receipt.planning_preflight_receipt_digest
            )
            is None
            or type(receipt.compilation_record_artifact_digest) is not str
            or _DIGEST.fullmatch(
                receipt.compilation_record_artifact_digest
            )
            is None
            or type(receipt.planning_receipt_digest) is not str
            or _DIGEST.fullmatch(receipt.planning_receipt_digest) is None
            or type(receipt.planning_output_artifact_digest) is not str
            or _DIGEST.fullmatch(
                receipt.planning_output_artifact_digest
            )
            is None
        ):
            raise PlanControlError(
                "TICKET_RESERVATION_CONFLICT",
                "Activation reservation requires one exact receipt",
            )
        reservation_key = self._reservation_key(receipt)
        planning_key = (
            receipt.repository,
            receipt.campaign_key,
            receipt.planning_stable_action_id,
        )
        expected_planning = PlanningReservation(
            repository=receipt.repository,
            campaign_key=receipt.campaign_key,
            ticket_keys=receipt.ticket_keys,
            subject_digest=receipt.planning_subject_digest,
            stable_action_id=receipt.planning_stable_action_id,
            preflight_receipt_digest=(
                receipt.planning_preflight_receipt_digest
            ),
        )
        with self._lock:
            existing = self.pending_reservations.get(reservation_key)
            if existing is not None:
                if existing != receipt:
                    raise PlanControlError(
                        "TICKET_RESERVATION_CONFLICT",
                        "Pending Ticket reservation changed its immutable identity",
                    )
                return
            existing_planning = self.planning_reservations.get(planning_key)
            if existing_planning is None or any(
                (
                    getattr(existing_planning, field)
                    != getattr(expected_planning, field)
                )
                for field in (
                    "repository",
                    "campaign_key",
                    "ticket_keys",
                    "subject_digest",
                    "stable_action_id",
                    "preflight_receipt_digest",
                )
            ):
                raise PlanControlError(
                    "PLANNING_RESERVATION_MISSING",
                    "Plan publication lacks its exact preflight Planning reservation",
                )
            owner = receipt.campaign_key
            conflicts = {
                ticket_key
                for ticket_key in receipt.ticket_keys
                if (
                    (receipt.repository, ticket_key) in self._claim_campaigns
                    and self._claim_campaigns[
                        (receipt.repository, ticket_key)
                    ]
                    != owner
                )
            }
            for pending in self.pending_reservations.values():
                if (
                    pending.repository == receipt.repository
                    and pending.campaign_key != receipt.campaign_key
                ):
                    conflicts.update(
                        set(pending.ticket_keys).intersection(
                            receipt.ticket_keys
                        )
                    )
            for pending in self.planning_reservations.values():
                if (
                    pending.repository == receipt.repository
                    and pending.campaign_key != receipt.campaign_key
                ):
                    conflicts.update(
                        set(pending.ticket_keys).intersection(
                            receipt.ticket_keys
                        )
                    )
            if conflicts:
                raise PlanControlError(
                    "TICKET_CLAIM_CONFLICT",
                    "Ticket claims overlap: " + ", ".join(sorted(conflicts)),
                )
            self.pending_reservations[reservation_key] = receipt
            self.planning_reservations.pop(planning_key)

    @_repository_locked
    def publish_revision(self, revision: PlanRevision) -> None:
        existing = self.revisions.get(revision.digest)
        if existing is not None and existing != revision:
            raise PlanControlError("PLAN_PUBLICATION_CONFLICT", "Plan revision digest resolves to different bytes")
        self.revisions[revision.digest] = revision

    @_repository_locked
    def read_revision(self, digest: str) -> PlanRevision | None:
        return self.revisions.get(digest)

    @_repository_locked
    def activate(self, receipt: ActivationReceipt) -> None:
        handle_key = (receipt.repository, receipt.campaign_key)
        current = self.activations.get(handle_key)
        current_digest = None if current is None else current.revision_digest
        if current_digest != receipt.expected_previous_revision_digest:
            if current == receipt:
                return
            self.pending_reservations.pop(self._reservation_key(receipt), None)
            raise PlanControlError("ACTIVATION_CAS_CONFLICT", "Campaign active revision differs from the expected previous revision")
        receipt_key = (
            receipt.repository,
            receipt.campaign_key,
            receipt.revision_digest,
            receipt.planning_stable_action_id,
        )
        published = self.activation_receipts.get(receipt_key)
        if published is not None and published != receipt:
            raise PlanControlError(
                "ACTIVATION_RECEIPT_IMMUTABLE",
                "Activation Receipt identity was already published with other bytes",
            )
        self.activation_receipts[receipt_key] = receipt
        self.activations[handle_key] = receipt

    @_repository_locked
    def finalize_claims(self, receipt: ActivationReceipt) -> None:
        handle = CampaignHandle(receipt.repository, receipt.campaign_key)
        if self.read_activation(handle) != receipt:
            raise PlanControlError(
                "ACTIVATION_READBACK_INVALID",
                "Ticket claims cannot finalize before the winning Activation Receipt reads back",
            )
        reservation_key = self._reservation_key(receipt)
        reservation = self.pending_reservations.get(reservation_key)
        active_keys = {
            key
            for key, campaign_key in self._claim_campaigns.items()
            if key[0] == receipt.repository
            and campaign_key == receipt.campaign_key
        }
        expected_keys = {
            (receipt.repository, ticket_key) for ticket_key in receipt.ticket_keys
        }
        if reservation is None:
            if (
                active_keys == expected_keys
                and all(
                    self.claims[key] == receipt.revision_digest
                    for key in expected_keys
                )
            ):
                return
            raise PlanControlError(
                "TICKET_RESERVATION_MISSING",
                "Winning Activation Receipt has no exact pending Ticket reservation",
            )
        if reservation != receipt:
            raise PlanControlError(
                "TICKET_RESERVATION_CONFLICT",
                "Pending Ticket reservation does not bind the winning revision",
            )
        for key in expected_keys:
            owner = self._claim_campaigns.get(key)
            if owner is not None and owner != receipt.campaign_key:
                raise PlanControlError(
                    "TICKET_CLAIM_CONFLICT",
                    "Winning activation overlaps an independently active Campaign",
                )
            claimed_revision = self.claims.get(key)
            if (
                owner == receipt.campaign_key
                and claimed_revision
                not in {
                    receipt.expected_previous_revision_digest,
                    receipt.revision_digest,
                }
            ):
                raise PlanControlError(
                    "TICKET_CLAIM_READBACK_INVALID",
                    "Existing Campaign claim is not guarded by the expected revision",
                )

        # This in-memory transaction models the durable repository's atomic
        # post-CAS reconcile: only the read-backed winning receipt can replace
        # the old Campaign claims.
        for key in active_keys - expected_keys:
            self.claims.pop(key, None)
            self._claim_campaigns.pop(key, None)
        for key in expected_keys:
            self.claims[key] = receipt.revision_digest
            self._claim_campaigns[key] = receipt.campaign_key
        for key, pending in tuple(self.pending_reservations.items()):
            if (
                pending.repository == receipt.repository
                and pending.campaign_key == receipt.campaign_key
            ):
                self.pending_reservations.pop(key, None)

    @_repository_locked
    def read_pending_reservation(
        self,
        receipt: ActivationReceipt,
    ) -> ActivationReceipt | None:
        return self.pending_reservations.get(self._reservation_key(receipt))

    @_repository_locked
    def read_activation(self, handle: CampaignHandle) -> ActivationReceipt | None:
        return self.active_receipt(handle)

    @_repository_locked
    def read_claim_proofs(
        self,
        handle: CampaignHandle,
        revision_digest: str,
    ) -> tuple[TicketClaimProof, ...]:
        # Kept as a narrow compatibility helper for callers that only need a
        # projection.  Authority validation must use the complete Campaign
        # ledger below, never this revision-filtered view.
        return tuple(
            proof
            for proof in self.read_campaign_claim_proofs(handle)
            if proof.plan_revision_digest == revision_digest
        )

    @_repository_locked
    def read_campaign_claim_proofs(
        self,
        handle: CampaignHandle,
    ) -> tuple[TicketClaimProof, ...]:
        proofs = []
        for (repository, ticket_key), claimed_revision in self.claims.items():
            campaign_key = self._claim_campaigns[(repository, ticket_key)]
            if (
                repository != handle.repository
                or campaign_key != handle.campaign_key
            ):
                continue
            proofs.append(
                TicketClaimProof(
                    ticket_key=ticket_key,
                    repository=repository,
                    campaign_key=campaign_key,
                    plan_revision_digest=claimed_revision,
                )
            )
        return tuple(sorted(proofs, key=lambda proof: proof.ticket_key))

    @_repository_locked
    def read_invalidation_classification(
        self,
        handle: CampaignHandle,
        action_id: str,
    ) -> PlanInvalidationClassification | None:
        return self.invalidation_classifications.get(
            (handle.repository, handle.campaign_key, action_id)
        )

    @_repository_locked
    def save_invalidation_classification(
        self,
        handle: CampaignHandle,
        classification: PlanInvalidationClassification,
    ) -> PlanInvalidationClassification:
        if type(classification) is not PlanInvalidationClassification:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "classification persistence requires the exact typed result",
            )
        key = (handle.repository, handle.campaign_key, classification.action_id)
        existing = self.invalidation_classifications.get(key)
        if existing is not None and existing != classification:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_CONFLICT",
                "classification action identity is already bound to another result",
            )
        self.invalidation_classifications[key] = classification
        return classification


class PlanControl:
    """One bounded Planning Pass followed by deterministic PlanSpec activation."""

    def __init__(self, *, source: CampaignSnapshotSource, artifacts: PlanningArtifacts, gateway: CampaignPlanningGateway, repository: PlanControlRepository, max_snapshot_bytes: int = 1_048_576):
        if type(max_snapshot_bytes) is not int or max_snapshot_bytes < 1:
            raise PlanControlError("PLAN_CONTROL_COMPOSITION_INVALID", "max_snapshot_bytes must be a positive integer")
        self._source = source
        self._artifacts = artifacts
        self._gateway = gateway
        self._repository = repository
        self._max_snapshot_bytes = max_snapshot_bytes

    def start(self, repository: str, ready_refs: Sequence[str], options: object = None, *, campaign_key: str | None = None, expected_previous_revision_digest: str | None | object = _AUTO_PREVIOUS) -> CampaignHandle:
        """Create or recover one immutable PlanSpec v3 Campaign revision.

        Runtime options are deliberately absent here: host composition binds
        them in RuntimeGateway's #111 preflight, outside PlanSpec.
        """

        if options is not None:
            raise PlanControlError("START_OPTIONS_INVALID", "Runtime options belong to RuntimeGateway host composition, not PlanControl")
        repository = _text(repository, "repository")
        refs = _ready_refs(ready_refs)
        key = campaign_key or "campaign:" + digest_value({"repository": repository, "ready_refs": list(refs)})[:24]
        handle = CampaignHandle(repository, _text(key, "campaign_key"))
        active = self._repository.active_receipt(handle)
        if expected_previous_revision_digest is _AUTO_PREVIOUS:
            expected: str | None | object = _AUTO_PREVIOUS
        else:
            expected = _optional_digest(
                expected_previous_revision_digest,
                "expected_previous_revision_digest",
            )
            # A successor is a recovery of one exact Campaign lineage, not a
            # second start spelling.  Reject a nonexistent, stale, or foreign
            # handle before source capture, preflight, Planning, publication,
            # claims, or even unrelated roll-forward mutation.
            if active is None:
                raise PlanControlError(
                    "ACTIVATION_CAS_CONFLICT",
                    "Successor request names a Campaign with no active Plan Revision",
                )
            if active.revision_digest != expected:
                replay = self._repository.read_attempt(handle, expected)
                if (
                    type(replay) is not _PlanningAttempt
                    or replay.revision is None
                    or active.expected_previous_revision_digest != expected
                    or replay.revision.digest != active.revision_digest
                ):
                    raise PlanControlError(
                        "ACTIVATION_CAS_CONFLICT",
                        "Successor request names a stale previous Plan Revision digest",
                    )
            successor_attempt = self._repository.read_attempt(handle, expected)
            if (
                type(successor_attempt) is _PlanningAttempt
                and successor_attempt.planning_protocol_id
                == REPLANNING_OUTPUT_PROTOCOL_ID
            ):
                # A tagged successor attempt is already the completed
                # one-pass continuation.  Dispatch it through the same
                # no-Gateway activation seam instead of treating its
                # ``policy_witness`` snapshot as an initial ``policy``
                # snapshot.
                self._verify_attempt_artifacts(successor_attempt)
                self._read_successor_compilation_record(successor_attempt)
                successor_classification = (
                    self._read_durable_successor_classification(
                        handle,
                        successor_attempt,
                    )
                )
                self.activate_successor(handle, successor_classification)
                return handle
        if active is not None:
            # This is deliberately before every claim-finalization path.  A
            # topologically plausible forged receipt must never seize Ticket
            # claims merely because it happens to be current.
            self._validate_active_receipt(
                handle,
                receipt=active,
                require_claims=False,
            )
            self._repository.finalize_claims(active)
        if expected_previous_revision_digest is _AUTO_PREVIOUS:
            if active is not None and active.ready_refs == refs:
                attempt = self._repository.read_attempt(
                    handle,
                    active.expected_previous_revision_digest,
                )
                if (
                    attempt is None
                    or attempt.ready_refs != active.ready_refs
                    or attempt.ticket_keys != active.ticket_keys
                    or attempt.compilation_record_artifact_digest is None
                ):
                    raise PlanControlError(
                        "ACTIVE_PLAN_CROSS_BINDING_INVALID",
                        "Active Campaign has no exact Planning attempt binding",
                    )
                self._verify_attempt_artifacts(attempt)
                preflight = self._gateway.planning_preflight(attempt.subject)
                _validate_preflight(preflight, attempt.subject)
                self._read_compilation_record(attempt)
                record = _read_artifact_json(
                    self._artifacts,
                    attempt.compilation_record_artifact_digest,
                    code="COMPILATION_RECORD_INVALID",
                )
                if record["preflight_receipt"] != {
                    "subject_digest": preflight.subject_digest,
                    "stable_action_id": preflight.stable_action_id,
                    "receipt_digest": preflight.receipt_digest,
                }:
                    raise PlanControlError(
                        "RUNTIME_PREFLIGHT_INVALID",
                        "Active Campaign preflight differs from its exact compiled binding",
                    )
                self.read_active(handle)
                return handle
            expected = None if active is None else active.revision_digest
        else:
            assert expected is not _AUTO_PREVIOUS

        split_decision = self._repository.read_split_decision(handle, expected)
        if split_decision is not None:
            self._raise_split_decision(split_decision, handle, refs, expected)

        attempt = self._repository.read_attempt(handle, expected)
        if attempt is None:
            attempt = self._new_attempt(handle, refs, expected)
            attempt = self._repository.save_attempt(attempt)
        elif attempt.ready_refs != refs:
            raise PlanControlError("PLANNING_ATTEMPT_IDENTITY_CONFLICT", "Campaign attempt has different selected Tickets")

        self._verify_attempt_artifacts(attempt)

        if attempt.compilation_record_artifact_digest is None:
            attempt = self._obtain_one_planning_intent(attempt)
            # A live semantic turn remains pending; its stable handle gives
            # #110 a durable continuation point without inventing a revision.
            if attempt.compilation_record_artifact_digest is None:
                return handle

        intent_bytes = self._read_compilation_record(attempt)
        intent = _intent_from_bytes(intent_bytes, attempt.snapshot_bytes)
        findings = tuple(DecisionFinding(**item) for item in intent["decision_requirements"])
        if findings:
            if attempt.revision is not None:
                raise PlanControlError(
                    "PLAN_REVISION_PROVENANCE_INVALID",
                    "Decision-only Planning output cannot have a persisted Plan Revision",
                )
            self._repository.release_planning(
                self._planning_reservation_from_compilation(attempt)
            )
            raise PlanControlDecision(attempt.snapshot_artifact_digest, findings)

        expected_revision = (
            self._compile_successor_revision(handle, attempt, intent_bytes)
            if attempt.planning_protocol_id == REPLANNING_OUTPUT_PROTOCOL_ID
            else _compile_plan(
                attempt.snapshot_bytes,
                attempt.snapshot_artifact_digest,
                intent_bytes,
                handle,
            )
        )
        if attempt.revision is not None:
            _validate_revision_provenance(
                attempt.revision,
                expected_revision,
            )
        revision = expected_revision
        if attempt.revision is None:
            attempt = self._repository.save_attempt(replace(attempt, revision=revision))

        planning_reservation = self._planning_reservation_from_compilation(
            attempt
        )
        compilation = self._compilation_authority(attempt)
        receipt = ActivationReceipt(
            repository=handle.repository,
            campaign_key=handle.campaign_key,
            revision_digest=revision.digest,
            expected_previous_revision_digest=expected,
            writer_generation=self._writer_generation(),
            ready_refs=refs,
            ticket_keys=attempt.ticket_keys,
            planning_subject_digest=planning_reservation.subject_digest,
            planning_stable_action_id=planning_reservation.stable_action_id,
            planning_preflight_receipt_digest=(
                planning_reservation.preflight_receipt_digest
            ),
            compilation_record_artifact_digest=(
                attempt.compilation_record_artifact_digest
            ),
            planning_receipt_digest=compilation["planning_receipt"][
                "receipt_digest"
            ],
            planning_output_artifact_digest=compilation[
                "planning_receipt"
            ]["planning_output_artifact_digest"],
        )

        self._publish_activate_readback(
            handle=handle,
            attempt=attempt,
            revision=revision,
            receipt=receipt,
        )
        return handle

    def read_active(self, handle: CampaignHandle) -> ActivePlanReadback:
        """Read one active revision without mutable-source, claim, or Runtime work.

        ExecutionKernel consumes this internal port before it admits Work Runs.
        It deliberately has no transition or recovery behavior.
        """

        return self._validate_active_receipt(handle, require_claims=True)

    def activate_successor(
        self,
        handle: CampaignHandle,
        classification: PlanInvalidationClassification,
    ) -> ActivePlanReadback:
        """Continue one durably completed ``replan:`` action without Gateway work.

        Classification owns the only semantic Planning Pass.  This boundary
        consumes only its exact repository readback and the frozen successor
        compilation record; it never asks RuntimeGateway to preflight or
        progress the action again.
        """

        if (
            type(handle) is not CampaignHandle
            or type(classification) is not PlanInvalidationClassification
            or classification.disposition
            is not PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR
        ):
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "successor activation requires one exact approved classification",
            )
        durable = self._repository.read_invalidation_classification(
            handle,
            classification.action_id,
        )
        if durable != classification:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                "successor classification did not read back exactly",
            )
        attempt = self._repository.read_attempt(
            handle,
            classification.plan_revision_digest,
        )
        if type(attempt) is not _PlanningAttempt:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                "successor classification has no exact Planning attempt",
            )
        self._verify_attempt_artifacts(attempt)
        intent_bytes = self._read_successor_compilation_record(attempt)
        self._validate_successor_classification_readback(
            handle,
            attempt,
            classification,
        )
        current = self._repository.active_receipt(handle)
        if current is None:
            raise PlanControlError(
                "ACTIVATION_CAS_CONFLICT",
                "successor predecessor is no longer active",
            )
        if current.revision_digest != classification.plan_revision_digest:
            return self._read_exact_successor_replay(
                handle=handle,
                attempt=attempt,
                classification=classification,
                active=current,
            )

        predecessor = self._validate_active_receipt(
            handle,
            receipt=current,
            require_claims=False,
        )
        self._validate_successor_attempt_identity(
            handle,
            attempt,
            predecessor.activation_receipt,
            classification,
        )
        self._validate_fresh_successor_source(handle, attempt)

        revision = self._compile_successor_revision(
            handle,
            attempt,
            intent_bytes,
        )
        if attempt.revision is not None:
            _validate_revision_provenance(attempt.revision, revision)
            revision = attempt.revision
        else:
            attempt = self._repository.save_attempt(
                replace(attempt, revision=revision)
            )
            if self._repository.read_attempt(
                handle,
                classification.plan_revision_digest,
            ) != attempt:
                raise PlanControlError(
                    "PLAN_READBACK_INVALID",
                    "Successor Planning attempt did not read back exactly",
                )
        receipt = self._successor_activation_receipt(attempt, revision)
        return self._publish_activate_readback(
            handle=handle,
            attempt=attempt,
            revision=revision,
            receipt=receipt,
        )

    def _read_durable_successor_classification(
        self,
        handle: CampaignHandle,
        attempt: _PlanningAttempt,
    ) -> PlanInvalidationClassification:
        record = self._compilation_authority(attempt)
        try:
            embedded = PlanInvalidationClassification.from_canonical(
                record["classification"]
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PlanControlError(
                "COMPILATION_RECORD_INVALID",
                "Successor compilation record omitted its exact classification",
            ) from error
        durable = self._repository.read_invalidation_classification(
            handle,
            embedded.action_id,
        )
        if durable != embedded:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                "Successor compilation classification does not match durable classification",
            )
        return durable

    def _validate_successor_classification_readback(
        self,
        handle: CampaignHandle,
        attempt: _PlanningAttempt,
        classification: PlanInvalidationClassification,
    ) -> None:
        embedded = self._read_durable_successor_classification(handle, attempt)
        if embedded != classification:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                "Successor classification does not match its immutable compilation record",
            )

    def _validate_successor_attempt_identity(
        self,
        handle: CampaignHandle,
        attempt: _PlanningAttempt,
        predecessor: ActivationReceipt,
        classification: PlanInvalidationClassification,
    ) -> None:
        if (
            attempt.handle != handle
            or attempt.planning_protocol_id != REPLANNING_OUTPUT_PROTOCOL_ID
            or attempt.expected_previous_revision_digest
            != classification.plan_revision_digest
            or attempt.ready_refs != predecessor.ready_refs
            or attempt.ticket_keys != predecessor.ticket_keys
            or attempt.subject.stable_action_id != classification.action_id
        ):
            raise PlanControlError(
                "PLANNING_ATTEMPT_IDENTITY_CONFLICT",
                "Successor Planning attempt does not bind its active predecessor",
            )

    def _validate_fresh_successor_source(
        self,
        handle: CampaignHandle,
        attempt: _PlanningAttempt,
    ) -> None:
        try:
            _derive_successor, _compile_successor, validate_fresh = (
                _successor_plan_api()
            )
            fresh = _normalize_snapshot(
                self._source.snapshot(handle.repository, attempt.ready_refs),
                handle.repository,
                attempt.ready_refs,
                allow_open_external_blockers=True,
            )
            validate_fresh(
                _snapshot_from_bytes(attempt.snapshot_bytes),
                fresh,
            )
        except PlanControlError:
            raise
        except Exception as error:
            raise PlanControlError(
                getattr(error, "code", "REPLAN_SOURCE_CHANGED"),
                getattr(error, "detail", "authoritative successor source changed"),
            ) from error

    def _read_exact_successor_replay(
        self,
        *,
        handle: CampaignHandle,
        attempt: _PlanningAttempt,
        classification: PlanInvalidationClassification,
        active: ActivationReceipt,
    ) -> ActivePlanReadback:
        if attempt.revision is None:
            raise PlanControlError(
                "ACTIVATION_CAS_CONFLICT",
                "active revision changed before this successor was durably compiled",
            )
        expected = self._successor_activation_receipt(attempt, attempt.revision)
        if (
            active != expected
            or active.expected_previous_revision_digest
            != classification.plan_revision_digest
            or active.planning_stable_action_id != classification.action_id
        ):
            raise PlanControlError(
                "ACTIVATION_CAS_CONFLICT",
                "another successor owns the active Campaign revision",
            )
        self._validate_active_receipt(
            handle,
            receipt=active,
            require_claims=False,
        )
        self._repository.finalize_claims(active)
        return self.read_active(handle)

    def _compile_successor_revision(
        self,
        handle: CampaignHandle,
        attempt: _PlanningAttempt,
        intent_bytes: bytes,
    ) -> PlanRevision:
        try:
            intent = load_canonical_json(intent_bytes)
            if canonical_bytes(intent) != intent_bytes:
                raise PlanControlError(
                    "PLAN_INTENT_READBACK_INVALID",
                    "Successor normalized intent is not canonical",
                )
            _derive, compile_successor, _validate_source = _successor_plan_api()
            snapshot = _snapshot_from_bytes(attempt.snapshot_bytes)
            plan = compile_successor(snapshot, intent)
            payload = canonical_bytes(plan)
            _validate_plan_spec(payload)
        except PlanControlError:
            raise
        except Exception as error:
            raise PlanControlError(
                getattr(error, "code", "SUCCESSOR_PLAN_INVALID"),
                getattr(error, "detail", "successor PlanSpec compilation failed"),
            ) from error
        return PlanRevision(
            repository=handle.repository,
            campaign_key=handle.campaign_key,
            snapshot_digest=attempt.snapshot_artifact_digest,
            canonical_bytes=payload,
            digest=digest_bytes(payload),
        )

    def _successor_activation_receipt(
        self,
        attempt: _PlanningAttempt,
        revision: PlanRevision,
    ) -> ActivationReceipt:
        record = self._compilation_authority(attempt)
        return ActivationReceipt(
            repository=attempt.handle.repository,
            campaign_key=attempt.handle.campaign_key,
            revision_digest=revision.digest,
            expected_previous_revision_digest=attempt.expected_previous_revision_digest,
            writer_generation=self._writer_generation(),
            ready_refs=attempt.ready_refs,
            ticket_keys=attempt.ticket_keys,
            planning_subject_digest=attempt.subject.digest,
            planning_stable_action_id=attempt.subject.stable_action_id,
            planning_preflight_receipt_digest=record["preflight_receipt"][
                "receipt_digest"
            ],
            compilation_record_artifact_digest=(
                attempt.compilation_record_artifact_digest
            ),
            planning_receipt_digest=record["planning_receipt"]["receipt_digest"],
            planning_output_artifact_digest=record["planning_receipt"][
                "planning_output_artifact_digest"
            ],
        )

    def _publish_activate_readback(
        self,
        *,
        handle: CampaignHandle,
        attempt: _PlanningAttempt,
        revision: PlanRevision,
        receipt: ActivationReceipt,
    ) -> ActivePlanReadback:
        """Run the one publication/CAS/claim-readback sequence for every revision."""

        current = self._repository.active_receipt(handle)
        if current == receipt:
            self._validate_active_receipt(
                handle,
                receipt=current,
                require_claims=False,
            )
            self._repository.finalize_claims(current)
            return self.read_active(handle)
        pending_reservation = self._repository.read_pending_reservation(receipt)
        if pending_reservation != receipt:
            planning_reservation = self._repository.read_planning_reservation(
                handle,
                attempt.subject.stable_action_id,
            )
            if (
                planning_reservation is None
                or planning_reservation.subject_digest != attempt.subject.digest
                or planning_reservation.ticket_keys != attempt.ticket_keys
                or planning_reservation.preflight_receipt_digest
                != receipt.planning_preflight_receipt_digest
                or (
                    attempt.planning_protocol_id == REPLANNING_OUTPUT_PROTOCOL_ID
                    and (
                        planning_reservation.snapshot_artifact_digest
                        != attempt.snapshot_artifact_digest
                        or planning_reservation.policy_witness_digest
                        != attempt.policy_witness_digest
                        or planning_reservation.planning_request_artifact_digest
                        != attempt.planning_request_artifact_digest
                    )
                )
            ):
                raise PlanControlError(
                    "PLANNING_RESERVATION_MISSING",
                    "Plan activation lacks its exact retained Planning reservation",
                )
        plan_artifact_digest = _put_canonical(
            self._artifacts,
            revision.plan_spec,
        )
        if (
            plan_artifact_digest != revision.digest
            or _read_artifact_json(
                self._artifacts,
                revision.digest,
                code="PLAN_READBACK_INVALID",
            )
            != revision.plan_spec
        ):
            raise PlanControlError(
                "PLAN_READBACK_INVALID",
                "PlanSpec Artifact does not read back exactly",
            )
        self._repository.reserve_claims(receipt)
        self._repository.publish_revision(revision)
        if self._repository.read_revision(revision.digest) != revision:
            raise PlanControlError(
                "PLAN_READBACK_INVALID",
                "Published Plan Revision does not read back exactly",
            )
        self._repository.activate(receipt)
        if self._repository.read_activation(handle) != receipt:
            raise PlanControlError(
                "ACTIVATION_READBACK_INVALID",
                "Activation Receipt does not read back exactly",
            )
        self._validate_active_receipt(
            handle,
            receipt=receipt,
            require_claims=False,
        )
        self._repository.finalize_claims(receipt)
        return self.read_active(handle)

    def classify_plan_invalidations(
        self,
        handle: CampaignHandle,
        invalidations: Sequence[object],
        execution_snapshot: Mapping[str, Any],
    ) -> PlanInvalidationClassification | None:
        """Run one bounded, read-only Coordinator classification.

        The method deliberately does not publish, activate, or mutate a
        successor Plan Revision.  Its only durable semantic result is the
        typed classification bound to the active revision and the complete
        pending Evidence set.  ExecutionKernel owns the later disposition
        transition and reads this result back before resuming a Work Run.
        """

        if type(handle) is not CampaignHandle:
            raise PlanControlError(
                "PLAN_INVALIDATION_HANDLE_INVALID",
                "invalidation classification requires an exact CampaignHandle",
            )
        active = self.read_active(handle)
        plan = load_canonical_json(active.plan_spec_bytes)
        if type(plan) is not dict:
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "active PlanSpec is not an object",
            )
        pending = _normalize_invalidation_observations(
            invalidations,
            handle=handle,
            active_revision_digest=active.current_revision_digest,
            plan=plan,
            execution_snapshot=execution_snapshot,
        )
        evidence_digests = tuple(
            sorted({item["evidence_digest"] for item in pending})
        )
        action_id = "replan:" + digest_value(
            {
                "repository": handle.repository,
                "campaign_key": handle.campaign_key,
                "plan_revision_digest": active.current_revision_digest,
                "evidence_digests": list(evidence_digests),
            }
        )
        snapshot = _build_replanning_snapshot(
            self,
            active=active,
            plan=plan,
            pending=pending,
            execution_snapshot=execution_snapshot,
        )
        snapshot_bytes = canonical_bytes(snapshot)
        if len(snapshot_bytes) > self._max_snapshot_bytes:
            finding = DecisionFinding(
                code="REPLAN_SNAPSHOT_TOO_LARGE",
                detail=(
                    "The complete bounded Campaign invalidation snapshot exceeds "
                    f"the configured Planning input bound of {self._max_snapshot_bytes} bytes"
                ),
            )
            raise PlanControlDecision(digest_bytes(snapshot_bytes), (finding,))
        snapshot_digest = _put_canonical(self._artifacts, snapshot)
        if snapshot_digest != digest_bytes(snapshot_bytes):
            raise PlanControlError(
                "SNAPSHOT_ARTIFACT_MISMATCH",
                "Campaign invalidation snapshot Artifact digest changed",
            )
        policy_digest = snapshot["policy_witness"]["digest"]
        stable_subject = CampaignPlanningSubject(
            repository=handle.repository,
            campaign_key=handle.campaign_key,
            campaign_handle=_handle_ref(handle),
            expected_previous_plan_revision_digest=active.current_revision_digest,
            snapshot_artifact_digest=snapshot_digest,
            policy_witness_digest=policy_digest,
            planning_request_artifact_digest="0" * 64,
            stable_action_id=action_id,
        )
        request_ref = _put_canonical(
            self._artifacts,
            replanning_prompt(
                subject_digest=stable_subject.prompt_binding_digest,
                authority_digest=policy_digest,
                snapshot_artifact_digest=snapshot_digest,
                policy_witness_artifact_digest=policy_digest,
            ),
        )
        subject = replace(
            stable_subject,
            planning_request_artifact_digest=request_ref,
        )

        # A pending Coordinator action owns one immutable bounded snapshot.
        # Unrelated Work Runs may advance while Runtime progress is parked, so
        # replay must recover the original Artifact identities from the
        # durable Planning reservation rather than rebuilding the subject from
        # a newer ExecutionKernel view.
        existing_reservation = self._repository.read_planning_reservation(
            handle,
            subject.stable_action_id,
        )
        if existing_reservation is not None:
            if (
                existing_reservation.snapshot_artifact_digest is None
                or existing_reservation.policy_witness_digest is None
                or existing_reservation.planning_request_artifact_digest is None
            ):
                raise PlanControlError(
                    "PLANNING_RESERVATION_CONFLICT",
                    "invalidation Planning reservation omitted its immutable input Artifacts",
                )
            snapshot_digest = existing_reservation.snapshot_artifact_digest
            policy_digest = existing_reservation.policy_witness_digest
            request_ref = existing_reservation.planning_request_artifact_digest
            snapshot = _read_artifact_json(
                self._artifacts,
                snapshot_digest,
                code="PLAN_INVALIDATION_SNAPSHOT_INVALID",
            )
            snapshot_bytes = canonical_bytes(snapshot)
            if digest_bytes(snapshot_bytes) != snapshot_digest:
                raise PlanControlError(
                    "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                    "invalidation Planning reservation snapshot bytes changed",
                )
            stored_request = _read_artifact_json(
                self._artifacts,
                request_ref,
                code="PLANNING_REQUEST_INVALID",
            )
            expected_request_subject = CampaignPlanningSubject(
                repository=handle.repository,
                campaign_key=handle.campaign_key,
                campaign_handle=_handle_ref(handle),
                expected_previous_plan_revision_digest=active.current_revision_digest,
                snapshot_artifact_digest=snapshot_digest,
                policy_witness_digest=policy_digest,
                planning_request_artifact_digest="0" * 64,
                stable_action_id=action_id,
            )
            if stored_request != replanning_prompt(
                subject_digest=expected_request_subject.prompt_binding_digest,
                authority_digest=policy_digest,
                snapshot_artifact_digest=snapshot_digest,
                policy_witness_artifact_digest=policy_digest,
            ):
                raise PlanControlError(
                    "PLANNING_REQUEST_INVALID",
                    "invalidation Planning request Artifact changed its exact binding",
                )
            if (
                type(snapshot) is not dict
                or snapshot.get("policy_witness", {}).get("digest") != policy_digest
                or snapshot.get("active_plan_revision", {}).get("digest")
                != active.current_revision_digest
                or tuple(
                    sorted(
                        {
                            item.get("evidence_digest")
                            for item in snapshot.get("pending_invalidations", [])
                            if type(item) is dict
                        }
                    )
                )
                != evidence_digests
            ):
                raise PlanControlError(
                    "PLANNING_RESERVATION_CONFLICT",
                    "invalidation Planning reservation changed its bounded Evidence snapshot",
                )
            stable_subject = expected_request_subject
            subject = replace(
                stable_subject,
                planning_request_artifact_digest=request_ref,
            )

        read_result = getattr(
            self._repository,
            "read_invalidation_classification",
            None,
        )
        saver = getattr(
            self._repository,
            "save_invalidation_classification",
            None,
        )
        if callable(read_result):
            existing = read_result(handle, action_id)
            if existing is not None:
                stored_snapshot = _read_artifact_json(
                    self._artifacts,
                    existing.snapshot_digest,
                    code="PLAN_INVALIDATION_SNAPSHOT_INVALID",
                )
                if (
                    type(stored_snapshot) is not dict
                    or stored_snapshot.get("active_plan_revision", {}).get("digest")
                    != active.current_revision_digest
                    or tuple(
                        sorted(
                            {
                                item.get("evidence_digest")
                                for item in stored_snapshot.get(
                                    "pending_invalidations", []
                                )
                                if type(item) is dict
                            }
                        )
                    )
                    != evidence_digests
                ):
                    raise PlanControlError(
                        "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                        "persisted invalidation classification snapshot is stale or incomplete",
                    )
                _validate_classification_binding(
                    existing,
                    action_id=action_id,
                    snapshot_digest=existing.snapshot_digest,
                    plan_revision_digest=active.current_revision_digest,
                    evidence_digests=evidence_digests,
                    snapshot=stored_snapshot,
                )
                if existing.disposition is PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR:
                    persisted_attempt = self._repository.read_attempt(
                        handle,
                        existing.plan_revision_digest,
                    )
                    if (
                        type(persisted_attempt) is not _PlanningAttempt
                        or persisted_attempt.planning_protocol_id
                        != REPLANNING_OUTPUT_PROTOCOL_ID
                        or persisted_attempt.compilation_record_artifact_digest
                        is None
                    ):
                        raise PlanControlError(
                            "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                            "successor classification has no exact durable Planning attempt",
                        )
                    self._read_successor_compilation_record(persisted_attempt)
                    retained = self._repository.read_planning_reservation(
                        handle,
                        existing.action_id,
                    )
                    if retained is None:
                        raise PlanControlError(
                            "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                            "successor classification lost its retained Planning reservation",
                        )
                return existing

        reservation = self._replanning_reservation(
            subject,
            active,
            snapshot_artifact_digest=snapshot_digest,
            policy_witness_digest=policy_digest,
            planning_request_artifact_digest=request_ref,
        )
        if existing_reservation is not None:
            expected_reservation = replace(
                reservation,
                preflight_receipt_digest=existing_reservation.preflight_receipt_digest,
            )
            if existing_reservation != expected_reservation:
                raise PlanControlError(
                    "PLANNING_RESERVATION_CONFLICT",
                    "invalidation Planning reservation changed its exact subject",
                )
            preflight = PlanningPreflightReceipt(
                subject_digest=existing_reservation.subject_digest,
                stable_action_id=existing_reservation.stable_action_id,
                receipt_digest=existing_reservation.preflight_receipt_digest,
            )
            reservation = existing_reservation
        else:
            preflight = self._gateway.planning_preflight(subject)
            _validate_preflight(preflight, subject)
            reservation = replace(
                reservation,
                preflight_receipt_digest=preflight.receipt_digest,
            )
            self._repository.reserve_planning(reservation)
        _validate_preflight(preflight, subject)

        # A crash after the successor attempt is durably committed but before
        # its classification is saved must resume from that exact one-pass
        # record.  Re-entering RuntimeGateway here would create a second
        # semantic Planning Pass and could produce a different classification
        # for the same stable action.
        persisted_attempt = self._repository.read_attempt(
            handle,
            active.current_revision_digest,
        )
        if (
            type(persisted_attempt) is _PlanningAttempt
            and persisted_attempt.planning_protocol_id
            == REPLANNING_OUTPUT_PROTOCOL_ID
        ):
            if existing_reservation is None:
                raise PlanControlError(
                    "PLANNING_RESERVATION_MISSING",
                    "Successor Planning attempt has no retained Planning reservation",
                )
            record = self._compilation_authority(persisted_attempt)
            recovered = PlanInvalidationClassification.from_canonical(
                record["classification"]
            )
            _validate_classification_binding(
                recovered,
                action_id=action_id,
                snapshot_digest=snapshot_digest,
                plan_revision_digest=active.current_revision_digest,
                evidence_digests=evidence_digests,
                snapshot=snapshot,
            )
            retained = self._repository.read_planning_reservation(
                handle,
                recovered.action_id,
            )
            if retained != existing_reservation:
                raise PlanControlError(
                    "PLANNING_RESERVATION_READBACK_INVALID",
                    "Successor Planning reservation changed during recovery",
                )
            if callable(saver):
                saver(handle, recovered)
                readback = (
                    read_result(handle, recovered.action_id)
                    if callable(read_result)
                    else recovered
                )
                if readback != recovered:
                    raise PlanControlError(
                        "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                        "recovered successor classification did not read back exactly",
                    )
            return recovered

        proof_reader = getattr(self._gateway, "_read_coordinator_capability", None)
        if not callable(proof_reader):
            # Keep compatibility with narrow test/host doubles that exposed
            # the pre-release seam before it was made private on the public
            # RuntimeGateway surface.
            proof_reader = getattr(self._gateway, "read_coordinator_capability", None)
        if not callable(proof_reader):
            raise PlanControlError(
                "REPLAN_CAPABILITY_PROOF_UNAVAILABLE",
                "RuntimeGateway omitted Coordinator capability readback",
            )
        proof = proof_reader(subject)
        _validate_coordinator_capability(proof, subject)
        receipt = self._gateway.progress(subject, preflight)
        _validate_planning_receipt(receipt, subject)
        if receipt.status != "completed":
            return None
        output_digest = receipt.planning_output_artifact_digest
        assert output_digest is not None
        payload = _planning_payload(self._artifacts, output_digest, subject)
        classification = _normalize_replanning_intent(
            payload,
            snapshot=snapshot,
            action_id=action_id,
            snapshot_digest=snapshot_digest,
            plan_revision_digest=active.current_revision_digest,
            evidence_digests=evidence_digests,
            capability_proof_digest=proof.digest,
        )
        successor_attempt = None
        if classification.disposition is PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR:
            successor_attempt = self._save_completed_successor_attempt(
                handle=handle,
                active=active,
                snapshot=snapshot,
                snapshot_bytes=snapshot_bytes,
                snapshot_digest=snapshot_digest,
                subject=subject,
                preflight=preflight,
                receipt=receipt,
                proof=proof,
                classification=classification,
                reservation=reservation,
                output_digest=output_digest,
            )
        if callable(saver):
            saver(handle, classification)
            readback = read_result(handle, action_id) if callable(read_result) else classification
            if readback != classification:
                raise PlanControlError(
                    "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
                    "classification did not read back exactly",
                )
        if classification.disposition is not PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR:
            self._repository.release_planning(reservation)
        elif successor_attempt is None:
            raise PlanControlError(
                "COMPILATION_RECORD_INVALID",
                "successor classification was not bound to a completed attempt",
            )
        return classification

    def _save_completed_successor_attempt(
        self,
        *,
        handle: CampaignHandle,
        active: ActivePlanReadback,
        snapshot: Mapping[str, Any],
        snapshot_bytes: bytes,
        snapshot_digest: str,
        subject: CampaignPlanningSubject,
        preflight: PlanningPreflightReceipt,
        receipt: PlanningReceipt,
        proof: CoordinatorCapabilityProof,
        classification: PlanInvalidationClassification,
        reservation: PlanningReservation,
        output_digest: str,
    ) -> _PlanningAttempt:
        """Persist the completed replan pass before its classification."""

        if receipt.status != "completed":
            raise PlanControlError(
                "RUNTIME_PLANNING_RECEIPT_INVALID",
                "successor attempt requires a completed Planning Receipt",
            )
        try:
            derive_successor, _compile_successor, _validate_source = _successor_plan_api()
            normalized_intent = derive_successor(
                snapshot,
                classification.canonical(),
            )
        except PlanControlError:
            raise
        except Exception as error:
            raise PlanControlError(
                getattr(error, "code", "SUCCESSOR_PLAN_INVALID"),
                getattr(error, "detail", "successor intent derivation failed"),
            ) from error
        output_value = _read_artifact_json(
            self._artifacts,
            output_digest,
            code="RUNTIME_PLANNING_OUTPUT_INVALID",
        )
        record = {
            "schema_version": "gwo.plan.successor-compilation.v1",
            "subject": subject.canonical(),
            "subject_digest": subject.digest,
            "snapshot_artifact_digest": snapshot_digest,
            "policy_witness_digest": subject.policy_witness_digest,
            "planning_request_artifact_digest": subject.planning_request_artifact_digest,
            "stable_action_id": subject.stable_action_id,
            "preflight_receipt": {
                "subject_digest": preflight.subject_digest,
                "stable_action_id": preflight.stable_action_id,
                "receipt_digest": preflight.receipt_digest,
            },
            "planning_receipt": {
                "subject_digest": receipt.subject_digest,
                "stable_action_id": receipt.stable_action_id,
                "status": receipt.status,
                "receipt_digest": receipt.receipt_digest,
                "command": None,
                "wake_cursor": receipt.wake_cursor,
                "wake_hints": list(receipt.wake_hints),
                "output_artifact_digest": receipt.output_artifact_digest,
                "planning_output_artifact_digest": receipt.planning_output_artifact_digest,
            },
            "output_artifact_digest": output_digest,
            "planning_output": output_value,
            "coordinator_capability_proof": proof.canonical(),
            "coordinator_capability_proof_digest": proof.digest,
            "classification": classification.canonical(),
            "classification_digest": classification.digest,
            "normalized_intent": normalized_intent,
            "normalized_intent_digest": digest_value(normalized_intent),
        }
        record_bytes = canonical_bytes(record)
        record_digest = _put_canonical(self._artifacts, record)
        if record_digest != digest_bytes(record_bytes):
            raise PlanControlError(
                "COMPILATION_RECORD_INVALID",
                "Successor compilation record Artifact changed its canonical bytes",
            )
        attempt = _PlanningAttempt(
            handle=handle,
            ready_refs=active.activation_receipt.ready_refs,
            ticket_keys=active.activation_receipt.ticket_keys,
            expected_previous_revision_digest=active.current_revision_digest,
            snapshot_bytes=snapshot_bytes,
            snapshot_artifact_digest=snapshot_digest,
            policy_witness_digest=subject.policy_witness_digest,
            planning_request_artifact_digest=subject.planning_request_artifact_digest,
            subject=subject,
            planning_protocol_id=REPLANNING_OUTPUT_PROTOCOL_ID,
            compilation_record_artifact_digest=record_digest,
            compilation_record_bytes=record_bytes,
        )
        saved = self._repository.save_attempt(attempt)
        readback = self._repository.read_attempt(
            handle,
            active.current_revision_digest,
        )
        if readback != saved:
            raise PlanControlError(
                "PLANNING_ATTEMPT_READBACK_INVALID",
                "Successor Planning attempt did not read back exactly",
            )
        self._read_successor_compilation_record(readback)
        retained = self._repository.read_planning_reservation(
            handle,
            subject.stable_action_id,
        )
        if retained != reservation:
            raise PlanControlError(
                "PLANNING_RESERVATION_READBACK_INVALID",
                "Successor Planning reservation did not read back exactly",
            )
        return readback

    def _replanning_reservation(
        self,
        subject: CampaignPlanningSubject,
        active: ActivePlanReadback,
        *,
        snapshot_artifact_digest: str | None = None,
        policy_witness_digest: str | None = None,
        planning_request_artifact_digest: str | None = None,
    ) -> PlanningReservation:
        return PlanningReservation(
            repository=subject.repository,
            campaign_key=subject.campaign_key,
            ticket_keys=tuple(active.activation_receipt.ticket_keys),
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            preflight_receipt_digest="0" * 64,
            snapshot_artifact_digest=snapshot_artifact_digest,
            policy_witness_digest=policy_witness_digest,
            planning_request_artifact_digest=planning_request_artifact_digest,
        )

    def _validate_active_receipt(
        self,
        handle: CampaignHandle,
        *,
        receipt: ActivationReceipt | None = None,
        require_claims: bool,
        _allow_hydration_retry: bool = True,
    ) -> ActivePlanReadback:
        """Validate every active binding before exposing or mutating it.

        The same closed reconstruction guards ordinary active reads, replay,
        crash roll-forward, and the claim-finalization precondition.  Keeping
        that ordering here prevents a forged current pointer from taking a
        claim before later readback happens to reject it.
        """

        if type(handle) is not CampaignHandle:
            raise PlanControlError("ACTIVE_READBACK_INVALID", "active readback requires an exact CampaignHandle")
        observed = self._repository.read_activation(handle)
        if receipt is None:
            receipt = observed
        elif observed != receipt:
            raise PlanControlError(
                "ACTIVE_PLAN_CROSS_BINDING_INVALID",
                "Activation Receipt changed before its complete cross-binding validation",
            )
        if receipt is None:
            raise PlanControlError("ACTIVATION_PENDING", "Campaign has no read-backed Activation Receipt")
        try:
            return self._validate_active_receipt_once(
                handle,
                receipt,
                require_claims=require_claims,
            )
        except PlanControlError as error:
            refresher = getattr(self._repository, "hydrate_active_artifacts", None)
            artifact_codes = {
                "SNAPSHOT_READBACK_INVALID",
                "POLICY_WITNESS_INVALID",
                "PLANNING_REQUEST_INVALID",
                "COMPILATION_RECORD_INVALID",
                "RUNTIME_PLANNING_OUTPUT_INVALID",
                "PLAN_READBACK_INVALID",
            }
            if (
                not _allow_hydration_retry
                or error.code not in artifact_codes
                or not callable(refresher)
            ):
                raise
            # A production GitHub repository refreshes from one exact control
            # ref and proves the same active identity before this sole retry.
            refresher(self._artifacts, handle, receipt)
            return self._validate_active_receipt(
                handle,
                receipt=receipt,
                require_claims=require_claims,
                _allow_hydration_retry=False,
            )

    def _validate_active_receipt_once(
        self,
        handle: CampaignHandle,
        receipt: ActivationReceipt,
        *,
        require_claims: bool,
    ) -> ActivePlanReadback:
        envelope = self._active_authority_envelope(handle, receipt)
        proofs_match = (
            tuple(proof.ticket_key for proof in envelope.claim_proofs)
            == receipt.ticket_keys
            and all(
                proof.repository == handle.repository
                and proof.campaign_key == handle.campaign_key
                and proof.plan_revision_digest == receipt.revision_digest
                for proof in envelope.claim_proofs
            )
        )
        if require_claims:
            if not proofs_match:
                raise PlanControlError(
                    "ACTIVE_PLAN_CROSS_BINDING_INVALID",
                    "Activated Ticket claims do not exactly bind the active Campaign and Plan Revision",
                )
        else:
            pending = self._repository.read_pending_reservation(receipt)
            if not proofs_match and pending != receipt:
                raise PlanControlError(
                    "ACTIVE_PLAN_CROSS_BINDING_INVALID",
                    "Active Campaign has neither exact finalized claims nor its exact pending reservation",
                )
        return ActivePlanReadback(
            handle,
            receipt.revision_digest,
            envelope.revision.canonical_bytes,
            receipt,
            envelope.claim_proofs,
        )

    def _active_authority_envelope(
        self,
        handle: CampaignHandle,
        receipt: ActivationReceipt,
    ) -> _ActiveAuthorityEnvelope:
        """Reconstruct one closed active authority before any effect.

        This is intentionally shared by normal reads, replay, recovery, and
        the precondition immediately before ``finalize_claims``.  An
        Activation Receipt is therefore a root for the exact compilation
        record and completed #111 Planning output, not a loose pointer to a
        format-valid revision.
        """
        if (
            type(receipt) is not ActivationReceipt
            or receipt.repository != handle.repository
            or receipt.campaign_key != handle.campaign_key
            or receipt.writer_generation != self._writer_generation()
            or type(receipt.revision_digest) is not str
            or _DIGEST.fullmatch(receipt.revision_digest) is None
            or (
                receipt.expected_previous_revision_digest is not None
                and (
                    type(receipt.expected_previous_revision_digest) is not str
                    or _DIGEST.fullmatch(
                        receipt.expected_previous_revision_digest
                    )
                    is None
                )
            )
            or type(receipt.ready_refs) is not tuple
            or type(receipt.ticket_keys) is not tuple
            or not receipt.ready_refs
            or len(set(receipt.ready_refs)) != len(receipt.ready_refs)
            or len(set(receipt.ticket_keys)) != len(receipt.ticket_keys)
            or any(type(ref) is not str or not ref for ref in receipt.ready_refs)
            or any(
                type(ticket_key) is not str or not ticket_key
                for ticket_key in receipt.ticket_keys
            )
            or type(receipt.planning_subject_digest) is not str
            or _DIGEST.fullmatch(receipt.planning_subject_digest) is None
            or type(receipt.planning_stable_action_id) is not str
            or not receipt.planning_stable_action_id
            or type(receipt.planning_preflight_receipt_digest) is not str
            or _DIGEST.fullmatch(
                receipt.planning_preflight_receipt_digest
            )
            is None
            or type(receipt.compilation_record_artifact_digest) is not str
            or _DIGEST.fullmatch(
                receipt.compilation_record_artifact_digest
            )
            is None
            or type(receipt.planning_receipt_digest) is not str
            or _DIGEST.fullmatch(receipt.planning_receipt_digest) is None
            or type(receipt.planning_output_artifact_digest) is not str
            or _DIGEST.fullmatch(
                receipt.planning_output_artifact_digest
            )
            is None
        ):
            raise PlanControlError(
                "ACTIVE_PLAN_CROSS_BINDING_INVALID",
                "Activation Receipt does not exactly bind the CampaignHandle and writer generation",
            )
        attempt = self._repository.read_attempt(
            handle,
            receipt.expected_previous_revision_digest,
        )
        if (
            type(attempt) is not _PlanningAttempt
            or attempt.handle != handle
            or attempt.expected_previous_revision_digest
            != receipt.expected_previous_revision_digest
            or attempt.ready_refs != receipt.ready_refs
            or attempt.ticket_keys != receipt.ticket_keys
            or attempt.subject.digest != receipt.planning_subject_digest
            or attempt.subject.stable_action_id
            != receipt.planning_stable_action_id
            or attempt.compilation_record_artifact_digest is None
            or attempt.compilation_record_artifact_digest
            != receipt.compilation_record_artifact_digest
        ):
            raise PlanControlError(
                "ACTIVE_PLAN_CROSS_BINDING_INVALID",
                "Activation Receipt does not exactly bind its Planning attempt",
            )
        self._verify_attempt_artifacts(attempt)
        snapshot = _snapshot_from_bytes(attempt.snapshot_bytes)
        if (
            snapshot["repository"] != handle.repository
            or tuple(ticket["key"] for ticket in snapshot["tickets"])
            != attempt.ticket_keys
            or tuple(sorted(ticket["source"]["ref"] for ticket in snapshot["tickets"]))
            != attempt.ready_refs
        ):
            raise PlanControlError(
                "ACTIVE_PLAN_CROSS_BINDING_INVALID",
                "Planning attempt Ticket and ready-reference sets differ from its frozen snapshot",
            )
        intent_bytes = self._read_compilation_record(attempt)
        compilation = self._compilation_authority(attempt)
        if (
            compilation["preflight_receipt"]["receipt_digest"]
            != receipt.planning_preflight_receipt_digest
            or compilation["planning_receipt"]["receipt_digest"]
            != receipt.planning_receipt_digest
            or compilation["planning_receipt"][
                "planning_output_artifact_digest"
            ]
            != receipt.planning_output_artifact_digest
            or compilation["output_artifact_digest"]
            != receipt.planning_output_artifact_digest
        ):
            raise PlanControlError(
                "ACTIVE_PLAN_CROSS_BINDING_INVALID",
                "Activation Receipt does not exactly bind its completed Planning authority",
            )
        revision = self._repository.read_revision(receipt.revision_digest)
        if (
            type(revision) is not PlanRevision
            or revision.repository != handle.repository
            or revision.campaign_key != handle.campaign_key
            or revision.digest != receipt.revision_digest
            or digest_bytes(revision.canonical_bytes) != receipt.revision_digest
        ):
            raise PlanControlError(
                "ACTIVE_PLAN_CROSS_BINDING_INVALID",
                "Activated Plan Revision does not exactly bind the Activation Receipt",
            )
        # The persisted revision is an audit fact, never compilation authority.
        # Rebuild it from the frozen snapshot and validated one-pass intent on
        # every active readback so a self-consistent repository substitution
        # cannot redirect an already activated Campaign.
        expected_revision = (
            self._compile_successor_revision(handle, attempt, intent_bytes)
            if attempt.planning_protocol_id == REPLANNING_OUTPUT_PROTOCOL_ID
            else _compile_plan(
                attempt.snapshot_bytes,
                attempt.snapshot_artifact_digest,
                intent_bytes,
                handle,
            )
        )
        _validate_revision_provenance(revision, expected_revision)
        _validate_plan_spec(revision.canonical_bytes)
        plan_spec = revision.plan_spec
        plan_ticket_keys = tuple(item["key"] for item in plan_spec["work"])
        if (
            plan_spec["repository"] != handle.repository
            or plan_spec["campaign"]["key"] != handle.campaign_key
            or plan_ticket_keys != receipt.ticket_keys
        ):
            raise PlanControlError(
                "ACTIVE_PLAN_CROSS_BINDING_INVALID",
                "PlanSpec work, Campaign identity, and Activation Receipt Tickets differ",
            )
        if (
            _read_artifact_json(
                self._artifacts,
                receipt.revision_digest,
                code="PLAN_READBACK_INVALID",
            )
            != plan_spec
        ):
            raise PlanControlError("PLAN_READBACK_INVALID", "PlanSpec Artifact does not read back exactly")
        self._verify_policy_witness(plan_spec)
        read_ledger = getattr(self._repository, "read_campaign_claim_proofs", None)
        if not callable(read_ledger):
            raise PlanControlError(
                "ACTIVE_PLAN_CROSS_BINDING_INVALID",
                "PlanControl repository omitted the complete Campaign claim ledger",
            )
        active_proofs = read_ledger(handle)
        if (
            type(active_proofs) is not tuple
            or any(type(proof) is not TicketClaimProof for proof in active_proofs)
            or tuple(sorted(proof.ticket_key for proof in active_proofs))
            != tuple(proof.ticket_key for proof in active_proofs)
        ):
            raise PlanControlError(
                "ACTIVE_PLAN_CROSS_BINDING_INVALID",
                "Campaign claim ledger is malformed or not canonical",
            )
        return _ActiveAuthorityEnvelope(
            handle=handle,
            receipt=receipt,
            attempt=attempt,
            revision=revision,
            snapshot=snapshot,
            compilation_record=compilation,
            claim_proofs=active_proofs,
        )

    def _new_attempt(self, handle: CampaignHandle, refs: tuple[str, ...], expected: str | None) -> _PlanningAttempt:
        snapshot = _normalize_snapshot(self._source.snapshot(handle.repository, refs), handle.repository, refs)
        ticket_keys = tuple(ticket["key"] for ticket in snapshot["tickets"])
        policy_witness = {key: value for key, value in snapshot["policy"].items() if key != "digest"}
        snapshot_bytes = canonical_bytes(snapshot)
        if len(snapshot_bytes) > self._max_snapshot_bytes:
            decision_value = {
                "schema_version": "gwo.plan.split-campaign-decision.v1",
                "campaign": {
                    "repository": handle.repository,
                    "campaign_key": handle.campaign_key,
                },
                "ready_refs": list(refs),
                "ticket_keys": list(ticket_keys),
                "expected_previous_revision_digest": expected,
                "snapshot_digest": digest_bytes(snapshot_bytes),
                "snapshot_byte_length": len(snapshot_bytes),
                "maximum_snapshot_bytes": self._max_snapshot_bytes,
            }
            decision_digest = _put_canonical(self._artifacts, decision_value)
            decision = self._repository.save_split_decision(
                _SplitCampaignDecisionRecord(
                    handle=handle,
                    ready_refs=refs,
                    ticket_keys=ticket_keys,
                    expected_previous_revision_digest=expected,
                    canonical_bytes=canonical_bytes(decision_value),
                    digest=decision_digest,
                )
            )
            self._raise_split_decision(decision, handle, refs, expected)
        policy_artifact_digest = _put_canonical(self._artifacts, policy_witness)
        if policy_artifact_digest != snapshot["policy"]["digest"]:
            raise PlanControlError("POLICY_WITNESS_INVALID", "Policy Witness Artifact digest differs from its frozen witness digest")
        snapshot_ref = _put_canonical(self._artifacts, snapshot)
        if snapshot_ref != digest_bytes(snapshot_bytes):
            raise PlanControlError("SNAPSHOT_ARTIFACT_MISMATCH", "Snapshot Artifact digest differs from canonical snapshot bytes")
        policy_digest = policy_artifact_digest
        stable_action_id = "planning:" + digest_value({"handle": handle.__dict__, "snapshot_digest": snapshot_ref, "policy_witness_digest": policy_digest, "expected_previous_revision_digest": expected})
        provisional = CampaignPlanningSubject(
            repository=handle.repository,
            campaign_key=handle.campaign_key,
            campaign_handle=_handle_ref(handle),
            expected_previous_plan_revision_digest=expected,
            snapshot_artifact_digest=snapshot_ref,
            policy_witness_digest=policy_digest,
            planning_request_artifact_digest="0" * 64,
            stable_action_id=stable_action_id,
        )
        request_ref = _put_canonical(
            self._artifacts,
            planning_prompt(
                subject_digest=provisional.prompt_binding_digest,
                authority_digest=policy_digest,
                snapshot_artifact_digest=snapshot_ref,
                policy_witness_artifact_digest=policy_digest,
            ),
        )
        subject = replace(provisional, planning_request_artifact_digest=request_ref)
        return _PlanningAttempt(
            handle=handle,
            ready_refs=refs,
            ticket_keys=ticket_keys,
            expected_previous_revision_digest=expected,
            snapshot_bytes=snapshot_bytes,
            snapshot_artifact_digest=snapshot_ref,
            policy_witness_digest=policy_digest,
            planning_request_artifact_digest=request_ref,
            subject=subject,
        )

    def _raise_split_decision(
        self,
        record: _SplitCampaignDecisionRecord,
        handle: CampaignHandle,
        refs: tuple[str, ...],
        expected: str | None,
    ) -> None:
        try:
            value = load_canonical_json(record.canonical_bytes)
        except CanonicalJsonError as error:
            raise PlanControlError(
                "SPLIT_CAMPAIGN_DECISION_INVALID",
                "Split-Campaign Decision is not canonical",
            ) from error
        expected_keys = {
            "schema_version",
            "campaign",
            "ready_refs",
            "ticket_keys",
            "expected_previous_revision_digest",
            "snapshot_digest",
            "snapshot_byte_length",
            "maximum_snapshot_bytes",
        }
        if (
            type(value) is not dict
            or set(value) != expected_keys
            or value["schema_version"]
            != "gwo.plan.split-campaign-decision.v1"
            or value["campaign"]
            != {
                "repository": handle.repository,
                "campaign_key": handle.campaign_key,
            }
            or value["ready_refs"] != list(refs)
            or value["ticket_keys"] != list(record.ticket_keys)
            or value["expected_previous_revision_digest"] != expected
            or record.handle != handle
            or record.ready_refs != refs
            or record.expected_previous_revision_digest != expected
            or digest_bytes(record.canonical_bytes) != record.digest
            or _read_artifact_json(
                self._artifacts,
                record.digest,
                code="SPLIT_CAMPAIGN_DECISION_INVALID",
            )
            != value
            or type(value["snapshot_byte_length"]) is not int
            or type(value["maximum_snapshot_bytes"]) is not int
            or value["snapshot_byte_length"]
            <= value["maximum_snapshot_bytes"]
        ):
            raise PlanControlError(
                "SPLIT_CAMPAIGN_DECISION_INVALID",
                "Split-Campaign Decision does not bind the exact Campaign snapshot",
            )
        snapshot_digest = _digest(
            value["snapshot_digest"],
            "Split-Campaign snapshot digest",
        )
        raise SplitCampaignDecision(
            handle=handle,
            snapshot_digest=snapshot_digest,
            snapshot_byte_length=value["snapshot_byte_length"],
            maximum_snapshot_bytes=value["maximum_snapshot_bytes"],
            decision_digest=record.digest,
        )

    def _obtain_one_planning_intent(self, attempt: _PlanningAttempt) -> _PlanningAttempt:
        # A pre-existing reservation proves only this stable action's immutable
        # identity.  RuntimeGateway.progress owns its readback-first recovery:
        # an action may be absent when PlanControl crashed between this durable
        # reservation and Runtime materialization.
        existing = self._repository.read_planning_reservation(
            attempt.handle,
            attempt.subject.stable_action_id,
        )
        if existing is not None:
            expected = PlanningReservation(
                repository=attempt.handle.repository,
                campaign_key=attempt.handle.campaign_key,
                ticket_keys=attempt.ticket_keys,
                subject_digest=attempt.subject.digest,
                stable_action_id=attempt.subject.stable_action_id,
                preflight_receipt_digest=existing.preflight_receipt_digest,
            )
            if existing != expected:
                raise PlanControlError(
                    "PLANNING_RESERVATION_CONFLICT",
                    "Existing Planning reservation changed its exact subject identity",
                )
            preflight = PlanningPreflightReceipt(
                subject_digest=existing.subject_digest,
                stable_action_id=existing.stable_action_id,
                receipt_digest=existing.preflight_receipt_digest,
            )
            receipt = self._gateway.progress(attempt.subject, preflight)
            _validate_planning_receipt(receipt, attempt.subject)
            return self._save_completed_planning_intent(attempt, preflight, receipt)

        # The preflight is intentionally before both claim reservation and
        # semantic progress.  Its receipt is opaque, but exact subject/action
        # binding is mechanically checked before it is consumed.
        preflight = self._gateway.planning_preflight(attempt.subject)
        _validate_preflight(preflight, attempt.subject)
        self._repository.reserve_planning(
            PlanningReservation(
                repository=attempt.handle.repository,
                campaign_key=attempt.handle.campaign_key,
                ticket_keys=attempt.ticket_keys,
                subject_digest=attempt.subject.digest,
                stable_action_id=attempt.subject.stable_action_id,
                preflight_receipt_digest=preflight.receipt_digest,
            )
        )
        receipt = self._gateway.progress(attempt.subject, preflight)
        _validate_planning_receipt(receipt, attempt.subject)
        return self._save_completed_planning_intent(attempt, preflight, receipt)

    def _save_completed_planning_intent(
        self,
        attempt: _PlanningAttempt,
        preflight: PlanningPreflightReceipt,
        receipt: PlanningReceipt,
    ) -> _PlanningAttempt:
        if receipt.status != "completed":
            return attempt
        output_digest = receipt.planning_output_artifact_digest
        assert output_digest is not None
        output_value = _read_artifact_json(
            self._artifacts,
            output_digest,
            code="RUNTIME_PLANNING_OUTPUT_INVALID",
        )
        intent = _planning_payload(self._artifacts, output_digest, attempt.subject)
        normalized = _normalize_intent(intent, _snapshot_from_bytes(attempt.snapshot_bytes))
        record = {
            "schema_version": "gwo.plan.compilation.v1",
            "subject": attempt.subject.canonical(),
            "subject_digest": attempt.subject.digest,
            "snapshot_artifact_digest": attempt.snapshot_artifact_digest,
            "policy_witness_digest": attempt.policy_witness_digest,
            "planning_request_artifact_digest": attempt.planning_request_artifact_digest,
            "stable_action_id": attempt.subject.stable_action_id,
            "preflight_receipt": {
                "subject_digest": preflight.subject_digest,
                "stable_action_id": preflight.stable_action_id,
                "receipt_digest": preflight.receipt_digest,
            },
            "planning_receipt": {
                "subject_digest": receipt.subject_digest,
                "stable_action_id": receipt.stable_action_id,
                "status": receipt.status,
                "receipt_digest": receipt.receipt_digest,
                "command": None,
                "wake_cursor": receipt.wake_cursor,
                "wake_hints": list(receipt.wake_hints),
                "output_artifact_digest": receipt.output_artifact_digest,
                "planning_output_artifact_digest": (
                    receipt.planning_output_artifact_digest
                ),
            },
            "output_artifact_digest": output_digest,
            "planning_output": output_value,
            "normalized_intent": normalized,
            "normalized_intent_digest": digest_value(normalized),
        }
        record_bytes = canonical_bytes(record)
        record_digest = _put_canonical(self._artifacts, record)
        if record_digest != digest_bytes(record_bytes):
            raise PlanControlError(
                "COMPILATION_RECORD_INVALID",
                "Compilation record Artifact changed its canonical bytes",
            )
        return self._repository.save_attempt(
            replace(
                attempt,
                compilation_record_artifact_digest=record_digest,
                compilation_record_bytes=record_bytes,
            )
        )

    def _read_compilation_record(self, attempt: _PlanningAttempt) -> bytes:
        if attempt.planning_protocol_id == REPLANNING_OUTPUT_PROTOCOL_ID:
            return self._read_successor_compilation_record(attempt)
        if attempt.planning_protocol_id != PLANNING_OUTPUT_PROTOCOL_ID:
            raise PlanControlError(
                "COMPILATION_RECORD_INVALID",
                "Planning attempt names an unsupported protocol",
            )
        digest = attempt.compilation_record_artifact_digest
        if digest is None:
            raise PlanControlError(
                "COMPILATION_RECORD_INVALID",
                "Campaign attempt has no completed compilation record",
            )
        try:
            record = _read_artifact_json(
                self._artifacts,
                digest,
                code="COMPILATION_RECORD_INVALID",
            )
            if type(attempt.compilation_record_bytes) is not bytes:
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Durable compilation record bytes are missing",
                )
            if (
                digest_bytes(attempt.compilation_record_bytes) != digest
                or load_canonical_json(attempt.compilation_record_bytes) != record
                or canonical_bytes(record) != attempt.compilation_record_bytes
            ):
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Durable compilation record bytes do not bind the Artifact",
                )
            expected = {
                "schema_version",
                "subject",
                "subject_digest",
                "snapshot_artifact_digest",
                "policy_witness_digest",
                "planning_request_artifact_digest",
                "stable_action_id",
                "preflight_receipt",
                "planning_receipt",
                "output_artifact_digest",
                "planning_output",
                "normalized_intent",
                "normalized_intent_digest",
            }
            if (
                type(record) is not dict
                or set(record) != expected
                or record["schema_version"] != "gwo.plan.compilation.v1"
            ):
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Compilation record has an unknown schema",
                )
            raw_subject = record["subject"]
            if type(raw_subject) is not dict or set(raw_subject) != {
                "kind",
                "repository",
                "campaign_key",
                "campaign_handle",
                "expected_previous_plan_revision_digest",
                "snapshot_artifact_digest",
                "policy_witness_digest",
                "planning_request_artifact_digest",
                "stable_action_id",
            } or raw_subject.get("kind") != "campaign_planning":
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Compilation record Planning subject is malformed",
                )
            subject = CampaignPlanningSubject(
                **{key: value for key, value in raw_subject.items() if key != "kind"}
            )
            if (
                subject != attempt.subject
                or record["subject_digest"] != subject.digest
                or record["snapshot_artifact_digest"]
                != attempt.snapshot_artifact_digest
                or record["policy_witness_digest"] != attempt.policy_witness_digest
                or record["planning_request_artifact_digest"]
                != attempt.planning_request_artifact_digest
                or record["stable_action_id"] != subject.stable_action_id
            ):
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Compilation record changed its snapshot, policy, request, or action identity",
                )
            preflight_value = record["preflight_receipt"]
            if type(preflight_value) is not dict or set(preflight_value) != {
                "subject_digest",
                "stable_action_id",
                "receipt_digest",
            }:
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Compilation preflight receipt is malformed",
                )
            preflight = PlanningPreflightReceipt(**preflight_value)
            _validate_preflight(preflight, subject)
            planning_value = record["planning_receipt"]
            if type(planning_value) is not dict or set(planning_value) != {
                "subject_digest",
                "stable_action_id",
                "status",
                "receipt_digest",
                "command",
                "wake_cursor",
                "wake_hints",
                "output_artifact_digest",
                "planning_output_artifact_digest",
            }:
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Compilation planning receipt is malformed",
                )
            wake_hints = planning_value["wake_hints"]
            if (
                planning_value["command"] is not None
                or type(wake_hints) is not list
            ):
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Compilation planning receipt is malformed",
                )
            planning = PlanningReceipt(
                **{
                    **planning_value,
                    "wake_hints": tuple(wake_hints),
                }
            )
            _validate_planning_receipt(planning, subject)
            if (
                planning.status != "completed"
                or planning.planning_output_artifact_digest
                != record["output_artifact_digest"]
            ):
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Compilation record is not bound to one completed Planning receipt",
                )
            if (
                _read_artifact_json(
                    self._artifacts,
                    record["output_artifact_digest"],
                    code="RUNTIME_PLANNING_OUTPUT_INVALID",
                )
                != record["planning_output"]
            ):
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Compilation record output does not read back at its exact Artifact digest",
                )
            raw_intent = _planning_payload(
                self._artifacts,
                record["output_artifact_digest"],
                subject,
            )
            normalized = _normalize_intent(
                raw_intent,
                _snapshot_from_bytes(attempt.snapshot_bytes),
            )
            if (
                record["normalized_intent"] != normalized
                or record["normalized_intent_digest"] != digest_value(normalized)
            ):
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Compilation record normalized intent changed identity",
                )
            return canonical_bytes(normalized)
        except PlanControlError as error:
            if error.code == "COMPILATION_RECORD_INVALID":
                raise
            raise PlanControlError(
                "COMPILATION_RECORD_INVALID",
                "Compilation record failed closed identity validation",
            ) from error
        except Exception as error:
            raise PlanControlError(
                "COMPILATION_RECORD_INVALID",
                "Compilation record is missing or malformed",
            ) from error

    def _read_successor_compilation_record(
        self,
        attempt: _PlanningAttempt,
    ) -> bytes:
        """Decode one exact successor compilation record and its authority."""

        digest = attempt.compilation_record_artifact_digest
        if digest is None:
            raise PlanControlError(
                "COMPILATION_RECORD_INVALID",
                "Successor attempt has no completed compilation record",
            )
        try:
            record = _read_artifact_json(
                self._artifacts,
                digest,
                code="COMPILATION_RECORD_INVALID",
            )
            if type(attempt.compilation_record_bytes) is not bytes:
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Durable successor compilation record bytes are missing",
                )
            if (
                digest_bytes(attempt.compilation_record_bytes) != digest
                or load_canonical_json(attempt.compilation_record_bytes) != record
                or canonical_bytes(record) != attempt.compilation_record_bytes
            ):
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Durable successor compilation record bytes do not bind the Artifact",
                )
            expected = {
                "schema_version",
                "subject",
                "subject_digest",
                "snapshot_artifact_digest",
                "policy_witness_digest",
                "planning_request_artifact_digest",
                "stable_action_id",
                "preflight_receipt",
                "planning_receipt",
                "output_artifact_digest",
                "planning_output",
                "coordinator_capability_proof",
                "coordinator_capability_proof_digest",
                "classification",
                "classification_digest",
                "normalized_intent",
                "normalized_intent_digest",
            }
            if (
                type(record) is not dict
                or set(record) != expected
                or record["schema_version"]
                != "gwo.plan.successor-compilation.v1"
            ):
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Successor compilation record has an unknown schema",
                )

            raw_subject = record["subject"]
            if type(raw_subject) is not dict or set(raw_subject) != {
                "kind",
                "repository",
                "campaign_key",
                "campaign_handle",
                "expected_previous_plan_revision_digest",
                "snapshot_artifact_digest",
                "policy_witness_digest",
                "planning_request_artifact_digest",
                "stable_action_id",
            } or raw_subject.get("kind") != "campaign_planning":
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Successor Planning subject is malformed",
                )
            subject = CampaignPlanningSubject(
                **{
                    key: value
                    for key, value in raw_subject.items()
                    if key != "kind"
                }
            )
            if (
                subject != attempt.subject
                or record["subject_digest"] != subject.digest
                or record["snapshot_artifact_digest"]
                != attempt.snapshot_artifact_digest
                or record["policy_witness_digest"]
                != attempt.policy_witness_digest
                or record["planning_request_artifact_digest"]
                != attempt.planning_request_artifact_digest
                or record["stable_action_id"] != subject.stable_action_id
                or attempt.expected_previous_revision_digest is None
                or subject.expected_previous_plan_revision_digest
                != attempt.expected_previous_revision_digest
                or not subject.stable_action_id.startswith("replan:")
            ):
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Successor compilation record changed its immutable subject binding",
                )

            preflight_value = record["preflight_receipt"]
            if type(preflight_value) is not dict or set(preflight_value) != {
                "subject_digest",
                "stable_action_id",
                "receipt_digest",
            }:
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Successor preflight receipt is malformed",
                )
            preflight = PlanningPreflightReceipt(**preflight_value)
            _validate_preflight(preflight, subject)

            planning_value = record["planning_receipt"]
            if type(planning_value) is not dict or set(planning_value) != {
                "subject_digest",
                "stable_action_id",
                "status",
                "receipt_digest",
                "command",
                "wake_cursor",
                "wake_hints",
                "output_artifact_digest",
                "planning_output_artifact_digest",
            } or type(planning_value["wake_hints"]) is not list:
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Successor Planning receipt is malformed",
                )
            planning = PlanningReceipt(
                **{
                    **planning_value,
                    "wake_hints": tuple(planning_value["wake_hints"]),
                }
            )
            _validate_planning_receipt(planning, subject)
            if (
                planning.status != "completed"
                or planning.planning_output_artifact_digest
                != record["output_artifact_digest"]
                or record["output_artifact_digest"]
                != record["planning_receipt"]["planning_output_artifact_digest"]
            ):
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Successor record is not bound to one completed Planning receipt",
                )

            output = _read_artifact_json(
                self._artifacts,
                record["output_artifact_digest"],
                code="RUNTIME_PLANNING_OUTPUT_INVALID",
            )
            if output != record["planning_output"]:
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Successor Planning output does not read back exactly",
                )
            payload = _planning_payload(
                self._artifacts,
                record["output_artifact_digest"],
                subject,
            )

            proof_value = record["coordinator_capability_proof"]
            if type(proof_value) is not dict or set(proof_value) != {
                "subject_digest",
                "repository_read_only",
                "tracker_read_only",
                "can_activate_plan_revision",
                "can_edit_tracker",
                "can_expand_authority",
                "delegation_enabled",
            }:
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Successor Coordinator capability proof is malformed",
                )
            proof = CoordinatorCapabilityProof(**proof_value)
            _validate_coordinator_capability(proof, subject)
            if record["coordinator_capability_proof_digest"] != proof.digest:
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Successor Coordinator capability proof digest changed",
                )

            classification = PlanInvalidationClassification.from_canonical(
                record["classification"]
            )
            if (
                record["classification_digest"] != classification.digest
                or classification.action_id != subject.stable_action_id
                or classification.snapshot_digest
                != attempt.snapshot_artifact_digest
                or classification.plan_revision_digest
                != attempt.expected_previous_revision_digest
                or classification.capability_proof_digest != proof.digest
            ):
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Successor classification is not bound to its exact subject",
                )

            output_classification = _normalize_replanning_intent(
                payload,
                snapshot=_snapshot_from_bytes(attempt.snapshot_bytes),
                action_id=classification.action_id,
                snapshot_digest=classification.snapshot_digest,
                plan_revision_digest=classification.plan_revision_digest,
                evidence_digests=classification.evidence_digests,
                capability_proof_digest=proof.digest,
            )
            if output_classification != classification:
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Successor output classification does not read back exactly",
                )

            derive_successor, _compile_successor, _validate_source = _successor_plan_api()
            normalized = derive_successor(
                _snapshot_from_bytes(attempt.snapshot_bytes),
                classification.canonical(),
            )
            if (
                record["normalized_intent"] != normalized
                or record["normalized_intent_digest"] != digest_value(normalized)
                or canonical_bytes(normalized) != canonical_bytes(record["normalized_intent"])
            ):
                raise PlanControlError(
                    "COMPILATION_RECORD_INVALID",
                    "Successor normalized intent changed identity",
                )
            return canonical_bytes(normalized)
        except PlanControlError:
            raise
        except Exception as error:
            raise PlanControlError(
                getattr(error, "code", "COMPILATION_RECORD_INVALID"),
                getattr(error, "detail", "Successor compilation record is missing or malformed"),
            ) from error

    def _compilation_authority(
        self,
        attempt: _PlanningAttempt,
    ) -> Mapping[str, Any]:
        """Return the fully validated immutable #111/compilation binding."""

        # ``_read_compilation_record`` is the sole closed decoder for this
        # object.  Read the Artifact only after it has verified every receipt,
        # output, snapshot, request, and normalized-intent relationship.
        self._read_compilation_record(attempt)
        digest = attempt.compilation_record_artifact_digest
        assert digest is not None
        record = _read_artifact_json(
            self._artifacts,
            digest,
            code="COMPILATION_RECORD_INVALID",
        )
        if type(record) is not dict:
            raise PlanControlError(
                "COMPILATION_RECORD_INVALID",
                "Compilation authority is not an exact object",
            )
        return record

    def _planning_reservation_from_compilation(
        self,
        attempt: _PlanningAttempt,
    ) -> PlanningReservation:
        digest = attempt.compilation_record_artifact_digest
        if digest is None:
            raise PlanControlError(
                "COMPILATION_RECORD_INVALID",
                "Planning reservation has no completed compilation record",
            )
        record = _read_artifact_json(
            self._artifacts,
            digest,
            code="COMPILATION_RECORD_INVALID",
        )
        preflight = record.get("preflight_receipt")
        if type(preflight) is not dict or set(preflight) != {
            "subject_digest",
            "stable_action_id",
            "receipt_digest",
        }:
            raise PlanControlError(
                "COMPILATION_RECORD_INVALID",
                "Planning reservation preflight binding is malformed",
            )
        reservation = PlanningReservation(
            repository=attempt.handle.repository,
            campaign_key=attempt.handle.campaign_key,
            ticket_keys=attempt.ticket_keys,
            subject_digest=preflight["subject_digest"],
            stable_action_id=preflight["stable_action_id"],
            preflight_receipt_digest=preflight["receipt_digest"],
        )
        expected = PlanningReservation(
            repository=attempt.handle.repository,
            campaign_key=attempt.handle.campaign_key,
            ticket_keys=attempt.ticket_keys,
            subject_digest=attempt.subject.digest,
            stable_action_id=attempt.subject.stable_action_id,
            preflight_receipt_digest=preflight["receipt_digest"],
        )
        if reservation != expected:
            raise PlanControlError(
                "COMPILATION_RECORD_INVALID",
                "Planning reservation does not bind the exact compiled subject",
            )
        return reservation

    def _verify_attempt_artifacts(self, attempt: _PlanningAttempt) -> None:
        """Read back the immutable snapshot and Policy Witness before use."""

        if type(attempt) is not _PlanningAttempt:
            raise PlanControlError(
                "PLANNING_ATTEMPT_IDENTITY_CONFLICT",
                "Planning attempt is not the exact durable attempt type",
            )
        if type(attempt.snapshot_bytes) is not bytes:
            raise PlanControlError(
                "SNAPSHOT_READBACK_INVALID",
                "Planning attempt snapshot bytes are missing",
            )
        if type(attempt.handle) is not CampaignHandle:
            raise PlanControlError(
                "PLANNING_ATTEMPT_IDENTITY_CONFLICT",
                "Planning attempt handle is malformed",
            )
        if type(attempt.subject) is not CampaignPlanningSubject:
            raise PlanControlError(
                "PLANNING_ATTEMPT_IDENTITY_CONFLICT",
                "Planning attempt subject is malformed",
            )
        snapshot = _snapshot_from_bytes(attempt.snapshot_bytes)
        if digest_bytes(attempt.snapshot_bytes) != attempt.snapshot_artifact_digest:
            raise PlanControlError("SNAPSHOT_DIGEST_MISMATCH", "Attempt snapshot digest changed")
        if (
            _read_artifact_json(
                self._artifacts,
                attempt.snapshot_artifact_digest,
                code="SNAPSHOT_READBACK_INVALID",
            )
            != snapshot
        ):
            raise PlanControlError("SNAPSHOT_READBACK_INVALID", "Snapshot Artifact does not read back exactly")
        policy_key = (
            "policy_witness"
            if attempt.planning_protocol_id == REPLANNING_OUTPUT_PROTOCOL_ID
            else "policy"
        )
        policy_value = snapshot.get(policy_key)
        if type(policy_value) is not dict:
            raise PlanControlError(
                "SNAPSHOT_READBACK_INVALID",
                f"Snapshot omitted its required {policy_key} binding",
            )
        witness = {key: value for key, value in policy_value.items() if key != "digest"}
        if (
            digest_value(witness) != attempt.policy_witness_digest
            or _read_artifact_json(
                self._artifacts,
                attempt.policy_witness_digest,
                code="POLICY_WITNESS_INVALID",
            )
            != witness
        ):
            raise PlanControlError("POLICY_WITNESS_INVALID", "Policy Witness Artifact does not read back exactly")
        expected_action = "planning:" + digest_value(
            {
                "handle": attempt.handle.__dict__,
                "snapshot_digest": attempt.snapshot_artifact_digest,
                "policy_witness_digest": attempt.policy_witness_digest,
                "expected_previous_revision_digest": attempt.expected_previous_revision_digest,
            }
        )
        subject = attempt.subject
        if (
            subject.repository != attempt.handle.repository
            or subject.campaign_key != attempt.handle.campaign_key
            or subject.campaign_handle != _handle_ref(attempt.handle)
            or subject.expected_previous_plan_revision_digest
            != attempt.expected_previous_revision_digest
            or subject.snapshot_artifact_digest
            != attempt.snapshot_artifact_digest
            or subject.policy_witness_digest != attempt.policy_witness_digest
            or subject.planning_request_artifact_digest
            != attempt.planning_request_artifact_digest
            or (
                subject.stable_action_id != expected_action
                if attempt.planning_protocol_id == PLANNING_OUTPUT_PROTOCOL_ID
                else not subject.stable_action_id.startswith("replan:")
            )
        ):
            raise PlanControlError(
                "PLANNING_ATTEMPT_IDENTITY_CONFLICT",
                "Planning attempt subject changed its immutable identity",
            )
        request = _read_artifact_json(
            self._artifacts,
            attempt.planning_request_artifact_digest,
            code="PLANNING_REQUEST_INVALID",
        )
        expected_request = (
            planning_prompt(
                subject_digest=subject.prompt_binding_digest,
                authority_digest=attempt.policy_witness_digest,
                snapshot_artifact_digest=attempt.snapshot_artifact_digest,
                policy_witness_artifact_digest=attempt.policy_witness_digest,
            )
            if attempt.planning_protocol_id == PLANNING_OUTPUT_PROTOCOL_ID
            else replanning_prompt(
                subject_digest=subject.prompt_binding_digest,
                authority_digest=attempt.policy_witness_digest,
                snapshot_artifact_digest=attempt.snapshot_artifact_digest,
                policy_witness_artifact_digest=attempt.policy_witness_digest,
            )
        )
        if request != expected_request:
            raise PlanControlError(
                "PLANNING_REQUEST_INVALID",
                "Planning request Artifact changed its subject or authority binding",
            )

    def _verify_policy_witness(self, plan_spec: Mapping[str, Any]) -> None:
        policy = plan_spec.get("policy")
        if type(policy) is not dict:
            raise PlanControlError("POLICY_WITNESS_INVALID", "PlanSpec policy projection is invalid")
        digest = _digest(policy.get("digest"), "PlanSpec Policy Witness digest")
        witness = _read_artifact_json(
            self._artifacts,
            digest,
            code="POLICY_WITNESS_INVALID",
        )
        if type(witness) is not dict or _normalize_policy({**witness, "digest": digest}) != {**witness, "digest": digest}:
            raise PlanControlError("POLICY_WITNESS_INVALID", "PlanSpec Policy Witness Artifact is invalid")

    def _writer_generation(self) -> str:
        value = getattr(self._repository, "writer_generation", None)
        return _text(value, "repository writer_generation")


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise PlanControlError("PLAN_CONTROL_INVALID", f"{label} must be non-empty exact text")
    return value


def _digest(value: object, label: str) -> str:
    value = _text(value, label)
    if _DIGEST.fullmatch(value) is None:
        raise PlanControlError("PLAN_CONTROL_INVALID", f"{label} must be a SHA-256 digest")
    return value


def _optional_digest(value: object, label: str) -> str | None:
    return None if value is None else _digest(value, label)


def _canonical(value: Any, *, code: str = "PLAN_CONTROL_INVALID") -> Any:
    try:
        return load_canonical_json(canonical_bytes(value))
    except CanonicalJsonError as error:
        raise PlanControlError(code, "value is outside canonical JSON") from error


def _ready_refs(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PlanControlError("READY_REFS_INVALID", "ready_refs must be a sequence")
    refs = tuple(_text(item, "ready_ref") for item in value)
    if not refs or len(set(refs)) != len(refs):
        raise PlanControlError("READY_REFS_INVALID", "ready_refs must be unique non-empty Ticket references")
    return tuple(sorted(refs))


def _put_canonical(artifacts: PlanningArtifacts, value: Any) -> str:
    reference = artifacts.put_canonical(_canonical(value))
    digest = getattr(reference, "digest", None)
    return _digest(digest, "Artifact digest")


def _read_artifact_json(
    artifacts: PlanningArtifacts,
    digest: str,
    *,
    code: str,
) -> Any:
    """Verify the #111 ArtifactRef, then decode through its canonical reader."""

    try:
        reference = artifacts.get(digest)
        if getattr(reference, "digest", None) != digest:
            raise PlanControlError(code, "Artifact reference does not bind its requested digest")
        value = _canonical(artifacts.read_json(digest), code=code)
        if digest_value(value) != digest:
            raise PlanControlError(code, "Artifact value does not bind its content digest")
        return value
    except PlanControlError:
        raise
    except Exception as error:
        raise PlanControlError(code, "Artifact is missing or cannot be read as canonical JSON") from error


def _handle_ref(handle: CampaignHandle) -> str:
    # The external API treats this as opaque while the Gateway needs a stable,
    # transport-safe identity to bind its independent campaign record.
    return "campaign-handle:" + digest_value(handle.__dict__)


def _frozen_ref(value: Any, label: str) -> dict[str, str]:
    if type(value) is not dict or set(value) != {"ref", "digest"}:
        raise PlanControlError("SNAPSHOT_INVALID", f"{label} must contain only ref and digest")
    return {"ref": _text(value["ref"], f"{label} ref"), "digest": _digest(value["digest"], f"{label} digest")}


def _campaign_source(value: Any, repository: str) -> dict[str, str]:
    """Validate the immutable Git identity selected for one Campaign.

    A branch name is only the input selector.  The resolved commit and tree
    are the durable authority used by PlanSpec and restart readback.
    """

    expected = {
        "repository",
        "input_ref",
        "resolved_commit_oid",
        "tree_oid",
        "digest",
    }
    if type(value) is not dict or set(value) != expected:
        raise PlanControlError(
            "SNAPSHOT_INVALID",
            "Campaign source must carry its complete immutable Git identity",
        )
    source_repository = _text(value["repository"], "Campaign source repository")
    input_ref = _text(value["input_ref"], "Campaign source input ref")
    commit_oid = _text(value["resolved_commit_oid"], "Campaign source commit")
    tree_oid = _text(value["tree_oid"], "Campaign source tree")
    if (
        source_repository != repository
        or not input_ref.startswith("refs/heads/")
        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit_oid) is None
        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", tree_oid) is None
    ):
        raise PlanControlError(
            "SNAPSHOT_INVALID",
            "Campaign source identity is malformed or belongs to another repository",
        )
    source = {
        "repository": source_repository,
        "input_ref": input_ref,
        "resolved_commit_oid": commit_oid,
        "tree_oid": tree_oid,
    }
    if _digest(value["digest"], "Campaign source digest") != digest_value(source):
        raise PlanControlError(
            "SNAPSHOT_INVALID",
            "Campaign source digest does not bind its exact Git identity",
        )
    return {**source, "digest": value["digest"]}


def _versioned(value: object, *, roots: frozenset[str], label: str) -> str:
    value = _text(value, label)
    if _VERSIONED_IDENTIFIER.fullmatch(value) is None or value.split(".", 1)[0] not in roots:
        raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} is not a permitted versioned policy identifier")
    return value


def _grants(value: Any, label: str) -> list[dict[str, str]]:
    if type(value) is not list:
        raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} must be a list")
    grants = []
    for grant in value:
        if type(grant) is not dict or set(grant) != {"operation_id", "resource_id"}:
            raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} contains an invalid grant")
        grants.append({"operation_id": _versioned(grant["operation_id"], roots=_OPERATION_ROOTS, label="operation_id"), "resource_id": _versioned(grant["resource_id"], roots=_RESOURCE_ROOTS, label="resource_id")})
    if len({(item["operation_id"], item["resource_id"]) for item in grants}) != len(grants):
        raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} repeats a grant")
    return sorted(grants, key=lambda item: (item["operation_id"], item["resource_id"]))


def _facts(value: Any, *, label: str, validator) -> list[str]:
    if type(value) is not list:
        raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} must be a list")
    facts = [validator(item) for item in value]
    if len(set(facts)) != len(facts):
        raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} repeats a fact")
    return sorted(facts)


def _normalize_policy(value: Any) -> dict[str, Any]:
    expected = {"schema_version", "ref", "digest", "authority_grants", "allowed_capabilities", "exclusive_resources"}
    if type(value) is not dict or set(value) != expected or value["schema_version"] != 1:
        raise PlanControlError("POLICY_WITNESS_INVALID", "Policy Witness schema is invalid")
    raw_grants = value["authority_grants"]
    if type(raw_grants) is not dict or set(raw_grants) != set(_POLICY_ROLES):
        raise PlanControlError("POLICY_WITNESS_INVALID", "Policy Witness must supply every authority role")
    authority_grants = {
        role: _grants(raw_grants[role], f"{role} grants")
        for role in _POLICY_ROLES
    }
    expected_grants = {
        role: [
            {
                "operation_id": operation_id,
                "resource_id": resource_id,
            }
            for operation_id, resource_id in _ROLE_AUTHORITY_GRANTS[role]
        ]
        for role in _POLICY_ROLES
    }
    if authority_grants != expected_grants:
        raise PlanControlError(
            "POLICY_WITNESS_INVALID",
            "Policy Witness grants do not match the exact role authority allowlists",
        )
    core = {
        "schema_version": 1,
        "ref": _text(value["ref"], "Policy Witness ref"),
        "authority_grants": authority_grants,
        "allowed_capabilities": _facts(value["allowed_capabilities"], label="allowed_capabilities", validator=lambda item: _capability(item, "allowed_capabilities")),
        "exclusive_resources": _facts(value["exclusive_resources"], label="exclusive_resources", validator=lambda item: _versioned(item, roots=_RESOURCE_ROOTS, label="exclusive_resources")),
    }
    digest = _digest(value["digest"], "Policy Witness digest")
    if digest != digest_value(core):
        raise PlanControlError("POLICY_WITNESS_INVALID", "Policy Witness digest does not bind its exact facts")
    return {**core, "digest": digest}


def _capability(value: object, label: str) -> str:
    value = _text(value, label)
    if _CAPABILITY.fullmatch(value) is None:
        raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} contains an invalid capability")
    return value


def _normalize_ticket_contract(
    value: Any,
    *,
    ticket_key: str,
    expected_repository: str,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise PlanControlError(
            "TICKET_CONTRACT_MISSING",
            f"Ticket {ticket_key} lacks a complete frozen contract",
        )
    expected = {
        "id",
        "node_id",
        "number",
        "title",
        "body",
        "state",
        "state_reason",
        "type",
        "repository",
        "labels",
        "comments",
        "updated_at",
    }
    if set(value) != expected or value["state"] != "open":
        raise PlanControlError(
            "TICKET_CONTRACT_MISSING",
            f"Ticket {ticket_key} lacks its complete open GitHub contract",
        )
    ticket_id = value["id"]
    node_id = value["node_id"]
    number = value["number"]
    title = _text(value["title"], "Ticket title")
    body = _text(value["body"], "Ticket body")
    state_reason = value["state_reason"]
    issue_type = value["type"]
    contract_repository = value["repository"]
    labels = value["labels"]
    comments = value["comments"]
    if (
        type(ticket_id) is not int
        or ticket_id < 1
        or type(node_id) is not str
        or not node_id
        or type(number) is not int
        or number < 1
        or ticket_key != f"issue:{number}"
        or type(value["updated_at"]) is not str
        or not value["updated_at"]
        or
        (state_reason is not None and type(state_reason) is not str)
        or (issue_type is not None and type(issue_type) is not dict)
        or type(contract_repository) is not dict
        or set(contract_repository) != {"full_name", "url"}
        or type(labels) is not list
        or any(type(label) is not dict for label in labels)
        or type(comments) is not list
        or any(type(comment) is not dict for comment in comments)
    ):
        raise PlanControlError(
            "TICKET_CONTRACT_MISSING",
            f"Ticket {ticket_key} has malformed frozen GitHub facts",
        )
    label_names = [label.get("name") for label in labels]
    comment_ids = [comment.get("id") for comment in comments]
    if (
        any(
            type(label.get("id")) is not int
            or label["id"] < 1
            or type(label.get("node_id")) is not str
            or not label["node_id"]
            or type(label.get("url")) is not str
            or not label["url"]
            or type(label.get("color")) is not str
            or type(label.get("default")) is not bool
            or label.get("description") is not None
            and type(label.get("description")) is not str
            or set(label)
            != {"id", "node_id", "url", "name", "color", "default", "description"}
            for label in labels
        )
        or
        any(type(name) is not str or not name for name in label_names)
        or len(set(label_names)) != len(label_names)
        or label_names != sorted(label_names)
        or any(type(comment_id) is not int or comment_id < 1 for comment_id in comment_ids)
        or len(set(comment_ids)) != len(comment_ids)
        or comment_ids != sorted(comment_ids)
        or any(
            type(comment.get("node_id")) is not str
            or not comment["node_id"]
            or type(comment.get("url")) is not str
            or not comment["url"]
            or type(comment.get("html_url")) is not str
            or not comment["html_url"]
            or type(comment.get("body")) is not str
            or type(comment.get("user")) is not dict
            or type(comment["user"].get("login")) is not str
            or not comment["user"]["login"]
            or type(comment.get("created_at")) is not str
            or not comment["created_at"]
            or type(comment.get("updated_at")) is not str
            or not comment["updated_at"]
            or type(comment.get("author_association")) is not str
            or not comment["author_association"]
            or set(comment)
            != {
                "id",
                "node_id",
                "url",
                "html_url",
                "body",
                "user",
                "created_at",
                "updated_at",
                "author_association",
            }
            for comment in comments
        )
    ):
        raise PlanControlError(
            "TICKET_CONTRACT_MISSING",
            f"Ticket {ticket_key} has non-canonical labels or comments",
        )
    normalized_repository = {
        "full_name": _text(
            contract_repository["full_name"],
            "Ticket repository full_name",
        ),
        "url": _text(contract_repository["url"], "Ticket repository URL"),
    }
    if normalized_repository != {
        "full_name": expected_repository,
        "url": f"https://api.github.com/repos/{expected_repository}",
    }:
        raise PlanControlError(
            "TICKET_CONTRACT_MISSING",
            f"Ticket {ticket_key} contract belongs to another repository",
        )
    return {
        "id": ticket_id,
        "node_id": node_id,
        "number": number,
        "title": title,
        "body": body,
        "state": "open",
        "state_reason": state_reason,
        "type": None if issue_type is None else dict(issue_type),
        "repository": normalized_repository,
        "labels": [dict(label) for label in labels],
        "comments": [dict(comment) for comment in comments],
        "updated_at": value["updated_at"],
    }


def _normalize_ticket(value: Any, *, repository: str) -> dict[str, Any]:
    expected = {"key", "labels", "source", "contract", "native_blockers"}
    if type(value) is not dict or set(value) != expected:
        raise PlanControlError("SNAPSHOT_INVALID", "Ticket snapshot schema is invalid")
    key = _text(value["key"], "Ticket key")
    if re.fullmatch(r"issue:[1-9][0-9]*", key) is None:
        raise PlanControlError("SNAPSHOT_INVALID", "Ticket key is not a canonical Issue identity")
    labels = value["labels"]
    if type(labels) is not list or any(type(label) is not str or not label for label in labels) or len(set(labels)) != len(labels) or labels != sorted(labels):
        raise PlanControlError("TICKET_LABEL_INVALID", f"Ticket {key} labels are invalid")
    contract = _normalize_ticket_contract(
        value["contract"],
        ticket_key=key,
        expected_repository=repository,
    )
    blockers = value["native_blockers"]
    if type(blockers) is not list:
        raise PlanControlError("SNAPSHOT_INVALID", "native_blockers must be a list")
    canonical_blockers = []
    for blocker in blockers:
        if type(blocker) is not dict or set(blocker) != {
            "key",
            "state",
            "repository",
            "source",
        }:
            raise PlanControlError("SNAPSHOT_INVALID", "native blocker schema is invalid")
        state = _text(blocker["state"], "native blocker state").lower()
        if state not in {"open", "closed"}:
            raise PlanControlError("SNAPSHOT_INVALID", "native blocker state is invalid")
        blocker_key = _text(blocker["key"], "native blocker key")
        if re.fullmatch(r"issue:[1-9][0-9]*", blocker_key) is None:
            raise PlanControlError(
                "SNAPSHOT_INVALID",
                "native blocker key must be one canonical Issue identity",
            )
        normalized_blocker = {
            "key": blocker_key,
            "state": state,
        }
        blocker_repository = blocker["repository"]
        if (
            type(blocker_repository) is not dict
            or set(blocker_repository) != {"full_name", "url"}
            or blocker_repository["full_name"] != repository
            or blocker_repository["url"]
            != f"https://api.github.com/repos/{repository}"
        ):
            raise PlanControlError(
                "SNAPSHOT_INVALID",
                "native blocker repository identity is invalid",
            )
        source = _frozen_ref(
            blocker["source"],
            "native blocker source",
        )
        source_binding = {
            "key": blocker_key,
            "state": state,
            "repository": {
                "full_name": repository,
                "url": f"https://api.github.com/repos/{repository}",
            },
        }
        if (
            source["ref"] != blocker_key
            or source["digest"] != digest_value(source_binding)
        ):
            raise PlanControlError(
                "SNAPSHOT_INVALID",
                "native blocker source does not bind its complete frozen contract",
            )
        normalized_blocker.update(
            {
                "repository": source_binding["repository"],
                "source": source,
            }
        )
        canonical_blockers.append(normalized_blocker)
    if len({item["key"] for item in canonical_blockers}) != len(canonical_blockers):
        raise PlanControlError("SNAPSHOT_INVALID", "native blockers repeat a Ticket")
    contract_labels = [label["name"] for label in contract["labels"]]
    if labels != contract_labels:
        raise PlanControlError(
            "TICKET_LABEL_INVALID",
            f"Ticket {key} top-level labels differ from its authoritative contract",
        )
    if "ready-for-agent" not in contract_labels or set(contract_labels).intersection(_TRIAGE - {"ready-for-agent"}):
        raise PlanControlError("TICKET_LABEL_INVALID", f"Ticket {key} is not ready-for-agent")
    source = _frozen_ref(value["source"], "Ticket source")
    if source["ref"] != key:
        raise PlanControlError(
            "SNAPSHOT_INVALID",
            "Ticket source does not name its canonical Issue identity",
        )
    projection = _frozen_ticket_contract_projection(
        key=key,
        contract=contract,
        labels=contract_labels,
        native_blockers=sorted(canonical_blockers, key=lambda item: item["key"]),
    )
    if source["digest"] != digest_value(projection):
        raise PlanControlError(
            "SNAPSHOT_INVALID",
            "Ticket source digest does not bind its complete frozen contract",
        )
    return {
        "key": key,
        "labels": contract_labels,
        "source": source,
        "contract": contract,
        "native_blockers": sorted(canonical_blockers, key=lambda item: item["key"]),
    }


def _frozen_ticket_contract_projection(
    *,
    key: str,
    contract: Mapping[str, Any],
    labels: Sequence[str],
    native_blockers: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The one digest domain shared by GitHub capture and PlanControl.

    The source ref is intentionally derived from the canonical Ticket key;
    transport aliases cannot become a second authority projection.
    """

    match = re.fullmatch(r"issue:([1-9][0-9]*)", key)
    if match is None:
        raise PlanControlError("SNAPSHOT_INVALID", "Ticket key is not canonical")
    return {
        "number": int(match.group(1)),
        "contract": dict(contract),
        "labels": list(labels),
        "source_ref": key,
        "native_blockers": [dict(item) for item in native_blockers],
    }


def frozen_ticket_contract_digest(
    *,
    key: str,
    contract: Mapping[str, Any],
    labels: Sequence[str],
    native_blockers: Sequence[Mapping[str, Any]],
) -> str:
    """Canonical Ticket source identity for the GitHub capture boundary."""

    return digest_value(
        _frozen_ticket_contract_projection(
            key=key,
            contract=contract,
            labels=labels,
            native_blockers=native_blockers,
        )
    )


def _normalize_snapshot(
    value: Any,
    repository: str,
    refs: tuple[str, ...],
    *,
    allow_open_external_blockers: bool = False,
) -> dict[str, Any]:
    value = _canonical(value, code="SNAPSHOT_INVALID")
    expected = {"repository", "target_branch", "campaign_source", "policy", "tickets"}
    if type(value) is not dict or set(value) != expected or value["repository"] != repository or type(value["tickets"]) is not list:
        raise PlanControlError("SNAPSHOT_INVALID", "Campaign snapshot schema or repository is invalid")
    tickets = sorted(
        (
            _normalize_ticket(ticket, repository=repository)
            for ticket in value["tickets"]
        ),
        key=lambda item: item["key"],
    )
    keys = tuple(ticket["key"] for ticket in tickets)
    source_refs = tuple(sorted(ticket["source"]["ref"] for ticket in tickets))
    if (
        not keys
        or len(set(keys)) != len(keys)
        or len(set(source_refs)) != len(source_refs)
        or source_refs != refs
    ):
        raise PlanControlError(
            "SNAPSHOT_OMISSION",
            "Snapshot sources must cover every requested ready reference exactly once",
        )
    selected = set(keys)
    external = sorted({blocker["key"] for ticket in tickets for blocker in ticket["native_blockers"] if blocker["state"] == "open" and blocker["key"] not in selected})
    if external and not allow_open_external_blockers:
        raise PlanControlError("EXTERNAL_BLOCKER_OPEN", "Selected Tickets have open external blockers: " + ", ".join(external))
    dependencies = {ticket["key"]: {blocker["key"] for blocker in ticket["native_blockers"] if blocker["state"] == "open" and blocker["key"] in selected} for ticket in tickets}
    _assert_acyclic(dependencies)
    normalized = {
        "schema_version": 1,
        "repository": repository,
        "target_branch": _text(value["target_branch"], "target_branch"),
        "campaign_source": _campaign_source(
            value["campaign_source"],
            repository,
        ),
        "policy": _normalize_policy(value["policy"]),
        "tickets": tickets,
    }
    if allow_open_external_blockers:
        normalized["external_dependencies"] = sorted(
            (
                {
                    "key": blocker["key"],
                    "state": blocker["state"],
                    "repository": blocker["repository"],
                    "source": blocker["source"],
                }
                for ticket in tickets
                for blocker in ticket["native_blockers"]
                if blocker["key"] not in selected
            ),
            key=lambda item: (item["key"], item["state"]),
        )
        # A blocker can be referenced by more than one approved Ticket; the
        # external dependency itself is one bounded fact in the Coordinator
        # input, not an implicit Campaign member.
        unique: dict[str, dict[str, Any]] = {}
        for blocker in normalized["external_dependencies"]:
            existing = unique.get(blocker["key"])
            if existing is not None and existing != blocker:
                raise PlanControlError(
                    "SNAPSHOT_INVALID",
                    "external blocker readback changed its canonical identity",
                )
            unique[blocker["key"]] = blocker
        normalized["external_dependencies"] = [
            unique[key] for key in sorted(unique)
        ]
    return normalized


_INVALIDATION_OBSERVATION_FIELDS = {
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
_REPLANNING_RUN_FIELDS = {
    "ticket_key",
    "work_run_key",
    "phase",
    "slot_held",
    "reason",
    "next_check_at",
    "runtime_binding_id",
    "claim_state",
    "exclusive_resources",
}
_REPLANNING_CLAIM_FIELDS = {
    "ticket_key",
    "repository",
    "campaign_key",
    "plan_revision_digest",
}


def _normalize_invalidation_observations(
    values: Sequence[object],
    *,
    handle: CampaignHandle,
    active_revision_digest: str,
    plan: Mapping[str, Any],
    execution_snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if type(values) is not tuple and type(values) is not list:
        raise PlanControlError(
            "PLAN_INVALIDATION_SNAPSHOT_INVALID",
            "pending invalidation Evidence must be one bounded sequence",
        )
    runs = execution_snapshot.get("runs") if type(execution_snapshot) is dict else None
    if type(runs) is not list:
        raise PlanControlError(
            "PLAN_INVALIDATION_SNAPSHOT_INVALID",
            "ExecutionKernel omitted the complete Work Run readback",
        )
    run_by_key: dict[str, Mapping[str, Any]] = {}
    for run in runs:
        if type(run) is not dict or set(run) != _REPLANNING_RUN_FIELDS:
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "Work Run readback contains unsupported or missing fields",
            )
        ticket_key = _text(run["ticket_key"], "Work Run Ticket key")
        if ticket_key in run_by_key:
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "Work Run readback repeats a Ticket",
            )
        run_by_key[ticket_key] = run
    work = plan.get("work")
    if type(work) is not list:
        raise PlanControlError(
            "PLAN_INVALIDATION_SNAPSHOT_INVALID",
            "active PlanSpec omitted its work manifest",
        )
    work_by_key = {item.get("key"): item for item in work if type(item) is dict}
    result: list[dict[str, Any]] = []
    for value in values:
        raw_value = value.canonical() if callable(getattr(value, "canonical", None)) else value
        if type(raw_value) is not dict:
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "pending invalidation is not a typed canonical observation",
            )
        if set(raw_value) == _INVALIDATION_OBSERVATION_FIELDS | {"observation_digest"}:
            expected_observation_digest = raw_value.get("observation_digest")
            raw_value = {
                key: raw_value[key] for key in _INVALIDATION_OBSERVATION_FIELDS
            }
            if expected_observation_digest != digest_value(raw_value):
                raise PlanControlError(
                    "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                    "pending invalidation observation digest changed",
                )
        if set(raw_value) != _INVALIDATION_OBSERVATION_FIELDS or raw_value.get("kind") != "plan_invalidation_observation.v1":
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "pending invalidation observation schema is not closed",
            )
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
            _text(raw_value[field_name], f"Plan Invalidation {field_name}")
        for field_name in (
            "plan_revision_digest",
            "authority_subtree_digest",
            "report_digest",
            "evidence_digest",
        ):
            _digest(raw_value[field_name], f"Plan Invalidation {field_name}")
        effects = raw_value["required_effects"]
        if type(effects) is not list or not effects or any(
            type(effect) is not str or not effect for effect in effects
        ) or len(set(effects)) != len(effects):
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "pending invalidation effects are not canonical",
            )
        if raw_value["reporter_role"] not in {"worker", "recovery_worker", "review"}:
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "pending invalidation reporter role is outside the closed union",
            )
        if (
            raw_value["repository"] != handle.repository
            or raw_value["campaign_key"] != handle.campaign_key
            or raw_value["plan_revision_digest"] != active_revision_digest
        ):
            raise PlanControlError(
                "PLAN_INVALIDATION_IDENTITY_MISMATCH",
                "pending invalidation is not bound to the active Campaign revision",
            )
        ticket_key = raw_value["ticket_key"]
        work_item = work_by_key.get(ticket_key)
        run = run_by_key.get(ticket_key)
        if work_item is None or run is None or run["work_run_key"] != raw_value["work_run_key"]:
            raise PlanControlError(
                "PLAN_INVALIDATION_IDENTITY_MISMATCH",
                "pending invalidation names an unknown Work Run",
            )
        expected_role = _replanning_reporter_role(work_item)
        authority = work_item.get("authority")
        role_authority = authority.get(expected_role) if type(authority) is dict else None
        if (
            raw_value["reporter_role"] != expected_role
            or type(role_authority) is not dict
            or role_authority.get("subtree_digest")
            != raw_value["authority_subtree_digest"]
            or run.get("runtime_binding_id") != raw_value["runtime_binding_id"]
        ):
            raise PlanControlError(
                "PLAN_INVALIDATION_IDENTITY_MISMATCH",
                "pending invalidation is not bound to the Work Run authority and Runtime Binding",
            )
        result.append({**raw_value, "required_effects": list(effects)})
    if not result:
        raise PlanControlError(
            "PLAN_INVALIDATION_SNAPSHOT_INVALID",
            "Campaign invalidation classification requires pending Evidence",
        )
    return sorted(result, key=lambda item: digest_value(item))


def _replanning_reporter_role(work_item: Mapping[str, Any]) -> str:
    explicit = work_item.get("reporter_role")
    if type(explicit) is str and explicit in {"worker", "recovery_worker", "review"}:
        return explicit
    purpose = work_item.get("purpose")
    if type(purpose) is dict:
        purpose = purpose.get("kind")
    contract = work_item.get("contract")
    if purpose is None and type(contract) is dict:
        purpose = contract.get("purpose")
        if type(purpose) is dict:
            purpose = purpose.get("kind")
    if purpose in {"formal_review", "invalid_review_payload_retry", "specialist_review", "review"}:
        return "review"
    if purpose in {"terminal_recovery_implementation", "recovery_worker"}:
        return "recovery_worker"
    return "worker"


def _normalize_replanning_execution_snapshot(
    value: Mapping[str, Any],
    *,
    ticket_keys: set[str],
    active_revision_digest: str,
) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "runs",
        "claims",
        "accepted_results",
    }:
        raise PlanControlError(
            "PLAN_INVALIDATION_SNAPSHOT_INVALID",
            "ExecutionKernel snapshot schema is not closed",
        )
    runs = value["runs"]
    if type(runs) is not list:
        raise PlanControlError(
            "PLAN_INVALIDATION_SNAPSHOT_INVALID",
            "ExecutionKernel Work Runs are not a list",
        )
    normalized_runs: list[dict[str, Any]] = []
    seen: set[str] = set()
    phases = {
        "pending",
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
    claim_states = {"unclaimed", "held", "released"}
    for run in runs:
        if type(run) is not dict or set(run) != _REPLANNING_RUN_FIELDS:
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "ExecutionKernel Work Run contains unsupported fields",
            )
        key = _text(run["ticket_key"], "Execution Work Run Ticket key")
        if key not in ticket_keys or key in seen:
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "ExecutionKernel Work Run set is not the complete selected set",
            )
        seen.add(key)
        if (
            type(run["work_run_key"]) is not str
            or not run["work_run_key"]
            or run["phase"] not in phases
            or type(run["slot_held"]) is not bool
            or (run["reason"] is not None and type(run["reason"]) is not str)
            or (run["next_check_at"] is not None and type(run["next_check_at"]) is not str)
            or (run["runtime_binding_id"] is not None and type(run["runtime_binding_id"]) is not str)
            or run["claim_state"] not in claim_states
            or type(run["exclusive_resources"]) is not list
            or any(type(item) is not str or not item for item in run["exclusive_resources"])
            or run["exclusive_resources"] != sorted(set(run["exclusive_resources"]))
        ):
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "ExecutionKernel Work Run facts are malformed",
            )
        normalized_runs.append(
            {
                **run,
                "exclusive_resources": list(run["exclusive_resources"]),
            }
        )
    if seen != ticket_keys:
        raise PlanControlError(
            "PLAN_INVALIDATION_SNAPSHOT_INVALID",
            "ExecutionKernel omitted an approved Ticket Work Run",
        )
    claims = value["claims"]
    if type(claims) is not list:
        raise PlanControlError(
            "PLAN_INVALIDATION_SNAPSHOT_INVALID",
            "ExecutionKernel claims are not a list",
        )
    normalized_claims: list[dict[str, str]] = []
    claim_keys: set[str] = set()
    for claim in claims:
        if type(claim) is not dict or set(claim) != _REPLANNING_CLAIM_FIELDS:
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "ExecutionKernel claim schema is not closed",
            )
        key = _text(claim["ticket_key"], "Execution claim Ticket key")
        if key not in ticket_keys or key in claim_keys:
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "ExecutionKernel claims are incomplete or repeated",
            )
        claim_keys.add(key)
        if claim["plan_revision_digest"] != active_revision_digest:
            raise PlanControlError(
                "PLAN_INVALIDATION_IDENTITY_MISMATCH",
                "ExecutionKernel claim belongs to another Plan Revision",
            )
        normalized_claims.append(dict(claim))
    if claim_keys != ticket_keys:
        raise PlanControlError(
            "PLAN_INVALIDATION_SNAPSHOT_INVALID",
            "ExecutionKernel omitted an approved Ticket claim",
        )
    results = value["accepted_results"]
    if type(results) is not list:
        raise PlanControlError(
            "PLAN_INVALIDATION_SNAPSHOT_INVALID",
            "accepted Results are not a list",
        )
    normalized_results: list[dict[str, Any]] = []
    for result in results:
        if type(result) is not dict:
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "accepted Result schema is not closed",
            )
        if set(result) == {"ticket_key", "result_digest"}:
            if result["ticket_key"] not in ticket_keys:
                raise PlanControlError(
                    "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                    "accepted Result names an unapproved Ticket",
                )
            _digest(result["result_digest"], "accepted Result digest")
            normalized_results.append(dict(result))
            continue
        if set(result) != {
            "kind",
            "ticket_key",
            "result_digest",
            "evidence_digests",
            "work_subject_digest",
            "target_facts_digest",
        }:
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "accepted Result schema is not closed",
            )
        try:
            binding = AcceptedResultBinding.from_canonical(result)
        except (KeyError, TypeError, ValueError) as error:
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "accepted Result binding identities are malformed",
            ) from error
        if binding.ticket_key not in ticket_keys:
            raise PlanControlError(
                "PLAN_INVALIDATION_SNAPSHOT_INVALID",
                "accepted Result names an unapproved Ticket",
            )
        normalized_results.append(binding.canonical())
    return {
        "runs": sorted(normalized_runs, key=lambda item: item["ticket_key"]),
        "claims": sorted(normalized_claims, key=lambda item: item["ticket_key"]),
        "accepted_results": sorted(normalized_results, key=lambda item: item["ticket_key"]),
    }


def _build_replanning_snapshot(
    control: PlanControl,
    *,
    active: ActivePlanReadback,
    plan: Mapping[str, Any],
    pending: list[dict[str, Any]],
    execution_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    source = _normalize_snapshot(
        control._source.snapshot(
            active.handle.repository,
            active.activation_receipt.ready_refs,
        ),
        active.handle.repository,
        active.activation_receipt.ready_refs,
        allow_open_external_blockers=True,
    )
    work = plan.get("work")
    if type(work) is not list:
        raise PlanControlError(
            "PLAN_INVALIDATION_SNAPSHOT_INVALID",
            "active PlanSpec work manifest is missing",
        )
    plan_work = {item["key"]: item for item in work if type(item) is dict and type(item.get("key")) is str}
    source_keys = {ticket["key"] for ticket in source["tickets"]}
    if source_keys != set(plan_work) or tuple(sorted(source_keys)) != active.activation_receipt.ticket_keys:
        raise PlanControlError(
            "REPLAN_SOURCE_CHANGED",
            "authoritative Ticket source changed its approved Campaign membership",
        )
    for ticket in source["tickets"]:
        item = plan_work[ticket["key"]]
        if ticket["source"] != item["source"] or ticket["contract"] != item["contract"]:
            raise PlanControlError(
                "REPLAN_SOURCE_CHANGED",
                "authoritative Ticket contract or source digest changed under the active Plan Revision",
            )
    if source["policy"]["digest"] != plan["policy"].get("digest"):
        raise PlanControlError(
            "REPLAN_POLICY_CHANGED",
            "Policy Witness changed under the active Plan Revision",
        )
    ticket_keys = set(plan_work)
    execution = _normalize_replanning_execution_snapshot(
        execution_snapshot,
        ticket_keys=ticket_keys,
        active_revision_digest=active.current_revision_digest,
    )
    expected_claims = sorted(
        (
            {
                "ticket_key": proof.ticket_key,
                "repository": proof.repository,
                "campaign_key": proof.campaign_key,
                "plan_revision_digest": proof.plan_revision_digest,
            }
            for proof in active.claim_proofs
        ),
        key=lambda item: item["ticket_key"],
    )
    if execution["claims"] != expected_claims:
        raise PlanControlError(
            "TICKET_CLAIM_READBACK_INVALID",
            "ExecutionKernel claims do not exactly bind the active Campaign",
        )
    native_graph = [
        {
            "ticket_key": ticket["key"],
            "blockers": ticket["native_blockers"],
        }
        for ticket in source["tickets"]
    ]
    approved_edges = {
        (blocker["key"], ticket["key"])
        for ticket in source["tickets"]
        for blocker in ticket["native_blockers"]
        if blocker["state"] == "open" and blocker["key"] in ticket_keys
    }
    approved_edges.update(
        (dependency, item["key"])
        for item in work
        if type(item) is dict
        for dependency in item.get("depends_on", [])
        if dependency in ticket_keys
    )
    policy_witness = source["policy"]
    return {
        "schema_version": "gwo.plan.invalidation-snapshot.v1",
        "repository": active.handle.repository,
        "campaign_key": active.handle.campaign_key,
        "target_branch": source["target_branch"],
        "campaign_source": source["campaign_source"],
        "active_plan_revision": {
            "digest": active.current_revision_digest,
            "plan_spec": dict(plan),
            "expected_previous_revision_digest": active.activation_receipt.expected_previous_revision_digest,
        },
        "tickets": source["tickets"],
        "native_blocker_graph": native_graph,
        "external_dependencies": source.get("external_dependencies", []),
        "work_runs": execution["runs"],
        "claims": execution["claims"],
        "accepted_results": execution["accepted_results"],
        "pending_invalidations": pending,
        "approved_dependency_edges": [
            {"from": source, "to": target}
            for source, target in sorted(approved_edges)
        ],
        "policy_witness": policy_witness,
    }


def _validate_coordinator_capability(
    proof: object,
    subject: CampaignPlanningSubject,
) -> CoordinatorCapabilityProof:
    if (
        type(proof) is not CoordinatorCapabilityProof
        or proof.subject_digest != subject.digest
        or not proof.is_proven
    ):
        raise PlanControlError(
            "REPLAN_CAPABILITY_PROOF_FAIL_CLOSED",
            "Coordinator Runtime readback did not prove read-only, non-delegating authority",
        )
    return proof


def _normalize_replanning_intent(
    value: Any,
    *,
    snapshot: Mapping[str, Any],
    action_id: str,
    snapshot_digest: str,
    plan_revision_digest: str,
    evidence_digests: tuple[str, ...],
    capability_proof_digest: str,
) -> PlanInvalidationClassification:
    value = _canonical(value, code="PLAN_INVALIDATION_CLASSIFICATION_INVALID")
    expected = {
        "evidence_digests",
        "disposition",
        "reason",
        "successor",
        "decision",
    }
    if type(value) is not dict or set(value) != expected:
        raise PlanControlError(
            "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
            "Coordinator invalidation output contains unsupported fields",
        )
    output_evidence = value["evidence_digests"]
    if (
        type(output_evidence) is not list
        or any(
            type(item) is not str or _DIGEST.fullmatch(item) is None
            for item in output_evidence
        )
        or tuple(sorted(set(output_evidence))) != tuple(output_evidence)
        or tuple(output_evidence) != evidence_digests
    ):
        raise PlanControlError(
            "PLAN_INVALIDATION_EVIDENCE_OMISSION",
            "Coordinator output must account for every pending invalidation Evidence exactly once",
        )
    try:
        disposition = PlanInvalidationDisposition(value["disposition"])
    except (TypeError, ValueError) as error:
        raise PlanControlError(
            "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
            "Coordinator disposition is outside the legal union",
        ) from error
    reason = _text(value["reason"], "classification reason")
    successor = value["successor"]
    decision = value["decision"]
    ticket_keys = {
        ticket["key"] for ticket in snapshot["tickets"]
    }
    successor_keys: tuple[str, ...] = ()
    dependencies: tuple[PlanInvalidationDependency, ...] = ()
    resources: tuple[PlanInvalidationExclusiveResource, ...] = ()
    if successor is not None:
        if type(successor) is not dict or set(successor) != {
            "approved_ticket_keys",
            "dependency_additions",
            "exclusive_resource_additions",
        }:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "Coordinator successor output is not closed",
            )
        raw_keys = successor["approved_ticket_keys"]
        if (
            type(raw_keys) is not list
            or not raw_keys
            or any(type(item) is not str or not item for item in raw_keys)
            or tuple(sorted(set(raw_keys))) != tuple(raw_keys)
            or not set(raw_keys).issubset(ticket_keys)
        ):
            raise PlanControlError(
                "PLAN_INVALIDATION_TICKET_INVALID",
                "Coordinator successor names unapproved or non-canonical Ticket work",
            )
        successor_keys = tuple(raw_keys)
        raw_dependencies = successor["dependency_additions"]
        if type(raw_dependencies) is not list or any(
            type(item) is not dict or set(item) != {"from", "to", "reason"}
            for item in raw_dependencies
        ):
            raise PlanControlError(
                "PLAN_INVALIDATION_DEPENDENCY_INVALID",
                "Coordinator successor dependencies are not a list",
            )
        dependencies = tuple(
            PlanInvalidationDependency(
                from_ticket=item["from"],
                to_ticket=item["to"],
                reason=item["reason"],
            )
            for item in raw_dependencies
        )
        if len({(item.from_ticket, item.to_ticket) for item in dependencies}) != len(dependencies):
            raise PlanControlError(
                "PLAN_INVALIDATION_DEPENDENCY_INVALID",
                "Coordinator successor dependencies repeat an edge",
            )
        raw_resources = successor["exclusive_resource_additions"]
        if type(raw_resources) is not list or any(
            type(item) is not dict
            or set(item) != {"ticket_key", "resource_id", "reason"}
            for item in raw_resources
        ):
            raise PlanControlError(
                "PLAN_INVALIDATION_RESOURCE_INVALID",
                "Coordinator successor Exclusive Resources are not a closed list",
            )
        resources = tuple(
            PlanInvalidationExclusiveResource(
                ticket_key=item["ticket_key"],
                resource_id=item["resource_id"],
                reason=item["reason"],
            )
            for item in raw_resources
        )
        if any(item.ticket_key not in ticket_keys for item in resources):
            raise PlanControlError(
                "PLAN_INVALIDATION_TICKET_INVALID",
                "Coordinator successor Exclusive Resource names an unapproved Ticket",
            )
        if len({(item.ticket_key, item.resource_id) for item in resources}) != len(resources):
            raise PlanControlError(
                "PLAN_INVALIDATION_RESOURCE_INVALID",
                "Coordinator successor Exclusive Resources repeat a resource",
            )
    if disposition is PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR:
        if successor is None or decision is not None:
            raise PlanControlError(
                "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
                "approved successor disposition requires only approved successor facts",
            )
    elif successor is not None:
        raise PlanControlError(
            "PLAN_INVALIDATION_CLASSIFICATION_INVALID",
            "resume/defer/Decision output cannot silently carry successor work",
        )
    decision_value: PlanInvalidationDecision | None = None
    if decision is not None:
        if type(decision) is not dict or set(decision) != {
            "code",
            "detail",
            "required_change",
        }:
            raise PlanControlError(
                "PLAN_INVALIDATION_DECISION_INVALID",
                "Coordinator Decision output is not closed",
            )
        decision_value = PlanInvalidationDecision(**decision)
    if disposition is PlanInvalidationDisposition.REQUIRE_HUMAN_DECISION:
        if decision_value is None or successor is not None:
            raise PlanControlError(
                "PLAN_INVALIDATION_DECISION_INVALID",
                "human Decision disposition requires one named Decision only",
            )
    elif decision_value is not None:
        raise PlanControlError(
            "PLAN_INVALIDATION_DECISION_INVALID",
            "non-Decision disposition cannot carry a human Decision",
        )
    return PlanInvalidationClassification(
        action_id=action_id,
        snapshot_digest=snapshot_digest,
        plan_revision_digest=plan_revision_digest,
        evidence_digests=evidence_digests,
        disposition=disposition,
        reason=reason,
        capability_proof_digest=capability_proof_digest,
        successor_ticket_keys=successor_keys,
        dependency_additions=tuple(
            sorted(
                dependencies,
                key=lambda item: (item.from_ticket, item.to_ticket, item.reason),
            )
        ),
        exclusive_resource_additions=tuple(
            sorted(
                resources,
                key=lambda item: (item.ticket_key, item.resource_id, item.reason),
            )
        ),
        decision=decision_value,
    )


def _validate_classification_binding(
    classification: object,
    *,
    action_id: str,
    snapshot_digest: str,
    plan_revision_digest: str,
    evidence_digests: tuple[str, ...],
    snapshot: Mapping[str, Any],
) -> PlanInvalidationClassification:
    if type(classification) is not PlanInvalidationClassification:
        raise PlanControlError(
            "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
            "persisted invalidation classification is not typed",
        )
    if (
        classification.action_id != action_id
        or classification.snapshot_digest != snapshot_digest
        or classification.plan_revision_digest != plan_revision_digest
        or classification.evidence_digests != evidence_digests
    ):
        raise PlanControlError(
            "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID",
            "persisted invalidation classification is bound to another snapshot",
        )
    if classification.disposition is PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR:
        allowed = {ticket["key"] for ticket in snapshot["tickets"]}
        if not set(classification.successor_ticket_keys).issubset(allowed):
            raise PlanControlError(
                "PLAN_INVALIDATION_TICKET_INVALID",
                "persisted successor names an unapproved Ticket",
            )
        if any(
            item.ticket_key not in allowed
            for item in classification.exclusive_resource_additions
        ):
            raise PlanControlError(
                "PLAN_INVALIDATION_TICKET_INVALID",
                "persisted successor Exclusive Resource names an unapproved Ticket",
            )
    return classification


def _assert_acyclic(dependencies: Mapping[str, set[str]]) -> None:
    """Iteratively validate one bounded closed Ticket dependency graph."""

    if (
        len(dependencies) > _MAX_DEPENDENCY_NODES
        or any(type(key) is not str or not key for key in dependencies)
        or any(
            type(values) is not set
            or any(type(item) is not str or not item for item in values)
            for values in dependencies.values()
        )
    ):
        raise PlanControlError(
            "DEPENDENCY_STRUCTURE_LIMIT",
            "Ticket dependency graph exceeds its bounded structural contract",
        )
    edge_count = sum(len(values) for values in dependencies.values())
    if edge_count > _MAX_DEPENDENCY_EDGES:
        raise PlanControlError(
            "DEPENDENCY_STRUCTURE_LIMIT",
            "Ticket dependency graph has too many edges",
        )
    keys = set(dependencies)
    if any(not values.issubset(keys) for values in dependencies.values()):
        raise PlanControlError(
            "DEPENDENCY_INVALID",
            "Ticket dependency graph names unselected work",
        )
    state: dict[str, int] = {}
    for root in sorted(keys):
        if state.get(root, 0) == 2:
            continue
        state[root] = 1
        stack: list[tuple[str, Any]] = [
            (root, iter(sorted(dependencies[root])))
        ]
        while stack:
            key, iterator = stack[-1]
            try:
                dependency = next(iterator)
            except StopIteration:
                state[key] = 2
                stack.pop()
                continue
            dependency_state = state.get(dependency, 0)
            if dependency_state == 1:
                raise PlanControlError(
                    "DEPENDENCY_CYCLE",
                    "Ticket dependencies contain a cycle",
                )
            if dependency_state == 2:
                continue
            state[dependency] = 1
            stack.append((dependency, iter(sorted(dependencies[dependency]))))


def _facts_by_ticket(value: Any, selected: set[str], label: str) -> dict[str, list[str]]:
    if (
        type(value) is not dict
        or any(type(key) is not str or not key for key in value)
        or set(value) != selected
    ):
        raise PlanControlError(
            "PLAN_INTENT_OMISSION",
            f"{label} must account for every selected Ticket exactly once",
        )
    result = {}
    for key in selected:
        facts = value[key]
        if type(facts) is not list or any(type(item) is not str or not item for item in facts) or len(set(facts)) != len(facts):
            raise PlanControlError("PLAN_INTENT_INVALID", f"{label} facts are invalid")
        result[key] = sorted(facts)
    return result


def _intent_text(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise PlanControlError(
            "PLAN_INTENT_INVALID",
            f"{label} must be non-empty exact text",
        )
    return value


def _normalize_intent(value: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    value = _canonical(value, code="PLAN_INTENT_INVALID")
    expected = {"admitted_work", "dependency_additions", "exclusive_resources", "capability_requirements", "decision_requirements"}
    if type(value) is not dict or set(value) != expected:
        raise PlanControlError("PLAN_INTENT_INVALID", "Planning output contains unsupported fields")
    selected = {ticket["key"] for ticket in snapshot["tickets"]}
    admitted = value["admitted_work"]
    if (
        type(admitted) is not list
        or any(type(item) is not str or not item for item in admitted)
        or set(admitted) != selected
        or len(admitted) != len(selected)
    ):
        raise PlanControlError("PLAN_INTENT_OMISSION", "Planning output must account for every selected Ticket")
    dependencies = {ticket["key"]: {blocker["key"] for blocker in ticket["native_blockers"] if blocker["state"] == "open" and blocker["key"] in selected} for ticket in snapshot["tickets"]}
    additions = []
    if type(value["dependency_additions"]) is not list:
        raise PlanControlError("PLAN_INTENT_INVALID", "dependency_additions must be a list")
    for item in value["dependency_additions"]:
        if type(item) is not dict or set(item) != {"from", "to", "reason"}:
            raise PlanControlError("PLAN_INTENT_INVALID", "dependency addition is invalid")
        source, target = (
            _intent_text(item["from"], "dependency source"),
            _intent_text(item["to"], "dependency target"),
        )
        if source not in selected or target not in selected or source == target:
            raise PlanControlError("PLAN_INTENT_INVALID", "dependency addition leaves the selected Ticket set")
        dependencies[source].add(target)
        additions.append(
            {
                "from": source,
                "to": target,
                "reason": _intent_text(item["reason"], "dependency reason"),
            }
        )
    if len({(item["from"], item["to"]) for item in additions}) != len(additions):
        raise PlanControlError("PLAN_INTENT_INVALID", "dependency additions repeat an edge")
    _assert_acyclic(dependencies)
    exclusive = _facts_by_ticket(value["exclusive_resources"], selected, "exclusive_resources")
    capabilities = _facts_by_ticket(value["capability_requirements"], selected, "capability_requirements")
    policy = snapshot["policy"]
    if any(fact not in policy["exclusive_resources"] for facts in exclusive.values() for fact in facts):
        raise PlanControlError("EXCLUSIVE_RESOURCE_INVALID", "Planning output names an unknown Exclusive Resource")
    if any(fact not in policy["allowed_capabilities"] for facts in capabilities.values() for fact in facts):
        raise PlanControlError("CAPABILITY_INVALID", "Planning output names an unknown factual capability")
    if type(value["decision_requirements"]) is not list:
        raise PlanControlError("PLAN_INTENT_INVALID", "decision_requirements must be a list")
    findings = []
    for finding in value["decision_requirements"]:
        if type(finding) is not dict or set(finding) not in ({"code", "detail"}, {"code", "detail", "ticket_key"}):
            raise PlanControlError("PLAN_INTENT_INVALID", "Decision finding is invalid")
        ticket_key = finding.get("ticket_key")
        if ticket_key is not None and (
            type(ticket_key) is not str
            or not ticket_key
            or ticket_key not in selected
        ):
            raise PlanControlError("PLAN_INTENT_INVALID", "Decision finding names unselected work")
        findings.append(
            {
                "code": _intent_text(finding["code"], "Decision code"),
                "detail": _intent_text(finding["detail"], "Decision detail"),
                "ticket_key": ticket_key,
            }
        )
    if len({(item["code"], item["detail"], item["ticket_key"]) for item in findings}) != len(findings):
        raise PlanControlError("PLAN_INTENT_INVALID", "Decision findings repeat")
    return {
        "admitted_work": sorted(admitted),
        "dependency_additions": sorted(additions, key=lambda item: (item["from"], item["to"], item["reason"])),
        "exclusive_resources": {key: exclusive[key] for key in sorted(exclusive)},
        "capability_requirements": {key: capabilities[key] for key in sorted(capabilities)},
        "decision_requirements": sorted(findings, key=lambda item: (item["code"], item["ticket_key"] or "", item["detail"])),
    }


def _snapshot_from_bytes(payload: bytes) -> dict[str, Any]:
    try:
        value = load_canonical_json(payload)
    except CanonicalJsonError as error:
        raise PlanControlError("SNAPSHOT_READBACK_INVALID", "Snapshot Artifact bytes are not canonical") from error
    if type(value) is not dict:
        raise PlanControlError("SNAPSHOT_READBACK_INVALID", "Snapshot Artifact is not an object")
    return value


def _intent_from_bytes(payload: bytes, snapshot_bytes: bytes) -> dict[str, Any]:
    try:
        value = load_canonical_json(payload)
    except CanonicalJsonError as error:
        raise PlanControlError("PLAN_INTENT_READBACK_INVALID", "Plan Intent bytes are not canonical") from error
    return _normalize_intent(value, _snapshot_from_bytes(snapshot_bytes))


def _authority(policy_digest: str, grants: list[dict[str, str]]) -> dict[str, Any]:
    core = {"policy_witness_digest": policy_digest, "grants": grants}
    return {**core, "subtree_digest": digest_value(core)}


def _compile_plan(snapshot_bytes: bytes, snapshot_digest: str, intent_bytes: bytes, handle: CampaignHandle) -> PlanRevision:
    if digest_bytes(snapshot_bytes) != snapshot_digest:
        raise PlanControlError("SNAPSHOT_DIGEST_MISMATCH", "Frozen snapshot digest changed before compilation")
    snapshot = _snapshot_from_bytes(snapshot_bytes)
    intent = _intent_from_bytes(intent_bytes, snapshot_bytes)
    if canonical_bytes(intent) != intent_bytes:
        raise PlanControlError("PLAN_INTENT_READBACK_INVALID", "Validated Plan Intent changed before compilation")
    policy = snapshot["policy"]
    selected = {ticket["key"] for ticket in snapshot["tickets"]}
    dependencies = {ticket["key"]: {blocker["key"] for blocker in ticket["native_blockers"] if blocker["state"] == "open" and blocker["key"] in selected} for ticket in snapshot["tickets"]}
    for addition in intent["dependency_additions"]:
        dependencies[addition["from"]].add(addition["to"])
    work = []
    for ticket in snapshot["tickets"]:
        key = ticket["key"]
        work.append(
            {
                "key": key,
                "source": ticket["source"],
                "contract": ticket["contract"],
                "depends_on": sorted(dependencies[key]),
                "exclusive_resources": intent["exclusive_resources"][key],
                "capabilities": intent["capability_requirements"][key],
                "authority": {
                    "policy_witness_digest": policy["digest"],
                    "worker": _authority(policy["digest"], policy["authority_grants"]["worker"]),
                    "recovery_worker": _authority(policy["digest"], policy["authority_grants"]["recovery_worker"]),
                    "review": _authority(policy["digest"], policy["authority_grants"]["review"]),
                },
            }
        )
    plan = {
        "schema_version": 3,
        "repository": handle.repository,
        "target_branch": snapshot["target_branch"],
        "campaign": {"key": handle.campaign_key, "source": snapshot["campaign_source"], "authority": _authority(policy["digest"], policy["authority_grants"]["campaign"])},
        "policy": {"ref": policy["ref"], "digest": policy["digest"]},
        "work": work,
    }
    payload = canonical_bytes(plan)
    _validate_plan_spec(payload)
    return PlanRevision(handle.repository, handle.campaign_key, snapshot_digest, payload, digest_bytes(payload))


def _validate_revision_provenance(
    candidate: object,
    expected: PlanRevision,
) -> None:
    if (
        type(candidate) is not PlanRevision
        or candidate != expected
        or candidate.repository != expected.repository
        or candidate.campaign_key != expected.campaign_key
        or candidate.snapshot_digest != expected.snapshot_digest
        or digest_bytes(candidate.canonical_bytes) != candidate.digest
    ):
        raise PlanControlError(
            "PLAN_REVISION_PROVENANCE_INVALID",
            "Persisted Plan Revision is not the exact deterministic compilation result",
        )
    _validate_plan_spec(candidate.canonical_bytes)


def _validate_plan_spec(payload: bytes) -> None:
    try:
        plan = load_canonical_json(payload)
    except CanonicalJsonError as error:
        raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec is not canonical") from error
    if type(plan) is not dict or set(plan) != {"schema_version", "repository", "target_branch", "campaign", "policy", "work"} or plan["schema_version"] != 3:
        raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec v3 top-level schema is invalid")
    campaign = plan["campaign"]
    policy = plan["policy"]
    if (
        type(campaign) is not dict
        or set(campaign) != {"key", "source", "authority"}
        or _campaign_source(campaign["source"], plan["repository"])
        != campaign["source"]
        or type(policy) is not dict
        or set(policy) != {"ref", "digest"}
        or _text(policy["ref"], "PlanSpec policy ref") != policy["ref"]
        or _digest(policy["digest"], "PlanSpec policy digest") != policy["digest"]
    ):
        raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec Campaign or Policy projection is invalid")
    if type(plan["work"]) is not list or not plan["work"]:
        raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec v3 has no work manifest")
    forbidden = {"provider", "model", "cli", "profile", "session", "binding", "capacity", "permission", "check", "review", "recovery", "integration"}
    if any(forbidden.intersection(item) for item in plan["work"] if type(item) is dict):
        raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec contains a Runtime or lifecycle field")
    keys = [item.get("key") for item in plan["work"] if type(item) is dict]
    if keys != sorted(set(keys)):
        raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec work manifests are not canonically unique")
    dependencies: dict[str, set[str]] = {}
    for item in plan["work"]:
        expected = {"key", "source", "contract", "depends_on", "exclusive_resources", "capabilities", "authority"}
        if type(item) is not dict or set(item) != expected or _text(item["key"], "PlanSpec work key") != item["key"] or _frozen_ref(item["source"], "PlanSpec work source") != item["source"]:
            raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec work manifest schema is invalid")
        contract = item["contract"]
        try:
                normalized_contract = _normalize_ticket_contract(
                    contract,
                    ticket_key=item["key"],
                    expected_repository=plan["repository"],
                )
        except PlanControlError as error:
            raise PlanControlError(
                "PLANSPEC_V3_INVALID",
                "PlanSpec work contract is invalid",
            ) from error
        if normalized_contract != contract:
            raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec work contract is invalid")
        for field in ("depends_on", "exclusive_resources", "capabilities"):
            facts = item[field]
            if type(facts) is not list or any(type(value) is not str or not value for value in facts) or facts != sorted(set(facts)):
                raise PlanControlError("PLANSPEC_V3_INVALID", f"PlanSpec work {field} is invalid")
        authority = item["authority"]
        if type(authority) is not dict or set(authority) != {"policy_witness_digest", "worker", "recovery_worker", "review"} or authority["policy_witness_digest"] != policy["digest"]:
            raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec work authority envelope is invalid")
        for role in ("worker", "recovery_worker", "review"):
            if authority[role] != _authority(policy["digest"], authority[role].get("grants", []) if type(authority[role]) is dict else []):
                raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec authority subtree is invalid")
        dependencies[item["key"]] = set(item["depends_on"])
    if campaign["authority"] != _authority(policy["digest"], campaign["authority"].get("grants", []) if type(campaign["authority"]) is dict else []):
        raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec Campaign authority subtree is invalid")
    _assert_acyclic(dependencies)


def _validate_preflight(receipt: object, subject: CampaignPlanningSubject) -> None:
    if (
        type(receipt) is not PlanningPreflightReceipt
        or receipt.subject_digest != subject.digest
        or receipt.stable_action_id != subject.stable_action_id
    ):
        raise PlanControlError("RUNTIME_PREFLIGHT_INVALID", "RuntimeGateway preflight receipt does not bind the exact Planning subject")
    _digest(receipt.receipt_digest, "RuntimeGateway preflight receipt digest")


def _validate_planning_receipt(receipt: object, subject: CampaignPlanningSubject) -> None:
    if (
        type(receipt) is not PlanningReceipt
        or receipt.subject_digest != subject.digest
        or receipt.stable_action_id != subject.stable_action_id
    ):
        raise PlanControlError("RUNTIME_PLANNING_RECEIPT_INVALID", "RuntimeGateway planning receipt does not bind the exact Planning subject")
    status = receipt.status
    if type(status) is not str or status not in {"running", "parked", "completed"}:
        raise PlanControlError("RUNTIME_PLANNING_RECEIPT_INVALID", "RuntimeGateway planning receipt has an invalid lifecycle")
    _digest(receipt.receipt_digest, "RuntimeGateway planning receipt digest")
    if (
        receipt.command is not None
        or (
            receipt.wake_cursor is not None
            and (type(receipt.wake_cursor) is not str or not receipt.wake_cursor)
        )
        or type(receipt.wake_hints) is not tuple
        or any(type(hint) is not str or not hint for hint in receipt.wake_hints)
    ):
        raise PlanControlError(
            "RUNTIME_PLANNING_RECEIPT_INVALID",
            "RuntimeGateway planning receipt contains malformed progress metadata",
        )
    output = receipt.planning_output_artifact_digest
    output_alias = receipt.output_artifact_digest
    if status == "completed":
        _digest(output, "RuntimeGateway planning output Artifact digest")
        if output_alias != output:
            raise PlanControlError(
                "RUNTIME_PLANNING_RECEIPT_INVALID",
                "Completed Planning receipt output digests differ",
            )
    elif output is not None or output_alias is not None:
        raise PlanControlError("RUNTIME_PLANNING_RECEIPT_INVALID", "Incomplete Planning receipt cannot name output")


def _planning_payload(artifacts: PlanningArtifacts, output_digest: str, subject: CampaignPlanningSubject) -> Any:
    output = _read_artifact_json(
        artifacts,
        output_digest,
        code="RUNTIME_PLANNING_OUTPUT_INVALID",
    )
    expected = {"schema_version", "subject_digest", "stable_action_id", "authority_digest", "payload"}
    if type(output) is not dict or set(output) != expected or output["schema_version"] != "gwo.runtime.output.v1" or output["subject_digest"] != subject.digest or output["stable_action_id"] != subject.stable_action_id or output["authority_digest"] != subject.authority_digest:
        raise PlanControlError("RUNTIME_PLANNING_OUTPUT_INVALID", "Planning output Artifact does not bind its exact subject and authority")
    return output["payload"]


def _successor_plan_api():
    """Load the exact pure Task 2 successor-plan interface lazily."""

    try:
        from .successor_plan import (
            compile_successor_plan_spec,
            derive_successor_plan_intent,
            validate_fresh_successor_source,
        )
    except ImportError as error:
        raise PlanControlError(
            "SUCCESSOR_PLAN_UNAVAILABLE",
            "Task 2 successor_plan.py is required to activate an approved successor",
        ) from error
    return (
        derive_successor_plan_intent,
        compile_successor_plan_spec,
        validate_fresh_successor_source,
    )


class _DirectControlStartHost:
    def __init__(self, control: PlanControl):
        self._control = control

    def start(
        self,
        repository: str,
        ready_refs: Sequence[str],
        options: object = None,
    ) -> CampaignHandle:
        return self._control.start(repository, ready_refs, options)


_default_start_host: Any | None = None


def _install_start_host(host: object) -> None:
    """Install one host-owned public Campaign start composition."""

    if not callable(getattr(host, "start", None)):
        raise TypeError("host must expose a callable start boundary")
    global _default_start_host
    _default_start_host = host


def _install_start_control(control: PlanControl) -> None:
    """Compatibility test hook for a directly composed PlanControl."""

    if type(control) is not PlanControl:
        raise TypeError("control must be an exact PlanControl")
    _install_start_host(_DirectControlStartHost(control))


def start(repository: str, ready_refs: Sequence[str], options: object = None) -> CampaignHandle:
    """Start one Campaign through the host-composed PlanControl boundary."""

    if _default_start_host is None:
        raise PlanControlError("PLAN_CONTROL_NOT_CONFIGURED", "Host composition has not installed PlanControl")
    return _default_start_host.start(repository, ready_refs, options)
