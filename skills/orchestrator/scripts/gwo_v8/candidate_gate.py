"""Read-only CandidateGate routing for late Plan Invalidation discoveries.

This module deliberately owns only the semantic seam added by Issue #137.  It
does not drive a Campaign, write a Ticket or Plan, edit a workspace, or call a
public workflow operation.  A deterministic audit may either reject an
ordinary unauthorized Candidate or prove that the frozen Ticket is no longer
safe to satisfy.  Only the latter is handed to the existing RuntimeGateway
Plan Invalidation contract.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field, replace
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
_MISSING = object()


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
    DECISION_REQUIRED = "decision_required"


class AssuranceMode(str, Enum):
    NO_REVIEW = "no_review"
    STANDARD = "standard"
    STRICT = "strict"


class InvalidReviewTransport(RuntimeError):
    """A typed, retryable Formal Review transport failure."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.code = "INVALID_REVIEW_TRANSPORT"
        self.detail = detail


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


def _require_object_id_for_format(
    value: object,
    field_name: str,
    object_format: str,
) -> str:
    expected_length = 40 if object_format == "sha1" else 64
    if (
        type(value) is not str
        or len(value) != expected_length
        or re.fullmatch(r"[0-9a-f]+", value) is None
    ):
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"{field_name} is not an exact {object_format} object ID",
        )
    return value


def _git_file_type(mode: str) -> str:
    if mode in {"100644", "100755"}:
        return "regular"
    if mode == "120000":
        return "symlink"
    if mode == "160000":
        return "gitlink"
    raise CandidateGateError(
        "CANDIDATE_GATE_DIFF_INVALID",
        "Candidate diff mode is not a supported Git file type",
    )


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
    try:
        _require_digest(stored, "content_digest")
    except CandidateGateError as error:
        if code == "CANDIDATE_GATE_EVIDENCE_INVALID":
            raise
        raise CandidateGateError(code, error.detail) from error
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


def _decode_candidate_path_token(token: str, field_name: str) -> bytes:
    _require_text(token, field_name)
    if "=" in token:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"{field_name} is padded instead of unpadded base64url",
        )
    try:
        raw = base64.b64decode(
            token + "=" * (-len(token) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as error:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"{field_name} is not canonical base64url",
        ) from error
    if not raw or b"\x00" in raw or raw.startswith(b"/"):
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"{field_name} is not a non-empty repository-relative raw path",
        )
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if encoded != token:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"{field_name} is not in canonical base64url form",
        )
    return raw


def _validate_diff_side(
    *,
    path: str | None,
    mode: str | None,
    object_type: str | None,
    oid: str | None,
    side: str,
    legacy: bool = False,
) -> None:
    values = (path, mode, object_type, oid)
    if all(value is None for value in values):
        return
    if any(value is None for value in values):
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"Candidate diff {side} identity is only partially present",
        )
    assert path is not None and mode is not None
    assert object_type is not None and oid is not None
    if legacy:
        _require_text(path, f"{side}_path")
    else:
        _decode_candidate_path_token(path, f"{side}_path")
    if re.fullmatch(r"[0-7]{6}", mode) is None:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"Candidate diff {side} mode is invalid",
        )
    if object_type not in {"blob", "gitlink"}:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"Candidate diff {side} object type is invalid",
        )
    file_type = _git_file_type(mode)
    if (file_type == "gitlink") != (object_type == "gitlink"):
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"Candidate diff {side} mode and object type are inconsistent",
        )
    _require_object_id(oid, f"{side}_oid")


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    """The exact Candidate/base identity consumed by deterministic audit."""

    reported_reference: str
    base_commit_oid: str
    base_tree_oid: str
    candidate_commit_oid: str
    candidate_tree_oid: str
    changed_path_tokens: tuple[str, ...]
    candidate_digest: str | None = None

    def __init__(
        self,
        reported_reference: str,
        base_commit_oid: str,
        base_tree_oid: str,
        candidate_commit_oid: str,
        candidate_tree_oid: str,
        changed_path_tokens: tuple[str, ...] | None = None,
        candidate_digest: str | None = None,
        *,
        changed_paths: tuple[str, ...] | None = None,
    ) -> None:
        if changed_path_tokens is not None and changed_paths is not None:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "CandidateIdentity received both changed path spellings",
            )
        object.__setattr__(
            self,
            "reported_reference",
            reported_reference,
        )
        object.__setattr__(self, "base_commit_oid", base_commit_oid)
        object.__setattr__(self, "base_tree_oid", base_tree_oid)
        object.__setattr__(self, "candidate_commit_oid", candidate_commit_oid)
        object.__setattr__(self, "candidate_tree_oid", candidate_tree_oid)
        object.__setattr__(
            self,
            "changed_path_tokens",
            changed_path_tokens if changed_path_tokens is not None else changed_paths,
        )
        object.__setattr__(self, "candidate_digest", candidate_digest)
        self.__post_init__()

    def __post_init__(self) -> None:
        _require_text(self.reported_reference, "reported_reference")
        for field_name in (
            "base_commit_oid",
            "base_tree_oid",
            "candidate_commit_oid",
            "candidate_tree_oid",
        ):
            _require_object_id(getattr(self, field_name), field_name)
        _require_text_tuple(self.changed_path_tokens, "changed_path_tokens")
        if self.changed_path_tokens != tuple(sorted(set(self.changed_path_tokens))):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "changed_path_tokens must be sorted and unique",
            )
        expected = digest_value(self._body())
        if self.candidate_digest is None:
            object.__setattr__(self, "candidate_digest", expected)
        else:
            _validate_stored_digest(
                self.candidate_digest,
                self._body(),
                code="CANDIDATE_GATE_EVIDENCE_INVALID",
                detail="CandidateIdentity digest changed",
            )

    def _body(self) -> dict[str, object]:
        return {
            "kind": "candidate_identity.v1",
            "reported_reference": self.reported_reference,
            "base_commit_oid": self.base_commit_oid,
            "base_tree_oid": self.base_tree_oid,
            "candidate_commit_oid": self.candidate_commit_oid,
            "candidate_tree_oid": self.candidate_tree_oid,
            "changed_path_tokens": list(self.changed_path_tokens),
        }

    @property
    def digest(self) -> str:
        assert self.candidate_digest is not None
        return self.candidate_digest

    @property
    def changed_paths(self) -> tuple[str, ...]:
        """Read-only migration spelling for predecessor tests."""
        return self.changed_path_tokens

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "candidate_digest": self.digest}


@dataclass(frozen=True, slots=True, init=False)
class CandidateDiffEntryV1:
    """One complete old/new raw-Git tree entry in a Candidate diff."""

    old_path: str | None
    new_path: str | None
    change_kind: str
    old_mode: str | None
    new_mode: str | None
    old_object_type: str | None
    new_object_type: str | None
    old_oid: str | None
    new_oid: str | None
    _legacy_mode: bool = field(default=False, init=False, repr=False, compare=False)

    def __init__(
        self,
        *args: object,
        old_path: str | None = _MISSING,
        new_path: str | None = _MISSING,
        change_kind: str = _MISSING,
        old_mode: str | None = _MISSING,
        new_mode: str | None = _MISSING,
        old_object_type: str | None = _MISSING,
        new_object_type: str | None = _MISSING,
        old_oid: str | None = _MISSING,
        new_oid: str | None = _MISSING,
        side: str = _MISSING,
        path: str = _MISSING,
        mode: str = _MISSING,
        object_type: str = _MISSING,
        object_oid: str = _MISSING,
    ) -> None:
        legacy = side is not _MISSING or any(
            value is not _MISSING
            for value in (path, mode, object_type, object_oid)
        )
        if args:
            if legacy:
                raise TypeError("CandidateDiffEntryV1 mixes old and new arguments")
            if len(args) == 9:
                (
                    old_path,
                    new_path,
                    change_kind,
                    old_mode,
                    new_mode,
                    old_object_type,
                    new_object_type,
                    old_oid,
                    new_oid,
                ) = args
            elif len(args) == 5:
                legacy = True
                side, path, mode, object_type, object_oid = args
            else:
                raise TypeError("CandidateDiffEntryV1 expects 9 or 5 positional values")
        if legacy:
            if any(
                value is not _MISSING
                for value in (
                    old_path,
                    new_path,
                    change_kind,
                    old_mode,
                    new_mode,
                    old_object_type,
                    new_object_type,
                    old_oid,
                    new_oid,
                )
            ):
                raise TypeError("CandidateDiffEntryV1 mixes old and new arguments")
            if side == "base":
                old_path, new_path, change_kind = path, None, "delete"
                old_mode, new_mode = mode, None
                old_object_type, new_object_type = object_type, None
                old_oid, new_oid = object_oid, None
            elif side == "candidate":
                old_path, new_path, change_kind = None, path, "add"
                old_mode, new_mode = None, mode
                old_object_type, new_object_type = None, object_type
                old_oid, new_oid = None, object_oid
            else:
                raise CandidateGateError(
                    "CANDIDATE_GATE_DIFF_INVALID",
                    "Candidate diff entry side is outside the closed union",
                )
            if object_type in {"tree", "commit", "submodule"}:
                if side == "base":
                    old_object_type = "gitlink"
                else:
                    new_object_type = "gitlink"
        for name, value in (
            ("old_path", old_path),
            ("new_path", new_path),
            ("change_kind", change_kind),
            ("old_mode", old_mode),
            ("new_mode", new_mode),
            ("old_object_type", old_object_type),
            ("new_object_type", new_object_type),
            ("old_oid", old_oid),
            ("new_oid", new_oid),
        ):
            object.__setattr__(self, name, None if value is _MISSING else value)
        object.__setattr__(self, "_legacy_mode", legacy)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.change_kind not in {"add", "delete", "modify", "type-change"}:
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff change kind is outside the closed union",
            )
        _validate_diff_side(
            path=self.old_path,
            mode=self.old_mode,
            object_type=self.old_object_type,
            oid=self.old_oid,
            side="old",
            legacy=self._legacy_mode,
        )
        _validate_diff_side(
            path=self.new_path,
            mode=self.new_mode,
            object_type=self.new_object_type,
            oid=self.new_oid,
            side="new",
            legacy=self._legacy_mode,
        )
        old_missing = self.old_path is None
        new_missing = self.new_path is None
        if (
            (self.change_kind == "add" and (not old_missing or new_missing))
            or (self.change_kind == "delete" and (old_missing or not new_missing))
            or (
                self.change_kind in {"modify", "type-change"}
                and (old_missing or new_missing)
            )
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff side presence does not match change kind",
            )
        if self.change_kind == "type-change" and (
            self.old_object_type == self.new_object_type
            and _git_file_type(self.old_mode) == _git_file_type(self.new_mode)
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "type-change requires different old and new object types",
            )

    def canonical(self) -> dict[str, str | None]:
        return {
            "old_path": self.old_path,
            "new_path": self.new_path,
            "change_kind": self.change_kind,
            "old_mode": self.old_mode,
            "new_mode": self.new_mode,
            "old_object_type": self.old_object_type,
            "new_object_type": self.new_object_type,
            "old_oid": self.old_oid,
            "new_oid": self.new_oid,
        }

    # The following read-only views retain the predecessor entry seam while
    # all new canonical payloads use the complete old/new identity above.
    @property
    def side(self) -> str:
        return "base" if self.new_path is None else "candidate"

    @property
    def path(self) -> str:
        value = self.old_path if self.old_path is not None else self.new_path
        assert value is not None
        return value

    @property
    def mode(self) -> str:
        value = self.old_mode if self.old_mode is not None else self.new_mode
        assert value is not None
        return value

    @property
    def object_type(self) -> str:
        value = (
            self.old_object_type
            if self.old_object_type is not None
            else self.new_object_type
        )
        assert value is not None
        return value

    @property
    def object_oid(self) -> str:
        value = self.old_oid if self.old_oid is not None else self.new_oid
        assert value is not None
        return value


@dataclass(frozen=True, slots=True, init=False)
class CandidateDiffRecordV1:
    """The one complete, digest-addressed raw-Git Candidate diff Artifact."""

    schema_version: str
    repository_object_format: str
    base_commit_oid: str
    base_tree_oid: str
    candidate_commit_oid: str
    candidate_tree_oid: str
    entries: tuple[CandidateDiffEntryV1, ...]
    record_digest: str | None = None
    _legacy_mode: bool = field(default=False, init=False, repr=False, compare=False)
    _legacy_repository: str | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __init__(
        self,
        *args: object,
        schema_version: str = _MISSING,
        repository_object_format: str = _MISSING,
        base_commit_oid: str = _MISSING,
        base_tree_oid: str = _MISSING,
        candidate_commit_oid: str = _MISSING,
        candidate_tree_oid: str = _MISSING,
        entries: tuple[CandidateDiffEntryV1, ...] = _MISSING,
        record_digest: str | None = None,
        repository: str = _MISSING,
        object_format: str = _MISSING,
    ) -> None:
        legacy = repository is not _MISSING or object_format is not _MISSING
        if args:
            if legacy:
                raise TypeError("CandidateDiffRecordV1 mixes old and new arguments")
            if len(args) == 7:
                (
                    schema_version,
                    repository_object_format,
                    base_commit_oid,
                    base_tree_oid,
                    candidate_commit_oid,
                    candidate_tree_oid,
                    entries,
                ) = args[:7]
                legacy = schema_version == "gwo.candidate-diff.v1"
                if legacy:
                    object_format = repository_object_format
            elif len(args) == 8 and args[0] in {
                "CandidateDiffRecordV1",
                "gwo.candidate-diff.v1",
            }:
                (
                    schema_version,
                    repository_object_format,
                    base_commit_oid,
                    base_tree_oid,
                    candidate_commit_oid,
                    candidate_tree_oid,
                    entries,
                    record_digest,
                ) = args
                legacy = schema_version == "gwo.candidate-diff.v1"
                if legacy:
                    object_format = repository_object_format
            elif len(args) in {8, 9}:
                # The predecessor positional order started with repository and
                # object_format and is retained for existing callers.
                legacy = True
                (
                    repository,
                    object_format,
                    base_commit_oid,
                    base_tree_oid,
                    candidate_commit_oid,
                    candidate_tree_oid,
                    entries,
                    record_digest,
                ) = args[:8]
                if len(args) == 9:
                    schema_version = args[8]
            else:
                raise TypeError("CandidateDiffRecordV1 has an unsupported positional shape")
        if legacy:
            if schema_version is not _MISSING and schema_version != "gwo.candidate-diff.v1":
                raise TypeError("legacy CandidateDiffRecordV1 schema is not supported")
            if object_format is _MISSING:
                raise CandidateGateError(
                    "CANDIDATE_GATE_DIFF_INVALID",
                    "legacy Candidate diff object format is missing",
                )
            schema_version = "gwo.candidate-diff.v1"
            repository_object_format = object_format
            if repository is not _MISSING:
                _require_text(repository, "Candidate diff repository")
        elif schema_version is _MISSING:
            schema_version = None
        if repository_object_format is _MISSING:
            repository_object_format = None
        if base_commit_oid is _MISSING:
            base_commit_oid = None
        if base_tree_oid is _MISSING:
            base_tree_oid = None
        if candidate_commit_oid is _MISSING:
            candidate_commit_oid = None
        if candidate_tree_oid is _MISSING:
            candidate_tree_oid = None
        if entries is _MISSING:
            entries = None
        for name, value in (
            ("schema_version", schema_version),
            ("repository_object_format", repository_object_format),
            ("base_commit_oid", base_commit_oid),
            ("base_tree_oid", base_tree_oid),
            ("candidate_commit_oid", candidate_commit_oid),
            ("candidate_tree_oid", candidate_tree_oid),
            ("entries", entries),
            ("record_digest", record_digest),
        ):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_legacy_mode", legacy)
        object.__setattr__(
            self,
            "_legacy_repository",
            None if repository is _MISSING else repository,
        )
        self.__post_init__()

    def __post_init__(self) -> None:
        if self._legacy_mode:
            expected_schema = "gwo.candidate-diff.v1"
        else:
            expected_schema = "CandidateDiffRecordV1"
        if self.schema_version != expected_schema:
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff schema version is invalid",
            )
        if self.repository_object_format not in {"sha1", "sha256"}:
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff repository object format is invalid",
            )
        for field_name in (
            "base_commit_oid",
            "base_tree_oid",
            "candidate_commit_oid",
            "candidate_tree_oid",
        ):
            _require_object_id_for_format(
                getattr(self, field_name),
                field_name,
                self.repository_object_format,
            )
        if type(self.entries) is not tuple or any(
            type(entry) is not CandidateDiffEntryV1 for entry in self.entries
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff entries are not an exact immutable tuple",
            )
        if self._legacy_mode:
            if any(not entry._legacy_mode for entry in self.entries):
                raise CandidateGateError(
                    "CANDIDATE_GATE_DIFF_INVALID",
                    "legacy Candidate diff entries are not explicit legacy values",
                )
        elif any(entry._legacy_mode for entry in self.entries):
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "exact CandidateDiffRecordV1 cannot contain legacy entries",
            )

        for entry in self.entries:
            for field_name in ("old_oid", "new_oid"):
                value = getattr(entry, field_name)
                if value is not None:
                    _require_object_id_for_format(
                        value,
                        f"entry {field_name}",
                        self.repository_object_format,
                    )

        def path_key(token: str | None, entry: CandidateDiffEntryV1) -> bytes:
            if token is None:
                return b""
            if self._legacy_mode:
                return token.encode("utf-8")
            return _decode_candidate_path_token(token, "entry path")

        ordered = tuple(
            sorted(
                self.entries,
                key=lambda entry: (
                    path_key(entry.old_path, entry),
                    path_key(entry.new_path, entry),
                    entry.change_kind,
                ),
            )
        )
        if self.entries != ordered or len(
            {canonical_bytes(entry.canonical()) for entry in self.entries}
        ) != len(self.entries):
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff entries are not canonical and unique",
            )
        expected = digest_bytes(
            b"gwo.candidate-diff-record.v1\x00" + canonical_bytes(self._body())
        )
        if self.record_digest is None:
            object.__setattr__(self, "record_digest", expected)
        elif self.record_digest != expected:
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff record digest changed",
            )

    @classmethod
    def from_tree_entries(
        cls,
        *,
        repository_object_format: str,
        base_commit_oid: str,
        base_tree_oid: str,
        candidate_commit_oid: str,
        candidate_tree_oid: str,
        base_entries: Mapping[bytes, tuple[str, str, str]],
        candidate_entries: Mapping[bytes, tuple[str, str, str]],
    ) -> "CandidateDiffRecordV1":
        """Build add/delete/modify/type-change entries without rename inference."""

        def encode_path(raw_path: bytes) -> str:
            return base64.urlsafe_b64encode(raw_path).decode("ascii").rstrip("=")

        entries: list[CandidateDiffEntryV1] = []
        for raw_path in sorted(set(base_entries) | set(candidate_entries)):
            old = base_entries.get(raw_path)
            new = candidate_entries.get(raw_path)
            if old is None:
                change_kind = "add"
            elif new is None:
                change_kind = "delete"
            elif old[1] != new[1] or _git_file_type(old[0]) != _git_file_type(new[0]):
                change_kind = "type-change"
            elif old != new:
                change_kind = "modify"
            else:
                continue
            entries.append(
                CandidateDiffEntryV1(
                    old_path=None if old is None else encode_path(raw_path),
                    new_path=None if new is None else encode_path(raw_path),
                    change_kind=change_kind,
                    old_mode=None if old is None else old[0],
                    new_mode=None if new is None else new[0],
                    old_object_type=None if old is None else old[1],
                    new_object_type=None if new is None else new[1],
                    old_oid=None if old is None else old[2],
                    new_oid=None if new is None else new[2],
                )
            )
        def entry_sort_key(entry: CandidateDiffEntryV1) -> tuple[bytes, bytes, str]:
            def path_key(token: str | None) -> bytes:
                if token is None:
                    return b""
                return _decode_candidate_path_token(token, "entry path")

            return (
                path_key(entry.old_path),
                path_key(entry.new_path),
                entry.change_kind,
            )

        entries.sort(key=entry_sort_key)
        return cls(
            schema_version="CandidateDiffRecordV1",
            repository_object_format=repository_object_format,
            base_commit_oid=base_commit_oid,
            base_tree_oid=base_tree_oid,
            candidate_commit_oid=candidate_commit_oid,
            candidate_tree_oid=candidate_tree_oid,
            entries=tuple(entries),
        )

    @property
    def changed_path_tokens(self) -> tuple[str, ...]:
        tokens: set[str] = set()
        for entry in self.entries:
            for token in (entry.old_path, entry.new_path):
                if token is not None:
                    tokens.add(token)
        return tuple(sorted(tokens))

    @property
    def digest(self) -> str:
        assert self.record_digest is not None
        return self.record_digest

    @property
    def changed_paths(self) -> tuple[str, ...]:
        """Read-only migration spelling for predecessor tests."""
        return self.changed_path_tokens

    @property
    def repository(self) -> str | None:
        return self._legacy_repository if self._legacy_mode else None

    @property
    def object_format(self) -> str:
        return self.repository_object_format

    def _body(self) -> dict[str, object]:
        if self._legacy_mode:
            return {
                "schema_version": self.schema_version,
                "kind": "candidate_diff_record.v1",
                "repository": self.repository,
                "object_format": self.repository_object_format,
                "base_commit_oid": self.base_commit_oid,
                "base_tree_oid": self.base_tree_oid,
                "candidate_commit_oid": self.candidate_commit_oid,
                "candidate_tree_oid": self.candidate_tree_oid,
                "entries": [entry.canonical() for entry in self.entries],
            }
        return {
            "schema_version": self.schema_version,
            "repository_object_format": self.repository_object_format,
            "base": {
                "commit_oid": self.base_commit_oid,
                "tree_oid": self.base_tree_oid,
            },
            "candidate": {
                "commit_oid": self.candidate_commit_oid,
                "tree_oid": self.candidate_tree_oid,
            },
            "entries": [entry.canonical() for entry in self.entries],
        }

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "record_digest": self.digest}


@dataclass(frozen=True)
class CandidateReadback:
    """Authoritative Candidate reference and its complete diff Artifact."""

    repository: str
    candidate: CandidateIdentity
    diff_record: CandidateDiffRecordV1
    readback_digest: str | None = None
    _legacy_compatibility: bool = field(
        default=False,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _require_text(self.repository, "Candidate readback repository")
        if type(self.candidate) is not CandidateIdentity or type(
            self.diff_record
        ) is not CandidateDiffRecordV1:
            raise CandidateGateError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "Candidate readback has an invalid typed identity",
            )
        object.__setattr__(
            self,
            "_legacy_compatibility",
            self.diff_record._legacy_mode,
        )
        if (
            self.diff_record.base_commit_oid != self.candidate.base_commit_oid
            or self.diff_record.base_tree_oid != self.candidate.base_tree_oid
            or self.diff_record.candidate_commit_oid != self.candidate.candidate_commit_oid
            or self.diff_record.candidate_tree_oid != self.candidate.candidate_tree_oid
            or self.diff_record.changed_path_tokens
            != self.candidate.changed_path_tokens
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


@dataclass(frozen=True, slots=True)
class CandidateReceipt:
    """The private authoritative Candidate receipt persisted by ExecutionKernel."""

    parent_digest: str
    repository: str
    campaign_key: str
    campaign_handle: str
    plan_revision_digest: str
    work_run_key: str
    ticket_key: str
    reported_reference: str
    base_commit_oid: str
    base_tree_oid: str
    candidate_commit_oid: str
    candidate_tree_oid: str
    diff_schema_version: str
    diff_record_digest: str
    authority_subtree_digest: str
    runtime_subject_digest: str
    receipt_digest: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "parent_digest",
            "plan_revision_digest",
            "authority_subtree_digest",
            "runtime_subject_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        for field_name in (
            "repository",
            "campaign_key",
            "campaign_handle",
            "work_run_key",
            "ticket_key",
            "reported_reference",
            "diff_schema_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "base_commit_oid",
            "base_tree_oid",
            "candidate_commit_oid",
            "candidate_tree_oid",
        ):
            _require_object_id(getattr(self, field_name), field_name)
        _require_digest(self.diff_record_digest, "diff_record_digest")
        if self.diff_schema_version != "CandidateDiffRecordV1":
            raise CandidateGateError(
                "CANDIDATE_RECEIPT_INVALID",
                "CandidateReceipt diff schema is not CandidateDiffRecordV1",
            )
        expected = digest_value(self._body())
        if self.receipt_digest is None:
            object.__setattr__(self, "receipt_digest", expected)
        else:
            _validate_stored_digest(
                self.receipt_digest,
                self._body(),
                code="CANDIDATE_RECEIPT_INVALID",
                detail="CandidateReceipt digest changed",
            )

    @classmethod
    def from_readback(
        cls,
        *,
        parent: CandidateGateParent,
        reported_reference: str,
        readback: CandidateReadback,
    ) -> "CandidateReceipt":
        subject = parent.runtime_subject
        candidate = readback.candidate
        return cls(
            parent_digest=parent.digest,
            repository=readback.repository,
            campaign_key=subject.campaign_key,
            campaign_handle=subject.campaign_handle,
            plan_revision_digest=subject.plan_revision_digest,
            work_run_key=subject.work_run_key,
            ticket_key=subject.ticket_key,
            reported_reference=reported_reference,
            base_commit_oid=candidate.base_commit_oid,
            base_tree_oid=candidate.base_tree_oid,
            candidate_commit_oid=candidate.candidate_commit_oid,
            candidate_tree_oid=candidate.candidate_tree_oid,
            diff_schema_version=readback.diff_record.schema_version,
            diff_record_digest=readback.diff_record.digest,
            authority_subtree_digest=subject.authority_subtree_digest,
            runtime_subject_digest=subject.digest,
        )

    @classmethod
    def from_canonical(cls, value: Mapping[str, Any]) -> "CandidateReceipt":
        expected_keys = frozenset(
            {
                "kind",
                "parent_digest",
                "repository",
                "campaign_key",
                "campaign_handle",
                "plan_revision_digest",
                "work_run_key",
                "ticket_key",
                "reported_reference",
                "base_commit_oid",
                "base_tree_oid",
                "candidate_commit_oid",
                "candidate_tree_oid",
                "diff_schema_version",
                "diff_record_digest",
                "authority_subtree_digest",
                "runtime_subject_digest",
                "receipt_digest",
            }
        )
        if not isinstance(value, Mapping) or frozenset(value) != expected_keys:
            raise CandidateGateError(
                "CANDIDATE_RECEIPT_INVALID",
                "CandidateReceipt canonical keys are not exact",
            )
        if value["kind"] != "candidate_receipt.v1":
            raise CandidateGateError(
                "CANDIDATE_RECEIPT_INVALID",
                "CandidateReceipt kind is invalid",
            )
        try:
            return cls(
                parent_digest=value["parent_digest"],
                repository=value["repository"],
                campaign_key=value["campaign_key"],
                campaign_handle=value["campaign_handle"],
                plan_revision_digest=value["plan_revision_digest"],
                work_run_key=value["work_run_key"],
                ticket_key=value["ticket_key"],
                reported_reference=value["reported_reference"],
                base_commit_oid=value["base_commit_oid"],
                base_tree_oid=value["base_tree_oid"],
                candidate_commit_oid=value["candidate_commit_oid"],
                candidate_tree_oid=value["candidate_tree_oid"],
                diff_schema_version=value["diff_schema_version"],
                diff_record_digest=value["diff_record_digest"],
                authority_subtree_digest=value["authority_subtree_digest"],
                runtime_subject_digest=value["runtime_subject_digest"],
                receipt_digest=value["receipt_digest"],
            )
        except CandidateGateError as error:
            raise CandidateGateError("CANDIDATE_RECEIPT_INVALID", error.detail) from error

    def _body(self) -> dict[str, object]:
        return {
            "kind": "candidate_receipt.v1",
            "parent_digest": self.parent_digest,
            "repository": self.repository,
            "campaign_key": self.campaign_key,
            "campaign_handle": self.campaign_handle,
            "plan_revision_digest": self.plan_revision_digest,
            "work_run_key": self.work_run_key,
            "ticket_key": self.ticket_key,
            "reported_reference": self.reported_reference,
            "base_commit_oid": self.base_commit_oid,
            "base_tree_oid": self.base_tree_oid,
            "candidate_commit_oid": self.candidate_commit_oid,
            "candidate_tree_oid": self.candidate_tree_oid,
            "diff_schema_version": self.diff_schema_version,
            "diff_record_digest": self.diff_record_digest,
            "authority_subtree_digest": self.authority_subtree_digest,
            "runtime_subject_digest": self.runtime_subject_digest,
        }

    @property
    def digest(self) -> str:
        assert self.receipt_digest is not None
        return self.receipt_digest

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "receipt_digest": self.digest}


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


@dataclass(frozen=True, slots=True)
class CandidateCheckEvidence:
    check_id: str
    candidate_tree_oid: str
    outcome: str
    definition_digest: str
    observation_digest: str
    failure: DeterministicAuditFailure | None = None

    def __post_init__(self) -> None:
        _require_text(self.check_id, "check_id")
        _require_object_id(self.candidate_tree_oid, "candidate_tree_oid")
        if self.outcome not in {"passed", "failed"}:
            raise CandidateGateError(
                "CANDIDATE_GATE_CHECK_INVALID",
                "Candidate check outcome is outside the closed union",
            )
        _require_digest(self.definition_digest, "definition_digest")
        _require_digest(self.observation_digest, "observation_digest")
        if (self.outcome == "failed") != (self.failure is not None):
            raise CandidateGateError(
                "CANDIDATE_GATE_CHECK_INVALID",
                "failed Candidate check must carry one deterministic failure",
            )
        if self.failure is not None and type(
            self.failure
        ) is not DeterministicAuditFailure:
            raise CandidateGateError(
                "CANDIDATE_GATE_CHECK_INVALID",
                "Candidate check failure is not exact typed Evidence",
            )

    def canonical(self) -> dict[str, str | None]:
        return {
            "check_id": self.check_id,
            "candidate_tree_oid": self.candidate_tree_oid,
            "outcome": self.outcome,
            "definition_digest": self.definition_digest,
            "observation_digest": self.observation_digest,
            "failure_digest": None if self.failure is None else self.failure.digest,
        }

    @property
    def digest(self) -> str:
        return digest_value(self.canonical())


@dataclass(frozen=True, slots=True)
class AssuranceRequirement:
    policy_id: str
    policy_version: str
    mode: AssuranceMode
    required_check_ids: tuple[str, ...]
    standards: tuple[str, ...]
    specialist_policy_id: str | None = None
    requirement_digest: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.policy_id, "policy_id")
        _require_text(self.policy_version, "policy_version")
        if type(self.mode) is not AssuranceMode:
            raise CandidateGateError(
                "CANDIDATE_GATE_ASSURANCE_INVALID",
                "AssuranceRequirement mode is not an exact AssuranceMode",
            )
        try:
            required_check_ids = _require_text_tuple(
                self.required_check_ids,
                "required_check_ids",
                allow_empty=False,
            )
        except CandidateGateError as error:
            raise CandidateGateError(
                "CANDIDATE_GATE_ASSURANCE_INVALID",
                error.detail,
            ) from error
        if required_check_ids != tuple(sorted(set(required_check_ids))):
            raise CandidateGateError(
                "CANDIDATE_GATE_ASSURANCE_INVALID",
                "required_check_ids must be sorted and unique",
            )
        _require_text_tuple(self.standards, "standards")
        if self.specialist_policy_id is not None:
            _require_text(self.specialist_policy_id, "specialist_policy_id")
        expected = digest_value(self._body())
        if self.requirement_digest is None:
            object.__setattr__(self, "requirement_digest", expected)
        else:
            _validate_stored_digest(
                self.requirement_digest,
                self._body(),
                code="CANDIDATE_GATE_ASSURANCE_INVALID",
                detail="AssuranceRequirement digest changed",
            )

    def _body(self) -> dict[str, object]:
        return {
            "kind": "assurance_requirement.v1",
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "mode": self.mode.value,
            "required_check_ids": list(self.required_check_ids),
            "standards": list(self.standards),
            "specialist_policy_id": self.specialist_policy_id,
        }

    @property
    def digest(self) -> str:
        return digest_value(self._body())

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "requirement_digest": self.digest}


def _candidate_check_observation_digest(
    *,
    check_id: str,
    candidate_tree_oid: str,
    diff_record_digest: str,
    outcome: str,
    failure_digest: str | None,
) -> str:
    return digest_value(
        {
            "kind": "candidate_check_observation.v1",
            "check_id": check_id,
            "candidate_tree_oid": candidate_tree_oid,
            "diff_record_digest": diff_record_digest,
            "outcome": outcome,
            "failure_digest": failure_digest,
        }
    )


class CandidateCheckRunner(Protocol):
    def run(
        self,
        parent: CandidateGateParent,
        readback: CandidateReadback,
    ) -> tuple[CandidateCheckEvidence, ...]:
        pass


class AssurancePolicy(Protocol):
    def derive(
        self,
        parent: CandidateGateParent,
        readback: CandidateReadback,
        checks: tuple[CandidateCheckEvidence, ...],
    ) -> AssuranceRequirement:
        pass


class InteractionClassification(str, Enum):
    ORDINARY = "ordinary"
    PROTECTED = "protected"
    HIGH_COUPLING = "high_coupling"
    NON_DECOMPOSABLE = "non_decomposable"


@dataclass(frozen=True, slots=True)
class InteractionKey:
    namespace: str
    value: str
    classification: InteractionClassification

    def __post_init__(self) -> None:
        _require_text(self.namespace, "interaction namespace")
        _require_text(self.value, "interaction value")
        if type(self.classification) is not InteractionClassification:
            raise CandidateGateError(
                "CANDIDATE_GATE_INTERACTION_INVALID",
                "interaction classification is outside the closed union",
            )

    @property
    def requires_singleton(self) -> bool:
        return self.classification is not InteractionClassification.ORDINARY

    def canonical(self) -> dict[str, str]:
        return {
            "namespace": self.namespace,
            "value": self.value,
            "classification": self.classification.value,
        }


def derive_interaction_keys(
    record: CandidateDiffRecordV1,
    *,
    protected_surfaces: tuple[str, ...],
) -> tuple[InteractionKey, ...]:
    protected = set(protected_surfaces)
    gitlink_paths = {
        token
        for entry in record.entries
        if entry.old_object_type == "gitlink" or entry.new_object_type == "gitlink"
        for token in (entry.old_path, entry.new_path)
        if token is not None
    }
    keys: list[InteractionKey] = []
    for token in record.changed_path_tokens:
        classification = (
            InteractionClassification.PROTECTED
            if token in protected
            else (
                InteractionClassification.HIGH_COUPLING
                if token in gitlink_paths
                else InteractionClassification.ORDINARY
            )
        )
        keys.append(InteractionKey("candidate-path", token, classification))
    return tuple(
        sorted(
            set(keys),
            key=lambda key: (key.namespace, key.value, key.classification.value),
        )
    )


def record_has_gitlink_change(record: CandidateDiffRecordV1) -> bool:
    return any(
        entry.old_object_type == "gitlink" or entry.new_object_type == "gitlink"
        for entry in record.entries
    )


class DigestEvidence(Protocol):
    @property
    def digest(self) -> str:
        pass


@dataclass(frozen=True, slots=True)
class CandidateAcceptanceFacts:
    target_branch: str
    integration_node_key: str
    accepted_sequence: int
    check_environment_digest: str
    delivery_identity_digest: str
    protected_surfaces: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.target_branch, "target_branch")
        _require_text(self.integration_node_key, "integration_node_key")
        if type(self.accepted_sequence) is not int or self.accepted_sequence < 1:
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "accepted_sequence must be a positive integer",
            )
        _require_digest(self.check_environment_digest, "check_environment_digest")
        _require_digest(self.delivery_identity_digest, "delivery_identity_digest")
        _require_text_tuple(self.protected_surfaces, "protected_surfaces")
        if self.protected_surfaces != tuple(sorted(set(self.protected_surfaces))):
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "protected_surfaces must be sorted and unique",
            )


@dataclass(frozen=True, slots=True)
class AcceptedCandidateReceipt:
    repository: str
    campaign_key: str
    plan_revision_digest: str
    target_branch: str
    ticket_key: str
    work_run_key: str
    integration_node_key: str
    accepted_sequence: int
    base_sha: str
    base_tree_oid: str
    candidate_sha: str
    candidate_tree_oid: str
    candidate_receipt_digest: str
    diff_record_digest: str
    authority_subtree_digest: str
    policy_witness_digest: str
    review_subject_digest: str
    assurance: str
    assurance_requirement_digest: str
    check_environment_digest: str
    delivery_identity_digest: str
    interaction_keys: tuple[InteractionKey, ...]
    protected_surfaces: tuple[str, ...]
    gitlink_change: bool
    evidence_digests: tuple[str, ...]
    review_finding_ledger_digest: str
    diff_schema_version: str = "CandidateDiffRecordV1"

    def __post_init__(self) -> None:
        for field_name in (
            "repository",
            "campaign_key",
            "target_branch",
            "ticket_key",
            "work_run_key",
            "integration_node_key",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "plan_revision_digest",
            "candidate_receipt_digest",
            "diff_record_digest",
            "authority_subtree_digest",
            "policy_witness_digest",
            "review_subject_digest",
            "assurance_requirement_digest",
            "check_environment_digest",
            "delivery_identity_digest",
            "review_finding_ledger_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        for field_name in ("base_sha", "base_tree_oid", "candidate_sha", "candidate_tree_oid"):
            _require_object_id(getattr(self, field_name), field_name)
        if self.diff_schema_version != "CandidateDiffRecordV1":
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "AcceptedCandidateReceipt diff schema is invalid",
            )
        if self.assurance not in {"no_review", "standard", "strict"}:
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "assurance must be standard or strict",
            )
        if type(self.accepted_sequence) is not int or self.accepted_sequence < 1:
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "accepted_sequence must be a positive integer",
            )
        if type(self.interaction_keys) is not tuple or any(
            type(value) is not InteractionKey for value in self.interaction_keys
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "interaction_keys are not canonical InteractionKey values",
            )
        _require_text_tuple(self.protected_surfaces, "protected_surfaces")
        if self.protected_surfaces != tuple(sorted(set(self.protected_surfaces))):
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "protected_surfaces are not sorted and unique",
            )
        if type(self.gitlink_change) is not bool:
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "gitlink_change is not an exact boolean",
            )
        _require_digest_tuple(self.evidence_digests, "evidence_digests")

    def _body(self) -> dict[str, object]:
        return {
            "kind": "accepted_candidate_receipt.v1",
            "repository": self.repository,
            "campaign_key": self.campaign_key,
            "plan_revision_digest": self.plan_revision_digest,
            "target_branch": self.target_branch,
            "ticket_key": self.ticket_key,
            "work_run_key": self.work_run_key,
            "integration_node_key": self.integration_node_key,
            "accepted_sequence": self.accepted_sequence,
            "base_sha": self.base_sha,
            "base_tree_oid": self.base_tree_oid,
            "candidate_sha": self.candidate_sha,
            "candidate_tree_oid": self.candidate_tree_oid,
            "candidate_receipt_digest": self.candidate_receipt_digest,
            "diff_schema_version": self.diff_schema_version,
            "diff_record_digest": self.diff_record_digest,
            "authority_subtree_digest": self.authority_subtree_digest,
            "policy_witness_digest": self.policy_witness_digest,
            "review_subject_digest": self.review_subject_digest,
            "assurance": self.assurance,
            "assurance_requirement_digest": self.assurance_requirement_digest,
            "check_environment_digest": self.check_environment_digest,
            "delivery_identity_digest": self.delivery_identity_digest,
            "interaction_keys": [key.canonical() for key in self.interaction_keys],
            "protected_surfaces": list(self.protected_surfaces),
            "gitlink_change": self.gitlink_change,
            "evidence_digests": list(self.evidence_digests),
            "review_finding_ledger_digest": self.review_finding_ledger_digest,
        }

    @property
    def digest(self) -> str:
        return digest_value(self._body())

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "receipt_digest": self.digest}


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
    _legacy_compatibility: bool = field(
        default=False,
        init=False,
        repr=False,
        compare=False,
    )

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
            object.__setattr__(
                self,
                "_legacy_compatibility",
                self.diff_record._legacy_mode,
            )
            if (
                self.diff_record.changed_path_tokens
                != self.candidate.changed_path_tokens
            ):
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


@dataclass(frozen=True, slots=True)
class ReviewSubject:
    parent_digest: str
    candidate_receipt_digest: str
    runtime_subject_digest: str
    candidate_digest: str
    candidate_audit_digest: str
    ticket_contract_digest: str
    policy_witness_digest: str
    base_commit_oid: str
    base_tree_oid: str
    candidate_commit_oid: str
    candidate_tree_oid: str
    diff_schema_version: str
    diff_record_digest: str
    standards: tuple[str, ...]
    check_evidence_digests: tuple[str, ...]
    assurance_requirement_digest: str
    protocol_version: str = "gwo.formal-review.v1"
    action_kind: str = "formal_review"
    prior_review_subject_digest: str | None = None
    repair_packet_digest: str | None = None
    repair_delta_digest: str | None = None
    subject_digest: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "parent_digest",
            "candidate_receipt_digest",
            "runtime_subject_digest",
            "candidate_digest",
            "candidate_audit_digest",
            "ticket_contract_digest",
            "policy_witness_digest",
            "diff_record_digest",
            "assurance_requirement_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        for field_name in (
            "base_commit_oid",
            "base_tree_oid",
            "candidate_commit_oid",
            "candidate_tree_oid",
        ):
            _require_object_id(getattr(self, field_name), field_name)
        if self.diff_schema_version != "CandidateDiffRecordV1":
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                "ReviewSubject diff schema is invalid",
            )
        _require_text_tuple(self.standards, "standards")
        _require_digest_tuple(
            self.check_evidence_digests,
            "check_evidence_digests",
            allow_empty=True,
        )
        _require_text(self.protocol_version, "protocol_version")
        if self.action_kind not in {"formal_review", "repair_verify"}:
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                "ReviewSubject action kind is outside the closed union",
            )
        repair_digests = (
            self.prior_review_subject_digest,
            self.repair_packet_digest,
            self.repair_delta_digest,
        )
        if self.action_kind == "repair_verify":
            if any(value is None for value in repair_digests):
                raise CandidateGateError(
                    "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                    "repair_verify subject lacks prior Subject, packet, or delta",
                )
            for value in repair_digests:
                _require_digest(value, "repair ReviewSubject digest")
        elif any(value is not None for value in repair_digests):
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                "initial ReviewSubject carries repair-only identity",
            )
        expected = digest_value(self._body())
        if self.subject_digest is None:
            object.__setattr__(self, "subject_digest", expected)
        else:
            _validate_stored_digest(
                self.subject_digest,
                self._body(),
                code="CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                detail="ReviewSubject digest changed",
            )

    @classmethod
    def from_assurance(
        cls,
        *,
        parent: CandidateGateParent,
        candidate_receipt: CandidateReceipt,
        readback: CandidateReadback,
        audit: CandidateAuditReport,
        checks: tuple[CandidateCheckEvidence, ...],
        requirement: AssuranceRequirement,
    ) -> "ReviewSubject":
        return cls(
            parent_digest=parent.digest,
            candidate_receipt_digest=candidate_receipt.digest,
            runtime_subject_digest=parent.runtime_subject.digest,
            candidate_digest=readback.candidate.digest,
            candidate_audit_digest=audit.evidence.digest,
            ticket_contract_digest=parent.ticket_contract_digest,
            policy_witness_digest=parent.policy_witness_digest,
            base_commit_oid=readback.candidate.base_commit_oid,
            base_tree_oid=readback.candidate.base_tree_oid,
            candidate_commit_oid=readback.candidate.candidate_commit_oid,
            candidate_tree_oid=readback.candidate.candidate_tree_oid,
            diff_schema_version=readback.diff_record.schema_version,
            diff_record_digest=readback.diff_record.digest,
            standards=requirement.standards,
            check_evidence_digests=tuple(sorted(check.digest for check in checks)),
            assurance_requirement_digest=requirement.digest,
        )

    @classmethod
    def from_parent(
        cls,
        parent: CandidateGateParent,
        audit: CandidateAuditReport,
    ) -> "ReviewSubject":
        """Compatibility constructor for the already-read predecessor seam."""

        diff_record_digest = (
            audit.candidate.digest
            if audit.diff_record is None
            else audit.diff_record.digest
        )
        requirement_digest = digest_value(
            {
                "kind": "assurance_requirement.legacy.v1",
                "value": audit.assurance_requirement,
            }
        )
        return cls(
            parent_digest=parent.digest,
            candidate_receipt_digest=audit.evidence.digest,
            runtime_subject_digest=parent.runtime_subject.digest,
            candidate_digest=audit.candidate.digest,
            candidate_audit_digest=audit.evidence.digest,
            ticket_contract_digest=parent.ticket_contract_digest,
            policy_witness_digest=parent.policy_witness_digest,
            base_commit_oid=audit.candidate.base_commit_oid,
            base_tree_oid=audit.candidate.base_tree_oid,
            candidate_commit_oid=audit.candidate.candidate_commit_oid,
            candidate_tree_oid=audit.candidate.candidate_tree_oid,
            diff_schema_version="CandidateDiffRecordV1",
            diff_record_digest=diff_record_digest,
            standards=audit.standards,
            check_evidence_digests=audit.check_evidence_digests,
            assurance_requirement_digest=requirement_digest,
        )

    def _body(self) -> dict[str, object]:
        return {
            "kind": "review_subject.v1",
            "parent_digest": self.parent_digest,
            "candidate_receipt_digest": self.candidate_receipt_digest,
            "runtime_subject_digest": self.runtime_subject_digest,
            "candidate_digest": self.candidate_digest,
            "candidate_audit_digest": self.candidate_audit_digest,
            "ticket_contract_digest": self.ticket_contract_digest,
            "policy_witness_digest": self.policy_witness_digest,
            "base_commit_oid": self.base_commit_oid,
            "base_tree_oid": self.base_tree_oid,
            "candidate_commit_oid": self.candidate_commit_oid,
            "candidate_tree_oid": self.candidate_tree_oid,
            "diff_schema_version": self.diff_schema_version,
            "diff_record_digest": self.diff_record_digest,
            "standards": list(self.standards),
            "check_evidence_digests": list(self.check_evidence_digests),
            "assurance_requirement_digest": self.assurance_requirement_digest,
            "protocol_version": self.protocol_version,
            "action_kind": self.action_kind,
            "prior_review_subject_digest": self.prior_review_subject_digest,
            "repair_packet_digest": self.repair_packet_digest,
            "repair_delta_digest": self.repair_delta_digest,
        }

    @property
    def digest(self) -> str:
        assert self.subject_digest is not None
        return self.subject_digest

    @property
    def diff_digest(self) -> str:
        """Read-only predecessor spelling for the complete diff digest."""

        return self.diff_record_digest

    @property
    def assurance_requirement(self) -> str:
        """Read-only predecessor spelling for the requirement identity."""

        return self.assurance_requirement_digest

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "subject_digest": self.digest}


FormalReviewRequest = ReviewSubject


@dataclass(frozen=True, slots=True)
class ReviewAction:
    kind: str
    subject: ReviewSubject
    runtime_subject_digest: str
    stable_action_id: str
    specialist_policy_id: str | None = None

    @classmethod
    def for_subject(
        cls,
        *,
        kind: str,
        subject: ReviewSubject,
        specialist_policy_id: str | None = None,
    ) -> "ReviewAction":
        if kind not in {"formal_review", "review_strong", "specialist_review"}:
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                "ReviewAction kind is outside the closed Review union",
            )
        if subject.action_kind != "formal_review":
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                "ReviewAction requires an initial formal-review Subject",
            )
        if (kind == "specialist_review") != (specialist_policy_id is not None):
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                "specialist policy identity does not match ReviewAction kind",
            )
        if specialist_policy_id is not None:
            _require_text(specialist_policy_id, "specialist_policy_id")
        return cls(
            kind=kind,
            subject=subject,
            runtime_subject_digest=subject.runtime_subject_digest,
            stable_action_id="review:" + digest_value(
                {
                    "kind": kind,
                    "subject_digest": subject.digest,
                    "specialist_policy_id": specialist_policy_id,
                }
            ),
            specialist_policy_id=specialist_policy_id,
        )

    @property
    def purpose(self) -> WorkRunPurpose:
        if self.kind == "formal_review":
            return WorkRunPurpose.formal_review()
        if self.kind == "review_strong":
            return WorkRunPurpose.invalid_review_payload_retry()
        assert self.specialist_policy_id is not None
        return WorkRunPurpose.specialist_review(self.specialist_policy_id)


class FormalReviewer(Protocol):
    def review(self, action: ReviewAction) -> FormalReviewResult:
        pass


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
            allowed_paths=tuple(sorted(candidate.changed_path_tokens)),
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
    candidate_receipt: CandidateReceipt | None = None
    candidate_diff_record: CandidateDiffRecordV1 | None = None
    assurance_requirement: AssuranceRequirement | None = None
    review_subject: ReviewSubject | None = None
    accepted_candidate_receipt: AcceptedCandidateReceipt | None = None
    review_finding_ledger_digest: str | None = None

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
        typed_optional = (
            (self.candidate_receipt, CandidateReceipt),
            (self.candidate_diff_record, CandidateDiffRecordV1),
            (self.assurance_requirement, AssuranceRequirement),
            (self.review_subject, ReviewSubject),
            (self.accepted_candidate_receipt, AcceptedCandidateReceipt),
        )
        if any(
            value is not None and type(value) is not expected
            for value, expected in typed_optional
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "CandidateGate result contains a non-exact typed value",
            )
        if self.review_finding_ledger_digest is not None:
            _require_digest(
                self.review_finding_ledger_digest,
                "review_finding_ledger_digest",
            )
        has_receipt = self.plan_invalidation_receipt is not None
        has_report = self.plan_invalidation_report is not None
        if has_receipt != has_report:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Plan Invalidation receipt and report must be both present or absent",
            )
        if has_receipt and (
            self.plan_invalidation_receipt.report_digest
            != self.plan_invalidation_report.digest
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Plan Invalidation receipt is not bound to its report",
            )
        has_invalidation = self.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
        if has_invalidation != (has_receipt and has_report):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "Plan Invalidation status and readback pair do not match",
            )
        if self.status in {
            CandidateGateStatus.ORDINARY_REJECTED,
            CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
        } and (
            self.review_subject is not None
            or self.accepted_candidate_receipt is not None
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "deterministic stop carries Review-only identity",
            )
        if self.accepted_candidate_receipt is not None:
            if (
                self.status
                not in {
                    CandidateGateStatus.REVIEW_ACCEPTED,
                    CandidateGateStatus.REPAIR_ACCEPTED,
                }
                or self.candidate_receipt is None
                or self.candidate_diff_record is None
                or self.assurance_requirement is None
                or self.review_subject is None
                or self.accepted_candidate_receipt.candidate_receipt_digest
                != self.candidate_receipt.digest
                or self.accepted_candidate_receipt.diff_record_digest
                != self.candidate_diff_record.digest
                or self.accepted_candidate_receipt.review_subject_digest
                != self.review_subject.digest
                or self.accepted_candidate_receipt.assurance_requirement_digest
                != self.assurance_requirement.digest
            ):
                raise CandidateGateError(
                    "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                    "accepted Candidate receipt is not bound to complete #114 identity",
                )
        if (
            self.status
            in {
                CandidateGateStatus.REVIEW_ACCEPTED,
                CandidateGateStatus.REPAIR_ACCEPTED,
            }
            and self.candidate_receipt is not None
            and self.accepted_candidate_receipt is None
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "public accepted Candidate lacks the delivery receipt",
            )

    @property
    def formal_review_request(self) -> ReviewSubject | None:
        """Read-only compatibility alias for predecessor callers."""

        return self.review_subject

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


class CandidateDiffArtifactStore(Protocol):
    def put(self, record: CandidateDiffRecordV1) -> str:
        pass

    def read(self, digest: str) -> CandidateDiffRecordV1:
        pass


class FormalReviewer(Protocol):
    """A read-only Formal Review action over one immutable request."""

    def review(self, action: ReviewAction) -> FormalReviewResult: ...


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
        check_runner: CandidateCheckRunner | None = None,
        assurance_policy: AssurancePolicy | None = None,
        acceptance_facts: CandidateAcceptanceFacts | None = None,
        diff_artifacts: CandidateDiffArtifactStore | None = None,
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
        if check_runner is not None and not callable(
            getattr(check_runner, "run", None)
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_ADAPTER_INVALID",
                "Candidate check runner does not expose run",
            )
        if assurance_policy is not None and not callable(
            getattr(assurance_policy, "derive", None)
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_ADAPTER_INVALID",
                "Assurance policy does not expose derive",
            )
        if acceptance_facts is not None and type(
            acceptance_facts
        ) is not CandidateAcceptanceFacts:
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "Candidate acceptance facts are not exact",
            )
        if diff_artifacts is not None and (
            not callable(getattr(diff_artifacts, "put", None))
            or not callable(getattr(diff_artifacts, "read", None))
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_ADAPTER_INVALID",
                "Candidate diff Artifact Store lacks put/read",
            )
        self._invalidation_reporter = invalidation_reporter
        self._candidate_reader = candidate_reader
        self._formal_reviewer = formal_reviewer
        self._repair_verifier = repair_verifier
        self._check_runner = check_runner
        self._assurance_policy = assurance_policy
        self._acceptance_facts = acceptance_facts
        self._diff_artifacts = diff_artifacts
        self._review_results: dict[str, FormalReviewResult] = {}

    def _store_candidate_diff(
        self,
        record: CandidateDiffRecordV1,
    ) -> CandidateDiffRecordV1:
        store = self._diff_artifacts
        if store is None:
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_ARTIFACT_INVALID",
                "Candidate diff Artifact Store is not configured",
            )
        stored_digest = store.put(record)
        if stored_digest != record.digest:
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_ARTIFACT_INVALID",
                "Candidate diff Artifact Store changed the record digest",
            )
        persisted = store.read(stored_digest)
        if type(persisted) is not CandidateDiffRecordV1 or persisted != record:
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_ARTIFACT_INVALID",
                "Candidate diff Artifact readback changed the complete record",
            )
        return persisted

    def gate_candidate(
        self,
        parent: CandidateGateParent,
        reported_reference: str,
    ) -> CandidateGateResult:
        reader = self._candidate_reader
        check_runner = self._check_runner
        assurance_policy = self._assurance_policy
        if reader is None or check_runner is None or assurance_policy is None:
            raise CandidateGateError(
                "CANDIDATE_GATE_ADAPTER_INVALID",
                "gate_candidate requires reader, checks, and Assurance policy",
            )
        readback = reader.read_candidate(
            parent.runtime_subject.repository,
            reported_reference,
        )
        if type(readback) is not CandidateReadback:
            raise CandidateGateError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "authoritative Candidate readback is not the exact typed value",
            )
        if (
            readback.repository != parent.runtime_subject.repository
            or readback.candidate.reported_reference != reported_reference
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_STALE",
                "authoritative Candidate readback changed repository or reference",
            )
        if self._diff_artifacts is not None:
            stored_record = self._store_candidate_diff(readback.diff_record)
            readback = replace(
                readback,
                diff_record=stored_record,
                readback_digest=None,
            )
        receipt = CandidateReceipt.from_readback(
            parent=parent,
            reported_reference=reported_reference,
            readback=readback,
        )
        checks = check_runner.run(parent, readback)
        if type(checks) is not tuple or any(
            type(check) is not CandidateCheckEvidence for check in checks
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_CHECK_INVALID",
                "Candidate check runner returned non-canonical Evidence",
            )
        requirement = assurance_policy.derive(parent, readback, checks)
        if type(requirement) is not AssuranceRequirement:
            raise CandidateGateError(
                "CANDIDATE_GATE_ASSURANCE_INVALID",
                "Assurance policy returned a non-exact requirement",
            )
        audit = self._audit_readback(parent, readback, checks, requirement)
        result = self._audit_without_second_readback(
            parent,
            audit,
            receipt=receipt,
            readback=readback,
            checks=checks,
            requirement=requirement,
        )
        if result.status is not CandidateGateStatus.REVIEW_ACCEPTED:
            return replace(
                result,
                candidate_receipt=receipt,
                candidate_diff_record=readback.diff_record,
                assurance_requirement=requirement,
            )
        if result.review_subject is None or self._acceptance_facts is None:
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "accepted Candidate lacks Review Subject or delivery identity facts",
            )
        accepted = self._make_accepted_candidate_receipt(
            parent=parent,
            candidate_receipt=receipt,
            candidate_diff_record=readback.diff_record,
            review_subject=result.review_subject,
            assurance_requirement=requirement,
            evidence=result.evidence,
            review_finding_ledger_digest=result.review_finding_ledger_digest,
        )
        return replace(
            result,
            candidate_receipt=receipt,
            candidate_diff_record=readback.diff_record,
            assurance_requirement=requirement,
            accepted_candidate_receipt=accepted,
        )

    def reuse_formal_review(
        self,
        *,
        subject: ReviewSubject,
        result: CandidateGateResult,
    ) -> CandidateGateResult:
        store = self._diff_artifacts
        if store is None:
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_ARTIFACT_INVALID",
                "Candidate diff Artifact Store is not configured",
            )
        record = store.read(subject.diff_record_digest)
        if type(record) is not CandidateDiffRecordV1 or record.digest != subject.diff_record_digest:
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_ARTIFACT_INVALID",
                "stored Candidate diff is missing or digest-invalid",
            )
        if result.review_subject is None or result.review_subject.digest != subject.digest:
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEW_REUSE_INVALID",
                "Review reuse Subject identity changed",
            )
        if result.candidate_diff_record != record:
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEW_REUSE_INVALID",
                "Review result is not bound to the stored complete diff",
            )
        self._validate_read_only_port(self._formal_reviewer, "Formal Reviewer")
        return result

    def _make_accepted_candidate_receipt(
        self,
        *,
        parent: CandidateGateParent,
        candidate_receipt: CandidateReceipt,
        candidate_diff_record: CandidateDiffRecordV1,
        review_subject: ReviewSubject,
        assurance_requirement: AssuranceRequirement,
        evidence: tuple[DigestEvidence, ...],
        review_finding_ledger_digest: str | None,
    ) -> AcceptedCandidateReceipt:
        facts = self._acceptance_facts
        if facts is None:
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "CandidateGate acceptance facts are not configured",
            )
        evidence_digests = tuple(sorted({item.digest for item in evidence}))
        ledger_digest = review_finding_ledger_digest or digest_value(
            {"kind": "review_finding_ledger.v1", "entries": []}
        )
        return AcceptedCandidateReceipt(
            repository=candidate_receipt.repository,
            campaign_key=candidate_receipt.campaign_key,
            plan_revision_digest=candidate_receipt.plan_revision_digest,
            target_branch=facts.target_branch,
            ticket_key=candidate_receipt.ticket_key,
            work_run_key=candidate_receipt.work_run_key,
            integration_node_key=facts.integration_node_key,
            accepted_sequence=facts.accepted_sequence,
            base_sha=candidate_receipt.base_commit_oid,
            base_tree_oid=candidate_receipt.base_tree_oid,
            candidate_sha=candidate_receipt.candidate_commit_oid,
            candidate_tree_oid=candidate_receipt.candidate_tree_oid,
            candidate_receipt_digest=candidate_receipt.digest,
            diff_schema_version=candidate_receipt.diff_schema_version,
            diff_record_digest=candidate_receipt.diff_record_digest,
            authority_subtree_digest=candidate_receipt.authority_subtree_digest,
            policy_witness_digest=parent.policy_witness_digest,
            review_subject_digest=review_subject.digest,
            assurance=assurance_requirement.mode.value,
            assurance_requirement_digest=assurance_requirement.digest,
            check_environment_digest=facts.check_environment_digest,
            delivery_identity_digest=facts.delivery_identity_digest,
            interaction_keys=derive_interaction_keys(
                candidate_diff_record,
                protected_surfaces=facts.protected_surfaces,
            ),
            protected_surfaces=facts.protected_surfaces,
            gitlink_change=record_has_gitlink_change(candidate_diff_record),
            evidence_digests=evidence_digests,
            review_finding_ledger_digest=ledger_digest,
        )

    def _audit_readback(
        self,
        parent: CandidateGateParent,
        readback: CandidateReadback,
        checks: tuple[CandidateCheckEvidence, ...],
        requirement: AssuranceRequirement,
    ) -> CandidateAuditReport:
        if type(checks) is not tuple or any(
            type(check) is not CandidateCheckEvidence for check in checks
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_CHECK_INVALID",
                "Candidate check Evidence is not an exact immutable tuple",
            )
        check_ids = tuple(check.check_id for check in checks)
        if len(set(check_ids)) != len(check_ids):
            raise CandidateGateError(
                "CANDIDATE_GATE_CHECK_INVALID",
                "Candidate check Evidence contains duplicate check IDs",
            )
        if tuple(sorted(check_ids)) != requirement.required_check_ids:
            raise CandidateGateError(
                "CANDIDATE_GATE_CHECK_INVALID",
                "Candidate check Evidence does not exactly satisfy the Assurance requirement",
            )
        for check in checks:
            if check.candidate_tree_oid != readback.candidate.candidate_tree_oid:
                raise CandidateGateError(
                    "CANDIDATE_GATE_EVIDENCE_STALE",
                    "Candidate check Evidence is bound to another Candidate tree",
                )
            if check.failure is not None:
                _validate_stored_digest(
                    check.failure.failure_digest,
                    check.failure._body(),
                    code="CANDIDATE_GATE_CHECK_INVALID",
                    detail="Candidate check failure Evidence digest changed",
                )
            expected_observation_digest = _candidate_check_observation_digest(
                check_id=check.check_id,
                candidate_tree_oid=check.candidate_tree_oid,
                diff_record_digest=readback.diff_record.digest,
                outcome=check.outcome,
                failure_digest=(
                    None if check.failure is None else check.failure.digest
                ),
            )
            if check.observation_digest != expected_observation_digest:
                raise CandidateGateError(
                    "CANDIDATE_GATE_CHECK_INVALID",
                    "Candidate check observation is not bound to the complete diff",
                )
        failures = tuple(
            sorted(
                (check.failure for check in checks if check.failure is not None),
                key=lambda failure: failure.digest,
            )
        )
        return CandidateAuditReport(
            parent_digest=parent.digest,
            candidate=readback.candidate,
            failures=failures,
            diff_record=readback.diff_record,
            standards=requirement.standards,
            check_evidence_digests=tuple(sorted(check.digest for check in checks)),
            assurance_requirement=requirement.digest,
        )

    def _invoke_review_action(
        self,
        parent: CandidateGateParent,
        action: ReviewAction,
    ) -> FormalReviewResult:
        reviewer = self._formal_reviewer
        if reviewer is None:
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEWER_UNAVAILABLE",
                "Assurance requires the CandidateGate Formal Reviewer",
            )
        self._validate_read_only_port(reviewer, "Formal Reviewer")
        result = reviewer.review(action)
        self._validate_review_result(parent, action.subject, result)
        return result

    def _review_with_transport_retry(
        self,
        parent: CandidateGateParent,
        action: ReviewAction,
    ) -> FormalReviewResult:
        try:
            return self._invoke_review_action(parent, action)
        except InvalidReviewTransport:
            retry = ReviewAction.for_subject(
                kind="review_strong",
                subject=action.subject,
            )
            if retry.subject.digest != action.subject.digest:
                raise CandidateGateError(
                    "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                    "review_strong retry changed ReviewSubject identity",
                )
            return self._invoke_review_action(parent, retry)

    def _merge_review_results(
        self,
        subject: ReviewSubject,
        results: tuple[FormalReviewResult, ...],
    ) -> FormalReviewResult:
        by_id: dict[str, FormalReviewFinding] = {}
        for result in results:
            for finding in result.findings:
                prior = by_id.get(finding.finding_id)
                if prior is not None and prior.digest != finding.digest:
                    raise CandidateGateError(
                        "CANDIDATE_GATE_EVIDENCE_INVALID",
                        "Reviewers returned conflicting Findings with one ID",
                    )
                by_id[finding.finding_id] = finding
        return FormalReviewResult(
            subject_digest=subject.digest,
            findings=tuple(by_id[key] for key in sorted(by_id)),
        )

    def _run_assurance_review(
        self,
        parent: CandidateGateParent,
        subject: ReviewSubject,
        requirement: AssuranceRequirement,
    ) -> FormalReviewResult | None:
        cached = self._review_results.get(subject.digest)
        if cached is not None:
            self._validate_review_result(parent, subject, cached)
            return cached
        if requirement.mode is AssuranceMode.NO_REVIEW:
            return FormalReviewResult(subject_digest=subject.digest, findings=())
        if (
            requirement.mode is AssuranceMode.STRICT
            and requirement.specialist_policy_id is None
        ):
            return None
        primary = self._review_with_transport_retry(
            parent,
            ReviewAction.for_subject(kind="formal_review", subject=subject),
        )
        results = [primary]
        if requirement.mode is AssuranceMode.STRICT:
            assert requirement.specialist_policy_id is not None
            results.append(
                self._review_with_transport_retry(
                    parent,
                    ReviewAction.for_subject(
                        kind="specialist_review",
                        subject=subject,
                        specialist_policy_id=requirement.specialist_policy_id,
                    ),
                )
            )
        merged = self._merge_review_results(subject, tuple(results))
        self._review_results[subject.digest] = merged
        return merged

    def _audit_without_second_readback(
        self,
        parent: CandidateGateParent,
        audit: CandidateAuditReport,
        *,
        receipt: CandidateReceipt,
        readback: CandidateReadback,
        checks: tuple[CandidateCheckEvidence, ...],
        requirement: AssuranceRequirement,
    ) -> CandidateGateResult:
        self._validate_parent(parent)
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
            invalidation_receipt, report = self._report_invalidation(
                parent,
                plan_evidence,
            )
            return CandidateGateResult(
                status=CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
                evidence=(candidate_evidence, plan_evidence),
                plan_invalidation_receipt=invalidation_receipt,
                plan_invalidation_report=report,
            )
        if audit.failures:
            return CandidateGateResult(
                status=CandidateGateStatus.ORDINARY_REJECTED,
                evidence=(candidate_evidence,),
            )
        subject = ReviewSubject.from_assurance(
            parent=parent,
            candidate_receipt=receipt,
            readback=readback,
            audit=audit,
            checks=checks,
            requirement=requirement,
        )
        if requirement.mode is AssuranceMode.NO_REVIEW:
            return CandidateGateResult(
                status=CandidateGateStatus.REVIEW_ACCEPTED,
                evidence=(candidate_evidence,),
                review_subject=subject,
            )
        review_result = self._run_assurance_review(parent, subject, requirement)
        if review_result is None:
            return CandidateGateResult(
                status=CandidateGateStatus.DECISION_REQUIRED,
                evidence=(candidate_evidence,),
                review_subject=subject,
            )
        findings = tuple(sorted(review_result.findings, key=lambda finding: finding.digest))
        scope_findings = tuple(finding for finding in findings if finding.scope_escape)
        if scope_findings:
            plan_evidence = self._plan_evidence_from_findings(
                parent,
                audit,
                findings,
            )
            invalidation_receipt, report = self._report_invalidation(
                parent,
                plan_evidence,
            )
            return CandidateGateResult(
                status=CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
                evidence=(candidate_evidence, *findings, plan_evidence),
                plan_invalidation_receipt=invalidation_receipt,
                plan_invalidation_report=report,
            )
        hard_findings = tuple(
            finding for finding in findings if finding.severity == "hard"
        )
        if hard_findings:
            packet = RepairPacket.from_findings(
                parent,
                audit.candidate,
                subject,
                hard_findings,
            )
            return CandidateGateResult(
                status=CandidateGateStatus.REPAIR_REQUIRED,
                evidence=(candidate_evidence, *findings),
                repair_packet=packet,
                review_subject=subject,
            )
        return CandidateGateResult(
            status=CandidateGateStatus.REVIEW_ACCEPTED,
            evidence=(candidate_evidence, *findings),
            review_subject=subject,
        )

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
                review_subject=request,
            )
        return CandidateGateResult(
            status=CandidateGateStatus.REVIEW_ACCEPTED,
            evidence=(candidate_evidence, *finding_evidence),
            review_subject=request,
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

    def _read_authoritative_repair_candidate(
        self,
        parent: CandidateGateParent,
        candidate: CandidateIdentity,
    ) -> CandidateReadback:
        """Read the repaired Candidate before trusting any changed-path claim."""

        reader = self._candidate_reader
        if reader is None:
            raise CandidateGateError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "Repair Verification requires authoritative Candidate readback",
            )
        try:
            readback = reader.read_candidate(
                parent.runtime_subject.repository,
                candidate.reported_reference,
            )
        except CandidateGateError:
            raise
        except Exception as error:
            raise CandidateGateError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "authoritative repaired Candidate reference readback failed",
            ) from error
        if type(readback) is not CandidateReadback:
            raise CandidateGateError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "authoritative repaired Candidate readback is not typed",
            )
        if readback.repository != parent.runtime_subject.repository:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_STALE",
                "authoritative repaired Candidate belongs to another repository",
            )
        authoritative = readback.candidate
        if authoritative.reported_reference != candidate.reported_reference:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_STALE",
                "authoritative repaired Candidate reference changed during readback",
            )
        immutable_fields = (
            "base_commit_oid",
            "base_tree_oid",
            "candidate_commit_oid",
            "candidate_tree_oid",
        )
        if any(
            getattr(authoritative, field) != getattr(candidate, field)
            for field in immutable_fields
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_STALE",
                "authoritative repaired Candidate immutable identity changed",
            )
        return readback

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
        readback = self._read_authoritative_repair_candidate(parent, candidate)
        candidate = readback.candidate
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
        candidate_paths = set(candidate.changed_path_tokens)
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
                    readback.diff_record.canonical(),
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
            plan_invalidation_report=report,
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
    "AcceptedCandidateReceipt",
    "AssuranceMode",
    "AssurancePolicy",
    "AssuranceRequirement",
    "CandidateAuditEvidence",
    "CandidateAuditReport",
    "CandidateAcceptanceFacts",
    "CandidateCheckEvidence",
    "CandidateCheckRunner",
    "CandidateDiffEntryV1",
    "CandidateDiffRecordV1",
    "CandidateDiffArtifactStore",
    "CandidateGate",
    "CandidateGateError",
    "CandidateGateParent",
    "CandidateGateResult",
    "CandidateGateStatus",
    "CandidateIdentity",
    "CandidateReadback",
    "CandidateReadbackPort",
    "CandidateReceipt",
    "DigestEvidence",
    "InvalidReviewTransport",
    "InteractionClassification",
    "InteractionKey",
    "derive_interaction_keys",
    "record_has_gitlink_change",
    "DeterministicAuditFailure",
    "FormalReviewer",
    "FormalReviewFinding",
    "FormalReviewRequest",
    "FormalReviewResult",
    "ReviewAction",
    "ReviewSubject",
    "PlanInvalidationEvidence",
    "PlanInvalidationReporter",
    "RepairPacket",
    "RepairVerifier",
    "RepairVerificationEvidence",
    "RepairVerificationRequest",
    "RepairVerificationResult",
    "RuntimeGatewayPlanInvalidationAdapter",
]
