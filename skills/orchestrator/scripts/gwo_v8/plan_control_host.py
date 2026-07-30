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
    _validate_preflight,
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
from .activation import GitHubCliContentClient, GitHubContentClient
from .github_snapshot import (
    GitHubCliIssueReadClient,
    GitHubIssueReadClient,
    GitHubReadySnapshotSource,
)
from .plan_control_github import (
    GitHubPlanRepository,
    WriterGenerationReadback,
)


_GatewayBuilder = Callable[..., Any]


class _RuntimeAssertionCommitGateway:
    """Commit the PlanControl mirror only after exact #111 preflight."""

    def __init__(
        self,
        *,
        gateway: Any,
        repository: PlanControlRepository,
        handle: CampaignHandle,
        assertion: CampaignStartRuntimeOverrides | None,
    ):
        self._gateway = gateway
        self._repository = repository
        self._handle = handle
        self._assertion = None if assertion is None else assertion.canonical()

    def planning_preflight(self, subject):
        receipt = self._gateway.planning_preflight(subject)
        _validate_preflight(receipt, subject)
        recovered = getattr(self._gateway, "_campaign_start_assertion", None)
        if callable(recovered):
            try:
                authoritative = recovered(
                    self._handle.repository,
                    self._handle.campaign_key,
                    _handle_ref(self._handle),
                )
            except RuntimeGatewayError as error:
                raise PlanControlError(
                    "RUNTIME_PREFLIGHT_INVALID",
                    "RuntimeGateway could not recover its durable Campaign assertion",
                ) from error
            if type(authoritative) is not CampaignStartRuntimeOverrides:
                raise PlanControlError(
                    "RUNTIME_PREFLIGHT_INVALID",
                    "RuntimeGateway preflight omitted its durable Campaign assertion",
                )
            assertion = authoritative.canonical()
        elif self._assertion is not None:
            # Boundary doubles implement the narrow #111 caller surface only.
            # Production RuntimeGateway always supplies the recovery seam.
            assertion = self._assertion
        else:
            assertion = CampaignStartRuntimeOverrides().canonical()
        if self._assertion is not None and assertion != self._assertion:
            raise PlanControlError(
                "START_OPTIONS_CONFLICT",
                "RuntimeGateway durable assertion conflicts with Campaign start input",
            )
        saved = self._repository.save_runtime_assertion(
            self._handle,
            assertion,
        )
        if saved != assertion:
            raise PlanControlError(
                "START_OPTIONS_CONFLICT",
                "PlanControl Runtime assertion mirror did not read back exactly",
            )
        return receipt

    def progress(self, subject, preflight):
        return self._gateway.progress(subject, preflight)


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
        hydrate = getattr(repository, "_hydrate_artifacts", None)
        if callable(hydrate):
            # Production GitHub PlanControl derives this cache from immutable
            # governed objects.  A replacement host never trusts a prior
            # machine's artifact directory as its durability source.
            hydrate(self._artifacts)
        self._max_snapshot_bytes = max_snapshot_bytes
        self._gateway_builder = _gateway_builder or _production_gateway_builder

    def start(
        self,
        repository: str,
        ready_refs: Sequence[str],
        options: object = None,
    ) -> CampaignHandle:
        if type(repository) is not str or not repository:
            raise PlanControlError(
                "PLAN_CONTROL_INVALID",
                "repository must be non-empty exact text",
            )
        raw_refs = _ready_refs(ready_refs)
        canonicalizer = getattr(self._source, "canonical_ready_refs", None)
        refs = (
            _ready_refs(canonicalizer(repository, raw_refs))
            if callable(canonicalizer)
            else raw_refs
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
        requested: CampaignStartRuntimeOverrides | None = None
        if options is not None:
            requested = _runtime_overrides(options, refs)
            _assert_profiles_are_composed(requested, self._configuration)

        assertions = dict(self._configuration.campaign_assertions)
        assertion_key = (
            handle.repository,
            handle.campaign_key,
            _handle_ref(handle),
        )
        configured = assertions.get(assertion_key)
        selected_assertion = requested or persisted or configured
        if configured is not None and configured != selected_assertion:
            raise PlanControlError(
                "START_OPTIONS_CONFLICT",
                "Campaign assertion conflicts with host Runtime configuration",
            )
        if selected_assertion is not None:
            assertions[assertion_key] = selected_assertion
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
            gateway=_RuntimeAssertionCommitGateway(
                gateway=gateway,
                repository=self._repository,
                handle=handle,
                assertion=selected_assertion,
            ),
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


def install_github_plan_control_start(
    *,
    repository: str,
    control_branch: str,
    target_branch: str,
    writer_generation: str,
    runtime_configuration: RuntimeConfiguration,
    repository_contexts: Mapping[str, RuntimeRepositoryContext],
    gateway_store_path: Path,
    artifact_root: Path,
    policy_path: str = ".gwo-v8/policy-witness.json",
    state_path: str = ".gwo-v8/plan-control-v3.json",
    maximum_artifact_bytes: int = 1_048_576,
    maximum_state_bytes: int = 16_777_216,
    max_snapshot_bytes: int = 1_048_576,
    _content_client: GitHubContentClient | None = None,
    _issue_client: GitHubIssueReadClient | None = None,
    _writer_control: WriterGenerationReadback | None = None,
    _gateway_builder: _GatewayBuilder | None = None,
) -> ProductionPlanControlStartHost:
    """Install the production GitHub-backed Campaign-start composition.

    The public production entrypoint constructs both semantic source readback
    and the complete durable PlanControl repository.  Underscored seams exist
    only for boundary tests; normal callers receive no source or persistence
    adapter choices.
    """

    if type(repository) is not str or not repository:
        raise PlanControlError(
            "PLAN_CONTROL_COMPOSITION_INVALID",
            "Production GitHub repository must be exact non-empty text",
        )
    content_client = _content_client or GitHubCliContentClient()
    issue_client = _issue_client or GitHubCliIssueReadClient()
    # A production writer must already be represented by an exact durable
    # Writer Record on the control ref.  Do not manufacture ``initial-writer``
    # from host configuration: that would make PlanControl authority a local
    # guess and reopen a cross-path TOCTOU window.  The underscored reader is
    # retained solely for in-memory boundary doubles which cannot model refs.
    if _writer_control is None and not all(
        callable(getattr(content_client, name, None))
        for name in ("read_ref", "read_at_ref", "compare_and_swap_ref")
    ):
        raise PlanControlError(
            "PLAN_CONTROL_COMPOSITION_INVALID",
            "Production GitHub PlanControl requires exact control-ref CAS and a durable Writer Record",
        )
    source = GitHubReadySnapshotSource(
        content_client=content_client,
        issue_client=issue_client,
        control_branch=control_branch,
        target_branch=target_branch,
        policy_path=policy_path,
    )
    durable_repository = GitHubPlanRepository(
        content_client,
        repository=repository,
        branch=control_branch,
        writer_generation=writer_generation,
        writer_control=_writer_control,
        path=state_path,
        maximum_state_bytes=maximum_state_bytes,
    )
    return install_plan_control_start(
        source=source,
        repository=durable_repository,
        runtime_configuration=runtime_configuration,
        repository_contexts=repository_contexts,
        gateway_store_path=gateway_store_path,
        artifact_root=artifact_root,
        maximum_artifact_bytes=maximum_artifact_bytes,
        max_snapshot_bytes=max_snapshot_bytes,
        _gateway_builder=_gateway_builder,
    )
