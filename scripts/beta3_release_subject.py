"""Closed value types for the V8 release subject manifest."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping


RELEASE_SUBJECT_SCHEMA = "gwo-v8-release-subject.v1"
RELEASE_SUBJECT_FILENAME = "gwo-v8-release-subject.json"
REPOSITORY = "NOirBRight/github-work-orchestrator"
REMOTE_REF = "origin/main"
ATTESTOR_FILENAMES = (
    "beta3_bootstrap_model.py",
    "beta3_control_ownership_attestor.py",
    "beta3_legacy_attestor.py",
    "beta3_replay_guard.py",
)

_BODY_KEYS = frozenset(
    {
        "schema",
        "repository",
        "repository_root",
        "evidence_root",
        "merged_main_sha",
        "merged_main_git_tree",
        "audited_source_tree_digest",
        "remote_ref",
        "runner",
        "attestors",
        "attestor_bundle_sha256",
        "reviewed_provenance",
    }
)
_TOP_LEVEL_KEYS = _BODY_KEYS | {"subject_digest"}
_FILE_IDENTITY_KEYS = frozenset({"module", "path", "sha256"})
_REVIEWED_PROVENANCE_KEYS = frozenset({"path", "sha256"})
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ReleaseSubjectError(ValueError):
    """Raised when a release subject is not a valid closed value."""

    code: str
    detail: str

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a value using the release subject canonical JSON encoding."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def release_subject_digest(body: Mapping[str, object]) -> str:
    """Return the SHA-256 digest of a canonical release subject body."""

    encoded = canonical_json_bytes(dict(body))
    return hashlib.sha256(encoded).hexdigest()


def _schema_invalid(detail: str) -> None:
    raise ReleaseSubjectError("RELEASE_SUBJECT_SCHEMA_INVALID", detail)


def _require_exact_text(value: object, field: str) -> str:
    if type(value) is not str or not value:
        _schema_invalid(f"{field} must be non-empty exact text")
    return value


def _require_digest(value: object, field: str, length: int = 64) -> str:
    text = _require_exact_text(value, field)
    pattern = _HEX40 if length == 40 else _HEX64
    if pattern.fullmatch(text) is None:
        _schema_invalid(f"{field} must be lowercase hexadecimal with length {length}")
    return text


def _require_closed_keys(
    value: object, expected: frozenset[str], field: str
) -> dict[str, object]:
    if type(value) is not dict or set(value) != expected:
        _schema_invalid(f"{field} must have exactly the closed key set")
    return value


def _canonical_path(path: Path) -> str:
    return str(path.expanduser().resolve(strict=False))


@dataclass(frozen=True)
class ReleaseFileIdentity:
    module: str
    path: str
    sha256: str

    def __post_init__(self) -> None:
        _require_exact_text(self.module, "module")
        _require_exact_text(self.path, "path")
        _require_digest(self.sha256, "sha256")

    def canonical(self) -> dict[str, str]:
        return {
            "module": self.module,
            "path": self.path,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class ReviewedProvenanceIdentity:
    path: str
    sha256: str

    def __post_init__(self) -> None:
        _require_exact_text(self.path, "path")
        _require_digest(self.sha256, "sha256")

    def canonical(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


def _validate_fixed_identities(
    repository_root: str,
    runner: ReleaseFileIdentity,
    attestors: tuple[ReleaseFileIdentity, ...],
    reviewed_provenance: ReviewedProvenanceIdentity,
) -> None:
    canonical_root = _canonical_path(Path(repository_root))
    if repository_root != canonical_root:
        _schema_invalid("repository_root must be canonical")
    expected_runner_path = _canonical_path(
        Path(canonical_root) / "scripts" / "run_beta3_live_guard.py"
    )
    if runner.module != "run_beta3_live_guard" or runner.path != expected_runner_path:
        _schema_invalid("runner identity is not the canonical live guard")
    if len(attestors) != len(ATTESTOR_FILENAMES):
        _schema_invalid("attestors must contain the four ordered attestors")
    for observed, filename in zip(attestors, ATTESTOR_FILENAMES, strict=True):
        expected_module = filename.removesuffix(".py")
        expected_path = _canonical_path(Path(canonical_root) / "scripts" / filename)
        if observed.module != expected_module or observed.path != expected_path:
            _schema_invalid(
                "attestor identity is not the required ordered canonical identity"
            )
    expected_reviewed_path = _canonical_path(
        Path(canonical_root) / "scripts" / "beta3_reviewed_provenance.json"
    )
    if reviewed_provenance.path != expected_reviewed_path:
        _schema_invalid("reviewed_provenance path is not canonical")


@dataclass(frozen=True)
class ReleaseSubject:
    schema: str
    repository: str
    repository_root: str
    evidence_root: str
    merged_main_sha: str
    merged_main_git_tree: str
    audited_source_tree_digest: str
    remote_ref: str
    runner: ReleaseFileIdentity
    attestors: tuple[
        ReleaseFileIdentity,
        ReleaseFileIdentity,
        ReleaseFileIdentity,
        ReleaseFileIdentity,
    ]
    attestor_bundle_sha256: str
    reviewed_provenance: ReviewedProvenanceIdentity
    subject_digest: str

    def __post_init__(self) -> None:
        if self.schema != RELEASE_SUBJECT_SCHEMA:
            _schema_invalid("schema is not the supported release subject schema")
        if self.repository != REPOSITORY:
            _schema_invalid("repository is not the supported repository")
        if self.remote_ref != REMOTE_REF:
            _schema_invalid("remote_ref is not the supported remote ref")
        for field in (
            "schema",
            "repository",
            "repository_root",
            "evidence_root",
            "remote_ref",
        ):
            _require_exact_text(getattr(self, field), field)
        _require_digest(self.merged_main_sha, "merged_main_sha", length=40)
        _require_digest(self.merged_main_git_tree, "merged_main_git_tree", length=40)
        _require_digest(
            self.audited_source_tree_digest,
            "audited_source_tree_digest",
        )
        _require_digest(self.attestor_bundle_sha256, "attestor_bundle_sha256")
        _require_digest(self.subject_digest, "subject_digest")
        if type(self.runner) is not ReleaseFileIdentity:
            _schema_invalid("runner must be a ReleaseFileIdentity")
        if type(self.attestors) is not tuple or any(
            type(attestor) is not ReleaseFileIdentity for attestor in self.attestors
        ):
            _schema_invalid("attestors must be a tuple of ReleaseFileIdentity values")
        if type(self.reviewed_provenance) is not ReviewedProvenanceIdentity:
            _schema_invalid("reviewed_provenance must be a ReviewedProvenanceIdentity")
        _validate_fixed_identities(
            self.repository_root,
            self.runner,
            self.attestors,
            self.reviewed_provenance,
        )
        if self.subject_digest != release_subject_digest(self.canonical_body()):
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_DIGEST_MISMATCH",
                "subject_digest is not the digest of the canonical body",
            )

    def canonical_body(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "repository": self.repository,
            "repository_root": self.repository_root,
            "evidence_root": self.evidence_root,
            "merged_main_sha": self.merged_main_sha,
            "merged_main_git_tree": self.merged_main_git_tree,
            "audited_source_tree_digest": self.audited_source_tree_digest,
            "remote_ref": self.remote_ref,
            "runner": self.runner.canonical(),
            "attestors": [attestor.canonical() for attestor in self.attestors],
            "attestor_bundle_sha256": self.attestor_bundle_sha256,
            "reviewed_provenance": self.reviewed_provenance.canonical(),
        }

    def canonical(self) -> dict[str, object]:
        return {**self.canonical_body(), "subject_digest": self.subject_digest}

    @classmethod
    def from_canonical(cls, value: Mapping[str, object]) -> "ReleaseSubject":
        runner = _require_closed_keys(value["runner"], _FILE_IDENTITY_KEYS, "runner")
        raw_attestors = value["attestors"]
        if type(raw_attestors) is not list:
            _schema_invalid("attestors must be a JSON array")
        attestors = tuple(
            ReleaseFileIdentity(
                module=_require_exact_text(item["module"], "attestors.module"),
                path=_require_exact_text(item["path"], "attestors.path"),
                sha256=_require_digest(item["sha256"], "attestors.sha256"),
            )
            for item in (
                _require_closed_keys(item, _FILE_IDENTITY_KEYS, "attestors[]")
                for item in raw_attestors
            )
        )
        reviewed = _require_closed_keys(
            value["reviewed_provenance"],
            _REVIEWED_PROVENANCE_KEYS,
            "reviewed_provenance",
        )
        return cls(
            schema=_require_exact_text(value["schema"], "schema"),
            repository=_require_exact_text(value["repository"], "repository"),
            repository_root=_require_exact_text(
                value["repository_root"], "repository_root"
            ),
            evidence_root=_require_exact_text(value["evidence_root"], "evidence_root"),
            merged_main_sha=_require_digest(
                value["merged_main_sha"], "merged_main_sha", length=40
            ),
            merged_main_git_tree=_require_digest(
                value["merged_main_git_tree"], "merged_main_git_tree", length=40
            ),
            audited_source_tree_digest=_require_digest(
                value["audited_source_tree_digest"],
                "audited_source_tree_digest",
            ),
            remote_ref=_require_exact_text(value["remote_ref"], "remote_ref"),
            runner=ReleaseFileIdentity(
                module=_require_exact_text(runner["module"], "runner.module"),
                path=_require_exact_text(runner["path"], "runner.path"),
                sha256=_require_digest(runner["sha256"], "runner.sha256"),
            ),
            attestors=attestors,
            attestor_bundle_sha256=_require_digest(
                value["attestor_bundle_sha256"], "attestor_bundle_sha256"
            ),
            reviewed_provenance=ReviewedProvenanceIdentity(
                path=_require_exact_text(reviewed["path"], "reviewed_provenance.path"),
                sha256=_require_digest(
                    reviewed["sha256"], "reviewed_provenance.sha256"
                ),
            ),
            subject_digest=_require_digest(value["subject_digest"], "subject_digest"),
        )


class _DuplicateKeyError(ValueError):
    pass


def _object_pairs_without_duplicates(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-standard JSON constant: {value}")


def _decode_exact_canonical_object(raw: bytes) -> dict[str, object]:
    if type(raw) is not bytes:
        _schema_invalid("raw release subject must be exact bytes")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_pairs_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _schema_invalid(f"raw release subject is not valid canonical JSON: {error}")
    if type(value) is not dict:
        _schema_invalid("raw release subject must contain a JSON object")
    try:
        canonical = canonical_json_bytes(value)
    except UnicodeEncodeError as error:
        _schema_invalid(f"raw release subject is not valid canonical JSON: {error}")
    if canonical != raw:
        _schema_invalid("raw release subject is not canonically encoded")
    return value


def _validate_closed_shape(
    value: dict[str, object],
    expected_repository_root: Path,
    expected_evidence_root: Path,
) -> None:
    _require_closed_keys(value, _TOP_LEVEL_KEYS, "release subject")
    if value["schema"] != RELEASE_SUBJECT_SCHEMA:
        _schema_invalid("schema is not the supported release subject schema")
    if value["repository"] != REPOSITORY:
        _schema_invalid("repository is not the supported repository")
    if value["remote_ref"] != REMOTE_REF:
        _schema_invalid("remote_ref is not the supported remote ref")

    repository_root = Path(expected_repository_root).expanduser().resolve(strict=False)
    evidence_root = Path(expected_evidence_root).expanduser().resolve(strict=False)
    if value["repository_root"] != _canonical_path(repository_root):
        _schema_invalid("repository_root is not the expected canonical root")
    if value["evidence_root"] != _canonical_path(evidence_root):
        _schema_invalid("evidence_root is not the expected canonical root")

    _require_exact_text(value["schema"], "schema")
    _require_exact_text(value["repository"], "repository")
    _require_exact_text(value["repository_root"], "repository_root")
    _require_exact_text(value["evidence_root"], "evidence_root")
    _require_exact_text(value["remote_ref"], "remote_ref")
    _require_digest(value["merged_main_sha"], "merged_main_sha", length=40)
    _require_digest(value["merged_main_git_tree"], "merged_main_git_tree", length=40)
    _require_digest(
        value["audited_source_tree_digest"],
        "audited_source_tree_digest",
    )
    _require_digest(value["attestor_bundle_sha256"], "attestor_bundle_sha256")
    _require_digest(value["subject_digest"], "subject_digest")

    runner = _require_closed_keys(value["runner"], _FILE_IDENTITY_KEYS, "runner")
    _require_exact_text(runner["module"], "runner.module")
    _require_exact_text(runner["path"], "runner.path")
    _require_digest(runner["sha256"], "runner.sha256")

    attestors = value["attestors"]
    if type(attestors) is not list:
        _schema_invalid("attestors must be a JSON array")
    for observed in attestors:
        identity = _require_closed_keys(observed, _FILE_IDENTITY_KEYS, "attestors[]")
        _require_exact_text(identity["module"], "attestors[].module")
        _require_exact_text(identity["path"], "attestors[].path")
        _require_digest(identity["sha256"], "attestors[].sha256")

    reviewed = _require_closed_keys(
        value["reviewed_provenance"],
        _REVIEWED_PROVENANCE_KEYS,
        "reviewed_provenance",
    )
    _require_exact_text(reviewed["path"], "reviewed_provenance.path")
    _require_digest(reviewed["sha256"], "reviewed_provenance.sha256")


def parse_release_subject(
    raw: bytes,
    expected_repository_root: Path,
    expected_evidence_root: Path,
) -> ReleaseSubject:
    """Parse, validate, and type a canonical release subject payload."""

    value = _decode_exact_canonical_object(raw)
    _validate_closed_shape(value, expected_repository_root, expected_evidence_root)
    return ReleaseSubject.from_canonical(value)
