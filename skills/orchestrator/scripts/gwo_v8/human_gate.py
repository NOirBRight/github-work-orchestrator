"""Durable, read-only contracts for the V8 human scope and authority gate."""

from __future__ import annotations

from base64 import b64decode, b64encode
from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol

from ._canonical import CanonicalJsonError, digest_bytes, digest_value, load_canonical_json
from .plan_control import CampaignHandle
from .planning_protocol import REPLANNING_OUTPUT_PROTOCOL_ID


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_DECISION_ID = re.compile(r"^decision:[0-9a-f]{24}$")

HUMAN_REQUIRED_CHANGES = (
    "new_ticket",
    "acceptance",
    "campaign_membership",
    "authority",
    "product",
    "replan_budget",
)
HUMAN_SOURCE_KINDS = ("tracker", "policy", "tracker_and_policy", "none")
HUMAN_SOURCE_STATES = (
    "pending",
    "approved",
    "rejected",
    "incomplete",
    "ambiguous",
    "reverted",
    "out_of_policy",
)
HUMAN_GATE_PHASES = (
    "awaiting_human_choice",
    "awaiting_durable_tracker_policy_readback",
    "planning_validated_successor",
    "active_successor",
    "rejected_change",
    "budget_exhausted",
)

_STATE_CODES = {
    "pending": "HUMAN_SOURCE_READBACK_PENDING",
    "approved": "HUMAN_SOURCE_APPROVED",
    "rejected": "HUMAN_SOURCE_REJECTED",
    "incomplete": "HUMAN_SOURCE_READBACK_INCOMPLETE",
    "ambiguous": "HUMAN_SOURCE_AMBIGUOUS",
    "reverted": "HUMAN_SOURCE_REVERTED",
    "out_of_policy": "HUMAN_SOURCE_OUT_OF_POLICY",
}
_REQUIRED_SOURCE_KIND = {
    "new_ticket": "tracker",
    "acceptance": "tracker",
    "campaign_membership": "tracker",
    "product": "tracker",
    "authority": "policy",
    "replan_budget": "none",
}


class HumanGateError(RuntimeError):
    """A named fail-closed human-gate contract error."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _fail(code: str, detail: str) -> None:
    raise HumanGateError(code, detail)


def _text(value: Any, label: str, *, code: str) -> str:
    if type(value) is not str or not value:
        _fail(code, f"{label} must be non-empty exact text")
    return value


def _digest(value: Any, label: str, *, code: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _fail(code, f"{label} must be a lowercase SHA-256 digest")
    return value


def _tuple_digests(value: Any, *, code: str) -> tuple[str, ...]:
    if type(value) is not tuple or not value:
        _fail(code, "Evidence digests must be a non-empty tuple")
    result = tuple(value)
    if any(type(item) is not str or _DIGEST.fullmatch(item) is None for item in result):
        _fail(code, "Evidence digests must be SHA-256 digests")
    if tuple(sorted(set(result))) != result:
        _fail(code, "Evidence digests must be sorted and unique")
    return result


def _closed(value: Any, expected: set[str], label: str, *, code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected:
        _fail(code, f"{label} schema is not closed")
    return value


def _campaign_canonical(campaign: CampaignHandle) -> dict[str, str]:
    if type(campaign) is not CampaignHandle:
        _fail("HUMAN_DECISION_RECORD_INVALID", "campaign must be a CampaignHandle")
    return {"repository": campaign.repository, "campaign_key": campaign.campaign_key}


def _campaign_from_canonical(value: Any, *, code: str) -> CampaignHandle:
    raw = _closed(value, {"repository", "campaign_key"}, "campaign", code=code)
    try:
        return CampaignHandle(
            repository=_text(raw["repository"], "campaign.repository", code=code),
            campaign_key=_text(raw["campaign_key"], "campaign.campaign_key", code=code),
        )
    except HumanGateError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise HumanGateError(code, "campaign cannot be decoded") from error


def _bytes_to_canonical(value: bytes | None, label: str, *, code: str) -> str | None:
    if value is None:
        return None
    if type(value) is not bytes:
        _fail(code, f"{label} must be bytes or None")
    return b64encode(value).decode("ascii")


def _bytes_from_canonical(value: Any, label: str, *, code: str) -> bytes | None:
    if value is None:
        return None
    if type(value) is not str:
        _fail(code, f"{label} must be base64 text or None")
    try:
        return b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as error:
        raise HumanGateError(code, f"{label} is not valid base64") from error


@dataclass(frozen=True)
class RequiredDurableSourceChange:
    required_change: str
    source_kind: str
    predecessor_source_digest: str
    required_subject: str
    detail: str

    @classmethod
    def source_kind_for(cls, required_change: str) -> str:
        if type(required_change) is not str or required_change not in HUMAN_REQUIRED_CHANGES:
            _fail("HUMAN_DECISION_RECORD_INVALID", "required_change is outside the closed union")
        return _REQUIRED_SOURCE_KIND[required_change]

    def __post_init__(self) -> None:
        expected = self.source_kind_for(self.required_change)
        if type(self.source_kind) is not str or self.source_kind not in HUMAN_SOURCE_KINDS:
            _fail("HUMAN_DECISION_RECORD_INVALID", "source_kind is outside the closed union")
        if self.source_kind != expected:
            _fail("HUMAN_DECISION_RECORD_INVALID", "source_kind does not match required_change")
        _digest(self.predecessor_source_digest, "predecessor_source_digest", code="HUMAN_DECISION_RECORD_INVALID")
        _text(self.required_subject, "required_subject", code="HUMAN_DECISION_RECORD_INVALID")
        _text(self.detail, "source change detail", code="HUMAN_DECISION_RECORD_INVALID")

    def canonical(self) -> dict[str, Any]:
        return {
            "kind": "gwo.human-required-source-change.v1",
            "required_change": self.required_change,
            "source_kind": self.source_kind,
            "predecessor_source_digest": self.predecessor_source_digest,
            "required_subject": self.required_subject,
            "detail": self.detail,
        }

    @classmethod
    def from_canonical(cls, value: Mapping[str, Any]) -> "RequiredDurableSourceChange":
        raw = _closed(
            value,
            {"kind", "required_change", "source_kind", "predecessor_source_digest", "required_subject", "detail"},
            "required source change",
            code="HUMAN_DECISION_RECORD_INVALID",
        )
        if raw["kind"] != "gwo.human-required-source-change.v1":
            _fail("HUMAN_DECISION_RECORD_INVALID", "required source change kind is invalid")
        try:
            return cls(**{key: raw[key] for key in (
                "required_change", "source_kind", "predecessor_source_digest", "required_subject", "detail"
            )})
        except HumanGateError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise HumanGateError("HUMAN_DECISION_RECORD_INVALID", "required source change cannot be decoded") from error


@dataclass(frozen=True)
class HumanDecisionRecord:
    decision_id: str
    campaign: CampaignHandle
    classification_action_id: str
    plan_revision_digest: str
    evidence_digests: tuple[str, ...]
    required_change: str
    detail: str
    required_source: RequiredDurableSourceChange

    def __post_init__(self) -> None:
        if type(self.decision_id) is not str or _DECISION_ID.fullmatch(self.decision_id) is None:
            _fail("HUMAN_DECISION_RECORD_INVALID", "decision_id is invalid")
        _campaign_canonical(self.campaign)
        _text(self.classification_action_id, "classification_action_id", code="HUMAN_DECISION_RECORD_INVALID")
        _digest(self.plan_revision_digest, "plan_revision_digest", code="HUMAN_DECISION_RECORD_INVALID")
        _tuple_digests(self.evidence_digests, code="HUMAN_DECISION_RECORD_INVALID")
        if type(self.required_change) is not str or self.required_change not in HUMAN_REQUIRED_CHANGES:
            _fail("HUMAN_DECISION_RECORD_INVALID", "required_change is outside the closed union")
        _text(self.detail, "Decision detail", code="HUMAN_DECISION_RECORD_INVALID")
        if type(self.required_source) is not RequiredDurableSourceChange:
            _fail("HUMAN_DECISION_RECORD_INVALID", "required_source is invalid")
        if self.required_source.required_change != self.required_change:
            _fail("HUMAN_DECISION_RECORD_INVALID", "required source does not match Decision")

    def canonical(self) -> dict[str, Any]:
        return {
            "kind": "gwo.human-decision.v1",
            "decision_id": self.decision_id,
            "campaign": _campaign_canonical(self.campaign),
            "classification_action_id": self.classification_action_id,
            "plan_revision_digest": self.plan_revision_digest,
            "evidence_digests": list(self.evidence_digests),
            "required_change": self.required_change,
            "detail": self.detail,
            "required_source": self.required_source.canonical(),
        }

    @property
    def digest(self) -> str:
        return digest_value(self.canonical())

    @classmethod
    def from_canonical(cls, value: Mapping[str, Any]) -> "HumanDecisionRecord":
        raw = _closed(
            value,
            {"kind", "decision_id", "campaign", "classification_action_id", "plan_revision_digest", "evidence_digests", "required_change", "detail", "required_source"},
            "human Decision",
            code="HUMAN_DECISION_RECORD_INVALID",
        )
        if raw["kind"] != "gwo.human-decision.v1":
            _fail("HUMAN_DECISION_RECORD_INVALID", "human Decision kind is invalid")
        if type(raw["evidence_digests"]) is not list:
            _fail("HUMAN_DECISION_RECORD_INVALID", "evidence_digests must be a list")
        try:
            return cls(
                decision_id=raw["decision_id"],
                campaign=_campaign_from_canonical(raw["campaign"], code="HUMAN_DECISION_RECORD_INVALID"),
                classification_action_id=raw["classification_action_id"],
                plan_revision_digest=raw["plan_revision_digest"],
                evidence_digests=tuple(raw["evidence_digests"]),
                required_change=raw["required_change"],
                detail=raw["detail"],
                required_source=RequiredDurableSourceChange.from_canonical(raw["required_source"]),
            )
        except HumanGateError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise HumanGateError("HUMAN_DECISION_RECORD_INVALID", "human Decision cannot be decoded") from error


@dataclass(frozen=True)
class HumanDecisionChoice:
    decision_id: str
    choice: str
    readback_ref: str

    def __post_init__(self) -> None:
        if type(self.decision_id) is not str or _DECISION_ID.fullmatch(self.decision_id) is None:
            _fail("HUMAN_APPROVAL_INPUT_INVALID", "choice Decision ID is invalid")
        if type(self.choice) is not str or self.choice not in {"approve", "reject"}:
            _fail("HUMAN_APPROVAL_INPUT_INVALID", "choice must be approve or reject")
        _text(self.readback_ref, "readback_ref", code="HUMAN_APPROVAL_INPUT_INVALID")

    def canonical(self) -> dict[str, str]:
        return {
            "kind": "gwo.human-decision-choice.v1",
            "decision_id": self.decision_id,
            "choice": self.choice,
            "readback_ref": self.readback_ref,
        }

    @classmethod
    def from_canonical(cls, value: Mapping[str, Any]) -> "HumanDecisionChoice":
        raw = _closed(value, {"kind", "decision_id", "choice", "readback_ref"}, "human choice", code="HUMAN_APPROVAL_INPUT_INVALID")
        if raw["kind"] != "gwo.human-decision-choice.v1":
            _fail("HUMAN_APPROVAL_INPUT_INVALID", "human choice kind is invalid")
        try:
            return cls(raw["decision_id"], raw["choice"], raw["readback_ref"])
        except HumanGateError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise HumanGateError("HUMAN_APPROVAL_INPUT_INVALID", "human choice cannot be decoded") from error


@dataclass(frozen=True)
class HumanSourceReadback:
    decision_id: str
    state: str
    approval_record_bytes: bytes | None
    tracker_source_bytes: bytes | None
    policy_witness_bytes: bytes | None
    approval_record_digest: str | None
    tracker_source_digest: str | None
    policy_witness_digest: str | None
    source_change_digest: str | None
    readback_digest: str
    code: str

    def __post_init__(self) -> None:
        if type(self.decision_id) is not str or _DECISION_ID.fullmatch(self.decision_id) is None:
            _fail("HUMAN_SOURCE_READBACK_INVALID", "source Decision ID is invalid")
        if type(self.state) is not str or self.state not in HUMAN_SOURCE_STATES:
            _fail("HUMAN_SOURCE_READBACK_INVALID", "source state is outside the closed union")
        _digest(self.readback_digest, "readback_digest", code="HUMAN_SOURCE_READBACK_INVALID")
        if self.code != _STATE_CODES[self.state]:
            _fail("HUMAN_SOURCE_READBACK_INVALID", "source state code is invalid")
        for payload, observed_digest, label in (
            (self.approval_record_bytes, self.approval_record_digest, "approval_record"),
            (self.tracker_source_bytes, self.tracker_source_digest, "tracker_source"),
            (self.policy_witness_bytes, self.policy_witness_digest, "policy_witness"),
        ):
            if payload is None:
                if self.state == "approved":
                    _fail("HUMAN_SOURCE_READBACK_INVALID", f"approved {label} bytes are required")
                if observed_digest is not None:
                    _fail("HUMAN_SOURCE_READBACK_INVALID", f"non-approved {label} must be absent")
                continue
            if type(payload) is not bytes:
                _fail("HUMAN_SOURCE_READBACK_INVALID", f"{label} bytes are invalid")
            _digest(observed_digest, f"{label}_digest", code="HUMAN_SOURCE_READBACK_INVALID")
            if digest_bytes(payload) != observed_digest:
                _fail("HUMAN_SOURCE_READBACK_INVALID", f"{label} digest does not match bytes")
            try:
                load_canonical_json(payload)
            except CanonicalJsonError as error:
                _fail("HUMAN_SOURCE_READBACK_INVALID", f"{label} bytes are not canonical JSON: {error}")
        if self.state == "approved":
            _digest(self.source_change_digest, "source_change_digest", code="HUMAN_SOURCE_READBACK_INVALID")
        elif self.source_change_digest is not None:
            _digest(self.source_change_digest, "source_change_digest", code="HUMAN_SOURCE_READBACK_INVALID")

    @property
    def approved(self) -> bool:
        return self.state == "approved"

    @property
    def reason_code(self) -> str:
        return self.code

    def canonical(self) -> dict[str, Any]:
        return {
            "kind": "gwo.human-source-readback.v1",
            "decision_id": self.decision_id,
            "state": self.state,
            "approval_record_bytes": _bytes_to_canonical(self.approval_record_bytes, "approval_record_bytes", code="HUMAN_SOURCE_READBACK_INVALID"),
            "tracker_source_bytes": _bytes_to_canonical(self.tracker_source_bytes, "tracker_source_bytes", code="HUMAN_SOURCE_READBACK_INVALID"),
            "policy_witness_bytes": _bytes_to_canonical(self.policy_witness_bytes, "policy_witness_bytes", code="HUMAN_SOURCE_READBACK_INVALID"),
            "approval_record_digest": self.approval_record_digest,
            "tracker_source_digest": self.tracker_source_digest,
            "policy_witness_digest": self.policy_witness_digest,
            "source_change_digest": self.source_change_digest,
            "readback_digest": self.readback_digest,
            "code": self.code,
        }

    @classmethod
    def from_canonical(cls, value: Mapping[str, Any]) -> "HumanSourceReadback":
        expected = {
            "kind", "decision_id", "state", "approval_record_bytes", "tracker_source_bytes", "policy_witness_bytes",
            "approval_record_digest", "tracker_source_digest", "policy_witness_digest", "source_change_digest", "readback_digest", "code",
        }
        raw = _closed(value, expected, "human source readback", code="HUMAN_SOURCE_READBACK_INVALID")
        if raw["kind"] != "gwo.human-source-readback.v1":
            _fail("HUMAN_SOURCE_READBACK_INVALID", "human source readback kind is invalid")
        return cls(
            decision_id=raw["decision_id"],
            state=raw["state"],
            approval_record_bytes=_bytes_from_canonical(raw["approval_record_bytes"], "approval_record_bytes", code="HUMAN_SOURCE_READBACK_INVALID"),
            tracker_source_bytes=_bytes_from_canonical(raw["tracker_source_bytes"], "tracker_source_bytes", code="HUMAN_SOURCE_READBACK_INVALID"),
            policy_witness_bytes=_bytes_from_canonical(raw["policy_witness_bytes"], "policy_witness_bytes", code="HUMAN_SOURCE_READBACK_INVALID"),
            approval_record_digest=raw["approval_record_digest"],
            tracker_source_digest=raw["tracker_source_digest"],
            policy_witness_digest=raw["policy_witness_digest"],
            source_change_digest=raw["source_change_digest"],
            readback_digest=raw["readback_digest"],
            code=raw["code"],
        )


@dataclass(frozen=True)
class ReplanBudgetPolicy:
    successor_revision_limit: int
    repeated_invalidation_limit: int
    policy_witness_digest: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.successor_revision_limit, "successor_revision_limit"),
            (self.repeated_invalidation_limit, "repeated_invalidation_limit"),
        ):
            if type(value) is not int or isinstance(value, bool) or value < 1:
                _fail("REPLAN_BUDGET_POLICY_INVALID", f"{label} must be a positive exact integer")
        _digest(self.policy_witness_digest, "policy_witness_digest", code="REPLAN_BUDGET_POLICY_INVALID")

    @classmethod
    def from_policy(cls, policy: Mapping[str, Any]) -> "ReplanBudgetPolicy":
        if type(policy) is not dict or "replan" not in policy or "digest" not in policy:
            _fail("REPLAN_BUDGET_POLICY_INVALID", "Policy Witness does not contain a replan budget")
        replan = policy["replan"]
        if type(replan) is not dict or set(replan) != {"successor_revision_limit", "repeated_invalidation_limit"}:
            _fail("REPLAN_BUDGET_POLICY_INVALID", "Policy Witness replan budget schema is invalid")
        digest = policy["digest"]
        _digest(digest, "policy digest", code="REPLAN_BUDGET_POLICY_INVALID")
        unsigned = {key: value for key, value in policy.items() if key != "digest"}
        if digest_value(unsigned) != digest:
            _fail("REPLAN_BUDGET_POLICY_INVALID", "Policy Witness digest does not match bytes")
        try:
            return cls(replan["successor_revision_limit"], replan["repeated_invalidation_limit"], digest)
        except HumanGateError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise HumanGateError("REPLAN_BUDGET_POLICY_INVALID", "Policy Witness budget cannot be decoded") from error

    def canonical(self) -> dict[str, Any]:
        return {
            "kind": "gwo.replan-budget-policy.v1",
            "successor_revision_limit": self.successor_revision_limit,
            "repeated_invalidation_limit": self.repeated_invalidation_limit,
            "policy_witness_digest": self.policy_witness_digest,
        }

    @classmethod
    def from_canonical(cls, value: Mapping[str, Any]) -> "ReplanBudgetPolicy":
        raw = _closed(
            value,
            {"kind", "successor_revision_limit", "repeated_invalidation_limit", "policy_witness_digest"},
            "replan budget policy",
            code="REPLAN_BUDGET_POLICY_INVALID",
        )
        if raw["kind"] != "gwo.replan-budget-policy.v1":
            _fail("REPLAN_BUDGET_POLICY_INVALID", "replan budget policy kind is invalid")
        return cls(raw["successor_revision_limit"], raw["repeated_invalidation_limit"], raw["policy_witness_digest"])


@dataclass(frozen=True)
class HumanGateSummary:
    phase: str
    decision_id: str
    classification_action_id: str
    required_change: str
    evidence_digests: tuple[str, ...]
    required_source_kind: str
    reason_code: str
    source_readback_digest: str | None
    planning_action_id: str | None
    predecessor_revision_digest: str
    successor_revision_digest: str | None
    successor_revisions_used: int
    successor_revision_limit: int
    repeated_invalidations: int
    repeated_invalidation_limit: int

    def __post_init__(self) -> None:
        if type(self.phase) is not str or self.phase not in HUMAN_GATE_PHASES:
            _fail("HUMAN_GATE_SUMMARY_INVALID", "phase is outside the closed union")
        if type(self.decision_id) is not str or _DECISION_ID.fullmatch(self.decision_id) is None:
            _fail("HUMAN_GATE_SUMMARY_INVALID", "summary Decision ID is invalid")
        _text(self.classification_action_id, "summary classification_action_id", code="HUMAN_GATE_SUMMARY_INVALID")
        if type(self.required_change) is not str or self.required_change not in HUMAN_REQUIRED_CHANGES:
            _fail("HUMAN_GATE_SUMMARY_INVALID", "summary required_change is invalid")
        _tuple_digests(self.evidence_digests, code="HUMAN_GATE_SUMMARY_INVALID")
        if type(self.required_source_kind) is not str or self.required_source_kind not in HUMAN_SOURCE_KINDS:
            _fail("HUMAN_GATE_SUMMARY_INVALID", "summary source kind is invalid")
        _text(self.reason_code, "summary reason_code", code="HUMAN_GATE_SUMMARY_INVALID")
        for value, label in (
            (self.source_readback_digest, "source_readback_digest"),
            (self.predecessor_revision_digest, "predecessor_revision_digest"),
            (self.successor_revision_digest, "successor_revision_digest"),
        ):
            if value is not None:
                _digest(value, label, code="HUMAN_GATE_SUMMARY_INVALID")
        if self.planning_action_id is not None:
            _text(self.planning_action_id, "planning_action_id", code="HUMAN_GATE_SUMMARY_INVALID")
        for value, label in (
            (self.successor_revisions_used, "successor_revisions_used"),
            (self.repeated_invalidations, "repeated_invalidations"),
        ):
            if type(value) is not int or isinstance(value, bool) or value < 0:
                _fail("HUMAN_GATE_SUMMARY_INVALID", f"{label} must be non-negative")
        for value, label in (
            (self.successor_revision_limit, "successor_revision_limit"),
            (self.repeated_invalidation_limit, "repeated_invalidation_limit"),
        ):
            if type(value) is not int or isinstance(value, bool) or value < 1:
                _fail("HUMAN_GATE_SUMMARY_INVALID", f"{label} must be positive")

    def canonical(self) -> dict[str, Any]:
        return {
            "kind": "gwo.human-gate-summary.v1",
            "phase": self.phase,
            "decision_id": self.decision_id,
            "classification_action_id": self.classification_action_id,
            "required_change": self.required_change,
            "evidence_digests": list(self.evidence_digests),
            "required_source_kind": self.required_source_kind,
            "reason_code": self.reason_code,
            "source_readback_digest": self.source_readback_digest,
            "planning_action_id": self.planning_action_id,
            "predecessor_revision_digest": self.predecessor_revision_digest,
            "successor_revision_digest": self.successor_revision_digest,
            "successor_revisions_used": self.successor_revisions_used,
            "successor_revision_limit": self.successor_revision_limit,
            "repeated_invalidations": self.repeated_invalidations,
            "repeated_invalidation_limit": self.repeated_invalidation_limit,
        }

    @classmethod
    def from_canonical(cls, value: Mapping[str, Any]) -> "HumanGateSummary":
        expected = {
            "kind", "phase", "decision_id", "classification_action_id", "required_change", "evidence_digests",
            "required_source_kind", "reason_code", "source_readback_digest", "planning_action_id", "predecessor_revision_digest",
            "successor_revision_digest", "successor_revisions_used", "successor_revision_limit", "repeated_invalidations", "repeated_invalidation_limit",
        }
        raw = _closed(value, expected, "human gate summary", code="HUMAN_GATE_SUMMARY_INVALID")
        if raw["kind"] != "gwo.human-gate-summary.v1" or type(raw["evidence_digests"]) is not list:
            _fail("HUMAN_GATE_SUMMARY_INVALID", "human gate summary is invalid")
        return cls(
            phase=raw["phase"], decision_id=raw["decision_id"],
            classification_action_id=raw["classification_action_id"], required_change=raw["required_change"],
            evidence_digests=tuple(raw["evidence_digests"]), required_source_kind=raw["required_source_kind"],
            reason_code=raw["reason_code"], source_readback_digest=raw["source_readback_digest"],
            planning_action_id=raw["planning_action_id"], predecessor_revision_digest=raw["predecessor_revision_digest"],
            successor_revision_digest=raw["successor_revision_digest"], successor_revisions_used=raw["successor_revisions_used"],
            successor_revision_limit=raw["successor_revision_limit"], repeated_invalidations=raw["repeated_invalidations"],
            repeated_invalidation_limit=raw["repeated_invalidation_limit"],
        )


@dataclass(frozen=True)
class HumanGateAttempt:
    decision_id: str
    campaign: CampaignHandle
    predecessor_revision_digest: str
    source_readback_digest: str
    tracker_source_digest: str
    policy_witness_digest: str
    planning_action_id: str
    planning_protocol_id: str
    state: str
    compilation_record_artifact_digest: str | None
    activation_receipt_digest: str | None

    def __post_init__(self) -> None:
        if type(self.decision_id) is not str or _DECISION_ID.fullmatch(self.decision_id) is None:
            _fail("HUMAN_GATE_ATTEMPT_INVALID", "attempt Decision ID is invalid")
        if type(self.campaign) is not CampaignHandle:
            _fail("HUMAN_GATE_ATTEMPT_INVALID", "attempt campaign is invalid")
        for value, label in (
            (self.predecessor_revision_digest, "predecessor_revision_digest"),
            (self.source_readback_digest, "source_readback_digest"),
            (self.tracker_source_digest, "tracker_source_digest"),
            (self.policy_witness_digest, "policy_witness_digest"),
        ):
            _digest(value, label, code="HUMAN_GATE_ATTEMPT_INVALID")
        _text(self.planning_action_id, "planning_action_id", code="HUMAN_GATE_ATTEMPT_INVALID")
        if self.planning_protocol_id != REPLANNING_OUTPUT_PROTOCOL_ID:
            _fail("HUMAN_GATE_ATTEMPT_INVALID", "attempt must use the replanning output protocol")
        if type(self.state) is not str or self.state not in HUMAN_GATE_PHASES:
            _fail("HUMAN_GATE_ATTEMPT_INVALID", "attempt state is outside the closed union")
        for value, label in (
            (self.compilation_record_artifact_digest, "compilation_record_artifact_digest"),
            (self.activation_receipt_digest, "activation_receipt_digest"),
        ):
            if value is not None:
                _digest(value, label, code="HUMAN_GATE_ATTEMPT_INVALID")

    def canonical(self) -> dict[str, Any]:
        return {
            "kind": "gwo.human-gate-attempt.v1",
            "decision_id": self.decision_id,
            "campaign": _campaign_canonical(self.campaign),
            "predecessor_revision_digest": self.predecessor_revision_digest,
            "source_readback_digest": self.source_readback_digest,
            "tracker_source_digest": self.tracker_source_digest,
            "policy_witness_digest": self.policy_witness_digest,
            "planning_action_id": self.planning_action_id,
            "planning_protocol_id": self.planning_protocol_id,
            "state": self.state,
            "compilation_record_artifact_digest": self.compilation_record_artifact_digest,
            "activation_receipt_digest": self.activation_receipt_digest,
        }

    @classmethod
    def from_canonical(cls, value: Mapping[str, Any]) -> "HumanGateAttempt":
        expected = {
            "kind", "decision_id", "campaign", "predecessor_revision_digest", "source_readback_digest", "tracker_source_digest",
            "policy_witness_digest", "planning_action_id", "planning_protocol_id", "state", "compilation_record_artifact_digest", "activation_receipt_digest",
        }
        raw = _closed(value, expected, "human gate attempt", code="HUMAN_GATE_ATTEMPT_INVALID")
        if raw["kind"] != "gwo.human-gate-attempt.v1":
            _fail("HUMAN_GATE_ATTEMPT_INVALID", "human gate attempt kind is invalid")
        return cls(
            decision_id=raw["decision_id"],
            campaign=_campaign_from_canonical(raw["campaign"], code="HUMAN_GATE_ATTEMPT_INVALID"),
            predecessor_revision_digest=raw["predecessor_revision_digest"],
            source_readback_digest=raw["source_readback_digest"],
            tracker_source_digest=raw["tracker_source_digest"],
            policy_witness_digest=raw["policy_witness_digest"],
            planning_action_id=raw["planning_action_id"], planning_protocol_id=raw["planning_protocol_id"],
            state=raw["state"], compilation_record_artifact_digest=raw["compilation_record_artifact_digest"],
            activation_receipt_digest=raw["activation_receipt_digest"],
        )


@dataclass(frozen=True)
class HumanGatePlanReadback:
    summary: HumanGateSummary

    def __post_init__(self) -> None:
        if type(self.summary) is not HumanGateSummary:
            _fail("HUMAN_GATE_SUMMARY_INVALID", "plan readback summary is invalid")

    def canonical(self) -> dict[str, Any]:
        return {"kind": "gwo.human-gate-plan-readback.v1", "summary": self.summary.canonical()}

    @classmethod
    def from_canonical(cls, value: Mapping[str, Any]) -> "HumanGatePlanReadback":
        raw = _closed(value, {"kind", "summary"}, "human gate plan readback", code="HUMAN_GATE_SUMMARY_INVALID")
        if raw["kind"] != "gwo.human-gate-plan-readback.v1":
            _fail("HUMAN_GATE_SUMMARY_INVALID", "human gate plan readback kind is invalid")
        return cls(HumanGateSummary.from_canonical(raw["summary"]))


class HumanApprovalSource(Protocol):
    """Read-only seam for the upstream durable approval workflow."""

    def read(self, handle: CampaignHandle, decision: HumanDecisionRecord, readback_ref: str) -> HumanSourceReadback:
        ...


__all__ = [
    "HUMAN_REQUIRED_CHANGES", "HUMAN_SOURCE_KINDS", "HUMAN_SOURCE_STATES", "HUMAN_GATE_PHASES",
    "HumanApprovalSource", "HumanDecisionChoice", "HumanDecisionRecord", "HumanGateAttempt",
    "HumanGateError", "HumanGatePlanReadback", "HumanGateSummary", "HumanSourceReadback",
    "RequiredDurableSourceChange", "ReplanBudgetPolicy",
]
