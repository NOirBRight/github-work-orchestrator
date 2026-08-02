"""GitHub-CAS durable repository for PlanControl v3 state.

Every PlanControl transition is applied to one closed state document and
published with GitHub Contents compare-and-swap followed by exact readback.
The document intentionally includes in-flight Planning attempts and
non-executable reservations, not only activated Plans.
"""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, TypeVar

from ._canonical import canonical_bytes, digest_bytes, digest_value, load_canonical_json
from .activation import GitHubContentClient
from .plan_control import (
    ActivationReceipt,
    CampaignHandle,
    InMemoryPlanRepository,
    PlanControlError,
    PlanRevision,
    PlanningReservation,
    TicketClaimProof,
    _PlanningAttempt,
    _SplitCampaignDecisionRecord,
)
from .planning_protocol import PLANNING_OUTPUT_PROTOCOL_ID
from .runtime_gateway import CampaignPlanningSubject
from .transition import (
    WriterTransitionRecord,
    _PLANNING_EFFECT_DISPATCH_BOUNDARIES,
    _PLANNING_EFFECT_DISPATCH_FIELDS,
    _PLANNING_EFFECT_DISPATCH_MAX_ACTIVE_ENTRIES,
    _PLANNING_EFFECT_DISPATCH_PATH,
    _PLANNING_EFFECT_DISPATCH_SCHEMA,
    _planning_effect_dispatch_entry_order,
    _planning_effect_dispatch_entries_at_ref,
    _planning_effect_dispatch_ledger_bytes,
    _planning_effect_dispatch_ticket,
    _validate_planning_effect_dispatch_entries,
)


_STATE_SCHEMA = "gwo.plan.github-state.v3"
_INDEX_SCHEMA = "gwo.plan.github-index.v5"
_OBJECT_SCHEMA = "gwo.plan.github-object.v1"
_OBJECT_MANIFEST_SCHEMA = "gwo.plan.github-object-manifest.v1"
_DEFAULT_PATH = ".gwo-v8/plan-control-v3.json"
# The mutable head is deliberately tiny.  Complete snapshots, PlanSpecs,
# receipts, and in-flight records are immutable digest-addressed objects; a
# large successor therefore cannot brick every later CAS by growing one JSON
# document beyond GitHub Contents' practical limit.
_MAXIMUM_STATE_BYTES = 262_144
_OBJECT_PREFIX = ".gwo-v8/plan-control-v3/objects"
_POLICY_PATH = ".gwo-v8/policy-witness.json"
_WRITER_PATH = ".gwo-v8/writer-transition.json"
_WRITER_ACTIVATION_PATH = ".gwo/v8/active-plan.json"
_MAXIMUM_OBJECT_PART_BYTES = 196_608
_T = TypeVar("_T")

_CATEGORY_NAMES = (
    "attempts",
    "split_decisions",
    "planning_reservations",
    "pending_reservations",
    "claims",
    "revisions",
    "activations",
    "activation_receipts",
)


@dataclass(frozen=True)
class _WriterEdgeRule:
    """One complete field partition for a legal Writer-lineage edge.

    A field is never silently ignored: it is either copied byte-for-byte from
    the predecessor, derived by the edge's state transition, or explicitly a
    fresh-lineage value.  Keeping this beside the durable decoder makes the
    Writer document a state machine rather than a collection of authority
    checks.
    """

    invariant: frozenset[str]
    derived: frozenset[str]
    allowed: frozenset[str] = frozenset()


_WRITER_RECORD_FIELDS = frozenset(WriterTransitionRecord.__dataclass_fields__)
_WRITER_EDGE_RULES: dict[tuple[str | None, str], _WriterEdgeRule] = {
    # There is no predecessor for the first pending V8 lineage.  Its closed
    # status rule validates every field; the partition remains explicit so
    # adding a WriterTransitionRecord field cannot create an unchecked root.
    (None, "pending"): _WriterEdgeRule(
        invariant=frozenset(),
        derived=_WRITER_RECORD_FIELDS,
    ),
    ("pending", "cut_over"): _WriterEdgeRule(
        invariant=frozenset(
            {
                "repository",
                "writer_generation",
                "plan_digest",
                "canary_evidence_digest",
                "canary_evidence_refs",
                "canary_manifest_ref",
                "reason",
            }
        ),
        derived=frozenset(
            {
                "record_id",
                "kind",
                "status",
                "previous_writer_generation",
                "activation_id",
                "worker_capacity",
                "coordinator_capacity",
                "created_at",
            }
        ),
    ),
    ("pending", "draining"): _WriterEdgeRule(
        invariant=frozenset(
            {
                "repository",
                "activation_id",
                "plan_digest",
                "canary_evidence_digest",
                "canary_evidence_refs",
                "canary_manifest_ref",
            }
        ),
        derived=frozenset(
            {
                "record_id",
                "kind",
                "status",
                "previous_writer_generation",
                "writer_generation",
                "worker_capacity",
                "coordinator_capacity",
                "reason",
                "created_at",
            }
        ),
    ),
    ("cut_over", "draining"): _WriterEdgeRule(
        invariant=frozenset(
            {
                "repository",
                "previous_writer_generation",
                "writer_generation",
                "activation_id",
                "plan_digest",
                "canary_evidence_digest",
                "canary_evidence_refs",
                "canary_manifest_ref",
            }
        ),
        derived=frozenset(
            {
                "record_id",
                "kind",
                "status",
                "worker_capacity",
                "coordinator_capacity",
                "reason",
                "created_at",
            }
        ),
    ),
    ("draining", "rolled_back"): _WriterEdgeRule(
        invariant=frozenset(
            {
                "repository",
                "activation_id",
                "plan_digest",
                "canary_evidence_refs",
                "canary_manifest_ref",
                "worker_capacity",
                "coordinator_capacity",
                "reason",
            }
        ),
        derived=frozenset(
            {
                "record_id",
                "kind",
                "status",
                "previous_writer_generation",
                "writer_generation",
                "canary_evidence_digest",
                "created_at",
            }
        ),
    ),
    ("rolled_back", "pending"): _WriterEdgeRule(
        invariant=frozenset({"repository"}),
        derived=frozenset(
            {
                "record_id",
                "kind",
                "status",
                "previous_writer_generation",
                "activation_id",
                "worker_capacity",
                "coordinator_capacity",
                "reason",
                "created_at",
            }
        ),
        allowed=frozenset(
            {
                "writer_generation",
                "plan_digest",
                "canary_evidence_digest",
                "canary_evidence_refs",
                "canary_manifest_ref",
            }
        ),
    ),
}

if any(
    rule.invariant | rule.derived | rule.allowed != _WRITER_RECORD_FIELDS
    or (rule.invariant & rule.derived)
    or (rule.invariant & rule.allowed)
    or (rule.derived & rule.allowed)
    for rule in _WRITER_EDGE_RULES.values()
):  # pragma: no cover - import-time maintainer invariant
    raise RuntimeError("Writer-lineage edge table does not classify every field")


class WriterGenerationReadback(Protocol):
    def read_current(self, repository: str) -> object: ...


class _WriterOperation(Enum):
    """Closed PlanControl Writer operation and recovery surface."""

    READ = "read"
    NEW_ATTEMPT = "new_attempt"
    NEW_RESERVATION = "new_reservation"
    SEMANTIC_COMPLETION = "semantic_completion"
    FIRST_PUBLICATION = "first_publication"
    FIRST_ACTIVATION = "first_activation"
    RECOVER_ATTEMPT = "recover_attempt"
    RECOVER_RESERVATION = "recover_reservation"
    RECOVER_PUBLICATION = "recover_publication"
    RECOVER_ACTIVATION = "recover_activation"
    FINALIZE_COMMITTED_CLAIMS = "finalize_committed_claims"


@dataclass(frozen=True)
class _CampaignObservation:
    """One target Campaign's coherent control-ref read transaction."""

    ref_digest: str
    handle: CampaignHandle
    receipt: ActivationReceipt
    attempt: _PlanningAttempt
    revision: PlanRevision
    writer_authority: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _StagedArtifactRef:
    digest: str


class _StagedArtifactCache:
    """In-memory Artifact transaction used by coherent ref hydration.

    A GitHub ref can advance while immutable objects are being read.  Collect
    verified canonical values first and commit them to the host cache only
    after the same target observation has been re-read.  This prevents a
    rejected retry from leaving artifacts from a superseded control-ref view.
    """

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    def put_canonical(self, value: Any) -> _StagedArtifactRef:
        payload = canonical_bytes(value)
        digest = digest_bytes(payload)
        canonical = load_canonical_json(payload)
        existing = self._values.get(digest)
        if existing is not None and canonical_bytes(existing) != payload:
            raise ValueError("staged Artifact digest collision")
        self._values[digest] = canonical
        return _StagedArtifactRef(digest)

    def commit(self, artifacts: Any) -> None:
        for digest, value in self._values.items():
            reference = artifacts.put_canonical(value)
            if getattr(reference, "digest", None) != digest:
                raise ValueError("staged Artifact digest changed")


class _GitHubPlanningEffectDispatch:
    """One host-private capability for Writer-fenced Planning dispatch."""

    def __init__(self, repository: "GitHubPlanRepository") -> None:
        self._repository = repository

    def mode(self, subject: CampaignPlanningSubject) -> str:
        return self._repository.planning_progress_mode(subject)

    def enter(
        self,
        subject: CampaignPlanningSubject,
        boundary: str,
        *,
        permission_request_id: str | None = None,
    ) -> str | None:
        return self._repository._enter_planning_effect_dispatch(
            subject,
            boundary,
            permission_request_id=permission_request_id,
        )

    def resolve(
        self,
        subject: CampaignPlanningSubject,
        boundary: str,
        ticket: str,
    ) -> None:
        self._repository._resolve_planning_effect_dispatch(subject, boundary, ticket)

    def reconcile(
        self,
        subject: CampaignPlanningSubject,
        effect_proofs: tuple[tuple[str, str | None, str | None], ...],
    ) -> None:
        self._repository._reconcile_planning_effect_dispatch(
            subject,
            effect_proofs,
        )


def _governed_path(value: object, label: str) -> str:
    """Accept only one normalized repository-relative GWO control path."""

    if (
        type(value) is not str
        or not value
        or value != value.strip("/")
        or "\\" in value
        or not value.startswith(".gwo-v8/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise PlanControlError(
            "PLAN_CONTROL_COMPOSITION_INVALID",
            f"{label} must be a normalized repository-contained .gwo-v8 path",
        )
    return value


def _paths_overlap(left: str, right: str) -> bool:
    return (
        left == right
        or left.startswith(right + "/")
        or right.startswith(left + "/")
    )


def validate_github_plan_control_paths(
    *,
    policy_path: str,
    state_path: str,
    object_prefix: str,
    writer_control_path: str,
) -> tuple[str, str, str, str]:
    """Fence all production control paths before any GitHub read or write."""

    normalized = (
        _governed_path(policy_path, "Policy Witness path"),
        _governed_path(state_path, "PlanControl index path"),
        _governed_path(object_prefix, "PlanControl object prefix"),
        _governed_path(writer_control_path, "Writer Record path"),
    )
    allowed = (
        _POLICY_PATH,
        _DEFAULT_PATH,
        _OBJECT_PREFIX,
        _WRITER_PATH,
    )
    if normalized != allowed:
        raise PlanControlError(
            "PLAN_CONTROL_COMPOSITION_INVALID",
            "Production PlanControl paths must use the closed .gwo-v8 namespace registry",
        )
    if any(
        _paths_overlap(left, right)
        for index, left in enumerate(normalized)
        for right in normalized[index + 1 :]
    ):
        raise PlanControlError(
            "PLAN_CONTROL_COMPOSITION_INVALID",
            "Policy, Writer Record, PlanControl index, and governed object paths must be disjoint",
        )
    return normalized


def _exact(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            f"Durable {label} has an unknown schema",
        )
    return value


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            f"Durable {label} must be exact non-empty text",
        )
    return value


def _texts(value: object, label: str) -> tuple[str, ...]:
    if (
        type(value) is not list
        or any(type(item) is not str or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            f"Durable {label} must be unique exact text",
        )
    return tuple(value)


def _nonempty_text(value: object) -> bool:
    return type(value) is str and bool(value)


def _is_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _bytes(value: object, label: str) -> bytes:
    if type(value) is not str:
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            f"Durable {label} is not base64 text",
        )
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as error:
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            f"Durable {label} is not exact base64",
        ) from error


def _encoded(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _revision_value(revision: PlanRevision) -> dict[str, Any]:
    return {
        "repository": revision.repository,
        "campaign_key": revision.campaign_key,
        "snapshot_digest": revision.snapshot_digest,
        "canonical_bytes_base64": _encoded(revision.canonical_bytes),
        "digest": revision.digest,
    }


def _revision_from(value: object) -> PlanRevision:
    item = _exact(
        value,
        {
            "repository",
            "campaign_key",
            "snapshot_digest",
            "canonical_bytes_base64",
            "digest",
        },
        "Plan Revision",
    )
    return PlanRevision(
        repository=_text(item["repository"], "Plan Revision repository"),
        campaign_key=_text(item["campaign_key"], "Plan Revision Campaign"),
        snapshot_digest=_text(
            item["snapshot_digest"],
            "Plan Revision snapshot digest",
        ),
        canonical_bytes=_bytes(
            item["canonical_bytes_base64"],
            "Plan Revision bytes",
        ),
        digest=_text(item["digest"], "Plan Revision digest"),
    )


def _activation_value(receipt: ActivationReceipt) -> dict[str, Any]:
    return {
        **asdict(receipt),
        "ready_refs": list(receipt.ready_refs),
        "ticket_keys": list(receipt.ticket_keys),
    }


def _activation_from(value: object) -> ActivationReceipt:
    fields = set(ActivationReceipt.__dataclass_fields__)
    item = _exact(value, fields, "Activation Receipt")
    return ActivationReceipt(
        **{
            **item,
            "ready_refs": _texts(
                item["ready_refs"],
                "Activation Receipt ready refs",
            ),
            "ticket_keys": _texts(
                item["ticket_keys"],
                "Activation Receipt Ticket keys",
            ),
        }
    )


def _planning_value(reservation: PlanningReservation) -> dict[str, Any]:
    return {
        **asdict(reservation),
        "ticket_keys": list(reservation.ticket_keys),
    }


def _planning_from(value: object) -> PlanningReservation:
    fields = set(PlanningReservation.__dataclass_fields__)
    legacy_fields = fields - {
        "snapshot_artifact_digest",
        "policy_witness_digest",
        "planning_request_artifact_digest",
    }
    if type(value) is dict and set(value) == legacy_fields:
        item = {
            **value,
            "snapshot_artifact_digest": None,
            "policy_witness_digest": None,
            "planning_request_artifact_digest": None,
        }
    else:
        item = _exact(value, fields, "Planning reservation")
    return PlanningReservation(
        **{
            **item,
            "ticket_keys": _texts(
                item["ticket_keys"],
                "Planning reservation Ticket keys",
            ),
        }
    )


def _attempt_value(attempt: _PlanningAttempt) -> dict[str, Any]:
    return {
        "handle": asdict(attempt.handle),
        "ready_refs": list(attempt.ready_refs),
        "ticket_keys": list(attempt.ticket_keys),
        "expected_previous_revision_digest": (
            attempt.expected_previous_revision_digest
        ),
        "snapshot_bytes_base64": _encoded(attempt.snapshot_bytes),
        "snapshot_artifact_digest": attempt.snapshot_artifact_digest,
        "policy_witness_digest": attempt.policy_witness_digest,
        "planning_request_artifact_digest": (
            attempt.planning_request_artifact_digest
        ),
        "planning_protocol_id": attempt.planning_protocol_id,
        "subject": attempt.subject.canonical(),
        "compilation_record_artifact_digest": (
            attempt.compilation_record_artifact_digest
        ),
        "revision": (
            None if attempt.revision is None else _revision_value(attempt.revision)
        ),
        "compilation_record_bytes_base64": (
            None
            if attempt.compilation_record_bytes is None
            else _encoded(attempt.compilation_record_bytes)
        ),
    }


def _attempt_from(value: object) -> _PlanningAttempt:
    fields = {
        "handle",
        "ready_refs",
        "ticket_keys",
        "expected_previous_revision_digest",
        "snapshot_bytes_base64",
        "snapshot_artifact_digest",
        "policy_witness_digest",
        "planning_request_artifact_digest",
        "planning_protocol_id",
        "subject",
        "compilation_record_artifact_digest",
        "revision",
        "compilation_record_bytes_base64",
    }
    legacy_fields = fields - {"planning_protocol_id"}
    if type(value) is dict and set(value) == legacy_fields:
        value = {**value, "planning_protocol_id": PLANNING_OUTPUT_PROTOCOL_ID}
    item = _exact(
        value,
        fields,
        "Planning attempt",
    )
    handle_value = _exact(
        item["handle"],
        {"repository", "campaign_key"},
        "Campaign handle",
    )
    subject_value = _exact(
        item["subject"],
        {
            "kind",
            "repository",
            "campaign_key",
            "campaign_handle",
            "expected_previous_plan_revision_digest",
            "snapshot_artifact_digest",
            "policy_witness_digest",
            "planning_request_artifact_digest",
            "stable_action_id",
        },
        "Planning subject",
    )
    if subject_value["kind"] != "campaign_planning":
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "Durable Planning subject kind is invalid",
        )
    revision_value = item["revision"]
    return _PlanningAttempt(
        handle=CampaignHandle(
            _text(handle_value["repository"], "Campaign repository"),
            _text(handle_value["campaign_key"], "Campaign key"),
        ),
        ready_refs=_texts(item["ready_refs"], "Planning ready refs"),
        ticket_keys=_texts(item["ticket_keys"], "Planning Ticket keys"),
        expected_previous_revision_digest=item[
            "expected_previous_revision_digest"
        ],
        snapshot_bytes=_bytes(
            item["snapshot_bytes_base64"],
            "Planning snapshot bytes",
        ),
        snapshot_artifact_digest=_text(
            item["snapshot_artifact_digest"],
            "Planning snapshot digest",
        ),
        policy_witness_digest=_text(
            item["policy_witness_digest"],
            "Planning Policy Witness digest",
        ),
        planning_request_artifact_digest=_text(
            item["planning_request_artifact_digest"],
            "Planning request digest",
        ),
        planning_protocol_id=_text(
            item["planning_protocol_id"],
            "Planning protocol id",
        ),
        subject=CampaignPlanningSubject(
            **{
                key: child
                for key, child in subject_value.items()
                if key != "kind"
            }
        ),
        compilation_record_artifact_digest=item[
            "compilation_record_artifact_digest"
        ],
        revision=(
            None if revision_value is None else _revision_from(revision_value)
        ),
        compilation_record_bytes=(
            None
            if item["compilation_record_bytes_base64"] is None
            else _bytes(
                item["compilation_record_bytes_base64"],
                "Planning compilation record bytes",
            )
        ),
    )


def _split_value(record: _SplitCampaignDecisionRecord) -> dict[str, Any]:
    return {
        "handle": asdict(record.handle),
        "ready_refs": list(record.ready_refs),
        "ticket_keys": list(record.ticket_keys),
        "expected_previous_revision_digest": (
            record.expected_previous_revision_digest
        ),
        "canonical_bytes_base64": _encoded(record.canonical_bytes),
        "digest": record.digest,
    }


def _split_from(value: object) -> _SplitCampaignDecisionRecord:
    item = _exact(
        value,
        {
            "handle",
            "ready_refs",
            "ticket_keys",
            "expected_previous_revision_digest",
            "canonical_bytes_base64",
            "digest",
        },
        "split-Campaign Decision",
    )
    handle = _exact(
        item["handle"],
        {"repository", "campaign_key"},
        "split-Campaign handle",
    )
    return _SplitCampaignDecisionRecord(
        handle=CampaignHandle(
            _text(handle["repository"], "split-Campaign repository"),
            _text(handle["campaign_key"], "split-Campaign key"),
        ),
        ready_refs=_texts(item["ready_refs"], "split-Campaign ready refs"),
        ticket_keys=_texts(item["ticket_keys"], "split-Campaign Ticket keys"),
        expected_previous_revision_digest=item[
            "expected_previous_revision_digest"
        ],
        canonical_bytes=_bytes(
            item["canonical_bytes_base64"],
            "split-Campaign Decision bytes",
        ),
        digest=_text(item["digest"], "split-Campaign Decision digest"),
    )


def _empty_state(repository: str, writer_generation: str) -> dict[str, Any]:
    return {
        "schema_version": _STATE_SCHEMA,
        "repository": repository,
        "writer_generation": writer_generation,
        "writer_fence": {
            "repository": repository,
            "writer_generation": writer_generation,
        },
        "attempts": [],
        "split_decisions": [],
        "planning_reservations": [],
        "pending_reservations": [],
        "claims": [],
        "revisions": [],
        "activations": [],
        "activation_receipts": [],
    }


def _repo_value(
    repository: str,
    writer_generation: str,
    repo: InMemoryPlanRepository,
) -> dict[str, Any]:
    return {
        "schema_version": _STATE_SCHEMA,
        "repository": repository,
        "writer_generation": writer_generation,
        "writer_fence": {
            "repository": repository,
            "writer_generation": writer_generation,
        },
        "attempts": sorted(
            (_attempt_value(item) for item in repo.attempts.values()),
            key=lambda item: (
                item["handle"]["campaign_key"],
                item["expected_previous_revision_digest"] or "",
            ),
        ),
        "split_decisions": sorted(
            (_split_value(item) for item in repo.split_decisions.values()),
            key=lambda item: (
                item["handle"]["campaign_key"],
                item["expected_previous_revision_digest"] or "",
            ),
        ),
        "planning_reservations": sorted(
            (
                _planning_value(item)
                for item in repo.planning_reservations.values()
            ),
            key=lambda item: (
                item["campaign_key"],
                item["stable_action_id"],
            ),
        ),
        "pending_reservations": sorted(
            (
                _activation_value(item)
                for item in repo.pending_reservations.values()
            ),
            key=lambda item: (
                item["campaign_key"],
                item["revision_digest"],
            ),
        ),
        "claims": [
            {
                "ticket_key": ticket_key,
                "revision_digest": revision_digest,
                "campaign_key": repo._claim_campaigns[
                    (claim_repository, ticket_key)
                ],
            }
            for (claim_repository, ticket_key), revision_digest in sorted(
                repo.claims.items()
            )
        ],
        "revisions": sorted(
            (_revision_value(item) for item in repo.revisions.values()),
            key=lambda item: item["digest"],
        ),
        "activations": sorted(
            (_activation_value(item) for item in repo.activations.values()),
            key=lambda item: item["campaign_key"],
        ),
        "activation_receipts": sorted(
            (
                _activation_value(item)
                for item in repo.activation_receipts.values()
            ),
            key=lambda item: (
                item["campaign_key"],
                item["revision_digest"],
                item["planning_stable_action_id"],
            ),
        ),
    }


def _repo_from_state(
    value: object,
    repository: str,
    writer_generation: str,
) -> InMemoryPlanRepository:
    state = _exact(
        value,
        set(_empty_state(repository, writer_generation)),
        "PlanControl state",
    )
    if (
        state["schema_version"] != _STATE_SCHEMA
        or state["repository"] != repository
        or state["writer_generation"] != writer_generation
    ):
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "Durable PlanControl state changed its repository or writer fence",
        )
    writer_fence = _exact(
        state["writer_fence"],
        {"repository", "writer_generation"},
        "writer fence",
    )
    if writer_fence != {
        "repository": repository,
        "writer_generation": writer_generation,
    }:
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "Durable PlanControl writer fence changed identity",
        )
    list_fields = {
        "attempts",
        "split_decisions",
        "planning_reservations",
        "pending_reservations",
        "claims",
        "revisions",
        "activations",
        "activation_receipts",
    }
    if any(type(state[field]) is not list for field in list_fields):
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "Durable PlanControl state collections must be exact lists",
        )
    repo = InMemoryPlanRepository(writer_generation=writer_generation)
    try:
        for raw in state["attempts"]:
            attempt = _attempt_from(raw)
            if attempt.handle.repository != repository:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Planning attempt belongs to another repository",
                )
            key = repo._attempt_key(
                attempt.handle,
                attempt.expected_previous_revision_digest,
            )
            if key in repo.attempts:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable Planning attempts repeat an identity",
                )
            repo.attempts[key] = attempt
        for raw in state["split_decisions"]:
            decision = _split_from(raw)
            key = repo._attempt_key(
                decision.handle,
                decision.expected_previous_revision_digest,
            )
            if decision.handle.repository != repository or key in repo.split_decisions:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable split-Campaign Decisions repeat or cross repositories",
                )
            repo.split_decisions[key] = decision
        for raw in state["planning_reservations"]:
            reservation = _planning_from(raw)
            key = repo._planning_reservation_key(reservation)
            if reservation.repository != repository or key in repo.planning_reservations:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable Planning reservations repeat or cross repositories",
                )
            repo.planning_reservations[key] = reservation
        for raw in state["pending_reservations"]:
            receipt = _activation_from(raw)
            key = repo._reservation_key(receipt)
            if receipt.repository != repository or key in repo.pending_reservations:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable activation reservations repeat or cross repositories",
                )
            repo.pending_reservations[key] = receipt
        for raw in state["claims"]:
            item = _exact(
                raw,
                {"ticket_key", "revision_digest", "campaign_key"},
                "Ticket claim",
            )
            ticket_key = _text(item["ticket_key"], "Ticket claim key")
            key = (repository, ticket_key)
            if key in repo.claims:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable Ticket claims repeat an identity",
                )
            repo.claims[key] = _text(
                item["revision_digest"],
                "Ticket claim revision",
            )
            repo._claim_campaigns[key] = _text(
                item["campaign_key"],
                "Ticket claim Campaign",
            )
        for raw in state["revisions"]:
            revision = _revision_from(raw)
            if (
                revision.repository != repository
                or revision.digest in repo.revisions
            ):
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable Plan Revisions repeat or cross repositories",
                )
            repo.revisions[revision.digest] = revision
        for raw in state["activations"]:
            receipt = _activation_from(raw)
            key = (receipt.repository, receipt.campaign_key)
            if receipt.repository != repository or key in repo.activations:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable activations repeat or cross repositories",
                )
            repo.activations[key] = receipt
        for raw in state["activation_receipts"]:
            receipt = _activation_from(raw)
            key = (
                receipt.repository,
                receipt.campaign_key,
                receipt.revision_digest,
                receipt.planning_stable_action_id,
            )
            if receipt.repository != repository or key in repo.activation_receipts:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable activation receipts repeat or cross repositories",
                )
            repo.activation_receipts[key] = receipt
    except PlanControlError:
        raise
    except Exception as error:
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "Durable PlanControl state cannot be reconstructed",
        ) from error
    _validate_activation_receipt_chains(repo)
    if _repo_value(repository, writer_generation, repo) != state:
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "Durable PlanControl state is not in exact canonical order",
        )
    return repo


def _validate_activation_receipt_chains(repo: InMemoryPlanRepository) -> None:
    """Require one complete linear immutable receipt ledger per Campaign."""

    by_campaign: dict[tuple[str, str], list[ActivationReceipt]] = {}
    for receipt in repo.activation_receipts.values():
        by_campaign.setdefault(
            (receipt.repository, receipt.campaign_key),
            [],
        ).append(receipt)
    if set(by_campaign) != set(repo.activations):
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "Activation Receipt ledger and current Campaign pointers disagree",
        )
    for campaign, receipts in by_campaign.items():
        current = repo.activations[campaign]
        by_revision: dict[str, ActivationReceipt] = {}
        successors: dict[str, str] = {}
        roots: list[ActivationReceipt] = []
        for receipt in receipts:
            prior = by_revision.get(receipt.revision_digest)
            if prior is not None:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Activation Receipt ledger repeats a revision identity",
                )
            by_revision[receipt.revision_digest] = receipt
            predecessor = receipt.expected_previous_revision_digest
            if predecessor is None:
                roots.append(receipt)
                continue
            existing_successor = successors.setdefault(
                predecessor,
                receipt.revision_digest,
            )
            if existing_successor != receipt.revision_digest:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Activation Receipt ledger forks a predecessor revision",
                )
        if len(roots) != 1:
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "Activation Receipt ledger must have exactly one initial receipt",
            )
        if any(
            receipt.expected_previous_revision_digest is not None
            and receipt.expected_previous_revision_digest not in by_revision
            for receipt in receipts
        ):
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "Activation Receipt ledger has a missing predecessor",
            )
        current_key = (
            current.repository,
            current.campaign_key,
            current.revision_digest,
            current.planning_stable_action_id,
        )
        if repo.activation_receipts.get(current_key) != current:
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "Current activation has no exact immutable receipt",
            )
        seen: set[str] = set()
        cursor: ActivationReceipt | None = current
        while cursor is not None:
            if cursor.revision_digest in seen:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Activation Receipt predecessor chain is cyclic",
                )
            seen.add(cursor.revision_digest)
            predecessor = cursor.expected_previous_revision_digest
            cursor = None if predecessor is None else by_revision[predecessor]
        if seen != set(by_revision) or roots[0].revision_digest not in seen:
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "Activation Receipt ledger has orphan or truncated receipts",
            )


def _category_values_for(
    repository: str,
    writer_generation: str,
    repo: InMemoryPlanRepository,
) -> dict[str, list[Any]]:
    state = _repo_value(repository, writer_generation, repo)
    return {name: state[name] for name in _CATEGORY_NAMES}


def _repo_from_categories(
    repository: str,
    writer_generation: str,
    categories: Mapping[str, Any],
) -> InMemoryPlanRepository:
    if type(categories) is not dict or set(categories) != set(_CATEGORY_NAMES):
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "Durable PlanControl category index is incomplete",
        )
    state = _empty_state(repository, writer_generation)
    state.update(categories)
    return _repo_from_state(state, repository, writer_generation)


def _object_manifest_path(prefix: str, digest: str) -> str:
    return f"{prefix}/{digest}/manifest.json"


def _object_part_path(prefix: str, digest: str, index: int) -> str:
    return f"{prefix}/{digest}/parts/{index:06d}"


def _object_changes(
    prefix: str,
    payload: bytes,
) -> tuple[str, dict[str, bytes]]:
    """Return the immutable, chunked Git objects for one exact payload."""

    digest = digest_bytes(payload)
    parts = [
        payload[offset : offset + _MAXIMUM_OBJECT_PART_BYTES]
        for offset in range(0, len(payload), _MAXIMUM_OBJECT_PART_BYTES)
    ] or [b""]
    part_values = []
    changes: dict[str, bytes] = {}
    for index, part in enumerate(parts):
        path = _object_part_path(prefix, digest, index)
        changes[path] = part
        part_values.append(
            {
                "path": path,
                "digest": digest_bytes(part),
                "byte_length": len(part),
            }
        )
    manifest = canonical_bytes(
        {
            "schema_version": _OBJECT_MANIFEST_SCHEMA,
            "digest": digest,
            "byte_length": len(payload),
            "parts": part_values,
        }
    )
    changes[_object_manifest_path(prefix, digest)] = manifest
    return digest, changes


def _writer_index_authority(authority: Mapping[str, str]) -> dict[str, str]:
    required = {
        "repository",
        "writer_generation",
        "activation_id",
        "plan_digest",
        "canary_evidence_digest",
        "canary_evidence_refs_digest",
        "canary_manifest_ref",
    }
    if not required.issubset(authority):
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "Writer authority omitted its authoritative Activation binding",
        )
    return {name: authority[name] for name in sorted(required)}


def _index_value(
    repository: str,
    writer_generation: str,
    authority: Mapping[str, str],
    category_digests: Mapping[str, str],
) -> dict[str, Any]:
    if set(category_digests) != set(_CATEGORY_NAMES):
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "PlanControl category index has an unknown schema",
        )
    return {
        "schema_version": _INDEX_SCHEMA,
        "repository": repository,
        "writer_authority": _writer_index_authority(authority),
        "categories": {
            name: category_digests[name] for name in _CATEGORY_NAMES
        },
    }


class GitHubPlanRepository:
    """Durable PlanControl facts rooted at one exact Git control-ref CAS.

    Production keeps a tiny mutable root index and stores every collection as
    immutable chunked objects.  The old one-file path remains only for injected
    in-memory boundary doubles which cannot model a Git ref transaction.
    """

    def __init__(
        self,
        client: GitHubContentClient,
        *,
        repository: str,
        branch: str,
        writer_generation: str,
        writer_control: WriterGenerationReadback | None = None,
        path: str = _DEFAULT_PATH,
        maximum_state_bytes: int = _MAXIMUM_STATE_BYTES,
        object_prefix: str = _OBJECT_PREFIX,
        writer_control_path: str = ".gwo-v8/writer-transition.json",
    ):
        if any(
            type(value) is not str or not value
            for value in (repository, branch, writer_generation)
        ):
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "GitHub PlanControl repository identity is incomplete",
            )
        if type(maximum_state_bytes) is not int or maximum_state_bytes < 1:
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "GitHub PlanControl state bound must be positive",
            )
        normalized_paths = (
            _governed_path(path, "PlanControl index path"),
            _governed_path(object_prefix, "PlanControl object prefix"),
            _governed_path(writer_control_path, "Writer Record path"),
        )
        if normalized_paths != (
            _DEFAULT_PATH,
            _OBJECT_PREFIX,
            _WRITER_PATH,
        ):
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "PlanControl persistence paths must use the closed .gwo-v8 namespace registry",
            )
        if any(
            _paths_overlap(left, right)
            for index, left in enumerate(normalized_paths)
            for right in normalized_paths[index + 1 :]
        ):
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "PlanControl index, governed objects, and Writer Record paths must be disjoint",
            )
        self.client = client
        self.repository = repository
        self.branch = branch
        self.writer_generation = writer_generation
        self.writer_control = writer_control
        self.path = normalized_paths[0]
        self.maximum_state_bytes = maximum_state_bytes
        self.object_prefix = normalized_paths[1]
        self.writer_control_path = normalized_paths[2]

    @property
    def _uses_ref_cas(self) -> bool:
        return (
            self.writer_control is None
            and callable(getattr(self.client, "read_ref", None))
            and callable(getattr(self.client, "read_at_ref", None))
            and callable(getattr(self.client, "compare_and_swap_ref", None))
        )

    def _assert_repository(self, repository: str) -> None:
        if repository != self.repository:
            raise PlanControlError(
                "DURABLE_REPOSITORY_MISMATCH",
                "GitHub PlanControl repository is fixed at composition",
            )

    def _assert_writer(self) -> None:
        if self.writer_control is None:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "Legacy PlanControl persistence requires an exact writer reader",
            )
        try:
            current = self.writer_control.read_current(self.repository)
        except Exception as error:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "GitHub writer-generation fence is not readable",
            ) from error
        if (
            getattr(current, "repository", None) != self.repository
            or getattr(current, "writer_generation", None)
            != self.writer_generation
            or type(getattr(current, "record_id", None)) is not str
            or not current.record_id
        ):
            raise PlanControlError(
                "WRITER_FENCE_CONFLICT",
                "Configured writer generation does not own the GitHub control state",
            )

    def _writer_authority_at_ref(self, ref_digest: str) -> dict[str, str]:
        """Decode and cross-bind the complete Writer ledger at one Git ref."""

        try:
            content = self.client.read_at_ref(
                self.repository,
                ref_digest,
                self.writer_control_path,
            )
        except Exception as error:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "GitHub writer authority cannot be read at the control ref",
            ) from error
        if content is None or type(content.content) is not bytes:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "GitHub control ref has no durable Writer Record",
            )
        try:
            value = _exact(
                load_canonical_json(content.content),
                {"schema_version", "current", "records"},
                "Writer Record",
            )
            current = _exact(
                value["current"],
                {"repository", "writer_generation", "record_id"},
                "Writer Record current pointer",
            )
            records = value["records"]
        except Exception as error:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "GitHub Writer Record is not complete canonical JSON",
            ) from error
        record_id = current["record_id"]
        if (
            value["schema_version"] != 1
            or type(records) is not list
            or current["repository"] != self.repository
            or current["writer_generation"] != self.writer_generation
            or type(record_id) is not str
            or not record_id
            or record_id == "initial-writer"
        ):
            raise PlanControlError(
                "WRITER_FENCE_CONFLICT",
                "GitHub control ref does not carry the configured Writer authority",
            )
        decoded = self._decode_writer_records(records)
        receipts, active = self._writer_activation_at_ref(ref_digest)
        self._validate_writer_ledger(decoded, current, receipts, active)
        matching = [record for record in decoded if record.record_id == record_id]
        if len(matching) != 1:
            raise PlanControlError(
                "WRITER_FENCE_CONFLICT",
                "GitHub Writer Record current pointer does not identify one transition",
            )
        record = matching[0]
        if record.status not in {"cut_over", "draining"}:
            raise PlanControlError(
                "WRITER_FENCE_CONFLICT",
                "GitHub Writer Transition Record is not an active Writer authority",
            )
        cut_over_records = [
            candidate
            for candidate in decoded
            if candidate.status == "cut_over"
            and candidate.writer_generation == record.writer_generation
            and candidate.activation_id == record.activation_id
            and candidate.plan_digest == record.plan_digest
        ]
        if len(cut_over_records) != 1:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "GitHub Writer authority has no exact cut-over lineage",
            )
        cut_over_record = cut_over_records[0]
        if (
            record.status == "cut_over"
            and cut_over_record.record_id != record.record_id
        ):
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "GitHub Writer cut-over authority changed its exact lineage",
            )
        return {
            "repository": self.repository,
            "writer_generation": self.writer_generation,
            "record_id": record.record_id,
            "cut_over_record_id": cut_over_record.record_id,
            "status": record.status,
            "activation_id": record.activation_id or "",
            "plan_digest": record.plan_digest or "",
            "canary_evidence_digest": record.canary_evidence_digest or "",
            "canary_evidence_refs_digest": digest_value(
                list(record.canary_evidence_refs)
            ),
            "canary_manifest_ref": record.canary_manifest_ref or "",
        }

    def _decode_writer_records(
        self,
        records: object,
    ) -> tuple[WriterTransitionRecord, ...]:
        fields = set(WriterTransitionRecord.__dataclass_fields__)
        if type(records) is not list:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "GitHub Writer Record history must be one exact list",
            )
        decoded: list[WriterTransitionRecord] = []
        try:
            for item in records:
                raw = _exact(item, fields, "Writer Transition Record")
                refs = raw["canary_evidence_refs"]
                if type(refs) is not list:
                    raise TypeError("Writer Transition Record references")
                decoded.append(
                    WriterTransitionRecord(
                        **{**raw, "canary_evidence_refs": tuple(refs)}
                    )
                )
        except (TypeError, PlanControlError) as error:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "GitHub Writer Record has a malformed transition record",
            ) from error
        return tuple(decoded)

    def _writer_activation_at_ref(
        self,
        ref_digest: str,
    ) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
        """Read the authoritative V8 activation receipt ledger at this ref."""

        try:
            content = self.client.read_at_ref(
                self.repository,
                ref_digest,
                _WRITER_ACTIVATION_PATH,
            )
        except Exception as error:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "GitHub Writer authority activation cannot be read at the control ref",
            ) from error
        if content is None or type(content.content) is not bytes:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "GitHub Writer authority has no authoritative Activation Receipt",
            )
        receipt_fields = {
            "schema_version",
            "repository",
            "writer_generation",
            "activation_id",
            "plan_digest",
            "expected_previous_digest",
            "plan_record_ref",
            "created_at",
        }
        try:
            value = _exact(
                load_canonical_json(content.content),
                {"schema_version", "repository", "active_plan_digest", "receipts"},
                "Writer Activation ledger",
            )
            if (
                value["schema_version"] != 1
                or value["repository"] != self.repository
                or type(value["receipts"]) is not list
                or not _is_digest(value["active_plan_digest"])
            ):
                raise ValueError("activation ledger identity")
            receipts: dict[str, dict[str, object]] = {}
            for raw in value["receipts"]:
                receipt = _exact(raw, receipt_fields, "Writer Activation Receipt")
                if (
                    receipt["schema_version"] != 1
                    or receipt["repository"] != self.repository
                    or not _nonempty_text(receipt["writer_generation"])
                    or not _nonempty_text(receipt["activation_id"])
                    or not _is_digest(receipt["plan_digest"])
                    or (
                        receipt["expected_previous_digest"] is not None
                        and not _is_digest(receipt["expected_previous_digest"])
                    )
                    or not _nonempty_text(receipt["plan_record_ref"])
                    or not _nonempty_text(receipt["created_at"])
                    or receipt["activation_id"] in receipts
                ):
                    raise ValueError("activation receipt")
                receipts[receipt["activation_id"]] = receipt
            active_matches = [
                receipt
                for receipt in receipts.values()
                if receipt["plan_digest"] == value["active_plan_digest"]
            ]
            if len(active_matches) != 1:
                raise ValueError("active activation")
            # The Writer ledger has one immutable Plan-activation lineage as
            # well.  A selected head is not enough: reject detached, forked,
            # or replayed historical authority before a Writer record may bind
            # to any of it.
            by_plan: dict[str, dict[str, object]] = {}
            successors: set[str] = set()
            roots: list[str] = []
            for receipt in receipts.values():
                plan_digest = receipt["plan_digest"]
                if plan_digest in by_plan:
                    raise ValueError("duplicate activation plan")
                by_plan[plan_digest] = receipt
            for receipt in receipts.values():
                predecessor = receipt["expected_previous_digest"]
                if predecessor is None:
                    roots.append(receipt["plan_digest"])
                    continue
                if predecessor not in by_plan or predecessor in successors:
                    raise ValueError("activation predecessor")
                successors.add(predecessor)
            if len(roots) != 1:
                raise ValueError("activation roots")
            seen: set[str] = set()
            cursor = active_matches[0]
            while True:
                plan_digest = cursor["plan_digest"]
                if plan_digest in seen:
                    raise ValueError("activation cycle")
                seen.add(plan_digest)
                predecessor = cursor["expected_previous_digest"]
                if predecessor is None:
                    break
                cursor = by_plan[predecessor]
            if seen != set(by_plan) or roots[0] not in seen:
                raise ValueError("activation orphan")
            return receipts, active_matches[0]
        except Exception as error:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "GitHub Writer authoritative Activation Receipt ledger is malformed",
            ) from error

    def _validate_writer_ledger(
        self,
        records: tuple[WriterTransitionRecord, ...],
        current: Mapping[str, object],
        receipts: Mapping[str, Mapping[str, object]],
        active: Mapping[str, object],
    ) -> None:
        """Validate the complete append-only Writer lineage as one machine.

        Every legal edge declares all fields as invariant, derived, or an
        explicitly constrained change.  This avoids separate status checks
        drifting into partial authority comparisons.
        """
        if not records or len({record.record_id for record in records}) != len(records):
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "GitHub Writer Record history is empty or repeats a record identity",
            )
        transitionable: dict[str, WriterTransitionRecord] = {}
        previous_transition: WriterTransitionRecord | None = None
        for record in records:
            self._validate_writer_record(record, receipts)
            prior = previous_transition
            if record.status == "blocked":
                self._validate_writer_blocked(prior, record)
            else:
                self._validate_writer_edge(prior, record, receipts)
            if record.status in {"pending", "cut_over", "draining", "rolled_back"}:
                if record.record_id in transitionable:
                    raise PlanControlError("WRITER_FENCE_READBACK_INVALID", "Writer transition history forks a record identity")
                transitionable[record.record_id] = record
                previous_transition = record
        selected = transitionable.get(str(current["record_id"]))
        if (
            selected is None
            or current["repository"] != self.repository
            or current["writer_generation"] != selected.writer_generation
            or selected.writer_generation != self.writer_generation
            or selected.status not in {"cut_over", "draining"}
            or selected.activation_id != active["activation_id"]
            or selected.plan_digest != active["plan_digest"]
            or selected.writer_generation != active["writer_generation"]
        ):
            raise PlanControlError(
                "WRITER_FENCE_CONFLICT",
                "Writer current record is not bound to the authoritative active Activation Receipt",
            )

    @staticmethod
    def _writer_fields_equal(
        prior: WriterTransitionRecord,
        record: WriterTransitionRecord,
        fields: tuple[str, ...],
        detail: str,
    ) -> None:
        if any(getattr(prior, field) != getattr(record, field) for field in fields):
            raise PlanControlError("WRITER_FENCE_READBACK_INVALID", detail)

    def _validate_writer_edge(
        self,
        prior: WriterTransitionRecord | None,
        record: WriterTransitionRecord,
        receipts: Mapping[str, Mapping[str, object]],
    ) -> None:
        """Closed transition table for every field of every active record."""
        edge = (None if prior is None else prior.status, record.status)
        rule = _WRITER_EDGE_RULES.get(edge)
        if rule is None:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "Writer transition history has an illegal status edge",
            )
        if prior is None:
            # A root is a genuine V8 cutover, never a no-op rewrite of the
            # legacy generation.  Its status validator supplies the remaining
            # field constraints declared in the root table row.
            if record.previous_writer_generation == record.writer_generation:
                raise PlanControlError(
                    "WRITER_FENCE_READBACK_INVALID",
                    "Writer root pending transition did not change generation",
                )
            return
        self._writer_fields_equal(
            prior,
            record,
            tuple(sorted(rule.invariant)),
            "Writer transition substituted an invariant lineage field",
        )
        if edge == ("pending", "cut_over"):
            if (
                record.writer_generation != prior.writer_generation
                or record.previous_writer_generation != record.writer_generation
                or record.activation_id is None
            ):
                raise PlanControlError(
                    "WRITER_FENCE_READBACK_INVALID",
                    "Writer cutover did not derive its generation and Activation fields",
                )
        elif edge == ("pending", "draining"):
            if (
                record.writer_generation != prior.writer_generation
                or record.previous_writer_generation != prior.writer_generation
            ):
                raise PlanControlError(
                    "WRITER_FENCE_READBACK_INVALID",
                    "Writer pending drain did not preserve the V8 generation",
                )
        elif edge == ("cut_over", "draining"):
            # All authority fields, including every canary reference, are
            # invariant in this table row.  The status validator constrains
            # capacities/reason without a second partial field comparison.
            pass
        elif edge == ("draining", "rolled_back"):
            if (
                record.previous_writer_generation != prior.writer_generation
                or record.writer_generation == prior.writer_generation
                or record.canary_evidence_digest is not None
            ):
                raise PlanControlError(
                    "WRITER_FENCE_READBACK_INVALID",
                    "Writer rollback did not derive its restored generation or evidence clearing",
                )
            if record.activation_id is not None:
                receipt = receipts.get(record.activation_id)
                if (
                    receipt is None
                    or receipt["writer_generation"] != prior.writer_generation
                    or receipt["plan_digest"] != record.plan_digest
                ):
                    raise PlanControlError(
                        "WRITER_FENCE_READBACK_INVALID",
                        "Writer rollback is not bound to the V8 Activation it rolls back",
                    )
        elif edge == ("rolled_back", "pending"):
            if (
                record.previous_writer_generation != prior.writer_generation
                or record.writer_generation == prior.writer_generation
            ):
                raise PlanControlError(
                    "WRITER_FENCE_READBACK_INVALID",
                    "Writer fresh pending transition does not start from rollback authority",
                )

    def _validate_writer_blocked(
        self,
        prior: WriterTransitionRecord | None,
        record: WriterTransitionRecord,
    ) -> None:
        """Validate non-authority ledger entries without advancing lineage."""

        expected_generation = (
            "v6.1" if prior is None else prior.writer_generation
        )
        if record.previous_writer_generation != expected_generation:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "Blocked Writer record does not bind the immediately preceding authority",
            )

    def _validate_writer_record(
        self,
        record: WriterTransitionRecord,
        receipts: Mapping[str, Mapping[str, object]],
    ) -> None:
        legal = {
            ("cutover_pending", "pending"),
            ("cutover", "cut_over"),
            ("drain", "draining"),
            ("rollback", "rolled_back"),
            ("cutover", "blocked"),
            ("rollback", "blocked"),
            ("rollback_restore", "blocked"),
        }
        if (
            (record.kind, record.status) not in legal
            or record.repository != self.repository
            or not _nonempty_text(record.record_id)
            or not _nonempty_text(record.previous_writer_generation)
            or not _nonempty_text(record.writer_generation)
            or not _nonempty_text(record.created_at)
            or type(record.worker_capacity) is not int
            or type(record.coordinator_capacity) is not int
            or record.worker_capacity < 0
            or record.coordinator_capacity < 0
            or type(record.canary_evidence_refs) is not tuple
            or len(set(record.canary_evidence_refs)) != len(record.canary_evidence_refs)
            or any(not _nonempty_text(item) for item in record.canary_evidence_refs)
            or (record.activation_id is not None and not _nonempty_text(record.activation_id))
            or (record.plan_digest is not None and not _is_digest(record.plan_digest))
            or (record.canary_evidence_digest is not None and not _is_digest(record.canary_evidence_digest))
            or (record.canary_manifest_ref is not None and not _nonempty_text(record.canary_manifest_ref))
            or (record.reason is not None and not _nonempty_text(record.reason))
        ):
            raise PlanControlError("WRITER_FENCE_READBACK_INVALID", "Writer Transition Record has invalid closed fields")
        identity = {
            "repository": record.repository,
            "kind": record.kind,
            "status": record.status,
            "previous_writer_generation": record.previous_writer_generation,
            "writer_generation": record.writer_generation,
            "activation_id": record.activation_id,
            "plan_digest": record.plan_digest,
            "canary_evidence_digest": record.canary_evidence_digest,
            "canary_evidence_refs": record.canary_evidence_refs,
            "canary_manifest_ref": record.canary_manifest_ref,
            "worker_capacity": record.worker_capacity,
            "coordinator_capacity": record.coordinator_capacity,
            "reason": record.reason,
        }
        if record.record_id != f"writer-transition:{digest_value(identity)[:24]}":
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "Writer Transition Record identity does not bind its complete lineage fields",
            )
        active_status = record.status in {"pending", "cut_over", "draining", "rolled_back"}
        if active_status and not _is_digest(record.plan_digest):
            raise PlanControlError("WRITER_FENCE_READBACK_INVALID", "Writer transition omitted its Plan binding")
        if record.status == "pending":
            valid = (
                record.activation_id is None
                and _is_digest(record.canary_evidence_digest)
                and bool(record.canary_evidence_refs)
                and _nonempty_text(record.canary_manifest_ref)
                and record.worker_capacity == 0
                and record.coordinator_capacity == 0
                and record.reason is None
            )
        elif record.status == "cut_over":
            valid = (
                _nonempty_text(record.activation_id)
                and _is_digest(record.canary_evidence_digest)
                and bool(record.canary_evidence_refs)
                and _nonempty_text(record.canary_manifest_ref)
                and record.worker_capacity == 8
                and record.coordinator_capacity == 1
                and record.reason is None
            )
        elif record.status == "draining":
            valid = (
                (record.activation_id is None or _nonempty_text(record.activation_id))
                and _is_digest(record.canary_evidence_digest)
                and bool(record.canary_evidence_refs)
                and _nonempty_text(record.canary_manifest_ref)
                and record.worker_capacity == 0
                and record.coordinator_capacity == 0
                and _nonempty_text(record.reason)
            )
        elif record.status == "rolled_back":
            valid = (
                # A pending cutover may be drained/rolled back before an
                # Activation exists; a committed rollback carries the V8
                # Activation identity in ``previous_writer_generation``.
                (record.activation_id is None or _nonempty_text(record.activation_id))
                and bool(record.canary_evidence_refs)
                and _nonempty_text(record.canary_manifest_ref)
                and record.canary_evidence_digest is None
                and record.worker_capacity == 0
                and record.coordinator_capacity == 0
                and _nonempty_text(record.reason)
            )
        else:
            valid = record.worker_capacity == 0 and record.coordinator_capacity == 0 and _nonempty_text(record.reason)
        if not valid:
            raise PlanControlError("WRITER_FENCE_READBACK_INVALID", "Writer Transition Record has an invalid status binding")
        if record.activation_id is not None and record.status != "rolled_back":
            receipt = receipts.get(record.activation_id)
            if (
                receipt is None
                or receipt["repository"] != record.repository
                or receipt["writer_generation"] != record.writer_generation
                or receipt["plan_digest"] != record.plan_digest
            ):
                raise PlanControlError("WRITER_FENCE_READBACK_INVALID", "Writer Transition Record is not bound to its authoritative Activation Receipt")

    def _read_object_at_ref(self, ref_digest: str, digest: str) -> bytes:
        try:
            manifest_blob = self.client.read_at_ref(
                self.repository,
                ref_digest,
                _object_manifest_path(self.object_prefix, digest),
            )
        except Exception as error:
            raise PlanControlError(
                "DURABLE_STATE_UNAVAILABLE",
                "GitHub governed object manifest cannot be read",
            ) from error
        if manifest_blob is None or type(manifest_blob.content) is not bytes:
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "GitHub governed object manifest is missing",
            )
        try:
            manifest = load_canonical_json(manifest_blob.content)
            manifest = _exact(
                manifest,
                {"schema_version", "digest", "byte_length", "parts"},
                "governed object manifest",
            )
        except Exception as error:
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "GitHub governed object manifest is invalid",
            ) from error
        parts = manifest["parts"]
        if (
            manifest["schema_version"] != _OBJECT_MANIFEST_SCHEMA
            or manifest["digest"] != digest
            or type(manifest["byte_length"]) is not int
            or manifest["byte_length"] < 0
            or type(parts) is not list
            or not parts
        ):
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "GitHub governed object manifest changed its identity",
            )
        values: list[bytes] = []
        for index, raw_part in enumerate(parts):
            try:
                part = _exact(
                    raw_part,
                    {"path", "digest", "byte_length"},
                    "governed object part",
                )
            except PlanControlError as error:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "GitHub governed object part is malformed",
                ) from error
            expected_path = _object_part_path(self.object_prefix, digest, index)
            if (
                part["path"] != expected_path
                or type(part["digest"]) is not str
                or type(part["byte_length"]) is not int
                or not 0 <= part["byte_length"] <= _MAXIMUM_OBJECT_PART_BYTES
            ):
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "GitHub governed object part changed its identity",
                )
            try:
                blob = self.client.read_at_ref(
                    self.repository,
                    ref_digest,
                    expected_path,
                )
            except Exception as error:
                raise PlanControlError(
                    "DURABLE_STATE_UNAVAILABLE",
                    "GitHub governed object part cannot be read",
                ) from error
            if (
                blob is None
                or type(blob.content) is not bytes
                or len(blob.content) != part["byte_length"]
                or digest_bytes(blob.content) != part["digest"]
            ):
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "GitHub governed object part did not read back exactly",
                )
            values.append(blob.content)
        payload = b"".join(values)
        if (
            len(payload) != manifest["byte_length"]
            or digest_bytes(payload) != digest
        ):
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "GitHub governed object payload changed its digest",
            )
        return payload

    def _read_ref_state(
        self,
    ) -> tuple[InMemoryPlanRepository, str, bytes | None, dict[str, str]]:
        try:
            ref_digest = self.client.read_ref(self.repository, self.branch)
        except Exception as error:
            raise PlanControlError(
                "DURABLE_STATE_UNAVAILABLE",
                "GitHub PlanControl control ref cannot be read",
            ) from error
        if type(ref_digest) is not str or not ref_digest:
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "GitHub PlanControl control ref is malformed",
            )
        authority = self._writer_authority_at_ref(ref_digest)
        try:
            root = self.client.read_at_ref(
                self.repository,
                ref_digest,
                self.path,
            )
        except Exception as error:
            raise PlanControlError(
                "DURABLE_STATE_UNAVAILABLE",
                "GitHub PlanControl index cannot be read",
            ) from error
        if root is None:
            return (
                InMemoryPlanRepository(writer_generation=self.writer_generation),
                ref_digest,
                None,
                authority,
            )
        if (
            type(root.content) is not bytes
            or type(root.blob_sha) is not str
            or not root.blob_sha
            or len(root.content) > self.maximum_state_bytes
        ):
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "GitHub PlanControl index receipt is malformed or oversized",
            )
        try:
            value = _exact(
                load_canonical_json(root.content),
                {"schema_version", "repository", "writer_authority", "categories"},
                "PlanControl index",
            )
        except Exception as error:
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "GitHub PlanControl index is not canonical JSON",
            ) from error
        if (
            value["schema_version"] != _INDEX_SCHEMA
            or value["repository"] != self.repository
            or value["writer_authority"] != _writer_index_authority(authority)
            or type(value["categories"]) is not dict
            or set(value["categories"]) != set(_CATEGORY_NAMES)
            or any(
                type(digest) is not str
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                for digest in value["categories"].values()
            )
        ):
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "GitHub PlanControl index changed its authority or categories",
            )
        categories: dict[str, Any] = {}
        for name in _CATEGORY_NAMES:
            try:
                object_value = load_canonical_json(
                    self._read_object_at_ref(
                        ref_digest,
                        value["categories"][name],
                    )
                )
                object_value = _exact(
                    object_value,
                    {
                        "schema_version",
                        "repository",
                        "writer_generation",
                        "category",
                        "items",
                    },
                    "governed category",
                )
            except PlanControlError:
                raise
            except Exception as error:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "GitHub governed category is invalid",
                ) from error
            if (
                object_value["schema_version"] != _OBJECT_SCHEMA
                or object_value["repository"] != self.repository
                or object_value["writer_generation"] != self.writer_generation
                or object_value["category"] != name
                or type(object_value["items"]) is not list
            ):
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "GitHub governed category changed its identity",
                )
            categories[name] = object_value["items"]
        return (
            _repo_from_categories(
                self.repository,
                self.writer_generation,
                categories,
            ),
            ref_digest,
            root.content,
            authority,
        )

    def _render_ref_state(
        self,
        repo: InMemoryPlanRepository,
        authority: Mapping[str, str],
    ) -> tuple[bytes, dict[str, bytes]]:
        category_digests: dict[str, str] = {}
        changes: dict[str, bytes] = {}
        for name, items in _category_values_for(
            self.repository,
            self.writer_generation,
            repo,
        ).items():
            payload = canonical_bytes(
                {
                    "schema_version": _OBJECT_SCHEMA,
                    "repository": self.repository,
                    "writer_generation": self.writer_generation,
                    "category": name,
                    "items": items,
                }
            )
            digest, object_changes = _object_changes(self.object_prefix, payload)
            category_digests[name] = digest
            changes.update(object_changes)
        rendered = canonical_bytes(
            _index_value(
                self.repository,
                self.writer_generation,
                authority,
                category_digests,
            )
        )
        if len(rendered) > self.maximum_state_bytes:
            raise PlanControlError(
                "DURABLE_STATE_TOO_LARGE",
                "GitHub PlanControl mutable index exceeds its configured bound",
            )
        changes[self.path] = rendered
        return rendered, changes

    def _mutate_ref(
        self,
        operation: str,
        callback: Callable[[InMemoryPlanRepository], _T],
        *,
        classify: Callable[[InMemoryPlanRepository], _WriterOperation],
    ) -> _T:
        repo, ref_digest, before, authority = self._read_ref_state()
        self._assert_writer_operation(authority, classify(repo))
        result = callback(repo)
        rendered, changes = self._render_ref_state(repo, authority)
        if before == rendered:
            return result
        try:
            committed = self.client.compare_and_swap_ref(
                self.repository,
                self.branch,
                expected_ref_digest=ref_digest,
                changes=changes,
                message=f"GWO PlanControl {operation}",
            )
        except Exception as error:
            # An acknowledgement loss may have committed the exact ref.  Only
            # the full indexed state at a fresh ref can recover it.
            try:
                _recovered, _ref, recovered_bytes, _authority = self._read_ref_state()
            except Exception:
                recovered_bytes = None
            if recovered_bytes != rendered:
                raise PlanControlError(
                    "DURABLE_CAS_CONFLICT",
                    "GitHub control-ref CAS did not commit the exact transition",
                ) from error
            return result
        if type(committed) is not str or not committed:
            raise PlanControlError(
                "DURABLE_STATE_READBACK_INVALID",
                "GitHub control-ref CAS omitted its committed ref",
            )
        _readback, readback_ref, readback_bytes, _authority = self._read_ref_state()
        if readback_ref != committed or readback_bytes != rendered:
            raise PlanControlError(
                "DURABLE_STATE_READBACK_INVALID",
                "GitHub control-ref transition did not read back exactly",
            )
        return result

    def _read(self) -> tuple[InMemoryPlanRepository, str | None, bytes | None]:
        if self._uses_ref_cas:
            repo, ref_digest, rendered, _authority = self._read_ref_state()
            return repo, ref_digest, rendered
        self._assert_writer()
        try:
            content = self.client.read(
                self.repository,
                self.branch,
                self.path,
            )
        except Exception as error:
            raise PlanControlError(
                "DURABLE_STATE_UNAVAILABLE",
                "GitHub PlanControl state cannot be read",
            ) from error
        if content is None:
            return (
                InMemoryPlanRepository(
                    writer_generation=self.writer_generation
                ),
                None,
                None,
            )
        if (
            type(content.content) is not bytes
            or type(content.blob_sha) is not str
            or not content.blob_sha
            or len(content.content) > self.maximum_state_bytes
        ):
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "GitHub PlanControl content receipt is malformed or oversized",
            )
        try:
            state = load_canonical_json(content.content)
            repo = _repo_from_state(
                state,
                self.repository,
                self.writer_generation,
            )
        except PlanControlError:
            raise
        except Exception as error:
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "GitHub PlanControl state is not canonical JSON",
            ) from error
        return repo, content.blob_sha, content.content

    def _mutate(
        self,
        operation: str,
        callback: Callable[[InMemoryPlanRepository], _T],
        *,
        classify: Callable[[InMemoryPlanRepository], _WriterOperation] | None = None,
    ) -> _T:
        if self._uses_ref_cas:
            return self._mutate_ref(
                operation,
                callback,
                classify=(
                    self._new_work_operation
                    if classify is None
                    else classify
                ),
            )
        repo, blob_sha, before = self._read()
        result = callback(repo)
        rendered = canonical_bytes(
            _repo_value(self.repository, self.writer_generation, repo)
        )
        if len(rendered) > self.maximum_state_bytes:
            raise PlanControlError(
                "DURABLE_STATE_TOO_LARGE",
                "GitHub PlanControl state exceeds its configured bound",
            )
        if before == rendered:
            return result
        try:
            written = self.client.compare_and_swap(
                self.repository,
                self.branch,
                self.path,
                rendered,
                expected_blob_sha=blob_sha,
                message=f"GWO PlanControl {operation}",
            )
            if written.content != rendered:
                raise PlanControlError(
                    "DURABLE_STATE_READBACK_INVALID",
                    "GitHub CAS receipt returned different PlanControl bytes",
                )
        except PlanControlError:
            raise
        except Exception as error:
            try:
                recovered = self.client.read(
                    self.repository,
                    self.branch,
                    self.path,
                )
            except Exception:
                recovered = None
            if recovered is None or recovered.content != rendered:
                raise PlanControlError(
                    "DURABLE_CAS_CONFLICT",
                    "GitHub PlanControl CAS did not commit the exact transition",
                ) from error
        try:
            readback = self.client.read(
                self.repository,
                self.branch,
                self.path,
            )
        except Exception as error:
            raise PlanControlError(
                "DURABLE_STATE_READBACK_INVALID",
                "GitHub PlanControl transition cannot be read back",
            ) from error
        if readback is None or readback.content != rendered:
            raise PlanControlError(
                "DURABLE_STATE_READBACK_INVALID",
                "GitHub PlanControl transition did not read back exactly",
            )
        return result

    @staticmethod
    def _new_work_operation(_repo: InMemoryPlanRepository) -> _WriterOperation:
        return _WriterOperation.NEW_ATTEMPT

    @staticmethod
    def _claim_operation(
        repo: InMemoryPlanRepository,
        receipt: ActivationReceipt,
    ) -> _WriterOperation:
        handle = CampaignHandle(receipt.repository, receipt.campaign_key)
        if (
            repo.active_receipt(handle) == receipt
            or repo.read_pending_reservation(receipt) == receipt
        ):
            return _WriterOperation.RECOVER_RESERVATION
        return _WriterOperation.NEW_RESERVATION

    @staticmethod
    def _revision_operation(
        repo: InMemoryPlanRepository,
        revision: PlanRevision,
    ) -> _WriterOperation:
        if repo.read_revision(revision.digest) == revision:
            return _WriterOperation.RECOVER_PUBLICATION
        return _WriterOperation.FIRST_PUBLICATION

    @staticmethod
    def _attempt_operation(
        repo: InMemoryPlanRepository,
        attempt: _PlanningAttempt,
    ) -> _WriterOperation:
        existing = repo.read_attempt(
            attempt.handle,
            attempt.expected_previous_revision_digest,
        )
        reservation = repo.read_planning_reservation(
            attempt.handle,
            attempt.subject.stable_action_id,
        )
        if (
            existing is not None
            and existing.compilation_record_artifact_digest is None
            and attempt.compilation_record_artifact_digest is not None
            and reservation is not None
            and reservation.subject_digest == attempt.subject.digest
            and reservation.ticket_keys == attempt.ticket_keys
        ):
            return _WriterOperation.SEMANTIC_COMPLETION
        return (
            _WriterOperation.RECOVER_ATTEMPT
            if existing is not None
            else _WriterOperation.NEW_ATTEMPT
        )

    @staticmethod
    def _activation_operation(
        repo: InMemoryPlanRepository,
        receipt: ActivationReceipt,
    ) -> _WriterOperation:
        handle = CampaignHandle(receipt.repository, receipt.campaign_key)
        return (
            _WriterOperation.RECOVER_ACTIVATION
            if repo.active_receipt(handle) == receipt
            else _WriterOperation.FIRST_ACTIVATION
        )

    @staticmethod
    def _finalize_operation(
        repo: InMemoryPlanRepository,
        receipt: ActivationReceipt,
    ) -> _WriterOperation:
        handle = CampaignHandle(receipt.repository, receipt.campaign_key)
        claims = repo.read_campaign_claim_proofs(handle)
        exact_claims = (
            tuple(proof.ticket_key for proof in claims) == receipt.ticket_keys
            and all(proof.plan_revision_digest == receipt.revision_digest for proof in claims)
        )
        return (
            _WriterOperation.FINALIZE_COMMITTED_CLAIMS
            if repo.active_receipt(handle) == receipt
            and (repo.read_pending_reservation(receipt) == receipt or exact_claims)
            else _WriterOperation.FIRST_ACTIVATION
        )

    def _assert_writer_operation(
        self,
        authority: Mapping[str, str],
        operation: _WriterOperation,
    ) -> None:
        if type(operation) is not _WriterOperation:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "PlanControl Writer operation classification is not closed",
            )
        status = authority.get("status")
        cut_over_only = {
            _WriterOperation.NEW_ATTEMPT,
            _WriterOperation.NEW_RESERVATION,
            _WriterOperation.FIRST_PUBLICATION,
            _WriterOperation.FIRST_ACTIVATION,
        }
        draining_recovery = {
            _WriterOperation.READ,
            _WriterOperation.RECOVER_ATTEMPT,
            _WriterOperation.SEMANTIC_COMPLETION,
            _WriterOperation.RECOVER_RESERVATION,
            _WriterOperation.RECOVER_PUBLICATION,
            _WriterOperation.RECOVER_ACTIVATION,
            _WriterOperation.FINALIZE_COMMITTED_CLAIMS,
        }
        if operation in cut_over_only and status != "cut_over":
            raise PlanControlError(
                "WRITER_FENCE_CONFLICT",
                "Draining Writer authority cannot begin new PlanControl work",
            )
        if operation in draining_recovery and status not in {
            "cut_over",
            "draining",
        }:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "Writer authority cannot perform this exact recovery operation",
            )

    def _read_repo(self) -> InMemoryPlanRepository:
        return self._read()[0]

    def _hydrate_repo_artifacts(
        self,
        repo: InMemoryPlanRepository,
        artifacts: Any,
        *,
        observation: _CampaignObservation | None = None,
    ) -> None:
        """Restore governed Artifacts from one already-observed durable state."""

        def restore(value: Any, digest: str, label: str) -> None:
            try:
                reference = artifacts.put_canonical(value)
                if getattr(reference, "digest", None) != digest:
                    raise ValueError("Artifact digest changed")
            except Exception as error:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    f"GitHub governed {label} cannot restore the exact Artifact",
                ) from error

        attempts = (
            (observation.attempt,)
            if observation is not None
            else tuple(repo.attempts.values())
        )
        for attempt in attempts:
            try:
                snapshot = load_canonical_json(attempt.snapshot_bytes)
                if digest_bytes(attempt.snapshot_bytes) != attempt.snapshot_artifact_digest:
                    raise ValueError("snapshot digest")
                restore(snapshot, attempt.snapshot_artifact_digest, "snapshot")
                policy = {
                    key: value
                    for key, value in snapshot["policy"].items()
                    if key != "digest"
                }
                restore(policy, attempt.policy_witness_digest, "Policy Witness")
                from .planning_protocol import planning_prompt

                request = planning_prompt(
                    subject_digest=attempt.subject.prompt_binding_digest,
                    authority_digest=attempt.policy_witness_digest,
                    snapshot_artifact_digest=attempt.snapshot_artifact_digest,
                    policy_witness_artifact_digest=attempt.policy_witness_digest,
                )
                restore(
                    request,
                    attempt.planning_request_artifact_digest,
                    "Planning request",
                )
                if attempt.compilation_record_artifact_digest is not None:
                    if attempt.compilation_record_bytes is None:
                        raise ValueError("completed attempt omitted record bytes")
                    record = load_canonical_json(attempt.compilation_record_bytes)
                    restore(
                        record,
                        attempt.compilation_record_artifact_digest,
                        "compilation record",
                    )
                    output = record["planning_output"]
                    restore(
                        output,
                        record["output_artifact_digest"],
                        "Planning output",
                    )
            except PlanControlError:
                raise
            except Exception as error:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "GitHub governed Planning attempt cannot hydrate a fresh host",
                ) from error
        revisions = (
            (observation.revision,)
            if observation is not None
            else tuple(repo.revisions.values())
        )
        for revision in revisions:
            try:
                restore(
                    load_canonical_json(revision.canonical_bytes),
                    revision.digest,
                    "Plan Revision",
                )
            except PlanControlError:
                raise
            except Exception as error:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "GitHub governed Plan Revision cannot hydrate a fresh host",
                ) from error
        decisions = () if observation is not None else repo.split_decisions.values()
        for decision in decisions:
            try:
                restore(
                    load_canonical_json(decision.canonical_bytes),
                    decision.digest,
                    "split-Campaign Decision",
                )
            except PlanControlError:
                raise
            except Exception as error:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "GitHub governed split-Campaign Decision cannot hydrate a fresh host",
                ) from error

    @staticmethod
    def _active_identity(
        repo: InMemoryPlanRepository,
        handle: CampaignHandle,
    ) -> tuple[ActivationReceipt, _PlanningAttempt, PlanRevision] | None:
        receipt = repo.active_receipt(handle)
        if receipt is None:
            return None
        attempt = repo.read_attempt(
            handle,
            receipt.expected_previous_revision_digest,
        )
        revision = repo.read_revision(receipt.revision_digest)
        if type(attempt) is not _PlanningAttempt or type(revision) is not PlanRevision:
            return None
        return receipt, attempt, revision

    def _observation_from_state(
        self,
        repo: InMemoryPlanRepository,
        ref_digest: str,
        authority: Mapping[str, str],
        handle: CampaignHandle,
        expected_previous_revision_digest: str | None = None,
    ) -> _CampaignObservation:
        if type(handle) is not CampaignHandle:
            raise PlanControlError(
                "START_SUCCESSOR_INVALID",
                "Campaign observation requires one exact CampaignHandle",
            )
        self._assert_repository(handle.repository)
        identity = self._active_identity(repo, handle)
        if identity is None:
            raise PlanControlError(
                "ACTIVATION_CAS_CONFLICT",
                "Campaign observation has no exact active receipt, attempt, and revision",
            )
        receipt, attempt, revision = identity
        if (
            expected_previous_revision_digest is not None
            and receipt.revision_digest != expected_previous_revision_digest
        ):
            replay = repo.read_attempt(
                handle,
                expected_previous_revision_digest,
            )
            if (
                type(replay) is not _PlanningAttempt
                or replay.revision is None
                or receipt.expected_previous_revision_digest
                != expected_previous_revision_digest
                or replay.revision.digest != receipt.revision_digest
            ):
                raise PlanControlError(
                    "ACTIVATION_CAS_CONFLICT",
                    "Campaign observation does not match the required predecessor revision",
                )
        return _CampaignObservation(
            ref_digest=ref_digest,
            handle=handle,
            receipt=receipt,
            attempt=attempt,
            revision=revision,
            writer_authority=tuple(sorted(authority.items())),
        )

    def observe_campaign(
        self,
        handle: CampaignHandle,
        expected_previous_revision_digest: str,
    ) -> _CampaignObservation:
        """Read one successor predecessor from exactly one control-ref OID."""

        if (
            type(expected_previous_revision_digest) is not str
            or not _is_digest(expected_previous_revision_digest)
        ):
            raise PlanControlError(
                "START_SUCCESSOR_INVALID",
                "Campaign observation requires one exact predecessor digest",
            )
        if self._uses_ref_cas:
            repo, ref_digest, _before, authority = self._read_ref_state()
            return self._observation_from_state(
                repo,
                ref_digest,
                authority,
                handle,
                expected_previous_revision_digest,
            )
        repo, _blob, _before = self._read()
        return self._observation_from_state(
            repo,
            "legacy",
            {
                "repository": self.repository,
                "writer_generation": self.writer_generation,
            },
            handle,
            expected_previous_revision_digest,
        )

    def hydrate_campaign_artifacts(
        self,
        artifacts: Any,
        observation: _CampaignObservation,
    ) -> None:
        """Stage only one validated Campaign's immutable Artifact set.

        A changed control ref gets at most one wholly new observation.  The
        target receipt/attempt/revision and Writer authority must remain
        identical; unrelated Campaign evolution can therefore never populate
        this cache from a mixed generation.
        """

        if type(observation) is not _CampaignObservation:
            raise PlanControlError(
                "DURABLE_STATE_CONCURRENT_CHANGE",
                "Campaign Artifact hydration requires one exact observation",
            )
        if not self._uses_ref_cas:
            self._hydrate_repo_artifacts(
                self._read_repo(), artifacts, observation=observation
            )
            return
        expected = observation
        for attempt_number in range(2):
            repo, ref_digest, _before, authority = self._read_ref_state()
            current = self._observation_from_state(
                repo,
                ref_digest,
                authority,
                expected.handle,
                expected.receipt.revision_digest,
            )
            if (
                current.receipt != expected.receipt
                or current.attempt != expected.attempt
                or current.revision != expected.revision
                or current.writer_authority != expected.writer_authority
            ):
                raise PlanControlError(
                    "DURABLE_STATE_CONCURRENT_CHANGE",
                    "Campaign or Writer identity changed before Artifact hydration",
                )
            staged = _StagedArtifactCache()
            self._hydrate_repo_artifacts(repo, staged, observation=current)
            _reread, reread_ref, _rendered, reread_authority = self._read_ref_state()
            if reread_ref == ref_digest:
                try:
                    staged.commit(artifacts)
                except Exception as error:
                    raise PlanControlError(
                        "DURABLE_STATE_INVALID",
                        "GitHub governed Artifacts could not commit their coherent staged cache",
                    ) from error
                return
            # A retry must start from one new coherent observation; never
            # combine its target facts with the first read.
            if attempt_number == 1:
                raise PlanControlError(
                    "DURABLE_STATE_CONCURRENT_CHANGE",
                    "GitHub control ref did not remain coherent during target hydration",
                )
            if tuple(sorted(reread_authority.items())) != expected.writer_authority:
                raise PlanControlError(
                    "DURABLE_STATE_CONCURRENT_CHANGE",
                    "Writer authority changed during target Artifact hydration",
                )
        raise PlanControlError(
            "DURABLE_STATE_CONCURRENT_CHANGE",
            "Campaign Artifact hydration exceeded its bounded retry",
        )

    def hydrate_active_artifacts(
        self,
        artifacts: Any,
        handle: CampaignHandle,
        receipt: ActivationReceipt,
    ) -> None:
        """Hydrate one active Campaign without mixing control-ref generations.

        A long-lived host may observe a receipt after another host publishes it.
        At most one changed-ref retry is permitted, and only when the complete
        active identity remains byte-for-byte the same.
        """

        if self._uses_ref_cas:
            repo, ref_digest, _before, authority = self._read_ref_state()
            observation = self._observation_from_state(
                repo,
                ref_digest,
                authority,
                handle,
                receipt.revision_digest,
            )
            if observation.receipt != receipt:
                raise PlanControlError(
                    "DURABLE_STATE_CONCURRENT_CHANGE",
                    "GitHub active Campaign changed before governed Artifact hydration",
                )
            self.hydrate_campaign_artifacts(artifacts, observation)
            return
        self._hydrate_repo_artifacts(self._read_repo(), artifacts)

    def active_receipt(self, handle: CampaignHandle) -> ActivationReceipt | None:
        self._assert_repository(handle.repository)
        return self._read_repo().active_receipt(handle)

    def read_attempt(
        self,
        handle: CampaignHandle,
        expected_previous_revision_digest: str | None,
    ) -> _PlanningAttempt | None:
        self._assert_repository(handle.repository)
        return self._read_repo().read_attempt(
            handle,
            expected_previous_revision_digest,
        )

    def save_attempt(self, attempt: _PlanningAttempt) -> _PlanningAttempt:
        self._assert_repository(attempt.handle.repository)
        return self._mutate(
            "save Planning attempt",
            lambda repo: repo.save_attempt(attempt),
            classify=lambda repo: self._attempt_operation(repo, attempt),
        )

    def read_split_decision(
        self,
        handle: CampaignHandle,
        expected_previous_revision_digest: str | None,
    ) -> _SplitCampaignDecisionRecord | None:
        self._assert_repository(handle.repository)
        return self._read_repo().read_split_decision(
            handle,
            expected_previous_revision_digest,
        )

    def save_split_decision(
        self,
        decision: _SplitCampaignDecisionRecord,
    ) -> _SplitCampaignDecisionRecord:
        self._assert_repository(decision.handle.repository)
        return self._mutate(
            "save split-Campaign Decision",
            lambda repo: repo.save_split_decision(decision),
            classify=lambda repo: (
                _WriterOperation.RECOVER_ATTEMPT
                if repo.read_split_decision(
                    decision.handle,
                    decision.expected_previous_revision_digest,
                )
                is not None
                else _WriterOperation.NEW_ATTEMPT
            ),
        )

    def reserve_planning(self, reservation: PlanningReservation) -> None:
        self._assert_repository(reservation.repository)
        self._mutate(
            "reserve Planning",
            lambda repo: repo.reserve_planning(reservation),
        )

    def release_planning(self, reservation: PlanningReservation) -> None:
        self._assert_repository(reservation.repository)
        self._mutate(
            "release Planning",
            lambda repo: repo.release_planning(reservation),
            classify=lambda repo: (
                _WriterOperation.RECOVER_RESERVATION
                if repo.read_planning_reservation(
                    CampaignHandle(reservation.repository, reservation.campaign_key),
                    reservation.stable_action_id,
                ) == reservation
                else _WriterOperation.FIRST_ACTIVATION
            ),
        )

    def read_planning_reservation(
        self,
        handle: CampaignHandle,
        stable_action_id: str,
    ) -> PlanningReservation | None:
        self._assert_repository(handle.repository)
        return self._read_repo().read_planning_reservation(
            handle,
            stable_action_id,
        )

    def planning_progress_mode(self, subject: CampaignPlanningSubject) -> str:
        """Return the exact Writer state consumed inside Gateway.progress.

        This is a host-composition policy callback, never a caller-visible
        RuntimeGateway operation.  It reads one validated Writer authority and
        exposes only the closed progress mode for this repository.
        """

        if (
            type(subject) is not CampaignPlanningSubject
            or subject.repository != self.repository
        ):
            raise PlanControlError(
                "WRITER_FENCE_CONFLICT",
                "Planning progress policy was requested for another repository",
            )
        _repo, _ref, _before, authority = self._read_ref_state()
        self._assert_writer_operation(authority, _WriterOperation.READ)
        status = authority.get("status")
        if type(status) is not str or status not in {"cut_over", "draining"}:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "Writer authority cannot select a Planning progress mode",
            )
        return status

    def planning_effect_dispatch(self) -> object:
        """Return the one private host capability for Planning provider I/O."""

        return _GitHubPlanningEffectDispatch(self)

    def _planning_effect_dispatch_entries(
        self,
        ref_digest: str,
    ) -> tuple[dict[str, Any], ...]:
        try:
            return _planning_effect_dispatch_entries_at_ref(
                self.client,
                self.repository,
                ref_digest,
            )
        except Exception as error:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "Planning effect dispatch ledger is unavailable or malformed",
            ) from error

    @staticmethod
    def _dispatch_entry_matches_subject(
        entry: Mapping[str, Any],
        subject: CampaignPlanningSubject,
        boundary: str,
        authority: Mapping[str, str],
        *,
        permission_request_id: str | None = None,
    ) -> bool:
        return (
            entry.get("repository") == subject.repository
            and entry.get("campaign_key") == subject.campaign_key
            and entry.get("campaign_handle") == subject.campaign_handle
            and entry.get("subject_digest") == subject.digest
            and entry.get("stable_action_id") == subject.stable_action_id
            and entry.get("effect_boundary") == boundary
            and entry.get("permission_request_id") == permission_request_id
            and entry.get("permission_decision")
            == ("allow" if boundary == "permission_allow" else None)
            and entry.get("writer_generation") == authority.get("writer_generation")
            and entry.get("writer_cut_over_record_id")
            == authority.get("cut_over_record_id")
        )

    @staticmethod
    def _dispatch_entry_for_action(
        entry: Mapping[str, Any],
        subject: CampaignPlanningSubject,
        authority: Mapping[str, str],
    ) -> bool:
        return (
            GitHubPlanRepository._dispatch_entry_same_action_lineage(
                entry,
                subject,
                authority,
            )
            and entry.get("campaign_key") == subject.campaign_key
            and entry.get("campaign_handle") == subject.campaign_handle
            and entry.get("subject_digest") == subject.digest
        )

    @staticmethod
    def _dispatch_entry_same_action_lineage(
        entry: Mapping[str, Any],
        subject: CampaignPlanningSubject,
        authority: Mapping[str, str],
    ) -> bool:
        return (
            entry.get("repository") == subject.repository
            and entry.get("stable_action_id") == subject.stable_action_id
            and entry.get("writer_generation") == authority.get("writer_generation")
            and entry.get("writer_cut_over_record_id")
            == authority.get("cut_over_record_id")
        )

    @staticmethod
    def _dispatch_entry_for_boundary(
        entry: Mapping[str, Any],
        subject: CampaignPlanningSubject,
        boundary: str,
        authority: Mapping[str, str],
    ) -> bool:
        return (
            GitHubPlanRepository._dispatch_entry_same_action_lineage(
                entry,
                subject,
                authority,
            )
            and entry.get("effect_boundary") == boundary
        )

    def _compact_planning_effect_dispatch_entries(
        self,
        entries: list[dict[str, Any]],
        *,
        retain_ticket: str | None = None,
    ) -> tuple[list[dict[str, Any]], bytes]:
        """Retain active facts and trim only ordered recovery evidence."""

        if (
            sum(entry["state"] == "active" for entry in entries)
            > _PLANNING_EFFECT_DISPATCH_MAX_ACTIVE_ENTRIES
        ):
            raise PlanControlError(
                "PLANNING_EFFECT_DISPATCH_BOUNDED",
                "Planning effect dispatch has reached its active-ticket budget",
            )
        compacted = list(entries)
        while True:
            ordered = sorted(
                compacted,
                key=_planning_effect_dispatch_entry_order,
            )
            try:
                _validate_planning_effect_dispatch_entries(
                    self.repository,
                    ordered,
                )
                return ordered, _planning_effect_dispatch_ledger_bytes(
                    self.repository,
                    ordered,
                )
            except ValueError as error:
                recovery = next(
                    (
                        entry
                        for entry in ordered
                        if (
                            entry["state"] == "recovery"
                            and entry["ticket"] != retain_ticket
                        )
                    ),
                    None,
                )
                if recovery is None:
                    raise PlanControlError(
                        "PLANNING_EFFECT_DISPATCH_BOUNDED",
                        "Planning effect dispatch cannot fit one exact retained ticket",
                    ) from error
                compacted.remove(recovery)

    def _enter_planning_effect_dispatch(
        self,
        subject: CampaignPlanningSubject,
        boundary: str,
        *,
        permission_request_id: str | None = None,
    ) -> str | None:
        """Publish one active dispatch fence before the adapter may be called."""

        if (
            type(subject) is not CampaignPlanningSubject
            or subject.repository != self.repository
            or type(boundary) is not str
            or boundary not in _PLANNING_EFFECT_DISPATCH_BOUNDARIES
            or (
                boundary == "permission_allow"
                and (type(permission_request_id) is not str or not permission_request_id)
            )
            or (
                boundary != "permission_allow"
                and permission_request_id is not None
            )
            or not self._uses_ref_cas
        ):
            return None
        _repo, ref_digest, _before, authority = self._read_ref_state()
        entries = self._planning_effect_dispatch_entries(ref_digest)
        if any(
            self._dispatch_entry_same_action_lineage(entry, subject, authority)
            and not self._dispatch_entry_for_action(entry, subject, authority)
            for entry in entries
        ):
            return None
        boundary_entries = [
            entry
            for entry in entries
            if self._dispatch_entry_for_boundary(
                entry,
                subject,
                boundary,
                authority,
            )
        ]
        if len(boundary_entries) > 1:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "Planning effect dispatch has duplicate boundary identities",
            )
        previous = None if not boundary_entries else boundary_entries[0]
        if (
            previous is not None
            and previous["state"] == "active"
            and not self._dispatch_entry_matches_subject(
            previous,
            subject,
            boundary,
            authority,
                permission_request_id=permission_request_id,
            )
        ):
            # The stable Planning action/boundary has already produced
            # recovery evidence for another exact Campaign subject.  Never
            # overwrite it or turn it into a new provider-effect authority.
            return None
        if previous is not None and previous["state"] == "active":
            return str(previous["ticket"])
        if any(
            entry["state"] == "active"
            and self._dispatch_entry_for_action(entry, subject, authority)
            for entry in entries
        ):
            return None
        if authority.get("status") != "cut_over" or (
            authority.get("record_id") != authority.get("cut_over_record_id")
        ):
            return None
        if (
            sum(entry["state"] == "active" for entry in entries)
            >= _PLANNING_EFFECT_DISPATCH_MAX_ACTIVE_ENTRIES
        ):
            raise PlanControlError(
                "PLANNING_EFFECT_DISPATCH_BOUNDED",
                "Planning effect dispatch has reached its active-ticket budget",
            )
        attempt = 1 if previous is None else int(previous["attempt"]) + 1
        identity = {
            "repository": self.repository,
            "campaign_key": subject.campaign_key,
            "campaign_handle": subject.campaign_handle,
            "subject_digest": subject.digest,
            "stable_action_id": subject.stable_action_id,
            "effect_boundary": boundary,
            "permission_request_id": permission_request_id,
            "permission_decision": (
                "allow" if boundary == "permission_allow" else None
            ),
            "writer_generation": authority["writer_generation"],
            "writer_cut_over_record_id": authority["cut_over_record_id"],
            "writer_observation_ref": ref_digest,
            "attempt": attempt,
        }
        ticket = _planning_effect_dispatch_ticket(identity)
        entry = {
            **identity,
            "ticket": ticket,
            "state": "active",
        }
        if set(entry) != _PLANNING_EFFECT_DISPATCH_FIELDS:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "Planning effect dispatch entry has an invalid closed schema",
            )
        updated = list(entries)
        if previous is None:
            updated.append(entry)
        else:
            updated[updated.index(previous)] = entry
        updated, rendered = self._compact_planning_effect_dispatch_entries(
            updated
        )
        try:
            committed = self.client.compare_and_swap_ref(
                self.repository,
                self.branch,
                expected_ref_digest=ref_digest,
                changes={_PLANNING_EFFECT_DISPATCH_PATH: rendered},
                message="GWO enter Planning provider dispatch",
            )
        except Exception:
            _recovered, recovered_ref, _before, recovered_authority = (
                self._read_ref_state()
            )
            recovered_entries = self._planning_effect_dispatch_entries(recovered_ref)
            recovered = [
                value
                for value in recovered_entries
                if self._dispatch_entry_matches_subject(
                    value,
                    subject,
                    boundary,
                    recovered_authority,
                    permission_request_id=permission_request_id,
                )
                and value["state"] == "active"
            ]
            if len(recovered) == 1:
                return str(recovered[0]["ticket"])
            return None
        if type(committed) is not str or not committed:
            raise PlanControlError(
                "DURABLE_STATE_READBACK_INVALID",
                "Planning effect dispatch CAS omitted its committed control ref",
            )
        return ticket

    def _resolve_planning_effect_dispatch(
        self,
        subject: CampaignPlanningSubject,
        boundary: str,
        ticket: str,
    ) -> None:
        """Close one active dispatch after a provider result or ambiguity."""

        if (
            type(subject) is not CampaignPlanningSubject
            or subject.repository != self.repository
            or type(boundary) is not str
            or boundary not in _PLANNING_EFFECT_DISPATCH_BOUNDARIES
            or type(ticket) is not str
            or not ticket
            or not self._uses_ref_cas
        ):
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "Planning effect dispatch resolution lacks one exact identity",
            )
        _repo, ref_digest, _before, authority = self._read_ref_state()
        entries = list(self._planning_effect_dispatch_entries(ref_digest))
        matching = [entry for entry in entries if entry["ticket"] == ticket]
        if len(matching) != 1:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "Planning effect dispatch ticket is stale or missing",
            )
        entry = matching[0]
        if not self._dispatch_entry_matches_subject(
            entry,
            subject,
            boundary,
            authority,
            permission_request_id=entry["permission_request_id"],
        ):
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "Planning effect dispatch ticket does not bind this subject and boundary",
            )
        if entry["state"] == "recovery":
            return
        if entry["state"] != "active":
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "Planning effect dispatch ticket is outside its active state",
            )
        resolved = {**entry, "state": "recovery"}
        entries[entries.index(entry)] = resolved
        _compacted, rendered = self._compact_planning_effect_dispatch_entries(
            entries,
            retain_ticket=ticket,
        )
        try:
            committed = self.client.compare_and_swap_ref(
                self.repository,
                self.branch,
                expected_ref_digest=ref_digest,
                changes={_PLANNING_EFFECT_DISPATCH_PATH: rendered},
                message="GWO resolve Planning provider dispatch",
            )
        except Exception as error:
            _repo, recovered_ref, _before, recovered_authority = self._read_ref_state()
            recovered = self._planning_effect_dispatch_entries(recovered_ref)
            if any(
                value["ticket"] == ticket
                and value["state"] == "recovery"
                and self._dispatch_entry_matches_subject(
                    value,
                    subject,
                    boundary,
                    recovered_authority,
                    permission_request_id=value["permission_request_id"],
                )
                for value in recovered
            ):
                return
            raise PlanControlError(
                "DURABLE_CAS_CONFLICT",
                "Planning effect dispatch resolution did not commit",
            ) from error
        if type(committed) is not str or not committed:
            raise PlanControlError(
                "DURABLE_STATE_READBACK_INVALID",
                "Planning effect dispatch resolution omitted its control ref",
            )

    def _reconcile_planning_effect_dispatch(
        self,
        subject: CampaignPlanningSubject,
        effect_proofs: tuple[tuple[str, str | None, str | None], ...],
    ) -> None:
        """Resolve only an active ticket whose readback proves its effect."""

        if (
            type(subject) is not CampaignPlanningSubject
            or subject.repository != self.repository
            or type(effect_proofs) is not tuple
            or not effect_proofs
            or any(
                type(proof) is not tuple
                or len(proof) != 3
                or type(proof[0]) is not str
                or proof[0] not in _PLANNING_EFFECT_DISPATCH_BOUNDARIES
                or (
                    proof[0] == "permission_allow"
                    and (
                        type(proof[1]) is not str
                        or not proof[1]
                        or proof[2] != "allow"
                    )
                )
                or (
                    proof[0] != "permission_allow"
                    and (proof[1] is not None or proof[2] is not None)
                )
                for proof in effect_proofs
            )
            or not self._uses_ref_cas
        ):
            return
        _repo, ref_digest, _before, authority = self._read_ref_state()
        entries = self._planning_effect_dispatch_entries(ref_digest)
        active = [
            entry
            for entry in entries
            if entry["state"] == "active"
            and self._dispatch_entry_for_action(entry, subject, authority)
        ]
        if len(active) > 1:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "Planning action has more than one active dispatch",
            )
        if not active:
            return
        entry = active[0]
        if (
            entry["effect_boundary"],
            entry["permission_request_id"],
            entry["permission_decision"],
        ) in effect_proofs:
            self._resolve_planning_effect_dispatch(
                subject,
                str(entry["effect_boundary"]),
                str(entry["ticket"]),
            )

    def reserve_claims(self, receipt: ActivationReceipt) -> None:
        self._assert_repository(receipt.repository)
        self._mutate(
            "reserve activation claims",
            lambda repo: repo.reserve_claims(receipt),
            classify=lambda repo: self._claim_operation(repo, receipt),
        )

    def publish_revision(self, revision: PlanRevision) -> None:
        self._assert_repository(revision.repository)
        self._mutate(
            "publish Plan Revision",
            lambda repo: repo.publish_revision(revision),
            classify=lambda repo: self._revision_operation(repo, revision),
        )

    def read_revision(self, digest: str) -> PlanRevision | None:
        return self._read_repo().read_revision(digest)

    def activate(self, receipt: ActivationReceipt) -> None:
        self._assert_repository(receipt.repository)
        # ``InMemoryPlanRepository.activate`` removes the losing pending
        # reservation before it reports a CAS conflict.  Preserve that cleanup
        # as a durable transition instead of letting an exception discard the
        # reconstructed state in this adapter.
        def transition(repo: InMemoryPlanRepository) -> PlanControlError | None:
            try:
                repo.activate(receipt)
            except PlanControlError as error:
                if error.code != "ACTIVATION_CAS_CONFLICT":
                    raise
                return error
            return None

        conflict = self._mutate(
            "activate Plan Revision",
            transition,
            classify=lambda repo: self._activation_operation(repo, receipt),
        )
        if conflict is not None:
            raise conflict

    def finalize_claims(self, receipt: ActivationReceipt) -> None:
        self._assert_repository(receipt.repository)
        self._mutate(
            "finalize Ticket claims",
            lambda repo: repo.finalize_claims(receipt),
            classify=lambda repo: self._finalize_operation(repo, receipt),
        )

    def read_activation(
        self,
        handle: CampaignHandle,
    ) -> ActivationReceipt | None:
        self._assert_repository(handle.repository)
        return self._read_repo().read_activation(handle)

    def read_pending_reservation(
        self,
        receipt: ActivationReceipt,
    ) -> ActivationReceipt | None:
        self._assert_repository(receipt.repository)
        return self._read_repo().read_pending_reservation(receipt)

    def read_claim_proofs(
        self,
        handle: CampaignHandle,
        revision_digest: str,
    ) -> tuple[TicketClaimProof, ...]:
        self._assert_repository(handle.repository)
        return self._read_repo().read_claim_proofs(handle, revision_digest)

    def read_campaign_claim_proofs(
        self,
        handle: CampaignHandle,
    ) -> tuple[TicketClaimProof, ...]:
        self._assert_repository(handle.repository)
        return self._read_repo().read_campaign_claim_proofs(handle)
