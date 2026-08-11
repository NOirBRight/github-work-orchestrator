"""Closed value types for the V8 release subject manifest."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from types import TracebackType
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
        os.close(descriptors.pop())


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
        if directory:
            desired_access = 0x00000001 | 0x00000020 | 0x00000080 | 0x00100000
            if writable:
                desired_access |= 0x00000002
        share_access = 0x00000003
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
        share = 0x00000003
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


def _open_bound_handle(
    path: Path,
    code: str,
    *,
    create_new: bool = False,
    writable: bool = False,
) -> tuple[int, dict[str, int | str]]:
    components: list[int] = []
    descriptor: int | None = None
    try:
        components, _identities = _open_directory_components(
            Path(path).parent,
            code,
            allow_file_create=create_new,
        )
        descriptor = _open_path_handle(
            Path(path).name,
            code,
            directory=False,
            parent=components[-1],
            create_new=create_new,
            writable=writable,
        )
        identity = _windows_handle_identity(descriptor, code, directory=False)
        observed_mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(observed_mode):
            raise ReleaseSubjectError(code, f"path is not a regular file: {path}")
        return descriptor, identity
    except ReleaseSubjectError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
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
) -> tuple[bytes, dict[str, int | str], int]:
    descriptor, identity = _open_bound_handle(path, code)
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
        return raw, dict(identity), descriptor
    except Exception:
        os.close(descriptor)
        raise


def _read_regular_file_once(
    path: Path,
    code: str,
) -> tuple[bytes, dict[str, int | str]]:
    raw, identity, descriptor = _read_held_regular_file(path, code)
    try:
        return raw, identity
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class ReleaseSubjectBinding:
    subject: ReleaseSubject
    manifest_path: Path
    raw_bytes: bytes
    identity: Mapping[str, object]
    handle: int

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

    def assert_stable(self) -> None:
        try:
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
            )
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
        try:
            os.close(self.handle)
        except OSError:
            pass

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


def _observer_paths(repository_root: Path) -> tuple[Path, tuple[Path, ...], Path]:
    scripts_root = Path(repository_root).expanduser().resolve(strict=False) / "scripts"
    runner = scripts_root / "run_beta3_live_guard.py"
    attestors = tuple(scripts_root / name for name in ATTESTOR_FILENAMES)
    reviewed = scripts_root / "beta3_reviewed_provenance.json"
    return runner, attestors, reviewed


def _observer_invalid(detail: str) -> None:
    raise ReleaseSubjectError("RELEASE_SUBJECT_OBSERVER_INVALID", detail)


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
            "RELEASE_SUBJECT_OBSERVER_INVALID", error.detail
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
) -> tuple[
    ReleaseFileIdentity,
    tuple[ReleaseFileIdentity, ...],
    str,
    ReviewedProvenanceIdentity,
]:
    runner_path, attestor_paths, reviewed_path = _observer_paths(repository_root)
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
    raw, identity, handle = _read_held_regular_file(
        manifest_path,
        "RELEASE_SUBJECT_UNAVAILABLE",
        file_reader=file_reader,
    )
    try:
        subject = _validate_manifest_and_observer_bytes(
            raw,
            identity,
            manifest_path,
            expected_repository_root,
            expected_evidence_root,
        )
    except Exception:
        os.close(handle)
        raise
    return ReleaseSubjectBinding(subject, manifest_path, raw, identity, handle)


def load_production_release_subject() -> ReleaseSubjectBinding:
    path = production_subject_path()
    _validate_manifest_path(path, EVIDENCE_ROOT)
    raw, identity, handle = _read_held_regular_file(
        path,
        "RELEASE_SUBJECT_UNAVAILABLE",
    )
    try:
        subject = _validate_manifest_and_observer_bytes(
            raw,
            identity,
            path,
            REPOSITORY_ROOT,
            EVIDENCE_ROOT,
        )
    except Exception:
        os.close(handle)
        raise
    return ReleaseSubjectBinding(subject, path, raw, identity, handle)


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
    parent_descriptors: list[int] = []
    descriptor: int | None = None
    try:
        parent_descriptors, _parent_identities = _open_directory_components(
            manifest_path.parent,
            "RELEASE_SUBJECT_PATH_INVALID",
            allow_file_create=True,
        )
        descriptor = _open_path_handle(
            Path(manifest_path.name),
            "RELEASE_SUBJECT_PATH_INVALID",
            directory=False,
            parent=parent_descriptors[-1],
            create_new=True,
            writable=True,
        )
        _write_all(descriptor, raw)
        observed_raw = _read_held_bytes(descriptor, "RELEASE_SUBJECT_WRITE_FAILED")
        if observed_raw != raw:
            raise ReleaseSubjectError(
                "RELEASE_SUBJECT_WRITE_FAILED",
                "subject write readback did not match canonical bytes",
            )
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
        return binding
    finally:
        if descriptor is not None:
            os.close(descriptor)
        _close_descriptors(parent_descriptors)


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


def source_tree_digest(repository_root: Path) -> str:
    """Return the existing V8 audited source-tree digest for a fixed root."""

    root = Path(repository_root).expanduser().resolve(strict=False)
    scripts_root = root / "skills" / "orchestrator" / "scripts"
    inserted = str(scripts_root) not in sys.path
    if inserted:
        sys.path.insert(0, str(scripts_root))
    try:
        from gwo_v8.cutover_guard import source_tree_digest as digest
    except (ImportError, ModuleNotFoundError, OSError) as error:
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_SOURCE_UNAVAILABLE",
            "V8 source-tree digest implementation is unavailable",
        ) from error
    finally:
        if inserted:
            try:
                sys.path.remove(str(scripts_root))
            except ValueError:
                pass
    try:
        value = digest(root)
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


def _default_git_output(args: Sequence[str], code: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=REPOSITORY_ROOT,
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


def _git_snapshot() -> tuple[str, str]:
    head = _default_git_output(
        ("rev-parse", "--verify", "HEAD"), "RELEASE_SUBJECT_HEAD_UNAVAILABLE"
    ).strip()
    tree = _default_git_output(
        ("rev-parse", "--verify", "HEAD^{tree}"),
        "RELEASE_SUBJECT_TREE_UNAVAILABLE",
    ).strip()
    origin_main = _default_git_output(
        ("rev-parse", "--verify", REMOTE_REF),
        "RELEASE_SUBJECT_ORIGIN_UNAVAILABLE",
    ).strip()
    if origin_main != head:
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_ORIGIN_MISMATCH",
            f"origin/main is {origin_main}, not HEAD {head}",
        )
    status = _default_git_output(
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
        "RELEASE_SUBJECT_STATUS_UNAVAILABLE",
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
    repository_descriptors: list[int] = []
    evidence_descriptors: list[int] = []
    try:
        repository_descriptors, _ = _open_directory_components(
            repository_root,
            "RELEASE_SUBJECT_REPOSITORY_INVALID",
        )
        evidence_descriptors, _ = _open_directory_components(
            evidence_root,
            "RELEASE_SUBJECT_EVIDENCE_INVALID",
        )
    finally:
        _close_descriptors(repository_descriptors)
        _close_descriptors(evidence_descriptors)
    head, tree = _git_snapshot()
    audited_source_tree_digest = source_tree_digest(repository_root)
    runner, attestors, bundle_sha256, reviewed = _observer_snapshot(repository_root)
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
    return ReleaseSubject(
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
