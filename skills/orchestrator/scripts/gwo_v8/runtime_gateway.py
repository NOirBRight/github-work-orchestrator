"""The single V8 boundary for semantic Runtime materialization.

The gateway deliberately gives its callers no provider command, session, or
binding choreography.  A caller supplies one closed semantic subject and an
Artifact reference; the gateway reads back an existing action before staging
or starting it.  Provider adapters are private implementation details.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from copy import deepcopy
from enum import Enum
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from ._canonical import canonical_bytes, digest_value
from .runtime import RuntimeProfile


_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_GIT_COMMIT_RE = re.compile(r"[0-9a-f]{40,64}")
_SPECIALIST_RE = re.compile(r"specialist:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_TICKET_ROLES = {
    "worker",
    "recovery_worker",
    "review_primary",
    "review_strong",
}
_LIFECYCLES = {"prepared", "running", "parked", "completed", "retired"}
_PASEO_BATCH_META = frozenset("&|<>^%!\"()")
_MAXIMUM_PASEO_COMMAND_CHARS = 7_500
_MAXIMUM_PASEO_PERMISSION_TEXT = 4_096
_MAXIMUM_PASEO_ERROR_JSON_BYTES = 4_096
_MAXIMUM_PASEO_STREAM_BYTES = 1_048_576
_MAXIMUM_PASEO_TOTAL_BYTES = 1_572_864
_PASEO_PIPE_CHUNK_BYTES = 65_536
_PASEO_PIPE_POLL_SECONDS = 0.005
_PASEO_POST_EXIT_DRAIN_SECONDS = 0.25
_PASEO_CLEANUP_GRACE_SECONDS = 0.5
_JOURNAL_LOCK_TIMEOUT_SECONDS = 5.0
_JOURNAL_LOCK_RETRY_SECONDS = 0.01
_MAXIMUM_RUNTIME_JOURNAL_BYTES = 16_777_216
_MAXIMUM_RUNTIME_EVENTS = 64
_MAXIMUM_RUNTIME_EVENT_PAGE = 16
_JOURNAL_MUTEX_GUARD = threading.Lock()
_JOURNAL_MUTEXES: dict[str, threading.Lock] = {}


class RuntimeGatewayError(RuntimeError):
    """A typed Gateway-owned configuration, identity, or transport failure."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


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
            return json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Runtime journal is unreadable"
            ) from error

    def replace_unlocked(self, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{uuid4().hex}.tmp")
        try:
            payload = canonical_bytes(value)
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


@dataclass(frozen=True)
class ArtifactRef:
    """Digest-addressed bounded bytes; Providers receive a ref/path, never text."""

    digest: str
    byte_length: int
    path: str


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
        self._root.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self.get(digest)
            return ArtifactRef(digest, len(payload), str(target))
        temporary = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, target)
        return ArtifactRef(digest, len(payload), str(target))

    def put_canonical(self, value: Any) -> ArtifactRef:
        return self.put(canonical_bytes(value))

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

    @staticmethod
    def _canonical_json(payload: bytes) -> Any:
        try:
            value = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_INVALID", "Artifact is not canonical JSON"
            ) from error
        if canonical_bytes(value) != payload:
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_INVALID", "Artifact JSON is not canonical"
            )
        return value

    def path_for(self, digest: str) -> Path:
        _require_digest(digest, "artifact digest")
        return self._root / digest

    @property
    def maximum_bytes(self) -> int:
        return self._maximum_bytes


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
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

    if not isinstance(value, str) or value != value.strip():
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


def _require_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise RuntimeGatewayError(
            "RUNTIME_SUBJECT_INVALID", f"{field_name} must be a SHA-256 digest"
        )
    return value


@dataclass(frozen=True, order=True)
class RuntimeSelector:
    """An exact Runtime assignment key; no generic role strings are accepted."""

    value: str

    def __post_init__(self) -> None:
        if self.value == "coordinator" or self.value in _TICKET_ROLES:
            return
        if _SPECIALIST_RE.fullmatch(self.value) is not None:
            return
        raise RuntimeGatewayError(
            "RUNTIME_SELECTOR_INVALID", f"unknown Runtime selector: {self.value}"
        )

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


@dataclass(frozen=True)
class ProfileMapping:
    """One required primary Profile and one optional availability fallback."""

    primary_profile_digest: str
    availability_fallback_profile_digest: str | None = None

    def __post_init__(self) -> None:
        _require_digest(self.primary_profile_digest, "primary_profile_digest")
        if self.availability_fallback_profile_digest is not None:
            _require_digest(
                self.availability_fallback_profile_digest,
                "availability_fallback_profile_digest",
            )


@dataclass(frozen=True)
class CampaignStartRuntimeOverrides:
    """Persisted Campaign-start assignments, never a PlanSpec field."""

    coordinator: ProfileMapping | None = None
    ticket_overrides: Mapping[tuple[str, str], ProfileMapping] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        if self.coordinator is not None and not isinstance(
            self.coordinator, ProfileMapping
        ):
            raise RuntimeGatewayError(
                "RUNTIME_OVERRIDE_INVALID", "Coordinator override must be a ProfileMapping"
            )
        normalized: dict[tuple[str, str], ProfileMapping] = {}
        for key, mapping in self.ticket_overrides.items():
            if not isinstance(key, tuple) or len(key) != 2:
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
            if not isinstance(mapping, ProfileMapping):
                raise RuntimeGatewayError(
                    "RUNTIME_OVERRIDE_INVALID", "Ticket override must be a ProfileMapping"
                )
            normalized[(ticket_key, selector.value)] = mapping
        object.__setattr__(self, "ticket_overrides", normalized)

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

    def __post_init__(self) -> None:
        profiles = dict(self.profiles)
        for digest, profile in profiles.items():
            _require_digest(digest, "profile digest")
            if not isinstance(profile, RuntimeProfile) or profile.digest != digest:
                raise RuntimeGatewayError(
                    "RUNTIME_CONFIGURATION_INVALID",
                    "Profile registry key must equal the immutable Profile digest",
                )
            # V3 does not attach provider semantics to these values here.  It
            # does require that an immutable Profile is a complete, usable
            # configuration before any campaign can be claimed or materialized.
            for field_name in ("name", "provider", "model", "thinking", "mode"):
                value = getattr(profile, field_name)
                if not isinstance(value, str) or not value.strip():
                    raise RuntimeGatewayError(
                        "RUNTIME_CONFIGURATION_INVALID",
                        f"Runtime Profile {field_name} must be a non-empty string",
                    )
            if not isinstance(profile.features, dict):
                raise RuntimeGatewayError(
                    "RUNTIME_CONFIGURATION_INVALID",
                    "Runtime Profile features must be an object",
                )
        object.__setattr__(self, "profiles", profiles)
        object.__setattr__(self, "host_mappings", _normalize_mappings(self.host_mappings))
        repositories: dict[str, dict[RuntimeSelector, ProfileMapping]] = {}
        for repository, mappings in self.repository_mappings.items():
            repositories[_require_text(repository, "repository")] = _normalize_mappings(
                mappings
            )
        object.__setattr__(self, "repository_mappings", repositories)


def _normalize_mappings(
    value: Mapping[RuntimeSelector | str, ProfileMapping],
) -> dict[RuntimeSelector, ProfileMapping]:
    normalized: dict[RuntimeSelector, ProfileMapping] = {}
    for raw_selector, mapping in value.items():
        selector = _selector(raw_selector)
        if not isinstance(mapping, ProfileMapping):
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID", "Runtime mapping must be a ProfileMapping"
            )
        normalized[selector] = mapping
    return normalized


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


@dataclass(frozen=True)
class WorkRunSubject:
    """The only post-Plan subject accepted by RuntimeGateway."""

    repository: str
    campaign_key: str
    campaign_handle: str
    plan_revision_digest: str
    work_run_key: str
    ticket_key: str
    role: str
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
        RuntimeSelector.ticket(self.role)
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
            "role": self.role,
            "prompt_artifact_digest": self.prompt_artifact_digest,
            "authority_subtree_digest": self.authority_subtree_digest,
            "stable_action_id": self.stable_action_id,
        }


RuntimeSubject = CampaignPlanningSubject | WorkRunSubject


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
        if self.decision not in {"allow", "deny"}:
            raise RuntimeGatewayError(
                "RUNTIME_COMMAND_INVALID",
                "permission decision must be exactly allow or deny",
            )


RuntimeTransition = RuntimeCommand | PermissionResponse


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


@dataclass(frozen=True)
class _CompletedPermissionResponse:
    """One bounded provider-neutral proof of a completed permission effect."""

    request_id: str
    decision: str
    request_digest: str
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


def _completed_permission_effect_matches(
    command: PermissionResponse,
    observation: _BoundRuntimeObservation,
) -> bool:
    """Check the one retained completion proof and exact request absence."""

    evidence = observation.completed_permission_response
    return (
        type(evidence) is _CompletedPermissionResponse
        and evidence.request_id == command.request_id
        and evidence.decision == command.decision
        and _DIGEST_RE.fullmatch(evidence.request_digest) is not None
        and _DIGEST_RE.fullmatch(evidence.provider_receipt_digest) is not None
        and evidence.stable_action_id == observation.stable_action_id
        and evidence.subject_digest == observation.subject_digest
        and evidence.binding_ref == observation.binding_ref
        and command.request_id
        not in {request.request_id for request in observation.permission_requests}
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


class _RuntimeProviderAdapter(Protocol):
    """The exact private seam shared by production and deterministic adapters."""

    def prepare(self, spec: _RuntimeActionSpec) -> _PrepareReceipt | _RuntimeFailure: ...

    def observe(
        self, stable_action_id: str
    ) -> _PreparedRuntimeObservation | _BoundRuntimeObservation | _RuntimeFailure: ...

    def command(
        self, stable_action_id: str, command: RuntimeTransition
    ) -> _CommandReceipt | _RuntimeFailure: ...

    def events(self, after_cursor: str | None) -> _RuntimeEventPage | _RuntimeFailure: ...


@dataclass(frozen=True)
class RuntimeRepositoryContext:
    """Host-owned source checkout used to create action-owned Workspaces."""

    path: Path
    base_ref: str

    def __post_init__(self) -> None:
        resolved_path = Path(self.path).resolve()
        if not resolved_path.is_dir():
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID", "Paseo repository context path is unavailable"
            )
        _require_text(self.base_ref, "Runtime repository context base_ref")
        object.__setattr__(self, "path", resolved_path)


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

    def _run(self, args: list[str]) -> Any:
        self.validate_arguments(args, executable=self._executable)
        started = time.monotonic()
        command_deadline = started + self._timeout_seconds
        hard_deadline = command_deadline + _PASEO_CLEANUP_GRACE_SECONDS
        process = subprocess.Popen(
            [self._executable, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
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
                os.set_blocking(stream.fileno(), False)
            while True:
                made_progress = False
                for name in tuple(open_streams):
                    stream = streams[name]
                    try:
                        chunk = os.read(stream.fileno(), _PASEO_PIPE_CHUNK_BYTES)
                    except BlockingIOError:
                        continue
                    except OSError:
                        stop_reason = "read_failed"
                        break
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
        if not stdout.strip():
            return {}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as error:
            raise ValueError("Paseo JSON response is invalid") from error

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
            if not isinstance(payload, str) or not payload.strip():
                continue
            # Error text is untrusted provider output.  Do not give JSON a
            # potentially unbounded document merely because the process
            # already exited non-zero.
            if len(payload.encode("utf-8")) > _MAXIMUM_PASEO_ERROR_JSON_BYTES:
                continue
            try:
                candidate = json.loads(payload)
            except json.JSONDecodeError:
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
        observed_id = value.get("id") or value.get("Id") or value.get("agentId")
        provider = value.get("provider") or value.get("Provider")
        model = value.get("model") or value.get("Model")
        thinking = value.get("thinking") or value.get("Thinking")
        mode = value.get("mode") or value.get("Mode")
        cwd = value.get("cwd") or value.get("Cwd")
        lifecycle = value.get("status") or value.get("Status")
        archived = value.get("archived", value.get("Archived"))
        pending = value.get("PendingPermissions")
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
        self._run([*args, "--json"])


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
        self._contexts = dict(repository_contexts)
        self._state_path = Path(state_path)
        self._journal = _V3JsonJournal(self._state_path)
        self._pending_save_state: dict[str, Any] | None = None
        (
            self._actions,
            self._events,
            self._workspace_intents,
            self._next_event_cursor,
        ) = self._load()

    @staticmethod
    def _failure(error: Exception) -> _RuntimeFailure:
        if isinstance(error, (OSError, TimeoutError)):
            return _RuntimeFailure.transport()
        if isinstance(error, RuntimeGatewayError):
            return _RuntimeFailure(error.code, "Runtime Artifact or configuration validation failed")
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
        workspace_id = candidate.get("id") or candidate.get("Id") or candidate.get("workspaceId")
        path = candidate.get("path") or candidate.get("Path") or candidate.get("cwd")
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
    ]:
        value = self._journal.read_unlocked()
        if value is None:
            return {}, [], {}, 1
        if not isinstance(value, dict):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo Runtime action record is invalid"
            )
        actions = value.get("actions")
        raw_events = value.get("events")
        if (
            value.get("schema_version") != 3
            or not isinstance(actions, dict)
            or not isinstance(raw_events, list)
            or not all(isinstance(item, dict) for item in actions.values())
        ):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo Runtime action record is invalid"
            )
        raw_intents = value.get("workspace_intents", {})
        if not isinstance(raw_intents, dict):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo Workspace intent record is invalid"
            )
        normalized_intents: dict[str, dict[str, str]] = {}
        for action, intent in raw_intents.items():
            if (
                not isinstance(action, str)
                or not isinstance(intent, dict)
                or frozenset(intent)
                not in {
                    frozenset(
                        {
                            "repository_path",
                            "base_commit",
                            "slug",
                            "spec_identity_digest",
                        }
                    ),
                    frozenset(
                        {
                            "repository_path",
                            "base_commit",
                            "slug",
                            "spec_identity_digest",
                            "phase",
                        }
                    ),
                }
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID", "Paseo Workspace intent record is invalid"
                )
            normalized = dict(intent)
            # A legacy intent was durable immediately before create.  Treat it
            # as create-pending so a V3 restart can only read back, never
            # duplicate the provider effect.
            normalized.setdefault("phase", "create_pending")
            if (
                any(not isinstance(part, str) or not part for part in normalized.values())
                or normalized["phase"] not in {"recorded", "create_pending"}
                or _GIT_COMMIT_RE.fullmatch(normalized["base_commit"]) is None
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID", "Paseo Workspace intent record is invalid"
                )
            normalized_intents[action] = normalized
        events: list[_RuntimeEvent] = []
        for raw in raw_events:
            if not isinstance(raw, dict):
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
            events.append(event)
        cursor_values: list[int] = []
        for event in events:
            try:
                cursor_value = int(event.cursor)
            except ValueError as error:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID", "Paseo Runtime event cursor is invalid"
                ) from error
            if cursor_value < 1:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID", "Paseo Runtime event cursor is invalid"
                )
            cursor_values.append(cursor_value)
        if cursor_values != sorted(set(cursor_values)):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo Runtime event cursor is invalid"
            )
        next_event_cursor = value.get(
            "next_event_cursor", (cursor_values[-1] + 1 if cursor_values else 1)
        )
        if (
            not isinstance(next_event_cursor, int)
            or isinstance(next_event_cursor, bool)
            or next_event_cursor < (cursor_values[-1] + 1 if cursor_values else 1)
        ):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo Runtime event cursor is invalid"
            )
        return (
            dict(actions),
            events[-_MAXIMUM_RUNTIME_EVENTS:],
            normalized_intents,
            next_event_cursor,
        )

    def _save(self) -> None:
        state = self._pending_save_state or {
            "actions": self._actions,
            "events": self._events,
            "workspace_intents": self._workspace_intents,
            "next_event_cursor": self._next_event_cursor,
        }
        self._journal.replace_unlocked(
            {
                "schema_version": 3,
                "actions": state["actions"],
                "events": [asdict(event) for event in state["events"]],
                "workspace_intents": state["workspace_intents"],
                "next_event_cursor": state["next_event_cursor"],
            }
        )

    def _publish_state(
        self,
        actions: dict[str, dict[str, Any]],
        events: list[_RuntimeEvent],
        workspace_intents: dict[str, dict[str, str]],
        next_event_cursor: int,
    ) -> None:
        self._actions = actions
        self._events = events
        self._workspace_intents = workspace_intents
        self._next_event_cursor = next_event_cursor

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
            }
            candidate = deepcopy(durable)
            try:
                result = mutation(candidate)
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
        already_claimed: Callable[[Mapping[str, Any]], bool],
        update: Callable[[dict[str, Any]], None],
    ) -> bool:
        """Atomically grant one caller ownership of a provider effect."""

        expected = deepcopy(record)
        stable_action_id = _require_text(
            expected.get("subject", {}).get("stable_action_id")
            if isinstance(expected.get("subject"), dict)
            else None,
            "persisted stable action id",
        )

        def commit(state: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
            current = state["actions"].get(stable_action_id)
            if current == expected:
                updated = deepcopy(current)
                update(updated)
                state["actions"][stable_action_id] = updated
                return True, deepcopy(updated)
            if isinstance(current, dict) and already_claimed(current):
                return False, deepcopy(current)
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
        if value in {"running", "busy"}:
            if record.get("pending_resume") is True or record.get("parked") is True:
                self._persist_record_update(
                    record,
                        lambda updated: updated.update(
                            {
                                "pending_resume": False,
                                "parked": False,
                                "pending_stop_command": None,
                            }
                        ),
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
                self._persist_record_update(
                    record,
                    lambda updated: updated.update(
                        {
                            "pending_park": False,
                            "parked": True,
                            "pending_stop_command": None,
                        }
                    ),
                )
            return "parked"
        if value in {"idle", "closed", "completed", "complete", "finished"}:
            if output_exists:
                if any(
                    record.get(key) is True
                    for key in ("pending_park", "pending_resume", "parked")
                ):
                    self._persist_record_update(
                        record,
                        lambda updated: updated.update(
                            {
                                "pending_park": False,
                                "pending_resume": False,
                                "parked": False,
                                "pending_stop_command": None,
                            }
                        ),
                    )
                return "completed"
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

    def _call(self, args: list[str]) -> Any:
        _PaseoCliTransport.validate_arguments(args)
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
        candidate = Path(value)
        return (candidate if candidate.is_absolute() else path / candidate).resolve()

    @classmethod
    def _verify_workspace_repository(
        cls,
        context: RuntimeRepositoryContext,
        workspace_path: str,
        *,
        expected_base_commit: str | None,
    ) -> None:
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
        if expected_base_commit is not None:
            if _GIT_COMMIT_RE.fullmatch(expected_base_commit) is None:
                raise ValueError("prepared Workspace base commit is invalid")
            workspace_head = cls._git_readback(workspace, "rev-parse", "HEAD^{commit}")
            if expected_base_commit != workspace_head:
                raise RuntimeGatewayError(
                    "RUNTIME_IDENTITY_AMBIGUOUS",
                    "Prepared Paseo Workspace does not start at its pinned base commit",
                )

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
            item.get("id") or item.get("Id") or item.get("agentId") or item.get("AgentId")
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

    def _verify_staged_workspace(
        self, record: Mapping[str, Any], *, require_pinned_base: bool
    ) -> tuple[RuntimeSubject, RuntimeProfile, str]:
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
        self._verify_workspace_repository(
            context,
            registered[1],
            expected_base_commit=(workspace_base_commit if require_pinned_base else None),
        )
        fenced = record.get("fenced", False)
        if type(fenced) is not bool:
            raise ValueError("prepared fence state is invalid")
        prompt_digest = _require_digest(
            record.get("prompt_artifact_digest"), "prepared prompt artifact digest"
        )
        prompt_file = record.get("prompt_file")
        if not isinstance(prompt_file, str) or not prompt_file:
            raise ValueError("prepared prompt file is invalid")
        self._artifacts.read_file(Path(prompt_file), prompt_digest)
        input_digests = record.get("input_artifact_digests")
        input_files = record.get("input_files")
        if (
            not isinstance(input_digests, list)
            or not all(isinstance(digest, str) for digest in input_digests)
            or not isinstance(input_files, dict)
        ):
            raise ValueError("prepared input Artifact record is invalid")
        for digest in input_digests:
            path = input_files.get(digest)
            if not isinstance(path, str) or not path:
                raise ValueError("prepared input Artifact file is invalid")
            self._artifacts.read_file(Path(path), _require_digest(digest, "input artifact digest"))
        schema_file = record.get("output_schema_file")
        schema_digest = record.get("output_schema_digest")
        if not isinstance(schema_file, str) or not schema_file:
            raise ValueError("prepared output schema file is invalid")
        self._artifacts.read_file(
            Path(schema_file), _require_digest(schema_digest, "output schema digest")
        )
        return subject, profile, prompt_digest

    def _prepared(self, record: Mapping[str, Any]) -> _PreparedRuntimeObservation:
        subject, profile, prompt_digest = self._verify_staged_workspace(
            record, require_pinned_base=True
        )
        fenced = record.get("fenced", False)
        if type(fenced) is not bool:
            raise ValueError("prepared fence state is invalid")
        return _PreparedRuntimeObservation(
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

    def _completed_output(
        self, record: dict[str, Any], subject: RuntimeSubject
    ) -> str | None:
        output_digest = record.get("output_artifact_digest")
        if isinstance(output_digest, str):
            output = self._artifacts.read_json(output_digest)
        else:
            try:
                output_ref, output = self._artifacts.put_json_file(
                    Path(record["result_file"])
                )
            except RuntimeGatewayError as error:
                if error.code == "RUNTIME_ARTIFACT_MISSING":
                    return None
                raise
            output_digest = output_ref.digest
        if (
            not isinstance(output, dict)
            or output.get("schema_version") != "gwo.runtime.output.v1"
            or output.get("subject_digest") != subject.digest
            or output.get("stable_action_id") != subject.stable_action_id
            or output.get("authority_digest") != subject.authority_digest
            or "payload" not in output
        ):
            raise RuntimeGatewayError(
                "RUNTIME_OUTPUT_ARTIFACT_INVALID",
                "Paseo result Artifact does not bind its exact action",
            )
        if record.get("output_artifact_digest") != output_digest:
            self._persist_record_update(
                record,
                lambda updated: updated.__setitem__(
                    "output_artifact_digest", output_digest
                ),
            )
        return output_digest

    def _bound(self, record: dict[str, Any], agent: _PaseoAgentReadback) -> _BoundRuntimeObservation:
        subject, profile = self._record_subject(record)
        # A Bound readback must continue to prove the same staged prompt,
        # inputs, output schema, and workspace registry identity as Prepared.
        # Never treat a previously accepted artifact as implicitly durable.
        self._verify_staged_workspace(record, require_pinned_base=False)
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
        if workspace_id != record["workspace_id"] or workspace_path != record["workspace_path"]:
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
            self._persist_record_update(
                record,
                lambda updated: updated.update(
                    {
                        "fenced": True,
                        "pending_fence": False,
                        "pending_fence_claim_id": None,
                        "pending_fence_quiesced": False,
                    }
                ),
            )
            fenced = True
        elif fenced_agent is not None and fenced and pending_fence:
            self._persist_record_update(
                record,
                lambda updated: updated.update(
                    {
                        "pending_fence": False,
                        "pending_fence_claim_id": None,
                        "pending_fence_quiesced": False,
                    }
                ),
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
            self._persist_record_update(
                record,
                lambda updated: updated.update(
                    {
                        "pending_fence": False,
                        "pending_fence_claim_id": None,
                        "pending_fence_quiesced": False,
                    }
                ),
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
        if pending_response is not None and (
            not isinstance(pending_response, dict)
            or set(pending_response)
            != {"request_id", "decision", "request_digest", "provider_receipt"}
            or not isinstance(pending_response["request_id"], str)
            or pending_response["decision"] not in {"allow", "deny"}
            or not isinstance(pending_response["request_digest"], str)
            or _DIGEST_RE.fullmatch(pending_response["request_digest"]) is None
            or (
                pending_response["provider_receipt"] is not None
                and (
                    not isinstance(pending_response["provider_receipt"], dict)
                    or set(pending_response["provider_receipt"])
                    != {"requestId", "agentId", "agentShortId", "name", "result"}
                    or not all(
                        isinstance(value, str) and value
                        for value in pending_response["provider_receipt"].values()
                    )
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
                self._persist_record_update(
                    record,
                    lambda updated: updated.__setitem__("pending_retire", False),
                )
            output_digest = record.get("output_artifact_digest")
            if output_digest is not None and not isinstance(output_digest, str):
                raise ValueError("retired Paseo output Artifact record is invalid")
            lifecycle = "retired"
        else:
            output_digest = self._completed_output(record, subject)
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
                "request_digest": pending_response["request_digest"],
                "provider_receipt": receipt,
                "provider_receipt_digest": digest_value(receipt),
                "stable_action_id": subject.stable_action_id,
                "subject_digest": subject.digest,
                "binding_ref": binding_ref,
            }
            self._persist_record_update(
                record,
                lambda updated: (
                    updated.pop("pending_permission_response", None),
                    updated.__setitem__(
                        "completed_permission_response", completion_record
                    ),
                ),
            )
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
            self._persist_record_update(
                record,
                lambda updated: updated.update(
                    {
                        "bound_agent_id": agent.agent_id,
                        "pending_start": False,
                    }
                ),
            )
        return _BoundRuntimeObservation(
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
                    operation_id="paseo/0.2.3:operation:" + digest_value(
                        {"provider": identity["provider"], "tool": tool, "name": name}
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
            != "paseo/0.2.3:operation:"
            + digest_value({"provider": "paseo/0.2.3", "tool": name, "name": name})
        ):
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo permission decision receipt does not bind the exact request",
            )
        return {
            "requestId": request_id,
            "agentId": receipt_agent_id,
            "agentShortId": agent_short_id,
            "name": name,
            "result": result,
        }

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
            "request_digest",
            "provider_receipt",
            "provider_receipt_digest",
            "stable_action_id",
            "subject_digest",
            "binding_ref",
        }:
            raise ValueError("Paseo completed permission response is invalid")
        receipt = value["provider_receipt"]
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
            or _DIGEST_RE.fullmatch(value["provider_receipt_digest"]) is None
            or value["provider_receipt_digest"] != digest_value(receipt)
            or receipt["requestId"] != value["request_id"][:8]
            or receipt["agentId"] != agent_id
            or receipt["agentShortId"] != agent_id[:7]
            or receipt["result"]
            != ("allowed" if value["decision"] == "allow" else "denied")
            or value["stable_action_id"] != subject.stable_action_id
            or value["subject_digest"] != subject.digest
            or value["binding_ref"] != f"paseo:{agent_id}"
        ):
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo completed permission response does not bind this Runtime action",
            )
        return _CompletedPermissionResponse(
            request_id=value["request_id"],
            decision=value["decision"],
            request_digest=value["request_digest"],
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
        payload = self._call(["workspace", "ls", "--json"])
        values = payload.get("workspaces", payload) if isinstance(payload, dict) else payload
        if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
            raise ValueError("workspace list response is invalid")
        for item in values:
            self._workspace_payload(item)
            if not isinstance(item.get("name"), str) or not item["name"]:
                raise ValueError("workspace list omitted a Workspace name")
            if not isinstance(item.get("isolation"), str) or not item["isolation"]:
                raise ValueError("workspace list omitted Workspace isolation")
        matches = []
        for item in values:
            workspace_id, workspace_path = self._workspace_payload(item)
            listed_path = item.get("cwd") or item.get("path") or item.get("Path")
            if (
                item.get("name") == slug
                and item.get("isolation") == "worktree"
                and listed_path == workspace_path
            ):
                matches.append((workspace_id, workspace_path))
        if len(matches) > 1:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS", "multiple Paseo Workspaces match one stable action"
            )
        if not matches:
            return None
        matched = matches[0]
        if expected is not None:
            expected_id, expected_path = expected
            if (
                matched[0] != expected_id
                or Path(matched[1]).resolve() != Path(expected_path).resolve()
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_IDENTITY_AMBIGUOUS",
                    "Paseo workspace create receipt does not match exact registry readback",
                )
        return matched

    def _workspace_for_agent(
        self,
        record: Mapping[str, Any],
        context: RuntimeRepositoryContext,
        agent_cwd: str,
    ) -> tuple[str, str] | None:
        payload = self._call(["workspace", "ls", "--json"])
        values = payload.get("workspaces", payload) if isinstance(payload, dict) else payload
        if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
            raise ValueError("workspace list response is invalid")
        for item in values:
            self._workspace_payload(item)
            if not isinstance(item.get("name"), str) or not item["name"]:
                raise ValueError("workspace list omitted a Workspace name")
            if not isinstance(item.get("isolation"), str) or not item["isolation"]:
                raise ValueError("workspace list omitted Workspace isolation")
        matches = []
        for item in values:
            _workspace_id, workspace_path = self._workspace_payload(item)
            if (
                _workspace_id == record.get("workspace_id")
                and item.get("name") == record.get("workspace_slug")
                and item.get("isolation") == "worktree"
                and Path(workspace_path).resolve() == Path(agent_cwd).resolve()
            ):
                matches.append(item)
        if len(matches) > 1:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS", "multiple Paseo Workspaces match one bound Agent"
            )
        if not matches:
            return None
        workspace = self._workspace_payload(matches[0])
        if Path(workspace[1]).resolve() != Path(str(record.get("workspace_path"))).resolve():
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo Workspace registry path changed after prepare",
            )
        self._verify_workspace_repository(
            context, workspace[1], expected_base_commit=None
        )
        return workspace

    def _stage_artifact(self, workspace_path: Path, artifact: ArtifactRef) -> Path:
        payload = self._artifacts.read_bytes(artifact.digest)
        target = workspace_path / ".gwo" / "runtime-artifacts" / f"{artifact.digest}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, target)
        with target.open("rb") as handle:
            staged = handle.read(self._artifacts.maximum_bytes + 1)
        if (
            len(staged) > self._artifacts.maximum_bytes
            or hashlib.sha256(staged).hexdigest() != artifact.digest
        ):
            raise RuntimeGatewayError(
                "RUNTIME_ARTIFACT_DIGEST_MISMATCH", "staged Runtime Artifact is invalid"
            )
        return target

    @staticmethod
    def _spec_identity_digest(spec: _RuntimeActionSpec) -> str:
        return digest_value(
            {
                "subject_digest": spec.subject_digest,
                "profile_digest": spec.profile.digest,
                "prompt_artifact_digest": spec.prompt_artifact.digest,
                "input_artifact_digests": [
                    artifact.digest for artifact in spec.input_artifacts
                ],
            }
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
        return _PrepareReceipt(spec.stable_action_id, str(action["workspace_id"]))

    def _ensure_workspace_intent(
        self,
        spec: _RuntimeActionSpec,
        context: RuntimeRepositoryContext,
        slug: str,
    ) -> dict[str, str] | None:
        """Freeze complete local workspace identity before any Paseo call."""

        stable_action_id = spec.stable_action_id
        expected_without_base = {
            "repository_path": str(Path(context.path).resolve()),
            "slug": slug,
            "spec_identity_digest": self._spec_identity_digest(spec),
        }
        existing = self._workspace_intents.get(stable_action_id)
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
            "spec_identity_digest": self._spec_identity_digest(spec),
            "phase": existing_intent.get("phase"),
        }
        if (
            existing_intent != expected_intent
            or not isinstance(base_commit, str)
            or existing_intent.get("phase") not in {"recorded", "create_pending"}
        ):
            raise RuntimeGatewayError(
                "RUNTIME_ACTION_IDENTITY_MISMATCH",
                "Paseo Workspace intent changed for one stable action",
            )
        if _GIT_COMMIT_RE.fullmatch(base_commit) is None:
            raise ValueError("Paseo Workspace intent base commit is invalid")
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

        if not self._transact(claim_create):
            raise RuntimeGatewayError(
                "RUNTIME_MATERIALIZATION_PENDING",
                "Paseo Workspace creation awaits exact action-owned Workspace readback",
            )
        create_args = [
            "workspace", "create", "--isolation", "worktree", "--path", str(context.path),
            "--mode", "branch-off", "--worktree-slug", slug,
            "--base", base_commit, "--title", slug, "--json",
        ]
        try:
            workspace = self._call(create_args)
        except Exception as create_error:
            # Every acknowledgement loss keeps the pre-existing exact
            # readback-first recovery path: a registry-proved Workspace is
            # safe to adopt.  Only a typed permanent reject combined with a
            # precise negative readback proves a retry is safe enough to drop
            # the create intent.
            recovered = self._workspace_by_identity(slug=slug)
            if recovered is not None:
                self._verify_workspace_repository(
                    context, recovered[1], expected_base_commit=base_commit
                )
                return (*recovered, base_commit)
            if not self._is_definitive_command_rejection(create_error):
                raise
            claimed_intent = deepcopy(self._workspace_intents.get(stable_action_id))

            def clear_rejected_create(state: dict[str, Any]) -> None:
                if state["workspace_intents"].get(stable_action_id) != claimed_intent:
                    raise RuntimeGatewayError(
                        "RUNTIME_ACTION_IDENTITY_MISMATCH",
                        "Paseo Workspace intent changed during create recovery",
                    )
                state["workspace_intents"].pop(stable_action_id)

            self._transact(clear_rejected_create)
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

    @staticmethod
    def _action_file_paths(
        workspace_path: Path, subject: RuntimeSubject
    ) -> tuple[Path, Path]:
        action_digest = digest_value(
            {
                "repository": subject.repository,
                "stable_action_id": subject.stable_action_id,
            }
        )
        return (
            workspace_path / ".gwo" / "runtime-results" / f"{action_digest}.json",
            workspace_path / ".gwo" / "runtime-schemas" / f"{action_digest}.json",
        )

    @staticmethod
    def _write_output_schema(schema_target: Path, spec: _RuntimeActionSpec) -> str:
        schema_target.parent.mkdir(parents=True, exist_ok=True)
        payload = canonical_bytes(
            {
                    "type": "object",
                    "required": [
                        "schema_version",
                        "subject_digest",
                        "stable_action_id",
                        "authority_digest",
                        "payload",
                    ],
                    "properties": {
                        "schema_version": {"const": "gwo.runtime.output.v1"},
                        "subject_digest": {"const": spec.subject_digest},
                        "stable_action_id": {"const": spec.stable_action_id},
                        "authority_digest": {"const": spec.subject.authority_digest},
                        "payload": {},
                    },
            }
        )
        schema_target.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    def prepare(self, spec: _RuntimeActionSpec) -> _PrepareReceipt | _RuntimeFailure:
        try:
            self._refresh()
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
            prompt = self._artifacts.get(spec.prompt_artifact.digest)
            staged = {
                artifact.digest: self._stage_artifact(Path(workspace_path), artifact)
                for artifact in (prompt, *spec.input_artifacts)
            }
            target = staged[prompt.digest]
            result_target, schema_target = self._action_file_paths(
                Path(workspace_path), spec.subject
            )
            schema_digest = self._write_output_schema(schema_target, spec)
            action_record = {
                "subject": spec.subject.canonical(), "subject_digest": spec.subject_digest,
                "profile": asdict(spec.profile), "profile_digest": spec.profile.digest,
                "prompt_artifact_digest": prompt.digest, "workspace_id": workspace_id,
                "workspace_path": workspace_path, "workspace_slug": slug,
                "workspace_base_commit": workspace_base_commit,
                "prompt_file": str(target), "fenced": False,
                "input_artifact_digests": [item.digest for item in spec.input_artifacts],
                "input_files": {digest: str(path) for digest, path in staged.items()},
                "result_file": str(result_target),
                "output_schema_file": str(schema_target),
                "output_schema_digest": schema_digest,
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
            return self._failure(error)

    def observe(
        self, stable_action_id: str
    ) -> _PreparedRuntimeObservation | _BoundRuntimeObservation | _RuntimeFailure:
        try:
            self._refresh()
            record = self._actions.get(stable_action_id)
            if record is None:
                return _RuntimeFailure.absent(stable_action_id)
            subject, profile = self._record_subject(record)
            labels = self._labels(
                _RuntimeActionSpec(
                    stable_action_id=stable_action_id, subject=subject, profile=profile,
                    prompt_artifact=self._artifacts.get(record["prompt_artifact_digest"]), input_artifacts=(),
                )
            )
            agent = self._one_agent(labels, include_archived=True)
            if agent is None:
                if isinstance(record.get("bound_agent_id"), str):
                    return _RuntimeFailure(
                        "RUNTIME_BINDING_MISSING",
                        "previously bound Paseo Agent is absent from exact label readback",
                    )
                if record.get("pending_start") is True:
                    return _RuntimeFailure(
                        "RUNTIME_MATERIALIZATION_PENDING",
                        "Paseo start acknowledgement awaits stable-action label readback",
                    )
                return self._prepared(record)
            bound_agent_id = record.get("bound_agent_id")
            if isinstance(bound_agent_id, str) and bound_agent_id != agent.agent_id:
                return _RuntimeFailure(
                    "RUNTIME_IDENTITY_AMBIGUOUS",
                    "Paseo label readback changed the exact bound Agent identity",
                )
            return self._bound(record, agent)
        except Exception as error:
            return self._failure(error)

    def _start_agent(self, stable_action_id: str, record: Mapping[str, Any]) -> None:
        subject, profile = self._record_subject(record)
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
            f"{Path(record['result_file']).relative_to(record['workspace_path']).as_posix()}."
        )
        args = [
            "run", "--background", "--title", f"GWO {stable_action_id}",
            "--provider", "kimi" if profile.provider == "kimi-cli" else profile.provider,
            "--model", profile.model, "--thinking", profile.thinking, "--mode", profile.mode,
            "--workspace", record["workspace_id"], "--cwd", record["workspace_path"],
            "--output-schema", record["output_schema_file"],
        ]
        for key, value in sorted(labels.items()):
            args.extend(["--label", f"{key}={value}"])
        self._call([*args, "--json", bootstrap])

    @staticmethod
    def _write_resume_file(record: Mapping[str, Any]) -> Path:
        """Atomically stage and re-read the replayable Paseo resume prompt."""

        resume_file = (
            Path(str(record["workspace_path"]))
            / ".gwo"
            / "runtime-artifacts"
            / "resume.txt"
        )
        payload = b"Resume the accepted GWO action from the verified Prompt Artifact."
        resume_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = resume_file.with_name(f"{resume_file.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, resume_file)
            if resume_file.read_bytes() != payload:
                raise OSError("Paseo resume prompt verification failed")
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return resume_file

    @staticmethod
    def _is_definitive_command_rejection(error: Exception) -> bool:
        """Only typed non-ambiguous rejects prove the provider did not act."""

        return isinstance(error, RuntimeGatewayError) and error.code not in {
            "RUNTIME_TRANSPORT_UNAVAILABLE",
            "RUNTIME_COMMAND_ACK_LOST",
            "RUNTIME_EFFECT_AMBIGUOUS",
            "RUNTIME_MATERIALIZATION_PENDING",
        }

    def _restore_pending_after_definitive_rejection(
        self,
        record: dict[str, Any],
        previous: Mapping[str, Any],
    ) -> None:
        """CAS-restore only the exact claim after a typed rejected command."""

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
        self, stable_action_id: str, command: RuntimeTransition
    ) -> _CommandReceipt | _RuntimeFailure:
        pending_before: dict[str, Any] | None = None
        fence_provider_call_started = False
        fence_claim_id: str | None = None
        try:
            if type(command) not in {RuntimeCommand, PermissionResponse}:
                return _RuntimeFailure("RUNTIME_COMMAND_INVALID", "Runtime command is outside the closed union")
            self._refresh()
            record = self._actions.get(stable_action_id)
            if record is None:
                return _RuntimeFailure("RUNTIME_ACTION_UNKNOWN", "Runtime action is unknown")
            observation = self.observe(stable_action_id)
            if isinstance(observation, _RuntimeFailure):
                return observation
            record = self._actions.get(stable_action_id)
            if record is None:
                return _RuntimeFailure(
                    "RUNTIME_ACTION_STATE_CHANGED",
                    "Runtime action disappeared during command readback",
                )
            subject, _profile = self._record_subject(record)
            if command is RuntimeCommand.START:
                if not isinstance(observation, _PreparedRuntimeObservation):
                    return _RuntimeFailure("RUNTIME_COMMAND_INVALID", "start requires a Prepared Runtime action")
                if observation.fenced is not False:
                    return _RuntimeFailure(
                        "RUNTIME_COMMAND_INVALID", "start requires an unfenced Prepared Runtime action"
                    )
                pending_before = deepcopy(record)
                claimed = self._claim_record_update(
                    record,
                    already_claimed=lambda current: current.get("pending_start") is True,
                    update=lambda updated: updated.__setitem__("pending_start", True),
                )
                if not claimed:
                    pending_before = None
                    return _RuntimeFailure(
                        "RUNTIME_MATERIALIZATION_PENDING",
                        "Paseo start already has one durable effect owner",
                    )
                self._start_agent(stable_action_id, record)
            elif not isinstance(observation, _BoundRuntimeObservation):
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
                pending_value = {
                    "request_id": command.request_id,
                    "decision": command.decision,
                    "request_digest": digest_value(asdict(matching[0])),
                    "provider_receipt": None,
                }
                claimed = self._claim_record_update(
                    record,
                    already_claimed=lambda current: (
                        isinstance(current.get("pending_permission_response"), dict)
                        and all(
                            current["pending_permission_response"].get(key)
                            == value
                            for key, value in pending_value.items()
                            if key != "provider_receipt"
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
                    )
                try:
                    receipt = self._call(
                        [
                            "permit", command.decision, observation.agent_id,
                            command.request_id, "--json",
                        ]
                    )
                except Exception as call_error:
                    # This is deliberately narrower than the surrounding
                    # command handler.  A typed non-ambiguous failure *from
                    # the permit call itself* proves Paseo rejected it before
                    # effect, so the exact pre-call record may be retried.
                    # Receipt parsing or identity validation happens below
                    # after the provider returned successfully and must keep
                    # the pending ambiguity evidence intact.
                    if self._is_definitive_command_rejection(call_error):
                        try:
                            self._restore_pending_after_definitive_rejection(
                                record, permission_pending_before
                            )
                        except Exception as rollback_error:
                            return self._failure(rollback_error)
                    return self._failure(call_error)
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
                pending_before = deepcopy(record)
                claimed = self._claim_record_update(
                    record,
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
                    )
                self._call(["send", "--no-wait", "--json", observation.agent_id, "--prompt-file", str(resume_file)])
            elif command in {RuntimeCommand.PARK, RuntimeCommand.INTERRUPT}:
                if observation.lifecycle in {"completed", "retired"}:
                    return _RuntimeFailure(
                        "RUNTIME_COMMAND_INVALID",
                        "park and interrupt require an active Runtime binding",
                    )
                stop_command = _transition_name(command)
                pending_before = deepcopy(record)
                claimed = self._claim_record_update(
                    record,
                    already_claimed=lambda current: (
                        current.get("pending_park") is True
                        and current.get("pending_stop_command") == stop_command
                    ),
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
                    )
                self._call(["stop", observation.agent_id, "--json"])
            elif command is RuntimeCommand.FENCE:
                if record.get("pending_fence") is True:
                    return _RuntimeFailure(
                        "RUNTIME_MATERIALIZATION_PENDING",
                        "Paseo fence already has one durable effect owner",
                    )
                pending_before = deepcopy(record)
                fence_claim_id = uuid4().hex
                claimed = self._claim_record_update(
                    record,
                    already_claimed=lambda current: current.get("pending_fence") is True,
                    update=lambda updated: updated.update(
                        {
                            "pending_fence": True,
                            "pending_fence_claim_id": fence_claim_id,
                            "pending_fence_quiesced": False,
                        }
                    ),
                )
                if not claimed:
                    pending_before = None
                    fence_claim_id = None
                    return _RuntimeFailure(
                        "RUNTIME_MATERIALIZATION_PENDING",
                        "Paseo fence already has one durable effect owner",
                    )
                fence_provider_call_started = True
                self._client.update_labels(observation.agent_id, {"gwo.runtime_fenced": "true"})
            elif command is RuntimeCommand.RETIRE:
                pending_before = deepcopy(record)
                claimed = self._claim_record_update(
                    record,
                    already_claimed=lambda current: current.get("pending_retire") is True,
                    update=lambda updated: updated.__setitem__("pending_retire", True),
                )
                if not claimed:
                    pending_before = None
                    return _RuntimeFailure(
                        "RUNTIME_MATERIALIZATION_PENDING",
                        "Paseo retirement already has one durable effect owner",
                    )
                self._call(["archive", observation.agent_id, "--force", "--json"])
            return _CommandReceipt(stable_action_id, command)
        except Exception as error:
            definitive_rejection = (
                pending_before is not None
                and self._is_definitive_command_rejection(error)
            )
            if definitive_rejection:
                try:
                    self._restore_pending_after_definitive_rejection(record, pending_before)
                except Exception as rollback_error:
                    return self._failure(rollback_error)
            elif fence_provider_call_started and fence_claim_id is not None:
                try:
                    self._mark_fence_claim_quiesced(record, fence_claim_id)
                except Exception:
                    # Failure to persist quiescence leaves the exclusive
                    # in-flight claim intact, which is safe and restartable.
                    # Keep the provider's original failure taxonomy.
                    pass
            return self._failure(error)

    def events(self, after_cursor: str | None) -> _RuntimeEventPage | _RuntimeFailure:
        try:
            cursor = 0 if after_cursor is None else int(after_cursor)
            if cursor < 0:
                raise ValueError("event cursor cannot be negative")
            self._refresh()
            observed_states: list[tuple[str, str, str]] = []
            for stable_action_id in sorted(self._actions):
                observation = self.observe(stable_action_id)
                if isinstance(observation, _RuntimeFailure):
                    return observation
                state = {
                    "lifecycle": observation.lifecycle,
                    "fenced": observation.fenced,
                    "permission_requests": (
                        [asdict(request) for request in observation.permission_requests]
                        if isinstance(observation, _BoundRuntimeObservation)
                        else []
                    ),
                }
                state_digest = digest_value(state)
                observed_states.append(
                    (stable_action_id, state_digest, observation.lifecycle)
                )

            def commit(state: dict[str, Any]) -> None:
                for stable_action_id, state_digest, lifecycle in observed_states:
                    record = state["actions"].get(stable_action_id)
                    if not isinstance(record, dict):
                        continue
                    if record.get("wake_state_digest") == state_digest:
                        continue
                    record["wake_state_digest"] = state_digest
                    event_cursor = state["next_event_cursor"]
                    state["next_event_cursor"] = event_cursor + 1
                    state["events"].append(
                        _RuntimeEvent(
                            cursor=str(event_cursor),
                            stable_action_id=stable_action_id,
                            kind=f"state:{lifecycle}",
                        )
                    )
                    del state["events"][:-_MAXIMUM_RUNTIME_EVENTS]

            self._transact(commit)
            available = [
                event for event in self._events if int(event.cursor) > cursor
            ]
            page = tuple(available[:_MAXIMUM_RUNTIME_EVENT_PAGE])
            latest_cursor = self._next_event_cursor - 1
            return _RuntimeEventPage(
                events=page,
                next_cursor=(
                    str(int(page[-1].cursor))
                    if page
                    else (None if latest_cursor == 0 else str(latest_cursor))
                ),
            )
        except (TypeError, ValueError):
            return _RuntimeFailure("RUNTIME_EVENT_CURSOR_INVALID", "event cursor is invalid")
        except Exception as error:
            return self._failure(error)


class _PaseoStaticAssignmentValidator:
    """Factory-owned static capability check, outside the four-method seam."""

    def __init__(self, repository_contexts: Mapping[str, RuntimeRepositoryContext]):
        self._contexts = dict(repository_contexts)

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


def build_runtime_gateway(
    *,
    store_path: Path,
    configuration: RuntimeConfiguration,
    repository_contexts: Mapping[str, RuntimeRepositoryContext],
    artifact_root: Path | None = None,
    maximum_artifact_bytes: int = 1_048_576,
) -> "RuntimeGateway":
    """Compose the V3 production Gateway without exposing provider machinery."""

    gateway_store = Path(store_path)
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
    ):
        self._store_path = Path(store_path)
        self._journal = _V3JsonJournal(self._store_path)
        self._pending_save_data: dict[str, Any] | None = None
        # Underscored parameters are internal/test composition hooks. Semantic
        # callers construct the default production Gateway through
        # build_runtime_gateway and never receive this Provider seam.
        self._adapter = _adapter
        self._configuration = configuration
        self._artifacts = _artifacts or ArtifactStore(
            self._store_path.parent / "runtime-artifacts"
        )
        self._static_assignment_validator = _static_assignment_validator
        self._data = self._load()

    # Caller interface operation 1.  It neither calls an adapter nor reserves
    # a slot, workspace, session, Agent, or provider action.
    def planning_preflight(
        self,
        subject: CampaignPlanningSubject,
        overrides: CampaignStartRuntimeOverrides | None = None,
    ) -> PlanningPreflightReceipt:
        if type(subject) is not CampaignPlanningSubject:
            raise RuntimeGatewayError(
                "RUNTIME_PREFLIGHT_SUBJECT_INVALID",
                "planning preflight accepts CampaignPlanningSubject only",
            )
        self._refresh()
        # Resolve and statically validate without persisting a Campaign first:
        # a production host/context/profile defect must be a pure preflight
        # failure, never a partial campaign claim.
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
            if overrides is not None and candidate_overrides != overrides.canonical():
                raise RuntimeGatewayError(
                    "RUNTIME_CAMPAIGN_IDENTITY_MISMATCH",
                    "Campaign handle was read back with different Runtime overrides",
                )
        else:
            candidate_overrides = (overrides or CampaignStartRuntimeOverrides()).canonical()
        assignment = self._resolve_assignment(
            subject.repository,
            RuntimeSelector.coordinator(),
            None,
            candidate_overrides,
        )
        self._validate_static_assignment(subject, assignment)
        campaign_value = {
            "repository": subject.repository,
            "campaign_key": subject.campaign_key,
            "overrides": candidate_overrides,
        }
        binding = {
            "subject_digest": subject.digest,
            "campaign_overrides_digest": digest_value(candidate_overrides),
            "assignment": assignment,
        }
        receipt_digest = digest_value(
            {
                "kind": "planning_preflight.v1",
                "subject_digest": subject.digest,
                "stable_action_id": subject.stable_action_id,
                "assignment_digest": digest_value(
                    {
                        "selector": assignment["selector"],
                        "configuration_source": assignment["configuration_source"],
                        "profile_digest": assignment["profile_digest"],
                        "availability_fallback_profile_digest": assignment[
                            "availability_fallback_profile_digest"
                        ],
                        "fallback_selected": False,
                    }
                ),
            }
        )
        expected = {**binding, "receipt_digest": receipt_digest}

        def commit(data: dict[str, Any]) -> None:
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
                    overrides is not None
                    and durable_campaign.get("overrides") != overrides.canonical()
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
        self._refresh()
        if isinstance(subject, CampaignPlanningSubject):
            persisted_preflight = self._require_preflight(subject, preflight)
        elif preflight is not None:
            raise RuntimeGatewayError(
                "RUNTIME_PREFLIGHT_INVALID",
                "Work Run progress does not accept a planning preflight receipt",
            )
        wake_hints, next_cursor = self._wake_hints(wake_cursor, subject)
        record = self._assignment_for_progress(
            subject,
            None if not isinstance(subject, CampaignPlanningSubject) else persisted_preflight,
        )
        self._validate_static_assignment(subject, record)
        observation_or_failure = self._observe(subject.stable_action_id)
        if isinstance(observation_or_failure, _RuntimeFailure):
            if not self._is_authoritative_absence(
                observation_or_failure, subject.stable_action_id
            ):
                self._raise_failure(observation_or_failure)
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
            prepared = self._prepare(spec)
            if isinstance(prepared, _RuntimeFailure):
                # Only an acknowledged prepare may be recovered from the
                # exact Prepared/Bound observation.  A permanent prepare
                # failure remains its original typed failure even if the
                # second readback still says absent.
                observation_or_failure = self._observe(subject.stable_action_id)
                if isinstance(observation_or_failure, (_PreparedRuntimeObservation, _BoundRuntimeObservation)):
                    pass
                elif (
                    isinstance(observation_or_failure, _RuntimeFailure)
                    and self._is_authoritative_absence(
                        observation_or_failure, subject.stable_action_id
                    )
                ):
                    self._raise_failure(prepared)
                elif isinstance(observation_or_failure, _RuntimeFailure):
                    self._raise_failure(observation_or_failure)
                else:
                    self._raise_failure(prepared)
            else:
                observation_or_failure = self._observe(subject.stable_action_id)
                if isinstance(observation_or_failure, _RuntimeFailure):
                    self._raise_failure(observation_or_failure)
        observation = observation_or_failure
        if isinstance(observation, _PreparedRuntimeObservation):
            self._validate_prepared_observation(subject, record, observation)
            if observation.fenced is not False:
                raise RuntimeGatewayError(
                    "RUNTIME_COMMAND_INVALID",
                    "start requires an unfenced Prepared Runtime observation",
                )
            self._record_observation(record, observation)
            observation = self._command_with_readback(
                subject.stable_action_id,
                RuntimeCommand.START,
            )
            self._validate_bound_observation(subject, record, observation)
            self._record_observation(record, observation)
        else:
            self._validate_bound_observation(subject, record, observation)
            if (
                observation.lifecycle in {"running", "completed"}
                and record["lifecycle"] is None
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_OBSERVATION_INVALID",
                    "Provider reported semantic execution before Gateway issued start or resume",
                )
            self._record_observation(record, observation)
        if observation.lifecycle == "parked":
            if observation.fenced is not False:
                raise RuntimeGatewayError(
                    "RUNTIME_COMMAND_INVALID",
                    "progress cannot resume a fenced Runtime binding",
                )
            observation = self._command_with_readback(
                subject.stable_action_id,
                RuntimeCommand.RESUME,
            )
            self._validate_bound_observation(subject, record, observation)
            self._record_observation(record, observation)
        elif observation.lifecycle not in {"running", "completed"}:
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                f"cannot progress Runtime lifecycle {observation.lifecycle}",
            )
        return self._progress_receipt(
            subject,
            observation,
            command=None,
            wake_cursor=next_cursor,
            wake_hints=wake_hints,
        )

    # Caller interface operation 3.  Binding refs remain private, including
    # for start/resume: they re-enter the same observe-gated progression path.
    def transition(
        self,
        stable_action_id: str,
        command: RuntimeTransition,
    ) -> RuntimeProgressReceipt:
        _require_text(stable_action_id, "stable_action_id")
        if type(command) not in {RuntimeCommand, PermissionResponse}:
            raise RuntimeGatewayError(
                "RUNTIME_COMMAND_INVALID", "Runtime command is outside the closed union"
            )
        self._refresh()
        record = self._data["actions"].get(stable_action_id)
        if not isinstance(record, dict):
            raise RuntimeGatewayError("RUNTIME_ACTION_UNKNOWN", "stable action is unknown")
        subject = _subject_from_canonical(record.get("subject"))
        if command in {RuntimeCommand.START, RuntimeCommand.RESUME}:
            observed = self._observe(stable_action_id)
            if isinstance(observed, _RuntimeFailure):
                self._raise_failure(observed)
            if command is RuntimeCommand.START and not isinstance(
                observed, _PreparedRuntimeObservation
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_COMMAND_INVALID",
                    "start requires an exact Prepared Runtime observation",
                )
            if command is RuntimeCommand.START and observed.fenced is not False:
                raise RuntimeGatewayError(
                    "RUNTIME_COMMAND_INVALID",
                    "start requires an unfenced Prepared Runtime observation",
                )
            if command is RuntimeCommand.RESUME and (
                not isinstance(observed, _BoundRuntimeObservation)
                or observed.lifecycle != "parked"
                or observed.fenced is not False
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_COMMAND_INVALID",
                    "resume requires an exact unfenced parked Bound Runtime observation",
                )
            preflight = (
                self._preflight_receipt(subject)
                if isinstance(subject, CampaignPlanningSubject)
                else None
            )
            progressed = self.progress(subject, preflight)
            return replace(
                progressed,
                command=command,
                receipt_digest=digest_value(
                    {
                        "progress_receipt": progressed.receipt_digest,
                        "requested_command": _transition_canonical(command),
                    }
                ),
            )
        observation = self._observe(stable_action_id)
        if isinstance(observation, _RuntimeFailure):
            self._raise_failure(observation)
        if not isinstance(observation, _BoundRuntimeObservation):
            raise RuntimeGatewayError(
                "RUNTIME_COMMAND_INVALID",
                "only start can be issued before Runtime binding exists",
            )
        self._validate_bound_observation(subject, record, observation)
        if type(command) is PermissionResponse:
            matching = [
                request
                for request in observation.permission_requests
                if request.request_id == command.request_id
            ]
            if len(matching) != 1:
                if _completed_permission_effect_matches(command, observation):
                    self._record_observation(record, observation)
                    return self._progress_receipt(subject, observation, command=command)
                raise RuntimeGatewayError(
                    "RUNTIME_PERMISSION_REQUEST_UNKNOWN",
                    "permission response does not bind one exact pending request",
                )
        observation = self._command_with_readback(
            stable_action_id,
            command,
        )
        self._validate_bound_observation(subject, record, observation)
        self._record_observation(record, observation)
        return self._progress_receipt(subject, observation, command=command)

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
            RuntimeSelector.ticket(subject.role),
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
            "prompt_artifact_digest": prompt_digest,
            "binding_ref": None,
            "lifecycle": None,
            "planning_output_artifact_digest": None,
            "observation_digest": None,
            "materialization_observed": False,
        }
        identity_fields = (
            "subject",
            "subject_digest",
            "selector",
            "configuration_source",
            "profile_digest",
            "availability_fallback_profile_digest",
            "fallback_selected",
            "prompt_artifact_digest",
        )

        def commit(data: dict[str, Any]) -> None:
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

    def _resolve_assignment(
        self,
        repository: str,
        selector: RuntimeSelector,
        ticket_key: str | None,
        persisted_overrides: Mapping[str, Any],
    ) -> dict[str, str]:
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
            repository_mapping = self._configuration.repository_mappings.get(
                repository, {}
            ).get(selector)
            if repository_mapping is not None:
                mapping = repository_mapping
                source = "repository"
        if mapping is None:
            host_mapping = self._configuration.host_mappings.get(selector)
            if host_mapping is not None:
                mapping = host_mapping
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
        try:
            return self._configuration.profiles[digest]
        except KeyError as error:
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID",
                "Runtime mapping refers to an unknown immutable Profile",
            ) from error

    def _validate_static_assignment(
        self, subject: RuntimeSubject, assignment: Mapping[str, Any]
    ) -> None:
        profile_digest = assignment.get("profile_digest")
        if not isinstance(profile_digest, str):
            raise RuntimeGatewayError(
                "RUNTIME_CONFIGURATION_INVALID", "Runtime assignment lacks a primary Profile"
            )
        profile = self._profile(profile_digest)
        if self._static_assignment_validator is not None:
            self._static_assignment_validator(subject, profile)
            fallback_digest = assignment.get("availability_fallback_profile_digest")
            if fallback_digest is not None:
                if not isinstance(fallback_digest, str):
                    raise RuntimeGatewayError(
                        "RUNTIME_CONFIGURATION_INVALID",
                        "Runtime assignment fallback Profile is invalid",
                    )
                self._static_assignment_validator(subject, self._profile(fallback_digest))

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
            if isinstance(subject, CampaignPlanningSubject)
            else subject.prompt_artifact_digest
        )
        prompt = self._artifacts.get(prompt_digest)
        payload = self._artifacts.read_json(prompt.digest)
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != "gwo.runtime.prompt.v1"
            or payload.get("subject_digest") != subject.prompt_binding_digest
            or payload.get("authority_digest") != subject.authority_digest
            or "payload" not in payload
        ):
            raise RuntimeGatewayError(
                "RUNTIME_PROMPT_ARTIFACT_INVALID",
                "Prompt Artifact does not bind its exact subject, payload, and authority",
            )
        if isinstance(subject, CampaignPlanningSubject):
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

    def _observe(
        self, stable_action_id: str
    ) -> _PreparedRuntimeObservation | _BoundRuntimeObservation | _RuntimeFailure:
        try:
            result = self._adapter.observe(stable_action_id)
        except (OSError, TimeoutError):
            return _RuntimeFailure.transport()
        except Exception:
            return _RuntimeFailure(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "Runtime provider observation failed",
            )
        if type(result) in {_PreparedRuntimeObservation, _BoundRuntimeObservation}:
            return result
        if type(result) is _RuntimeFailure:
            if (
                result.code == "RUNTIME_ACTION_ABSENT"
                or result.authoritative_absence is not False
            ) and not self._is_authoritative_absence(result, stable_action_id):
                return _RuntimeFailure(
                    "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                    "Runtime provider returned malformed absence evidence",
                )
            return result
        return _RuntimeFailure(
            "RUNTIME_PROVIDER_PROTOCOL_INVALID",
            "Runtime provider observation result is invalid",
        )

    @staticmethod
    def _is_authoritative_absence(
        failure: _RuntimeFailure, stable_action_id: str
    ) -> bool:
        return (
            type(failure) is _RuntimeFailure
            and failure.code == "RUNTIME_ACTION_ABSENT"
            and failure.detail == "authoritative stable-action absence"
            and failure.stable_action_id == stable_action_id
            and failure.authoritative_absence is True
        )

    def _prepare(self, spec: _RuntimeActionSpec) -> _PrepareReceipt | _RuntimeFailure:
        try:
            result = self._adapter.prepare(spec)
        except (OSError, TimeoutError):
            return _RuntimeFailure.transport()
        except Exception:
            return _RuntimeFailure(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID", "Runtime provider prepare failed"
            )
        if isinstance(result, (_PrepareReceipt, _RuntimeFailure)):
            return result
        return _RuntimeFailure(
            "RUNTIME_PROVIDER_PROTOCOL_INVALID", "Runtime provider prepare result is invalid"
        )

    def _wake_hints(
        self, cursor: str | None, subject: RuntimeSubject
    ) -> tuple[tuple[str, ...], str | None]:
        try:
            page = self._adapter.events(cursor)
        except (OSError, TimeoutError):
            return (), cursor
        except Exception:
            return (), cursor
        if isinstance(page, _RuntimeFailure):
            return (), cursor
        if not isinstance(page, _RuntimeEventPage):
            return (), cursor
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

    @staticmethod
    def _raise_failure(failure: _RuntimeFailure) -> None:
        raise RuntimeGatewayError(failure.code, failure.detail)

    def _preflight_receipt(
        self, subject: CampaignPlanningSubject
    ) -> PlanningPreflightReceipt:
        value = self._data["preflights"].get(subject.stable_action_id)
        if not isinstance(value, dict) or value.get("subject_digest") != subject.digest:
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
            or persisted is None
            or receipt.subject_digest != subject.digest
            or receipt.stable_action_id != subject.stable_action_id
            or persisted.get("receipt_digest") != receipt.receipt_digest
        ):
            raise RuntimeGatewayError(
                "RUNTIME_PREFLIGHT_REQUIRED",
                "Campaign Planning progress requires its exact read-only preflight receipt",
            )
        return persisted

    def _validate_prepared_observation(
        self,
        subject: RuntimeSubject,
        record: Mapping[str, Any],
        observation: _PreparedRuntimeObservation,
    ) -> None:
        expected_plan = (
            None
            if isinstance(subject, CampaignPlanningSubject)
            else subject.plan_revision_digest
        )
        expected_work = None if isinstance(subject, CampaignPlanningSubject) else subject.work_run_key
        values_match = (
            observation.stable_action_id == subject.stable_action_id
            and observation.repository == subject.repository
            and observation.campaign_key == subject.campaign_key
            and observation.campaign_handle == subject.campaign_handle
            and observation.plan_revision_digest == expected_plan
            and observation.work_run_key == expected_work
            and observation.authority_subtree_digest == subject.authority_digest
            and observation.subject_digest == subject.digest
            and observation.profile_digest == record["profile_digest"]
            and observation.prompt_artifact_digest == record["prompt_artifact_digest"]
            and observation.binding_ref is None
            and observation.agent_id is None
            and observation.session_id is None
            and isinstance(observation.workspace_id, str)
            and bool(observation.workspace_id)
            and observation.lifecycle == "prepared"
            and observation.prompt_staged is True
            and type(observation.fenced) is bool
        )
        if not values_match:
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
        expected_plan = (
            None
            if isinstance(subject, CampaignPlanningSubject)
            else subject.plan_revision_digest
        )
        expected_work = None if isinstance(subject, CampaignPlanningSubject) else subject.work_run_key
        expected_authority = subject.authority_digest
        permissions_valid = isinstance(observation.permission_requests, tuple) and all(
            isinstance(request, _PermissionRequest)
            and request.stable_action_id == subject.stable_action_id
            and request.subject_digest == subject.digest
            and request.binding_ref == observation.binding_ref
            and request.authority_subtree_digest == subject.authority_digest
            for request in observation.permission_requests
        )
        permission_ids = (
            [request.request_id for request in observation.permission_requests]
            if permissions_valid
            else []
        )
        completed = observation.completed_permission_response
        completed_valid = (
            completed is None
            or (
                type(completed) is _CompletedPermissionResponse
                and _DIGEST_RE.fullmatch(completed.request_digest) is not None
                and _DIGEST_RE.fullmatch(completed.provider_receipt_digest) is not None
                and completed.stable_action_id == subject.stable_action_id
                and completed.subject_digest == subject.digest
                and completed.binding_ref == observation.binding_ref
                and completed.decision in {"allow", "deny"}
                and isinstance(completed.request_id, str)
                and bool(completed.request_id)
            )
        )
        identifiers_are_exact = all(
            isinstance(value, str) and bool(value)
            for value in (
                observation.binding_ref,
                observation.agent_id,
                observation.session_id,
                observation.workspace_id,
            )
        )
        values_match = (
            observation.stable_action_id == subject.stable_action_id
            and observation.repository == subject.repository
            and observation.campaign_key == subject.campaign_key
            and observation.campaign_handle == subject.campaign_handle
            and observation.plan_revision_digest == expected_plan
            and observation.work_run_key == expected_work
            and observation.authority_subtree_digest == expected_authority
            and observation.subject_digest == subject.digest
            and observation.profile_digest == record["profile_digest"]
            and observation.prompt_artifact_digest == record["prompt_artifact_digest"]
            and observation.prompt_accepted is True
            and identifiers_are_exact
            and observation.lifecycle in _LIFECYCLES
            and type(observation.fenced) is bool
            and permissions_valid
            and len(permission_ids) == len(set(permission_ids))
            and completed_valid
        )
        if not values_match:
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "authoritative observation does not prove the complete Runtime binding",
            )

    def _record_observation(
        self,
        record: dict[str, Any],
        observation: _PreparedRuntimeObservation | _BoundRuntimeObservation,
    ) -> None:
        canonical = asdict(observation)
        observation_digest = digest_value(canonical)
        stable_action_id = observation.stable_action_id
        expected_subject = record.get("subject_digest")
        expected_profile = record.get("profile_digest")
        expected_observation = record.get("observation_digest")

        def commit(data: dict[str, Any]) -> dict[str, Any]:
            current = data["actions"].get(stable_action_id)
            if (
                not isinstance(current, dict)
                or current.get("subject_digest") != expected_subject
                or current.get("profile_digest") != expected_profile
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
            current.update(
                {
                    "binding_ref": observation.binding_ref,
                    "lifecycle": observation.lifecycle,
                    "planning_output_artifact_digest": getattr(
                        observation, "planning_output_artifact_digest", None
                    ),
                    "observation_digest": observation_digest,
                    "materialization_observed": True,
                }
            )
            current.pop("observations", None)
            return deepcopy(current)

        updated = self._transact(commit)
        record.clear()
        record.update(updated)

    def _require_bound_observation(self, stable_action_id: str) -> _BoundRuntimeObservation:
        observation = self._observe(stable_action_id)
        if isinstance(observation, _RuntimeFailure):
            self._raise_failure(observation)
        if not isinstance(observation, _BoundRuntimeObservation):
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "command readback did not bind an Agent, session, and Runtime binding",
            )
        return observation

    def _command_with_readback(
        self,
        stable_action_id: str,
        command: RuntimeTransition,
    ) -> _BoundRuntimeObservation:
        try:
            result = self._adapter.command(stable_action_id, command)
        except (OSError, TimeoutError):
            result = _RuntimeFailure.transport()
        except Exception:
            result = _RuntimeFailure(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID", "Runtime provider command failed"
            )
        if isinstance(result, _RuntimeFailure):
            if result.code not in {
                "RUNTIME_TRANSPORT_UNAVAILABLE",
                "RUNTIME_COMMAND_ACK_LOST",
                "RUNTIME_EFFECT_AMBIGUOUS",
            }:
                self._raise_failure(result)
            # A transport/ack ambiguity may follow a successful command.
            # Readback is authoritative; semantic/unknown failures never get
            # converted into a successful transition merely by a later poll.
            observation = self._observe(stable_action_id)
            if isinstance(observation, _RuntimeFailure):
                self._raise_failure(observation)
            if not isinstance(observation, _BoundRuntimeObservation):
                raise RuntimeGatewayError(
                    "RUNTIME_OBSERVATION_INVALID",
                    "command acknowledgement loss read back an unbound Runtime action",
                )
            self._validate_command_effect(command, observation)
            return observation
        if not isinstance(result, _CommandReceipt):
            raise RuntimeGatewayError(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID", "Runtime provider command result is invalid"
            )
        if result.stable_action_id != stable_action_id or result.command != command:
            raise RuntimeGatewayError(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "Runtime provider command receipt does not bind the requested action",
            )
        observation = self._require_bound_observation(stable_action_id)
        self._validate_command_effect(command, observation)
        return observation

    def _validate_command_effect(
        self,
        command: RuntimeTransition,
        observation: _BoundRuntimeObservation,
    ) -> None:
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
        observation: _BoundRuntimeObservation,
        *,
        command: RuntimeTransition | None = None,
        wake_cursor: str | None = None,
        wake_hints: tuple[str, ...] = (),
    ) -> RuntimeProgressReceipt:
        kind = "planning" if isinstance(subject, CampaignPlanningSubject) else "work_run"
        payload = {
            "kind": f"runtime_{kind}_receipt.v1",
            "subject_digest": subject.digest,
            "stable_action_id": subject.stable_action_id,
            "lifecycle": observation.lifecycle,
            "output_artifact_digest": observation.output_artifact_digest,
            "command": _transition_canonical(command),
            "observation_digest": digest_value(asdict(observation)),
        }
        if observation.lifecycle == "completed":
            output_digest = observation.output_artifact_digest
            if output_digest is None:
                raise RuntimeGatewayError(
                    "RUNTIME_OUTPUT_ARTIFACT_MISSING",
                    "completed Runtime action omitted its Artifact-backed output",
                )
            output = self._artifacts.read_json(output_digest)
            if (
                not isinstance(output, dict)
                or output.get("schema_version") != "gwo.runtime.output.v1"
                or output.get("subject_digest") != subject.digest
                or output.get("stable_action_id") != subject.stable_action_id
                or output.get("authority_digest") != subject.authority_digest
                or "payload" not in output
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_OUTPUT_ARTIFACT_INVALID",
                    "output Artifact does not bind its exact subject and authority",
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
        )

    def _load(self) -> dict[str, Any]:
        with self._journal.exclusive():
            return self._load_unlocked()

    def _load_unlocked(self) -> dict[str, Any]:
        value = self._journal.read_unlocked()
        if value is None:
            return {"schema_version": 1, "campaigns": {}, "actions": {}, "preflights": {}}
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or not all(isinstance(value.get(key), dict) for key in ("campaigns", "actions", "preflights"))
        ):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "RuntimeGateway durable record has an unknown schema"
            )
        normalized = deepcopy(value)
        for record in normalized["actions"].values():
            if not isinstance(record, dict):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID", "RuntimeGateway action record is invalid"
                )
            legacy_observations = record.pop("observations", None)
            if "materialization_observed" not in record:
                record["materialization_observed"] = bool(legacy_observations)
            if type(record["materialization_observed"]) is not bool:
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID",
                    "RuntimeGateway materialization history is invalid",
                )
        return normalized

    def _refresh(self) -> None:
        with self._journal.exclusive():
            self._data = self._load_unlocked()

    def _transact(self, mutation: Callable[[dict[str, Any]], Any]) -> Any:
        with self._journal.exclusive():
            durable = self._load_unlocked()
            candidate = deepcopy(durable)
            try:
                result = mutation(candidate)
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
    if not isinstance(value, dict):
        raise RuntimeGatewayError(
            "RUNTIME_STORE_INVALID", "persisted Campaign override is malformed"
        )
    return ProfileMapping(
        primary_profile_digest=value.get("primary_profile_digest"),
        availability_fallback_profile_digest=value.get(
            "availability_fallback_profile_digest"
        ),
    )


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
                role=value["role"],
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


@dataclass
class _InMemoryAction:
    spec: _RuntimeActionSpec
    workspace_id: str
    binding_ref: str | None = None
    lifecycle: str = "prepared"
    fenced: bool = False
    output_artifact_digest: str | None = None
    pending_permissions: list[tuple[str, str, str]] = field(default_factory=list)
    completed_permission_response: _CompletedPermissionResponse | None = None
    wake_state_digest: str | None = None


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
        self._actions: dict[str, _InMemoryAction] = {}
        self._events: list[_RuntimeEvent] = []
        self._next_event_cursor = 1
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

        action.output_artifact_digest = self._artifacts.put_canonical(
            {
                "schema_version": "gwo.runtime.output.v1",
                "subject_digest": action.spec.subject_digest,
                "stable_action_id": action.spec.stable_action_id,
                "authority_digest": action.spec.subject.authority_digest,
                "payload": {
                    "input_artifact_digests": [
                        artifact.digest for artifact in action.spec.input_artifacts
                    ]
                },
            }
        ).digest
        action.lifecycle = "completed"

    def prepare(self, spec: _RuntimeActionSpec) -> _PrepareReceipt | _RuntimeFailure:
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
            lifecycle="prepared",
            pending_permissions=list(self._pending_permissions.get(spec.stable_action_id, ())),
        )
        self._actions[spec.stable_action_id] = action
        self.staged_prompt_count += 1
        if self._lose_prepare_ack_once:
            self._lose_prepare_ack_once = False
            return _RuntimeFailure(
                "RUNTIME_PREPARE_ACK_LOST", "Provider prepare acknowledgement was lost"
            )
        return _PrepareReceipt(spec.stable_action_id, action.workspace_id)

    def observe(
        self, stable_action_id: str
    ) -> _PreparedRuntimeObservation | _BoundRuntimeObservation | _RuntimeFailure:
        self.observe_calls.append(stable_action_id)
        if self.observe_failure is not None:
            return self.observe_failure
        action = self._actions.get(stable_action_id)
        if action is None:
            return _RuntimeFailure.absent(stable_action_id)
        try:
            self._verify_action_artifacts(action)
        except RuntimeGatewayError as error:
            return _RuntimeFailure(error.code, "staged Runtime Artifact is invalid")
        subject = action.spec.subject
        if action.binding_ref is None:
            return _PreparedRuntimeObservation(
                stable_action_id=stable_action_id,
                repository=subject.repository,
                campaign_key=subject.campaign_key,
                campaign_handle=subject.campaign_handle,
                plan_revision_digest=(
                    None
                    if isinstance(subject, CampaignPlanningSubject)
                    else subject.plan_revision_digest
                ),
                work_run_key=(
                    None if isinstance(subject, CampaignPlanningSubject) else subject.work_run_key
                ),
                subject_digest=subject.digest,
                profile_digest=action.spec.profile.digest,
                workspace_id=action.workspace_id,
                prompt_artifact_digest=action.spec.prompt_artifact.digest,
                fenced=action.fenced,
                authority_subtree_digest=subject.authority_digest,
            )
        return _BoundRuntimeObservation(
            stable_action_id=stable_action_id,
            binding_ref=action.binding_ref,
            repository=subject.repository,
            campaign_key=subject.campaign_key,
            campaign_handle=subject.campaign_handle,
            plan_revision_digest=(
                None
                if isinstance(subject, CampaignPlanningSubject)
                else subject.plan_revision_digest
            ),
            work_run_key=(
                None if isinstance(subject, CampaignPlanningSubject) else subject.work_run_key
            ),
            subject_digest=subject.digest,
            profile_digest=action.spec.profile.digest,
            agent_id=f"agent:{action.binding_ref}",
            session_id=f"session:{action.binding_ref}",
            workspace_id=action.workspace_id,
            prompt_artifact_digest=action.spec.prompt_artifact.digest,
            prompt_accepted=True,
            lifecycle=action.lifecycle,
            permission_requests=tuple(sorted(
                (
                    _PermissionRequest(
                    request_id=request_id,
                    operation_id=operation_id,
                    resource_id=resource_id,
                    binding_ref=action.binding_ref,
                    authority_subtree_digest=subject.authority_digest,
                    stable_action_id=stable_action_id,
                    subject_digest=subject.digest,
                    )
                    for request_id, operation_id, resource_id in action.pending_permissions
                ),
                key=lambda request: (
                    request.request_id, request.operation_id, request.resource_id
                ),
            )),
            fenced=action.fenced,
            authority_subtree_digest=subject.authority_digest,
            planning_output_artifact_digest=action.output_artifact_digest,
            completed_permission_response=action.completed_permission_response,
        )

    def command(
        self, stable_action_id: str, command: RuntimeTransition
    ) -> _CommandReceipt | _RuntimeFailure:
        if type(command) not in {RuntimeCommand, PermissionResponse}:
            return _RuntimeFailure(
                "RUNTIME_COMMAND_INVALID", "Runtime command is outside the closed union"
            )
        action = self._actions.get(stable_action_id)
        if action is None:
            return _RuntimeFailure("RUNTIME_BINDING_UNKNOWN", "Runtime binding is unknown")
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
            matching = [
                request
                for request in action.pending_permissions
                if request[0] == command.request_id
            ]
            if len(matching) != 1:
                completed = action.completed_permission_response
                if (
                    type(completed) is _CompletedPermissionResponse
                    and completed.request_id == command.request_id
                    and completed.decision == command.decision
                ):
                    return _CommandReceipt(action.spec.stable_action_id, command)
                return _RuntimeFailure(
                    "RUNTIME_PERMISSION_REQUEST_UNKNOWN",
                    "permission response does not bind one exact pending request",
                )
            request_id, operation_id, resource_id = matching[0]
            action.pending_permissions.remove(matching[0])
            action.completed_permission_response = _CompletedPermissionResponse(
                request_id=request_id,
                decision=command.decision,
                request_digest=digest_value(
                    asdict(
                        _PermissionRequest(
                            request_id=request_id,
                            operation_id=operation_id,
                            resource_id=resource_id,
                            binding_ref=action.binding_ref,
                            authority_subtree_digest=action.spec.subject.authority_digest,
                            stable_action_id=stable_action_id,
                            subject_digest=action.spec.subject.digest,
                        )
                    )
                ),
                provider_receipt_digest=digest_value(
                    {
                        "adapter": "in-memory.v1",
                        "request_id": request_id,
                        "decision": command.decision,
                        "binding_ref": action.binding_ref,
                    }
                ),
                stable_action_id=stable_action_id,
                subject_digest=action.spec.subject.digest,
                binding_ref=action.binding_ref,
            )
            if not action.pending_permissions:
                self._complete_action(action)
        if command is RuntimeCommand.START:
            if action.lifecycle != "prepared":
                return _RuntimeFailure(
                    "RUNTIME_COMMAND_INVALID", "start requires a prepared binding"
                )
            if action.fenced is not False:
                return _RuntimeFailure(
                    "RUNTIME_COMMAND_INVALID", "start requires an unfenced Prepared action"
                )
            action.binding_ref = f"binding:{digest_value({'stable_action_id': stable_action_id})[:24]}"
            self.created_agent_count += 1
            # A provider may be otherwise idle while an exact pending
            # permission keeps the semantic action active.  Match the
            # production normalization and expose a Bound ``running`` state
            # until the pending request is resolved.
            if action.pending_permissions:
                action.lifecycle = "running"
                action.output_artifact_digest = None
            else:
                self._complete_action(action)
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
        elif command is RuntimeCommand.RETIRE:
            action.lifecycle = "retired"
        if self._lose_command_ack_once is command:
            self._lose_command_ack_once = None
            return _RuntimeFailure(
                "RUNTIME_COMMAND_ACK_LOST",
                "Provider command acknowledgement was lost",
            )
        return _CommandReceipt(action.spec.stable_action_id, command)

    def events(self, after_cursor: str | None) -> _RuntimeEventPage | _RuntimeFailure:
        try:
            cursor = 0 if after_cursor is None else int(after_cursor)
            if cursor < 0:
                raise ValueError("event cursor cannot be negative")
        except (TypeError, ValueError):
            return _RuntimeFailure("RUNTIME_EVENT_CURSOR_INVALID", "event cursor is invalid")
        for stable_action_id, action in sorted(self._actions.items()):
            observation = self.observe(stable_action_id)
            if isinstance(observation, _RuntimeFailure):
                return observation
            state = {
                "lifecycle": observation.lifecycle,
                "fenced": observation.fenced,
                "permission_requests": (
                    [asdict(request) for request in observation.permission_requests]
                    if isinstance(observation, _BoundRuntimeObservation)
                    else []
                ),
            }
            state_digest = digest_value(state)
            if action.wake_state_digest == state_digest:
                continue
            action.wake_state_digest = state_digest
            self._events.append(
                _RuntimeEvent(
                    str(self._next_event_cursor),
                    stable_action_id,
                    f"state:{observation.lifecycle}",
                )
            )
            self._next_event_cursor += 1
            del self._events[:-_MAXIMUM_RUNTIME_EVENTS]
        available = [event for event in self._events if int(event.cursor) > cursor]
        events = tuple(available[:_MAXIMUM_RUNTIME_EVENT_PAGE])
        latest_cursor = self._next_event_cursor - 1
        return _RuntimeEventPage(
            events=events,
            next_cursor=(
                events[-1].cursor
                if events
                else (None if latest_cursor == 0 else str(latest_cursor))
            ),
        )
