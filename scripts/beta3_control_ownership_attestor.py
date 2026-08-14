"""Read-only control, ownership, Runtime, and static-input attestation.

This module is deliberately narrower than the production Guard.  It reads the
authoritative inputs once, turns them into exact current-main readback values,
and records the bytes and identities from which every value was derived.  The
replay Guard receives only those frozen values; it never imports this module's
live readers.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from urllib.parse import quote

from beta3_bootstrap_model import (
    AttemptIdentity,
    BootstrapError,
    ComponentObservation,
    FieldBinding,
    SourceObservation,
    SourceRecord,
    WriterAuthorityObservation,
)
from beta3_release_subject import ReleaseSubject
from gwo_v8._canonical import canonical_bytes, digest_bytes, digest_value, load_canonical_json
from gwo_v8.cutover_guard import (
    CompatibilityPathReadback,
    CutoverSubject,
    DurableStateReadback,
    PackageIdentity,
    PackageReadback,
    ProductionPathScanner,
    ReadOnlyPackageValidator,
    RuntimeConfigurationReader,
    RuntimePreflightReadback,
    WriterFenceReadback,
    OwnershipReadback,
)
from gwo_v8.plan_control_github import GitHubPlanRepository
from gwo_v8.runtime_gateway import ProfileMapping, RuntimeConfiguration
from gwo_v8.runtime_profile import RuntimeProfile
from gwo_v8.transition import WriterTransitionRecord


CONTROL_PATHS = (
    ".gwo-v8/writer-transition.json",
    ".gwo/v8/active-plan.json",
    ".gwo-v8/legacy-writer-fence.json",
)
RUNTIME_SELECTORS = (
    "coordinator",
    "worker",
    "recovery_worker",
    "review_primary",
    "review_strong",
)
RUNTIME_TIERS = ("light", "standard", "heavy", "frontier")
RUNTIME_ROLE_PROFILES = (
    "coordinator_auto",
    "reviewer_recovery",
    "reviewer_standard",
    "reviewer_strict",
)
LEGACY_FENCE_PATH = CONTROL_PATHS[2]
WRITER_PATH = CONTROL_PATHS[0]
ACTIVE_PLAN_PATH = CONTROL_PATHS[1]
STORE_SCHEMA = "gwo.v8.store.v1"
PRODUCTION_REPOSITORY = "NOirBRight/github-work-orchestrator"
PRODUCTION_REPOSITORY_ROOT = Path(r"D:\Workstation\github-work-orchestrator")
PRODUCTION_STORE = Path(
    r"C:\Users\noirb\.orch\v8\NOirBRight__github-work-orchestrator"
    r"\store-20260809T081500Z.sqlite3"
)
# The legacy fresh-Store identity above remains evidence data; only
# PRODUCTION_STORE.parent is the canonical location for a subject-bound Store.
PRODUCTION_STORE_GENERATION = "store:v8:production:20260809T081500Z"
PRODUCTION_STORE_SHA256 = "afff1078e7a65fb8acccde28fee78fab3cf2278db9dd6548f5ef96a882076b98"
PRODUCTION_RECEIPT = Path(
    r"D:\gwo-release-evidence\2026-08-09-gwo-v8-beta3-production-cutover"
    r"\fresh-store-exact-main-receipt.json"
)
PRODUCTION_RUNTIME_CONFIG = Path(r"C:\Users\noirb\.orch\config.json")
PRODUCTION_ROLLBACK_STORE = Path(
    r"C:\Users\noirb\.orch\v8\NOirBRight__github-work-orchestrator\store.sqlite3"
)
PRODUCTION_ROLLBACK_STORE_SHA256 = "1cc3f304044032fdab9569f8561b28220ecfd93e4efc35cf6bb2e492c1ca72b8"
PRODUCTION_PRIOR_STORE = Path(
    r"C:\Users\noirb\.orch\v8\NOirBRight__github-work-orchestrator"
    r"\store-20260809T023000Z.sqlite3"
)
PRODUCTION_PRIOR_STORE_SHA256 = "df2341d76eb2ab54110ac3e70ff137a93d05ffbb02352c61b654321dba188ed7"
PRODUCTION_RECEIPT_RUNBOOK_SHA256 = "329bade311df03d0b52a344ce7062c7c7984e2fa35b3d0fa9cbb5386a88e0c6c"
PRODUCTION_RECEIPT_SCHEMA_DIGEST = "69ac6babce5db564fcc60fc5dd97feb0635911e07955234098210ddd97a93aed"
PRODUCTION_INSTALL_ROOTS = tuple(
    Path(rf"C:\Users\noirb\{surface}\skills")
    for surface in (".agents", ".codex", ".claude")
)
PRODUCTION_PACKAGE_CONTENT_DIGESTS = (
    ("implement-gwo", "fcafa60645a2ea18408ec97369fdf5a01402a950b90e701fa2305624a1bfeaa9"),
    ("orchestrator", "1a10f3f19e6db951150bd97a40561de1093ae20ba07d8c503a244cd1f0123639"),
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_STORE_GENERATION = re.compile(r"^store:v8:[A-Za-z0-9][A-Za-z0-9._:-]*$")
_PRODUCTION_FRESH_STORE_NAME = re.compile(
    r"^store-[0-9]{8}T[0-9]{6}Z\.sqlite3$"
)
_DYNAMIC_SIDE_FILE = re.compile(
    r"^(?P<prefix>.+)\.(?P<token>[0-9a-fA-F-]{16,64})\.(?P<suffix>tmp|staging|partial|lock|wal|shm)$"
)


@dataclass(frozen=True)
class ControlOwnershipSourceSet:
    control: object
    runtime_registry: object
    runtime_config: object
    local_inputs: object


_GWO_V8_MODULE_PREFIX = "gwo_v8"


def _gwo_v8_source_root() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "skills"
        / "orchestrator"
        / "scripts"
        / "gwo_v8"
    )


def _gwo_v8_origin_path(module: object, label: str) -> Path:
    module_path = getattr(module, "__file__", None)
    module_spec = getattr(module, "__spec__", None)
    spec_path = getattr(module_spec, "origin", None)
    if type(module_path) is not str or type(spec_path) is not str:
        raise BootstrapError(
            "UNSAFE_SOURCE_CAPABILITY",
            f"{label} has no exact source origin",
        )
    try:
        path = Path(module_path).resolve(strict=True)
        origin = Path(spec_path).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise BootstrapError(
            "UNSAFE_SOURCE_CAPABILITY",
            f"{label} source origin is unavailable",
        ) from error
    if path != origin:
        raise BootstrapError(
            "UNSAFE_SOURCE_CAPABILITY",
            f"{label} file and spec origins differ",
        )
    return path


def _validate_gwo_v8_provenance() -> None:
    """Reject a preloaded V8 package from outside this attestor checkout."""

    expected_root = _gwo_v8_source_root()
    package = sys.modules.get(_GWO_V8_MODULE_PREFIX)
    if package is None:
        raise BootstrapError(
            "UNSAFE_SOURCE_CAPABILITY",
            "gwo_v8 package is not loaded from the attestor checkout",
        )
    package_path = _gwo_v8_origin_path(package, "gwo_v8 package")
    if package_path != expected_root / "__init__.py":
        raise BootstrapError(
            "UNSAFE_SOURCE_CAPABILITY",
            "gwo_v8 package origin is not canonical",
        )
    package_paths = getattr(package, "__path__", None)
    if type(package_paths) not in (list, tuple) or len(package_paths) != 1:
        raise BootstrapError(
            "UNSAFE_SOURCE_CAPABILITY",
            "gwo_v8 package path is not exact",
        )
    try:
        observed_package_root = Path(package_paths[0]).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise BootstrapError(
            "UNSAFE_SOURCE_CAPABILITY",
            "gwo_v8 package path is unavailable",
        ) from error
    if observed_package_root != expected_root:
        raise BootstrapError(
            "UNSAFE_SOURCE_CAPABILITY",
            "gwo_v8 package path is not canonical",
        )

    for name, module in tuple(sys.modules.items()):
        if name != _GWO_V8_MODULE_PREFIX and not name.startswith(
            f"{_GWO_V8_MODULE_PREFIX}."
        ):
            continue
        if module is None:
            raise BootstrapError(
                "UNSAFE_SOURCE_CAPABILITY",
                f"{name} is not an exact loaded module",
            )
        origin = _gwo_v8_origin_path(module, name)
        try:
            relative = origin.relative_to(expected_root)
        except ValueError as error:
            raise BootstrapError(
                "UNSAFE_SOURCE_CAPABILITY",
                f"{name} module origin is not canonical",
            ) from error
        if not relative.parts:
            raise BootstrapError(
                "UNSAFE_SOURCE_CAPABILITY",
                f"{name} module origin is not a source file",
            )


@dataclass(frozen=True)
class _ControlRef:
    repository: str
    ref: str
    commit_oid: str
    object_type: str


@dataclass(frozen=True)
class _Blob:
    repository: str
    ref: str
    commit_oid: str
    path: str
    blob_sha: str
    object_type: str
    encoding: str
    size: int
    content: bytes


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    content: bytes
    identity: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _StoreFacts:
    store_record: SourceRecord
    receipt_record: SourceRecord
    durable: DurableStateReadback
    active_admissions: tuple[str, ...]
    active_attempts: tuple[str, ...]
    integration_lease_owner: str | None
    resource_claims: tuple[str, ...]
    runtime_links: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _RawFileObservation:
    payload: bytes
    identity: tuple[tuple[str, str], ...]
    locator: str
    complete: bool = True


class _GitHubControlSource:
    """A tiny GitHub read adapter using only the supplied command runner."""

    def __init__(self, command_runner: Callable[[tuple[str, ...]], bytes]) -> None:
        self._command_runner = command_runner

    def read_ref(self, repository: str, branch: str) -> _ControlRef:
        payload = self._run(
            (
                "gh",
                "api",
                "--method",
                "GET",
                f"repos/{repository}/git/ref/heads/{branch}",
            )
        )
        value = _decode_json_response(payload, "CONTROL_REF_UNAVAILABLE")
        if type(value) is not dict:
            _fail("CONTROL_REF_UNAVAILABLE", "GitHub ref response is not an object")
        ref_name = value.get("ref")
        response_url = value.get("url")
        ref = value.get("object")
        if type(ref) is not dict or type(ref.get("sha")) is not str:
            _fail("CONTROL_REF_UNAVAILABLE", "GitHub ref response has no exact OID")
        oid = ref["sha"]
        expected_ref = f"refs/heads/{branch}"
        expected_url = f"https://api.github.com/repos/{repository}/git/refs/heads/{branch}"
        if (
            _HEX40.fullmatch(oid) is None
            or ref_name != expected_ref
            or response_url != expected_url
            or ref.get("type") != "commit"
            or ref.get("url")
            != f"https://api.github.com/repos/{repository}/git/commits/{oid}"
        ):
            _fail("CONTROL_REF_UNAVAILABLE", "GitHub ref OID is not a commit identity")
        return _ControlRef(
            repository=repository,
            ref=ref_name,
            commit_oid=oid,
            object_type=ref["type"],
        )

    def read_at_oid(self, repository: str, oid: str, path: str) -> _Blob | None:
        payload = self._run(
            (
                "gh",
                "api",
                "--method",
                "GET",
                f"repos/{repository}/contents/{path}",
                "-f",
                f"ref={oid}",
            )
        )
        value = _decode_json_response(payload, "CONTROL_BLOB_UNAVAILABLE")
        if value is None:
            return None
        if type(value) is not dict:
            _fail("CONTROL_BLOB_UNAVAILABLE", "GitHub blob response is not an object")
        encoded = value.get("content")
        blob_sha = value.get("sha")
        if type(encoded) is not str or type(blob_sha) is not str or not blob_sha:
            _fail("CONTROL_BLOB_UNAVAILABLE", "GitHub blob response is incomplete")
        try:
            content = base64.b64decode(encoded.replace("\n", ""), validate=True)
        except (ValueError, TypeError) as error:
            raise BootstrapError(
                "CONTROL_BLOB_UNAVAILABLE", "GitHub blob content is not exact base64"
            ) from error
        if (
            value.get("path") != path
            or value.get("type") != "file"
            or value.get("encoding") != "base64"
            or type(value.get("size")) is not int
            or value["size"] != len(content)
            or value.get("url")
            != f"https://api.github.com/repos/{repository}/contents/{path}?ref={oid}"
            or value.get("git_url")
            != f"https://api.github.com/repos/{repository}/git/blobs/{blob_sha}"
            or _HEX40.fullmatch(blob_sha) is None
            or _git_blob_sha(content) != blob_sha
        ):
            _fail("CONTROL_BLOB_UNAVAILABLE", "GitHub blob response identity is not exact")
        return _Blob(
            repository=repository,
            ref=oid,
            commit_oid=oid,
            path=path,
            blob_sha=blob_sha,
            object_type=value["type"],
            encoding=value["encoding"],
            size=value["size"],
            content=content,
        )

    def _run(self, command: tuple[str, ...]) -> bytes:
        try:
            result = self._command_runner(command)
        except BootstrapError:
            raise
        except Exception as error:
            raise BootstrapError("CONTROL_SOURCE_UNAVAILABLE", "GitHub read failed") from error
        if type(result) is not bytes:
            _fail("CONTROL_SOURCE_UNAVAILABLE", "command runner did not return exact bytes")
        return result


class _RuntimeRegistrySource:
    def __init__(
        self,
        command_runner: Callable[[tuple[str, ...]], bytes],
        producer_sha256: str,
    ) -> None:
        self._command_runner = command_runner
        self._producer_sha256 = producer_sha256

    def read(self, repository: str) -> SourceObservation:
        command = (
            "paseo",
            "runtime",
            "registry",
            "--repository",
            repository,
            "--json",
        )
        try:
            payload = self._command_runner(command)
        except Exception as error:
            raise BootstrapError(
                "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE", "Runtime registry read failed"
            ) from error
        if type(payload) is not bytes:
            _fail(
                "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE",
                "Runtime registry command did not return exact bytes",
            )
        try:
            value = load_canonical_json(payload)
        except Exception as error:
            raise BootstrapError(
                "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE",
                "Runtime registry response is not canonical JSON",
            ) from error
        canonical = canonical_bytes(value)
        if canonical != payload:
            _fail(
                "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE",
                "Runtime registry bytes are not canonical",
            )
        record = _source_record(
            role="runtime.registry",
            locator=f"runtime-registry://{repository}",
            repository=repository,
            read_mode="COMPLETE_OBSERVATION",
            identity={"observation_digest": digest_bytes(canonical)},
            payload=canonical,
            producer_sha256=self._producer_sha256,
        )
        return SourceObservation(record=record, canonical_payload=canonical, complete=True)


class _RuntimeConfigSource:
    def __init__(self, producer_sha256: str, repository: str) -> None:
        self._producer_sha256 = producer_sha256
        self._repository = repository

    def read(self, path: Path) -> SourceObservation:
        path = _absolute_local_path(Path(path))
        snapshot = _read_file_snapshot(path, "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE")
        record = _source_record(
            role="runtime.config",
            locator=str(path),
            repository=self._repository,
            read_mode="EXACT_FILE",
            identity=dict(snapshot.identity),
            payload=snapshot.content,
            producer_sha256=self._producer_sha256,
        )
        return SourceObservation(record=record, canonical_payload=snapshot.content, complete=True)


class _LocalInputsSource:
    def __init__(
        self,
        command_runner: Callable[[tuple[str, ...]], bytes],
        producer_sha256: str,
    ) -> None:
        self._command_runner = command_runner
        self._producer_sha256 = producer_sha256

    def read(self, config: object, subject: CutoverSubject) -> SourceObservation:
        root = _canonical_local_directory(
            Path(config.repository_root),
            "STATIC_INPUT_SOURCE_UNAVAILABLE",
        )

        def read_oid(revision: str, label: str) -> str:
            command = ("git", "-C", str(root), "rev-parse", "--verify", revision)
            try:
                raw_oid = self._command_runner(command)
            except Exception as error:
                raise BootstrapError(
                    "STATIC_INPUT_SOURCE_UNAVAILABLE",
                    f"checkout {label} identity read failed",
                ) from error
            if type(raw_oid) is not bytes:
                _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", f"checkout {label} identity is not bytes")
            try:
                oid = raw_oid.decode("ascii")
            except UnicodeDecodeError as error:
                raise BootstrapError(
                    "STATIC_INPUT_SOURCE_UNAVAILABLE",
                    f"checkout {label} identity is not ASCII",
                ) from error
            if oid.endswith("\r\n"):
                oid = oid[:-2]
            elif oid.endswith("\n"):
                oid = oid[:-1]
            if _HEX40.fullmatch(oid) is None:
                _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", f"checkout {label} identity is malformed")
            return oid

        commit = read_oid("HEAD", "commit")
        tree = read_oid("HEAD^{tree}", "tree")
        status_command = (
            "git",
            "-C",
            str(root),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        )
        try:
            status = self._command_runner(status_command)
        except Exception as error:
            raise BootstrapError(
                "STATIC_INPUT_SOURCE_UNAVAILABLE",
                "checkout status identity read failed",
            ) from error
        if type(status) is not bytes:
            _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "checkout status is not bytes")
        unexpected = _unexpected_status_records(status)
        if unexpected:
            _fail(
                "STATIC_INPUT_SOURCE_UNAVAILABLE",
                "unexpected Git status: " + "; ".join(unexpected),
            )
        files = []
        checkout_paths = _checkout_source_files(root, subject)
        checkout_snapshots = _checkout_source_snapshots(root, subject)
        if tuple(snapshot.path for snapshot in checkout_snapshots) != checkout_paths:
            _fail(
                "STATIC_INPUT_SOURCE_UNAVAILABLE",
                "checkout file enumeration changed before held readback",
            )
        for snapshot in checkout_snapshots:
            files.append(
                {
                    "relative_path": snapshot.path.relative_to(root).as_posix(),
                    "byte_sha256": digest_bytes(snapshot.content),
                }
            )
        value = {
            "repository_root": str(root),
            "commit_oid": commit,
            "git_tree_oid": tree,
            "git_status_sha256": digest_bytes(status),
            "files": files,
        }
        payload = canonical_bytes(value)
        record = _source_record(
            role="local.inputs",
            locator=f"local-checkout://{root}",
            repository=subject.repository,
            read_mode="EXACT_GIT_SNAPSHOT",
            identity={
                "repository_root": str(root),
                "commit_oid": commit,
                "git_tree_oid": tree,
                "git_status_sha256": digest_bytes(status),
                "file_set_digest": digest_value(files),
                "observation_digest": digest_bytes(payload),
            },
            payload=payload,
            producer_sha256=self._producer_sha256,
        )
        return SourceObservation(record=record, canonical_payload=payload, complete=True)


def _fail(code: str, detail: str) -> None:
    raise BootstrapError(code, detail)


def _unexpected_status_records(status: bytes) -> tuple[str, ...]:
    try:
        text = status.decode("utf-8")
    except UnicodeDecodeError:
        return ("<invalid utf-8 Git status>",)
    unexpected: list[str] = []
    for record in text.split("\0"):
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            unexpected.append(record)
            continue
        path = record[3:]
        if os.name == "nt":
            path = path.replace("\\", "/")
        elif os.name != "posix":
            unexpected.append(record)
            continue
        if record[:2] != "??" or not (
            path == ".codex-tmp" or path.startswith(".codex-tmp/")
        ):
            unexpected.append(record)
    return tuple(unexpected)


def _require_digest(value: object, name: str, code: str) -> str:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        _fail(code, f"{name} is not a lowercase SHA-256")
    return value


def _require_text(value: object, name: str, code: str) -> str:
    if type(value) is not str or not value:
        _fail(code, f"{name} must be non-empty exact text")
    return value


def _decode_json_response(payload: bytes, code: str) -> object:
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise BootstrapError(code, "JSON source response is unavailable") from error


def _canonical_payload(value: object, code: str) -> bytes:
    try:
        return canonical_bytes(value)
    except Exception as error:
        raise BootstrapError(code, "source value is not canonically encodable") from error


def _identity_pairs(values: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for key, value in values.items():
        if type(key) is not str or not key or type(value) is not str or not value:
            _fail("SOURCE_RECORD_INVALID", "source identity is not exact text")
        pairs.append((key, value))
    return tuple(sorted(pairs))


def _source_record(
    *,
    role: str,
    locator: str,
    repository: str,
    read_mode: str,
    identity: Mapping[str, object],
    payload: bytes,
    producer_sha256: str,
    readback_digest: str | None = None,
) -> SourceRecord:
    if type(payload) is not bytes:
        _fail("SOURCE_RECORD_INVALID", "source payload is not exact bytes")
    try:
        return SourceRecord(
            role=role,
            locator=locator,
            repository=repository,
            read_mode=read_mode,
            identity=_identity_pairs(identity),
            content_sha256=digest_bytes(payload),
            readback_digest=readback_digest,
            producer_sha256=producer_sha256,
        )
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError("SOURCE_RECORD_INVALID", "source record is malformed") from error


def _file_identity(path: Path, stat_result: os.stat_result) -> tuple[tuple[str, str], ...]:
    return _identity_pairs(
        {
            "byte_sha256": digest_bytes(path.read_bytes()),
            "inode": str(stat_result.st_ino),
            "mtime_ns": str(stat_result.st_mtime_ns),
            "path": str(_absolute_local_path(path)),
            "size": str(stat_result.st_size),
        }
    )


def _held_file_bytes(path: Path, code: str) -> tuple[bytes, Mapping[str, object]]:
    """Read one local file through the runner's no-follow held-handle boundary."""

    try:
        from run_beta3_live_guard import _bound_bytes

        content, identity = _bound_bytes(Path(path), code)
        if type(content) is not bytes or type(identity) is not dict:
            raise TypeError("held local file boundary returned the wrong exact types")
        return content, identity
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError(code, f"local file is unavailable: {path}") from error


def _file_snapshot_from_held(
    path: Path,
    content: bytes,
    held_identity: Mapping[str, object],
) -> _FileSnapshot:
    inode = held_identity.get("st_ino", held_identity.get("file_id"))
    if inode is None:
        raise OSError("held file identity has no inode")
    mtime_ns = held_identity.get("st_mtime_ns")
    size = held_identity.get("st_size")
    if mtime_ns is None or size is None:
        raise OSError("held file identity is incomplete")
    identity = _identity_pairs(
        {
            "byte_sha256": digest_bytes(content),
            "inode": str(inode),
            "mtime_ns": str(mtime_ns),
            "path": str(_absolute_local_path(path)),
            "size": str(size),
        }
    )
    return _FileSnapshot(
        path=_absolute_local_path(path),
        content=content,
        identity=identity,
    )


def _read_file_snapshot(path: Path, code: str) -> _FileSnapshot:
    path = Path(path)
    try:
        content, held_identity = _held_file_bytes(path, code)
        return _file_snapshot_from_held(path, content, held_identity)
    except BootstrapError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise BootstrapError(code, f"local file is unavailable: {path}") from error


def _source_error_code(role: str) -> str:
    return {
        "runtime.registry": "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE",
        "runtime.config": "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE",
        "local.inputs": "STATIC_INPUT_SOURCE_UNAVAILABLE",
    }.get(role, f"{role.upper().replace('.', '_')}_SOURCE_UNAVAILABLE")


def _source_observation(
    value: object,
    *,
    role: str,
    repository: str,
    producer_sha256: str,
    default_locator: str,
    default_read_mode: str,
) -> SourceObservation:
    error_code = _source_error_code(role)
    if type(value) is SourceObservation:
        if (
            type(value.record) is not SourceRecord
            or type(value.canonical_payload) is not bytes
            or value.record.repository != repository
            or value.record.producer_sha256 != producer_sha256
            or type(value.complete) is not bool
            or not value.complete
        ):
            _fail(error_code, "source identity or completeness is not exact")
        if value.record.role != role or value.record.read_mode != default_read_mode:
            _fail(error_code, "source role or read mode is not the fixed contract")
        if value.record.locator != default_locator:
            _fail(error_code, "source locator is not the fixed provenance contract")
        identity = dict(value.record.identity)
        observed_digest = digest_bytes(value.canonical_payload)
        if value.record.content_sha256 != observed_digest:
            _fail(error_code, "source record content hash is not bound to its bytes")
        for key in ("observation_digest", "byte_sha256", "sha256"):
            if key in identity and identity[key] != observed_digest:
                _fail(error_code, "source identity is not bound to its bytes")
        if role == "runtime.registry" and identity != {
            "observation_digest": observed_digest
        }:
            _fail(error_code, "Runtime registry observation provenance is absent")
        return value
    _fail(error_code, "source did not return an exact complete SourceObservation")


def compare_complete_observations(
    first: SourceObservation,
    second: SourceObservation,
) -> SourceObservation:
    """Purely compare two already-captured complete source observations."""

    for observation in (first, second):
        if (
            type(observation) is not SourceObservation
            or type(observation.record) is not SourceRecord
            or type(observation.canonical_payload) is not bytes
            or observation.complete is not True
            or observation.record.content_sha256
            != digest_bytes(observation.canonical_payload)
        ):
            _fail("LIVE_INPUT_DRIFT", "complete source observation is malformed")
    if first != second:
        _fail("LIVE_INPUT_DRIFT", "complete source observation identity changed")
    return first


def _validate_checkout_observation(
    observation: SourceObservation,
    config: object,
    subject: CutoverSubject,
) -> SourceRecord:
    code = "STATIC_INPUT_SOURCE_UNAVAILABLE"
    if (
        type(observation) is not SourceObservation
        or type(observation.record) is not SourceRecord
        or type(observation.canonical_payload) is not bytes
        or observation.complete is not True
    ):
        _fail(code, "checkout source is not one complete observation")
    root = _canonical_local_directory(
        Path(config.repository_root),
        "STATIC_INPUT_SOURCE_UNAVAILABLE",
    )
    expected = {
        "repository_root": str(root),
        "commit_oid": config.merged_main_sha,
        "git_tree_oid": config.merged_main_git_tree,
    }
    try:
        value = load_canonical_json(observation.canonical_payload)
        identity = dict(observation.record.identity)
    except Exception as error:
        raise BootstrapError(code, "checkout source identity is malformed") from error
    status_digest = value.get("git_status_sha256") if type(value) is dict else None
    if not _is_digest(status_digest):
        _fail(code, "checkout Git status identity is malformed")
    files = value.get("files") if type(value) is dict else None
    if (
        type(files) is not list
        or any(
            type(item) is not dict
            or set(item) != {"relative_path", "byte_sha256"}
            or type(item["relative_path"]) is not str
            or not item["relative_path"]
            or Path(item["relative_path"]).is_absolute()
            or ".." in Path(item["relative_path"]).parts
            or type(item["byte_sha256"]) is not str
            or _HEX64.fullmatch(item["byte_sha256"]) is None
            for item in files
        )
        or [item["relative_path"] for item in files]
        != sorted({item["relative_path"] for item in files})
    ):
        _fail(code, "checkout source file identity set is malformed")
    expected_value = {
        **expected,
        "git_status_sha256": status_digest,
        "files": files,
    }
    expected_identity = {
        **expected,
        "git_status_sha256": status_digest,
        "file_set_digest": digest_value(files),
        "observation_digest": digest_bytes(observation.canonical_payload),
    }
    if (
        type(value) is not dict
        or value != expected_value
        or canonical_bytes(value) != observation.canonical_payload
        or observation.record.role != "local.inputs"
        or observation.record.repository != subject.repository
        or observation.record.read_mode != "EXACT_GIT_SNAPSHOT"
        or observation.record.locator != f"local-checkout://{root}"
        or observation.record.content_sha256 != digest_bytes(observation.canonical_payload)
        or identity != expected_identity
    ):
        _fail(code, "checkout commit, tree, or root identity changed")
    return observation.record


def _read_source(
    source: object,
    method: str,
    args: tuple[object, ...],
    *,
    role: str,
    repository: str,
    producer_sha256: str,
    default_locator: str,
    default_read_mode: str,
) -> SourceObservation:
    reader = getattr(source, method, None)
    if not callable(reader):
        _fail("UNSAFE_SOURCE_CAPABILITY", f"{role} has no read method")
    try:
        value = reader(*args)
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError(
            _source_error_code(role),
            f"{role} source read failed",
        ) from error
    return _source_observation(
        value,
        role=role,
        repository=repository,
        producer_sha256=producer_sha256,
        default_locator=default_locator,
        default_read_mode=default_read_mode,
    )


def _git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def _blob(
    value: object,
    code: str,
    *,
    repository: str,
    oid: str,
    path: str,
) -> tuple[bytes, str]:
    if value is None:
        _fail(code, "control blob is missing")
    content = value if type(value) is bytes else getattr(value, "content", None)
    blob_sha = getattr(value, "blob_sha", getattr(value, "sha", None))
    if (
        type(content) is not bytes
        or getattr(value, "repository", None) != repository
        or getattr(value, "ref", None) != oid
        or getattr(value, "commit_oid", None) != oid
        or getattr(value, "path", None) != path
        or getattr(value, "object_type", None) != "file"
        or getattr(value, "encoding", None) != "base64"
        or type(getattr(value, "size", None)) is not int
        or getattr(value, "size", None) != len(content)
        or type(blob_sha) is not str
        or _HEX40.fullmatch(blob_sha) is None
        or _git_blob_sha(content) != blob_sha
    ):
        _fail(code, "control blob lacks exact bytes or blob identity")
    return content, blob_sha


def _load_canonical_object(payload: bytes, code: str) -> object:
    try:
        return load_canonical_json(payload)
    except Exception as error:
        raise BootstrapError(code, "control bytes are not canonical JSON") from error


def _exact_object(value: object, keys: set[str], code: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        _fail(code, "control object has unknown or missing keys")
    return value


def _is_digest(value: object) -> bool:
    return type(value) is str and _HEX64.fullmatch(value) is not None


def _is_nonempty(value: object) -> bool:
    return type(value) is str and bool(value)


class _WriterLedgerValidator:
    """Expose the current-main validator methods without a repository object."""

    _validate_writer_record = GitHubPlanRepository._validate_writer_record
    _validate_writer_edge = GitHubPlanRepository._validate_writer_edge
    _validate_writer_blocked = GitHubPlanRepository._validate_writer_blocked
    _writer_fields_equal = staticmethod(GitHubPlanRepository._writer_fields_equal)

    def __init__(self, repository: str, writer_generation: str) -> None:
        self.repository = repository
        self.writer_generation = writer_generation


def _activation_ledger(
    payload: bytes,
    *,
    repository: str,
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    value = _exact_object(
        _load_canonical_object(payload, "WRITER_FENCE_SOURCE_UNAVAILABLE"),
        {"schema_version", "repository", "active_plan_digest", "receipts"},
        "WRITER_FENCE_SOURCE_UNAVAILABLE",
    )
    if (
        value["schema_version"] != 1
        or value["repository"] != repository
        or not _is_digest(value["active_plan_digest"])
        or type(value["receipts"]) is not list
    ):
        _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "active-plan ledger identity is invalid")
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
    receipts: dict[str, dict[str, object]] = {}
    by_plan: dict[str, dict[str, object]] = {}
    roots: list[str] = []
    successors: set[str] = set()
    for raw in value["receipts"]:
        receipt = _exact_object(raw, receipt_fields, "WRITER_FENCE_SOURCE_UNAVAILABLE")
        predecessor = receipt["expected_previous_digest"]
        if (
            receipt["schema_version"] != 1
            or receipt["repository"] != repository
            or not _is_nonempty(receipt["writer_generation"])
            or not _is_nonempty(receipt["activation_id"])
            or not _is_digest(receipt["plan_digest"])
            or (predecessor is not None and not _is_digest(predecessor))
            or not _is_nonempty(receipt["plan_record_ref"])
            or not _is_nonempty(receipt["created_at"])
            or receipt["activation_id"] in receipts
            or receipt["plan_digest"] in by_plan
        ):
            _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "Activation Receipt is invalid")
        receipts[receipt["activation_id"]] = receipt
        by_plan[receipt["plan_digest"]] = receipt
    active_matches = [
        receipt
        for receipt in receipts.values()
        if receipt["plan_digest"] == value["active_plan_digest"]
    ]
    if len(active_matches) != 1:
        _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "active Activation Receipt is ambiguous")
    for receipt in receipts.values():
        predecessor = receipt["expected_previous_digest"]
        if predecessor is None:
            roots.append(receipt["plan_digest"])
        elif predecessor not in by_plan or predecessor in successors:
            _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "Activation predecessor lineage is invalid")
        else:
            successors.add(predecessor)
    if len(roots) != 1:
        _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "Activation lineage has multiple roots")
    seen: set[str] = set()
    cursor = active_matches[0]
    while True:
        plan_digest = cursor["plan_digest"]
        if plan_digest in seen:
            _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "Activation lineage contains a cycle")
        seen.add(plan_digest)
        predecessor = cursor["expected_previous_digest"]
        if predecessor is None:
            break
        cursor = by_plan[predecessor]
    if seen != set(by_plan):
        _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "Activation lineage contains an orphan")
    return value, receipts


def _decode_writer_records(
    payload: bytes,
    *,
    repository: str,
    source_generation: str,
    target_generation: str,
    active_plan: dict[str, object],
    receipts: Mapping[str, dict[str, object]],
) -> tuple[WriterTransitionRecord, WriterTransitionRecord, tuple[WriterTransitionRecord, ...]]:
    value = _exact_object(
        _load_canonical_object(payload, "WRITER_FENCE_SOURCE_UNAVAILABLE"),
        {"schema_version", "current", "records"},
        "WRITER_FENCE_SOURCE_UNAVAILABLE",
    )
    current = _exact_object(
        value["current"],
        {"repository", "writer_generation", "record_id"},
        "WRITER_FENCE_SOURCE_UNAVAILABLE",
    )
    if value["schema_version"] != 1 or type(value["records"]) is not list:
        _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "Writer ledger schema is invalid")
    current_id = current["record_id"]
    if (
        current["repository"] != repository
        or not _is_nonempty(current_id)
        or current_id == "initial-writer"
        or current["writer_generation"] != source_generation
    ):
        _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "Writer current pointer is not explicit V6.1 authority")
    record_fields = set(WriterTransitionRecord.__dataclass_fields__)
    decoded: list[WriterTransitionRecord] = []
    try:
        for raw in value["records"]:
            item = _exact_object(raw, record_fields, "WRITER_FENCE_SOURCE_UNAVAILABLE")
            refs = item["canary_evidence_refs"]
            if type(refs) is not list:
                raise ValueError("canary evidence references")
            decoded.append(WriterTransitionRecord(**{**item, "canary_evidence_refs": tuple(refs)}))
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError(
            "WRITER_FENCE_SOURCE_UNAVAILABLE", "Writer transition record is malformed"
        ) from error
    validator = _WriterLedgerValidator(repository, target_generation)
    by_id: dict[str, WriterTransitionRecord] = {}
    previous: WriterTransitionRecord | None = None
    transitionable: list[WriterTransitionRecord] = []
    try:
        for record in decoded:
            if record.record_id in by_id:
                _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "Writer ledger repeats a record identity")
            by_id[record.record_id] = record
            validator._validate_writer_record(record, receipts)
            if record.status == "blocked":
                validator._validate_writer_blocked(previous, record)
            else:
                validator._validate_writer_edge(previous, record, receipts)
                previous = record
                transitionable.append(record)
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError(
            "WRITER_FENCE_SOURCE_UNAVAILABLE", "Writer transition lineage is invalid"
        ) from error
    selected = by_id.get(current_id)
    if selected is None or previous is None or selected is not previous:
        _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "Writer current pointer does not select the latest authority")
    if (
        selected.kind != "rollback"
        or selected.status != "rolled_back"
        or selected.writer_generation != source_generation
        or selected.activation_id is not None
        or selected.plan_digest != active_plan["active_plan_digest"]
        or current["writer_generation"] != selected.writer_generation
    ):
        _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "Writer authority is not the explicit V6.1 rollback")
    active_receipts = [
        receipt
        for receipt in receipts.values()
        if receipt["plan_digest"] == selected.plan_digest
    ]
    if any(receipt["writer_generation"] != target_generation for receipt in receipts.values()):
        _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "Activation Receipt generation is not V8")
    if len(active_receipts) != 1 or active_receipts[0]["writer_generation"] != target_generation:
        _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "Writer rollback is not bound to the active V8 plan")
    return selected, active_receipts[0], tuple(decoded)


def _legacy_fence(payload: bytes, repository: str) -> bool:
    value = _exact_object(
        _load_canonical_object(payload, "WRITER_FENCE_SOURCE_UNAVAILABLE"),
        {"schema_version", "repository", "stopped", "events"},
        "WRITER_FENCE_SOURCE_UNAVAILABLE",
    )
    events = value["events"]
    if (
        value["schema_version"] != 1
        or value["repository"] != repository
        or type(value["stopped"] ) is not bool
        or type(events) is not list
    ):
        _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "legacy Writer fence schema is invalid")
    action_operations: dict[str, str] = {}
    for event in events:
        item = _exact_object(
            event,
            {"action_key", "operation"},
            "WRITER_FENCE_SOURCE_UNAVAILABLE",
        )
        key = item["action_key"]
        operation = item["operation"]
        if not _is_nonempty(key) or operation not in {"stop", "restore"}:
            _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "legacy Writer fence event is invalid")
        prior = action_operations.setdefault(key, operation)
        if prior != operation:
            _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "legacy Writer action key was reused")
    expected = bool(events and events[-1]["operation"] == "stop")
    if value["stopped"] is not expected or value["stopped"] is not True:
        _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "legacy Writer fence is not finally stopped")
    return True


def _read_control(
    source: object,
    *,
    subject: CutoverSubject,
    attempt: AttemptIdentity,
) -> tuple[WriterFenceReadback, WriterAuthorityObservation, list[SourceRecord]]:
    read_ref = getattr(source, "read_ref", None)
    read_at_oid = getattr(source, "read_at_oid", None)
    if not callable(read_ref) or not callable(read_at_oid):
        _fail("UNSAFE_SOURCE_CAPABILITY", "control source lacks the fixed-OID read surface")
    try:
        ref_value = read_ref(subject.repository, subject.control_branch)
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError("CONTROL_REF_UNAVAILABLE", "control branch ref read failed") from error
    expected_ref = f"refs/heads/{subject.control_branch}"
    oid = getattr(ref_value, "commit_oid", None)
    if (
        getattr(ref_value, "repository", None) != subject.repository
        or getattr(ref_value, "ref", None) != expected_ref
        or getattr(ref_value, "object_type", None) != "commit"
        or type(oid) is not str
        or _HEX40.fullmatch(oid) is None
    ):
        _fail("CONTROL_REF_UNAVAILABLE", "control branch ref is not one exact commit OID")
    blobs: dict[str, tuple[bytes, str]] = {}
    for path in CONTROL_PATHS:
        try:
            value = read_at_oid(subject.repository, oid, path)
        except BootstrapError:
            raise
        except Exception as error:
            raise BootstrapError("CONTROL_BLOB_UNAVAILABLE", f"control read failed: {path}") from error
        blobs[path] = _blob(
            value,
            "WRITER_FENCE_SOURCE_UNAVAILABLE"
            if path in {WRITER_PATH, ACTIVE_PLAN_PATH, LEGACY_FENCE_PATH}
            else "CONTROL_BLOB_UNAVAILABLE",
            repository=subject.repository,
            oid=oid,
            path=path,
        )
    writer_payload, writer_blob = blobs[WRITER_PATH]
    active_payload, active_blob = blobs[ACTIVE_PLAN_PATH]
    legacy_payload, legacy_blob = blobs[LEGACY_FENCE_PATH]
    active_plan, receipts = _activation_ledger(active_payload, repository=subject.repository)
    selected, active_receipt, records = _decode_writer_records(
        writer_payload,
        repository=subject.repository,
        source_generation=subject.source_writer_generation,
        target_generation=subject.target_writer_generation,
        active_plan=active_plan,
        receipts=receipts,
    )
    if active_receipt["plan_digest"] != selected.plan_digest:
        _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "Writer and active-plan bindings differ")
    _legacy_fence(legacy_payload, subject.repository)
    control_binding = {
        "repository": subject.repository,
        "branch": subject.control_branch,
        "commit_oid": oid,
        "writer": {
            "path": WRITER_PATH,
            "blob_oid": writer_blob,
            "byte_sha256": digest_bytes(writer_payload),
            "record_id": selected.record_id,
            "record_digests": [
                digest_value(record.__dict__) for record in records
            ],
        },
        "active_plan": {
            "path": ACTIVE_PLAN_PATH,
            "blob_oid": active_blob,
            "byte_sha256": digest_bytes(active_payload),
            "active_plan_digest": active_plan["active_plan_digest"],
        },
        "legacy_fence": {
            "path": LEGACY_FENCE_PATH,
            "blob_oid": legacy_blob,
            "byte_sha256": digest_bytes(legacy_payload),
        },
    }
    control_digest = digest_value(control_binding)
    body = {
        "repository": subject.repository,
        "writer_generation": subject.source_writer_generation,
        "authority_state": "authoritative",
        "record_id": selected.record_id,
        "activation_id": None,
        "control_ref_digest": control_digest,
    }
    fence = WriterFenceReadback(**body, readback_digest=digest_value(body))
    source_records = [
        _source_record(
            role="control.ref",
            locator=f"github://{subject.repository}/{subject.control_branch}",
            repository=subject.repository,
            read_mode="GET_REF",
            identity={"branch": subject.control_branch, "commit_oid": oid},
            payload=oid.encode("ascii"),
            producer_sha256=attempt.attestor_sha256,
        ),
        _source_record(
            role="control.writer",
            locator=f"github://{subject.repository}/{subject.control_branch}@{oid}/{WRITER_PATH}",
            repository=subject.repository,
            read_mode="GET_AT_OID",
            identity={
                "byte_sha256": digest_bytes(writer_payload),
                "blob_oid": writer_blob,
                "branch": subject.control_branch,
                "commit_oid": oid,
                "path": WRITER_PATH,
                "selected_record_id": selected.record_id,
            },
            payload=writer_payload,
            producer_sha256=attempt.attestor_sha256,
            readback_digest=fence.readback_digest,
        ),
        _source_record(
            role="control.active_plan",
            locator=f"github://{subject.repository}/{subject.control_branch}@{oid}/{ACTIVE_PLAN_PATH}",
            repository=subject.repository,
            read_mode="GET_AT_OID",
            identity={
                "active_plan_digest": active_plan["active_plan_digest"],
                "byte_sha256": digest_bytes(active_payload),
                "blob_oid": active_blob,
                "branch": subject.control_branch,
                "commit_oid": oid,
                "path": ACTIVE_PLAN_PATH,
            },
            payload=active_payload,
            producer_sha256=attempt.attestor_sha256,
            readback_digest=fence.readback_digest,
        ),
        _source_record(
            role="control.legacy_fence",
            locator=f"github://{subject.repository}/{subject.control_branch}@{oid}/{LEGACY_FENCE_PATH}",
            repository=subject.repository,
            read_mode="GET_AT_OID",
            identity={
                "byte_sha256": digest_bytes(legacy_payload),
                "blob_oid": legacy_blob,
                "branch": subject.control_branch,
                "commit_oid": oid,
                "path": LEGACY_FENCE_PATH,
            },
            payload=legacy_payload,
            producer_sha256=attempt.attestor_sha256,
            readback_digest=fence.readback_digest,
        ),
    ]
    authority = WriterAuthorityObservation(
        writer_generation=subject.source_writer_generation,
        record_id=selected.record_id,
        authority_state="authoritative",
        activation_id=None,
        legacy_stopped=True,
        source_record_digests=tuple(sorted(record.digest for record in source_records)),
    )
    return fence, authority, source_records


def _fixed_store_contract() -> tuple[tuple[str, ...], Mapping[str, tuple[tuple[str, str, int, str | None, int], ...]]]:
    try:
        from run_beta3_live_guard import FIXED_STORE_SCHEMA_CONTRACT, FIXED_STORE_TABLES
    except Exception as error:
        raise BootstrapError(
            "STORE_SOURCE_UNAVAILABLE",
            "fixed current-main Store contract is unavailable",
        ) from error
    if (
        type(FIXED_STORE_TABLES) is not tuple
        or not FIXED_STORE_TABLES
        or type(FIXED_STORE_SCHEMA_CONTRACT) is not dict
        or set(FIXED_STORE_SCHEMA_CONTRACT) != set(FIXED_STORE_TABLES)
    ):
        _fail("STORE_SOURCE_UNAVAILABLE", "fixed current-main Store contract is malformed")
    return FIXED_STORE_TABLES, FIXED_STORE_SCHEMA_CONTRACT


def _configured_store_tables(config: object, expected_tables: tuple[str, ...]) -> tuple[str, ...]:
    configured = config.expected_store_tables
    if type(configured) is not tuple or configured != expected_tables:
        _fail("STORE_SOURCE_UNAVAILABLE", "configured Store tables are not the fixed current-main contract")
    return expected_tables


def _path_text(path: Path) -> str:
    return str(_absolute_local_path(Path(path)))


def _sidecars(path: Path) -> tuple[Path, ...]:
    return tuple(
        Path(f"{path}{suffix}")
        for suffix in (
            "-journal",
            "-wal",
            "-shm",
            ".staging",
            ".tmp",
            ".partial",
            "-staging",
            ".lock",
        )
    )


def _dynamic_sidecars(path: Path) -> tuple[Path, ...]:
    parent = Path(path).parent
    try:
        parent = _canonical_local_directory(parent, "STORE_SOURCE_UNAVAILABLE")
    except BootstrapError as error:
        if not os.path.lexists(parent):
            _validate_missing_local_path(parent, "STORE_SOURCE_UNAVAILABLE")
            return ()
        raise error
    try:
        candidates: list[Path] = []
        with _held_directory_scan(parent, "STORE_SOURCE_UNAVAILABLE") as (held, scanner):
            for entry in sorted(scanner, key=lambda item: item.name):
                match = _DYNAMIC_SIDE_FILE.fullmatch(entry.name)
                if match is None or match.group("prefix") != Path(path).name:
                    continue
                entry_path = parent / entry.name
                entry_stat = entry.stat(follow_symlinks=False)
                held.assert_stable()
                if stat.S_ISLNK(entry_stat.st_mode) or _is_reparse(entry_stat):
                    _fail(
                        "STORE_SOURCE_UNAVAILABLE",
                        f"SQLite sidecar is a link or reparse point: {entry_path}",
                    )
                if stat.S_ISREG(entry_stat.st_mode):
                    _validate_held_file(
                        entry_path,
                        held,
                        "STORE_SOURCE_UNAVAILABLE",
                        entry=entry,
                        entry_stat=entry_stat,
                    )
                elif stat.S_ISDIR(entry_stat.st_mode):
                    _held_child_directory_identities(
                        entry_path,
                        held,
                        "STORE_SOURCE_UNAVAILABLE",
                        entry=entry,
                        entry_stat=entry_stat,
                    )
                candidates.append(entry_path)
        return tuple(sorted(candidates, key=str))
    except BootstrapError:
        raise
    except OSError as error:
        raise BootstrapError("STORE_SOURCE_UNAVAILABLE", "SQLite sidecar scan failed") from error


def _check_sidecars(path: Path) -> None:
    dynamic = _dynamic_sidecars(path)
    fixed = tuple(candidate for candidate in _sidecars(path) if os.path.lexists(candidate))
    present = tuple(str(candidate) for candidate in (*fixed, *dynamic))
    if present:
        _fail("STORE_SOURCE_UNAVAILABLE", "SQLite sidecar is present")


def _sqlite_schema_digest(connection: sqlite3.Connection) -> str:
    objects = [
        {"type": row[0], "name": row[1], "tbl_name": row[2], "sql": row[3]}
        for row in connection.execute(
            "select type, name, tbl_name, sql from sqlite_master "
            "order by type, name, tbl_name, sql"
        ).fetchall()
    ]
    options = {
        name: connection.execute(f"pragma {name}").fetchone()[0]
        for name in (
            "application_id",
            "auto_vacuum",
            "encoding",
            "foreign_keys",
            "journal_mode",
            "recursive_triggers",
            "user_version",
        )
    }
    return digest_bytes(canonical_bytes({"sqlite_master": objects, "options": options}))


def _text(value: object, label: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if type(value) is not str or not value:
        _fail("STORE_SOURCE_UNAVAILABLE", f"Store {label} is malformed")
    return value


def _digest(value: object, label: str, *, optional: bool = False) -> str | None:
    text = _text(value, label, optional=optional)
    if text is None:
        return None
    if _HEX64.fullmatch(text) is None:
        _fail("STORE_SOURCE_UNAVAILABLE", f"Store {label} is not a digest")
    return text


def _json_text(value: object, label: str) -> object:
    text = _text(value, label)
    try:
        parsed = load_canonical_json(text)
    except Exception as error:
        raise BootstrapError("STORE_SOURCE_UNAVAILABLE", f"Store {label} is not canonical JSON") from error
    return parsed


def _receipt(
    config: object,
    subject: CutoverSubject,
    snapshot: _FileSnapshot,
    store_path: Path,
    *,
    observed_store_sha256: str | None = None,
) -> dict[str, object]:
    try:
        value = _load_canonical_object(snapshot.content, "STORE_SOURCE_UNAVAILABLE")
    except BootstrapError:
        raise
    if type(value) is not dict:
        _fail("STORE_SOURCE_UNAVAILABLE", "fresh Store receipt is not an object")
    expected_keys = {
        "schema",
        "repository",
        "source_main_sha",
        "source_main_tree",
        "runbook_sha256",
        "store_path",
        "store_generation",
        "store_sha256",
        "integrity",
        "tables",
        "schema_digest",
        "generation_rows",
        "row_counts",
        "existing_store_hashes_before",
        "existing_store_hashes_after",
        "old_stores_untouched",
    }
    if set(value) != expected_keys:
        _fail("STORE_SOURCE_UNAVAILABLE", "fresh Store receipt schema is not exact")
    expected = {
        "schema": "gwo-v8-fresh-store-provision.v1",
        "repository": subject.repository,
        "source_main_sha": config.merged_main_sha,
        "source_main_tree": config.merged_main_git_tree,
        "store_generation": config.store_generation,
        "store_sha256": config.expected_fresh_store_sha256,
        "integrity": "ok",
        "store_path": _path_text(store_path),
    }
    for name, expected_value in expected.items():
        if expected_value is not None and value.get(name) != expected_value:
            _fail("STORE_SOURCE_UNAVAILABLE", f"fresh Store receipt {name} is not exact")
    configured_digest = config.expected_fresh_receipt_sha256
    observed_digest = digest_bytes(snapshot.content)
    if observed_digest != configured_digest:
        _fail("STORE_SOURCE_UNAVAILABLE", "fresh Store receipt bytes changed")
    configured_runbook = config.expected_fresh_receipt_runbook_sha256
    if value["runbook_sha256"] != configured_runbook:
        _fail("STORE_SOURCE_UNAVAILABLE", "fresh Store receipt runbook is not exact")
    configured_schema = config.expected_fresh_receipt_schema_digest
    if value["schema_digest"] != configured_schema:
        _fail("STORE_SOURCE_UNAVAILABLE", "fresh Store receipt schema digest is not exact")
    expected_rows = [list(row) for row in config.expected_fresh_receipt_generation_rows]
    if value["generation_rows"] != expected_rows:
        _fail("STORE_SOURCE_UNAVAILABLE", "fresh Store receipt generation rows are not exact")
    expected_counts = dict(config.expected_fresh_receipt_row_counts)
    if value["row_counts"] != expected_counts:
        _fail("STORE_SOURCE_UNAVAILABLE", "fresh Store receipt row counts are not exact")
    expected_old = {
        _path_text(Path(config.rollback_store)): config.expected_rollback_store_sha256,
        _path_text(Path(config.prior_store)): config.expected_prior_store_sha256,
    }
    for name in ("existing_store_hashes_before", "existing_store_hashes_after"):
        old = value[name]
        if (
            type(old) is not dict
            or old != expected_old
            or any(
                type(key) is not str
                or type(child) is not str
                or _HEX64.fullmatch(child) is None
                for key, child in old.items()
            )
        ):
            _fail("STORE_SOURCE_UNAVAILABLE", "fresh receipt old Store hashes are not exact")
    for name in ("runbook_sha256", "schema_digest", "store_sha256"):
        _require_digest(value.get(name), f"receipt.{name}", "STORE_SOURCE_UNAVAILABLE")
    if observed_store_sha256 is not None:
        _require_digest(observed_store_sha256, "observed Store SHA-256", "STORE_SOURCE_UNAVAILABLE")
        if value["store_sha256"] != observed_store_sha256:
            _fail("STORE_SOURCE_UNAVAILABLE", "fresh Store receipt does not bind Store bytes")
    if (
        type(value["tables"]) is not list
        or any(type(item) is not str or not item for item in value["tables"])
        or len(set(value["tables"])) != len(value["tables"])
    ):
        _fail("STORE_SOURCE_UNAVAILABLE", "receipt table contract is malformed")
    expected_tables, _ = _fixed_store_contract()
    _configured_store_tables(config, expected_tables)
    if tuple(value["tables"]) != tuple(expected_tables):
        _fail("STORE_SOURCE_UNAVAILABLE", "receipt table contract changed")
    if (
        type(value["generation_rows"]) is not list
        or any(
            type(item) is not list
            or len(item) != 2
            or any(type(part) is not str or not part for part in item)
            for item in value["generation_rows"]
        )
        or type(value["row_counts"]) is not dict
        or any(
            type(key) is not str
            or type(count) is not int
            or count < 0
            for key, count in value["row_counts"].items()
        )
    ):
        _fail("STORE_SOURCE_UNAVAILABLE", "receipt generation/count contract is malformed")
    if expected_tables and set(value["row_counts"]) != set(expected_tables):
        _fail("STORE_SOURCE_UNAVAILABLE", "receipt row-count table contract is not exact")
    if value["old_stores_untouched"] is not True:
        _fail("STORE_SOURCE_UNAVAILABLE", "receipt old-store flag is not authoritative")
    return value


def _row_dict(row: sqlite3.Row | Sequence[object], names: Sequence[str]) -> dict[str, object]:
    if isinstance(row, sqlite3.Row):
        return {name: row[name] for name in names}
    return {name: row[index] for index, name in enumerate(names)}


def _read_store(config: object, subject: CutoverSubject, attempt: AttemptIdentity) -> _StoreFacts:
    path_value = config.fresh_store
    receipt_path_value = config.fresh_receipt
    try:
        path = Path(path_value)
        receipt_path = Path(receipt_path_value)
    except (OSError, TypeError, ValueError) as error:
        raise BootstrapError("STORE_SOURCE_UNAVAILABLE", "fixed Store paths are malformed") from error
    store_snapshot = _read_file_snapshot(path, "STORE_SOURCE_UNAVAILABLE")
    expected_hash = config.expected_fresh_store_sha256
    observed_hash = dict(store_snapshot.identity).get("byte_sha256")
    if observed_hash != expected_hash:
        _fail("STORE_SOURCE_UNAVAILABLE", "fresh Store hash is not the configured identity")
    receipt_snapshot = _read_file_snapshot(receipt_path, "STORE_SOURCE_UNAVAILABLE")
    try:
        receipt = _receipt(
            config,
            subject,
            receipt_snapshot,
            path,
            observed_store_sha256=dict(store_snapshot.identity).get("byte_sha256"),
        )
    except BootstrapError:
        raise
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise BootstrapError("STORE_SOURCE_UNAVAILABLE", "fresh Store receipt is malformed") from error
    _check_sidecars(path)
    expected_tables, schema_contract = _fixed_store_contract()
    _configured_store_tables(config, expected_tables)
    connection: sqlite3.Connection | None = None
    try:
        uri = f"file:{quote(_path_text(path).replace(chr(92), '/'), safe='/:')}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        integrity = connection.execute("pragma integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            _fail("STORE_SOURCE_UNAVAILABLE", "Store integrity check failed")
        tables = tuple(
            sorted(
                str(row[0])
                for row in connection.execute(
                    "select name from sqlite_master where type='table'"
                ).fetchall()
            )
        )
        if expected_tables and tables != tuple(sorted(expected_tables)):
            _fail("STORE_SOURCE_UNAVAILABLE", "Store table schema is not exact")
        for table, expected_columns in schema_contract.items():
            actual = tuple(
                (str(row[1]), str(row[2]), int(row[3]), row[4], int(row[5]))
                for row in connection.execute(f'pragma table_info("{table}")').fetchall()
            )
            if actual != expected_columns:
                _fail("STORE_SOURCE_UNAVAILABLE", f"Store columns are not exact for {table}")
        schema_digest = _sqlite_schema_digest(connection)
        if receipt.get("schema_digest") != schema_digest:
            _fail("STORE_SOURCE_UNAVAILABLE", "Store schema digest is not receipt-bound")
        row_counts = receipt.get("row_counts")
        if type(row_counts) is not dict:
            _fail("STORE_SOURCE_UNAVAILABLE", "Store row-count receipt is malformed")
        if expected_tables:
            actual_counts = {
                table: int(connection.execute(f'select count(*) from "{table}"').fetchone()[0])
                for table in expected_tables
            }
            if actual_counts != row_counts:
                _fail("STORE_SOURCE_UNAVAILABLE", "Store rows are not receipt-bound")
        generation_rows = receipt.get("generation_rows")
        current_generation_rows = [
            [row["repository"], row["writer_generation"]]
            for row in connection.execute(
                'select repository, writer_generation from "v8_writer_generations" order by repository'
            ).fetchall()
        ]
        if generation_rows != current_generation_rows:
            _fail("STORE_SOURCE_UNAVAILABLE", "Store generation rows are not receipt-bound")
        repository = subject.repository
        active_plan_digests: set[str] = set()
        pending_activation_ids: list[str] = []
        predecessor_refs: list[str] = []
        for table in (
            "v8_execution_state",
            "v8_node_execution_state",
            "v8_node_states",
            "v8_admissions",
            "v8_attempts",
            "v8_integration_batches",
            "v8_verified_results",
            "v8_active_plans",
            "v8_pending_activations",
            "v8_plan_revisions",
            "v8_goal_holds",
            "v8_resource_claims",
            "v8_integration_leases",
            "v8_writer_fences",
            "v8_writer_generations",
        ):
            for row in connection.execute(f'select * from "{table}"').fetchall():
                if "repository" in row.keys() and row["repository"] != repository:
                    _fail("STORE_SOURCE_UNAVAILABLE", f"Store {table} repository changed")
        for row in connection.execute(
            'select repository, plan_digest, state_json from "v8_execution_state"'
        ).fetchall():
            _digest(row["plan_digest"], "v8_execution_state.plan_digest")
            _json_text(row["state_json"], "v8_execution_state.state_json")
        for row in connection.execute(
            'select repository, plan_digest, node_key, state_json '
            'from "v8_node_execution_state"'
        ).fetchall():
            _digest(row["plan_digest"], "v8_node_execution_state.plan_digest")
            _text(row["node_key"], "v8_node_execution_state.node_key")
            _json_text(row["state_json"], "v8_node_execution_state.state_json")
        for row in connection.execute(
            'select repository, plan_digest, node_key, state '
            'from "v8_node_states"'
        ).fetchall():
            _digest(row["plan_digest"], "v8_node_states.plan_digest")
            _text(row["node_key"], "v8_node_states.node_key")
            _text(row["state"], "v8_node_states.state")
        for row in connection.execute(
            'select repository, plan_digest, node_key, goal_key, state '
            'from "v8_admissions"'
        ).fetchall():
            _digest(row["plan_digest"], "v8_admissions.plan_digest")
            _text(row["node_key"], "v8_admissions.node_key")
            _text(row["goal_key"], "v8_admissions.goal_key")
            _text(row["state"], "v8_admissions.state")
        for row in connection.execute(
            'select repository, plan_digest, node_key, admission_id, state '
            'from "v8_attempts"'
        ).fetchall():
            _digest(row["plan_digest"], "v8_attempts.plan_digest")
            _text(row["node_key"], "v8_attempts.node_key")
            _text(row["admission_id"], "v8_attempts.admission_id")
            _text(row["state"], "v8_attempts.state")
        for row in connection.execute(
            'select repository, plan_digest, batch_id, state_json '
            'from "v8_integration_batches"'
        ).fetchall():
            _digest(row["plan_digest"], "v8_integration_batches.plan_digest")
            _text(row["batch_id"], "v8_integration_batches.batch_id")
            _json_text(row["state_json"], "v8_integration_batches.state_json")
        for row in connection.execute(
            'select repository, goal_key, reason from "v8_goal_holds"'
        ).fetchall():
            _text(row["goal_key"], "v8_goal_holds.goal_key")
            _text(row["reason"], "v8_goal_holds.reason")
        for row in connection.execute(
            'select repository, plan_digest, node_key, contract_digest, candidate_sha, '
            'result_digest, base_sha, evidence_manifest_digest, evidence_json, superseded '
            'from "v8_verified_results"'
        ).fetchall():
            _digest(row["plan_digest"], "v8_verified_results.plan_digest")
            _text(row["node_key"], "v8_verified_results.node_key")
            for column in ("contract_digest", "candidate_sha", "result_digest", "base_sha"):
                _digest(row[column], f"v8_verified_results.{column}")
            if row["evidence_manifest_digest"] is not None:
                _digest(row["evidence_manifest_digest"], "v8_verified_results.evidence_manifest_digest")
            if row["evidence_json"] is not None:
                _json_text(row["evidence_json"], "v8_verified_results.evidence_json")
            if type(row["superseded"]) is not int or row["superseded"] not in (0, 1):
                _fail("STORE_SOURCE_UNAVAILABLE", "v8_verified_results.superseded is malformed")
        for row in connection.execute(
            'select repository, holder from "v8_integration_leases"'
        ).fetchall():
            _text(row["holder"], "v8_integration_leases.holder")
        for row in connection.execute(
            'select repository, writer_generation, activation_id, state '
            'from "v8_writer_fences"'
        ).fetchall():
            if row["writer_generation"] != subject.target_writer_generation:
                _fail("STORE_SOURCE_UNAVAILABLE", "Store writer fence generation changed")
            _text(row["activation_id"], "v8_writer_fences.activation_id")
            _text(row["state"], "v8_writer_fences.state")
        for row in connection.execute(
            'select repository, writer_generation from "v8_writer_generations"'
        ).fetchall():
            _text(row["writer_generation"], "v8_writer_generations.writer_generation")
        for row in connection.execute(
            'select repository, plan_digest, writer_generation, activation_id '
            'from "v8_active_plans" order by repository'
        ).fetchall():
            active_plan_digests.add(_digest(row["plan_digest"], "v8_active_plans.plan_digest") or "")
            _text(row["repository"], "v8_active_plans.repository")
            if row["writer_generation"] != subject.target_writer_generation:
                _fail("STORE_SOURCE_UNAVAILABLE", "active Store plan generation changed")
            _text(row["activation_id"], "v8_active_plans.activation_id", optional=True)
        for row in connection.execute(
            'select repository, plan_digest, expected_previous_digest, writer_generation, activation_id, receipt_json '
            'from "v8_pending_activations" order by repository'
        ).fetchall():
            plan_digest = _digest(row["plan_digest"], "v8_pending_activations.plan_digest")
            previous = row["expected_previous_digest"]
            if previous is not None:
                _digest(previous, "v8_pending_activations.expected_previous_digest")
            if row["writer_generation"] != subject.target_writer_generation:
                _fail("STORE_SOURCE_UNAVAILABLE", "pending Store activation generation changed")
            activation_id = _text(row["activation_id"], "v8_pending_activations.activation_id")
            pending_activation_ids.append(activation_id or "")
            raw_receipt = _text(row["receipt_json"], "v8_pending_activations.receipt_json")
            pending = _json_text(raw_receipt, "v8_pending_activations.receipt_json")
            pending_fields = {
                "schema_version",
                "repository",
                "writer_generation",
                "activation_id",
                "plan_digest",
                "expected_previous_digest",
                "plan_record_ref",
                "created_at",
            }
            if (
                type(pending) is not dict
                or set(pending) != pending_fields
                or pending.get("schema_version") != 1
                or pending.get("repository") != repository
                or pending.get("writer_generation") != subject.target_writer_generation
                or pending.get("activation_id") != activation_id
                or pending.get("plan_digest") != plan_digest
                or pending.get("expected_previous_digest") != previous
                or _text(pending.get("plan_record_ref"), "pending receipt.plan_record_ref") is None
                or _text(pending.get("created_at"), "pending receipt.created_at") is None
                or raw_receipt != canonical_bytes(pending).decode("utf-8")
            ):
                _fail("STORE_SOURCE_UNAVAILABLE", "pending Store receipt is not bound")
            active_plan_digests.add(plan_digest or "")
        for row in connection.execute(
            'select repository, plan_digest, canonical_bytes, compilation_record, writer_generation '
            'from "v8_plan_revisions" order by repository, plan_digest'
        ).fetchall():
            plan_digest = _digest(row["plan_digest"], "v8_plan_revisions.plan_digest")
            if type(row["canonical_bytes"]) is not bytes or digest_bytes(row["canonical_bytes"]) != plan_digest:
                _fail("STORE_SOURCE_UNAVAILABLE", "Plan Revision bytes are not bound")
            _json_text(row["compilation_record"], "v8_plan_revisions.compilation_record")
            if row["writer_generation"] != subject.target_writer_generation:
                _fail("STORE_SOURCE_UNAVAILABLE", "Plan Revision generation changed")
            predecessor_refs.append(plan_digest or "")
            active_plan_digests.add(plan_digest or "")
        admission_rows = connection.execute(
            'select admission_id, state from "v8_admissions" order by admission_id'
        ).fetchall()
        admission_ids: set[str] = set()
        active_admission_values: list[str] = []
        for row in admission_rows:
            admission_id = _text(row["admission_id"], "v8_admissions.admission_id")
            state = _text(row["state"], "v8_admissions.state")
            if admission_id in admission_ids:
                _fail("STORE_SOURCE_UNAVAILABLE", "admission identities are duplicated")
            admission_ids.add(admission_id or "")
            if state not in {"consumed", "abandoned"}:
                active_admission_values.append(admission_id or "")
        attempt_rows_full = connection.execute(
            'select attempt_id, admission_id, state from "v8_attempts" order by attempt_id'
        ).fetchall()
        attempt_rows: dict[str, str] = {}
        active_attempt_values: list[str] = []
        for row in attempt_rows_full:
            attempt_id = _text(row["attempt_id"], "v8_attempts.attempt_id")
            admission_id = _text(row["admission_id"], "v8_attempts.admission_id")
            state = _text(row["state"], "v8_attempts.state")
            if attempt_id in attempt_rows:
                _fail("STORE_SOURCE_UNAVAILABLE", "attempt identities are duplicated")
            if admission_id not in admission_ids:
                _fail("STORE_SOURCE_UNAVAILABLE", "attempt admission link is missing")
            attempt_rows[attempt_id or ""] = admission_id or ""
            if state not in {"verified", "terminal"}:
                active_attempt_values.append(attempt_id or "")
        active_admissions = tuple(sorted(active_admission_values))
        active_attempts = tuple(sorted(active_attempt_values))
        leases = connection.execute(
            'select repository, holder from "v8_integration_leases" order by repository'
        ).fetchall()
        if len(leases) > 1:
            _fail("STORE_SOURCE_UNAVAILABLE", "integration lease rows are contradictory")
        integration_lease_owner = None if not leases else _text(leases[0]["holder"], "v8_integration_leases.holder")
        claims: list[str] = []
        seen_claims: set[str] = set()
        for row in connection.execute(
            'select resource_key, admission_id, attempt_id from "v8_resource_claims" '
            'order by resource_key, admission_id, attempt_id'
        ).fetchall():
            resource = _text(row["resource_key"], "v8_resource_claims.resource_key")
            if resource in seen_claims:
                _fail("STORE_SOURCE_UNAVAILABLE", "resource claim identities are duplicated")
            seen_claims.add(resource or "")
            claim_admission = _text(
                row["admission_id"],
                "v8_resource_claims.admission_id",
                optional=True,
            )
            claim_attempt = _text(
                row["attempt_id"],
                "v8_resource_claims.attempt_id",
                optional=True,
            )
            if claim_admission is not None and claim_admission not in admission_ids:
                _fail("STORE_SOURCE_UNAVAILABLE", "resource claim admission link is missing")
            if claim_attempt is not None and claim_attempt not in attempt_rows:
                _fail("STORE_SOURCE_UNAVAILABLE", "resource claim attempt link is missing")
            if claim_attempt is not None and claim_admission is not None and attempt_rows[claim_attempt] != claim_admission:
                _fail("STORE_SOURCE_UNAVAILABLE", "resource claim links cross different admissions")
            claims.append(f"claim:{resource}")
        for row in connection.execute(
            'select repository, writer_generation from "v8_writer_generations" order by repository'
        ).fetchall():
            if row["writer_generation"] != config.store_generation and row["repository"] == repository:
                _fail("STORE_SOURCE_UNAVAILABLE", "Store writer generation changed")
        if len(pending_activation_ids) != len(set(pending_activation_ids)):
            _fail("STORE_SOURCE_UNAVAILABLE", "pending Activation identities are duplicated")
        if len(predecessor_refs) != len(set(predecessor_refs)):
            _fail("STORE_SOURCE_UNAVAILABLE", "Plan Revision identities are duplicated")
        store_after = _read_file_snapshot(path, "STORE_SOURCE_UNAVAILABLE")
        if store_after.identity != store_snapshot.identity:
            _fail("STORE_SOURCE_UNAVAILABLE", "Store bytes changed during immutable read")
        receipt_after = _read_file_snapshot(receipt_path, "STORE_SOURCE_UNAVAILABLE")
        if (
            receipt_after.identity != receipt_snapshot.identity
            or receipt_after.content != receipt_snapshot.content
        ):
            _fail("STORE_SOURCE_UNAVAILABLE", "Store receipt changed during immutable read")
        values = {
            "repository": repository,
            "generation_id": config.store_generation,
            "state_schema": STORE_SCHEMA,
            "compatible": True,
            "active_plan_digests": tuple(sorted(active_plan_digests)),
            "pending_activation_ids": tuple(sorted(pending_activation_ids)),
            "predecessor_identity_refs": tuple(sorted(predecessor_refs)),
        }
        durable = DurableStateReadback(**values, readback_digest=digest_value(values))
        return _StoreFacts(
            store_record=_source_record(
                role="store.sqlite",
                locator=_path_text(path),
                repository=repository,
                read_mode="IMMUTABLE_SQLITE",
                identity={
                    "generation": values["generation_id"],
                    "path": _path_text(path),
                    "sha256": dict(store_snapshot.identity)["byte_sha256"],
                    "schema_digest": schema_digest,
                },
                payload=store_snapshot.content,
                producer_sha256=attempt.attestor_sha256,
                readback_digest=durable.readback_digest,
            ),
            receipt_record=_source_record(
                role="store.receipt",
                locator=_path_text(receipt_path),
                repository=repository,
                read_mode="EXACT_FILE",
                identity={
                    "path": _path_text(receipt_path),
                    "sha256": digest_bytes(receipt_snapshot.content),
                    "store_generation": values["generation_id"],
                },
                payload=receipt_snapshot.content,
                producer_sha256=attempt.attestor_sha256,
                readback_digest=durable.readback_digest,
            ),
            durable=durable,
            active_admissions=active_admissions,
            active_attempts=active_attempts,
            integration_lease_owner=integration_lease_owner,
            resource_claims=tuple(sorted(claims)),
            runtime_links=(),
        )
    except BootstrapError:
        raise
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as error:
        raise BootstrapError("STORE_SOURCE_UNAVAILABLE", "immutable Store read failed") from error
    finally:
        if connection is not None:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            connection.close()
        _check_sidecars(path)


def _registry_refs(value: object) -> tuple[str, ...]:
    if type(value) is not dict or set(value) != {"runtimes"}:
        _fail(
            "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE",
            "Runtime registry mapping shape is not authoritative",
        )
    value = value["runtimes"]
    if type(value) is not list:
        _fail("RUNTIME_REGISTRY_SOURCE_UNAVAILABLE", "Runtime registry enumeration is not a list")
    identities: list[str] = []
    for item in value:
        identity = item.get("identity") if type(item) is dict and set(item) == {"identity"} else None
        if type(identity) is not str or not identity:
            _fail("RUNTIME_REGISTRY_SOURCE_UNAVAILABLE", "Runtime registry identity is absent")
        identity = identity.removeprefix("runtime:")
        if not identity:
            _fail("RUNTIME_REGISTRY_SOURCE_UNAVAILABLE", "Runtime registry identity is absent")
        identities.append(identity)
    if len(set(identities)) != len(identities):
        _fail("RUNTIME_REGISTRY_SOURCE_UNAVAILABLE", "Runtime registry identity is duplicated")
    return tuple(sorted(f"runtime:{identity}" for identity in identities))


def _runtime_config_value(
    payload: bytes,
    repository: str,
) -> tuple[RuntimeConfiguration, RuntimePreflightReadback]:
    value = _load_canonical_object(payload, "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE")
    if (
        type(value) is not dict
        or value.get("schema_version") != 1
        or not {"global", "tiers", "role_profiles"} <= set(value)
        or set(value)
        - {
            "schema_version",
            "global",
            "tiers",
            "role_profiles",
            "reviewer_tiers",
            "repositories",
        }
        or type(repository) is not str
        or not repository
    ):
        _fail("RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE", "Runtime configuration schema is invalid")
    try:
        global_value = value["global"]
        tiers = value["tiers"]
        role_profiles = value["role_profiles"]
        repositories = value.get("repositories", {})
        reviewer_tiers = value.get("reviewer_tiers", {})
        if (
            type(global_value) is not dict
            or set(global_value) - {"default_tier", "execution_slots"}
            or type(tiers) is not dict
            or not tiers
            or type(role_profiles) is not dict
            or type(repositories) is not dict
            or type(reviewer_tiers) is not dict
        ):
            raise ValueError("Runtime configuration mappings are malformed")
        default_tier = global_value.get("default_tier")
        if type(default_tier) is not str or default_tier not in RUNTIME_TIERS:
            raise ValueError("Runtime configuration default tier is absent")
        if default_tier not in tiers:
            raise ValueError("Runtime configuration default tier mapping is absent")
        if any(type(name) is not str or name not in RUNTIME_TIERS for name in tiers):
            raise ValueError("Runtime configuration contains an unknown tier")
        if any(type(name) is not str or name not in RUNTIME_ROLE_PROFILES for name in role_profiles):
            raise ValueError("Runtime configuration contains an unknown role profile")
        if not set(RUNTIME_ROLE_PROFILES) <= set(role_profiles):
            raise ValueError("Runtime configuration role profiles are incomplete")
        if any(
            type(name) is not str
            or not name
            or type(tier) is not str
            or tier not in RUNTIME_TIERS
            for name, tier in reviewer_tiers.items()
        ):
            raise ValueError("Runtime reviewer tier mapping is malformed")

        def validate_profile_shape(raw: object) -> tuple[str, dict[str, object]]:
            if (
                type(raw) is not dict
                or set(raw) != {"provider", "settings"}
                or type(raw.get("provider")) is not str
                or not raw["provider"]
                or type(raw.get("settings")) is not dict
            ):
                raise ValueError("profile is malformed")
            settings = raw["settings"]
            if set(settings) - {
                "model",
                "thinkingOptionId",
                "modeId",
                "features",
            } or not {
                "model",
                "thinkingOptionId",
                "modeId",
            } <= set(settings):
                raise ValueError("profile settings are malformed")
            if any(
                type(settings[field]) is not str or not settings[field]
                for field in ("model", "thinkingOptionId", "modeId")
            ) or type(settings.get("features", {})) is not dict:
                raise ValueError("profile settings are malformed")
            return raw["provider"], settings

        for raw in (*tiers.values(), *role_profiles.values()):
            validate_profile_shape(raw)
        for configured_repository, configured_value in repositories.items():
            if (
                type(configured_repository) is not str
                or "/" not in configured_repository
                or type(configured_value) is not dict
                or set(configured_value) - {"default_tier", "tiers", "role_profiles"}
            ):
                raise ValueError("repository Runtime configuration is malformed")
            configured_tiers = configured_value.get("tiers", {})
            configured_roles = configured_value.get("role_profiles", {})
            configured_default = configured_value.get("default_tier", default_tier)
            if (
                type(configured_tiers) is not dict
                or type(configured_roles) is not dict
                or type(configured_default) is not str
                or configured_default not in RUNTIME_TIERS
                or any(
                    type(name) is not str or name not in RUNTIME_TIERS
                    for name in configured_tiers
                )
                or any(
                    type(name) is not str or name not in RUNTIME_ROLE_PROFILES
                    for name in configured_roles
                )
            ):
                raise ValueError("repository Runtime mappings are malformed")
            for raw in (*configured_tiers.values(), *configured_roles.values()):
                validate_profile_shape(raw)

        repository_value = repositories.get(repository, {})
        if type(repository_value) is not dict or set(repository_value) - {
            "default_tier",
            "tiers",
            "role_profiles",
        }:
            raise ValueError("repository Runtime configuration is malformed")
        repository_tiers = repository_value.get("tiers", {})
        repository_roles = repository_value.get("role_profiles", {})
        if type(repository_tiers) is not dict or type(repository_roles) is not dict:
            raise ValueError("repository Runtime mappings are malformed")
        if any(type(name) is not str or name not in RUNTIME_TIERS for name in repository_tiers):
            raise ValueError("repository Runtime configuration contains an unknown tier")
        if any(type(name) is not str or name not in RUNTIME_ROLE_PROFILES for name in repository_roles):
            raise ValueError("repository Runtime configuration contains an unknown role profile")
        repository_default_tier = repository_value.get("default_tier", default_tier)
        if type(repository_default_tier) is not str or repository_default_tier not in RUNTIME_TIERS:
            raise ValueError("repository Runtime default tier is invalid")

        profiles: dict[str, RuntimeProfile] = {}

        def profile(name: str, raw: object) -> RuntimeProfile:
            provider, settings = validate_profile_shape(raw)
            result = RuntimeProfile(
                name=name,
                provider=provider,
                model=settings["model"],
                thinking=settings["thinkingOptionId"],
                mode=settings["modeId"],
                features=settings.get("features", {}),
            )
            profiles[result.digest] = result
            return result

        def resolved_profiles() -> dict[str, RuntimeProfile]:
            worker_raw = repository_tiers.get(
                repository_default_tier,
                tiers.get(repository_default_tier),
            )
            if worker_raw is None:
                raise ValueError("Runtime worker tier mapping is absent")
            return {
                "coordinator": profile(
                    "coordinator_auto",
                    repository_roles.get("coordinator_auto", role_profiles["coordinator_auto"]),
                ),
                "worker": profile(repository_default_tier, worker_raw),
                "recovery_worker": profile(
                    "reviewer_recovery",
                    repository_roles.get("reviewer_recovery", role_profiles["reviewer_recovery"]),
                ),
                "review_primary": profile(
                    "reviewer_standard",
                    repository_roles.get("reviewer_standard", role_profiles["reviewer_standard"]),
                ),
                "review_strong": profile(
                    "reviewer_strict",
                    repository_roles.get("reviewer_strict", role_profiles["reviewer_strict"]),
                ),
            }

        global_profiles = {
            "coordinator": profile("coordinator_auto", role_profiles["coordinator_auto"]),
            "worker": profile(default_tier, tiers[default_tier]),
            "recovery_worker": profile("reviewer_recovery", role_profiles["reviewer_recovery"]),
            "review_primary": profile("reviewer_standard", role_profiles["reviewer_standard"]),
            "review_strong": profile("reviewer_strict", role_profiles["reviewer_strict"]),
        }
        repository_profiles = resolved_profiles()
        repository_mapping: dict[str, ProfileMapping] = {}
        if repository_value and (
            "default_tier" in repository_value
            or repository_tiers
            or repository_roles
        ):
            for selector, item in repository_profiles.items():
                repository_mapping[selector] = ProfileMapping(item.digest)
        configuration = RuntimeConfiguration(
            profiles=profiles,
            host_mappings={
                selector: ProfileMapping(item.digest)
                for selector, item in global_profiles.items()
            },
            repository_mappings=(
                {repository: repository_mapping} if repository_mapping else {}
            ),
        )
        readback = RuntimeConfigurationReader(configuration).read(repository, RUNTIME_SELECTORS)
        return configuration, readback
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError(
            "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE",
            "Runtime configuration mapping or Profile identity is invalid",
        ) from error


def _validate_runtime_config_source(
    observation: SourceObservation,
    expected_path_value: Path,
) -> None:
    """Keep the Runtime projection bound to the one host config file."""

    expected_path = _absolute_local_path(Path(expected_path_value))
    record = observation.record
    try:
        locator = _absolute_local_path(Path(record.locator))
    except (OSError, TypeError, ValueError) as error:
        raise BootstrapError(
            "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE",
            "Runtime configuration locator is not a local file path",
        ) from error
    identity = dict(record.identity)
    expected_identity_keys = {"byte_sha256", "inode", "mtime_ns", "path", "size"}
    if (
        record.read_mode != "EXACT_FILE"
        or locator != expected_path
        or set(identity) != expected_identity_keys
        or identity.get("path") != str(expected_path)
        or identity.get("byte_sha256") != digest_bytes(observation.canonical_payload)
        or identity.get("size") != str(len(observation.canonical_payload))
        or type(identity.get("inode")) is not str
        or not identity["inode"].isdigit()
        or type(identity.get("mtime_ns")) is not str
        or not identity["mtime_ns"].isdigit()
    ):
        _fail(
            "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE",
            "Runtime configuration source is not bound to the exact file",
        )


def _is_reparse(stat_result: os.stat_result) -> bool:
    return bool(getattr(stat_result, "st_file_attributes", 0) & 0x0400)


def _absolute_local_path(path: Path) -> Path:
    return Path(os.path.abspath(Path(path)))


@dataclass(frozen=True)
class _EnumeratedStat:
    st_mode: int
    st_ino: int
    st_dev: int
    st_size: int
    st_mtime_ns: int
    st_file_attributes: int


@dataclass(frozen=True)
class _HeldDirectoryEntry:
    name: str
    path: Path
    _stat_result: _EnumeratedStat

    def stat(self, *, follow_symlinks: bool = True) -> _EnumeratedStat:
        del follow_symlinks
        return self._stat_result

    def inode(self) -> int:
        return self._stat_result.st_ino


def _held_directory_identity(path: Path, code: str) -> Mapping[str, int | str]:
    try:
        from run_beta3_live_guard import _close_descriptors, _open_directory_components
    except Exception as error:
        raise BootstrapError(code, f"local directory is unavailable: {path}") from error
    descriptors: list[int] = []
    try:
        descriptors, identities = _open_directory_components(Path(path), code)
        if not identities:
            raise OSError("directory identity is unavailable")
        return dict(identities[-1])
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError(code, f"local directory identity is unavailable: {path}") from error
    finally:
        try:
            _close_descriptors(descriptors)
        except OSError as error:
            raise BootstrapError(code, f"local directory handles could not be closed: {path}") from error


def _entry_inode(entry: object, entry_stat: os.stat_result) -> int | None:
    inode = getattr(entry, "inode", None)
    if callable(inode):
        try:
            value = inode()
        except (OSError, TypeError, ValueError):
            return None
        if type(value) is int and value >= 0:
            return value
    if os.name != "nt" and type(entry_stat.st_ino) is int and entry_stat.st_ino >= 0:
        return int(entry_stat.st_ino)
    return None


def _entry_identity_matches(
    entry: object,
    entry_stat: os.stat_result,
    opened_identity: Mapping[str, object],
) -> bool:
    if opened_identity.get("st_mode") is None or stat.S_IFMT(
        int(opened_identity["st_mode"])
    ) != stat.S_IFMT(entry_stat.st_mode):
        return False
    inode = _entry_inode(entry, entry_stat)
    if inode is None:
        return False
    if not stat.S_ISDIR(entry_stat.st_mode) and (
        opened_identity.get("st_size") != int(entry_stat.st_size)
        or opened_identity.get("st_mtime_ns") != int(entry_stat.st_mtime_ns)
    ):
        return False
    if os.name == "nt":
        file_id = opened_identity.get("file_id")
        if type(file_id) is not str:
            return False
        try:
            expected_prefix = inode.to_bytes(8, "little", signed=False).hex()
        except OverflowError:
            return False
        return file_id.startswith(expected_prefix)
    return (
        opened_identity.get("st_dev") == int(entry_stat.st_dev)
        and opened_identity.get("st_ino") == inode
    )


def _held_identity_matches_expected(
    current: Mapping[str, object],
    expected: Mapping[str, object],
    identity_matches: Callable[[Mapping[str, object], Mapping[str, object]], bool],
) -> bool:
    if ("file_id" in current) != ("file_id" in expected):
        return False
    return identity_matches(current, expected)


@dataclass
class _HeldDirectory:
    path: Path
    component_descriptors: list[int]
    component_identities: list[dict[str, int | str]]
    code: str
    assert_directory_handles: Callable[..., None]

    @property
    def descriptor(self) -> int:
        if not self.component_descriptors:
            raise OSError(f"directory is not held: {self.path}")
        return self.component_descriptors[-1]

    def assert_stable(self) -> None:
        self.assert_directory_handles(
            self.path,
            self.component_descriptors,
            self.component_identities,
            self.code,
        )


@contextmanager
def _held_local_directory(
    path: Path,
    code: str,
    *,
    expected_identities: Sequence[Mapping[str, object]] | None = None,
    expected_leaf_identity: Mapping[str, object] | None = None,
):
    try:
        from run_beta3_live_guard import (
            _assert_directory_handles,
            _close_descriptors,
            _identity_matches,
            _open_directory_components,
        )
    except Exception as error:
        raise BootstrapError(code, f"local directory is unavailable: {path}") from error
    descriptors: list[int] = []
    try:
        descriptors, identities = _open_directory_components(Path(path), code)
        if expected_identities is not None and (
            len(identities) != len(expected_identities)
            or any(
                not _held_identity_matches_expected(current, expected, _identity_matches)
                for current, expected in zip(identities, expected_identities, strict=True)
            )
        ):
            raise OSError("directory components are not the enumerated identities")
        if expected_leaf_identity is not None and (
            not identities
            or not _held_identity_matches_expected(
                identities[-1],
                expected_leaf_identity,
                _identity_matches,
            )
        ):
            raise OSError("directory is not the expected initial identity")
        yield _HeldDirectory(
            path=Path(path),
            component_descriptors=descriptors,
            component_identities=identities,
            code=code,
            assert_directory_handles=_assert_directory_handles,
        )
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError(code, f"local directory is unavailable: {path}") from error
    finally:
        try:
            _close_descriptors(descriptors)
        except OSError as error:
            raise BootstrapError(code, f"local directory handles could not be closed: {path}") from error


def _windows_directory_entries(
    held: _HeldDirectory,
    code: str,
) -> tuple[_HeldDirectoryEntry, ...]:
    try:
        import ctypes
        import msvcrt
        import struct

        class IoStatusBlock(ctypes.Structure):
            _fields_ = [
                ("status", ctypes.c_void_p),
                ("information", ctypes.c_size_t),
            ]

        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtQueryDirectoryFile.restype = ctypes.c_long
        ntdll.NtQueryDirectoryFile.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(IoStatusBlock),
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_ubyte,
            ctypes.c_void_p,
            ctypes.c_ubyte,
        ]
        handle = ctypes.c_void_p(msvcrt.get_osfhandle(held.descriptor))
        status_no_more_files = 0x80000006
        status_buffer_overflow = 0x80000005
        unix_epoch_filetime = 116444736000000000
        entries: list[_HeldDirectoryEntry] = []
        restart_scan = 1
        while True:
            buffer = ctypes.create_string_buffer(64 * 1024)
            io_status = IoStatusBlock()
            status = int(
                ntdll.NtQueryDirectoryFile(
                    handle,
                    None,
                    None,
                    None,
                    ctypes.byref(io_status),
                    ctypes.byref(buffer),
                    ctypes.sizeof(buffer),
                    37,
                    0,
                    None,
                    restart_scan,
                )
            ) & 0xFFFFFFFF
            restart_scan = 0
            if status == status_no_more_files:
                break
            if status not in {0, status_buffer_overflow}:
                raise OSError(status, "NtQueryDirectoryFile failed")
            information = int(io_status.information)
            if information <= 0:
                raise OSError("directory enumeration returned no data")
            data = buffer.raw[:information]
            offset = 0
            while offset < information:
                if offset + 104 > information:
                    raise OSError("directory enumeration record is truncated")
                next_offset = struct.unpack_from("<I", data, offset)[0]
                file_attributes = struct.unpack_from("<I", data, offset + 56)[0]
                file_name_length = struct.unpack_from("<I", data, offset + 60)[0]
                file_size = struct.unpack_from("<q", data, offset + 40)[0]
                filetime = struct.unpack_from("<q", data, offset + 24)[0]
                file_id = struct.unpack_from("<Q", data, offset + 96)[0]
                name_start = offset + 104
                name_end = name_start + file_name_length
                if (
                    file_name_length % 2
                    or name_end > information
                    or file_id == 0
                    or (next_offset and (next_offset < 104 or offset + next_offset > information))
                ):
                    raise OSError("directory enumeration record is malformed")
                name = data[name_start:name_end].decode("utf-16-le")
                if name not in {".", ".."}:
                    mode = (
                        stat.S_IFDIR | 0o755
                        if file_attributes & 0x10
                        else stat.S_IFREG | 0o644
                    )
                    entries.append(
                        _HeldDirectoryEntry(
                            name=name,
                            path=held.path / name,
                            _stat_result=_EnumeratedStat(
                                st_mode=mode,
                                st_ino=file_id,
                                st_dev=0,
                                st_size=max(file_size, 0),
                                st_mtime_ns=(filetime - unix_epoch_filetime) * 100,
                                st_file_attributes=file_attributes,
                            ),
                        )
                    )
                if not next_offset:
                    break
                offset += next_offset
            if not offset and information:
                raise OSError("directory enumeration did not advance")
        return tuple(entries)
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError(code, "Windows directory enumeration failed") from error


@contextmanager
def _enumerate_held_directory(
    path: Path,
    held: _HeldDirectory,
    code: str,
):
    del path
    if os.name == "nt":
        yield _windows_directory_entries(held, code)
        return
    scan_descriptor: int | None = None
    try:
        scan_descriptor = os.dup(held.descriptor)
        scanner = os.scandir(scan_descriptor)
        scan_descriptor = None
        with scanner:
            yield scanner
    except BootstrapError:
        raise
    except OSError as error:
        raise BootstrapError(code, "descriptor-relative directory enumeration failed") from error
    finally:
        if scan_descriptor is not None:
            try:
                os.close(scan_descriptor)
            except OSError as error:
                raise BootstrapError(
                    code,
                    "descriptor-relative directory handle could not be closed",
                ) from error


@contextmanager
def _held_directory_scan(
    path: Path,
    code: str,
    *,
    expected_identities: Sequence[Mapping[str, object]] | None = None,
    expected_leaf_identity: Mapping[str, object] | None = None,
):
    with _held_local_directory(
        path,
        code,
        expected_identities=expected_identities,
        expected_leaf_identity=expected_leaf_identity,
    ) as held:
        try:
            held.assert_stable()
            with _enumerate_held_directory(path, held, code) as scanner:
                yield held, scanner
            held.assert_stable()
        except BootstrapError:
            raise
        except Exception as error:
            raise BootstrapError(code, f"local directory scan failed: {path}") from error


@contextmanager
def _held_file_handle(path: Path, parent: _HeldDirectory, code: str):
    try:
        from run_beta3_live_guard import _open_bound_handle
    except Exception as error:
        raise BootstrapError(code, f"local file is unavailable: {path}") from error
    descriptor: int | None = None
    try:
        descriptor, identity = _open_bound_handle(
            Path(path),
            code,
            parent=parent,
        )
        yield descriptor, identity
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError(code, f"local file is unavailable: {path}") from error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as error:
                raise BootstrapError(code, f"local file handle could not be closed: {path}") from error


def _held_file_snapshot(
    path: Path,
    parent: _HeldDirectory,
    code: str,
    *,
    entry: object | None = None,
    entry_stat: os.stat_result | None = None,
) -> _FileSnapshot:
    try:
        from run_beta3_live_guard import (
            _identity_matches,
            _read_held_bytes,
            _windows_handle_identity,
        )
        with _held_file_handle(path, parent, code) as (descriptor, identity):
            if (
                entry is not None
                and entry_stat is not None
                and not _entry_identity_matches(entry, entry_stat, identity)
            ):
                raise OSError("opened file identity is not the enumerated entry")
            content = _read_held_bytes(descriptor, code)
            after_identity = _windows_handle_identity(descriptor, code, directory=False)
            if (
                not _identity_matches(after_identity, identity)
                or after_identity.get("st_size") != identity.get("st_size")
            ):
                raise OSError("file changed during held read")
            parent.assert_stable()
            return _file_snapshot_from_held(path, content, identity)
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError(code, f"local file is unavailable: {path}") from error


def _validate_held_file(
    path: Path,
    parent: _HeldDirectory,
    code: str,
    *,
    entry: object | None = None,
    entry_stat: os.stat_result | None = None,
) -> None:
    with _held_file_handle(path, parent, code) as (_descriptor, identity):
        if (
            entry is not None
            and entry_stat is not None
            and not _entry_identity_matches(entry, entry_stat, identity)
        ):
            raise OSError("opened file identity is not the enumerated entry")
        parent.assert_stable()


def _held_child_directory_identities(
    path: Path,
    parent: _HeldDirectory,
    code: str,
    *,
    entry: object | None = None,
    entry_stat: os.stat_result | None = None,
) -> tuple[Mapping[str, object], ...]:
    try:
        from run_beta3_live_guard import _open_path_handle, _windows_handle_identity
    except Exception as error:
        raise BootstrapError(code, f"local directory is unavailable: {path}") from error
    descriptor: int | None = None
    try:
        parent.assert_stable()
        descriptor = _open_path_handle(
            Path(path.name),
            code,
            directory=True,
            parent=parent.descriptor,
        )
        identity = _windows_handle_identity(descriptor, code, directory=True)
        if (
            entry is not None
            and entry_stat is not None
            and not _entry_identity_matches(entry, entry_stat, identity)
        ):
            raise OSError("opened directory identity is not the enumerated entry")
        parent.assert_stable()
        return tuple((*parent.component_identities, identity))
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError(code, f"local directory is unavailable: {path}") from error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as error:
                raise BootstrapError(code, f"local directory handle could not be closed: {path}") from error


def _validate_missing_local_path(path: Path, code: str) -> None:
    candidate = _absolute_local_path(Path(path)).parent
    while True:
        try:
            identity = candidate.lstat()
        except FileNotFoundError:
            parent = candidate.parent
            if parent == candidate:
                _fail(code, f"local path has no existing ancestor: {path}")
            candidate = parent
            continue
        except OSError as error:
            raise BootstrapError(code, f"local path is unavailable: {path}") from error
        if stat.S_ISLNK(identity.st_mode) or _is_reparse(identity):
            _fail(code, f"local path has a reparse ancestor: {path}")
        if not stat.S_ISDIR(identity.st_mode):
            _fail(code, f"local path ancestor is not a directory: {candidate}")
        with _held_local_directory(candidate, code):
            return


def _canonical_local_directory(path: Path, code: str) -> Path:
    path = Path(path)
    try:
        identity = path.lstat()
        if stat.S_ISLNK(identity.st_mode) or _is_reparse(identity):
            raise OSError("directory is a link or reparse point")
        if not stat.S_ISDIR(identity.st_mode):
            raise OSError("path is not a directory")
        with _held_local_directory(path, code):
            return _absolute_local_path(path)
    except BootstrapError:
        raise
    except (OSError, ValueError) as error:
        raise BootstrapError(code, f"local directory is unavailable: {path}") from error


def _local_tree_snapshots(
    root: Path,
    code: str,
    *,
    allow_missing: bool = False,
) -> tuple[_FileSnapshot, ...]:
    root = _absolute_local_path(Path(root))
    root_identity: Mapping[str, object] | None = None
    try:
        if os.path.lexists(root):
            root_identity = _held_directory_identity(root, code)
        identity = root.lstat()
    except FileNotFoundError:
        if allow_missing:
            _validate_missing_local_path(root, code)
            return ()
        raise BootstrapError(code, f"local directory is unavailable: {root}")
    except OSError as error:
        raise BootstrapError(code, f"local directory is unavailable: {root}") from error
    if stat.S_ISLNK(identity.st_mode) or _is_reparse(identity):
        _fail(code, f"local directory is a link or reparse point: {root}")
    if not stat.S_ISDIR(identity.st_mode):
        _fail(code, f"local path is not a directory: {root}")
    if root_identity is None:
        root_identity = _held_directory_identity(root, code)
    result: list[_FileSnapshot] = []
    pending: list[
        tuple[
            Path,
            tuple[Mapping[str, object], ...] | None,
            Mapping[str, object] | None,
        ]
    ] = [(root, None, root_identity)]
    while pending:
        directory, expected_identities, expected_leaf_identity = pending.pop()
        with _held_directory_scan(
            directory,
            code,
            expected_identities=expected_identities,
            expected_leaf_identity=expected_leaf_identity,
        ) as (held, scanner):
            for entry in sorted(scanner, key=lambda item: item.name):
                path = directory / entry.name
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError as error:
                    raise BootstrapError(code, f"local entry is unavailable: {path}") from error
                held.assert_stable()
                if stat.S_ISLNK(entry_stat.st_mode) or _is_reparse(entry_stat):
                    _fail(code, f"local entry is a link or reparse point: {path}")
                if stat.S_ISDIR(entry_stat.st_mode):
                    child_identities = _held_child_directory_identities(
                        path,
                        held,
                        code,
                        entry=entry,
                        entry_stat=entry_stat,
                    )
                    if entry.name != "__pycache__":
                        pending.append((path, child_identities, None))
                elif stat.S_ISREG(entry_stat.st_mode):
                    if "__pycache__" not in path.parts and path.suffix != ".pyc":
                        result.append(
                            _held_file_snapshot(
                                path,
                                held,
                                code,
                                entry=entry,
                                entry_stat=entry_stat,
                            )
                        )
                    else:
                        _validate_held_file(
                            path,
                            held,
                            code,
                            entry=entry,
                            entry_stat=entry_stat,
                        )
                else:
                    _fail(code, f"local entry is not a regular file or directory: {path}")
    return tuple(sorted(result, key=lambda snapshot: _path_text(snapshot.path)))


def _local_tree_files(
    root: Path,
    code: str,
    *,
    allow_missing: bool = False,
) -> tuple[Path, ...]:
    return tuple(
        snapshot.path
        for snapshot in _local_tree_snapshots(
            root,
            code,
            allow_missing=allow_missing,
        )
    )


def _local_file_if_present(path: Path, code: str) -> Path | None:
    path = _absolute_local_path(Path(path))
    try:
        identity = path.lstat()
    except FileNotFoundError:
        _validate_missing_local_path(path, code)
        return None
    except OSError as error:
        raise BootstrapError(code, f"local file is unavailable: {path}") from error
    if stat.S_ISLNK(identity.st_mode) or _is_reparse(identity):
        _fail(code, f"local file is a link or reparse point: {path}")
    if not stat.S_ISREG(identity.st_mode):
        _fail(code, f"local path is not a regular file: {path}")
    _held_file_bytes(path, code)
    return path


def _audited_files(root: Path) -> tuple[Path, ...]:
    return tuple(snapshot.path for snapshot in _audited_file_snapshots(root))


def _audited_file_snapshots(root: Path) -> tuple[_FileSnapshot, ...]:
    root = _canonical_local_directory(root, "STATIC_INPUT_SOURCE_UNAVAILABLE")
    snapshots: list[_FileSnapshot] = []
    for candidate in (
        root / "skills" / "implement-gwo" / "SKILL.md",
        root / "skills" / "orchestrator" / "SKILL.md",
    ):
        path = _local_file_if_present(
            candidate,
            "STATIC_INPUT_SOURCE_UNAVAILABLE",
        )
        if path is not None:
            snapshots.append(
                _read_file_snapshot(path, "STATIC_INPUT_SOURCE_UNAVAILABLE")
            )
    snapshots.extend(
        snapshot
        for snapshot in _local_tree_snapshots(
            root / "skills" / "orchestrator" / "scripts" / "gwo_v8",
            "STATIC_INPUT_SOURCE_UNAVAILABLE",
            allow_missing=True,
        )
        if snapshot.path.suffix == ".py"
    )
    return tuple(
        sorted(
            snapshots,
            key=lambda snapshot: snapshot.path.relative_to(root).as_posix(),
        )
    )


def _checkout_source_snapshots(
    root: Path,
    subject: CutoverSubject,
) -> tuple[_FileSnapshot, ...]:
    root = _canonical_local_directory(Path(root), "STATIC_INPUT_SOURCE_UNAVAILABLE")
    snapshots = list(_audited_file_snapshots(root))
    for package_name in subject.package_names:
        package = root / "skills" / package_name
        if not package.is_dir():
            package = root / package_name
        snapshots.extend(
            _local_tree_snapshots(package, "STATIC_INPUT_SOURCE_UNAVAILABLE")
        )
    unique: dict[Path, _FileSnapshot] = {}
    for snapshot in snapshots:
        previous = unique.get(snapshot.path)
        if previous is not None and previous != snapshot:
            _fail(
                "STATIC_INPUT_SOURCE_UNAVAILABLE",
                f"checkout file was changed during held traversal: {snapshot.path}",
            )
        unique[snapshot.path] = snapshot
    return tuple(
        sorted(
            unique.values(),
            key=lambda snapshot: snapshot.path.relative_to(root).as_posix(),
        )
    )


def _checkout_source_files(root: Path, subject: CutoverSubject) -> tuple[Path, ...]:
    return tuple(snapshot.path for snapshot in _checkout_source_snapshots(root, subject))


def _snapshot_files(paths: Sequence[Path], code: str) -> tuple[_FileSnapshot, ...]:
    return tuple(_read_file_snapshot(path, code) for path in paths)


def _require_stable_snapshots(
    before: Sequence[_FileSnapshot],
    after: Sequence[_FileSnapshot],
    code: str,
) -> None:
    before_values = tuple(
        (snapshot.path, snapshot.identity, snapshot.content) for snapshot in before
    )
    after_values = tuple(
        (snapshot.path, snapshot.identity, snapshot.content) for snapshot in after
    )
    if before_values != after_values:
        _fail(code, "validated file set changed before SourceRecord creation")


def _snapshot_tree_digest(root: Path, snapshots: Sequence[_FileSnapshot]) -> str:
    digest = hashlib.sha256()
    resolved_root = _absolute_local_path(root)
    for snapshot in snapshots:
        relative = snapshot.path.relative_to(resolved_root).as_posix().encode("utf-8")
        content = snapshot.content.replace(b"\r\n", b"\n")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _static_records(
    root: Path,
    *,
    repository: str,
    producer_sha256: str,
    role: str,
    source_commit: str,
    source_tree_digest: str,
    readback_digest: str,
    snapshots: Sequence[_FileSnapshot] | None = None,
) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    observed = (
        tuple(snapshots)
        if snapshots is not None
        else _audited_file_snapshots(root)
    )
    snapshot_tree_sha256 = _snapshot_tree_digest(root, observed)
    for snapshot in observed:
        relative = snapshot.path.relative_to(_absolute_local_path(root)).as_posix()
        records.append(
            _source_record(
                role=role,
                locator=str(snapshot.path),
                repository=repository,
                read_mode="FIXED_COMMIT_TREE",
                identity={
                    **dict(snapshot.identity),
                    "commit_oid": source_commit,
                    "relative_path": relative,
                    "snapshot_tree_sha256": snapshot_tree_sha256,
                    "tree_digest": source_tree_digest,
                },
                payload=snapshot.content,
                producer_sha256=producer_sha256,
                readback_digest=readback_digest,
            )
        )
    if not records:
        _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "audited static input set is empty")
    return records


def _read_stable_static_inputs(
    root: Path,
    subject: CutoverSubject,
    *,
    producer_sha256: str,
) -> tuple[CompatibilityPathReadback, list[SourceRecord]]:
    root = _canonical_local_directory(Path(root), "STATIC_INPUT_SOURCE_UNAVAILABLE")
    before = _audited_file_snapshots(root)
    if not before:
        _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "audited static input set is empty")
    snapshot_tree_sha256 = _snapshot_tree_digest(root, before)
    scan_subject = replace(subject, source_tree_digest=snapshot_tree_sha256)
    try:
        scanned = ProductionPathScanner(root).read(scan_subject)
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError(
            "STATIC_INPUT_SOURCE_UNAVAILABLE",
            "production path scan failed",
        ) from error
    _validate_readback(
        scanned,
        CompatibilityPathReadback,
        code="STATIC_INPUT_SOURCE_UNAVAILABLE",
        repository=subject.repository,
    )
    if (
        scanned.source_commit != subject.source_commit
        or scanned.source_tree_digest != snapshot_tree_sha256
    ):
        _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "compatibility readback source identity changed")
    after = _audited_file_snapshots(root)
    _require_stable_snapshots(before, after, "STATIC_INPUT_SOURCE_UNAVAILABLE")
    readback = replace(
        scanned,
        source_tree_digest=subject.source_tree_digest,
        readback_digest="",
    )
    readback_values = readback.canonical()
    readback_values.pop("readback_digest")
    readback = replace(readback, readback_digest=digest_value(readback_values))
    records = _static_records(
        root,
        repository=subject.repository,
        producer_sha256=producer_sha256,
        role="compatibility.module",
        source_commit=subject.source_commit,
        source_tree_digest=subject.source_tree_digest,
        readback_digest=readback.readback_digest,
        snapshots=before,
    )
    return readback, records


def _install_roots(config: object, subject: CutoverSubject) -> dict[str, Path]:
    raw = config.install_roots
    if isinstance(raw, Mapping):
        if tuple(raw) != subject.install_surfaces:
            _fail("PACKAGE_SOURCE_UNAVAILABLE", "install surfaces are not in the fixed order")
        result = {surface: Path(raw[surface]) for surface in subject.install_surfaces}
    elif type(raw) in (tuple, list):
        if len(raw) != len(subject.install_surfaces):
            _fail("PACKAGE_SOURCE_UNAVAILABLE", "install surfaces are incomplete")
        result = dict(zip(subject.install_surfaces, (Path(item) for item in raw), strict=True))
    else:
        _fail("PACKAGE_SOURCE_UNAVAILABLE", "install surfaces are absent")
    if tuple(result) != subject.install_surfaces:
        _fail("PACKAGE_SOURCE_UNAVAILABLE", "install surfaces are not in the fixed order")
    return result


def _package_records(
    root: Path,
    install_roots: Mapping[str, Path],
    subject: CutoverSubject,
    *,
    producer_sha256: str,
    readback_digest: str,
    snapshots: Sequence[tuple[str, Path, _FileSnapshot]] | None = None,
) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    observed = (
        tuple(snapshots)
        if snapshots is not None
        else _package_file_snapshots(root, install_roots, subject)
    )
    for label, package, snapshot in observed:
        path = snapshot.path
        records.append(
            _source_record(
                role="package.file",
                locator=str(path),
                repository=subject.repository,
                read_mode="FIXED_PACKAGE_FILE",
                identity={
                    **dict(snapshot.identity),
                    "commit_oid": subject.source_commit,
                    "package": label,
                    "relative_path": path.relative_to(package).as_posix(),
                    "tree_digest": subject.source_tree_digest,
                },
                payload=snapshot.content,
                producer_sha256=producer_sha256,
                readback_digest=readback_digest,
            )
        )
    if not records:
        _fail("PACKAGE_SOURCE_UNAVAILABLE", "package provenance is empty")
    return records


def _package_paths(
    root: Path,
    install_roots: Mapping[str, Path],
    subject: CutoverSubject,
) -> tuple[tuple[str, Path], ...]:
    package_paths: list[tuple[str, Path]] = []
    for package_name in subject.package_names:
        source = root / "skills" / package_name
        if not source.is_dir():
            source = root / package_name
        package_paths.append(
            (
                f"source:{package_name}",
                _canonical_local_directory(source, "PACKAGE_SOURCE_UNAVAILABLE"),
            )
        )
        for surface, install_root in install_roots.items():
            installed = install_root / package_name
            package_paths.append(
                (
                    f"{surface}:{package_name}",
                    _canonical_local_directory(installed, "PACKAGE_SOURCE_UNAVAILABLE"),
                )
            )
    return tuple(package_paths)


def _package_file_snapshots(
    root: Path,
    install_roots: Mapping[str, Path],
    subject: CutoverSubject,
) -> tuple[tuple[str, Path, _FileSnapshot], ...]:
    snapshots: list[tuple[str, Path, _FileSnapshot]] = []
    package_paths = _package_paths(root, install_roots, subject)
    for label, package in package_paths:
        manifest = package / ".skill-package.json"
        if _local_file_if_present(manifest, "PACKAGE_SOURCE_UNAVAILABLE") is None:
            raise BootstrapError(
                "PACKAGE_SOURCE_UNAVAILABLE", f"package manifest is unavailable: {manifest}"
            )
        files = _local_tree_snapshots(package, "PACKAGE_SOURCE_UNAVAILABLE")
        if not files:
            _fail("PACKAGE_SOURCE_UNAVAILABLE", f"package file set is empty: {package}")
        snapshots.extend((label, package, snapshot) for snapshot in files)
    return tuple(snapshots)


def _package_content_digest(
    package: Path,
    snapshots: Sequence[_FileSnapshot],
) -> str:
    digest = hashlib.sha256()
    for snapshot in sorted(
        (item for item in snapshots if item.path.name != ".skill-package.json"),
        key=lambda item: item.path.relative_to(package).as_posix(),
    ):
        relative = snapshot.path.relative_to(package).as_posix().encode("utf-8")
        content = snapshot.content
        if snapshot.path.suffix.lower() in {
            ".json",
            ".md",
            ".py",
            ".toml",
            ".txt",
            ".yaml",
            ".yml",
        }:
            content = content.replace(b"\r\n", b"\n")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _snapshot_package_identities(
    snapshots: Sequence[tuple[str, Path, _FileSnapshot]],
) -> tuple[tuple[PackageIdentity, ...], tuple[PackageIdentity, ...]]:
    grouped: dict[tuple[str, Path], list[_FileSnapshot]] = {}
    for label, package, snapshot in snapshots:
        grouped.setdefault((label, package), []).append(snapshot)
    source: list[PackageIdentity] = []
    installed: list[PackageIdentity] = []
    for (label, package), files in grouped.items():
        manifests = [item for item in files if item.path.name == ".skill-package.json"]
        if len(manifests) != 1:
            _fail("PACKAGE_SOURCE_UNAVAILABLE", "package manifest snapshot is not unique")
        try:
            manifest = json.loads(manifests[0].content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise BootstrapError("PACKAGE_SOURCE_UNAVAILABLE", "package manifest is malformed") from error
        package_name = label.split(":", 1)[1]
        if (
            type(manifest) is not dict
            or type(manifest.get("version")) is not str
            or not manifest["version"]
        ):
            _fail("PACKAGE_SOURCE_UNAVAILABLE", "package manifest identity is absent")
        surface = None if label.startswith("source:") else label.split(":", 1)[0]
        identity = PackageIdentity(
            package_name=package_name,
            version=manifest["version"],
            content_digest=_package_content_digest(package, files),
            manifest_content_digest=digest_bytes(manifests[0].content),
            install_surface=surface,
        )
        (source if surface is None else installed).append(identity)
    def sort_key(item: PackageIdentity) -> tuple[int, str]:
        surface_index = (
            -1
            if item.install_surface is None
            else (".agents", ".codex", ".claude").index(item.install_surface)
        )
        return surface_index, item.package_name

    return tuple(sorted(source, key=sort_key)), tuple(sorted(installed, key=sort_key))


def _read_stable_package_inputs(
    root: Path,
    install_roots: Mapping[str, Path],
    subject: CutoverSubject,
    *,
    producer_sha256: str,
) -> tuple[PackageReadback, list[SourceRecord]]:
    root = _canonical_local_directory(Path(root), "PACKAGE_SOURCE_UNAVAILABLE")
    before = _package_file_snapshots(root, install_roots, subject)
    if not before:
        _fail("PACKAGE_SOURCE_UNAVAILABLE", "package provenance is empty")
    expected_source, expected_installed = _snapshot_package_identities(before)
    try:
        readback = ReadOnlyPackageValidator(root, install_roots).read(subject)
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError("PACKAGE_SOURCE_UNAVAILABLE", "package readback failed") from error
    _validate_readback(readback, PackageReadback, code="PACKAGE_SOURCE_UNAVAILABLE")
    after = _package_file_snapshots(root, install_roots, subject)
    _require_stable_snapshots(
        tuple(item[2] for item in before),
        tuple(item[2] for item in after),
        "PACKAGE_SOURCE_UNAVAILABLE",
    )
    if (
        readback.source_packages != expected_source
        or readback.installed_packages != expected_installed
    ):
        _fail("PACKAGE_SOURCE_UNAVAILABLE", "package readback is not bound to file snapshots")
    records = _package_records(
        root,
        install_roots,
        subject,
        producer_sha256=producer_sha256,
        readback_digest=readback.readback_digest,
        snapshots=before,
    )
    return readback, records


def _validate_checkout_file_bindings(
    checkout: SourceObservation,
    root: Path,
    static_records: Sequence[SourceRecord],
    package_records: Sequence[SourceRecord],
) -> None:
    code = "STATIC_INPUT_SOURCE_UNAVAILABLE"
    try:
        value = load_canonical_json(checkout.canonical_payload)
        files = value["files"]
        expected = {item["relative_path"]: item["byte_sha256"] for item in files}
    except (KeyError, TypeError, ValueError) as error:
        raise BootstrapError(code, "checkout file manifest is malformed") from error
    observed: dict[str, str] = {}
    root = _absolute_local_path(Path(root))

    def bind(record: SourceRecord, relative: str) -> None:
        identity = dict(record.identity)
        digest = identity.get("byte_sha256")
        if (
            type(relative) is not str
            or not relative
            or type(digest) is not str
            or _HEX64.fullmatch(digest) is None
            or (relative in observed and observed[relative] != digest)
        ):
            _fail(code, "checkout-bound SourceRecord identity is malformed")
        observed[relative] = digest

    for record in static_records:
        if record.role != "compatibility.module":
            _fail(code, "compatibility SourceRecord role changed")
        bind(record, dict(record.identity).get("relative_path"))
    for record in package_records:
        identity = dict(record.identity)
        package = identity.get("package")
        if type(package) is not str or not package.startswith("source:"):
            continue
        try:
            relative = _absolute_local_path(Path(record.locator)).relative_to(root).as_posix()
        except (OSError, TypeError, ValueError) as error:
            raise BootstrapError(code, "source package path escapes the checkout") from error
        bind(record, relative)
    if observed != expected:
        _fail(code, "static or source-package files differ from the checkout observation")


def _validate_config_subject(
    config: object,
    subject: CutoverSubject,
    release_subject: ReleaseSubject,
) -> None:
    required_fields = {
        "repository": "STATIC_INPUT_SOURCE_UNAVAILABLE",
        "control_branch": "STATIC_INPUT_SOURCE_UNAVAILABLE",
        "target_branch": "STATIC_INPUT_SOURCE_UNAVAILABLE",
        "source_writer_generation": "STATIC_INPUT_SOURCE_UNAVAILABLE",
        "target_writer_generation": "STATIC_INPUT_SOURCE_UNAVAILABLE",
        "merged_main_sha": "STATIC_INPUT_SOURCE_UNAVAILABLE",
        "merged_main_git_tree": "STATIC_INPUT_SOURCE_UNAVAILABLE",
        "audited_source_tree_digest": "STATIC_INPUT_SOURCE_UNAVAILABLE",
        "repository_root": "STATIC_INPUT_SOURCE_UNAVAILABLE",
        "fresh_store": "STORE_SOURCE_UNAVAILABLE",
        "expected_fresh_store_sha256": "STORE_SOURCE_UNAVAILABLE",
        "fresh_receipt": "STORE_SOURCE_UNAVAILABLE",
        "expected_fresh_receipt_sha256": "STORE_SOURCE_UNAVAILABLE",
        "expected_fresh_receipt_runbook_sha256": "STORE_SOURCE_UNAVAILABLE",
        "expected_fresh_receipt_schema_digest": "STORE_SOURCE_UNAVAILABLE",
        "expected_fresh_receipt_generation_rows": "STORE_SOURCE_UNAVAILABLE",
        "expected_fresh_receipt_row_counts": "STORE_SOURCE_UNAVAILABLE",
        "rollback_store": "STORE_SOURCE_UNAVAILABLE",
        "expected_rollback_store_sha256": "STORE_SOURCE_UNAVAILABLE",
        "prior_store": "STORE_SOURCE_UNAVAILABLE",
        "expected_prior_store_sha256": "STORE_SOURCE_UNAVAILABLE",
        "runtime_config_path": "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE",
        "store_generation": "STORE_SOURCE_UNAVAILABLE",
        "expected_store_tables": "STORE_SOURCE_UNAVAILABLE",
        "install_roots": "PACKAGE_SOURCE_UNAVAILABLE",
        "package_names": "PACKAGE_SOURCE_UNAVAILABLE",
        "expected_package_version": "PACKAGE_SOURCE_UNAVAILABLE",
        "expected_package_content_digests": "PACKAGE_SOURCE_UNAVAILABLE",
    }
    for name, code in required_fields.items():
        if not hasattr(config, name):
            _fail(code, f"fixed configuration {name} is absent")
    if type(release_subject) is not ReleaseSubject:
        _fail("COMPONENT_INVALID", "release subject must be one exact typed value")
    if subject.required_runtime_selectors != RUNTIME_SELECTORS:
        _fail(
            "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE",
            "subject Runtime selectors are not the fixed current contract",
        )
    if (
        type(subject.store_generation) is not str
        or _STORE_GENERATION.fullmatch(subject.store_generation) is None
        or type(config.store_generation) is not str
        or _STORE_GENERATION.fullmatch(config.store_generation) is None
    ):
        _fail("STORE_SOURCE_UNAVAILABLE", "Store generation is malformed")
    expected_values = {
        "repository": subject.repository,
        "control_branch": subject.control_branch,
        "target_branch": subject.target_branch,
        "source_writer_generation": subject.source_writer_generation,
        "target_writer_generation": subject.target_writer_generation,
        "store_generation": subject.store_generation,
    }
    for name, expected in expected_values.items():
        if getattr(config, name) != expected:
            _fail(
                "STATIC_INPUT_SOURCE_UNAVAILABLE",
                f"fixed configuration {name} is not bound to the subject",
            )
    if config.merged_main_sha != release_subject.merged_main_sha:
        _fail(
            "STATIC_INPUT_SOURCE_UNAVAILABLE",
            "config merged_main_sha is not manifest-bound",
        )
    if config.merged_main_git_tree != release_subject.merged_main_git_tree:
        _fail(
            "STATIC_INPUT_SOURCE_UNAVAILABLE",
            "config merged_main_git_tree is not manifest-bound",
        )
    if config.audited_source_tree_digest != release_subject.audited_source_tree_digest:
        _fail(
            "STATIC_INPUT_SOURCE_UNAVAILABLE",
            "config audited source digest is not manifest-bound",
        )
    if subject.source_commit != release_subject.merged_main_sha:
        _fail(
            "STATIC_INPUT_SOURCE_UNAVAILABLE",
            "CutoverSubject source commit is not manifest-bound",
        )
    if subject.source_tree_digest != release_subject.audited_source_tree_digest:
        _fail(
            "STATIC_INPUT_SOURCE_UNAVAILABLE",
            "CutoverSubject audited source digest is not manifest-bound",
        )
    if subject.repository != release_subject.repository:
        _fail(
            "STATIC_INPUT_SOURCE_UNAVAILABLE",
            "CutoverSubject repository is not manifest-bound",
        )
    if config.repository != release_subject.repository:
        _fail(
            "STATIC_INPUT_SOURCE_UNAVAILABLE",
            "config repository is not manifest-bound",
        )
    fixed_subject_values = {
        "control_branch": "gwo-control",
        "target_branch": "main",
        "source_writer_generation": "v6.1",
        "target_writer_generation": "v8",
        "required_runtime_selectors": RUNTIME_SELECTORS,
        "package_names": ("implement-gwo", "orchestrator"),
        "install_surfaces": (".agents", ".codex", ".claude"),
    }
    for name, expected in fixed_subject_values.items():
        if getattr(subject, name) != expected:
            _fail(
                "STATIC_INPUT_SOURCE_UNAVAILABLE",
                f"subject {name} is not the fixed current-main contract",
            )
    if (
        type(config.merged_main_sha) is not str
        or _HEX40.fullmatch(config.merged_main_sha) is None
    ):
        _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "config merged main SHA is malformed")
    if (
        type(config.merged_main_git_tree) is not str
        or _HEX40.fullmatch(config.merged_main_git_tree) is None
    ):
        _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "config merged main Git tree is malformed")
    if (
        type(config.audited_source_tree_digest) is not str
        or _HEX64.fullmatch(config.audited_source_tree_digest) is None
    ):
        _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "config audited source digest is malformed")
    if (
        type(subject.source_commit) is not str
        or _HEX40.fullmatch(subject.source_commit) is None
    ):
        _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "subject source commit is malformed")
    if (
        type(subject.source_tree_digest) is not str
        or _HEX64.fullmatch(subject.source_tree_digest) is None
    ):
        _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "subject audited source digest is malformed")
    for name in (
        "expected_fresh_store_sha256",
        "expected_fresh_receipt_sha256",
        "expected_fresh_receipt_runbook_sha256",
        "expected_fresh_receipt_schema_digest",
        "expected_rollback_store_sha256",
        "expected_prior_store_sha256",
    ):
        _require_digest(getattr(config, name), name, "STORE_SOURCE_UNAVAILABLE")
    for name in (
        "repository_root",
        "fresh_store",
        "fresh_receipt",
        "rollback_store",
        "prior_store",
        "runtime_config_path",
    ):
        try:
            _absolute_local_path(Path(getattr(config, name)))
        except (OSError, TypeError, ValueError) as error:
            raise BootstrapError(
                "STATIC_INPUT_SOURCE_UNAVAILABLE",
                f"fixed configuration {name} is not a path",
            ) from error
    package_names = getattr(config, "package_names")
    if type(package_names) is not tuple or package_names != subject.package_names:
        _fail("PACKAGE_SOURCE_UNAVAILABLE", "configured package names are not exact")
    if getattr(config, "expected_package_version") != "8.0.0":
        _fail("PACKAGE_SOURCE_UNAVAILABLE", "configured package version is not exact")
    if type(getattr(config, "expected_store_tables")) is not tuple:
        _fail("STORE_SOURCE_UNAVAILABLE", "configured Store tables are malformed")
    generation_rows = getattr(config, "expected_fresh_receipt_generation_rows")
    if type(generation_rows) is not tuple:
        _fail("STORE_SOURCE_UNAVAILABLE", "configured Store generation rows are malformed")
    if (
        len(generation_rows) != 1
        or type(generation_rows[0]) is not tuple
        or len(generation_rows[0]) != 2
        or generation_rows[0] != (subject.repository, config.store_generation)
    ):
        _fail("STORE_SOURCE_UNAVAILABLE", "configured Store generation rows are not subject-bound")
    row_counts = getattr(config, "expected_fresh_receipt_row_counts")
    if type(row_counts) is not tuple or any(
        type(row) is not tuple
        or len(row) != 2
        or type(row[0]) is not str
        or not row[0]
        or type(row[1]) is not int
        or row[1] < 0
        for row in row_counts
    ) or len({row[0] for row in row_counts}) != len(row_counts):
        _fail("STORE_SOURCE_UNAVAILABLE", "configured Store row counts are malformed")
    _install_roots(config, subject)
    try:
        package_content = dict(config.expected_package_content_digests)
    except (TypeError, ValueError) as error:
        raise BootstrapError(
            "PACKAGE_SOURCE_UNAVAILABLE",
            "configured package content identities are malformed",
        ) from error
    if set(package_content) != set(subject.package_names) or any(
        type(value) is not str or _HEX64.fullmatch(value) is None
        for value in package_content.values()
    ):
        _fail("PACKAGE_SOURCE_UNAVAILABLE", "configured package content identities are incomplete")
    if subject.repository == PRODUCTION_REPOSITORY:
        release_receipt_digest = _require_digest(
            getattr(release_subject, "fresh_receipt_sha256", None),
            "release subject fresh receipt digest",
            "STORE_SOURCE_UNAVAILABLE",
        )
        if config.expected_fresh_receipt_sha256 != release_receipt_digest:
            _fail(
                "STORE_SOURCE_UNAVAILABLE",
                "production fresh receipt digest is not release-subject-bound",
            )
        try:
            fresh_store = Path(config.fresh_store)
        except (OSError, TypeError, ValueError) as error:
            raise BootstrapError(
                "STORE_SOURCE_UNAVAILABLE",
                "production fresh Store path is malformed",
            ) from error
        canonical_fresh_store_directory = PRODUCTION_STORE.parent
        if (
            fresh_store.parent != canonical_fresh_store_directory
            or _PRODUCTION_FRESH_STORE_NAME.fullmatch(fresh_store.name) is None
        ):
            _fail(
                "STORE_SOURCE_UNAVAILABLE",
                "production fresh Store path is not a controlled canonical file",
            )
        production_store_tables, _ = _fixed_store_contract()
        production_config = {
            "repository_root": PRODUCTION_REPOSITORY_ROOT,
            "fresh_receipt": PRODUCTION_RECEIPT,
            "expected_fresh_receipt_sha256": release_receipt_digest,
            "runtime_config_path": PRODUCTION_RUNTIME_CONFIG,
            "rollback_store": PRODUCTION_ROLLBACK_STORE,
            "expected_rollback_store_sha256": PRODUCTION_ROLLBACK_STORE_SHA256,
            "prior_store": PRODUCTION_PRIOR_STORE,
            "expected_prior_store_sha256": PRODUCTION_PRIOR_STORE_SHA256,
            "expected_fresh_receipt_runbook_sha256": PRODUCTION_RECEIPT_RUNBOOK_SHA256,
            "expected_fresh_receipt_schema_digest": PRODUCTION_RECEIPT_SCHEMA_DIGEST,
            "expected_store_tables": production_store_tables,
            "expected_fresh_receipt_row_counts": tuple(
                (table, 1 if table == "v8_writer_generations" else 0)
                for table in production_store_tables
            ),
            "install_roots": PRODUCTION_INSTALL_ROOTS,
            "expected_package_content_digests": PRODUCTION_PACKAGE_CONTENT_DIGESTS,
        }
        for name, expected in production_config.items():
            actual = getattr(config, name)
            if isinstance(expected, Path):
                try:
                    matches = Path(actual) == expected
                except (OSError, TypeError, ValueError):
                    matches = False
            elif name == "install_roots":
                try:
                    matches = tuple(Path(item) for item in actual) == expected
                except (OSError, TypeError, ValueError):
                    matches = False
            else:
                matches = actual == expected
            if not matches:
                _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", f"production configuration {name} changed")


def _validate_readback(
    value: object,
    expected_type: type,
    *,
    code: str,
    repository: str | None = None,
) -> None:
    if type(value) is not expected_type:
        _fail(code, "readback has the wrong exact current-main type")
    try:
        canonical = value.canonical()
        digest = canonical.get("readback_digest")
        body = dict(canonical)
        body.pop("readback_digest")
    except Exception as error:
        raise BootstrapError(code, "readback canonical projection is unavailable") from error
    if type(digest) is not str or digest_value(body) != digest:
        _fail(code, "readback digest is not bound to its canonical body")
    if repository is not None and canonical.get("repository") != repository:
        _fail(code, "readback repository differs from subject")


def _validate_package_identity_config(
    config: object,
    packages: PackageReadback,
    subject: CutoverSubject,
) -> None:
    try:
        expected = dict(config.expected_package_content_digests)
    except (TypeError, ValueError) as error:
        raise BootstrapError(
            "PACKAGE_SOURCE_UNAVAILABLE", "configured package content identities are malformed"
        ) from error
    source_by_name = {item.package_name: item for item in packages.source_packages}
    if set(expected) != set(subject.package_names):
        _fail("PACKAGE_SOURCE_UNAVAILABLE", "configured package content identities are incomplete")
    for package_name in subject.package_names:
        expected_digest = expected[package_name]
        if type(expected_digest) is not str or _HEX64.fullmatch(expected_digest) is None:
            _fail("PACKAGE_SOURCE_UNAVAILABLE", "configured package content identity is malformed")
        observed = source_by_name.get(package_name)
        if observed is None or observed.content_digest != expected_digest:
            _fail("PACKAGE_SOURCE_UNAVAILABLE", f"source package identity changed: {package_name}")
    expected_version = config.expected_package_version
    identities = (*packages.source_packages, *packages.installed_packages)
    if type(expected_version) is not str or not expected_version or any(
        item.version != expected_version for item in identities
    ):
        _fail("PACKAGE_SOURCE_UNAVAILABLE", "package version identity changed")


def _bindings(
    readbacks: Mapping[str, object],
    source_records: Sequence[SourceRecord],
    groups: Mapping[str, Sequence[SourceRecord]],
) -> tuple[FieldBinding, ...]:
    del source_records
    result: list[FieldBinding] = []
    for name, readback in readbacks.items():
        try:
            canonical = readback.canonical()
        except Exception as error:
            raise BootstrapError("COMPONENT_INVALID", f"{name} has no canonical projection") from error
        group_records = groups.get(name)
        if group_records is None:
            _fail("COMPONENT_INVALID", f"{name} has no source provenance group")
        group = tuple(sorted({record.digest for record in group_records}))
        if not group:
            _fail("COMPONENT_INVALID", f"{name} has no source provenance")
        for field in canonical:
            result.append(
                FieldBinding(
                    target=f"{name}.{field}",
                    source_record_digests=group,
                    derivation="source_readback" if field != "readback_digest" else "canonical_digest",
                )
            )
    return tuple(sorted(result, key=lambda item: item.target))


class ControlOwnershipAttestor:
    def __init__(self, sources: ControlOwnershipSourceSet) -> None:
        if type(sources) is not ControlOwnershipSourceSet:
            raise BootstrapError("COMPONENT_INVALID", "sources must be one exact source set")
        _validate_gwo_v8_provenance()
        self._sources = sources
        self._check_source(sources.control, ("read_ref", "read_at_oid"))
        self._check_source(sources.runtime_registry, ("read",))
        self._check_source(sources.runtime_config, ("read",))
        self._check_source(sources.local_inputs, ("read",))

    @staticmethod
    def _check_source(source: object, methods: tuple[str, ...]) -> None:
        try:
            source_type = type(source)
            if type(source_type) is not type:
                raise BootstrapError(
                    "UNSAFE_SOURCE_CAPABILITY",
                    "source type uses an unsupported custom metaclass",
                )

            source_mro = source_type.__mro__
            dynamic_resolution = False
            for cls in source_mro:
                if cls is object:
                    continue
                namespace = vars(cls)
                if any(name in namespace for name in ("__dir__", "__getattr__")):
                    dynamic_resolution = True
                    break
                getattribute = namespace.get("__getattribute__")
                if getattribute is not None and not inspect.ismethoddescriptor(getattribute):
                    dynamic_resolution = True
                    break
            if dynamic_resolution:
                raise BootstrapError(
                    "UNSAFE_SOURCE_CAPABILITY",
                    "source exposes dynamic attribute resolution",
                )

            exposed = {
                name
                for cls in source_mro
                for name in vars(cls)
                if not name.startswith("_")
            }
            instance_dict_descriptor_type = type(vars(type)["__dict__"])
            try:
                instance_dict_descriptor = inspect.getattr_static(source, "__dict__")
            except AttributeError:
                instance_namespace = {}
            else:
                if not (
                    type(instance_dict_descriptor) is instance_dict_descriptor_type
                    or inspect.ismemberdescriptor(instance_dict_descriptor)
                    or inspect.isgetsetdescriptor(instance_dict_descriptor)
                ):
                    raise BootstrapError(
                        "UNSAFE_SOURCE_CAPABILITY",
                        "source instance namespace uses an unsupported descriptor",
                    )
                instance_namespace = object.__getattribute__(source, "__dict__")
                if type(instance_namespace) is not dict:
                    raise BootstrapError(
                        "UNSAFE_SOURCE_CAPABILITY",
                        "source instance namespace is not an exact dictionary",
                    )
            if type(instance_namespace) is dict:
                exposed.update(
                    name
                    for name in instance_namespace
                    if type(name) is str and not name.startswith("_")
                )

            def unsupported_descriptor(value: object) -> bool:
                is_descriptor = any(
                    "__get__" in vars(value_type)
                    for value_type in type(value).__mro__
                )
                return is_descriptor and not inspect.isroutine(value)

            for name in sorted(exposed):
                attr = inspect.getattr_static(source, name)
                if unsupported_descriptor(attr):
                    raise BootstrapError(
                        "UNSAFE_SOURCE_CAPABILITY",
                        f"source exposes an unsupported descriptor: {name}",
                    )
                if name not in methods and (
                    callable(attr) or inspect.isroutine(attr)
                ):
                    raise BootstrapError(
                        "UNSAFE_SOURCE_CAPABILITY",
                        f"source exposes an unlisted public callable: {name}",
                    )

            for name in methods:
                try:
                    required_attr = inspect.getattr_static(source, name)
                except AttributeError:
                    required_attr = None
                if (
                    required_attr is None
                    or unsupported_descriptor(required_attr)
                    or not (
                        callable(required_attr) or inspect.isroutine(required_attr)
                    )
                ):
                    raise BootstrapError(
                        "UNSAFE_SOURCE_CAPABILITY",
                        "source does not expose the required read method",
                    )
        except BootstrapError:
            raise
        except Exception as error:
            raise BootstrapError("UNSAFE_SOURCE_CAPABILITY", "source capability cannot be inspected") from error

    def observe(
        self,
        *,
        config: object,
        subject: CutoverSubject,
        attempt: AttemptIdentity,
        release_subject: ReleaseSubject,
    ) -> ComponentObservation:
        if (
            type(subject) is not CutoverSubject
            or type(attempt) is not AttemptIdentity
            or type(release_subject) is not ReleaseSubject
        ):
            _fail(
                "COMPONENT_INVALID",
                "subject, attempt, and release subject must be exact current contracts",
            )
        if attempt.repository != subject.repository:
            _fail("COMPONENT_INVALID", "attempt and subject repositories differ")
        if attempt.cutover_subject_digest != digest_value(subject.canonical()):
            _fail("COMPONENT_INVALID", "attempt does not bind the cutover subject")
        _validate_config_subject(config, subject, release_subject)

        checkout = _read_source(
            self._sources.local_inputs,
            "read",
            (config, subject),
            role="local.inputs",
            repository=subject.repository,
            producer_sha256=attempt.attestor_sha256,
            default_locator=f"local-checkout://{_absolute_local_path(Path(config.repository_root))}",
            default_read_mode="EXACT_GIT_SNAPSHOT",
        )
        checkout_record = _validate_checkout_observation(checkout, config, subject)

        writer_fence, authority, control_records = _read_control(
            self._sources.control,
            subject=subject,
            attempt=attempt,
        )
        _validate_readback(
            writer_fence,
            WriterFenceReadback,
            code="WRITER_FENCE_SOURCE_UNAVAILABLE",
            repository=subject.repository,
        )
        if (
            authority.writer_generation != writer_fence.writer_generation
            or authority.record_id != writer_fence.record_id
            or authority.activation_id != writer_fence.activation_id
            or authority.authority_state != writer_fence.authority_state
            or set(authority.source_record_digests)
            != {record.digest for record in control_records}
        ):
            _fail("WRITER_FENCE_SOURCE_UNAVAILABLE", "writer authority observation is not source-bound")
        registry = _read_source(
            self._sources.runtime_registry,
            "read",
            (subject.repository,),
            role="runtime.registry",
            repository=subject.repository,
            producer_sha256=attempt.attestor_sha256,
            default_locator=f"runtime-registry://{subject.repository}",
            default_read_mode="COMPLETE_OBSERVATION",
        )
        registry_value = _load_canonical_object(registry.canonical_payload, "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE")
        runtime_refs = _registry_refs(registry_value)

        runtime_raw = _read_source(
            self._sources.runtime_config,
            "read",
            (Path(config.runtime_config_path),),
            role="runtime.config",
            repository=subject.repository,
            producer_sha256=attempt.attestor_sha256,
            default_locator=str(_absolute_local_path(Path(config.runtime_config_path))),
            default_read_mode="EXACT_FILE",
        )
        _validate_runtime_config_source(runtime_raw, Path(config.runtime_config_path))
        configuration, runtime = _runtime_config_value(runtime_raw.canonical_payload, subject.repository)
        if tuple(item.selector for item in runtime.selectors) != RUNTIME_SELECTORS:
            _fail("RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE", "Runtime selectors are not in fixed order")
        _validate_readback(
            runtime,
            RuntimePreflightReadback,
            code="RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE",
            repository=subject.repository,
        )
        del configuration

        store = _read_store(config, subject, attempt)
        _validate_readback(
            store.durable,
            DurableStateReadback,
            code="STORE_SOURCE_UNAVAILABLE",
            repository=subject.repository,
        )
        ownership_values = {
            "repository": subject.repository,
            "active_admissions": store.active_admissions,
            "active_attempts": store.active_attempts,
            "integration_lease_owner": store.integration_lease_owner,
            "runtime_resource_refs": tuple(sorted((*store.resource_claims, *runtime_refs))),
        }
        ownership = OwnershipReadback(
            **ownership_values,
            readback_digest=digest_value(ownership_values),
        )
        _validate_readback(
            ownership,
            OwnershipReadback,
            code="STORE_SOURCE_UNAVAILABLE",
            repository=subject.repository,
        )

        root = Path(config.repository_root)
        compatibility, static_records = _read_stable_static_inputs(
            root,
            subject,
            producer_sha256=attempt.attestor_sha256,
        )
        install_roots = _install_roots(config, subject)
        packages, package_records = _read_stable_package_inputs(
            root,
            install_roots,
            subject,
            producer_sha256=attempt.attestor_sha256,
        )
        _validate_package_identity_config(config, packages, subject)
        _validate_checkout_file_bindings(
            checkout,
            root,
            static_records,
            package_records,
        )
        readbacks: dict[str, object] = {
            "durable_state": store.durable,
            "writer_fence": writer_fence,
            "ownership": ownership,
            "compatibility": compatibility,
            "runtime": runtime,
            "packages": packages,
        }
        source_records = tuple(
            sorted(
                (*control_records, store.store_record, store.receipt_record, registry.record, runtime_raw.record, checkout_record, *static_records, *package_records),
                key=lambda record: record.digest,
            )
        )
        if len({record.digest for record in source_records}) != len(source_records):
            _fail("COMPONENT_INVALID", "component source record identities are duplicated")
        groups = {
            "durable_state": (store.store_record, store.receipt_record),
            "writer_fence": tuple(control_records),
            "ownership": (store.store_record, registry.record),
            "compatibility": (checkout_record, *static_records),
            "runtime": (runtime_raw.record,),
            "packages": (checkout_record, *package_records),
        }
        bindings = _bindings(readbacks, source_records, groups)
        return ComponentObservation(
            readbacks=tuple(readbacks.items()),
            source_records=source_records,
            field_bindings=bindings,
            writer_authority=authority,
        )


def production_control_ownership_sources(
    *,
    command_runner: Callable[[tuple[str, ...]], bytes],
    producer_sha256: str,
) -> ControlOwnershipSourceSet:
    if not callable(command_runner):
        _fail("UNSAFE_SOURCE_CAPABILITY", "command_runner must be callable")
    _require_digest(producer_sha256, "producer_sha256", "SOURCE_RECORD_INVALID")
    return ControlOwnershipSourceSet(
        control=_GitHubControlSource(command_runner),
        runtime_registry=_RuntimeRegistrySource(command_runner, producer_sha256),
        runtime_config=_RuntimeConfigSource(producer_sha256, PRODUCTION_REPOSITORY),
        local_inputs=_LocalInputsSource(command_runner, producer_sha256),
    )
