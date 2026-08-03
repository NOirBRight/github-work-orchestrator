"""Read-only CandidateGate routing for late Plan Invalidation discoveries.

This module deliberately owns only the semantic seam added by Issue #137.  It
does not drive a Campaign, write a Ticket or Plan, edit a workspace, or call a
public workflow operation.  A deterministic audit may either reject an
ordinary unauthorized Candidate or prove that the frozen Ticket is no longer
safe to satisfy.  Only the latter is handed to the existing RuntimeGateway
Plan Invalidation contract.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import re
from typing import Any, Mapping, Protocol

from ._canonical import canonical_bytes, digest_bytes, digest_value, load_canonical_json
from .runtime_gateway import (
    CapabilityPolicyProof,
    PlanInvalidationReceipt,
    PlanInvalidationReport,
    WorkRunPurpose,
    WorkRunSubject,
)


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_ID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class CandidateGateError(RuntimeError):
    """A typed, fail-closed CandidateGate boundary error."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class AuditFailureKind(str, Enum):
    """The deterministic audit that found the Candidate fact."""

    SCOPE = "scope"
    PROTECTED_EFFECT = "protected_effect"
    AUTHORITY = "authority"
    AFFECTED_CHECK = "affected_check"


class AuditFailureRoute(str, Enum):
    """A local audit route, not a Campaign classification disposition."""

    ORDINARY_UNAUTHORIZED = "ordinary_unauthorized"
    TICKET_UNSATISFIABLE = "ticket_unsatisfiable"


class CandidateGateStatus(str, Enum):
    """Read-only local result states owned by CandidateGate."""

    ORDINARY_REJECTED = "ordinary_rejected"
    PLAN_INVALIDATION_REPORTED = "plan_invalidation_reported"
    REVIEW_ACCEPTED = "review_accepted"
    REPAIR_REQUIRED = "repair_required"
    REPAIR_REJECTED = "repair_rejected"
    REPAIR_ACCEPTED = "repair_accepted"


def _require_text(value: object, field_name: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise CandidateGateError(
            "CANDIDATE_GATE_EVIDENCE_INVALID",
            f"{field_name} must be non-empty text without NUL",
        )
    return value


def _require_digest(value: object, field_name: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise CandidateGateError(
            "CANDIDATE_GATE_EVIDENCE_INVALID",
            f"{field_name} must be a lowercase SHA-256 digest",
        )
    return value


def _require_object_id(value: object, field_name: str) -> str:
    if type(value) is not str or _OBJECT_ID_RE.fullmatch(value) is None:
        raise CandidateGateError(
            "CANDIDATE_GATE_EVIDENCE_INVALID",
            f"{field_name} must be a lowercase SHA-1 or SHA-256 object ID",
        )
    return value


def _require_text_tuple(
    value: object,
    field_name: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise CandidateGateError(
            "CANDIDATE_GATE_EVIDENCE_INVALID",
            f"{field_name} must be a tuple of text values",
        )
    if any(type(item) is not str or not item or "\x00" in item for item in value):
        raise CandidateGateError(
            "CANDIDATE_GATE_EVIDENCE_INVALID",
            f"{field_name} contains an invalid text value",
        )
    if not allow_empty and not value:
        raise CandidateGateError(
            "CANDIDATE_GATE_EVIDENCE_INVALID",
            f"{field_name} must not be empty",
        )
    return value


def _require_digest_tuple(
    value: object,
    field_name: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    """Validate one canonical, immutable tuple of Evidence digests."""

    if type(value) is not tuple:
        raise CandidateGateError(
            "CANDIDATE_GATE_EVIDENCE_INVALID",
            f"{field_name} must be a tuple of Evidence digests",
        )
    if not allow_empty and not value:
        raise CandidateGateError(
            "CANDIDATE_GATE_EVIDENCE_INVALID",
            f"{field_name} must not be empty",
        )
    for digest in value:
        _require_digest(digest, field_name)
    if value != tuple(sorted(set(value))):
        raise CandidateGateError(
            "CANDIDATE_GATE_EVIDENCE_INVALID",
            f"{field_name} must use deterministic unique digest ordering",
        )
    return value


def _body_digest(body: Mapping[str, Any]) -> str:
    try:
        return digest_value(dict(body))
    except Exception as error:
        raise CandidateGateError(
            "CANDIDATE_GATE_EVIDENCE_INVALID",
            "CandidateGate evidence is outside the canonical JSON domain",
        ) from error


def _validate_stored_digest(
    stored: object,
    body: Mapping[str, Any],
    *,
    code: str = "CANDIDATE_GATE_EVIDENCE_INVALID",
    detail: str = "CandidateGate evidence digest changed",
) -> str:
    _require_digest(stored, "content_digest")
    expected = _body_digest(body)
    if stored != expected:
        raise CandidateGateError(code, detail)
    return stored


@dataclass(frozen=True)
class CandidateGateParent:
    """The immutable parent identity for one CandidateGate invocation."""

    runtime_subject: WorkRunSubject
    ticket_contract_digest: str
    policy_witness_digest: str
    workspace_identity: str
    parent_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self.runtime_subject) is not WorkRunSubject:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "CandidateGate parent requires an exact WorkRunSubject",
            )
        _require_digest(self.ticket_contract_digest, "ticket_contract_digest")
        _require_digest(self.policy_witness_digest, "policy_witness_digest")
        _require_text(self.workspace_identity, "workspace_identity")
        expected = _body_digest(self._body())
        if self.parent_digest is None:
            object.__setattr__(self, "parent_digest", expected)
        else:
            _validate_stored_digest(self.parent_digest, self._body())

    def _body(self) -> dict[str, Any]:
        return {
            "kind": "candidate_gate_parent.v1",
            "runtime_subject": self.runtime_subject.canonical(),
            "ticket_contract_digest": self.ticket_contract_digest,
            "policy_witness_digest": self.policy_witness_digest,
            "workspace_identity": self.workspace_identity,
        }

    @property
    def digest(self) -> str:
        assert self.parent_digest is not None
        return self.parent_digest

    def canonical(self) -> dict[str, Any]:
        return {**self._body(), "parent_digest": self.digest}


@dataclass(frozen=True)
class CandidateIdentity:
    """The exact Candidate/base identity consumed by deterministic audit."""

    reported_reference: str
    base_commit_oid: str
    base_tree_oid: str
    candidate_commit_oid: str
    candidate_tree_oid: str
    changed_paths: tuple[str, ...]
    candidate_digest: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.reported_reference, "reported_reference")
        for field_name in (
            "base_commit_oid",
            "base_tree_oid",
            "candidate_commit_oid",
            "candidate_tree_oid",
        ):
            _require_object_id(getattr(self, field_name), field_name)
        _require_text_tuple(self.changed_paths, "changed_paths")
        if len(set(self.changed_paths)) != len(self.changed_paths):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "changed_paths must not contain duplicates",
            )
        if self.changed_paths != tuple(sorted(self.changed_paths)):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "changed_paths must use deterministic repository-relative ordering",
            )
        expected = _body_digest(self._body())
        if self.candidate_digest is None:
            object.__setattr__(self, "candidate_digest", expected)
        else:
            _validate_stored_digest(self.candidate_digest, self._body())

    def _body(self) -> dict[str, Any]:
        return {
            "kind": "candidate_identity.v1",
            "reported_reference": self.reported_reference,
            "base_commit_oid": self.base_commit_oid,
            "base_tree_oid": self.base_tree_oid,
            "candidate_commit_oid": self.candidate_commit_oid,
            "candidate_tree_oid": self.candidate_tree_oid,
            "changed_paths": list(self.changed_paths),
        }

    @property
    def digest(self) -> str:
        assert self.candidate_digest is not None
        return self.candidate_digest

    def canonical(self) -> dict[str, Any]:
        return {**self._body(), "candidate_digest": self.digest}


@dataclass(frozen=True)
class CandidateDiffEntryV1:
    """One raw Git tree entry used by the complete Candidate diff record."""

    side: str
    path: str
    mode: str
    object_type: str
    object_oid: str

    def __post_init__(self) -> None:
        if self.side not in {"base", "candidate"}:
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff entry side is outside the closed union",
            )
        _require_text(self.path, "Candidate diff path")
        if self.path.startswith("/") or "\\" in self.path or any(
            part in {"", ".", ".."} for part in self.path.split("/")
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff path is not repository-relative",
            )
        _require_text(self.mode, "Candidate diff mode")
        if not re.fullmatch(r"[0-7]{6}", self.mode):
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff mode is not an exact Git mode",
            )
        if self.object_type not in {"blob", "tree", "commit", "submodule"}:
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff object type is outside the closed union",
            )
        _require_object_id(self.object_oid, "Candidate diff object OID")

    def canonical(self) -> dict[str, str]:
        return {
            "side": self.side,
            "path": self.path,
            "mode": self.mode,
            "object_type": self.object_type,
            "object_oid": self.object_oid,
        }


@dataclass(frozen=True)
class CandidateDiffRecordV1:
    """The one complete, digest-addressed raw-Git Candidate diff Artifact."""

    repository: str
    object_format: str
    base_commit_oid: str
    base_tree_oid: str
    candidate_commit_oid: str
    candidate_tree_oid: str
    entries: tuple[CandidateDiffEntryV1, ...]
    record_digest: str | None = None
    schema_version: str = "gwo.candidate-diff.v1"

    def __post_init__(self) -> None:
        _require_text(self.repository, "Candidate diff repository")
        _require_text(self.object_format, "Candidate diff object format")
        if self.object_format not in {"sha1", "sha256"}:
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff object format is outside the closed union",
            )
        for value, label in (
            (self.base_commit_oid, "base_commit_oid"),
            (self.base_tree_oid, "base_tree_oid"),
            (self.candidate_commit_oid, "candidate_commit_oid"),
            (self.candidate_tree_oid, "candidate_tree_oid"),
        ):
            _require_object_id(value, label)
        if self.schema_version != "gwo.candidate-diff.v1":
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff schema version is invalid",
            )
        if type(self.entries) is not tuple or any(
            type(entry) is not CandidateDiffEntryV1 for entry in self.entries
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff entries are not an immutable typed tuple",
            )
        ordered = tuple(
            sorted(
                self.entries,
                key=lambda entry: (
                    entry.side,
                    entry.path,
                    entry.mode,
                    entry.object_type,
                    entry.object_oid,
                ),
            )
        )
        if ordered != self.entries or len(set(entry.canonical().__repr__() for entry in self.entries)) != len(self.entries):
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff entries are not canonical and unique",
            )
        expected = digest_value(self._body())
        if self.record_digest is None:
            object.__setattr__(self, "record_digest", expected)
        else:
            _validate_stored_digest(self.record_digest, self._body(), code="CANDIDATE_GATE_DIFF_INVALID", detail="Candidate diff record digest changed")

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": "candidate_diff_record.v1",
            "repository": self.repository,
            "object_format": self.object_format,
            "base_commit_oid": self.base_commit_oid,
            "base_tree_oid": self.base_tree_oid,
            "candidate_commit_oid": self.candidate_commit_oid,
            "candidate_tree_oid": self.candidate_tree_oid,
            "entries": [entry.canonical() for entry in self.entries],
        }

    @property
    def digest(self) -> str:
        assert self.record_digest is not None
        return self.record_digest

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(sorted({entry.path for entry in self.entries}))

    def canonical(self) -> dict[str, Any]:
        return {**self._body(), "record_digest": self.digest}


@dataclass(frozen=True)
class CandidateReadback:
    """Authoritative Candidate reference and its complete diff Artifact."""

    repository: str
    candidate: CandidateIdentity
    diff_record: CandidateDiffRecordV1
    readback_digest: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.repository, "Candidate readback repository")
        if type(self.candidate) is not CandidateIdentity or type(self.diff_record) is not CandidateDiffRecordV1:
            raise CandidateGateError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "Candidate readback has an invalid typed identity",
            )
        if (
            self.diff_record.repository != self.repository
            or self.diff_record.base_commit_oid != self.candidate.base_commit_oid
            or self.diff_record.base_tree_oid != self.candidate.base_tree_oid
            or self.diff_record.candidate_commit_oid != self.candidate.candidate_commit_oid
            or self.diff_record.candidate_tree_oid != self.candidate.candidate_tree_oid
            or self.diff_record.changed_paths != self.candidate.changed_paths
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "Candidate readback diff does not bind the exact Candidate identity",
            )
        expected = digest_value(self._body())
        if self.readback_digest is None:
            object.__setattr__(self, "readback_digest", expected)
        else:
            _validate_stored_digest(self.readback_digest, self._body(), code="CANDIDATE_GATE_READBACK_INVALID", detail="Candidate readback digest changed")

    def _body(self) -> dict[str, Any]:
        return {
            "kind": "candidate_readback.v1",
            "repository": self.repository,
            "candidate": self.candidate.canonical(),
            "diff_record": self.diff_record.canonical(),
        }

    @property
    def digest(self) -> str:
        assert self.readback_digest is not None
        return self.readback_digest

    def canonical(self) -> dict[str, Any]:
        return {**self._body(), "readback_digest": self.digest}


@dataclass(frozen=True)
class DeterministicAuditFailure:
    """One closed deterministic audit observation."""

    kind: AuditFailureKind
    route: AuditFailureRoute
    code: str
    detail: str
    invalidated_obligation: str | None = None
    required_effects: tuple[str, ...] = ()
    failure_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not AuditFailureKind:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "deterministic audit kind is not closed",
            )
        if type(self.route) is not AuditFailureRoute:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "deterministic audit route is not closed",
            )
        _require_text(self.code, "audit failure code")
        _require_text(self.detail, "audit failure detail")
        if self.invalidated_obligation is not None:
            _require_text(self.invalidated_obligation, "invalidated_obligation")
        _require_text_tuple(self.required_effects, "required_effects")
        if self.route is AuditFailureRoute.TICKET_UNSATISFIABLE and (
            self.invalidated_obligation is None or not self.required_effects
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "a Ticket-unsatisfiable audit must name its obligation and effects",
            )
        expected = _body_digest(self._body())
        if self.failure_digest is None:
            object.__setattr__(self, "failure_digest", expected)
        else:
            _validate_stored_digest(self.failure_digest, self._body())

    def _body(self) -> dict[str, Any]:
        return {
            "kind": "deterministic_audit_failure.v1",
            "audit_kind": self.kind.value,
            "route": self.route.value,
            "code": self.code,
            "detail": self.detail,
            "invalidated_obligation": self.invalidated_obligation,
            "required_effects": list(self.required_effects),
        }

    @property
    def digest(self) -> str:
        assert self.failure_digest is not None
        return self.failure_digest

    def canonical(self) -> dict[str, Any]:
        return {**self._body(), "failure_digest": self.digest}


@dataclass(frozen=True)
class CandidateAuditEvidence:
    """Evidence preserving one Candidate audit and its complete failures."""

    parent_digest: str
    candidate_digest: str
    report_digest: str
    failure_digests: tuple[str, ...]
    diff_record_digest: str | None = None
    content_digest: str | None = None

    def __post_init__(self) -> None:
        _require_digest(self.parent_digest, "parent_digest")
        _require_digest(self.candidate_digest, "candidate_digest")
        _require_digest(self.report_digest, "report_digest")
        _require_text_tuple(self.failure_digests, "failure_digests")
        if self.diff_record_digest is not None:
            _require_digest(self.diff_record_digest, "diff_record_digest")
        for digest in self.failure_digests:
            _require_digest(digest, "failure_digest")
        expected = _body_digest(self._body())
        if self.content_digest is None:
            object.__setattr__(self, "content_digest", expected)
        else:
            _validate_stored_digest(self.content_digest, self._body())

    @property
    def kind(self) -> str:
        return "candidate_audit"

    def _body(self) -> dict[str, Any]:
        return {
            "kind": "candidate_audit.v1",
            "parent_digest": self.parent_digest,
            "candidate_digest": self.candidate_digest,
            "report_digest": self.report_digest,
            "failure_digests": list(self.failure_digests),
            "diff_record_digest": self.diff_record_digest,
        }

    @property
    def digest(self) -> str:
        assert self.content_digest is not None
        return self.content_digest

    def has_valid_digest(self) -> bool:
        return self.digest == _body_digest(self._body())

    def canonical(self) -> dict[str, Any]:
        return {**self._body(), "content_digest": self.digest}


@dataclass(frozen=True)
class CandidateAuditReport:
    """The closed read-only result of scope/protected/authority/check audits."""

    parent_digest: str
    candidate: CandidateIdentity
    failures: tuple[DeterministicAuditFailure, ...] = ()
    diff_record: CandidateDiffRecordV1 | None = None
    standards: tuple[str, ...] = ()
    check_evidence_digests: tuple[str, ...] = ()
    assurance_requirement: str = "standard"
    report_digest: str | None = None

    def __post_init__(self) -> None:
        _require_digest(self.parent_digest, "parent_digest")
        if type(self.candidate) is not CandidateIdentity:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Candidate audit requires an exact CandidateIdentity",
            )
        if self.diff_record is not None:
            if type(self.diff_record) is not CandidateDiffRecordV1:
                raise CandidateGateError(
                    "CANDIDATE_GATE_EVIDENCE_INVALID",
                    "Candidate audit diff record is not the exact V1 type",
                )
            if self.diff_record.changed_paths != self.candidate.changed_paths:
                raise CandidateGateError(
                    "CANDIDATE_GATE_EVIDENCE_INVALID",
                    "Candidate audit diff paths do not bind the Candidate identity",
                )
        _require_text_tuple(self.standards, "review standards")
        _require_digest_tuple(
            self.check_evidence_digests,
            "check_evidence_digests",
            allow_empty=True,
        )
        _require_text(self.assurance_requirement, "assurance_requirement")
        if type(self.failures) is not tuple or any(
            type(failure) is not DeterministicAuditFailure for failure in self.failures
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Candidate audit failures are not a closed tuple",
            )
        digests = [failure.digest for failure in self.failures]
        if len(set(digests)) != len(digests):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Candidate audit contains duplicate failure Evidence",
            )
        expected = _body_digest(self._body())
        if self.report_digest is None:
            object.__setattr__(self, "report_digest", expected)
        else:
            _validate_stored_digest(self.report_digest, self._body())

    def _ordered_failures(self) -> tuple[DeterministicAuditFailure, ...]:
        return tuple(sorted(self.failures, key=lambda failure: failure.digest))

    def _body(self) -> dict[str, Any]:
        return {
            "kind": "candidate_audit_report.v1",
            "parent_digest": self.parent_digest,
            "candidate": self.candidate.canonical(),
            "diff_record": None if self.diff_record is None else self.diff_record.canonical(),
            "standards": list(self.standards),
            "check_evidence_digests": list(self.check_evidence_digests),
            "assurance_requirement": self.assurance_requirement,
            "failures": [
                failure.canonical() for failure in self._ordered_failures()
            ],
        }

    @property
    def digest(self) -> str:
        assert self.report_digest is not None
        return self.report_digest

    @property
    def evidence(self) -> CandidateAuditEvidence:
        return CandidateAuditEvidence(
            parent_digest=self.parent_digest,
            candidate_digest=self.candidate.digest,
            report_digest=self.digest,
            failure_digests=tuple(
                failure.digest for failure in self._ordered_failures()
            ),
            diff_record_digest=(
                None if self.diff_record is None else self.diff_record.digest
            ),
        )

    def canonical(self) -> dict[str, Any]:
        return {**self._body(), "report_digest": self.digest}


@dataclass(frozen=True)
class FormalReviewRequest:
    """The only immutable input a Formal Review port receives."""

    parent_digest: str
    candidate_digest: str
    candidate_audit_digest: str
    ticket_contract_digest: str
    policy_witness_digest: str
    protocol_version: str = "gwo.formal-review.v1"
    base_commit_oid: str | None = None
    base_tree_oid: str | None = None
    candidate_commit_oid: str | None = None
    candidate_tree_oid: str | None = None
    diff_schema_version: str = "gwo.candidate-diff.v1"
    diff_digest: str | None = None
    standards: tuple[str, ...] = ()
    check_evidence_digests: tuple[str, ...] = ()
    assurance_requirement: str = "standard"
    subject_digest: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.parent_digest, "parent_digest"),
            (self.candidate_digest, "candidate_digest"),
            (self.candidate_audit_digest, "candidate_audit_digest"),
            (self.ticket_contract_digest, "ticket_contract_digest"),
            (self.policy_witness_digest, "policy_witness_digest"),
        ):
            _require_digest(value, label)
        _require_text(self.protocol_version, "protocol_version")
        for value, label in (
            (self.base_commit_oid, "base_commit_oid"),
            (self.base_tree_oid, "base_tree_oid"),
            (self.candidate_commit_oid, "candidate_commit_oid"),
            (self.candidate_tree_oid, "candidate_tree_oid"),
        ):
            if value is not None:
                _require_object_id(value, label)
        _require_text(self.diff_schema_version, "diff_schema_version")
        if self.diff_digest is None:
            object.__setattr__(self, "diff_digest", self.candidate_digest)
        else:
            _require_digest(self.diff_digest, "diff_digest")
        _require_text_tuple(self.standards, "review standards")
        _require_digest_tuple(
            self.check_evidence_digests,
            "check_evidence_digests",
            allow_empty=True,
        )
        _require_text(self.assurance_requirement, "assurance_requirement")
        expected = _body_digest(self._body())
        if self.subject_digest is None:
            object.__setattr__(self, "subject_digest", expected)
        else:
            _validate_stored_digest(self.subject_digest, self._body())

    @classmethod
    def from_parent(
        cls,
        parent: CandidateGateParent,
        audit: CandidateAuditReport,
    ) -> "FormalReviewRequest":
        return cls(
            parent_digest=parent.digest,
            candidate_digest=audit.candidate.digest,
            candidate_audit_digest=audit.evidence.digest,
            ticket_contract_digest=parent.ticket_contract_digest,
            policy_witness_digest=parent.policy_witness_digest,
            base_commit_oid=audit.candidate.base_commit_oid,
            base_tree_oid=audit.candidate.base_tree_oid,
            candidate_commit_oid=audit.candidate.candidate_commit_oid,
            candidate_tree_oid=audit.candidate.candidate_tree_oid,
            diff_schema_version=(
                "gwo.candidate-diff.v1"
                if audit.diff_record is None
                else audit.diff_record.schema_version
            ),
            diff_digest=(
                audit.candidate.digest
                if audit.diff_record is None
                else audit.diff_record.digest
            ),
            standards=audit.standards,
            check_evidence_digests=audit.check_evidence_digests,
            assurance_requirement=audit.assurance_requirement,
        )

    def _body(self) -> dict[str, Any]:
        return {
            "kind": "formal_review_subject.v1",
            "parent_digest": self.parent_digest,
            "candidate_digest": self.candidate_digest,
            "candidate_audit_digest": self.candidate_audit_digest,
            "ticket_contract_digest": self.ticket_contract_digest,
            "policy_witness_digest": self.policy_witness_digest,
            "protocol_version": self.protocol_version,
            "base_commit_oid": self.base_commit_oid,
            "base_tree_oid": self.base_tree_oid,
            "candidate_commit_oid": self.candidate_commit_oid,
            "candidate_tree_oid": self.candidate_tree_oid,
            "diff_schema_version": self.diff_schema_version,
            "diff_digest": self.diff_digest,
            "standards": list(self.standards),
            "check_evidence_digests": list(self.check_evidence_digests),
            "assurance_requirement": self.assurance_requirement,
            "action_kind": "formal_review",
        }

    @property
    def digest(self) -> str:
        assert self.subject_digest is not None
        return self.subject_digest

    def canonical(self) -> dict[str, Any]:
        return {**self._body(), "subject_digest": self.digest}


@dataclass(frozen=True)
class FormalReviewFinding:
    """Artifact-backed immutable Formal Review Finding Evidence."""

    parent_digest: str
    candidate_digest: str
    review_subject_digest: str
    finding_id: str
    severity: str
    code: str
    message: str
    scope_escape: bool = False
    invalidated_obligation: str | None = None
    required_effects: tuple[str, ...] = ()
    content_digest: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.parent_digest, "parent_digest"),
            (self.candidate_digest, "candidate_digest"),
            (self.review_subject_digest, "review_subject_digest"),
        ):
            _require_digest(value, label)
        _require_text(self.finding_id, "finding_id")
        if self.severity not in {"hard", "advisory"}:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Formal Review finding severity is outside the closed union",
            )
        _require_text(self.code, "finding code")
        _require_text(self.message, "finding message")
        if type(self.scope_escape) is not bool:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Formal Review scope_escape must be boolean",
            )
        if self.invalidated_obligation is not None:
            _require_text(self.invalidated_obligation, "invalidated_obligation")
        _require_text_tuple(self.required_effects, "required_effects")
        if self.scope_escape and (
            self.invalidated_obligation is None or not self.required_effects
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "an out-of-scope Finding must name its obligation and effects",
            )
        expected = _body_digest(self._body())
        if self.content_digest is None:
            object.__setattr__(self, "content_digest", expected)
        else:
            _validate_stored_digest(self.content_digest, self._body())

    @property
    def kind(self) -> str:
        return "formal_review_finding"

    def _body(self) -> dict[str, Any]:
        return {
            "kind": "formal_review_finding.v1",
            "parent_digest": self.parent_digest,
            "candidate_digest": self.candidate_digest,
            "review_subject_digest": self.review_subject_digest,
            "finding_id": self.finding_id,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "scope_escape": self.scope_escape,
            "invalidated_obligation": self.invalidated_obligation,
            "required_effects": list(self.required_effects),
        }

    @property
    def digest(self) -> str:
        assert self.content_digest is not None
        return self.content_digest

    def canonical(self) -> dict[str, Any]:
        return {**self._body(), "content_digest": self.digest}


@dataclass(frozen=True)
class FormalReviewResult:
    """A complete read-only result returned by the Formal Review port."""

    subject_digest: str
    findings: tuple[FormalReviewFinding, ...] = ()
    result_digest: str | None = None

    def __post_init__(self) -> None:
        _require_digest(self.subject_digest, "review result subject_digest")
        if type(self.findings) is not tuple or any(
            type(finding) is not FormalReviewFinding for finding in self.findings
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Formal Review findings are not a closed tuple",
            )
        finding_ids = [finding.finding_id for finding in self.findings]
        if len(set(finding_ids)) != len(finding_ids):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Formal Review returned duplicate Finding IDs",
            )
        expected = _body_digest(self._body())
        if self.result_digest is None:
            object.__setattr__(self, "result_digest", expected)
        else:
            _validate_stored_digest(self.result_digest, self._body())

    def _body(self) -> dict[str, Any]:
        return {
            "kind": "formal_review_result.v1",
            "subject_digest": self.subject_digest,
            "findings": [
                finding.canonical()
                for finding in sorted(self.findings, key=lambda item: item.digest)
            ],
        }

    @property
    def digest(self) -> str:
        assert self.result_digest is not None
        return self.result_digest

    def canonical(self) -> dict[str, Any]:
        return {**self._body(), "result_digest": self.digest}


@dataclass(frozen=True)
class RepairPacket:
    """The bounded repair scope produced only for ordinary Review Findings."""

    parent_digest: str
    rejected_candidate_digest: str
    prior_review_subject_digest: str
    finding_digests: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    required_effects: tuple[str, ...] = ()
    packet_digest: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.parent_digest, "parent_digest"),
            (self.rejected_candidate_digest, "rejected_candidate_digest"),
            (self.prior_review_subject_digest, "prior_review_subject_digest"),
        ):
            _require_digest(value, label)
        _require_text_tuple(self.finding_digests, "finding_digests", allow_empty=False)
        for digest in self.finding_digests:
            _require_digest(digest, "finding_digest")
        _require_text_tuple(self.allowed_paths, "allowed_paths")
        _require_text_tuple(self.required_effects, "required_effects")
        expected = _body_digest(self._body())
        if self.packet_digest is None:
            object.__setattr__(self, "packet_digest", expected)
        else:
            _validate_stored_digest(self.packet_digest, self._body())

    @classmethod
    def from_findings(
        cls,
        parent: CandidateGateParent,
        candidate: CandidateIdentity,
        request: FormalReviewRequest,
        findings: tuple[FormalReviewFinding, ...],
    ) -> "RepairPacket":
        hard = tuple(finding for finding in findings if finding.severity == "hard")
        effects = tuple(
            sorted(
                {
                    effect
                    for finding in hard
                    for effect in finding.required_effects
                }
            )
        )
        return cls(
            parent_digest=parent.digest,
            rejected_candidate_digest=candidate.digest,
            prior_review_subject_digest=request.digest,
            finding_digests=tuple(sorted(finding.digest for finding in hard)),
            allowed_paths=tuple(sorted(candidate.changed_paths)),
            required_effects=effects,
        )

    def _body(self) -> dict[str, Any]:
        return {
            "kind": "repair_packet.v1",
            "parent_digest": self.parent_digest,
            "rejected_candidate_digest": self.rejected_candidate_digest,
            "prior_review_subject_digest": self.prior_review_subject_digest,
            "finding_digests": list(self.finding_digests),
            "allowed_paths": list(self.allowed_paths),
            "required_effects": list(self.required_effects),
        }

    @property
    def digest(self) -> str:
        assert self.packet_digest is not None
        return self.packet_digest

    def canonical(self) -> dict[str, Any]:
        return {**self._body(), "packet_digest": self.digest}


@dataclass(frozen=True)
class RepairVerificationRequest:
    """The bounded successor action input; it cannot request a new Review."""

    parent_digest: str
    repair_packet_digest: str
    candidate: CandidateIdentity
    prior_review_subject_digest: str
    request_digest: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.parent_digest, "parent_digest"),
            (self.repair_packet_digest, "repair_packet_digest"),
            (self.prior_review_subject_digest, "prior_review_subject_digest"),
        ):
            _require_digest(value, label)
        if type(self.candidate) is not CandidateIdentity:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Repair Verification requires an exact CandidateIdentity",
            )
        expected = _body_digest(self._body())
        if self.request_digest is None:
            object.__setattr__(self, "request_digest", expected)
        else:
            _validate_stored_digest(self.request_digest, self._body())

    def _body(self) -> dict[str, Any]:
        return {
            "kind": "repair_verification_subject.v1",
            "parent_digest": self.parent_digest,
            "repair_packet_digest": self.repair_packet_digest,
            "candidate": self.candidate.canonical(),
            "prior_review_subject_digest": self.prior_review_subject_digest,
            "action_kind": "repair_verify",
        }

    @property
    def digest(self) -> str:
        assert self.request_digest is not None
        return self.request_digest

    def canonical(self) -> dict[str, Any]:
        return {**self._body(), "request_digest": self.digest}


@dataclass(frozen=True)
class RepairVerificationResult:
    """Typed result of the bounded Repair Verification port."""

    request_digest: str
    accepted: bool
    scope_escape_paths: tuple[str, ...] = ()
    details: tuple[str, ...] = ()
    invalidated_obligation: str | None = None
    required_effects: tuple[str, ...] = ()
    result_digest: str | None = None

    def __post_init__(self) -> None:
        _require_digest(self.request_digest, "repair result request_digest")
        if type(self.accepted) is not bool:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Repair Verification accepted must be boolean",
            )
        _require_text_tuple(self.scope_escape_paths, "scope_escape_paths")
        _require_text_tuple(self.details, "repair verification details")
        if self.invalidated_obligation is not None:
            _require_text(self.invalidated_obligation, "invalidated_obligation")
        _require_text_tuple(self.required_effects, "required_effects")
        if self.scope_escape_paths and (
            self.invalidated_obligation is None or not self.required_effects
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Repair scope escape must name its obligation and effects",
            )
        expected = _body_digest(self._body())
        if self.result_digest is None:
            object.__setattr__(self, "result_digest", expected)
        else:
            _validate_stored_digest(self.result_digest, self._body())

    def _body(self) -> dict[str, Any]:
        return {
            "kind": "repair_verification_result.v1",
            "request_digest": self.request_digest,
            "accepted": self.accepted,
            "scope_escape_paths": list(self.scope_escape_paths),
            "details": list(self.details),
            "invalidated_obligation": self.invalidated_obligation,
            "required_effects": list(self.required_effects),
        }

    @property
    def digest(self) -> str:
        assert self.result_digest is not None
        return self.result_digest

    def canonical(self) -> dict[str, Any]:
        return {**self._body(), "result_digest": self.digest}


@dataclass(frozen=True)
class RepairVerificationEvidence:
    """Evidence retained for both normal and out-of-scope repair outcomes."""

    parent_digest: str
    candidate_digest: str
    repair_packet_digest: str
    request_digest: str
    accepted: bool
    scope_escape_paths: tuple[str, ...]
    details: tuple[str, ...]
    content_digest: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.parent_digest, "parent_digest"),
            (self.candidate_digest, "candidate_digest"),
            (self.repair_packet_digest, "repair_packet_digest"),
            (self.request_digest, "request_digest"),
        ):
            _require_digest(value, label)
        if type(self.accepted) is not bool:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "repair Evidence accepted must be boolean",
            )
        _require_text_tuple(self.scope_escape_paths, "scope_escape_paths")
        _require_text_tuple(self.details, "repair verification details")
        expected = _body_digest(self._body())
        if self.content_digest is None:
            object.__setattr__(self, "content_digest", expected)
        else:
            _validate_stored_digest(self.content_digest, self._body())

    @property
    def kind(self) -> str:
        return "repair_scope_escape" if self.scope_escape_paths else "repair_verification"

    def _body(self) -> dict[str, Any]:
        return {
            "kind": (
                "repair_scope_escape.v1"
                if self.scope_escape_paths
                else "repair_verification.v1"
            ),
            "parent_digest": self.parent_digest,
            "candidate_digest": self.candidate_digest,
            "repair_packet_digest": self.repair_packet_digest,
            "request_digest": self.request_digest,
            "accepted": self.accepted,
            "scope_escape_paths": list(self.scope_escape_paths),
            "details": list(self.details),
        }

    @property
    def digest(self) -> str:
        assert self.content_digest is not None
        return self.content_digest

    def canonical(self) -> dict[str, Any]:
        return {**self._body(), "content_digest": self.digest}


@dataclass(frozen=True)
class PlanInvalidationEvidence:
    """The exact Artifact payload accepted by RuntimeGateway._report... ."""

    runtime_subject: WorkRunSubject
    parent_digest: str
    candidate_digest: str
    source_kind: str
    source_evidence_digest: str
    invalidated_obligation: str
    required_effects: tuple[str, ...]
    workspace_identity: str
    discovered_facts: tuple[str, ...]
    reproduction: str
    content_digest: str | None = None
    source_evidence_digests: tuple[str, ...] | None = None
    lineage_artifacts: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if type(self.runtime_subject) is not WorkRunSubject:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Plan Invalidation Evidence requires an exact WorkRunSubject",
            )
        _require_digest(self.parent_digest, "parent_digest")
        _require_digest(self.candidate_digest, "candidate_digest")
        if self.source_kind not in {
            "scope_audit",
            "formal_review",
            "repair_verification",
        }:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Plan Invalidation Evidence source kind is outside the closed union",
            )
        _require_digest(self.source_evidence_digest, "source_evidence_digest")
        source_digests = (
            (self.source_evidence_digest,)
            if self.source_evidence_digests is None
            else _require_digest_tuple(
                self.source_evidence_digests,
                "source_evidence_digests",
            )
        )
        if source_digests[0] != self.source_evidence_digest:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "source_evidence_digest must name the first canonical source digest",
            )
        object.__setattr__(self, "source_evidence_digests", source_digests)
        if type(self.lineage_artifacts) is not tuple or any(
            type(item) is not dict for item in self.lineage_artifacts
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Plan Invalidation lineage Artifacts must be an immutable object tuple",
            )
        for item in self.lineage_artifacts:
            try:
                encoded = canonical_bytes(item)
                if load_canonical_json(encoded) != item:
                    raise ValueError("non-canonical lineage Artifact")
            except Exception as error:
                raise CandidateGateError(
                    "CANDIDATE_GATE_EVIDENCE_INVALID",
                    "Plan Invalidation lineage Artifact is not canonical JSON",
                ) from error
        _require_text(self.invalidated_obligation, "invalidated_obligation")
        _require_text_tuple(self.required_effects, "required_effects", allow_empty=False)
        _require_text(self.workspace_identity, "workspace_identity")
        _require_text_tuple(self.discovered_facts, "discovered_facts", allow_empty=False)
        _require_text(self.reproduction, "reproduction")
        if any("=" in fact and fact.split("=", 1)[0] in {
            "parent_digest",
            "candidate_digest",
            "source_kind",
            "source_evidence_digest",
            "source_evidence_digests",
        } for fact in self.discovered_facts):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Plan Invalidation discovered facts contain reserved lineage markers",
            )
        expected = digest_bytes(self._canonical_bytes())
        if self.content_digest is None:
            object.__setattr__(self, "content_digest", expected)
        else:
            _require_digest(self.content_digest, "content_digest")
            if self.content_digest != expected:
                raise CandidateGateError(
                    "CANDIDATE_GATE_EVIDENCE_INVALID",
                    "Plan Invalidation Evidence digest changed",
                )

    @property
    def kind(self) -> str:
        return "plan_invalidation"

    def _facts(self) -> tuple[str, ...]:
        assert self.source_evidence_digests is not None
        return (
            f"parent_digest={self.parent_digest}",
            f"candidate_digest={self.candidate_digest}",
            f"source_kind={self.source_kind}",
            f"source_evidence_digest={self.source_evidence_digest}",
            *self.discovered_facts,
        )

    def canonical(self) -> dict[str, Any]:
        return {
            "schema_version": "gwo.evidence.v1",
            "kind": "plan_invalidation",
            "subject": self.runtime_subject.canonical(),
            "source_evidence_digests": list(self.source_evidence_digests),
            "discovered_facts": list(self._facts()),
            "reproduction": self.reproduction,
            "invalidated_obligation": self.invalidated_obligation,
            "required_effects": list(self.required_effects),
            "workspace_identity": self.workspace_identity,
            "lineage_artifacts": [dict(item) for item in self.lineage_artifacts],
        }

    def _canonical_bytes(self) -> bytes:
        from ._canonical import canonical_bytes

        return canonical_bytes(self.canonical())

    @property
    def digest(self) -> str:
        assert self.content_digest is not None
        return self.content_digest

    @property
    def evidence_digest(self) -> str:
        return self.digest

    def has_valid_digest(self) -> bool:
        return self.digest == digest_bytes(self._canonical_bytes())


@dataclass(frozen=True)
class CandidateGateResult:
    """Read-only CandidateGate output; no Campaign classification is present."""

    status: CandidateGateStatus
    evidence: tuple[object, ...]
    plan_invalidation_receipt: PlanInvalidationReceipt | None = None
    plan_invalidation_report: PlanInvalidationReport | None = None
    repair_packet: RepairPacket | None = None
    formal_review_request: FormalReviewRequest | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not CandidateGateStatus:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "CandidateGate result status is outside the closed union",
            )
        if type(self.evidence) is not tuple or any(
            item is None for item in self.evidence
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "CandidateGate result Evidence is not an immutable tuple",
            )
        if self.plan_invalidation_receipt is not None and type(
            self.plan_invalidation_receipt
        ) is not PlanInvalidationReceipt:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "CandidateGate result receipt is not a PlanInvalidationReceipt",
            )
        if self.plan_invalidation_report is not None and type(
            self.plan_invalidation_report
        ) is not PlanInvalidationReport:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "CandidateGate result report is not a PlanInvalidationReport",
            )

    @property
    def receipt(self) -> PlanInvalidationReceipt | None:
        """Compatibility alias for callers that call the readback a receipt."""

        return self.plan_invalidation_receipt

    @property
    def classification(self) -> None:
        """CandidateGate never selects a Campaign classification disposition."""

        return None


class PlanInvalidationReporter(Protocol):
    """The minimal private adapter needed by CandidateGate."""

    def report_plan_invalidation(
        self,
        subject: WorkRunSubject,
        evidence: PlanInvalidationEvidence,
        report: PlanInvalidationReport,
    ) -> PlanInvalidationReceipt: ...


class CandidateReadbackPort(Protocol):
    """Authoritative exact-reference Candidate readback port."""

    def read_candidate(
        self,
        repository: str,
        reported_reference: str,
    ) -> CandidateReadback: ...


class FormalReviewer(Protocol):
    """A read-only Formal Review action over one immutable request."""

    def review(self, request: FormalReviewRequest) -> FormalReviewResult: ...


class RepairVerifier(Protocol):
    """A bounded read-only Repair Verification action."""

    def verify(self, request: RepairVerificationRequest) -> RepairVerificationResult: ...


class RuntimeGatewayPlanInvalidationAdapter:
    """Bridge to the existing private RuntimeGateway report contract.

    The adapter is intentionally explicit because RuntimeGateway does not
    expose a fourth public workflow operation.  It stores the exact canonical
    Evidence in the Gateway-owned ArtifactStore, then calls only
    ``_report_plan_invalidation``.  The Gateway's existing dedup/readback path
    therefore handles duplicate and replay submissions without a new kernel or
    runtime path.
    """

    def __init__(self, gateway: object, *, artifact_store: object | None = None):
        report_method = getattr(gateway, "_report_plan_invalidation", None)
        if not callable(report_method):
            raise CandidateGateError(
                "CANDIDATE_GATE_ADAPTER_INVALID",
                "RuntimeGateway private Plan Invalidation report seam is unavailable",
            )
        store = artifact_store if artifact_store is not None else getattr(
            gateway, "_artifacts", None
        )
        if store is None or not callable(getattr(store, "put_canonical", None)):
            raise CandidateGateError(
                "CANDIDATE_GATE_ADAPTER_INVALID",
                "RuntimeGateway ArtifactStore canonical write seam is unavailable",
            )
        self._gateway = gateway
        self._artifact_store = store
        self._report_method = report_method

    def report_plan_invalidation(
        self,
        subject: WorkRunSubject,
        evidence: PlanInvalidationEvidence,
        report: PlanInvalidationReport,
    ) -> PlanInvalidationReceipt:
        reference = self._artifact_store.put_canonical(evidence.canonical())
        if getattr(reference, "digest", None) != evidence.digest:
            raise CandidateGateError(
                "CANDIDATE_GATE_ADAPTER_INVALID",
                "RuntimeGateway ArtifactStore changed Plan Invalidation Evidence digest",
            )
        if report.evidence_digest != evidence.digest:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Plan Invalidation report is not bound to the Evidence Artifact",
            )
        return self._report_method(subject, report)


def _reporter_role(subject: WorkRunSubject) -> str:
    purpose = subject.purpose
    if type(purpose) is not WorkRunPurpose:
        raise CandidateGateError(
            "CANDIDATE_GATE_EVIDENCE_INVALID",
            "CandidateGate parent purpose is not an exact WorkRunPurpose",
        )
    if purpose.kind == "implementation":
        return "worker"
    if purpose.kind == "terminal_recovery_implementation":
        return "recovery_worker"
    return "review"


def _unique_sorted(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


class CandidateGate:
    """Route Candidate, Formal Review, and Repair facts without mutation."""

    def __init__(
        self,
        *,
        invalidation_reporter: PlanInvalidationReporter,
        candidate_reader: CandidateReadbackPort | None = None,
        formal_reviewer: FormalReviewer | None = None,
        repair_verifier: RepairVerifier | None = None,
    ) -> None:
        if not callable(getattr(invalidation_reporter, "report_plan_invalidation", None)):
            raise CandidateGateError(
                "CANDIDATE_GATE_ADAPTER_INVALID",
                "CandidateGate requires an explicit Plan Invalidation reporter",
            )
        if candidate_reader is not None and not callable(
            getattr(candidate_reader, "read_candidate", None)
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_ADAPTER_INVALID",
                "Candidate readback port does not expose read_candidate",
            )
        if formal_reviewer is not None and not callable(
            getattr(formal_reviewer, "review", None)
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_ADAPTER_INVALID",
                "Formal Reviewer does not expose the read-only review protocol",
            )
        if repair_verifier is not None and not callable(
            getattr(repair_verifier, "verify", None)
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_ADAPTER_INVALID",
                "Repair Verifier does not expose the bounded verify protocol",
            )
        self._invalidation_reporter = invalidation_reporter
        self._candidate_reader = candidate_reader
        self._formal_reviewer = formal_reviewer
        self._repair_verifier = repair_verifier

    def audit_candidate(
        self,
        parent: CandidateGateParent,
        audit: CandidateAuditReport,
    ) -> CandidateGateResult:
        """Audit one Candidate and optionally enter exactly one Formal Review."""

        self._validate_parent(parent)
        audit = self._read_authoritative_candidate(parent, audit)
        self._validate_audit(parent, audit)
        candidate_evidence = audit.evidence
        invalidating = tuple(
            failure
            for failure in audit.failures
            if failure.route is AuditFailureRoute.TICKET_UNSATISFIABLE
        )
        if invalidating:
            plan_evidence = self._plan_evidence_from_audit(
                parent,
                audit,
                invalidating,
            )
            receipt, report = self._report_invalidation(parent, plan_evidence)
            return CandidateGateResult(
                status=CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
                evidence=(candidate_evidence, plan_evidence),
                plan_invalidation_receipt=receipt,
                plan_invalidation_report=report,
            )
        if audit.failures:
            return CandidateGateResult(
                status=CandidateGateStatus.ORDINARY_REJECTED,
                evidence=(candidate_evidence,),
            )
        request = FormalReviewRequest.from_parent(parent, audit)
        reviewer = self._formal_reviewer
        if reviewer is None:
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEWER_UNAVAILABLE",
                "a clean deterministic Candidate requires the CandidateGate Formal Reviewer",
            )
        self._validate_read_only_port(reviewer, "Formal Reviewer")
        review_result = reviewer.review(request)
        self._validate_review_result(parent, request, review_result)
        findings = tuple(sorted(review_result.findings, key=lambda finding: finding.digest))
        finding_evidence: tuple[FormalReviewFinding, ...] = findings
        scope_findings = tuple(finding for finding in findings if finding.scope_escape)
        if scope_findings:
            plan_evidence = self._plan_evidence_from_findings(
                parent,
                audit,
                findings,
            )
            receipt, report = self._report_invalidation(parent, plan_evidence)
            return CandidateGateResult(
                status=CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
                evidence=(candidate_evidence, *finding_evidence, plan_evidence),
                plan_invalidation_receipt=receipt,
                plan_invalidation_report=report,
                formal_review_request=request,
            )
        hard_findings = tuple(
            finding for finding in findings if finding.severity == "hard"
        )
        if hard_findings:
            packet = RepairPacket.from_findings(
                parent,
                audit.candidate,
                request,
                hard_findings,
            )
            return CandidateGateResult(
                status=CandidateGateStatus.REPAIR_REQUIRED,
                evidence=(candidate_evidence, *finding_evidence),
                repair_packet=packet,
                formal_review_request=request,
            )
        return CandidateGateResult(
            status=CandidateGateStatus.REVIEW_ACCEPTED,
            evidence=(candidate_evidence, *finding_evidence),
            formal_review_request=request,
        )

    def _read_authoritative_candidate(
        self,
        parent: CandidateGateParent,
        audit: CandidateAuditReport,
    ) -> CandidateAuditReport:
        """Bind the deterministic audit to one exact Candidate readback.

        The optional port preserves the pure, already-read audit seam used by
        predecessor tests.  Production composition supplies it; once present,
        every identity and the complete diff Artifact must agree before any
        deterministic failure, Formal Review, or Plan Invalidation effect.
        """

        reader = self._candidate_reader
        if reader is None:
            return audit
        if type(audit) is not CandidateAuditReport:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "CandidateGate requires an exact CandidateAuditReport",
            )
        try:
            readback = reader.read_candidate(
                parent.runtime_subject.repository,
                audit.candidate.reported_reference,
            )
        except CandidateGateError:
            raise
        except Exception as error:
            raise CandidateGateError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "authoritative Candidate reference readback failed",
            ) from error
        if type(readback) is not CandidateReadback:
            raise CandidateGateError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "authoritative Candidate readback is not the exact typed value",
            )
        if readback.repository != parent.runtime_subject.repository:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_STALE",
                "authoritative Candidate belongs to another repository",
            )
        if readback.candidate != audit.candidate:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_STALE",
                "deterministic Candidate audit differs from authoritative readback",
            )
        if audit.diff_record is not None and audit.diff_record != readback.diff_record:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_STALE",
                "Candidate audit diff differs from authoritative readback",
            )
        return replace(audit, diff_record=readback.diff_record, report_digest=None)

    def verify_repair(
        self,
        parent: CandidateGateParent,
        packet: RepairPacket,
        candidate: CandidateIdentity,
    ) -> CandidateGateResult:
        """Verify a bounded Repair Packet without reopening Formal Review."""

        self._validate_parent(parent)
        self._validate_repair_packet(parent, packet)
        if type(candidate) is not CandidateIdentity:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Repair Verification requires an exact CandidateIdentity",
            )
        request = RepairVerificationRequest(
            parent_digest=parent.digest,
            repair_packet_digest=packet.digest,
            candidate=candidate,
            prior_review_subject_digest=packet.prior_review_subject_digest,
        )
        verifier = self._repair_verifier
        if verifier is None:
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_VERIFIER_UNAVAILABLE",
                "Repair Verification requires the CandidateGate Repair Verifier",
            )
        self._validate_read_only_port(verifier, "Repair Verifier")
        result = verifier.verify(request)
        self._validate_repair_result(request, result)
        allowed_paths = set(packet.allowed_paths)
        candidate_paths = set(candidate.changed_paths)
        reported_paths = set(result.scope_escape_paths)
        # The verifier is a read-only observer.  Its claim cannot enlarge the
        # repair boundary, and an allowed path is not a Campaign-level scope
        # escape merely because the verifier mentioned it.  CandidateGate
        # derives the authoritative escape set from the exact repaired
        # Candidate paths and fails closed on an unverifiable extra claim.
        if reported_paths & allowed_paths:
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_SCOPE_INVALID",
                "Repair Verification reported a path already allowed by the packet",
            )
        if reported_paths - candidate_paths:
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_SCOPE_INVALID",
                "Repair Verification reported a path absent from the exact Candidate",
            )
        authoritative_escape_paths = tuple(sorted(candidate_paths - allowed_paths))
        if authoritative_escape_paths and (
            result.invalidated_obligation is None or not result.required_effects
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_SCOPE_INVALID",
                "an exact Candidate scope escape lacks its obligation and effects",
            )
        verification_evidence = RepairVerificationEvidence(
            parent_digest=parent.digest,
            candidate_digest=candidate.digest,
            repair_packet_digest=packet.digest,
            request_digest=request.digest,
            accepted=result.accepted,
            scope_escape_paths=authoritative_escape_paths,
            details=result.details,
        )
        if authoritative_escape_paths:
            assert result.invalidated_obligation is not None
            plan_evidence = PlanInvalidationEvidence(
                runtime_subject=parent.runtime_subject,
                parent_digest=parent.digest,
                candidate_digest=candidate.digest,
                source_kind="repair_verification",
                source_evidence_digest=verification_evidence.digest,
                source_evidence_digests=(verification_evidence.digest,),
                invalidated_obligation=result.invalidated_obligation,
                required_effects=result.required_effects,
                workspace_identity=parent.workspace_identity,
                discovered_facts=tuple(
                    f"escaped_path={path}" for path in authoritative_escape_paths
                ),
                reproduction=(
                    "repair_verification:"
                    f"candidate={candidate.digest}:packet={packet.digest}"
                ),
                lineage_artifacts=(
                    candidate.canonical(),
                    packet.canonical(),
                    request.canonical(),
                    result.canonical(),
                    verification_evidence.canonical(),
                ),
            )
            receipt, report = self._report_invalidation(parent, plan_evidence)
            return CandidateGateResult(
                status=CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
                evidence=(verification_evidence, plan_evidence),
                plan_invalidation_receipt=receipt,
                plan_invalidation_report=report,
            )
        return CandidateGateResult(
            status=(
                CandidateGateStatus.REPAIR_ACCEPTED
                if result.accepted
                else CandidateGateStatus.REPAIR_REJECTED
            ),
            evidence=(verification_evidence,),
            repair_packet=packet,
        )

    def replay_plan_invalidation(
        self,
        parent: CandidateGateParent,
        evidence: PlanInvalidationEvidence,
        report: PlanInvalidationReport,
    ) -> CandidateGateResult:
        """Read back one duplicate report through the same Gateway contract."""

        self._validate_parent(parent)
        self._validate_plan_evidence(parent, evidence)
        self._validate_report_binding(parent, evidence, report)
        receipt = self._invalidation_reporter.report_plan_invalidation(
            parent.runtime_subject,
            evidence,
            report,
        )
        self._validate_receipt(parent, evidence, report, receipt)
        return CandidateGateResult(
            status=CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
            evidence=(evidence,),
            plan_invalidation_receipt=receipt,
        )

    @staticmethod
    def _validate_parent(parent: CandidateGateParent) -> None:
        if type(parent) is not CandidateGateParent:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "CandidateGate requires an exact CandidateGateParent",
            )
        _validate_stored_digest(parent.parent_digest, parent._body())

    @staticmethod
    def _validate_read_only_port(port: object, role: str) -> None:
        proof = getattr(port, "capability_policy_proof", None)
        if type(proof) is not CapabilityPolicyProof or not proof.capability_policy.is_proven:
            raise CandidateGateError(
                "CANDIDATE_GATE_CAPABILITY_PROOF_INVALID",
                f"{role} capability readback does not prove a read-only, non-delegating boundary",
            )

    @classmethod
    def _validate_audit(
        cls,
        parent: CandidateGateParent,
        audit: CandidateAuditReport,
    ) -> None:
        if type(audit) is not CandidateAuditReport:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "CandidateGate requires an exact CandidateAuditReport",
            )
        if audit.parent_digest != parent.digest:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_STALE",
                "Candidate audit is bound to a different parent revision",
            )
        _validate_stored_digest(audit.report_digest, audit._body())
        evidence = audit.evidence
        if not evidence.has_valid_digest():
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Candidate audit Evidence digest changed",
            )
        if any(
            failure.digest != _body_digest(failure._body())
            for failure in audit.failures
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "deterministic audit failure Evidence digest changed",
            )

    @classmethod
    def _validate_review_result(
        cls,
        parent: CandidateGateParent,
        request: FormalReviewRequest,
        result: object,
    ) -> FormalReviewResult:
        if type(result) is not FormalReviewResult:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Formal Reviewer returned malformed Evidence",
            )
        _validate_stored_digest(result.result_digest, result._body())
        if result.subject_digest != request.digest:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_STALE",
                "Formal Review result is bound to a stale subject",
            )
        for finding in result.findings:
            if (
                finding.parent_digest != parent.digest
                or finding.candidate_digest != request.candidate_digest
                or finding.review_subject_digest != request.digest
            ):
                raise CandidateGateError(
                    "CANDIDATE_GATE_EVIDENCE_STALE",
                    "Formal Review Finding is not bound to the current parent subject",
                )
            _validate_stored_digest(finding.content_digest, finding._body())
        return result

    @classmethod
    def _validate_repair_packet(
        cls,
        parent: CandidateGateParent,
        packet: RepairPacket,
    ) -> None:
        if type(packet) is not RepairPacket:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "CandidateGate requires an exact RepairPacket",
            )
        if packet.parent_digest != parent.digest:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_STALE",
                "Repair Packet belongs to a different parent revision",
            )
        _validate_stored_digest(packet.packet_digest, packet._body())

    @classmethod
    def _validate_repair_result(
        cls,
        request: RepairVerificationRequest,
        result: object,
    ) -> RepairVerificationResult:
        if type(result) is not RepairVerificationResult:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Repair Verifier returned malformed Evidence",
            )
        _validate_stored_digest(result.result_digest, result._body())
        if result.request_digest != request.digest:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_STALE",
                "Repair Verification result belongs to a stale request",
            )
        return result

    def _plan_evidence_from_audit(
        self,
        parent: CandidateGateParent,
        audit: CandidateAuditReport,
        failures: tuple[DeterministicAuditFailure, ...],
    ) -> PlanInvalidationEvidence:
        obligation = self._combined_obligation(
            tuple(failure.invalidated_obligation for failure in failures)
        )
        effects = _unique_sorted(
            [effect for failure in failures for effect in failure.required_effects]
        )
        return PlanInvalidationEvidence(
            runtime_subject=parent.runtime_subject,
            parent_digest=parent.digest,
            candidate_digest=audit.candidate.digest,
            source_kind="scope_audit",
            source_evidence_digest=audit.evidence.digest,
            source_evidence_digests=(audit.evidence.digest,),
            invalidated_obligation=obligation,
            required_effects=effects,
            workspace_identity=parent.workspace_identity,
            discovered_facts=tuple(
                f"{failure.kind.value}:{failure.code}:{failure.detail}"
                for failure in sorted(failures, key=lambda item: item.digest)
            ),
            reproduction=(
                "scope_audit:"
                f"candidate={audit.candidate.digest}:audit={audit.evidence.digest}"
            ),
            lineage_artifacts=tuple(
                [audit.canonical(), audit.evidence.canonical()]
                + [failure.canonical() for failure in audit._ordered_failures()]
            ),
        )

    def _plan_evidence_from_findings(
        self,
        parent: CandidateGateParent,
        audit: CandidateAuditReport,
        findings: tuple[FormalReviewFinding, ...],
    ) -> PlanInvalidationEvidence:
        candidate = audit.candidate
        obligation = self._combined_obligation(
            tuple(
                finding.invalidated_obligation
                for finding in findings
                if finding.scope_escape
            )
        )
        effects = _unique_sorted(
            [
                effect
                for finding in findings
                if finding.scope_escape
                for effect in finding.required_effects
            ]
        )
        source_digests = tuple(sorted(finding.digest for finding in findings))
        source_digest = source_digests[0]
        request = FormalReviewRequest.from_parent(parent, audit)
        review_result = FormalReviewResult(
            subject_digest=request.digest,
            findings=findings,
        )
        return PlanInvalidationEvidence(
            runtime_subject=parent.runtime_subject,
            parent_digest=parent.digest,
            candidate_digest=candidate.digest,
            source_kind="formal_review",
            source_evidence_digest=source_digest,
            source_evidence_digests=source_digests,
            invalidated_obligation=obligation,
            required_effects=effects,
            workspace_identity=parent.workspace_identity,
            discovered_facts=tuple(
                f"finding={finding.finding_id}:code={finding.code}:message={finding.message}"
                for finding in findings
            ),
            reproduction=(
                "formal_review:"
                f"candidate={candidate.digest}:findings={','.join(source_digests)}"
            ),
            lineage_artifacts=tuple(
                [
                    audit.canonical(),
                    audit.evidence.canonical(),
                    request.canonical(),
                    review_result.canonical(),
                ]
                + [finding.canonical() for finding in findings]
            ),
        )

    @staticmethod
    def _combined_obligation(values: tuple[str | None, ...]) -> str:
        obligations = tuple(sorted({value for value in values if value is not None}))
        if not obligations:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "plan invalidation Evidence has no invalidated obligation",
            )
        return " | ".join(obligations)

    def _report_invalidation(
        self,
        parent: CandidateGateParent,
        evidence: PlanInvalidationEvidence,
    ) -> tuple[PlanInvalidationReceipt, PlanInvalidationReport]:
        self._validate_plan_evidence(parent, evidence)
        report = PlanInvalidationReport(
            repository=parent.runtime_subject.repository,
            campaign_key=parent.runtime_subject.campaign_key,
            plan_revision_digest=parent.runtime_subject.plan_revision_digest,
            ticket_key=parent.runtime_subject.ticket_key,
            work_run_key=parent.runtime_subject.work_run_key,
            runtime_binding_id=parent.runtime_subject.stable_action_id,
            authority_subtree_digest=parent.runtime_subject.authority_subtree_digest,
            reporter_role=_reporter_role(parent.runtime_subject),
            evidence_digest=evidence.digest,
            dedup_identity=(
                "candidate-gate:"
                + digest_value(
                    {
                        "kind": "candidate_gate_plan_invalidation.v1",
                        "parent_digest": parent.digest,
                        "candidate_digest": evidence.candidate_digest,
                        "source_kind": evidence.source_kind,
                        "source_evidence_digest": evidence.source_evidence_digest,
                        "source_evidence_digests": list(
                            evidence.source_evidence_digests
                        ),
                        "invalidated_obligation": evidence.invalidated_obligation,
                        "required_effects": list(evidence.required_effects),
                    }
                )
            ),
            invalidated_obligation=evidence.invalidated_obligation,
            required_effects=evidence.required_effects,
            workspace_identity=parent.workspace_identity,
        )
        receipt = self._invalidation_reporter.report_plan_invalidation(
            parent.runtime_subject,
            evidence,
            report,
        )
        receipt = self._validate_receipt(parent, evidence, report, receipt)
        return receipt, report

    @classmethod
    def _validate_plan_evidence(
        cls,
        parent: CandidateGateParent,
        evidence: PlanInvalidationEvidence,
    ) -> None:
        if type(evidence) is not PlanInvalidationEvidence:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Plan Invalidation Evidence is not the exact closed type",
            )
        if evidence.parent_digest != parent.digest:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_STALE",
                "Plan Invalidation Evidence belongs to a stale parent",
            )
        if evidence.runtime_subject != parent.runtime_subject:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_STALE",
                "Plan Invalidation Evidence subject changed",
            )
        if not evidence.has_valid_digest():
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Plan Invalidation Evidence digest changed",
            )

    @classmethod
    def _validate_report_binding(
        cls,
        parent: CandidateGateParent,
        evidence: PlanInvalidationEvidence,
        report: PlanInvalidationReport,
    ) -> None:
        if type(report) is not PlanInvalidationReport:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Plan Invalidation report is not the exact Gateway type",
            )
        subject = parent.runtime_subject
        expected = (
            report.repository == subject.repository
            and report.campaign_key == subject.campaign_key
            and report.plan_revision_digest == subject.plan_revision_digest
            and report.ticket_key == subject.ticket_key
            and report.work_run_key == subject.work_run_key
            and report.runtime_binding_id == subject.stable_action_id
            and report.authority_subtree_digest == subject.authority_subtree_digest
            and report.reporter_role == _reporter_role(subject)
            and report.evidence_digest == evidence.digest
            and report.workspace_identity == parent.workspace_identity
        )
        if not expected:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_STALE",
                "Plan Invalidation report is not bound to the current parent",
            )

    @classmethod
    def _validate_receipt(
        cls,
        parent: CandidateGateParent,
        evidence: PlanInvalidationEvidence,
        report: PlanInvalidationReport,
        receipt: object,
    ) -> PlanInvalidationReceipt:
        cls._validate_report_binding(parent, evidence, report)
        if type(receipt) is not PlanInvalidationReceipt:
            raise CandidateGateError(
                "CANDIDATE_GATE_RECEIPT_INVALID",
                "RuntimeGateway did not return a PlanInvalidationReceipt",
            )
        proof = receipt.capability_policy_proof
        if type(proof) is not CapabilityPolicyProof or not proof.capability_policy.is_proven:
            raise CandidateGateError(
                "CANDIDATE_GATE_RECEIPT_INVALID",
                "Plan Invalidation Receipt lacks a closed capability proof",
            )
        _require_digest(receipt.receipt_digest, "receipt_digest")
        if receipt.report_digest != report.digest:
            raise CandidateGateError(
                "CANDIDATE_GATE_RECEIPT_INVALID",
                "Plan Invalidation Receipt report digest is stale",
            )
        expected_receipt_digest = digest_value(
            {
                "kind": "plan_invalidation_receipt.v1",
                "report_digest": report.digest,
                "subject_digest": parent.runtime_subject.digest,
                "authority_record_digest": proof.authority_record_digest,
            }
        )
        if receipt.receipt_digest != expected_receipt_digest:
            raise CandidateGateError(
                "CANDIDATE_GATE_RECEIPT_INVALID",
                "Plan Invalidation Receipt digest is malformed",
            )
        observation = receipt.observation
        expected_observation = {
            "kind": "plan_invalidation_observation.v1",
            "repository": report.repository,
            "campaign_key": report.campaign_key,
            "plan_revision_digest": report.plan_revision_digest,
            "ticket_key": report.ticket_key,
            "work_run_key": report.work_run_key,
            "runtime_binding_id": report.runtime_binding_id,
            "authority_subtree_digest": report.authority_subtree_digest,
            "reporter_role": report.reporter_role,
            "report_digest": report.digest,
            "evidence_digest": evidence.digest,
            "dedup_identity": report.dedup_identity,
            "invalidated_obligation": report.invalidated_obligation,
            "required_effects": list(report.required_effects),
            "workspace_identity": report.workspace_identity,
        }
        if not isinstance(observation, Mapping):
            raise CandidateGateError(
                "CANDIDATE_GATE_RECEIPT_INVALID",
                "Plan Invalidation Receipt readback changed its observation",
            )
        observed = dict(observation)
        expected_source_digests = list(evidence.source_evidence_digests)
        if "source_evidence_digests" not in observed:
            raise CandidateGateError(
                "CANDIDATE_GATE_RECEIPT_INVALID",
                "Plan Invalidation Receipt omitted authoritative source Evidence lineage",
            )
        elif observed["source_evidence_digests"] != expected_source_digests:
            raise CandidateGateError(
                "CANDIDATE_GATE_RECEIPT_INVALID",
                "Plan Invalidation Receipt source Evidence lineage is stale",
            )
        expected_observation["source_evidence_digests"] = expected_source_digests
        if observed != expected_observation:
            raise CandidateGateError(
                "CANDIDATE_GATE_RECEIPT_INVALID",
                "Plan Invalidation Receipt readback changed its observation",
            )
        return receipt


__all__ = [
    "AuditFailureKind",
    "AuditFailureRoute",
    "CandidateAuditEvidence",
    "CandidateAuditReport",
    "CandidateDiffEntryV1",
    "CandidateDiffRecordV1",
    "CandidateGate",
    "CandidateGateError",
    "CandidateGateParent",
    "CandidateGateResult",
    "CandidateGateStatus",
    "CandidateIdentity",
    "CandidateReadback",
    "CandidateReadbackPort",
    "DeterministicAuditFailure",
    "FormalReviewer",
    "FormalReviewFinding",
    "FormalReviewRequest",
    "FormalReviewResult",
    "PlanInvalidationEvidence",
    "PlanInvalidationReporter",
    "RepairPacket",
    "RepairVerifier",
    "RepairVerificationEvidence",
    "RepairVerificationRequest",
    "RepairVerificationResult",
    "RuntimeGatewayPlanInvalidationAdapter",
]
