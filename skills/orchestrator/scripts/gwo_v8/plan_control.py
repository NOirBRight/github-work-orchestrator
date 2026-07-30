"""PlanControl: compile and activate one immutable PlanSpec v3 revision.

This module owns source readback, one Campaign Planning Pass, immutable
PlanSpec compilation, Ticket claims, and activation readback.  RuntimeGateway
owns every Runtime concern.  In particular, this module sees only its planning
subject plus opaque preflight/progress receipts; it has no provider, Profile,
session, Workspace, or command seam.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import wraps
import re
import threading
from typing import Any, Mapping, Protocol, Sequence

from ._canonical import CanonicalJsonError, canonical_bytes, digest_bytes, digest_value, load_canonical_json
from .planning_protocol import planning_prompt
from .runtime_gateway import CampaignPlanningSubject, PlanningPreflightReceipt, PlanningReceipt


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


@dataclass(frozen=True)
class PlanningReservation:
    """Repository-global non-executable claim made after exact preflight."""

    repository: str
    campaign_key: str
    ticket_keys: tuple[str, ...]
    subject_digest: str
    stable_action_id: str
    preflight_receipt_digest: str


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
    compilation_record_artifact_digest: str | None = None
    revision: PlanRevision | None = None
    # The governed production repository persists this immutable copy so a
    # fresh host can reconstruct the Artifact-backed record before any active
    # Plan readback.  It is absent only while Planning is still incomplete.
    compilation_record_bytes: bytes | None = None


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

    def read_runtime_assertion(
        self,
        handle: CampaignHandle,
    ) -> Mapping[str, Any] | None: ...

    def save_runtime_assertion(
        self,
        handle: CampaignHandle,
        assertion: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def reserve_planning(self, reservation: PlanningReservation) -> None: ...

    def release_planning(self, reservation: PlanningReservation) -> None: ...

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
        self.runtime_assertions: dict[tuple[str, str], dict[str, Any]] = {}
        self.revisions: dict[str, PlanRevision] = {}
        self.activations: dict[tuple[str, str], ActivationReceipt] = {}
        # ``activations`` is the mutable current pointer only.  Retain every
        # published receipt separately so a successor cannot erase audit
        # evidence for its predecessor.
        self.activation_receipts: dict[
            tuple[str, str, str, str], ActivationReceipt
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

    @_repository_locked
    def read_runtime_assertion(
        self,
        handle: CampaignHandle,
    ) -> Mapping[str, Any] | None:
        with self._lock:
            value = self.runtime_assertions.get(
                (handle.repository, handle.campaign_key)
            )
            return None if value is None else _canonical(value)

    @_repository_locked
    def save_runtime_assertion(
        self,
        handle: CampaignHandle,
        assertion: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        value = _canonical(assertion)
        if type(value) is not dict:
            raise PlanControlError(
                "START_OPTIONS_INVALID",
                "Campaign Runtime assertion must be a canonical object",
            )
        key = (handle.repository, handle.campaign_key)
        with self._lock:
            existing = self.runtime_assertions.get(key)
            if existing is not None and existing != value:
                raise PlanControlError(
                    "START_OPTIONS_CONFLICT",
                    "Campaign Runtime assertion differs from its durable identity",
                )
            self.runtime_assertions[key] = value
            return _canonical(value)

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
            if self.planning_reservations.get(planning_key) != expected_planning:
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
        proofs = []
        for (repository, ticket_key), claimed_revision in self.claims.items():
            campaign_key = self._claim_campaigns[(repository, ticket_key)]
            if (
                repository != handle.repository
                or campaign_key != handle.campaign_key
                or claimed_revision != revision_digest
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

        expected_revision = _compile_plan(
            attempt.snapshot_bytes,
            attempt.snapshot_artifact_digest,
            intent_bytes,
            handle,
        )
        if attempt.revision is not None:
            _validate_revision_provenance(
                attempt.revision,
                expected_revision,
            )
        revision = expected_revision
        if attempt.revision is None:
            attempt = self._repository.save_attempt(replace(attempt, revision=revision))

        # Both the complete immutable PlanSpec and the full Policy Witness are
        # digest-addressed facts.  RuntimeGateway and later permission work
        # can reconstruct authority from these exact bytes without accepting a
        # mutable PlanControl projection.
        plan_artifact_digest = _put_canonical(self._artifacts, revision.plan_spec)
        if (
            plan_artifact_digest != revision.digest
            or _read_artifact_json(
                self._artifacts,
                plan_artifact_digest,
                code="PLAN_READBACK_INVALID",
            )
            != revision.plan_spec
        ):
            raise PlanControlError("PLAN_READBACK_INVALID", "PlanSpec Artifact does not read back at its revision digest")

        planning_reservation = self._planning_reservation_from_compilation(
            attempt
        )
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
        )

        # An explicitly replayed successor request is idempotent only when it
        # reconstructs the exact already-active receipt for the same required
        # predecessor.  Do not ask it to recreate a consumed reservation.
        current = self._repository.active_receipt(handle)
        if current == receipt:
            self._validate_active_receipt(
                handle,
                receipt=current,
                require_claims=False,
            )
            self._repository.finalize_claims(current)
            self.read_active(handle)
            return handle

        # Reservation is durable but cannot replace an active claim.  The
        # revision is published before activation; claims move only after the
        # winning CAS receipt has read back exactly.
        self._repository.reserve_claims(receipt)
        self._repository.publish_revision(revision)
        if self._repository.read_revision(revision.digest) != revision:
            raise PlanControlError("PLAN_READBACK_INVALID", "Published Plan Revision does not read back exactly")
        self._repository.activate(receipt)
        if self._repository.read_activation(handle) != receipt:
            raise PlanControlError("ACTIVATION_READBACK_INVALID", "Activation Receipt does not read back exactly")
        self._validate_active_receipt(
            handle,
            receipt=receipt,
            require_claims=False,
        )
        self._repository.finalize_claims(receipt)
        return handle

    def read_active(self, handle: CampaignHandle) -> ActivePlanReadback:
        """Read one active revision without mutable-source, claim, or Runtime work.

        ExecutionKernel consumes this internal port before it admits Work Runs.
        It deliberately has no transition or recovery behavior.
        """

        return self._validate_active_receipt(handle, require_claims=True)

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
        if (
            type(receipt) is not ActivationReceipt
            or receipt.repository != handle.repository
            or receipt.campaign_key != handle.campaign_key
            or receipt.writer_generation != self._writer_generation()
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
            or attempt.ready_refs != receipt.ready_refs
            or attempt.ticket_keys != receipt.ticket_keys
            or attempt.subject.digest != receipt.planning_subject_digest
            or attempt.subject.stable_action_id
            != receipt.planning_stable_action_id
            or attempt.compilation_record_artifact_digest is None
        ):
            raise PlanControlError(
                "ACTIVE_PLAN_CROSS_BINDING_INVALID",
                "Activation Receipt does not exactly bind its Planning attempt",
            )
        self._verify_attempt_artifacts(attempt)
        snapshot = _snapshot_from_bytes(attempt.snapshot_bytes)
        if (
            tuple(ticket["key"] for ticket in snapshot["tickets"])
            != attempt.ticket_keys
            or tuple(sorted(ticket["source"]["ref"] for ticket in snapshot["tickets"]))
            != attempt.ready_refs
        ):
            raise PlanControlError(
                "ACTIVE_PLAN_CROSS_BINDING_INVALID",
                "Planning attempt Ticket and ready-reference sets differ from its frozen snapshot",
            )
        intent_bytes = self._read_compilation_record(attempt)
        compilation = _read_artifact_json(
            self._artifacts,
            attempt.compilation_record_artifact_digest,
            code="COMPILATION_RECORD_INVALID",
        )
        if (
            compilation["preflight_receipt"]["receipt_digest"]
            != receipt.planning_preflight_receipt_digest
        ):
            raise PlanControlError(
                "ACTIVE_PLAN_CROSS_BINDING_INVALID",
                "Activation Receipt does not exactly bind its Planning preflight",
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
        expected_revision = _compile_plan(
            attempt.snapshot_bytes,
            attempt.snapshot_artifact_digest,
            intent_bytes,
            handle,
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
        active_proofs = self._repository.read_claim_proofs(
            handle,
            receipt.revision_digest,
        )
        proofs_match = (
            type(active_proofs) is tuple
            and all(type(proof) is TicketClaimProof for proof in active_proofs)
            and tuple(proof.ticket_key for proof in active_proofs)
            == receipt.ticket_keys
            and all(
                proof.repository == handle.repository
                and proof.campaign_key == handle.campaign_key
                and proof.plan_revision_digest == receipt.revision_digest
                for proof in active_proofs
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
            revision.canonical_bytes,
            receipt,
            active_proofs,
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
            if attempt.compilation_record_bytes is not None:
                if (
                    digest_bytes(attempt.compilation_record_bytes) != digest
                    or load_canonical_json(attempt.compilation_record_bytes)
                    != record
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
        witness = {key: value for key, value in snapshot["policy"].items() if key != "digest"}
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
            or subject.stable_action_id != expected_action
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
        if request != planning_prompt(
            subject_digest=subject.prompt_binding_digest,
            authority_digest=attempt.policy_witness_digest,
            snapshot_artifact_digest=attempt.snapshot_artifact_digest,
            policy_witness_artifact_digest=attempt.policy_witness_digest,
        ):
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
) -> dict[str, Any]:
    if type(value) is not dict:
        raise PlanControlError(
            "TICKET_CONTRACT_MISSING",
            f"Ticket {ticket_key} lacks a complete frozen contract",
        )
    expected = {
        "id",
        "node_id",
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
    title = _text(value["title"], "Ticket title")
    body = _text(value["body"], "Ticket body")
    state_reason = value["state_reason"]
    issue_type = value["type"]
    repository = value["repository"]
    labels = value["labels"]
    comments = value["comments"]
    if (
        type(ticket_id) is not int
        or ticket_id < 1
        or type(node_id) is not str
        or not node_id
        or type(value["updated_at"]) is not str
        or not value["updated_at"]
        or
        (state_reason is not None and type(state_reason) is not str)
        or (issue_type is not None and type(issue_type) is not dict)
        or type(repository) is not dict
        or set(repository) != {"full_name", "url"}
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
    return {
        "id": ticket_id,
        "node_id": node_id,
        "title": title,
        "body": body,
        "state": "open",
        "state_reason": state_reason,
        "type": None if issue_type is None else dict(issue_type),
        "repository": {
            "full_name": _text(
                repository["full_name"],
                "Ticket repository full_name",
            ),
            "url": _text(repository["url"], "Ticket repository URL"),
        },
        "labels": [dict(label) for label in labels],
        "comments": [dict(comment) for comment in comments],
        "updated_at": value["updated_at"],
    }


def _normalize_ticket(value: Any, *, repository: str) -> dict[str, Any]:
    expected = {"key", "labels", "source", "contract", "native_blockers"}
    if type(value) is not dict or set(value) != expected:
        raise PlanControlError("SNAPSHOT_INVALID", "Ticket snapshot schema is invalid")
    key = _text(value["key"], "Ticket key")
    labels = value["labels"]
    if type(labels) is not list or any(type(label) is not str or not label for label in labels) or len(set(labels)) != len(labels):
        raise PlanControlError("TICKET_LABEL_INVALID", f"Ticket {key} labels are invalid")
    if "ready-for-agent" not in labels or set(labels).intersection(_TRIAGE - {"ready-for-agent"}):
        raise PlanControlError("TICKET_LABEL_INVALID", f"Ticket {key} is not ready-for-agent")
    contract = _normalize_ticket_contract(
        value["contract"],
        ticket_key=key,
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
    return {
        "key": key,
        "labels": sorted(labels),
        "source": _frozen_ref(value["source"], "Ticket source"),
        "contract": contract,
        "native_blockers": sorted(canonical_blockers, key=lambda item: item["key"]),
    }


def _normalize_snapshot(value: Any, repository: str, refs: tuple[str, ...]) -> dict[str, Any]:
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
    if external:
        raise PlanControlError("EXTERNAL_BLOCKER_OPEN", "Selected Tickets have open external blockers: " + ", ".join(external))
    dependencies = {ticket["key"]: {blocker["key"] for blocker in ticket["native_blockers"] if blocker["state"] == "open" and blocker["key"] in selected} for ticket in tickets}
    _assert_acyclic(dependencies)
    return {
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
