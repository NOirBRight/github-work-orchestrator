"""Closed value types for the V8 release subject manifest."""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from types import TracebackType
from types import ModuleType
from typing import Callable, Mapping, Sequence


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
REPOSITORY_ROOT = Path(r"D:\Workstation\github-work-orchestrator").resolve()
EVIDENCE_ROOT = Path(
    r"D:\gwo-release-evidence\2026-08-09-gwo-v8-beta3-production-cutover"
).resolve()

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
    _generation_lease: "_GenerationLease | None" = dataclass_field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

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

    def _take_generation_lease(self) -> "_GenerationLease | None":
        lease = self._generation_lease
        object.__setattr__(self, "_generation_lease", None)
        return lease

    def close(self) -> None:
        """Close an in-flight generation boundary, if this subject owns one."""

        lease = self._take_generation_lease()
        if lease is not None:
            lease.close()

    def __del__(self) -> None:
        # A caller normally transfers the generation lease to the writer.  The
        # finalizer is only a safety net for test/error paths that discard a
        # generated value without writing it.
        lease = getattr(self, "_generation_lease", None)
        if lease is not None:
            lease.close()

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


def _path_invalid(detail: str) -> None:
    raise ReleaseSubjectError("RELEASE_SUBJECT_PATH_INVALID", detail)


def _drift(detail: str) -> None:
    raise ReleaseSubjectError("RELEASE_SUBJECT_DRIFT", detail)


def _is_reparse(stat_result: os.stat_result) -> bool:
    return bool(getattr(stat_result, "st_file_attributes", 0) & 0x0400)


def _directory_components(path: Path) -> tuple[Path, ...]:
    absolute = Path(os.path.abspath(path))
    parts = absolute.parts
    return tuple(Path(*parts[:index]) for index in range(1, len(parts) + 1))


def _close_descriptors(descriptors: list[int]) -> None:
    while descriptors:
        try:
            os.close(descriptors.pop())
        except OSError:
            pass


def _reject_reparse_component(path: Path) -> None:
    try:
        observed = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        return
    if stat.S_ISLNK(observed.st_mode) or _is_reparse(observed):
        _path_invalid(f"path is a link or reparse point: {path}")


def _windows_handle_identity(
    descriptor: int,
    code: str,
    *,
    directory: bool,
) -> dict[str, int | str]:
    if os.name != "nt":
        try:
            observed = os.fstat(descriptor)
        except OSError as error:
            raise ReleaseSubjectError(
                code, "held handle identity is unavailable"
            ) from error
        return {
            "st_dev": int(observed.st_dev),
            "st_ino": int(observed.st_ino),
            "st_mode": int(observed.st_mode),
            "st_size": int(observed.st_size),
            "st_mtime_ns": int(observed.st_mtime_ns),
        }
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
            handle,
            18,
            ctypes.byref(file_id),
            ctypes.sizeof(file_id),
        ):
            raise OSError(ctypes.get_last_error(), "FILE_ID_INFO unavailable")
        if not kernel32.GetFileInformationByHandleEx(
            handle,
            1,
            ctypes.byref(standard),
            ctypes.sizeof(standard),
        ):
            raise OSError(ctypes.get_last_error(), "FILE_STANDARD_INFO unavailable")
        observed = os.fstat(descriptor)
        if _is_reparse(observed):
            _path_invalid("held handle is a reparse point")
        if stat.S_ISDIR(observed.st_mode) != directory:
            raise ReleaseSubjectError(code, "held handle type is not expected")
        if not directory and int(standard.number_of_links) != 1:
            raise ReleaseSubjectError(
                code, "held file has an unexpected hard-link count"
            )
        return {
            "volume_id": int(file_id.volume_serial_number),
            "file_id": bytes(file_id.file_id).hex(),
            "st_mode": int(observed.st_mode),
            "st_size": int(standard.end_of_file),
            "st_mtime_ns": int(observed.st_mtime_ns),
        }
    except ReleaseSubjectError:
        raise
    except FileNotFoundError:
        raise
    except (ImportError, OSError, AttributeError, TypeError) as error:
        raise ReleaseSubjectError(
            code, "Windows handle identity is unavailable"
        ) from error


def _identity_matches(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    if "file_id" in left or "file_id" in right:
        return left.get("volume_id") == right.get("volume_id") and left.get(
            "file_id"
        ) == right.get("file_id")
    return left.get("st_dev") == right.get("st_dev") and left.get(
        "st_ino"
    ) == right.get("st_ino")


def _open_windows_relative_handle(
    path: Path,
    code: str,
    *,
    directory: bool,
    parent: int,
    create_new: bool = False,
    writable: bool = False,
    delete: bool = False,
    share_delete: bool = False,
    missing_ok: bool = False,
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
            _path_invalid(f"relative component is invalid: {path}")
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
            0x00001040,
            None,
            None,
        )
        io_status = IoStatusBlock()
        handle = ctypes.c_void_p()
        desired_access = 0x80000000 | 0x00100000
        if writable:
            desired_access |= 0x40000000
        if delete:
            desired_access |= 0x00010000
        if directory:
            desired_access = 0x00000001 | 0x00000020 | 0x00000080 | 0x00100000
            if writable:
                desired_access |= 0x00000002
        share_access = 0x00000003
        if delete or share_delete:
            share_access |= 0x00000004
        create_disposition = 2 if create_new else 1
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
        status = (
            int(
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
            )
            & 0xFFFFFFFF
        )
        if status & 0x80000000:
            if create_new and status == 0xC0000035:
                raise ReleaseSubjectError(
                    "RELEASE_SUBJECT_EXISTS",
                    f"subject appeared during exclusive create: {path}",
                )
            if missing_ok and status in {0xC0000034, 0xC000003A}:
                raise FileNotFoundError(str(path))
            raise OSError(status, "NtCreateFile failed")
        try:
            return msvcrt.open_osfhandle(
                int(handle.value),
                (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_BINARY", 0),
            )
        except OSError:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
            raise
    except ReleaseSubjectError:
        raise
    except FileNotFoundError:
        raise
    except (ImportError, OSError, AttributeError, TypeError) as error:
        raise ReleaseSubjectError(
            code, f"relative Windows handle could not be opened: {path}"
        ) from error


def _open_path_handle(
    path: Path,
    code: str,
    *,
    directory: bool,
    parent: int | None = None,
    create_new: bool = False,
    writable: bool = False,
    delete: bool = False,
    share_delete: bool = False,
    missing_ok: bool = False,
) -> int:
    if os.name != "nt":
        flags = (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_BINARY", 0)
        if create_new:
            flags |= os.O_CREAT | os.O_EXCL
        if directory:
            flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        else:
            flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            if parent is None:
                return os.open(path, flags, 0o600 if create_new else 0o644)
            return os.open(
                Path(path).name,
                flags,
                0o600 if create_new else 0o644,
                dir_fd=parent,
            )
        except FileExistsError as error:
            if create_new:
                raise ReleaseSubjectError(
                    "RELEASE_SUBJECT_EXISTS",
                    f"subject appeared during exclusive create: {path}",
                ) from error
            raise ReleaseSubjectError(
                code, f"path already exists unexpectedly: {path}"
            ) from error
        except FileNotFoundError:
            if missing_ok:
                raise
            raise ReleaseSubjectError(
                code, f"path could not be opened without reparse following: {path}"
            )
        except OSError as error:
            if parent is None:
                _reject_reparse_component(path)
            raise ReleaseSubjectError(
                code, f"path could not be opened without reparse following: {path}"
            ) from error
    if parent is not None:
        return _open_windows_relative_handle(
            path,
            code,
            directory=directory,
            parent=parent,
            create_new=create_new,
            writable=writable,
            delete=delete,
            share_delete=share_delete,
            missing_ok=missing_ok,
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
            0x80000000 | (0x40000000 if writable else 0)
            if not directory
            else 0x00000001
            | 0x00000020
            | 0x00000080
            | 0x00100000
            | (0x00000002 if writable else 0)
        )
        if delete:
            access |= 0x00010000
        share = 0x00000003 | (0x00000004 if delete or share_delete else 0)
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
                raise ReleaseSubjectError(
                    "RELEASE_SUBJECT_EXISTS",
                    f"subject appeared during exclusive create: {path}",
                )
            raise OSError(error_code, "CreateFileW failed")
        try:
            return msvcrt.open_osfhandle(
                handle,
                (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_BINARY", 0),
            )
        except OSError:
            kernel32.CloseHandle(handle)
            raise
    except ReleaseSubjectError:
        raise
    except (ImportError, OSError, AttributeError, TypeError) as error:
        raise ReleaseSubjectError(
            code, f"path could not be opened by Windows handle: {path}"
        ) from error


def _open_directory_components(
    path: Path,
    code: str,
    *,
    allow_file_create: bool = False,
) -> tuple[list[int], list[dict[str, int | str]]]:
    descriptors: list[int] = []
    identities: list[dict[str, int | str]] = []
    try:
        components = _directory_components(path)
        for index, component in enumerate(components):
            _reject_reparse_component(component)
            parent = descriptors[-1] if descriptors else None
            open_path = component if parent is None else Path(component.name)
            descriptor = _open_path_handle(
                open_path,
                code,
                directory=True,
                parent=parent,
                writable=allow_file_create and index == len(components) - 1,
            )
            try:
                identity = _windows_handle_identity(descriptor, code, directory=True)
            except Exception:
                os.close(descriptor)
                raise
            descriptors.append(descriptor)
            identities.append(identity)
        if not descriptors:
            raise ReleaseSubjectError(code, f"directory path is empty: {path}")
        return descriptors, identities
    except ReleaseSubjectError:
        _close_descriptors(descriptors)
        raise
    except OSError as error:
        _close_descriptors(descriptors)
        raise ReleaseSubjectError(
            code, f"directory component could not be held: {path}"
        ) from error


@dataclass
class _DirectoryLease:
    """Own one identity-checked, descriptor-relative directory chain."""

    path: Path
    handles: tuple[int, ...]
    identities: tuple[Mapping[str, object], ...]
    _closed: bool = dataclass_field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.handles) is not tuple or not self.handles:
            raise TypeError("directory lease handles must be a non-empty tuple")
        if any(type(handle) is not int for handle in self.handles):
            raise TypeError("directory lease handles must be integer descriptors")
        if type(self.identities) is not tuple or len(self.identities) != len(
            self.handles
        ):
            raise TypeError("directory lease identities must match handles")
        if any(not isinstance(identity, Mapping) for identity in self.identities):
            raise TypeError("directory lease identities must be mappings")

    def assert_stable(self, code: str = "RELEASE_SUBJECT_DRIFT") -> None:
        if self._closed:
            raise ReleaseSubjectError(code, "directory lease is already closed")
        for handle, identity in zip(self.handles, self.identities, strict=True):
            current = _windows_handle_identity(handle, code, directory=True)
            if not _identity_matches(current, identity):
                _drift("held directory component identity changed")
        fresh_handles: list[int] = []
        try:
            fresh_handles, fresh_identities = _open_directory_components(
                self.path,
                code,
            )
            if len(fresh_identities) != len(self.identities) or any(
                not _identity_matches(current, expected)
                for current, expected in zip(
                    fresh_identities,
                    self.identities,
                    strict=True,
                )
            ):
                _drift("directory lease path identity changed")
        finally:
            _close_descriptors(fresh_handles)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for handle in reversed(self.handles):
            try:
                os.close(handle)
            except OSError:
                pass


@dataclass
class _GenerationLease:
    """Keep both release roots held until the subject file is durable."""

    repository: _DirectoryLease
    evidence: _DirectoryLease
    _closed: bool = dataclass_field(default=False, init=False, repr=False)

    def assert_stable(self) -> None:
        if self._closed:
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_DRIFT", "generation lease is already closed"
            )
        self.repository.assert_stable()
        self.evidence.assert_stable()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.evidence.close()
        self.repository.close()


def _directory_lease(path: Path, code: str, *, allow_file_create: bool = False) -> _DirectoryLease:
    descriptors, identities = _open_directory_components(
        path,
        code,
        allow_file_create=allow_file_create,
    )
    try:
        return _DirectoryLease(
            Path(os.path.abspath(path)),
            tuple(descriptors),
            tuple(dict(identity) for identity in identities),
        )
    except Exception:
        _close_descriptors(descriptors)
        raise


def _open_relative_regular_file(
    parent: int,
    relative: str,
    code: str,
) -> tuple[bytes, dict[str, int | str]]:
    """Read one canonical repository file beneath an already-held root."""

    components = [Path(part) for part in relative.split("/") if part]
    if not components:
        raise ReleaseSubjectError(code, "relative observer path is empty")
    directories: list[int] = []
    descriptor: int | None = None
    current_parent = parent
    try:
        for component in components[:-1]:
            descriptor = _open_path_handle(
                component,
                code,
                directory=True,
                parent=current_parent,
            )
            directories.append(descriptor)
            current_parent = descriptor
            descriptor = None
        descriptor = _open_path_handle(
            components[-1],
            code,
            directory=False,
            parent=current_parent,
        )
        identity = _windows_handle_identity(descriptor, code, directory=False)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ReleaseSubjectError(code, f"path is not a regular file: {relative}")
        raw = _read_held_bytes(descriptor, code)
        after = _windows_handle_identity(descriptor, code, directory=False)
        if not _identity_matches(after, identity) or after.get("st_size") != len(raw):
            _drift(f"held observer file changed during read: {relative}")
        return raw, identity
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        _close_descriptors(directories)


def _open_bound_handle(
    path: Path,
    code: str,
    *,
    create_new: bool = False,
    writable: bool = False,
    share_delete: bool = False,
) -> tuple[int, dict[str, int | str], _DirectoryLease]:
    components: list[int] = []
    descriptor: int | None = None
    lease: _DirectoryLease | None = None
    try:
        components, identities = _open_directory_components(
            Path(path).parent,
            code,
            allow_file_create=create_new,
        )
        lease = _DirectoryLease(
            Path(os.path.abspath(path)).parent,
            tuple(components),
            tuple(dict(identity) for identity in identities),
        )
        components = []
        descriptor = _open_path_handle(
            Path(path).name,
            code,
            directory=False,
            parent=lease.handles[-1],
            create_new=create_new,
            writable=writable,
            share_delete=share_delete,
        )
        identity = _windows_handle_identity(descriptor, code, directory=False)
        observed_mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(observed_mode):
            raise ReleaseSubjectError(code, f"path is not a regular file: {path}")
        return descriptor, identity, lease
    except ReleaseSubjectError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if lease is not None:
            lease.close()
        raise
    except OSError as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if lease is not None:
            lease.close()
        raise ReleaseSubjectError(
            code, f"path could not be opened read-only: {path}"
        ) from error
    finally:
        _close_descriptors(components)


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
        raise ReleaseSubjectError(code, "held input readback failed") from error


def _read_held_regular_file(
    path: Path,
    code: str,
    *,
    file_reader: Callable[[Path], tuple[bytes, Mapping[str, object]]] | None = None,
    share_delete: bool = False,
) -> tuple[bytes, dict[str, int | str], int, _DirectoryLease]:
    descriptor, identity, lease = _open_bound_handle(
        path, code, share_delete=share_delete
    )
    try:
        raw = _read_held_bytes(descriptor, code)
        after_identity = _windows_handle_identity(descriptor, code, directory=False)
        if not _identity_matches(after_identity, identity) or after_identity.get(
            "st_size"
        ) != len(raw):
            _drift(f"path changed during held read: {path}")
        if file_reader is not None:
            observed_raw, observed_identity = file_reader(Path(path))
            if type(observed_raw) is not bytes or not isinstance(
                observed_identity, Mapping
            ):
                _drift(f"test file reader returned an invalid observation: {path}")
            if observed_raw != raw or not _identity_matches(
                observed_identity, identity
            ):
                _drift(f"test file reader disagreed with the held observation: {path}")
        return raw, dict(identity), descriptor, lease
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        lease.close()
        raise


def _read_regular_file_once(
    path: Path,
    code: str,
    *,
    parent_lease: _DirectoryLease | None = None,
) -> tuple[bytes, dict[str, int | str]]:
    if parent_lease is not None:
        parent_lease.assert_stable(code)
        if Path(os.path.abspath(path)).parent != parent_lease.path:
            _path_invalid(f"fresh observation escaped its held parent: {path}")
        descriptor = _open_path_handle(
            Path(path).name,
            code,
            directory=False,
            parent=parent_lease.handles[-1],
        )
        try:
            identity = _windows_handle_identity(descriptor, code, directory=False)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                _path_invalid(f"path is not a regular file: {path}")
            raw = _read_held_bytes(descriptor, code)
            after_identity = _windows_handle_identity(
                descriptor, code, directory=False
            )
            if not _identity_matches(after_identity, identity) or after_identity.get(
                "st_size"
            ) != len(raw):
                _drift(f"path changed during held fresh read: {path}")
            parent_lease.assert_stable(code)
            return raw, identity
        finally:
            os.close(descriptor)
    raw, identity, descriptor, lease = _read_held_regular_file(path, code)
    try:
        return raw, identity
    finally:
        os.close(descriptor)
        lease.close()


@dataclass(frozen=True)
class ReleaseSubjectBinding:
    subject: ReleaseSubject
    manifest_path: Path
    raw_bytes: bytes
    identity: Mapping[str, object]
    handle: int
    parent_lease: _DirectoryLease | None = None
    _closed: bool = dataclass_field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.subject) is not ReleaseSubject:
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_SCHEMA_INVALID", "binding subject is not typed"
            )
        if not isinstance(self.manifest_path, Path):
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_PATH_INVALID", "binding path is not a Path"
            )
        if type(self.raw_bytes) is not bytes:
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_SCHEMA_INVALID", "binding bytes are not exact bytes"
            )
        if not isinstance(self.identity, Mapping):
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_DRIFT", "binding identity is not a mapping"
            )
        if type(self.handle) is not int:
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_DRIFT", "binding handle is not an integer"
            )
        if self.parent_lease is not None and type(self.parent_lease) is not _DirectoryLease:
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_DRIFT", "binding parent lease is not typed"
            )

    def assert_stable(self) -> None:
        try:
            if self.parent_lease is None:
                _drift("binding has no retained parent directory lease")
            self.parent_lease.assert_stable()
            current_identity = _windows_handle_identity(
                self.handle,
                "RELEASE_SUBJECT_DRIFT",
                directory=False,
            )
            if not _identity_matches(current_identity, self.identity):
                _drift(f"held manifest identity changed: {self.manifest_path}")
            held_raw = _read_held_bytes(self.handle, "RELEASE_SUBJECT_DRIFT")
            if held_raw != self.raw_bytes:
                _drift(f"held manifest bytes changed: {self.manifest_path}")
            fresh_raw, fresh_identity = _read_regular_file_once(
                self.manifest_path,
                "RELEASE_SUBJECT_DRIFT",
                parent_lease=self.parent_lease,
            )
        except FileNotFoundError as error:
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_DRIFT",
                f"held manifest is unavailable: {self.manifest_path}",
            ) from error
        except ReleaseSubjectError:
            raise
        except OSError as error:
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_DRIFT",
                f"held manifest is unavailable: {self.manifest_path}",
            ) from error
        if (
            not _identity_matches(fresh_identity, self.identity)
            or fresh_raw != self.raw_bytes
        ):
            _drift(f"manifest changed after binding: {self.manifest_path}")

    def close(self) -> None:
        if self._closed:
            return
        object.__setattr__(self, "_closed", True)
        try:
            os.close(self.handle)
        except OSError:
            pass
        if self.parent_lease is not None:
            self.parent_lease.close()

    def __enter__(self) -> "ReleaseSubjectBinding":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _validate_manifest_path(path: Path, expected_evidence_root: Path) -> None:
    expected = Path(expected_evidence_root).expanduser().resolve(strict=False)
    if (
        Path(path).name != RELEASE_SUBJECT_FILENAME
        or Path(path).parent.resolve(strict=False) != expected
    ):
        _path_invalid(f"manifest is not the fixed evidence-root subject path: {path}")


def _require_subject_absent(path: Path) -> None:
    manifest_path = Path(path)
    parent_descriptors: list[int] = []
    try:
        parent_descriptors, _ = _open_directory_components(
            manifest_path.parent,
            "RELEASE_SUBJECT_PATH_INVALID",
        )
        try:
            observed = os.lstat(manifest_path)
        except FileNotFoundError:
            return
        except OSError as error:
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_PATH_INVALID",
                f"subject path could not be observed without following links: {manifest_path}",
            ) from error
        if stat.S_ISLNK(observed.st_mode) or _is_reparse(observed):
            _path_invalid(f"subject path is a link or reparse point: {manifest_path}")
        if not stat.S_ISREG(observed.st_mode):
            _path_invalid(f"subject path is not a regular file: {manifest_path}")
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_EXISTS",
            f"subject already exists: {manifest_path}",
        )
    finally:
        _close_descriptors(parent_descriptors)


def _require_subject_absent_held(
    path: Path,
    parent_lease: _DirectoryLease,
) -> None:
    """Check the fixed subject name beneath an already-held evidence root."""

    parent_lease.assert_stable()
    name = Path(path).name
    parent = parent_lease.handles[-1]
    if os.name != "nt":
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(name, flags, dir_fd=parent)
        except FileNotFoundError:
            parent_lease.assert_stable()
            return
        except OSError as error:
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_PATH_INVALID",
                f"subject path could not be observed beneath held evidence root: {path}",
            ) from error
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                _path_invalid(f"subject path is not a regular file: {path}")
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_EXISTS",
                f"subject already exists: {path}",
            )
        finally:
            os.close(descriptor)
    try:
        descriptor = _open_path_handle(
            Path(name),
            "RELEASE_SUBJECT_PATH_INVALID",
            directory=False,
            parent=parent,
            missing_ok=True,
        )
    except FileNotFoundError:
        parent_lease.assert_stable()
        return
    try:
        _windows_handle_identity(
            descriptor,
            "RELEASE_SUBJECT_PATH_INVALID",
            directory=False,
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            _path_invalid(f"subject path is not a regular file: {path}")
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_EXISTS",
            f"subject already exists: {path}",
        )
    finally:
        os.close(descriptor)


def _observer_paths(repository_root: Path) -> tuple[Path, tuple[Path, ...], Path]:
    scripts_root = Path(repository_root).expanduser().resolve(strict=False) / "scripts"
    runner = scripts_root / "run_beta3_live_guard.py"
    attestors = tuple(scripts_root / name for name in ATTESTOR_FILENAMES)
    reviewed = scripts_root / "beta3_reviewed_provenance.json"
    return runner, attestors, reviewed


def _observer_invalid(detail: str) -> None:
    raise ReleaseSubjectError("RELEASE_SUBJECT_PROVENANCE_MISMATCH", detail)


def _validate_reviewed_provenance_bytes(
    raw: bytes,
    runner: ReleaseFileIdentity,
    attestors: tuple[ReleaseFileIdentity, ...],
    bundle_sha256: str,
) -> None:
    try:
        value = _decode_exact_canonical_object(raw)
    except ReleaseSubjectError as error:
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_PROVENANCE_MISMATCH", error.detail
        ) from error
    expected = {
        "schema": "gwo-beta3-reviewed-provenance.v1",
        "runner": runner.canonical(),
        "attestors": [attestor.canonical() for attestor in attestors],
        "attestor_bundle_sha256": bundle_sha256,
    }
    if value != expected:
        _observer_invalid("reviewed provenance does not match held observer identities")


def _attestor_bundle_sha256(
    raw_attestors: tuple[tuple[str, bytes], ...],
) -> str:
    digest = hashlib.sha256()
    for name, content in raw_attestors:
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _observer_snapshot(
    repository_root: Path,
    *,
    repository_lease: _DirectoryLease | None = None,
) -> tuple[
    ReleaseFileIdentity,
    tuple[ReleaseFileIdentity, ...],
    str,
    ReviewedProvenanceIdentity,
]:
    runner_path, attestor_paths, reviewed_path = _observer_paths(repository_root)
    if repository_lease is None:
        runner_raw, _runner_identity = _read_regular_file_once(
            runner_path,
            "RELEASE_SUBJECT_OBSERVER_UNAVAILABLE",
        )
        attestor_raw: list[tuple[str, bytes]] = []
        for name, path in zip(ATTESTOR_FILENAMES, attestor_paths, strict=True):
            raw, _identity = _read_regular_file_once(
                path,
                "RELEASE_SUBJECT_OBSERVER_UNAVAILABLE",
            )
            attestor_raw.append((name, raw))
        reviewed_raw, _reviewed_identity = _read_regular_file_once(
            reviewed_path,
            "RELEASE_SUBJECT_OBSERVER_UNAVAILABLE",
        )
    else:
        repository_lease.assert_stable()
        scripts_descriptor = _open_path_handle(
            Path("scripts"),
            "RELEASE_SUBJECT_OBSERVER_UNAVAILABLE",
            directory=True,
            parent=repository_lease.handles[-1],
        )
        try:
            scripts_identity = _windows_handle_identity(
                scripts_descriptor,
                "RELEASE_SUBJECT_OBSERVER_UNAVAILABLE",
                directory=True,
            )
            runner_raw, _runner_identity = _open_relative_regular_file(
                scripts_descriptor,
                "run_beta3_live_guard.py",
                "RELEASE_SUBJECT_OBSERVER_UNAVAILABLE",
            )
            attestor_raw = []
            for name in ATTESTOR_FILENAMES:
                raw, _identity = _open_relative_regular_file(
                    scripts_descriptor,
                    name,
                    "RELEASE_SUBJECT_OBSERVER_UNAVAILABLE",
                )
                attestor_raw.append((name, raw))
            reviewed_raw, _reviewed_identity = _open_relative_regular_file(
                scripts_descriptor,
                "beta3_reviewed_provenance.json",
                "RELEASE_SUBJECT_OBSERVER_UNAVAILABLE",
            )
            after_scripts_identity = _windows_handle_identity(
                scripts_descriptor,
                "RELEASE_SUBJECT_OBSERVER_UNAVAILABLE",
                directory=True,
            )
            if not _identity_matches(after_scripts_identity, scripts_identity):
                _drift("observer scripts directory changed during read")
        finally:
            os.close(scripts_descriptor)
        repository_lease.assert_stable()
    runner = ReleaseFileIdentity(
        module="run_beta3_live_guard",
        path=_canonical_path(runner_path),
        sha256=hashlib.sha256(runner_raw).hexdigest(),
    )
    attestors = tuple(
        ReleaseFileIdentity(
            module=name.removesuffix(".py"),
            path=_canonical_path(path),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        for (name, raw), path in zip(attestor_raw, attestor_paths, strict=True)
    )
    bundle_sha256 = _attestor_bundle_sha256(tuple(attestor_raw))
    _validate_reviewed_provenance_bytes(
        reviewed_raw,
        runner,
        attestors,
        bundle_sha256,
    )
    reviewed = ReviewedProvenanceIdentity(
        path=_canonical_path(reviewed_path),
        sha256=hashlib.sha256(reviewed_raw).hexdigest(),
    )
    return runner, attestors, bundle_sha256, reviewed


def _validate_manifest_and_observer_bytes(
    raw: bytes,
    identity: Mapping[str, object],
    manifest_path: Path,
    expected_repository_root: Path,
    expected_evidence_root: Path,
    *,
    repository_lease: _DirectoryLease,
) -> ReleaseSubject:
    if type(raw) is not bytes or not isinstance(identity, Mapping):
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_SCHEMA_INVALID",
            "manifest observation is not exact bytes and identity",
        )
    if identity.get("st_size") != len(raw):
        _drift(f"manifest size does not match held identity: {manifest_path}")
    subject = parse_release_subject(
        raw,
        expected_repository_root=expected_repository_root,
        expected_evidence_root=expected_evidence_root,
    )
    runner, attestors, bundle_sha256, reviewed = _observer_snapshot(
        expected_repository_root,
        repository_lease=repository_lease,
    )
    if subject.runner != runner:
        _observer_invalid("runner identity does not match held bytes")
    if subject.attestors != attestors:
        _observer_invalid("attestor identities do not match held bytes or order")
    if subject.attestor_bundle_sha256 != bundle_sha256:
        _observer_invalid("attestor bundle does not match held bytes")
    if subject.reviewed_provenance != reviewed:
        _observer_invalid("reviewed provenance identity does not match held bytes")
    return subject


def production_subject_path() -> Path:
    return EVIDENCE_ROOT / RELEASE_SUBJECT_FILENAME


def load_release_subject_for_test(
    path: Path,
    expected_repository_root: Path,
    expected_evidence_root: Path,
    file_reader: Callable[[Path], tuple[bytes, Mapping[str, object]]] | None = None,
) -> ReleaseSubjectBinding:
    manifest_path = Path(path)
    _validate_manifest_path(manifest_path, expected_evidence_root)
    raw, identity, handle, parent_lease = _read_held_regular_file(
        manifest_path,
        "RELEASE_SUBJECT_UNAVAILABLE",
        file_reader=file_reader,
        share_delete=True,
    )
    try:
        repository_lease = _directory_lease(
            Path(expected_repository_root),
            "RELEASE_SUBJECT_REPOSITORY_INVALID",
        )
    except Exception:
        try:
            os.close(handle)
        except OSError:
            pass
        parent_lease.close()
        raise
    try:
        subject = _validate_manifest_and_observer_bytes(
            raw,
            identity,
            manifest_path,
            expected_repository_root,
            expected_evidence_root,
            repository_lease=repository_lease,
        )
    except Exception:
        try:
            os.close(handle)
        except OSError:
            pass
        parent_lease.close()
        repository_lease.close()
        raise
    repository_lease.close()
    try:
        return ReleaseSubjectBinding(
            subject, manifest_path, raw, identity, handle, parent_lease
        )
    except Exception:
        try:
            os.close(handle)
        except OSError:
            pass
        parent_lease.close()
        raise


def load_production_release_subject() -> ReleaseSubjectBinding:
    path = production_subject_path()
    _validate_manifest_path(path, EVIDENCE_ROOT)
    raw, identity, handle, parent_lease = _read_held_regular_file(
        path,
        "RELEASE_SUBJECT_UNAVAILABLE",
        share_delete=True,
    )
    try:
        repository_lease = _directory_lease(
            REPOSITORY_ROOT,
            "RELEASE_SUBJECT_REPOSITORY_INVALID",
        )
    except Exception:
        try:
            os.close(handle)
        except OSError:
            pass
        parent_lease.close()
        raise
    try:
        subject = _validate_manifest_and_observer_bytes(
            raw,
            identity,
            path,
            REPOSITORY_ROOT,
            EVIDENCE_ROOT,
            repository_lease=repository_lease,
        )
    except Exception:
        try:
            os.close(handle)
        except OSError:
            pass
        parent_lease.close()
        repository_lease.close()
        raise
    repository_lease.close()
    try:
        return ReleaseSubjectBinding(subject, path, raw, identity, handle, parent_lease)
    except Exception:
        try:
            os.close(handle)
        except OSError:
            pass
        parent_lease.close()
        raise


def _write_all(descriptor: int, raw: bytes) -> None:
    offset = 0
    while offset < len(raw):
        try:
            written = os.write(descriptor, raw[offset:])
        except OSError as error:
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_WRITE_FAILED",
                "subject write failed",
            ) from error
        if written <= 0:
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_WRITE_FAILED", "subject write made no progress"
            )
        offset += written
    try:
        os.fsync(descriptor)
    except OSError as error:
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_WRITE_FAILED", "subject fsync failed"
        ) from error


def _remove_created_subject(
    path: Path,
    parent: int,
    created_identity: Mapping[str, object] | None,
    created_raw: bytes,
    *,
    created_descriptor: int | None = None,
) -> None:
    """Remove only a failed-create leaf whose path still matches its identity."""

    current: int | None = None
    try:
        if created_identity is None:
            if created_descriptor is None:
                return
            if os.name == "nt":
                import ctypes
                import msvcrt

                class FileDispositionInfo(ctypes.Structure):
                    _fields_ = [("delete_file", ctypes.c_int)]

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                disposition = FileDispositionInfo(1)
                if not kernel32.SetFileInformationByHandle(
                    msvcrt.get_osfhandle(created_descriptor),
                    4,
                    ctypes.byref(disposition),
                    ctypes.sizeof(disposition),
                ):
                    raise OSError(
                        ctypes.get_last_error(),
                        "SetFileInformationByHandle failed",
                    )
                return

            created_stat = os.fstat(created_descriptor)
            current = _open_path_handle(
                Path(path.name),
                "RELEASE_SUBJECT_WRITE_FAILED",
                directory=False,
                parent=parent,
            )
            current_stat = os.fstat(current)
            if (
                not stat.S_ISREG(current_stat.st_mode)
                or current_stat.st_dev != created_stat.st_dev
                or current_stat.st_ino != created_stat.st_ino
                or _read_held_bytes(current, "RELEASE_SUBJECT_WRITE_FAILED")
                != created_raw
            ):
                return
            os.unlink(Path(path.name).name, dir_fd=parent)
            return

        current = _open_path_handle(
            Path(path.name),
            "RELEASE_SUBJECT_WRITE_FAILED",
            directory=False,
            parent=parent,
            delete=os.name == "nt",
        )
        current_identity = _windows_handle_identity(
            current, "RELEASE_SUBJECT_WRITE_FAILED", directory=False
        )
        if os.name != "nt" and not stat.S_ISREG(os.fstat(current).st_mode):
            return
        if (
            not _identity_matches(current_identity, created_identity)
            or _read_held_bytes(current, "RELEASE_SUBJECT_WRITE_FAILED") != created_raw
        ):
            return
        if os.name == "nt":
            import ctypes
            import msvcrt

            class FileDispositionInfo(ctypes.Structure):
                _fields_ = [("delete_file", ctypes.c_int)]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            disposition = FileDispositionInfo(1)
            if not kernel32.SetFileInformationByHandle(
                msvcrt.get_osfhandle(current),
                4,
                ctypes.byref(disposition),
                ctypes.sizeof(disposition),
            ):
                raise OSError(ctypes.get_last_error(), "SetFileInformationByHandle failed")
            return

        os.unlink(Path(path.name).name, dir_fd=parent)
    except (FileNotFoundError, OSError, ReleaseSubjectError):
        return
    finally:
        if current is not None:
            try:
                os.close(current)
            except OSError:
                pass


def _write_subject_exclusive(
    subject: ReleaseSubject,
    path: Path,
    *,
    runtime_loader: Callable[[], ReleaseSubjectBinding] | None = None,
) -> ReleaseSubjectBinding:
    if type(subject) is not ReleaseSubject:
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_SCHEMA_INVALID", "subject is not a typed ReleaseSubject"
        )
    manifest_path = Path(path)
    expected_path = Path(subject.evidence_root) / RELEASE_SUBJECT_FILENAME
    if _canonical_path(manifest_path) != _canonical_path(expected_path):
        _path_invalid(
            f"subject path is not bound to its evidence root: {manifest_path}"
        )
    _validate_manifest_path(manifest_path, Path(subject.evidence_root))
    raw = canonical_json_bytes(subject.canonical())
    generation_lease = subject._take_generation_lease()
    parent_descriptors: list[int] = []
    descriptor: int | None = None
    created_identity: dict[str, int | str] | None = None
    parent: int | None = None
    completed = False
    try:
        if generation_lease is not None:
            generation_lease.assert_stable()
            evidence_parent = generation_lease.evidence
        else:
            evidence_parent = None
            parent_descriptors, _parent_identities = _open_directory_components(
                manifest_path.parent,
                "RELEASE_SUBJECT_PATH_INVALID",
                allow_file_create=True,
            )
        parent = (
            evidence_parent.handles[-1]
            if evidence_parent is not None
            else parent_descriptors[-1]
        )
        descriptor = _open_path_handle(
            Path(manifest_path.name),
            "RELEASE_SUBJECT_PATH_INVALID",
            directory=False,
            parent=parent,
            create_new=True,
            writable=True,
            delete=os.name == "nt",
            share_delete=True,
        )
        try:
            created_identity = _windows_handle_identity(
                descriptor, "RELEASE_SUBJECT_WRITE_FAILED", directory=False
            )
        except Exception:
            raise
        _write_all(descriptor, raw)
        observed_raw = _read_held_bytes(descriptor, "RELEASE_SUBJECT_WRITE_FAILED")
        if observed_raw != raw:
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_WRITE_FAILED",
                "subject write readback did not match canonical bytes",
            )
        if generation_lease is not None:
            generation_lease.assert_stable()
        # Keep the newly-created handle open while the runtime loader acquires
        # its own held read boundary.  Windows sharing excludes delete/rename.
        if runtime_loader is None:
            binding = load_release_subject_for_test(
                manifest_path,
                Path(subject.repository_root),
                Path(subject.evidence_root),
            )
        else:
            binding = runtime_loader()
        completed = True
        return binding
    finally:
        if descriptor is not None:
            if not completed and parent is not None:
                try:
                    created_raw = _read_held_bytes(
                        descriptor, "RELEASE_SUBJECT_WRITE_FAILED"
                    )
                except (OSError, ReleaseSubjectError):
                    created_raw = b""
                if created_identity is None:
                    _remove_created_subject(
                        manifest_path,
                        parent,
                        None,
                        created_raw,
                        created_descriptor=descriptor,
                    )
                else:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                    descriptor = None
                    _remove_created_subject(
                        manifest_path, parent, created_identity, created_raw
                    )
            try:
                if descriptor is not None:
                    os.close(descriptor)
            except OSError:
                pass
        _close_descriptors(parent_descriptors)
        if generation_lease is not None:
            generation_lease.close()


def write_subject_for_test_exclusive(
    subject: ReleaseSubject, path: Path
) -> ReleaseSubjectBinding:
    return _write_subject_exclusive(subject, Path(path))


def write_production_subject_exclusive(
    subject: ReleaseSubject,
) -> ReleaseSubjectBinding:
    if type(subject) is not ReleaseSubject:
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_SCHEMA_INVALID",
            "subject is not a typed ReleaseSubject",
        )
    if subject.repository_root != _canonical_path(REPOSITORY_ROOT):
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_SCHEMA_INVALID",
            "production subject repository root is not fixed",
        )
    if subject.evidence_root != _canonical_path(EVIDENCE_ROOT):
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_SCHEMA_INVALID",
            "production subject evidence root is not fixed",
        )
    return _write_subject_exclusive(
        subject,
        production_subject_path(),
        runtime_loader=load_production_release_subject,
    )


def source_tree_digest(
    repository_root: Path,
    *,
    root_handle: int | None = None,
) -> str:
    """Return the existing V8 audited source-tree digest for a fixed root."""

    root = Path(repository_root).expanduser().resolve(strict=False)
    scripts_root = root / "skills" / "orchestrator" / "scripts"
    package_root = scripts_root / "gwo_v8"
    if not package_root.is_dir():
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_SOURCE_UNAVAILABLE",
            "V8 source-tree digest implementation is unavailable",
        )
    alias = "_gwo_v8_release_subject_" + hashlib.sha256(
        str(scripts_root).encode("utf-8")
    ).hexdigest()[:16]
    package_name = alias
    module_name = f"{alias}.cutover_guard"
    package = ModuleType(package_name)
    package.__file__ = str(package_root / "__init__.py")
    package.__package__ = package_name
    package.__path__ = [str(package_root)]  # type: ignore[attr-defined]
    package.__spec__ = importlib.util.spec_from_loader(
        package_name, loader=None, is_package=True
    )
    spec = importlib.util.spec_from_file_location(
        module_name,
        package_root / "cutover_guard.py",
        submodule_search_locations=None,
    )
    if spec is None or spec.loader is None:
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_SOURCE_UNAVAILABLE",
            "V8 source-tree digest implementation is unavailable",
        )
    module = importlib.util.module_from_spec(spec)
    previous_bytecode_setting = sys.dont_write_bytecode
    sys.modules[package_name] = package
    sys.modules[module_name] = module
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
        digest = getattr(module, "source_tree_digest", None)
        module_path = _canonical_path(Path(getattr(module, "__file__", "")))
        spec_origin = _canonical_path(
            Path(getattr(getattr(module, "__spec__", None), "origin", ""))
        )
        expected_path = _canonical_path(package_root / "cutover_guard.py")
        if module_path != expected_path or spec_origin != expected_path:
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_SOURCE_UNAVAILABLE",
                "V8 source-tree digest implementation has a non-canonical origin",
            )
        if not callable(digest):
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_SOURCE_UNAVAILABLE",
                "V8 source-tree digest implementation is unavailable",
            )
    except ReleaseSubjectError:
        raise
    except (ImportError, ModuleNotFoundError, OSError, TypeError, ValueError) as error:
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_SOURCE_UNAVAILABLE",
            "V8 source-tree digest implementation is unavailable",
        ) from error
    finally:
        sys.dont_write_bytecode = previous_bytecode_setting
        for name in tuple(sys.modules):
            if name == alias or name.startswith(f"{alias}."):
                sys.modules.pop(name, None)
    try:
        value = digest(root, root_handle=root_handle)
    except Exception as error:
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_SOURCE_UNAVAILABLE",
            "V8 source-tree digest could not be computed",
        ) from error
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_SOURCE_INVALID",
            "V8 source-tree digest is not a lowercase SHA-256",
        )
    return value


def _default_git_output(
    args: Sequence[str],
    code: str,
    *,
    repository_lease: _DirectoryLease | None = None,
) -> str:
    if repository_lease is not None:
        repository_lease.assert_stable(code)
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repository_lease.path if repository_lease is not None else REPOSITORY_ROOT,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise ReleaseSubjectError(
            code, f"git command was unavailable: {args}"
        ) from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        raise ReleaseSubjectError(code, detail)
    if type(result.stdout) is not str:
        raise ReleaseSubjectError(code, "git output was not exact text")
    if repository_lease is not None:
        repository_lease.assert_stable(code)
    return result.stdout


def _unexpected_status_records(output: str) -> tuple[str, ...]:
    unexpected: list[str] = []
    for record in output.split("\0"):
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            unexpected.append(record)
            continue
        status = record[:2]
        path = record[3:].replace("\\", "/")
        if status != "??" or not (
            path == ".codex-tmp" or path.startswith(".codex-tmp/")
        ):
            unexpected.append(record)
    return tuple(unexpected)


def _git_snapshot(
    *,
    repository_lease: _DirectoryLease | None = None,
) -> tuple[str, str]:
    head = _default_git_output(
        ("rev-parse", "--verify", "HEAD"),
        "RELEASE_SUBJECT_HEAD_UNAVAILABLE",
        repository_lease=repository_lease,
    ).strip()
    tree = _default_git_output(
        ("rev-parse", "--verify", "HEAD^{tree}"),
        "RELEASE_SUBJECT_TREE_UNAVAILABLE",
        repository_lease=repository_lease,
    ).strip()
    origin_main = _default_git_output(
        ("rev-parse", "--verify", REMOTE_REF),
        "RELEASE_SUBJECT_ORIGIN_UNAVAILABLE",
        repository_lease=repository_lease,
    ).strip()
    if origin_main != head:
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_ORIGIN_MISMATCH",
            f"origin/main is {origin_main}, not HEAD {head}",
        )
    status = _default_git_output(
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
        "RELEASE_SUBJECT_STATUS_UNAVAILABLE",
        repository_lease=repository_lease,
    )
    unexpected = _unexpected_status_records(status)
    if unexpected:
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_GIT_DIRTY",
            "unexpected Git status: " + "; ".join(unexpected),
        )
    if _HEX40.fullmatch(head) is None or _HEX40.fullmatch(tree) is None:
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_GIT_INVALID",
            "Git HEAD/tree output is not lowercase 40-character hexadecimal",
        )
    return head, tree


def generate_production_subject() -> ReleaseSubject:
    """Build a subject from only the fixed local checkout and evidence roots."""

    repository_root = Path(REPOSITORY_ROOT).expanduser().resolve(strict=False)
    evidence_root = Path(EVIDENCE_ROOT).expanduser().resolve(strict=False)
    repository_lease: _DirectoryLease | None = None
    evidence_lease: _DirectoryLease | None = None
    try:
        repository_lease = _directory_lease(
            repository_root,
            "RELEASE_SUBJECT_REPOSITORY_INVALID",
        )
        evidence_lease = _directory_lease(
            evidence_root,
            "RELEASE_SUBJECT_EVIDENCE_INVALID",
        )
        generation_lease = _GenerationLease(repository_lease, evidence_lease)
        generation_lease.assert_stable()
        _require_subject_absent_held(
            evidence_root / RELEASE_SUBJECT_FILENAME,
            evidence_lease,
        )
        generation_lease.assert_stable()
        head, tree = _git_snapshot(repository_lease=repository_lease)
        generation_lease.assert_stable()
        audited_source_tree_digest = source_tree_digest(
            repository_root,
            root_handle=repository_lease.handles[-1],
        )
        generation_lease.assert_stable()
        runner, attestors, bundle_sha256, reviewed = _observer_snapshot(
            repository_root,
            repository_lease=repository_lease,
        )
        generation_lease.assert_stable()
        final_head, final_tree = _git_snapshot(repository_lease=repository_lease)
        if (final_head, final_tree) != (head, tree):
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_REPOSITORY_DRIFT",
                "Git revision changed during subject generation",
            )
        final_source_digest = source_tree_digest(
            repository_root,
            root_handle=repository_lease.handles[-1],
        )
        if final_source_digest != audited_source_tree_digest:
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_REPOSITORY_DRIFT",
                "source tree changed during subject generation",
            )
        final_runner, final_attestors, final_bundle, final_reviewed = _observer_snapshot(
            repository_root,
            repository_lease=repository_lease,
        )
        if (
            final_runner != runner
            or final_attestors != attestors
            or final_bundle != bundle_sha256
            or final_reviewed != reviewed
        ):
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_REPOSITORY_DRIFT",
                "observer inputs changed during subject generation",
            )
        generation_lease.assert_stable()
        body: dict[str, object] = {
            "schema": RELEASE_SUBJECT_SCHEMA,
            "repository": REPOSITORY,
            "repository_root": _canonical_path(repository_root),
            "evidence_root": _canonical_path(evidence_root),
            "merged_main_sha": head,
            "merged_main_git_tree": tree,
            "audited_source_tree_digest": audited_source_tree_digest,
            "remote_ref": REMOTE_REF,
            "runner": runner.canonical(),
            "attestors": [attestor.canonical() for attestor in attestors],
            "attestor_bundle_sha256": bundle_sha256,
            "reviewed_provenance": reviewed.canonical(),
        }
        subject = ReleaseSubject(
            schema=RELEASE_SUBJECT_SCHEMA,
            repository=REPOSITORY,
            repository_root=_canonical_path(repository_root),
            evidence_root=_canonical_path(evidence_root),
            merged_main_sha=head,
            merged_main_git_tree=tree,
            audited_source_tree_digest=audited_source_tree_digest,
            remote_ref=REMOTE_REF,
            runner=runner,
            attestors=attestors,  # type: ignore[arg-type]
            attestor_bundle_sha256=bundle_sha256,
            reviewed_provenance=reviewed,
            subject_digest=release_subject_digest(body),
        )
        object.__setattr__(subject, "_generation_lease", generation_lease)
        repository_lease = None
        evidence_lease = None
        return subject
    finally:
        if evidence_lease is not None:
            evidence_lease.close()
        if repository_lease is not None:
            repository_lease.close()
