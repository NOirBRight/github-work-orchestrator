"""Host composition for the public PlanControl Campaign start boundary.

This module alone translates host-owned Campaign start assertions into
RuntimeGateway configuration.  PlanControl continues to see only its semantic
planning subject and opaque Gateway receipts, and PlanSpec never receives
these assignment facts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .plan_control import (
    CampaignHandle,
    CampaignSnapshotSource,
    PlanControl,
    PlanControlError,
    PlanControlRepository,
    _handle_ref,
    _install_start_host,
    _ready_refs,
)
from .runtime_gateway import (
    ArtifactStore,
    CampaignStartRuntimeOverrides,
    ProfileMapping,
    RuntimeConfiguration,
    RuntimeGatewayError,
    RuntimeRepositoryContext,
    build_runtime_gateway,
)
from ._canonical import digest_value


_GatewayBuilder = Callable[..., Any]


def _mapping(value: Any, label: str) -> ProfileMapping:
    if type(value) is not dict or set(value) != {
        "primary_profile_digest",
        "availability_fallback_profile_digest",
    }:
        raise PlanControlError(
            "START_OPTIONS_INVALID",
            f"{label} must contain one primary and one optional fallback digest",
        )
    try:
        return ProfileMapping(
            value["primary_profile_digest"],
            value["availability_fallback_profile_digest"],
        )
    except (TypeError, RuntimeGatewayError) as error:
        raise PlanControlError(
            "START_OPTIONS_INVALID",
            f"{label} contains an invalid profile mapping",
        ) from error


def _runtime_overrides(
    value: object,
    ticket_keys: tuple[str, ...],
) -> CampaignStartRuntimeOverrides:
    if type(value) is not dict or not set(value).issubset(
        {"coordinator", "ticket_overrides"}
    ):
        raise PlanControlError(
            "START_OPTIONS_INVALID",
            "start options allow only coordinator and ticket_overrides",
        )
    coordinator_value = value.get("coordinator")
    coordinator = (
        None
        if coordinator_value is None
        else _mapping(coordinator_value, "coordinator override")
    )
    raw_ticket_overrides = value.get("ticket_overrides", [])
    if type(raw_ticket_overrides) is not list:
        raise PlanControlError(
            "START_OPTIONS_INVALID",
            "ticket_overrides must be an exact list",
        )
    selected = set(ticket_keys)
    ticket_overrides: dict[tuple[str, str], ProfileMapping] = {}
    for item in raw_ticket_overrides:
        if type(item) is not dict or set(item) != {
            "ticket_key",
            "role",
            "mapping",
        }:
            raise PlanControlError(
                "START_OPTIONS_INVALID",
                "each Ticket override has an unknown schema",
            )
        ticket_key = item["ticket_key"]
        role = item["role"]
        if type(ticket_key) is not str or ticket_key not in selected:
            raise PlanControlError(
                "START_OPTIONS_INVALID",
                "Ticket override must name one selected Ticket",
            )
        if type(role) is not str or not role:
            raise PlanControlError(
                "START_OPTIONS_INVALID",
                "Ticket override role must be exact non-empty text",
            )
        key = (ticket_key, role)
        if key in ticket_overrides:
            raise PlanControlError(
                "START_OPTIONS_INVALID",
                "Ticket overrides repeat an exact Ticket and role",
            )
        ticket_overrides[key] = _mapping(
            item["mapping"],
            f"override for {ticket_key}/{role}",
        )
    try:
        return CampaignStartRuntimeOverrides(
            coordinator=coordinator,
            ticket_overrides=ticket_overrides,
        )
    except (TypeError, RuntimeGatewayError) as error:
        raise PlanControlError(
            "START_OPTIONS_INVALID",
            "Campaign start Runtime overrides are invalid",
        ) from error


def _assert_profiles_are_composed(
    overrides: CampaignStartRuntimeOverrides,
    configuration: RuntimeConfiguration,
) -> None:
    mappings = list(overrides.ticket_overrides.values())
    if overrides.coordinator is not None:
        mappings.append(overrides.coordinator)
    known = set(configuration.profiles)
    for mapping in mappings:
        if mapping.primary_profile_digest not in known or (
            mapping.availability_fallback_profile_digest is not None
            and mapping.availability_fallback_profile_digest not in known
        ):
            raise PlanControlError(
                "START_OPTIONS_INVALID",
                "Campaign start override names an uncomposed profile digest",
            )


def _production_gateway_builder(
    *,
    gateway_store_path: Path,
    configuration: RuntimeConfiguration,
    repository_contexts: Mapping[str, RuntimeRepositoryContext],
    artifacts: ArtifactStore,
) -> Any:
    return build_runtime_gateway(
        store_path=gateway_store_path,
        configuration=configuration,
        repository_contexts=repository_contexts,
        _shared_artifacts=artifacts,
    )


class ProductionPlanControlStartHost:
    """Concrete host composition with one shared #111 ArtifactStore."""

    def __init__(
        self,
        *,
        source: CampaignSnapshotSource,
        repository: PlanControlRepository,
        runtime_configuration: RuntimeConfiguration,
        repository_contexts: Mapping[str, RuntimeRepositoryContext],
        gateway_store_path: Path,
        artifact_root: Path,
        maximum_artifact_bytes: int = 1_048_576,
        max_snapshot_bytes: int = 1_048_576,
        _gateway_builder: _GatewayBuilder | None = None,
    ):
        if type(runtime_configuration) is not RuntimeConfiguration:
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "runtime_configuration must be one exact host snapshot",
            )
        self._source = source
        self._repository = repository
        self._configuration = runtime_configuration
        self._repository_contexts = dict(repository_contexts)
        self._gateway_store_path = Path(gateway_store_path)
        self._artifacts = ArtifactStore(
            Path(artifact_root),
            maximum_bytes=maximum_artifact_bytes,
        )
        self._max_snapshot_bytes = max_snapshot_bytes
        self._gateway_builder = _gateway_builder or _production_gateway_builder

    def start(
        self,
        repository: str,
        ready_refs: Sequence[str],
        options: object = None,
    ) -> CampaignHandle:
        refs = _ready_refs(ready_refs)
        if type(repository) is not str or not repository:
            raise PlanControlError(
                "PLAN_CONTROL_INVALID",
                "repository must be non-empty exact text",
            )
        campaign_key = "campaign:" + digest_value(
            {"repository": repository, "ready_refs": list(refs)}
        )[:24]
        handle = CampaignHandle(repository, campaign_key)

        persisted_value = self._repository.read_runtime_assertion(handle)
        persisted: CampaignStartRuntimeOverrides | None = None
        if persisted_value is not None:
            persisted = _runtime_overrides(persisted_value, refs)
            _assert_profiles_are_composed(persisted, self._configuration)
        if options is not None:
            requested = _runtime_overrides(options, refs)
            _assert_profiles_are_composed(requested, self._configuration)
            saved = self._repository.save_runtime_assertion(
                handle,
                requested.canonical(),
            )
            persisted = _runtime_overrides(saved, refs)

        assertions = dict(self._configuration.campaign_assertions)
        assertion_key = (
            handle.repository,
            handle.campaign_key,
            _handle_ref(handle),
        )
        if persisted is not None:
            configured = assertions.get(assertion_key)
            if configured is not None and configured != persisted:
                raise PlanControlError(
                    "START_OPTIONS_CONFLICT",
                    "Campaign assertion conflicts with host Runtime configuration",
                )
            assertions[assertion_key] = persisted
        try:
            configuration = RuntimeConfiguration(
                profiles=dict(self._configuration.profiles),
                host_mappings=dict(self._configuration.host_mappings),
                repository_mappings={
                    name: dict(mappings)
                    for name, mappings in self._configuration.repository_mappings.items()
                },
                campaign_assertions=assertions,
            )
            gateway = self._gateway_builder(
                gateway_store_path=self._gateway_store_path,
                configuration=configuration,
                repository_contexts=self._repository_contexts,
                artifacts=self._artifacts,
            )
        except PlanControlError:
            raise
        except (TypeError, RuntimeGatewayError, ValueError) as error:
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "RuntimeGateway host composition rejected the Campaign assertion",
            ) from error

        return PlanControl(
            source=self._source,
            artifacts=self._artifacts,
            gateway=gateway,
            repository=self._repository,
            max_snapshot_bytes=self._max_snapshot_bytes,
        ).start(
            repository,
            refs,
            campaign_key=campaign_key,
        )


def install_plan_control_start(
    *,
    source: CampaignSnapshotSource,
    repository: PlanControlRepository,
    runtime_configuration: RuntimeConfiguration,
    repository_contexts: Mapping[str, RuntimeRepositoryContext],
    gateway_store_path: Path,
    artifact_root: Path,
    maximum_artifact_bytes: int = 1_048_576,
    max_snapshot_bytes: int = 1_048_576,
    _gateway_builder: _GatewayBuilder | None = None,
) -> ProductionPlanControlStartHost:
    """Install the concrete public Campaign-start composition."""

    host = ProductionPlanControlStartHost(
        source=source,
        repository=repository,
        runtime_configuration=runtime_configuration,
        repository_contexts=repository_contexts,
        gateway_store_path=gateway_store_path,
        artifact_root=artifact_root,
        maximum_artifact_bytes=maximum_artifact_bytes,
        max_snapshot_bytes=max_snapshot_bytes,
        _gateway_builder=_gateway_builder,
    )
    _install_start_host(host)
    return host
