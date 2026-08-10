from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
from typing import Callable, Mapping, Sequence, TextIO


REPOSITORY_ROOT = Path(r"D:\Workstation\github-work-orchestrator").resolve()
EVIDENCE_ROOT = Path(
    r"D:\gwo-release-evidence\2026-08-09-gwo-v8-beta3-production-cutover"
).resolve()
REPOSITORY = "NOirBRight/github-work-orchestrator"
EXPECTED_HEAD = "5de34bdaee45f0aba44077a8d1d3e3ed8293f237"
EXPECTED_TREE = "104ee822dbfb494d33d56b8ccf54092d9d1d9c86"
CONTROL_BRANCH = "gwo-control"
TARGET_BRANCH = "main"
SOURCE_WRITER_GENERATION = "v6.1"
TARGET_WRITER_GENERATION = "v8"
FRESH_STORE = Path(
    r"C:\Users\noirb\.orch\v8\NOirBRight__github-work-orchestrator"
    r"\store-20260809T081500Z.sqlite3"
)
STORE_GENERATION = "store:v8:production:20260809T081500Z"
EXPECTED_FRESH_STORE_SHA256 = (
    "afff1078e7a65fb8acccde28fee78fab3cf2278db9dd6548f5ef96a882076b98"
)
ROLLBACK_STORE = Path(
    r"C:\Users\noirb\.orch\v8\NOirBRight__github-work-orchestrator"
    r"\store.sqlite3"
)
EXPECTED_ROLLBACK_STORE_SHA256 = (
    "1cc3f304044032fdab9569f8561b28220ecfd93e4efc35cf6bb2e492c1ca72b8"
)
PRIOR_STORE = Path(
    r"C:\Users\noirb\.orch\v8\NOirBRight__github-work-orchestrator"
    r"\store-20260809T023000Z.sqlite3"
)
EXPECTED_PRIOR_STORE_SHA256 = (
    "df2341d76eb2ab54110ac3e70ff137a93d05ffbb02352c61b654321dba188ed7"
)
FRESH_RECEIPT = EVIDENCE_ROOT / "fresh-store-exact-main-receipt.json"
REPORT_PATH = EVIDENCE_ROOT / "beta3-live-guard-report.json"
EVIDENCE_PATH = EVIDENCE_ROOT / "beta3-live-guard-evidence.json"
GATEWAY_STORE_PATH = EVIDENCE_ROOT / "beta3-live-guard-gateway.sqlite3"
ARTIFACT_ROOT = EVIDENCE_ROOT / "beta3-live-guard-artifacts"
EXPECTED_FRESH_RECEIPT_RUNBOOK_SHA256 = (
    "0378be64a95aa4eeb09626c120254ad8105a1a5cc2dfd1f60ddf089dfba821f2"
)
EXPECTED_FRESH_RECEIPT_SHA256 = (
    "46814d166c857e3d7f847b7da6f3da5b39c394b42402b2f1d2cdd61d78ce7781"
)
EXPECTED_FRESH_RECEIPT_SCHEMA_DIGEST = (
    "69ac6babce5db564fcc60fc5dd97feb0635911e07955234098210ddd97a93aed"
)
EXPECTED_FRESH_RECEIPT_GENERATION_ROWS = (
    (REPOSITORY, STORE_GENERATION),
)
EXPECTED_FRESH_RECEIPT_ROW_COUNTS = {
    "v8_active_plans": 0,
    "v8_admissions": 0,
    "v8_attempts": 0,
    "v8_execution_state": 0,
    "v8_goal_holds": 0,
    "v8_integration_batches": 0,
    "v8_integration_leases": 0,
    "v8_node_execution_state": 0,
    "v8_node_states": 0,
    "v8_pending_activations": 0,
    "v8_plan_revisions": 0,
    "v8_resource_claims": 0,
    "v8_verified_results": 0,
    "v8_writer_fences": 0,
    "v8_writer_generations": 1,
}
PACKAGE_NAMES = ("implement-gwo", "orchestrator")
EXPECTED_PACKAGE_VERSION = "8.0.0"
INSTALL_SURFACES = (".agents", ".codex", ".claude")
INSTALL_ROOTS = tuple(Path.home() / surface / "skills" for surface in INSTALL_SURFACES)
FIXED_STORE_SCHEMA_CONTRACT: dict[
    str, tuple[tuple[str, str, int, str | None, int], ...]
] = {
    "v8_active_plans": (
        ("repository", "TEXT", 0, None, 1),
        ("plan_digest", "TEXT", 1, None, 0),
        ("writer_generation", "TEXT", 1, None, 0),
        ("activation_id", "TEXT", 0, None, 0),
    ),
    "v8_admissions": (
        ("admission_id", "TEXT", 0, None, 1),
        ("repository", "TEXT", 1, None, 0),
        ("plan_digest", "TEXT", 1, None, 0),
        ("node_key", "TEXT", 1, None, 0),
        ("goal_key", "TEXT", 1, None, 0),
        ("state", "TEXT", 1, None, 0),
    ),
    "v8_attempts": (
        ("attempt_id", "TEXT", 0, None, 1),
        ("repository", "TEXT", 1, None, 0),
        ("plan_digest", "TEXT", 1, None, 0),
        ("node_key", "TEXT", 1, None, 0),
        ("admission_id", "TEXT", 1, None, 0),
        ("state", "TEXT", 1, None, 0),
    ),
    "v8_execution_state": (
        ("repository", "TEXT", 1, None, 1),
        ("plan_digest", "TEXT", 1, None, 2),
        ("state_json", "TEXT", 1, None, 0),
    ),
    "v8_goal_holds": (
        ("repository", "TEXT", 1, None, 1),
        ("goal_key", "TEXT", 1, None, 2),
        ("reason", "TEXT", 1, None, 0),
    ),
    "v8_integration_batches": (
        ("repository", "TEXT", 1, None, 1),
        ("plan_digest", "TEXT", 1, None, 2),
        ("batch_id", "TEXT", 1, None, 3),
        ("state_json", "TEXT", 1, None, 0),
    ),
    "v8_integration_leases": (
        ("repository", "TEXT", 0, None, 1),
        ("holder", "TEXT", 1, None, 0),
    ),
    "v8_node_execution_state": (
        ("repository", "TEXT", 1, None, 1),
        ("plan_digest", "TEXT", 1, None, 2),
        ("node_key", "TEXT", 1, None, 3),
        ("state_json", "TEXT", 1, None, 0),
    ),
    "v8_node_states": (
        ("repository", "TEXT", 1, None, 1),
        ("plan_digest", "TEXT", 1, None, 2),
        ("node_key", "TEXT", 1, None, 3),
        ("state", "TEXT", 1, None, 0),
    ),
    "v8_pending_activations": (
        ("repository", "TEXT", 0, None, 1),
        ("plan_digest", "TEXT", 1, None, 0),
        ("expected_previous_digest", "TEXT", 0, None, 0),
        ("writer_generation", "TEXT", 1, None, 0),
        ("activation_id", "TEXT", 1, None, 0),
        ("receipt_json", "TEXT", 1, None, 0),
    ),
    "v8_plan_revisions": (
        ("repository", "TEXT", 1, None, 1),
        ("plan_digest", "TEXT", 1, None, 2),
        ("canonical_bytes", "BLOB", 1, None, 0),
        ("compilation_record", "TEXT", 1, None, 0),
        ("writer_generation", "TEXT", 1, None, 0),
    ),
    "v8_resource_claims": (
        ("repository", "TEXT", 1, None, 1),
        ("resource_key", "TEXT", 1, None, 2),
        ("admission_id", "TEXT", 0, None, 0),
        ("attempt_id", "TEXT", 0, None, 0),
    ),
    "v8_verified_results": (
        ("repository", "TEXT", 1, None, 1),
        ("plan_digest", "TEXT", 1, None, 2),
        ("node_key", "TEXT", 1, None, 3),
        ("contract_digest", "TEXT", 1, None, 0),
        ("candidate_sha", "TEXT", 1, None, 4),
        ("result_digest", "TEXT", 1, None, 0),
        ("base_sha", "TEXT", 1, None, 0),
        ("evidence_manifest_digest", "TEXT", 0, None, 0),
        ("evidence_json", "TEXT", 0, None, 0),
        ("superseded", "INTEGER", 1, "0", 0),
    ),
    "v8_writer_fences": (
        ("repository", "TEXT", 0, None, 1),
        ("writer_generation", "TEXT", 1, None, 0),
        ("activation_id", "TEXT", 1, None, 0),
        ("state", "TEXT", 1, None, 0),
    ),
    "v8_writer_generations": (
        ("repository", "TEXT", 0, None, 1),
        ("writer_generation", "TEXT", 1, None, 0),
    ),
}
FIXED_STORE_TABLES = (
    "v8_active_plans",
    "v8_admissions",
    "v8_attempts",
    "v8_execution_state",
    "v8_goal_holds",
    "v8_integration_batches",
    "v8_integration_leases",
    "v8_node_execution_state",
    "v8_node_states",
    "v8_pending_activations",
    "v8_plan_revisions",
    "v8_resource_claims",
    "v8_verified_results",
    "v8_writer_fences",
    "v8_writer_generations",
)
EXPECTED_STORE_TABLES = FIXED_STORE_TABLES
EXPECTED_CHECK_IDS = (
    "source_writer",
    "legacy_quiescence",
    "durable_state",
    "writer_and_lease",
    "production_paths",
    "runtime_configuration",
    "package_installation",
)
GUARD_READBACK_SCHEMA = "gwo.cutover-readback-bundle.v1"
GUARD_PORT_ORDER = (
    "legacy",
    "durable_state",
    "writer_fence",
    "ownership",
    "compatibility",
    "runtime",
    "packages",
)
CHECK_TO_GUARD_PORT = dict(
    zip(
        EXPECTED_CHECK_IDS,
        (
            "writer_fence",
            "legacy",
            "durable_state",
            "ownership",
            "compatibility",
            "runtime",
            "packages",
        ),
        strict=True,
    )
)
PRODUCTION_ENTRY_REFS = (
    "gwo_v8.plan_control_host:ProductionPlanControlStartHost.start",
    "gwo_v8.execution_kernel:advance",
    "gwo_v8.execution_kernel:inspect",
)
REPORT_SCHEMA = "gwo-v8-beta3-live-guard-report.v1"
EVIDENCE_SCHEMA = "gwo-v8-beta3-live-guard-evidence.v1"
EVIDENCE_MODE = "live_composed_ports"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
FRESH_RECEIPT_KEYS = frozenset(
    {
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
)


class RunnerError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class RunnerConfig:
    repository_root: Path
    evidence_root: Path
    repository: str
    expected_head: str
    expected_tree: str
    control_branch: str
    target_branch: str
    source_writer_generation: str
    target_writer_generation: str
    fresh_store: Path
    store_generation: str
    expected_fresh_store_sha256: str
    rollback_store: Path
    expected_rollback_store_sha256: str
    prior_store: Path
    expected_prior_store_sha256: str
    fresh_receipt: Path
    report_path: Path
    evidence_path: Path
    install_roots: tuple[Path, Path, Path]
    package_names: tuple[str, ...] = PACKAGE_NAMES
    expected_store_tables: tuple[str, ...] = EXPECTED_STORE_TABLES
    expected_fresh_receipt_runbook_sha256: str = EXPECTED_FRESH_RECEIPT_RUNBOOK_SHA256
    expected_fresh_receipt_sha256: str | None = EXPECTED_FRESH_RECEIPT_SHA256
    expected_fresh_receipt_schema_digest: str | None = None
    expected_fresh_receipt_generation_rows: tuple[tuple[str, str], ...] | None = None
    expected_fresh_receipt_row_counts: tuple[tuple[str, int], ...] | None = None
    expected_package_digest: str | None = None
    expected_package_content_digests: tuple[tuple[str, str], ...] = (
        (
            "implement-gwo",
            "fcafa60645a2ea18408ec97369fdf5a01402a950b90e701fa2305624a1bfeaa9",
        ),
        (
            "orchestrator",
            "1a10f3f19e6db951150bd97a40561de1093ae20ba07d8c503a244cd1f0123639",
        ),
    )
    gateway_store_path: Path = GATEWAY_STORE_PATH
    artifact_root: Path = ARTIFACT_ROOT
    authoritative_legacy_snapshot: Path | None = None
    production_readers: object | None = None


DEFAULT_CONFIG = RunnerConfig(
    repository_root=REPOSITORY_ROOT,
    evidence_root=EVIDENCE_ROOT,
    repository=REPOSITORY,
    expected_head=EXPECTED_HEAD,
    expected_tree=EXPECTED_TREE,
    control_branch=CONTROL_BRANCH,
    target_branch=TARGET_BRANCH,
    source_writer_generation=SOURCE_WRITER_GENERATION,
    target_writer_generation=TARGET_WRITER_GENERATION,
    fresh_store=FRESH_STORE,
    store_generation=STORE_GENERATION,
    expected_fresh_store_sha256=EXPECTED_FRESH_STORE_SHA256,
    rollback_store=ROLLBACK_STORE,
    expected_rollback_store_sha256=EXPECTED_ROLLBACK_STORE_SHA256,
    prior_store=PRIOR_STORE,
    expected_prior_store_sha256=EXPECTED_PRIOR_STORE_SHA256,
    fresh_receipt=FRESH_RECEIPT,
    report_path=REPORT_PATH,
    evidence_path=EVIDENCE_PATH,
    install_roots=INSTALL_ROOTS,
    expected_fresh_receipt_schema_digest=EXPECTED_FRESH_RECEIPT_SCHEMA_DIGEST,
    expected_fresh_receipt_generation_rows=EXPECTED_FRESH_RECEIPT_GENERATION_ROWS,
    expected_fresh_receipt_row_counts=tuple(EXPECTED_FRESH_RECEIPT_ROW_COUNTS.items()),
    expected_fresh_receipt_sha256=EXPECTED_FRESH_RECEIPT_SHA256,
)


@dataclass(frozen=True)
class GuardSubject:
    repository: str
    control_branch: str
    target_branch: str
    source_writer_generation: str
    target_writer_generation: str
    store_generation: str
    source_commit: str
    source_tree_digest: str


@dataclass(frozen=True)
class ExecutionDependencies:
    live_guard: Callable[[RunnerConfig, object], object]
    control_read: Callable[[], object]
    package_read: Callable[[RunnerConfig], object]
    subject_factory: Callable[[RunnerConfig], object] | None = None
    guard_contract: "GuardTypeContract | None" = None


@dataclass(frozen=True)
class GuardTypeContract:
    report_type: type
    subject_type: type
    bundle_type: type
    readback_types: tuple[tuple[str, type], ...]
    digest_value: Callable[[object], str]


@dataclass(frozen=True)
class GuardExecution:
    """A Guard report plus the independently bound values it observed."""

    report: object
    subject: object
    readback_bundle: object
    contract: GuardTypeContract | None = None


@dataclass(frozen=True)
class ProductionReaders:
    """Explicit read-only dependencies required by the real V3 host."""

    legacy_read: object
    durable_state_read: object
    writer_fence_read: object
    ownership_read: object
    content_client: object
    source: object
    repository: object


def canonical_json_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise RunnerError("CANONICAL_JSON_INVALID", "value cannot be canonical JSON") from error
    try:
        return (encoded + "\n").encode("utf-8")
    except UnicodeEncodeError as error:
        raise RunnerError("CANONICAL_JSON_INVALID", "value contains invalid Unicode") from error


def _exact_digest_value(value: object) -> str:
    """Use current-main digest semantics without importing it during preflight."""
    try:
        from gwo_v8._canonical import digest_value
    except (ImportError, ModuleNotFoundError):
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError) as error:
            raise RunnerError("CANONICAL_JSON_INVALID", "value cannot be exact-main canonical JSON") from error
        return hashlib.sha256(encoded).hexdigest()
    try:
        return str(digest_value(value))
    except Exception as error:
        raise RunnerError("CANONICAL_JSON_INVALID", "value cannot be exact-main canonical JSON") from error


def _path_text(path: Path) -> str:
    return str(Path(path).resolve())


def _same_path(left: object, right: Path) -> bool:
    try:
        return Path(str(left)).resolve() == Path(right).resolve()
    except (OSError, RuntimeError, TypeError):
        return False


def _is_reparse(stat_result: os.stat_result) -> bool:
    return bool(getattr(stat_result, "st_file_attributes", 0) & 0x0400)


def _lstat(path: Path, code: str) -> os.stat_result:
    try:
        result = os.lstat(path)
    except OSError as error:
        raise RunnerError(code, f"path is unavailable: {path}") from error
    if stat.S_ISLNK(result.st_mode) or _is_reparse(result):
        raise RunnerError(code, f"path is a link or reparse point: {path}")
    return result


def _directory_components(path: Path) -> tuple[Path, ...]:
    absolute = Path(os.path.abspath(path))
    parts = absolute.parts
    return tuple(Path(*parts[:index]) for index in range(1, len(parts) + 1))


def _close_descriptors(descriptors: list[int]) -> None:
    while descriptors:
        os.close(descriptors.pop())


def _open_directory_components(
    path: Path, code: str, *, allow_file_create: bool = False
) -> tuple[list[int], list[dict[str, int | str]]]:
    descriptors: list[int] = []
    identities: list[dict[str, int | str]] = []
    path_components = _directory_components(path)
    try:
        for index, component in enumerate(path_components):
            parent = descriptors[-1] if descriptors else None
            component_name = component if parent is None else Path(component.name)
            descriptor = _open_path_handle(
                component_name,
                code,
                directory=True,
                parent=parent,
                writable=allow_file_create and index == len(path_components) - 1,
            )
            try:
                identity = _windows_handle_identity(descriptor, code, directory=True)
            except Exception:
                os.close(descriptor)
                raise
            descriptors.append(descriptor)
            identities.append(identity)
        if not descriptors:
            raise RunnerError(code, f"directory path is empty: {path}")
        return descriptors, identities
    except RunnerError:
        _close_descriptors(descriptors)
        raise
    except OSError as error:
        _close_descriptors(descriptors)
        raise RunnerError(code, f"directory component could not be held: {path}") from error


def _assert_directory_handles(
    path: Path,
    descriptors: Sequence[int],
    identities: Sequence[Mapping[str, object]],
    code: str,
) -> None:
    if not descriptors or len(descriptors) != len(identities):
        raise RunnerError(code, f"directory components are not fully held: {path}")
    for descriptor, identity in zip(descriptors, identities, strict=True):
        current = _windows_handle_identity(descriptor, code, directory=True)
        if not _identity_matches(current, identity):
            raise RunnerError(code, f"held directory component changed: {path}")
    current_descriptors, current_identities = _open_directory_components(path, code)
    try:
        if len(current_identities) != len(identities) or any(
            not _identity_matches(current, expected)
            for current, expected in zip(current_identities, identities, strict=True)
        ):
            raise RunnerError(code, f"directory path no longer names the held components: {path}")
    finally:
        _close_descriptors(current_descriptors)


@dataclass
class _HeldDirectory:
    path: Path
    component_descriptors: list[int]
    component_identities: list[dict[str, int | str]]

    @property
    def descriptor(self) -> int:
        if not self.component_descriptors:
            raise RunnerError("LIVE_INPUT_DRIFT", f"directory is not held: {self.path}")
        return self.component_descriptors[-1]

    @property
    def identity(self) -> dict[str, int | str]:
        if not self.component_identities:
            raise RunnerError("LIVE_INPUT_DRIFT", f"directory identity is not held: {self.path}")
        return self.component_identities[-1]

    def assert_stable(self) -> None:
        _assert_directory_handles(
            self.path,
            self.component_descriptors,
            self.component_identities,
            "LIVE_INPUT_DRIFT",
        )

    def close(self) -> None:
        _close_descriptors(self.component_descriptors)
        self.component_identities.clear()


def _require_directory(path: Path, code: str) -> None:
    descriptors, _identities = _open_directory_components(path, code)
    _close_descriptors(descriptors)


def _require_regular_file(path: Path, code: str) -> None:
    descriptor, _identity = _open_bound_handle(path, code)
    os.close(descriptor)


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _require_absent(path: Path, code: str) -> None:
    if _lexists(path):
        raise RunnerError(code, f"path already exists: {path}")


def _file_identity(stat_result: os.stat_result) -> dict[str, int]:
    return {
        "st_dev": int(stat_result.st_dev),
        "st_ino": int(stat_result.st_ino),
        "st_mode": int(stat_result.st_mode),
        "st_size": int(stat_result.st_size),
        "st_mtime_ns": int(stat_result.st_mtime_ns),
    }


def _windows_handle_identity(descriptor: int, code: str, *, directory: bool) -> dict[str, int | str]:
    if os.name != "nt":
        return _file_identity(os.fstat(descriptor))
    try:
        import ctypes
        import msvcrt

        class FileIdInfo(ctypes.Structure):
            _fields_ = [
                ("volume_serial_number", ctypes.c_ulonglong),
                ("file_id", ctypes.c_ubyte * 16),
            ]

        class FileStandardInfo(ctypes.Structure):
            _fields_ = [
                ("allocation_size", ctypes.c_longlong),
                ("end_of_file", ctypes.c_longlong),
                ("number_of_links", ctypes.c_ulong),
                ("delete_pending", ctypes.c_int),
                ("directory", ctypes.c_int),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = msvcrt.get_osfhandle(descriptor)
        file_id = FileIdInfo()
        standard = FileStandardInfo()
        if not kernel32.GetFileInformationByHandleEx(
            handle, 18, ctypes.byref(file_id), ctypes.sizeof(file_id)
        ):
            raise OSError(ctypes.get_last_error(), "GetFileInformationByHandleEx(FILE_ID_INFO) failed")
        if not kernel32.GetFileInformationByHandleEx(
            handle, 1, ctypes.byref(standard), ctypes.sizeof(standard)
        ):
            raise OSError(ctypes.get_last_error(), "GetFileInformationByHandleEx(FILE_STANDARD_INFO) failed")
        attributes = int(getattr(os.fstat(descriptor), "st_file_attributes", 0))
        if attributes & 0x0400:
            raise RunnerError(code, "handle is a reparse point")
        if stat.S_ISDIR(os.fstat(descriptor).st_mode) != directory:
            raise RunnerError(code, "handle type is not the expected file/directory")
        if not directory and int(standard.number_of_links) != 1:
            raise RunnerError(code, "file has an unexpected hard-link count")
        return {
            "volume_id": int(file_id.volume_serial_number),
            "file_id": bytes(file_id.file_id).hex(),
            "st_mode": int(os.fstat(descriptor).st_mode),
            "st_size": int(standard.end_of_file),
            "st_mtime_ns": int(os.fstat(descriptor).st_mtime_ns),
        }
    except RunnerError:
        raise
    except (ImportError, OSError, AttributeError, TypeError) as error:
        raise RunnerError(code, "Windows handle identity is unavailable") from error


def _identity_matches(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    if "file_id" in left or "file_id" in right:
        return (
            left.get("volume_id") == right.get("volume_id")
            and left.get("file_id") == right.get("file_id")
        )
    return left.get("st_dev") == right.get("st_dev") and left.get("st_ino") == right.get("st_ino")


def _validate_closed_file_identity(value: object, label: str) -> None:
    if type(value) is not dict:
        raise _existing_output_collision(f"{label} identity is not an object")
    if "file_id" in value:
        expected = {"volume_id", "file_id", "st_mode", "st_size", "st_mtime_ns"}
        if set(value) != expected or type(value["volume_id"]) is not int or type(value["file_id"]) is not str:
            raise _existing_output_collision(f"{label} identity shape is not exact")
    else:
        expected = {"st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns"}
        if set(value) != expected or any(type(value[name]) is not int for name in expected):
            raise _existing_output_collision(f"{label} identity shape is not exact")
    for name in ("st_mode", "st_size", "st_mtime_ns"):
        if type(value[name]) is not int or value[name] < 0:
            raise _existing_output_collision(f"{label} identity value is not exact")


def _open_windows_relative_handle(
    path: Path,
    code: str,
    *,
    directory: bool,
    parent: int,
    create_new: bool = False,
    writable: bool = False,
) -> int:
    try:
        import ctypes
        import msvcrt

        class UnicodeString(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ushort),
                ("maximum_length", ctypes.c_ushort),
                ("buffer", ctypes.c_wchar_p),
            ]

        class ObjectAttributes(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("root_directory", ctypes.c_void_p),
                ("object_name", ctypes.POINTER(UnicodeString)),
                ("attributes", ctypes.c_ulong),
                ("security_descriptor", ctypes.c_void_p),
                ("security_quality_of_service", ctypes.c_void_p),
            ]

        class IoStatusBlock(ctypes.Structure):
            _fields_ = [
                ("status", ctypes.c_void_p),
                ("information", ctypes.c_size_t),
            ]

        name = str(Path(path).name)
        if not name or name in {".", ".."}:
            raise RunnerError(code, f"relative component is invalid: {path}")
        name_buffer = ctypes.create_unicode_buffer(name)
        unicode_name = UnicodeString(
            len(name) * ctypes.sizeof(ctypes.c_wchar),
            ctypes.sizeof(name_buffer),
            ctypes.cast(name_buffer, ctypes.c_wchar_p),
        )
        object_attributes = ObjectAttributes(
            ctypes.sizeof(ObjectAttributes),
            ctypes.c_void_p(msvcrt.get_osfhandle(parent)),
            ctypes.pointer(unicode_name),
            # OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE: a relative native
            # lookup must not redirect through a newly introduced reparse
            # point before the handle identity is checked.
            0x00001040,
            None,
            None,
        )
        io_status = IoStatusBlock()
        handle = ctypes.c_void_p()
        desired_access = 0x80000000 | 0x00100000
        if writable:
            desired_access |= 0x40000000
        if directory:
            desired_access = 0x00000001 | 0x00000020 | 0x00000080 | 0x00100000
            if writable:
                desired_access |= 0x00000002
        share_access = 0x00000003 if directory else 0x00000001
        create_disposition = 2 if create_new else 1
        # FILE_OPEN_REPARSE_POINT applies to opens of an existing leaf.  The
        # native create disposition rejects that option for FILE_CREATE;
        # FILE_CREATE is already exclusive and cannot replace or traverse an
        # existing leaf.  Keep the no-reparse option for all non-creating
        # component/file opens below.
        create_options = 0x00000020
        if not create_new:
            create_options |= 0x00200000
        create_options |= 0x00000001 if directory else 0x00000040
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtCreateFile.restype = ctypes.c_long
        ntdll.NtCreateFile.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_ulong,
            ctypes.POINTER(ObjectAttributes),
            ctypes.POINTER(IoStatusBlock),
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.c_ulong,
        ]
        status = int(
            ntdll.NtCreateFile(
                ctypes.byref(handle),
                desired_access,
                ctypes.byref(object_attributes),
                ctypes.byref(io_status),
                None,
                0x00000080,
                share_access,
                create_disposition,
                create_options,
                None,
                0,
            )
        ) & 0xFFFFFFFF
        if status & 0x80000000:
            if create_new and status in {0xC0000035, 0xC0000034}:
                raise RunnerError("OUTPUT_COLLISION", f"output appeared during exclusive create: {path}")
            raise OSError(status, "NtCreateFile failed")
        try:
            return msvcrt.open_osfhandle(
                int(handle.value),
                (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_BINARY", 0),
            )
        except OSError:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
            raise
    except RunnerError:
        raise
    except (ImportError, OSError, AttributeError, TypeError) as error:
        raise RunnerError(code, f"relative Windows handle could not be opened: {path}") from error


def _open_path_handle(
    path: Path,
    code: str,
    *,
    directory: bool,
    parent: int | None = None,
    create_new: bool = False,
    writable: bool = False,
) -> int:
    if os.name != "nt":
        flags = (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_BINARY", 0)
        if create_new:
            flags |= os.O_CREAT | os.O_EXCL
        if directory:
            flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        else:
            flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            if parent is None:
                return os.open(path, flags, 0o600 if create_new else 0o644)
            return os.open(Path(path).name, flags, 0o600 if create_new else 0o644, dir_fd=parent)
        except FileExistsError as error:
            raise RunnerError("OUTPUT_COLLISION", f"output appeared during exclusive create: {path}") from error
        except OSError as error:
            raise RunnerError(code, f"path could not be opened without reparse following: {path}") from error
    if parent is not None:
        return _open_windows_relative_handle(
            path,
            code,
            directory=directory,
            parent=parent,
            create_new=create_new,
            writable=writable,
        )
    try:
        import ctypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        access = (
            (0x80000000 | (0x40000000 if writable else 0))
            if not directory
            else 0x00000001
            | 0x00000020
            | 0x00000080
            | 0x00100000
            | (0x00000002 if writable else 0)
        )
        share = 0x00000001 if not directory else 0x00000003
        flags = 0x00200000 | (0x02000000 if directory else 0)
        handle = kernel32.CreateFileW(
            str(Path(path).absolute()),
            access,
            share,
            None,
            1 if create_new else 3,
            flags,
            None,
        )
        if handle in (None, ctypes.c_void_p(-1).value):
            error_code = ctypes.get_last_error()
            if create_new and error_code in (80, 183):
                raise RunnerError("OUTPUT_COLLISION", f"output appeared during exclusive create: {path}")
            raise OSError(error_code, "CreateFileW failed")
        try:
            return msvcrt.open_osfhandle(
                handle,
                (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_BINARY", 0),
            )
        except OSError:
            kernel32.CloseHandle(handle)
            raise
    except RunnerError:
        raise
    except (ImportError, OSError, AttributeError, TypeError) as error:
        raise RunnerError(code, f"path could not be opened by Windows handle: {path}") from error


def _open_bound_handle(
    path: Path,
    code: str,
    *,
    expected_identity: Mapping[str, object] | None = None,
    components_out: list[int] | None = None,
    component_identities_out: list[dict[str, int | str]] | None = None,
    parent: object | None = None,
) -> tuple[int, dict[str, int | str]]:
    components: list[int] = []
    component_identities: list[dict[str, int | str]] = []
    if parent is None:
        components, component_identities = _open_directory_components(path.parent, code)
        parent_descriptor = components[-1]
        open_path = Path(path.name)
    else:
        assert_stable = getattr(parent, "assert_stable", None)
        if not callable(assert_stable):
            raise RunnerError(code, f"held parent is unavailable: {path}")
        assert_stable()
        parent_descriptor = getattr(parent, "descriptor", None)
        if type(parent_descriptor) is not int:
            raise RunnerError(code, f"held parent descriptor is unavailable: {path}")
        open_path = Path(path.name)
    descriptor: int | None = None
    try:
        descriptor = _open_path_handle(
            open_path,
            code,
            directory=False,
            parent=parent_descriptor,
        )
        observed_identity = _windows_handle_identity(descriptor, code, directory=False)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RunnerError(code, f"path is not a regular file: {path}")
        if expected_identity is not None and (
            not _identity_matches(observed_identity, expected_identity)
            or observed_identity.get("st_size") != expected_identity.get("st_size")
        ):
            raise RunnerError(code, f"path identity is not the preflight identity: {path}")
        if components_out is not None:
            components_out.extend(components)
            if component_identities_out is not None:
                component_identities_out.extend(component_identities)
            components = []
            component_identities = []
        return descriptor, dict(observed_identity)
    except RunnerError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise RunnerError(code, f"path could not be opened read-only: {path}") from error
    finally:
        _close_descriptors(components)


def _open_bound_directory(path: Path, code: str) -> tuple[int, dict[str, int | str]]:
    descriptors, identities = _open_directory_components(path, code)
    try:
        descriptor = descriptors.pop()
        _close_descriptors(descriptors)
        return descriptor, identities[-1]
    except RunnerError:
        _close_descriptors(descriptors)
        raise
    except OSError as error:
        _close_descriptors(descriptors)
        raise RunnerError(code, f"directory could not be held: {path}") from error


def _read_held_bytes(descriptor: int, code: str) -> bytes:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                return b"".join(chunks)
            chunks.append(block)
    except OSError as error:
        raise RunnerError(code, "held input readback failed") from error


def _bound_bytes(path: Path, code: str) -> tuple[bytes, dict[str, int]]:
    descriptor, identity = _open_bound_handle(path, code)
    try:
        content = _read_held_bytes(descriptor, code)
        after_identity = _windows_handle_identity(descriptor, code, directory=False)
        if not _identity_matches(after_identity, identity) or after_identity.get("st_size") != identity.get(
            "st_size"
        ):
            raise RunnerError(code, f"path changed during read: {path}")
    except RunnerError:
        raise
    except OSError as error:
        raise RunnerError(code, f"cannot read path: {path}") from error
    finally:
        os.close(descriptor)
    return content, identity


def _bound_file_snapshot(path: Path, code: str) -> dict[str, object]:
    content, identity = _bound_bytes(path, code)
    return {
        "path": _path_text(path),
        "identity": identity,
        "size": identity["st_size"],
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _sha256(path: Path, code: str = "FILE_READ_FAILED") -> str:
    return str(_bound_file_snapshot(path, code)["sha256"])


@dataclass
class _InputBinding:
    path: Path
    descriptor: int
    identity: dict[str, int | str]
    sha256: str
    parent: _HeldDirectory | None


class _InputLease:
    def __init__(self, expected: Mapping[Path, Mapping[str, object]]) -> None:
        self._expected = expected
        self._bindings: list[_InputBinding] = []

    def __enter__(self) -> "_InputLease":
        try:
            for path, expected in self._expected.items():
                identity = expected.get("identity", expected)
                if type(identity) is not dict:
                    raise RunnerError("LIVE_INPUT_DRIFT", f"input identity is malformed: {path}")
                expected_hash = expected.get("sha256")
                if expected_hash is not None and type(expected_hash) is not str:
                    raise RunnerError("LIVE_INPUT_DRIFT", f"input hash is malformed: {path}")
                component_descriptors: list[int] = []
                component_identities: list[dict[str, int | str]] = []
                descriptor, observed = _open_bound_handle(
                    path,
                    "LIVE_INPUT_DRIFT",
                    expected_identity=identity,
                    components_out=component_descriptors,
                    component_identities_out=component_identities,
                )
                parent = (
                    _HeldDirectory(path.parent, component_descriptors, component_identities)
                    if component_descriptors
                    else None
                )
                content = _read_held_bytes(descriptor, "LIVE_INPUT_DRIFT")
                current = _windows_handle_identity(descriptor, "LIVE_INPUT_DRIFT", directory=False)
                if not _identity_matches(current, observed) or current.get("st_size") != observed.get(
                    "st_size"
                ):
                    os.close(descriptor)
                    if parent is not None:
                        parent.close()
                    raise RunnerError("LIVE_INPUT_DRIFT", f"input changed while being held: {path}")
                observed_hash = hashlib.sha256(content).hexdigest()
                if expected_hash is not None and observed_hash != expected_hash:
                    os.close(descriptor)
                    if parent is not None:
                        parent.close()
                    raise RunnerError("LIVE_INPUT_DRIFT", f"input hash changed while being held: {path}")
                self._bindings.append(
                    _InputBinding(
                        path,
                        descriptor,
                        observed,
                        observed_hash,
                        parent,
                    )
                )
            return self
        except RunnerError:
            self.close()
            raise

    def close(self) -> None:
        while self._bindings:
            binding = self._bindings.pop()
            os.close(binding.descriptor)
            if binding.parent is not None:
                binding.parent.close()

    def assert_stable(self) -> None:
        for binding in self._bindings:
            current = _windows_handle_identity(binding.descriptor, "LIVE_INPUT_DRIFT", directory=False)
            content = _read_held_bytes(binding.descriptor, "LIVE_INPUT_DRIFT")
            after = _windows_handle_identity(binding.descriptor, "LIVE_INPUT_DRIFT", directory=False)
            if (
                not _identity_matches(current, binding.identity)
                or not _identity_matches(after, binding.identity)
                or current.get("st_size") != binding.identity.get("st_size")
                or after.get("st_size") != binding.identity.get("st_size")
                or hashlib.sha256(content).hexdigest() != binding.sha256
            ):
                raise RunnerError("LIVE_INPUT_DRIFT", f"held input changed: {binding.path}")
            if binding.parent is not None:
                binding.parent.assert_stable()
                path_descriptor, path_identity = _open_bound_handle(
                    binding.path,
                    "LIVE_INPUT_DRIFT",
                    expected_identity=binding.identity,
                    parent=binding.parent,
                )
                try:
                    path_content = _read_held_bytes(path_descriptor, "LIVE_INPUT_DRIFT")
                finally:
                    os.close(path_descriptor)
                if (
                    not _identity_matches(path_identity, binding.identity)
                    or hashlib.sha256(path_content).hexdigest() != binding.sha256
                ):
                    raise RunnerError(
                        "LIVE_INPUT_DRIFT",
                        f"input path no longer names its held file: {binding.path}",
                    )

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()


_SCHEMA_OPTION_NAMES = (
    "application_id",
    "auto_vacuum",
    "encoding",
    "foreign_keys",
    "journal_mode",
    "recursive_triggers",
    "user_version",
)


def _sqlite_schema_digest(connection: sqlite3.Connection) -> str:
    objects = [
        {
            "type": row[0],
            "name": row[1],
            "tbl_name": row[2],
            "sql": row[3],
        }
        for row in connection.execute(
            "select type, name, tbl_name, sql from sqlite_master "
            "order by type, name, tbl_name, sql"
        ).fetchall()
    ]
    options = {
        name: connection.execute(f"pragma {name}").fetchone()[0]
        for name in _SCHEMA_OPTION_NAMES
    }
    payload = json.dumps(
        {"sqlite_master": objects, "options": options},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_fixed_store_configuration(config: RunnerConfig) -> None:
    if (
        type(config.expected_store_tables) is not tuple
        or config.expected_store_tables != FIXED_STORE_TABLES
    ):
        raise RunnerError(
            "STORE_SCHEMA_CONFIG_INVALID",
            "RunnerConfig.expected_store_tables is not the fixed current-main contract",
        )
    if config.expected_fresh_receipt_schema_digest != EXPECTED_FRESH_RECEIPT_SCHEMA_DIGEST:
        raise RunnerError(
            "STORE_SCHEMA_CONFIG_INVALID",
            "RunnerConfig fresh receipt schema digest is not the fixed current-main contract",
        )


def _validate_exact_store_schema(
    connection: sqlite3.Connection,
    validated_receipt: Mapping[str, object],
) -> str:
    tables = tuple(
        sorted(
            str(row[0])
            for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        )
    )
    if tables != tuple(sorted(FIXED_STORE_TABLES)):
        raise RunnerError(
            "LIVE_GUARD_UNAVAILABLE",
            "durable Store tables are not the exact current-main schema",
        )
    for table, expected_columns in FIXED_STORE_SCHEMA_CONTRACT.items():
        actual_columns = tuple(
            (str(row[1]), str(row[2]), int(row[3]), row[4], int(row[5]))
            for row in connection.execute(f'pragma table_info("{table}")').fetchall()
        )
        if actual_columns != expected_columns:
            raise RunnerError(
                "LIVE_GUARD_UNAVAILABLE",
                f"durable Store columns are not exact for {table}",
            )
    schema_digest = _sqlite_schema_digest(connection)
    if schema_digest != EXPECTED_FRESH_RECEIPT_SCHEMA_DIGEST:
        raise RunnerError(
            "LIVE_GUARD_UNAVAILABLE",
            "durable Store sqlite_master schema digest is not exact",
        )
    if validated_receipt.get("schema_digest") != schema_digest:
        raise RunnerError(
            "LIVE_GUARD_UNAVAILABLE",
            "durable Store schema digest is not bound to the validated fresh receipt",
        )
    if validated_receipt.get("tables") != list(FIXED_STORE_TABLES):
        raise RunnerError(
            "LIVE_GUARD_UNAVAILABLE",
            "validated fresh receipt table contract is not exact",
        )
    return schema_digest


def _read_only_sqlite(path: Path, code: str) -> tuple[str, tuple[str, ...]]:
    uri = "file:" + str(path) + "?mode=ro&immutable=1"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True)
        integrity_row = connection.execute("pragma integrity_check").fetchone()
        integrity = None if integrity_row is None else integrity_row[0]
        if integrity != "ok":
            raise RunnerError(code, f"SQLite integrity check was not ok: {path}")
        tables = tuple(
            sorted(
                str(row[0])
                for row in connection.execute(
                    "select name from sqlite_master where type='table'"
                )
            )
        )
        return "ok", tables
    except RunnerError:
        raise
    except (OSError, sqlite3.Error) as error:
        raise RunnerError(code, f"SQLite readback was unavailable: {path}") from error
    finally:
        if connection is not None:
            connection.close()


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


_DYNAMIC_SIDE_FILE = re.compile(
    r"^(?P<prefix>.+)\.(?P<token>[0-9a-fA-F-]{16,64})\.(?P<suffix>tmp|staging|partial|lock|wal|shm)$"
)


def _dynamic_sidecars(path: Path) -> tuple[Path, ...]:
    parent = path.parent
    if not parent.is_dir():
        return ()
    result: list[Path] = []
    try:
        for candidate in parent.iterdir():
            match = _DYNAMIC_SIDE_FILE.fullmatch(candidate.name)
            if match and match.group("prefix") == path.name:
                result.append(candidate)
    except OSError as error:
        raise RunnerError("SIDECAR_SCAN_FAILED", f"sidecar family is unavailable: {path}") from error
    return tuple(sorted(result, key=str))


def _check_sidecars(path: Path) -> tuple[str, ...]:
    candidates = (*_sidecars(path), *_dynamic_sidecars(path))
    present = tuple(str(candidate) for candidate in candidates if _lexists(candidate))
    if present:
        raise RunnerError("STORE_SIDECAR_PRESENT", "SQLite sidecar is present: " + "; ".join(present))
    return ()


class _ImmutableDurableStateReadPort:
    """Runner-owned immutable read of the configured exact-main Store."""

    def __init__(
        self,
        path: Path,
        repository: str,
        generation: str,
        expected_tables: tuple[str, ...],
        contract: GuardTypeContract,
        validated_receipt: Mapping[str, object] | None = None,
    ) -> None:
        if not isinstance(path, Path) or type(repository) is not str or type(generation) is not str:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "durable Store adapter configuration is invalid")
        if expected_tables != FIXED_STORE_TABLES:
            raise RunnerError(
                "LIVE_GUARD_UNAVAILABLE",
                "durable Store table contract is not the fixed current-main contract",
            )
        readback_type = dict(contract.readback_types).get("durable_state")
        if readback_type is None:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "durable Store readback type is unavailable")
        self._path = path.resolve()
        self._repository = repository
        self._generation = generation
        self._expected_tables = tuple(sorted(expected_tables))
        self._readback_type = readback_type
        self._digest_value = contract.digest_value
        self.sqlite_uri = f"{self._path.as_uri()}?mode=ro&immutable=1"
        self._validated_receipt = (
            None if validated_receipt is None else dict(validated_receipt)
        )

    @staticmethod
    def _identifier(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    @staticmethod
    def _text(value: object, label: str, *, optional: bool = False) -> str | None:
        if optional and value is None:
            return None
        if type(value) is not str or not value:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", f"durable Store {label} is malformed")
        return value

    @classmethod
    def _digest(cls, value: object, label: str) -> str:
        text = cls._text(value, label)
        if text is None or not _HEX64.fullmatch(text):
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", f"durable Store {label} is not a digest")
        return text

    def _require_repository(self, value: object, label: str) -> None:
        if self._text(value, label) != self._repository:
            raise RunnerError(
                "LIVE_GUARD_UNAVAILABLE",
                f"durable Store {label} is not the configured repository",
            )

    def _json_object(self, value: object, label: str) -> dict[str, object]:
        text = self._text(value, label)
        try:
            decoded = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", f"durable Store {label} is invalid JSON") from error
        if type(decoded) is not dict:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", f"durable Store {label} is not an object")
        return decoded

    def _validate_pending_receipt(
        self,
        raw_value: object,
        *,
        plan_digest: str,
        expected_previous_digest: object,
        activation_id: str,
    ) -> None:
        raw = self._text(raw_value, "v8_pending_activations.receipt_json")
        receipt = self._json_object(raw, "v8_pending_activations.receipt_json")
        expected_keys = {
            "schema_version",
            "repository",
            "writer_generation",
            "activation_id",
            "plan_digest",
            "expected_previous_digest",
            "plan_record_ref",
            "created_at",
        }
        if set(receipt) != expected_keys or receipt.get("schema_version") != 1:
            raise RunnerError(
                "LIVE_GUARD_UNAVAILABLE",
                "pending Activation receipt schema is not exact",
            )
        self._require_repository(receipt.get("repository"), "pending Activation receipt.repository")
        if self._text(
            receipt.get("writer_generation"),
            "pending Activation receipt.writer_generation",
        ) != self._generation:
            raise RunnerError(
                "LIVE_GUARD_UNAVAILABLE",
                "pending Activation receipt writer generation is not exact",
            )
        if self._text(
            receipt.get("activation_id"),
            "pending Activation receipt.activation_id",
        ) != activation_id:
            raise RunnerError(
                "LIVE_GUARD_UNAVAILABLE",
                "pending Activation receipt identity is not exact",
            )
        if self._digest(
            receipt.get("plan_digest"),
            "pending Activation receipt.plan_digest",
        ) != plan_digest:
            raise RunnerError(
                "LIVE_GUARD_UNAVAILABLE",
                "pending Activation receipt plan identity is not exact",
            )
        receipt_previous = receipt.get("expected_previous_digest")
        if receipt_previous is not None:
            receipt_previous = self._digest(
                receipt_previous,
                "pending Activation receipt.expected_previous_digest",
            )
        if receipt_previous != expected_previous_digest:
            raise RunnerError(
                "LIVE_GUARD_UNAVAILABLE",
                "pending Activation receipt predecessor is not exact",
            )
        self._text(receipt.get("plan_record_ref"), "pending Activation receipt.plan_record_ref")
        self._text(receipt.get("created_at"), "pending Activation receipt.created_at")
        if canonical_json_bytes(receipt).decode("utf-8") != raw:
            raise RunnerError(
                "LIVE_GUARD_UNAVAILABLE",
                "pending Activation receipt JSON is not canonical",
            )

    def _validate_rows(self, connection: sqlite3.Connection) -> tuple[set[str], tuple[str, ...], tuple[str, ...]]:
        active_plan_digests: set[str] = set()

        for table in (
            "v8_execution_state",
            "v8_node_execution_state",
            "v8_node_states",
            "v8_admissions",
            "v8_attempts",
            "v8_integration_batches",
            "v8_verified_results",
        ):
            rows = connection.execute(
                f"select * from {self._identifier(table)}"
            ).fetchall()
            for row in rows:
                self._require_repository(row["repository"], f"{table}.repository")
                active_plan_digests.add(
                    self._digest(row["plan_digest"], f"{table}.plan_digest")
                )
                if table == "v8_execution_state":
                    self._json_object(row["state_json"], f"{table}.state_json")
                elif table == "v8_node_execution_state":
                    self._text(row["node_key"], f"{table}.node_key")
                    self._json_object(row["state_json"], f"{table}.state_json")
                elif table == "v8_node_states":
                    self._text(row["node_key"], f"{table}.node_key")
                    self._text(row["state"], f"{table}.state")
                elif table == "v8_admissions":
                    self._text(row["admission_id"], f"{table}.admission_id")
                    self._text(row["node_key"], f"{table}.node_key")
                    self._text(row["goal_key"], f"{table}.goal_key")
                    self._text(row["state"], f"{table}.state")
                elif table == "v8_attempts":
                    self._text(row["attempt_id"], f"{table}.attempt_id")
                    self._text(row["node_key"], f"{table}.node_key")
                    self._text(row["admission_id"], f"{table}.admission_id")
                    self._text(row["state"], f"{table}.state")
                elif table == "v8_integration_batches":
                    self._text(row["batch_id"], f"{table}.batch_id")
                    self._json_object(row["state_json"], f"{table}.state_json")
                else:
                    self._text(row["node_key"], f"{table}.node_key")
                    for column in (
                        "contract_digest",
                        "candidate_sha",
                        "result_digest",
                        "base_sha",
                    ):
                        self._digest(row[column], f"{table}.{column}")
                    self._digest(
                        row["evidence_manifest_digest"],
                        f"{table}.evidence_manifest_digest",
                    ) if row["evidence_manifest_digest"] is not None else None
                    if row["evidence_json"] is not None:
                        self._json_object(row["evidence_json"], f"{table}.evidence_json")
                    if type(row["superseded"]) is not int or row["superseded"] not in (0, 1):
                        raise RunnerError(
                            "LIVE_GUARD_UNAVAILABLE",
                            f"durable Store {table}.superseded is malformed",
                        )

        for row in connection.execute(
            "select repository, plan_digest, writer_generation, activation_id "
            "from \"v8_active_plans\" order by repository"
        ).fetchall():
            self._require_repository(row["repository"], "v8_active_plans.repository")
            active_plan_digests.add(
                self._digest(row["plan_digest"], "v8_active_plans.plan_digest")
            )
            if self._text(row["writer_generation"], "v8_active_plans.writer_generation") != self._generation:
                raise RunnerError("LIVE_GUARD_UNAVAILABLE", "active Plan writer generation is not exact")
            self._text(row["activation_id"], "v8_active_plans.activation_id", optional=True)

        pending_activation_ids: list[str] = []
        for row in connection.execute(
            "select repository, plan_digest, expected_previous_digest, writer_generation, "
            "activation_id, receipt_json from \"v8_pending_activations\" order by repository"
        ).fetchall():
            self._require_repository(row["repository"], "v8_pending_activations.repository")
            plan_digest = self._digest(row["plan_digest"], "v8_pending_activations.plan_digest")
            expected_previous = row["expected_previous_digest"]
            if expected_previous is not None:
                self._digest(expected_previous, "v8_pending_activations.expected_previous_digest")
            if self._text(row["writer_generation"], "v8_pending_activations.writer_generation") != self._generation:
                raise RunnerError("LIVE_GUARD_UNAVAILABLE", "pending Activation writer generation is not exact")
            activation_id = self._text(row["activation_id"], "v8_pending_activations.activation_id")
            pending_activation_ids.append(str(activation_id))
            self._validate_pending_receipt(
                row["receipt_json"],
                plan_digest=plan_digest,
                expected_previous_digest=expected_previous,
                activation_id=activation_id,
            )

        predecessor_identity_refs: list[str] = []
        for row in connection.execute(
            "select repository, plan_digest, canonical_bytes, compilation_record, writer_generation "
            "from \"v8_plan_revisions\" order by repository, plan_digest"
        ).fetchall():
            self._require_repository(row["repository"], "v8_plan_revisions.repository")
            plan_digest = self._digest(row["plan_digest"], "v8_plan_revisions.plan_digest")
            canonical_bytes = row["canonical_bytes"]
            if type(canonical_bytes) is not bytes or hashlib.sha256(canonical_bytes).hexdigest() != plan_digest:
                raise RunnerError("LIVE_GUARD_UNAVAILABLE", "Plan Revision canonical bytes are not bound")
            self._json_object(row["compilation_record"], "v8_plan_revisions.compilation_record")
            if self._text(row["writer_generation"], "v8_plan_revisions.writer_generation") != self._generation:
                raise RunnerError("LIVE_GUARD_UNAVAILABLE", "Plan Revision writer generation is not exact")
            predecessor_identity_refs.append(plan_digest)

        for row in connection.execute(
            "select repository, holder from \"v8_integration_leases\" order by repository"
        ).fetchall():
            self._require_repository(row["repository"], "v8_integration_leases.repository")
            self._text(row["holder"], "v8_integration_leases.holder")
        for row in connection.execute(
            "select repository, goal_key, reason from \"v8_goal_holds\" order by repository, goal_key"
        ).fetchall():
            self._require_repository(row["repository"], "v8_goal_holds.repository")
            self._text(row["goal_key"], "v8_goal_holds.goal_key")
            self._text(row["reason"], "v8_goal_holds.reason")
        for row in connection.execute(
            "select repository, resource_key, admission_id, attempt_id "
            "from \"v8_resource_claims\" order by repository, resource_key"
        ).fetchall():
            self._require_repository(row["repository"], "v8_resource_claims.repository")
            self._text(row["resource_key"], "v8_resource_claims.resource_key")
            self._text(row["admission_id"], "v8_resource_claims.admission_id", optional=True)
            self._text(row["attempt_id"], "v8_resource_claims.attempt_id", optional=True)
        for row in connection.execute(
            "select repository, writer_generation, activation_id, state "
            "from \"v8_writer_fences\" order by repository"
        ).fetchall():
            self._require_repository(row["repository"], "v8_writer_fences.repository")
            if self._text(row["writer_generation"], "v8_writer_fences.writer_generation") != self._generation:
                raise RunnerError("LIVE_GUARD_UNAVAILABLE", "writer fence generation is not exact")
            self._text(row["activation_id"], "v8_writer_fences.activation_id")
            self._text(row["state"], "v8_writer_fences.state")

        if len(pending_activation_ids) != len(set(pending_activation_ids)):
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "pending Activation identities are duplicated")
        if len(predecessor_identity_refs) != len(set(predecessor_identity_refs)):
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "Plan Revision identities are duplicated")
        return (
            active_plan_digests,
            tuple(sorted(pending_activation_ids)),
            tuple(sorted(predecessor_identity_refs)),
        )

    def _read_from_connection(self, connection: sqlite3.Connection) -> object:
        integrity = connection.execute("pragma integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "durable Store integrity check failed")
        if self._validated_receipt is None:
            raise RunnerError(
                "LIVE_GUARD_UNAVAILABLE",
                "durable Store read is not bound to the validated fresh receipt",
            )
        receipt = self._validated_receipt
        if (
            receipt.get("repository") != self._repository
            or receipt.get("store_path") != _path_text(self._path)
            or receipt.get("store_generation") != self._generation
        ):
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "durable Store receipt identity is not exact")
        _validate_exact_store_schema(connection, receipt)
        row_counts = receipt.get("row_counts")
        if type(row_counts) is not dict or set(row_counts) != set(FIXED_STORE_TABLES):
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "fresh receipt row-count contract is not exact")
        actual_counts = {
            table: int(
                connection.execute(
                    f"select count(*) from {self._identifier(table)}"
                ).fetchone()[0]
            )
            for table in FIXED_STORE_TABLES
        }
        if actual_counts != row_counts:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "durable Store rows are not bound to the fresh receipt")
        rows = connection.execute(
            "select repository, writer_generation from \"v8_writer_generations\" order by repository"
        ).fetchall()
        expected_generation_rows = receipt.get("generation_rows")
        if (
            type(expected_generation_rows) is not list
            or any(type(row) is not list or len(row) != 2 for row in expected_generation_rows)
            or [list(row) for row in rows] != expected_generation_rows
        ):
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "durable Store repository/generation is not exact")
        active_plan_digests, pending_activation_ids, predecessor_identity_refs = self._validate_rows(
            connection
        )
        values = {
            "repository": self._repository,
            "generation_id": self._generation,
            "state_schema": "gwo.v8.store.v1",
            "compatible": True,
            "active_plan_digests": tuple(sorted(active_plan_digests)),
            "pending_activation_ids": pending_activation_ids,
            "predecessor_identity_refs": predecessor_identity_refs,
        }
        return self._readback_type(
            **values,
            readback_digest=self._digest_value(values),
        )

    def read(self, repository: str) -> object:
        if repository != self._repository:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "durable Store repository is not exact")
        connection: sqlite3.Connection | None = None
        try:
            _check_sidecars(self._path)
            connection = sqlite3.connect(self.sqlite_uri, uri=True)
            connection.row_factory = sqlite3.Row
            return self._read_from_connection(connection)
        except RunnerError:
            raise
        except BaseException as error:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "durable Store live read failed") from error
        finally:
            try:
                if connection is not None:
                    connection.close()
            finally:
                try:
                    _check_sidecars(self._path)
                except RunnerError as error:
                    raise RunnerError(
                        "LIVE_GUARD_UNAVAILABLE",
                        "durable Store live read is not sidecar-free",
                    ) from error


def _store_snapshot(
    path: Path,
    expected_hash: str,
    code_prefix: str,
    *,
    expected_tables: tuple[str, ...] = (),
) -> dict[str, object]:
    _require_directory(path.parent, f"{code_prefix}_PARENT_INVALID")
    _require_regular_file(path, f"{code_prefix}_UNAVAILABLE")
    _check_sidecars(path)
    first = _bound_file_snapshot(path, f"{code_prefix}_READ_FAILED")
    observed_hash = str(first["sha256"])
    if observed_hash != expected_hash:
        raise RunnerError(
            f"{code_prefix}_HASH_MISMATCH",
            f"{path} hash is {observed_hash}, expected {expected_hash}",
        )
    integrity, tables = _read_only_sqlite(path, f"{code_prefix}_INVALID")
    second = _bound_file_snapshot(path, f"{code_prefix}_READ_FAILED")
    if first != second:
        raise RunnerError(
            f"{code_prefix}_IDENTITY_DRIFT",
            f"{path} changed during the read-only snapshot",
        )
    _check_sidecars(path)
    if expected_tables and tables != tuple(sorted(expected_tables)):
        raise RunnerError(
            f"{code_prefix}_SCHEMA_MISMATCH",
            f"{path} tables are {tables}, expected {tuple(sorted(expected_tables))}",
        )
    return {
        "path": _path_text(path),
        "sha256": observed_hash,
        "identity": first["identity"],
        "size": first["size"],
        "integrity": integrity,
        "tables": list(tables),
        "sidecars": [],
    }


def _decode_status_record(record: str) -> tuple[str, str]:
    if len(record) < 4 or record[2] != " " or "\x00" in record:
        raise RunnerError("GIT_STATUS_INVALID", "Git porcelain record is malformed")
    return record[:2], record[3:]


def _unquote_status_path(path: str) -> str:
    if not path:
        raise RunnerError("GIT_STATUS_INVALID", "Git porcelain path is empty")
    if path[0] != '"':
        if '"' in path:
            raise RunnerError("GIT_STATUS_INVALID", "Git porcelain path has an unmatched quote")
        return path
    if len(path) < 2 or path[-1] != '"':
        raise RunnerError("GIT_STATUS_INVALID", "Git quoted path is unterminated")
    result: list[str] = []
    index = 1
    end = len(path) - 1
    escapes = {
        "a": "\a",
        "b": "\b",
        "t": "\t",
        "n": "\n",
        "v": "\v",
        "f": "\f",
        "r": "\r",
        '"': '"',
        "\\": "\\",
    }
    while index < end:
        char = path[index]
        if char != "\\":
            result.append(char)
            index += 1
            continue
        index += 1
        if index >= end:
            raise RunnerError("GIT_STATUS_INVALID", "Git quoted path has a dangling escape")
        escaped = path[index]
        if escaped in escapes:
            result.append(escapes[escaped])
            index += 1
            continue
        if escaped in "01234567" and index + 2 < end and all(
            value in "01234567" for value in path[index : index + 3]
        ):
            result.append(chr(int(path[index : index + 3], 8)))
            index += 3
            continue
        raise RunnerError("GIT_STATUS_INVALID", "Git quoted path has an invalid escape")
    return "".join(result)


def parse_porcelain_z_status(output: str | bytes) -> tuple[str, ...]:
    if type(output) is bytes:
        try:
            output = output.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RunnerError("GIT_STATUS_INVALID", "Git status was not UTF-8") from error
    if type(output) is not str:
        raise RunnerError("GIT_STATUS_INVALID", "Git status was not exact text")
    unexpected: list[str] = []
    records = output.split("\0")
    for record in records:
        if not record:
            continue
        status, raw_path = _decode_status_record(record)
        path = _unquote_status_path(raw_path).replace("\\", "/")
        if status != "??" or not (
            path == ".codex-tmp" or path.startswith(".codex-tmp/")
        ):
            unexpected.append(record)
    return tuple(unexpected)


def _default_git_runner(
    args: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
    )


def _git_output(
    config: RunnerConfig,
    args: list[str],
    code: str,
    git_runner: Callable[..., subprocess.CompletedProcess[str]],
) -> str:
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = git_runner(args, cwd=config.repository_root, env=env)
    except OSError as error:
        raise RunnerError(code, f"git command was unavailable: {args}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        raise RunnerError(code, detail)
    value = result.stdout
    if type(value) is bytes:
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RunnerError(code, "git output was not UTF-8") from error
    if type(value) is not str:
        raise RunnerError(code, "git output was not exact text")
    return value


def _git_snapshot(
    config: RunnerConfig,
    git_runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, str]:
    head = _git_output(
        config, ["rev-parse", "--verify", "HEAD"], "GIT_HEAD_UNAVAILABLE", git_runner
    ).strip()
    if head != config.expected_head:
        raise RunnerError("GIT_HEAD_MISMATCH", f"HEAD is {head}, not {config.expected_head}")
    tree = _git_output(
        config,
        ["rev-parse", "--verify", "HEAD^{tree}"],
        "GIT_TREE_UNAVAILABLE",
        git_runner,
    ).strip()
    if tree != config.expected_tree:
        raise RunnerError("GIT_TREE_MISMATCH", f"HEAD tree is {tree}, not {config.expected_tree}")
    origin_main = _git_output(
        config,
        ["rev-parse", "--verify", "origin/main"],
        "GIT_ORIGIN_MAIN_UNAVAILABLE",
        git_runner,
    ).strip()
    if origin_main != config.expected_head:
        raise RunnerError(
            "GIT_ORIGIN_MAIN_MISMATCH",
            f"origin/main is {origin_main}, not {config.expected_head}",
        )
    status = _git_output(
        config,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        "GIT_STATUS_UNAVAILABLE",
        git_runner,
    )
    unexpected = parse_porcelain_z_status(status)
    if unexpected:
        raise RunnerError(
            "GIT_STATUS_DIRTY",
            "unexpected Git status: " + "; ".join(unexpected),
        )
    return {
        "head": head,
        "tree": tree,
        "origin_main": origin_main,
        "status": "clean-except-.codex-tmp",
    }


def _read_canonical_json(
    path: Path, code: str
) -> tuple[dict[str, object], str, dict[str, int | str]]:
    _require_regular_file(path, code)
    try:
        raw, identity = _bound_bytes(path, code)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RunnerError(code, f"canonical JSON is unavailable: {path}") from error
    if type(value) is not dict:
        raise RunnerError(code, f"canonical JSON root is not an object: {path}")
    if canonical_json_bytes(value) != raw:
        raise RunnerError(code, f"JSON is not canonical: {path}")
    return value, hashlib.sha256(raw).hexdigest(), identity


def _mapping_path_hashes(value: object) -> dict[str, str]:
    if type(value) is not dict:
        return {}
    result: dict[str, str] = {}
    for key, digest in value.items():
        if type(key) is not str or type(digest) is not str:
            return {}
        result[_path_text(Path(key))] = digest
    return result


def _validate_receipt(config: RunnerConfig) -> tuple[dict[str, object], str]:
    _validate_fixed_store_configuration(config)
    _require_directory(config.fresh_receipt.parent, "RECEIPT_PARENT_INVALID")
    receipt, digest, receipt_identity = _read_canonical_json(
        config.fresh_receipt, "FRESH_RECEIPT_INVALID"
    )
    if set(receipt) != FRESH_RECEIPT_KEYS:
        raise RunnerError(
            "FRESH_RECEIPT_SCHEMA_MISMATCH",
            "fresh receipt keys are not the closed exact schema",
        )
    exact_values = (
        ("schema", "gwo-v8-fresh-store-provision.v1"),
        ("repository", config.repository),
        ("source_main_sha", config.expected_head),
        ("source_main_tree", config.expected_tree),
        ("store_generation", config.store_generation),
        ("store_sha256", config.expected_fresh_store_sha256),
        ("integrity", "ok"),
    )
    for name, expected in exact_values:
        if receipt.get(name) != expected:
            raise RunnerError("FRESH_RECEIPT_IDENTITY_MISMATCH", f"receipt {name} is not exact")
    if type(receipt.get("store_path")) is not str or type(receipt.get("tables")) is not list:
        raise RunnerError("FRESH_RECEIPT_SCHEMA_MISMATCH", "fresh receipt path/table fields are malformed")
    if receipt.get("runbook_sha256") != config.expected_fresh_receipt_runbook_sha256:
        raise RunnerError("FRESH_RECEIPT_RUNBOOK_MISMATCH", "fresh Store runbook identity changed")
    if receipt.get("store_path") != _path_text(config.fresh_store):
        raise RunnerError("FRESH_RECEIPT_STORE_MISMATCH", "fresh Store path is not exact")
    if receipt.get("tables") != list(FIXED_STORE_TABLES):
        raise RunnerError("FRESH_RECEIPT_SCHEMA_MISMATCH", "fresh Store table identity changed")
    if any(type(table) is not str for table in receipt["tables"]):
        raise RunnerError("FRESH_RECEIPT_SCHEMA_MISMATCH", "fresh Store table names are malformed")
    if (
        config.expected_fresh_receipt_schema_digest is not None
        and receipt.get("schema_digest") != config.expected_fresh_receipt_schema_digest
    ):
        raise RunnerError("FRESH_RECEIPT_SCHEMA_MISMATCH", "fresh Store schema digest changed")
    if config.expected_fresh_receipt_generation_rows is not None:
        expected_rows = [list(row) for row in config.expected_fresh_receipt_generation_rows]
        if receipt.get("generation_rows") != expected_rows:
            raise RunnerError("FRESH_RECEIPT_GENERATION_MISMATCH", "fresh Store generation rows changed")
        if any(
            type(row) is not list or len(row) != 2 or any(type(item) is not str for item in row)
            for row in receipt["generation_rows"]
        ):
            raise RunnerError("FRESH_RECEIPT_GENERATION_MISMATCH", "fresh Store generation rows are malformed")
    if config.expected_fresh_receipt_row_counts is not None:
        expected_counts = dict(config.expected_fresh_receipt_row_counts)
        if receipt.get("row_counts") != expected_counts:
            raise RunnerError("FRESH_RECEIPT_ROW_COUNTS_MISMATCH", "fresh Store row counts changed")
        if type(receipt.get("row_counts")) is not dict or any(
            type(key) is not str or type(value) is not int or value < 0
            for key, value in receipt["row_counts"].items()
        ):
            raise RunnerError("FRESH_RECEIPT_ROW_COUNTS_MISMATCH", "fresh Store row counts are malformed")
    expected_old = {
        _path_text(config.rollback_store): config.expected_rollback_store_sha256,
        _path_text(config.prior_store): config.expected_prior_store_sha256,
    }
    for name in ("runbook_sha256", "schema_digest", "store_sha256"):
        if type(receipt.get(name)) is not str or not _HEX64.fullmatch(receipt[name]):
            raise RunnerError("FRESH_RECEIPT_SCHEMA_MISMATCH", f"receipt {name} is not a digest")
    for name in ("existing_store_hashes_before", "existing_store_hashes_after"):
        value = receipt.get(name)
        if (
            type(value) is not dict
            or set(value) != set(expected_old)
            or any(
                type(key) is not str
                or type(child) is not str
                or not _HEX64.fullmatch(child)
                for key, child in value.items()
            )
        ):
            raise RunnerError("FRESH_RECEIPT_OLD_STORE_MISMATCH", "old Store hash map is malformed")
    if receipt.get("existing_store_hashes_before") != expected_old:
        raise RunnerError("FRESH_RECEIPT_OLD_STORE_MISMATCH", "old Store before hashes are not exact")
    if receipt.get("existing_store_hashes_after") != expected_old:
        raise RunnerError("FRESH_RECEIPT_OLD_STORE_MISMATCH", "old Store after hashes are not exact")
    if receipt.get("old_stores_untouched") is not True:
        raise RunnerError("FRESH_RECEIPT_OLD_STORE_MISMATCH", "receipt does not prove old Stores untouched")
    if (
        type(config.expected_fresh_receipt_sha256) is not str
        or not _HEX64.fullmatch(config.expected_fresh_receipt_sha256)
    ):
        raise RunnerError("FRESH_RECEIPT_DIGEST_UNAVAILABLE", "expected fresh receipt digest is not pinned")
    if digest != config.expected_fresh_receipt_sha256:
        raise RunnerError(
            "FRESH_RECEIPT_DIGEST_MISMATCH",
            "fresh receipt bytes are not the expected digest",
        )
    receipt["_identity"] = receipt_identity
    return receipt, digest


def _package_path(source_root: Path, package_name: str) -> Path:
    in_skills = source_root / "skills" / package_name
    return in_skills if _lexists(in_skills) else source_root / package_name


def _tree_snapshot(root: Path, code: str) -> list[dict[str, object]]:
    _require_directory(root, code)
    entries: list[dict[str, object]] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for name in sorted(directories):
            if name == "__pycache__":
                continue
            path = current_path / name
            result = _lstat(path, code)
            if not stat.S_ISDIR(result.st_mode):
                raise RunnerError(code, f"package entry is not a directory: {path}")
            kept_directories.append(name)
        directories[:] = kept_directories
        for name in sorted(files):
            if name.endswith(".pyc"):
                continue
            path = current_path / name
            result = _lstat(path, code)
            if not stat.S_ISREG(result.st_mode):
                raise RunnerError(code, f"package entry is not a regular file: {path}")
            entries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": result.st_size,
                    "sha256": _sha256(path, code),
                }
            )
    return entries


_PACKAGE_MANIFEST = ".skill-package.json"
_PACKAGE_TEXT_SUFFIXES = frozenset({".toml", ".md", ".py", ".yaml", ".yml", ".json", ".txt"})


def _package_digest(package_root: Path) -> str:
    digest = hashlib.sha256()
    _require_directory(package_root, "PACKAGE_INVALID")
    files = sorted(
        (
            path
            for path in package_root.rglob("*")
            if path.is_file()
            and path.name != _PACKAGE_MANIFEST
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
        ),
        key=lambda path: path.relative_to(package_root).as_posix(),
    )
    for path in files:
        relative = path.relative_to(package_root).as_posix().encode("utf-8")
        content = _bound_bytes(path, "PACKAGE_INVALID")[0]
        if path.suffix.lower() in _PACKAGE_TEXT_SUFFIXES:
            content = content.replace(b"\r\n", b"\n")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _expected_package_manifest(package_root: Path, package_name: str) -> dict[str, object]:
    if package_root.name != package_name or package_name not in PACKAGE_NAMES:
        raise RunnerError("PACKAGE_MANIFEST_INVALID", f"unknown Skill package: {package_name}")
    return {
        "content_sha256": _package_digest(package_root),
        "schema_version": 1,
        "skill": package_name,
        "version": EXPECTED_PACKAGE_VERSION,
    }


def _package_manifest(package_root: Path, package_name: str) -> dict[str, object]:
    path = package_root / ".skill-package.json"
    expected = _expected_package_manifest(package_root, package_name)
    try:
        _require_regular_file(path, "PACKAGE_MANIFEST_INVALID")
        raw, _identity = _bound_bytes(path, "PACKAGE_MANIFEST_INVALID")
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise RunnerError("PACKAGE_MANIFEST_INVALID", f"manifest is unavailable: {path}") from error
    if type(manifest) is not dict:
        raise RunnerError("PACKAGE_MANIFEST_INVALID", f"manifest is not an object: {path}")
    if manifest != expected:
        raise RunnerError(
            "PACKAGE_MANIFEST_INVALID",
            f"package manifest identity is not the exact expected manifest for {package_name}",
        )
    return manifest


def _package_snapshot(config: RunnerConfig) -> dict[str, object]:
    labels = tuple(path.parent.name for path in config.install_roots)
    if labels != INSTALL_SURFACES:
        raise RunnerError("INSTALL_ROOTS_INVALID", "install roots are not .agents/.codex/.claude")
    sources: dict[str, object] = {}
    installed: dict[str, object] = {}
    file_paths: list[str] = []
    file_identities: dict[str, dict[str, int | str]] = {}
    file_hashes: dict[str, str] = {}
    for root in config.install_roots:
        _require_directory(root, "INSTALL_ROOT_UNAVAILABLE")
    for package_name in config.package_names:
        source = _package_path(config.repository_root, package_name)
        source_manifest = _package_manifest(source, package_name)
        source_digest = str(source_manifest["content_sha256"])
        expected_content_digests = dict(config.expected_package_content_digests)
        if package_name in expected_content_digests and source_digest != expected_content_digests[package_name]:
            raise RunnerError("PACKAGE_IDENTITY_MISMATCH", f"source package digest changed: {package_name}")
        sources[package_name] = _tree_snapshot(source, "SOURCE_PACKAGE_INVALID")
        source_entries = sources[package_name]
        if type(source_entries) is list:
            for entry in source_entries:
                path = source / str(entry["path"])
                path_text = _path_text(path)
                file_paths.append(path_text)
                snapshot = _bound_file_snapshot(
                    path,
                    "SOURCE_PACKAGE_INVALID",
                )
                file_identities[path_text] = snapshot["identity"]
                file_hashes[path_text] = str(snapshot["sha256"])
        for surface, root in zip(INSTALL_SURFACES, config.install_roots, strict=True):
            package_root = root / package_name
            installed_manifest = _package_manifest(package_root, package_name)
            installed[f"{surface}:{package_name}"] = _tree_snapshot(
                package_root, "INSTALLED_PACKAGE_INVALID"
            )
            if installed_manifest != source_manifest:
                raise RunnerError(
                    "PACKAGE_IDENTITY_MISMATCH",
                    f"{surface}:{package_name} manifest differs from source",
                )
            if installed[f"{surface}:{package_name}"] != sources[package_name]:
                raise RunnerError(
                    "PACKAGE_IDENTITY_MISMATCH",
                    f"{surface}:{package_name} content differs from source",
                )
            if installed_manifest != source_manifest:
                raise RunnerError(
                    "PACKAGE_IDENTITY_MISMATCH",
                    f"{surface}:{package_name} manifest drifted from source",
                )
            installed_entries = installed[f"{surface}:{package_name}"]
            if type(installed_entries) is list:
                for entry in installed_entries:
                    path = package_root / str(entry["path"])
                    path_text = _path_text(path)
                    file_paths.append(path_text)
                    snapshot = _bound_file_snapshot(
                        path,
                        "INSTALLED_PACKAGE_INVALID",
                    )
                    file_identities[path_text] = snapshot["identity"]
                    file_hashes[path_text] = str(snapshot["sha256"])
    value = {"sources": sources, "installed": installed}
    package_digest = _exact_digest_value(value)
    if config.expected_package_digest is not None and package_digest != config.expected_package_digest:
        raise RunnerError("PACKAGE_IDENTITY_MISMATCH", "package digest is not the expected identity")
    return {
        "digest": package_digest,
        "value": value,
        "file_paths": sorted(file_paths),
        "file_identities": file_identities,
        "file_hashes": file_hashes,
    }


def _validate_parented_path(path: Path, evidence_root: Path, code: str) -> None:
    _require_directory(path.parent, code)
    if path.parent.resolve() != evidence_root.resolve():
        raise RunnerError(code, f"path is outside the evidence root: {path}")


def _directory_identity(path: Path, code: str) -> dict[str, int | str]:
    descriptor, identity = _open_bound_directory(path, code)
    try:
        return identity
    finally:
        os.close(descriptor)


class _PublicationLease:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.descriptor: int | None = None
        self.identity: dict[str, int | str] | None = None
        self.component_descriptors: list[int] = []
        self.component_identities: list[dict[str, int | str]] = []

    def __enter__(self) -> "_PublicationLease":
        self.component_descriptors, self.component_identities = _open_directory_components(
            self.path,
            "OUTPUT_PARENT_INVALID",
            allow_file_create=True,
        )
        self.descriptor = self.component_descriptors[-1]
        self.identity = self.component_identities[-1]
        return self

    def assert_stable(self) -> None:
        if (
            self.descriptor is None
            or self.identity is None
            or not self.component_descriptors
            or not self.component_identities
        ):
            raise RunnerError("LIVE_INPUT_DRIFT", "evidence parent is not held")
        _assert_directory_handles(
            self.path,
            self.component_descriptors,
            self.component_identities,
            "LIVE_INPUT_DRIFT",
        )

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        _close_descriptors(self.component_descriptors)
        self.component_identities.clear()
        self.descriptor = None
        self.identity = None


def _assert_publication_parent(
    config: RunnerConfig,
    expected_identity: Mapping[str, object] | None = None,
    lease: _PublicationLease | None = None,
) -> dict[str, int | str]:
    _validate_parented_path(config.report_path, config.evidence_root, "OUTPUT_PARENT_INVALID")
    _validate_parented_path(config.evidence_path, config.evidence_root, "OUTPUT_PARENT_INVALID")
    if lease is not None:
        lease.assert_stable()
        if expected_identity is None or lease.identity is None:
            return dict(lease.identity or {})
        observed = dict(lease.identity)
    else:
        observed = _directory_identity(config.evidence_root, "OUTPUT_PARENT_INVALID")
    if expected_identity is not None and not _identity_matches(observed, expected_identity):
        raise RunnerError("LIVE_INPUT_DRIFT", "evidence parent identity changed")
    return observed


def _validate_outputs(config: RunnerConfig, *, allow_existing: bool = False) -> None:
    _validate_parented_path(config.report_path, config.evidence_root, "OUTPUT_PARENT_INVALID")
    _validate_parented_path(config.evidence_path, config.evidence_root, "OUTPUT_PARENT_INVALID")
    if config.report_path.resolve() == config.evidence_path.resolve():
        raise RunnerError("OUTPUT_COLLISION", "report and evidence paths are identical")
    if not allow_existing:
        _require_absent(config.report_path, "OUTPUT_COLLISION")
        _require_absent(config.evidence_path, "OUTPUT_COLLISION")


def _validate_no_side_effect_paths(config: RunnerConfig) -> None:
    _require_absent(config.gateway_store_path, "GATEWAY_PATH_PRESENT")
    _require_absent(config.artifact_root, "ARTIFACT_PATH_PRESENT")
    for candidate in (*_sidecars(config.gateway_store_path), *_dynamic_sidecars(config.gateway_store_path)):
        if _lexists(candidate):
            raise RunnerError("GATEWAY_SIDECAR_PRESENT", f"gateway sidecar is present: {candidate}")
    for candidate in (*_sidecars(config.artifact_root), *_dynamic_sidecars(config.artifact_root)):
        if _lexists(candidate):
            raise RunnerError("ARTIFACT_SIDECAR_PRESENT", f"artifact sidecar is present: {candidate}")


def _validate_config_paths(config: RunnerConfig, *, allow_existing_outputs: bool = False) -> None:
    _validate_fixed_store_configuration(config)
    _require_directory(config.repository_root, "REPOSITORY_ROOT_INVALID")
    _require_directory(config.evidence_root, "EVIDENCE_ROOT_INVALID")
    _validate_parented_path(config.fresh_receipt, config.evidence_root, "RECEIPT_PARENT_INVALID")
    _validate_outputs(config, allow_existing=allow_existing_outputs)
    _validate_no_side_effect_paths(config)
    if config.authoritative_legacy_snapshot is not None:
        _require_directory(config.authoritative_legacy_snapshot.parent, "LEGACY_SNAPSHOT_PARENT_INVALID")
        _require_regular_file(config.authoritative_legacy_snapshot, "LEGACY_SNAPSHOT_UNAVAILABLE")


def _store_snapshots(config: RunnerConfig) -> dict[str, object]:
    return {
        _path_text(config.fresh_store): _store_snapshot(
            config.fresh_store,
            config.expected_fresh_store_sha256,
            "FRESH_STORE",
            expected_tables=config.expected_store_tables,
        ),
        _path_text(config.rollback_store): _store_snapshot(
            config.rollback_store,
            config.expected_rollback_store_sha256,
            "ROLLBACK_STORE",
        ),
        _path_text(config.prior_store): _store_snapshot(
            config.prior_store,
            config.expected_prior_store_sha256,
            "PRIOR_STORE",
        ),
    }


def _input_lease(config: RunnerConfig, preflight_result: dict[str, object]) -> _InputLease:
    expected: dict[Path, Mapping[str, object]] = {}
    stores = preflight_result["_stores"]
    if type(stores) is not dict:
        raise RunnerError("LIVE_INPUT_DRIFT", "preflight Store identities are unavailable")
    for snapshot in stores.values():
        if type(snapshot) is not dict:
            raise RunnerError("LIVE_INPUT_DRIFT", "preflight Store snapshot is malformed")
        path = Path(str(snapshot["path"]))
        identity = snapshot.get("identity")
        if type(identity) is not dict:
            raise RunnerError("LIVE_INPUT_DRIFT", f"preflight identity is absent: {path}")
        expected[path] = {
            "identity": identity,
            "sha256": snapshot.get("sha256"),
        }
    receipt = preflight_result.get("_receipt")
    if type(receipt) is not dict or type(receipt.get("_identity")) is not dict:
        raise RunnerError("LIVE_INPUT_DRIFT", "preflight receipt identity is unavailable")
    expected[config.fresh_receipt] = {
        "identity": receipt["_identity"],
        "sha256": preflight_result.get("_receipt_digest"),
    }
    packages = preflight_result.get("_packages")
    if (
        type(packages) is not dict
        or type(packages.get("file_identities")) is not dict
        or type(packages.get("file_hashes")) is not dict
    ):
        raise RunnerError("LIVE_INPUT_DRIFT", "preflight package identities are unavailable")
    for path, identity in packages["file_identities"].items():
        if type(identity) is not dict:
            raise RunnerError("LIVE_INPUT_DRIFT", f"preflight package identity is malformed: {path}")
        expected[Path(str(path))] = {
            "identity": identity,
            "sha256": packages["file_hashes"].get(path),
        }
    snapshot = preflight_result.get("_legacy_snapshot")
    if type(snapshot) is dict:
        path = Path(str(snapshot["path"]))
        identity = snapshot.get("identity")
        if type(identity) is not dict:
            raise RunnerError("LIVE_INPUT_DRIFT", "legacy snapshot identity is malformed")
        expected[path] = {
            "identity": identity,
            "sha256": snapshot.get("sha256"),
        }
    return _InputLease(expected)


def _pre_guard_refresh(
    config: RunnerConfig,
    preflight_result: dict[str, object],
    git_runner: Callable[..., subprocess.CompletedProcess[str]],
    *,
    allow_existing_outputs: bool = False,
) -> None:
    try:
        _validate_config_paths(config, allow_existing_outputs=allow_existing_outputs)
        git = _git_snapshot(config, git_runner)
        receipt, receipt_digest = _validate_receipt(config)
        stores = _store_snapshots(config)
        packages = _package_snapshot(config)
    except RunnerError as error:
        raise RunnerError("LIVE_INPUT_DRIFT", f"pre-Guard input changed: {error.detail}") from error
    if git != preflight_result["_git"]:
        raise RunnerError("LIVE_INPUT_DRIFT", "Git identity changed before Guard")
    if receipt_digest != preflight_result["_receipt_digest"]:
        raise RunnerError("LIVE_INPUT_DRIFT", "fresh receipt changed before Guard")
    if stores != preflight_result["_stores"]:
        raise RunnerError("LIVE_INPUT_DRIFT", "Store identity changed before Guard")
    previous_packages = preflight_result["_packages"]
    if packages != previous_packages:
        raise RunnerError("LIVE_INPUT_DRIFT", "package identity changed before Guard")
    if config.authoritative_legacy_snapshot is not None:
        snapshot = preflight_result.get("_legacy_snapshot")
        if type(snapshot) is not dict:
            raise RunnerError("LIVE_INPUT_DRIFT", "legacy snapshot identity is unavailable")
        current_snapshot = _bound_file_snapshot(
            config.authoritative_legacy_snapshot,
            "LIVE_INPUT_DRIFT",
        )
        current = current_snapshot["sha256"]
        current_identity = current_snapshot["identity"]
        if current != snapshot.get("sha256") or not _identity_matches(
            current_identity, snapshot.get("identity", {})
        ):
            raise RunnerError("LIVE_INPUT_DRIFT", "legacy snapshot changed before Guard")
    del receipt


def preflight(
    config: RunnerConfig = DEFAULT_CONFIG,
    *,
    git_runner: Callable[..., subprocess.CompletedProcess[str]] = _default_git_runner,
    allow_existing_outputs: bool = False,
) -> dict[str, object]:
    _validate_config_paths(config, allow_existing_outputs=allow_existing_outputs)
    git = _git_snapshot(config, git_runner)
    receipt, receipt_digest = _validate_receipt(config)
    stores = _store_snapshots(config)
    packages = _package_snapshot(config)
    legacy_snapshot: dict[str, object] | None = None
    if config.authoritative_legacy_snapshot is not None:
        _require_regular_file(config.authoritative_legacy_snapshot, "LEGACY_SNAPSHOT_UNAVAILABLE")
        contract = _guard_contract()
        _OperatorLegacyReadPort(config.authoritative_legacy_snapshot, contract).read(config.repository)
        snapshot = _bound_file_snapshot(config.authoritative_legacy_snapshot, "LEGACY_SNAPSHOT_INVALID")
        legacy_snapshot = snapshot
    return {
        "status": "PREFLIGHT_OK",
        "repository": config.repository,
        "head": git["head"],
        "tree": git["tree"],
        "origin_main": git["origin_main"],
        "tracked_status": git["status"],
        "fresh_receipt_sha256": receipt_digest,
        "fresh_store_sha256": stores[_path_text(config.fresh_store)]["sha256"],
        "rollback_store_sha256": stores[_path_text(config.rollback_store)]["sha256"],
        "prior_store_sha256": stores[_path_text(config.prior_store)]["sha256"],
        "store_generation": config.store_generation,
        "install_roots": [_path_text(path) for path in config.install_roots],
        "package_snapshot_digest": packages["digest"],
        "outputs_absent": not (_lexists(config.report_path) or _lexists(config.evidence_path)),
        "gateway_artifact_absent": True,
        "_git": git,
        "_receipt": receipt,
        "_receipt_digest": receipt_digest,
        "_stores": stores,
        "_packages": packages,
        "_legacy_snapshot": legacy_snapshot,
        "_evidence_parent_identity": _directory_identity(
            config.evidence_root, "EVIDENCE_ROOT_INVALID"
        ),
    }


def _plain_observation(value: object) -> object:
    canonical = getattr(value, "canonical", None)
    if callable(canonical):
        value = canonical()
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if type(value) in (str, int, float, bool) or value is None:
        return value
    if type(value) is dict:
        return {str(key): _plain_observation(child) for key, child in value.items()}
    if type(value) in (tuple, list):
        return [_plain_observation(child) for child in value]
    if hasattr(value, "__dict__"):
        return {
            str(key): _plain_observation(child)
            for key, child in vars(value).items()
            if not str(key).startswith("_")
        }
    raise RunnerError("READBACK_INVALID", "read-only observation has no canonical projection")


def _observation_digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(_plain_observation(value))).hexdigest()


def _guard_digest(value: object) -> str:
    return _exact_digest_value(_plain_observation(value))


def _guard_contract() -> GuardTypeContract:
    try:
        from gwo_v8._canonical import digest_value
        from gwo_v8.cutover_guard import (
            CompatibilityPathReadback,
            CutoverGuardReport,
            CutoverReadbackBundle,
            CutoverSubject,
            DurableStateReadback,
            LegacyReadback,
            OwnershipReadback,
            PackageReadback,
            RuntimePreflightReadback,
            WriterFenceReadback,
        )
    except (ImportError, ModuleNotFoundError, OSError) as error:
        raise RunnerError("LIVE_GUARD_UNAVAILABLE", "exact-main Guard types are unavailable") from error
    return GuardTypeContract(
        report_type=CutoverGuardReport,
        subject_type=CutoverSubject,
        bundle_type=CutoverReadbackBundle,
        readback_types=(
            ("legacy", LegacyReadback),
            ("durable_state", DurableStateReadback),
            ("writer_fence", WriterFenceReadback),
            ("ownership", OwnershipReadback),
            ("compatibility", CompatibilityPathReadback),
            ("runtime", RuntimePreflightReadback),
            ("packages", PackageReadback),
        ),
        digest_value=digest_value,
    )


def _canonical_guard_projection(value: object) -> dict[str, object]:
    canonical = getattr(value, "canonical", None)
    if not callable(canonical):
        raise RunnerError("LIVE_GUARD_INVALID", "typed Guard value has no canonical projection")
    try:
        projection = canonical()
    except Exception as error:
        raise RunnerError("LIVE_GUARD_INVALID", "typed Guard value could not be canonicalized") from error
    if type(projection) is not dict:
        raise RunnerError("LIVE_GUARD_INVALID", "typed Guard canonical projection is not an object")
    return projection


def _readback_digest(value: object) -> str:
    plain = _canonical_guard_projection(value)
    supplied = plain.get("readback_digest")
    if type(supplied) is not str or not _HEX64.fullmatch(supplied):
        raise RunnerError("LIVE_GUARD_INVALID", "readback digest is malformed")
    without_digest = {key: child for key, child in plain.items() if key != "readback_digest"}
    if supplied != _guard_digest(without_digest):
        raise RunnerError("LIVE_GUARD_INVALID", "readback digest is not independently bound")
    return _guard_digest(plain)


def _writer_generation(value: object, expected: str) -> str:
    plain = _plain_observation(value)
    observed: object | None = None
    if type(plain) is dict:
        observed = plain.get("writer_generation")
    if observed is None:
        observed = getattr(value, "writer_generation", None)
    if observed is None:
        raise RunnerError("WRITER_GENERATION_INVALID", "Writer generation is absent")
    if type(observed) is not str:
        raise RunnerError("WRITER_GENERATION_INVALID", "Writer generation is not exact text")
    if observed != expected:
        raise RunnerError(
            "WRITER_GENERATION_DRIFT",
            f"Writer generation is {observed}, not {expected}",
        )
    return observed


def _guard_execution(result: object) -> GuardExecution:
    if type(result) is GuardExecution:
        return result
    raise RunnerError("LIVE_GUARD_INVALID", "Guard result is not the exact bound GuardExecution")


def _bound_readbacks(
    value: object,
    contract: GuardTypeContract,
    expected_subject: object,
) -> tuple[dict[str, object], ...]:
    if type(value) is not contract.bundle_type:
        raise RunnerError("LIVE_GUARD_INVALID", "Guard readback bundle is not the exact current-main type")
    bundle_subject = getattr(value, "subject", None)
    if type(bundle_subject) is not contract.subject_type or bundle_subject != expected_subject:
        raise RunnerError("LIVE_GUARD_INVALID", "Guard readback subject is not the exact live subject")
    try:
        readback_types = dict(contract.readback_types)
        result = []
        for check_id in EXPECTED_CHECK_IDS:
            port_name = CHECK_TO_GUARD_PORT[check_id]
            readback = getattr(value, port_name)
            if type(readback) is not readback_types[port_name]:
                raise RunnerError("LIVE_GUARD_INVALID", f"Guard readback type is not exact: {port_name}")
            projection = _canonical_guard_projection(readback)
            result.append({"check_id": check_id, "readback": projection})
        return tuple(result)
    except AttributeError as error:
        raise RunnerError("LIVE_GUARD_INVALID", "Guard readback bundle does not contain seven exact reads") from error


def _digest_without(value: dict[str, object], excluded: str) -> str:
    return _guard_digest({key: child for key, child in value.items() if key != excluded})


def _guard_payload(
    result: object,
    expected_repository: str,
    *,
    expected_subject: object | None = None,
    contract: GuardTypeContract | None = None,
) -> dict[str, object]:
    execution = _guard_execution(result)
    contract = contract or execution.contract or _guard_contract()
    report = execution.report
    if type(report) is not contract.report_type:
        raise RunnerError("LIVE_GUARD_INVALID", "Guard report is not the exact current-main type")
    decision = getattr(report, "decision", None)
    if decision not in {"GO", "NO_GO"}:
        raise RunnerError("LIVE_GUARD_INVALID", "Guard did not return a typed decision")
    payload = _canonical_guard_projection(report)
    if set(payload) != {
        "schema",
        "decision",
        "repository",
        "subject_digest",
        "readback_digest",
        "checks",
        "blockers",
        "receipt",
    } or payload.get("decision") != decision:
        raise RunnerError("LIVE_GUARD_INVALID", "Guard report decision is not canonical")
    if payload.get("schema") != "gwo.cutover-guard.v1":
        raise RunnerError("LIVE_GUARD_INVALID", "Guard report schema is not canonical")
    if payload.get("repository") != expected_repository:
        raise RunnerError("LIVE_GUARD_INVALID", "Guard report repository is not exact")
    try:
        if type(execution.subject) is not contract.subject_type:
            raise RunnerError("LIVE_GUARD_INVALID", "Guard subject is not the exact current-main type")
        if expected_subject is not None and type(expected_subject) is not contract.subject_type:
            raise RunnerError("LIVE_GUARD_INVALID", "expected Guard subject is not the exact current-main type")
        subject_identity = _canonical_guard_projection(execution.subject)
        expected_identity = None if expected_subject is None else _canonical_guard_projection(expected_subject)
        if expected_identity is not None and subject_identity != expected_identity:
            raise RunnerError("LIVE_GUARD_INVALID", "Guard subject identity changed at the live boundary")
        bound_readbacks = _bound_readbacks(execution.readback_bundle, contract, execution.subject)
        expected_subject_digest = contract.digest_value(subject_identity)
        expected_readback_digest = contract.digest_value(
            {
                CHECK_TO_GUARD_PORT[check_id]: item["readback"]
                for check_id, item in zip(EXPECTED_CHECK_IDS, bound_readbacks, strict=True)
            }
        )
    except RunnerError as error:
        if error.code == "LIVE_GUARD_INVALID":
            raise
        raise RunnerError("LIVE_GUARD_INVALID", error.detail) from error
    if payload.get("subject_digest") != expected_subject_digest:
        raise RunnerError("LIVE_GUARD_INVALID", "Guard subject digest is not independently bound")
    if payload.get("readback_digest") != expected_readback_digest:
        raise RunnerError("LIVE_GUARD_INVALID", "Guard readback digest is not independently bound")
    if "activation_performed" in payload and payload["activation_performed"] is not False:
        raise RunnerError("LIVE_GUARD_INVALID", "Guard report claims activation was performed")
    for name in ("subject_digest", "readback_digest"):
        value = payload.get(name)
        if type(value) is not str or not _HEX64.fullmatch(value):
            raise RunnerError("LIVE_GUARD_INVALID", f"Guard report {name} is not a digest")
    checks = payload.get("checks")
    if type(checks) is not list or len(checks) != len(EXPECTED_CHECK_IDS):
        raise RunnerError("LIVE_GUARD_INVALID", "Guard report does not contain seven checks")
    if any(
        type(item) is not dict
        or type(item.get("passed")) is not bool
        or (item.get("observed_digest") is not None
            and (type(item["observed_digest"]) is not str
                 or not _HEX64.fullmatch(item["observed_digest"])))
        for item in checks
    ):
        raise RunnerError("LIVE_GUARD_INVALID", "Guard report checks are malformed")
    check_ids = tuple(item.get("check_id") for item in checks if type(item) is dict)
    if check_ids != EXPECTED_CHECK_IDS:
        raise RunnerError("LIVE_GUARD_INVALID", "Guard report check order is not canonical")
    blockers = payload.get("blockers")
    if type(blockers) is not list or any(
        type(item) is not dict
        or type(item.get("code")) is not str
        or type(item.get("check_id")) is not str
        or type(item.get("detail")) is not str
        or (item.get("observed_digest") is not None
            and (type(item["observed_digest"]) is not str
                 or not _HEX64.fullmatch(item["observed_digest"])))
        for item in blockers
    ):
        raise RunnerError("LIVE_GUARD_INVALID", "Guard report blockers are malformed")
    if decision == "GO" and type(payload.get("receipt")) is not dict:
        raise RunnerError("LIVE_GUARD_INVALID", "GO report has no receipt")
    if decision == "NO_GO" and payload.get("receipt") is not None:
        raise RunnerError("LIVE_GUARD_INVALID", "NO_GO report has a receipt")
    if decision == "GO" and (
        blockers or any(item["passed"] is not True for item in checks)
    ):
        raise RunnerError("LIVE_GUARD_INVALID", "GO report contains failed checks or blockers")
    for item, bound in zip(checks, bound_readbacks, strict=True):
        observed = item.get("observed_digest")
        if decision == "GO" and (type(observed) is not str or not observed):
            raise RunnerError("LIVE_GUARD_INVALID", "GO check digest is empty")
        if observed is not None and observed != contract.digest_value(bound["readback"]):
            raise RunnerError(
                "LIVE_GUARD_INVALID",
                f"Guard check digest is not bound: {item.get('check_id')}",
            )
    if decision == "NO_GO" and not blockers:
        raise RunnerError("LIVE_GUARD_INVALID", "NO_GO report has no canonical blocker")
    payload = dict(payload)
    payload["evidence_mode"] = EVIDENCE_MODE
    payload["activation_performed"] = False
    payload["subject_identity"] = subject_identity
    payload["readback_bundle"] = list(bound_readbacks)
    return payload


def _validate_guard_receipt(config: RunnerConfig, payload: dict[str, object]) -> None:
    receipt = payload.get("receipt")
    if payload["decision"] == "NO_GO":
        if receipt is not None:
            raise RunnerError("LIVE_GUARD_INVALID", "NO_GO report has a receipt")
        return
    if type(receipt) is not dict:
        raise RunnerError("LIVE_GUARD_INVALID", "GO report receipt is not canonical")
    expected_keys = {
        "schema",
        "repository",
        "subject_digest",
        "readback_digest",
        "source_writer_generation",
        "target_writer_generation",
        "store_generation",
        "writer_control_ref_digest",
        "runtime_configuration_digest",
        "compatibility_audit_digest",
        "package_readback_digest",
        "receipt_digest",
    }
    if set(receipt) != expected_keys:
        raise RunnerError("LIVE_GUARD_INVALID", "GO receipt schema is not closed")
    exact_values = (
        ("schema", "gwo.cutover-guard-receipt.v1"),
        ("repository", config.repository),
        ("source_writer_generation", config.source_writer_generation),
        ("target_writer_generation", config.target_writer_generation),
        ("store_generation", config.store_generation),
    )
    for name, expected in exact_values:
        if receipt.get(name) != expected:
            raise RunnerError("LIVE_GUARD_INVALID", f"GO receipt {name} is not exact")
    for name in (
        "subject_digest",
        "readback_digest",
        "writer_control_ref_digest",
        "runtime_configuration_digest",
        "compatibility_audit_digest",
        "package_readback_digest",
        "receipt_digest",
    ):
        value = receipt.get(name)
        if type(value) is not str or not _HEX64.fullmatch(value):
            raise RunnerError("LIVE_GUARD_INVALID", f"GO receipt {name} is not a digest")
    if receipt["subject_digest"] != payload["subject_digest"]:
        raise RunnerError("LIVE_GUARD_INVALID", "GO receipt subject digest is not bound")
    if receipt["readback_digest"] != payload["readback_digest"]:
        raise RunnerError("LIVE_GUARD_INVALID", "GO receipt readback digest is not bound")
    if receipt["receipt_digest"] != _digest_without(receipt, "receipt_digest"):
        raise RunnerError("LIVE_GUARD_INVALID", "GO receipt digest is not independently bound")
    readbacks = payload.get("readback_bundle")
    if type(readbacks) is not list or len(readbacks) != len(EXPECTED_CHECK_IDS):
        raise RunnerError("LIVE_GUARD_INVALID", "GO receipt has no exact readback binding")
    by_check = {
        item.get("check_id"): item.get("readback")
        for item in readbacks
        if type(item) is dict
    }
    writer = by_check.get("source_writer")
    runtime = by_check.get("runtime_configuration")
    compatibility = by_check.get("production_paths")
    packages = by_check.get("package_installation")
    if (
        type(writer) is not dict
        or type(runtime) is not dict
        or type(compatibility) is not dict
        or type(packages) is not dict
        or receipt["writer_control_ref_digest"] != writer.get("control_ref_digest")
        or receipt["runtime_configuration_digest"] != runtime.get("configuration_digest")
        or receipt["compatibility_audit_digest"] != compatibility.get("readback_digest")
        or receipt["package_readback_digest"] != packages.get("readback_digest")
    ):
        raise RunnerError("LIVE_GUARD_INVALID", "GO receipt components are not bound to the seven readbacks")


_REPORT_PAYLOAD_KEYS = frozenset(
    {
        "schema",
        "decision",
        "repository",
        "subject_digest",
        "readback_digest",
        "checks",
        "blockers",
        "receipt",
        "evidence_mode",
        "activation_performed",
        "subject_identity",
        "readback_bundle",
    }
)
_REPORT_METADATA_KEYS = frozenset(
    {
        "captured_at",
        "runbook_schema",
        "runbook",
        "runbook_sha256",
        "source_head",
        "source_tree",
        "origin_main",
        "store_generation",
        "fresh_store_sha256",
        "writer_generation",
        "default_writer_changed",
        "publication_protocol",
    }
)
_EVIDENCE_KEYS = frozenset(
    {
        "schema",
        "captured_at",
        "runbook",
        "runbook_sha256",
        "head",
        "tree",
        "origin_main",
        "decision",
        "exit_code",
        "evidence_mode",
        "activation_performed",
        "default_writer_changed",
        "writer_generation",
        "canonical_guard_evidence",
        "checks",
        "blocker_codes",
        "receipt",
        "report_digest",
        "report_file_identity",
        "guard_subject_digest",
        "guard_readback_digest",
        "report_path",
        "read_only_inputs",
        "before",
        "after",
        "safety",
    }
)


def _existing_output_collision(detail: str) -> RunnerError:
    return RunnerError("OUTPUT_COLLISION", detail)


def _open_existing_json(
    path: Path,
    parent: _PublicationLease,
    code: str,
) -> tuple["_OwnedOutput", dict[str, object], str]:
    descriptor, identity = _open_bound_handle(path, code, parent=parent)
    owner = _OwnedOutput(path, descriptor, identity, parent)
    try:
        raw = _read_descriptor_bytes(descriptor, code)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        owner.close()
        raise RunnerError(code, f"canonical JSON is unavailable: {path}") from error
    if type(value) is not dict:
        owner.close()
        raise RunnerError(code, f"canonical JSON root is not an object: {path}")
    if canonical_json_bytes(value) != raw:
        owner.close()
        raise RunnerError(code, f"JSON is not canonical: {path}")
    current = _windows_handle_identity(descriptor, code, directory=False)
    if not _identity_matches(current, identity):
        owner.close()
        raise RunnerError(code, f"canonical JSON identity changed: {path}")
    owner.data = raw
    return owner, value, hashlib.sha256(raw).hexdigest()


def _existing_report_payload(
    config: RunnerConfig,
    subject: object,
    publication: _PublicationLease,
) -> tuple[
    dict[str, object],
    str,
    dict[str, int | str],
    dict[str, object],
    "_OwnedOutput",
]:
    owner: _OwnedOutput | None = None
    try:
        owner, report, digest = _open_existing_json(
            config.report_path,
            publication,
            "OUTPUT_COLLISION",
        )
        result = _validate_existing_report_value(
            config,
            subject,
            report,
            digest,
            owner.identity,
        )
        return (*result, owner)
    except RunnerError as error:
        if owner is not None:
            owner.close()
        if error.code == "OUTPUT_COLLISION":
            raise
        raise _existing_output_collision(f"existing report is not valid: {error.detail}") from error


def _rehydrate_existing_guard_report(
    subject: object,
    report: dict[str, object],
    payload: dict[str, object],
) -> None:
    contract = _guard_contract()
    if type(subject) is not contract.subject_type:
        raise _existing_output_collision("existing report subject is not the exact Guard type")
    expected_subject = _canonical_guard_projection(subject)
    subject_identity = payload.get("subject_identity")
    if type(subject_identity) is not dict or subject_identity != expected_subject:
        raise _existing_output_collision("existing report subject identity is not exact")

    readback_items = payload.get("readback_bundle")
    if type(readback_items) is not list or len(readback_items) != len(EXPECTED_CHECK_IDS):
        raise _existing_output_collision("existing report readback bundle is not exact")
    by_check: dict[str, dict[str, object]] = {}
    for expected_check, item in zip(EXPECTED_CHECK_IDS, readback_items, strict=True):
        if type(item) is not dict or set(item) != {"check_id", "readback"}:
            raise _existing_output_collision("existing report readback binding is not closed")
        if item.get("check_id") != expected_check or type(item.get("readback")) is not dict:
            raise _existing_output_collision("existing report readback binding is not exact")
        by_check[expected_check] = item["readback"]

    try:
        import gwo_v8.cutover_guard as cutover_guard

        decode_bundle = getattr(cutover_guard, "_decode_bundle")
        bundle_value = {
            "schema": GUARD_READBACK_SCHEMA,
            "subject": deepcopy(subject_identity),
            "readbacks": {
                CHECK_TO_GUARD_PORT[check_id]: deepcopy(by_check[check_id])
                for check_id in EXPECTED_CHECK_IDS
            },
        }
        expected_bundle = deepcopy(bundle_value)
        bundle = decode_bundle(bundle_value)
    except Exception as error:
        raise _existing_output_collision(
            "existing report readback bundle is not an exact current-main bundle"
        ) from error
    if type(bundle) is not contract.bundle_type or bundle.subject != subject:
        raise _existing_output_collision("existing report readback subject is not exact")
    if _canonical_guard_projection(bundle) != expected_bundle:
        raise _existing_output_collision("existing report readback bundle is not canonical")
    for port_name in ("legacy", "durable_state", "writer_fence", "ownership", "compatibility", "runtime"):
        readback = getattr(bundle, port_name)
        if readback.repository != subject.repository:
            raise _existing_output_collision(
                f"existing report {port_name} readback is not bound to the subject"
            )
    if bundle.legacy.writer_generation != subject.source_writer_generation:
        raise _existing_output_collision("existing report legacy readback writer is not bound")
    if bundle.durable_state.generation_id != subject.store_generation:
        raise _existing_output_collision("existing report durable Store generation is not bound")
    if (
        bundle.writer_fence.writer_generation != subject.source_writer_generation
        or bundle.compatibility.source_commit != subject.source_commit
        or bundle.compatibility.source_tree_digest != subject.source_tree_digest
        or tuple(item.selector for item in bundle.runtime.selectors)
        != subject.required_runtime_selectors
    ):
        raise _existing_output_collision("existing report readback identity is not bound")

    expected_readback_bundle = [
        {
            "check_id": check_id,
            "readback": _canonical_guard_projection(
                getattr(bundle, CHECK_TO_GUARD_PORT[check_id])
            ),
        }
        for check_id in EXPECTED_CHECK_IDS
    ]
    if payload.get("readback_bundle") != expected_readback_bundle:
        raise _existing_output_collision("existing report readback bundle is not bound to the typed bundle")

    try:
        import gwo_v8.cutover_guard as cutover_guard

        replay_port = getattr(cutover_guard, "_ReplayReadPort")
        sources = cutover_guard.CutoverGuardSources(
            **{
                name: replay_port(getattr(bundle, name))
                for name in GUARD_PORT_ORDER
            }
        )
        replayed_report = cutover_guard.CutoverGuard(sources).evaluate(subject)
        expected_report = _canonical_guard_projection(replayed_report)
    except Exception as error:
        raise _existing_output_collision(
            "existing report could not be replayed through the exact current-main Guard"
        ) from error
    persisted_report = {
        name: report[name]
        for name in (
            "schema",
            "decision",
            "repository",
            "subject_digest",
            "readback_digest",
            "checks",
            "blockers",
            "receipt",
        )
    }
    if persisted_report != expected_report:
        raise _existing_output_collision(
            "existing report does not match the exact current-main Guard semantics"
        )


def _validate_existing_report_value(
    config: RunnerConfig,
    subject: object,
    report: dict[str, object],
    digest: str,
    identity: Mapping[str, object],
    *,
    replay_semantics: bool = True,
) -> tuple[dict[str, object], str, dict[str, int | str], dict[str, object]]:
    _validate_closed_file_identity(identity, "existing report")
    if set(report) != _REPORT_PAYLOAD_KEYS | _REPORT_METADATA_KEYS:
        raise _existing_output_collision("existing report has an unknown or missing field")
    exact_values = (
        ("schema", "gwo.cutover-guard.v1"),
        ("runbook_schema", REPORT_SCHEMA),
        ("runbook", _path_text(Path(__file__))),
        ("runbook_sha256", _runbook_hash()),
        ("source_head", config.expected_head),
        ("source_tree", config.expected_tree),
        ("store_generation", config.store_generation),
        ("fresh_store_sha256", config.expected_fresh_store_sha256),
        ("writer_generation", config.source_writer_generation),
        ("default_writer_changed", False),
        ("publication_protocol", "report-first-exclusive-v1"),
        ("repository", config.repository),
        ("evidence_mode", EVIDENCE_MODE),
        ("activation_performed", False),
    )
    for name, expected in exact_values:
        if report.get(name) != expected:
            raise _existing_output_collision(f"existing report {name} is not bound to this run")
    _canonical_utc_timestamp(report.get("captured_at"), "OUTPUT_COLLISION")
    if report.get("origin_main") != config.expected_head:
        raise _existing_output_collision("existing report origin is not bound to this run")
    if type(report.get("subject_identity")) is not dict:
        raise _existing_output_collision("existing report subject identity is malformed")
    expected_subject_identity = _canonical_guard_projection(subject)
    if report["subject_identity"] != expected_subject_identity:
        raise _existing_output_collision("existing report subject is not the current exact subject")
    if report.get("subject_digest") != _guard_digest(expected_subject_identity):
        raise _existing_output_collision("existing report subject digest is stale")
    decision = report.get("decision")
    if decision not in {"GO", "NO_GO"}:
        raise _existing_output_collision("existing report decision is malformed")
    checks = report.get("checks")
    if type(checks) is not list or len(checks) != len(EXPECTED_CHECK_IDS):
        raise _existing_output_collision("existing report does not contain seven checks")
    if tuple(item.get("check_id") for item in checks if type(item) is dict) != EXPECTED_CHECK_IDS:
        raise _existing_output_collision("existing report check order is not canonical")
    for item in checks:
        if (
            type(item) is not dict
            or set(item) != {"check_id", "passed", "observed_digest"}
            or type(item.get("passed")) is not bool
            or (item.get("observed_digest") is not None
                and (type(item["observed_digest"]) is not str
                     or not _HEX64.fullmatch(item["observed_digest"])))
        ):
            raise _existing_output_collision("existing report check is malformed")
    blockers = report.get("blockers")
    if type(blockers) is not list:
        raise _existing_output_collision("existing report blockers are malformed")
    for blocker in blockers:
        if (
            type(blocker) is not dict
            or set(blocker) != {"code", "check_id", "observed_digest", "detail"}
            or type(blocker.get("code")) is not str
            or type(blocker.get("check_id")) is not str
            or type(blocker.get("detail")) is not str
            or (blocker.get("observed_digest") is not None
                and (type(blocker["observed_digest"]) is not str
                     or not _HEX64.fullmatch(blocker["observed_digest"])))
        ):
            raise _existing_output_collision("existing report blocker is malformed")
    if decision == "GO" and (blockers or any(item["passed"] is not True for item in checks)):
        raise _existing_output_collision("existing report GO decision is not complete")
    if decision == "NO_GO" and not blockers:
        raise _existing_output_collision("existing report NO_GO has no blocker")
    readbacks = report.get("readback_bundle")
    if type(readbacks) is not list or len(readbacks) != len(EXPECTED_CHECK_IDS):
        raise _existing_output_collision("existing report readback bundle is incomplete")
    bound: dict[str, dict[str, object]] = {}
    for expected_check, item in zip(EXPECTED_CHECK_IDS, readbacks, strict=True):
        if type(item) is not dict or item.get("check_id") != expected_check:
            raise _existing_output_collision("existing report readback order is not canonical")
        readback = item.get("readback")
        if type(readback) is not dict:
            raise _existing_output_collision("existing report readback is malformed")
        supplied = readback.get("readback_digest")
        if type(supplied) is not str or not _HEX64.fullmatch(supplied):
            raise _existing_output_collision("existing report readback digest is malformed")
        if supplied != _guard_digest({key: value for key, value in readback.items() if key != "readback_digest"}):
            raise _existing_output_collision("existing report readback digest is stale")
        bound[expected_check] = readback
        observed = next(check["observed_digest"] for check in checks if check["check_id"] == expected_check)
        if decision == "GO" and (type(observed) is not str or not observed):
            raise _existing_output_collision("existing report GO check digest is empty")
        if observed is not None and observed != _guard_digest(readback):
            raise _existing_output_collision("existing report check digest is stale")
    expected_readback_digest = _guard_digest(
        {
            CHECK_TO_GUARD_PORT[check_id]: bound[check_id]
            for check_id in EXPECTED_CHECK_IDS
        }
    )
    if report.get("readback_digest") != expected_readback_digest:
        raise _existing_output_collision("existing report readback digest is stale")
    payload = {key: report[key] for key in _REPORT_PAYLOAD_KEYS}
    if replay_semantics:
        _rehydrate_existing_guard_report(subject, report, payload)
    try:
        _validate_guard_receipt(config, payload)
    except RunnerError as error:
        raise _existing_output_collision(f"existing report receipt is not bound: {error.detail}") from error
    return payload, digest, identity, report


def _validate_existing_evidence(
    config: RunnerConfig,
    report: dict[str, object],
    payload: dict[str, object],
    report_digest: str,
    report_identity: Mapping[str, object],
    publication: _PublicationLease,
    preflight_result: dict[str, object],
    current_control: object,
    current_packages: object,
) -> tuple[dict[str, object], str, _OwnedOutput]:
    owner: _OwnedOutput | None = None
    try:
        owner, evidence, evidence_digest = _open_existing_json(
            config.evidence_path,
            publication,
            "OUTPUT_COLLISION",
        )
        value, digest = _validate_existing_evidence_value(
            config,
            report,
            payload,
            report_digest,
            report_identity,
            evidence,
            evidence_digest,
            preflight_result,
            current_control,
            current_packages,
        )
        return value, digest, owner
    except RunnerError as error:
        if owner is not None:
            owner.close()
        if error.code == "OUTPUT_COLLISION":
            raise
        raise _existing_output_collision(f"existing evidence is not valid: {error.detail}") from error


def _validate_existing_evidence_value(
    config: RunnerConfig,
    report: dict[str, object],
    payload: dict[str, object],
    report_digest: str,
    report_identity: Mapping[str, object],
    evidence: dict[str, object],
    evidence_digest: str,
    preflight_result: dict[str, object],
    current_control: object,
    current_packages: object,
) -> tuple[dict[str, object], str]:
    _validate_closed_file_identity(report_identity, "report file")
    if set(evidence) != _EVIDENCE_KEYS:
        raise _existing_output_collision("existing evidence has an unknown or missing field")
    if evidence.get("schema") != EVIDENCE_SCHEMA:
        raise _existing_output_collision("existing evidence schema is not exact")
    report_bindings = (
        ("captured_at", report.get("captured_at")),
        ("runbook", report.get("runbook")),
        ("runbook_sha256", report.get("runbook_sha256")),
        ("head", report.get("source_head")),
        ("tree", report.get("source_tree")),
        ("origin_main", report.get("origin_main")),
        ("evidence_mode", report.get("evidence_mode")),
        ("writer_generation", report.get("writer_generation")),
        ("report_path", _path_text(config.report_path)),
    )
    for name, expected in report_bindings:
        if evidence.get(name) != expected:
            raise _existing_output_collision(f"existing evidence {name} is not bound to the report")
    try:
        evidence_captured_at = _canonical_utc_timestamp(
            evidence.get("captured_at"), "OUTPUT_COLLISION"
        )
        report_captured_at = _canonical_utc_timestamp(
            report.get("captured_at"), "OUTPUT_COLLISION"
        )
    except RunnerError as error:
        raise _existing_output_collision(error.detail) from error
    if evidence_captured_at != report_captured_at:
        raise _existing_output_collision("existing evidence capture time is not bound to the report")
    if evidence.get("evidence_mode") != EVIDENCE_MODE:
        raise _existing_output_collision("existing evidence mode is not exact")
    if evidence.get("activation_performed") is not False or evidence.get("default_writer_changed") is not False:
        raise _existing_output_collision("existing evidence records a forbidden mutation")
    expected_inputs = {
        "fresh_store": _path_text(config.fresh_store),
        "rollback_store": _path_text(config.rollback_store),
        "prior_store": _path_text(config.prior_store),
        "fresh_receipt": _path_text(config.fresh_receipt),
        "install_roots": [_path_text(path) for path in config.install_roots],
        "control_branch": config.control_branch,
        "target_branch": config.target_branch,
    }
    if evidence.get("read_only_inputs") != expected_inputs:
        raise _existing_output_collision("existing evidence read-only inputs are not exact")
    if evidence.get("blocker_codes") != [item["code"] for item in payload["blockers"]]:
        raise _existing_output_collision("existing evidence blocker codes are not bound")
    if evidence.get("report_digest") != report_digest:
        raise _existing_output_collision("existing evidence is not bound to the report bytes")
    _validate_closed_file_identity(evidence.get("report_file_identity"), "evidence report file")
    if not _identity_matches(evidence["report_file_identity"], report_identity):
        raise _existing_output_collision("existing evidence report identity is not exact")
    if evidence.get("report_path") != _path_text(config.report_path):
        raise _existing_output_collision("existing evidence report path is not exact")
    if evidence.get("decision") != report.get("decision"):
        raise _existing_output_collision("existing evidence decision is not bound")
    expected_exit = 0 if report.get("decision") == "GO" else 2
    if evidence.get("exit_code") != expected_exit:
        raise _existing_output_collision("existing evidence exit code is not bound")
    if evidence.get("canonical_guard_evidence") != payload:
        raise _existing_output_collision("existing evidence Guard payload is not bound")
    if evidence.get("checks") != report.get("checks") or evidence.get("receipt") != report.get("receipt"):
        raise _existing_output_collision("existing evidence checks or receipt are not bound")
    if evidence.get("guard_subject_digest") != report.get("subject_digest"):
        raise _existing_output_collision("existing evidence subject digest is not bound")
    if evidence.get("guard_readback_digest") != report.get("readback_digest"):
        raise _existing_output_collision("existing evidence readback digest is not bound")
    expected_observation_keys = {"git", "stores", "fresh_receipt_sha256", "control", "packages"}
    expected_git_keys = {"head", "tree", "origin_main", "status"}
    expected_control = _plain_observation(current_control)
    expected_packages = _plain_observation(current_packages)
    _writer_generation(current_control, config.source_writer_generation)
    for label in ("before", "after"):
        observation = evidence.get(label)
        if type(observation) is not dict or set(observation) != expected_observation_keys:
            raise _existing_output_collision(f"existing evidence {label} observation is not closed")
        git = observation["git"]
        if type(git) is not dict or set(git) != expected_git_keys or any(
            type(value) is not str for value in git.values()
        ):
            raise _existing_output_collision(f"existing evidence {label} Git metadata is malformed")
        if observation["git"] != preflight_result.get("_git"):
            raise _existing_output_collision(f"existing evidence {label} Git metadata is not current")
        stores = observation["stores"]
        if type(stores) is not dict or stores != preflight_result.get("_stores"):
            raise _existing_output_collision(f"existing evidence {label} Store metadata is not current")
        receipt_digest = observation["fresh_receipt_sha256"]
        if type(receipt_digest) is not str or not _HEX64.fullmatch(receipt_digest):
            raise _existing_output_collision(f"existing evidence {label} receipt metadata is malformed")
        if receipt_digest != preflight_result.get("_receipt_digest"):
            raise _existing_output_collision(f"existing evidence {label} receipt metadata is not current")
        if observation["control"] != expected_control:
            raise _existing_output_collision(f"existing evidence {label} control metadata is not current")
        if observation["packages"] != expected_packages:
            raise _existing_output_collision(f"existing evidence {label} package metadata is not current")
    safety = evidence.get("safety")
    if type(safety) is not dict or set(safety) != {
        "github_mutation",
        "sqlite_write",
        "gateway_created",
        "artifact_created",
        "sqlite_sidecar_created",
        "package_installed",
        "production_admission",
        "writer_activation",
        "default_writer_changed",
        "tag_or_release_published",
        "paseo_mutation",
        "provider_action",
    } or any(value is not False for value in safety.values()):
        raise _existing_output_collision("existing evidence safety metadata is not closed")
    return evidence, evidence_digest


def _recovery_evidence(
    config: RunnerConfig,
    preflight_result: dict[str, object],
    payload: dict[str, object],
    report: dict[str, object],
    report_digest: str,
    report_identity: Mapping[str, object],
    current_control: object,
    current_packages: object,
) -> dict[str, object]:
    return _evidence(
        config,
        preflight_result,
        current_control,
        current_packages,
        current_control,
        current_packages,
        preflight_result,
        payload,
        report,
        report_digest,
        0 if report["decision"] == "GO" else 2,
        report["writer_generation"],
        report_identity,
    )


def _resume_existing_outputs(
    config: RunnerConfig,
    preflight_result: dict[str, object],
    subject: object,
    inputs: _InputLease,
    publication: _PublicationLease,
    expected_parent: Mapping[str, object],
    current_control: object,
    current_packages: object,
    dependencies: ExecutionDependencies,
    git_runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, object] | None:
    report_exists = _lexists(config.report_path)
    evidence_exists = _lexists(config.evidence_path)
    if not report_exists and not evidence_exists:
        return None
    if not report_exists:
        raise _existing_output_collision("evidence exists without its report")
    report_output: _OwnedOutput | None = None
    evidence_output: _OwnedOutput | None = None
    outputs: list[_OwnedOutput] = []
    try:
        payload, report_digest, report_identity, report, report_output = _existing_report_payload(
            config,
            subject,
            publication,
        )
        inputs.assert_stable()
        publication.assert_stable()
        if evidence_exists:
            _evidence_value, _evidence_digest, evidence_output = _validate_existing_evidence(
                config,
                report,
                payload,
                report_digest,
                report_identity,
                publication,
                preflight_result,
                current_control,
                current_packages,
            )
            inputs.assert_stable()
            _revalidate_owned_output(report_output, "OUTPUT_COLLISION")
            _revalidate_owned_output(evidence_output, "OUTPUT_COLLISION")
            publication.assert_stable()
            _validate_existing_report_value(
                config,
                subject,
                report,
                report_digest,
                report_identity,
            )
            _validate_existing_evidence_value(
                config,
                report,
                payload,
                report_digest,
                report_identity,
                _evidence_value,
                _evidence_digest,
                preflight_result,
                current_control,
                current_packages,
            )
            fresh_observation = _fresh_complete_observation(
                config,
                preflight_result,
                dependencies,
                git_runner,
            )
            if fresh_observation != _evidence_value.get("after"):
                raise _existing_output_collision(
                    "fresh complete observation is not exactly bound to existing evidence"
                )
            inputs.assert_stable()
            _revalidate_owned_output(report_output, "OUTPUT_COLLISION")
            _revalidate_owned_output(evidence_output, "OUTPUT_COLLISION")
            publication.assert_stable()
            _validate_existing_report_value(
                config,
                subject,
                report,
                report_digest,
                report_identity,
            )
            _validate_existing_evidence_value(
                config,
                report,
                payload,
                report_digest,
                report_identity,
                _evidence_value,
                _evidence_digest,
                preflight_result,
                current_control,
                current_packages,
            )
            exit_code = 0 if report["decision"] == "GO" else 2
            return _result(
                "GO" if exit_code == 0 else "NO_GO",
                exit_code,
                decision=report["decision"],
                report_path=_path_text(config.report_path),
                evidence_path=_path_text(config.evidence_path),
                report_digest=report_digest,
            )
        evidence = _recovery_evidence(
            config,
            preflight_result,
            payload,
            report,
            report_digest,
            report_identity,
            current_control,
            current_packages,
        )
        _assert_publication_parent(config, expected_parent, lease=publication)
        inputs.assert_stable()
        evidence_digest = _write_exclusive_json(
            config.evidence_path,
            evidence,
            parent=publication,
            ownership_out=outputs,
        )
        if len(outputs) != 1:
            raise RunnerError("OUTPUT_WRITE_FAILED", "evidence ownership was not retained")
        evidence_output = outputs[0]
        inputs.assert_stable()
        _revalidate_owned_output(report_output, "OUTPUT_COLLISION")
        _revalidate_owned_output(evidence_output, "OUTPUT_COLLISION")
        _validate_existing_report_value(
            config,
            subject,
            report,
            report_digest,
            report_identity,
        )
        _validate_existing_evidence_value(
            config,
            report,
            payload,
            report_digest,
            report_identity,
            evidence,
            evidence_digest,
            preflight_result,
            current_control,
            current_packages,
        )
    finally:
        for output in (evidence_output, report_output):
            if output is not None and output.descriptor >= 0:
                output.close()
        for output in outputs:
            if output is not evidence_output and output.descriptor >= 0:
                output.close()
    exit_code = 0 if report["decision"] == "GO" else 2
    return _result(
        "GO" if exit_code == 0 else "NO_GO",
        exit_code,
        decision=report["decision"],
        report_path=_path_text(config.report_path),
        evidence_path=_path_text(config.evidence_path),
        report_digest=report_digest,
    )


def _precheck_existing_output_bytes(config: RunnerConfig) -> None:
    if _lexists(config.report_path):
        raise _existing_output_collision("report output already exists")
    if _lexists(config.evidence_path):
        raise _existing_output_collision("evidence output already exists")


def _verify_post_files(
    config: RunnerConfig,
    before: dict[str, object],
    git_runner: Callable[..., subprocess.CompletedProcess[str]],
    *,
    allow_existing_outputs: bool = False,
) -> dict[str, object]:
    try:
        git = _git_snapshot(config, git_runner)
    except RunnerError as error:
        raise RunnerError("LIVE_INPUT_DRIFT", f"Git identity changed: {error.detail}") from error
    if git != before["_git"]:
        raise RunnerError("LIVE_INPUT_DRIFT", "Git identity or status changed during Guard")
    _validate_config_paths(config, allow_existing_outputs=allow_existing_outputs)
    _validate_no_side_effect_paths(config)
    try:
        receipt, receipt_digest = _validate_receipt(config)
        if receipt_digest != before["_receipt_digest"]:
            raise RunnerError("LIVE_INPUT_DRIFT", "fresh Store receipt changed during Guard")
        stores = _store_snapshots(config)
    except RunnerError as error:
        if (
            error.code.startswith("FRESH_RECEIPT_")
            or error.code.endswith("_HASH_MISMATCH")
            or error.code == "STORE_SIDECAR_PRESENT"
        ):
            raise RunnerError(
                "LIVE_INPUT_DRIFT",
                f"read-only Store input changed: {error.detail}",
            ) from error
        raise
    if stores != before["_stores"]:
        raise RunnerError("LIVE_INPUT_DRIFT", "Store identity changed during Guard")
    if config.authoritative_legacy_snapshot is not None:
        before_snapshot = before.get("_legacy_snapshot")
        if type(before_snapshot) is not dict:
            raise RunnerError("LIVE_INPUT_DRIFT", "legacy snapshot identity is unavailable")
        current_snapshot = _bound_file_snapshot(
            config.authoritative_legacy_snapshot,
            "LIVE_INPUT_DRIFT",
        )
        if current_snapshot["sha256"] != before_snapshot.get("sha256") or not _identity_matches(
            current_snapshot["identity"], before_snapshot.get("identity", {})
        ):
            raise RunnerError("LIVE_INPUT_DRIFT", "legacy snapshot changed during Guard")
    return {
        "_git": git,
        "_receipt": receipt,
        "_receipt_digest": receipt_digest,
        "_stores": stores,
        "_legacy_snapshot": before.get("_legacy_snapshot"),
    }


def _runbook_hash() -> str:
    return _sha256(Path(__file__), "RUNBOOK_READ_FAILED")


@dataclass
class _OwnedOutput:
    path: Path
    descriptor: int
    identity: dict[str, int | str]
    parent: _PublicationLease
    data: bytes | None = None

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


def _read_descriptor_bytes(descriptor: int, code: str) -> bytes:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                return b"".join(chunks)
            chunks.append(block)
    except OSError as error:
        raise RunnerError(code, "output handle readback failed") from error


def _revalidate_owned_output(output: _OwnedOutput, code: str) -> None:
    if output.descriptor < 0 or output.data is None:
        raise RunnerError(code, f"retained output handle is unavailable: {output.path}")
    output.parent.assert_stable()
    current = _windows_handle_identity(output.descriptor, code, directory=False)
    if not _identity_matches(current, output.identity):
        raise RunnerError(code, f"retained output identity changed: {output.path}")
    if _read_descriptor_bytes(output.descriptor, code) != output.data:
        raise RunnerError(code, f"retained output bytes changed: {output.path}")
    path_descriptor, path_identity = _open_bound_handle(
        output.path,
        code,
        expected_identity=output.identity,
        parent=output.parent,
    )
    try:
        if not _identity_matches(path_identity, output.identity):
            raise RunnerError(code, f"retained output path identity changed: {output.path}")
        if _read_descriptor_bytes(path_descriptor, code) != output.data:
            raise RunnerError(code, f"retained output path bytes changed: {output.path}")
    finally:
        os.close(path_descriptor)


def _delete_owned_handle(output: _OwnedOutput) -> None:
    if output.descriptor < 0:
        return
    current = _windows_handle_identity(output.descriptor, "OUTPUT_WRITE_FAILED", directory=False)
    if not _identity_matches(current, output.identity):
        return
    output.parent.assert_stable()
    if os.name == "nt":
        try:
            import ctypes
            import msvcrt

            class FileDispositionInfo(ctypes.Structure):
                _fields_ = [("delete_file", ctypes.c_int)]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            disposition = FileDispositionInfo(1)
            handle = msvcrt.get_osfhandle(output.descriptor)
            if not kernel32.SetFileInformationByHandle(
                handle,
                4,
                ctypes.byref(disposition),
                ctypes.sizeof(disposition),
            ):
                raise OSError(ctypes.get_last_error(), "SetFileInformationByHandle failed")
            return
        except (ImportError, OSError, AttributeError, TypeError) as error:
            raise RunnerError("OUTPUT_WRITE_FAILED", "owned output could not be removed by handle") from error
    try:
        os.unlink(output.path.name, dir_fd=output.parent.descriptor)
    except FileNotFoundError:
        return
    except OSError as error:
        raise RunnerError("OUTPUT_WRITE_FAILED", "owned output could not be removed") from error


def _remove_owned_output(output: _OwnedOutput) -> None:
    try:
        _delete_owned_handle(output)
    except (FileNotFoundError, RunnerError, OSError):
        return
    finally:
        output.close()


def _create_exclusive_output_handle(
    path: Path,
    code: str,
    *,
    parent: _PublicationLease | None = None,
) -> int:
    if parent is not None:
        parent.assert_stable()
        if parent.descriptor is None:
            raise RunnerError(code, f"held output parent is unavailable: {path}")
        return _open_path_handle(
            Path(path.name),
            code,
            directory=False,
            parent=parent.descriptor,
            create_new=True,
            writable=True,
        )
    return _open_path_handle(
        path,
        code,
        directory=False,
        create_new=True,
        writable=True,
    )


def _flush_output_handle(descriptor: int, code: str) -> None:
    if os.name != "nt":
        try:
            os.fsync(descriptor)
        except OSError as error:
            raise RunnerError(code, "output flush failed") from error
        return
    try:
        import ctypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = msvcrt.get_osfhandle(descriptor)
        if not kernel32.FlushFileBuffers(handle):
            raise OSError(ctypes.get_last_error(), "FlushFileBuffers failed")
    except (ImportError, OSError, AttributeError, TypeError) as error:
        raise RunnerError(code, "output flush failed") from error


def _write_exclusive_json(
    path: Path,
    value: object,
    *,
    parent: _PublicationLease | None = None,
    ownership_out: list[_OwnedOutput] | None = None,
) -> str:
    data = canonical_json_bytes(value)
    local_parent: _PublicationLease | None = None
    descriptor: int | None = None
    output: _OwnedOutput | None = None
    try:
        if parent is None:
            local_parent = _PublicationLease(path.parent)
            parent = local_parent.__enter__()
        parent.assert_stable()
        descriptor = _create_exclusive_output_handle(
            path,
            "OUTPUT_WRITE_FAILED",
            parent=parent,
        )
        created_identity = _windows_handle_identity(descriptor, "OUTPUT_WRITE_FAILED", directory=False)
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise RunnerError("OUTPUT_WRITE_FAILED", f"short write for output: {path}")
            offset += written
        if offset != len(data):
            raise RunnerError("OUTPUT_WRITE_FAILED", f"output write was incomplete: {path}")
        _flush_output_handle(descriptor, "OUTPUT_WRITE_FAILED")
        written_identity = _windows_handle_identity(descriptor, "OUTPUT_WRITE_FAILED", directory=False)
        if not _identity_matches(written_identity, created_identity):
            raise RunnerError("OUTPUT_WRITE_FAILED", f"output handle identity changed: {path}")
        if _read_descriptor_bytes(descriptor, "OUTPUT_WRITE_FAILED") != data:
            raise RunnerError("OUTPUT_WRITE_FAILED", f"output handle readback differs: {path}")
        os.close(descriptor)
        descriptor = None
        reopened, reopened_identity = _open_bound_handle(
            path,
            "OUTPUT_WRITE_FAILED",
            expected_identity=written_identity,
            parent=parent,
        )
        try:
            if _read_descriptor_bytes(reopened, "OUTPUT_WRITE_FAILED") != data:
                raise RunnerError("OUTPUT_WRITE_FAILED", f"output path readback differs: {path}")
            if not _identity_matches(reopened_identity, written_identity):
                raise RunnerError("OUTPUT_WRITE_FAILED", f"output path identity differs: {path}")
            output = _OwnedOutput(path, reopened, reopened_identity, parent, data)
            reopened = -1
        finally:
            if reopened >= 0:
                os.close(reopened)
        if ownership_out is not None:
            ownership_out.append(output)
            output = None
        else:
            output.close()
            output = None
        return hashlib.sha256(data).hexdigest()
    except RunnerError:
        if descriptor is not None:
            os.close(descriptor)
        if output is not None:
            output.close()
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        if output is not None:
            output.close()
        raise RunnerError("OUTPUT_WRITE_FAILED", f"cannot durably write output: {path}") from error
    finally:
        if local_parent is not None:
            local_parent.__exit__(None, None, None)


def _post_observation(
    config: RunnerConfig,
    before: dict[str, object],
    dependencies: ExecutionDependencies,
    git_runner: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[object, object, dict[str, object]]:
    after_control = dependencies.control_read()
    after_packages = dependencies.package_read(config)
    if _observation_digest(after_control) != before["_control_digest"]:
        raise RunnerError("LIVE_INPUT_DRIFT", "control ref changed during Guard")
    if _observation_digest(after_packages) != before["_package_digest"]:
        raise RunnerError("LIVE_INPUT_DRIFT", "package identity changed during Guard")
    _writer_generation(after_control, config.source_writer_generation)
    after_files = _verify_post_files(config, before["preflight"], git_runner)
    return after_control, after_packages, after_files


def _fresh_complete_observation(
    config: RunnerConfig,
    preflight_result: dict[str, object],
    dependencies: ExecutionDependencies,
    git_runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, object]:
    control = dependencies.control_read()
    packages = dependencies.package_read(config)
    _writer_generation(control, config.source_writer_generation)
    files = _verify_post_files(
        config,
        preflight_result,
        git_runner,
    )
    return {
        "git": files["_git"],
        "stores": files["_stores"],
        "fresh_receipt_sha256": files["_receipt_digest"],
        "control": _plain_observation(control),
        "packages": _plain_observation(packages),
    }


def _canonical_utc_timestamp(value: object, code: str) -> str:
    if type(value) is not str:
        raise RunnerError(code, "capture timestamp is not exact text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise RunnerError(code, "capture timestamp is not canonical ISO-8601") from error
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or parsed.isoformat() != value
    ):
        raise RunnerError(code, "capture timestamp is not canonical UTC")
    return value


def _evidence(
    config: RunnerConfig,
    preflight_result: dict[str, object],
    before_control: object,
    before_packages: object,
    after_control: object,
    after_packages: object,
    after_files: dict[str, object],
    payload: dict[str, object],
    report_body: dict[str, object],
    report_digest: str,
    exit_code: int,
    writer_generation: str,
    report_identity: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema": EVIDENCE_SCHEMA,
        "captured_at": _canonical_utc_timestamp(
            report_body["captured_at"], "LIVE_GUARD_INVALID"
        ),
        "runbook": _path_text(Path(__file__)),
        "runbook_sha256": report_body["runbook_sha256"],
        "head": preflight_result["head"],
        "tree": preflight_result["tree"],
        "origin_main": preflight_result["origin_main"],
        "decision": payload["decision"],
        "exit_code": exit_code,
        "evidence_mode": EVIDENCE_MODE,
        "activation_performed": False,
        "default_writer_changed": False,
        "writer_generation": writer_generation,
        "canonical_guard_evidence": payload,
        "checks": payload["checks"],
        "blocker_codes": [item["code"] for item in payload["blockers"]],
        "receipt": payload["receipt"],
        "report_digest": report_digest,
        "report_file_identity": dict(report_identity),
        "guard_subject_digest": payload["subject_digest"],
        "guard_readback_digest": payload["readback_digest"],
        "report_path": _path_text(config.report_path),
        "read_only_inputs": {
            "fresh_store": _path_text(config.fresh_store),
            "rollback_store": _path_text(config.rollback_store),
            "prior_store": _path_text(config.prior_store),
            "fresh_receipt": _path_text(config.fresh_receipt),
            "install_roots": [_path_text(path) for path in config.install_roots],
            "control_branch": config.control_branch,
            "target_branch": config.target_branch,
        },
        "before": {
            "git": preflight_result["_git"],
            "stores": preflight_result["_stores"],
            "fresh_receipt_sha256": preflight_result["_receipt_digest"],
            "control": _plain_observation(before_control),
            "packages": _plain_observation(before_packages),
        },
        "after": {
            "git": after_files["_git"],
            "stores": after_files["_stores"],
            "fresh_receipt_sha256": after_files["_receipt_digest"],
            "control": _plain_observation(after_control),
            "packages": _plain_observation(after_packages),
        },
        "safety": {
            "github_mutation": False,
            "sqlite_write": False,
            "gateway_created": False,
            "artifact_created": False,
            "sqlite_sidecar_created": False,
            "package_installed": False,
            "production_admission": False,
            "writer_activation": False,
            "default_writer_changed": False,
            "tag_or_release_published": False,
            "paseo_mutation": False,
            "provider_action": False,
        },
    }


def _result(status: str, exit_code: int, **values: object) -> dict[str, object]:
    return {"status": status, "exit_code": exit_code, **values}


def _dependencies_or_raise(
    config: RunnerConfig,
    dependencies: ExecutionDependencies | None,
    guard_factory: Callable[[RunnerConfig, object], object] | None,
    control_reader: Callable[[], object] | None,
    package_reader: Callable[[RunnerConfig], object] | None,
    validated_receipt: Mapping[str, object] | None = None,
) -> ExecutionDependencies:
    if dependencies is not None:
        return dependencies
    if guard_factory is not None or control_reader is not None or package_reader is not None:
        if guard_factory is None or control_reader is None or package_reader is None:
            raise RunnerError("DEPENDENCY_INJECTION_INVALID", "all live Guard dependencies are required")
        return ExecutionDependencies(guard_factory, control_reader, package_reader)
    return _production_dependencies(config, validated_receipt=validated_receipt)


def run(
    config: RunnerConfig = DEFAULT_CONFIG,
    *,
    execute: bool,
    git_runner: Callable[..., subprocess.CompletedProcess[str]] = _default_git_runner,
    dependencies: ExecutionDependencies | None = None,
    guard_factory: Callable[[RunnerConfig, object], object] | None = None,
    control_reader: Callable[[], object] | None = None,
    package_reader: Callable[[RunnerConfig], object] | None = None,
) -> dict[str, object]:
    try:
        preflight_result = preflight(
            config,
            git_runner=git_runner,
            allow_existing_outputs=False,
        )
    except RunnerError as error:
        return _result("REFUSED", 1, code=error.code, detail=error.detail)
    except Exception as error:
        return _result("UNAVAILABLE", 3, code="PREFLIGHT_UNAVAILABLE", detail=str(error))
    if not execute:
        return preflight_result | {"exit_code": 0}
    try:
        expected_parent = preflight_result.get("_evidence_parent_identity")
        if type(expected_parent) is not dict:
            raise RunnerError("LIVE_INPUT_DRIFT", "evidence parent identity is unavailable")
        with _PublicationLease(config.evidence_root) as publication:
            _assert_publication_parent(config, expected_parent, lease=publication)
            _precheck_existing_output_bytes(config)
            live_dependencies = _dependencies_or_raise(
                config,
                dependencies,
                guard_factory,
                control_reader,
                package_reader,
                preflight_result.get("_receipt"),
            )
            before_control = live_dependencies.control_read()
            before_packages = live_dependencies.package_read(config)
            before = dict(preflight_result)
            before["preflight"] = preflight_result
            before["_control_digest"] = _observation_digest(before_control)
            before["_package_digest"] = _observation_digest(before_packages)
            _pre_guard_refresh(
                config,
                preflight_result,
                git_runner,
            )
            guard_control = live_dependencies.control_read()
            guard_packages = live_dependencies.package_read(config)
            if _observation_digest(guard_control) != before["_control_digest"]:
                raise RunnerError("LIVE_INPUT_DRIFT", "control ref changed before Guard")
            if _observation_digest(guard_packages) != before["_package_digest"]:
                raise RunnerError("LIVE_INPUT_DRIFT", "package identity changed before Guard")
            before_control = guard_control
            before_packages = guard_packages
            writer_generation = _writer_generation(before_control, config.source_writer_generation)
            subject = (
                live_dependencies.subject_factory(config)
                if live_dependencies.subject_factory is not None
                else _default_subject_factory(config)
            )
            with _input_lease(config, preflight_result) as inputs:
                publication.assert_stable()
                inputs.assert_stable()
                try:
                    guard_report = live_dependencies.live_guard(config, subject)
                except RunnerError:
                    raise
                except OSError as error:
                    raise RunnerError(
                        "LIVE_INPUT_DRIFT",
                        "mutable input could not be held through Guard",
                    ) from error
                payload = _guard_payload(
                    guard_report,
                    config.repository,
                    expected_subject=subject,
                    contract=live_dependencies.guard_contract,
                )
                _validate_guard_receipt(config, payload)
                inputs.assert_stable()
                publication.assert_stable()
                after_control, after_packages, after_files = _post_observation(
                    config,
                    before,
                    live_dependencies,
                    git_runner,
                )
                inputs.assert_stable()
                publication.assert_stable()
                report_body = dict(payload)
                report_body.update(
                    {
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                        "runbook_schema": REPORT_SCHEMA,
                        "runbook": _path_text(Path(__file__)),
                        "runbook_sha256": _runbook_hash(),
                        "source_head": preflight_result["head"],
                        "source_tree": preflight_result["tree"],
                        "origin_main": preflight_result["origin_main"],
                        "store_generation": config.store_generation,
                        "fresh_store_sha256": preflight_result["fresh_store_sha256"],
                        "writer_generation": writer_generation,
                        "default_writer_changed": False,
                        "publication_protocol": "report-first-exclusive-v1",
                    }
                )
                report_outputs: list[_OwnedOutput] = []
                evidence_outputs: list[_OwnedOutput] = []
                try:
                    _assert_publication_parent(config, expected_parent, lease=publication)
                    inputs.assert_stable()
                    report_digest = _write_exclusive_json(
                        config.report_path,
                        report_body,
                        parent=publication,
                        ownership_out=report_outputs,
                    )
                    if len(report_outputs) != 1:
                        raise RunnerError("OUTPUT_WRITE_FAILED", "report ownership was not retained")
                    _assert_publication_parent(config, expected_parent, lease=publication)
                    inputs.assert_stable()
                    exit_code = 0 if payload["decision"] == "GO" else 2
                    evidence = _evidence(
                        config,
                        preflight_result,
                        before_control,
                        before_packages,
                        after_control,
                        after_packages,
                        after_files,
                        payload,
                        report_body,
                        report_digest,
                        exit_code,
                        writer_generation,
                        report_outputs[0].identity,
                    )
                    inputs.assert_stable()
                    evidence_digest = _write_exclusive_json(
                        config.evidence_path,
                        evidence,
                        parent=publication,
                        ownership_out=evidence_outputs,
                    )
                    if len(evidence_outputs) != 1:
                        raise RunnerError("OUTPUT_WRITE_FAILED", "evidence ownership was not retained")
                    inputs.assert_stable()
                    _revalidate_owned_output(report_outputs[0], "OUTPUT_WRITE_FAILED")
                    _revalidate_owned_output(evidence_outputs[0], "OUTPUT_WRITE_FAILED")
                    _validate_existing_report_value(
                        config,
                        subject,
                        report_body,
                        report_digest,
                        report_outputs[0].identity,
                        replay_semantics=False,
                    )
                    _validate_existing_evidence_value(
                        config,
                        report_body,
                        payload,
                        report_digest,
                        report_outputs[0].identity,
                        evidence,
                        evidence_digest,
                        preflight_result,
                        before_control,
                        before_packages,
                    )
                except RunnerError:
                    raise
                finally:
                    for output in evidence_outputs + report_outputs:
                        if output.descriptor >= 0:
                            output.close()
                status = "GO" if exit_code == 0 else "NO_GO"
                return _result(
                    status,
                    exit_code,
                    decision=payload["decision"],
                    report_path=_path_text(config.report_path),
                    evidence_path=_path_text(config.evidence_path),
                    report_digest=report_digest,
                )
    except RunnerError as error:
        exit_code = 1 if error.code in {"OUTPUT_COLLISION", "LIVE_INPUT_DRIFT"} else 3
        status = "REFUSED" if exit_code == 1 else "UNAVAILABLE"
        return _result(status, exit_code, code=error.code, detail=error.detail)
    except Exception as error:
        return _result("UNAVAILABLE", 3, code="LIVE_GUARD_UNAVAILABLE", detail=str(error))


def _default_subject_factory(config: RunnerConfig) -> object:
    _add_repo_import_paths(config.repository_root)
    from gwo_v8.cutover_guard import CutoverSubject, source_tree_digest

    return CutoverSubject(
        repository=config.repository,
        control_branch=config.control_branch,
        target_branch=config.target_branch,
        source_writer_generation=config.source_writer_generation,
        target_writer_generation=config.target_writer_generation,
        store_generation=config.store_generation,
        source_commit=config.expected_head,
        source_tree_digest=source_tree_digest(config.repository_root),
        production_entry_refs=PRODUCTION_ENTRY_REFS,
    )


def _add_repo_import_paths(root: Path) -> None:
    for candidate in (
        root / "skills" / "orchestrator" / "scripts",
        root,
    ):
        value = str(candidate)
        if value not in sys.path:
            sys.path.insert(0, value)


def _profile_configuration(config: RunnerConfig) -> object:
    _add_repo_import_paths(config.repository_root)
    from gwo_v8.runtime_gateway import ProfileMapping, RuntimeConfiguration
    from gwo_v8.runtime_profile import RuntimeProfile

    raw_config_path = Path.home() / ".orch" / "config.json"
    try:
        raw_config = json.loads(raw_config_path.read_text(encoding="utf-8"))
        role_profiles = raw_config["role_profiles"]
        default_tier = raw_config["tiers"][raw_config["global"]["default_tier"]]
        profiles: dict[str, object] = {}

        def make_profile(name: str, raw: Mapping[str, object]) -> object:
            settings = raw["settings"]
            if type(settings) is not dict:
                raise ValueError("Runtime profile settings are invalid")
            profile = RuntimeProfile(
                name=name,
                provider=raw["provider"],
                model=settings["model"],
                thinking=settings["thinkingOptionId"],
                mode=settings["modeId"],
                features=settings.get("features", {}),
            )
            profiles[profile.digest] = profile
            return profile

        resolved = {
            "coordinator": make_profile("coordinator_auto", role_profiles["coordinator_auto"]),
            "worker": make_profile("standard", default_tier),
            "recovery_worker": make_profile(
                "reviewer_recovery", role_profiles["reviewer_recovery"]
            ),
            "review_primary": make_profile(
                "reviewer_standard", role_profiles["reviewer_standard"]
            ),
            "review_strong": make_profile("reviewer_strict", role_profiles["reviewer_strict"]),
        }
        return RuntimeConfiguration(
            profiles=profiles,
            host_mappings={
                selector: ProfileMapping(profile.digest)
                for selector, profile in resolved.items()
            },
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RunnerError("RUNTIME_CONFIGURATION_UNAVAILABLE", "Runtime config is unavailable") from error


LEGACY_READBACK_KEYS = frozenset(
    {
        "repository",
        "writer_generation",
        "authority_state",
        "active_dispatches",
        "active_workers",
        "integration_lease_owner",
        "v2_execution_refs",
        "v2_execution_state",
        "original_decoder_readable",
        "durable_state_digest",
        "readback_digest",
    }
)


def _read_exact_main_json(path: Path, code: str) -> tuple[object, dict[str, int | str]]:
    raw, identity = _bound_bytes(path, code)
    try:
        from gwo_v8._canonical import load_canonical_json

        value = load_canonical_json(raw)
    except (ImportError, ModuleNotFoundError):
        try:
            value = json.loads(raw.decode("utf-8"))
            expected = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise RunnerError(code, f"exact-main JSON is unavailable: {path}") from error
        if raw != expected:
            raise RunnerError(code, f"exact-main JSON is not canonical: {path}")
    except Exception as error:
        raise RunnerError(code, f"exact-main JSON is not canonical: {path}") from error
    return value, identity


class _OperatorLegacyReadPort:
    def __init__(self, path: Path, contract: GuardTypeContract) -> None:
        self._path = path
        self._contract = contract

    def read(self, repository: str) -> object:
        value, _identity = _read_exact_main_json(self._path, "LIVE_GUARD_UNAVAILABLE")
        if type(value) is not dict or set(value) != LEGACY_READBACK_KEYS:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "legacy snapshot has an unknown or missing fact")
        if value.get("repository") != repository:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "legacy snapshot repository is not exact")
        active_dispatches = value.get("active_dispatches")
        active_workers = value.get("active_workers")
        v2_refs = value.get("v2_execution_refs")
        if any(
            type(items) is not list or any(type(item) is not str for item in items)
            for items in (active_dispatches, active_workers, v2_refs)
        ):
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "legacy snapshot tuple facts are malformed")
        if value.get("authority_state") not in {"active", "authoritative_quiescent", "stopped"}:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "legacy snapshot authority state is invalid")
        if value.get("v2_execution_state") not in {"none", "running", "terminal", "quiescent_read_only"}:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "legacy snapshot V2 state is invalid")
        lease_owner = value.get("integration_lease_owner")
        if lease_owner is not None and type(lease_owner) is not str:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "legacy snapshot lease owner is malformed")
        if type(value.get("original_decoder_readable")) is not bool:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "legacy snapshot decoder fact is absent")
        for name in ("repository", "writer_generation", "durable_state_digest", "readback_digest"):
            if type(value.get(name)) is not str:
                raise RunnerError("LIVE_GUARD_UNAVAILABLE", f"legacy snapshot fact is absent: {name}")
        for name in ("durable_state_digest", "readback_digest"):
            if not _HEX64.fullmatch(value[name]):
                raise RunnerError("LIVE_GUARD_UNAVAILABLE", f"legacy snapshot digest is malformed: {name}")
        body = {key: child for key, child in value.items() if key != "readback_digest"}
        if value["readback_digest"] != self._contract.digest_value(body):
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "legacy snapshot digest is stale")
        readback_type = dict(self._contract.readback_types)["legacy"]
        try:
            return readback_type(
                repository=value["repository"],
                writer_generation=value["writer_generation"],
                authority_state=value["authority_state"],
                active_dispatches=tuple(active_dispatches),
                active_workers=tuple(active_workers),
                integration_lease_owner=lease_owner,
                v2_execution_refs=tuple(v2_refs),
                v2_execution_state=value["v2_execution_state"],
                original_decoder_readable=value["original_decoder_readable"],
                durable_state_digest=value["durable_state_digest"],
                readback_digest=value["readback_digest"],
            )
        except (TypeError, ValueError) as error:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "legacy snapshot is not an exact typed readback") from error


def _production_dependencies(
    config: RunnerConfig,
    *,
    validated_receipt: Mapping[str, object] | None = None,
) -> ExecutionDependencies:
    _add_repo_import_paths(config.repository_root)
    if validated_receipt is None:
        validated_receipt, _receipt_digest = _validate_receipt(config)
    if type(validated_receipt) is not dict:
        raise RunnerError(
            "LIVE_GUARD_UNAVAILABLE",
            "validated fresh receipt is not an exact object",
        )
    contract = _guard_contract()
    readers = config.production_readers
    if type(readers) is not ProductionReaders:
        raise RunnerError(
            "LIVE_GUARD_UNAVAILABLE",
            "exact authoritative read-only production readers were not supplied",
        )
    try:
        from gwo_v8.cutover_guard import CutoverReadbackBundle
        from gwo_v8.plan_control_host import (
            CutoverGuardRequest,
            ProductionCutoverGuardHost,
            ProductionCutoverReadAdapterResolver,
            ProductionPlanControlStartHost,
        )
    except (ImportError, ModuleNotFoundError, OSError) as error:
        raise RunnerError("LIVE_GUARD_UNAVAILABLE", "exact-main Guard host types are unavailable") from error

    def _require_read_port(value: object, name: str) -> object:
        if not callable(getattr(value, "read", None)):
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", f"{name} is not a read-only port")
        forbidden = ("start", "stop", "restore", "write", "mutate", "activate", "cas", "advance")
        if any(callable(getattr(value, member, None)) for member in forbidden):
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", f"{name} exposes a mutating operation")
        return value

    if config.authoritative_legacy_snapshot is not None:
        legacy = _OperatorLegacyReadPort(config.authoritative_legacy_snapshot, contract)
    else:
        legacy = _require_read_port(readers.legacy_read, "legacy")
    if readers.durable_state_read is not None:
        raise RunnerError(
            "LIVE_GUARD_UNAVAILABLE",
            "durable-state Guard reads must be constructed by this runner",
        )
    durable = _ImmutableDurableStateReadPort(
        config.fresh_store,
        config.repository,
        config.store_generation,
        config.expected_store_tables,
        contract,
        validated_receipt,
    )
    writer = _require_read_port(readers.writer_fence_read, "writer_fence")
    ownership = _require_read_port(readers.ownership_read, "ownership")
    runtime_configuration = _profile_configuration(config)
    resolver = ProductionCutoverReadAdapterResolver(
        legacy=legacy,
        durable_state=durable,
        writer_fence=writer,
        ownership=ownership,
        runtime_configuration=runtime_configuration,
    )

    def reject_gateway(*_args: object, **_kwargs: object) -> object:
        raise RunnerError("LIVE_GUARD_UNAVAILABLE", "Gateway construction is forbidden in live Guard")

    host = ProductionPlanControlStartHost(
        source=readers.source,
        repository=readers.repository,
        runtime_configuration=runtime_configuration,
        repository_contexts={},
        gateway_store_path=config.gateway_store_path,
        artifact_root=config.artifact_root,
        _gateway_builder=reject_gateway,
        cutover_read_adapter_resolver=resolver,
    )
    if type(host) is not ProductionPlanControlStartHost:
        raise RunnerError("LIVE_GUARD_UNAVAILABLE", "exact ProductionPlanControlStartHost was not constructed")

    def read_port(name: str, port: object, subject: object) -> object:
        try:
            if name in {"legacy", "durable_state", "writer_fence", "ownership"}:
                value = port.read(config.repository)
            elif name == "runtime":
                value = port.read(config.repository, subject.required_runtime_selectors)
            else:
                value = port.read(subject)
        except RunnerError:
            raise
        except Exception as error:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", f"{name} readback is unavailable") from error
        expected_type = dict(contract.readback_types)[name]
        if type(value) is not expected_type:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", f"{name} readback type is not exact")
        _readback_digest(value)
        return value

    def live_guard(_config: RunnerConfig, subject: object) -> object:
        if type(subject) is not contract.subject_type:
            raise RunnerError("LIVE_GUARD_INVALID", "live subject is not exact-main CutoverSubject")
        request = CutoverGuardRequest(
            subject=subject,
            package_root=config.repository_root,
            install_roots=config.install_roots,
        )
        try:
            sources = host.resolve_cutover_guard_sources(request)
            if type(sources).__name__ != "CutoverGuardSources":
                raise RunnerError("LIVE_GUARD_UNAVAILABLE", "Guard sources are not exact-main typed")
            guard_host = host.install_cutover_guard(sources=sources)
            if type(guard_host) is not ProductionCutoverGuardHost:
                raise RunnerError("LIVE_GUARD_UNAVAILABLE", "Guard host is not exact-main typed")
            report = guard_host.check(subject)
            readbacks = {
                name: read_port(name, getattr(sources, name), subject)
                for name in GUARD_PORT_ORDER
            }
            bundle = CutoverReadbackBundle(
                schema=GUARD_READBACK_SCHEMA,
                subject=subject,
                **readbacks,
            )
        except RunnerError:
            raise
        except Exception as error:
            raise RunnerError("LIVE_GUARD_UNAVAILABLE", "exact-main read-only Guard composition failed") from error
        return GuardExecution(report, subject, bundle, contract)

    return ExecutionDependencies(
        live_guard=live_guard,
        control_read=lambda: writer.read(config.repository),
        package_read=_package_snapshot,
        subject_factory=_default_subject_factory,
        guard_contract=contract,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Beta3 production live Guard preflight/runbook",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="run the read-only live Guard and exclusively create the two evidence files",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    config: RunnerConfig = DEFAULT_CONFIG,
    git_runner: Callable[..., subprocess.CompletedProcess[str]] = _default_git_runner,
    dependencies: ExecutionDependencies | None = None,
    guard_factory: Callable[[RunnerConfig, object], object] | None = None,
    control_reader: Callable[[], object] | None = None,
    package_reader: Callable[[RunnerConfig], object] | None = None,
    stdout: TextIO | None = None,
) -> int:
    try:
        args = build_parser().parse_args(list(argv) if argv is not None else None)
    except SystemExit:
        return 1
    result = run(
        config,
        execute=args.execute,
        git_runner=git_runner,
        dependencies=dependencies,
        guard_factory=guard_factory,
        control_reader=control_reader,
        package_reader=package_reader,
    )
    (stdout or sys.stdout).write(canonical_json_bytes(result).decode("utf-8"))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
