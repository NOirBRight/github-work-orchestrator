"""The single V8 boundary for semantic Runtime materialization.

The gateway deliberately gives its callers no provider command, session, or
binding choreography.  A caller supplies one closed semantic subject and an
Artifact reference; the gateway reads back an existing action before staging
or starting it.  Provider adapters are private implementation details.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping, Protocol
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
_PASEO_BATCH_META = frozenset("&|<>^%!")
_MAXIMUM_PASEO_COMMAND_CHARS = 7_500


class RuntimeGatewayError(RuntimeError):
    """A typed Gateway-owned configuration, identity, or transport failure."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


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
    PERMISSION_RESPONSE = "permission_response"
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
    return command.value if isinstance(command, RuntimeCommand) else "permission_response"


def _transition_canonical(command: RuntimeTransition | None) -> Any:
    if command is None:
        return None
    if isinstance(command, RuntimeCommand):
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

    @property
    def output_artifact_digest(self) -> str | None:
        return self.planning_output_artifact_digest


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


class _PaseoCliTransport:
    """Concrete V3-only JSON transport for the documented Paseo 0.2.3 surface."""

    def __init__(self, executable: str = "paseo", *, timeout_seconds: int = 60):
        self._executable = shutil.which(executable) or executable
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def validate_arguments(args: list[str]) -> None:
        """Fail closed before a dynamic value reaches a Paseo CLI wrapper."""

        if not isinstance(args, list) or not args or not all(
            isinstance(argument, str) and argument for argument in args
        ):
            raise ValueError("Paseo command arguments are invalid")
        for argument in args:
            _require_paseo_argument(argument, "Paseo command argument")
        command_length = sum(len(argument) + 1 for argument in args)
        if command_length > _MAXIMUM_PASEO_COMMAND_CHARS:
            raise RuntimeGatewayError(
                "RUNTIME_VENDOR_ARGUMENT_INVALID",
                "Paseo command exceeds the bounded command-line limit",
            )

    def _run(self, args: list[str]) -> Any:
        self.validate_arguments(args)
        try:
            result = subprocess.run(
                [self._executable, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("Paseo command timed out") from error
        if result.returncode != 0:
            raise OSError("Paseo command failed")
        if not result.stdout.strip():
            return {}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ValueError("Paseo JSON response is invalid") from error

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
        return _PaseoAgentReadback(
            agent_id=observed_id,
            provider=provider,
            model=model,
            thinking=thinking,
            mode=mode,
            cwd=cwd,
            lifecycle=lifecycle,
            archived=archived,
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
        self._actions, self._events, self._workspace_intents = self._load()

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
    ) -> tuple[dict[str, dict[str, Any]], list[_RuntimeEvent], dict[str, dict[str, str]]]:
        if not self._state_path.exists():
            return {}, [], {}
        try:
            value = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo Runtime action record is unreadable"
            ) from error
        if not isinstance(value, dict):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo Runtime action record is invalid"
            )
        # The original repair draft persisted the action map directly.  It
        # has no events, but its deterministic action records remain valid.
        if "actions" not in value:
            if not all(isinstance(item, dict) for item in value.values()):
                raise RuntimeGatewayError(
                    "RUNTIME_STORE_INVALID", "Paseo Runtime action record is invalid"
                )
            return dict(value), [], {}
        actions = value.get("actions")
        raw_events = value.get("events")
        if (
            value.get("schema_version") not in {2, 3}
            or not isinstance(actions, dict)
            or not isinstance(raw_events, list)
            or not all(isinstance(item, dict) for item in actions.values())
        ):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo Runtime action record is invalid"
            )
        raw_intents = value.get("workspace_intents", {})
        if not isinstance(raw_intents, dict) or not all(
            isinstance(action, str)
            and isinstance(intent, dict)
            and set(intent)
            == {"repository_path", "base_commit", "slug", "spec_identity_digest"}
            and all(isinstance(part, str) and part for part in intent.values())
            and _GIT_COMMIT_RE.fullmatch(intent["base_commit"]) is not None
            for action, intent in raw_intents.items()
        ):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "Paseo Workspace intent record is invalid"
            )
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
        return dict(actions), events, {
            action: dict(intent) for action, intent in raw_intents.items()
        }

    def _save(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        temporary.write_bytes(
            canonical_bytes(
                {
                    "schema_version": 3,
                    "actions": self._actions,
                    "events": [asdict(event) for event in self._events],
                    "workspace_intents": self._workspace_intents,
                }
            )
        )
        temporary.replace(self._state_path)

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
                record["pending_resume"] = False
                record["parked"] = False
                self._save()
            return "running"
        if value == "idle" and (permission_pending or permission_response_pending):
            return "running"
        if value in {"idle", "closed", "completed", "complete", "finished"}:
            if output_exists:
                if any(
                    record.get(key) is True
                    for key in ("pending_park", "pending_resume", "parked")
                ):
                    record["pending_park"] = False
                    record["pending_resume"] = False
                    record["parked"] = False
                    self._save()
                return "completed"
            if record.get("pending_resume") is True:
                raise RuntimeGatewayError(
                    "RUNTIME_MATERIALIZATION_PENDING",
                    "Paseo resume acknowledgement awaits running or output readback",
                )
            if record.get("pending_park") is True or record.get("parked") is True:
                if record.get("pending_park") is True or record.get("parked") is not True:
                    record["pending_park"] = False
                    record["parked"] = True
                    self._save()
                return "parked"
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
        return agent

    def _record_subject(self, record: Mapping[str, Any]) -> tuple[RuntimeSubject, RuntimeProfile]:
        return _subject_from_canonical(record["subject"]), RuntimeProfile(**record["profile"])

    def _prepared(self, record: Mapping[str, Any]) -> _PreparedRuntimeObservation:
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
        self._verify_workspace_repository(
            context, workspace_path, expected_base_commit=workspace_base_commit
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
            record["output_artifact_digest"] = output_digest
            self._save()
        return output_digest

    def _bound(self, record: dict[str, Any], agent: _PaseoAgentReadback) -> _BoundRuntimeObservation:
        subject, profile = self._record_subject(record)
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
        if type(fenced) is not bool or type(pending_fence) is not bool:
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
        if fenced_agent is not None and not fenced and pending_fence:
            record["fenced"] = True
            record["pending_fence"] = False
            fenced = True
            self._save()
        elif fenced_agent is not None and fenced and pending_fence:
            record["pending_fence"] = False
            self._save()
        elif fenced_agent is None and pending_fence:
            # An exact negative label query proves that the attempted update
            # did not take effect.  Clear only the retry intent; the enclosing
            # command effect check rejects this attempt and a later transition
            # may safely retry.
            record["pending_fence"] = False
            self._save()
        if (fenced_agent is not None) != fenced:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo fence label readback does not match the action record",
            )
        permissions = self._permissions(agent, subject, f"paseo:{agent.agent_id}")
        pending_response = record.get("pending_permission_response")
        if pending_response is not None and (
            not isinstance(pending_response, dict)
            or set(pending_response) != {"request_id", "decision"}
            or not isinstance(pending_response["request_id"], str)
            or pending_response["decision"] not in {"allow", "deny"}
        ):
            raise ValueError("Paseo pending permission response is invalid")
        response_effect_observed = (
            isinstance(pending_response, dict)
            and all(
                request.request_id != pending_response["request_id"]
                for request in permissions
            )
        )
        if agent.archived is True:
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
            record.pop("pending_permission_response", None)
            self._save()
        bound_agent_id = record.get("bound_agent_id")
        if bound_agent_id is not None and bound_agent_id != agent.agent_id:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "Paseo Bound observation changed the exact Agent identity",
            )
        binding_changed = False
        if bound_agent_id is None:
            record["bound_agent_id"] = agent.agent_id
            binding_changed = True
        if record.get("pending_start") is True:
            record["pending_start"] = False
            binding_changed = True
        if binding_changed:
            self._save()
        return _BoundRuntimeObservation(
            stable_action_id=subject.stable_action_id,
            binding_ref=f"paseo:{agent.agent_id}",
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
        )

    def _permissions(
        self,
        agent: Any,
        subject: RuntimeSubject,
        binding_ref: str,
    ) -> tuple[_PermissionRequest, ...]:
        payload = self._call(["permit", "ls", "--json"])
        values = payload.get("permissions", payload) if isinstance(payload, dict) else payload
        if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
            raise ValueError("permission list response is invalid")
        normalized: list[_PermissionRequest] = []
        for raw in values:
            owner = raw.get("agent_id") or raw.get("agentId") or raw.get("AgentId")
            if not isinstance(owner, str) or not owner:
                raise ValueError("permission response owner is invalid")
            _require_paseo_argument(owner, "Paseo permission owner")
            if owner != agent.agent_id:
                continue
            request_id = raw.get("request_id") or raw.get("requestId") or raw.get("id")
            operation_id = raw.get("operation_id") or raw.get("operationId") or raw.get("operation")
            resource_id = raw.get("resource_id") or raw.get("resourceId") or raw.get("resource")
            _require_paseo_argument(request_id, "Paseo permission request id")
            _require_paseo_argument(operation_id, "Paseo permission operation id")
            _require_paseo_argument(resource_id, "Paseo permission resource id")
            normalized.append(
                _PermissionRequest(
                    request_id=request_id,
                    operation_id=operation_id,
                    resource_id=resource_id,
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

    def _workspace_by_identity(
        self,
        *,
        slug: str,
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
        matches = [
            item
            for item in values
            if item.get("name") == slug
            and item.get("isolation") == "worktree"
            and isinstance(item.get("cwd"), str)
            and bool(item["cwd"])
        ]
        if len(matches) > 1:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS", "multiple Paseo Workspaces match one stable action"
            )
        return None if not matches else self._workspace_payload(matches[0])

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
        matches = [
            item
            for item in values
            if (item.get("workspaceId") or item.get("id") or item.get("Id"))
            == record.get("workspace_id")
            and item.get("name") == record.get("workspace_slug")
            and item.get("isolation") == "worktree"
            and item.get("cwd") == agent_cwd
        ]
        if len(matches) > 1:
            raise RuntimeGatewayError(
                "RUNTIME_IDENTITY_AMBIGUOUS", "multiple Paseo Workspaces match one bound Agent"
            )
        if not matches:
            return None
        workspace = self._workspace_payload(matches[0])
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

    def _workspace_for_prepare(
        self,
        spec: _RuntimeActionSpec,
        context: RuntimeRepositoryContext,
        slug: str,
    ) -> tuple[str, str, str]:
        stable_action_id = spec.stable_action_id
        existing_intent = self._workspace_intents.get(stable_action_id)
        first_create_attempt = existing_intent is None
        if first_create_attempt:
            base_commit = self._git_readback(
                Path(context.path), "rev-parse", f"{context.base_ref}^{{commit}}"
            )
            if _GIT_COMMIT_RE.fullmatch(base_commit) is None:
                raise ValueError("configured Workspace base does not resolve to one commit")
            intent = {
                "repository_path": str(Path(context.path).resolve()),
                "base_commit": base_commit,
                "slug": slug,
                "spec_identity_digest": digest_value(
                    {
                        "subject_digest": spec.subject_digest,
                        "profile_digest": spec.profile.digest,
                        "prompt_artifact_digest": spec.prompt_artifact.digest,
                        "input_artifact_digests": [
                            artifact.digest for artifact in spec.input_artifacts
                        ],
                    }
                ),
            }
            self._workspace_intents[stable_action_id] = intent
            self._save()
        else:
            base_commit = existing_intent.get("base_commit")
            expected_intent = {
                "repository_path": str(Path(context.path).resolve()),
                "base_commit": base_commit,
                "slug": slug,
                "spec_identity_digest": digest_value(
                    {
                        "subject_digest": spec.subject_digest,
                        "profile_digest": spec.profile.digest,
                        "prompt_artifact_digest": spec.prompt_artifact.digest,
                        "input_artifact_digests": [
                            artifact.digest for artifact in spec.input_artifacts
                        ],
                    }
                ),
            }
            if existing_intent != expected_intent or not isinstance(base_commit, str):
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
            self._workspace_intents.pop(stable_action_id, None)
            self._save()
            return (*recovered, base_commit)
        if not first_create_attempt:
            raise RuntimeGatewayError(
                "RUNTIME_MATERIALIZATION_PENDING",
                "Paseo Workspace creation awaits exact action-owned Workspace readback",
            )
        try:
            workspace = self._call(
                [
                    "workspace", "create", "--isolation", "worktree", "--path", str(context.path),
                    "--mode", "branch-off", "--worktree-slug", slug,
                    "--base", base_commit, "--title", slug, "--json",
                ]
            )
            created = self._workspace_payload(workspace)
            self._verify_workspace_repository(
                context, created[1], expected_base_commit=base_commit
            )
            self._workspace_intents.pop(stable_action_id, None)
            self._save()
            return (*created, base_commit)
        except Exception:
            recovered = self._workspace_by_identity(slug=slug)
            if recovered is None:
                raise
            self._verify_workspace_repository(
                context, recovered[1], expected_base_commit=base_commit
            )
            self._workspace_intents.pop(stable_action_id, None)
            self._save()
            return (*recovered, base_commit)

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
            existing = self._actions.get(spec.stable_action_id)
            if existing is not None:
                expected_inputs = [item.digest for item in spec.input_artifacts]
                if (
                    existing.get("subject_digest") != spec.subject_digest
                    or existing.get("profile_digest") != spec.profile.digest
                    or existing.get("prompt_artifact_digest") != spec.prompt_artifact.digest
                    or existing.get("input_artifact_digests") != expected_inputs
                ):
                    return _RuntimeFailure("RUNTIME_ACTION_IDENTITY_MISMATCH", "stable action changed during prepare")
                self._artifacts.get(str(existing["prompt_artifact_digest"]))
                for digest in existing["input_artifact_digests"]:
                    self._artifacts.get(digest)
                return _PrepareReceipt(spec.stable_action_id, str(existing["workspace_id"]))
            context = self._contexts.get(spec.subject.repository)
            if context is None:
                return _RuntimeFailure("RUNTIME_CONFIGURATION_INVALID", "Paseo repository context is missing")
            if spec.profile.features:
                return _RuntimeFailure(
                    "RUNTIME_CONFIGURATION_INVALID",
                    "Paseo V3 cannot prove non-empty Runtime Profile features",
                )
            if self._one_agent(self._labels(spec), include_archived=True) is not None:
                return _RuntimeFailure(
                    "RUNTIME_ACTION_STATE_MISSING",
                    "Paseo Agent exists but the durable action record is absent",
                )
            slug = digest_value(
                {
                    "repository": spec.subject.repository,
                    "stable_action_id": spec.stable_action_id,
                }
            )[:24]
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
            self._actions[spec.stable_action_id] = {
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
            self._save()
            return _PrepareReceipt(spec.stable_action_id, workspace_id)
        except Exception as error:
            return self._failure(error)

    def observe(
        self, stable_action_id: str
    ) -> _PreparedRuntimeObservation | _BoundRuntimeObservation | _RuntimeFailure:
        try:
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

    def command(
        self, stable_action_id: str, command: RuntimeTransition
    ) -> _CommandReceipt | _RuntimeFailure:
        try:
            if not isinstance(command, (RuntimeCommand, PermissionResponse)):
                return _RuntimeFailure("RUNTIME_COMMAND_INVALID", "Runtime command is outside the closed union")
            record = self._actions.get(stable_action_id)
            if record is None:
                return _RuntimeFailure("RUNTIME_ACTION_UNKNOWN", "Runtime action is unknown")
            observation = self.observe(stable_action_id)
            if isinstance(observation, _RuntimeFailure):
                return observation
            if command is RuntimeCommand.START:
                if not isinstance(observation, _PreparedRuntimeObservation):
                    return _RuntimeFailure("RUNTIME_COMMAND_INVALID", "start requires a Prepared Runtime action")
                if observation.fenced is not False:
                    return _RuntimeFailure(
                        "RUNTIME_COMMAND_INVALID", "start requires an unfenced Prepared Runtime action"
                    )
                record["pending_start"] = True
                self._save()
                self._start_agent(stable_action_id, record)
            elif not isinstance(observation, _BoundRuntimeObservation):
                return _RuntimeFailure("RUNTIME_COMMAND_INVALID", "only start is allowed before Runtime binding exists")
            elif isinstance(command, PermissionResponse):
                matching = [
                    request
                    for request in observation.permission_requests
                    if request.request_id == command.request_id
                ]
                if len(matching) != 1:
                    return _RuntimeFailure(
                        "RUNTIME_PERMISSION_REQUEST_UNKNOWN",
                        "permission response does not bind one exact pending request",
                    )
                record["pending_permission_response"] = {
                    "request_id": command.request_id,
                    "decision": command.decision,
                }
                self._save()
                self._call(
                    [
                        "permit", command.decision, observation.agent_id,
                        command.request_id, "--json",
                    ]
                )
            elif command is RuntimeCommand.RESUME:
                if observation.lifecycle != "parked" or observation.fenced is not False:
                    return _RuntimeFailure(
                        "RUNTIME_COMMAND_INVALID", "resume requires an unfenced parked Runtime binding"
                    )
                record["pending_park"] = False
                record["pending_resume"] = True
                self._save()
                resume_file = Path(record["workspace_path"]) / ".gwo" / "runtime-artifacts" / "resume.txt"
                resume_file.write_text(
                    "Resume the accepted GWO action from the verified Prompt Artifact.", encoding="utf-8"
                )
                self._call(["send", "--no-wait", "--json", observation.agent_id, "--prompt-file", str(resume_file)])
            elif command in {RuntimeCommand.PARK, RuntimeCommand.INTERRUPT}:
                record["pending_park"] = True
                record["pending_resume"] = False
                self._save()
                self._call(["stop", observation.agent_id, "--json"])
            elif command is RuntimeCommand.FENCE:
                record["pending_fence"] = True
                self._save()
                self._client.update_labels(observation.agent_id, {"gwo.runtime_fenced": "true"})
            elif command is RuntimeCommand.RETIRE:
                self._call(["archive", observation.agent_id, "--force", "--json"])
            elif command is RuntimeCommand.PERMISSION_RESPONSE:
                return _RuntimeFailure(
                    "RUNTIME_PERMISSION_DECISION_REQUIRED", "Gateway has no exact permission decision payload"
                )
            return _CommandReceipt(stable_action_id, command)
        except Exception as error:
            return self._failure(error)

    def events(self, after_cursor: str | None) -> _RuntimeEventPage | _RuntimeFailure:
        try:
            start = 0 if after_cursor is None else int(after_cursor)
            if start < 0:
                raise ValueError("event cursor cannot be negative")
            changed = False
            for stable_action_id, record in sorted(self._actions.items()):
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
                if record.get("wake_state_digest") == state_digest:
                    continue
                record["wake_state_digest"] = state_digest
                self._events.append(
                    _RuntimeEvent(
                        cursor=str(len(self._events) + 1),
                        stable_action_id=stable_action_id,
                        kind=f"state:{observation.lifecycle}",
                    )
                )
                changed = True
            if changed:
                self._save()
            return _RuntimeEventPage(
                events=tuple(self._events[start:]),
                next_cursor=(None if not self._events else str(len(self._events))),
            )
        except (TypeError, ValueError):
            return _RuntimeFailure("RUNTIME_EVENT_CURSOR_INVALID", "event cursor is invalid")
        except Exception as error:
            return self._failure(error)


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
    ):
        self._store_path = Path(store_path)
        # Underscored parameters are internal/test composition hooks. Semantic
        # callers construct the default production Gateway through
        # build_runtime_gateway and never receive this Provider seam.
        self._adapter = _adapter
        self._configuration = configuration
        self._artifacts = _artifacts or ArtifactStore(
            self._store_path.parent / "runtime-artifacts"
        )
        self._data = self._load()

    # Caller interface operation 1.  It neither calls an adapter nor reserves
    # a slot, workspace, session, Agent, or provider action.
    def planning_preflight(
        self,
        subject: CampaignPlanningSubject,
        overrides: CampaignStartRuntimeOverrides | None = None,
    ) -> PlanningPreflightReceipt:
        if not isinstance(subject, CampaignPlanningSubject):
            raise RuntimeGatewayError(
                "RUNTIME_PREFLIGHT_SUBJECT_INVALID",
                "planning preflight accepts CampaignPlanningSubject only",
            )
        campaign = self._campaign(subject, overrides)
        assignment = self._resolve_assignment(
            subject.repository,
            RuntimeSelector.coordinator(),
            None,
            campaign["overrides"],
        )
        binding = {
            "subject_digest": subject.digest,
            "campaign_overrides_digest": digest_value(campaign["overrides"]),
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
        existing = self._data["preflights"].get(subject.stable_action_id)
        expected = {**binding, "receipt_digest": receipt_digest}
        if existing is not None and existing != expected:
            raise RuntimeGatewayError(
                "RUNTIME_PREFLIGHT_IDENTITY_MISMATCH",
                "stable planning action is already bound to another subject, options, or configuration",
            )
        if existing is None:
            self._data["preflights"][subject.stable_action_id] = expected
            self._save()
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
        if not isinstance(subject, (CampaignPlanningSubject, WorkRunSubject)):
            raise RuntimeGatewayError(
                "RUNTIME_SUBJECT_INVALID",
                "RuntimeGateway accepts only Campaign Planning and Plan-Revision Work Run subjects",
            )
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
        observation_or_failure = self._observe(subject.stable_action_id)
        if isinstance(observation_or_failure, _RuntimeFailure):
            if not observation_or_failure.authoritative_absence:
                self._raise_failure(observation_or_failure)
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
                # Acknowledge loss may follow a successful stage.  Only a
                # fresh authoritative observation can decide that fact.
                observation_or_failure = self._observe(subject.stable_action_id)
                if isinstance(observation_or_failure, _RuntimeFailure):
                    self._raise_failure(observation_or_failure)
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
        if not isinstance(command, (RuntimeCommand, PermissionResponse)):
            raise RuntimeGatewayError(
                "RUNTIME_COMMAND_INVALID", "Runtime command is outside the closed union"
            )
        if command is RuntimeCommand.PERMISSION_RESPONSE:
            raise RuntimeGatewayError(
                "RUNTIME_COMMAND_INVALID",
                "permission_response requires one exact PermissionResponse payload",
            )
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
        observation = self._command_with_readback(
            stable_action_id,
            command,
        )
        self._validate_bound_observation(subject, record, observation)
        self._record_observation(record, observation)
        return self._progress_receipt(subject, observation, command=command)

    def _campaign(
        self,
        subject: CampaignPlanningSubject,
        overrides: CampaignStartRuntimeOverrides | None,
    ) -> dict[str, Any]:
        existing = self._data["campaigns"].get(subject.campaign_handle)
        if existing is not None and overrides is None:
            if (
                existing.get("repository") != subject.repository
                or existing.get("campaign_key") != subject.campaign_key
            ):
                raise RuntimeGatewayError(
                    "RUNTIME_CAMPAIGN_IDENTITY_MISMATCH",
                    "Campaign handle was read back for another repository or Campaign key",
                )
            return existing
        overrides = overrides or CampaignStartRuntimeOverrides()
        value = {
            "repository": subject.repository,
            "campaign_key": subject.campaign_key,
            "overrides": overrides.canonical(),
        }
        if existing is not None and existing != value:
            raise RuntimeGatewayError(
                "RUNTIME_CAMPAIGN_IDENTITY_MISMATCH",
                "Campaign handle was read back with different Runtime overrides",
            )
        if existing is None:
            self._data["campaigns"][subject.campaign_handle] = value
            self._save()
        return value if existing is None else existing

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
        existing = self._data["actions"].get(subject.stable_action_id)
        if existing is not None:
            if existing.get("subject_digest") != subject.digest:
                raise RuntimeGatewayError(
                    "RUNTIME_ACTION_IDENTITY_MISMATCH",
                    "stable action was already bound to another Runtime subject",
                )
            return existing
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
            "observations": [],
        }
        self._data["actions"][subject.stable_action_id] = record
        self._save()
        return record

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
        if isinstance(
            result,
            (_PreparedRuntimeObservation, _BoundRuntimeObservation, _RuntimeFailure),
        ):
            return result
        return _RuntimeFailure(
            "RUNTIME_PROVIDER_PROTOCOL_INVALID",
            "Runtime provider observation result is invalid",
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
        record.update(
            {
                "binding_ref": observation.binding_ref,
                "lifecycle": observation.lifecycle,
                "planning_output_artifact_digest": getattr(
                    observation, "planning_output_artifact_digest", None
                ),
                "observation_digest": observation_digest,
            }
        )
        if observation_digest not in {
            item["digest"] for item in record["observations"]
        }:
            record["observations"].append(
                {"digest": observation_digest, "observation": canonical}
            )
        self._save()

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
            # A command acknowledgement may be lost after the Provider has
            # acted.  Readback is authoritative; reissuing start/resume could
            # launch a second semantic pass.
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

    @staticmethod
    def _validate_command_effect(
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
                isinstance(command, PermissionResponse)
                and all(
                    request.request_id != command.request_id
                    for request in observation.permission_requests
                )
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
        if not self._store_path.exists():
            return {"schema_version": 1, "campaigns": {}, "actions": {}, "preflights": {}}
        try:
            value = json.loads(self._store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "RuntimeGateway durable record is unreadable"
            ) from error
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or not all(isinstance(value.get(key), dict) for key in ("campaigns", "actions", "preflights"))
        ):
            raise RuntimeGatewayError(
                "RUNTIME_STORE_INVALID", "RuntimeGateway durable record has an unknown schema"
            )
        return value

    def _save(self) -> None:
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._store_path.with_suffix(self._store_path.suffix + ".tmp")
        temporary.write_bytes(canonical_bytes(self._data))
        temporary.replace(self._store_path)


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
            return _PrepareReceipt(spec.stable_action_id, existing.workspace_id)
        self.prepare_calls.append(spec.stable_action_id)
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
        )

    def command(
        self, stable_action_id: str, command: RuntimeTransition
    ) -> _CommandReceipt | _RuntimeFailure:
        if not isinstance(command, (RuntimeCommand, PermissionResponse)):
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
        if isinstance(command, PermissionResponse):
            matching = [
                request
                for request in action.pending_permissions
                if request[0] == command.request_id
            ]
            if len(matching) != 1:
                return _RuntimeFailure(
                    "RUNTIME_PERMISSION_REQUEST_UNKNOWN",
                    "permission response does not bind one exact pending request",
                )
            action.pending_permissions.remove(matching[0])
            if not action.pending_permissions and action.output_artifact_digest is not None:
                action.lifecycle = "completed"
        elif command is RuntimeCommand.PERMISSION_RESPONSE:
            return _RuntimeFailure(
                "RUNTIME_PERMISSION_DECISION_REQUIRED",
                "Gateway has no exact permission decision payload",
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
            action.binding_ref = f"binding:{digest_value({'stable_action_id': stable_action_id})[:24]}"
            self.created_agent_count += 1
            # A provider may be otherwise idle while an exact pending
            # permission keeps the semantic action active.  Match the
            # production normalization and expose a Bound ``running`` state
            # until the pending request is resolved.
            action.lifecycle = (
                "running" if action.pending_permissions else "completed"
            )
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
        elif command is RuntimeCommand.RESUME:
            if action.lifecycle != "parked" or action.fenced is not False:
                return _RuntimeFailure(
                    "RUNTIME_COMMAND_INVALID", "resume requires an unfenced parked binding"
                )
            action.lifecycle = "running"
        elif command is RuntimeCommand.PARK:
            action.lifecycle = "parked"
        elif command is RuntimeCommand.INTERRUPT:
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
            start = 0 if after_cursor is None else int(after_cursor)
            if start < 0:
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
                _RuntimeEvent(str(len(self._events) + 1), stable_action_id, f"state:{observation.lifecycle}")
            )
        events = tuple(self._events[start:])
        return _RuntimeEventPage(
            events=events,
            next_cursor=(None if not self._events else str(len(self._events))),
        )
