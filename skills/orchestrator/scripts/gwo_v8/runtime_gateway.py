"""The single V8 boundary for semantic Runtime materialization.

The gateway deliberately gives its callers no provider command, session, or
binding choreography.  A caller supplies one closed semantic subject and an
Artifact reference; the gateway reads back an existing action before staging
or starting it.  Provider adapters are private implementation details.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Mapping, Protocol

from ._canonical import canonical_bytes, digest_value
from .runtime import RuntimeProfile


_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_SPECIALIST_RE = re.compile(r"specialist:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_TICKET_ROLES = {
    "worker",
    "recovery_worker",
    "review_primary",
    "review_strong",
}
_LIFECYCLES = {"prepared", "running", "parked", "completed", "retired"}


class RuntimeGatewayError(RuntimeError):
    """A typed Gateway-owned configuration, identity, or transport failure."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeGatewayError(
            "RUNTIME_SUBJECT_INVALID", f"{field_name} must be a non-empty string"
        )
    return value


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
class RuntimeActionSpec:
    """Private provider input, always rendered from a closed Gateway subject."""

    stable_action_id: str
    subject: RuntimeSubject
    profile: RuntimeProfile
    prompt_artifact_digest: str

    @property
    def subject_digest(self) -> str:
        return self.subject.digest


@dataclass(frozen=True)
class PrepareReceipt:
    stable_action_id: str
    binding_ref: str


@dataclass(frozen=True)
class CommandReceipt:
    stable_action_id: str
    binding_ref: str
    command: RuntimeCommand


@dataclass(frozen=True)
class RuntimeObservation:
    """Authoritative Provider readback, not an event callback or local guess."""

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
    permission_requests: tuple[str, ...]
    fenced: bool
    authority_subtree_digest: str | None
    planning_output_artifact_digest: str | None = None


@dataclass(frozen=True)
class RuntimeEvent:
    cursor: str
    stable_action_id: str
    kind: str


@dataclass(frozen=True)
class RuntimeEventPage:
    events: tuple[RuntimeEvent, ...]
    next_cursor: str | None


class RuntimeProviderAdapter(Protocol):
    """The exact private seam shared by production and deterministic adapters."""

    def prepare(self, spec: RuntimeActionSpec) -> PrepareReceipt: ...

    def observe(self, stable_action_id: str) -> RuntimeObservation | None: ...

    def command(
        self, binding_ref: str, command: RuntimeCommand
    ) -> CommandReceipt: ...

    def events(self, after_cursor: str | None) -> RuntimeEventPage: ...


class _PaseoRuntimeClient(Protocol):
    """Private native Paseo bridge; its calls carry Artifact refs, never text."""

    def stage_runtime_action(self, spec: RuntimeActionSpec) -> PrepareReceipt: ...

    def observe_runtime_action(
        self, stable_action_id: str
    ) -> RuntimeObservation | None: ...

    def send_runtime_command(
        self, binding_ref: str, command: RuntimeCommand
    ) -> CommandReceipt: ...

    def runtime_events(self, after_cursor: str | None) -> RuntimeEventPage: ...


class PaseoRuntimeProviderAdapter:
    """Production Paseo adapter at the four-method Provider seam.

    The native client is intentionally private: it receives a staged
    ``RuntimeActionSpec`` whose complete semantic material remains in the
    referenced Artifact, and must provide a paused/staged lifecycle before a
    separate ``start`` command.  This adapter exposes no Paseo command to
    RuntimeGateway callers.
    """

    def __init__(self, client: _PaseoRuntimeClient):
        self._client = client

    def prepare(self, spec: RuntimeActionSpec) -> PrepareReceipt:
        return self._client.stage_runtime_action(spec)

    def observe(self, stable_action_id: str) -> RuntimeObservation | None:
        return self._client.observe_runtime_action(stable_action_id)

    def command(
        self, binding_ref: str, command: RuntimeCommand
    ) -> CommandReceipt:
        if not isinstance(command, RuntimeCommand):
            raise RuntimeGatewayError(
                "RUNTIME_COMMAND_INVALID", "Runtime command is outside the closed union"
            )
        return self._client.send_runtime_command(binding_ref, command)

    def events(self, after_cursor: str | None) -> RuntimeEventPage:
        return self._client.runtime_events(after_cursor)


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


@dataclass(frozen=True)
class PlanningReceipt(RuntimeProgressReceipt):
    planning_output_artifact_digest: str | None


@dataclass(frozen=True)
class RuntimeActionRecord:
    """Durable, inspectable private state reconstructed from Provider readback."""

    stable_action_id: str
    subject_digest: str
    selector: str
    configuration_source: str
    profile_digest: str
    availability_fallback_profile_digest: str | None
    fallback_selected: bool
    binding_ref: str | None
    lifecycle: str | None
    prompt_artifact_digest: str
    planning_output_artifact_digest: str | None
    observation_digest: str | None


class RuntimeGateway:
    """Own Runtime materialization; callers only preflight, progress, and read wakes."""

    def __init__(
        self,
        *,
        store_path: Path,
        adapter: RuntimeProviderAdapter,
        configuration: RuntimeConfiguration,
    ):
        self._store_path = Path(store_path)
        self._adapter = adapter
        self._configuration = configuration
        self._data = self._load()

    # Caller interface operation 1.  It neither calls an adapter nor reserves
    # a slot, workspace, session, Agent, or provider action.
    def planning_preflight(
        self,
        subject: CampaignPlanningSubject,
        overrides: CampaignStartRuntimeOverrides | None = None,
    ) -> PlanningPreflightReceipt:
        campaign = self._campaign(subject, overrides)
        assignment = self._resolve_assignment(
            subject.repository,
            RuntimeSelector.coordinator(),
            None,
            campaign["overrides"],
        )
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
        self._data["preflights"][subject.stable_action_id] = {
            "subject_digest": subject.digest,
            "receipt_digest": receipt_digest,
            "assignment": assignment,
        }
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
        record = self._assignment_for_progress(
            subject,
            None if not isinstance(subject, CampaignPlanningSubject) else persisted_preflight,
        )
        observation = self._adapter.observe(subject.stable_action_id)
        if observation is None:
            spec = RuntimeActionSpec(
                stable_action_id=subject.stable_action_id,
                subject=subject,
                profile=self._profile(record["profile_digest"]),
                prompt_artifact_digest=record["prompt_artifact_digest"],
            )
            try:
                self._adapter.prepare(spec)
            except RuntimeGatewayError:
                # Lost acknowledgements and process crashes are never a reason
                # to retry create.  One authoritative readback decides whether
                # the action exists; it is then progressed exactly once.
                observation = self._adapter.observe(subject.stable_action_id)
                if observation is None:
                    raise
            if observation is None:
                observation = self._adapter.observe(subject.stable_action_id)
        if observation is None:
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_UNAVAILABLE",
                "prepare did not produce an authoritative Runtime observation",
            )
        self._validate_observation(subject, record, observation)
        if (
            observation.lifecycle in {"running", "completed"}
            and record["lifecycle"] is None
        ):
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "Provider reported semantic execution before Gateway issued start or resume",
            )
        self._record_observation(record, observation)

        if observation.lifecycle == "prepared":
            observation = self._command_with_readback(
                subject.stable_action_id,
                observation.binding_ref,
                RuntimeCommand.START,
            )
            self._validate_observation(subject, record, observation)
            self._record_observation(record, observation)
        elif observation.lifecycle == "parked":
            observation = self._command_with_readback(
                subject.stable_action_id,
                observation.binding_ref,
                RuntimeCommand.RESUME,
            )
            self._validate_observation(subject, record, observation)
            self._record_observation(record, observation)
        elif observation.lifecycle not in {"running", "completed"}:
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                f"cannot progress Runtime lifecycle {observation.lifecycle}",
            )
        return self._progress_receipt(subject, observation)

    # Caller interface operation 3.  Events are wake hints only and carry no
    # binding facts; a caller must call progress for authoritative readback.
    def wake_hints(self, after_cursor: str | None) -> RuntimeEventPage:
        return self._adapter.events(after_cursor)

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

    def _validate_observation(
        self,
        subject: RuntimeSubject,
        record: Mapping[str, Any],
        observation: RuntimeObservation,
    ) -> None:
        expected_plan = (
            None
            if isinstance(subject, CampaignPlanningSubject)
            else subject.plan_revision_digest
        )
        expected_work = None if isinstance(subject, CampaignPlanningSubject) else subject.work_run_key
        expected_authority = (
            None
            if isinstance(subject, CampaignPlanningSubject)
            else subject.authority_subtree_digest
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
            and observation.prompt_accepted
            and bool(observation.binding_ref)
            and bool(observation.agent_id)
            and bool(observation.session_id)
            and bool(observation.workspace_id)
            and observation.lifecycle in _LIFECYCLES
        )
        if not values_match:
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_INVALID",
                "authoritative observation does not prove the complete Runtime binding",
            )

    def _record_observation(
        self, record: dict[str, Any], observation: RuntimeObservation
    ) -> None:
        canonical = asdict(observation)
        canonical["permission_requests"] = list(observation.permission_requests)
        observation_digest = digest_value(canonical)
        record.update(
            {
                "binding_ref": observation.binding_ref,
                "lifecycle": observation.lifecycle,
                "planning_output_artifact_digest": observation.planning_output_artifact_digest,
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

    def _require_observation(self, stable_action_id: str) -> RuntimeObservation:
        observation = self._adapter.observe(stable_action_id)
        if observation is None:
            raise RuntimeGatewayError(
                "RUNTIME_OBSERVATION_UNAVAILABLE",
                "Provider command acknowledgement lacked authoritative readback",
            )
        return observation

    def _command_with_readback(
        self,
        stable_action_id: str,
        binding_ref: str,
        command: RuntimeCommand,
    ) -> RuntimeObservation:
        try:
            self._adapter.command(binding_ref, command)
        except RuntimeGatewayError:
            # A command acknowledgement may be lost after the Provider has
            # acted.  Readback is authoritative; reissuing start/resume could
            # launch a second semantic pass.
            observation = self._adapter.observe(stable_action_id)
            if observation is None:
                raise
            return observation
        return self._require_observation(stable_action_id)

    def _progress_receipt(
        self, subject: RuntimeSubject, observation: RuntimeObservation
    ) -> RuntimeProgressReceipt:
        kind = "planning" if isinstance(subject, CampaignPlanningSubject) else "work_run"
        payload = {
            "kind": f"runtime_{kind}_receipt.v1",
            "subject_digest": subject.digest,
            "stable_action_id": subject.stable_action_id,
            "lifecycle": observation.lifecycle,
            "planning_output_artifact_digest": observation.planning_output_artifact_digest,
        }
        if isinstance(subject, CampaignPlanningSubject):
            if observation.lifecycle == "completed" and not observation.planning_output_artifact_digest:
                raise RuntimeGatewayError(
                    "RUNTIME_PLANNING_OUTPUT_MISSING",
                    "completed Planning Pass omitted its Artifact-backed output",
                )
            return PlanningReceipt(
                subject_digest=subject.digest,
                stable_action_id=subject.stable_action_id,
                status=observation.lifecycle,
                receipt_digest=digest_value(payload),
                planning_output_artifact_digest=observation.planning_output_artifact_digest,
            )
        return RuntimeProgressReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            status=observation.lifecycle,
            receipt_digest=digest_value(payload),
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


@dataclass
class _InMemoryAction:
    spec: RuntimeActionSpec
    binding_ref: str
    lifecycle: str = "prepared"
    fenced: bool = False
    output_artifact_digest: str | None = None


class InMemoryRuntimeProviderAdapter:
    """Deterministic adapter subject to the same strict Gateway conformance seam."""

    def __init__(
        self,
        *,
        lose_prepare_ack_once: bool = False,
        lose_command_ack_once: RuntimeCommand | None = None,
        initial_lifecycles: Mapping[str, str] | None = None,
    ):
        self._actions: dict[str, _InMemoryAction] = {}
        self._events: list[RuntimeEvent] = []
        self._lose_prepare_ack_once = lose_prepare_ack_once
        self._lose_command_ack_once = lose_command_ack_once
        self._initial_lifecycles = dict(initial_lifecycles or {})
        if any(value not in _LIFECYCLES for value in self._initial_lifecycles.values()):
            raise ValueError("initial_lifecycles contains an unknown lifecycle")
        self.prepare_calls: list[str] = []
        self.observe_calls: list[str] = []
        self.command_calls: list[tuple[str, str]] = []
        self.created_agent_count = 0
        self.staged_prompt_count = 0

    def prepare(self, spec: RuntimeActionSpec) -> PrepareReceipt:
        existing = self._actions.get(spec.stable_action_id)
        if existing is not None:
            if existing.spec.subject_digest != spec.subject_digest:
                raise RuntimeGatewayError(
                    "RUNTIME_ACTION_IDENTITY_MISMATCH", "prepare changed a stable action"
                )
            return PrepareReceipt(spec.stable_action_id, existing.binding_ref)
        self.prepare_calls.append(spec.stable_action_id)
        suffix = digest_value({"stable_action_id": spec.stable_action_id})[:24]
        action = _InMemoryAction(
            spec=spec,
            binding_ref=f"binding:{suffix}",
            lifecycle=self._initial_lifecycles.get(spec.stable_action_id, "prepared"),
        )
        self._actions[spec.stable_action_id] = action
        self.created_agent_count += 1
        self.staged_prompt_count += 1
        if self._lose_prepare_ack_once:
            self._lose_prepare_ack_once = False
            raise RuntimeGatewayError(
                "RUNTIME_PREPARE_ACK_LOST", "synthetic prepare acknowledgement loss"
            )
        return PrepareReceipt(spec.stable_action_id, action.binding_ref)

    def observe(self, stable_action_id: str) -> RuntimeObservation | None:
        self.observe_calls.append(stable_action_id)
        action = self._actions.get(stable_action_id)
        if action is None:
            return None
        subject = action.spec.subject
        return RuntimeObservation(
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
            workspace_id=f"workspace:{action.binding_ref}",
            prompt_artifact_digest=action.spec.prompt_artifact_digest,
            prompt_accepted=True,
            lifecycle=action.lifecycle,
            permission_requests=(),
            fenced=action.fenced,
            authority_subtree_digest=(
                None
                if isinstance(subject, CampaignPlanningSubject)
                else subject.authority_subtree_digest
            ),
            planning_output_artifact_digest=action.output_artifact_digest,
        )

    def command(
        self, binding_ref: str, command: RuntimeCommand
    ) -> CommandReceipt:
        if not isinstance(command, RuntimeCommand):
            raise RuntimeGatewayError(
                "RUNTIME_COMMAND_INVALID", "Runtime command is outside the closed union"
            )
        action = next(
            (item for item in self._actions.values() if item.binding_ref == binding_ref),
            None,
        )
        if action is None:
            raise RuntimeGatewayError("RUNTIME_BINDING_UNKNOWN", "unknown binding")
        self.command_calls.append((binding_ref, command.value))
        if command is RuntimeCommand.START:
            if action.lifecycle != "prepared":
                raise RuntimeGatewayError(
                    "RUNTIME_COMMAND_INVALID", "start requires a prepared binding"
                )
            action.lifecycle = "completed"
            if isinstance(action.spec.subject, CampaignPlanningSubject):
                action.output_artifact_digest = digest_value(
                    {
                        "kind": "planning_output.v1",
                        "subject_digest": action.spec.subject_digest,
                        "request_artifact_digest": action.spec.prompt_artifact_digest,
                    }
                )
        elif command is RuntimeCommand.RESUME:
            if action.lifecycle != "parked":
                raise RuntimeGatewayError(
                    "RUNTIME_COMMAND_INVALID", "resume requires a parked binding"
                )
            action.lifecycle = "running"
        elif command is RuntimeCommand.PARK:
            action.lifecycle = "parked"
        elif command is RuntimeCommand.FENCE:
            action.fenced = True
        elif command is RuntimeCommand.RETIRE:
            action.lifecycle = "retired"
        self._events.append(
            RuntimeEvent(
                cursor=str(len(self._events) + 1),
                stable_action_id=action.spec.stable_action_id,
                kind=command.value,
            )
        )
        if self._lose_command_ack_once is command:
            self._lose_command_ack_once = None
            raise RuntimeGatewayError(
                "RUNTIME_COMMAND_ACK_LOST",
                "synthetic Provider command acknowledgement loss",
            )
        return CommandReceipt(action.spec.stable_action_id, binding_ref, command)

    def events(self, after_cursor: str | None) -> RuntimeEventPage:
        start = 0 if after_cursor is None else int(after_cursor)
        events = tuple(self._events[start:])
        return RuntimeEventPage(
            events=events,
            next_cursor=(None if not self._events else str(len(self._events))),
        )
