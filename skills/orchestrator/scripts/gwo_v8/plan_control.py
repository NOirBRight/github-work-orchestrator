"""PlanControl's V3-only Campaign-start vertical slice.

This module deliberately does not import the V2 compiler, activation store, or
Goal/Node driver.  V2 remains an audit decoder for work that was already
active; new Campaigns begin here as one immutable Ticket Manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from threading import RLock
from typing import Any, Callable, Mapping, Protocol, Sequence

from ._canonical import canonical_bytes, digest_bytes, digest_value


TRIAGE_LABELS = frozenset(
    {"needs-triage", "needs-info", "ready-for-agent", "ready-for-human", "wontfix"}
)
_REQUIRED_ROLES = ("campaign", "worker", "recovery_worker", "review")
_FORBIDDEN_MANIFEST_FIELDS = frozenset(
    {
        "provider",
        "model",
        "cli",
        "runtime",
        "runtime_binding",
        "runtime_assignment",
        "assignment",
        "profile",
        "selector",
        "fallback",
        "capacity",
        "timeout",
        "permission_decision",
        "lifecycle",
        "lifecycle_graph",
        "nodes",
        "edges",
        "check",
        "checks",
        "review",
        "reviews",
        "recovery",
        "integration",
        "difficulty",
        "risk",
        "predicted_paths",
    }
)


class PlanControlError(RuntimeError):
    """A stable, fail-closed PlanControl rejection."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SplitCampaignDecision:
    """The typed alternative to silently truncating a planning input."""

    kind: str
    snapshot_digest: str
    actual_bytes: int
    max_bytes: int


class PlanControlDecision(PlanControlError):
    """A durable Decision is required before PlanControl can publish."""

    def __init__(self, decision: SplitCampaignDecision):
        super().__init__(
            "SPLIT_CAMPAIGN_REQUIRED",
            "the complete selected Ticket snapshot exceeds the planning byte limit",
        )
        self.decision = decision


@dataclass(frozen=True)
class CampaignHandle:
    """Opaque, stable Campaign identity independent of a Plan Revision."""

    repository: str
    campaign_key: str


@dataclass(frozen=True)
class CampaignStartOptions:
    """The only start options owned by PlanControl in this slice."""

    campaign_key: str | None = None
    expected_previous_revision_digest: str | None = None

    @classmethod
    def from_value(
        cls, value: CampaignStartOptions | Mapping[str, Any] | None
    ) -> CampaignStartOptions:
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise PlanControlError("START_OPTIONS_INVALID", "start options must be an object")
        unknown = set(value) - {"campaign_key", "expected_previous_revision_digest"}
        if unknown:
            raise PlanControlError(
                "START_OPTIONS_INVALID",
                f"unsupported start options: {sorted(unknown)}",
            )
        campaign_key = value.get("campaign_key")
        previous = value.get("expected_previous_revision_digest")
        if campaign_key is not None and (not isinstance(campaign_key, str) or not campaign_key):
            raise PlanControlError("START_OPTIONS_INVALID", "campaign_key must be a non-empty string")
        if previous is not None and (
            not isinstance(previous, str) or len(previous) != 64
        ):
            raise PlanControlError(
                "START_OPTIONS_INVALID",
                "expected_previous_revision_digest must be a SHA-256 digest",
            )
        return cls(campaign_key=campaign_key, expected_previous_revision_digest=previous)


@dataclass(frozen=True)
class PlanRevision:
    """An immutable canonical PlanSpec v3 revision."""

    repository: str
    campaign_key: str
    digest: str
    canonical_bytes: bytes
    snapshot_digest: str

    @property
    def plan_spec(self) -> dict[str, Any]:
        """Return a new decoded projection; callers cannot mutate durable bytes."""

        return json.loads(self.canonical_bytes)


@dataclass(frozen=True)
class ActivationReceiptV3:
    repository: str
    campaign_key: str
    revision_digest: str
    expected_previous_revision_digest: str | None
    writer_generation: str


@dataclass(frozen=True)
class ActiveCampaign:
    handle: CampaignHandle
    revision: PlanRevision
    receipt: ActivationReceiptV3

    @property
    def plan_spec(self) -> dict[str, Any]:
        return self.revision.plan_spec

    @property
    def digest(self) -> str:
        return self.revision.digest

    @property
    def canonical_bytes(self) -> bytes:
        return self.revision.canonical_bytes


@dataclass(frozen=True)
class _TicketSnapshot:
    key: str
    labels: tuple[str, ...]
    source: dict[str, str]
    contract: dict[str, Any]
    native_blockers: tuple[dict[str, str], ...]

    def as_value(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "labels": list(self.labels),
            "source": self.source,
            "contract": self.contract,
            "native_blockers": list(self.native_blockers),
        }


@dataclass(frozen=True)
class CampaignSnapshot:
    """The complete authoritative start input, normalized before planning."""

    repository: str
    target_branch: str
    campaign_source: dict[str, str]
    policy: dict[str, Any]
    tickets: tuple[_TicketSnapshot, ...]

    @classmethod
    def from_value(cls, value: CampaignSnapshot | Mapping[str, Any]) -> CampaignSnapshot:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise PlanControlError("SNAPSHOT_INVALID", "Campaign snapshot must be an object")
        expected = {"repository", "target_branch", "campaign_source", "policy", "tickets"}
        if set(value) != expected:
            raise PlanControlError(
                "SNAPSHOT_INVALID",
                "Campaign snapshot must contain repository, target_branch, campaign_source, policy, and tickets",
            )
        repository = _nonempty_string(value["repository"], "snapshot repository")
        target_branch = _nonempty_string(value["target_branch"], "target branch")
        campaign_source = _frozen_ref(value["campaign_source"], "Campaign source")
        policy = _json_object(value["policy"], "Policy Witness")
        raw_tickets = value["tickets"]
        if not isinstance(raw_tickets, Sequence) or isinstance(raw_tickets, (str, bytes)):
            raise PlanControlError("SNAPSHOT_INVALID", "tickets must be a list")
        tickets = tuple(sorted((_ticket_snapshot(ticket) for ticket in raw_tickets), key=lambda ticket: ticket.key))
        return cls(
            repository=repository,
            target_branch=target_branch,
            campaign_source=campaign_source,
            policy=policy,
            tickets=tickets,
        )

    def as_value(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "target_branch": self.target_branch,
            "campaign_source": self.campaign_source,
            "policy": self.policy,
            "tickets": [ticket.as_value() for ticket in self.tickets],
        }


@dataclass(frozen=True)
class _PlanningReservation:
    repository: str
    campaign_key: str
    snapshot_digest: str
    planning_id: str
    state: str
    intent: dict[str, Any] | None


class CampaignSource(Protocol):
    def snapshot(
        self, repository: str, ready_refs: tuple[str, ...]
    ) -> CampaignSnapshot | Mapping[str, Any]: ...


class PlanningPass(Protocol):
    def plan(self, snapshot: CampaignSnapshot, planning_id: str) -> Mapping[str, Any]: ...


class PlanControlStore(Protocol):
    def assert_claims_available(
        self, handle: CampaignHandle, ticket_keys: tuple[str, ...]
    ) -> None: ...

    def reserve_planning(
        self, handle: CampaignHandle, snapshot_digest: str
    ) -> _PlanningReservation: ...

    def begin_planning(self, reservation: _PlanningReservation) -> bool: ...

    def persist_intent(
        self, reservation: _PlanningReservation, intent: dict[str, Any]
    ) -> _PlanningReservation: ...

    def publish_revision(self, revision: PlanRevision) -> None: ...

    def read_revision(self, handle: CampaignHandle, digest: str) -> PlanRevision | None: ...

    def activate(
        self,
        handle: CampaignHandle,
        revision: PlanRevision,
        *,
        expected_previous_revision_digest: str | None,
        writer_generation: str,
        ticket_keys: tuple[str, ...],
    ) -> ActivationReceiptV3: ...

    def read_receipt(
        self, handle: CampaignHandle, revision_digest: str
    ) -> ActivationReceiptV3 | None: ...

    def finalize_activation(
        self,
        handle: CampaignHandle,
        revision: PlanRevision,
        receipt: ActivationReceiptV3,
    ) -> ActiveCampaign: ...

    def read_active(self, handle: CampaignHandle) -> ActiveCampaign | None: ...


class InMemoryCampaignSource:
    """Contract fake for tests; production source readback remains a private adapter."""

    def __init__(
        self,
        *,
        repository: str,
        target_branch: str,
        campaign_source: Mapping[str, Any],
        policy: Mapping[str, Any],
        tickets: Mapping[str, Mapping[str, Any]],
    ):
        self.repository = repository
        self.target_branch = target_branch
        self.campaign_source = _copy_json(campaign_source)
        self.policy = _copy_json(policy)
        self.tickets = {key: _copy_json(ticket) for key, ticket in tickets.items()}
        self.calls = 0

    def snapshot(self, repository: str, ready_refs: tuple[str, ...]) -> dict[str, Any]:
        self.calls += 1
        if repository != self.repository:
            raise PlanControlError("SNAPSHOT_REPOSITORY_MISMATCH", "source repository differs")
        missing = [key for key in ready_refs if key not in self.tickets]
        if missing:
            raise PlanControlError(
                "SNAPSHOT_TICKET_MISSING", f"selected Ticket is absent: {missing[0]}"
            )
        return {
            "repository": self.repository,
            "target_branch": self.target_branch,
            "campaign_source": _copy_json(self.campaign_source),
            "policy": _copy_json(self.policy),
            "tickets": [_copy_json(self.tickets[key]) for key in ready_refs],
        }


class InMemoryPlanningPass:
    """A strict, observable planning-pass fake used by behavior tests."""

    def __init__(self, intent: Mapping[str, Any]):
        self._intent = _copy_json(intent)
        self.calls = 0
        self.planning_ids: list[str] = []
        self.snapshots: list[CampaignSnapshot] = []

    def plan(self, snapshot: CampaignSnapshot, planning_id: str) -> dict[str, Any]:
        self.calls += 1
        self.planning_ids.append(planning_id)
        self.snapshots.append(snapshot)
        return _copy_json(self._intent)


class InMemoryPlanControlStore:
    """Atomic in-memory durable control record with Campaign-scoped CAS and claims."""

    def __init__(self):
        self._lock = RLock()
        self._planning: dict[tuple[str, str, str], _PlanningReservation] = {}
        self._revisions: dict[tuple[str, str, str], PlanRevision] = {}
        self._active: dict[CampaignHandle, ActiveCampaign] = {}
        self._pending: dict[
            CampaignHandle, tuple[PlanRevision, ActivationReceiptV3, tuple[str, ...]]
        ] = {}
        self._receipts: dict[tuple[CampaignHandle, str], ActivationReceiptV3] = {}
        self._claims: dict[tuple[str, str], CampaignHandle] = {}

    def reserve_planning(
        self, handle: CampaignHandle, snapshot_digest: str
    ) -> _PlanningReservation:
        key = (handle.repository, handle.campaign_key, snapshot_digest)
        with self._lock:
            existing = self._planning.get(key)
            if existing is not None:
                return existing
            reservation = _PlanningReservation(
                repository=handle.repository,
                campaign_key=handle.campaign_key,
                snapshot_digest=snapshot_digest,
                planning_id=f"planning:{digest_value({'repository': handle.repository, 'campaign_key': handle.campaign_key, 'snapshot_digest': snapshot_digest})}",
                state="reserved",
                intent=None,
            )
            self._planning[key] = reservation
            return reservation

    def begin_planning(self, reservation: _PlanningReservation) -> bool:
        key = (reservation.repository, reservation.campaign_key, reservation.snapshot_digest)
        with self._lock:
            current = self._planning.get(key)
            if current is None or current.planning_id != reservation.planning_id:
                raise PlanControlError("PLANNING_RESERVATION_LOST", "planning reservation changed")
            if current.intent is not None or current.state == "planning":
                return False
            if current.state != "reserved":
                raise PlanControlError("PLANNING_READBACK_AMBIGUOUS", "planning state is invalid")
            self._planning[key] = _PlanningReservation(
                repository=current.repository,
                campaign_key=current.campaign_key,
                snapshot_digest=current.snapshot_digest,
                planning_id=current.planning_id,
                state="planning",
                intent=None,
            )
            return True

    def assert_claims_available(
        self, handle: CampaignHandle, ticket_keys: tuple[str, ...]
    ) -> None:
        with self._lock:
            for ticket_key in ticket_keys:
                owner = self._claims.get((handle.repository, ticket_key))
                if owner is not None and owner != handle:
                    raise PlanControlError(
                        "TICKET_CLAIM_CONFLICT",
                        f"Ticket {ticket_key} is claimed by another Campaign",
                    )

    def persist_intent(
        self, reservation: _PlanningReservation, intent: dict[str, Any]
    ) -> _PlanningReservation:
        key = (reservation.repository, reservation.campaign_key, reservation.snapshot_digest)
        normalized = _copy_json(intent)
        with self._lock:
            current = self._planning.get(key)
            if current is None or current.planning_id != reservation.planning_id:
                raise PlanControlError("PLANNING_RESERVATION_LOST", "planning reservation changed")
            if current.intent is not None:
                if current.intent != normalized:
                    raise PlanControlError(
                        "PLANNING_INTENT_CONFLICT", "a Planning Pass already produced another intent"
                    )
                return current
            if current.state != "planning":
                raise PlanControlError(
                    "PLANNING_READBACK_AMBIGUOUS", "Planning Pass was not durably begun"
                )
            updated = _PlanningReservation(
                repository=current.repository,
                campaign_key=current.campaign_key,
                snapshot_digest=current.snapshot_digest,
                planning_id=current.planning_id,
                state="intended",
                intent=normalized,
            )
            self._planning[key] = updated
            return updated

    def publish_revision(self, revision: PlanRevision) -> None:
        key = (revision.repository, revision.campaign_key, revision.digest)
        with self._lock:
            existing = self._revisions.get(key)
            if existing is not None and existing != revision:
                raise PlanControlError(
                    "PLAN_REVISION_IMMUTABLE", "an immutable Plan Revision differs at the same digest"
                )
            self._revisions[key] = revision

    def read_revision(self, handle: CampaignHandle, digest: str) -> PlanRevision | None:
        with self._lock:
            return self._revisions.get((handle.repository, handle.campaign_key, digest))

    def activate(
        self,
        handle: CampaignHandle,
        revision: PlanRevision,
        *,
        expected_previous_revision_digest: str | None,
        writer_generation: str,
        ticket_keys: tuple[str, ...],
    ) -> ActivationReceiptV3:
        with self._lock:
            pending = self._pending.get(handle)
            if pending is not None:
                pending_revision, pending_receipt, _pending_ticket_keys = pending
                if (
                    pending_revision == revision
                    and pending_receipt.expected_previous_revision_digest
                    == expected_previous_revision_digest
                ):
                    return pending_receipt
                raise PlanControlError(
                    "ACTIVATION_PENDING_CONFLICT",
                    "a different Activation is pending receipt readback",
                )
            existing = self._active.get(handle)
            existing_receipt = self._receipts.get((handle, revision.digest))
            if existing is not None and existing.revision.digest == revision.digest:
                if (
                    existing.receipt.expected_previous_revision_digest
                    != expected_previous_revision_digest
                ):
                    raise PlanControlError(
                        "ACTIVATION_CAS_CONFLICT", "retry has a different expected previous revision"
                    )
                return existing.receipt
            actual_previous = None if existing is None else existing.revision.digest
            if actual_previous != expected_previous_revision_digest:
                raise PlanControlError(
                    "ACTIVATION_CAS_CONFLICT",
                    "active Campaign revision differs from the expected previous revision",
                )
            for ticket_key in ticket_keys:
                owner = self._claims.get((handle.repository, ticket_key))
                if owner is not None and owner != handle:
                    raise PlanControlError(
                        "TICKET_CLAIM_CONFLICT",
                        f"Ticket {ticket_key} is claimed by another Campaign",
                    )
            for ticket_key in ticket_keys:
                self._claims[(handle.repository, ticket_key)] = handle
            receipt = ActivationReceiptV3(
                repository=handle.repository,
                campaign_key=handle.campaign_key,
                revision_digest=revision.digest,
                expected_previous_revision_digest=expected_previous_revision_digest,
                writer_generation=writer_generation,
            )
            if existing_receipt is not None and existing_receipt != receipt:
                raise PlanControlError(
                    "ACTIVATION_RECEIPT_CONFLICT", "a receipt at this revision differs"
                )
            self._receipts[(handle, revision.digest)] = receipt
            self._pending[handle] = (revision, receipt, ticket_keys)
            return receipt

    def read_receipt(
        self, handle: CampaignHandle, revision_digest: str
    ) -> ActivationReceiptV3 | None:
        with self._lock:
            return self._receipts.get((handle, revision_digest))

    def finalize_activation(
        self,
        handle: CampaignHandle,
        revision: PlanRevision,
        receipt: ActivationReceiptV3,
    ) -> ActiveCampaign:
        with self._lock:
            existing = self._active.get(handle)
            if existing is not None and existing.revision == revision and existing.receipt == receipt:
                return existing
            pending = self._pending.get(handle)
            if pending is None or pending[0] != revision or pending[1] != receipt:
                raise PlanControlError(
                    "ACTIVATION_FINALIZE_MISMATCH",
                    "Activation can finalize only from its read-backed Receipt",
                )
            ticket_keys = set(pending[2])
            for claim_key, owner in tuple(self._claims.items()):
                if owner == handle and claim_key[1] not in ticket_keys:
                    del self._claims[claim_key]
            active = ActiveCampaign(handle=handle, revision=revision, receipt=receipt)
            self._active[handle] = active
            del self._pending[handle]
            return active

    def read_active(self, handle: CampaignHandle) -> ActiveCampaign | None:
        with self._lock:
            return self._active.get(handle)

    def claimed_ticket_keys(self, handle: CampaignHandle) -> frozenset[str]:
        with self._lock:
            return frozenset(
                ticket_key
                for (repository, ticket_key), owner in self._claims.items()
                if repository == handle.repository and owner == handle
            )


class PlanControl:
    """Own selected-Ticket planning, V3 compilation, publication, and activation."""

    def __init__(
        self,
        *,
        source: CampaignSource,
        planner: PlanningPass,
        store: PlanControlStore,
        max_snapshot_bytes: int = 1_000_000,
        writer_generation: str = "v8",
        checkpoint: Callable[[str], None] | None = None,
    ):
        if not isinstance(max_snapshot_bytes, int) or max_snapshot_bytes <= 0:
            raise ValueError("max_snapshot_bytes must be positive")
        self.source = source
        self.planner = planner
        self.store = store
        self.max_snapshot_bytes = max_snapshot_bytes
        self.writer_generation = writer_generation
        self.checkpoint = checkpoint

    def start(
        self,
        repository: str,
        ready_refs: Sequence[str],
        options: CampaignStartOptions | Mapping[str, Any] | None = None,
    ) -> CampaignHandle:
        repository = _nonempty_string(repository, "repository")
        refs = _ready_refs(ready_refs)
        options_value = CampaignStartOptions.from_value(options)
        snapshot = CampaignSnapshot.from_value(self.source.snapshot(repository, refs))
        _validate_snapshot(snapshot, repository, refs)
        snapshot_value = snapshot.as_value()
        snapshot_bytes = canonical_bytes(snapshot_value)
        snapshot_digest = digest_bytes(snapshot_bytes)
        if len(snapshot_bytes) > self.max_snapshot_bytes:
            raise PlanControlDecision(
                SplitCampaignDecision(
                    kind="SplitCampaign",
                    snapshot_digest=snapshot_digest,
                    actual_bytes=len(snapshot_bytes),
                    max_bytes=self.max_snapshot_bytes,
                )
            )
        campaign_key = options_value.campaign_key or _default_campaign_key(repository, refs)
        handle = CampaignHandle(repository=repository, campaign_key=campaign_key)
        self.store.assert_claims_available(
            handle, tuple(ticket.key for ticket in snapshot.tickets)
        )
        reservation = self.store.reserve_planning(handle, snapshot_digest)
        if reservation.intent is None:
            if not self.store.begin_planning(reservation):
                raise PlanControlError(
                    "PLANNING_READBACK_AMBIGUOUS",
                    "Planning Pass was reserved but its private intent was not read back",
                )
            raw_intent = self.planner.plan(snapshot, reservation.planning_id)
            intent = _validate_plan_intent(raw_intent, snapshot)
            reservation = self.store.persist_intent(reservation, intent)
            self._checkpoint("planning_intent_read_back")
        else:
            intent = _validate_plan_intent(reservation.intent, snapshot)
        _raise_decision_requirements(intent, snapshot_digest)
        revision = _compile_v3(
            snapshot=snapshot,
            snapshot_digest=snapshot_digest,
            handle=handle,
            intent=intent,
        )
        self.store.publish_revision(revision)
        self._checkpoint("plan_published")
        if self.store.read_revision(handle, revision.digest) != revision:
            raise PlanControlError(
                "PLAN_READBACK_MISMATCH", "published Plan Revision did not read back exactly"
            )
        self._checkpoint("plan_read_back")
        receipt = self.store.activate(
            handle,
            revision,
            expected_previous_revision_digest=options_value.expected_previous_revision_digest,
            writer_generation=self.writer_generation,
            ticket_keys=tuple(ticket.key for ticket in snapshot.tickets),
        )
        self._checkpoint("activation_cas")
        if self.store.read_receipt(handle, revision.digest) != receipt:
            raise PlanControlError(
                "ACTIVATION_RECEIPT_READBACK_MISMATCH",
                "Activation Receipt did not read back exactly",
            )
        self._checkpoint("activation_receipt_read_back")
        active = self.store.finalize_activation(handle, revision, receipt)
        if active.revision != revision or active.receipt != receipt:
            raise PlanControlError(
                "ACTIVATION_READBACK_MISMATCH",
                "active Campaign did not read back from the Activation Receipt",
            )
        return handle

    def read_active(self, handle: CampaignHandle) -> ActiveCampaign:
        active = self.store.read_active(handle)
        if active is None:
            raise PlanControlError("CAMPAIGN_NOT_ACTIVE", "Campaign has no read-backed active Plan Revision")
        return active

    def _checkpoint(self, boundary: str) -> None:
        if self.checkpoint is not None:
            self.checkpoint(boundary)


def start(
    repository: str,
    ready_refs: Sequence[str],
    options: CampaignStartOptions | Mapping[str, Any] | None = None,
    *,
    control: PlanControl,
) -> CampaignHandle:
    """Public V8 start seam; only a configured PlanControl may create Campaigns."""

    return control.start(repository, ready_refs, options)


def _copy_json(value: Any) -> Any:
    try:
        return json.loads(canonical_bytes(value))
    except (TypeError, ValueError) as error:
        raise PlanControlError("SNAPSHOT_INVALID", "input must be canonical JSON data") from error


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PlanControlError("SNAPSHOT_INVALID", f"{label} must be an object")
    return _copy_json(value)


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PlanControlError("SNAPSHOT_INVALID", f"{label} must be a non-empty string")
    return value


def _frozen_ref(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"ref", "digest"}:
        raise PlanControlError("SNAPSHOT_INVALID", f"{label} must contain ref and digest")
    return {
        "ref": _nonempty_string(value["ref"], f"{label} ref"),
        "digest": _nonempty_string(value["digest"], f"{label} digest"),
    }


def _ticket_snapshot(value: Any) -> _TicketSnapshot:
    if not isinstance(value, Mapping):
        raise PlanControlError("SNAPSHOT_INVALID", "Ticket snapshot must be an object")
    expected = {"key", "labels", "source", "contract", "native_blockers"}
    if set(value) != expected:
        raise PlanControlError(
            "SNAPSHOT_INVALID",
            "Ticket snapshot must contain key, labels, source, contract, and native_blockers",
        )
    labels = value["labels"]
    if not isinstance(labels, Sequence) or isinstance(labels, (str, bytes)) or any(
        not isinstance(label, str) or not label for label in labels
    ):
        raise PlanControlError("SNAPSHOT_INVALID", "Ticket labels must be non-empty strings")
    blockers = value["native_blockers"]
    if not isinstance(blockers, Sequence) or isinstance(blockers, (str, bytes)):
        raise PlanControlError("SNAPSHOT_INVALID", "native_blockers must be a list")
    normalized_blockers: list[dict[str, str]] = []
    for blocker in blockers:
        if not isinstance(blocker, Mapping) or set(blocker) != {"key", "state"}:
            raise PlanControlError(
                "SNAPSHOT_INVALID", "each native blocker must contain key and state"
            )
        key = _nonempty_string(blocker["key"], "native blocker key")
        state = _nonempty_string(blocker["state"], "native blocker state").lower()
        if state not in {"open", "closed"}:
            raise PlanControlError("SNAPSHOT_INVALID", "native blocker state must be open or closed")
        normalized_blockers.append({"key": key, "state": state})
    return _TicketSnapshot(
        key=_nonempty_string(value["key"], "Ticket key"),
        labels=tuple(sorted(set(labels))),
        source=_frozen_ref(value["source"], "Ticket source"),
        contract=_json_object(value["contract"], "Ticket contract"),
        native_blockers=tuple(sorted(normalized_blockers, key=lambda blocker: blocker["key"])),
    )


def _ready_refs(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PlanControlError("READY_REFS_INVALID", "ready_refs must be a non-empty sequence")
    refs = tuple(value)
    if not refs or any(not isinstance(ref, str) or not ref for ref in refs):
        raise PlanControlError("READY_REFS_INVALID", "ready_refs must be non-empty strings")
    if len(set(refs)) != len(refs):
        raise PlanControlError("READY_REFS_INVALID", "ready_refs must not repeat a Ticket")
    return refs


def _validate_snapshot(snapshot: CampaignSnapshot, repository: str, refs: tuple[str, ...]) -> None:
    if snapshot.repository != repository:
        raise PlanControlError("SNAPSHOT_REPOSITORY_MISMATCH", "snapshot repository differs")
    ticket_keys = tuple(ticket.key for ticket in snapshot.tickets)
    if len(set(ticket_keys)) != len(ticket_keys) or set(ticket_keys) != set(refs):
        raise PlanControlError(
            "SNAPSHOT_OMISSION", "authoritative snapshot must contain exactly every selected Ticket"
        )
    for ticket in snapshot.tickets:
        labels = set(ticket.labels)
        if "ready-for-agent" not in labels or labels.intersection(TRIAGE_LABELS - {"ready-for-agent"}):
            raise PlanControlError(
                "TICKET_LABEL_INVALID", f"Ticket {ticket.key} is not exclusively ready-for-agent"
            )
        if not ticket.contract:
            raise PlanControlError("TICKET_CONTRACT_MISSING", f"Ticket {ticket.key} has no contract")
    _policy_shape(snapshot.policy)
    _reject_forbidden_manifest_fields(
        {"tickets": [ticket.as_value() for ticket in snapshot.tickets]}
    )
    _assert_acyclic(_native_dependencies(snapshot))


def _policy_shape(policy: dict[str, Any]) -> None:
    required = {"ref", "content", "authority_grants", "allowed_capabilities", "exclusive_resources"}
    if set(policy) != required:
        raise PlanControlError(
            "POLICY_WITNESS_INVALID",
            "Policy Witness must contain ref, content, authority_grants, allowed_capabilities, and exclusive_resources",
        )
    _nonempty_string(policy["ref"], "Policy Witness ref")
    if not isinstance(policy["content"], Mapping):
        raise PlanControlError("POLICY_WITNESS_INVALID", "Policy Witness content must be an object")
    grants = policy["authority_grants"]
    if not isinstance(grants, Mapping) or set(grants) != set(_REQUIRED_ROLES):
        raise PlanControlError(
            "POLICY_WITNESS_INVALID", "Policy Witness must define all required authority roles"
        )
    for role in _REQUIRED_ROLES:
        _canonical_grants(grants[role], f"Policy Witness {role} grants")
    for field in ("allowed_capabilities", "exclusive_resources"):
        values = policy[field]
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or any(
            not isinstance(item, str) or not item for item in values
        ):
            raise PlanControlError("POLICY_WITNESS_INVALID", f"{field} must be a string list")


def _canonical_grants(value: Any, label: str) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} must be a list")
    grants: list[dict[str, str]] = []
    for grant in value:
        if not isinstance(grant, Mapping) or set(grant) != {"operation_id", "resource_id"}:
            raise PlanControlError(
                "POLICY_WITNESS_INVALID", f"{label} has an invalid Authority Grant"
            )
        grants.append(
            {
                "operation_id": _nonempty_string(grant["operation_id"], "operation_id"),
                "resource_id": _nonempty_string(grant["resource_id"], "resource_id"),
            }
        )
    if len({(grant["operation_id"], grant["resource_id"]) for grant in grants}) != len(grants):
        raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} repeats an Authority Grant")
    return sorted(grants, key=lambda grant: (grant["operation_id"], grant["resource_id"]))


def _reject_forbidden_manifest_fields(value: Any, path: tuple[str, ...] = ()) -> None:
    if path == ("policy", "witness"):
        return
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise PlanControlError("PLAN_FIELD_INVALID", "manifest field names must be strings")
            key = raw_key.lower().replace("-", "_")
            next_path = (*path, key)
            allowed_authority_review = key == "review" and path and path[-1] == "authority"
            policy_witness_detail = len(path) >= 2 and path[-2:] == ("policy", "witness")
            if key in _FORBIDDEN_MANIFEST_FIELDS and not allowed_authority_review and not policy_witness_detail:
                raise PlanControlError(
                    "PLAN_FIELD_FORBIDDEN",
                    f"PlanSpec v3 cannot contain {raw_key} at {'.'.join(next_path)}",
                )
            _reject_forbidden_manifest_fields(child, next_path)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_forbidden_manifest_fields(child, path)


def _native_dependencies(snapshot: CampaignSnapshot) -> dict[str, set[str]]:
    dependencies = {ticket.key: set() for ticket in snapshot.tickets}
    for ticket in snapshot.tickets:
        for blocker in ticket.native_blockers:
            dependencies[ticket.key].add(blocker["key"])
    return dependencies


def _assert_acyclic(dependencies: Mapping[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise PlanControlError("DEPENDENCY_CYCLE", "canonical Ticket blockers contain a cycle")
        if key in visited:
            return
        visiting.add(key)
        for blocker in sorted(dependencies[key]):
            if blocker in dependencies:
                visit(blocker)
        visiting.remove(key)
        visited.add(key)

    for key in sorted(dependencies):
        visit(key)


def _validate_plan_intent(value: Any, snapshot: CampaignSnapshot) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PlanControlError("PLAN_INTENT_INVALID", "Planning Pass output must be an object")
    expected = {
        "admitted_work",
        "dependency_additions",
        "exclusive_resources",
        "capability_requirements",
        "decision_requirements",
    }
    if set(value) != expected:
        raise PlanControlError(
            "PLAN_INTENT_INVALID", "Planning Pass output contains unsupported fields"
        )
    selected = {ticket.key for ticket in snapshot.tickets}
    admitted_raw = value["admitted_work"]
    if not isinstance(admitted_raw, Sequence) or isinstance(admitted_raw, (str, bytes)):
        raise PlanControlError("PLAN_INTENT_INVALID", "admitted_work must be a list")
    admitted = tuple(admitted_raw)
    if set(admitted) != selected or len(admitted) != len(selected):
        raise PlanControlError(
            "PLAN_INTENT_OMISSION", "Planning Pass must account for every selected Ticket"
        )
    if any(not isinstance(key, str) or not key for key in admitted):
        raise PlanControlError("PLAN_INTENT_INVALID", "admitted_work contains invalid Ticket key")
    dependencies = _native_dependencies(snapshot)
    additions = value["dependency_additions"]
    if not isinstance(additions, Sequence) or isinstance(additions, (str, bytes)):
        raise PlanControlError("PLAN_INTENT_INVALID", "dependency_additions must be a list")
    canonical_additions: list[dict[str, str]] = []
    for addition in additions:
        if not isinstance(addition, Mapping) or set(addition) != {"from", "to", "reason"}:
            raise PlanControlError("PLAN_INTENT_INVALID", "dependency addition must contain from, to, reason")
        from_key = _nonempty_string(addition["from"], "dependency from")
        to_key = _nonempty_string(addition["to"], "dependency to")
        reason = _nonempty_string(addition["reason"], "dependency reason")
        if from_key not in selected or to_key not in selected or from_key == to_key:
            raise PlanControlError("PLAN_INTENT_INVALID", "dependency addition is outside selected work")
        dependencies[from_key].add(to_key)
        canonical_additions.append({"from": from_key, "to": to_key, "reason": reason})
    if len({(item["from"], item["to"]) for item in canonical_additions}) != len(canonical_additions):
        raise PlanControlError("PLAN_INTENT_INVALID", "dependency additions repeat an edge")
    _assert_acyclic(dependencies)
    exclusive = _per_ticket_string_lists(
        value["exclusive_resources"], selected, "exclusive_resources"
    )
    allowed_resources = set(snapshot.policy["exclusive_resources"])
    if any(resource not in allowed_resources for values in exclusive.values() for resource in values):
        raise PlanControlError(
            "EXCLUSIVE_RESOURCE_INVALID", "Planning Pass named a resource outside repository policy"
        )
    capabilities = _per_ticket_string_lists(
        value["capability_requirements"], selected, "capability_requirements"
    )
    allowed_capabilities = set(snapshot.policy["allowed_capabilities"])
    if any(capability not in allowed_capabilities for values in capabilities.values() for capability in values):
        raise PlanControlError(
            "CAPABILITY_INVALID", "Planning Pass named a capability outside repository policy"
        )
    decisions = value["decision_requirements"]
    if not isinstance(decisions, Sequence) or isinstance(decisions, (str, bytes)):
        raise PlanControlError("PLAN_INTENT_INVALID", "decision_requirements must be a list")
    canonical_decisions: list[dict[str, str]] = []
    for decision in decisions:
        if not isinstance(decision, Mapping) or set(decision) != {"code", "detail"}:
            raise PlanControlError("PLAN_INTENT_INVALID", "Decision requirement must contain code and detail")
        canonical_decisions.append(
            {
                "code": _nonempty_string(decision["code"], "Decision code"),
                "detail": _nonempty_string(decision["detail"], "Decision detail"),
            }
        )
    return {
        "admitted_work": sorted(admitted),
        "dependency_additions": sorted(canonical_additions, key=lambda item: (item["from"], item["to"])),
        "exclusive_resources": {key: sorted(exclusive[key]) for key in sorted(exclusive)},
        "capability_requirements": {key: sorted(capabilities[key]) for key in sorted(capabilities)},
        "decision_requirements": sorted(canonical_decisions, key=lambda item: item["code"]),
    }


def _per_ticket_string_lists(value: Any, selected: set[str], label: str) -> dict[str, list[str]]:
    if not isinstance(value, Mapping) or not set(value).issubset(selected):
        raise PlanControlError(
            "PLAN_INTENT_INVALID", f"{label} may name only selected Tickets"
        )
    normalized: dict[str, list[str]] = {}
    for key, raw_values in value.items():
        if not isinstance(raw_values, Sequence) or isinstance(raw_values, (str, bytes)) or any(
            not isinstance(item, str) or not item for item in raw_values
        ):
            raise PlanControlError("PLAN_INTENT_INVALID", f"{label} values must be string lists")
        values = list(raw_values)
        if len(set(values)) != len(values):
            raise PlanControlError("PLAN_INTENT_INVALID", f"{label} repeats a value")
        normalized[key] = values
    for key in selected:
        normalized.setdefault(key, [])
    return normalized


def _raise_decision_requirements(intent: dict[str, Any], snapshot_digest: str) -> None:
    decisions = intent["decision_requirements"]
    if not decisions:
        return
    first = decisions[0]
    error = PlanControlError(first["code"], first["detail"])
    error.snapshot_digest = snapshot_digest  # type: ignore[attr-defined]
    raise error


def _compile_v3(
    *,
    snapshot: CampaignSnapshot,
    snapshot_digest: str,
    handle: CampaignHandle,
    intent: dict[str, Any],
) -> PlanRevision:
    policy = _copy_json(snapshot.policy)
    authority = policy["authority_grants"]
    policy_digest = digest_value(policy)
    dependencies = _native_dependencies(snapshot)
    for addition in intent["dependency_additions"]:
        dependencies[addition["from"]].add(addition["to"])
    work: list[dict[str, Any]] = []
    tickets = {ticket.key: ticket for ticket in snapshot.tickets}
    for key in sorted(tickets):
        ticket = tickets[key]
        work.append(
            {
                "key": key,
                "source": ticket.source,
                "contract": ticket.contract,
                "depends_on": sorted(dependencies[key]),
                "exclusive_resources": intent["exclusive_resources"][key],
                "capabilities": intent["capability_requirements"][key],
                "authority": {
                    "policy_witness_digest": policy_digest,
                    "worker": {"grants": _canonical_grants(authority["worker"], "worker grants")},
                    "recovery_worker": {
                        "grants": _canonical_grants(authority["recovery_worker"], "recovery_worker grants")
                    },
                    "review": {"grants": _canonical_grants(authority["review"], "review grants")},
                },
            }
        )
    plan_spec = {
        "schema_version": 3,
        "repository": snapshot.repository,
        "target_branch": snapshot.target_branch,
        "campaign": {
            "key": handle.campaign_key,
            "source": snapshot.campaign_source,
            "authority": {
                "policy_witness_digest": policy_digest,
                "grants": _canonical_grants(authority["campaign"], "campaign grants"),
            },
        },
        "policy": {"ref": policy["ref"], "digest": policy_digest, "witness": policy},
        "work": work,
    }
    _reject_forbidden_manifest_fields(plan_spec)
    canonical = canonical_bytes(plan_spec)
    return PlanRevision(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        digest=digest_bytes(canonical),
        canonical_bytes=canonical,
        snapshot_digest=snapshot_digest,
    )


def _default_campaign_key(repository: str, refs: tuple[str, ...]) -> str:
    return "campaign:" + digest_value({"repository": repository, "ready_refs": sorted(refs)})[:24]
