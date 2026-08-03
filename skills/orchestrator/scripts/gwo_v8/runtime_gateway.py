"""The single V8 boundary for semantic Runtime materialization.

The gateway deliberately gives its callers no provider command, session, or
binding choreography.  A caller supplies one closed semantic subject and an
Artifact reference; the gateway reads back an existing action before staging
or starting it.  Provider adapters are private implementation details.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from copy import deepcopy
from enum import Enum
import errno
import hashlib
import ntpath
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
import time
import weakref
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from ._canonical import (
    CanonicalJsonError,
    canonical_bytes,
    digest_value,
    load_canonical_json,
    strict_json_loads,
)
from .planning_protocol import (
    planning_output_schema_from_prompt,
    planning_protocol_kind_from_prompt,
    replanning_output_schema_from_prompt,
)
from .runtime_profile import (
    RuntimeProfile,
    _SealedValueMeta,
    _reject_reinitialization,
)


_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_GIT_COMMIT_RE = re.compile(r"[0-9a-f]{40,64}")
_SPECIALIST_RE = re.compile(r"specialist:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_TICKET_ROLES = {
    "worker",
    "recovery_worker",
    "review_primary",
    "review_strong",
}
_PLAN_INVALIDATION_REPORT_ROLES = frozenset({"worker", "recovery_worker", "review"})
_PLAN_INVALIDATION_ROLE_GRANTS = {
    "worker": frozenset({("workspace.write.v1", "work-run.workspace.v1")}),
    "recovery_worker": frozenset({("workspace.write.v1", "work-run.workspace.v1")}),
    "review": frozenset({("repository.read.v1", "review.subject.v1")}),
}
_BOUND_LIFECYCLES = frozenset(
    {"running", "parked", "completed", "retired"}
)
_PASEO_BATCH_META = frozenset("&|<>^%!\"()")
_MAXIMUM_PASEO_COMMAND_CHARS = 7_500
_MAXIMUM_PASEO_PERMISSION_TEXT = 4_096
_MAXIMUM_PASEO_ERROR_JSON_BYTES = 4_096
_MAXIMUM_PASEO_STREAM_BYTES = 1_048_576
_MAXIMUM_PASEO_TOTAL_BYTES = 1_572_864
_NO_PASEO_BODY_ACK = object()
_PASEO_PIPE_CHUNK_BYTES = 65_536
_PASEO_PIPE_POLL_SECONDS = 0.005
_PASEO_POST_EXIT_DRAIN_SECONDS = 0.25
_PASEO_CLEANUP_GRACE_SECONDS = 0.5
_JOURNAL_LOCK_TIMEOUT_SECONDS = 5.0
_JOURNAL_LOCK_RETRY_SECONDS = 0.01
_MAXIMUM_RUNTIME_JOURNAL_BYTES = 16_777_216
_MAXIMUM_RUNTIME_EVENTS = 64
_MAXIMUM_RUNTIME_EVENT_PAGE = 16
_MAXIMUM_RUNTIME_EVENT_READBACKS = 1
_RUNTIME_RECOVERY_RETRY_SECONDS = 60
_MAXIMUM_RUNTIME_SCALAR_INTEGER = (1 << 63) - 1
_MAXIMUM_RUNTIME_EVENT_CURSOR = _MAXIMUM_RUNTIME_SCALAR_INTEGER
_MAXIMUM_RUNTIME_EVENT_CURSOR_TEXT = str(_MAXIMUM_RUNTIME_EVENT_CURSOR)
_RUNTIME_EVENT_KINDS = frozenset(
    {
        "state:prepared",
        "state:running",
        "state:parked",
        "state:completed",
        "state:retired",
    }
)
_RUNTIME_WORKSPACE_LAYOUT_VERSION = "1"
_RUNTIME_WORKSPACE_OWNER_SCHEMA = "gwo.runtime.workspace-owner.v1"
_RUNTIME_WORKSPACE_OWNER_FILE = "runtime-owner.v1.json"
_RUNTIME_OUTPUT_SCHEMA_VERSION = "gwo.runtime.output.v1"
_RUNTIME_WORKSPACE_DIRECTORIES = (
    "runtime-artifacts",
    "runtime-results",
    "runtime-schemas",
)
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_JOURNAL_MUTEX_GUARD = threading.Lock()
_JOURNAL_MUTEXES: dict[str, threading.Lock] = {}


def _is_local_absolute_workspace_path(value: object) -> bool:
    """Accept only a locally rooted Workspace path without touching it.

    Paseo registry, durable-action, and inspect-cwd values are untrusted until
    this purely lexical check succeeds.  In particular, a Windows UNC/device
    path must never reach ``Path.resolve`` because resolution can contact a
    remote host or a device namespace.
    """

    if type(value) is not str or not value or "\0" in value:
        return False
    if os.name == "nt":
        if value.startswith(("\\\\", "//")):
            return False
        drive, tail = ntpath.splitdrive(value)
        return (
            len(drive) == 2
            and drive[1] == ":"
            and ("A" <= drive[0] <= "Z" or "a" <= drive[0] <= "z")
            and bool(tail)
            and tail[0] in {"\\", "/"}
        )
    # POSIX reserves a double-leading slash for implementation-defined network
    # semantics.  Treat it as untrusted rather than normalizing it locally.
    return value.startswith("/") and not value.startswith("//")


class RuntimeGatewayError(RuntimeError):
    """A typed Gateway-owned configuration, identity, or transport failure."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _load_runtime_artifact_json(payload: bytes) -> Any:
    try:
        return load_canonical_json(payload)
    except CanonicalJsonError as error:
        raise RuntimeGatewayError(
            "RUNTIME_ARTIFACT_INVALID", "Artifact is not canonical JSON"
        ) from error


class _V3JsonJournal:
    """Private bounded-lock JSON journal with unique atomic replacements."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")
        mutex_key = str(self.lock_path.resolve())
        with _JOURNAL_MUTEX_GUARD:
            self._mutex = _JOURNAL_MUTEXES.setdefault(mutex_key, threading.Lock())

    @contextmanager
    def exclusive(self):
        deadline = time.monotonic() + _JOURNAL_LOCK_TIMEOUT_SECONDS
        if not self._mutex.acquire(timeout=_JOURNAL_LOCK_TIMEOUT_SECONDS):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_BUSY",
                "Runtime journal lock could not be acquired within its bound",
            )
        handle = None
        acquired = False
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.lock_path.open("a+b")
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as error:
                    if error.errno not in {
                        errno.EACCES,
                        errno.EAGAIN,
                        errno.EDEADLK,
                    }:
                        raise RuntimeGatewayError(
                            "RUNTIME_STORE_INVALID",
                            "Runtime journal lock is unavailable",
                        ) from error
                    if time.monotonic() >= deadline:
                        raise RuntimeGatewayError(
                            "RUNTIME_STORE_BUSY",
                            "Runtime journal lock could not be acquired within its bound",
                        ) from error
                    time.sleep(_JOURNAL_LOCK_RETRY_SECONDS)
            yield
        finally:
            try:
                if acquired and handle is not None:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                try:
                    if handle is not None:
                        handle.close()
                finally:
                    self._mutex.release()

    def read_unlocked(self) -> Any | None:
        if not self.path.exists():
            return None
        try:
            with self.path.open("rb") as handle:
                payload = handle.read(_MAXIMUM_RUNTIME_JOURNAL_BYTES + 1)
            if len(payload) > _MAXIMUM_RUNTIME_JOURNAL_BYTES:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID", "Runtime journal exceeds its byte bound"
                )
            return load_canonical_json(payload)
        except (OSError, CanonicalJsonError) as error:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Runtime journal is unreadable"
            ) from error

    def replace_unlocked(self, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{uuid4().hex}.tmp")
        try:
            try:
                payload = canonical_bytes(value)
            except CanonicalJsonError as error:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "Runtime journal contains a non-canonical value",
                ) from error
            if len(payload) > _MAXIMUM_RUNTIME_JOURNAL_BYTES:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID", "Runtime journal exceeds its byte bound"
                )
            with temporary.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                directory_fd = os.open(
                    self.path.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


@dataclass(frozen=True)
class _RuntimeFailure:
    """Private normalized Provider result; it never carries vendor text."""

    code: str
    detail: str
    stable_action_id: str | None = None
    authoritative_absence: bool = False
    observation_id: str | None = None

    @classmethod
    def absent(cls, stable_action_id: str) -> "_RuntimeFailure":
        return cls(
            "RUNTIME_ACTION_ABSENT",
            "authoritative stable-action absence",
            stable_action_id=stable_action_id,
            authoritative_absence=True,
        )

    @classmethod
    def transport(cls, _native_detail: str = "") -> "_RuntimeFailure":
        return cls(
            "RUNTIME_TRANSPORT_UNAVAILABLE",
            "Runtime provider transport is unavailable",
        )

    @classmethod
    def ambiguous(cls, stable_action_id: str) -> "_RuntimeFailure":
        return cls(
            "RUNTIME_IDENTITY_AMBIGUOUS",
            "Runtime identity readback is ambiguous",
            stable_action_id=stable_action_id,
        )

    @classmethod
    def provider_unavailable(
        cls,
        observation_id: str,
        *,
        stable_action_id: str | None = None,
    ) -> "_RuntimeFailure":
        _require_text(observation_id, "provider unavailable observation_id")
        return cls(
            "RUNTIME_PROVIDER_UNAVAILABLE",
            "Runtime provider is unavailable",
            stable_action_id=stable_action_id,
            observation_id=observation_id,
        )


class _RuntimeRecoverySignal(Exception):
    """Private transport of a classified command failure to Gateway policy."""

    def __init__(self, failure: _RuntimeFailure):
        self.failure = failure
        super().__init__(failure.code)


@dataclass(frozen=True)
class ArtifactRef:
    """Digest-addressed bounded bytes; Providers receive a ref/path, never text."""

    digest: str
    byte_length: int
    path: str


@dataclass(frozen=True, slots=True)
class _RuntimeOutputIdentity:
    subject_digest: str
    stable_action_id: str
    authority_digest: str

    def canonical(self) -> dict[str, str]:
        """Return the sole canonical Runtime-output identity field table."""

        return {
            "subject_digest": self.subject_digest,
            "stable_action_id": self.stable_action_id,
            "authority_digest": self.authority_digest,
        }


@dataclass(frozen=True, slots=True)
class _RuntimeOutputArtifactProof:
    artifact_digest: str
    byte_length: int
    schema_version: str
    subject_digest: str
    stable_action_id: str
    authority_digest: str


def _runtime_output_schema_bytes(
    identity: _RuntimeOutputIdentity,
    payload_schema: Mapping[str, Any] | None = None,
) -> bytes:
    identity_fields = identity.canonical()
    return canonical_bytes(
        {
            "type": "object",
            "required": [
                "schema_version",
                *identity_fields,
                "payload",
            ],
            "properties": {
                "schema_version": {"const": _RUNTIME_OUTPUT_SCHEMA_VERSION},
                **{
                    name: {"const": value}
                    for name, value in identity_fields.items()
                },
                "payload": {} if payload_schema is None else payload_schema,
            },
            "additionalProperties": False,
        }
    )


def _prove_runtime_output_bytes(
    payload: bytes,
    identity: _RuntimeOutputIdentity,
    invalid_detail: str,
) -> _RuntimeOutputArtifactProof:
    output = _load_runtime_artifact_json(payload)
    identity_fields = identity.canonical()
    expected_fields = {
        "schema_version": _RUNTIME_OUTPUT_SCHEMA_VERSION,
        **identity_fields,
    }
    if (
        type(output) is not dict
        or set(output) != {*expected_fields, "payload"}
        or any(output.get(name) != value for name, value in expected_fields.items())
    ):
        raise RuntimeGatewayError(
            "RUNTIME_OUTPUT_ARTIFACT_INVALID",
            invalid_detail,
        )
    return _RuntimeOutputArtifactProof(
        artifact_digest=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        schema_version=_RUNTIME_OUTPUT_SCHEMA_VERSION,
        **identity_fields,
    )


class ArtifactStore:
    """Gateway-owned durable Artifact resolver with verified bounded readback."""

    def __init__(self, root: Path, *, maximum_bytes: int = 1_048_576):
        self._root = Path(root)
        self._maximum_bytes = maximum_bytes
        if not isinstance(maximum_bytes, int) or isinstance(maximum_bytes, bool) or maximum_bytes < 1:
            raise ValueError("maximum_bytes must be a positive integer")

    def put(self, payload: bytes) -> ArtifactRef:
        if not isinstance(payload, bytes):
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_INVALID", "Artifact payload must be bytes"
            )
        if len(payload) > self._maximum_bytes:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_TOO_LARGE", "Artifact exceeds the bounded transport limit"
            )
        digest = hashlib.sha256(payload).hexdigest()
        target = self.path_for(digest)
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_UNAVAILABLE",
                "Artifact Store directory is unavailable",
            ) from error
        if target.exists():
            reference = self._verify_put_target(digest, payload)
            self._fsync_directory()
            return reference
        temporary = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
        temporary_created = False
        try:
            try:
                with temporary.open("xb") as handle:
                    temporary_created = True
                    if handle.write(payload) != len(payload):
                        raise OSError("Artifact temporary write was incomplete")
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as error:
                raise RuntimeGatewayError(
                    "RUNTIME_ARTIFACT_UNAVAILABLE",
                    "Artifact temporary write is unavailable",
                ) from error
            try:
                os.replace(temporary, target)
            except OSError as replace_error:
                # Another same-digest writer may have won the atomic replace.
                # It is safe to adopt only exact bounded bytes at this digest.
                try:
                    reference = self._verify_put_target(digest, payload)
                    self._fsync_directory()
                    return reference
                except RuntimeGatewayError as verification_error:
                    if (
                        verification_error.code
                        == "RUNTIME_ARTIFACT_DIGEST_MISMATCH"
                    ):
                        raise
                    raise RuntimeGatewayError(
                        "RUNTIME_ARTIFACT_UNAVAILABLE",
                        "Artifact atomic replacement is unavailable",
                    ) from replace_error
            self._fsync_directory()
            return self._verify_put_target(digest, payload)
        finally:
            if temporary_created:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def put_canonical(self, value: Any) -> ArtifactRef:
        try:
            payload = canonical_bytes(value)
        except CanonicalJsonError as error:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_INVALID",
                "Artifact value is outside the canonical JSON domain",
            ) from error
        return self.put(payload)

    def get(self, digest: str) -> ArtifactRef:
        reference, _payload = self._read(digest)
        return reference

    def read_bytes(self, digest: str) -> bytes:
        _reference, payload = self._read(digest)
        return payload

    def put_file(self, path: Path) -> ArtifactRef:
        return self.put(self._read_path(Path(path)))

    def put_json_file(self, path: Path) -> tuple[ArtifactRef, Any]:
        """Store and validate one bounded external canonical-JSON result read."""

        payload = self._read_path(Path(path))
        return self.put(payload), self._canonical_json(payload)

    def read_file(self, path: Path, digest: str) -> bytes:
        """Bound and verify one externally staged digest-addressed Artifact."""

        _require_digest(digest, "artifact digest")
        payload = self._read_path(Path(path))
        if hashlib.sha256(payload).hexdigest() != digest:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_DIGEST_MISMATCH", "Artifact bytes do not match their digest"
            )
        return payload

    def _read(self, digest: str) -> tuple[ArtifactRef, bytes]:
        _require_digest(digest, "artifact digest")
        target = self.path_for(digest)
        payload = self._read_path(target)
        if hashlib.sha256(payload).hexdigest() != digest:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_DIGEST_MISMATCH", "Artifact bytes do not match their digest"
            )
        return ArtifactRef(digest, len(payload), str(target)), payload

    def _verify_put_target(self, digest: str, payload: bytes) -> ArtifactRef:
        last_error: RuntimeGatewayError | None = None
        for attempt in range(3):
            try:
                reference, observed = self._read(digest)
            except RuntimeGatewayError as error:
                last_error = error
                if error.code == "RUNTIME_ARTIFACT_DIGEST_MISMATCH":
                    raise
                if attempt < 2:
                    time.sleep(0.001)
                continue
            if observed != payload:
                raise RuntimeGatewayError(
                    "RUNTIME_ARTIFACT_DIGEST_MISMATCH",
                    "Artifact target bytes do not match the put payload",
                )
            return reference
        assert last_error is not None
        raise last_error

    def _fsync_directory(self) -> None:
        if os.name == "nt":
            return
        try:
            directory_fd = os.open(
                self._root,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as error:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_UNAVAILABLE",
                "Artifact Store directory durability is unavailable",
            ) from error

    def _read_path(self, target: Path) -> bytes:
        try:
            with target.open("rb") as handle:
                payload = handle.read(self._maximum_bytes + 1)
        except FileNotFoundError as error:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_MISSING", "required Artifact is not readable"
            ) from error
        except OSError as error:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_UNAVAILABLE", "required Artifact is unavailable"
            ) from error
        if len(payload) > self._maximum_bytes:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_TOO_LARGE", "Artifact exceeds the bounded transport limit"
            )
        return payload

    def read_json(self, digest: str) -> Any:
        _reference, payload = self._read(digest)
        return self._canonical_json(payload)

    def prove_runtime_output(
        self,
        digest: str,
        *,
        subject_digest: str,
        stable_action_id: str,
        authority_digest: str,
    ) -> _RuntimeOutputArtifactProof:
        """Read and prove one exact closed Runtime output Artifact."""

        reference, payload = self._read(digest)
        proof = _prove_runtime_output_bytes(
            payload,
            _RuntimeOutputIdentity(
                subject_digest=subject_digest,
                stable_action_id=stable_action_id,
                authority_digest=authority_digest,
            ),
            "Runtime output Artifact does not bind its exact action",
        )
        if (
            proof.artifact_digest != reference.digest
            or proof.byte_length != reference.byte_length
        ):
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_DIGEST_MISMATCH",
                "Artifact bytes do not match their digest",
            )
        return proof

    @staticmethod
    def _canonical_json(payload: bytes) -> Any:
        return _load_runtime_artifact_json(payload)

    def path_for(self, digest: str) -> Path:
        _require_digest(digest, "artifact digest")
        return self._root / digest

    @property
    def maximum_bytes(self) -> int:
        return self._maximum_bytes


class _RuntimeWorkspaceFiles:
    """Action-owned, fixed-name files below one verified Workspace.

    This is a fail-closed defense for non-racing link, junction, reparse-point,
    hard-link, and journal-redirection attacks.  Python's path APIs cannot
    provide descriptor-relative no-follow operations portably on Windows, so
    this helper deliberately does not claim protection against an attacker
    racing the lstat/resolve/open or lstat/replace sequences.
    """

    def __init__(
        self,
        *,
        workspace_path: str,
        workspace_id: str,
        workspace_slug: str,
        workspace_base_commit: str,
        ownership_nonce: str,
        repository: str,
        stable_action_id: str,
        subject_digest: str,
        spec_identity_digest: str,
        maximum_bytes: int,
    ):
        if (
            not isinstance(ownership_nonce, str)
            or re.fullmatch(r"[0-9a-f]{32}", ownership_nonce) is None
        ):
            raise RuntimeGatewayError(
                "RUNTIME_WORKSPACE_UNSAFE",
                "Runtime Workspace ownership nonce is invalid",
            )
        if (
            not isinstance(maximum_bytes, int)
            or isinstance(maximum_bytes, bool)
            or maximum_bytes < 1
        ):
            raise ValueError("maximum_bytes must be a positive integer")
        if not _is_local_absolute_workspace_path(workspace_path):
            raise self._unsafe(
                "Runtime Workspace path is not a supported local absolute path"
            )
        self.workspace = Path(workspace_path).resolve()
        self.runtime_root = self.workspace / ".gwo"
        self.ownership_nonce = ownership_nonce
        self.maximum_bytes = maximum_bytes
        self._marker = {
            "schema_version": _RUNTIME_WORKSPACE_OWNER_SCHEMA,
            "layout_version": _RUNTIME_WORKSPACE_LAYOUT_VERSION,
            "ownership_nonce": ownership_nonce,
            "repository": repository,
            "workspace_id": workspace_id,
            "workspace_slug": workspace_slug,
            "workspace_path": str(self.workspace),
            "workspace_base_commit": workspace_base_commit,
            "stable_action_id": stable_action_id,
            "subject_digest": subject_digest,
            "spec_identity_digest": spec_identity_digest,
        }
        self._marker_payload = canonical_bytes(self._marker)
        self.marker_digest = hashlib.sha256(self._marker_payload).hexdigest()
        self._action_digest = digest_value(
            {
                "repository": repository,
                "stable_action_id": stable_action_id,
            }
        )

    @property
    def marker_path(self) -> Path:
        return self.runtime_root / _RUNTIME_WORKSPACE_OWNER_FILE

    def artifact_path(self, digest: str) -> Path:
        return (
            self.runtime_root
            / "runtime-artifacts"
            / f"{_require_digest(digest, 'Workspace artifact digest')}.json"
        )

    @property
    def result_path(self) -> Path:
        return (
            self.runtime_root
            / "runtime-results"
            / f"{self._action_digest}.json"
        )

    @property
    def schema_path(self) -> Path:
        return (
            self.runtime_root
            / "runtime-schemas"
            / f"{self._action_digest}.json"
        )

    @property
    def resume_path(self) -> Path:
        return self.runtime_root / "runtime-artifacts" / "resume.txt"

    @staticmethod
    def _unsafe(detail: str) -> RuntimeGatewayError:
        return RuntimeGatewayError("RUNTIME_WORKSPACE_UNSAFE", detail)

    def _assert_contained(self, path: Path, *, strict: bool) -> None:
        try:
            resolved = path.resolve(strict=strict)
            common = Path(
                os.path.commonpath((str(self.workspace), str(resolved)))
            )
        except (OSError, ValueError) as error:
            raise self._unsafe("Runtime Workspace path containment is invalid") from error
        if common != self.workspace:
            raise self._unsafe("Runtime Workspace path escapes its verified root")

    @staticmethod
    def _lstat(path: Path) -> os.stat_result | None:
        try:
            return os.lstat(path)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise RuntimeGatewayError(
                "RUNTIME_WORKSPACE_UNSAFE",
                "Runtime Workspace path metadata is unavailable",
            ) from error

    def _validate_stat(
        self, path: Path, value: os.stat_result, *, directory: bool
    ) -> None:
        if (
            stat.S_ISLNK(value.st_mode)
            or getattr(value, "st_file_attributes", 0)
            & _WINDOWS_REPARSE_POINT
        ):
            raise self._unsafe(
                "Runtime Workspace descendants must not be links or reparse points"
            )
        if directory:
            if not stat.S_ISDIR(value.st_mode):
                raise self._unsafe("Runtime Workspace parent is not a directory")
        else:
            if not stat.S_ISREG(value.st_mode):
                raise self._unsafe("Runtime Workspace leaf is not a regular file")
            if getattr(value, "st_nlink", 1) != 1:
                raise self._unsafe("Runtime Workspace leaf must not be hard-linked")
        self._assert_contained(path, strict=True)

    def _check_path(
        self,
        path: Path,
        *,
        directory: bool,
        missing_leaf_ok: bool = False,
    ) -> os.stat_result | None:
        candidate = Path(path)
        try:
            relative = candidate.relative_to(self.workspace)
        except ValueError as error:
            raise self._unsafe("Runtime Workspace path is outside its verified root") from error
        if not relative.parts:
            raise self._unsafe("Runtime Workspace root is not a governed leaf")
        current = self.workspace
        for index, part in enumerate(relative.parts):
            current /= part
            final = index == len(relative.parts) - 1
            value = self._lstat(current)
            if value is None:
                if final and missing_leaf_ok:
                    self._assert_contained(current, strict=False)
                    return None
                raise self._unsafe("Runtime Workspace governed path is missing")
            self._validate_stat(
                current,
                value,
                directory=(directory if final else True),
            )
        return value

    def _fsync_directory(self, path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _remove_validated_temporary(self, temporary: Path) -> None:
        value = self._check_path(
            temporary,
            directory=False,
            missing_leaf_ok=True,
        )
        if value is None:
            return
        try:
            temporary.unlink()
        except OSError as error:
            raise self._unsafe(
                "Runtime Workspace temporary leaf could not be removed"
            ) from error
        if self._lstat(temporary) is not None:
            raise self._unsafe("Runtime Workspace temporary leaf still exists")
        self._fsync_directory(temporary.parent)

    def _atomic_write(
        self,
        target: Path,
        payload: bytes,
        *,
        recoverable_temporary: Path | None = None,
    ) -> None:
        if len(payload) > self.maximum_bytes:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_TOO_LARGE",
                "Artifact exceeds the bounded transport limit",
        )
        self._check_path(target.parent, directory=True)
        self._check_path(target, directory=False, missing_leaf_ok=True)
        temporary = (
            target.with_name(f"{target.name}.{uuid4().hex}.tmp")
            if recoverable_temporary is None
            else Path(recoverable_temporary)
        )
        existing_temporary = self._check_path(
            temporary,
            directory=False,
            missing_leaf_ok=True,
        )
        if existing_temporary is not None:
            if recoverable_temporary is None:
                raise self._unsafe(
                    "Runtime Workspace temporary leaf already exists"
                )
            self._remove_validated_temporary(temporary)
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._check_path(temporary, directory=False)
            self._check_path(target, directory=False, missing_leaf_ok=True)
            os.replace(temporary, target)
            self._fsync_directory(target.parent)
            self._check_path(target, directory=False)
            if self._read_bytes(target) != payload:
                raise self._unsafe("Runtime Workspace atomic write verification failed")
        except FileExistsError as error:
            raise self._unsafe("Runtime Workspace temporary leaf already exists") from error
        except OSError as error:
            raise self._unsafe("Runtime Workspace atomic write failed") from error
        finally:
            self._remove_validated_temporary(temporary)

    def _read_bytes(self, target: Path, *, missing_ok: bool = False) -> bytes | None:
        try:
            value = self._check_path(
                target, directory=False, missing_leaf_ok=True
            )
            if value is None:
                if missing_ok:
                    return None
                raise RuntimeGatewayError(
                    "RUNTIME_ARTIFACT_MISSING",
                    "required Runtime Workspace Artifact is missing",
                )
            with target.open("rb") as handle:
                payload = handle.read(self.maximum_bytes + 1)
            self._check_path(target, directory=False)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_MISSING",
                "required Runtime Workspace Artifact is missing",
            )
        except OSError as error:
            raise self._unsafe("Runtime Workspace governed file is unavailable") from error
        if len(payload) > self.maximum_bytes:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_TOO_LARGE",
                "Artifact exceeds the bounded transport limit",
            )
        return payload

    def _validate_staging_tree(self, root: Path) -> None:
        self._check_path(root, directory=True)
        try:
            names = {entry.name for entry in os.scandir(root)}
        except OSError as error:
            raise self._unsafe("Runtime Workspace staging tree is unreadable") from error
        allowed = {
            *_RUNTIME_WORKSPACE_DIRECTORIES,
            _RUNTIME_WORKSPACE_OWNER_FILE,
            (
                f"{_RUNTIME_WORKSPACE_OWNER_FILE}."
                f"{self.ownership_nonce}.tmp"
            ),
        }
        if not names <= allowed:
            raise self._unsafe("Runtime Workspace staging tree has foreign entries")
        for directory_name in _RUNTIME_WORKSPACE_DIRECTORIES:
            child = root / directory_name
            value = self._lstat(child)
            if value is not None:
                self._validate_stat(child, value, directory=True)
        marker = root / _RUNTIME_WORKSPACE_OWNER_FILE
        value = self._lstat(marker)
        if value is not None:
            self._validate_stat(marker, value, directory=False)
        marker_temporary = root / (
            f"{_RUNTIME_WORKSPACE_OWNER_FILE}."
            f"{self.ownership_nonce}.tmp"
        )
        value = self._lstat(marker_temporary)
        if value is not None:
            self._validate_stat(marker_temporary, value, directory=False)

    def establish(self) -> None:
        """Create or recover exactly this nonce-owned reserved tree."""

        final_value = self._lstat(self.runtime_root)
        staging = self.workspace / f".gwo-init-{self.ownership_nonce}"
        staging_value = self._lstat(staging)
        if final_value is not None:
            self._validate_stat(self.runtime_root, final_value, directory=True)
            if staging_value is not None:
                raise self._unsafe(
                    "Runtime Workspace has both final and staging ownership trees"
                )
            self.verify()
            return
        if staging_value is None:
            self._assert_contained(staging, strict=False)
            try:
                os.mkdir(staging)
            except OSError as error:
                raise self._unsafe(
                    "Runtime Workspace ownership staging tree could not be created"
                ) from error
        else:
            self._validate_stat(staging, staging_value, directory=True)
        self._validate_staging_tree(staging)
        for directory_name in _RUNTIME_WORKSPACE_DIRECTORIES:
            directory = staging / directory_name
            if self._lstat(directory) is None:
                self._check_path(staging, directory=True)
                try:
                    os.mkdir(directory)
                except OSError as error:
                    raise self._unsafe(
                        "Runtime Workspace governed directory could not be created"
                    ) from error
            self._check_path(directory, directory=True)
        staging_marker = staging / _RUNTIME_WORKSPACE_OWNER_FILE
        staging_marker_temporary = staging / (
            f"{_RUNTIME_WORKSPACE_OWNER_FILE}."
            f"{self.ownership_nonce}.tmp"
        )
        self._remove_validated_temporary(staging_marker_temporary)
        existing_marker = self._read_bytes(staging_marker, missing_ok=True)
        if existing_marker is None:
            self._atomic_write(
                staging_marker,
                self._marker_payload,
                recoverable_temporary=staging_marker_temporary,
            )
        elif existing_marker != self._marker_payload:
            raise self._unsafe("Runtime Workspace ownership marker is not exact")
        self._validate_staging_tree(staging)
        if self._lstat(self.runtime_root) is not None:
            raise self._unsafe("Runtime Workspace reserved tree appeared unexpectedly")
        try:
            os.rename(staging, self.runtime_root)
            self._fsync_directory(self.workspace)
        except OSError as error:
            raise self._unsafe(
                "Runtime Workspace ownership tree could not be finalized"
            ) from error
        self.verify()

    def verify(self) -> None:
        self._check_path(self.runtime_root, directory=True)
        for directory_name in _RUNTIME_WORKSPACE_DIRECTORIES:
            self._check_path(
                self.runtime_root / directory_name,
                directory=True,
            )
        try:
            marker = self._read_bytes(self.marker_path)
        except RuntimeGatewayError as error:
            if error.code == "RUNTIME_ARTIFACT_MISSING":
                raise self._unsafe(
                    "Runtime Workspace ownership marker is absent"
                ) from error
            raise
        if marker != self._marker_payload:
            raise self._unsafe("Runtime Workspace ownership marker is not exact")

    def assert_record_paths(self, record: Mapping[str, Any]) -> None:
        input_digests = record.get("input_artifact_digests")
        input_files = record.get("input_files")
        if (
            not isinstance(input_digests, list)
            or not all(isinstance(digest, str) for digest in input_digests)
            or not isinstance(input_files, dict)
        ):
            raise self._unsafe("Runtime Workspace input path record is invalid")
        expected_inputs = {
            digest: str(self.artifact_path(digest)) for digest in input_digests
        }
        expected = {
            "workspace_path": str(self.workspace),
            "workspace_owner_nonce": self.ownership_nonce,
            "workspace_layout_version": _RUNTIME_WORKSPACE_LAYOUT_VERSION,
            "workspace_owner_marker_digest": self.marker_digest,
            "prompt_file": str(
                self.artifact_path(str(record.get("prompt_artifact_digest")))
            ),
            "input_files": expected_inputs,
            "result_file": str(self.result_path),
            "output_schema_file": str(self.schema_path),
        }
        if any(record.get(key) != value for key, value in expected.items()):
            raise self._unsafe(
                "Runtime Workspace journal paths do not match their fixed derivation"
            )
        # Pointer equivalence is a pure check.  Do it before any stat, resolve,
        # open, or marker read so a journal path cannot select a filesystem
        # target merely by being inspected during rejection.
        self.verify()

    def write_artifact(self, digest: str, payload: bytes) -> Path:
        target = self.artifact_path(digest)
        if hashlib.sha256(payload).hexdigest() != digest:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_DIGEST_MISMATCH",
                "staged Runtime Artifact is invalid",
            )
        self._atomic_write(target, payload)
        return target

    def read_artifact(self, digest: str) -> bytes:
        payload = self._read_bytes(self.artifact_path(digest))
        assert payload is not None
        if hashlib.sha256(payload).hexdigest() != digest:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_DIGEST_MISMATCH",
                "Artifact bytes do not match their digest",
            )
        return payload

    def write_schema(self, payload: bytes) -> str:
        self._atomic_write(self.schema_path, payload)
        return hashlib.sha256(payload).hexdigest()

    def read_schema(self, digest: str) -> bytes:
        _require_digest(digest, "Runtime Workspace schema digest")
        payload = self._read_bytes(self.schema_path)
        assert payload is not None
        if hashlib.sha256(payload).hexdigest() != digest:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_DIGEST_MISMATCH",
                "Runtime Workspace schema bytes do not match their digest",
            )
        return payload

    def read_result(self) -> bytes | None:
        return self._read_bytes(self.result_path, missing_ok=True)

    def verify_result_target(self) -> bool:
        """Safely prove whether the fixed result leaf exists."""

        return (
            self._check_path(
                self.result_path,
                directory=False,
                missing_leaf_ok=True,
            )
            is not None
        )

    def require_result_absent(self) -> None:
        """Prepared provenance requires that no result effect exists yet."""

        if (
            self._check_path(
                self.result_path,
                directory=False,
                missing_leaf_ok=True,
            )
            is not None
        ):
            raise RuntimeGatewayError(
                "RUNTIME_RESULT_PROVENANCE_INVALID",
                "Runtime result exists before one exact Bound action could produce it",
            )

    def write_resume(self, payload: bytes) -> Path:
        self._atomic_write(self.resume_path, payload)
        return self.resume_path


def _require_text(value: object, field_name: str) -> str:
    if type(value) is not str or not value:
        raise RuntimeGatewayError(
            "RUNTIME_SUBJECT_INVALID", f"{field_name} must be a non-empty string"
        )
    return value


def _require_paseo_argument(value: object, field_name: str) -> str:
    """Accept one bounded, batch-wrapper-safe Paseo argv value.

    Windows may dispatch a ``.cmd`` Paseo launcher through ``cmd.exe`` even
    when ``subprocess`` receives an argv list.  The Gateway therefore rejects
    control characters and batch metacharacters before any dynamic identity
    can reach the vendor boundary.
    """

    text = _require_text(value, field_name)
    if any(character in text for character in "\r\n\0") or any(
        character in _PASEO_BATCH_META for character in text
    ):
        raise RuntimeGatewayError(
            "RUNTIME_VENDOR_ARGUMENT_INVALID",
            f"{field_name} is unsafe for the Paseo command boundary",
        )
    return text


def _require_paseo_profile_argument(value: object, field_name: str) -> str:
    """Validate an immutable Profile value before it reaches Paseo argv."""

    if type(value) is not str or value != value.strip():
        raise RuntimeGatewayError(
            "RUNTIME_CONFIGURATION_INVALID",
            f"{field_name} must not have leading or trailing whitespace",
        )
    try:
        return _require_paseo_argument(value, field_name)
    except RuntimeGatewayError as error:
        raise RuntimeGatewayError(
            "RUNTIME_CONFIGURATION_INVALID",
            f"{field_name} is unsafe for the Paseo command boundary",
        ) from error


def _decode_exact_paseo_alias(
    value: Mapping[str, Any],
    aliases: tuple[str, ...],
    field_name: str,
) -> Any | None:
    """Decode one native field without silently choosing conflicting aliases."""

    populated = [
        value[alias]
        for alias in aliases
        if alias in value and value[alias] is not None and value[alias] != ""
    ]
    if not populated:
        return None
    expected = populated[0]
    if any(
        type(candidate) is not type(expected) or candidate != expected
        for candidate in populated[1:]
    ):
        raise RuntimeGatewayError(
            "RUNTIME_IDENTITY_AMBIGUOUS",
            f"Paseo {field_name} compatibility aliases conflict",
        )
    return expected


def _require_digest(value: object, field_name: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise RuntimeGatewayError(
            "RUNTIME_SUBJECT_INVALID", f"{field_name} must be a SHA-256 digest"
        )
    return value


# ---------------------------------------------------------------------------
# Plan Invalidation: #133 RuntimeGateway readback seam.
#
# A Worker, Formal Review, Repair Verification, or CandidateGate scope audit
# may submit one typed, Artifact-backed report that identifies a newly
# discovered fact and explains why the active Plan Revision cannot safely
# satisfy the affected Ticket as written.  The report is Evidence of plan
# invalidation, not a replacement plan and not authority to widen a
# Candidate.  RuntimeGateway reads it and proves the reporting role's
# capability policy; it does not decide whether or how the Campaign is
# replanned.
# ---------------------------------------------------------------------------


class CapabilityPolicy:
    """The frozen effective capability policy for one reporting role.

    RuntimeGateway must prove the reporting role cannot create or edit Issues,
    change blockers or Campaign membership, activate a Plan Revision, merge,
    expand authority, or invoke global planning.  Inability to prove that
    policy fails closed.  The policy is read-backed from the same frozen
    authority that backs permission approval; it is never inferred from
    prompt text or provider behavior.
    """

    __slots__ = (
        "worker_can_edit_issues",
        "worker_can_edit_blockers",
        "worker_can_edit_campaign_membership",
        "worker_can_activate_plan_revision",
        "worker_can_merge",
        "worker_can_expand_authority",
        "worker_can_invoke_global_planning",
    )

    def __init__(
        self,
        *,
        worker_can_edit_issues: bool,
        worker_can_edit_blockers: bool = False,
        worker_can_edit_campaign_membership: bool = False,
        worker_can_activate_plan_revision: bool = False,
        worker_can_merge: bool = False,
        worker_can_expand_authority: bool = False,
        worker_can_invoke_global_planning: bool = False,
    ) -> None:
        # All flags must be False for a Worker/Reviewer role.  A True value
        # means the read-back proved an authority the role must not have, so
        # report acceptance fails closed.  The fields are stored verbatim so a
        # failed proof can name the exact violation.
        for flag_name, value in {
            "worker_can_edit_issues": worker_can_edit_issues,
            "worker_can_edit_blockers": worker_can_edit_blockers,
            "worker_can_edit_campaign_membership": worker_can_edit_campaign_membership,
            "worker_can_activate_plan_revision": worker_can_activate_plan_revision,
            "worker_can_merge": worker_can_merge,
            "worker_can_expand_authority": worker_can_expand_authority,
            "worker_can_invoke_global_planning": worker_can_invoke_global_planning,
        }.items():
            if type(value) is not bool:
                raise RuntimeGatewayError(
                    "RUNTIME_AUTHORITY_INVALID",
                    f"capability policy {flag_name} must be a boolean",
                )
            setattr(self, flag_name, value)

    @property
    def is_proven(self) -> bool:
        return not any(
            getattr(self, flag_name)
            for flag_name in (
                "worker_can_edit_issues",
                "worker_can_edit_blockers",
                "worker_can_edit_campaign_membership",
                "worker_can_activate_plan_revision",
                "worker_can_merge",
                "worker_can_expand_authority",
                "worker_can_invoke_global_planning",
            )
        )

    def canonical(self) -> dict[str, bool]:
        return {
            "worker_can_edit_issues": self.worker_can_edit_issues,
            "worker_can_edit_blockers": self.worker_can_edit_blockers,
            "worker_can_edit_campaign_membership": self.worker_can_edit_campaign_membership,
            "worker_can_activate_plan_revision": self.worker_can_activate_plan_revision,
            "worker_can_merge": self.worker_can_merge,
            "worker_can_expand_authority": self.worker_can_expand_authority,
            "worker_can_invoke_global_planning": self.worker_can_invoke_global_planning,
        }

    def __eq__(self, other: object) -> bool:
        if type(other) is not CapabilityPolicy:
            return NotImplemented
        return self.canonical() == other.canonical()

    def __hash__(self) -> int:
        return hash(tuple(sorted(self.canonical().items())))

    def __repr__(self) -> str:
        return f"CapabilityPolicy({self.canonical()})"


@dataclass(frozen=True)
class CoordinatorCapabilityProof:
    """Read-back proof for the transient Campaign Coordinator boundary.

    The Coordinator may inspect the bounded repository/Tracker snapshot, but
    it is not a repository writer, Tracker writer, Plan activator, authority
    owner, or delegating workflow driver.  These booleans are intentionally
    explicit: a missing or positive forbidden capability fails closed rather
    than being inferred from a prompt or Runtime Profile.
    """

    subject_digest: str
    repository_read_only: bool
    tracker_read_only: bool
    can_activate_plan_revision: bool
    can_edit_tracker: bool
    can_expand_authority: bool
    delegation_enabled: bool
    can_edit_labels: bool = False
    can_edit_campaign_membership: bool = False
    can_grant_authority: bool = False
    can_merge: bool = False
    can_invoke_global_planning: bool = False

    def __post_init__(self) -> None:
        _require_digest(self.subject_digest, "Coordinator subject digest")
        for field_name in (
            "repository_read_only",
            "tracker_read_only",
            "can_activate_plan_revision",
            "can_edit_tracker",
            "can_expand_authority",
            "delegation_enabled",
            "can_edit_labels",
            "can_edit_campaign_membership",
            "can_grant_authority",
            "can_merge",
            "can_invoke_global_planning",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise RuntimeGatewayError(
                    "COORDINATOR_CAPABILITY_PROOF_INVALID",
                    f"Coordinator capability {field_name} must be boolean",
                )
        if not self.is_proven:
            raise RuntimeGatewayError(
                "COORDINATOR_CAPABILITY_PROOF_FAIL_CLOSED",
                "Coordinator capability readback permits a forbidden effect",
            )

    @property
    def is_proven(self) -> bool:
        return (
            self.repository_read_only
            and self.tracker_read_only
            and not self.can_activate_plan_revision
            and not self.can_edit_tracker
            and not self.can_expand_authority
            and not self.delegation_enabled
            and not self.can_edit_labels
            and not self.can_edit_campaign_membership
            and not self.can_grant_authority
            and not self.can_merge
            and not self.can_invoke_global_planning
        )

    def canonical(self) -> dict[str, Any]:
        return {
            "subject_digest": self.subject_digest,
            "repository_read_only": self.repository_read_only,
            "tracker_read_only": self.tracker_read_only,
            "can_activate_plan_revision": self.can_activate_plan_revision,
            "can_edit_tracker": self.can_edit_tracker,
            "can_expand_authority": self.can_expand_authority,
            "delegation_enabled": self.delegation_enabled,
            "can_edit_labels": self.can_edit_labels,
            "can_edit_campaign_membership": self.can_edit_campaign_membership,
            "can_grant_authority": self.can_grant_authority,
            "can_merge": self.can_merge,
            "can_invoke_global_planning": self.can_invoke_global_planning,
        }

    @property
    def digest(self) -> str:
        return digest_value(self.canonical())


@dataclass(frozen=True)
class CapabilityPolicyProof:
    """The read-back proof that a reporting role's capability policy is closed.

    The proof carries the exact capability policy and the digest of the
    authority record that backed it, so ``inspect`` can name the proven
    boundary without a model transcript.
    """

    capability_policy: CapabilityPolicy
    authority_record_digest: str

    def __post_init__(self) -> None:
        if type(self.capability_policy) is not CapabilityPolicy:
            raise RuntimeGatewayError(
                "RUNTIME_AUTHORITY_INVALID",
                "capability policy proof requires an exact CapabilityPolicy",
            )
        _require_digest(self.authority_record_digest, "authority_record_digest")

    def canonical(self) -> dict[str, Any]:
        return {
            "kind": "gwo.capability-policy-proof.v1",
            "capability_policy": self.capability_policy.canonical(),
            "authority_record_digest": self.authority_record_digest,
        }

    @property
    def digest(self) -> str:
        return digest_value(self.canonical())


@dataclass(frozen=True)
class HumanGateCapabilityProof:
    """The immutable, read-backed capability boundary for the human gate."""

    subject_digest: str
    policy_witness_digest: str
    gateway_configuration_digest: str
    worker_capability_policy_proof: CapabilityPolicyProof
    reviewer_capability_policy_proof: CapabilityPolicyProof
    coordinator_capability_proof: CoordinatorCapabilityProof
    proof_digest: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.subject_digest, "human-gate subject digest"),
            (self.policy_witness_digest, "human-gate Policy Witness digest"),
            (self.gateway_configuration_digest, "human-gate Gateway configuration digest"),
        ):
            _require_digest(value, label)
        if type(self.worker_capability_policy_proof) is not CapabilityPolicyProof:
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED",
                "human-gate Worker capability proof is missing or malformed",
            )
        if type(self.reviewer_capability_policy_proof) is not CapabilityPolicyProof:
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED",
                "human-gate Reviewer capability proof is missing or malformed",
            )
        if type(self.coordinator_capability_proof) is not CoordinatorCapabilityProof:
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED",
                "human-gate Coordinator capability proof is missing or malformed",
            )
        if self.proof_digest is None:
            object.__setattr__(self, "proof_digest", self.digest)
        else:
            _require_digest(self.proof_digest, "human-gate capability proof digest")

    def canonical(self) -> dict[str, Any]:
        return {
            "kind": "gwo.human-gate-capability-proof.v1",
            "subject_digest": self.subject_digest,
            "policy_witness_digest": self.policy_witness_digest,
            "gateway_configuration_digest": self.gateway_configuration_digest,
            "worker_capability_policy_proof": self.worker_capability_policy_proof.canonical(),
            "reviewer_capability_policy_proof": self.reviewer_capability_policy_proof.canonical(),
            "coordinator_capability_proof": self.coordinator_capability_proof.canonical(),
        }

    @property
    def digest(self) -> str:
        return digest_value(self.canonical())


def validate_human_gate_capability(
    proof: object,
    *,
    expected_subject_digest: str | None = None,
    expected_policy_witness_digest: str | None = None,
    expected_gateway_configuration_digest: str | None = None,
) -> HumanGateCapabilityProof:
    """Validate one complete human-gate capability proof without mutation."""

    code = "PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED"
    if type(proof) is not HumanGateCapabilityProof:
        raise RuntimeGatewayError(code, "human-gate capability proof is missing or malformed")
    try:
        if proof.proof_digest != proof.digest:
            raise RuntimeGatewayError(code, "human-gate capability proof digest changed")
        if expected_subject_digest is not None and proof.subject_digest != expected_subject_digest:
            raise RuntimeGatewayError(code, "human-gate capability proof subject changed")
        if (
            expected_policy_witness_digest is not None
            and proof.policy_witness_digest != expected_policy_witness_digest
        ):
            raise RuntimeGatewayError(code, "human-gate Policy Witness digest changed")
        if (
            expected_gateway_configuration_digest is not None
            and proof.gateway_configuration_digest != expected_gateway_configuration_digest
        ):
            raise RuntimeGatewayError(code, "human-gate Gateway configuration digest changed")
        for role, policy_proof in (
            ("Worker", proof.worker_capability_policy_proof),
            ("Reviewer", proof.reviewer_capability_policy_proof),
        ):
            if (
                type(policy_proof) is not CapabilityPolicyProof
                or type(policy_proof.capability_policy) is not CapabilityPolicy
                or not policy_proof.capability_policy.is_proven
            ):
                raise RuntimeGatewayError(
                    code,
                    f"human-gate {role} capability policy permits a forbidden effect",
                )
            _require_digest(policy_proof.authority_record_digest, f"{role} authority record digest")
        coordinator = proof.coordinator_capability_proof
        if (
            type(coordinator) is not CoordinatorCapabilityProof
            or coordinator.subject_digest != proof.subject_digest
            or not coordinator.is_proven
        ):
            raise RuntimeGatewayError(
                code,
                "human-gate Coordinator capability proof permits a forbidden effect",
            )
    except RuntimeGatewayError:
        raise
    except Exception as error:
        raise RuntimeGatewayError(code, "human-gate capability proof is not closed") from error
    return proof


@dataclass(frozen=True, init=False)
class PlanInvalidationReport(tuple, metaclass=_SealedValueMeta):
    """One typed, Artifact-backed Plan Invalidation report.

    It binds the exact Campaign, Plan Revision, Ticket, Work Run, Runtime
    Binding, authority-subtree digest, reporter role, Evidence digest, and a
    stable deduplication identity.  It contains only discovered facts,
    reproducible Evidence, the invalidated Ticket obligation, newly required
    effects/interfaces/state, and current workspace or Candidate identity.
    It cannot carry a replacement plan, Ticket owner, dependency edit, Campaign
    membership change, merge request, or Campaign order edit.
    """

    __slots__ = ()

    repository: str
    campaign_key: str
    plan_revision_digest: str
    ticket_key: str
    work_run_key: str
    runtime_binding_id: str
    authority_subtree_digest: str
    reporter_role: str
    evidence_digest: str
    dedup_identity: str
    invalidated_obligation: str
    required_effects: tuple[str, ...]
    workspace_identity: str
    replacement_planspec: object | None = None
    ticket_owner: object | None = None
    dependency_edit: object | None = None
    campaign_membership: object | None = None
    merge_request: object | None = None
    campaign_order: object | None = None

    def __new__(
        cls,
        *,
        repository: str,
        campaign_key: str,
        plan_revision_digest: str,
        ticket_key: str,
        work_run_key: str,
        runtime_binding_id: str,
        authority_subtree_digest: str,
        reporter_role: str,
        evidence_digest: str,
        dedup_identity: str,
        invalidated_obligation: str,
        required_effects: tuple[str, ...],
        workspace_identity: str,
        replacement_planspec: object | None = None,
        ticket_owner: object | None = None,
        dependency_edit: object | None = None,
        campaign_membership: object | None = None,
        merge_request: object | None = None,
        campaign_order: object | None = None,
    ) -> "PlanInvalidationReport":
        for field_name, value in (
            ("repository", repository),
            ("campaign_key", campaign_key),
            ("ticket_key", ticket_key),
            ("work_run_key", work_run_key),
            ("runtime_binding_id", runtime_binding_id),
            ("reporter_role", reporter_role),
            ("dedup_identity", dedup_identity),
            ("invalidated_obligation", invalidated_obligation),
            ("workspace_identity", workspace_identity),
        ):
            _require_text(value, field_name)
        for digest_field, value in (
            ("plan_revision_digest", plan_revision_digest),
            ("authority_subtree_digest", authority_subtree_digest),
            ("evidence_digest", evidence_digest),
        ):
            _require_digest(value, digest_field)
        if reporter_role not in _PLAN_INVALIDATION_REPORT_ROLES:
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_REPORT_INVALID",
                "reporter_role must be one exact semantic role",
            )
        if (
            type(required_effects) is not tuple
            or any(type(effect) is not str or not effect for effect in required_effects)
        ):
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_REPORT_INVALID",
                "required_effects must be a tuple of non-empty strings",
            )
        forbidden = {
            "replacement_planspec": replacement_planspec,
            "ticket_owner": ticket_owner,
            "dependency_edit": dependency_edit,
            "campaign_membership": campaign_membership,
            "merge_request": merge_request,
            "campaign_order": campaign_order,
        }
        for field_name, value in forbidden.items():
            if value is not None:
                raise RuntimeGatewayError(
                    "PLAN_INVALIDATION_REPORT_INVALID",
                    f"Plan Invalidation report cannot carry a {field_name}",
                )
        return tuple.__new__(
            cls,
            (
                repository,
                campaign_key,
                plan_revision_digest,
                ticket_key,
                work_run_key,
                runtime_binding_id,
                authority_subtree_digest,
                reporter_role,
                evidence_digest,
                dedup_identity,
                invalidated_obligation,
                required_effects,
                workspace_identity,
            ),
        )

    __init__ = _reject_reinitialization

    @property
    def repository(self) -> str:
        return tuple.__getitem__(self, 0)

    @property
    def campaign_key(self) -> str:
        return tuple.__getitem__(self, 1)

    @property
    def plan_revision_digest(self) -> str:
        return tuple.__getitem__(self, 2)

    @property
    def ticket_key(self) -> str:
        return tuple.__getitem__(self, 3)

    @property
    def work_run_key(self) -> str:
        return tuple.__getitem__(self, 4)

    @property
    def runtime_binding_id(self) -> str:
        return tuple.__getitem__(self, 5)

    @property
    def authority_subtree_digest(self) -> str:
        return tuple.__getitem__(self, 6)

    @property
    def reporter_role(self) -> str:
        return tuple.__getitem__(self, 7)

    @property
    def evidence_digest(self) -> str:
        return tuple.__getitem__(self, 8)

    @property
    def dedup_identity(self) -> str:
        return tuple.__getitem__(self, 9)

    @property
    def invalidated_obligation(self) -> str:
        return tuple.__getitem__(self, 10)

    @property
    def required_effects(self) -> tuple[str, ...]:
        return tuple.__getitem__(self, 11)

    @property
    def workspace_identity(self) -> str:
        return tuple.__getitem__(self, 12)

    def canonical(self) -> dict[str, Any]:
        return {
            "kind": "plan_invalidation_report.v1",
            "repository": self.repository,
            "campaign_key": self.campaign_key,
            "plan_revision_digest": self.plan_revision_digest,
            "ticket_key": self.ticket_key,
            "work_run_key": self.work_run_key,
            "runtime_binding_id": self.runtime_binding_id,
            "authority_subtree_digest": self.authority_subtree_digest,
            "reporter_role": self.reporter_role,
            "evidence_digest": self.evidence_digest,
            "dedup_identity": self.dedup_identity,
            "invalidated_obligation": self.invalidated_obligation,
            "required_effects": list(self.required_effects),
            "workspace_identity": self.workspace_identity,
        }

    @property
    def digest(self) -> str:
        return digest_value(self.canonical())


@dataclass(frozen=True)
class PlanInvalidationReceipt:
    """The authoritative read-back receipt for one accepted report.

    It carries the report digest, the stable receipt digest, and the
    capability-policy proof that the reporting role cannot widen its
    authority.  It is not authority to replan; only Coordinator/PlanControl
    may create a successor Plan Revision after human-approved tracker
    readback.
    """

    report_digest: str
    receipt_digest: str
    capability_policy_proof: CapabilityPolicyProof
    observation: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _require_digest(self.report_digest, "report_digest")
        _require_digest(self.receipt_digest, "receipt_digest")
        if type(self.capability_policy_proof) is not CapabilityPolicyProof:
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_RECEIPT_INVALID",
                "capability policy proof must be an exact CapabilityPolicyProof",
            )
        if self.observation is not None:
            legacy_expected = {
                "kind",
                "repository",
                "campaign_key",
                "plan_revision_digest",
                "ticket_key",
                "work_run_key",
                "runtime_binding_id",
                "authority_subtree_digest",
                "reporter_role",
                "report_digest",
                "evidence_digest",
                "dedup_identity",
                "invalidated_obligation",
                "required_effects",
                "workspace_identity",
            }
            expected_with_source_lineage = legacy_expected | {
                "source_evidence_digests"
            }
            observation_keys = (
                set(self.observation) if isinstance(self.observation, Mapping) else set()
            )
            source_digests = (
                self.observation.get("source_evidence_digests")
                if isinstance(self.observation, Mapping)
                and "source_evidence_digests" in self.observation
                else None
            )
            source_lineage_valid = (
                "source_evidence_digests" not in observation_keys
                or (
                    type(source_digests) is list
                    and bool(source_digests)
                    and all(
                        type(digest) is str
                        and _DIGEST_RE.fullmatch(digest) is not None
                        for digest in source_digests
                    )
                    and source_digests == sorted(set(source_digests))
                )
            )
            if (
                not isinstance(self.observation, Mapping)
                or observation_keys
                not in (legacy_expected, expected_with_source_lineage)
                or self.observation.get("kind")
                != "plan_invalidation_observation.v1"
                or self.observation.get("report_digest") != self.report_digest
                or type(self.observation.get("required_effects")) is not list
                or not source_lineage_valid
            ):
                raise RuntimeGatewayError(
                    "PLAN_INVALIDATION_RECEIPT_INVALID",
                    "receipt observation is not the exact closed observation schema",
                )
            object.__setattr__(self, "observation", dict(self.observation))


@dataclass(frozen=True, order=True, init=False)
class RuntimeSelector(tuple, metaclass=_SealedValueMeta):
    """An exact Runtime assignment key; no generic role strings are accepted."""

    __slots__ = ()

    value: str

    def __new__(cls, value: str) -> "RuntimeSelector":
        if type(value) is not str:
            raise RuntimeGatewayError(
                "RUNTIME_SELECTOR_INVALID",
                "Runtime selector must be one exact string",
            )
        if (
            value != "coordinator"
            and value not in _TICKET_ROLES
            and _SPECIALIST_RE.fullmatch(value) is None
        ):
            raise RuntimeGatewayError(
                "RUNTIME_SELECTOR_INVALID",
                f"unknown Runtime selector: {value}",
            )
        return tuple.__new__(cls, (value,))

    __init__ = _reject_reinitialization

    @property
    def value(self) -> str:
        return tuple.__getitem__(self, 0)

    @classmethod
    def coordinator(cls) -> "RuntimeSelector":
        return cls("coordinator")

    @classmethod
    def worker(cls) -> "RuntimeSelector":
        return cls("worker")

    @classmethod
    def ticket(cls, role: str) -> "RuntimeSelector":
        selector = cls(role)
        if selector.is_coordinator:
            raise RuntimeGatewayError(
                "RUNTIME_SELECTOR_INVALID",
                "coordinator is Campaign-scoped and cannot be a Ticket selector",
            )
        return selector

    @property
    def is_coordinator(self) -> bool:
        return self.value == "coordinator"

    @property
    def is_ticket_scoped(self) -> bool:
        return not self.is_coordinator


def _selector(value: RuntimeSelector | str) -> RuntimeSelector:
    return value if isinstance(value, RuntimeSelector) else RuntimeSelector(value)


@dataclass(frozen=True, init=False)
class ProfileMapping(tuple, metaclass=_SealedValueMeta):
    """One required primary Profile and one optional availability fallback."""

    __slots__ = ()

    primary_profile_digest: str
    availability_fallback_profile_digest: str | None = None

    def __new__(
        cls,
        primary_profile_digest: str,
        availability_fallback_profile_digest: str | None = None,
    ) -> "ProfileMapping":
        primary = _require_digest(
            primary_profile_digest, "primary_profile_digest"
        )
        fallback = availability_fallback_profile_digest
        if fallback is not None:
            _require_digest(
                fallback,
                "availability_fallback_profile_digest",
            )
        return tuple.__new__(cls, (primary, fallback))

    __init__ = _reject_reinitialization

    @property
    def primary_profile_digest(self) -> str:
        return tuple.__getitem__(self, 0)

    @property
    def availability_fallback_profile_digest(self) -> str | None:
        return tuple.__getitem__(self, 1)


@dataclass(frozen=True, init=False)
class CampaignStartRuntimeOverrides(tuple, metaclass=_SealedValueMeta):
    """Persisted Campaign-start assignments, never a PlanSpec field."""

    __slots__ = ()

    coordinator: ProfileMapping | None = None
    ticket_overrides: Mapping[tuple[str, str], ProfileMapping] = field(
        default_factory=dict
    )

    def __new__(
        cls,
        coordinator: ProfileMapping | None = None,
        ticket_overrides: Mapping[
            tuple[str, str], ProfileMapping
        ] | None = None,
    ) -> "CampaignStartRuntimeOverrides":
        if coordinator is not None and type(coordinator) is not ProfileMapping:
            raise RuntimeGatewayError(
                "RUNTIME_OVERRIDE_INVALID", "Coordinator override must be a ProfileMapping"
            )
        coordinator = (
            None
            if coordinator is None
            else ProfileMapping(
                coordinator.primary_profile_digest,
                coordinator.availability_fallback_profile_digest,
            )
        )
        normalized: dict[tuple[str, str], ProfileMapping] = {}
        values = {} if ticket_overrides is None else ticket_overrides
        for key, mapping in values.items():
            if type(key) is not tuple or len(key) != 2:
                raise RuntimeGatewayError(
                    "RUNTIME_OVERRIDE_INVALID",
                    "Ticket overrides must use an exact (ticket_key, role) key",
                )
            ticket_key, role = key
            _require_text(ticket_key, "ticket_key")
            if role == "coordinator":
                raise RuntimeGatewayError(
                    "RUNTIME_OVERRIDE_INVALID",
                    "Ticket overrides cannot target the coordinator",
                )
            try:
                selector = RuntimeSelector.ticket(role)
            except RuntimeGatewayError as error:
                raise RuntimeGatewayError(
                    "RUNTIME_OVERRIDE_INVALID",
                    "Ticket overrides require an exact Ticket key and exact Ticket role",
                ) from error
            if type(mapping) is not ProfileMapping:
                raise RuntimeGatewayError(
                    "RUNTIME_OVERRIDE_INVALID", "Ticket override must be a ProfileMapping"
                )
            normalized[(ticket_key, selector.value)] = ProfileMapping(
                mapping.primary_profile_digest,
                mapping.availability_fallback_profile_digest,
            )
        return tuple.__new__(
            cls,
            (
                coordinator,
                MappingProxyType(normalized),
            ),
        )

    __init__ = _reject_reinitialization

    @property
    def coordinator(self) -> ProfileMapping | None:
        return tuple.__getitem__(self, 0)

    @property
    def ticket_overrides(
        self,
    ) -> Mapping[tuple[str, str], ProfileMapping]:
        return tuple.__getitem__(self, 1)

    def __copy__(self) -> "CampaignStartRuntimeOverrides":
        return self

    def __deepcopy__(
        self, _memo: dict[int, Any]
    ) -> "CampaignStartRuntimeOverrides":
        return self

    def canonical(self) -> dict[str, Any]:
        return {
            "coordinator": _mapping_value(self.coordinator),
            "ticket_overrides": [
                {
                    "ticket_key": ticket_key,
                    "role": role,
                    "mapping": _mapping_value(mapping),
                }
                for (ticket_key, role), mapping in sorted(self.ticket_overrides.items())
            ],
        }


def _mapping_value(mapping: ProfileMapping | None) -> dict[str, str | None] | None:
    if mapping is None:
        return None
    return {
        "primary_profile_digest": mapping.primary_profile_digest,
        "availability_fallback_profile_digest": mapping.availability_fallback_profile_digest,
    }


@dataclass(frozen=True)
class RuntimeConfiguration:
    """Host-local configuration; callers never receive this resolved detail."""

    profiles: Mapping[str, RuntimeProfile]
    host_mappings: Mapping[RuntimeSelector | str, ProfileMapping]
    repository_mappings: Mapping[
        str, Mapping[RuntimeSelector | str, ProfileMapping]
    ] = field(default_factory=dict)
    campaign_assertions: Mapping[
        tuple[str, str, str], CampaignStartRuntimeOverrides
    ] = field(default_factory=dict)

    def __post_init__(self) -> None:
        profiles: dict[str, RuntimeProfile] = {}
        for digest, profile in self.profiles.items():
            validated = _validate_runtime_profile_registry_entry(digest, profile)
            profiles[digest] = RuntimeProfile(**validated.canonical())
        object.__setattr__(
            self,
            "profiles",
            MappingProxyType(profiles),
        )
        object.__setattr__(self, "host_mappings", _normalize_mappings(self.host_mappings))
        repositories: dict[
            str, Mapping[RuntimeSelector, ProfileMapping]
        ] = {}
        for repository, mappings in self.repository_mappings.items():
            repositories[_require_text(repository, "repository")] = _normalize_mappings(
                mappings
            )
        object.__setattr__(
            self,
            "repository_mappings",
            MappingProxyType(repositories),
        )
        assertions: dict[
            tuple[str, str, str], CampaignStartRuntimeOverrides
        ] = {}
        for key, assertion in self.campaign_assertions.items():
            if not isinstance(key, tuple) or len(key) != 3:
                raise RuntimeGatewayError(
                    "RUNTIME_CONFIGURATION_INVALID",
                    "Campaign Runtime assertions require an exact repository, Campaign key, and handle",
                )
            repository, campaign_key, campaign_handle = key
            normalized_key = (
                _require_text(repository, "repository"),
                _require_text(campaign_key, "campaign_key"),
                _require_text(campaign_handle, "campaign_handle"),
            )
            if type(assertion) is not CampaignStartRuntimeOverrides:
                raise RuntimeGatewayError(
                    "RUNTIME_CONFIGURATION_INVALID",
                    "Campaign Runtime assertion has an invalid host value",
                )
            assertions[normalized_key] = CampaignStartRuntimeOverrides(
                coordinator=(
                    None
                    if assertion.coordinator is None
                    else ProfileMapping(
                        assertion.coordinator.primary_profile_digest,
                        assertion.coordinator.availability_fallback_profile_digest,
                    )
                ),
                ticket_overrides=dict(assertion.ticket_overrides),
            )
        object.__setattr__(
            self,
            "campaign_assertions",
            MappingProxyType(assertions),
        )


def _normalize_mappings(
    value: Mapping[RuntimeSelector | str, ProfileMapping],
) -> Mapping[RuntimeSelector, ProfileMapping]:
    normalized: dict[RuntimeSelector, ProfileMapping] = {}
    for raw_selector, mapping in value.items():
        selector = _selector(raw_selector)
        if type(mapping) is not ProfileMapping:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID", "Runtime mapping must be a ProfileMapping"
            )
        canonical_selector = RuntimeSelector(selector.value)
        if canonical_selector in normalized:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                "Runtime mappings contain colliding canonical selectors",
            )
        normalized[canonical_selector] = ProfileMapping(
            mapping.primary_profile_digest,
            mapping.availability_fallback_profile_digest,
        )
    return MappingProxyType(normalized)


def _validated_configuration_mapping(value: object) -> ProfileMapping:
    if type(value) is not ProfileMapping:
        raise RuntimeGatewayError(
            "RUNTIME_CONFIGURATION_INVALID",
            "Runtime mapping must remain one exact immutable ProfileMapping",
        )
    try:
        return ProfileMapping(
            value.primary_profile_digest,
            value.availability_fallback_profile_digest,
        )
    except (TypeError, RuntimeGatewayError) as error:
        raise RuntimeGatewayError(
            "RUNTIME_CONFIGURATION_INVALID",
            "Runtime mapping identity is invalid",
        ) from error


def _validate_runtime_profile_registry_entry(
    digest: object,
    profile: object,
) -> RuntimeProfile:
    try:
        normalized_digest = _require_digest(digest, "profile digest")
    except RuntimeGatewayError as error:
        raise RuntimeGatewayError(
            "RUNTIME_CONFIGURATION_INVALID",
            "Profile registry key must be a SHA-256 digest",
        ) from error
    if type(profile) is not RuntimeProfile:
        raise RuntimeGatewayError(
            "RUNTIME_CONFIGURATION_INVALID",
            "Profile registry value must be one exact immutable Runtime Profile",
        )
    try:
        observed_digest = profile.digest
    except Exception as error:
        raise RuntimeGatewayError(
            "RUNTIME_CONFIGURATION_INVALID",
            "Runtime Profile canonical identity is invalid",
        ) from error
    if observed_digest != normalized_digest:
        raise RuntimeGatewayError(
            "RUNTIME_CONFIGURATION_INVALID",
            "Profile registry key must equal the immutable Profile digest",
        )
    # V3 does not attach provider semantics to these values here. It does
    # require a complete usable value before any campaign or provider effect.
    for field_name in ("name", "provider", "model", "thinking", "mode"):
        value = getattr(profile, field_name)
        if type(value) is not str or not value.strip():
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                f"Runtime Profile {field_name} must be a non-empty string",
            )
    if not isinstance(profile.features, Mapping):
        raise RuntimeGatewayError(
            "RUNTIME_CONFIGURATION_INVALID",
            "Runtime Profile features must be an object",
        )
    try:
        if RuntimeProfile(**profile.canonical()).digest != normalized_digest:
            raise ValueError("profile snapshot digest changed")
    except Exception as error:
        raise RuntimeGatewayError(
            "RUNTIME_CONFIGURATION_INVALID",
            "Runtime Profile canonical projection is invalid",
        ) from error
    return profile


def _runtime_configuration_canonical(
    configuration: object,
) -> dict[str, Any]:
    """Project and revalidate the complete host configuration snapshot."""

    if type(configuration) is not RuntimeConfiguration:
        raise RuntimeGatewayError(
            "RUNTIME_CONFIGURATION_INVALID",
            "Runtime configuration must be one exact composed value",
        )
    try:
        profiles = [
            {
                "digest": digest,
                "profile": _validate_runtime_profile_registry_entry(
                    digest, profile
                ).canonical(),
            }
            for digest, profile in sorted(configuration.profiles.items())
        ]

        def mappings(
            values: Mapping[RuntimeSelector, ProfileMapping],
        ) -> list[dict[str, Any]]:
            projected: list[dict[str, Any]] = []
            for selector, mapping in values.items():
                if type(selector) is not RuntimeSelector:
                    raise RuntimeGatewayError(
                        "RUNTIME_CONFIGURATION_INVALID",
                        "Runtime mapping selector is not an exact immutable value",
                    )
                validated = _validated_configuration_mapping(mapping)
                projected.append(
                    {
                        "selector": selector.value,
                        "mapping": _mapping_value(validated),
                    }
                )
            return sorted(projected, key=lambda item: item["selector"])

        host = mappings(configuration.host_mappings)
        repositories = [
            {
                "repository": _require_text(repository, "repository"),
                "mappings": mappings(repository_mappings),
            }
            for repository, repository_mappings in sorted(
                configuration.repository_mappings.items()
            )
        ]
        assertions: list[dict[str, Any]] = []
        for key, assertion in configuration.campaign_assertions.items():
            if (
                type(key) is not tuple
                or len(key) != 3
                or type(assertion) is not CampaignStartRuntimeOverrides
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_CONFIGURATION_INVALID",
                    "Campaign Runtime assertion identity is invalid",
                )
            repository, campaign_key, campaign_handle = key
            assertions.append(
                {
                    "repository": _require_text(repository, "repository"),
                    "campaign_key": _require_text(
                        campaign_key, "campaign_key"
                    ),
                    "campaign_handle": _require_text(
                        campaign_handle, "campaign_handle"
                    ),
                    "overrides": assertion.canonical(),
                }
            )
        assertions.sort(
            key=lambda item: (
                item["repository"],
                item["campaign_key"],
                item["campaign_handle"],
            )
        )
        return {
            "profiles": profiles,
            "host_mappings": host,
            "repository_mappings": repositories,
            "campaign_assertions": assertions,
        }
    except RuntimeGatewayError:
        raise
    except Exception as error:
        raise RuntimeGatewayError(
            "RUNTIME_CONFIGURATION_INVALID",
            "Runtime configuration snapshot is invalid",
        ) from error


def _runtime_configuration_snapshot(
    configuration: object,
) -> RuntimeConfiguration:
    """Capture one deep sealed configuration without retaining caller views."""

    canonical = _runtime_configuration_canonical(configuration)
    try:
        profiles = {
            item["digest"]: RuntimeProfile(**item["profile"])
            for item in canonical["profiles"]
        }

        def mappings(
            values: list[dict[str, Any]],
        ) -> dict[RuntimeSelector, ProfileMapping]:
            return {
                RuntimeSelector(item["selector"]): _mapping_from_value(
                    item["mapping"]
                )
                for item in values
            }

        host_mappings = mappings(canonical["host_mappings"])
        repository_mappings = {
            item["repository"]: mappings(item["mappings"])
            for item in canonical["repository_mappings"]
        }
        campaign_assertions = {
            (
                item["repository"],
                item["campaign_key"],
                item["campaign_handle"],
            ): _campaign_overrides_from_value(item["overrides"])
            for item in canonical["campaign_assertions"]
        }
        snapshot = RuntimeConfiguration(
            profiles=profiles,
            host_mappings=host_mappings,
            repository_mappings=repository_mappings,
            campaign_assertions=campaign_assertions,
        )
        if _runtime_configuration_canonical(snapshot) != canonical:
            raise ValueError("Runtime configuration snapshot changed identity")
        return snapshot
    except Exception as error:
        raise RuntimeGatewayError(
            "RUNTIME_CONFIGURATION_INVALID",
            "Runtime configuration cannot be captured as one deep snapshot",
        ) from error


@dataclass(frozen=True)
class CampaignPlanningSubject:
    """The only pre-Plan Runtime subject; it deliberately has no Plan Revision."""

    repository: str
    campaign_key: str
    campaign_handle: str
    expected_previous_plan_revision_digest: str | None
    snapshot_artifact_digest: str
    policy_witness_digest: str
    planning_request_artifact_digest: str
    stable_action_id: str

    def __post_init__(self) -> None:
        _require_text(self.repository, "repository")
        _require_text(self.campaign_key, "campaign_key")
        _require_text(self.campaign_handle, "campaign_handle")
        _require_text(self.stable_action_id, "stable_action_id")
        if self.expected_previous_plan_revision_digest is not None:
            _require_digest(
                self.expected_previous_plan_revision_digest,
                "expected_previous_plan_revision_digest",
            )
        _require_digest(self.snapshot_artifact_digest, "snapshot_artifact_digest")
        _require_digest(self.policy_witness_digest, "policy_witness_digest")
        _require_digest(
            self.planning_request_artifact_digest,
            "planning_request_artifact_digest",
        )

    @property
    def digest(self) -> str:
        return digest_value(self.canonical())

    @property
    def planning_protocol_request_artifact_digest(self) -> str:
        """The immutable protocol/request Artifact name used in governing ADRs."""

        return self.planning_request_artifact_digest

    @property
    def prompt_binding_digest(self) -> str:
        """Break the Artifact/self-digest cycle while binding every other subject fact."""

        value = self.canonical()
        value["planning_request_artifact_digest"] = None
        return digest_value(value)

    @property
    def authority_digest(self) -> str:
        return self.policy_witness_digest

    def canonical(self) -> dict[str, Any]:
        return {
            "kind": "campaign_planning",
            "repository": self.repository,
            "campaign_key": self.campaign_key,
            "campaign_handle": self.campaign_handle,
            "expected_previous_plan_revision_digest": self.expected_previous_plan_revision_digest,
            "snapshot_artifact_digest": self.snapshot_artifact_digest,
            "policy_witness_digest": self.policy_witness_digest,
            "planning_request_artifact_digest": self.planning_request_artifact_digest,
            "stable_action_id": self.stable_action_id,
        }


def _reject_work_run_purpose_reinitialization(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeGatewayError(
        "RUNTIME_SUBJECT_INVALID",
        "Work Run purpose is a sealed semantic value and cannot be reinitialized",
    )


@dataclass(frozen=True, init=False)
class WorkRunPurpose(tuple, metaclass=_SealedValueMeta):
    """Closed semantic purpose; exact Runtime selectors remain Gateway-private."""

    __slots__ = ()

    kind: str
    policy_id: str | None = None

    def __new__(cls, kind: str, policy_id: str | None = None) -> "WorkRunPurpose":
        if type(kind) is not str or (policy_id is not None and type(policy_id) is not str):
            raise RuntimeGatewayError(
                "RUNTIME_SUBJECT_INVALID",
                "Work Run purpose must use exact built-in text values",
            )
        ordinary = {
            "implementation",
            "terminal_recovery_implementation",
            "formal_review",
            "invalid_review_payload_retry",
        }
        if kind in ordinary and policy_id is None:
            return tuple.__new__(cls, (kind, None))
        if (
            kind == "specialist_review"
            and policy_id is not None
            and _SPECIALIST_RE.fullmatch(f"specialist:{policy_id}") is not None
        ):
            return tuple.__new__(cls, (kind, policy_id))
        raise RuntimeGatewayError(
            "RUNTIME_SUBJECT_INVALID",
            "Work Run purpose is outside the closed semantic union",
        )

    __init__ = _reject_work_run_purpose_reinitialization

    @property
    def kind(self) -> str:
        return tuple.__getitem__(self, 0)

    @property
    def policy_id(self) -> str | None:
        return tuple.__getitem__(self, 1)

    @classmethod
    def implementation(cls) -> "WorkRunPurpose":
        return cls("implementation")

    @classmethod
    def terminal_recovery_implementation(cls) -> "WorkRunPurpose":
        return cls("terminal_recovery_implementation")

    @classmethod
    def formal_review(cls) -> "WorkRunPurpose":
        return cls("formal_review")

    @classmethod
    def invalid_review_payload_retry(cls) -> "WorkRunPurpose":
        return cls("invalid_review_payload_retry")

    @classmethod
    def specialist_review(cls, policy_id: str) -> "WorkRunPurpose":
        return cls("specialist_review", policy_id)

    def canonical(self) -> dict[str, str | None]:
        return {"kind": self.kind, "policy_id": self.policy_id}


def _selector_for_purpose(purpose: WorkRunPurpose) -> RuntimeSelector:
    if type(purpose) is not WorkRunPurpose:
        raise RuntimeGatewayError(
            "RUNTIME_SUBJECT_INVALID",
            "Work Run purpose must be one exact semantic value",
        )
    ordinary = {
        "implementation": "worker",
        "terminal_recovery_implementation": "recovery_worker",
        "formal_review": "review_primary",
        "invalid_review_payload_retry": "review_strong",
    }
    if purpose.kind in ordinary:
        return RuntimeSelector.ticket(ordinary[purpose.kind])
    assert purpose.kind == "specialist_review"
    assert purpose.policy_id is not None
    return RuntimeSelector.ticket(f"specialist:{purpose.policy_id}")


@dataclass(frozen=True)
class WorkRunSubject:
    """The only post-Plan subject accepted by RuntimeGateway."""

    repository: str
    campaign_key: str
    campaign_handle: str
    plan_revision_digest: str
    work_run_key: str
    ticket_key: str
    purpose: WorkRunPurpose
    prompt_artifact_digest: str
    authority_subtree_digest: str
    stable_action_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "repository",
            "campaign_key",
            "campaign_handle",
            "work_run_key",
            "ticket_key",
            "stable_action_id",
        ):
            _require_text(getattr(self, field_name), field_name)
        if type(self.purpose) is not WorkRunPurpose:
            raise RuntimeGatewayError(
                "RUNTIME_SUBJECT_INVALID",
                "Work Run purpose must be one exact semantic value",
            )
        _require_digest(self.plan_revision_digest, "plan_revision_digest")
        _require_digest(self.prompt_artifact_digest, "prompt_artifact_digest")
        _require_digest(self.authority_subtree_digest, "authority_subtree_digest")

    @property
    def digest(self) -> str:
        return digest_value(self.canonical())

    @property
    def prompt_binding_digest(self) -> str:
        value = self.canonical()
        value["prompt_artifact_digest"] = None
        return digest_value(value)

    @property
    def authority_digest(self) -> str:
        return self.authority_subtree_digest

    def canonical(self) -> dict[str, Any]:
        return {
            "kind": "work_run",
            "repository": self.repository,
            "campaign_key": self.campaign_key,
            "campaign_handle": self.campaign_handle,
            "plan_revision_digest": self.plan_revision_digest,
            "work_run_key": self.work_run_key,
            "ticket_key": self.ticket_key,
            "purpose": self.purpose.canonical(),
            "prompt_artifact_digest": self.prompt_artifact_digest,
            "authority_subtree_digest": self.authority_subtree_digest,
            "stable_action_id": self.stable_action_id,
        }


RuntimeSubject = CampaignPlanningSubject | WorkRunSubject


def _resolve_runtime_subject_protocol(
    subject: object,
    prompt: object,
) -> Mapping[str, Any] | None:
    """Bind one exact Runtime subject kind to its only permitted protocol.

    This is deliberately shared by both provider adapters.  A Planning-shaped
    Prompt is not authority by itself: only the exact pre-Plan subject may use
    it, and its input Artifact identities must equal that subject's frozen
    snapshot and Policy Witness.
    """

    if type(subject) not in {CampaignPlanningSubject, WorkRunSubject}:
        raise RuntimeGatewayError(
            "RUNTIME_SUBJECT_INVALID",
            "Runtime protocol resolution accepts exact closed subject types only",
        )
    if (
        type(prompt) is not dict
        or set(prompt) != {
            "schema_version",
            "subject_digest",
            "authority_digest",
            "payload",
        }
        or type(prompt["schema_version"]) is not str
        or prompt["schema_version"] != "gwo.runtime.prompt.v1"
        or type(prompt["subject_digest"]) is not str
        or prompt["subject_digest"] != subject.prompt_binding_digest
        or type(prompt["authority_digest"]) is not str
        or prompt["authority_digest"] != subject.authority_digest
    ):
        raise RuntimeGatewayError(
            "RUNTIME_PROMPT_ARTIFACT_INVALID",
            "Prompt Artifact does not bind its exact Runtime subject and authority",
        )
    planning_schema = planning_output_schema_from_prompt(prompt)
    replanning_schema = replanning_output_schema_from_prompt(prompt)
    if type(subject) is CampaignPlanningSubject:
        if (planning_schema is None) == (replanning_schema is None):
            raise RuntimeGatewayError(
                "RUNTIME_PROMPT_ARTIFACT_INVALID",
                "Campaign Planning requires one exact canonical Planning or invalidation prompt",
            )
        inputs = prompt["payload"]["input_artifacts"]
        if (
            inputs["snapshot_artifact_digest"] != subject.snapshot_artifact_digest
            or inputs["policy_witness_artifact_digest"]
            != subject.policy_witness_digest
        ):
            raise RuntimeGatewayError(
                "RUNTIME_PROMPT_ARTIFACT_INVALID",
                "Campaign Planning prompt changed its frozen input Artifact identities",
            )
        return planning_schema or replanning_schema
    if planning_schema is not None or replanning_schema is not None:
        raise RuntimeGatewayError(
            "RUNTIME_PROMPT_ARTIFACT_INVALID",
            "Plan-Revision Work Run cannot use the exclusive Campaign Planning protocol",
        )
    return None


class RuntimeCommand(str, Enum):
    START = "start"
    RESUME = "resume"
    PARK = "park"
    INTERRUPT = "interrupt"
    FENCE = "fence"
    RETIRE = "retire"


@dataclass(frozen=True)
class PermissionResponse:
    """A closed permission transition with one exact provider request ID."""

    request_id: str
    decision: str

    def __post_init__(self) -> None:
        _require_text(self.request_id, "permission request_id")
        if type(self.decision) is not str or self.decision not in {
            "allow",
            "deny",
        }:
            raise RuntimeGatewayError(
                "RUNTIME_COMMAND_INVALID",
                "permission decision must be exactly allow or deny",
            )


@dataclass(frozen=True)
class PermissionRequired:
    """Opaque, exact descriptor for one Runtime permission needing attention."""

    stable_action_id: str
    request_id: str
    descriptor_digest: str

    def __post_init__(self) -> None:
        _require_text(self.stable_action_id, "permission stable_action_id")
        _require_text(self.request_id, "permission request_id")
        _require_digest(self.descriptor_digest, "permission descriptor_digest")


@dataclass(frozen=True)
class RuntimeRecoveryOutcome:
    """Closed non-scheduling recovery result for one persisted observation."""

    kind: str
    reason: str
    next_check_at: int | None
    observation_digest: str

    def __post_init__(self) -> None:
        if self.kind not in {"wait", "blocked", "decision"}:
            raise RuntimeGatewayError(
                "RUNTIME_RECOVERY_INVALID", "Runtime recovery kind is invalid"
            )
        if self.reason not in {
            "RuntimeProviderUnavailable",
            "RuntimeTransportUnavailable",
            "RuntimeConfigurationInvalid",
            "RuntimeConfigurationRepairRequired",
            "RuntimeObservationUnavailable",
            "RuntimeProviderRecoveryRequired",
        }:
            raise RuntimeGatewayError(
                "RUNTIME_RECOVERY_INVALID", "Runtime recovery reason is invalid"
            )
        allowed_kinds = {
            "RuntimeProviderUnavailable": {"wait", "blocked"},
            "RuntimeTransportUnavailable": {"wait", "blocked"},
            "RuntimeConfigurationInvalid": {"blocked"},
            "RuntimeConfigurationRepairRequired": {"decision"},
            "RuntimeObservationUnavailable": {"decision"},
            "RuntimeProviderRecoveryRequired": {"decision"},
        }
        if self.kind not in allowed_kinds[self.reason]:
            raise RuntimeGatewayError(
                "RUNTIME_RECOVERY_INVALID",
                "Runtime recovery kind does not match its classified reason",
            )
        if self.kind == "wait":
            if (
                type(self.next_check_at) is not int
                or isinstance(self.next_check_at, bool)
                or self.next_check_at < 0
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_RECOVERY_INVALID",
                    "Runtime recovery wait requires a due time",
                )
        elif self.next_check_at is not None:
            raise RuntimeGatewayError(
                "RUNTIME_RECOVERY_INVALID",
                "terminal Runtime recovery outcome cannot carry a due time",
            )
        _require_digest(self.observation_digest, "recovery observation_digest")


@dataclass(frozen=True)
class TerminalBindingEvidence:
    """Opaque proof required before a caller can select a replacement binding."""

    stable_action_id: str
    evidence_digest: str

    def __post_init__(self) -> None:
        _require_text(self.stable_action_id, "terminal evidence stable_action_id")
        _require_digest(self.evidence_digest, "terminal evidence digest")


RuntimeTransition = RuntimeCommand | PermissionResponse


def _planning_transition_effect_boundary(
    command: RuntimeTransition,
) -> str | None:
    """Name only Planning transitions that can authorize semantic work."""

    if command is RuntimeCommand.START:
        return "start"
    if command is RuntimeCommand.RESUME:
        return "resume"
    if type(command) is PermissionResponse and command.decision == "allow":
        return "permission_allow"
    return None


def _runtime_transition_is_structurally_valid(value: object) -> bool:
    """Validate one exact closed transition without invoking foreign code."""

    try:
        if type(value) is RuntimeCommand:
            return True
        return (
            type(value) is PermissionResponse
            and set(vars(value)) == {"request_id", "decision"}
            and type(value.request_id) is str
            and bool(value.request_id)
            and type(value.decision) is str
            and value.decision in {"allow", "deny"}
        )
    except Exception:
        return False


def _transition_name(command: RuntimeTransition) -> str:
    return command.value if type(command) is RuntimeCommand else "permission_response"


def _transition_canonical(command: RuntimeTransition | None) -> Any:
    if command is None:
        return None
    if type(command) is RuntimeCommand:
        return command.value
    return {
        "kind": "permission_response",
        "request_id": command.request_id,
        "decision": command.decision,
    }


def _json_projection(value: Any) -> Any:
    """Project internal tuple-bearing values into the closed JSON domain."""

    if type(value) is dict:
        return {
            key: _json_projection(child) for key, child in value.items()
        }
    if type(value) in {list, tuple}:
        return [_json_projection(child) for child in value]
    return value


@dataclass(frozen=True)
class _RuntimeActionSpec:
    """Private provider input, always rendered from a closed Gateway subject."""

    stable_action_id: str
    subject: RuntimeSubject
    profile: RuntimeProfile
    prompt_artifact: ArtifactRef
    input_artifacts: tuple[ArtifactRef, ...]

    @property
    def subject_digest(self) -> str:
        return self.subject.digest


@dataclass(frozen=True)
class _PermissionRequest:
    """One exact normalized permission request; policy remains #112-owned."""

    request_id: str
    operation_id: str
    resource_id: str
    binding_ref: str
    authority_subtree_digest: str
    stable_action_id: str
    subject_digest: str

    def __post_init__(self) -> None:
        for field_name in (
            "request_id",
            "operation_id",
            "resource_id",
            "binding_ref",
            "stable_action_id",
        ):
            _require_text(getattr(self, field_name), field_name)
        _require_digest(self.authority_subtree_digest, "authority_subtree_digest")
        _require_digest(self.subject_digest, "subject_digest")


def _permission_request_from_value(value: object) -> _PermissionRequest:
    if not isinstance(value, dict) or set(value) != {
        "request_id",
        "operation_id",
        "resource_id",
        "binding_ref",
        "authority_subtree_digest",
        "stable_action_id",
        "subject_digest",
    }:
        raise RuntimeGatewayError(
            "RUNTIME_OBSERVATION_INVALID",
            "completed permission request proof is malformed",
        )
    try:
        return _PermissionRequest(**value)
    except (TypeError, RuntimeGatewayError) as error:
        raise RuntimeGatewayError(
            "RUNTIME_OBSERVATION_INVALID",
            "completed permission request proof is invalid",
        ) from error


@dataclass(frozen=True)
class _FrozenPermissionAuthorityV1:
    """One read-backed, provider-neutral authority view for a Work Run."""

    plan_revision_digest: str
    ticket_key: str
    purpose: WorkRunPurpose
    authority_subtree_digest: str
    policy_witness_digest: str
    grant_pairs: frozenset[tuple[str, str]]
    witness_pairs: frozenset[tuple[str, str]]
    capability_policy: "CapabilityPolicy | None" = None

    def __post_init__(self) -> None:
        for field_name in (
            "plan_revision_digest",
            "authority_subtree_digest",
            "policy_witness_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        _require_text(self.ticket_key, "ticket_key")
        if type(self.purpose) is not WorkRunPurpose:
            raise RuntimeGatewayError(
                "RUNTIME_AUTHORITY_INVALID",
                "frozen permission authority purpose is invalid",
            )
        for field_name in ("grant_pairs", "witness_pairs"):
            pairs = getattr(self, field_name)
            if type(pairs) is not frozenset or not all(
                type(pair) is tuple
                and len(pair) == 2
                and type(pair[0]) is str
                and bool(pair[0])
                and type(pair[1]) is str
                and bool(pair[1])
                for pair in pairs
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_AUTHORITY_INVALID",
                    "frozen permission authority pairs are invalid",
                )
        if self.capability_policy is not None and type(self.capability_policy) is not CapabilityPolicy:
            raise RuntimeGatewayError(
                "RUNTIME_AUTHORITY_INVALID",
                "frozen permission authority capability policy is invalid",
            )


class _AuthorityReadback(Protocol):
    """Private bridge to the published PlanSpec v3 authority projection."""

    def read(
        self, subject: WorkRunSubject
    ) -> _FrozenPermissionAuthorityV1 | None: ...


class _ArtifactAuthorityReadback:
    """Read one Work Run's frozen authority from published V3 Artifacts.

    This is deliberately not a PlanControl import.  RuntimeGateway needs only
    the immutable readback contract: the Plan Revision Artifact identifies the
    authority subtree, and its Policy Witness Artifact independently carries
    the policy-side grants.  Missing, malformed, or cross-bound records are a
    closed no-auto-approval result, not a reason to infer authority.
    """

    def __init__(self, artifacts: ArtifactStore):
        self._artifacts = artifacts

    def read(
        self, subject: WorkRunSubject
    ) -> _FrozenPermissionAuthorityV1 | None:
        try:
            plan = self._artifacts.read_json(subject.plan_revision_digest)
        except RuntimeGatewayError:
            return None
        if (
            type(plan) is not dict
            or plan.get("schema_version") != 3
            or plan.get("repository") != subject.repository
            or type(plan.get("campaign")) is not dict
            or plan["campaign"].get("key") != subject.campaign_key
            or type(plan.get("policy")) is not dict
            or type(plan["policy"].get("digest")) is not str
            or _DIGEST_RE.fullmatch(plan["policy"]["digest"]) is None
            or type(plan.get("work")) is not list
        ):
            return None
        matching_work = [
            item
            for item in plan["work"]
            if type(item) is dict and item.get("key") == subject.ticket_key
        ]
        if len(matching_work) != 1:
            return None
        authority = matching_work[0].get("authority")
        if (
            type(authority) is not dict
            or authority.get("policy_witness_digest") != plan["policy"]["digest"]
        ):
            return None
        role = _authority_role_for_purpose(subject.purpose)
        subtree = authority.get(role)
        if type(subtree) is not dict or set(subtree) != {
            "policy_witness_digest",
            "grants",
            "subtree_digest",
        }:
            return None
        policy_witness_digest = subtree.get("policy_witness_digest")
        subtree_digest = subtree.get("subtree_digest")
        if (
            type(policy_witness_digest) is not str
            or policy_witness_digest != plan["policy"]["digest"]
            or type(subtree_digest) is not str
            or subtree_digest != subject.authority_digest
            or _DIGEST_RE.fullmatch(subtree_digest) is None
        ):
            return None
        grants = _authority_pairs(subtree.get("grants"))
        if grants is None:
            return None
        expected_grants = _PLAN_INVALIDATION_ROLE_GRANTS.get(role)
        if expected_grants is None or grants != expected_grants:
            return None
        if digest_value(
            {
                "policy_witness_digest": policy_witness_digest,
                "grants": [
                    {"operation_id": operation, "resource_id": resource}
                    for operation, resource in sorted(grants)
                ],
            }
        ) != subtree_digest:
            return None
        try:
            witness = self._artifacts.read_json(policy_witness_digest)
        except RuntimeGatewayError:
            return None
        witness_pairs = _policy_witness_pairs(witness, role, policy_witness_digest)
        if witness_pairs is None or witness_pairs != expected_grants:
            return None
        return _FrozenPermissionAuthorityV1(
            plan_revision_digest=subject.plan_revision_digest,
            ticket_key=subject.ticket_key,
            purpose=subject.purpose,
            authority_subtree_digest=subtree_digest,
            policy_witness_digest=policy_witness_digest,
            grant_pairs=grants,
            witness_pairs=witness_pairs,
            capability_policy=CapabilityPolicy(
                worker_can_edit_issues=False,
                worker_can_edit_blockers=False,
                worker_can_edit_campaign_membership=False,
                worker_can_activate_plan_revision=False,
                worker_can_merge=False,
                worker_can_expand_authority=False,
                worker_can_invoke_global_planning=False,
            ),
        )


def _authority_role_for_purpose(purpose: WorkRunPurpose) -> str:
    if purpose.kind == "implementation":
        return "worker"
    if purpose.kind == "terminal_recovery_implementation":
        return "recovery_worker"
    return "review"


def _authority_pairs(value: object) -> frozenset[tuple[str, str]] | None:
    if type(value) is not list:
        return None
    pairs: list[tuple[str, str]] = []
    for grant in value:
        if (
            type(grant) is not dict
            or set(grant) != {"operation_id", "resource_id"}
            or type(grant["operation_id"]) is not str
            or not grant["operation_id"]
            or type(grant["resource_id"]) is not str
            or not grant["resource_id"]
        ):
            return None
        pairs.append((grant["operation_id"], grant["resource_id"]))
    frozen = frozenset(pairs)
    return None if len(frozen) != len(pairs) else frozen


def _policy_witness_pairs(
    value: object,
    role: str,
    expected_digest: str,
) -> frozenset[tuple[str, str]] | None:
    """Validate the digest-addressed Policy Witness side independently."""

    if (
        type(value) is not dict
        or set(value) != {
            "schema_version",
            "ref",
            "authority_grants",
            "allowed_capabilities",
            "exclusive_resources",
        }
        or value.get("schema_version") != 1
        or type(value.get("ref")) is not str
        or not value["ref"]
        or type(value.get("authority_grants")) is not dict
        or set(value["authority_grants"]) != {
            "campaign",
            "worker",
            "recovery_worker",
            "review",
        }
        or digest_value(value) != expected_digest
    ):
        return None
    for field_name in ("allowed_capabilities", "exclusive_resources"):
        facts = value.get(field_name)
        if type(facts) is not list or any(type(fact) is not str or not fact for fact in facts):
            return None
        if len(set(facts)) != len(facts):
            return None
    return _authority_pairs(value["authority_grants"].get(role))


@dataclass(frozen=True)
class _CompletedPermissionResponse:
    """One bounded provider-neutral proof of a completed permission effect."""

    request_id: str
    decision: str
    request: _PermissionRequest
    request_digest: str
    provider_receipt: Mapping[str, Any]
    provider_receipt_digest: str
    stable_action_id: str
    subject_digest: str
    binding_ref: str

    def __post_init__(self) -> None:
        for field_name in (
            "request_id",
            "decision",
            "request_digest",
            "provider_receipt_digest",
            "binding_ref",
            "stable_action_id",
        ):
            _require_text(getattr(self, field_name), field_name)
        _require_digest(self.subject_digest, "subject_digest")
        _require_digest(self.request_digest, "request_digest")
        _require_digest(self.provider_receipt_digest, "provider_receipt_digest")
        if type(self.request) is not _PermissionRequest:
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "completed permission request proof is invalid",
            )
        if not isinstance(self.provider_receipt, Mapping):
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "completed permission provider receipt proof is invalid",
            )
        if self.decision not in {"allow", "deny"}:
            raise RuntimeGatewayError(
                "RUNTIME_COMMAND_INVALID", "permission decision must be exactly allow or deny"
            )


@dataclass(frozen=True)
class _PrepareReceipt:
    stable_action_id: str
    workspace_id: str


@dataclass(frozen=True)
class _CommandReceipt:
    stable_action_id: str
    command: RuntimeTransition


@dataclass(frozen=True, slots=True)
class _RuntimePrepareResultVerdict:
    kind: str
    receipt: _PrepareReceipt | None
    failure: _RuntimeFailure | None


@dataclass(frozen=True, slots=True)
class _RuntimeCommandResultVerdict:
    kind: str
    receipt: _CommandReceipt | None
    failure: _RuntimeFailure | None


def _runtime_prepare_result_is_valid(
    value: object,
    spec: _RuntimeActionSpec,
) -> bool:
    """Exact private prepare union validation; never raises."""

    try:
        expected_stable_action_id = spec.stable_action_id
        if (
            type(expected_stable_action_id) is not str
            or not expected_stable_action_id
        ):
            return False
        if type(value) is _RuntimeFailure:
            return (
                _runtime_failure_is_structurally_valid(value)
                and (
                    value.stable_action_id is None
                    or value.stable_action_id == expected_stable_action_id
                )
                and (
                    value.code != "RUNTIME_PREPARE_ACK_LOST"
                    or value.stable_action_id == expected_stable_action_id
                )
            )
        return (
            type(value) is _PrepareReceipt
            and set(vars(value)) == {"stable_action_id", "workspace_id"}
            and type(value.stable_action_id) is str
            and bool(value.stable_action_id)
            and value.stable_action_id == expected_stable_action_id
            and type(value.workspace_id) is str
            and bool(value.workspace_id)
        )
    except Exception:
        return False


def _runtime_command_result_is_valid(
    value: object,
    stable_action_id: str,
    command: RuntimeTransition,
) -> bool:
    """Exact private command union validation; never raises."""

    try:
        if (
            type(stable_action_id) is not str
            or not stable_action_id
            or not _runtime_transition_is_structurally_valid(command)
        ):
            return False
        if type(value) is _RuntimeFailure:
            return (
                _runtime_failure_is_structurally_valid(value)
                and (
                    value.stable_action_id is None
                    or value.stable_action_id == stable_action_id
                )
                and (
                    value.code != "RUNTIME_COMMAND_ACK_LOST"
                    or value.stable_action_id == stable_action_id
                )
            )
        return (
            type(value) is _CommandReceipt
            and set(vars(value)) == {"stable_action_id", "command"}
            and type(value.stable_action_id) is str
            and bool(value.stable_action_id)
            and value.stable_action_id == stable_action_id
            and _runtime_transition_is_structurally_valid(value.command)
            and value.command == command
        )
    except Exception:
        return False


class _RuntimePrepareResultProtocol:
    """Total classifier for the private prepare result union."""

    @staticmethod
    def validate(
        value: object,
        spec: _RuntimeActionSpec,
    ) -> _RuntimePrepareResultVerdict:
        try:
            valid = _runtime_prepare_result_is_valid(value, spec)
        except Exception:
            valid = False
        if not valid:
            return _RuntimePrepareResultVerdict(
                "invalid",
                None,
                _RuntimeFailure(
                    "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                    "Runtime provider prepare result is invalid",
                ),
            )
        if type(value) is _PrepareReceipt:
            return _RuntimePrepareResultVerdict("receipt", value, None)
        assert type(value) is _RuntimeFailure
        return _RuntimePrepareResultVerdict(
            (
                "recoverable_failure"
                if _runtime_prepare_failure_allows_readback_recovery(
                    value,
                    spec.stable_action_id,
                )
                else "failure"
            ),
            None,
            value,
        )


class _RuntimeCommandResultProtocol:
    """Total classifier for the private command result union."""

    @staticmethod
    def validate(
        value: object,
        stable_action_id: str,
        command: RuntimeTransition,
    ) -> _RuntimeCommandResultVerdict:
        try:
            valid = _runtime_command_result_is_valid(
                value,
                stable_action_id,
                command,
            )
        except Exception:
            valid = False
        if not valid:
            return _RuntimeCommandResultVerdict(
                "invalid",
                None,
                _RuntimeFailure(
                    "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                    "Runtime provider command result is invalid",
                ),
            )
        if type(value) is _CommandReceipt:
            return _RuntimeCommandResultVerdict("receipt", value, None)
        assert type(value) is _RuntimeFailure
        return _RuntimeCommandResultVerdict(
            (
                "recoverable_failure"
                if _runtime_command_failure_allows_readback_recovery(
                    value,
                    stable_action_id,
                )
                else "failure"
            ),
            None,
            value,
        )


def _runtime_prepare_failure_allows_readback_recovery(
    value: object,
    stable_action_id: str,
) -> bool:
    """Admit only explicit same-action post-dispatch prepare ambiguity."""

    return (
        type(value) is _RuntimeFailure
        and _runtime_failure_is_structurally_valid(value)
        and value.code in _RUNTIME_PREPARE_READBACK_RECOVERABLE_FAILURE_CODES
        and value.stable_action_id == stable_action_id
    )


def _runtime_command_failure_allows_readback_recovery(
    value: object,
    stable_action_id: str,
) -> bool:
    """Admit only the closed command ambiguity taxonomy."""

    return (
        type(value) is _RuntimeFailure
        and _runtime_failure_is_structurally_valid(value)
        and value.code
        in {
            "RUNTIME_TRANSPORT_UNAVAILABLE",
            "RUNTIME_COMMAND_ACK_LOST",
            "RUNTIME_EFFECT_AMBIGUOUS",
        }
        and (
            value.stable_action_id is None
            or value.stable_action_id == stable_action_id
        )
    )


@dataclass(frozen=True)
class _PreparedRuntimeObservation:
    """Authoritative staged Workspace readback before any Agent exists."""

    stable_action_id: str
    repository: str
    campaign_key: str
    campaign_handle: str
    plan_revision_digest: str | None
    work_run_key: str | None
    subject_digest: str
    profile_digest: str
    workspace_id: str
    prompt_artifact_digest: str
    fenced: bool
    authority_subtree_digest: str | None
    binding_ref: None = None
    agent_id: None = None
    session_id: None = None
    lifecycle: str = "prepared"
    prompt_staged: bool = True


@dataclass(frozen=True)
class _BoundRuntimeObservation:
    """Authoritative post-start Agent/session/binding readback."""

    stable_action_id: str
    binding_ref: str
    repository: str
    campaign_key: str
    campaign_handle: str
    plan_revision_digest: str | None
    work_run_key: str | None
    subject_digest: str
    profile_digest: str
    agent_id: str
    session_id: str
    workspace_id: str
    prompt_artifact_digest: str
    prompt_accepted: bool
    lifecycle: str
    permission_requests: tuple[_PermissionRequest, ...]
    fenced: bool
    authority_subtree_digest: str | None
    planning_output_artifact_digest: str | None = None
    completed_permission_response: _CompletedPermissionResponse | None = None

    @property
    def output_artifact_digest(self) -> str | None:
        return self.planning_output_artifact_digest


def _completed_permission_evidence_is_bound(
    observation: _BoundRuntimeObservation,
) -> bool:
    """Validate the retained proof against independent observation identity."""

    evidence = observation.completed_permission_response
    return (
        type(evidence) is _CompletedPermissionResponse
        and type(evidence.request) is _PermissionRequest
        and evidence.request_id == evidence.request.request_id
        and evidence.request_digest == digest_value(asdict(evidence.request))
        and evidence.provider_receipt_digest
        == digest_value(dict(evidence.provider_receipt))
        and evidence.stable_action_id == observation.stable_action_id
        and evidence.subject_digest == observation.subject_digest
        and evidence.binding_ref == observation.binding_ref
        and evidence.request.stable_action_id == observation.stable_action_id
        and evidence.request.subject_digest == observation.subject_digest
        and evidence.request.binding_ref == observation.binding_ref
        and evidence.request.authority_subtree_digest
        == observation.authority_subtree_digest
        and evidence.request_id
        not in {request.request_id for request in observation.permission_requests}
    )


def _completed_permission_effect_matches(
    command: PermissionResponse,
    observation: _BoundRuntimeObservation,
) -> bool:
    """Check one exact replay against the complete retained effect proof."""

    evidence = observation.completed_permission_response
    return (
        _completed_permission_evidence_is_bound(observation)
        and evidence is not None
        and evidence.request_id == command.request_id
        and evidence.decision == command.decision
    )

@dataclass(frozen=True)
class _RuntimeEvent:
    cursor: str
    stable_action_id: str
    kind: str


@dataclass(frozen=True)
class _RuntimeEventPage:
    events: tuple[_RuntimeEvent, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class _RuntimeCampaignWake:
    cursor: str
    repository: str
    campaign_key: str
    source: str
    stable_action_id: str
    kind: str


@dataclass(frozen=True)
class _RuntimeCampaignWakePage:
    events: tuple[_RuntimeCampaignWake, ...]
    next_cursor: str | None


def _runtime_event_cursor_value(value: object) -> int | None:
    """Parse only the closed external event-cursor scalar."""

    try:
        if value is None:
            return 0
        if (
            type(value) is not str
            or not 1 <= len(value) <= len(_MAXIMUM_RUNTIME_EVENT_CURSOR_TEXT)
            or not "1" <= value[0] <= "9"
            or any(not "0" <= character <= "9" for character in value[1:])
            or (
                len(value) == len(_MAXIMUM_RUNTIME_EVENT_CURSOR_TEXT)
                and value > _MAXIMUM_RUNTIME_EVENT_CURSOR_TEXT
            )
        ):
            return None
        return int(value)
    except Exception:
        return None


def _runtime_v3_event_journal_counters(
    journal: object,
    *,
    cursor_values: tuple[int, ...],
) -> tuple[int, int] | None:
    """Validate the two required counters that seal one schema-v3 event journal."""

    try:
        if (
            type(journal) is not dict
            or "next_event_cursor" not in journal
            or "event_scan_cursor" not in journal
        ):
            return None
        next_event_cursor = journal["next_event_cursor"]
        event_scan_cursor = journal["event_scan_cursor"]
        if (
            type(next_event_cursor) is not int
            or not 1
            <= next_event_cursor
            <= _MAXIMUM_RUNTIME_EVENT_CURSOR + 1
            or next_event_cursor
            != (cursor_values[-1] + 1 if cursor_values else 1)
            or type(event_scan_cursor) is not int
            or not 0
            <= event_scan_cursor
            <= _MAXIMUM_RUNTIME_EVENT_CURSOR
        ):
            return None
        return next_event_cursor, event_scan_cursor
    except Exception:
        return None


@dataclass(frozen=True, slots=True)
class _RuntimeEventPageVerdict:
    kind: str
    page: _RuntimeEventPage | None
    failure: _RuntimeFailure | None


@dataclass(frozen=True)
class _RuntimeEventSelectionToken:
    """Detached readback identity required by the final event CAS."""

    scan_cursor: int
    eligible_digest: str
    stable_action_id: str
    action_record_digest: str


@dataclass(frozen=True, slots=True)
class _RuntimeArtifactReadProof:
    artifact_digest: str
    byte_length: int


@dataclass(frozen=True, slots=True)
class _RuntimeArtifactEvidence:
    prompt: _RuntimeArtifactReadProof
    inputs: tuple[_RuntimeArtifactReadProof, ...]
    output: _RuntimeOutputArtifactProof | None


@dataclass(frozen=True, slots=True)
class _RuntimeObservationIdentity:
    stable_action_id: str
    repository: str
    campaign_key: str
    campaign_handle: str
    plan_revision_digest: str | None
    work_run_key: str | None
    subject_digest: str
    profile_digest: str
    workspace_id: str
    prompt_artifact_digest: str
    authority_subtree_digest: str
    input_artifact_digests: tuple[str, ...]
    spec_identity_digest: str
    binding_ref: str | None
    agent_id: str | None
    session_id: str | None


@dataclass(frozen=True, slots=True)
class _RuntimeObservationReadToken:
    stable_action_id: str
    identity_digest: str
    selected_record_digest: str
    observation_digest: str | None
    output_artifact_digest: str | None


class _OneShotObservationGate:
    """Private, lifecycle-keyed observe-to-command handoff for both Adapters."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._gates: weakref.WeakKeyDictionary[
            threading.Thread, dict[str, _RuntimeObservationReadToken]
        ] = weakref.WeakKeyDictionary()

    def replace(
        self,
        stable_action_id: str,
        token: _RuntimeObservationReadToken | None,
    ) -> None:
        """Open one fresh gate or clear its predecessor for this lifecycle."""

        with self._lock:
            owner = threading.current_thread()
            gates = self._gates.get(owner)
            if token is None:
                if gates is not None:
                    gates.pop(stable_action_id, None)
                    if not gates:
                        self._gates.pop(owner, None)
                return
            if gates is None:
                gates = {}
                self._gates[owner] = gates
            gates[stable_action_id] = token

    def consume(self, stable_action_id: str) -> _RuntimeObservationReadToken | None:
        """Consume exactly one fresh gate for the current Thread lifecycle."""

        with self._lock:
            owner = threading.current_thread()
            gates = self._gates.get(owner)
            token = None if gates is None else gates.pop(stable_action_id, None)
            if gates is not None and not gates:
                self._gates.pop(owner, None)
            return token

    def clear(self) -> None:
        """An event-only or failed read cannot retain a command capability."""

        with self._lock:
            self._gates.pop(threading.current_thread(), None)


@dataclass(frozen=True, slots=True)
class _RuntimeObservationRead:
    selected_stable_action_id: str
    identity: _RuntimeObservationIdentity | None
    result: (
        _PreparedRuntimeObservation
        | _BoundRuntimeObservation
        | _RuntimeFailure
    )
    artifact_evidence: _RuntimeArtifactEvidence | None
    token: _RuntimeObservationReadToken | None


@dataclass(frozen=True, slots=True)
class _RuntimeObservationVerdict:
    kind: str
    observation: (
        _PreparedRuntimeObservation | _BoundRuntimeObservation | None
    )
    failure: _RuntimeFailure | None
    identity: _RuntimeObservationIdentity | None
    artifact_evidence: _RuntimeArtifactEvidence | None
    token: _RuntimeObservationReadToken | None


_RUNTIME_OBSERVATION_FAILURE_VERDICT_KINDS = frozenset(
    {
        "authoritative_absence",
        "fairness_advance",
        "failure",
        "invalid",
    }
)


def _runtime_exact_nonempty_text(value: object) -> bool:
    return type(value) is str and bool(value)


def _runtime_exact_digest(value: object) -> bool:
    return (
        type(value) is str
        and _DIGEST_RE.fullmatch(value) is not None
    )


def _runtime_exact_optional_digest(value: object) -> bool:
    return value is None or _runtime_exact_digest(value)


def _runtime_exact_optional_text(value: object) -> bool:
    return value is None or _runtime_exact_nonempty_text(value)


def _runtime_exact_nonnegative_integer(value: object) -> bool:
    return (
        type(value) is int
        and 0 <= value <= _MAXIMUM_RUNTIME_SCALAR_INTEGER
    )


def _runtime_exact_boolean(value: object) -> bool:
    return type(value) is bool


def _runtime_exact_true(value: object) -> bool:
    return type(value) is bool and value is True


def _runtime_exact_none(value: object) -> bool:
    return value is None


def _runtime_exact_digest_tuple(value: object) -> bool:
    return (
        type(value) is tuple
        and all(_runtime_exact_digest(item) for item in value)
    )


def _runtime_exact_type(expected: type[object]) -> Callable[[object], bool]:
    return lambda value: type(value) is expected


def _runtime_exact_optional_type(
    expected: type[object],
) -> Callable[[object], bool]:
    return lambda value: value is None or type(value) is expected


def _runtime_exact_text_literal(
    expected: str,
) -> Callable[[object], bool]:
    return lambda value: type(value) is str and value == expected


def _runtime_exact_text_member(
    allowed: frozenset[str],
) -> Callable[[object], bool]:
    return lambda value: type(value) is str and value in allowed


def _runtime_exact_result(value: object) -> bool:
    return type(value) in {
        _PreparedRuntimeObservation,
        _BoundRuntimeObservation,
        _RuntimeFailure,
    }


_RUNTIME_SEALED_SCALAR_SCHEMAS: dict[
    type[object],
    tuple[tuple[str, Callable[[object], bool]], ...],
] = {
    _RuntimeEvent: (
        ("cursor", _runtime_exact_nonempty_text),
        ("stable_action_id", _runtime_exact_nonempty_text),
        ("kind", _runtime_exact_text_member(_RUNTIME_EVENT_KINDS)),
    ),
    _RuntimeEventPage: (
        ("events", _runtime_exact_type(tuple)),
        ("next_cursor", _runtime_exact_optional_text),
    ),
    _RuntimeArtifactReadProof: (
        ("artifact_digest", _runtime_exact_digest),
        ("byte_length", _runtime_exact_nonnegative_integer),
    ),
    _RuntimeOutputArtifactProof: (
        ("artifact_digest", _runtime_exact_digest),
        ("byte_length", _runtime_exact_nonnegative_integer),
        (
            "schema_version",
            _runtime_exact_text_literal(_RUNTIME_OUTPUT_SCHEMA_VERSION),
        ),
        ("subject_digest", _runtime_exact_digest),
        ("stable_action_id", _runtime_exact_nonempty_text),
        ("authority_digest", _runtime_exact_digest),
    ),
    _RuntimeArtifactEvidence: (
        ("prompt", _runtime_exact_type(_RuntimeArtifactReadProof)),
        ("inputs", _runtime_exact_type(tuple)),
        ("output", _runtime_exact_optional_type(_RuntimeOutputArtifactProof)),
    ),
    _RuntimeObservationIdentity: (
        ("stable_action_id", _runtime_exact_nonempty_text),
        ("repository", _runtime_exact_nonempty_text),
        ("campaign_key", _runtime_exact_nonempty_text),
        ("campaign_handle", _runtime_exact_nonempty_text),
        ("plan_revision_digest", _runtime_exact_optional_digest),
        ("work_run_key", _runtime_exact_optional_text),
        ("subject_digest", _runtime_exact_digest),
        ("profile_digest", _runtime_exact_digest),
        ("workspace_id", _runtime_exact_nonempty_text),
        ("prompt_artifact_digest", _runtime_exact_digest),
        ("authority_subtree_digest", _runtime_exact_digest),
        ("input_artifact_digests", _runtime_exact_digest_tuple),
        ("spec_identity_digest", _runtime_exact_digest),
        ("binding_ref", _runtime_exact_optional_text),
        ("agent_id", _runtime_exact_optional_text),
        ("session_id", _runtime_exact_optional_text),
    ),
    _RuntimeObservationReadToken: (
        ("stable_action_id", _runtime_exact_nonempty_text),
        ("identity_digest", _runtime_exact_digest),
        ("selected_record_digest", _runtime_exact_digest),
        ("observation_digest", _runtime_exact_optional_digest),
        ("output_artifact_digest", _runtime_exact_optional_digest),
    ),
    _RuntimeObservationRead: (
        ("selected_stable_action_id", _runtime_exact_nonempty_text),
        (
            "identity",
            _runtime_exact_optional_type(_RuntimeObservationIdentity),
        ),
        ("result", _runtime_exact_result),
        (
            "artifact_evidence",
            _runtime_exact_optional_type(_RuntimeArtifactEvidence),
        ),
        (
            "token",
            _runtime_exact_optional_type(_RuntimeObservationReadToken),
        ),
    ),
    _PreparedRuntimeObservation: (
        ("stable_action_id", _runtime_exact_nonempty_text),
        ("repository", _runtime_exact_nonempty_text),
        ("campaign_key", _runtime_exact_nonempty_text),
        ("campaign_handle", _runtime_exact_nonempty_text),
        ("plan_revision_digest", _runtime_exact_optional_digest),
        ("work_run_key", _runtime_exact_optional_text),
        ("subject_digest", _runtime_exact_digest),
        ("profile_digest", _runtime_exact_digest),
        ("workspace_id", _runtime_exact_nonempty_text),
        ("prompt_artifact_digest", _runtime_exact_digest),
        ("fenced", _runtime_exact_boolean),
        ("authority_subtree_digest", _runtime_exact_digest),
        ("binding_ref", _runtime_exact_none),
        ("agent_id", _runtime_exact_none),
        ("session_id", _runtime_exact_none),
        ("lifecycle", _runtime_exact_text_literal("prepared")),
        ("prompt_staged", _runtime_exact_true),
    ),
    _BoundRuntimeObservation: (
        ("stable_action_id", _runtime_exact_nonempty_text),
        ("binding_ref", _runtime_exact_nonempty_text),
        ("repository", _runtime_exact_nonempty_text),
        ("campaign_key", _runtime_exact_nonempty_text),
        ("campaign_handle", _runtime_exact_nonempty_text),
        ("plan_revision_digest", _runtime_exact_optional_digest),
        ("work_run_key", _runtime_exact_optional_text),
        ("subject_digest", _runtime_exact_digest),
        ("profile_digest", _runtime_exact_digest),
        ("agent_id", _runtime_exact_nonempty_text),
        ("session_id", _runtime_exact_nonempty_text),
        ("workspace_id", _runtime_exact_nonempty_text),
        ("prompt_artifact_digest", _runtime_exact_digest),
        ("prompt_accepted", _runtime_exact_true),
        ("lifecycle", _runtime_exact_text_member(_BOUND_LIFECYCLES)),
        ("permission_requests", _runtime_exact_type(tuple)),
        ("fenced", _runtime_exact_boolean),
        ("authority_subtree_digest", _runtime_exact_digest),
        (
            "planning_output_artifact_digest",
            _runtime_exact_optional_digest,
        ),
        (
            "completed_permission_response",
            _runtime_exact_optional_type(_CompletedPermissionResponse),
        ),
    ),
}


def _runtime_sealed_scalar_fields_are_valid(value: object) -> bool:
    """Validate one sealed value before any equality, membership, or hashing."""

    try:
        value_type = type(value)
        schema = _RUNTIME_SEALED_SCALAR_SCHEMAS.get(value_type)
        if schema is None:
            return False
        expected_names = tuple(name for name, _validator in schema)
        try:
            instance_fields = object.__getattribute__(value, "__dict__")
        except AttributeError:
            instance_fields = None
        if instance_fields is not None:
            if (
                type(instance_fields) is not dict
                or len(instance_fields) != len(expected_names)
                or not all(type(name) is str for name in instance_fields)
                or tuple(sorted(instance_fields)) != tuple(sorted(expected_names))
            ):
                return False
        for name, validator in schema:
            field_value = object.__getattribute__(value, name)
            if not validator(field_value):
                return False
        return True
    except Exception:
        return False


_PERMISSION_REQUEST_FIELDS = frozenset(
    {
        "request_id",
        "operation_id",
        "resource_id",
        "binding_ref",
        "authority_subtree_digest",
        "stable_action_id",
        "subject_digest",
    }
)
_COMPLETED_PERMISSION_FIELDS = frozenset(
    {
        "request_id",
        "decision",
        "request",
        "request_digest",
        "provider_receipt",
        "provider_receipt_digest",
        "stable_action_id",
        "subject_digest",
        "binding_ref",
    }
)


def _runtime_permission_request_is_structurally_valid(value: object) -> bool:
    try:
        return (
            type(value) is _PermissionRequest
            and frozenset(vars(value)) == _PERMISSION_REQUEST_FIELDS
            and all(
                type(part) is str and bool(part)
                for part in (
                    value.request_id,
                    value.operation_id,
                    value.resource_id,
                    value.binding_ref,
                    value.stable_action_id,
                )
            )
            and type(value.authority_subtree_digest) is str
            and _DIGEST_RE.fullmatch(value.authority_subtree_digest) is not None
            and type(value.subject_digest) is str
            and _DIGEST_RE.fullmatch(value.subject_digest) is not None
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _paseo_permission_operation_id(tool: str, name: str) -> str:
    return "paseo/0.2.3:operation:" + digest_value(
        {
            "provider": "paseo/0.2.3",
            "tool": tool,
            "name": name,
        }
    )


def _paseo_permission_receipt_is_bound(
    receipt: object,
    *,
    request: _PermissionRequest,
    decision: str,
    agent_id: str,
) -> bool:
    """Validate the normalized durable form of one Paseo permit receipt."""

    try:
        return (
            type(receipt) is dict
            and set(receipt)
            == {
                "requestId",
                "agentId",
                "agentShortId",
                "name",
                "result",
            }
            and all(
                type(part) is str and bool(part)
                for part in receipt.values()
            )
            and type(request) is _PermissionRequest
            and decision in {"allow", "deny"}
            and request.binding_ref == f"paseo:{agent_id}"
            and receipt["requestId"] == request.request_id[:8]
            and receipt["agentId"] == agent_id
            and receipt["agentShortId"] == agent_id[:7]
            and receipt["name"] == request.operation_id
            and receipt["result"]
            == ("allowed" if decision == "allow" else "denied")
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _runtime_completed_permission_is_structurally_valid(
    value: object,
    observation: _BoundRuntimeObservation,
) -> bool:
    try:
        if (
            type(value) is not _CompletedPermissionResponse
            or frozenset(vars(value)) != _COMPLETED_PERMISSION_FIELDS
            or not _runtime_permission_request_is_structurally_valid(
                value.request
            )
            or type(value.provider_receipt) is not dict
            or type(value.decision) is not str
            or value.decision not in {"allow", "deny"}
            or not all(
                type(part) is str and bool(part)
                for part in (
                    value.request_id,
                    value.request_digest,
                    value.provider_receipt_digest,
                    value.stable_action_id,
                    value.subject_digest,
                    value.binding_ref,
                )
            )
            or _DIGEST_RE.fullmatch(value.request_digest) is None
            or _DIGEST_RE.fullmatch(value.provider_receipt_digest) is None
            or value.request_digest != digest_value(asdict(value.request))
            or value.provider_receipt_digest
            != digest_value(value.provider_receipt)
            or value.request_id != value.request.request_id
            or value.stable_action_id != observation.stable_action_id
            or value.subject_digest != observation.subject_digest
            or value.binding_ref != observation.binding_ref
            or value.request.stable_action_id != observation.stable_action_id
            or value.request.subject_digest != observation.subject_digest
            or value.request.binding_ref != observation.binding_ref
            or value.request.authority_subtree_digest
            != observation.authority_subtree_digest
        ):
            return False
        receipt = value.provider_receipt
        if frozenset(receipt) == {
            "adapter",
            "request_id",
            "decision",
            "binding_ref",
        }:
            return (
                all(type(part) is str and bool(part) for part in receipt.values())
                and receipt["adapter"] == "in-memory.v1"
                and receipt["request_id"] == value.request_id
                and receipt["decision"] == value.decision
                and receipt["binding_ref"] == value.binding_ref
            )
        if frozenset(receipt) == {
            "requestId",
            "agentId",
            "agentShortId",
            "name",
            "result",
        }:
            agent_id = value.binding_ref.removeprefix("paseo:")
            return (
                value.binding_ref.startswith("paseo:")
                and bool(agent_id)
                and _paseo_permission_receipt_is_bound(
                    receipt,
                    request=value.request,
                    decision=value.decision,
                    agent_id=agent_id,
                )
            )
        return False
    except (
        AttributeError,
        CanonicalJsonError,
        RuntimeGatewayError,
        TypeError,
        ValueError,
    ):
        return False


def _runtime_observation_is_structurally_valid(observation: object) -> bool:
    """Validate every provider-neutral field without I/O or durable mutation."""

    if not _runtime_sealed_scalar_fields_are_valid(observation):
        return False
    try:
        plan_and_work_are_closed = (
            observation.plan_revision_digest is None
            and observation.work_run_key is None
        ) or (
            observation.plan_revision_digest is not None
            and observation.work_run_key is not None
        )
        if not plan_and_work_are_closed:
            return False
        if type(observation) is _PreparedRuntimeObservation:
            return True

        assert type(observation) is _BoundRuntimeObservation
        permission_requests = observation.permission_requests
        if not all(
            _runtime_permission_request_is_structurally_valid(request)
            and request.stable_action_id == observation.stable_action_id
            and request.subject_digest == observation.subject_digest
            and request.binding_ref == observation.binding_ref
            and request.authority_subtree_digest
            == observation.authority_subtree_digest
            for request in permission_requests
        ):
            return False
        permission_ids = [request.request_id for request in permission_requests]
        output_digest = observation.planning_output_artifact_digest
        output_is_closed = (
            output_digest is None
            if observation.lifecycle in {"running", "parked"}
            else (
                output_digest is not None
                if observation.lifecycle == "completed"
                else True
            )
        )
        completed_is_closed = (
            observation.completed_permission_response is None
            or _runtime_completed_permission_is_structurally_valid(
                observation.completed_permission_response,
                observation,
            )
        )
        return (
            len(permission_ids) == len(set(permission_ids))
            and output_is_closed
            and completed_is_closed
        )
    except Exception:
        return False


def _runtime_observation_matches(
    observation: object,
    *,
    subject: RuntimeSubject,
    profile_digest: object,
    prompt_artifact_digest: object,
) -> bool:
    """Bind one complete observation to its independently captured identity."""

    if (
        type(subject) not in {CampaignPlanningSubject, WorkRunSubject}
        or not _runtime_observation_is_structurally_valid(observation)
    ):
        return False
    expected_plan = (
        None
        if type(subject) is CampaignPlanningSubject
        else subject.plan_revision_digest
    )
    expected_work = (
        None if type(subject) is CampaignPlanningSubject else subject.work_run_key
    )
    return (
        observation.stable_action_id == subject.stable_action_id
        and observation.repository == subject.repository
        and observation.campaign_key == subject.campaign_key
        and observation.campaign_handle == subject.campaign_handle
        and observation.plan_revision_digest == expected_plan
        and observation.work_run_key == expected_work
        and observation.authority_subtree_digest == subject.authority_digest
        and observation.subject_digest == subject.digest
        and observation.profile_digest == profile_digest
        and observation.prompt_artifact_digest == prompt_artifact_digest
    )


def _runtime_event_observation_state(
    observation: object,
    selected_stable_action_id: str | None = None,
) -> tuple[dict[str, Any], str] | None:
    """Derive a wake state only from a structurally valid observation."""

    if (
        not _runtime_observation_is_structurally_valid(observation)
        or (
            selected_stable_action_id is not None
            and observation.stable_action_id != selected_stable_action_id
        )
    ):
        return None
    if type(observation) is _PreparedRuntimeObservation:
        permission_requests: tuple[_PermissionRequest, ...] = ()
    else:
        assert type(observation) is _BoundRuntimeObservation
        permission_requests = observation.permission_requests

    lifecycle = observation.lifecycle
    return (
        {
            "stable_action_id": observation.stable_action_id,
            "subject_digest": observation.subject_digest,
            "lifecycle": lifecycle,
            "fenced": observation.fenced,
            "permission_requests": [
                asdict(request) for request in permission_requests
            ],
            "output_artifact_digest": getattr(
                observation, "planning_output_artifact_digest", None
            ),
        },
        lifecycle,
    )


_RUNTIME_PROVIDER_FAILURE_CODES = frozenset(
    {
        "RUNTIME_ACTION_ABSENT",
        "RUNTIME_ACTION_IDENTITY_MISMATCH",
        "RUNTIME_ACTION_STATE_CHANGED",
        "RUNTIME_ACTION_STATE_MISSING",
        "RUNTIME_ACTION_UNKNOWN",
        "RUNTIME_ARTIFACT_DIGEST_MISMATCH",
        "RUNTIME_ARTIFACT_INVALID",
        "RUNTIME_ARTIFACT_MISSING",
        "RUNTIME_ARTIFACT_TOO_LARGE",
        "RUNTIME_ARTIFACT_UNAVAILABLE",
        "RUNTIME_BINDING_MISSING",
        "RUNTIME_BINDING_UNKNOWN",
        "RUNTIME_CAMPAIGN_IDENTITY_MISMATCH",
        "RUNTIME_CAMPAIGN_UNKNOWN",
        "RUNTIME_COMMAND_ACK_LOST",
        "RUNTIME_COMMAND_INVALID",
        "RUNTIME_CONFIGURATION_INVALID",
        "RUNTIME_EFFECT_AMBIGUOUS",
        "RUNTIME_EVENT_CURSOR_EXHAUSTED",
        "RUNTIME_EVENT_CURSOR_INVALID",
        "RUNTIME_IDENTITY_AMBIGUOUS",
        "RUNTIME_LIFECYCLE_UNKNOWN",
        "RUNTIME_MATERIALIZATION_PENDING",
        "RUNTIME_OBSERVATION_INVALID",
        "RUNTIME_OUTPUT_ARTIFACT_INVALID",
        "RUNTIME_OUTPUT_ARTIFACT_MISSING",
        "RUNTIME_OVERRIDE_INVALID",
        "RUNTIME_PERMISSION_REQUEST_UNKNOWN",
        "RUNTIME_PREFLIGHT_IDENTITY_MISMATCH",
        "RUNTIME_PREFLIGHT_INVALID",
        "RUNTIME_PREFLIGHT_REQUIRED",
        "RUNTIME_PREFLIGHT_SUBJECT_INVALID",
        "RUNTIME_PREPARE_ACK_LOST",
        "RUNTIME_PROMPT_ARTIFACT_INVALID",
        "RUNTIME_PROVIDER_COMMAND_FAILED",
        "RUNTIME_PROVIDER_UNAVAILABLE",
        "RUNTIME_PROVIDER_PROTOCOL_INVALID",
        "RUNTIME_RESULT_PROVENANCE_INVALID",
        "RUNTIME_SELECTOR_INVALID",
        "RUNTIME_STORE_BUSY",
        "RUNTIME_STORE_INVALID",
        "RUNTIME_SUBJECT_INVALID",
        "RUNTIME_TRANSPORT_UNAVAILABLE",
        "RUNTIME_VENDOR_ARGUMENT_INVALID",
        "RUNTIME_WORKSPACE_UNSAFE",
    }
)

_RUNTIME_ACTION_BOUND_FAILURE_CODES = frozenset(
    {
        "RUNTIME_ACTION_ABSENT",
        "RUNTIME_BINDING_MISSING",
        "RUNTIME_MATERIALIZATION_PENDING",
        "RUNTIME_PREPARE_ACK_LOST",
        "RUNTIME_COMMAND_ACK_LOST",
        "RUNTIME_EFFECT_AMBIGUOUS",
    }
)

_RUNTIME_PREPARE_READBACK_RECOVERABLE_FAILURE_CODES = frozenset(
    {
        "RUNTIME_PREPARE_ACK_LOST",
        "RUNTIME_EFFECT_AMBIGUOUS",
    }
)


def _runtime_failure_is_structurally_valid(value: object) -> bool:
    """Validate the exact closed Provider-failure value schema."""

    try:
        if (
            type(value) is not _RuntimeFailure
            or set(vars(value))
            != {
                "code",
                "detail",
                "stable_action_id",
                "authoritative_absence",
                "observation_id",
            }
            or type(value.code) is not str
            or value.code not in _RUNTIME_PROVIDER_FAILURE_CODES
            or type(value.detail) is not str
            or not value.detail
            or (
                value.stable_action_id is not None
                and (
                    type(value.stable_action_id) is not str
                    or not value.stable_action_id
                )
            )
            or type(value.authoritative_absence) is not bool
            or (
                value.observation_id is not None
                and (
                    type(value.observation_id) is not str
                    or not value.observation_id
                )
            )
            or (
                value.code in _RUNTIME_ACTION_BOUND_FAILURE_CODES
                and value.stable_action_id is None
            )
        ):
            return False
        if value.code == "RUNTIME_ACTION_ABSENT":
            return (
                value.detail == "authoritative stable-action absence"
                and value.stable_action_id is not None
                and value.authoritative_absence is True
            )
        return (
            value.authoritative_absence is False
            and (
                value.code != "RUNTIME_PROVIDER_UNAVAILABLE"
                or value.observation_id is not None
            )
        )
    except Exception:
        return False


def _runtime_failure_is_authoritative_absence(
    value: object, stable_action_id: str
) -> bool:
    return (
        _runtime_failure_is_structurally_valid(value)
        and value.code == "RUNTIME_ACTION_ABSENT"
        and value.detail == "authoritative stable-action absence"
        and value.stable_action_id == stable_action_id
        and value.authoritative_absence is True
    )


def _runtime_event_page_is_structurally_valid(
    value: object,
    *,
    after_cursor: object = None,
) -> bool:
    """Validate the exact closed event-page schema without raising."""

    try:
        after_value = _runtime_event_cursor_value(after_cursor)
        if (
            after_value is None
            or not _runtime_sealed_scalar_fields_are_valid(value)
            or len(value.events) > _MAXIMUM_RUNTIME_EVENT_PAGE
        ):
            return False
        previous = after_value
        for event in value.events:
            cursor_value = (
                _runtime_event_cursor_value(event.cursor)
                if _runtime_sealed_scalar_fields_are_valid(event)
                else None
            )
            if (
                cursor_value is None
                or cursor_value <= previous
            ):
                return False
            previous = cursor_value
        if not value.events:
            return value.next_cursor == after_cursor
        return value.next_cursor == value.events[-1].cursor
    except Exception:
        return False


class _RuntimeEventPageProtocol:
    """Total classifier for one closed Adapter event result."""

    @staticmethod
    def validate(
        value: object,
        *,
        after_cursor: object = None,
    ) -> _RuntimeEventPageVerdict:
        try:
            if _runtime_event_cursor_value(after_cursor) is None:
                return _RuntimeEventPageVerdict(
                    "invalid",
                    None,
                    _RuntimeFailure(
                        "RUNTIME_EVENT_CURSOR_INVALID",
                        "Runtime event request cursor is malformed",
                    ),
                )
            if type(value) is _RuntimeFailure:
                if _runtime_failure_is_structurally_valid(value):
                    return _RuntimeEventPageVerdict(
                        (
                            "transient_failure"
                            if value.code
                            == "RUNTIME_TRANSPORT_UNAVAILABLE"
                            else "failure"
                        ),
                        None,
                        value,
                    )
                return _RuntimeEventPageVerdict(
                    "invalid",
                    None,
                    _RuntimeFailure(
                        "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                        "Runtime provider event failure is malformed",
                    ),
                )
            if _runtime_event_page_is_structurally_valid(
                value,
                after_cursor=after_cursor,
            ):
                assert type(value) is _RuntimeEventPage
                return _RuntimeEventPageVerdict("page", value, None)
        except Exception:
            pass
        return _RuntimeEventPageVerdict(
            "invalid",
            None,
            _RuntimeFailure(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "Runtime provider event page is malformed",
            ),
        )


def _runtime_observation_identity_digest(
    identity: _RuntimeObservationIdentity,
) -> str:
    return digest_value(_json_projection(asdict(identity)))


def _runtime_observation_identity_is_structurally_valid(
    identity: object,
) -> bool:
    try:
        if not _runtime_sealed_scalar_fields_are_valid(identity):
            return False
        plan_and_work_are_closed = (
            identity.plan_revision_digest is None
            and identity.work_run_key is None
        ) or (
            identity.plan_revision_digest is not None
            and identity.work_run_key is not None
        )
        binding_fields = (
            identity.binding_ref,
            identity.agent_id,
            identity.session_id,
        )
        binding_is_closed = all(value is None for value in binding_fields) or all(
            type(value) is str and bool(value) for value in binding_fields
        )
        return plan_and_work_are_closed and binding_is_closed
    except Exception:
        return False


def _runtime_observation_matches_identity(
    observation: object,
    identity: _RuntimeObservationIdentity,
) -> bool:
    if (
        not _runtime_observation_is_structurally_valid(observation)
        or not _runtime_observation_identity_is_structurally_valid(identity)
    ):
        return False
    expected_binding = (
        (None, None, None)
        if type(observation) is _PreparedRuntimeObservation
        else (
            observation.binding_ref,
            observation.agent_id,
            observation.session_id,
        )
    )
    return (
        observation.stable_action_id == identity.stable_action_id
        and observation.repository == identity.repository
        and observation.campaign_key == identity.campaign_key
        and observation.campaign_handle == identity.campaign_handle
        and observation.plan_revision_digest == identity.plan_revision_digest
        and observation.work_run_key == identity.work_run_key
        and observation.subject_digest == identity.subject_digest
        and observation.profile_digest == identity.profile_digest
        and observation.workspace_id == identity.workspace_id
        and observation.prompt_artifact_digest
        == identity.prompt_artifact_digest
        and observation.authority_subtree_digest
        == identity.authority_subtree_digest
        and expected_binding
        == (
            identity.binding_ref,
            identity.agent_id,
            identity.session_id,
        )
    )


def _runtime_artifact_evidence(
    artifacts: ArtifactStore,
    identity: _RuntimeObservationIdentity,
    observation: _PreparedRuntimeObservation | _BoundRuntimeObservation,
) -> _RuntimeArtifactEvidence:
    prompt = artifacts.get(identity.prompt_artifact_digest)
    inputs = tuple(
        artifacts.get(digest) for digest in identity.input_artifact_digests
    )
    output: _RuntimeOutputArtifactProof | None = None
    output_digest = (
        None
        if type(observation) is _PreparedRuntimeObservation
        else observation.output_artifact_digest
    )
    if output_digest is not None:
        output = artifacts.prove_runtime_output(
            output_digest,
            subject_digest=identity.subject_digest,
            stable_action_id=identity.stable_action_id,
            authority_digest=identity.authority_subtree_digest,
        )
    return _RuntimeArtifactEvidence(
        prompt=_RuntimeArtifactReadProof(
            prompt.digest,
            prompt.byte_length,
        ),
        inputs=tuple(
            _RuntimeArtifactReadProof(reference.digest, reference.byte_length)
            for reference in inputs
        ),
        output=output,
    )


def _runtime_artifact_evidence_is_valid(
    evidence: object,
    identity: _RuntimeObservationIdentity,
    observation: _PreparedRuntimeObservation | _BoundRuntimeObservation,
    *,
    maximum_artifact_bytes: object,
) -> bool:
    try:
        if (
            type(maximum_artifact_bytes) is not int
            or not 1
            <= maximum_artifact_bytes
            <= _MAXIMUM_RUNTIME_SCALAR_INTEGER
            or not _runtime_observation_identity_is_structurally_valid(
                identity
            )
            or not _runtime_observation_is_structurally_valid(observation)
            or not _runtime_sealed_scalar_fields_are_valid(evidence)
            or not _runtime_sealed_scalar_fields_are_valid(evidence.prompt)
            or not all(
                _runtime_sealed_scalar_fields_are_valid(item)
                for item in evidence.inputs
            )
            or any(
                item.byte_length > maximum_artifact_bytes
                for item in (evidence.prompt, *evidence.inputs)
            )
            or evidence.prompt.artifact_digest
            != identity.prompt_artifact_digest
            or tuple(item.artifact_digest for item in evidence.inputs)
            != identity.input_artifact_digests
        ):
            return False
        output_digest = (
            None
            if type(observation) is _PreparedRuntimeObservation
            else observation.output_artifact_digest
        )
        if output_digest is None:
            return evidence.output is None
        proof = evidence.output
        return (
            _runtime_sealed_scalar_fields_are_valid(proof)
            and proof.byte_length <= maximum_artifact_bytes
            and proof.artifact_digest == output_digest
            and proof.subject_digest == identity.subject_digest
            and proof.stable_action_id == identity.stable_action_id
            and proof.authority_digest == identity.authority_subtree_digest
        )
    except Exception:
        return False


def _runtime_observation_identity_matches_expectation(
    identity: object,
    *,
    selected_stable_action_id: str,
    expected_subject: RuntimeSubject | None,
    expected_profile_digest: object,
    expected_prompt_artifact_digest: object,
    prior_record: Mapping[str, Any] | None,
) -> bool:
    """Pure total comparison against requested and already-pinned identity."""

    try:
        if (
            not _runtime_observation_identity_is_structurally_valid(identity)
            or identity.stable_action_id != selected_stable_action_id
        ):
            return False
        if expected_subject is not None and (
            type(expected_subject)
            not in {CampaignPlanningSubject, WorkRunSubject}
            or identity.repository != expected_subject.repository
            or identity.campaign_key != expected_subject.campaign_key
            or identity.campaign_handle != expected_subject.campaign_handle
            or identity.plan_revision_digest
            != (
                None
                if type(expected_subject) is CampaignPlanningSubject
                else expected_subject.plan_revision_digest
            )
            or identity.work_run_key
            != (
                None
                if type(expected_subject) is CampaignPlanningSubject
                else expected_subject.work_run_key
            )
            or identity.subject_digest != expected_subject.digest
            or identity.authority_subtree_digest
            != expected_subject.authority_digest
            or identity.profile_digest != expected_profile_digest
            or identity.prompt_artifact_digest
            != expected_prompt_artifact_digest
        ):
            return False
        if prior_record is not None and (
            (
                prior_record.get("workspace_id") is not None
                and prior_record.get("workspace_id") != identity.workspace_id
            )
            or (
                prior_record.get("binding_ref") is not None
                and prior_record.get("binding_ref") != identity.binding_ref
            )
            or (
                prior_record.get("agent_id") is not None
                and prior_record.get("agent_id") != identity.agent_id
            )
            or (
                prior_record.get("session_id") is not None
                and prior_record.get("session_id") != identity.session_id
            )
            or (
                prior_record.get("spec_identity_digest") is not None
                and prior_record.get("spec_identity_digest")
                != identity.spec_identity_digest
            )
        ):
            return False
        return True
    except (AttributeError, TypeError, ValueError):
        return False


def _runtime_observation_read_token_is_structurally_valid(
    token: object,
) -> bool:
    return _runtime_sealed_scalar_fields_are_valid(token)


class _ObservationProtocol:
    """One deep module for exact readback validation and classification."""

    @staticmethod
    def invalid(detail: str) -> _RuntimeObservationVerdict:
        return _RuntimeObservationVerdict(
            kind="invalid",
            observation=None,
            failure=_RuntimeFailure(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                detail,
            ),
            identity=None,
            artifact_evidence=None,
            token=None,
        )

    @classmethod
    def failure(
        cls,
        failure: _RuntimeFailure,
        *,
        selected_stable_action_id: str | None = None,
    ) -> _RuntimeObservationVerdict:
        """Build one trusted, structurally closed failure verdict."""

        if not _runtime_failure_is_structurally_valid(failure):
            return cls.invalid("Runtime observation failure is malformed")
        kind = "failure"
        if (
            selected_stable_action_id is not None
            and _runtime_failure_is_authoritative_absence(
                failure,
                selected_stable_action_id,
            )
        ):
            kind = "authoritative_absence"
        return _RuntimeObservationVerdict(
            kind,
            None,
            failure,
            None,
            None,
            None,
        )

    @classmethod
    def validate(
        cls,
        read: object,
        *,
        selected_stable_action_id: str,
        expected_subject: RuntimeSubject | None = None,
        expected_profile_digest: object = None,
        expected_prompt_artifact_digest: object = None,
        prior_record: Mapping[str, Any] | None = None,
        maximum_artifact_bytes: object = _MAXIMUM_PASEO_STREAM_BYTES,
    ) -> _RuntimeObservationVerdict:
        """Pure, total validation of one sealed Adapter read envelope."""

        try:
            if (
                type(selected_stable_action_id) is not str
                or not selected_stable_action_id
                or type(maximum_artifact_bytes) is not int
                or not 1
                <= maximum_artifact_bytes
                <= _MAXIMUM_RUNTIME_SCALAR_INTEGER
                or not _runtime_sealed_scalar_fields_are_valid(read)
                or read.selected_stable_action_id
                != selected_stable_action_id
            ):
                return cls.invalid("Runtime observation envelope is malformed")
            result = read.result
            if type(result) is _RuntimeFailure:
                if not _runtime_failure_is_structurally_valid(result):
                    return cls.invalid(
                        "Runtime observation failure is malformed"
                    )
                if (
                    result.stable_action_id is not None
                    and result.stable_action_id
                    != selected_stable_action_id
                ):
                    return cls.invalid(
                        "Runtime observation failure names another action"
                    )
                if read.artifact_evidence is not None:
                    return cls.invalid(
                        "Runtime failure cannot carry Artifact evidence"
                    )
                if (read.identity is None) != (read.token is None):
                    return cls.invalid(
                        "Runtime failure read token is incomplete"
                    )
                if read.identity is not None:
                    if (
                        not _runtime_observation_identity_is_structurally_valid(
                            read.identity
                        )
                        or not (
                            _runtime_observation_read_token_is_structurally_valid(
                                read.token
                            )
                        )
                        or read.identity.stable_action_id
                        != selected_stable_action_id
                        or read.token.stable_action_id
                        != selected_stable_action_id
                        or read.token.identity_digest
                        != _runtime_observation_identity_digest(
                            read.identity
                        )
                        or read.token.observation_digest is not None
                        or read.token.output_artifact_digest is not None
                        or not _runtime_observation_identity_matches_expectation(
                            read.identity,
                            selected_stable_action_id=(
                                selected_stable_action_id
                            ),
                            expected_subject=expected_subject,
                            expected_profile_digest=(
                                expected_profile_digest
                            ),
                            expected_prompt_artifact_digest=(
                                expected_prompt_artifact_digest
                            ),
                            prior_record=prior_record,
                        )
                    ):
                        return cls.invalid(
                            "Runtime failure read token is malformed"
                        )
                if result.code == "RUNTIME_ACTION_ABSENT":
                    if not _runtime_failure_is_authoritative_absence(
                        result,
                        selected_stable_action_id,
                    ):
                        return cls.invalid(
                            "Runtime authoritative absence is malformed"
                        )
                    return _RuntimeObservationVerdict(
                        "authoritative_absence",
                        None,
                        result,
                        read.identity,
                        None,
                        read.token,
                    )
                if (
                    result.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
                    and result.stable_action_id is None
                ) or (
                    result.code
                    in {
                        "RUNTIME_BINDING_MISSING",
                        "RUNTIME_MATERIALIZATION_PENDING",
                    }
                    and result.stable_action_id
                    == selected_stable_action_id
                ):
                    return _RuntimeObservationVerdict(
                        "fairness_advance",
                        None,
                        result,
                        read.identity,
                        None,
                        read.token,
                    )
                return _RuntimeObservationVerdict(
                    "failure",
                    None,
                    result,
                    read.identity,
                    None,
                    read.token,
                )
            if type(result) not in {
                _PreparedRuntimeObservation,
                _BoundRuntimeObservation,
            }:
                return cls.invalid("Runtime observation result is malformed")
            identity = read.identity
            token = read.token
            evidence = read.artifact_evidence
            if (
                not _runtime_observation_identity_is_structurally_valid(
                    identity
                )
                or not _runtime_observation_read_token_is_structurally_valid(
                    token
                )
                or not _runtime_observation_matches_identity(result, identity)
                or result.stable_action_id != selected_stable_action_id
                or token.stable_action_id != selected_stable_action_id
                or token.identity_digest
                != _runtime_observation_identity_digest(identity)
                or token.observation_digest
                != digest_value(_json_projection(asdict(result)))
                or token.output_artifact_digest
                != (
                    None
                    if type(result) is _PreparedRuntimeObservation
                    else result.output_artifact_digest
                )
                or not _runtime_artifact_evidence_is_valid(
                    evidence,
                    identity,
                    result,
                    maximum_artifact_bytes=maximum_artifact_bytes,
                )
            ):
                return cls.invalid(
                    "Runtime observation envelope does not bind its exact read"
                )
            if not _runtime_observation_identity_matches_expectation(
                identity,
                selected_stable_action_id=selected_stable_action_id,
                expected_subject=expected_subject,
                expected_profile_digest=expected_profile_digest,
                expected_prompt_artifact_digest=(
                    expected_prompt_artifact_digest
                ),
                prior_record=prior_record,
            ):
                return cls.invalid(
                    "Runtime observation changed its requested or frozen identity"
                )
            return _RuntimeObservationVerdict(
                (
                    "prepared"
                    if type(result) is _PreparedRuntimeObservation
                    else "bound"
                ),
                result,
                None,
                identity,
                evidence,
                token,
            )
        except Exception:
            return cls.invalid("Runtime observation envelope is malformed")


def _validated_observation_projection(
    read: object,
    *,
    stable_action_id: str,
    maximum_artifact_bytes: object = _MAXIMUM_PASEO_STREAM_BYTES,
) -> (
    _PreparedRuntimeObservation
    | _BoundRuntimeObservation
    | _RuntimeFailure
):
    """External test/debug compatibility projection from a validated read."""

    verdict = _ObservationProtocol.validate(
        read,
        selected_stable_action_id=stable_action_id,
        maximum_artifact_bytes=maximum_artifact_bytes,
    )
    if verdict.kind in {"prepared", "bound"}:
        assert verdict.observation is not None
        return verdict.observation
    assert verdict.failure is not None
    return verdict.failure


def _runtime_sealed_observation_read(
    *,
    artifacts: ArtifactStore,
    selected_stable_action_id: str,
    identity: _RuntimeObservationIdentity,
    selected_record_digest: str,
    observation: _PreparedRuntimeObservation | _BoundRuntimeObservation,
) -> _RuntimeObservationRead:
    evidence = _runtime_artifact_evidence(
        artifacts,
        identity,
        observation,
    )
    observation_digest = digest_value(
        _json_projection(asdict(observation))
    )
    return _RuntimeObservationRead(
        selected_stable_action_id=selected_stable_action_id,
        identity=identity,
        result=observation,
        artifact_evidence=evidence,
        token=_RuntimeObservationReadToken(
            stable_action_id=selected_stable_action_id,
            identity_digest=_runtime_observation_identity_digest(identity),
            selected_record_digest=selected_record_digest,
            observation_digest=observation_digest,
            output_artifact_digest=(
                None
                if type(observation) is _PreparedRuntimeObservation
                else observation.output_artifact_digest
            ),
        ),
    )


def _runtime_sealed_failure_read(
    selected_stable_action_id: str,
    failure: _RuntimeFailure,
    *,
    identity: _RuntimeObservationIdentity | None = None,
    selected_record_digest: str | None = None,
) -> _RuntimeObservationRead:
    token = (
        None
        if identity is None or selected_record_digest is None
        else _RuntimeObservationReadToken(
            stable_action_id=selected_stable_action_id,
            identity_digest=_runtime_observation_identity_digest(identity),
            selected_record_digest=selected_record_digest,
            observation_digest=None,
            output_artifact_digest=None,
        )
    )
    return _RuntimeObservationRead(
        selected_stable_action_id=selected_stable_action_id,
        identity=identity,
        result=failure,
        artifact_evidence=None,
        token=token,
    )


def _runtime_read_token_matches_record(
    token: object,
    *,
    stable_action_id: str,
    identity: _RuntimeObservationIdentity,
    selected_record_digest: str,
) -> bool:
    """Check a detached read's causal precondition against current state."""

    try:
        return (
            _runtime_observation_read_token_is_structurally_valid(token)
            and token.stable_action_id == stable_action_id
            and token.identity_digest
            == _runtime_observation_identity_digest(identity)
            and token.selected_record_digest == selected_record_digest
        )
    except Exception:
        return False


class _RuntimeProviderAdapter(Protocol):
    """The exact private seam shared by production and deterministic adapters."""

    def prepare(self, spec: _RuntimeActionSpec) -> _PrepareReceipt | _RuntimeFailure: ...

    def observe(
        self, stable_action_id: str
    ) -> _PreparedRuntimeObservation | _BoundRuntimeObservation | _RuntimeFailure: ...

    def command(
        self,
        stable_action_id: str,
        transition: RuntimeTransition,
    ) -> _CommandReceipt | _RuntimeFailure: ...

    def events(self, after_cursor: str | None) -> _RuntimeEventPage | _RuntimeFailure: ...


def _reject_repository_context_reinitialization(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeGatewayError(
        "RUNTIME_CONFIGURATION_INVALID",
        "Runtime repository context is a sealed value and cannot be reinitialized",
    )


class _RuntimeRepositoryContextMeta(_SealedValueMeta):
    """Keep the sealed constructor's public contract visible to callers."""

    def __call__(cls, path: Path, base_ref: str) -> Any:
        return cls.__new__(cls, path, base_ref)


@dataclass(frozen=True, init=False)
class RuntimeRepositoryContext(tuple, metaclass=_RuntimeRepositoryContextMeta):
    """Host-owned source checkout used to create action-owned Workspaces."""

    __slots__ = ()

    path: Path
    base_ref: str

    def __new__(cls, path: Path, base_ref: str) -> "RuntimeRepositoryContext":
        resolved_path = Path(path).resolve()
        if not resolved_path.is_dir():
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID", "Paseo repository context path is unavailable"
            )
        exact_base_ref = _require_text(base_ref, "Runtime repository context base_ref")
        return tuple.__new__(cls, (resolved_path, exact_base_ref))

    __init__ = _reject_repository_context_reinitialization

    @property
    def path(self) -> Path:
        return tuple.__getitem__(self, 0)

    @property
    def base_ref(self) -> str:
        return tuple.__getitem__(self, 1)


def _snapshot_repository_contexts(
    value: Mapping[str, RuntimeRepositoryContext],
) -> Mapping[str, RuntimeRepositoryContext]:
    """Take a private, immutable host snapshot before any Runtime operation."""

    if not isinstance(value, Mapping):
        raise RuntimeGatewayError(
            "RUNTIME_CONFIGURATION_INVALID",
            "Paseo repository contexts must be a mapping",
        )
    snapshot: dict[str, RuntimeRepositoryContext] = {}
    for repository, context in value.items():
        repository_name = _require_text(repository, "Paseo repository context repository")
        if type(context) is not RuntimeRepositoryContext:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                "Paseo repository context must be one exact sealed value",
            )
        if repository_name in snapshot:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                "Paseo repository context identity is duplicated",
            )
        snapshot[repository_name] = RuntimeRepositoryContext(
            Path(str(context.path)),
            str(context.base_ref),
        )
    return MappingProxyType(snapshot)


@dataclass(frozen=True)
class _PaseoAgentReadback:
    agent_id: str
    provider: str
    model: str
    thinking: str
    mode: str
    cwd: str
    lifecycle: str
    archived: bool
    pending_permissions: tuple[tuple[str, str], ...]


class _ProviderNotDispatched(RuntimeError):
    """Private proof that provider process creation never dispatched."""

    def __init__(self, cause: Exception):
        super().__init__(str(cause))
        self.cause = cause


class _PaseoCliTransport:
    """Concrete V3-only JSON transport for the documented Paseo 0.2.3 surface."""

    def __init__(self, executable: str = "paseo", *, timeout_seconds: int = 60):
        self._executable = shutil.which(executable) or executable
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def validate_arguments(args: list[str], *, executable: str = "paseo") -> None:
        """Fail closed before a dynamic value reaches a Paseo CLI wrapper."""

        if not isinstance(args, list) or not args or not all(
            isinstance(argument, str) and argument for argument in args
        ):
            raise ValueError("Paseo command arguments are invalid")
        for argument in args:
            _require_paseo_argument(argument, "Paseo command argument")
        # The Windows launcher can be a .cmd file.  Bound the *encoded*
        # command line, not a lossy sum of Python argument lengths.
        command_length = len(subprocess.list2cmdline([executable, *args]))
        if command_length > _MAXIMUM_PASEO_COMMAND_CHARS:
            raise RuntimeGatewayError(
                "RUNTIME_VENDOR_ARGUMENT_INVALID",
                "Paseo command exceeds the bounded command-line limit",
            )

    @staticmethod
    def _decode_transport_envelope(payload: str) -> Any:
        """Decode bounded native JSON without treating its spelling as identity.

        Paseo's CLI stdout and stderr are external transport envelopes, not GWO
        Artifacts.  Strict decoding produces one fresh exact-builtin JSON value;
        the original vendor bytes do not leave this boundary.  A downstream
        journal, Artifact, schema, or identity operation still creates and
        verifies its own exact canonical bytes from that value.
        """

        try:
            return strict_json_loads(payload)
        except CanonicalJsonError as error:
            raise ValueError("Paseo JSON response is invalid") from error

    @staticmethod
    def _configure_pipe(stream: Any) -> None:
        """Put a subprocess pipe in the polling mode available on this host.

        Some supported Windows Python builds do not expose ``os.set_blocking``
        for anonymous subprocess pipes.  Windows named-pipe polling is handled
        by ``_read_pipe_chunk`` below; no blocking mode toggle is required in
        that case.  POSIX keeps the existing native non-blocking path.
        """

        if hasattr(os, "set_blocking"):
            os.set_blocking(stream.fileno(), False)
            return
        if os.name != "nt":
            raise OSError("the host cannot configure non-blocking subprocess pipes")

    @staticmethod
    def _read_pipe_chunk(
        stream: Any,
        process: Any,
        maximum_bytes: int,
    ) -> bytes | None:
        """Read one bounded chunk, returning ``None`` when Windows has no data."""

        if hasattr(os, "set_blocking") or os.name != "nt":
            return os.read(stream.fileno(), maximum_bytes)

        # ``select`` and ``os.set_blocking`` are not available for anonymous
        # Windows pipes in this Python runtime.  PeekNamedPipe gives the same
        # non-consuming readiness check while retaining the transport caps.
        import ctypes
        import ctypes.wintypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        peek = kernel32.PeekNamedPipe
        peek.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.wintypes.DWORD,
            ctypes.POINTER(ctypes.wintypes.DWORD),
            ctypes.POINTER(ctypes.wintypes.DWORD),
            ctypes.POINTER(ctypes.wintypes.DWORD),
        ]
        peek.restype = ctypes.wintypes.BOOL
        available = ctypes.wintypes.DWORD()
        bytes_read = ctypes.wintypes.DWORD()
        message_bytes = ctypes.wintypes.DWORD()
        handle = ctypes.wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
        if not peek(
            handle,
            None,
            0,
            ctypes.byref(bytes_read),
            ctypes.byref(available),
            ctypes.byref(message_bytes),
        ):
            error = ctypes.get_last_error()
            # ERROR_BROKEN_PIPE / ERROR_NO_DATA mean the producer closed the
            # pipe.  Returning EOF lets the normal drain/cleanup path run.
            if error in {109, 232}:
                return b""
            raise OSError(error, "PeekNamedPipe failed")
        if available.value == 0:
            # Never perform a potentially blocking read merely because the
            # parent process exited: an inherited grandchild may still own
            # the pipe, and the bounded post-exit drain must remain in charge.
            return None
        return os.read(stream.fileno(), min(maximum_bytes, available.value))

    def _run(self, args: list[str], *, allow_empty: bool = False) -> Any:
        self.validate_arguments(args, executable=self._executable)
        started = time.monotonic()
        command_deadline = started + self._timeout_seconds
        hard_deadline = command_deadline + _PASEO_CLEANUP_GRACE_SECONDS
        try:
            process = subprocess.Popen(
                [self._executable, *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
            )
        except Exception as error:
            raise _ProviderNotDispatched(error) from error
        if process.stdout is None or process.stderr is None:
            cleanup_deadline = min(
                hard_deadline,
                time.monotonic() + _PASEO_CLEANUP_GRACE_SECONDS,
            )
            self._stop_and_reap(process, cleanup_deadline)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass
            raise RuntimeGatewayError(
                "RUNTIME_TRANSPORT_UNAVAILABLE",
                "Paseo command pipes are unavailable",
            )
        streams = {"stdout": process.stdout, "stderr": process.stderr}
        buffers = {name: bytearray() for name in streams}
        stream_totals = {name: 0 for name in streams}
        total_bytes = 0
        open_streams = set(streams)
        stop_reason: str | None = None
        process_reaped = False
        close_failed = False
        returncode: int | None = None
        parent_exit_drain_deadline: float | None = None

        try:
            for stream in streams.values():
                self._configure_pipe(stream)
            while True:
                made_progress = False
                for name in tuple(open_streams):
                    stream = streams[name]
                    try:
                        chunk = self._read_pipe_chunk(
                            stream,
                            process,
                            _PASEO_PIPE_CHUNK_BYTES,
                        )
                    except BlockingIOError:
                        continue
                    except OSError:
                        stop_reason = "read_failed"
                        break
                    if chunk is None:
                        continue
                    if not chunk:
                        open_streams.remove(name)
                        continue
                    made_progress = True
                    stream_totals[name] += len(chunk)
                    total_bytes += len(chunk)
                    stream_remaining = max(
                        0, _MAXIMUM_PASEO_STREAM_BYTES - len(buffers[name])
                    )
                    total_retained = sum(len(buffer) for buffer in buffers.values())
                    total_remaining = max(
                        0, _MAXIMUM_PASEO_TOTAL_BYTES - total_retained
                    )
                    retain = min(len(chunk), stream_remaining, total_remaining)
                    buffers[name].extend(chunk[:retain])
                    if (
                        stream_totals[name] > _MAXIMUM_PASEO_STREAM_BYTES
                        or total_bytes > _MAXIMUM_PASEO_TOTAL_BYTES
                    ):
                        stop_reason = "overflow"
                        break
                if stop_reason is not None:
                    break

                now = time.monotonic()
                polled = self._poll(process)
                if polled is not None:
                    returncode = polled
                    if parent_exit_drain_deadline is None:
                        parent_exit_drain_deadline = min(
                            command_deadline,
                            now + _PASEO_POST_EXIT_DRAIN_SECONDS,
                        )
                    if not open_streams or now >= parent_exit_drain_deadline:
                        break
                elif now >= command_deadline:
                    stop_reason = "timeout"
                    break

                if not made_progress:
                    sleep_until = command_deadline
                    if parent_exit_drain_deadline is not None:
                        sleep_until = min(sleep_until, parent_exit_drain_deadline)
                    remaining = max(0.0, sleep_until - time.monotonic())
                    if remaining:
                        time.sleep(min(_PASEO_PIPE_POLL_SECONDS, remaining))
        except (OSError, ValueError):
            stop_reason = "read_failed"
        finally:
            cleanup_deadline = min(
                hard_deadline,
                time.monotonic() + _PASEO_CLEANUP_GRACE_SECONDS,
            )
            if self._poll(process) is None:
                process_reaped = self._stop_and_reap(
                    process, cleanup_deadline
                )
            else:
                process_reaped = self._confirm_reaped(
                    process, cleanup_deadline
                )
            returncode = process.returncode
            for stream in streams.values():
                try:
                    stream.close()
                except (OSError, ValueError):
                    close_failed = True

        stdout_bytes = bytes(buffers["stdout"])
        stderr_bytes = bytes(buffers["stderr"])
        if not process_reaped or close_failed:
            raise RuntimeGatewayError(
                "RUNTIME_TRANSPORT_UNAVAILABLE",
                "Paseo command cleanup could not confirm bounded process exit",
            )
        if stop_reason == "timeout":
            raise TimeoutError("Paseo command timed out")
        if stop_reason == "overflow":
            raise RuntimeGatewayError(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "Paseo command output exceeded its bounded capture limit",
            )
        if stop_reason == "read_failed":
            raise RuntimeGatewayError(
                "RUNTIME_TRANSPORT_UNAVAILABLE",
                "Paseo command output transport failed",
            )
        try:
            stdout = stdout_bytes.decode("utf-8", errors="strict")
            stderr = stderr_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("Paseo output is not strict UTF-8") from error
        if returncode != 0:
            raise self._nonzero_failure(stdout, stderr)
        if stdout == "":
            if allow_empty:
                return _NO_PASEO_BODY_ACK
            raise ValueError("Paseo JSON response is empty")
        return self._decode_transport_envelope(stdout)

    @staticmethod
    def _poll(process: Any) -> int | None:
        try:
            return process.poll()
        except (OSError, ValueError):
            return None

    @staticmethod
    def _confirm_reaped(process: Any, deadline: float) -> bool:
        remaining = min(0.05, max(0.0, deadline - time.monotonic()))
        try:
            process.wait(timeout=remaining)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False
        return process.returncode is not None

    @classmethod
    def _stop_and_reap(cls, process: Any, deadline: float) -> bool:
        if cls._poll(process) is not None:
            return cls._confirm_reaped(process, deadline)
        try:
            process.terminate()
        except OSError:
            pass
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=min(0.1, remaining))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        if process.returncode is not None:
            return True
        try:
            process.kill()
        except OSError:
            pass
        return cls._confirm_reaped(process, deadline)

    @staticmethod
    def _nonzero_failure(stdout: str, stderr: str) -> RuntimeGatewayError:
        """Classify a bounded Paseo JSON error without exposing vendor text."""

        error: Mapping[str, Any] | None = None
        for payload in (stdout, stderr):
            if not isinstance(payload, str) or payload == "":
                continue
            # Error text is untrusted provider output.  Do not give JSON a
            # potentially unbounded document merely because the process
            # already exited non-zero.
            if len(payload.encode("utf-8")) > _MAXIMUM_PASEO_ERROR_JSON_BYTES:
                continue
            try:
                candidate = strict_json_loads(payload)
            except CanonicalJsonError:
                continue
            if isinstance(candidate, dict) and isinstance(candidate.get("error"), dict):
                error = candidate["error"]
                break
        code = error.get("code") if error is not None else None
        if not isinstance(code, str) or not code or len(code) > 128:
            return RuntimeGatewayError(
                "RUNTIME_PROVIDER_COMMAND_FAILED", "Paseo command was rejected"
            )
        normalized = code.upper()
        if normalized in {
            "DAEMON_UNAVAILABLE", "DAEMON_NOT_RUNNING", "TRANSPORT_UNAVAILABLE",
            "CONNECTION_REFUSED", "SOCKET_UNAVAILABLE",
        }:
            return RuntimeGatewayError(
                "RUNTIME_TRANSPORT_UNAVAILABLE", "Paseo daemon transport is unavailable"
            )
        if normalized == "AGENT_NOT_FOUND":
            return RuntimeGatewayError("RUNTIME_ACTION_UNKNOWN", "Paseo Agent is not found")
        if normalized == "PERMISSION_NOT_FOUND":
            return RuntimeGatewayError(
                "RUNTIME_PERMISSION_REQUEST_UNKNOWN", "Paseo permission request is not found"
            )
        if "CONFIG" in normalized:
            return RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID", "Paseo command configuration is invalid"
            )
        if normalized in {"PROTOCOL_ERROR", "INVALID_JSON"}:
            return RuntimeGatewayError(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID", "Paseo command protocol is invalid"
            )
        return RuntimeGatewayError(
            "RUNTIME_PROVIDER_COMMAND_FAILED", "Paseo command was rejected"
        )

    def inspect(self, agent_id: str) -> _PaseoAgentReadback:
        _require_paseo_argument(agent_id, "Paseo Agent id")
        payload = self._run(["inspect", agent_id, "--json"])
        value = payload.get("agent", payload) if isinstance(payload, dict) else payload
        if not isinstance(value, dict):
            raise ValueError("Paseo inspect response is invalid")
        observed_id = _decode_exact_paseo_alias(
            value, ("id", "Id", "agentId", "AgentId"), "Agent id"
        )
        provider = _decode_exact_paseo_alias(
            value, ("provider", "Provider"), "Agent provider"
        )
        model = _decode_exact_paseo_alias(
            value, ("model", "Model"), "Agent model"
        )
        thinking = _decode_exact_paseo_alias(
            value, ("thinking", "Thinking"), "Agent thinking"
        )
        mode = _decode_exact_paseo_alias(
            value, ("mode", "Mode"), "Agent mode"
        )
        cwd = _decode_exact_paseo_alias(
            value, ("cwd", "Cwd"), "Agent cwd"
        )
        lifecycle = _decode_exact_paseo_alias(
            value, ("status", "Status"), "Agent lifecycle"
        )
        archived = _decode_exact_paseo_alias(
            value, ("archived", "Archived"), "Agent archived state"
        )
        pending = _decode_exact_paseo_alias(
            value,
            ("PendingPermissions", "pendingPermissions"),
            "Agent pending permissions",
        )
        if not all(
            isinstance(item, str) and item
            for item in (observed_id, provider, model, thinking, mode, cwd, lifecycle)
        ):
            raise ValueError("Paseo inspect omitted Agent profile or identity")
        _require_paseo_argument(observed_id, "Paseo inspected Agent id")
        _require_paseo_argument(provider, "Paseo inspected provider")
        _require_paseo_argument(model, "Paseo inspected model")
        _require_paseo_argument(thinking, "Paseo inspected thinking mode")
        _require_paseo_argument(mode, "Paseo inspected mode")
        _require_paseo_argument(cwd, "Paseo inspected cwd")
        _require_paseo_argument(lifecycle, "Paseo inspected lifecycle")
        if type(archived) is not bool:
            raise ValueError("Paseo inspect omitted exact Archived state")
        if not isinstance(pending, list):
            raise ValueError("Paseo inspect omitted exact PendingPermissions")
        pending_permissions: list[tuple[str, str]] = []
        for item in pending:
            if (
                not isinstance(item, dict)
                or set(item) != {"id", "tool"}
                or not isinstance(item["id"], str)
                or not isinstance(item["tool"], str)
                or len(item["id"]) > _MAXIMUM_PASEO_PERMISSION_TEXT
                or len(item["tool"]) > _MAXIMUM_PASEO_PERMISSION_TEXT
            ):
                raise ValueError("Paseo PendingPermissions entry is invalid")
            _require_paseo_argument(item["id"], "Paseo pending permission id")
            _require_paseo_argument(item["tool"], "Paseo pending permission tool")
            pending_permissions.append((item["id"], item["tool"]))
        if len({item[0] for item in pending_permissions}) != len(pending_permissions):
            raise ValueError("Paseo PendingPermissions contains duplicate ids")
        return _PaseoAgentReadback(
            agent_id=observed_id,
            provider=provider,
            model=model,
            thinking=thinking,
            mode=mode,
            cwd=cwd,
            lifecycle=lifecycle,
            archived=archived,
            pending_permissions=tuple(pending_permissions),
        )

    def update_labels(self, agent_id: str, labels: Mapping[str, str]) -> None:
        _require_paseo_argument(agent_id, "Paseo Agent id")
        args = ["agent", "update", agent_id]
        for key, value in sorted(labels.items()):
            _require_paseo_argument(key, "Paseo label key")
            _require_paseo_argument(value, "Paseo label value")
            args.extend(["--label", f"{key}={value}"])
        # Label mutation has no provider receipt shape.  The caller's
        # authoritative inspect readback, not this optional empty envelope,
        # establishes the effect.
        self._run([*args, "--json"], allow_empty=True)


class _PaseoRuntimeProviderAdapter:
    """Concrete Paseo 0.2.3 lifecycle behind the private Provider seam.

    A Workspace and verified Prompt file exist before semantic execution.  The
    first Agent is created only by ``paseo run`` after Gateway has read a
    Prepared observation.  Its short initial prompt names the Workspace file;
    complete semantic material never enters a CLI argument.
    """

    def __init__(
        self,
        *,
        client: _PaseoCliTransport,
        artifacts: ArtifactStore,
        repository_contexts: Mapping[str, RuntimeRepositoryContext],
        state_path: Path,
    ):
        self._client = client
        self._artifacts = artifacts
        self._contexts = _snapshot_repository_contexts(repository_contexts)
        self._state_path = Path(state_path)
        self._journal = _V3JsonJournal(self._state_path)
        self._pending_save_state: dict[str, Any] | None = None
        self._command_gate = _OneShotObservationGate()
        (
            self._actions,
            self._events,
            self._workspace_intents,
            self._next_event_cursor,
            self._event_scan_cursor,
        ) = self._load()

    @staticmethod
    def _failure(
        error: Exception,
        stable_action_id: str | None = None,
    ) -> _RuntimeFailure:
        if isinstance(error, _ProviderNotDispatched):
            error = error.cause
        if isinstance(error, (OSError, TimeoutError)):
            return _RuntimeFailure.transport()
        if isinstance(error, RuntimeGatewayError):
            return _RuntimeFailure(
                error.code,
                "Runtime Artifact or configuration validation failed",
                stable_action_id=(
                    stable_action_id
                    if error.code in _RUNTIME_ACTION_BOUND_FAILURE_CODES
                    else None
                ),
            )
        return _RuntimeFailure(
            "RUNTIME_PROVIDER_PROTOCOL_INVALID",
            "Paseo Runtime returned an invalid result",
        )

    @staticmethod
    def _labels(spec: _RuntimeActionSpec) -> dict[str, str]:
        subject = spec.subject
        return {
            "gwo.runtime_action": spec.stable_action_id,
            "gwo.runtime_subject": subject.digest,
            "gwo.runtime_profile": spec.profile.digest,
            "gwo.runtime_prompt": spec.prompt_artifact.digest,
            "gwo.runtime_authority": subject.authority_digest,
            "gwo.runtime_repository": subject.repository,
            "gwo.runtime_campaign": subject.campaign_handle,
        }

    @staticmethod
    def _workspace_payload(value: Any) -> tuple[str, str]:
        candidate = value.get("workspace", value) if isinstance(value, dict) else value
        if not isinstance(candidate, dict):
            raise ValueError("workspace response is invalid")
        workspace_id = _decode_exact_paseo_alias(
            candidate,
            ("id", "Id", "workspaceId"),
            "Workspace id",
        )
        path = _decode_exact_paseo_alias(
            candidate,
            ("path", "Path", "cwd"),
            "Workspace path",
        )
        if not isinstance(workspace_id, str) or not workspace_id or not isinstance(path, str) or not path:
            raise ValueError("workspace identity is incomplete")
        _require_paseo_argument(workspace_id, "Paseo Workspace id")
        _require_paseo_argument(path, "Paseo Workspace path")
        return workspace_id, path

    def _load(
        self,
    ) -> tuple[
        dict[str, dict[str, Any]],
        list[_RuntimeEvent],
        dict[str, dict[str, str]],
        int,
        int,
    ]:
        with self._journal.exclusive():
            return self._load_unlocked()

    def _load_unlocked(
        self,
    ) -> tuple[
        dict[str, dict[str, Any]],
        list[_RuntimeEvent],
        dict[str, dict[str, str]],
        int,
        int,
    ]:
        value = self._journal.read_unlocked()
        if value is None:
            return {}, [], {}, 1, 0
        if not isinstance(value, dict):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo Runtime action record is invalid"
            )
        actions = value.get("actions")
        raw_events = value.get("events")
        if (
            frozenset(value)
            != {
                "schema_version",
                "actions",
                "events",
                "workspace_intents",
                "next_event_cursor",
                "event_scan_cursor",
            }
            or value.get("schema_version") != 5
            or type(actions) is not dict
            or type(raw_events) is not list
            or len(raw_events) > _MAXIMUM_RUNTIME_EVENTS
        ):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo Runtime action record is invalid"
        )
        for stable_action_id, action in actions.items():
            _validate_paseo_action_record(stable_action_id, action)
        raw_intents = value["workspace_intents"]
        if type(raw_intents) is not dict:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo Workspace intent record is invalid"
            )
        normalized_intents: dict[str, dict[str, str]] = {}
        for action, intent in raw_intents.items():
            if (
                type(action) is not str
                or type(intent) is not dict
                or frozenset(intent)
                != frozenset(
                    {
                        "repository_path",
                        "base_commit",
                        "slug",
                        "branch",
                        "spec_identity_digest",
                        "ownership_nonce",
                        "layout_version",
                        "phase",
                    }
                )
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID", "Paseo Workspace intent record is invalid"
                )
            normalized = dict(intent)
            if (
                any(type(part) is not str or not part for part in normalized.values())
                or normalized["phase"] not in {"recorded", "create_pending"}
                or _GIT_COMMIT_RE.fullmatch(normalized["base_commit"]) is None
                or normalized["branch"] != f"gwo-{normalized['slug']}"
                or re.fullmatch(r"[0-9a-f]{32}", normalized["ownership_nonce"])
                is None
                or normalized["layout_version"]
                != _RUNTIME_WORKSPACE_LAYOUT_VERSION
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID", "Paseo Workspace intent record is invalid"
                )
            normalized_intents[action] = normalized
        if set(actions).intersection(normalized_intents):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID",
                "Paseo action and Workspace intent ownership overlap",
            )
        events: list[_RuntimeEvent] = []
        for raw in raw_events:
            if (
                type(raw) is not dict
                or not all(type(name) is str for name in raw)
                or tuple(sorted(raw))
                != ("cursor", "kind", "stable_action_id")
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID", "Paseo Runtime event record is invalid"
                )
            try:
                event = _RuntimeEvent(
                    cursor=_require_text(raw["cursor"], "Paseo event cursor"),
                    stable_action_id=_require_text(
                        raw["stable_action_id"], "Paseo event stable action"
                    ),
                    kind=_require_text(raw["kind"], "Paseo event kind"),
                )
            except (KeyError, RuntimeGatewayError) as error:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID", "Paseo Runtime event record is invalid"
                ) from error
            if not _runtime_sealed_scalar_fields_are_valid(event):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "Paseo Runtime event record is invalid",
                )
            if event.stable_action_id not in actions:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "Paseo Runtime event refers to an unknown action",
                )
            events.append(event)
        cursor_values: list[int] = []
        for event in events:
            cursor_value = _runtime_event_cursor_value(event.cursor)
            if cursor_value is None:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID", "Paseo Runtime event cursor is invalid"
                )
            cursor_values.append(cursor_value)
        if (
            cursor_values
            and len(cursor_values) < _MAXIMUM_RUNTIME_EVENTS
            and cursor_values[0] != 1
        ) or any(
            current != previous + 1
            for previous, current in zip(cursor_values, cursor_values[1:])
        ):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo Runtime event cursor is invalid"
            )
        counters = _runtime_v3_event_journal_counters(
            value,
            cursor_values=tuple(cursor_values),
        )
        if counters is None:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo Runtime event cursor is invalid"
            )
        next_event_cursor, event_scan_cursor = counters
        return (
            dict(actions),
            events,
            normalized_intents,
            next_event_cursor,
            event_scan_cursor,
        )

    @staticmethod
    def _persisted_state_projection(state: Mapping[str, Any]) -> dict[str, Any]:
        """Return the exact durable shape emitted by _save."""

        return {
            "schema_version": 5,
            "actions": state["actions"],
            "events": [asdict(event) for event in state["events"]],
            "workspace_intents": state["workspace_intents"],
            "next_event_cursor": state["next_event_cursor"],
            "event_scan_cursor": state["event_scan_cursor"],
        }

    def _save(self) -> None:
        state = self._pending_save_state or {
            "actions": self._actions,
            "events": self._events,
            "workspace_intents": self._workspace_intents,
            "next_event_cursor": self._next_event_cursor,
            "event_scan_cursor": self._event_scan_cursor,
        }
        self._journal.replace_unlocked(self._persisted_state_projection(state))

    def _publish_state(
        self,
        actions: dict[str, dict[str, Any]],
        events: list[_RuntimeEvent],
        workspace_intents: dict[str, dict[str, str]],
        next_event_cursor: int,
        event_scan_cursor: int,
    ) -> None:
        self._actions = actions
        self._events = events
        self._workspace_intents = workspace_intents
        self._next_event_cursor = next_event_cursor
        self._event_scan_cursor = event_scan_cursor

    def _refresh(self) -> None:
        with self._journal.exclusive():
            self._publish_state(*self._load_unlocked())

    def _transact(self, mutation: Callable[[dict[str, Any]], Any]) -> Any:
        with self._journal.exclusive():
            loaded = self._load_unlocked()
            durable = {
                "actions": loaded[0],
                "events": loaded[1],
                "workspace_intents": loaded[2],
                "next_event_cursor": loaded[3],
                "event_scan_cursor": loaded[4],
            }
            candidate = deepcopy(durable)
            try:
                result = mutation(candidate)
                if canonical_bytes(
                    self._persisted_state_projection(candidate)
                ) != canonical_bytes(self._persisted_state_projection(durable)):
                    self._pending_save_state = candidate
                    self._save()
            except Exception:
                try:
                    self._publish_state(*self._load_unlocked())
                except RuntimeGatewayError:
                    self._publish_state(*loaded)
                raise
            finally:
                self._pending_save_state = None
            self._publish_state(
                candidate["actions"],
                candidate["events"],
                candidate["workspace_intents"],
                candidate["next_event_cursor"],
                candidate["event_scan_cursor"],
            )
            return result

    def _persist_record_update(
        self,
        record: dict[str, Any],
        update: Callable[[dict[str, Any]], None],
    ) -> None:
        """CAS one detached action update and publish only after replacement."""

        expected = deepcopy(record)
        stable_action_id = _require_text(
            expected.get("subject", {}).get("stable_action_id")
            if isinstance(expected.get("subject"), dict)
            else None,
            "persisted stable action id",
        )

        def commit(state: dict[str, Any]) -> dict[str, Any]:
            current = state["actions"].get(stable_action_id)
            if current != expected:
                raise RuntimeGatewayError(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Paseo action update lost its durable CAS",
                )
            updated = deepcopy(current)
            update(updated)
            state["actions"][stable_action_id] = updated
            return deepcopy(updated)

        updated = self._transact(commit)
        record.clear()
        record.update(updated)

    def _claim_record_update(
        self,
        record: dict[str, Any],
        *,
        precondition: _RuntimeObservationReadToken,
        already_claimed: Callable[[Mapping[str, Any]], bool],
        update: Callable[[dict[str, Any]], None],
    ) -> bool:
        """Atomically validate one sealed read and grant its provider effect."""

        expected = deepcopy(record)
        stable_action_id = _require_text(
            expected.get("subject", {}).get("stable_action_id")
            if isinstance(expected.get("subject"), dict)
            else None,
            "persisted stable action id",
        )

        def commit(state: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
            current = state["actions"].get(stable_action_id)
            if not isinstance(current, dict) or not (
                _runtime_read_token_matches_record(
                    precondition,
                    stable_action_id=stable_action_id,
                    identity=self._observation_identity(current),
                    selected_record_digest=digest_value(current),
                )
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Paseo provider-effect claim lost its causal read CAS",
                )
            if isinstance(current, dict) and already_claimed(current):
                return False, deepcopy(current)
            if current == expected:
                updated = deepcopy(current)
                update(updated)
                state["actions"][stable_action_id] = updated
                return True, deepcopy(updated)
            raise RuntimeGatewayError(
                "RUNTIME_ACTION_STATE_CHANGED",
                "Paseo provider-effect claim lost its durable CAS",
            )

        claimed, updated = self._transact(commit)
        record.clear()
        record.update(updated)
        return claimed

    def _lifecycle(
        self,
        record: dict[str, Any],
        agent: _PaseoAgentReadback,
        *,
        output_exists: bool,
        permission_pending: bool,
        permission_response_pending: bool,
    ) -> str:
        if agent.archived is True:
            return "retired"
        value = agent.lifecycle.casefold()
        if output_exists:
            if (
                any(
                    record.get(key) is True
                    for key in ("pending_park", "pending_resume", "parked")
                )
                or record.get("pending_stop_command") is not None
            ):
                record.update(
                    {
                        "pending_park": False,
                        "pending_resume": False,
                        "parked": False,
                        "pending_stop_command": None,
                    }
                )
            return "completed"
        if value in {"running", "busy"}:
            if record.get("pending_resume") is True or record.get("parked") is True:
                record.update(
                    {
                        "pending_resume": False,
                        "parked": False,
                        "pending_stop_command": None,
                    }
                )
            return "running"
        if value == "idle" and record.get("pending_resume") is True:
            raise RuntimeGatewayError(
                "RUNTIME_MATERIALIZATION_PENDING",
                "Paseo resume acknowledgement awaits running or output readback",
            )
        if value == "idle" and (
            record.get("pending_park") is True or record.get("parked") is True
        ):
            if record.get("pending_park") is True or record.get("parked") is not True:
                record.update(
                    {
                        "pending_park": False,
                        "parked": True,
                        "pending_stop_command": None,
                    }
                )
            return "parked"
        if value in {"idle", "closed", "completed", "complete", "finished"}:
            if value == "idle" and (
                permission_pending or permission_response_pending
            ):
                return "running"
            if record.get("pending_resume") is True:
                raise RuntimeGatewayError(
                    "RUNTIME_MATERIALIZATION_PENDING",
                    "Paseo resume acknowledgement awaits running or output readback",
                )
        raise RuntimeGatewayError(
            "RUNTIME_LIFECYCLE_UNKNOWN",
            "Paseo status does not prove running, parked, completed, or retired",
        )

    def _call(self, args: list[str], *, allow_empty: bool = False) -> Any:
        _PaseoCliTransport.validate_arguments(args)
        if allow_empty:
            return self._client._run(args, allow_empty=True)  # type: ignore[attr-defined]
        return self._client._run(args)  # type: ignore[attr-defined]

    @staticmethod
    def _git_readback(path: Path, *arguments: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(path), *arguments],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("repository identity readback timed out") from error
        if result.returncode != 0:
            raise OSError("repository identity readback failed")
        value = result.stdout.strip()
        if not value or "\n" in value:
            raise ValueError("repository identity readback is invalid")
        return value

    @classmethod
    def _git_common_dir(cls, path: Path) -> Path:
        value = cls._git_readback(path, "rev-parse", "--git-common-dir")
        if type(value) is not str or not value or "\0" in value:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Git common-directory readback is invalid",
            )
        candidate = Path(value)
        if candidate.is_absolute():
            if not _is_local_absolute_workspace_path(value):
                raise RuntimeGatewayError(
                    "RUNTIME_IDENTITY_AMBIGUOUS",
                    "Git common-directory readback is not a supported local absolute path",
                )
            return candidate.resolve()
        if os.name == "nt":
            drive, _tail = ntpath.splitdrive(value)
            if drive or value.startswith(("\\", "/")):
                raise RuntimeGatewayError(
                    "RUNTIME_IDENTITY_AMBIGUOUS",
                    "Git common-directory readback is not a safe relative path",
                )
        elif value.startswith("/"):
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Git common-directory readback is not a safe relative path",
            )
        # ``path`` is a validated local Workspace/source root.  A relative Git
        # result is interpreted only beneath that trusted local root; rooted,
        # drive-relative, UNC, and device forms were rejected above.
        return (path / candidate).resolve()

    @classmethod
    def _verify_workspace_repository(
        cls,
        context: RuntimeRepositoryContext,
        workspace_path: str,
        *,
        expected_base_commit: str | None,
        allow_descendant: bool = False,
    ) -> str:
        if not _is_local_absolute_workspace_path(workspace_path):
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo Workspace path is not a supported local absolute path",
            )
        source = Path(context.path).resolve()
        workspace = Path(workspace_path).resolve()
        if workspace == source:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo Workspace must not be the configured source checkout",
            )
        if cls._git_common_dir(source) != cls._git_common_dir(workspace):
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo Workspace is not a worktree of the configured repository",
            )
        workspace_head = cls._git_readback(
            workspace, "rev-parse", "HEAD^{commit}"
        )
        if expected_base_commit is not None:
            if _GIT_COMMIT_RE.fullmatch(expected_base_commit) is None:
                raise ValueError("prepared Workspace base commit is invalid")
            if allow_descendant:
                if not cls._git_is_ancestor(
                    workspace, expected_base_commit, workspace_head
                ):
                    raise RuntimeGatewayError(
                        "RUNTIME_IDENTITY_AMBIGUOUS",
                        "Bound Paseo Workspace head is not descended from its pinned base",
                    )
            elif expected_base_commit != workspace_head:
                raise RuntimeGatewayError(
                    "RUNTIME_IDENTITY_AMBIGUOUS",
                    "Prepared Paseo Workspace does not start at its pinned base commit",
                )
        return workspace_head

    @staticmethod
    def _git_is_ancestor(path: Path, ancestor: str, descendant: str) -> bool:
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(path),
                    "merge-base",
                    "--is-ancestor",
                    ancestor,
                    descendant,
                ],
                capture_output=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("repository ancestry readback timed out") from error
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise OSError("repository ancestry readback failed")

    def _one_agent(self, labels: Mapping[str, str], *, include_archived: bool = False) -> Any | None:
        args = ["ls", "--global"]
        if include_archived:
            args.append("--all")
        for key, value in sorted(labels.items()):
            args.extend(["--label", f"{key}={value}"])
        args.append("--json")
        payload = self._call(args)
        values = payload.get("agents", payload) if isinstance(payload, dict) else payload
        if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
            raise ValueError("agent list response is invalid")
        agent_ids = [
            _decode_exact_paseo_alias(
                item,
                ("id", "Id", "agentId", "AgentId"),
                "listed Agent id",
            )
            for item in values
        ]
        if not all(isinstance(agent_id, str) and agent_id for agent_id in agent_ids):
            raise ValueError("agent list omitted an Agent id")
        for agent_id in agent_ids:
            _require_paseo_argument(agent_id, "Paseo listed Agent id")
        if len(agent_ids) > 1:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS", "multiple Paseo Agents match one stable action"
            )
        if not agent_ids:
            return None
        agent = self._client.inspect(agent_ids[0])
        if agent.agent_id != agent_ids[0]:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo Agent inspect identity does not match label readback",
            )
        for field_name in ("agent_id", "provider", "model", "thinking", "mode", "cwd", "lifecycle"):
            _require_paseo_argument(
                getattr(agent, field_name), f"Paseo inspected Agent {field_name}"
            )
        if type(agent.archived) is not bool:
            raise ValueError("Paseo inspect omitted exact Archived state")
        if not isinstance(agent.pending_permissions, tuple):
            raise ValueError("Paseo inspect PendingPermissions is invalid")
        for pending in agent.pending_permissions:
            if (
                not isinstance(pending, tuple)
                or len(pending) != 2
                or not isinstance(pending[0], str)
                or len(pending[0]) < 8
                or len(pending[0]) > _MAXIMUM_PASEO_PERMISSION_TEXT
                or not isinstance(pending[1], str)
                or len(pending[1]) > _MAXIMUM_PASEO_PERMISSION_TEXT
            ):
                raise ValueError("Paseo inspect PendingPermissions is invalid")
            _require_paseo_argument(pending[0], "Paseo pending permission id")
            _require_paseo_argument(pending[1], "Paseo pending permission tool")
        return agent

    def _record_subject(self, record: Mapping[str, Any]) -> tuple[RuntimeSubject, RuntimeProfile]:
        return _subject_from_canonical(record["subject"]), RuntimeProfile(**record["profile"])

    def _observation_identity(
        self,
        record: Mapping[str, Any],
        observation: (
            _PreparedRuntimeObservation | _BoundRuntimeObservation | None
        ) = None,
    ) -> _RuntimeObservationIdentity:
        subject, profile = self._record_subject(record)
        input_digests = record.get("input_artifact_digests")
        if (
            type(input_digests) is not list
            or not all(
                type(digest) is str
                and _DIGEST_RE.fullmatch(digest) is not None
                for digest in input_digests
            )
        ):
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "Runtime input Artifact identity is malformed",
            )
        if type(observation) is _BoundRuntimeObservation:
            binding_ref = observation.binding_ref
            agent_id = observation.agent_id
            session_id = observation.session_id
        else:
            bound_agent_id = record.get("bound_agent_id")
            if type(bound_agent_id) is str and bound_agent_id:
                binding_ref = f"paseo:{bound_agent_id}"
                agent_id = bound_agent_id
                session_id = f"paseo-agent:{bound_agent_id}"
            else:
                binding_ref = None
                agent_id = None
                session_id = None
        return _RuntimeObservationIdentity(
            stable_action_id=subject.stable_action_id,
            repository=subject.repository,
            campaign_key=subject.campaign_key,
            campaign_handle=subject.campaign_handle,
            plan_revision_digest=(
                None
                if type(subject) is CampaignPlanningSubject
                else subject.plan_revision_digest
            ),
            work_run_key=(
                None
                if type(subject) is CampaignPlanningSubject
                else subject.work_run_key
            ),
            subject_digest=subject.digest,
            profile_digest=profile.digest,
            workspace_id=str(record.get("workspace_id")),
            prompt_artifact_digest=str(
                record.get("prompt_artifact_digest")
            ),
            authority_subtree_digest=subject.authority_digest,
            input_artifact_digests=tuple(input_digests),
            spec_identity_digest=self._record_spec_identity_digest(record),
            binding_ref=binding_ref,
            agent_id=agent_id,
            session_id=session_id,
        )

    def _verify_staged_workspace(
        self,
        record: Mapping[str, Any],
        *,
        require_pinned_base: bool,
        require_result_absent: bool = False,
    ) -> tuple[RuntimeSubject, RuntimeProfile, str, str]:
        """Prove registry, repository identity, and staged Artifacts.

        Prepared state additionally proves its pinned base commit.  Bound
        state deliberately does not: normal Worker commits must not turn the
        same exact Workspace into an invalid Runtime Binding.
        """
        subject, profile = self._record_subject(record)
        context = self._contexts.get(subject.repository)
        if context is None:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID", "Runtime repository context is missing"
            )
        workspace_path = record.get("workspace_path")
        if not isinstance(workspace_path, str) or not workspace_path:
            raise ValueError("prepared workspace path is invalid")
        workspace_base_commit = record.get("workspace_base_commit")
        if not isinstance(workspace_base_commit, str):
            raise ValueError("prepared workspace base commit is invalid")
        workspace_id = record.get("workspace_id")
        workspace_slug = record.get("workspace_slug")
        if not isinstance(workspace_id, str) or not isinstance(workspace_slug, str):
            raise ValueError("prepared workspace identity is invalid")
        registered = self._workspace_by_identity(
            slug=workspace_slug, expected=(workspace_id, workspace_path)
        )
        if registered is None:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "prepared Paseo Workspace is absent from exact registry readback",
            )
        workspace_head = self._verify_workspace_repository(
            context,
            registered[1],
            expected_base_commit=workspace_base_commit,
            allow_descendant=not require_pinned_base,
        )
        # The registry's exact readback, not the journal text, becomes the
        # filesystem root.  A matching journal value is still required below.
        files = self._workspace_files(
            workspace_path=registered[1],
            workspace_id=workspace_id,
            workspace_slug=workspace_slug,
            workspace_base_commit=workspace_base_commit,
            ownership_nonce=record["workspace_owner_nonce"],
            subject=subject,
            spec_identity_digest=self._record_spec_identity_digest(record),
        )
        files.assert_record_paths(record)
        fenced = record.get("fenced", False)
        if type(fenced) is not bool:
            raise ValueError("prepared fence state is invalid")
        prompt_digest = _require_digest(
            record.get("prompt_artifact_digest"), "prepared prompt artifact digest"
        )
        files.read_artifact(prompt_digest)
        input_digests = record.get("input_artifact_digests")
        input_files = record.get("input_files")
        if (
            not isinstance(input_digests, list)
            or not all(isinstance(digest, str) for digest in input_digests)
            or not isinstance(input_files, dict)
        ):
            raise ValueError("prepared input Artifact record is invalid")
        for digest in input_digests:
            files.read_artifact(_require_digest(digest, "input artifact digest"))
        schema_file = record.get("output_schema_file")
        schema_digest = record.get("output_schema_digest")
        if not isinstance(schema_file, str) or not schema_file:
            raise ValueError("prepared output schema file is invalid")
        files.read_schema(_require_digest(schema_digest, "output schema digest"))
        if require_result_absent:
            files.require_result_absent()
        return subject, profile, prompt_digest, workspace_head

    def _prepared(
        self,
        record: Mapping[str, Any],
        *,
        selected_stable_action_id: str | None = None,
    ) -> _PreparedRuntimeObservation | _RuntimeObservationRead:
        subject, profile, prompt_digest, _workspace_head = (
            self._verify_staged_workspace(
            record,
            require_pinned_base=True,
            require_result_absent=True,
            )
        )
        fenced = record.get("fenced", False)
        if type(fenced) is not bool:
            raise ValueError("prepared fence state is invalid")
        observation = _PreparedRuntimeObservation(
            stable_action_id=subject.stable_action_id,
            repository=subject.repository,
            campaign_key=subject.campaign_key,
            campaign_handle=subject.campaign_handle,
            plan_revision_digest=(None if isinstance(subject, CampaignPlanningSubject) else subject.plan_revision_digest),
            work_run_key=(None if isinstance(subject, CampaignPlanningSubject) else subject.work_run_key),
            subject_digest=subject.digest,
            profile_digest=profile.digest,
            workspace_id=record["workspace_id"],
            prompt_artifact_digest=prompt_digest,
            fenced=fenced,
            authority_subtree_digest=subject.authority_digest,
        )
        if selected_stable_action_id is None:
            return observation
        identity = self._observation_identity(record, observation)
        return _runtime_sealed_observation_read(
            artifacts=self._artifacts,
            selected_stable_action_id=selected_stable_action_id,
            identity=identity,
            selected_record_digest=digest_value(dict(record)),
            observation=observation,
        )

    def _completed_output(
        self, record: dict[str, Any], subject: RuntimeSubject
    ) -> tuple[str | None, bytes | None]:
        output_digest = record.get("output_artifact_digest")
        pending_artifact: bytes | None = None
        identity = _RuntimeOutputIdentity(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            authority_digest=subject.authority_digest,
        )
        if isinstance(output_digest, str):
            self._artifacts.prove_runtime_output(
                output_digest,
                subject_digest=subject.digest,
                stable_action_id=subject.stable_action_id,
                authority_digest=subject.authority_digest,
            )
            return output_digest, None
        else:
            files = self._workspace_files_from_record(record, subject)
            files.assert_record_paths(record)
            payload = files.read_result()
            if payload is None:
                return None, None
            proof = _prove_runtime_output_bytes(
                payload,
                identity,
                "Paseo result Artifact does not bind its exact action",
            )
            output_digest = proof.artifact_digest
            pending_artifact = payload
        if record.get("output_artifact_digest") != output_digest:
            record["output_artifact_digest"] = output_digest
        return output_digest, pending_artifact

    def _verify_bound_workspace_history(
        self,
        record: dict[str, Any],
        subject: RuntimeSubject,
        current: str,
    ) -> None:
        """Require monotonic descendant history for one Bound worktree."""

        if self._contexts.get(subject.repository) is None:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                "Runtime repository context is missing",
            )
        base = record.get("workspace_base_commit")
        workspace_path = record.get("workspace_path")
        if (
            not isinstance(base, str)
            or _GIT_COMMIT_RE.fullmatch(base) is None
            or not _is_local_absolute_workspace_path(workspace_path)
        ):
            raise ValueError("Bound Workspace history identity is invalid")
        previous = record.get("workspace_observed_head_commit", base)
        if (
            not isinstance(previous, str)
            or _GIT_COMMIT_RE.fullmatch(previous) is None
            or not self._git_is_ancestor(
                Path(workspace_path), previous, current
            )
        ):
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Bound Paseo Workspace head rewound or changed ancestry",
            )
        if record.get("workspace_observed_head_commit") != current:
            record["workspace_observed_head_commit"] = current

    def _bound(
        self,
        record: dict[str, Any],
        agent: _PaseoAgentReadback,
        *,
        selected_stable_action_id: str | None = None,
    ) -> _BoundRuntimeObservation | _RuntimeObservationRead:
        durable_record = record
        record = deepcopy(record)
        subject, profile = self._record_subject(record)
        # Establish Workspace registry and repository provenance first.  That
        # routine rejects every journal path by pure fixed-path comparison
        # before it reads a marker or touches a filesystem path.
        (
            _verified_subject,
            _verified_profile,
            _verified_prompt,
            workspace_head,
        ) = self._verify_staged_workspace(record, require_pinned_base=False)
        files = self._workspace_files_from_record(record, subject)
        output_may_exist = isinstance(
            record.get("output_artifact_digest"), str
        ) or files.verify_result_target()
        if (
            agent.archived is not True
            and not output_may_exist
            and agent.lifecycle.casefold() not in {"running", "busy", "idle"}
        ):
            # Reject an impossible Bound lifecycle before workspace-history,
            # wake, fence, or acknowledgement state can be published.
            raise RuntimeGatewayError(
                "RUNTIME_LIFECYCLE_UNKNOWN",
                "Paseo status does not prove running, parked, completed, or retired",
            )
        # A Bound readback must continue to prove the same staged prompt,
        # inputs, output schema, and workspace registry identity as Prepared.
        # The initial verification above intentionally happens before result
        # existence inspection, so reuse its verified Workspace head.
        self._verify_bound_workspace_history(
            record, subject, workspace_head
        )
        if profile.features:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                "Paseo V3 cannot prove non-empty Runtime Profile features",
            )
        expected_provider = "kimi" if profile.provider == "kimi-cli" else profile.provider
        if (
            agent.provider != expected_provider
            or agent.model != profile.model
            or agent.thinking != profile.thinking
            or agent.mode != profile.mode
        ):
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo Agent inspect profile does not match the exact Runtime Profile",
            )
        context = self._contexts.get(subject.repository)
        if context is None:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID", "Paseo repository context is missing"
            )
        workspace = self._workspace_for_agent(record, context, agent.cwd)
        if workspace is None:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo Agent cwd does not join the exact action Workspace",
            )
        workspace_id, workspace_path = workspace
        if (
            workspace_id != record["workspace_id"]
            or Path(workspace_path).resolve()
            != Path(str(record["workspace_path"])).resolve()
        ):
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo Workspace readback changed the action identity",
            )
        fenced = record.get("fenced", False)
        pending_fence = record.get("pending_fence", False)
        pending_fence_claim_id = record.get("pending_fence_claim_id")
        pending_fence_quiesced = record.get("pending_fence_quiesced", False)
        if (
            type(fenced) is not bool
            or type(pending_fence) is not bool
            or (
                pending_fence_claim_id is not None
                and (
                    not isinstance(pending_fence_claim_id, str)
                    or not pending_fence_claim_id
                )
            )
            or type(pending_fence_quiesced) is not bool
        ):
            raise ValueError("Paseo fence state is invalid")
        labels = self._labels(
            _RuntimeActionSpec(
                stable_action_id=subject.stable_action_id,
                subject=subject,
                profile=profile,
                prompt_artifact=self._artifacts.get(record["prompt_artifact_digest"]),
                input_artifacts=(),
            )
        )
        fenced_agent = self._one_agent(
            {**labels, "gwo.runtime_fenced": "true"}, include_archived=True
        )
        if fenced_agent is not None and fenced_agent.agent_id != agent.agent_id:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo fence label readback selected another Agent",
            )
        if fenced_agent is None and (
            (
                pending_fence_quiesced
                and (
                    not pending_fence
                    or not isinstance(pending_fence_claim_id, str)
                )
            )
            or (not pending_fence and pending_fence_claim_id is not None)
        ):
            raise ValueError("Paseo fence ownership evidence is invalid")
        if fenced_agent is not None and not fenced and pending_fence:
            record.update(
                {
                    "fenced": True,
                    "pending_fence": False,
                    "pending_fence_claim_id": None,
                    "pending_fence_quiesced": False,
                }
            )
            fenced = True
        elif fenced_agent is not None and fenced and pending_fence:
            record.update(
                {
                    "pending_fence": False,
                    "pending_fence_claim_id": None,
                    "pending_fence_quiesced": False,
                }
            )
        elif (
            fenced_agent is None
            and not fenced
            and pending_fence
            and isinstance(pending_fence_claim_id, str)
            and pending_fence_quiesced
        ):
            # Exact negative label readback is retry authority only after the
            # effect owner durably proved its provider call has returned.
            # Clear only that quiesced claim under the record CAS.
            record.update(
                {
                    "pending_fence": False,
                    "pending_fence_claim_id": None,
                    "pending_fence_quiesced": False,
                }
            )
        if (fenced_agent is not None) != fenced:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo fence label readback does not match the action record",
            )
        binding_ref = f"paseo:{agent.agent_id}"
        permissions = self._permissions(agent, subject, binding_ref)
        completed_permission_response = self._completed_permission_response(
            record, subject, agent.agent_id
        )
        pending_response = record.get("pending_permission_response")
        pending_request: _PermissionRequest | None = None
        if isinstance(pending_response, dict) and "request" in pending_response:
            try:
                pending_request = _permission_request_from_value(
                    pending_response["request"]
                )
            except RuntimeGatewayError as error:
                raise ValueError(
                    "Paseo pending permission response is invalid"
                ) from error
        if pending_response is not None and (
            not isinstance(pending_response, dict)
            or set(pending_response)
            != {
                "request_id",
                "decision",
                "request",
                "request_digest",
                "provider_receipt",
            }
            or not isinstance(pending_response["request_id"], str)
            or pending_response["decision"] not in {"allow", "deny"}
            or type(pending_request) is not _PermissionRequest
            or pending_request.request_id != pending_response["request_id"]
            or pending_request.stable_action_id != subject.stable_action_id
            or pending_request.subject_digest != subject.digest
            or pending_request.binding_ref != binding_ref
            or pending_request.authority_subtree_digest
            != subject.authority_digest
            or not isinstance(pending_response["request_digest"], str)
            or _DIGEST_RE.fullmatch(pending_response["request_digest"]) is None
            or pending_response["request_digest"]
            != digest_value(asdict(pending_request))
            or (
                pending_response["provider_receipt"] is not None
                and not _paseo_permission_receipt_is_bound(
                    pending_response["provider_receipt"],
                    request=pending_request,
                    decision=pending_response["decision"],
                    agent_id=agent.agent_id,
                )
            )
        ):
            raise ValueError("Paseo pending permission response is invalid")
        response_still_pending = (
            isinstance(pending_response, dict)
            and any(
                request.request_id == pending_response["request_id"]
                for request in permissions
            )
        )
        if (
            isinstance(pending_response, dict)
            and not response_still_pending
            and pending_response["provider_receipt"] is None
        ):
            raise RuntimeGatewayError(
                "RUNTIME_EFFECT_AMBIGUOUS",
                "Paseo permission request disappeared without a same-decision receipt",
            )
        response_effect_observed = (
            isinstance(pending_response, dict)
            # This record is written only after an exact pre-command match.
            # It therefore cannot turn a stale/nonexistent ID into a vacuous
            # success merely because a later list happens not to contain it.
            and pending_response["provider_receipt"] is not None
            and not response_still_pending
        )
        if agent.archived is True:
            if record.get("pending_retire") is True:
                record["pending_retire"] = False
            output_digest = record.get("output_artifact_digest")
            pending_output_artifact = None
            if output_digest is not None and not isinstance(output_digest, str):
                raise ValueError("retired Paseo output Artifact record is invalid")
            lifecycle = "retired"
        else:
            output_digest, pending_output_artifact = self._completed_output(
                record, subject
            )
            lifecycle = self._lifecycle(
                record,
                agent,
                output_exists=output_digest is not None,
                permission_pending=bool(permissions),
                permission_response_pending=response_effect_observed,
            )
        if response_effect_observed:
            assert isinstance(pending_response, dict)
            receipt = pending_response["provider_receipt"]
            assert isinstance(receipt, dict)
            completion_record = {
                "request_id": pending_response["request_id"],
                "decision": pending_response["decision"],
                "request": pending_response["request"],
                "request_digest": pending_response["request_digest"],
                "provider_receipt": receipt,
                "provider_receipt_digest": digest_value(receipt),
                "stable_action_id": subject.stable_action_id,
                "subject_digest": subject.digest,
                "binding_ref": binding_ref,
            }
            record["pending_permission_response"] = None
            record["completed_permission_response"] = completion_record
            completed_permission_response = self._completed_permission_response(
                record, subject, agent.agent_id
            )
        bound_agent_id = record.get("bound_agent_id")
        if bound_agent_id is not None and bound_agent_id != agent.agent_id:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo Bound observation changed the exact Agent identity",
            )
        if bound_agent_id is None or record.get("pending_start") is True:
            record.update(
                {
                    "bound_agent_id": agent.agent_id,
                    "binding_established": True,
                    "pending_start": False,
                }
            )
        observation = _BoundRuntimeObservation(
            stable_action_id=subject.stable_action_id,
            binding_ref=binding_ref,
            repository=subject.repository,
            campaign_key=subject.campaign_key,
            campaign_handle=subject.campaign_handle,
            plan_revision_digest=(None if isinstance(subject, CampaignPlanningSubject) else subject.plan_revision_digest),
            work_run_key=(None if isinstance(subject, CampaignPlanningSubject) else subject.work_run_key),
            subject_digest=subject.digest,
            profile_digest=profile.digest,
            agent_id=agent.agent_id,
            # Paseo inspect exposes no Provider session identity.  This stable
            # adapter-derived reference is the sole V3 session representation.
            session_id=f"paseo-agent:{agent.agent_id}",
            workspace_id=record["workspace_id"],
            prompt_artifact_digest=record["prompt_artifact_digest"],
            prompt_accepted=True,
            lifecycle=lifecycle,
            permission_requests=permissions,
            fenced=fenced,
            authority_subtree_digest=subject.authority_digest,
            planning_output_artifact_digest=output_digest,
            completed_permission_response=completed_permission_response,
        )
        if _runtime_event_observation_state(observation) is None:
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "Paseo Bound observation is outside the closed observation contract",
            )
        if pending_output_artifact is not None:
            output_ref = self._artifacts.put(pending_output_artifact)
            if output_ref.digest != output_digest:
                raise RuntimeGatewayError(
                    "RUNTIME_ARTIFACT_DIGEST_MISMATCH",
                    "Paseo result Artifact digest changed during publication",
                )
        if record != durable_record:
            candidate = deepcopy(record)

            def publish_validated(updated: dict[str, Any]) -> None:
                updated.clear()
                updated.update(candidate)

            self._persist_record_update(
                durable_record,
                publish_validated,
            )
        if selected_stable_action_id is None:
            return observation
        identity = self._observation_identity(record, observation)
        return _runtime_sealed_observation_read(
            artifacts=self._artifacts,
            selected_stable_action_id=selected_stable_action_id,
            identity=identity,
            selected_record_digest=digest_value(record),
            observation=observation,
        )

    def _permissions(
        self,
        agent: _PaseoAgentReadback,
        subject: RuntimeSubject,
        binding_ref: str,
    ) -> tuple[_PermissionRequest, ...]:
        """Join Paseo's two permission projections without guessing meaning.

        Paseo 0.2.3 exposes a short id/name/description descriptor from
        ``permit ls`` and the actionable full id/tool from ``inspect``.  The
        short id is only a join key.  Any missing, excess, duplicate, or
        colliding entry makes the Provider readback unusable.
        """
        payload = self._call(["permit", "ls", "--json"])
        values = payload.get("permissions", payload) if isinstance(payload, dict) else payload
        if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
            raise ValueError("permission list response is invalid")
        descriptors: dict[str, tuple[str, str]] = {}
        for raw in values:
            if set(raw) != {"id", "agentId", "agentShortId", "name", "description"}:
                raise ValueError("Paseo permit list descriptor shape is invalid")
            owner = raw["agentId"]
            owner_short_id = raw["agentShortId"]
            if not isinstance(owner, str) or not isinstance(owner_short_id, str):
                raise ValueError("Paseo permission owner is invalid")
            _require_paseo_argument(owner, "Paseo permission owner")
            if owner_short_id != owner[:7]:
                raise RuntimeGatewayError(
                    "RUNTIME_IDENTITY_AMBIGUOUS",
                    "Paseo permit descriptor owner short id does not match agent id",
                )
            _require_paseo_argument(owner_short_id, "Paseo permission owner short id")
            if owner != agent.agent_id:
                continue
            short_id = raw["id"]
            name = raw["name"]
            description = raw["description"]
            if (
                not isinstance(short_id, str)
                or len(short_id) != 8
                or not isinstance(name, str)
                or not name
                or len(name) > _MAXIMUM_PASEO_PERMISSION_TEXT
                or not isinstance(description, str)
                or not description
                or len(description) > _MAXIMUM_PASEO_PERMISSION_TEXT
            ):
                raise ValueError("Paseo permit descriptor fields are invalid")
            _require_paseo_argument(short_id, "Paseo permission short id")
            if short_id in descriptors:
                raise RuntimeGatewayError(
                    "RUNTIME_IDENTITY_AMBIGUOUS",
                    "Paseo permit list contains a colliding short permission id",
                )
            descriptors[short_id] = (name, description)
        if not isinstance(agent.pending_permissions, tuple):
            raise ValueError("Paseo inspect PendingPermissions is invalid")
        pending: dict[str, str] = {}
        for raw_pending in agent.pending_permissions:
            if (
                not isinstance(raw_pending, tuple)
                or len(raw_pending) != 2
                or not isinstance(raw_pending[0], str)
                or len(raw_pending[0]) < 8
                or len(raw_pending[0]) > _MAXIMUM_PASEO_PERMISSION_TEXT
                or not isinstance(raw_pending[1], str)
                or len(raw_pending[1]) > _MAXIMUM_PASEO_PERMISSION_TEXT
            ):
                raise ValueError("Paseo inspect PendingPermissions entry is invalid")
            full_id, tool = raw_pending
            _require_paseo_argument(full_id, "Paseo pending permission id")
            _require_paseo_argument(tool, "Paseo pending permission tool")
            if full_id in pending:
                raise RuntimeGatewayError(
                    "RUNTIME_IDENTITY_AMBIGUOUS",
                    "Paseo inspect contains duplicate pending permission ids",
                )
            pending[full_id] = tool
        if len({full_id[:8] for full_id in pending}) != len(pending):
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo inspect contains colliding pending permission prefixes",
            )
        if set(descriptors) != {full_id[:8] for full_id in pending}:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo permit list and inspect PendingPermissions do not join exactly",
            )
        normalized: list[_PermissionRequest] = []
        for full_id, tool in pending.items():
            short_id = full_id[:8]
            name, description = descriptors[short_id]
            if name != tool:
                raise RuntimeGatewayError(
                    "RUNTIME_IDENTITY_AMBIGUOUS",
                    "Paseo pending permission tool does not match its descriptor name",
                )
            identity = {
                "provider": "paseo/0.2.3",
                "tool": tool,
                "name": name,
                "description": description,
            }
            normalized.append(
                _PermissionRequest(
                    # The full inspect id is opaque but is the only value Paseo
                    # accepts for permit allow/deny.  The public transition
                    # never accepts a lossy short prefix.
                    request_id=full_id,
                    operation_id=_paseo_permission_operation_id(
                        tool,
                        name,
                    ),
                    resource_id="paseo/0.2.3:resource:" + digest_value(identity),
                    binding_ref=binding_ref,
                    authority_subtree_digest=subject.authority_digest,
                    stable_action_id=subject.stable_action_id,
                    subject_digest=subject.digest,
                )
            )
        return tuple(
            sorted(
                normalized,
                key=lambda request: (
                    request.request_id,
                    request.operation_id,
                    request.resource_id,
                ),
            )
        )

    @staticmethod
    def _verify_permission_decision_receipt(
        payload: Any,
        command: PermissionResponse,
        agent_id: str,
        request: _PermissionRequest,
    ) -> dict[str, str]:
        """Accept only Paseo's exact same-decision permission receipt."""

        # Paseo 0.2.3 renders permit allow/deny JSON as a singleton array.
        # The receipt exposes only the request's eight-character prefix; the
        # command still sends the opaque full inspect identifier.
        if not isinstance(payload, list) or len(payload) != 1:
            raise RuntimeGatewayError(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "Paseo permission decision receipt is invalid",
            )
        value = payload[0]
        if not isinstance(value, dict) or set(value) != {
            "requestId", "agentId", "agentShortId", "name", "result"
        }:
            raise RuntimeGatewayError(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "Paseo permission decision receipt is invalid",
            )
        request_id = value["requestId"]
        receipt_agent_id = value["agentId"]
        agent_short_id = value["agentShortId"]
        name = value["name"]
        result = value["result"]
        if not all(isinstance(part, str) and part for part in value.values()):
            raise RuntimeGatewayError(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "Paseo permission decision receipt is invalid",
            )
        if (
            request_id != command.request_id[:8]
            or receipt_agent_id != agent_id
            or agent_short_id != agent_id[:7]
            or result != ("allowed" if command.decision == "allow" else "denied")
            or request.operation_id
            != _paseo_permission_operation_id(name, name)
        ):
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo permission decision receipt does not bind the exact request",
            )
        normalized = {
            "requestId": request_id,
            "agentId": receipt_agent_id,
            "agentShortId": agent_short_id,
            # Retained evidence must be self-binding.  The native tool name
            # was checked above; the durable receipt stores the normalized
            # operation identity used by the request.
            "name": request.operation_id,
            "result": result,
        }
        if not _paseo_permission_receipt_is_bound(
            normalized,
            request=request,
            decision=command.decision,
            agent_id=agent_id,
        ):
            raise RuntimeGatewayError(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "Paseo permission decision receipt normalization failed",
            )
        return normalized

    @staticmethod
    def _completed_permission_response(
        record: Mapping[str, Any],
        subject: RuntimeSubject,
        agent_id: str,
    ) -> _CompletedPermissionResponse | None:
        """Read the one retained, provider-neutral permission completion proof."""

        value = record.get("completed_permission_response")
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {
            "request_id",
            "decision",
            "request",
            "request_digest",
            "provider_receipt",
            "provider_receipt_digest",
            "stable_action_id",
            "subject_digest",
            "binding_ref",
        }:
            raise ValueError("Paseo completed permission response is invalid")
        receipt = value["provider_receipt"]
        try:
            request = _permission_request_from_value(value["request"])
        except RuntimeGatewayError as error:
            raise ValueError(
                "Paseo completed permission response is invalid"
            ) from error
        if (
            not isinstance(receipt, dict)
            or set(receipt) != {"requestId", "agentId", "agentShortId", "name", "result"}
            or not all(isinstance(part, str) and part for part in receipt.values())
            or not all(
                isinstance(value[key], str) and value[key]
                for key in (
                    "request_id",
                    "decision",
                    "request_digest",
                    "provider_receipt_digest",
                    "stable_action_id",
                    "subject_digest",
                    "binding_ref",
                )
            )
            or value["decision"] not in {"allow", "deny"}
            or _DIGEST_RE.fullmatch(value["request_digest"]) is None
            or value["request_digest"] != digest_value(asdict(request))
            or _DIGEST_RE.fullmatch(value["provider_receipt_digest"]) is None
            or value["provider_receipt_digest"] != digest_value(receipt)
            or not _paseo_permission_receipt_is_bound(
                receipt,
                request=request,
                decision=value["decision"],
                agent_id=agent_id,
            )
            or value["stable_action_id"] != subject.stable_action_id
            or value["subject_digest"] != subject.digest
            or value["binding_ref"] != f"paseo:{agent_id}"
            or request.request_id != value["request_id"]
            or request.stable_action_id != subject.stable_action_id
            or request.subject_digest != subject.digest
            or request.binding_ref != value["binding_ref"]
            or request.authority_subtree_digest != subject.authority_digest
        ):
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo completed permission response does not bind this Runtime action",
            )
        return _CompletedPermissionResponse(
            request_id=value["request_id"],
            decision=value["decision"],
            request=request,
            request_digest=value["request_digest"],
            provider_receipt=deepcopy(receipt),
            provider_receipt_digest=value["provider_receipt_digest"],
            stable_action_id=value["stable_action_id"],
            subject_digest=value["subject_digest"],
            binding_ref=value["binding_ref"],
        )

    def _workspace_by_identity(
        self,
        *,
        slug: str,
        expected: tuple[str, str] | None = None,
    ) -> tuple[str, str] | None:
        values = self._workspace_registry()
        related: list[tuple[str, str, Mapping[str, Any], bool]] = []
        expected_id = None if expected is None else expected[0]
        expected_path = None if expected is None else expected[1]
        if expected is not None and not _is_local_absolute_workspace_path(
            expected_path
        ):
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo durable Workspace path is not a supported local absolute path",
            )
        for item in values:
            workspace_id, workspace_path = self._workspace_payload(item)
            slug_candidate = (
                item.get("isolation") == "worktree"
                and item.get("name") == slug
            )
            is_related = slug_candidate or (
                expected_id is not None and workspace_id == expected_id
            ) or (
                expected_path is not None
                and workspace_path == expected_path
            )
            if is_related:
                if not _is_local_absolute_workspace_path(workspace_path):
                    raise RuntimeGatewayError(
                        "RUNTIME_IDENTITY_AMBIGUOUS",
                        "Paseo target Workspace path is not a supported local absolute path",
                    )
                related.append(
                    (workspace_id, workspace_path, item, slug_candidate)
                )
        if not related:
            return None
        matches = [
            (workspace_id, workspace_path)
            for workspace_id, workspace_path, _item, slug_candidate in related
            if slug_candidate
            and (
                expected is None
                or (
                    workspace_id == expected_id
                    and (
                        workspace_path == expected_path
                        or (
                            # All target/durable paths were lexical-local
                            # checked above before either can be resolved.
                            Path(workspace_path).resolve()
                            == Path(str(expected_path)).resolve()
                        )
                    )
                )
            )
        ]
        if len(related) != 1 or len(matches) != 1:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo target Workspace identity has conflicting registry candidates",
            )
        return matches[0]

    def _workspace_registry(self) -> list[dict[str, Any]]:
        """Decode every row without imposing unsupported global uniqueness."""

        payload = self._call(["workspace", "ls", "--json"])
        values = payload.get("workspaces", payload) if isinstance(payload, dict) else payload
        if type(values) is not list or not all(
            type(item) is dict for item in values
        ):
            raise ValueError("workspace list response is invalid")
        for item in values:
            self._workspace_payload(item)
            name = item.get("name")
            isolation = item.get("isolation")
            if type(name) is not str or not name:
                raise ValueError("workspace list omitted a Workspace name")
            if type(isolation) is not str or not isolation:
                raise ValueError("workspace list omitted Workspace isolation")
        return values

    def _workspace_for_agent(
        self,
        record: Mapping[str, Any],
        context: RuntimeRepositoryContext,
        agent_cwd: str,
    ) -> tuple[str, str] | None:
        expected_id = record.get("workspace_id")
        expected_slug = record.get("workspace_slug")
        expected_path = record.get("workspace_path")
        if not all(
            type(value) is str and bool(value)
            for value in (
                expected_id,
                expected_slug,
                expected_path,
                agent_cwd,
            )
        ):
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo target Workspace identity is incomplete",
            )
        if not _is_local_absolute_workspace_path(expected_path):
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo durable Workspace path is not a supported local absolute path",
            )
        if not _is_local_absolute_workspace_path(agent_cwd):
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo inspected Agent cwd is not a supported local absolute path",
            )
        workspace = self._workspace_by_identity(
            slug=expected_slug,
            expected=(expected_id, expected_path),
        )
        if workspace is None:
            return None
        if Path(workspace[1]).resolve() != Path(agent_cwd).resolve():
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo Agent cwd changed from its exact Workspace",
            )
        return workspace

    @staticmethod
    def _spec_identity_digest(spec: _RuntimeActionSpec) -> str:
        return _runtime_action_spec_identity(spec)

    @staticmethod
    def _record_spec_identity_digest(record: Mapping[str, Any]) -> str:
        input_digests = record.get("input_artifact_digests")
        if (
            not isinstance(input_digests, list)
            or not all(isinstance(digest, str) for digest in input_digests)
        ):
            raise RuntimeGatewayError(
                "RUNTIME_WORKSPACE_UNSAFE",
                "Runtime Workspace input identity is invalid",
            )
        subject = _subject_from_canonical(record.get("subject"))
        profile_value = record.get("profile")
        if type(profile_value) is not dict:
            raise RuntimeGatewayError(
                "RUNTIME_WORKSPACE_UNSAFE",
                "Runtime Workspace Profile identity is invalid",
            )
        profile = RuntimeProfile(**profile_value)
        return digest_value(
            {
                "stable_action_id": subject.stable_action_id,
                "subject": subject.canonical(),
                "profile": profile.canonical(),
                "prompt_artifact_digest": record.get(
                    "prompt_artifact_digest"
                ),
                "input_artifact_digests": input_digests,
            }
        )

    def _workspace_files(
        self,
        *,
        workspace_path: str,
        workspace_id: str,
        workspace_slug: str,
        workspace_base_commit: str,
        ownership_nonce: str,
        subject: RuntimeSubject,
        spec_identity_digest: str,
    ) -> _RuntimeWorkspaceFiles:
        return _RuntimeWorkspaceFiles(
            workspace_path=workspace_path,
            workspace_id=workspace_id,
            workspace_slug=workspace_slug,
            workspace_base_commit=workspace_base_commit,
            ownership_nonce=ownership_nonce,
            repository=subject.repository,
            stable_action_id=subject.stable_action_id,
            subject_digest=subject.digest,
            spec_identity_digest=spec_identity_digest,
            maximum_bytes=self._artifacts.maximum_bytes,
        )

    def _workspace_files_from_record(
        self,
        record: Mapping[str, Any],
        subject: RuntimeSubject | None = None,
    ) -> _RuntimeWorkspaceFiles:
        if subject is None:
            subject, _profile = self._record_subject(record)
        required = {
            key: record.get(key)
            for key in (
                "workspace_path",
                "workspace_id",
                "workspace_slug",
                "workspace_base_commit",
                "workspace_owner_nonce",
            )
        }
        if not all(isinstance(value, str) and value for value in required.values()):
            raise RuntimeGatewayError(
                "RUNTIME_WORKSPACE_UNSAFE",
                "Runtime Workspace ownership record is invalid",
            )
        if (
            record.get("workspace_layout_version")
            != _RUNTIME_WORKSPACE_LAYOUT_VERSION
        ):
            raise RuntimeGatewayError(
                "RUNTIME_WORKSPACE_UNSAFE",
                "Runtime Workspace layout version is invalid",
            )
        return self._workspace_files(
            workspace_path=required["workspace_path"],
            workspace_id=required["workspace_id"],
            workspace_slug=required["workspace_slug"],
            workspace_base_commit=required["workspace_base_commit"],
            ownership_nonce=required["workspace_owner_nonce"],
            subject=subject,
            spec_identity_digest=self._record_spec_identity_digest(record),
        )

    @staticmethod
    def _verify_reserved_runtime_tree_absent(
        context: RuntimeRepositoryContext, base_commit: str
    ) -> None:
        """Reserve casefold-equivalent ``.gwo`` before any mutating effect."""

        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(context.path),
                    "ls-tree",
                    "-z",
                    "--name-only",
                    base_commit,
                ],
                capture_output=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("repository reserved-tree readback timed out") from error
        if result.returncode != 0:
            raise OSError("repository reserved-tree readback failed")
        top_level_names = (
            os.fsdecode(name)
            for name in result.stdout.split(b"\0")
            if name
        )
        if any(name.casefold() == ".gwo" for name in top_level_names):
            raise RuntimeGatewayError(
                "RUNTIME_WORKSPACE_UNSAFE",
                "The pinned repository base owns a casefold-equivalent reserved .gwo tree",
            )

    @staticmethod
    def _action_matches_spec(
        action: object, spec: _RuntimeActionSpec
    ) -> bool:
        return (
            isinstance(action, dict)
            and action.get("subject_digest") == spec.subject_digest
            and action.get("profile_digest") == spec.profile.digest
            and action.get("prompt_artifact_digest")
            == spec.prompt_artifact.digest
            and action.get("input_artifact_digests")
            == [item.digest for item in spec.input_artifacts]
        )

    def _prepared_receipt_from_action(
        self, spec: _RuntimeActionSpec, action: dict[str, Any]
    ) -> _PrepareReceipt:
        if not self._action_matches_spec(action, spec):
            raise RuntimeGatewayError(
                "RUNTIME_ACTION_IDENTITY_MISMATCH",
                "stable action changed during prepare",
            )
        self._artifacts.get(str(action["prompt_artifact_digest"]))
        for digest in action["input_artifact_digests"]:
            self._artifacts.get(digest)
        agent = self._one_agent(self._labels(spec), include_archived=True)
        bound_agent_id = action.get("bound_agent_id")
        if agent is not None:
            if (
                bound_agent_id is not None
                and bound_agent_id != agent.agent_id
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_IDENTITY_AMBIGUOUS",
                    "Bound prepare replay changed the exact Agent identity",
                )
            self._bound(action, agent)
        elif isinstance(bound_agent_id, str):
            raise RuntimeGatewayError(
                "RUNTIME_BINDING_MISSING",
                "Bound prepare replay cannot prove its exact Paseo Agent",
            )
        else:
            self._verify_staged_workspace(
                action,
                require_pinned_base=True,
                require_result_absent=True,
            )
        return _PrepareReceipt(spec.stable_action_id, str(action["workspace_id"]))

    def _ensure_workspace_intent(
        self,
        spec: _RuntimeActionSpec,
        context: RuntimeRepositoryContext,
        slug: str,
    ) -> dict[str, str] | None:
        """Freeze complete local workspace identity before any Paseo call."""

        stable_action_id = spec.stable_action_id
        existing = self._workspace_intents.get(stable_action_id)
        ownership_nonce = (
            uuid4().hex
            if existing is None
            else existing.get("ownership_nonce")
            if isinstance(existing, dict)
            else None
        )
        expected_without_base = {
            "repository_path": str(Path(context.path).resolve()),
            "slug": slug,
            "branch": f"gwo-{slug}",
            "spec_identity_digest": self._spec_identity_digest(spec),
            "ownership_nonce": ownership_nonce,
            "layout_version": _RUNTIME_WORKSPACE_LAYOUT_VERSION,
        }
        if existing is None:
            base_commit = self._git_readback(
                Path(context.path), "rev-parse", f"{context.base_ref}^{{commit}}"
            )
            if _GIT_COMMIT_RE.fullmatch(base_commit) is None:
                raise ValueError("configured Workspace base does not resolve to one commit")
            proposed = {
                **expected_without_base,
                "base_commit": base_commit,
                "phase": "recorded",
            }
        else:
            proposed = dict(existing)

        def commit(state: dict[str, Any]) -> dict[str, str] | None:
            current_action = state["actions"].get(stable_action_id)
            if current_action is not None:
                if not self._action_matches_spec(current_action, spec):
                    raise RuntimeGatewayError(
                        "RUNTIME_ACTION_IDENTITY_MISMATCH",
                        "stable action changed while recording its Workspace intent",
                    )
                # A stale adapter may have passed its initial action read
                # before another process committed Prepared.  The durable
                # action is authoritative, and any older intent must vanish
                # in this same transaction.
                state["workspace_intents"].pop(stable_action_id, None)
                return None
            current = state["workspace_intents"].get(stable_action_id)
            if current is None:
                state["workspace_intents"][stable_action_id] = deepcopy(proposed)
                return deepcopy(proposed)
            if (
                not isinstance(current, dict)
                or any(
                    current.get(key) != value
                    for key, value in expected_without_base.items()
                )
                or not isinstance(current.get("base_commit"), str)
                or _GIT_COMMIT_RE.fullmatch(current["base_commit"]) is None
                or not isinstance(current.get("ownership_nonce"), str)
                or re.fullmatch(r"[0-9a-f]{32}", current["ownership_nonce"])
                is None
                or current.get("layout_version")
                != _RUNTIME_WORKSPACE_LAYOUT_VERSION
                or current.get("phase") not in {"recorded", "create_pending"}
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_ACTION_IDENTITY_MISMATCH",
                    "Paseo Workspace intent changed for one stable action",
                )
            return deepcopy(current)

        return self._transact(commit)

    def _workspace_for_prepare(
        self,
        spec: _RuntimeActionSpec,
        context: RuntimeRepositoryContext,
        slug: str,
    ) -> tuple[str, str, str]:
        stable_action_id = spec.stable_action_id
        existing_intent = self._workspace_intents.get(stable_action_id)
        if not isinstance(existing_intent, dict):
            raise RuntimeGatewayError(
                "RUNTIME_ACTION_IDENTITY_MISMATCH",
                "Paseo Workspace intent is absent before prepare readback",
            )
        base_commit = existing_intent.get("base_commit")
        expected_intent = {
            "repository_path": str(Path(context.path).resolve()),
            "base_commit": base_commit,
            "slug": slug,
            "branch": existing_intent.get("branch"),
            "spec_identity_digest": self._spec_identity_digest(spec),
            "ownership_nonce": existing_intent.get("ownership_nonce"),
            "layout_version": existing_intent.get("layout_version"),
            "phase": existing_intent.get("phase"),
        }
        if (
            existing_intent != expected_intent
            or not isinstance(base_commit, str)
            or not isinstance(existing_intent.get("ownership_nonce"), str)
            or re.fullmatch(
                r"[0-9a-f]{32}", existing_intent["ownership_nonce"]
            )
            is None
            or existing_intent.get("layout_version")
            != _RUNTIME_WORKSPACE_LAYOUT_VERSION
            or existing_intent.get("phase") not in {"recorded", "create_pending"}
        ):
            raise RuntimeGatewayError(
                "RUNTIME_ACTION_IDENTITY_MISMATCH",
                "Paseo Workspace intent changed for one stable action",
            )
        if _GIT_COMMIT_RE.fullmatch(base_commit) is None:
            raise ValueError("Paseo Workspace intent base commit is invalid")
        self._verify_reserved_runtime_tree_absent(context, base_commit)
        recovered = self._workspace_by_identity(slug=slug)
        if recovered is not None:
            self._verify_workspace_repository(
                context, recovered[1], expected_base_commit=base_commit
            )
            return (*recovered, base_commit)
        if existing_intent["phase"] == "create_pending":
            # There is intentionally no lease or takeover for this phase.
            # Exact absence cannot distinguish a claim-before-effect crash
            # from an acknowledgement loss.  Kernel performs bounded polling
            # and escalates to Blocked; it must never authorize a second
            # provider create effect.
            raise RuntimeGatewayError(
                "RUNTIME_MATERIALIZATION_PENDING",
                "Paseo Workspace creation awaits exact action-owned Workspace readback",
            )

        def claim_create(state: dict[str, Any]) -> bool:
            current = state["workspace_intents"].get(stable_action_id)
            if current == existing_intent:
                current["phase"] = "create_pending"
                return True
            if (
                isinstance(current, dict)
                and current.get("phase") == "create_pending"
                and all(
                    current.get(key) == value
                    for key, value in existing_intent.items()
                    if key != "phase"
                )
            ):
                return False
            raise RuntimeGatewayError(
                "RUNTIME_ACTION_IDENTITY_MISMATCH",
                "Paseo Workspace create claim lost its exact intent CAS",
            )

        # Supplying the frozen action-owned branch prevents Paseo 0.2.3 from
        # selecting a random branch identity for branch-off Workspaces.
        workspace_branch = existing_intent["branch"]
        create_args = [
            "workspace", "create", "--isolation", "worktree", "--path", str(context.path),
            "--mode", "branch-off", "--worktree-slug", slug,
            "--new-branch", workspace_branch,
            "--base", base_commit, "--title", slug, "--json",
        ]
        _PaseoCliTransport.validate_arguments(create_args)
        if not self._transact(claim_create):
            raise RuntimeGatewayError(
                "RUNTIME_MATERIALIZATION_PENDING",
                "Paseo Workspace creation awaits exact action-owned Workspace readback",
            )
        try:
            workspace = self._call(create_args)
        except Exception as create_error:
            if self._was_not_dispatched(create_error):
                # Process creation failure proves the external effect never
                # began. Restore retryability before any registry readback;
                # registry availability is independent of that proof.
                claimed_intent = deepcopy(
                    self._workspace_intents.get(stable_action_id)
                )

                def restore_rejected_create(
                    state: dict[str, Any],
                ) -> None:
                    current = state["workspace_intents"].get(
                        stable_action_id
                    )
                    if (
                        current != claimed_intent
                        or not isinstance(current, dict)
                        or current.get("phase") != "create_pending"
                    ):
                        raise RuntimeGatewayError(
                            "RUNTIME_ACTION_IDENTITY_MISMATCH",
                            "Paseo Workspace intent changed during create rollback",
                        )
                    current["phase"] = "recorded"

                self._transact(restore_rejected_create)
                raise create_error
            # Every acknowledgement loss keeps the pre-existing exact
            # readback-first recovery path: a registry-proved Workspace is
            # safe to adopt. Post-dispatch ambiguity remains pending.
            recovered = self._workspace_by_identity(slug=slug)
            if recovered is not None:
                self._verify_workspace_repository(
                    context, recovered[1], expected_base_commit=base_commit
                )
                return (*recovered, base_commit)
            raise create_error
        # ``_call`` returned successfully, so a malformed receipt,
        # non-matching registry identity, absent registry entry, or registry
        # readback failure must propagate without slug-only adoption.  The
        # pre-effect workspace intent remains durable for a fresh readback.
        created = self._workspace_payload(workspace)
        registered = self._workspace_by_identity(slug=slug, expected=created)
        if registered is None:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo workspace create receipt is absent from exact registry readback",
            )
        self._verify_workspace_repository(
            context, registered[1], expected_base_commit=base_commit
        )
        return (*registered, base_commit)

    def _output_schema_payload(self, spec: _RuntimeActionSpec) -> bytes:
        prompt = self._artifacts.read_json(spec.prompt_artifact.digest)
        return _runtime_output_schema_bytes(
            _RuntimeOutputIdentity(
                subject_digest=spec.subject_digest,
                stable_action_id=spec.stable_action_id,
                authority_digest=spec.subject.authority_digest,
            ),
            _resolve_runtime_subject_protocol(spec.subject, prompt),
        )

    def prepare(self, spec: _RuntimeActionSpec) -> _PrepareReceipt | _RuntimeFailure:
        try:
            self._refresh()
            # Resolve the complete subject/protocol/schema relation before any
            # workspace, durable intent, or provider operation is attempted.
            output_schema = self._output_schema_payload(spec)
            existing = self._actions.get(spec.stable_action_id)
            if existing is not None:
                return self._prepared_receipt_from_action(spec, existing)
            context = self._contexts.get(spec.subject.repository)
            if context is None:
                return _RuntimeFailure("RUNTIME_CONFIGURATION_INVALID", "Paseo repository context is missing")
            if spec.profile.features:
                return _RuntimeFailure(
                    "RUNTIME_CONFIGURATION_INVALID",
                    "Paseo V3 cannot prove non-empty Runtime Profile features",
                )
            slug = digest_value(
                {
                    "repository": spec.subject.repository,
                    "stable_action_id": spec.stable_action_id,
                }
            )[:24]
            intent = self._ensure_workspace_intent(spec, context, slug)
            if intent is None:
                existing = self._actions.get(spec.stable_action_id)
                if not isinstance(existing, dict):
                    raise RuntimeGatewayError(
                        "RUNTIME_ACTION_STATE_CHANGED",
                        "Prepared action disappeared after stale intent cleanup",
                    )
                return self._prepared_receipt_from_action(spec, existing)
            if self._one_agent(self._labels(spec), include_archived=True) is not None:
                return _RuntimeFailure(
                    "RUNTIME_ACTION_STATE_MISSING",
                    "Paseo Agent exists but the durable action record is absent",
                )
            workspace_id, workspace_path, workspace_base_commit = self._workspace_for_prepare(
                spec, context, slug
            )
            ownership_nonce = intent.get("ownership_nonce")
            if not isinstance(ownership_nonce, str):
                raise RuntimeGatewayError(
                    "RUNTIME_WORKSPACE_UNSAFE",
                    "Runtime Workspace ownership nonce is absent",
                )
            files = self._workspace_files(
                workspace_path=workspace_path,
                workspace_id=workspace_id,
                workspace_slug=slug,
                workspace_base_commit=workspace_base_commit,
                ownership_nonce=ownership_nonce,
                subject=spec.subject,
                spec_identity_digest=self._spec_identity_digest(spec),
            )
            files.establish()
            files.require_result_absent()
            prompt = self._artifacts.get(spec.prompt_artifact.digest)
            staged = {
                artifact.digest: files.write_artifact(
                    artifact.digest,
                    self._artifacts.read_bytes(artifact.digest),
                )
                for artifact in (prompt, *spec.input_artifacts)
            }
            target = staged[prompt.digest]
            schema_digest = files.write_schema(
                output_schema
            )
            files.require_result_absent()
            action_record = {
                "subject": spec.subject.canonical(), "subject_digest": spec.subject_digest,
                "profile": spec.profile.canonical(), "profile_digest": spec.profile.digest,
                "prompt_artifact_digest": prompt.digest, "workspace_id": workspace_id,
                "workspace_path": str(files.workspace), "workspace_slug": slug,
                "workspace_base_commit": workspace_base_commit,
                "workspace_owner_nonce": ownership_nonce,
                "workspace_layout_version": _RUNTIME_WORKSPACE_LAYOUT_VERSION,
                "workspace_owner_marker_digest": files.marker_digest,
                "prompt_file": str(target), "fenced": False,
                "input_artifact_digests": [item.digest for item in spec.input_artifacts],
                "input_files": {
                    item.digest: str(staged[item.digest])
                    for item in spec.input_artifacts
                },
                "result_file": str(files.result_path),
                "output_schema_file": str(files.schema_path),
                "output_schema_digest": schema_digest,
                # Every recovery-relevant action state is present from the
                # first durable write.  Restart never supplies a missing
                # default because that could re-own an already-dispatched
                # provider effect.
                "bound_agent_id": None,
                "binding_established": False,
                "pending_start": False,
                "pending_resume": False,
                "pending_park": False,
                "parked": False,
                "pending_stop_command": None,
                "pending_fence": False,
                "pending_fence_claim_id": None,
                "pending_fence_quiesced": False,
                "pending_retire": False,
                "pending_permission_response": None,
                "completed_permission_response": None,
                "output_artifact_digest": None,
                "workspace_observed_head_commit": workspace_base_commit,
                "wake_state": None,
                "wake_state_digest": None,
                "wake_terminal_emitted": False,
            }
            # Persist the complete action record and removal of the create
            # intent together.  A crash at any earlier point retains enough
            # immutable identity to recover the same workspace without a
            # second base resolution or create request.
            expected_intent = deepcopy(
                self._workspace_intents.get(spec.stable_action_id)
            )

            def commit_action(state: dict[str, Any]) -> None:
                existing_action = state["actions"].get(spec.stable_action_id)
                if existing_action is not None:
                    if not self._action_matches_spec(existing_action, spec):
                        raise RuntimeGatewayError(
                            "RUNTIME_ACTION_IDENTITY_MISMATCH",
                            "stable action changed during prepare commit",
                        )
                    state["workspace_intents"].pop(spec.stable_action_id, None)
                    return
                if (
                    not isinstance(expected_intent, dict)
                    or state["workspace_intents"].get(spec.stable_action_id)
                    != expected_intent
                ):
                    raise RuntimeGatewayError(
                        "RUNTIME_ACTION_IDENTITY_MISMATCH",
                        "Paseo Workspace intent changed before Prepared commit",
                    )
                state["actions"][spec.stable_action_id] = deepcopy(action_record)
                state["workspace_intents"].pop(spec.stable_action_id)

            self._transact(commit_action)
            committed = self._actions.get(spec.stable_action_id)
            if not isinstance(committed, dict):
                raise RuntimeGatewayError(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Prepared action is absent after commit",
                )
            return self._prepared_receipt_from_action(spec, committed)
        except Exception as error:
            return self._failure(error, spec.stable_action_id)

    def _reconcile_observation(
        self, stable_action_id: str
    ) -> _RuntimeObservationRead:
        record: dict[str, Any] | None = None
        identity: _RuntimeObservationIdentity | None = None
        record_digest: str | None = None
        try:
            self._refresh()
            record = self._actions.get(stable_action_id)
            if record is None:
                return _runtime_sealed_failure_read(
                    stable_action_id,
                    _RuntimeFailure.absent(stable_action_id),
                )
            identity = self._observation_identity(record)
            record_digest = digest_value(record)
            subject, profile = self._record_subject(record)
            labels = self._labels(
                _RuntimeActionSpec(
                    stable_action_id=stable_action_id,
                    subject=subject,
                    profile=profile,
                    prompt_artifact=self._artifacts.get(
                        record["prompt_artifact_digest"]
                    ),
                    input_artifacts=(),
                )
            )
            agent = self._one_agent(labels, include_archived=True)
            if agent is None:
                if isinstance(record.get("bound_agent_id"), str):
                    return _runtime_sealed_failure_read(
                        stable_action_id,
                        _RuntimeFailure(
                            "RUNTIME_BINDING_MISSING",
                            "previously bound Paseo Agent is absent from exact label readback",
                            stable_action_id=stable_action_id,
                        ),
                        identity=identity,
                        selected_record_digest=record_digest,
                    )
                if record.get("pending_start") is True:
                    self._verify_staged_workspace(
                        record,
                        require_pinned_base=True,
                        require_result_absent=True,
                    )
                    return _runtime_sealed_failure_read(
                        stable_action_id,
                        _RuntimeFailure(
                            "RUNTIME_MATERIALIZATION_PENDING",
                            "Paseo start acknowledgement awaits stable-action label readback",
                            stable_action_id=stable_action_id,
                        ),
                        identity=identity,
                        selected_record_digest=record_digest,
                    )
                read = self._prepared(
                    record,
                    selected_stable_action_id=stable_action_id,
                )
                assert type(read) is _RuntimeObservationRead
            else:
                bound_agent_id = record.get("bound_agent_id")
                if (
                    isinstance(bound_agent_id, str)
                    and bound_agent_id != agent.agent_id
                ):
                    return _runtime_sealed_failure_read(
                        stable_action_id,
                        _RuntimeFailure(
                            "RUNTIME_IDENTITY_AMBIGUOUS",
                            "Paseo label readback changed the exact bound Agent identity",
                        ),
                        identity=identity,
                        selected_record_digest=record_digest,
                    )
                read = self._bound(
                    record,
                    agent,
                    selected_stable_action_id=stable_action_id,
                )
                assert type(read) is _RuntimeObservationRead
            verdict = _ObservationProtocol.validate(
                read,
                selected_stable_action_id=stable_action_id,
                maximum_artifact_bytes=self._artifacts.maximum_bytes,
            )
            if verdict.kind not in {"prepared", "bound"}:
                assert verdict.failure is not None
                return _runtime_sealed_failure_read(
                    stable_action_id,
                    verdict.failure,
                )
            return read
        except Exception as error:
            return _runtime_sealed_failure_read(
                stable_action_id,
                self._failure(error, stable_action_id),
                identity=identity,
                selected_record_digest=record_digest,
            )

    def observe(
        self, stable_action_id: str
    ) -> _PreparedRuntimeObservation | _BoundRuntimeObservation | _RuntimeFailure:
        read = self._reconcile_observation(stable_action_id)
        verdict = _ObservationProtocol.validate(
            read,
            selected_stable_action_id=stable_action_id,
            maximum_artifact_bytes=self._artifacts.maximum_bytes,
        )
        self._command_gate.replace(
            stable_action_id,
            verdict.token
            if (
                verdict.kind in {"prepared", "bound"}
                and type(verdict.token) is _RuntimeObservationReadToken
            )
            else None,
        )
        if verdict.kind in {"prepared", "bound"}:
            assert verdict.observation is not None
            return verdict.observation
        assert verdict.failure is not None
        return verdict.failure

    def _start_agent_arguments(
        self, stable_action_id: str, record: Mapping[str, Any]
    ) -> list[str]:
        subject, profile = self._record_subject(record)
        files = self._workspace_files_from_record(record, subject)
        files.assert_record_paths(record)
        files.read_artifact(str(record["prompt_artifact_digest"]))
        for digest in record["input_artifact_digests"]:
            files.read_artifact(str(digest))
        files.read_schema(str(record["output_schema_digest"]))
        files.require_result_absent()
        labels = self._labels(
            _RuntimeActionSpec(
                stable_action_id,
                subject,
                profile,
                self._artifacts.get(record["prompt_artifact_digest"]),
                (),
            )
        )
        bootstrap = (
            "Read, SHA-256 verify, and execute only the GWO Prompt Artifact at "
            f".gwo/runtime-artifacts/{record['prompt_artifact_digest']}.json. "
            f"Expected digest: {record['prompt_artifact_digest']}. "
            "Every governed input Artifact is at "
            ".gwo/runtime-artifacts/SHA-256.json; verify each referenced digest. "
            "Write the canonical GWO result JSON atomically to "
            f"{files.result_path.relative_to(files.workspace).as_posix()}."
        )
        args = [
            "run", "--background", "--title", f"GWO {stable_action_id}",
            "--provider", "kimi" if profile.provider == "kimi-cli" else profile.provider,
            "--model", profile.model, "--thinking", profile.thinking, "--mode", profile.mode,
            "--workspace", record["workspace_id"], "--cwd", record["workspace_path"],
            "--output-schema", str(files.schema_path),
        ]
        for key, value in sorted(labels.items()):
            args.extend(["--label", f"{key}={value}"])
        return [*args, "--json", bootstrap]

    def _write_resume_file(self, record: Mapping[str, Any]) -> Path:
        """Atomically stage and re-read the replayable Paseo resume prompt."""

        subject, _profile = self._record_subject(record)
        files = self._workspace_files_from_record(record, subject)
        files.assert_record_paths(record)
        payload = b"Resume the accepted GWO action from the verified Prompt Artifact."
        return files.write_resume(payload)

    @staticmethod
    def _was_not_dispatched(error: Exception) -> bool:
        """Only process-creation failure proves a claimed effect never began."""

        return isinstance(error, _ProviderNotDispatched)

    def _restore_claim_not_dispatched(
        self,
        record: dict[str, Any],
        previous: Mapping[str, Any],
    ) -> None:
        """CAS-restore one exact claim after proved process-creation failure."""

        expected = deepcopy(record)
        restored = deepcopy(dict(previous))
        stable_action_id = _require_text(
            restored.get("subject", {}).get("stable_action_id")
            if isinstance(restored.get("subject"), dict)
            else None,
            "persisted stable action id",
        )

        def commit(state: dict[str, Any]) -> dict[str, Any]:
            if state["actions"].get(stable_action_id) != expected:
                raise RuntimeGatewayError(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Paseo rejection rollback lost its durable CAS",
                )
            state["actions"][stable_action_id] = deepcopy(restored)
            return deepcopy(restored)

        updated = self._transact(commit)
        record.clear()
        record.update(updated)

    def _mark_fence_claim_quiesced(
        self, record: dict[str, Any], claim_id: str
    ) -> None:
        """Durably prove one exact fence owner returned without a receipt."""

        stable_action_id = _require_text(
            record.get("subject", {}).get("stable_action_id")
            if isinstance(record.get("subject"), dict)
            else None,
            "persisted stable action id",
        )

        def commit(state: dict[str, Any]) -> dict[str, Any]:
            current = state["actions"].get(stable_action_id)
            if not isinstance(current, dict):
                raise RuntimeGatewayError(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Paseo fence claim disappeared before quiescence",
                )
            # The claim id makes this update safe across unrelated record
            # changes.  If label readback already converged the action, or a
            # different claim somehow replaced it, never recreate ownership.
            if (
                current.get("pending_fence") is not True
                or current.get("pending_fence_claim_id") != claim_id
                or current.get("fenced") is True
            ):
                return deepcopy(current)
            updated = deepcopy(current)
            updated["pending_fence_quiesced"] = True
            state["actions"][stable_action_id] = updated
            return deepcopy(updated)

        updated = self._transact(commit)
        record.clear()
        record.update(updated)

    def command(
        self,
        stable_action_id: str,
        transition: RuntimeTransition,
    ) -> _CommandReceipt | _RuntimeFailure:
        command = transition
        pending_before: dict[str, Any] | None = None
        fence_provider_call_started = False
        fence_claim_id: str | None = None
        try:
            if not _runtime_transition_is_structurally_valid(command):
                return _RuntimeFailure("RUNTIME_COMMAND_INVALID", "Runtime command is outside the closed union")
            precondition = self._command_gate.consume(stable_action_id)
            if precondition is None:
                return _RuntimeFailure(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Runtime command requires one fresh observation gate",
                    stable_action_id=stable_action_id,
                )
            if not _runtime_observation_read_token_is_structurally_valid(
                precondition
            ):
                return _RuntimeFailure(
                    "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                    "Runtime command observation gate is structurally invalid",
                    stable_action_id=stable_action_id,
                )
            self._refresh()
            record = self._actions.get(stable_action_id)
            if record is None:
                return _RuntimeFailure("RUNTIME_ACTION_UNKNOWN", "Runtime action is unknown")
            if not (
                _runtime_read_token_matches_record(
                    precondition,
                    stable_action_id=stable_action_id,
                    identity=self._observation_identity(record),
                    selected_record_digest=digest_value(record),
                )
            ):
                return _RuntimeFailure(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Runtime command read precondition is stale",
                    stable_action_id=stable_action_id,
                )
            read = self._reconcile_observation(stable_action_id)
            verdict = _ObservationProtocol.validate(
                read,
                selected_stable_action_id=stable_action_id,
                maximum_artifact_bytes=self._artifacts.maximum_bytes,
            )
            if (
                verdict.kind
                in _RUNTIME_OBSERVATION_FAILURE_VERDICT_KINDS
            ):
                assert verdict.failure is not None
                return verdict.failure
            if verdict.kind not in {"prepared", "bound"}:
                return _RuntimeFailure(
                    "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                    "Runtime command readback verdict is outside the closed union",
                )
            if verdict.token != precondition:
                return _RuntimeFailure(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Runtime command read precondition changed before dispatch",
                    stable_action_id=stable_action_id,
                )
            observation = verdict.observation
            record = self._actions.get(stable_action_id)
            if record is None:
                return _RuntimeFailure(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Runtime action disappeared during command readback",
                )
            subject, _profile = self._record_subject(record)
            if command is RuntimeCommand.START:
                if verdict.kind != "prepared":
                    return _RuntimeFailure("RUNTIME_COMMAND_INVALID", "start requires a Prepared Runtime action")
                if observation.fenced is not False:
                    return _RuntimeFailure(
                        "RUNTIME_COMMAND_INVALID", "start requires an unfenced Prepared Runtime action"
                    )
                start_args = self._start_agent_arguments(
                    stable_action_id, record
                )
                _PaseoCliTransport.validate_arguments(start_args)
                pending_before = deepcopy(record)
                claimed = self._claim_record_update(
                    record,
                    precondition=precondition,
                    already_claimed=lambda current: current.get("pending_start") is True,
                    update=lambda updated: updated.__setitem__("pending_start", True),
                )
                if not claimed:
                    pending_before = None
                    return _RuntimeFailure(
                        "RUNTIME_MATERIALIZATION_PENDING",
                        "Paseo start already has one durable effect owner",
                        stable_action_id=stable_action_id,
                    )
                self._call(start_args, allow_empty=True)
            elif verdict.kind != "bound":
                return _RuntimeFailure("RUNTIME_COMMAND_INVALID", "only start is allowed before Runtime binding exists")
            elif (
                command in {RuntimeCommand.PARK, RuntimeCommand.INTERRUPT}
                and observation.lifecycle == "parked"
            ):
                return _CommandReceipt(stable_action_id, command)
            elif command is RuntimeCommand.FENCE and observation.fenced is True:
                return _CommandReceipt(stable_action_id, command)
            elif command is RuntimeCommand.RETIRE and observation.lifecycle == "retired":
                return _CommandReceipt(stable_action_id, command)
            elif type(command) is PermissionResponse:
                if observation.lifecycle in {"completed", "retired"}:
                    if _completed_permission_effect_matches(command, observation):
                        return _CommandReceipt(stable_action_id, command)
                    return _RuntimeFailure(
                        "RUNTIME_PERMISSION_REQUEST_UNKNOWN",
                        "terminal Runtime bindings reject new permission responses",
                    )
                matching = [
                    request
                    for request in observation.permission_requests
                    if request.request_id == command.request_id
                ]
                if len(matching) != 1:
                    completed = self._completed_permission_response(
                        record, subject, observation.agent_id
                    )
                    if completed is not None and completed.request_id == command.request_id and completed.decision == command.decision:
                        return _CommandReceipt(stable_action_id, command)
                    return _RuntimeFailure(
                        "RUNTIME_PERMISSION_REQUEST_UNKNOWN",
                        "permission response does not bind one exact pending request",
                    )
                permission_pending_before = deepcopy(record)
                permission_args = [
                    "permit",
                    command.decision,
                    observation.agent_id,
                    command.request_id,
                    "--json",
                ]
                _PaseoCliTransport.validate_arguments(permission_args)
                pending_value = {
                    "request_id": command.request_id,
                    "decision": command.decision,
                    "request": asdict(matching[0]),
                    "request_digest": digest_value(asdict(matching[0])),
                    "provider_receipt": None,
                }
                claimed = self._claim_record_update(
                    record,
                    precondition=precondition,
                    already_claimed=lambda current: (
                        isinstance(
                            current.get("pending_permission_response"), dict
                        )
                    ),
                    update=lambda updated: updated.__setitem__(
                        "pending_permission_response", deepcopy(pending_value)
                    ),
                )
                if not claimed:
                    return _RuntimeFailure(
                        "RUNTIME_EFFECT_AMBIGUOUS",
                        "Paseo permission response already has one durable effect owner",
                        stable_action_id=stable_action_id,
                    )
                try:
                    receipt = self._call(permission_args)
                except Exception as call_error:
                    # Only process-creation failure proves permit was never
                    # dispatched.  Every provider/native/protocol failure
                    # after that boundary retains ambiguity evidence.
                    if self._was_not_dispatched(call_error):
                        try:
                            self._restore_claim_not_dispatched(
                                record, permission_pending_before
                            )
                        except Exception as rollback_error:
                            return self._failure(
                                rollback_error,
                                stable_action_id,
                            )
                    return self._failure(call_error, stable_action_id)
                verified = self._verify_permission_decision_receipt(
                    receipt, command, observation.agent_id, matching[0]
                )
                self._persist_record_update(
                    record,
                    lambda updated: updated["pending_permission_response"].__setitem__(
                        "provider_receipt", deepcopy(verified)
                    ),
                )
            elif command is RuntimeCommand.RESUME:
                if observation.lifecycle != "parked" or observation.fenced is not False:
                    return _RuntimeFailure(
                        "RUNTIME_COMMAND_INVALID", "resume requires an unfenced parked Runtime binding"
                    )
                # A local resume prompt is a required replay input, not a
                # provider effect.  Do not leave an unsendable durable intent
                # behind if this write fails.
                resume_file = self._write_resume_file(record)
                resume_args = [
                    "send",
                    "--no-wait",
                    "--json",
                    observation.agent_id,
                    "--prompt-file",
                    str(resume_file),
                ]
                _PaseoCliTransport.validate_arguments(resume_args)
                pending_before = deepcopy(record)
                claimed = self._claim_record_update(
                    record,
                    precondition=precondition,
                    already_claimed=lambda current: current.get("pending_resume") is True,
                    update=lambda updated: updated.update(
                        {"pending_park": False, "pending_resume": True}
                    ),
                )
                if not claimed:
                    pending_before = None
                    return _RuntimeFailure(
                        "RUNTIME_MATERIALIZATION_PENDING",
                        "Paseo resume already has one durable effect owner",
                        stable_action_id=stable_action_id,
                    )
                self._call(resume_args, allow_empty=True)
            elif command in {RuntimeCommand.PARK, RuntimeCommand.INTERRUPT}:
                if observation.lifecycle in {"completed", "retired"}:
                    return _RuntimeFailure(
                        "RUNTIME_COMMAND_INVALID",
                        "park and interrupt require an active Runtime binding",
                    )
                stop_command = _transition_name(command)
                stop_args = ["stop", observation.agent_id, "--json"]
                _PaseoCliTransport.validate_arguments(stop_args)
                pending_before = deepcopy(record)
                claimed = self._claim_record_update(
                    record,
                    precondition=precondition,
                    already_claimed=lambda current: current.get("pending_park")
                    is True,
                    update=lambda updated: updated.update(
                        {
                            "pending_park": True,
                            "pending_resume": False,
                            "pending_stop_command": stop_command,
                        }
                    ),
                )
                if not claimed:
                    pending_before = None
                    return _RuntimeFailure(
                        "RUNTIME_MATERIALIZATION_PENDING",
                        "Paseo stop already has one durable effect owner",
                        stable_action_id=stable_action_id,
                    )
                self._call(stop_args, allow_empty=True)
            elif command is RuntimeCommand.FENCE:
                if record.get("pending_fence") is True:
                    return _RuntimeFailure(
                        "RUNTIME_MATERIALIZATION_PENDING",
                        "Paseo fence already has one durable effect owner",
                        stable_action_id=stable_action_id,
                    )
                _PaseoCliTransport.validate_arguments(
                    [
                        "agent",
                        "update",
                        observation.agent_id,
                        "--label",
                        "gwo.runtime_fenced=true",
                        "--json",
                    ]
                )
                pending_before = deepcopy(record)
                fence_claim_id = uuid4().hex
                claimed = self._claim_record_update(
                    record,
                    precondition=precondition,
                    already_claimed=lambda current: current.get("pending_fence") is True,
                    update=lambda updated: updated.update(
                        {
                            "pending_fence": True,
                            "pending_fence_claim_id": fence_claim_id,
                            "pending_fence_quiesced": False,
                            "wake_terminal_emitted": False,
                        }
                    ),
                )
                if not claimed:
                    pending_before = None
                    fence_claim_id = None
                    return _RuntimeFailure(
                        "RUNTIME_MATERIALIZATION_PENDING",
                        "Paseo fence already has one durable effect owner",
                        stable_action_id=stable_action_id,
                    )
                fence_provider_call_started = True
                self._client.update_labels(observation.agent_id, {"gwo.runtime_fenced": "true"})
            elif command is RuntimeCommand.RETIRE:
                retire_args = [
                    "archive",
                    observation.agent_id,
                    "--force",
                    "--json",
                ]
                _PaseoCliTransport.validate_arguments(retire_args)
                pending_before = deepcopy(record)
                claimed = self._claim_record_update(
                    record,
                    precondition=precondition,
                    already_claimed=lambda current: current.get("pending_retire") is True,
                    update=lambda updated: updated.update(
                        {
                            "pending_retire": True,
                            "wake_terminal_emitted": False,
                        }
                    ),
                )
                if not claimed:
                    pending_before = None
                    return _RuntimeFailure(
                        "RUNTIME_MATERIALIZATION_PENDING",
                        "Paseo retirement already has one durable effect owner",
                        stable_action_id=stable_action_id,
                    )
                self._call(retire_args, allow_empty=True)
            return _CommandReceipt(stable_action_id, command)
        except Exception as error:
            not_dispatched = (
                pending_before is not None
                and self._was_not_dispatched(error)
            )
            if not_dispatched:
                try:
                    self._restore_claim_not_dispatched(record, pending_before)
                except Exception as rollback_error:
                    return self._failure(rollback_error, stable_action_id)
            elif fence_provider_call_started and fence_claim_id is not None:
                try:
                    self._mark_fence_claim_quiesced(record, fence_claim_id)
                except Exception:
                    # Failure to persist quiescence leaves the exclusive
                    # in-flight claim intact, which is safe and restartable.
                    # Keep the provider's original failure taxonomy.
                    pass
            return self._failure(error, stable_action_id)

    def events(self, after_cursor: str | None) -> _RuntimeEventPage | _RuntimeFailure:
        self._command_gate.clear()
        try:
            cursor = _runtime_event_cursor_value(after_cursor)
            if cursor is None:
                return _RuntimeFailure(
                    "RUNTIME_EVENT_CURSOR_INVALID",
                    "event cursor is invalid",
                )
            self._refresh()
            if cursor > self._next_event_cursor - 1:
                return _RuntimeFailure(
                    "RUNTIME_EVENT_CURSOR_INVALID",
                    "event cursor is ahead of the Runtime event journal",
                )
            eligible = sorted(
                stable_action_id
                for stable_action_id, record in self._actions.items()
                if isinstance(record, dict)
                and record.get("wake_terminal_emitted") is not True
            )
            selected_scan_cursor = self._event_scan_cursor
            selected_eligible_digest = digest_value(eligible)
            stable_action_id = (
                None
                if not eligible
                else eligible[selected_scan_cursor % len(eligible)]
            )
            verdict: _RuntimeObservationVerdict | None = None
            if stable_action_id is not None:
                try:
                    read = self._reconcile_observation(stable_action_id)
                except Exception:
                    return _RuntimeFailure(
                        "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                        "Runtime provider readback raised outside its envelope",
                    )
                verdict = _ObservationProtocol.validate(
                    read,
                    selected_stable_action_id=stable_action_id,
                    maximum_artifact_bytes=self._artifacts.maximum_bytes,
                )
                if verdict.kind == "invalid":
                    assert verdict.failure is not None
                    return verdict.failure
            derived = (
                None
                if verdict is None
                or verdict.kind not in {"prepared", "bound"}
                else _runtime_event_observation_state(
                    verdict.observation,
                    stable_action_id,
                )
            )
            if stable_action_id is not None and (
                derived is not None
                or (
                    verdict is not None
                    and verdict.kind
                    in {
                        "authoritative_absence",
                        "fairness_advance",
                    }
                )
            ):
                assert verdict is not None
                read_token = verdict.token
                if not (
                    _runtime_observation_read_token_is_structurally_valid(
                        read_token
                    )
                ):
                    return _RuntimeFailure(
                        "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                        "Runtime event readback omitted its causal token",
                    )
                selection_token = _RuntimeEventSelectionToken(
                    scan_cursor=selected_scan_cursor,
                    eligible_digest=selected_eligible_digest,
                    stable_action_id=stable_action_id,
                    action_record_digest=(
                        read_token.selected_record_digest
                    ),
                )
                observed_state, lifecycle = (
                    derived if derived is not None else (None, None)
                )
                state_digest = (
                    digest_value(observed_state)
                    if observed_state is not None
                    else None
                )

                def commit_scan(state: dict[str, Any]) -> bool:
                    current_eligible = sorted(
                        action_id
                        for action_id, record in state["actions"].items()
                        if isinstance(record, dict)
                        and record.get("wake_terminal_emitted") is not True
                    )
                    current_record = state["actions"].get(
                        selection_token.stable_action_id
                    )
                    if (
                        not current_eligible
                        or state["event_scan_cursor"]
                        != selection_token.scan_cursor
                        or digest_value(current_eligible)
                        != selection_token.eligible_digest
                        or current_eligible[
                            state["event_scan_cursor"] % len(current_eligible)
                        ]
                        != selection_token.stable_action_id
                        or not isinstance(current_record, dict)
                        or digest_value(current_record)
                        != selection_token.action_record_digest
                    ):
                        # The cursor, eligibility set, selection, or selected
                        # record changed after readback.  Discard the detached
                        # result; a later bounded scan re-reads authoritative
                        # state before publishing.
                        return False
                    publish_event = (
                        state_digest is not None
                        and lifecycle is not None
                        and current_record.get("wake_state_digest")
                        != state_digest
                    )
                    if (
                        publish_event
                        and state["next_event_cursor"]
                        > _MAXIMUM_RUNTIME_EVENT_CURSOR
                    ):
                        raise RuntimeGatewayError(
                            "RUNTIME_EVENT_CURSOR_EXHAUSTED",
                            "Runtime event cursor space is exhausted",
                        )
                    state["event_scan_cursor"] = (
                        0
                        if state["event_scan_cursor"]
                        == _MAXIMUM_RUNTIME_EVENT_CURSOR
                        else state["event_scan_cursor"]
                        + _MAXIMUM_RUNTIME_EVENT_READBACKS
                    )
                    if state_digest is None or lifecycle is None:
                        return True
                    if publish_event:
                        current_record["wake_state"] = deepcopy(
                            observed_state
                        )
                        current_record["wake_state_digest"] = state_digest
                        event_cursor = state["next_event_cursor"]
                        state["next_event_cursor"] = event_cursor + 1
                        state["events"].append(
                            _RuntimeEvent(
                                cursor=str(event_cursor),
                                stable_action_id=(
                                    selection_token.stable_action_id
                                ),
                                kind=f"state:{lifecycle}",
                            )
                        )
                        del state["events"][:-_MAXIMUM_RUNTIME_EVENTS]
                    if lifecycle in {"completed", "retired"}:
                        current_record["wake_terminal_emitted"] = True
                    return True

                self._transact(commit_scan)
            available = [
                event
                for event in self._events
                if (
                    _runtime_event_cursor_value(event.cursor) is not None
                    and _runtime_event_cursor_value(event.cursor) > cursor
                )
            ]
            page = tuple(available[:_MAXIMUM_RUNTIME_EVENT_PAGE])
            return _RuntimeEventPage(
                events=page,
                next_cursor=(
                    page[-1].cursor if page else after_cursor
                ),
            )
        except (TypeError, ValueError):
            return _RuntimeFailure("RUNTIME_EVENT_CURSOR_INVALID", "event cursor is invalid")
        except Exception as error:
            return self._failure(error)


class _PaseoStaticAssignmentValidator:
    """Factory-owned static capability check, outside the four-method seam."""

    def __init__(self, repository_contexts: Mapping[str, RuntimeRepositoryContext]):
        self._contexts = _snapshot_repository_contexts(repository_contexts)

    def __call__(self, subject: RuntimeSubject, profile: RuntimeProfile) -> None:
        context = self._contexts.get(subject.repository)
        if not isinstance(context, RuntimeRepositoryContext):
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID", "Paseo repository context is missing"
            )
        if profile.features:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                "Paseo V3 cannot prove non-empty Runtime Profile features",
            )
        # RuntimeConfiguration proves these are non-empty.  The second pass
        # protects durable configurations produced by older program versions.
        for field_name in ("provider", "model", "thinking", "mode"):
            _require_paseo_profile_argument(
                getattr(profile, field_name), f"Paseo Runtime Profile {field_name}"
            )
        try:
            _require_paseo_argument(str(context.path), "Paseo repository context path")
            # base_ref never reaches Paseo.  It is one argument in a direct
            # no-shell Git invocation and may therefore use ordinary Git ref
            # syntax that would be unsafe for a Windows batch launcher.
            if _PaseoRuntimeProviderAdapter._git_readback(
                context.path, "rev-parse", "--is-inside-work-tree"
            ) != "true":
                raise ValueError("repository context is not a worktree")
            top_level = Path(
                _PaseoRuntimeProviderAdapter._git_readback(
                    context.path, "rev-parse", "--show-toplevel"
                )
            ).resolve()
            if top_level != context.path.resolve():
                raise ValueError("repository context is not the Git worktree root")
            base_commit = _PaseoRuntimeProviderAdapter._git_readback(
                context.path, "rev-parse", f"{context.base_ref}^{{commit}}"
            )
            if _GIT_COMMIT_RE.fullmatch(base_commit) is None:
                raise ValueError("repository context base ref is invalid")
        except (OSError, TimeoutError, ValueError, RuntimeGatewayError) as error:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                "Paseo repository context is not a usable Git worktree at its base ref",
            ) from error


class _PlanningEffectDispatch(Protocol):
    """Host-private Writer-fenced capability for Planning provider effects."""

    def mode(self, subject: CampaignPlanningSubject) -> str: ...

    def enter(
        self,
        subject: CampaignPlanningSubject,
        boundary: str,
        *,
        permission_request_id: str | None = None,
    ) -> str | None: ...

    def resolve(
        self,
        subject: CampaignPlanningSubject,
        boundary: str,
        ticket: str,
    ) -> None: ...

    def reconcile(
        self,
        subject: CampaignPlanningSubject,
        effect_proofs: tuple[tuple[str, str | None, str | None], ...],
    ) -> None: ...


def build_runtime_gateway(
    *,
    store_path: Path,
    configuration: RuntimeConfiguration,
    repository_contexts: Mapping[str, RuntimeRepositoryContext],
    artifact_root: Path | None = None,
    maximum_artifact_bytes: int = 1_048_576,
    _shared_artifacts: ArtifactStore | None = None,
    _planning_effect_dispatch: _PlanningEffectDispatch | None = None,
) -> "RuntimeGateway":
    """Compose the V3 production Gateway without exposing provider machinery."""

    gateway_store = Path(store_path)
    if _shared_artifacts is not None:
        if type(_shared_artifacts) is not ArtifactStore:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                "shared Artifact Store must be one exact Gateway-owned store",
            )
        if artifact_root is not None:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                "shared Artifact Store and artifact_root are mutually exclusive",
            )
        artifacts = _shared_artifacts
    else:
        artifacts = ArtifactStore(
            Path(artifact_root)
            if artifact_root is not None
            else gateway_store.parent / "runtime-artifacts",
            maximum_bytes=maximum_artifact_bytes,
        )
    return RuntimeGateway(
        store_path=gateway_store,
        _adapter=_PaseoRuntimeProviderAdapter(
            client=_PaseoCliTransport(
                timeout_seconds=60
            ),
            artifacts=artifacts,
            repository_contexts=repository_contexts,
            state_path=gateway_store.with_name(
                f"{gateway_store.name}.paseo-actions.json"
            ),
        ),
        configuration=configuration,
        _artifacts=artifacts,
        _static_assignment_validator=_PaseoStaticAssignmentValidator(repository_contexts),
        _planning_effect_dispatch=_planning_effect_dispatch,
    )


@dataclass(frozen=True)
class PlanningPreflightReceipt:
    """Opaque proof that only Coordinator configuration was mechanically read."""

    subject_digest: str
    stable_action_id: str
    receipt_digest: str


@dataclass(frozen=True)
class RuntimeProgressReceipt:
    subject_digest: str
    stable_action_id: str
    status: str
    receipt_digest: str
    command: RuntimeTransition | None = None
    wake_cursor: str | None = None
    wake_hints: tuple[str, ...] = ()
    output_artifact_digest: str | None = None
    permission_required: PermissionRequired | None = None
    recovery_outcome: RuntimeRecoveryOutcome | None = None
    terminal_binding_evidence: TerminalBindingEvidence | None = None


@dataclass(frozen=True)
class PlanningReceipt(RuntimeProgressReceipt):
    planning_output_artifact_digest: str | None = None


class RuntimeGateway:
    """Own Runtime materialization; callers only preflight, progress, and read wakes."""

    def __init__(
        self,
        *,
        store_path: Path,
        _adapter: _RuntimeProviderAdapter,
        configuration: RuntimeConfiguration,
        _artifacts: ArtifactStore | None = None,
        _static_assignment_validator: Callable[[RuntimeSubject, RuntimeProfile], None]
        | None = None,
        _planning_effect_dispatch: _PlanningEffectDispatch | None = None,
        _authority_readback: _AuthorityReadback | None = None,
    ):
        self._store_path = Path(store_path)
        self._journal = _V3JsonJournal(self._store_path)
        self._pending_save_data: dict[str, Any] | None = None
        # Underscored parameters are internal/test composition hooks. Semantic
        # callers construct the default production Gateway through
        # build_runtime_gateway and never receive this Provider seam.
        self._adapter = _adapter
        self._configuration = _runtime_configuration_snapshot(configuration)
        self._configuration_identity = digest_value(
            _runtime_configuration_canonical(self._configuration)
        )
        self._artifacts = _artifacts or ArtifactStore(
            self._store_path.parent / "runtime-artifacts"
        )
        self._static_assignment_validator = _static_assignment_validator
        self._planning_effect_dispatch = _planning_effect_dispatch
        self._authority_readback = (
            _authority_readback
            if _authority_readback is not None
            else _ArtifactAuthorityReadback(self._artifacts)
        )
        self._data = self._load()

    # Caller interface operation 1.  It neither calls an adapter nor reserves
    # a slot, workspace, session, Agent, or provider action.
    def planning_preflight(
        self,
        subject: CampaignPlanningSubject,
    ) -> PlanningPreflightReceipt:
        if type(subject) is not CampaignPlanningSubject:
            raise RuntimeGatewayError(
                "RUNTIME_PREFLIGHT_SUBJECT_INVALID",
                "planning preflight accepts CampaignPlanningSubject only",
            )
        self._assert_configuration_identity()
        self._refresh()
        # Resolve and statically validate without persisting a Campaign first:
        # a production host/context/profile defect must be a pure preflight
        # failure, never a partial campaign claim.
        assertion_key = (
            subject.repository,
            subject.campaign_key,
            subject.campaign_handle,
        )
        assertion_present = (
            assertion_key in self._configuration.campaign_assertions
        )
        asserted_overrides = None
        if assertion_present:
            assertion = self._configuration.campaign_assertions[assertion_key]
            if type(assertion) is not CampaignStartRuntimeOverrides:
                raise RuntimeGatewayError(
                    "RUNTIME_CONFIGURATION_INVALID",
                    "Campaign Runtime assertion is not an exact immutable value",
                )
            try:
                asserted_overrides = CampaignStartRuntimeOverrides(
                    coordinator=assertion.coordinator,
                    ticket_overrides=dict(assertion.ticket_overrides),
                ).canonical()
            except (TypeError, RuntimeGatewayError) as error:
                raise RuntimeGatewayError(
                    "RUNTIME_CONFIGURATION_INVALID",
                    "Campaign Runtime assertion identity is invalid",
                ) from error
        existing_campaign = self._data["campaigns"].get(subject.campaign_handle)
        if existing_campaign is not None:
            if (
                existing_campaign.get("repository") != subject.repository
                or existing_campaign.get("campaign_key") != subject.campaign_key
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_CAMPAIGN_IDENTITY_MISMATCH",
                    "Campaign handle was read back for another repository or Campaign key",
                )
            candidate_overrides = existing_campaign.get("overrides")
            if (
                assertion_present
                and candidate_overrides != asserted_overrides
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_CAMPAIGN_IDENTITY_MISMATCH",
                    "Campaign handle was read back with different Runtime overrides",
                )
        else:
            candidate_overrides = (
                asserted_overrides
                if assertion_present
                else CampaignStartRuntimeOverrides().canonical()
            )
        assignment = self._resolve_assignment(
            subject.repository,
            RuntimeSelector.coordinator(),
            None,
            candidate_overrides,
        )
        self._validate_static_assignment(subject, assignment)
        assignment_record = {**assignment, "fallback_selected": False}
        assignment_digest = _assignment_digest(subject, assignment_record)
        campaign_overrides_digest = digest_value(candidate_overrides)
        subject_value = subject.canonical()
        preflight_binding = {
            "subject": subject_value,
            "subject_digest": subject.digest,
            "campaign_overrides_digest": campaign_overrides_digest,
            "assignment_digest": assignment_digest,
        }
        campaign_value = {
            "repository": subject.repository,
            "campaign_key": subject.campaign_key,
            "overrides": candidate_overrides,
            "overrides_digest": campaign_overrides_digest,
            "preflight_bindings": {
                subject.stable_action_id: preflight_binding
            },
        }
        binding = {
            "subject": subject_value,
            "subject_digest": subject.digest,
            "campaign_overrides_digest": campaign_overrides_digest,
            "assignment": assignment,
            "assignment_digest": assignment_digest,
        }
        receipt_digest = digest_value(
            {
                "kind": "planning_preflight.v1",
                "subject_digest": subject.digest,
                "stable_action_id": subject.stable_action_id,
                "campaign_overrides_digest": binding[
                    "campaign_overrides_digest"
                ],
                "assignment_digest": assignment_digest,
            }
        )
        expected = {**binding, "receipt_digest": receipt_digest}

        def commit(data: dict[str, Any]) -> None:
            self._reserve_action_identity(
                data,
                subject,
                conflict_code="RUNTIME_PREFLIGHT_IDENTITY_MISMATCH",
            )
            durable_campaign = data["campaigns"].get(subject.campaign_handle)
            if durable_campaign is not None:
                if (
                    durable_campaign.get("repository") != subject.repository
                    or durable_campaign.get("campaign_key") != subject.campaign_key
                ):
                    raise RuntimeGatewayError(
                        "RUNTIME_CAMPAIGN_IDENTITY_MISMATCH",
                        "Campaign handle was read back for another repository or Campaign key",
                    )
                if (
                    assertion_present
                    and durable_campaign.get("overrides") != asserted_overrides
                ):
                    raise RuntimeGatewayError(
                        "RUNTIME_CAMPAIGN_IDENTITY_MISMATCH",
                        "Campaign handle was read back with different Runtime overrides",
                    )
                if durable_campaign.get("overrides") != candidate_overrides:
                    raise RuntimeGatewayError(
                        "RUNTIME_PREFLIGHT_IDENTITY_MISMATCH",
                        "Campaign Runtime configuration changed during preflight",
                    )
                durable_binding = durable_campaign[
                    "preflight_bindings"
                ].get(subject.stable_action_id)
                if (
                    durable_binding is not None
                    and durable_binding != preflight_binding
                ):
                    raise RuntimeGatewayError(
                        "RUNTIME_PREFLIGHT_IDENTITY_MISMATCH",
                        "Campaign preflight cross-record binding changed",
                    )
                durable_campaign["preflight_bindings"][
                    subject.stable_action_id
                ] = deepcopy(preflight_binding)
            else:
                data["campaigns"][subject.campaign_handle] = deepcopy(campaign_value)
            durable_preflight = data["preflights"].get(subject.stable_action_id)
            if durable_preflight is not None and durable_preflight != expected:
                raise RuntimeGatewayError(
                    "RUNTIME_PREFLIGHT_IDENTITY_MISMATCH",
                    "stable planning action is already bound to another subject, options, or configuration",
                )
            if durable_preflight is None:
                data["preflights"][subject.stable_action_id] = deepcopy(expected)

        self._transact(commit)
        return PlanningPreflightReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            receipt_digest=receipt_digest,
        )

    def _read_coordinator_capability(
        self,
        subject: CampaignPlanningSubject,
    ) -> CoordinatorCapabilityProof:
        """Read back the closed Coordinator capability boundary.

        This is a private semantic readback seam, not another workflow
        operation.  It requires the exact persisted planning preflight and
        returns only the safe, non-delegating capability projection consumed
        by PlanControl's invalidation classifier.
        """

        if type(subject) is not CampaignPlanningSubject:
            raise RuntimeGatewayError(
                "COORDINATOR_CAPABILITY_SUBJECT_INVALID",
                "Coordinator capability readback accepts CampaignPlanningSubject only",
            )
        self._assert_configuration_identity()
        self._refresh()
        self._preflight_receipt(subject)
        return CoordinatorCapabilityProof(
            subject_digest=subject.digest,
            repository_read_only=True,
            tracker_read_only=True,
            can_activate_plan_revision=False,
            can_edit_tracker=False,
            can_expand_authority=False,
            delegation_enabled=False,
        )

    def _read_human_gate_capability(
        self,
        subject: RuntimeSubject,
    ) -> HumanGateCapabilityProof:
        """Read back the complete, non-writing human-gate capability boundary."""

        code = "PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED"
        if type(subject) not in {CampaignPlanningSubject, WorkRunSubject}:
            raise RuntimeGatewayError(
                code,
                "human-gate capability readback accepts an exact Runtime subject only",
            )
        self._assert_configuration_identity()
        self._refresh()
        reader = getattr(self._authority_readback, "read_human_gate_capability", None)
        if not callable(reader):
            raise RuntimeGatewayError(
                code,
                "human-gate capability readback is unavailable",
            )
        try:
            proof = reader(subject)
        except RuntimeGatewayError:
            raise
        except Exception as error:
            raise RuntimeGatewayError(
                code,
                "human-gate capability readback failed closed",
            ) from error
        return validate_human_gate_capability(
            proof,
            expected_subject_digest=subject.digest,
            expected_policy_witness_digest=subject.authority_digest,
            expected_gateway_configuration_digest=self._configuration_identity,
        )

    def _validate_human_gate_capability(
        self,
        proof: object,
        subject: RuntimeSubject | None = None,
    ) -> HumanGateCapabilityProof:
        """Validate a previously read proof against this Gateway identity."""

        if subject is not None and type(subject) not in {
            CampaignPlanningSubject,
            WorkRunSubject,
        }:
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED",
                "human-gate capability validation subject is invalid",
            )
        self._assert_configuration_identity()
        return validate_human_gate_capability(
            proof,
            expected_subject_digest=None if subject is None else subject.digest,
            expected_policy_witness_digest=(
                None if subject is None else subject.authority_digest
            ),
            expected_gateway_configuration_digest=self._configuration_identity,
        )

    def __getattr__(self, name: str) -> Any:
        """Expose private capability seams without adding workflow operations.

        The Gateway's public class surface remains the three workflow
        operations (``planning_preflight``, ``progress``, ``transition``).
        Human-gate capability reads are private semantic seams; the dynamic
        aliases preserve the narrow test/host adapter without advertising a
        fourth public operation to callers or package introspection.
        """

        if name == "read_human_gate_capability":
            return self._read_human_gate_capability
        if name == "validate_human_gate_capability":
            return self._validate_human_gate_capability
        raise AttributeError(name)

    # Caller interface operation 2.  This owns the entire readback-first
    # prepare/observe/start-or-resume loop; callers cannot issue provider
    # commands or inspect a Runtime Binding.
    def progress(
        self,
        subject: RuntimeSubject,
        preflight: PlanningPreflightReceipt | None = None,
        wake_cursor: str | None = None,
    ) -> RuntimeProgressReceipt:
        if type(subject) not in {CampaignPlanningSubject, WorkRunSubject}:
            raise RuntimeGatewayError(
                "RUNTIME_SUBJECT_INVALID",
                "RuntimeGateway accepts only Campaign Planning and Plan-Revision Work Run subjects",
            )
        self._assert_configuration_identity()
        self._refresh()
        planning_mode: str | None = None
        if type(subject) is CampaignPlanningSubject:
            persisted_preflight = self._require_preflight(subject, preflight)
            planning_mode = self._planning_progress_mode(subject)
        elif preflight is not None:
            raise RuntimeGatewayError(
                "RUNTIME_PREFLIGHT_INVALID",
                "Work Run progress does not accept a planning preflight receipt",
            )
        record = self._assignment_for_progress(
            subject,
            None if not isinstance(subject, CampaignPlanningSubject) else persisted_preflight,
        )
        self._validate_static_assignment(subject, record)
        prepared_dispatch_ticket: str | None = None
        observation_verdict = self._observe_verdict(
            subject.stable_action_id
        )
        if observation_verdict.kind == "authoritative_absence":
            if planning_mode == "draining":
                raise RuntimeGatewayError(
                    "RUNTIME_RECOVERY_ONLY",
                    "Draining Planning recovery cannot materialize an absent Runtime action",
                )
            if self._record_has_materialization_history(record):
                raise RuntimeGatewayError(
                    "RUNTIME_BINDING_MISSING",
                    "provider action is absent after Gateway recorded materialization history",
                )
            prompt_artifact, input_artifacts = self._resolve_input_artifacts(subject)
            spec = _RuntimeActionSpec(
                stable_action_id=subject.stable_action_id,
                subject=subject,
                profile=self._profile(record["profile_digest"]),
                prompt_artifact=prompt_artifact,
                input_artifacts=input_artifacts,
            )
            prepared_dispatch_ticket = self._enter_planning_effect_dispatch(
                subject,
                "prepare",
            )
            prepared_verdict = self._prepare_verdict(spec)
            if prepared_verdict.failure is not None:
                retry_with_fallback, recovery = self._recovery_for_failure(
                    subject, record, prepared_verdict.failure
                )
                if retry_with_fallback:
                    return self.progress(subject, preflight, wake_cursor)
                if recovery is not None:
                    return self._recovery_receipt(subject, recovery)
            if prepared_verdict.kind == "recoverable_failure":
                assert prepared_verdict.failure is not None
                prepare_failure = prepared_verdict.failure
                # Only an acknowledged prepare may be recovered from the
                # exact Prepared/Bound observation.  A permanent prepare
                # failure remains its original typed failure even if the
                # second readback still says absent.
                observation_verdict = self._observe_verdict(
                    subject.stable_action_id
                )
                if observation_verdict.kind == "authoritative_absence":
                    self._raise_failure(prepare_failure)
                if observation_verdict.kind in {
                    "fairness_advance",
                    "failure",
                    "invalid",
                }:
                    assert observation_verdict.failure is not None
                    self._raise_failure(observation_verdict.failure)
                if observation_verdict.kind not in {"prepared", "bound"}:
                    self._raise_failure(prepare_failure)
            elif prepared_verdict.kind == "receipt":
                observation_verdict = self._observe_verdict(
                    subject.stable_action_id
                )
                if observation_verdict.kind not in {"prepared", "bound"}:
                    assert observation_verdict.failure is not None
                    self._raise_failure(observation_verdict.failure)
            else:
                assert prepared_verdict.failure is not None
                self._raise_failure(prepared_verdict.failure)
        elif observation_verdict.kind in {
            "fairness_advance",
            "failure",
            "invalid",
        }:
            assert observation_verdict.failure is not None
            retry_with_fallback, recovery = self._recovery_for_failure(
                subject, record, observation_verdict.failure
            )
            if retry_with_fallback:
                return self.progress(subject, preflight, wake_cursor)
            if recovery is not None:
                return self._recovery_receipt(subject, recovery)
            self._raise_failure(observation_verdict.failure)
        if observation_verdict.kind == "prepared":
            observation = observation_verdict.observation
            assert observation is not None
            self._validate_prepared_observation(subject, record, observation)
            if prepared_dispatch_ticket is not None:
                self._resolve_planning_effect_dispatch(
                    subject,
                    "prepare",
                    prepared_dispatch_ticket,
                )
            elif type(subject) is CampaignPlanningSubject:
                self._reconcile_planning_effect_dispatch(
                    subject,
                    observation_verdict,
                )
            if planning_mode == "draining":
                raise RuntimeGatewayError(
                    "RUNTIME_RECOVERY_ONLY",
                    "Draining Planning recovery cannot start a Prepared Runtime action",
                )
            if observation.fenced is not False:
                raise RuntimeGatewayError(
                    "RUNTIME_COMMAND_INVALID",
                    "start requires an unfenced Prepared Runtime observation",
            )
            self._record_observation(record, observation_verdict)
            observation_verdict = self._planning_command_with_readback(
                subject,
                RuntimeCommand.START,
                planning_mode=planning_mode,
            )
            assert observation_verdict.observation is not None
            observation = observation_verdict.observation
            self._validate_bound_observation(subject, record, observation)
            self._record_observation(record, observation_verdict)
        elif observation_verdict.kind == "bound":
            observation = observation_verdict.observation
            assert observation is not None
            self._validate_bound_observation(subject, record, observation)
            if prepared_dispatch_ticket is not None:
                self._resolve_planning_effect_dispatch(
                    subject,
                    "prepare",
                    prepared_dispatch_ticket,
                )
            elif type(subject) is CampaignPlanningSubject:
                self._reconcile_planning_effect_dispatch(
                    subject,
                    observation_verdict,
                )
            if planning_mode == "draining" and observation.lifecycle != "completed":
                raise RuntimeGatewayError(
                    "RUNTIME_RECOVERY_ONLY",
                    "Draining Planning recovery requires one exact completed Runtime action",
                )
            if (
                observation.lifecycle in {"running", "completed"}
                and record["lifecycle"] is None
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_OBSERVATION_INVALID",
                    "Provider reported semantic execution before Gateway issued start or resume",
                )
            self._record_observation(record, observation_verdict)
        else:
            raise RuntimeGatewayError(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "Runtime observation verdict is outside the closed union",
            )
        if observation.lifecycle == "parked":
            if type(subject) is CampaignPlanningSubject:
                if observation.fenced is not False:
                    raise RuntimeGatewayError(
                        "RUNTIME_COMMAND_INVALID",
                        "progress cannot resume a fenced Runtime binding",
                    )
                observation_verdict = self._planning_command_with_readback(
                    subject,
                    RuntimeCommand.RESUME,
                    planning_mode=planning_mode,
                )
                assert observation_verdict.observation is not None
                observation = observation_verdict.observation
                self._validate_bound_observation(subject, record, observation)
                self._record_observation(record, observation_verdict)
        elif observation.lifecycle not in {"running", "parked", "completed"}:
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                f"cannot progress Runtime lifecycle {observation.lifecycle}",
            )
        if planning_mode == "draining":
            return self._progress_receipt(
                subject,
                observation_verdict,
                command=None,
            )
        if type(subject) is WorkRunSubject:
            permission = self._auto_allowed_permission(subject, observation)
            if permission is not None:
                try:
                    observation_verdict = self._command_with_readback(
                        subject.stable_action_id,
                        permission,
                    )
                except _RuntimeRecoverySignal as signal:
                    return self._command_recovery_receipt(
                        subject,
                        record,
                        signal.failure,
                    )
                assert observation_verdict.observation is not None
                observation = observation_verdict.observation
                self._validate_bound_observation(subject, record, observation)
                self._record_observation(record, observation_verdict)
                wake_hints, next_cursor = self._wake_hints(wake_cursor, subject)
                return self._progress_receipt(
                    subject,
                    observation_verdict,
                    command=permission,
                    wake_cursor=next_cursor,
                    wake_hints=wake_hints,
                )
            permission_required = self._permission_required(subject, observation)
        else:
            permission_required = None
        wake_hints, next_cursor = self._wake_hints(wake_cursor, subject)
        return self._progress_receipt(
            subject,
            observation_verdict,
            command=None,
            wake_cursor=next_cursor,
            wake_hints=wake_hints,
            permission_required=permission_required,
        )

    def _planning_progress_mode(self, subject: CampaignPlanningSubject) -> str:
        """Resolve the closed dynamic policy inside the sole progress operation."""

        dispatch = self._planning_effect_dispatch
        if dispatch is None:
            return "cut_over"
        try:
            mode = dispatch.mode(subject)
        except RuntimeGatewayError:
            raise
        except Exception as error:
            raise RuntimeGatewayError(
                "RUNTIME_RECOVERY_POLICY_INVALID",
                "Planning progress policy is unavailable",
            ) from error
        if type(mode) is not str or mode not in {"cut_over", "draining"}:
            raise RuntimeGatewayError(
                "RUNTIME_RECOVERY_POLICY_INVALID",
                "Planning progress policy is outside its closed state union",
            )
        return mode

    def _enter_planning_effect_dispatch(
        self,
        subject: CampaignPlanningSubject,
        boundary: str,
        *,
        permission_request_id: str | None = None,
    ) -> str | None:
        """Enter a Writer-blocking durable state immediately before I/O."""

        if type(boundary) is not str or boundary not in {
            "prepare",
            "start",
            "resume",
            "permission_allow",
        }:
            raise RuntimeGatewayError(
                "RUNTIME_RECOVERY_ONLY",
                "Planning provider-effect boundary is outside the closed dispatch union",
            )
        if (
            boundary == "permission_allow"
            and (type(permission_request_id) is not str or not permission_request_id)
        ) or (
            boundary != "permission_allow" and permission_request_id is not None
        ):
            raise RuntimeGatewayError(
                "RUNTIME_RECOVERY_ONLY",
                "Planning permission dispatch identity is missing or malformed",
            )
        dispatch = self._planning_effect_dispatch
        if dispatch is None:
            return None
        try:
            ticket = dispatch.enter(
                subject,
                boundary,
                permission_request_id=permission_request_id,
            )
        except RuntimeGatewayError:
            raise
        except Exception as error:
            if getattr(error, "code", None) == "PLANNING_EFFECT_DISPATCH_BOUNDED":
                raise RuntimeGatewayError(
                    "RUNTIME_PLANNING_DISPATCH_BOUNDED",
                    "Planning provider-effect dispatch exceeded its durable budget",
                ) from error
            raise RuntimeGatewayError(
                "RUNTIME_RECOVERY_ONLY",
                "Planning provider-effect dispatch admission is unavailable",
            ) from error
        if type(ticket) is not str or not ticket:
            raise RuntimeGatewayError(
                "RUNTIME_RECOVERY_ONLY",
                "Planning provider-effect dispatch admission was denied or malformed",
            )
        return ticket

    def _resolve_planning_effect_dispatch(
        self,
        subject: CampaignPlanningSubject,
        boundary: str,
        ticket: str | None,
    ) -> None:
        dispatch = self._planning_effect_dispatch
        if dispatch is None:
            if ticket is not None:
                raise RuntimeGatewayError(
                    "RUNTIME_RECOVERY_ONLY",
                    "Planning dispatch ticket appeared without a host capability",
                )
            return
        if type(ticket) is not str or not ticket:
            raise RuntimeGatewayError(
                "RUNTIME_RECOVERY_ONLY",
                "Planning provider-effect dispatch ticket is missing or malformed",
            )
        try:
            dispatch.resolve(subject, boundary, ticket)
        except RuntimeGatewayError:
            raise
        except Exception as error:
            raise RuntimeGatewayError(
                "RUNTIME_RECOVERY_ONLY",
                "Planning provider-effect dispatch could not resolve its durable state",
            ) from error

    def _reconcile_planning_effect_dispatch(
        self,
        subject: CampaignPlanningSubject,
        observation_verdict: _RuntimeObservationVerdict,
    ) -> None:
        """Resolve only the active boundary mechanically proved by readback."""

        dispatch = self._planning_effect_dispatch
        if dispatch is None:
            return
        effect_proofs = self._planning_effect_readback_proofs(observation_verdict)
        if not effect_proofs:
            return
        try:
            dispatch.reconcile(subject, effect_proofs)
        except RuntimeGatewayError:
            raise
        except Exception as error:
            raise RuntimeGatewayError(
                "RUNTIME_RECOVERY_ONLY",
                "Planning provider-effect dispatch could not reconcile its durable state",
            ) from error

    @staticmethod
    def _planning_effect_readback_proofs(
        observation_verdict: _RuntimeObservationVerdict,
    ) -> tuple[tuple[str, str | None, str | None], ...]:
        """Project validated readback into closed, adapter-private effect proofs."""

        if observation_verdict.kind == "prepared":
            return (("prepare", None, None),)
        if observation_verdict.kind != "bound":
            return ()
        observation = observation_verdict.observation
        assert observation is not None
        proofs = [("prepare", None, None)]
        if observation.lifecycle in {"running", "completed"}:
            proofs.extend(
                (("start", None, None), ("resume", None, None))
            )
        completed = observation.completed_permission_response
        if (
            type(completed) is _CompletedPermissionResponse
            and completed.decision == "allow"
            and _completed_permission_evidence_is_bound(observation)
        ):
            proofs.append(
                ("permission_allow", completed.request_id, completed.decision)
            )
        return tuple(proofs)

    def _planning_command_with_readback(
        self,
        subject: RuntimeSubject,
        command: RuntimeTransition,
        *,
        planning_mode: str | None = None,
    ) -> _RuntimeObservationVerdict:
        """Fence only Planning transitions that can begin semantic work."""

        if type(subject) is not CampaignPlanningSubject:
            return self._command_with_readback(subject.stable_action_id, command)
        boundary = _planning_transition_effect_boundary(command)
        if boundary is None:
            return self._command_with_readback(subject.stable_action_id, command)
        mode = (
            self._planning_progress_mode(subject)
            if planning_mode is None
            else planning_mode
        )
        if mode != "cut_over":
            raise RuntimeGatewayError(
                "RUNTIME_RECOVERY_ONLY",
                "Draining Writer authority cannot authorize new Planning semantic work",
            )
        ticket = self._enter_planning_effect_dispatch(
            subject,
            boundary,
            permission_request_id=(
                command.request_id
                if type(command) is PermissionResponse
                else None
            ),
        )
        return self._command_with_readback(
            subject.stable_action_id,
            command,
            _after_dispatch=lambda: self._resolve_planning_effect_dispatch(
                subject,
                boundary,
                ticket,
            ),
        )

    # Private Coordinator handoff for Plan Invalidation (#133).  It is not a
    # fourth public workflow operation: the public seam remains
    # start -> advance -> inspect.  The returned receipt is consumed by the
    # Kernel's existing ``advance(..., plan_invalidation=...)`` keyword.
    def _report_plan_invalidation(
        self,
        subject: WorkRunSubject,
        report: PlanInvalidationReport,
    ) -> PlanInvalidationReceipt:
        if type(subject) is not WorkRunSubject:
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_SUBJECT_INVALID",
                "Plan Invalidation accepts a WorkRunSubject only",
            )
        if type(report) is not PlanInvalidationReport:
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_REPORT_INVALID",
                "Plan Invalidation requires an exact PlanInvalidationReport",
            )
        # Bind the report to the exact Campaign, Plan Revision, Ticket, Work
        # Run, Runtime Binding, and authority subtree of the submitted subject.
        if (
            report.repository != subject.repository
            or report.campaign_key != subject.campaign_key
            or report.plan_revision_digest != subject.plan_revision_digest
            or report.ticket_key != subject.ticket_key
            or report.work_run_key != subject.work_run_key
            or report.runtime_binding_id != subject.stable_action_id
            or report.authority_subtree_digest != subject.authority_subtree_digest
        ):
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_SUBJECT_INVALID",
                "Plan Invalidation report is not bound to this Work Run subject",
            )
        self._assert_configuration_identity()
        self._refresh()
        record = self._data["actions"].get(subject.stable_action_id)
        if type(record) is not dict:
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_RUNTIME_BINDING_INVALID",
                "Plan Invalidation requires a persisted Runtime action",
            )
        verdict = self._observe_verdict(subject.stable_action_id)
        if verdict.kind != "bound" or type(verdict.observation) is not _BoundRuntimeObservation:
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_RUNTIME_BINDING_INVALID",
                "Plan Invalidation requires an authoritative Bound Runtime readback",
            )
        bound = verdict.observation
        self._validate_bound_observation(subject, record, bound)
        expected_role = _authority_role_for_purpose(subject.purpose)
        if report.reporter_role != expected_role:
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_AUTHORITY_ROLE_MISMATCH",
                "Plan Invalidation reporter role does not match the Work Run purpose",
            )
        if (
            bound.stable_action_id != report.runtime_binding_id
            or report.workspace_identity != bound.workspace_id
        ):
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_RUNTIME_BINDING_INVALID",
                "Plan Invalidation report does not bind the current Runtime Workspace",
            )
        # Read and validate the complete canonical Evidence payload.  Merely
        # finding bytes at a digest is insufficient: arbitrary junk must not
        # become a plan-invalidating fact.
        try:
            evidence = self._artifacts.read_json(report.evidence_digest)
        except RuntimeGatewayError as error:
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_REPORT_INVALID",
                "Plan Invalidation Evidence Artifact is not readable",
            ) from error
        legacy_evidence_keys = {
            "schema_version",
            "kind",
            "subject",
            "discovered_facts",
            "reproduction",
            "invalidated_obligation",
            "required_effects",
            "workspace_identity",
        }
        evidence_keys_with_source_lineage = legacy_evidence_keys | {
            "source_evidence_digests"
        }
        evidence_keys_with_complete_lineage = evidence_keys_with_source_lineage | {
            "lineage_artifacts"
        }
        evidence_keys = set(evidence) if type(evidence) is dict else set()
        has_source_lineage = (
            type(evidence) is dict and "source_evidence_digests" in evidence
        )
        source_digests = (
            evidence.get("source_evidence_digests")
            if has_source_lineage
            else None
        )
        source_lineage_valid = (
            not has_source_lineage
            or (
                type(source_digests) is list
                and bool(source_digests)
                and all(
                    type(digest) is str and _DIGEST_RE.fullmatch(digest) is not None
                    for digest in source_digests
                )
                and source_digests == sorted(set(source_digests))
            )
        )
        lineage_artifacts = (
            evidence.get("lineage_artifacts")
            if type(evidence) is dict and "lineage_artifacts" in evidence
            else None
        )
        lineage_valid = (
            lineage_artifacts is None
            or (
                type(lineage_artifacts) is list
                and all(
                    type(artifact) is dict
                    and load_canonical_json(canonical_bytes(artifact)) == artifact
                    for artifact in lineage_artifacts
                )
            )
        )
        if (
            type(evidence) is not dict
            or evidence_keys
            not in (
                legacy_evidence_keys,
                evidence_keys_with_source_lineage,
                evidence_keys_with_complete_lineage,
            )
            or evidence.get("schema_version") != "gwo.evidence.v1"
            or evidence.get("kind") != "plan_invalidation"
            or evidence.get("subject") != subject.canonical()
            or type(evidence.get("discovered_facts")) is not list
            or not evidence["discovered_facts"]
            or any(type(fact) is not str or not fact for fact in evidence["discovered_facts"])
            or type(evidence.get("reproduction")) is not str
            or not evidence["reproduction"]
            or evidence.get("invalidated_obligation") != report.invalidated_obligation
            or evidence.get("required_effects") != list(report.required_effects)
            or evidence.get("workspace_identity") != report.workspace_identity
            or not source_lineage_valid
            or not lineage_valid
        ):
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_REPORT_INVALID",
                "Plan Invalidation Evidence Artifact does not prove the exact report",
            )
        source_evidence_digests = (
            list(source_digests) if has_source_lineage else None
        )
        # Effective capability-policy readback must prove the reporting role
        # cannot edit Issues, blockers, Campaign membership, activate a Plan
        # Revision, merge, expand authority, or invoke global planning.
        # Inability to prove that policy fails closed.
        authority = self._authority_readback.read(subject)
        if authority is None:
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED",
                "Plan Invalidation requires an authoritative capability policy proof",
            )
        if type(authority) is not _FrozenPermissionAuthorityV1:
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED",
                "Plan Invalidation authority readback is not an exact frozen record",
            )
        if (
            authority.plan_revision_digest != subject.plan_revision_digest
            or authority.ticket_key != subject.ticket_key
            or authority.purpose != subject.purpose
            or authority.authority_subtree_digest != subject.authority_digest
            or authority.grant_pairs
            != _PLAN_INVALIDATION_ROLE_GRANTS[expected_role]
            or authority.witness_pairs
            != _PLAN_INVALIDATION_ROLE_GRANTS[expected_role]
        ):
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED",
                "Plan Invalidation authority readback does not prove the exact role boundary",
            )
        capability_policy = authority.capability_policy
        if capability_policy is None or not capability_policy.is_proven:
            raise RuntimeGatewayError(
                "PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED",
                "Plan Invalidation capability policy did not prove the role boundary",
            )
        authority_record_digest = digest_value(
            {
                "plan_revision_digest": authority.plan_revision_digest,
                "ticket_key": authority.ticket_key,
                "authority_subtree_digest": authority.authority_subtree_digest,
                "policy_witness_digest": authority.policy_witness_digest,
                "capability_policy": capability_policy.canonical(),
            }
        )
        proof = CapabilityPolicyProof(
            capability_policy=capability_policy,
            authority_record_digest=authority_record_digest,
        )
        receipt_digest = digest_value(
            {
                "kind": "plan_invalidation_receipt.v1",
                "report_digest": report.digest,
                "subject_digest": subject.digest,
                "authority_record_digest": authority_record_digest,
            }
        )
        observation = {
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
            "evidence_digest": report.evidence_digest,
            "dedup_identity": report.dedup_identity,
            "invalidated_obligation": report.invalidated_obligation,
            "required_effects": list(report.required_effects),
            "workspace_identity": report.workspace_identity,
        }
        if source_evidence_digests is not None:
            observation["source_evidence_digests"] = source_evidence_digests

        def commit(data: dict[str, Any]) -> None:
            invalidations = data.setdefault("plan_invalidation", {})
            dedup_key = digest_value(
                {
                    "subject_digest": subject.digest,
                    "dedup_identity": report.dedup_identity,
                }
            )
            candidate = {
                "report_digest": report.digest,
                "receipt_digest": receipt_digest,
                "subject_digest": subject.digest,
                "repository": report.repository,
                "campaign_key": report.campaign_key,
                "plan_revision_digest": report.plan_revision_digest,
                "ticket_key": report.ticket_key,
                "work_run_key": report.work_run_key,
                "runtime_binding_id": report.runtime_binding_id,
                "authority_subtree_digest": report.authority_subtree_digest,
                "reporter_role": report.reporter_role,
                "evidence_digest": report.evidence_digest,
                "dedup_identity": report.dedup_identity,
                "invalidated_obligation": report.invalidated_obligation,
                "required_effects": list(report.required_effects),
                "workspace_identity": report.workspace_identity,
                "observation": observation,
                "capability_policy": capability_policy.canonical(),
                "authority_record_digest": authority_record_digest,
                "policy_witness_digest": authority.policy_witness_digest,
            }
            if source_evidence_digests is not None:
                candidate["source_evidence_digests"] = source_evidence_digests
            existing = invalidations.get(dedup_key)
            if existing is not None:
                # Duplicate callbacks compare the complete identity, not only
                # a receipt digest that could be reused with changed Evidence.
                if existing != candidate:
                    raise RuntimeGatewayError(
                        "PLAN_INVALIDATION_DEDUP_CONFLICT",
                        "duplicate Plan Invalidation dedup identity bound to a different report",
                    )
                return
            invalidations[dedup_key] = candidate

        self._transact(commit)
        return PlanInvalidationReceipt(
            report_digest=report.digest,
            receipt_digest=receipt_digest,
            capability_policy_proof=proof,
            observation=observation,
        )

    # Caller interface operation 3.  Binding refs remain private, including
    # for start/resume: they re-enter the same observe-gated progression path.
    def transition(
        self,
        stable_action_id: str,
        transition: RuntimeTransition,
    ) -> RuntimeProgressReceipt:
        command = transition
        _require_text(stable_action_id, "stable_action_id")
        if not _runtime_transition_is_structurally_valid(command):
            raise RuntimeGatewayError(
                "RUNTIME_COMMAND_INVALID", "Runtime command is outside the closed union"
            )
        self._assert_configuration_identity()
        self._refresh()
        record = self._data["actions"].get(stable_action_id)
        if not isinstance(record, dict):
            raise RuntimeGatewayError("RUNTIME_ACTION_UNKNOWN", "stable action is unknown")
        subject = _subject_from_canonical(record.get("subject"))
        planning_boundary = (
            _planning_transition_effect_boundary(command)
            if type(subject) is CampaignPlanningSubject
            else None
        )
        if (
            planning_boundary is not None
            and self._planning_progress_mode(subject) != "cut_over"
        ):
            raise RuntimeGatewayError(
                "RUNTIME_RECOVERY_ONLY",
                "Draining Writer authority cannot authorize new Planning semantic work",
            )
        self._validate_static_assignment(subject, record)
        if command in {RuntimeCommand.START, RuntimeCommand.RESUME}:
            observation_verdict = self._observe_verdict(stable_action_id)
            if (
                observation_verdict.kind
                in _RUNTIME_OBSERVATION_FAILURE_VERDICT_KINDS
            ):
                assert observation_verdict.failure is not None
                return self._command_recovery_receipt(
                    subject,
                    record,
                    observation_verdict.failure,
                )
            if (
                command is RuntimeCommand.START
                and observation_verdict.kind != "prepared"
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_COMMAND_INVALID",
                    "start requires an exact Prepared Runtime observation",
                )
            observed = observation_verdict.observation
            assert observed is not None
            if command is RuntimeCommand.START and observed.fenced is not False:
                raise RuntimeGatewayError(
                    "RUNTIME_COMMAND_INVALID",
                    "start requires an unfenced Prepared Runtime observation",
                )
            if command is RuntimeCommand.RESUME and (
                observation_verdict.kind != "bound"
                or observed.lifecycle != "parked"
                or observed.fenced is not False
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_COMMAND_INVALID",
                    "resume requires an exact unfenced parked Bound Runtime observation",
                )
            if observation_verdict.kind == "prepared":
                self._validate_prepared_observation(
                    subject,
                    record,
                    observed,
                )
            else:
                self._validate_bound_observation(subject, record, observed)
            if type(subject) is CampaignPlanningSubject:
                self._reconcile_planning_effect_dispatch(
                    subject,
                    observation_verdict,
                )
            self._record_observation(record, observation_verdict)
            try:
                progressed_verdict = self._planning_command_with_readback(
                    subject,
                    command,
                    planning_mode="cut_over" if planning_boundary is not None else None,
                )
            except _RuntimeRecoverySignal as signal:
                return self._command_recovery_receipt(
                    subject,
                    record,
                    signal.failure,
                )
            assert progressed_verdict.observation is not None
            progressed = progressed_verdict.observation
            self._validate_bound_observation(subject, record, progressed)
            self._record_observation(record, progressed_verdict)
            return self._progress_receipt(
                subject,
                progressed_verdict,
                command=command,
            )
        observation_verdict = self._observe_verdict(stable_action_id)
        if (
            observation_verdict.kind
            in _RUNTIME_OBSERVATION_FAILURE_VERDICT_KINDS
        ):
            assert observation_verdict.failure is not None
            return self._command_recovery_receipt(
                subject,
                record,
                observation_verdict.failure,
            )
        if observation_verdict.kind != "bound":
            raise RuntimeGatewayError(
                "RUNTIME_COMMAND_INVALID",
                "only start can be issued before Runtime binding exists",
            )
        observation = observation_verdict.observation
        assert observation is not None
        self._validate_bound_observation(subject, record, observation)
        if type(subject) is CampaignPlanningSubject:
            self._reconcile_planning_effect_dispatch(subject, observation_verdict)
        if type(command) is PermissionResponse:
            if observation.lifecycle in {"completed", "retired"}:
                if _completed_permission_effect_matches(command, observation):
                    self._record_observation(record, observation_verdict)
                    return self._progress_receipt(
                        subject, observation_verdict, command=command
                    )
                raise RuntimeGatewayError(
                    "RUNTIME_PERMISSION_REQUEST_UNKNOWN",
                    "terminal Runtime bindings reject new permission responses",
                )
            matching = [
                request
                for request in observation.permission_requests
                if request.request_id == command.request_id
            ]
            if len(matching) != 1:
                if _completed_permission_effect_matches(command, observation):
                    self._record_observation(record, observation_verdict)
                    return self._progress_receipt(
                        subject,
                        observation_verdict,
                        command=command,
                    )
                raise RuntimeGatewayError(
                    "RUNTIME_PERMISSION_REQUEST_UNKNOWN",
                    "permission response does not bind one exact pending request",
                )
        try:
            observation_verdict = self._planning_command_with_readback(
                subject,
                command,
                planning_mode="cut_over" if planning_boundary is not None else None,
            )
        except _RuntimeRecoverySignal as signal:
            return self._command_recovery_receipt(
                subject,
                record,
                signal.failure,
            )
        assert observation_verdict.observation is not None
        observation = observation_verdict.observation
        self._validate_bound_observation(subject, record, observation)
        self._record_observation(record, observation_verdict)
        return self._progress_receipt(
            subject,
            observation_verdict,
            command=command,
        )

    def _assignment_for_progress(
        self,
        subject: RuntimeSubject,
        preflight: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self._data["actions"].get(subject.stable_action_id)
        if existing is not None:
            if existing.get("subject_digest") != subject.digest:
                raise RuntimeGatewayError(
                    "RUNTIME_ACTION_IDENTITY_MISMATCH",
                    "stable action was already bound to another Runtime subject",
                )
            if not _gateway_action_assignment_is_bound(existing, subject):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "stable action assignment is not sealed to its exact Runtime subject",
                )
            if isinstance(subject, CampaignPlanningSubject):
                persisted = self._data["preflights"].get(subject.stable_action_id)
                if (
                    type(persisted) is not dict
                ):
                    raise RuntimeGatewayError(
                        "RUNTIME_STORE_INVALID",
                        "planning action lacks its exact frozen preflight",
                    )
            return existing
        if isinstance(subject, CampaignPlanningSubject):
            if preflight is None:
                raise RuntimeGatewayError(
                    "RUNTIME_PREFLIGHT_REQUIRED",
                    "Campaign Planning must complete configuration preflight first",
                )
            assignment = preflight.get("assignment")
            if not isinstance(assignment, dict):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "planning preflight lacks its resolved Runtime assignment",
                )
            assignment_record = _assignment_record_from_value(
                {**assignment, "fallback_selected": False}
            )
            if (
                preflight.get("assignment_digest")
                != _assignment_digest(subject, assignment_record)
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "planning preflight assignment seal is invalid",
                )
            return self._ensure_assignment(subject, assignment)
        campaign = self._data["campaigns"].get(subject.campaign_handle)
        if campaign is None:
            raise RuntimeGatewayError(
                "RUNTIME_CAMPAIGN_UNKNOWN",
                "Work Run Runtime action requires its persisted Campaign",
            )
        if campaign.get("repository") != subject.repository or campaign.get(
            "campaign_key"
        ) != subject.campaign_key:
            raise RuntimeGatewayError(
                "RUNTIME_CAMPAIGN_IDENTITY_MISMATCH",
                "Work Run subject does not match its persisted Campaign",
            )
        assignment = self._resolve_assignment(
            subject.repository,
            _selector_for_purpose(subject.purpose),
            subject.ticket_key,
            campaign["overrides"],
        )
        return self._ensure_assignment(subject, assignment)

    def _ensure_assignment(
        self, subject: RuntimeSubject, assignment: dict[str, Any]
    ) -> dict[str, Any]:
        self._validate_static_assignment(subject, assignment)
        prompt_digest = (
            subject.planning_request_artifact_digest
            if isinstance(subject, CampaignPlanningSubject)
            else subject.prompt_artifact_digest
        )
        record = {
            "subject": subject.canonical(),
            "subject_digest": subject.digest,
            "selector": assignment["selector"],
            "configuration_source": assignment["configuration_source"],
            "profile_digest": assignment["profile_digest"],
            "availability_fallback_profile_digest": assignment[
                "availability_fallback_profile_digest"
            ],
            "fallback_selected": False,
            "assignment_digest": _assignment_digest(
                subject,
                {**assignment, "fallback_selected": False},
            ),
            "prompt_artifact_digest": prompt_digest,
            "binding_ref": None,
            "agent_id": None,
            "session_id": None,
            "workspace_id": None,
            "lifecycle": None,
            "planning_output_artifact_digest": None,
            "observation_digest": None,
            "materialization_observed": False,
            "ever_bound": False,
            "last_observation": None,
            "recovery": _initial_recovery_state(),
        }
        identity_fields = (
            "subject",
            "subject_digest",
            "selector",
            "configuration_source",
            "profile_digest",
            "availability_fallback_profile_digest",
            "fallback_selected",
            "assignment_digest",
            "prompt_artifact_digest",
        )

        def commit(data: dict[str, Any]) -> None:
            self._reserve_action_identity(
                data,
                subject,
                conflict_code="RUNTIME_ACTION_IDENTITY_MISMATCH",
            )
            existing = data["actions"].get(subject.stable_action_id)
            if existing is None:
                data["actions"][subject.stable_action_id] = deepcopy(record)
                return
            if not isinstance(existing, dict) or any(
                existing.get(field_name) != record[field_name]
                for field_name in identity_fields
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_ACTION_IDENTITY_MISMATCH",
                    "stable action was already bound to another Runtime subject or assignment",
                )

        self._transact(commit)
        return self._data["actions"][subject.stable_action_id]

    @staticmethod
    def _subject_identity(subject: RuntimeSubject) -> dict[str, str]:
        canonical = subject.canonical()
        return {
            "subject_kind": str(canonical["kind"]),
            "subject_digest": subject.digest,
        }

    @classmethod
    def _reserve_action_identity(
        cls,
        data: dict[str, Any],
        subject: RuntimeSubject,
        *,
        conflict_code: str,
    ) -> None:
        identities = data.get("action_identities")
        if not isinstance(identities, dict):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID",
                "RuntimeGateway stable-action identity registry is invalid",
            )
        expected = cls._subject_identity(subject)
        existing = identities.get(subject.stable_action_id)
        if existing is None:
            identities[subject.stable_action_id] = expected
            return
        if existing != expected:
            raise RuntimeGatewayError(
                conflict_code,
                "stable action was already reserved for another exact Runtime subject",
            )

    def _resolve_assignment(
        self,
        repository: str,
        selector: RuntimeSelector,
        ticket_key: str | None,
        persisted_overrides: Mapping[str, Any],
    ) -> dict[str, str]:
        self._assert_configuration_identity()
        mapping: ProfileMapping | None = None
        source: str | None = None
        if selector.is_coordinator:
            raw = persisted_overrides.get("coordinator")
            if raw is not None:
                mapping = _mapping_from_value(raw)
                source = "campaign_start.coordinator"
        else:
            assert ticket_key is not None
            for item in persisted_overrides.get("ticket_overrides", ()):
                if (
                    item.get("ticket_key") == ticket_key
                    and item.get("role") == selector.value
                ):
                    mapping = _mapping_from_value(item.get("mapping"))
                    source = "campaign_start.ticket"
                    break
        if mapping is None:
            repository_values = self._configuration.repository_mappings.get(
                repository, {}
            )
            if not isinstance(repository_values, Mapping):
                raise RuntimeGatewayError(
                    "RUNTIME_CONFIGURATION_INVALID",
                    "Repository Runtime mappings are invalid",
                )
            repository_mapping = repository_values.get(selector)
            if repository_mapping is not None:
                mapping = _validated_configuration_mapping(repository_mapping)
                source = "repository"
        if mapping is None:
            host_mapping = self._configuration.host_mappings.get(selector)
            if host_mapping is not None:
                mapping = _validated_configuration_mapping(host_mapping)
                source = "host_global"
        if mapping is None:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                f"required Runtime mapping is missing for {selector.value}",
            )
        self._profile(mapping.primary_profile_digest)
        if mapping.availability_fallback_profile_digest is not None:
            self._profile(mapping.availability_fallback_profile_digest)
        return {
            "selector": selector.value,
            "configuration_source": str(source),
            "profile_digest": mapping.primary_profile_digest,
            "availability_fallback_profile_digest": mapping.availability_fallback_profile_digest,
        }

    def _profile(self, digest: str) -> RuntimeProfile:
        self._assert_configuration_identity()
        try:
            profile = self._configuration.profiles[digest]
        except Exception as error:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                "Runtime mapping refers to an unknown immutable Profile",
            ) from error
        return _validate_runtime_profile_registry_entry(digest, profile)

    def _assert_configuration_identity(self) -> None:
        try:
            observed = digest_value(
                _runtime_configuration_canonical(self._configuration)
            )
        except Exception as error:
            if (
                isinstance(error, RuntimeGatewayError)
                and error.code == "RUNTIME_CONFIGURATION_INVALID"
            ):
                raise
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                "Runtime configuration snapshot is invalid",
            ) from error
        if observed != self._configuration_identity:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                "Runtime configuration changed after Gateway composition",
            )

    def _validate_static_assignment(
        self, subject: RuntimeSubject, assignment: Mapping[str, Any]
    ) -> None:
        profile_digest = assignment.get("profile_digest")
        if not isinstance(profile_digest, str):
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID", "Runtime assignment lacks a primary Profile"
            )
        profile = self._profile(profile_digest)
        fallback_digest = assignment.get("availability_fallback_profile_digest")
        fallback_profile: RuntimeProfile | None = None
        if fallback_digest is not None:
            if not isinstance(fallback_digest, str):
                raise RuntimeGatewayError(
                    "RUNTIME_CONFIGURATION_INVALID",
                    "Runtime assignment fallback Profile is invalid",
                )
            fallback_profile = self._profile(fallback_digest)
        if self._static_assignment_validator is not None:
            self._static_assignment_validator(subject, profile)
            if fallback_profile is not None:
                self._static_assignment_validator(subject, fallback_profile)

    @staticmethod
    def _record_has_materialization_history(record: Mapping[str, Any]) -> bool:
        return (
            record.get("materialization_observed") is True
            or record.get("binding_ref") is not None
            or record.get("lifecycle") is not None
        )

    def _resolve_input_artifacts(
        self, subject: RuntimeSubject
    ) -> tuple[ArtifactRef, tuple[ArtifactRef, ...]]:
        prompt_digest = (
            subject.planning_request_artifact_digest
            if type(subject) is CampaignPlanningSubject
            else subject.prompt_artifact_digest
        )
        prompt = self._artifacts.get(prompt_digest)
        payload = self._artifacts.read_json(prompt.digest)
        _resolve_runtime_subject_protocol(subject, payload)
        if type(subject) is CampaignPlanningSubject:
            # The planning subject binds these governed inputs by digest.  They
            # remain protocol Artifacts, so existence alone is insufficient.
            self._artifacts.read_json(subject.snapshot_artifact_digest)
            self._artifacts.read_json(subject.policy_witness_digest)
            return prompt, (
                self._artifacts.get(subject.snapshot_artifact_digest),
                self._artifacts.get(subject.policy_witness_digest),
                prompt,
            )
        return prompt, (prompt,)

    def _observe_verdict(
        self, stable_action_id: str
    ) -> _RuntimeObservationVerdict:
        self._refresh_before_adapter_io()
        record = self._data["actions"].get(stable_action_id)
        expected_subject = (
            _subject_from_canonical(record.get("subject"))
            if type(record) is dict
            else None
        )
        try:
            result = self._adapter.observe(stable_action_id)
        except (OSError, TimeoutError):
            return _ObservationProtocol.failure(
                _RuntimeFailure.transport()
            )
        except Exception:
            return _ObservationProtocol.failure(
                _RuntimeFailure(
                    "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                    "Runtime provider observation failed",
                )
            )
        if type(result) is _RuntimeFailure:
            if (
                not _runtime_failure_is_structurally_valid(result)
                or (
                    result.stable_action_id is not None
                    and result.stable_action_id != stable_action_id
                )
            ):
                return _ObservationProtocol.invalid(
                    "Runtime observation failure is malformed"
                )
            if _runtime_failure_is_authoritative_absence(
                result, stable_action_id
            ):
                kind = "authoritative_absence"
            elif (
                result.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
                and result.stable_action_id is None
            ) or (
                result.code
                in {
                    "RUNTIME_BINDING_MISSING",
                    "RUNTIME_MATERIALIZATION_PENDING",
                }
                and result.stable_action_id == stable_action_id
            ):
                kind = "fairness_advance"
            else:
                kind = "failure"
            return _RuntimeObservationVerdict(
                kind, None, result, None, None, None
            )
        if (
            type(result)
            not in {
                _PreparedRuntimeObservation,
                _BoundRuntimeObservation,
            }
            or expected_subject is None
            or type(record) is not dict
            or not _runtime_observation_matches(
                result,
                subject=expected_subject,
                profile_digest=record.get("profile_digest"),
                prompt_artifact_digest=record.get(
                    "prompt_artifact_digest"
                ),
            )
            or (
                record.get("workspace_id") is not None
                and record.get("workspace_id") != result.workspace_id
            )
            or (
                record.get("binding_ref") is not None
                and record.get("binding_ref") != result.binding_ref
            )
            or (
                record.get("agent_id") is not None
                and record.get("agent_id") != result.agent_id
            )
            or (
                record.get("session_id") is not None
                and record.get("session_id") != result.session_id
            )
            or (
                record.get("ever_bound") is True
                and type(result) is _PreparedRuntimeObservation
            )
        ):
            return _ObservationProtocol.invalid(
                "Runtime observation changed its requested or frozen identity"
            )
        try:
            self._resolve_input_artifacts(expected_subject)
            if (
                type(result) is _BoundRuntimeObservation
                and result.output_artifact_digest is not None
            ):
                self._artifacts.prove_runtime_output(
                    result.output_artifact_digest,
                    subject_digest=expected_subject.digest,
                    stable_action_id=stable_action_id,
                    authority_digest=expected_subject.authority_digest,
                )
        except RuntimeGatewayError as error:
            return _ObservationProtocol.failure(
                _RuntimeFailure(
                    error.code,
                    "Runtime Artifact evidence is invalid",
                )
            )
        return _RuntimeObservationVerdict(
            (
                "prepared"
                if type(result) is _PreparedRuntimeObservation
                else "bound"
            ),
            result,
            None,
            None,
            None,
            None,
        )

    def _prepare_verdict(
        self,
        spec: _RuntimeActionSpec,
    ) -> _RuntimePrepareResultVerdict:
        self._refresh_before_adapter_io()
        try:
            result = self._adapter.prepare(spec)
        except (OSError, TimeoutError):
            result = _RuntimeFailure.transport()
        except Exception:
            result = _RuntimeFailure(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID", "Runtime provider prepare failed"
            )
        return _RuntimePrepareResultProtocol.validate(
            result,
            spec,
        )

    def _prepare(
        self,
        spec: _RuntimeActionSpec,
    ) -> _PrepareReceipt | _RuntimeFailure:
        """External compatibility projection of a validated prepare result."""

        verdict = self._prepare_verdict(spec)
        if verdict.receipt is not None:
            return verdict.receipt
        assert verdict.failure is not None
        return verdict.failure

    def _wake_hints(
        self, cursor: str | None, subject: RuntimeSubject
    ) -> tuple[tuple[str, ...], str | None]:
        self._refresh_before_adapter_io()
        try:
            page = self._adapter.events(cursor)
        except (OSError, TimeoutError):
            return (), cursor
        except Exception:
            raise RuntimeGatewayError(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "Runtime provider event readback failed outside its envelope",
            )
        verdict = _RuntimeEventPageProtocol.validate(
            page,
            after_cursor=cursor,
        )
        if verdict.kind == "transient_failure":
            return (), cursor
        if verdict.kind != "page":
            assert verdict.failure is not None
            self._raise_failure(verdict.failure)
        assert verdict.page is not None
        page = verdict.page
        hints: list[str] = []
        for event in page.events:
            record = self._data["actions"].get(event.stable_action_id)
            if not isinstance(record, dict):
                continue
            event_subject = _subject_from_canonical(record.get("subject"))
            if (
                event_subject.repository == subject.repository
                and event_subject.campaign_handle == subject.campaign_handle
            ):
                hints.append(f"{event.cursor}:{event.stable_action_id}:{event.kind}")
        return tuple(hints), page.next_cursor

    def _read_watchdog_events(
        self,
        after_cursor: str | None,
    ) -> _RuntimeCampaignWakePage:
        self._refresh_before_adapter_io()
        raw_page = self._adapter.events(after_cursor)
        verdict = _RuntimeEventPageProtocol.validate(raw_page, after_cursor=after_cursor)
        if verdict.kind != "page":
            assert verdict.failure is not None
            self._raise_failure(verdict.failure)
        assert verdict.page is not None
        return _RuntimeCampaignWakePage(
            events=tuple(self._watchdog_wake_for_event(event) for event in verdict.page.events),
            next_cursor=verdict.page.next_cursor,
        )

    @staticmethod
    def _raise_failure(failure: _RuntimeFailure) -> None:
        raise RuntimeGatewayError(failure.code, failure.detail)

    def _preflight_receipt(
        self, subject: CampaignPlanningSubject
    ) -> PlanningPreflightReceipt:
        value = self._data["preflights"].get(subject.stable_action_id)
        if (
            type(value) is not dict
            or value.get("subject") != subject.canonical()
            or value.get("subject_digest") != subject.digest
            or value.get("receipt_digest")
            != _preflight_receipt_digest(subject.stable_action_id, value)
        ):
            raise RuntimeGatewayError(
                "RUNTIME_PREFLIGHT_REQUIRED",
                "Campaign Planning action lacks its exact persisted preflight",
            )
        return PlanningPreflightReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            receipt_digest=str(value["receipt_digest"]),
        )

    def _require_preflight(
        self,
        subject: CampaignPlanningSubject,
        receipt: PlanningPreflightReceipt | None,
    ) -> Mapping[str, Any]:
        persisted = self._data["preflights"].get(subject.stable_action_id)
        if (
            receipt is None
            or type(persisted) is not dict
            or persisted.get("subject") != subject.canonical()
            or receipt.subject_digest != subject.digest
            or receipt.stable_action_id != subject.stable_action_id
            or persisted.get("receipt_digest") != receipt.receipt_digest
            or persisted.get("receipt_digest")
            != _preflight_receipt_digest(subject.stable_action_id, persisted)
        ):
            raise RuntimeGatewayError(
                "RUNTIME_PREFLIGHT_REQUIRED",
                "Campaign Planning progress requires its exact read-only preflight receipt",
            )
        campaign = self._data["campaigns"].get(subject.campaign_handle)
        if (
            type(campaign) is not dict
            or campaign.get("repository") != subject.repository
            or campaign.get("campaign_key") != subject.campaign_key
            or persisted.get("campaign_overrides_digest")
            != campaign.get("overrides_digest")
            or campaign.get("preflight_bindings", {}).get(
                subject.stable_action_id
            )
            != {
                "subject": subject.canonical(),
                "subject_digest": subject.digest,
                "campaign_overrides_digest": persisted.get(
                    "campaign_overrides_digest"
                ),
                "assignment_digest": persisted.get("assignment_digest"),
            }
        ):
            raise RuntimeGatewayError(
                "RUNTIME_PREFLIGHT_IDENTITY_MISMATCH",
                "planning preflight no longer binds the exact Campaign overrides",
            )
        _validate_assignment_value(persisted.get("assignment"))
        return persisted

    def _validate_prepared_observation(
        self,
        subject: RuntimeSubject,
        record: Mapping[str, Any],
        observation: _PreparedRuntimeObservation,
    ) -> None:
        if not _runtime_observation_matches(
            observation,
            subject=subject,
            profile_digest=record.get("profile_digest"),
            prompt_artifact_digest=record.get("prompt_artifact_digest"),
        ):
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "prepared observation does not prove the exact staged Runtime action",
            )

    def _validate_bound_observation(
        self,
        subject: RuntimeSubject,
        record: Mapping[str, Any],
        observation: _BoundRuntimeObservation,
    ) -> None:
        if not _runtime_observation_matches(
            observation,
            subject=subject,
            profile_digest=record.get("profile_digest"),
            prompt_artifact_digest=record.get("prompt_artifact_digest"),
        ):
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "authoritative observation does not prove the complete Runtime binding",
            )

    def _auto_allowed_permission(
        self,
        subject: WorkRunSubject,
        observation: _BoundRuntimeObservation,
    ) -> PermissionResponse | None:
        """Return one exact auto-allow transition, or retain human attention.

        #112 deliberately keeps this policy beside the provider boundary.  The
        adapter normalizes a request, but it cannot infer semantic authority
        from a vendor operation name.  The existing authority-subtree digest
        names an immutable Authority Grant Artifact which, in turn, binds one
        immutable Policy Witness Artifact.  Both must enumerate the same
        exact operation/resource pair before the Gateway sends a response.
        """

        if observation.authority_subtree_digest != subject.authority_digest:
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "permission observation changed its frozen authority subtree",
            )
        if not observation.permission_requests:
            return None
        authority = self._authority_readback.read(subject)
        if authority is None:
            return None
        if type(authority) is not _FrozenPermissionAuthorityV1:
            raise RuntimeGatewayError(
                "RUNTIME_AUTHORITY_INVALID",
                "authority readback returned an invalid frozen authority",
            )
        if (
            authority.plan_revision_digest != subject.plan_revision_digest
            or authority.ticket_key != subject.ticket_key
            or authority.purpose != subject.purpose
            or authority.authority_subtree_digest != subject.authority_digest
        ):
            raise RuntimeGatewayError(
                "RUNTIME_AUTHORITY_INVALID",
                "authority readback does not bind the exact Work Run subject",
            )
        for request in observation.permission_requests:
            if (
                request.stable_action_id == subject.stable_action_id
                and request.subject_digest == subject.digest
                and request.binding_ref == observation.binding_ref
                and request.authority_subtree_digest == subject.authority_digest
                and (request.operation_id, request.resource_id) in authority.grant_pairs
                and (request.operation_id, request.resource_id) in authority.witness_pairs
            ):
                return PermissionResponse(request.request_id, "allow")
        return None

    @staticmethod
    def _permission_required(
        subject: RuntimeSubject,
        observation: _BoundRuntimeObservation,
    ) -> PermissionRequired | None:
        """Project one outstanding exact request without exposing a binding."""

        if not observation.permission_requests:
            return None
        request = observation.permission_requests[0]
        if (
            request.stable_action_id != subject.stable_action_id
            or request.subject_digest != subject.digest
            or request.binding_ref != observation.binding_ref
            or request.authority_subtree_digest != subject.authority_digest
        ):
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "permission request does not bind the authoritative Runtime observation",
            )
        return PermissionRequired(
            stable_action_id=request.stable_action_id,
            request_id=request.request_id,
            descriptor_digest=digest_value(asdict(request)),
        )

    def _recovery_for_failure(
        self,
        subject: RuntimeSubject,
        record: dict[str, Any],
        failure: _RuntimeFailure,
    ) -> tuple[bool, RuntimeRecoveryOutcome | None]:
        """Persist one classified Runtime failure without scheduling recovery.

        The returned boolean means that a single pre-identity availability
        fallback was durably selected.  ``progress`` then re-enters its normal
        readback-first loop under that assignment; it never starts another
        binding or changes an already materialized action.
        """

        if failure.code not in {
            "RUNTIME_PROVIDER_UNAVAILABLE",
            "RUNTIME_TRANSPORT_UNAVAILABLE",
            "RUNTIME_CONFIGURATION_INVALID",
        }:
            return False, None
        pre_identity = record.get("ever_bound") is False
        if failure.code == "RUNTIME_CONFIGURATION_INVALID":
            outcome = RuntimeRecoveryOutcome(
                kind="blocked" if pre_identity else "decision",
                reason=(
                    "RuntimeConfigurationInvalid"
                    if pre_identity
                    else "RuntimeConfigurationRepairRequired"
                ),
                next_check_at=None,
                observation_digest=self._recovery_observation_digest(record, failure),
            )
            self._persist_recovery_outcome(record, outcome, None)
            return False, outcome
        channel = (
            "provider_unavailable"
            if failure.code == "RUNTIME_PROVIDER_UNAVAILABLE"
            else "transport_unavailable"
        )
        observation_digest = self._recovery_observation_digest(record, failure)
        binding_ref = record.get("binding_ref") if not pre_identity else None
        if binding_ref is not None and (type(binding_ref) is not str or not binding_ref):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID",
                "Runtime recovery record has an invalid binding reference",
            )
        count, duplicate = self._persist_recovery_observation(
            record,
            channel=channel,
            observation_digest=observation_digest,
            binding_ref=binding_ref,
        )
        if (
            failure.code == "RUNTIME_PROVIDER_UNAVAILABLE"
            and pre_identity
            and not self._record_has_materialization_history(record)
            and record.get("fallback_selected") is False
            and record.get("availability_fallback_profile_digest") is not None
        ):
            self._select_pre_identity_fallback(record)
            return True, None
        if failure.code == "RUNTIME_PROVIDER_UNAVAILABLE":
            if pre_identity:
                outcome = RuntimeRecoveryOutcome(
                    kind="wait" if count < 3 else "blocked",
                    reason="RuntimeProviderUnavailable",
                    next_check_at=(
                        int(time.time()) + _RUNTIME_RECOVERY_RETRY_SECONDS
                        if count < 3
                        else None
                    ),
                    observation_digest=observation_digest,
                )
            else:
                outcome = RuntimeRecoveryOutcome(
                    kind="wait" if count < 3 else "decision",
                    reason=(
                        "RuntimeProviderUnavailable"
                        if count < 3
                        else "RuntimeProviderRecoveryRequired"
                    ),
                    next_check_at=(
                        int(time.time()) + _RUNTIME_RECOVERY_RETRY_SECONDS
                        if count < 3
                        else None
                    ),
                    observation_digest=observation_digest,
                )
        else:
            outcome = RuntimeRecoveryOutcome(
                kind="wait" if count < 3 else ("blocked" if pre_identity else "decision"),
                reason=(
                    "RuntimeTransportUnavailable"
                    if count < 3 or pre_identity
                    else "RuntimeObservationUnavailable"
                ),
                next_check_at=(
                    int(time.time()) + _RUNTIME_RECOVERY_RETRY_SECONDS
                    if count < 3
                    else None
                ),
                observation_digest=observation_digest,
            )
        if duplicate:
            persisted = record["recovery"].get("last_outcome")
            if type(persisted) is dict:
                outcome = RuntimeRecoveryOutcome(**persisted)
        self._persist_recovery_outcome(record, outcome, None)
        return False, outcome

    def _recovery_receipt(
        self,
        subject: RuntimeSubject,
        outcome: RuntimeRecoveryOutcome,
    ) -> RuntimeProgressReceipt:
        """Return the persisted recovery fact without inventing provider state.

        A caller receives only a typed next action and opaque observation
        proof.  In particular, this operation neither reacquires a slot nor
        selects a replacement binding; those decisions remain with #110.
        """

        self._refresh()
        record = self._data["actions"].get(subject.stable_action_id)
        if type(record) is not dict or record.get("subject_digest") != subject.digest:
            raise RuntimeGatewayError(
                "RUNTIME_ACTION_IDENTITY_MISMATCH",
                "Runtime recovery receipt no longer binds the submitted subject",
            )
        persisted = record.get("recovery", {}).get("last_outcome")
        if type(persisted) is not dict:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID",
                "Runtime recovery outcome was not durably persisted",
            )
        try:
            durable_outcome = RuntimeRecoveryOutcome(**persisted)
        except (TypeError, RuntimeGatewayError) as error:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID",
                "Runtime recovery outcome is malformed",
            ) from error
        if durable_outcome != outcome:
            raise RuntimeGatewayError(
                "RUNTIME_ACTION_STATE_CHANGED",
                "Runtime recovery outcome changed before receipt emission",
            )
        payload = {
            "kind": (
                "runtime_planning_recovery_receipt.v1"
                if type(subject) is CampaignPlanningSubject
                else "runtime_work_run_recovery_receipt.v1"
            ),
            "subject_digest": subject.digest,
            "stable_action_id": subject.stable_action_id,
            "recovery_outcome": asdict(durable_outcome),
        }
        common = {
            "subject_digest": subject.digest,
            "stable_action_id": subject.stable_action_id,
            "status": durable_outcome.kind,
            "receipt_digest": digest_value(payload),
            "recovery_outcome": durable_outcome,
        }
        if type(subject) is CampaignPlanningSubject:
            return PlanningReceipt(
                **common,
                planning_output_artifact_digest=None,
            )
        return RuntimeProgressReceipt(**common)

    def _command_recovery_receipt(
        self,
        subject: RuntimeSubject,
        record: dict[str, Any],
        failure: _RuntimeFailure,
    ) -> RuntimeProgressReceipt:
        """Classify a command-path failure without retrying or replacing it."""

        retry_with_fallback, outcome = self._recovery_for_failure(
            subject,
            record,
            failure,
        )
        if retry_with_fallback:
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "a commanded Runtime binding cannot take a pre-identity fallback",
            )
        if outcome is not None:
            return self._recovery_receipt(subject, outcome)
        self._raise_failure(failure)
        raise AssertionError("_raise_failure must not return")

    @staticmethod
    def _recovery_observation_digest(
        record: Mapping[str, Any], failure: _RuntimeFailure
    ) -> str:
        payload = {
            "failure_code": failure.code,
            "stable_action_id": record.get("subject", {}).get(
                "stable_action_id"
            ),
            "provider_observation_id": failure.observation_id,
            "last_observation_digest": record.get("observation_digest"),
        }
        if (
            failure.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
            and failure.observation_id is None
        ):
            # A transport failure carries no provider observation identity. Each
            # fresh authoritative read is nevertheless a bounded retry; bind
            # its digest to the durable episode length so repeated reads can
            # reach the declared terminal transport outcome without inventing
            # provider identity.
            recovery = record.get("recovery")
            entries = (
                recovery.get("transport_unavailable", [])
                if isinstance(recovery, Mapping)
                else []
            )
            payload["transport_attempt"] = len(entries)
        return digest_value(payload)

    def _persist_recovery_observation(
        self,
        record: dict[str, Any],
        *,
        channel: str,
        observation_digest: str,
        binding_ref: str | None,
    ) -> tuple[int, bool]:
        stable_action_id = record["subject"]["stable_action_id"]
        expected_subject = record["subject_digest"]
        expected_recovery = deepcopy(record["recovery"])

        def commit(data: dict[str, Any]) -> tuple[dict[str, Any], int, bool]:
            current = data["actions"].get(stable_action_id)
            if (
                type(current) is not dict
                or current.get("subject_digest") != expected_subject
                or current.get("recovery") != expected_recovery
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Runtime recovery state changed during observation persistence",
                )
            recovery = current["recovery"]
            entries = recovery[channel]
            duplicate = any(
                entry["observation_digest"] == observation_digest for entry in entries
            )
            if not duplicate:
                entries.append(
                    {
                        "observation_digest": observation_digest,
                        "binding_ref": binding_ref,
                    }
                )
            count = sum(
                entry["binding_ref"] == binding_ref for entry in entries
            )
            return deepcopy(current), count, duplicate

        updated, count, duplicate = self._transact(commit)
        record.clear()
        record.update(updated)
        return count, duplicate

    def _persist_recovery_outcome(
        self,
        record: dict[str, Any],
        outcome: RuntimeRecoveryOutcome,
        _unused: None,
    ) -> None:
        stable_action_id = record["subject"]["stable_action_id"]
        expected_subject = record["subject_digest"]
        expected_recovery = deepcopy(record["recovery"])

        def commit(data: dict[str, Any]) -> dict[str, Any]:
            current = data["actions"].get(stable_action_id)
            if (
                type(current) is not dict
                or current.get("subject_digest") != expected_subject
                or current.get("recovery") != expected_recovery
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Runtime recovery outcome changed before persistence",
                )
            current["recovery"]["last_outcome"] = asdict(outcome)
            return deepcopy(current)

        updated = self._transact(commit)
        record.clear()
        record.update(updated)

    def _select_pre_identity_fallback(self, record: dict[str, Any]) -> None:
        fallback = record.get("availability_fallback_profile_digest")
        if type(fallback) is not str or _DIGEST_RE.fullmatch(fallback) is None:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                "Runtime availability fallback is invalid",
            )
        stable_action_id = record["subject"]["stable_action_id"]
        expected_subject = record["subject_digest"]
        expected_profile = record["profile_digest"]
        expected_recovery = deepcopy(record["recovery"])

        def commit(data: dict[str, Any]) -> dict[str, Any]:
            current = data["actions"].get(stable_action_id)
            if (
                type(current) is not dict
                or current.get("subject_digest") != expected_subject
                or current.get("profile_digest") != expected_profile
                or current.get("recovery") != expected_recovery
                or current.get("fallback_selected") is not False
                or self._record_has_materialization_history(current)
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Runtime availability fallback no longer binds an unmaterialized action",
                )
            current["profile_digest"] = fallback
            current["fallback_selected"] = True
            subject = _subject_from_canonical(current["subject"])
            current["assignment_digest"] = _assignment_digest(
                subject,
                {
                    key: current[key]
                    for key in _ASSIGNMENT_RECORD_KEYS
                },
            )
            return deepcopy(current)

        updated = self._transact(commit)
        record.clear()
        record.update(updated)

    def _record_observation(
        self,
        record: dict[str, Any],
        verdict: _RuntimeObservationVerdict,
    ) -> None:
        if verdict.kind not in {"prepared", "bound"}:
            raise RuntimeGatewayError(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "Only a validated Runtime observation verdict can be recorded",
            )
        observation = verdict.observation
        assert observation is not None
        canonical = _json_projection(asdict(observation))
        observation_digest = digest_value(canonical)
        stable_action_id = observation.stable_action_id
        expected_subject = record.get("subject_digest")
        expected_profile = record.get("profile_digest")
        expected_observation = record.get("observation_digest")
        expected_workspace = record.get("workspace_id")
        expected_binding = record.get("binding_ref")
        expected_agent = record.get("agent_id")
        expected_session = record.get("session_id")
        expected_ever_bound = record.get("ever_bound")
        expected_snapshot = record.get("last_observation")

        def commit(data: dict[str, Any]) -> dict[str, Any]:
            current = data["actions"].get(stable_action_id)
            if (
                not isinstance(current, dict)
                or current.get("subject_digest") != expected_subject
                or current.get("profile_digest") != expected_profile
                or current.get("workspace_id") != expected_workspace
                or current.get("binding_ref") != expected_binding
                or current.get("agent_id") != expected_agent
                or current.get("session_id") != expected_session
                or current.get("ever_bound") != expected_ever_bound
                or current.get("last_observation") != expected_snapshot
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_ACTION_IDENTITY_MISMATCH",
                    "Runtime observation no longer binds the persisted action identity",
                )
            durable_observation = current.get("observation_digest")
            if durable_observation not in {expected_observation, observation_digest}:
                raise RuntimeGatewayError(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Runtime observation CAS lost to a newer durable readback",
                )
            if current.get("ever_bound") is True and verdict.kind != "bound":
                raise RuntimeGatewayError(
                    "RUNTIME_OBSERVATION_INVALID",
                    "a previously Bound Runtime action cannot return to Prepared",
                )
            current.update(
                {
                    "binding_ref": observation.binding_ref,
                    "agent_id": observation.agent_id,
                    "session_id": observation.session_id,
                    "workspace_id": observation.workspace_id,
                    "lifecycle": observation.lifecycle,
                    "planning_output_artifact_digest": getattr(
                        observation, "planning_output_artifact_digest", None
                    ),
                    "observation_digest": observation_digest,
                    "materialization_observed": True,
                    "ever_bound": (
                        current.get("ever_bound") is True
                        or verdict.kind == "bound"
                    ),
                    "last_observation": canonical,
                }
            )
            current.pop("observations", None)
            return deepcopy(current)

        updated = self._transact(commit)
        record.clear()
        record.update(updated)

    def _require_bound_verdict(
        self, stable_action_id: str
    ) -> _RuntimeObservationVerdict:
        verdict = self._observe_verdict(stable_action_id)
        if (
            verdict.kind
            in _RUNTIME_OBSERVATION_FAILURE_VERDICT_KINDS
        ):
            assert verdict.failure is not None
            self._raise_command_failure_or_signal(verdict.failure)
        if verdict.kind != "bound":
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "command readback did not bind an Agent, session, and Runtime binding",
            )
        return verdict

    def _command_with_readback(
        self,
        stable_action_id: str,
        command: RuntimeTransition,
        *,
        _after_dispatch: Callable[[], None] | None = None,
    ) -> _RuntimeObservationVerdict:
        self._refresh_before_adapter_io()
        try:
            result = self._adapter.command(
                stable_action_id,
                command,
            )
        except (OSError, TimeoutError):
            result = _RuntimeFailure.transport()
        except Exception:
            result = _RuntimeFailure(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID", "Runtime provider command failed"
            )
        verdict = _RuntimeCommandResultProtocol.validate(
            result,
            stable_action_id,
            command,
        )
        if verdict.kind == "invalid":
            assert verdict.failure is not None
            self._raise_command_failure_or_signal(verdict.failure)
        if verdict.kind == "failure":
            assert verdict.failure is not None
            self._raise_command_failure_or_signal(verdict.failure)
        if verdict.kind == "recoverable_failure":
            # A transport/ack ambiguity may follow a successful command.
            # Readback is authoritative; semantic/unknown failures never get
            # converted into a successful transition merely by a later poll.
            observation_verdict = self._observe_verdict(stable_action_id)
            if (
                observation_verdict.kind
                in _RUNTIME_OBSERVATION_FAILURE_VERDICT_KINDS
            ):
                assert observation_verdict.failure is not None
                self._raise_command_failure_or_signal(observation_verdict.failure)
            if observation_verdict.kind != "bound":
                raise RuntimeGatewayError(
                    "RUNTIME_OBSERVATION_INVALID",
                    "command acknowledgement loss read back an unbound Runtime action",
                )
            self._validate_command_effect(command, observation_verdict)
            if _after_dispatch is not None:
                _after_dispatch()
            return observation_verdict
        assert verdict.kind == "receipt"
        assert verdict.receipt is not None
        observation_verdict = self._require_bound_verdict(
            stable_action_id
        )
        self._validate_command_effect(command, observation_verdict)
        if _after_dispatch is not None:
            _after_dispatch()
        return observation_verdict

    @staticmethod
    def _raise_command_failure_or_signal(failure: _RuntimeFailure) -> None:
        if failure.code in {
            "RUNTIME_PROVIDER_UNAVAILABLE",
            "RUNTIME_TRANSPORT_UNAVAILABLE",
            "RUNTIME_CONFIGURATION_INVALID",
        }:
            raise _RuntimeRecoverySignal(failure)
        RuntimeGateway._raise_failure(failure)

    def _validate_command_effect(
        self,
        command: RuntimeTransition,
        verdict: _RuntimeObservationVerdict,
    ) -> None:
        if verdict.kind != "bound":
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "Runtime command readback is not a Bound verdict",
            )
        observation = verdict.observation
        assert observation is not None
        valid = (
            (
                command is RuntimeCommand.START
                and observation.lifecycle in {"running", "completed"}
            )
            or (
                command is RuntimeCommand.RESUME
                and observation.lifecycle in {"running", "completed"}
            )
            or (
                command in {RuntimeCommand.PARK, RuntimeCommand.INTERRUPT}
                and observation.lifecycle == "parked"
            )
            or (command is RuntimeCommand.FENCE and observation.fenced is True)
            or (command is RuntimeCommand.RETIRE and observation.lifecycle == "retired")
            or (
                type(command) is PermissionResponse
                and _completed_permission_effect_matches(command, observation)
            )
        )
        if not valid:
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "Runtime command readback did not prove the requested state transition",
            )

    def _progress_receipt(
        self,
        subject: RuntimeSubject,
        verdict: _RuntimeObservationVerdict,
        *,
        command: RuntimeTransition | None = None,
        wake_cursor: str | None = None,
        wake_hints: tuple[str, ...] = (),
        permission_required: PermissionRequired | None = None,
    ) -> RuntimeProgressReceipt:
        # Receipt emission is an authoritative Gateway boundary.  Re-read the
        # complete durable state under the Journal lock so an earlier completed
        # action cannot lose or drift its output while another action advances.
        self._refresh()
        if verdict.kind != "bound":
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "Runtime progress receipt requires one Bound verdict",
            )
        observation = verdict.observation
        assert observation is not None
        kind = "planning" if isinstance(subject, CampaignPlanningSubject) else "work_run"
        observation_snapshot = _json_projection(asdict(observation))
        observation_digest = digest_value(observation_snapshot)
        terminal_binding_evidence = self._terminal_binding_evidence(
            subject,
            observation,
            observation_digest,
        )
        payload = {
            "kind": f"runtime_{kind}_receipt.v1",
            "subject_digest": subject.digest,
            "stable_action_id": subject.stable_action_id,
            "lifecycle": observation.lifecycle,
            "output_artifact_digest": observation.output_artifact_digest,
            "command": _transition_canonical(command),
            "permission_required": (
                None
                if permission_required is None
                else asdict(permission_required)
            ),
            "terminal_binding_evidence": (
                None
                if terminal_binding_evidence is None
                else asdict(terminal_binding_evidence)
            ),
            "observation_digest": observation_digest,
        }
        if observation.lifecycle == "completed":
            output_digest = observation.output_artifact_digest
            if output_digest is None:
                raise RuntimeGatewayError(
                    "RUNTIME_OUTPUT_ARTIFACT_MISSING",
                    "completed Runtime action omitted its Artifact-backed output",
                )
            self._artifacts.prove_runtime_output(
                output_digest,
                subject_digest=subject.digest,
                stable_action_id=subject.stable_action_id,
                authority_digest=subject.authority_digest,
            )
        if isinstance(subject, CampaignPlanningSubject):
            return PlanningReceipt(
                subject_digest=subject.digest,
                stable_action_id=subject.stable_action_id,
                status=observation.lifecycle,
                receipt_digest=digest_value(payload),
                command=command,
                wake_cursor=wake_cursor,
                wake_hints=wake_hints,
                output_artifact_digest=observation.output_artifact_digest,
                permission_required=permission_required,
                terminal_binding_evidence=terminal_binding_evidence,
                planning_output_artifact_digest=observation.output_artifact_digest,
            )
        return RuntimeProgressReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            status=observation.lifecycle,
            receipt_digest=digest_value(payload),
            command=command,
            wake_cursor=wake_cursor,
            wake_hints=wake_hints,
            output_artifact_digest=observation.output_artifact_digest,
            permission_required=permission_required,
            terminal_binding_evidence=terminal_binding_evidence,
        )

    @staticmethod
    def _terminal_binding_evidence(
        subject: RuntimeSubject,
        observation: _BoundRuntimeObservation,
        observation_digest: str,
    ) -> TerminalBindingEvidence | None:
        """Derive replacement precondition evidence from a terminal readback.

        The Provider observation has already passed the exact subject, prompt,
        authority, workspace, Agent/session, and binding validation on every
        call path that can emit a receipt.  The returned digest freezes that
        readback as the durable workspace checkpoint; no replacement is
        selected or created here.
        """

        if (
            type(subject) is not WorkRunSubject
            or observation.lifecycle != "retired"
            or observation.fenced is not True
        ):
            return None
        evidence = {
            "kind": "runtime_terminal_binding_evidence.v1",
            "stable_action_id": subject.stable_action_id,
            "subject_digest": subject.digest,
            "plan_revision_digest": subject.plan_revision_digest,
            "work_run_key": subject.work_run_key,
            "authority_subtree_digest": subject.authority_digest,
            "prompt_artifact_digest": observation.prompt_artifact_digest,
            "binding_ref": observation.binding_ref,
            "agent_id": observation.agent_id,
            "session_id": observation.session_id,
            "workspace_id": observation.workspace_id,
            "fenced": observation.fenced,
            "lifecycle": observation.lifecycle,
            "workspace_checkpoint_digest": observation_digest,
        }
        return TerminalBindingEvidence(
            stable_action_id=subject.stable_action_id,
            evidence_digest=digest_value(evidence),
        )

    def _load(self) -> dict[str, Any]:
        with self._journal.exclusive():
            return self._load_unlocked(wrap_completed_output_errors=True)

    def _load_unlocked(
        self, *, wrap_completed_output_errors: bool = False
    ) -> dict[str, Any]:
        value = self._journal.read_unlocked()
        if value is None:
            return {
                "schema_version": 4,
                "campaigns": {},
                "actions": {},
                "preflights": {},
                "action_identities": {},
                "plan_invalidation": {},
            }
        schema_v3_keys = frozenset(
            {
                "schema_version",
                "campaigns",
                "actions",
                "preflights",
                "action_identities",
            }
        )
        schema_v1_keys = schema_v3_keys - {"action_identities"}
        migrate_v1 = (
            type(value) is dict
            and frozenset(value) == schema_v1_keys
            and value.get("schema_version") == 1
            and all(
                type(value.get(key)) is dict
                for key in ("campaigns", "actions", "preflights")
            )
        )
        migrate_v2 = (
            type(value) is dict
            and frozenset(value) == schema_v3_keys
            and value.get("schema_version") == 2
            and all(
                type(value.get(key)) is dict
                for key in ("campaigns", "actions", "preflights", "action_identities")
            )
        )
        migrate_v3 = (
            type(value) is dict
            and frozenset(value) == schema_v3_keys
            and value.get("schema_version") == 3
            and all(
                type(value.get(key)) is dict
                for key in ("campaigns", "actions", "preflights", "action_identities")
            )
        )
        schema_v4_keys = schema_v3_keys | {"plan_invalidation"}
        valid_v4_shape = (
            type(value) is dict
            and frozenset(value) == schema_v4_keys
            and value.get("schema_version") == 4
            and all(
                type(value.get(key)) is dict
                for key in (
                    "campaigns",
                    "actions",
                    "preflights",
                    "action_identities",
                    "plan_invalidation",
                )
            )
        )
        if not migrate_v1 and not migrate_v2 and not migrate_v3 and not valid_v4_shape:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "RuntimeGateway durable record has an unknown schema"
            )
        normalized = deepcopy(value)
        if migrate_v1:
            normalized["schema_version"] = 2
            normalized["action_identities"] = {}
        if migrate_v1 or migrate_v2:
            for record in normalized["actions"].values():
                if type(record) is dict and "recovery" not in record:
                    record["recovery"] = _initial_recovery_state()
        if migrate_v1 or migrate_v2 or migrate_v3:
            normalized["schema_version"] = 4
            normalized["plan_invalidation"] = {}
        self._validate_plan_invalidation_store(normalized["plan_invalidation"])
        rebuilt_identities: dict[str, dict[str, str]] = {}

        def rebuild_identity(
            stable_action_id: object,
            identity: dict[str, str],
        ) -> None:
            if not isinstance(stable_action_id, str) or not stable_action_id:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway stable-action identity key is invalid",
                )
            existing = rebuilt_identities.get(stable_action_id)
            if existing is not None and existing != identity:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway stable action is bound to conflicting subject identities",
                )
            rebuilt_identities[stable_action_id] = identity

        campaign_preflight_links: list[
            tuple[str, str, dict[str, Any]]
        ] = []
        for campaign_handle, campaign in normalized["campaigns"].items():
            if (
                type(campaign_handle) is not str
                or not campaign_handle
                or type(campaign) is not dict
                or set(campaign)
                != {
                    "repository",
                    "campaign_key",
                    "overrides",
                    "overrides_digest",
                    "preflight_bindings",
                }
                or type(campaign["repository"]) is not str
                or not campaign["repository"]
                or type(campaign["campaign_key"]) is not str
                or not campaign["campaign_key"]
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway Campaign record has an unknown schema",
                )
            _campaign_overrides_from_value(campaign["overrides"])
            if (
                type(campaign["overrides_digest"]) is not str
                or campaign["overrides_digest"]
                != digest_value(campaign["overrides"])
                or type(campaign["preflight_bindings"]) is not dict
                or not campaign["preflight_bindings"]
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway Campaign override binding is invalid",
                )
            for stable_action_id, binding in campaign[
                "preflight_bindings"
            ].items():
                if (
                    type(stable_action_id) is not str
                    or not stable_action_id
                    or type(binding) is not dict
                    or set(binding)
                    != {
                        "subject",
                        "subject_digest",
                        "campaign_overrides_digest",
                        "assignment_digest",
                    }
                    or type(binding["subject_digest"]) is not str
                    or _DIGEST_RE.fullmatch(binding["subject_digest"])
                    is None
                    or binding["campaign_overrides_digest"]
                    != campaign["overrides_digest"]
                    or type(binding["assignment_digest"]) is not str
                    or _DIGEST_RE.fullmatch(binding["assignment_digest"])
                    is None
                ):
                    raise RuntimeGatewayError(
                        "RUNTIME_STORE_INVALID",
                        "RuntimeGateway Campaign preflight binding is invalid",
                    )
                try:
                    link_subject = _subject_from_canonical(
                        binding["subject"]
                    )
                except RuntimeGatewayError as error:
                    raise RuntimeGatewayError(
                        "RUNTIME_STORE_INVALID",
                        "RuntimeGateway Campaign preflight subject is invalid",
                    ) from error
                if (
                    type(link_subject) is not CampaignPlanningSubject
                    or link_subject.canonical() != binding["subject"]
                    or link_subject.stable_action_id != stable_action_id
                    or link_subject.digest != binding["subject_digest"]
                    or link_subject.repository != campaign["repository"]
                    or link_subject.campaign_key != campaign["campaign_key"]
                    or link_subject.campaign_handle != campaign_handle
                ):
                    raise RuntimeGatewayError(
                        "RUNTIME_STORE_INVALID",
                        "RuntimeGateway Campaign preflight ownership is invalid",
                    )
                campaign_preflight_links.append(
                    (campaign_handle, stable_action_id, binding)
                )

        preflight_subjects: dict[str, CampaignPlanningSubject] = {}
        for stable_action_id, preflight in normalized["preflights"].items():
            if (
                type(preflight) is not dict
                or set(preflight)
                != {
                    "subject",
                    "subject_digest",
                    "campaign_overrides_digest",
                    "assignment",
                    "assignment_digest",
                    "receipt_digest",
                }
                or type(preflight.get("subject_digest")) is not str
                or _DIGEST_RE.fullmatch(preflight["subject_digest"]) is None
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway planning preflight identity is invalid",
                )
            try:
                preflight_subject = _subject_from_canonical(
                    preflight["subject"]
                )
            except RuntimeGatewayError as error:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway planning preflight subject is invalid",
                ) from error
            if (
                type(preflight_subject) is not CampaignPlanningSubject
                or preflight_subject.canonical() != preflight["subject"]
                or preflight_subject.stable_action_id != stable_action_id
                or preflight_subject.digest != preflight["subject_digest"]
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway planning preflight subject does not bind",
                )
            preflight_subjects[stable_action_id] = preflight_subject
            try:
                expected_receipt = _preflight_receipt_digest(
                    stable_action_id, preflight
                )
            except RuntimeGatewayError as error:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway planning preflight receipt is invalid",
                ) from error
            if preflight["receipt_digest"] != expected_receipt:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway planning preflight receipt digest is invalid",
                )
            assignment = _validate_assignment_value(preflight["assignment"])
            if preflight["assignment_digest"] != _assignment_digest(
                preflight_subject,
                {**assignment, "fallback_selected": False},
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway planning preflight assignment seal is invalid",
                )
            campaign = normalized["campaigns"].get(
                preflight_subject.campaign_handle
            )
            if (
                type(campaign) is not dict
                or campaign.get("repository")
                != preflight_subject.repository
                or campaign.get("campaign_key")
                != preflight_subject.campaign_key
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway planning preflight lacks its exact Campaign",
                )
            try:
                expected_assignment = self._resolve_assignment(
                    preflight_subject.repository,
                    RuntimeSelector.coordinator(),
                    None,
                    campaign["overrides"],
                )
            except RuntimeGatewayError as error:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway planning preflight assignment cannot be recovered",
                ) from error
            if assignment != expected_assignment:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway planning preflight assignment changed from its frozen source",
                )
            rebuild_identity(
                stable_action_id,
                {
                    "subject_kind": "campaign_planning",
                    "subject_digest": preflight["subject_digest"],
                },
            )
        linked_preflights: set[str] = set()
        for (
            _campaign_handle,
            stable_action_id,
            binding,
        ) in campaign_preflight_links:
            preflight = normalized["preflights"].get(stable_action_id)
            if (
                stable_action_id in linked_preflights
                or type(preflight) is not dict
                or preflight["subject"] != binding["subject"]
                or preflight["subject_digest"] != binding["subject_digest"]
                or preflight["campaign_overrides_digest"]
                != binding["campaign_overrides_digest"]
                or preflight["assignment_digest"]
                != binding["assignment_digest"]
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway Campaign and preflight records do not bind",
                )
            linked_preflights.add(stable_action_id)
        if linked_preflights != set(normalized["preflights"]):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID",
                "RuntimeGateway preflight lacks one exact Campaign binding",
            )
        for stable_action_id, record in normalized["actions"].items():
            if type(record) is not dict or frozenset(record) != _GATEWAY_ACTION_KEYS:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID", "RuntimeGateway action record is invalid"
                )
            try:
                subject = _subject_from_canonical(record.get("subject"))
                assignment = _assignment_record_from_value(
                    {
                        key: record.get(key)
                        for key in _ASSIGNMENT_RECORD_KEYS
                    }
                )
            except RuntimeGatewayError as error:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway action identity is invalid",
                ) from error
            if (
                subject.canonical() != record.get("subject")
                or subject.stable_action_id != stable_action_id
                or record.get("subject_digest") != subject.digest
                or any(record.get(key) != value for key, value in assignment.items())
                or record.get("assignment_digest")
                != _assignment_digest(subject, assignment)
                or not _gateway_action_recovery_state_is_valid(record, subject)
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway action subject identity is invalid",
                )
            campaign = normalized["campaigns"].get(
                subject.campaign_handle
            )
            if (
                type(campaign) is not dict
                or campaign.get("repository") != subject.repository
                or campaign.get("campaign_key") != subject.campaign_key
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway action lacks its exact Campaign",
                )
            selector = (
                RuntimeSelector.coordinator()
                if type(subject) is CampaignPlanningSubject
                else _selector_for_purpose(subject.purpose)
            )
            ticket_key = (
                None
                if type(subject) is CampaignPlanningSubject
                else subject.ticket_key
            )
            try:
                resolved_assignment = self._resolve_assignment(
                    subject.repository,
                    selector,
                    ticket_key,
                    campaign["overrides"],
                )
                expected_assignment = {
                    **resolved_assignment,
                    "fallback_selected": assignment["fallback_selected"],
                }
                if assignment["fallback_selected"] is True:
                    fallback = resolved_assignment[
                        "availability_fallback_profile_digest"
                    ]
                    if fallback is None:
                        raise RuntimeGatewayError(
                            "RUNTIME_STORE_INVALID",
                            "Runtime assignment selected an unavailable fallback",
                        )
                    expected_assignment["profile_digest"] = fallback
            except RuntimeGatewayError as error:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway action assignment cannot be recovered",
                ) from error
            if assignment != expected_assignment:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway action assignment changed from its frozen source",
                )
            if type(subject) is CampaignPlanningSubject:
                owning_preflight_subject = preflight_subjects.get(
                    stable_action_id
                )
                if (
                    owning_preflight_subject is None
                    or owning_preflight_subject.canonical()
                    != record["subject"]
                ):
                    raise RuntimeGatewayError(
                        "RUNTIME_STORE_INVALID",
                        "RuntimeGateway planning action lacks its exact preflight",
                    )
            rebuild_identity(stable_action_id, self._subject_identity(subject))
        self._verify_completed_outputs_unlocked(
            normalized,
            wrap_artifact_errors=wrap_completed_output_errors,
        )
        persisted_identities = normalized.get("action_identities")
        if (
            not migrate_v1
            and not migrate_v2
            and persisted_identities != rebuilt_identities
        ):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID",
                "RuntimeGateway stable-action identity registry does not match durable subjects",
            )
        normalized["action_identities"] = rebuilt_identities
        if migrate_v1 or migrate_v2:
            # The caller holds the journal lock.  Publish only after every
            # legacy Campaign, preflight, action, and rebuilt identity has
            # passed the v2 validator; conflicts leave the v1 bytes intact.
            self._journal.replace_unlocked(normalized)
        return normalized

    def _refresh(self) -> None:
        with self._journal.exclusive():
            self._data = self._load_unlocked()

    def _refresh_before_adapter_io(self) -> None:
        """Prove every durable completed output immediately before Adapter I/O."""

        with self._journal.exclusive():
            self._data = self._load_unlocked()

    @staticmethod
    def _validate_plan_invalidation_store(value: object) -> None:
        """Validate the closed durable #133 journal projection on every read."""

        if type(value) is not dict:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID",
                "RuntimeGateway Plan Invalidation collection is invalid",
            )
        legacy_record = {
            "report_digest",
            "receipt_digest",
            "subject_digest",
            "repository",
            "campaign_key",
            "plan_revision_digest",
            "ticket_key",
            "work_run_key",
            "runtime_binding_id",
            "authority_subtree_digest",
            "reporter_role",
            "evidence_digest",
            "dedup_identity",
            "invalidated_obligation",
            "required_effects",
            "workspace_identity",
            "observation",
            "capability_policy",
            "authority_record_digest",
            "policy_witness_digest",
        }
        record_with_source_lineage = legacy_record | {"source_evidence_digests"}
        legacy_observation = {
            "kind",
            "repository",
            "campaign_key",
            "plan_revision_digest",
            "ticket_key",
            "work_run_key",
            "runtime_binding_id",
            "authority_subtree_digest",
            "reporter_role",
            "report_digest",
            "evidence_digest",
            "dedup_identity",
            "invalidated_obligation",
            "required_effects",
            "workspace_identity",
        }
        observation_with_source_lineage = legacy_observation | {
            "source_evidence_digests"
        }
        expected_policy = {
            "worker_can_edit_issues",
            "worker_can_edit_blockers",
            "worker_can_edit_campaign_membership",
            "worker_can_activate_plan_revision",
            "worker_can_merge",
            "worker_can_expand_authority",
            "worker_can_invoke_global_planning",
        }
        for dedup_key, record in value.items():
            if (
                type(dedup_key) is not str
                or _DIGEST_RE.fullmatch(dedup_key) is None
                or type(record) is not dict
                or set(record) not in (legacy_record, record_with_source_lineage)
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway Plan Invalidation record schema is invalid",
                )
            if any(
                type(record.get(name)) is not str
                or _DIGEST_RE.fullmatch(record[name]) is None
                for name in (
                    "report_digest",
                    "receipt_digest",
                    "subject_digest",
                    "plan_revision_digest",
                    "authority_subtree_digest",
                    "evidence_digest",
                    "policy_witness_digest",
                    "authority_record_digest",
                )
            ) or any(
                type(record.get(name)) is not str or not record[name]
                for name in (
                    "repository",
                    "campaign_key",
                    "ticket_key",
                    "work_run_key",
                    "runtime_binding_id",
                    "reporter_role",
                    "dedup_identity",
                    "invalidated_obligation",
                    "workspace_identity",
                )
            ) or record["reporter_role"] not in _PLAN_INVALIDATION_REPORT_ROLES:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway Plan Invalidation identity is invalid",
                )
            effects = record["required_effects"]
            if type(effects) is not list or any(
                type(effect) is not str or not effect for effect in effects
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway Plan Invalidation effects are invalid",
                )
            observation = record["observation"]
            has_source_lineage = "source_evidence_digests" in record
            source_digests = record.get("source_evidence_digests")
            source_lineage_valid = (
                not has_source_lineage
                or (
                    type(source_digests) is list
                    and bool(source_digests)
                    and all(
                        type(digest) is str
                        and _DIGEST_RE.fullmatch(digest) is not None
                        for digest in source_digests
                    )
                    and source_digests == sorted(set(source_digests))
                )
            )
            observation_keys = (
                set(observation) if type(observation) is dict else set()
            )
            if (
                type(observation) is not dict
                or observation_keys
                not in (legacy_observation, observation_with_source_lineage)
                or observation.get("kind") != "plan_invalidation_observation.v1"
                or observation.get("required_effects") != effects
                or ("source_evidence_digests" in observation_keys)
                != has_source_lineage
                or not source_lineage_valid
                or observation.get("source_evidence_digests") != source_digests
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway Plan Invalidation observation is invalid",
                )
            for name in legacy_observation - {"kind", "required_effects"}:
                if observation.get(name) != record.get(name):
                    raise RuntimeGatewayError(
                        "RUNTIME_STORE_INVALID",
                        "RuntimeGateway Plan Invalidation observation identity changed",
                    )
            policy = record["capability_policy"]
            if (
                type(policy) is not dict
                or set(policy) != expected_policy
                or any(value is not False for value in policy.values())
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway capability policy proof is not closed",
                )
            report_value = {
                "kind": "plan_invalidation_report.v1",
                "repository": record["repository"],
                "campaign_key": record["campaign_key"],
                "plan_revision_digest": record["plan_revision_digest"],
                "ticket_key": record["ticket_key"],
                "work_run_key": record["work_run_key"],
                "runtime_binding_id": record["runtime_binding_id"],
                "authority_subtree_digest": record["authority_subtree_digest"],
                "reporter_role": record["reporter_role"],
                "evidence_digest": record["evidence_digest"],
                "dedup_identity": record["dedup_identity"],
                "invalidated_obligation": record["invalidated_obligation"],
                "required_effects": effects,
                "workspace_identity": record["workspace_identity"],
            }
            if record["authority_record_digest"] != digest_value(
                {
                    "plan_revision_digest": record["plan_revision_digest"],
                    "ticket_key": record["ticket_key"],
                    "authority_subtree_digest": record["authority_subtree_digest"],
                    "policy_witness_digest": record["policy_witness_digest"],
                    "capability_policy": policy,
                }
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway authority proof changed",
                )
            if record["report_digest"] != digest_value(report_value):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway Plan Invalidation report digest changed",
                )
            if record["observation"].get("report_digest") != record["report_digest"]:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway Plan Invalidation report identity changed",
                )
            if record["receipt_digest"] != digest_value(
                {
                    "kind": "plan_invalidation_receipt.v1",
                    "report_digest": record["report_digest"],
                    "subject_digest": record["subject_digest"],
                    "authority_record_digest": record["authority_record_digest"],
                }
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway Plan Invalidation receipt digest changed",
                )
            if dedup_key != digest_value(
                {
                    "subject_digest": record["subject_digest"],
                    "dedup_identity": record["dedup_identity"],
                }
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway Plan Invalidation dedup identity changed",
                )

    def _verify_completed_outputs_unlocked(
        self,
        data: Mapping[str, Any],
        *,
        wrap_artifact_errors: bool = False,
    ) -> None:
        """Prove every completed output before a Gateway state boundary."""

        actions = data.get("actions")
        if type(actions) is not dict:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID",
                "RuntimeGateway completed-output collection is invalid",
            )
        for stable_action_id, record in actions.items():
            if type(stable_action_id) is not str or type(record) is not dict:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway completed-output record is invalid",
                )
            output_digest = record.get("planning_output_artifact_digest")
            if output_digest is None:
                continue
            try:
                subject = _subject_from_canonical(record.get("subject"))
            except RuntimeGatewayError as error:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway completed-output subject is invalid",
                ) from error
            if subject.stable_action_id != stable_action_id:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway completed-output action is invalid",
                )
            try:
                self._artifacts.prove_runtime_output(
                    output_digest,
                    subject_digest=subject.digest,
                    stable_action_id=subject.stable_action_id,
                    authority_digest=subject.authority_digest,
                )
            except RuntimeGatewayError as error:
                if not wrap_artifact_errors:
                    raise
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway completed output proof is invalid",
                ) from error

    def _transact(self, mutation: Callable[[dict[str, Any]], Any]) -> Any:
        with self._journal.exclusive():
            durable = self._load_unlocked()
            candidate = deepcopy(durable)
            try:
                result = mutation(candidate)
                self._verify_completed_outputs_unlocked(candidate)
                if canonical_bytes(candidate) != canonical_bytes(durable):
                    self._pending_save_data = candidate
                    self._save()
            except Exception:
                try:
                    self._data = self._load_unlocked()
                except RuntimeGatewayError:
                    self._data = durable
                raise
            finally:
                self._pending_save_data = None
            self._data = candidate
            return result

    def _save(self) -> None:
        self._journal.replace_unlocked(
            self._data if self._pending_save_data is None else self._pending_save_data
        )


def _mapping_from_value(value: object) -> ProfileMapping:
    if (
        type(value) is not dict
        or set(value)
        != {
            "primary_profile_digest",
            "availability_fallback_profile_digest",
        }
    ):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID", "persisted Campaign override is malformed"
        )
    try:
        return ProfileMapping(
            primary_profile_digest=value["primary_profile_digest"],
            availability_fallback_profile_digest=value[
                "availability_fallback_profile_digest"
            ],
        )
    except (TypeError, RuntimeGatewayError) as error:
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID", "persisted Campaign override is invalid"
        ) from error


_ASSIGNMENT_KEYS = frozenset(
    {
        "selector",
        "configuration_source",
        "profile_digest",
        "availability_fallback_profile_digest",
    }
)
_ASSIGNMENT_RECORD_KEYS = _ASSIGNMENT_KEYS | frozenset({"fallback_selected"})
_GATEWAY_ACTION_KEYS = frozenset(
    {
        "subject",
        "subject_digest",
        "selector",
        "configuration_source",
        "profile_digest",
        "availability_fallback_profile_digest",
        "fallback_selected",
        "assignment_digest",
        "prompt_artifact_digest",
        "binding_ref",
        "agent_id",
        "session_id",
        "workspace_id",
        "lifecycle",
        "planning_output_artifact_digest",
        "observation_digest",
        "materialization_observed",
        "ever_bound",
        "last_observation",
        "recovery",
    }
)


def _initial_recovery_state() -> dict[str, Any]:
    return {
        "provider_unavailable": [],
        "transport_unavailable": [],
        "last_outcome": None,
    }


def _recovery_state_is_valid(value: object) -> bool:
    if type(value) is not dict or set(value) != {
        "provider_unavailable",
        "transport_unavailable",
        "last_outcome",
    }:
        return False
    seen: set[str] = set()
    for field_name in ("provider_unavailable", "transport_unavailable"):
        entries = value[field_name]
        if type(entries) is not list:
            return False
        for entry in entries:
            if (
                type(entry) is not dict
                or set(entry) != {"observation_digest", "binding_ref"}
                or type(entry["observation_digest"]) is not str
                or _DIGEST_RE.fullmatch(entry["observation_digest"]) is None
                or (
                    entry["binding_ref"] is not None
                    and (
                        type(entry["binding_ref"]) is not str
                        or not entry["binding_ref"]
                    )
                )
                or entry["observation_digest"] in seen
            ):
                return False
            seen.add(entry["observation_digest"])
    outcome = value["last_outcome"]
    if outcome is None:
        return True
    if type(outcome) is not dict or set(outcome) != {
        "kind",
        "reason",
        "next_check_at",
        "observation_digest",
    }:
        return False
    try:
        typed_outcome = RuntimeRecoveryOutcome(**outcome)
    except (TypeError, RuntimeGatewayError):
        return False
    channel_by_reason = {
        "RuntimeProviderUnavailable": "provider_unavailable",
        "RuntimeProviderRecoveryRequired": "provider_unavailable",
        "RuntimeTransportUnavailable": "transport_unavailable",
        "RuntimeObservationUnavailable": "transport_unavailable",
    }
    channel = channel_by_reason.get(typed_outcome.reason)
    if channel is not None and not any(
        entry["observation_digest"] == typed_outcome.observation_digest
        for entry in value[channel]
    ):
        return False
    return True


def _gateway_action_recovery_state_is_valid(
    record: Mapping[str, Any],
    subject: RuntimeSubject,
) -> bool:
    """Validate the complete closed Gateway action state before Adapter I/O."""

    try:
        prompt_digest = record.get("prompt_artifact_digest")
        expected_prompt_digest = (
            subject.planning_request_artifact_digest
            if type(subject) is CampaignPlanningSubject
            else subject.prompt_artifact_digest
        )
        if (
            type(prompt_digest) is not str
            or _DIGEST_RE.fullmatch(prompt_digest) is None
            or prompt_digest != expected_prompt_digest
            or type(record.get("materialization_observed")) is not bool
            or type(record.get("ever_bound")) is not bool
            or type(record.get("fallback_selected")) is not bool
            or not _recovery_state_is_valid(record.get("recovery"))
        ):
            return False
        if record["fallback_selected"] is True and (
            record.get("availability_fallback_profile_digest") is None
            or record.get("profile_digest")
            != record.get("availability_fallback_profile_digest")
        ):
            return False

        lifecycle = record.get("lifecycle")
        if lifecycle is not None and (
            type(lifecycle) is not str
            or lifecycle
            not in {"prepared", "running", "parked", "completed", "retired"}
        ):
            return False

        identity = tuple(
            record.get(name)
            for name in ("binding_ref", "agent_id", "session_id")
        )
        identity_is_absent = identity == (None, None, None)
        identity_is_complete = all(
            type(value) is str and bool(value) for value in identity
        )
        workspace_id = record.get("workspace_id")
        workspace_is_absent = workspace_id is None
        workspace_is_present = (
            type(workspace_id) is str and bool(workspace_id)
        )
        output_digest = record.get("planning_output_artifact_digest")
        observation_digest = record.get("observation_digest")
        output_is_absent = output_digest is None
        output_is_digest = (
            type(output_digest) is str
            and _DIGEST_RE.fullmatch(output_digest) is not None
        )
        observation_is_absent = observation_digest is None
        observation_is_digest = (
            type(observation_digest) is str
            and _DIGEST_RE.fullmatch(observation_digest) is not None
        )
        materialized = record["materialization_observed"]
        ever_bound = record["ever_bound"]
        snapshot = record.get("last_observation")

        if lifecycle is None:
            return (
                materialized is False
                and ever_bound is False
                and workspace_is_absent
                and identity_is_absent
                and output_is_absent
                and observation_is_absent
                and snapshot is None
            )
        if (
            materialized is not True
            or type(snapshot) is not dict
            or not observation_is_digest
            or digest_value(snapshot) != observation_digest
        ):
            return False

        observation = _gateway_observation_from_snapshot(snapshot)
        if (
            observation is None
            or not _runtime_observation_matches(
                observation,
                subject=subject,
                profile_digest=record.get("profile_digest"),
                prompt_artifact_digest=prompt_digest,
            )
            or observation.lifecycle != lifecycle
            or observation.workspace_id != workspace_id
            or observation.binding_ref != record.get("binding_ref")
            or observation.agent_id != record.get("agent_id")
            or observation.session_id != record.get("session_id")
            or getattr(
                observation, "planning_output_artifact_digest", None
            )
            != output_digest
        ):
            return False

        if type(observation) is _PreparedRuntimeObservation:
            return (
                lifecycle == "prepared"
                and ever_bound is False
                and workspace_is_present
                and identity_is_absent
                and output_is_absent
            )
        return (
            type(observation) is _BoundRuntimeObservation
            and ever_bound is True
            and workspace_is_present
            and identity_is_complete
            and (
                output_is_digest
                if lifecycle == "completed"
                else (
                    output_is_absent
                    if lifecycle in {"running", "parked"}
                    else lifecycle == "retired"
                    and (output_is_absent or output_is_digest)
                )
            )
        )
    except Exception:
        return False


def _gateway_observation_from_snapshot(
    value: object,
) -> _PreparedRuntimeObservation | _BoundRuntimeObservation | None:
    """Decode one exact canonical observation retained by the Gateway."""

    if type(value) is not dict:
        return None
    try:
        prepared_fields = frozenset(
            field_name
            for field_name, _validator in _RUNTIME_SEALED_SCALAR_SCHEMAS[
                _PreparedRuntimeObservation
            ]
        )
        bound_fields = frozenset(
            field_name
            for field_name, _validator in _RUNTIME_SEALED_SCALAR_SCHEMAS[
                _BoundRuntimeObservation
            ]
        )
        if frozenset(value) == prepared_fields:
            observation: object = _PreparedRuntimeObservation(**value)
        elif frozenset(value) == bound_fields:
            permission_values = value.get("permission_requests")
            if type(permission_values) is not list:
                return None
            permission_requests = tuple(
                _permission_request_from_value(request)
                for request in permission_values
            )
            completed_value = value.get("completed_permission_response")
            completed: _CompletedPermissionResponse | None = None
            if completed_value is not None:
                if (
                    type(completed_value) is not dict
                    or frozenset(completed_value)
                    != _COMPLETED_PERMISSION_FIELDS
                    or type(completed_value.get("provider_receipt")) is not dict
                ):
                    return None
                completed = _CompletedPermissionResponse(
                    request_id=completed_value["request_id"],
                    decision=completed_value["decision"],
                    request=_permission_request_from_value(
                        completed_value["request"]
                    ),
                    request_digest=completed_value["request_digest"],
                    provider_receipt=deepcopy(
                        completed_value["provider_receipt"]
                    ),
                    provider_receipt_digest=completed_value[
                        "provider_receipt_digest"
                    ],
                    stable_action_id=completed_value["stable_action_id"],
                    subject_digest=completed_value["subject_digest"],
                    binding_ref=completed_value["binding_ref"],
                )
            observation = _BoundRuntimeObservation(
                **{
                    key: deepcopy(child)
                    for key, child in value.items()
                    if key
                    not in {
                        "permission_requests",
                        "completed_permission_response",
                    }
                },
                permission_requests=permission_requests,
                completed_permission_response=completed,
            )
        else:
            return None
        return (
            observation
            if _runtime_observation_is_structurally_valid(observation)
            else None
        )
    except Exception:
        return None


_ASSIGNMENT_SOURCES = frozenset(
    {
        "campaign_start.coordinator",
        "campaign_start.ticket",
        "repository",
        "host_global",
    }
)


def _validate_assignment_value(value: object) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) != _ASSIGNMENT_KEYS:
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "persisted Runtime assignment has an unknown schema",
        )
    try:
        selector = RuntimeSelector(value["selector"])
        source = value["configuration_source"]
        if type(source) is not str or source not in _ASSIGNMENT_SOURCES:
            raise ValueError("configuration source is invalid")
        primary = _require_digest(value["profile_digest"], "profile_digest")
        fallback = value["availability_fallback_profile_digest"]
        if fallback is not None:
            fallback = _require_digest(
                fallback, "availability_fallback_profile_digest"
            )
    except (TypeError, ValueError, RuntimeGatewayError) as error:
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "persisted Runtime assignment is invalid",
        ) from error
    return {
        "selector": selector.value,
        "configuration_source": source,
        "profile_digest": primary,
        "availability_fallback_profile_digest": fallback,
    }


def _assignment_record_from_value(value: object) -> dict[str, Any]:
    """Decode the closed, persisted assignment value without recovery defaults."""

    if type(value) is not dict or frozenset(value) != _ASSIGNMENT_RECORD_KEYS:
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "persisted Runtime assignment record has an unknown schema",
        )
    assignment = _validate_assignment_value(
        {key: value[key] for key in _ASSIGNMENT_KEYS}
    )
    if type(value["fallback_selected"]) is not bool:
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "persisted Runtime assignment fallback selection is invalid",
        )
    return {**assignment, "fallback_selected": value["fallback_selected"]}


def _assignment_digest(subject: RuntimeSubject, value: object) -> str:
    """Hash the complete assignment provenance, not merely the selected Profile."""

    assignment = _assignment_record_from_value(value)
    return digest_value(
        {
            "schema_version": "gwo.runtime.assignment.v2",
            "repository": subject.repository,
            "campaign_key": subject.campaign_key,
            "campaign_handle": subject.campaign_handle,
            "subject_digest": subject.digest,
            **assignment,
        }
    )


def _gateway_action_assignment_is_bound(
    record: object,
    subject: RuntimeSubject,
) -> bool:
    if type(record) is not dict or type(record.get("assignment_digest")) is not str:
        return False
    try:
        assignment = _assignment_record_from_value(
            {key: record.get(key) for key in _ASSIGNMENT_RECORD_KEYS}
        )
        return (
            record.get("subject") == subject.canonical()
            and record.get("subject_digest") == subject.digest
            and record["assignment_digest"] == _assignment_digest(subject, assignment)
        )
    except (KeyError, RuntimeGatewayError):
        return False


_PASEO_ACTION_KEYS = frozenset(
    {
        "subject", "subject_digest", "profile", "profile_digest",
        "prompt_artifact_digest", "workspace_id", "workspace_path",
        "workspace_slug", "workspace_base_commit", "workspace_owner_nonce",
        "workspace_layout_version", "workspace_owner_marker_digest", "prompt_file",
        "fenced", "input_artifact_digests", "input_files", "result_file",
        "output_schema_file", "output_schema_digest", "bound_agent_id",
        "binding_established",
        "pending_start", "pending_resume", "pending_park", "pending_stop_command",
        "parked",
        "pending_fence", "pending_fence_claim_id", "pending_fence_quiesced",
        "pending_retire", "pending_permission_response",
        "completed_permission_response", "output_artifact_digest",
        "workspace_observed_head_commit", "wake_state", "wake_state_digest",
        "wake_terminal_emitted",
    }
)


def _validate_paseo_action_record(stable_action_id: object, record: object) -> None:
    """Reject any incomplete or widened Paseo recovery record before readback."""

    if (
        type(stable_action_id) is not str
        or not stable_action_id
        or type(record) is not dict
        or frozenset(record) != _PASEO_ACTION_KEYS
    ):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID", "Paseo Runtime action record has an unknown schema"
        )
    try:
        subject = _subject_from_canonical(record["subject"])
        profile = RuntimeProfile(**record["profile"])
    except (TypeError, RuntimeGatewayError) as error:
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID", "Paseo Runtime action subject or Profile is invalid"
        ) from error
    scalar_digests = (
        "subject_digest", "profile_digest", "prompt_artifact_digest",
        "workspace_owner_marker_digest", "output_schema_digest",
    )
    if (
        subject.stable_action_id != stable_action_id
        or subject.canonical() != record["subject"]
        or subject.digest != record["subject_digest"]
        or profile.canonical() != record["profile"]
        or profile.digest != record["profile_digest"]
        or any(
            type(record[name]) is not str
            or _DIGEST_RE.fullmatch(record[name]) is None
            for name in scalar_digests
        )
        or any(
            type(record[name]) is not str or not record[name]
            for name in (
                "workspace_id", "workspace_path", "workspace_slug",
                "workspace_owner_nonce", "workspace_layout_version", "prompt_file",
                "result_file", "output_schema_file",
            )
        )
        or _GIT_COMMIT_RE.fullmatch(record["workspace_base_commit"]) is None
        or _GIT_COMMIT_RE.fullmatch(record["workspace_observed_head_commit"]) is None
        or record["workspace_layout_version"] != _RUNTIME_WORKSPACE_LAYOUT_VERSION
        or re.fullmatch(r"[0-9a-f]{32}", record["workspace_owner_nonce"]) is None
        or type(record["fenced"]) is not bool
        or type(record["input_artifact_digests"]) is not list
        or any(
            type(digest) is not str or _DIGEST_RE.fullmatch(digest) is None
            for digest in record["input_artifact_digests"]
        )
        or len(set(record["input_artifact_digests"])) != len(record["input_artifact_digests"])
        or type(record["input_files"]) is not dict
        or set(record["input_files"]) != set(record["input_artifact_digests"])
        or any(type(path) is not str or not path for path in record["input_files"].values())
    ):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID", "Paseo Runtime action identity is invalid"
        )
    for name in (
        "binding_established", "pending_start", "pending_resume", "pending_park",
        "pending_fence", "parked", "pending_fence_quiesced", "pending_retire",
        "wake_terminal_emitted",
    ):
        if type(record[name]) is not bool:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo Runtime action recovery state is invalid"
            )
    bound_agent_id = record["bound_agent_id"]
    binding_established = record["binding_established"]
    if (
        bound_agent_id is not None
        and (type(bound_agent_id) is not str or not bound_agent_id)
    ) or (
        bound_agent_id is None and binding_established is True
    ) or (
        bound_agent_id is not None and binding_established is False
    ) or (
        bound_agent_id is not None and record["pending_start"] is True
    ) or (
        record["pending_stop_command"] is not None
        and (
            type(record["pending_stop_command"]) is not str
            or record["pending_stop_command"] not in {"park", "interrupt"}
        )
    ) or (
        record["pending_fence_claim_id"] is not None
        and (
            type(record["pending_fence_claim_id"]) is not str
            or re.fullmatch(r"[0-9a-f]{32}", record["pending_fence_claim_id"])
            is None
        )
    ) or (
        record["output_artifact_digest"] is not None
        and (
            type(record["output_artifact_digest"]) is not str
            or _DIGEST_RE.fullmatch(record["output_artifact_digest"]) is None
        )
    ) or (
        record["wake_state_digest"] is not None
        and (
            type(record["wake_state_digest"]) is not str
            or _DIGEST_RE.fullmatch(record["wake_state_digest"]) is None
        )
    ) or (
        record["wake_terminal_emitted"] is True
        and record["wake_state_digest"] is None
    ):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID", "Paseo Runtime action recovery state is invalid"
        )
    _validate_paseo_wake_state(stable_action_id, record, subject)
    # Once a label readback has established an Agent binding, the durable
    # marker never returns to Prepared.  Without this proof, deleting only
    # bound_agent_id could turn a post-dispatch action into a second START.
    if bound_agent_id is None and (
        record["fenced"] is True
        or record["pending_resume"] is True
        or record["pending_park"] is True
        or record["parked"] is True
        or record["pending_stop_command"] is not None
        or record["pending_fence"] is True
        or record["pending_fence_claim_id"] is not None
        or record["pending_fence_quiesced"] is True
        or record["pending_retire"] is True
        or record["pending_permission_response"] is not None
        or record["completed_permission_response"] is not None
        or record["output_artifact_digest"] is not None
        or record["wake_terminal_emitted"] is True
    ):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "Paseo post-binding recovery state lacks its durable Agent binding",
        )
    pending_fence = record["pending_fence"]
    pending_fence_claim_id = record["pending_fence_claim_id"]
    pending_fence_quiesced = record["pending_fence_quiesced"]
    if (
        pending_fence and not isinstance(pending_fence_claim_id, str)
    ) or (
        not pending_fence
        and (
            pending_fence_claim_id is not None
            or pending_fence_quiesced is True
        )
    ):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "Paseo fence recovery claim is inconsistent",
        )
    # These four fields form one closed stop/resume recovery state machine.
    # `parked` is the last readback-confirmed state; a resume remains pending
    # while that confirmation is still true, whereas a stop has not yet
    # reached its idle confirmation and therefore cannot be parked.
    pending_park = record["pending_park"]
    pending_resume = record["pending_resume"]
    parked = record["parked"]
    pending_stop_command = record["pending_stop_command"]
    if (
        (pending_park and (pending_resume or parked))
        or (pending_park and pending_stop_command not in {"park", "interrupt"})
        or (not pending_park and pending_stop_command is not None)
        or (pending_resume and not parked)
    ):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "Paseo park/resume recovery state has an invalid transition",
        )
    _validate_paseo_pending_permission_response(record["pending_permission_response"])
    completed = record["completed_permission_response"]
    if completed is not None:
        if type(record["bound_agent_id"]) is not str:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo permission completion lacks a bound Agent"
            )
        try:
            _PaseoRuntimeProviderAdapter._completed_permission_response(
                record, subject, record["bound_agent_id"]
            )
        except (TypeError, ValueError, RuntimeGatewayError) as error:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo permission completion is invalid"
            ) from error


_PASEO_WAKE_STATE_KEYS = frozenset(
    {
        "stable_action_id",
        "subject_digest",
        "lifecycle",
        "fenced",
        "permission_requests",
        "output_artifact_digest",
    }
)
_PASEO_PENDING_EFFECT_FIELDS = (
    "pending_start",
    "pending_resume",
    "pending_park",
    "pending_stop_command",
    "pending_fence",
    "pending_fence_claim_id",
    "pending_fence_quiesced",
    "pending_retire",
    "pending_permission_response",
)


def _validate_paseo_wake_state(
    stable_action_id: str,
    record: Mapping[str, Any],
    subject: RuntimeSubject,
) -> None:
    """Bind the durable wake digest to one closed canonical state snapshot."""

    wake_state = record.get("wake_state")
    wake_digest = record.get("wake_state_digest")
    terminal = record.get("wake_terminal_emitted")
    if wake_state is None:
        if wake_digest is not None or terminal is not False:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID",
                "Paseo wake state is incomplete",
            )
        return
    if (
        type(wake_state) is not dict
        or frozenset(wake_state) != _PASEO_WAKE_STATE_KEYS
        or type(wake_digest) is not str
        or _DIGEST_RE.fullmatch(wake_digest) is None
        or digest_value(wake_state) != wake_digest
        or wake_state.get("stable_action_id") != stable_action_id
        or wake_state.get("subject_digest") != subject.digest
        or type(wake_state.get("fenced")) is not bool
        or wake_state.get("lifecycle")
        not in {"prepared", "running", "parked", "completed", "retired"}
        or type(wake_state.get("permission_requests")) is not list
    ):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "Paseo wake state is invalid",
        )
    output_digest = wake_state.get("output_artifact_digest")
    lifecycle = wake_state["lifecycle"]
    if (
        output_digest is not None
        and (
            type(output_digest) is not str
            or _DIGEST_RE.fullmatch(output_digest) is None
        )
    ) or (
        lifecycle in {"prepared", "running", "parked"}
        and output_digest is not None
    ) or (
        lifecycle == "completed"
        and output_digest is None
    ):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "Paseo wake output state is invalid",
        )
    try:
        permission_requests = tuple(
            _permission_request_from_value(value)
            for value in wake_state["permission_requests"]
        )
    except RuntimeGatewayError as error:
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "Paseo wake permission state is invalid",
        ) from error
    request_ids = [request.request_id for request in permission_requests]
    expected_binding = (
        None
        if record.get("bound_agent_id") is None
        else f"paseo:{record['bound_agent_id']}"
    )
    if (
        len(request_ids) != len(set(request_ids))
        or (lifecycle == "prepared" and permission_requests)
        or any(
            request.stable_action_id != stable_action_id
            or request.subject_digest != subject.digest
            or request.authority_subtree_digest != subject.authority_digest
            or request.binding_ref != expected_binding
            for request in permission_requests
        )
    ):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "Paseo wake permission state is not bound",
        )
    unresolved_effect = any(
        record.get(field_name) is not False
        and record.get(field_name) is not None
        for field_name in _PASEO_PENDING_EFFECT_FIELDS
    )
    if terminal is True and (
        lifecycle not in {"completed", "retired"}
        or record.get("bound_agent_id") is None
        or output_digest != record.get("output_artifact_digest")
        or unresolved_effect
    ):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "Paseo terminal wake state has unresolved effects",
        )


def _validate_paseo_pending_permission_response(value: object) -> None:
    if value is None:
        return
    if type(value) is not dict or set(value) != {
        "request_id", "decision", "request", "request_digest", "provider_receipt"
    }:
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID", "Paseo pending permission recovery state is invalid"
        )
    try:
        request = _permission_request_from_value(value["request"])
    except RuntimeGatewayError as error:
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID", "Paseo pending permission request is invalid"
        ) from error
    if (
        type(value["request_id"]) is not str
        or value["request_id"] != request.request_id
        or type(value["decision"]) is not str
        or value["decision"] not in {"allow", "deny"}
        or type(value["request_digest"]) is not str
        or _DIGEST_RE.fullmatch(value["request_digest"]) is None
        or value["request_digest"] != digest_value(asdict(request))
        or (
            value["provider_receipt"] is not None
            and (
                type(value["provider_receipt"]) is not dict
                or set(value["provider_receipt"])
                != {"requestId", "agentId", "agentShortId", "name", "result"}
                or not all(
                    type(part) is str and part
                    for part in value["provider_receipt"].values()
                )
            )
        )
    ):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID", "Paseo pending permission recovery state is invalid"
        )


def _campaign_overrides_from_value(
    value: object,
) -> CampaignStartRuntimeOverrides:
    if (
        type(value) is not dict
        or set(value) != {"coordinator", "ticket_overrides"}
        or type(value["ticket_overrides"]) is not list
    ):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "persisted Campaign overrides have an unknown schema",
        )
    coordinator = (
        None
        if value["coordinator"] is None
        else _mapping_from_value(value["coordinator"])
    )
    ticket_overrides: dict[tuple[str, str], ProfileMapping] = {}
    for item in value["ticket_overrides"]:
        if (
            type(item) is not dict
            or set(item) != {"ticket_key", "role", "mapping"}
            or type(item["ticket_key"]) is not str
            or not item["ticket_key"]
            or type(item["role"]) is not str
        ):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID",
                "persisted Campaign ticket override is malformed",
            )
        try:
            selector = RuntimeSelector.ticket(item["role"])
        except RuntimeGatewayError as error:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID",
                "persisted Campaign ticket override role is invalid",
            ) from error
        key = (item["ticket_key"], selector.value)
        if key in ticket_overrides:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID",
                "persisted Campaign ticket override identity is duplicated",
            )
        ticket_overrides[key] = _mapping_from_value(item["mapping"])
    try:
        overrides = CampaignStartRuntimeOverrides(
            coordinator=coordinator,
            ticket_overrides=ticket_overrides,
        )
    except (TypeError, RuntimeGatewayError) as error:
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "persisted Campaign overrides are invalid",
        ) from error
    if overrides.canonical() != value:
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "persisted Campaign overrides are not in canonical order",
        )
    return overrides


def _preflight_receipt_digest(
    stable_action_id: str,
    value: Mapping[str, Any],
) -> str:
    assignment = _validate_assignment_value(value.get("assignment"))
    try:
        subject = _subject_from_canonical(value.get("subject"))
    except RuntimeGatewayError as error:
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID", "planning preflight subject is invalid"
        ) from error
    if type(subject) is not CampaignPlanningSubject:
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID", "planning preflight subject is not Campaign Planning"
        )
    assignment_digest = _require_digest(
        value.get("assignment_digest"), "assignment_digest"
    )
    if assignment_digest != _assignment_digest(
        subject,
        {**assignment, "fallback_selected": False},
    ):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID", "planning preflight assignment seal is invalid"
        )
    overrides_digest = _require_digest(
        value.get("campaign_overrides_digest"),
        "campaign_overrides_digest",
    )
    subject_digest = _require_digest(
        value.get("subject_digest"), "subject_digest"
    )
    return digest_value(
        {
            "kind": "planning_preflight.v1",
            "subject_digest": subject_digest,
            "stable_action_id": stable_action_id,
            "campaign_overrides_digest": overrides_digest,
            "assignment_digest": assignment_digest,
        }
    )


def _purpose_from_value(value: object) -> WorkRunPurpose:
    if (
        not isinstance(value, dict)
        or set(value) != {"kind", "policy_id"}
    ):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "persisted Work Run purpose is malformed",
        )
    try:
        return WorkRunPurpose(
            kind=value["kind"],
            policy_id=value["policy_id"],
        )
    except (TypeError, RuntimeGatewayError) as error:
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID",
            "persisted Work Run purpose is invalid",
        ) from error


def _subject_from_canonical(value: object) -> RuntimeSubject:
    if not isinstance(value, dict):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID", "persisted Runtime action lacks a subject"
        )
    try:
        if value.get("kind") == "campaign_planning":
            return CampaignPlanningSubject(
                repository=value["repository"],
                campaign_key=value["campaign_key"],
                campaign_handle=value["campaign_handle"],
                expected_previous_plan_revision_digest=value.get(
                    "expected_previous_plan_revision_digest"
                ),
                snapshot_artifact_digest=value["snapshot_artifact_digest"],
                policy_witness_digest=value["policy_witness_digest"],
                planning_request_artifact_digest=value[
                    "planning_request_artifact_digest"
                ],
                stable_action_id=value["stable_action_id"],
            )
        if value.get("kind") == "work_run":
            return WorkRunSubject(
                repository=value["repository"],
                campaign_key=value["campaign_key"],
                campaign_handle=value["campaign_handle"],
                plan_revision_digest=value["plan_revision_digest"],
                work_run_key=value["work_run_key"],
                ticket_key=value["ticket_key"],
                purpose=_purpose_from_value(value["purpose"]),
                prompt_artifact_digest=value["prompt_artifact_digest"],
                authority_subtree_digest=value["authority_subtree_digest"],
                stable_action_id=value["stable_action_id"],
            )
    except (KeyError, TypeError, RuntimeGatewayError) as error:
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID", "persisted Runtime action subject is invalid"
        ) from error
    raise RuntimeGatewayError(
        "RUNTIME_STORE_INVALID", "persisted Runtime action has an unknown subject"
    )


def _runtime_action_spec_identity(spec: _RuntimeActionSpec) -> str:
    return digest_value(
        {
            "stable_action_id": spec.stable_action_id,
            "subject": spec.subject.canonical(),
            "profile": spec.profile.canonical(),
            "prompt_artifact_digest": spec.prompt_artifact.digest,
            "input_artifact_digests": [
                artifact.digest for artifact in spec.input_artifacts
            ],
        }
    )


def _runtime_in_memory_observation_identity(
    spec: _RuntimeActionSpec,
    workspace_id: str,
    binding_ref: str | None,
) -> str:
    return digest_value(
        {
            "spec_identity_digest": _runtime_action_spec_identity(spec),
            "workspace_id": workspace_id,
            "binding_ref": binding_ref,
        }
    )


def _runtime_observation_identity_from_spec(
    spec: _RuntimeActionSpec,
    *,
    workspace_id: str,
    binding_ref: str | None,
    agent_id: str | None,
    session_id: str | None,
) -> _RuntimeObservationIdentity:
    subject = spec.subject
    return _RuntimeObservationIdentity(
        stable_action_id=spec.stable_action_id,
        repository=subject.repository,
        campaign_key=subject.campaign_key,
        campaign_handle=subject.campaign_handle,
        plan_revision_digest=(
            None
            if type(subject) is CampaignPlanningSubject
            else subject.plan_revision_digest
        ),
        work_run_key=(
            None
            if type(subject) is CampaignPlanningSubject
            else subject.work_run_key
        ),
        subject_digest=subject.digest,
        profile_digest=spec.profile.digest,
        workspace_id=workspace_id,
        prompt_artifact_digest=spec.prompt_artifact.digest,
        authority_subtree_digest=subject.authority_digest,
        input_artifact_digests=tuple(
            artifact.digest for artifact in spec.input_artifacts
        ),
        spec_identity_digest=_runtime_action_spec_identity(spec),
        binding_ref=binding_ref,
        agent_id=agent_id,
        session_id=session_id,
    )


@dataclass
class _InMemoryAction:
    spec: _RuntimeActionSpec
    workspace_id: str
    spec_identity_digest: str
    expected_workspace_id: str
    expected_binding_ref: str | None
    observation_identity_digest: str
    binding_ref: str | None = None
    lifecycle: str = "prepared"
    fenced: bool = False
    output_artifact_digest: str | None = None
    pending_permissions: list[tuple[str, str, str]] = field(default_factory=list)
    completed_permission_response: _CompletedPermissionResponse | None = None
    wake_state: dict[str, Any] | None = None
    wake_state_digest: str | None = None
    wake_terminal_emitted: bool = False


def _runtime_in_memory_action_record_digest(
    action: _InMemoryAction,
) -> str:
    return digest_value(
        {
            "spec_identity_digest": action.spec_identity_digest,
            "expected_workspace_id": action.expected_workspace_id,
            "expected_binding_ref": action.expected_binding_ref,
            "observation_identity_digest": action.observation_identity_digest,
            "workspace_id": action.workspace_id,
            "binding_ref": action.binding_ref,
            "lifecycle": action.lifecycle,
            "fenced": action.fenced,
            "output_artifact_digest": action.output_artifact_digest,
            "pending_permissions": [
                list(request) for request in action.pending_permissions
            ],
            "completed_permission_response": (
                None
                if action.completed_permission_response is None
                else _json_projection(
                    asdict(action.completed_permission_response)
                )
            ),
            "wake_state": deepcopy(action.wake_state),
            "wake_state_digest": action.wake_state_digest,
            "wake_terminal_emitted": action.wake_terminal_emitted,
        }
    )


def _runtime_in_memory_selected_record_digest(value: object) -> str:
    if type(value) is _InMemoryAction:
        return _runtime_in_memory_action_record_digest(value)
    return digest_value(_json_projection(vars(value)))


class _InMemoryRuntimeProviderAdapter:
    """Deterministic adapter subject to the same strict Gateway conformance seam."""

    def __init__(
        self,
        artifacts: ArtifactStore,
        *,
        lose_prepare_ack_once: bool = False,
        lose_command_ack_once: RuntimeCommand | None = None,
        pending_permissions: Mapping[str, tuple[tuple[str, str, str], ...]] | None = None,
    ):
        self._artifacts = artifacts
        self._read_lock = threading.RLock()
        self._command_gate = _OneShotObservationGate()
        self._actions: dict[str, _InMemoryAction] = {}
        self._events: list[_RuntimeEvent] = []
        self._next_event_cursor = 1
        self._event_scan_cursor = 0
        self._lose_prepare_ack_once = lose_prepare_ack_once
        self._lose_command_ack_once = lose_command_ack_once
        self._pending_permissions = {
            stable_action_id: list(requests)
            for stable_action_id, requests in (pending_permissions or {}).items()
        }
        self.observe_failure: _RuntimeFailure | None = None
        self.prepare_calls: list[str] = []
        self.observe_calls: list[str] = []
        self.command_calls: list[tuple[str, str]] = []
        self.created_agent_count = 0
        self.staged_prompt_count = 0
        self.last_prompt_byte_lengths: list[int] = []

    def _verify_action_artifacts(self, action: _InMemoryAction) -> None:
        """Read every referenced Artifact on every authoritative observation."""

        self._artifacts.get(action.spec.prompt_artifact.digest)
        for artifact in action.spec.input_artifacts:
            self._artifacts.get(artifact.digest)

    def _complete_action(self, action: _InMemoryAction) -> None:
        """Publish output only when the deterministic action actually completes."""

        if action.lifecycle in {"completed", "retired"}:
            return
        identity = _RuntimeOutputIdentity(
            subject_digest=action.spec.subject_digest,
            stable_action_id=action.spec.stable_action_id,
            authority_digest=action.spec.subject.authority_digest,
        )
        prompt = self._artifacts.read_json(action.spec.prompt_artifact.digest)
        planning_schema = _resolve_runtime_subject_protocol(
            action.spec.subject,
            prompt,
        )
        planning_kind = planning_protocol_kind_from_prompt(prompt)
        payload: dict[str, Any]
        if planning_schema is not None:
            snapshot = self._artifacts.read_json(
                action.spec.subject.snapshot_artifact_digest
            )
            if planning_kind == "invalidation":
                pending = snapshot.get("pending_invalidations") if type(snapshot) is dict else None
                if (
                    type(pending) is not list
                    or not pending
                    or any(
                        type(item) is not dict
                        or type(item.get("evidence_digest")) is not str
                        or not item["evidence_digest"]
                        for item in pending
                    )
                ):
                    raise RuntimeGatewayError(
                        "RUNTIME_ARTIFACT_INVALID",
                        "Campaign invalidation snapshot has no closed pending Evidence",
                    )
                payload = {
                    "evidence_digests": sorted(
                        {item["evidence_digest"] for item in pending}
                    ),
                    "disposition": "reject_invalid_evidence",
                    "reason": "The deterministic Coordinator default rejects unproved invalidation Evidence.",
                    "successor": None,
                    "decision": None,
                }
            else:
                payload = None
            if (
                type(snapshot) is not dict
                or type(snapshot.get("tickets")) is not list
                or any(
                    type(ticket) is not dict
                    or type(ticket.get("key")) is not str
                    or not ticket["key"]
                    for ticket in snapshot["tickets"]
                )
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_ARTIFACT_INVALID",
                    "Campaign Planning snapshot Artifact is malformed",
                )
            ticket_keys = [ticket["key"] for ticket in snapshot["tickets"]]
            if not ticket_keys or len(set(ticket_keys)) != len(ticket_keys):
                raise RuntimeGatewayError(
                    "RUNTIME_ARTIFACT_INVALID",
                    "Campaign Planning snapshot repeats or omits Ticket keys",
                )
            if planning_kind == "initial":
                payload = {
                    "admitted_work": ticket_keys,
                    "dependency_additions": [],
                    "exclusive_resources": {key: [] for key in ticket_keys},
                    "capability_requirements": {key: [] for key in ticket_keys},
                    "decision_requirements": [],
                }
        else:
            payload = {
                "input_artifact_digests": [
                    artifact.digest for artifact in action.spec.input_artifacts
                ]
            }
        action.output_artifact_digest = self._artifacts.put_canonical(
            {
                "schema_version": _RUNTIME_OUTPUT_SCHEMA_VERSION,
                **identity.canonical(),
                "payload": payload,
            }
        ).digest
        action.lifecycle = "completed"

    def prepare(self, spec: _RuntimeActionSpec) -> _PrepareReceipt | _RuntimeFailure:
        # Query, validate, and publish one action under the same lock used by
        # observation and command.  Otherwise two absence reads can both
        # stage the Prompt and the later publisher can overwrite a binding
        # established by the first contender.
        with self._read_lock:
            return self._prepare_locked(spec)

    def _prepare_locked(
        self, spec: _RuntimeActionSpec
    ) -> _PrepareReceipt | _RuntimeFailure:
        try:
            prompt = self._artifacts.read_json(spec.prompt_artifact.digest)
            _resolve_runtime_subject_protocol(spec.subject, prompt)
        except RuntimeGatewayError as error:
            return _RuntimeFailure(error.code, "Runtime subject/prompt protocol is invalid")
        existing = self._actions.get(spec.stable_action_id)
        if existing is not None:
            if (
                existing.spec.subject_digest != spec.subject_digest
                or existing.spec.profile.digest != spec.profile.digest
                or existing.spec.prompt_artifact.digest != spec.prompt_artifact.digest
                or tuple(item.digest for item in existing.spec.input_artifacts)
                != tuple(item.digest for item in spec.input_artifacts)
            ):
                return _RuntimeFailure(
                    "RUNTIME_ACTION_IDENTITY_MISMATCH", "stable action changed during prepare"
                )
            try:
                self._verify_action_artifacts(existing)
            except RuntimeGatewayError as error:
                return _RuntimeFailure(error.code, "staged Runtime Artifact is invalid")
            return _PrepareReceipt(spec.stable_action_id, existing.workspace_id)
        self.prepare_calls.append(spec.stable_action_id)
        if spec.profile.features:
            return _RuntimeFailure(
                "RUNTIME_CONFIGURATION_INVALID",
                "In-memory V3 conformance adapter cannot prove non-empty Runtime Profile features",
            )
        try:
            prompt_bytes = self._artifacts.read_bytes(spec.prompt_artifact.digest)
        except RuntimeGatewayError as error:
            return _RuntimeFailure(error.code, "staged Prompt Artifact is invalid")
        if hashlib.sha256(prompt_bytes).hexdigest() != spec.prompt_artifact.digest:
            return _RuntimeFailure(
                "RUNTIME_ARTIFACT_DIGEST_MISMATCH", "staged Prompt Artifact is invalid"
            )
        try:
            for artifact in spec.input_artifacts:
                self._artifacts.get(artifact.digest)
        except RuntimeGatewayError as error:
            return _RuntimeFailure(error.code, "staged input Artifact is invalid")
        self.last_prompt_byte_lengths.append(len(prompt_bytes))
        suffix = digest_value({"stable_action_id": spec.stable_action_id})[:24]
        action = _InMemoryAction(
            spec=spec,
            workspace_id=f"workspace:{suffix}",
            spec_identity_digest=_runtime_action_spec_identity(spec),
            expected_workspace_id=f"workspace:{suffix}",
            expected_binding_ref=None,
            observation_identity_digest=_runtime_in_memory_observation_identity(
                spec,
                f"workspace:{suffix}",
                None,
            ),
            lifecycle="prepared",
            pending_permissions=list(self._pending_permissions.get(spec.stable_action_id, ())),
        )
        self._actions[spec.stable_action_id] = action
        self.staged_prompt_count += 1
        if self._lose_prepare_ack_once:
            self._lose_prepare_ack_once = False
            return _RuntimeFailure(
                "RUNTIME_PREPARE_ACK_LOST",
                "Provider prepare acknowledgement was lost",
                stable_action_id=spec.stable_action_id,
            )
        return _PrepareReceipt(spec.stable_action_id, action.workspace_id)

    @staticmethod
    def _observation(
        action: _InMemoryAction,
    ) -> _PreparedRuntimeObservation | _BoundRuntimeObservation:
        subject = action.spec.subject
        expected_plan = (
            None
            if type(subject) is CampaignPlanningSubject
            else subject.plan_revision_digest
        )
        expected_work = (
            None if type(subject) is CampaignPlanningSubject else subject.work_run_key
        )
        if action.binding_ref is None:
            if (
                action.lifecycle != "prepared"
                or action.output_artifact_digest is not None
                or action.completed_permission_response is not None
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_OBSERVATION_INVALID",
                    "Prepared in-memory action contains Bound state",
                )
            observation: _PreparedRuntimeObservation | _BoundRuntimeObservation = (
                _PreparedRuntimeObservation(
                    stable_action_id=action.spec.stable_action_id,
                    repository=subject.repository,
                    campaign_key=subject.campaign_key,
                    campaign_handle=subject.campaign_handle,
                    plan_revision_digest=expected_plan,
                    work_run_key=expected_work,
                    subject_digest=subject.digest,
                    profile_digest=action.spec.profile.digest,
                    workspace_id=action.workspace_id,
                    prompt_artifact_digest=action.spec.prompt_artifact.digest,
                    fenced=action.fenced,
                    authority_subtree_digest=subject.authority_digest,
                )
            )
        else:
            observation = _BoundRuntimeObservation(
                stable_action_id=action.spec.stable_action_id,
                binding_ref=action.binding_ref,
                repository=subject.repository,
                campaign_key=subject.campaign_key,
                campaign_handle=subject.campaign_handle,
                plan_revision_digest=expected_plan,
                work_run_key=expected_work,
                subject_digest=subject.digest,
                profile_digest=action.spec.profile.digest,
                agent_id=f"agent:{action.binding_ref}",
                session_id=f"session:{action.binding_ref}",
                workspace_id=action.workspace_id,
                prompt_artifact_digest=action.spec.prompt_artifact.digest,
                prompt_accepted=True,
                lifecycle=action.lifecycle,
                permission_requests=tuple(
                    sorted(
                        (
                            _PermissionRequest(
                                request_id=request_id,
                                operation_id=operation_id,
                                resource_id=resource_id,
                                binding_ref=action.binding_ref,
                                authority_subtree_digest=subject.authority_digest,
                                stable_action_id=action.spec.stable_action_id,
                                subject_digest=subject.digest,
                            )
                            for request_id, operation_id, resource_id in (
                                action.pending_permissions
                            )
                        ),
                        key=lambda request: (
                            request.request_id,
                            request.operation_id,
                            request.resource_id,
                        ),
                    )
                ),
                fenced=action.fenced,
                authority_subtree_digest=subject.authority_digest,
                planning_output_artifact_digest=action.output_artifact_digest,
                completed_permission_response=action.completed_permission_response,
            )
        if not _runtime_observation_matches(
            observation,
            subject=subject,
            profile_digest=action.spec.profile.digest,
            prompt_artifact_digest=action.spec.prompt_artifact.digest,
        ):
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "in-memory observation is outside the complete Runtime contract",
            )
        return observation

    @staticmethod
    def _identity(action: _InMemoryAction) -> _RuntimeObservationIdentity:
        binding_ref = action.binding_ref
        return _runtime_observation_identity_from_spec(
            action.spec,
            workspace_id=action.expected_workspace_id,
            binding_ref=binding_ref,
            agent_id=(
                None if binding_ref is None else f"agent:{binding_ref}"
            ),
            session_id=(
                None if binding_ref is None else f"session:{binding_ref}"
            ),
        )

    def _reconcile_observation(
        self, stable_action_id: str
    ) -> _RuntimeObservationRead:
        with self._read_lock:
            self.observe_calls.append(stable_action_id)
            action = self._actions.get(stable_action_id)
            if action is None and self.observe_failure is not None:
                return _runtime_sealed_failure_read(
                    stable_action_id,
                    self.observe_failure,
                )
            if action is None:
                return _runtime_sealed_failure_read(
                    stable_action_id,
                    _RuntimeFailure.absent(stable_action_id),
                )
            identity: _RuntimeObservationIdentity | None = None
            record_digest: str | None = None
            try:
                if (
                    _runtime_action_spec_identity(action.spec)
                    != action.spec_identity_digest
                    or action.spec.stable_action_id != stable_action_id
                    or action.workspace_id != action.expected_workspace_id
                    or action.binding_ref != action.expected_binding_ref
                    or _runtime_in_memory_observation_identity(
                        action.spec,
                        action.expected_workspace_id,
                        action.expected_binding_ref,
                    )
                    != action.observation_identity_digest
                ):
                    raise RuntimeGatewayError(
                        "RUNTIME_OBSERVATION_INVALID",
                        "in-memory action identity changed after preparation",
                    )
                identity = self._identity(action)
                record_digest = _runtime_in_memory_action_record_digest(action)
                if self.observe_failure is not None:
                    return _runtime_sealed_failure_read(
                        stable_action_id,
                        self.observe_failure,
                        identity=identity,
                        selected_record_digest=record_digest,
                    )
                observation = self._observation(action)
                read = _runtime_sealed_observation_read(
                    artifacts=self._artifacts,
                    selected_stable_action_id=stable_action_id,
                    identity=identity,
                    selected_record_digest=record_digest,
                    observation=observation,
                )
                verdict = _ObservationProtocol.validate(
                    read,
                    selected_stable_action_id=stable_action_id,
                    maximum_artifact_bytes=self._artifacts.maximum_bytes,
                )
                if verdict.kind not in {"prepared", "bound"}:
                    raise RuntimeGatewayError(
                        "RUNTIME_OBSERVATION_INVALID",
                        "in-memory observation is outside the complete Runtime contract",
                    )
                if type(observation) is _PreparedRuntimeObservation:
                    return read
                if (
                    action.lifecycle == "running"
                    and not action.pending_permissions
                    and action.output_artifact_digest is None
                ):
                    try:
                        self._complete_action(action)
                    except RuntimeGatewayError:
                        # The already validated Bound/running read is
                        # authoritative. Output publication remains a
                        # retryable local effect on this same binding.
                        return read
                identity = self._identity(action)
                record_digest = _runtime_in_memory_action_record_digest(action)
                completed = self._observation(action)
                read = _runtime_sealed_observation_read(
                    artifacts=self._artifacts,
                    selected_stable_action_id=stable_action_id,
                    identity=identity,
                    selected_record_digest=record_digest,
                    observation=completed,
                )
                verdict = _ObservationProtocol.validate(
                    read,
                    selected_stable_action_id=stable_action_id,
                    maximum_artifact_bytes=self._artifacts.maximum_bytes,
                )
                if verdict.kind != "bound":
                    raise RuntimeGatewayError(
                        "RUNTIME_OBSERVATION_INVALID",
                        "in-memory observation is outside the complete Runtime contract",
                    )
                return read
            except RuntimeGatewayError as error:
                return _runtime_sealed_failure_read(
                    stable_action_id,
                    _RuntimeFailure(
                        error.code,
                        "in-memory Runtime readback is invalid",
                    ),
                    identity=identity,
                    selected_record_digest=record_digest,
                )
            except (
                AttributeError,
                CanonicalJsonError,
                TypeError,
                ValueError,
            ):
                return _runtime_sealed_failure_read(
                    stable_action_id,
                    _RuntimeFailure(
                        "RUNTIME_OBSERVATION_INVALID",
                        "in-memory observation is outside the complete Runtime contract",
                    ),
                    identity=identity,
                    selected_record_digest=record_digest,
                )

    def observe(
        self, stable_action_id: str
    ) -> _PreparedRuntimeObservation | _BoundRuntimeObservation | _RuntimeFailure:
        read = self._reconcile_observation(stable_action_id)
        verdict = _ObservationProtocol.validate(
            read,
            selected_stable_action_id=stable_action_id,
            maximum_artifact_bytes=self._artifacts.maximum_bytes,
        )
        self._command_gate.replace(
            stable_action_id,
            verdict.token
            if (
                verdict.kind in {"prepared", "bound"}
                and type(verdict.token) is _RuntimeObservationReadToken
            )
            else None,
        )
        if verdict.kind in {"prepared", "bound"}:
            assert verdict.observation is not None
            return verdict.observation
        assert verdict.failure is not None
        return verdict.failure

    def command(
        self,
        stable_action_id: str,
        transition: RuntimeTransition,
    ) -> _CommandReceipt | _RuntimeFailure:
        command = transition
        with self._read_lock:
            if not _runtime_transition_is_structurally_valid(command):
                return _RuntimeFailure(
                    "RUNTIME_COMMAND_INVALID",
                    "Runtime command is outside the closed union",
                )
            precondition = self._command_gate.consume(stable_action_id)
            if precondition is None:
                return _RuntimeFailure(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Runtime command requires one fresh observation gate",
                    stable_action_id=stable_action_id,
                )
            if not _runtime_observation_read_token_is_structurally_valid(
                precondition
            ):
                return _RuntimeFailure(
                    "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                    "Runtime command observation gate is structurally invalid",
                    stable_action_id=stable_action_id,
                )
            verdict = _ObservationProtocol.validate(
                self._reconcile_observation(stable_action_id),
                selected_stable_action_id=stable_action_id,
                maximum_artifact_bytes=self._artifacts.maximum_bytes,
            )
            if (
                verdict.kind
                in _RUNTIME_OBSERVATION_FAILURE_VERDICT_KINDS
            ):
                assert verdict.failure is not None
                return verdict.failure
            if verdict.token != precondition:
                return _RuntimeFailure(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Runtime command read precondition is stale",
                    stable_action_id=stable_action_id,
                )
            if verdict.kind not in {"prepared", "bound"}:
                return _RuntimeFailure(
                    "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                    "Runtime command readback verdict is outside the closed union",
                )
            return self._command_locked(
                stable_action_id,
                command,
                observation_verdict=verdict,
            )

    def _command_readback_verdict(
        self,
        stable_action_id: str,
    ) -> _RuntimeObservationVerdict:
        return _ObservationProtocol.validate(
            self._reconcile_observation(stable_action_id),
            selected_stable_action_id=stable_action_id,
            maximum_artifact_bytes=self._artifacts.maximum_bytes,
        )

    def _command_locked(
        self,
        stable_action_id: str,
        command: RuntimeTransition,
        *,
        observation_verdict: _RuntimeObservationVerdict,
    ) -> _CommandReceipt | _RuntimeFailure:
        if not _runtime_transition_is_structurally_valid(command):
            return _RuntimeFailure(
                "RUNTIME_COMMAND_INVALID", "Runtime command is outside the closed union"
            )
        action = self._actions.get(stable_action_id)
        if action is None:
            return _RuntimeFailure("RUNTIME_BINDING_UNKNOWN", "Runtime binding is unknown")
        if (
            command is RuntimeCommand.START
            and observation_verdict.kind != "prepared"
        ):
            return _RuntimeFailure(
                "RUNTIME_COMMAND_INVALID",
                "start requires a Prepared Runtime action",
            )
        if (
            command is not RuntimeCommand.START
            and observation_verdict.kind != "bound"
        ):
            return _RuntimeFailure(
                "RUNTIME_COMMAND_INVALID",
                "only start is allowed before Runtime binding exists",
            )
        self.command_calls.append((stable_action_id, _transition_name(command)))
        if command is not RuntimeCommand.START and action.binding_ref is None:
            return _RuntimeFailure(
                "RUNTIME_COMMAND_INVALID", "only start is allowed before Runtime binding exists"
            )
        if (
            command in {RuntimeCommand.PARK, RuntimeCommand.INTERRUPT}
            and action.lifecycle == "parked"
        ):
            return _CommandReceipt(action.spec.stable_action_id, command)
        if command is RuntimeCommand.FENCE and action.fenced is True:
            return _CommandReceipt(action.spec.stable_action_id, command)
        if command is RuntimeCommand.RETIRE and action.lifecycle == "retired":
            return _CommandReceipt(action.spec.stable_action_id, command)
        if type(command) is PermissionResponse:
            if action.lifecycle in {"completed", "retired"}:
                readback_verdict = self._command_readback_verdict(
                    stable_action_id
                )
                if (
                    readback_verdict.kind
                    in _RUNTIME_OBSERVATION_FAILURE_VERDICT_KINDS
                ):
                    assert readback_verdict.failure is not None
                    return readback_verdict.failure
                if (
                    readback_verdict.kind == "bound"
                    and readback_verdict.observation is not None
                    and _completed_permission_effect_matches(
                        command,
                        readback_verdict.observation,
                    )
                ):
                    return _CommandReceipt(action.spec.stable_action_id, command)
                return _RuntimeFailure(
                    "RUNTIME_PERMISSION_REQUEST_UNKNOWN",
                    "terminal Runtime bindings reject new permission responses",
                )
            matching = [
                request
                for request in action.pending_permissions
                if request[0] == command.request_id
            ]
            if len(matching) != 1:
                readback_verdict = self._command_readback_verdict(
                    stable_action_id
                )
                if (
                    readback_verdict.kind
                    in _RUNTIME_OBSERVATION_FAILURE_VERDICT_KINDS
                ):
                    assert readback_verdict.failure is not None
                    return readback_verdict.failure
                if (
                    readback_verdict.kind == "bound"
                    and readback_verdict.observation is not None
                    and _completed_permission_effect_matches(
                        command,
                        readback_verdict.observation,
                    )
                ):
                    return _CommandReceipt(action.spec.stable_action_id, command)
                return _RuntimeFailure(
                    "RUNTIME_PERMISSION_REQUEST_UNKNOWN",
                    "permission response does not bind one exact pending request",
                )
            request_id, operation_id, resource_id = matching[0]
            action.pending_permissions.remove(matching[0])
            completed_request = _PermissionRequest(
                request_id=request_id,
                operation_id=operation_id,
                resource_id=resource_id,
                binding_ref=action.binding_ref,
                authority_subtree_digest=action.spec.subject.authority_digest,
                stable_action_id=stable_action_id,
                subject_digest=action.spec.subject.digest,
            )
            provider_receipt = {
                "adapter": "in-memory.v1",
                "request_id": request_id,
                "decision": command.decision,
                "binding_ref": action.binding_ref,
            }
            action.completed_permission_response = _CompletedPermissionResponse(
                request_id=request_id,
                decision=command.decision,
                request=completed_request,
                request_digest=digest_value(asdict(completed_request)),
                provider_receipt=provider_receipt,
                provider_receipt_digest=digest_value(provider_receipt),
                stable_action_id=stable_action_id,
                subject_digest=action.spec.subject.digest,
                binding_ref=action.binding_ref,
            )
            if not action.pending_permissions:
                try:
                    self._complete_action(action)
                except RuntimeGatewayError as error:
                    return _RuntimeFailure(
                        error.code,
                        "Runtime output Artifact could not be made durable",
                    )
        if command is RuntimeCommand.START:
            if action.lifecycle != "prepared":
                return _RuntimeFailure(
                    "RUNTIME_COMMAND_INVALID", "start requires a prepared binding"
                )
            if action.fenced is not False:
                return _RuntimeFailure(
                    "RUNTIME_COMMAND_INVALID", "start requires an unfenced Prepared action"
                )
            binding_ref = (
                f"binding:{digest_value({'stable_action_id': stable_action_id})[:24]}"
            )
            action.binding_ref = binding_ref
            action.expected_binding_ref = binding_ref
            action.observation_identity_digest = (
                _runtime_in_memory_observation_identity(
                    action.spec,
                    action.expected_workspace_id,
                    action.expected_binding_ref,
                )
            )
            self.created_agent_count += 1
            # A provider may be otherwise idle while an exact pending
            # permission keeps the semantic action active.  Match the
            # production normalization and expose a Bound ``running`` state
            # until the pending request is resolved.
            if action.pending_permissions:
                action.lifecycle = "running"
                action.output_artifact_digest = None
            else:
                action.lifecycle = "running"
                try:
                    self._complete_action(action)
                except RuntimeGatewayError as error:
                    return _RuntimeFailure(
                        error.code,
                        "Runtime output Artifact could not be made durable",
                    )
        elif command is RuntimeCommand.RESUME:
            if action.lifecycle != "parked" or action.fenced is not False:
                return _RuntimeFailure(
                    "RUNTIME_COMMAND_INVALID", "resume requires an unfenced parked binding"
                )
            action.lifecycle = "running"
        elif command is RuntimeCommand.PARK:
            if action.lifecycle in {"completed", "retired"}:
                return _RuntimeFailure(
                    "RUNTIME_COMMAND_INVALID",
                    "park requires an active Runtime binding",
                )
            action.lifecycle = "parked"
        elif command is RuntimeCommand.INTERRUPT:
            if action.lifecycle in {"completed", "retired"}:
                return _RuntimeFailure(
                    "RUNTIME_COMMAND_INVALID",
                    "interrupt requires an active Runtime binding",
                )
            action.lifecycle = "parked"
        elif command is RuntimeCommand.FENCE:
            action.fenced = True
            action.wake_terminal_emitted = False
        elif command is RuntimeCommand.RETIRE:
            action.lifecycle = "retired"
            action.wake_terminal_emitted = False
        if self._lose_command_ack_once is command:
            self._lose_command_ack_once = None
            return _RuntimeFailure(
                "RUNTIME_COMMAND_ACK_LOST",
                "Provider command acknowledgement was lost",
                stable_action_id=stable_action_id,
            )
        return _CommandReceipt(action.spec.stable_action_id, command)

    def events(self, after_cursor: str | None) -> _RuntimeEventPage | _RuntimeFailure:
        self._command_gate.clear()
        cursor = _runtime_event_cursor_value(after_cursor)
        if cursor is None:
            return _RuntimeFailure("RUNTIME_EVENT_CURSOR_INVALID", "event cursor is invalid")
        with self._read_lock:
            if cursor > self._next_event_cursor - 1:
                return _RuntimeFailure(
                    "RUNTIME_EVENT_CURSOR_INVALID",
                    "event cursor is ahead of the Runtime event journal",
                )
            eligible = sorted(
                stable_action_id
                for stable_action_id, action in self._actions.items()
                if action.wake_terminal_emitted is not True
            )
            selected_scan_cursor = self._event_scan_cursor
            selected_eligible_digest = digest_value(eligible)
            stable_action_id = (
                None
                if not eligible
                else eligible[selected_scan_cursor % len(eligible)]
            )
        if stable_action_id is not None:
            try:
                read = self._reconcile_observation(stable_action_id)
            except Exception:
                return _RuntimeFailure(
                    "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                    "Runtime provider readback raised outside its envelope",
                )
            verdict = _ObservationProtocol.validate(
                read,
                selected_stable_action_id=stable_action_id,
                maximum_artifact_bytes=self._artifacts.maximum_bytes,
            )
            if verdict.kind == "invalid":
                assert verdict.failure is not None
                return verdict.failure
            derived = (
                None
                if verdict.kind not in {"prepared", "bound"}
                else _runtime_event_observation_state(
                    verdict.observation,
                    stable_action_id,
                )
            )
            if derived is not None or verdict.kind in {
                "authoritative_absence",
                "fairness_advance",
            }:
                token = verdict.token
                if not (
                    _runtime_observation_read_token_is_structurally_valid(
                        token
                    )
                ):
                    return _RuntimeFailure(
                        "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                        "Runtime event readback omitted its causal token",
                    )
                with self._read_lock:
                    current_eligible = sorted(
                        action_id
                        for action_id, action in self._actions.items()
                        if action.wake_terminal_emitted is not True
                    )
                    action = self._actions.get(stable_action_id)
                    if (
                        not current_eligible
                        or self._event_scan_cursor != selected_scan_cursor
                        or digest_value(current_eligible)
                        != selected_eligible_digest
                        or current_eligible[
                            self._event_scan_cursor % len(current_eligible)
                        ]
                        != stable_action_id
                        or action is None
                        or _runtime_in_memory_selected_record_digest(action)
                        != token.selected_record_digest
                    ):
                        action = None
                    state: dict[str, Any] | None = None
                    lifecycle: str | None = None
                    state_digest: str | None = None
                    publish_event = False
                    if action is not None and derived is not None:
                        state, lifecycle = derived
                        state_digest = digest_value(state)
                        publish_event = (
                            action.wake_state_digest != state_digest
                        )
                    if (
                        action is not None
                        and publish_event
                        and self._next_event_cursor
                        > _MAXIMUM_RUNTIME_EVENT_CURSOR
                    ):
                        return _RuntimeFailure(
                            "RUNTIME_EVENT_CURSOR_EXHAUSTED",
                            "Runtime event cursor space is exhausted",
                        )
                    if action is not None:
                        self._event_scan_cursor = (
                            0
                            if self._event_scan_cursor
                            == _MAXIMUM_RUNTIME_EVENT_CURSOR
                            else self._event_scan_cursor
                            + _MAXIMUM_RUNTIME_EVENT_READBACKS
                        )
                    if (
                        action is not None
                        and derived is not None
                        and state_digest is not None
                        and lifecycle is not None
                    ):
                        if publish_event:
                            action.wake_state = deepcopy(state)
                            action.wake_state_digest = state_digest
                            self._events.append(
                                _RuntimeEvent(
                                    str(self._next_event_cursor),
                                    stable_action_id,
                                    f"state:{lifecycle}",
                                )
                            )
                            self._next_event_cursor += 1
                            del self._events[:-_MAXIMUM_RUNTIME_EVENTS]
                        if lifecycle in {"completed", "retired"}:
                            action.wake_terminal_emitted = True
        with self._read_lock:
            available = [
                event
                for event in self._events
                if (
                    _runtime_event_cursor_value(event.cursor) is not None
                    and _runtime_event_cursor_value(event.cursor) > cursor
                )
            ]
            events = tuple(available[:_MAXIMUM_RUNTIME_EVENT_PAGE])
        return _RuntimeEventPage(
            events=events,
            next_cursor=(
                events[-1].cursor if events else after_cursor
            ),
        )
