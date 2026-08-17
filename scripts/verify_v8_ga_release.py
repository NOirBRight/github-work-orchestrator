#!/usr/bin/env python3
"""Verify the GWO V8 GA release contract and clean-install smoke gate."""

from __future__ import annotations

import argparse
import dataclasses
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
from typing import Mapping, Protocol, Sequence


PYTHON = sys.executable
GA_VERSION = "8.0.0"
GA_RELEASE_RECORD_SCHEMA = "gwo-v8-ga-release-record.v1"
ALLOWED_METADATA_PATHS = (
    "CHANGELOG.md",
    "docs/e2e/gwo-v8-root-canary.md",
    "docs/releases/v8.0.0.md",
)
DYNAMIC_METADATA_FIELDS = frozenset(
    {
        "ci_conclusion",
        "ci_head_sha",
        "ci_run_id",
        "ci_url",
        "dynamic_pass_summary",
        "final_metadata_commit_sha",
        "main_sha",
        "metadata_commit_sha",
        "pytest_pass_count",
        "tag_candidate_sha",
    }
)
_STATIC_RECORD_FIELDS = frozenset(
    {
        "activation_receipt_digest",
        "activation_id",
        "campaign_key",
        "canary_receipt_digest",
        "canary_target_sha",
        "default_writer_receipt_digest",
        "evidence_base_sha",
        "post_canary_changed_paths",
        "repository",
        "version",
        "writer_generation",
    }
)
_SHA = re.compile(r"[0-9a-f]{40}\Z")


class ReleaseGateError(RuntimeError):
    """A named fail-closed outcome from the release gate."""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def digest_value(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class _DuplicateJsonKey(ValueError):
    pass


def _strict_json_loads(raw: bytes | str, *, require_canonical: bool) -> object:
    def reject_constant(token: str) -> None:
        raise ValueError(f"non-standard JSON number: {token}")

    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, child in pairs:
            if key in value:
                raise _DuplicateJsonKey(key)
            value[key] = child
        return value

    try:
        text = raw.decode("utf-8") if type(raw) is bytes else raw
        if type(text) is not str:
            raise TypeError("JSON input must be UTF-8 bytes or text")
        value = json.loads(
            text,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
        if require_canonical and canonical_json_bytes(value) != (
            raw if type(raw) is bytes else raw.encode("utf-8")
        ):
            raise ValueError("JSON is not canonical")
        return value
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ReleaseGateError("GA_CANONICAL_JSON_INVALID") from error


def _strict_canonical_json_loads(raw: bytes | str) -> object:
    return _strict_json_loads(raw, require_canonical=True)


def _require_sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ReleaseGateError(code)
    return value


def _require_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseGateError(code)
    return value


@dataclass(frozen=True, slots=True)
class GaReleaseRecord:
    """The committed, static half of the GA release subject."""

    version: str
    repository: str
    evidence_base_sha: str
    canary_target_sha: str
    canary_receipt_digest: str
    activation_receipt_digest: str
    default_writer_receipt_digest: str
    post_canary_changed_paths: tuple[str, ...] = ALLOWED_METADATA_PATHS
    campaign_key: str | None = None
    activation_id: str | None = None
    writer_generation: str | None = None

    def __post_init__(self) -> None:
        if self.version != GA_VERSION:
            raise ReleaseGateError("GA_VERSION_INVALID")
        _require_text(self.repository, "GA_REPOSITORY_INVALID")
        _require_sha(self.evidence_base_sha, "GA_STATIC_SHA_INVALID")
        _require_sha(self.canary_target_sha, "GA_STATIC_SHA_INVALID")
        for digest in (
            self.canary_receipt_digest,
            self.activation_receipt_digest,
            self.default_writer_receipt_digest,
        ):
            _require_text(digest, "GA_STATIC_RECEIPT_INVALID")
        if tuple(self.post_canary_changed_paths) != ALLOWED_METADATA_PATHS:
            raise ReleaseGateError("GA_METADATA_PATH_ALLOWLIST_INVALID")
        for value in (self.campaign_key, self.activation_id, self.writer_generation):
            if value is not None:
                _require_text(value, "GA_STATIC_IDENTITY_INVALID")

    @classmethod
    def from_fixture(cls, fixture: object) -> "GaReleaseRecord":
        try:
            return cls(
                version=str(getattr(fixture, "version")),
                repository=str(getattr(fixture, "repository")),
                evidence_base_sha=str(getattr(fixture, "evidence_base_sha")),
                canary_target_sha=str(getattr(fixture, "canary_target_sha")),
                canary_receipt_digest=str(getattr(fixture, "canary_receipt_digest")),
                activation_receipt_digest=str(
                    getattr(fixture, "activation_receipt_digest")
                ),
                default_writer_receipt_digest=str(
                    getattr(fixture, "default_writer_receipt_digest")
                ),
                campaign_key=getattr(fixture, "campaign_key", None),
                activation_id=getattr(fixture, "activation_id", None),
                writer_generation=getattr(fixture, "writer_generation", None),
            )
        except AttributeError as error:
            raise ReleaseGateError("GA_STATIC_RECORD_FIELDS_MISSING") from error

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "GaReleaseRecord":
        if not isinstance(raw, Mapping):
            raise ReleaseGateError("GA_STATIC_RECORD_NOT_OBJECT")
        if "schema" in raw and raw["schema"] != GA_RELEASE_RECORD_SCHEMA:
            raise ReleaseGateError("GA_RELEASE_RECORD_SCHEMA_INVALID")
        dynamic = DYNAMIC_METADATA_FIELDS.intersection(raw)
        if dynamic:
            raise ReleaseGateError("GA_STATIC_RECORD_CONTAINS_DYNAMIC_SHA_OR_CI")
        allowed = _STATIC_RECORD_FIELDS | {"schema"}
        unknown = set(raw) - allowed
        if unknown:
            raise ReleaseGateError("GA_STATIC_RECORD_FIELDS_INVALID")
        required = _STATIC_RECORD_FIELDS - {
            "activation_id",
            "campaign_key",
            "post_canary_changed_paths",
            "writer_generation",
        }
        if required - set(raw):
            raise ReleaseGateError("GA_STATIC_RECORD_FIELDS_MISSING")
        paths = raw.get("post_canary_changed_paths", ALLOWED_METADATA_PATHS)
        if not isinstance(paths, (list, tuple)):
            raise ReleaseGateError("GA_METADATA_PATH_ALLOWLIST_INVALID")
        if not all(type(path) is str for path in paths):
            raise ReleaseGateError("GA_METADATA_PATH_ALLOWLIST_INVALID")
        try:
            normalized_paths = tuple(paths)
            return cls(
                version=raw["version"],  # type: ignore[arg-type]
                repository=raw["repository"],  # type: ignore[arg-type]
                evidence_base_sha=raw["evidence_base_sha"],  # type: ignore[arg-type]
                canary_target_sha=raw["canary_target_sha"],  # type: ignore[arg-type]
                canary_receipt_digest=raw["canary_receipt_digest"],  # type: ignore[arg-type]
                activation_receipt_digest=raw["activation_receipt_digest"],  # type: ignore[arg-type]
                default_writer_receipt_digest=raw["default_writer_receipt_digest"],  # type: ignore[arg-type]
                post_canary_changed_paths=normalized_paths,
                campaign_key=raw.get("campaign_key"),  # type: ignore[arg-type]
                activation_id=raw.get("activation_id"),  # type: ignore[arg-type]
                writer_generation=raw.get("writer_generation"),  # type: ignore[arg-type]
            )
        except KeyError as error:
            raise ReleaseGateError("GA_STATIC_RECORD_FIELDS_MISSING") from error


def write_ga_release_record(path: Path, fixture: object) -> Path:
    record = GaReleaseRecord.from_fixture(fixture)
    payload = {
        "schema": GA_RELEASE_RECORD_SCHEMA,
        **dataclasses.asdict(record),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))
    return path


def load_ga_release_record(path: Path) -> GaReleaseRecord:
    try:
        raw = _strict_canonical_json_loads(path.read_bytes())
    except (OSError, UnicodeError, ReleaseGateError) as error:
        raise ReleaseGateError("GA_RELEASE_RECORD_UNREADABLE") from error
    if not isinstance(raw, Mapping) or raw.get("schema") != GA_RELEASE_RECORD_SCHEMA:
        raise ReleaseGateError("GA_RELEASE_RECORD_SCHEMA_INVALID")
    return GaReleaseRecord.from_mapping(raw)


@dataclass(frozen=True, slots=True)
class CleanInstallResult:
    surfaces: tuple[str, str, str]
    public_names: tuple[str, str, str]
    source_checkout_imported: bool


def run(
    arguments: Sequence[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(arguments),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr or completed.stdout
        raise ReleaseGateError("GA_COMMAND_FAILED", detail)
    return completed


def _stage_install_source(source: Path, run_root: Path) -> Path:
    source = source.resolve()
    sync_source = source / "scripts" / "sync_orchestrator.py"
    skills_source = source / "skills"
    if not sync_source.is_file() or not skills_source.is_dir():
        raise ReleaseGateError("GA_CLEAN_INSTALL_SOURCE_INVALID")
    staging = Path(tempfile.mkdtemp(prefix=".release-source-", dir=run_root))
    (staging / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(sync_source, staging / "scripts" / "sync_orchestrator.py")
    shutil.copytree(
        skills_source,
        staging / "skills",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return staging


def _public_import_smoke(install_root: Path, source_root: Path, run_root: Path) -> None:
    python_root = (install_root / "orchestrator" / "scripts").resolve()
    code = f"""
import sys
from pathlib import Path

source_package_root = (
    Path({str(source_root)!r}).resolve()
    / "skills"
    / "orchestrator"
    / "scripts"
)
installed_root = Path({str(python_root)!r}).resolve()
sys.path.insert(0, str(installed_root))
import gwo_v8
from gwo_v8 import advance, inspect, start

expected = ("advance", "inspect", "start")
if gwo_v8.__all__ != expected:
    raise SystemExit("unexpected public GWO V8 API")
if not all(callable(operation) for operation in (start, advance, inspect)):
    raise SystemExit("public GWO V8 operation is not callable")
for name, module in tuple(sys.modules.items()):
    if name != "gwo_v8" and not name.startswith("gwo_v8."):
        continue
    origin = getattr(module, "__file__", None)
    if origin is None:
        raise SystemExit(f"module has no origin: {{name}}")
    origin_path = Path(origin).resolve()
    if installed_root not in origin_path.parents:
        raise SystemExit(f"GWO V8 import escaped install root: {{origin_path}}")
    if source_package_root in origin_path.parents:
        raise SystemExit(f"GWO V8 import used source checkout: {{origin_path}}")
print("advance,inspect,start")
"""
    smoke = subprocess.run(
        [PYTHON, "-I", "-c", code],
        cwd=run_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if smoke.returncode != 0 or smoke.stdout.strip() != "advance,inspect,start":
        detail = smoke.stderr or smoke.stdout
        raise ReleaseGateError("GA_CLEAN_INSTALL_PUBLIC_IMPORT_FAILED", detail)


def clean_install_and_smoke(source: Path, run_root: Path) -> CleanInstallResult:
    """Install a source snapshot into three temporary surfaces and smoke it."""

    source = source.resolve()
    run_root = run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    staging = _stage_install_source(source, run_root)
    surfaces = (".agents", ".codex", ".claude")
    roots = tuple(run_root / surface / "skills" for surface in surfaces)
    sync_script = staging / "scripts" / "sync_orchestrator.py"
    common = [
        PYTHON,
        str(sync_script),
        "--root",
        str(staging),
        "--backup-root",
        str(run_root / "install-backups"),
    ]
    install = [*common, "--install"]
    check = [*common, "--check"]
    for root in roots:
        install.extend(("--install-root", str(root)))
        check.extend(("--install-root", str(root)))

    # Check the source manifest without allowing the installer to regenerate
    # it, then check any existing installed package before replacement. A
    # fresh surface has no package to verify and is installed below.
    run([*common, "--check"], cwd=run_root)
    existing_roots = tuple(
        root
        for root in roots
        if any((root / skill).exists() for skill in ("implement-gwo", "orchestrator"))
    )
    if existing_roots:
        existing_check = [*common, "--check"]
        for root in existing_roots:
            existing_check.extend(("--install-root", str(root)))
        run(existing_check, cwd=run_root)
    run(install, cwd=run_root)
    run(check, cwd=run_root)
    for root in roots:
        _public_import_smoke(root, source.resolve(), run_root)
    return CleanInstallResult(surfaces, ("advance", "inspect", "start"), False)


@dataclass(frozen=True, slots=True)
class CiReadback:
    run_id: int
    head_sha: str
    conclusion: str
    pytest_pass_count: int


@dataclass(frozen=True, slots=True)
class ReleaseGateReceipt:
    version: str
    repository: str
    evidence_base_sha: str
    canary_target_sha: str
    tag_candidate_sha: str
    tag_candidate_tree_sha: str
    ci_run_id: int
    ci_head_sha: str
    pytest_pass_count: int
    canary_receipt_digest: str
    activation_receipt_digest: str
    default_writer_receipt_digest: str
    campaign_key: str
    activation_id: str
    writer_generation: str

    def __post_init__(self) -> None:
        if self.version != GA_VERSION:
            raise ReleaseGateError("GA_VERSION_INVALID")
        _require_text(self.repository, "GA_REPOSITORY_INVALID")
        for value in (
            self.evidence_base_sha,
            self.canary_target_sha,
            self.tag_candidate_sha,
            self.tag_candidate_tree_sha,
            self.ci_head_sha,
        ):
            _require_sha(value, "GA_RELEASE_RECEIPT_SHA_INVALID")
        if type(self.ci_run_id) is not int or self.ci_run_id < 1:
            raise ReleaseGateError("GA_RELEASE_RECEIPT_CI_INVALID")
        if type(self.pytest_pass_count) is not int or self.pytest_pass_count < 1:
            raise ReleaseGateError("GA_RELEASE_RECEIPT_CI_INVALID")
        for value in (
            self.canary_receipt_digest,
            self.activation_receipt_digest,
            self.default_writer_receipt_digest,
            self.campaign_key,
            self.activation_id,
            self.writer_generation,
        ):
            _require_text(value, "GA_RELEASE_RECEIPT_IDENTITY_INVALID")

    @classmethod
    def from_exact(
        cls,
        record: GaReleaseRecord,
        canary: object,
        activation: object,
        admission: object,
        ci: object,
        main_sha: str,
        *,
        main_tree_sha: str,
        canary_target_sha: str | None = None,
        campaign_key: str | None = None,
        activation_id: str | None = None,
        writer_generation: str | None = None,
    ) -> "ReleaseGateReceipt":
        if canary_target_sha is None:
            if isinstance(canary, Mapping):
                canary_target_sha = str(canary["canary_target_sha"])
            else:
                canary_target_sha = str(getattr(canary, "canary_target_sha"))
        del activation, admission
        return cls(
            version=record.version,
            repository=record.repository,
            evidence_base_sha=record.evidence_base_sha,
            canary_target_sha=canary_target_sha,
            tag_candidate_sha=main_sha,
            tag_candidate_tree_sha=main_tree_sha,
            ci_run_id=int(getattr(ci, "run_id")),
            ci_head_sha=str(getattr(ci, "head_sha")),
            pytest_pass_count=int(getattr(ci, "pytest_pass_count")),
            canary_receipt_digest=record.canary_receipt_digest,
            activation_receipt_digest=record.activation_receipt_digest,
            default_writer_receipt_digest=record.default_writer_receipt_digest,
            campaign_key=campaign_key or record.campaign_key or "",
            activation_id=activation_id or record.activation_id or "",
            writer_generation=writer_generation or record.writer_generation or "",
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "ReleaseGateReceipt":
        fields = {field.name for field in dataclasses.fields(cls)}
        if set(raw) != fields:
            raise ReleaseGateError("GA_PRE_TAG_RECEIPT_FIELDS_INVALID")
        try:
            return cls(**raw)  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise ReleaseGateError("GA_PRE_TAG_RECEIPT_INVALID") from error


class GitAncestryReadback(Protocol):
    repository: str

    def current_origin_main_sha(self) -> str: ...

    def tree_sha(self, commit: str) -> str: ...

    def is_ancestor(self, ancestor: str, descendant: str) -> bool: ...

    def changed_paths(self, ancestor: str, descendant: str) -> tuple[str, ...]: ...


def _attribute(value: object, name: str, code: str) -> object:
    try:
        return getattr(value, name)
    except AttributeError as error:
        raise ReleaseGateError(code) from error


def _canonical_readback_payload(
    value: object, code: str
) -> tuple[dict[str, object], str]:
    try:
        if isinstance(value, Mapping):
            payload = dict(value)
        elif dataclasses.is_dataclass(value):
            payload = dataclasses.asdict(value)
        elif hasattr(value, "__dict__"):
            payload = dict(vars(value))
        else:
            raise TypeError("readback has no canonical object payload")
        claimed = payload.pop("receipt_digest")
        if type(claimed) is not str or claimed != digest_value(payload):
            raise ValueError("receipt digest is not the canonical payload digest")
        canonical_json_bytes(payload)
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        raise ReleaseGateError(code) from error
    return payload, claimed


def _required_readback_field(
    payload: Mapping[str, object], name: str, code: str
) -> object:
    try:
        return payload[name]
    except KeyError as error:
        raise ReleaseGateError(code) from error


def _required_readback_text(payload: Mapping[str, object], name: str, code: str) -> str:
    value = _required_readback_field(payload, name, code)
    if type(value) is not str or not value.strip():
        raise ReleaseGateError(code)
    return value


def _optional_readback_text(
    payload: Mapping[str, object], name: str, code: str
) -> str | None:
    value = _required_readback_field(payload, name, code)
    if value is None:
        return None
    if type(value) is not str or not value.strip():
        raise ReleaseGateError(code)
    return value


_IDENTITY_ALIASES = {
    "repository": frozenset({"repository", "repo"}),
    "campaign_key": frozenset({"campaign_key", "campaign", "campaign_id"}),
    "activation_id": frozenset({"activation_id", "activation"}),
    "writer_generation": frozenset({"writer_generation", "writer"}),
}


def _assert_nested_identity(
    value: object,
    expected: Mapping[str, str],
    code: str,
    *,
    allow_none: frozenset[str] = frozenset(),
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))
            normalized = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_").lower()
            for field, aliases in _IDENTITY_ALIASES.items():
                if normalized in aliases and field in expected:
                    if child is None and field in allow_none:
                        continue
                    if type(child) is not str or child != expected[field]:
                        raise ReleaseGateError(code)
            _assert_nested_identity(child, expected, code, allow_none=allow_none)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_nested_identity(child, expected, code, allow_none=allow_none)


def _read_origin_main_sha(git: GitAncestryReadback) -> str:
    try:
        value = git.current_origin_main_sha()
    except (AttributeError, OSError, subprocess.CalledProcessError) as error:
        raise ReleaseGateError("GA_MAIN_SHA_READBACK_REQUIRED") from error
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ReleaseGateError("GA_MAIN_SHA_READBACK_INVALID")
    return value


def verify_pre_tag(
    record: GaReleaseRecord,
    *,
    main_sha: str | None,
    canary: object,
    activation: object,
    admission: object,
    ci: CiReadback,
    git: GitAncestryReadback,
) -> ReleaseGateReceipt:
    if any(
        type(value) is not str or not value.strip()
        for value in (
            record.campaign_key,
            record.activation_id,
            record.writer_generation,
        )
    ):
        raise ReleaseGateError("GA_STATIC_IDENTITY_INVALID")
    repository = _attribute(git, "repository", "GA_REPOSITORY_READBACK_INVALID")
    if repository != record.repository:
        raise ReleaseGateError("GA_REPOSITORY_READBACK_INVALID")
    current_main_sha = _read_origin_main_sha(git)
    if main_sha is not None and main_sha != current_main_sha:
        raise ReleaseGateError("GA_MAIN_SHA_READBACK_MISMATCH")
    main_sha = current_main_sha

    try:
        ci_run_id = _attribute(ci, "run_id", "GA_EXACT_CI_REQUIRED")
        ci_head_sha = _attribute(ci, "head_sha", "GA_EXACT_CI_REQUIRED")
        conclusion = _attribute(ci, "conclusion", "GA_EXACT_CI_REQUIRED")
        pytest_pass_count = _attribute(ci, "pytest_pass_count", "GA_EXACT_CI_REQUIRED")
    except (TypeError, ValueError) as error:
        raise ReleaseGateError("GA_EXACT_CI_REQUIRED") from error
    if (
        type(ci_run_id) is not int
        or ci_run_id < 1
        or type(ci_head_sha) is not str
        or _SHA.fullmatch(ci_head_sha) is None
        or type(conclusion) is not str
        or main_sha != ci_head_sha
        or conclusion != "success"
        or type(pytest_pass_count) is not int
        or pytest_pass_count < 1
    ):
        raise ReleaseGateError("GA_EXACT_CI_REQUIRED")

    canary_payload, canary_receipt_digest = _canonical_readback_payload(
        canary, "GA_CANARY_RECEIPT_INVALID"
    )
    activation_payload, activation_receipt_digest = _canonical_readback_payload(
        activation, "GA_ACTIVATION_READBACK_INVALID"
    )
    admission_payload, default_writer_receipt_digest = _canonical_readback_payload(
        admission, "GA_DEFAULT_WRITER_READBACK_INVALID"
    )

    canary_repository = _required_readback_text(
        canary_payload, "repository", "GA_CANARY_RECEIPT_MISMATCH"
    )
    canary_campaign = _required_readback_text(
        canary_payload, "campaign_key", "GA_CANARY_RECEIPT_MISMATCH"
    )
    canary_target_sha = _required_readback_field(
        canary_payload, "canary_target_sha", "GA_CANARY_RECEIPT_MISMATCH"
    )
    if (
        canary_repository != record.repository
        or canary_receipt_digest != record.canary_receipt_digest
        or canary_target_sha != record.canary_target_sha
        or canary_campaign != record.campaign_key
    ):
        raise ReleaseGateError("GA_CANARY_RECEIPT_MISMATCH")
    expected_campaign = record.campaign_key
    canary_identity = {
        "repository": record.repository,
        "campaign_key": expected_campaign,
    }
    canary_activation_id = _required_readback_text(
        canary_payload, "activation_id", "GA_CANARY_RECEIPT_MISMATCH"
    )
    canary_identity["activation_id"] = canary_activation_id
    _assert_nested_identity(
        canary_payload, canary_identity, "GA_CANARY_RECEIPT_MISMATCH"
    )

    activation_repository = _required_readback_text(
        activation_payload, "repository", "GA_ACTIVATION_READBACK_INVALID"
    )
    activation_campaign = _required_readback_text(
        activation_payload, "campaign_key", "GA_ACTIVATION_READBACK_INVALID"
    )
    activation_id = _required_readback_text(
        activation_payload, "activation_id", "GA_ACTIVATION_READBACK_INVALID"
    )
    writer_generation = _required_readback_text(
        activation_payload, "writer_generation", "GA_ACTIVATION_READBACK_INVALID"
    )
    if (
        activation_receipt_digest != record.activation_receipt_digest
        or activation_repository != record.repository
        or activation_campaign != expected_campaign
        or activation_id != record.activation_id
        or writer_generation != record.writer_generation
        or writer_generation != "v8"
    ):
        raise ReleaseGateError("GA_ACTIVATION_READBACK_INVALID")
    _assert_nested_identity(
        activation_payload,
        {
            "repository": record.repository,
            "campaign_key": expected_campaign,
            "activation_id": activation_id,
            "writer_generation": writer_generation,
        },
        "GA_ACTIVATION_READBACK_INVALID",
    )
    if canary_activation_id != activation_id:
        raise ReleaseGateError("GA_CANARY_RECEIPT_MISMATCH")

    admission_mode = _required_readback_field(
        admission_payload, "mode", "GA_DEFAULT_WRITER_READBACK_INVALID"
    )
    admission_repository = _required_readback_text(
        admission_payload, "repository", "GA_DEFAULT_WRITER_READBACK_INVALID"
    )
    admission_campaign = _optional_readback_text(
        admission_payload, "campaign_key", "GA_DEFAULT_WRITER_READBACK_INVALID"
    )
    admission_writer_generation = _required_readback_text(
        admission_payload, "writer_generation", "GA_DEFAULT_WRITER_READBACK_INVALID"
    )
    admission_activation_id = _required_readback_text(
        admission_payload, "activation_id", "GA_DEFAULT_WRITER_READBACK_INVALID"
    )
    admission_acceptance_digest = _required_readback_text(
        admission_payload,
        "acceptance_receipt_digest",
        "GA_DEFAULT_WRITER_READBACK_INVALID",
    )
    if (
        admission_mode != "default_v8"
        or admission_repository != record.repository
        or (
            admission_campaign is not None
            and admission_campaign != expected_campaign
        )
        or admission_writer_generation != writer_generation
        or admission_activation_id != activation_id
        or admission_acceptance_digest != record.canary_receipt_digest
        or default_writer_receipt_digest != record.default_writer_receipt_digest
        or admission_activation_id != record.activation_id
        or admission_writer_generation != record.writer_generation
    ):
        raise ReleaseGateError("GA_DEFAULT_WRITER_READBACK_INVALID")
    _assert_nested_identity(
        admission_payload,
        {
            "repository": record.repository,
            "campaign_key": expected_campaign,
            "activation_id": activation_id,
            "writer_generation": writer_generation,
        },
        "GA_DEFAULT_WRITER_READBACK_INVALID",
        allow_none=frozenset({"campaign_key"}),
    )

    try:
        if not git.is_ancestor(
            record.evidence_base_sha, main_sha
        ) or not git.is_ancestor(record.canary_target_sha, main_sha):
            raise ReleaseGateError("GA_CANARY_SHA_NOT_ANCESTOR")
        changed_paths = tuple(
            str(path) for path in git.changed_paths(record.evidence_base_sha, main_sha)
        )
    except ReleaseGateError:
        raise
    except Exception as error:
        raise ReleaseGateError("GA_GIT_READBACK_FAILED", str(error)) from error
    if (
        len(changed_paths) != len(set(changed_paths))
        or set(changed_paths) != set(record.post_canary_changed_paths)
        or set(changed_paths) - set(ALLOWED_METADATA_PATHS)
    ):
        raise ReleaseGateError("GA_POST_CANARY_DELTA_NOT_METADATA_ONLY")

    try:
        main_tree_sha = git.tree_sha(main_sha)
    except (AttributeError, OSError, subprocess.CalledProcessError) as error:
        raise ReleaseGateError("GA_GIT_TREE_READBACK_REQUIRED") from error
    if not isinstance(main_tree_sha, str) or _SHA.fullmatch(main_tree_sha) is None:
        raise ReleaseGateError("GA_GIT_TREE_READBACK_INVALID")
    if _read_origin_main_sha(git) != main_sha:
        raise ReleaseGateError("GA_MAIN_SHA_READBACK_MISMATCH")

    return ReleaseGateReceipt.from_exact(
        record,
        canary,
        activation,
        admission,
        ci,
        main_sha,
        main_tree_sha=main_tree_sha,
        canary_target_sha=str(canary_target_sha),
        campaign_key=expected_campaign,
        activation_id=activation_id,
        writer_generation=writer_generation,
    )


def _verify_post_release_pre_tag_receipt(
    record: GaReleaseRecord,
    receipt: ReleaseGateReceipt,
    git: GitAncestryReadback,
) -> None:
    if (
        receipt.version != record.version
        or receipt.repository != record.repository
        or receipt.evidence_base_sha != record.evidence_base_sha
        or receipt.canary_target_sha != record.canary_target_sha
        or receipt.canary_receipt_digest != record.canary_receipt_digest
        or receipt.activation_receipt_digest != record.activation_receipt_digest
        or receipt.default_writer_receipt_digest
        != record.default_writer_receipt_digest
        or receipt.campaign_key != record.campaign_key
        or receipt.activation_id != record.activation_id
        or receipt.writer_generation != record.writer_generation
        or receipt.tag_candidate_sha != receipt.ci_head_sha
    ):
        raise ReleaseGateError("GA_PRE_TAG_RECEIPT_RECORD_MISMATCH")

    repository = _attribute(git, "repository", "GA_REPOSITORY_READBACK_INVALID")
    if repository != record.repository:
        raise ReleaseGateError("GA_REPOSITORY_READBACK_INVALID")
    try:
        main_tree_sha = git.tree_sha(receipt.tag_candidate_sha)
    except (AttributeError, OSError, subprocess.CalledProcessError) as error:
        raise ReleaseGateError("GA_GIT_TREE_READBACK_REQUIRED") from error
    if main_tree_sha != receipt.tag_candidate_tree_sha:
        raise ReleaseGateError("GA_TAG_RELEASE_SUBJECT_MISMATCH")
    try:
        if not git.is_ancestor(
            record.evidence_base_sha, receipt.tag_candidate_sha
        ) or not git.is_ancestor(record.canary_target_sha, receipt.tag_candidate_sha):
            raise ReleaseGateError("GA_CANARY_SHA_NOT_ANCESTOR")
        changed_paths = tuple(
            str(path)
            for path in git.changed_paths(
                record.evidence_base_sha, receipt.tag_candidate_sha
            )
        )
    except ReleaseGateError:
        raise
    except Exception as error:
        raise ReleaseGateError("GA_GIT_READBACK_FAILED", str(error)) from error
    if (
        len(changed_paths) != len(set(changed_paths))
        or set(changed_paths) != set(record.post_canary_changed_paths)
        or set(changed_paths) - set(ALLOWED_METADATA_PATHS)
    ):
        raise ReleaseGateError("GA_POST_CANARY_DELTA_NOT_METADATA_ONLY")


def parse_pytest_count(log: str) -> int:
    matches = re.findall(r"(\d+) passed", log)
    if not matches:
        raise ReleaseGateError("GA_CI_PYTEST_COUNT_MISSING")
    return int(matches[-1])


def _repository_from_remote(remote: str) -> str:
    value = remote.strip()
    if value.startswith("git@github.com:"):
        repository = value.removeprefix("git@github.com:")
    elif value.startswith(("https://github.com/", "http://github.com/")):
        repository = value.split("github.com/", 1)[1]
    elif value.startswith("ssh://git@github.com/"):
        repository = value.removeprefix("ssh://git@github.com/")
    else:
        raise ReleaseGateError("GA_GIT_REMOTE_INVALID")
    repository = repository.removesuffix(".git").strip("/")
    if repository.count("/") != 1 or any(not part for part in repository.split("/")):
        raise ReleaseGateError("GA_GIT_REMOTE_INVALID")
    return repository


@dataclass(frozen=True, slots=True)
class GitCliReadback:
    repository: str
    checkout: Path | None = None

    def __post_init__(self) -> None:
        _require_text(self.repository, "GA_REPOSITORY_INVALID")
        checkout = (
            self.checkout
            if self.checkout is not None
            else Path(__file__).resolve().parents[1]
        ).resolve()
        if not checkout.is_dir():
            raise ReleaseGateError("GA_GIT_CHECKOUT_INVALID")
        object.__setattr__(self, "checkout", checkout)
        try:
            remote = subprocess.check_output(
                ("git", "remote", "get-url", "origin"),
                cwd=checkout,
                text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise ReleaseGateError("GA_GIT_REMOTE_READBACK_REQUIRED") from error
        if _repository_from_remote(remote) != self.repository:
            raise ReleaseGateError("GA_GIT_REMOTE_REPOSITORY_MISMATCH")

    def _check_output(self, arguments: Sequence[str]) -> str:
        return subprocess.check_output(
            tuple(arguments),
            cwd=self.checkout,
            text=True,
        )

    def current_origin_main_sha(self) -> str:
        output = self._check_output(
            ("git", "rev-parse", "--verify", "refs/remotes/origin/main")
        ).strip()
        if _SHA.fullmatch(output) is None:
            raise ReleaseGateError("GA_MAIN_SHA_READBACK_INVALID")
        return output

    def tree_sha(self, commit: str) -> str:
        output = self._check_output(
            ("git", "rev-parse", "--verify", f"{commit}^{{tree}}")
        ).strip()
        if _SHA.fullmatch(output) is None:
            raise ReleaseGateError("GA_GIT_TREE_READBACK_INVALID")
        return output

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return (
            subprocess.run(
                ("git", "merge-base", "--is-ancestor", ancestor, descendant),
                cwd=self.checkout,
                check=False,
            ).returncode
            == 0
        )

    def changed_paths(self, ancestor: str, descendant: str) -> tuple[str, ...]:
        output = self._check_output(
            ("git", "diff", "--name-only", f"{ancestor}..{descendant}")
        )
        return tuple(line for line in output.splitlines() if line)

    def tag_subject(self, tag: str) -> tuple[str, str]:
        commit = self._check_output(
            ("git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
        ).strip()
        tree = self._check_output(
            ("git", "rev-parse", "--verify", f"refs/tags/{tag}^{{tree}}")
        ).strip()
        if _SHA.fullmatch(commit) is None or _SHA.fullmatch(tree) is None:
            raise ReleaseGateError("GA_TAG_READBACK_INVALID")
        return commit, tree

    def archive_tag(self, tag: str) -> bytes:
        return subprocess.check_output(
            ("git", "archive", "--format=tar", tag),
            cwd=self.checkout,
        )


def _snapshot(path: Path) -> SimpleNamespace:
    try:
        payload = _strict_canonical_json_loads(path.read_bytes())
    except (OSError, UnicodeError, ReleaseGateError) as error:
        raise ReleaseGateError("GA_RECEIPT_UNREADABLE") from error
    if not isinstance(payload, Mapping):
        raise ReleaseGateError("GA_RECEIPT_NOT_OBJECT")
    return SimpleNamespace(**payload)


def _load_release_gate_receipt(path: Path) -> ReleaseGateReceipt:
    try:
        payload = _strict_canonical_json_loads(path.read_bytes())
    except (OSError, UnicodeError, ReleaseGateError) as error:
        raise ReleaseGateError("GA_PRE_TAG_RECEIPT_INVALID") from error
    if not isinstance(payload, Mapping):
        raise ReleaseGateError("GA_PRE_TAG_RECEIPT_INVALID")
    try:
        return ReleaseGateReceipt.from_mapping(payload)
    except ReleaseGateError:
        raise
    except (TypeError, ValueError) as error:
        raise ReleaseGateError("GA_PRE_TAG_RECEIPT_INVALID") from error


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _required_path(value: Path | None, code: str) -> Path:
    if value is None:
        raise ReleaseGateError(code)
    return value


def verify_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--pre-tag", action="store_true")
    mode.add_argument("--post-release", action="store_true")
    parser.add_argument("--main-sha")
    parser.add_argument("--record", type=Path)
    parser.add_argument("--canary", type=Path)
    parser.add_argument("--activation", type=Path)
    parser.add_argument("--default-writer", type=Path)
    parser.add_argument("--ci-run", type=int)
    parser.add_argument("--repository", default="NOirBRight/github-work-orchestrator")
    parser.add_argument("--checkout", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--pre-tag-receipt", type=Path)
    parser.add_argument("--run-root", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.pre_tag:
            record = load_ga_release_record(
                _required_path(args.record, "GA_RELEASE_RECORD_REQUIRED")
            )
            ci_run_id = args.ci_run
            if ci_run_id is None:
                raise ReleaseGateError("GA_PRE_TAG_INPUTS_REQUIRED")
            git = GitCliReadback(args.repository, checkout=args.checkout)
            run_json = _strict_json_loads(
                subprocess.check_output(
                    (
                        "gh",
                        "run",
                        "view",
                        str(ci_run_id),
                        "--repo",
                        git.repository,
                        "--json",
                        "databaseId,headSha,conclusion",
                    ),
                    text=True,
                ),
                require_canonical=False,
            )
            if not isinstance(run_json, Mapping):
                raise ReleaseGateError("GA_EXACT_CI_REQUIRED")
            try:
                if int(run_json["databaseId"]) != ci_run_id:
                    raise ReleaseGateError("GA_EXACT_CI_REQUIRED")
                ci_head_sha = run_json["headSha"]
                ci_conclusion = run_json["conclusion"]
            except (KeyError, TypeError, ValueError) as error:
                raise ReleaseGateError("GA_EXACT_CI_REQUIRED") from error
            log = subprocess.check_output(
                (
                    "gh",
                    "run",
                    "view",
                    str(ci_run_id),
                    "--repo",
                    git.repository,
                    "--log",
                ),
                text=True,
            )
            ci = CiReadback(
                run_id=ci_run_id,
                head_sha=ci_head_sha,
                conclusion=ci_conclusion,
                pytest_pass_count=parse_pytest_count(log),
            )
            receipt = verify_pre_tag(
                record,
                main_sha=args.main_sha,
                canary=_snapshot(
                    _required_path(args.canary, "GA_CANARY_RECEIPT_REQUIRED")
                ),
                activation=_snapshot(
                    _required_path(args.activation, "GA_ACTIVATION_RECEIPT_REQUIRED")
                ),
                admission=_snapshot(
                    _required_path(
                        args.default_writer,
                        "GA_DEFAULT_WRITER_RECEIPT_REQUIRED",
                    )
                ),
                ci=ci,
                git=git,
            )
            _write_json(
                _required_path(args.output, "GA_OUTPUT_REQUIRED"),
                dataclasses.asdict(receipt),
            )
            return 0

        if args.post_release:
            tag = args.tag
            run_root = _required_path(args.run_root, "GA_RUN_ROOT_REQUIRED")
            if not isinstance(tag, str) or not tag:
                raise ReleaseGateError("GA_TAG_REQUIRED")
            output = _required_path(args.output, "GA_OUTPUT_REQUIRED")
            record = load_ga_release_record(
                _required_path(args.record, "GA_RELEASE_RECORD_REQUIRED")
            )
            pre_tag = _load_release_gate_receipt(
                _required_path(args.pre_tag_receipt, "GA_PRE_TAG_RECEIPT_REQUIRED")
            )
            if pre_tag.repository != args.repository:
                raise ReleaseGateError("GA_REPOSITORY_READBACK_INVALID")
            git = GitCliReadback(args.repository, checkout=args.checkout)
            tag_commit_sha, tag_tree_sha = git.tag_subject(tag)
            if (
                tag_commit_sha != pre_tag.tag_candidate_sha
                or tag_tree_sha != pre_tag.tag_candidate_tree_sha
            ):
                raise ReleaseGateError("GA_TAG_RELEASE_SUBJECT_MISMATCH")
            _verify_post_release_pre_tag_receipt(record, pre_tag, git)
            run_root.mkdir(parents=True, exist_ok=True)
            source = run_root / "tag-source"
            source.mkdir(parents=True, exist_ok=True)
            archive = git.archive_tag(tag_commit_sha)
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
                tar.extractall(source, filter="data")
            result = clean_install_and_smoke(source, run_root)
            _write_json(
                output,
                {
                    **dataclasses.asdict(result),
                    "tag": tag,
                    "tag_commit_sha": tag_commit_sha,
                    "tag_tree_sha": tag_tree_sha,
                    "pre_tag_tag_candidate_sha": pre_tag.tag_candidate_sha,
                    "pre_tag_tag_candidate_tree_sha": pre_tag.tag_candidate_tree_sha,
                },
            )
            return 0
        return 3
    except (
        ReleaseGateError,
        subprocess.CalledProcessError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        if isinstance(error, ReleaseGateError):
            print(f"error: {error.code}", file=sys.stderr)
        else:
            print(f"error: {error}", file=sys.stderr)
        return 2


RELEASE_CONTRACT = """# GWO V8 GA Release Contract

Schema: `gwo-v8-ga-release-record.v1`

The committed record freezes `evidence_base_sha`, `canary_target_sha`, the
repository/campaign/activation/default-writer identity, the
Canary/Activation/default-writer receipt digests, and the exact metadata path
allow-list. It deliberately contains no tag-candidate SHA, final metadata commit SHA,
CI run ID, or pytest count. The pre-tag command obtains those dynamic values
only from the current `origin/main` and exact CI readback, then writes a
separate `ReleaseGateReceipt`.

Every pre-tag receipt input is strict canonical JSON: duplicate names,
non-canonical bytes, and `NaN`/`Infinity` are rejected. Its complete payload
digest is recomputed; a claimed `receipt_digest` is never accepted by itself.
The gate also binds all readbacks to the committed repository, campaign,
activation, and default-writer identity; the default-v8 readback may carry
`campaign_key: null` because it is not campaign-scoped. Git readback runs in
the requested canonical checkout, proves its `origin` remote is the requested
repository, and rereads `origin/main` before success. The pre-tag receipt
freezes the candidate commit and tree. The renderer rejects dynamic SHA/CI
fields at any nesting or alias, cross-binds its input identities, and uses a
durable staged publication journal with flushed files, atomic replacement,
directory sync, and exact final readback. The post-release gate requires that
the supplied pre-tag receipt is bound to the static record and rechecks the
pre-tag ancestry, metadata-delta, and commit/tree invariants. It rejects a tag
whose peeled commit or tree differs, then archives by the captured immutable
commit SHA rather than the mutable tag name. Publication rejects symlink and
reparse output targets before any backup or replacement. It checks existing
package manifests before any regeneration, then installs both Skill packages
into temporary `.agents`, `.codex`, and `.claude` surfaces before smoking only
the public `start`, `advance`, and `inspect` operations.
"""


def write_release_contract(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RELEASE_CONTRACT, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(verify_main())
