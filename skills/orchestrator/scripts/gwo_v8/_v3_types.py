"""Private V3 PlanControl value types and state vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


STATE_SNAPSHOTTED = "SNAPSHOTTED"
STATE_CLAIMS_RESERVED = "CLAIMS_RESERVED"
STATE_PLANNING_STARTED = "PLANNING_STARTED"
STATE_INTENT_ACCEPTED = "INTENT_ACCEPTED"
STATE_DECISION_REQUIRED = "DECISION_REQUIRED"
STATE_PLANNING_AMBIGUOUS = "PLANNING_AMBIGUOUS"
STATE_PLAN_PUBLISHED = "PLAN_PUBLISHED"
STATE_ACTIVATION_COMMITTED = "ACTIVATION_COMMITTED"
STATE_ACTIVE_LOCAL = "ACTIVE_LOCAL"

DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
AUTO_PREVIOUS = object()


class PlanControlError(RuntimeError):
    """A stable fail-closed PlanControl error."""

    code: str
    detail: str
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, order=True)
class DecisionFinding:
    code: str
    detail: str
    ticket_key: str | None = None

    def as_value(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "ticket_key": self.ticket_key,
        }


class PlanControlDecision(PlanControlError):
    """A complete, digest-addressed aggregate Decision."""

    repository: str
    campaign_key: str
    snapshot_digest: str
    planning_action_id: str
    findings: tuple[DecisionFinding, ...]
    decision_digest: str
    __slots__ = (
        "repository",
        "campaign_key",
        "snapshot_digest",
        "planning_action_id",
        "findings",
        "decision_digest",
    )

    def __init__(
        self,
        *,
        repository: str,
        campaign_key: str,
        snapshot_digest: str,
        planning_action_id: str,
        findings: tuple[DecisionFinding, ...],
        decision_digest: str,
    ):
        super().__init__(
            "PLAN_CONTROL_DECISION_REQUIRED",
            "PlanControl requires a durable Decision before publication",
        )
        self.repository = repository
        self.campaign_key = campaign_key
        self.snapshot_digest = snapshot_digest
        self.planning_action_id = planning_action_id
        self.findings = findings
        self.decision_digest = decision_digest


@dataclass(frozen=True)
class CampaignHandle:
    repository: str
    campaign_key: str


@dataclass(frozen=True)
class Content:
    content: bytes
    blob_sha: str


@dataclass(frozen=True)
class WriterWitness:
    repository: str
    writer_generation: str
    v8_start_allowed: bool
    digest: str


@dataclass(frozen=True)
class PlanRevision:
    repository: str
    campaign_key: str
    snapshot_digest: str
    canonical_bytes: bytes
    digest: str

    @property
    def plan_spec(self) -> dict[str, Any]:
        from ._v3_canonical import strict_json_decode

        return strict_json_decode(self.canonical_bytes)


@dataclass(frozen=True)
class ActivationReceipt:
    repository: str
    campaign_key: str
    revision_digest: str
    expected_previous_revision_digest: str | None
    writer_generation: str
    writer_witness_digest: str
    snapshot_digest: str
    planning_action_id: str
    ticket_keys: tuple[str, ...]

    def as_value(self) -> dict[str, Any]:
        return {
            "schema_version": 3,
            "repository": self.repository,
            "campaign_key": self.campaign_key,
            "revision_digest": self.revision_digest,
            "expected_previous_revision_digest": (
                self.expected_previous_revision_digest
            ),
            "writer_generation": self.writer_generation,
            "writer_witness_digest": self.writer_witness_digest,
            "snapshot_digest": self.snapshot_digest,
            "planning_action_id": self.planning_action_id,
            "ticket_keys": list(self.ticket_keys),
        }


@dataclass(frozen=True)
class ActiveCampaign:
    handle: CampaignHandle
    revision: PlanRevision
    receipt: ActivationReceipt

    @property
    def canonical_bytes(self) -> bytes:
        return self.revision.canonical_bytes

    @property
    def plan_spec(self) -> dict[str, Any]:
        return self.revision.plan_spec


@dataclass(frozen=True)
class JournalRecord:
    repository: str
    campaign_key: str
    snapshot_digest: str
    state: str
    snapshot_bytes: bytes
    options_bytes: bytes
    options_digest: str
    planning_action_id: str
    expected_previous_revision_digest: str | None
    writer_generation: str | None = None
    writer_witness_digest: str | None = None
    intent_bytes: bytes | None = None
    intent_digest: str | None = None
    decision_bytes: bytes | None = None
    decision_digest: str | None = None
    plan_bytes: bytes | None = None
    plan_digest: str | None = None
    receipt_bytes: bytes | None = None
    receipt_digest: str | None = None
