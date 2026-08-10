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
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
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
from gwo_v8._canonical import canonical_bytes, digest_bytes, digest_value, load_canonical_json
from gwo_v8.cutover_guard import (
    CompatibilityPathReadback,
    CutoverSubject,
    DurableStateReadback,
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
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_DYNAMIC_SIDE_FILE = re.compile(
    r"^(?P<prefix>.+)\.(?P<token>[0-9a-fA-F-]{16,64})\.(?P<suffix>tmp|staging|partial|lock|wal|shm)$"
)


@dataclass(frozen=True)
class ControlOwnershipSourceSet:
    control: object
    runtime_registry: object
    runtime_config: object
    local_inputs: object


@dataclass(frozen=True)
class _Blob:
    content: bytes
    blob_sha: str


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

    def read_ref(self, repository: str, branch: str) -> str:
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
        ref = value.get("object")
        if type(ref) is not dict or type(ref.get("sha")) is not str:
            _fail("CONTROL_REF_UNAVAILABLE", "GitHub ref response has no exact OID")
        oid = ref["sha"]
        if _HEX40.fullmatch(oid) is None:
            _fail("CONTROL_REF_UNAVAILABLE", "GitHub ref OID is not a commit identity")
        return oid

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
        return _Blob(content=content, blob_sha=blob_sha)

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
            read_mode="COMPLETE_DOUBLE_READ",
            identity={"observation_digest": digest_bytes(canonical)},
            payload=canonical,
            producer_sha256=self._producer_sha256,
        )
        return SourceObservation(record=record, canonical_payload=canonical, complete=True)


class _RuntimeConfigSource:
    def read(self) -> _RawFileObservation:
        path = Path.home() / ".orch" / "config.json"
        snapshot = _read_file_snapshot(path, "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE")
        return _RawFileObservation(
            payload=snapshot.content,
            identity=snapshot.identity,
            locator=str(path),
        )


class _LocalInputsSource:
    """The production local source has no extra side channel.

    The attestor owns the Store, compatibility, and package reads directly so
    that no caller can inject a preformed durable readback.  The source exists
    as an explicit capability slot for local identity probes and for tests.
    """

    def read(self, _config: object, _subject: CutoverSubject) -> None:
        return None


def _fail(code: str, detail: str) -> None:
    raise BootstrapError(code, detail)


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
            "path": str(path.resolve()),
            "size": str(stat_result.st_size),
        }
    )


def _read_file_snapshot(path: Path, code: str) -> _FileSnapshot:
    path = Path(path)
    try:
        first = path.lstat()
        if not stat.S_ISREG(first.st_mode):
            raise OSError("path is not a regular file")
        content = path.read_bytes()
        second = path.lstat()
        if (
            first.st_dev,
            first.st_ino,
            first.st_mode,
            first.st_size,
            first.st_mtime_ns,
            first.st_ctime_ns,
        ) != (
            second.st_dev,
            second.st_ino,
            second.st_mode,
            second.st_size,
            second.st_mtime_ns,
            second.st_ctime_ns,
        ):
            raise OSError("file identity changed during read")
        identity = _identity_pairs(
            {
                "byte_sha256": digest_bytes(content),
                "inode": str(second.st_ino),
                "mtime_ns": str(second.st_mtime_ns),
                "path": str(path.resolve()),
                "size": str(second.st_size),
            }
        )
        return _FileSnapshot(path=path.resolve(), content=content, identity=identity)
    except BootstrapError:
        raise
    except (OSError, ValueError) as error:
        raise BootstrapError(code, f"local file is unavailable: {path}") from error


def _source_observation(
    value: object,
    *,
    role: str,
    repository: str,
    producer_sha256: str,
    default_locator: str,
    default_read_mode: str,
) -> SourceObservation:
    error_code = f"{role.upper().replace('.', '_')}_SOURCE_UNAVAILABLE"
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
    if type(value) is _RawFileObservation:
        if type(value.payload) is not bytes:
            _fail(error_code, "local file payload is not exact bytes")
        try:
            identity = dict(value.identity)
        except (TypeError, ValueError) as error:
            raise BootstrapError(error_code, "local file identity is malformed") from error
        observed_digest = digest_bytes(value.payload)
        if identity.get("byte_sha256") != observed_digest:
            _fail(error_code, "local file identity is not bound to its bytes")
        if identity.get("size") != str(len(value.payload)):
            _fail(error_code, "local file size identity is not exact")
        if type(value.complete) is not bool or not value.complete:
            _fail(error_code, "source enumeration is incomplete")
        record = _source_record(
            role=role,
            locator=value.locator,
            repository=repository,
            read_mode=default_read_mode,
            identity=identity,
            payload=value.payload,
            producer_sha256=producer_sha256,
        )
        return SourceObservation(record=record, canonical_payload=value.payload, complete=True)
    if type(value) is tuple:
        if len(value) != 2:
            _fail(error_code, "source tuple observation is malformed")
        first, second = value
        if type(first) is SourceRecord and type(second) is bytes:
            observation = SourceObservation(record=first, canonical_payload=second, complete=True)
        elif type(first) is bytes and type(second) is SourceRecord:
            observation = SourceObservation(record=second, canonical_payload=first, complete=True)
        else:
            _fail(error_code, "source tuple observation is malformed")
        return _source_observation(
            observation,
            role=role,
            repository=repository,
            producer_sha256=producer_sha256,
            default_locator=default_locator,
            default_read_mode=default_read_mode,
        )
    if type(value) is bytes:
        payload = value
    else:
        payload = _canonical_payload(value, error_code)
    record = _source_record(
        role=role,
        locator=default_locator,
        repository=repository,
        read_mode=default_read_mode,
        identity={"observation_digest": digest_bytes(payload)},
        payload=payload,
        producer_sha256=producer_sha256,
    )
    return SourceObservation(record=record, canonical_payload=payload, complete=True)


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
            f"{role.upper().replace('.', '_')}_SOURCE_UNAVAILABLE",
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


def _blob(value: object, code: str) -> tuple[bytes, str]:
    if value is None:
        _fail(code, "control blob is missing")
    content = value if type(value) is bytes else getattr(value, "content", None)
    blob_sha = getattr(value, "blob_sha", getattr(value, "sha", None))
    if (
        type(content) is not bytes
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
        oid = read_ref(subject.repository, subject.control_branch)
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError("CONTROL_REF_UNAVAILABLE", "control branch ref read failed") from error
    if type(oid) is not str or _HEX40.fullmatch(oid) is None:
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
    configured = getattr(config, "expected_store_tables", expected_tables)
    if type(configured) is not tuple or configured != expected_tables:
        _fail("STORE_SOURCE_UNAVAILABLE", "configured Store tables are not the fixed current-main contract")
    return expected_tables


def _path_text(path: Path) -> str:
    return str(Path(path).resolve())


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
    parent = path.parent
    if not parent.is_dir():
        return ()
    try:
        return tuple(
            sorted(
                (
                    candidate
                    for candidate in parent.iterdir()
                    if (
                        (match := _DYNAMIC_SIDE_FILE.fullmatch(candidate.name))
                        is not None
                        and match.group("prefix") == path.name
                    )
                ),
                key=str,
            )
        )
    except OSError as error:
        raise BootstrapError("STORE_SOURCE_UNAVAILABLE", "SQLite sidecar scan failed") from error


def _check_sidecars(path: Path) -> None:
    present = tuple(
        str(candidate)
        for candidate in (*_sidecars(path), *_dynamic_sidecars(path))
        if os.path.lexists(candidate)
    )
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
        "source_main_sha": subject.source_commit,
        "source_main_tree": subject.source_tree_digest,
        "store_generation": getattr(config, "store_generation", subject.store_generation),
        "store_sha256": getattr(config, "expected_fresh_store_sha256", None),
        "integrity": "ok",
        "store_path": _path_text(snapshot.path),
    }
    for name, expected_value in expected.items():
        if expected_value is not None and value.get(name) != expected_value:
            _fail("STORE_SOURCE_UNAVAILABLE", f"fresh Store receipt {name} is not exact")
    configured_digest = getattr(config, "expected_fresh_receipt_sha256", None)
    observed_digest = digest_bytes(snapshot.content)
    if configured_digest is not None and observed_digest != configured_digest:
        _fail("STORE_SOURCE_UNAVAILABLE", "fresh Store receipt bytes changed")
    configured_runbook = getattr(config, "expected_fresh_receipt_runbook_sha256", None)
    if configured_runbook is not None and value["runbook_sha256"] != configured_runbook:
        _fail("STORE_SOURCE_UNAVAILABLE", "fresh Store receipt runbook is not exact")
    configured_schema = getattr(config, "expected_fresh_receipt_schema_digest", None)
    if configured_schema is not None and value["schema_digest"] != configured_schema:
        _fail("STORE_SOURCE_UNAVAILABLE", "fresh Store receipt schema digest is not exact")
    configured_generation_rows = getattr(config, "expected_fresh_receipt_generation_rows", None)
    if configured_generation_rows is not None:
        expected_rows = [list(row) for row in configured_generation_rows]
        if value["generation_rows"] != expected_rows:
            _fail("STORE_SOURCE_UNAVAILABLE", "fresh Store receipt generation rows are not exact")
    configured_row_counts = getattr(config, "expected_fresh_receipt_row_counts", None)
    if configured_row_counts is not None:
        expected_counts = dict(configured_row_counts)
        if value["row_counts"] != expected_counts:
            _fail("STORE_SOURCE_UNAVAILABLE", "fresh Store receipt row counts are not exact")
    rollback_path = getattr(config, "rollback_store", None)
    prior_path = getattr(config, "prior_store", None)
    rollback_hash = getattr(config, "expected_rollback_store_sha256", None)
    prior_hash = getattr(config, "expected_prior_store_sha256", None)
    if None not in (rollback_path, prior_path, rollback_hash, prior_hash):
        expected_old = {
            _path_text(Path(rollback_path)): rollback_hash,
            _path_text(Path(prior_path)): prior_hash,
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
    path_value = getattr(config, "fresh_store", None)
    receipt_path_value = getattr(config, "fresh_receipt", None)
    if path_value is None or receipt_path_value is None:
        _fail("STORE_SOURCE_UNAVAILABLE", "fixed Store and receipt paths are absent")
    try:
        path = Path(path_value)
        receipt_path = Path(receipt_path_value)
    except (OSError, TypeError, ValueError) as error:
        raise BootstrapError("STORE_SOURCE_UNAVAILABLE", "fixed Store paths are malformed") from error
    store_snapshot = _read_file_snapshot(path, "STORE_SOURCE_UNAVAILABLE")
    expected_hash = getattr(config, "expected_fresh_store_sha256", None)
    if expected_hash is not None and store_snapshot.identity:
        observed_hash = dict(store_snapshot.identity).get("byte_sha256")
        if observed_hash != expected_hash:
            _fail("STORE_SOURCE_UNAVAILABLE", "fresh Store hash is not the configured identity")
    receipt_snapshot = _read_file_snapshot(receipt_path, "STORE_SOURCE_UNAVAILABLE")
    try:
        receipt = _receipt(
            config,
            subject,
            receipt_snapshot,
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
            if row["writer_generation"] != subject.target_writer_generation and row["repository"] == repository:
                _fail("STORE_SOURCE_UNAVAILABLE", "Store writer generation changed")
        if len(pending_activation_ids) != len(set(pending_activation_ids)):
            _fail("STORE_SOURCE_UNAVAILABLE", "pending Activation identities are duplicated")
        if len(predecessor_refs) != len(set(predecessor_refs)):
            _fail("STORE_SOURCE_UNAVAILABLE", "Plan Revision identities are duplicated")
        store_after = _read_file_snapshot(path, "STORE_SOURCE_UNAVAILABLE")
        if store_after.identity != store_snapshot.identity:
            _fail("STORE_SOURCE_UNAVAILABLE", "Store bytes changed during immutable read")
        values = {
            "repository": repository,
            "generation_id": getattr(config, "store_generation", subject.store_generation),
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
    while type(value) is dict:
        for key in ("runtimes", "runtime_registry", "agents", "records", "items"):
            if key in value:
                value = value[key]
                break
        else:
            if not value or any(
                type(key) is not str
                or not key
                or type(item) is not dict
                for key, item in value.items()
            ):
                _fail(
                    "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE",
                    "Runtime registry mapping shape is not authoritative",
                )
            value = list(value.keys())
    if type(value) not in (list, tuple):
        _fail("RUNTIME_REGISTRY_SOURCE_UNAVAILABLE", "Runtime registry enumeration is not a sequence")
    identities: list[str] = []
    for item in value:
        if type(item) is str:
            identity = item
        elif type(item) is dict:
            identity = next(
                (
                    item.get(name)
                    for name in ("identity", "runtime_id", "agent_id", "id")
                    if type(item.get(name)) is str
                ),
                None,
            )
        else:
            identity = None
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
        or type(repository) is not str
        or not repository
    ):
        _fail("RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE", "Runtime configuration schema is invalid")
    try:
        global_value = value["global"]
        tiers = value["tiers"]
        role_profiles = value["role_profiles"]
        repositories = value.get("repositories", {})
        if (
            type(global_value) is not dict
            or type(tiers) is not dict
            or not tiers
            or type(role_profiles) is not dict
            or type(repositories) is not dict
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

        repository_value = repositories.get(repository, {})
        if type(repository_value) is not dict:
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
            result = RuntimeProfile(
                name=name,
                provider=raw["provider"],
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


def _validate_runtime_config_source(observation: SourceObservation) -> None:
    """Keep the Runtime projection bound to the one host config file."""

    expected_path = (Path.home() / ".orch" / "config.json").resolve()
    record = observation.record
    try:
        locator = Path(record.locator).resolve()
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


def _audited_files(root: Path) -> tuple[Path, ...]:
    candidates = [
        root / "skills" / "implement-gwo" / "SKILL.md",
        root / "skills" / "orchestrator" / "SKILL.md",
        *(root / "skills" / "orchestrator" / "scripts" / "gwo_v8").rglob("*.py"),
    ]
    return tuple(
        sorted(
            (path for path in candidates if path.is_file()),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    )


def _static_records(
    root: Path,
    *,
    repository: str,
    producer_sha256: str,
    role: str,
    source_commit: str,
    source_tree_digest: str,
    readback_digest: str,
) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for path in _audited_files(root):
        snapshot = _read_file_snapshot(path, "STATIC_INPUT_SOURCE_UNAVAILABLE")
        relative = path.resolve().relative_to(root.resolve()).as_posix()
        records.append(
            _source_record(
                role=role,
                locator=str(path.resolve()),
                repository=repository,
                read_mode="FIXED_COMMIT_TREE",
                identity={
                    **dict(snapshot.identity),
                    "commit_oid": source_commit,
                    "relative_path": relative,
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


def _install_roots(config: object, subject: CutoverSubject) -> dict[str, Path]:
    raw = getattr(config, "install_roots", None)
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
) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    package_paths: list[tuple[str, Path]] = []
    for package_name in subject.package_names:
        source = root / "skills" / package_name
        if not source.is_dir():
            source = root / package_name
        if not source.is_dir():
            _fail("PACKAGE_SOURCE_UNAVAILABLE", f"source package is unavailable: {package_name}")
        package_paths.append((f"source:{package_name}", source))
        for surface, install_root in install_roots.items():
            installed = install_root / package_name
            if not installed.is_dir():
                _fail(
                    "PACKAGE_SOURCE_UNAVAILABLE",
                    f"installed package is unavailable: {surface}:{package_name}",
                )
            package_paths.append((f"{surface}:{package_name}", installed))
    for label, package in package_paths:
        manifest = package / ".skill-package.json"
        try:
            manifest_stat = manifest.lstat()
        except OSError as error:
            raise BootstrapError(
                "PACKAGE_SOURCE_UNAVAILABLE", f"package manifest is unavailable: {manifest}"
            ) from error
        if not stat.S_ISREG(manifest_stat.st_mode):
            _fail("PACKAGE_SOURCE_UNAVAILABLE", f"package manifest is not a regular file: {manifest}")
        files = sorted(
            (path for path in package.rglob("*") if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"),
            key=lambda path: path.relative_to(package).as_posix(),
        )
        if not files:
            _fail("PACKAGE_SOURCE_UNAVAILABLE", f"package file set is empty: {package}")
        for path in files:
            snapshot = _read_file_snapshot(path, "PACKAGE_SOURCE_UNAVAILABLE")
            records.append(
                _source_record(
                role="package.file",
                locator=str(path.resolve()),
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


def _validate_config_subject(config: object, subject: CutoverSubject) -> None:
    if subject.required_runtime_selectors != RUNTIME_SELECTORS:
        _fail(
            "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE",
            "subject Runtime selectors are not the fixed current contract",
        )
    expected_values = {
        "repository": subject.repository,
        "control_branch": subject.control_branch,
        "target_branch": subject.target_branch,
        "source_writer_generation": subject.source_writer_generation,
        "target_writer_generation": subject.target_writer_generation,
        "store_generation": subject.store_generation,
        "expected_head": subject.source_commit,
        "expected_tree": subject.source_tree_digest,
    }
    for name, expected in expected_values.items():
        if hasattr(config, name) and getattr(config, name) != expected:
            _fail(
                "STATIC_INPUT_SOURCE_UNAVAILABLE",
                f"fixed configuration {name} is not bound to the subject",
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
    if _HEX40.fullmatch(subject.source_commit) is None or _HEX64.fullmatch(subject.source_tree_digest) is None:
        _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "subject source identity is malformed")
    package_names = getattr(config, "package_names", subject.package_names)
    if type(package_names) is not tuple or package_names != subject.package_names:
        _fail("PACKAGE_SOURCE_UNAVAILABLE", "configured package names are not exact")


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
    expected_content = getattr(config, "expected_package_content_digests", None)
    if expected_content is not None:
        try:
            expected = dict(expected_content)
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
    expected_version = getattr(config, "expected_package_version", None)
    if expected_version is not None:
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
        self._sources = sources
        self._check_source(sources.control, ("read_ref", "read_at_oid"))
        self._check_source(sources.runtime_registry, ("read",))
        self._check_source(sources.runtime_config, ("read",))
        self._check_source(sources.local_inputs, ("read",))

    @staticmethod
    def _check_source(source: object, methods: tuple[str, ...]) -> None:
        try:
            exposed = set(dir(source))
            forbidden = {
                "start",
                "stop",
                "restore",
                "drain",
                "write",
                "publish",
                "compare_and_swap",
                "compare_and_swap_ref",
                "activate",
                "advance",
                "install",
                "prepare",
                "command",
                "events",
                "put",
                "delete",
                "unlink",
            }
            if exposed & forbidden or any(not callable(getattr(source, name, None)) for name in methods):
                raise BootstrapError("UNSAFE_SOURCE_CAPABILITY", "source exposes an unsafe or incomplete capability")
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
    ) -> ComponentObservation:
        if type(subject) is not CutoverSubject or type(attempt) is not AttemptIdentity:
            _fail("COMPONENT_INVALID", "subject and attempt must be exact current contracts")
        if attempt.repository != subject.repository:
            _fail("COMPONENT_INVALID", "attempt and subject repositories differ")
        if attempt.cutover_subject_digest != digest_value(subject.canonical()):
            _fail("COMPONENT_INVALID", "attempt does not bind the cutover subject")
        _validate_config_subject(config, subject)

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
            default_read_mode="COMPLETE_DOUBLE_READ",
        )
        registry_value = _load_canonical_object(registry.canonical_payload, "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE")
        runtime_refs = _registry_refs(registry_value)

        runtime_raw = _read_source(
            self._sources.runtime_config,
            "read",
            (),
            role="runtime.config",
            repository=subject.repository,
            producer_sha256=attempt.attestor_sha256,
            default_locator=str(Path.home() / ".orch" / "config.json"),
            default_read_mode="EXACT_FILE",
        )
        _validate_runtime_config_source(runtime_raw)
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

        root = Path(getattr(config, "repository_root", "."))
        try:
            compatibility = ProductionPathScanner(root).read(subject)
        except BootstrapError:
            raise
        except Exception as error:
            raise BootstrapError("STATIC_INPUT_SOURCE_UNAVAILABLE", "production path scan failed") from error
        _validate_readback(
            compatibility,
            CompatibilityPathReadback,
            code="STATIC_INPUT_SOURCE_UNAVAILABLE",
            repository=subject.repository,
        )
        if (
            compatibility.source_commit != subject.source_commit
            or compatibility.source_tree_digest != subject.source_tree_digest
        ):
            _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "compatibility readback source identity changed")
        try:
            install_roots = _install_roots(config, subject)
            packages = ReadOnlyPackageValidator(root, install_roots).read(subject)
        except BootstrapError:
            raise
        except Exception as error:
            raise BootstrapError("PACKAGE_SOURCE_UNAVAILABLE", "package readback failed") from error
        _validate_readback(packages, PackageReadback, code="PACKAGE_SOURCE_UNAVAILABLE")
        _validate_package_identity_config(config, packages, subject)

        static_records = _static_records(
            root,
            repository=subject.repository,
            producer_sha256=attempt.attestor_sha256,
            role="compatibility.module",
            source_commit=subject.source_commit,
            source_tree_digest=subject.source_tree_digest,
            readback_digest=compatibility.readback_digest,
        )
        package_records = _package_records(
            root,
            install_roots,
            subject,
            producer_sha256=attempt.attestor_sha256,
            readback_digest=packages.readback_digest,
        )
        local_observation = self._sources.local_inputs.read(config, subject)
        local_records: list[SourceRecord] = []
        if local_observation is not None:
            observed = _source_observation(
                local_observation,
                role="local.inputs",
                repository=subject.repository,
                producer_sha256=attempt.attestor_sha256,
                default_locator="local-inputs://beta3",
                default_read_mode="COMPLETE_READ",
            )
            local_records.append(observed.record)

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
                (*control_records, store.store_record, store.receipt_record, registry.record, runtime_raw.record, *static_records, *package_records, *local_records),
                key=lambda record: record.digest,
            )
        )
        if len({record.digest for record in source_records}) != len(source_records):
            _fail("COMPONENT_INVALID", "component source record identities are duplicated")
        groups = {
            "durable_state": (store.store_record, store.receipt_record),
            "writer_fence": tuple(control_records),
            "ownership": (store.store_record, registry.record),
            "compatibility": tuple(static_records),
            "runtime": (runtime_raw.record,),
            "packages": tuple(package_records),
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
        runtime_config=_RuntimeConfigSource(),
        local_inputs=_LocalInputsSource(),
    )
