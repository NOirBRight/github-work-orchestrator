"""Host composition for the public PlanControl Campaign start boundary.

This module alone translates host-owned Campaign start assertions into
RuntimeGateway configuration.  PlanControl continues to see only its semantic
planning subject and opaque Gateway receipts, and PlanSpec never receives
these assignment facts.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from .plan_control import (
    ActivePlanReadback,
    CampaignHandle,
    CampaignSnapshotSource,
    CampaignPlanningSubject,
    PlanControl,
    PlanControlError,
    PlanInvalidationClassification,
    PlanControlRepository,
    _PlanningAttempt,
    _handle_ref,
    _install_start_host,
    _ready_refs,
    _validate_preflight,
)
from .campaign_watchdog import (
    CampaignWatchdog,
    WatchdogEventSource,
    WatchdogWake,
    WatchdogWakePage,
)
from .runtime_gateway import (
    ArtifactStore,
    CampaignStartRuntimeOverrides,
    ProfileMapping,
    RuntimeConfiguration,
    RuntimeGatewayError,
    RuntimeGateway,
    RuntimeRepositoryContext,
    build_runtime_gateway,
)
from .cutover_guard import (
    CutoverGuard,
    CutoverGuardReceipt,
    CutoverGuardReport,
    CutoverGuardSources,
    CutoverSubject,
    DurableStateReadPort,
    LegacyReadPort,
    OwnershipReadPort,
    ProductionPathScanner,
    ReadOnlyPackageValidator,
    RuntimeConfigurationReader,
    WriterFenceReadPort,
)
from ._canonical import digest_bytes, digest_value, load_canonical_json
from .activation import GitHubCliContentClient, GitHubContentClient
from .github_snapshot import (
    GitHubCliIssueReadClient,
    GitHubCliHumanApprovalReadClient,
    GitHubHumanApprovalReadClient,
    GitHubIssueReadClient,
    GitHubReadySnapshotSource,
)
from .human_source import GitHubHumanApprovalSource
from .plan_control_github import (
    GitHubPlanRepository,
    WriterGenerationReadback,
    validate_github_plan_control_paths,
)
from .production_effects import ProductionCompositionError
from .planning_protocol import (
    PLANNING_OUTPUT_PROTOCOL_ID,
    REPLANNING_OUTPUT_PROTOCOL_ID,
    planning_prompt,
    replanning_prompt,
)

if TYPE_CHECKING:
    from .execution_kernel import (
        ExecutionKernel,
        ExecutionKernelConfiguration,
        WorkRunEffects,
    )
    from .production_host import PlanningContinuation


_GatewayBuilder = Callable[..., Any]


class RuntimeGatewayWatchdogEventSource:
    def __init__(self, gateway: Any) -> None:
        if not callable(getattr(gateway, "_read_watchdog_events", None)):
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "RuntimeGateway must expose private Watchdog event readback",
            )
        self._gateway = gateway

    def read(self, after_cursor: str | None) -> WatchdogWakePage:
        page = self._gateway._read_watchdog_events(after_cursor)
        return WatchdogWakePage(
            events=tuple(
                WatchdogWake(
                    cursor=event.cursor,
                    campaign=CampaignHandle(event.repository, event.campaign_key),
                    source=event.source,
                    source_identity=event.stable_action_id,
                )
                for event in page.events
            ),
            next_cursor=page.next_cursor,
        )


class _PlanControlGateway:
    """Forward the exact three-operation #111 caller surface to RuntimeGateway."""

    def __init__(
        self,
        *,
        gateway: Any,
    ):
        self._gateway = gateway

    def planning_preflight(self, subject):
        receipt = self._gateway.planning_preflight(subject)
        _validate_preflight(receipt, subject)
        return receipt

    def progress(self, subject, preflight):
        return self._gateway.progress(subject, preflight)

    def _read_coordinator_capability(self, subject):
        return self._gateway._read_coordinator_capability(subject)


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
    planning_effect_dispatch: object | None = None,
) -> Any:
    return build_runtime_gateway(
        store_path=gateway_store_path,
        configuration=configuration,
        repository_contexts=repository_contexts,
        _shared_artifacts=artifacts,
        _planning_effect_dispatch=planning_effect_dispatch,
    )


@dataclass(frozen=True)
class CutoverGuardRequest:
    subject: CutoverSubject
    package_root: Path
    install_roots: tuple[Path, Path, Path]


_FORBIDDEN_SOURCE_NAMES = frozenset(
    {
        "stop",
        "restore",
        "drain",
        "publish",
        "compare_and_swap",
        "write",
        "prepare",
        "command",
        "events",
        "install",
        "start",
    }
)


def _declared_surface(value: object) -> set[str]:
    names = set(vars(value)) if hasattr(value, "__dict__") else set()
    for cls in type(value).__mro__:
        names.update(vars(cls))
    return names


class _ReadOnlyGuardAdapter:
    def __init__(self, control: object, method_name: str) -> None:
        self._control = control
        self._method_name = method_name

    def read(self, *args: object, **kwargs: object) -> object:
        return getattr(self._control, self._method_name)(*args, **kwargs)


def _adapt_guard_read_port(
    value: object,
    label: str,
    method_names: tuple[str, ...],
) -> object:
    surface = _declared_surface(value)
    if "read" in surface and callable(getattr(value, "read", None)):
        if _FORBIDDEN_SOURCE_NAMES.intersection(surface):
            return _ReadOnlyGuardAdapter(value, "read")
        return value
    for method_name in method_names:
        if method_name in surface and callable(getattr(value, method_name, None)):
            return _ReadOnlyGuardAdapter(value, method_name)
    raise PlanControlError(
        "CUTOVER_GUARD_COMPOSITION_INVALID",
        f"{label} does not expose an exact read-only Guard adapter",
    )


class ProductionCutoverReadAdapterResolver:
    def __init__(
        self,
        *,
        legacy: LegacyReadPort,
        durable_state: DurableStateReadPort,
        writer_fence: WriterFenceReadPort,
        ownership: OwnershipReadPort,
        runtime_configuration: RuntimeConfiguration,
    ) -> None:
        self._legacy = _adapt_guard_read_port(
            legacy,
            "legacy",
            ("read", "readback"),
        )
        self._durable_state = _adapt_guard_read_port(
            durable_state,
            "durable_state",
            ("read", "readback"),
        )
        self._writer_fence = _adapt_guard_read_port(
            writer_fence,
            "writer_fence",
            ("read", "readback", "read_current"),
        )
        self._ownership = _adapt_guard_read_port(
            ownership,
            "ownership",
            ("read", "readback"),
        )
        self._runtime_configuration = runtime_configuration

    def resolve(
        self,
        *,
        subject: CutoverSubject,
        package_root: Path,
        install_roots: tuple[Path, Path, Path],
    ) -> CutoverGuardSources:
        surfaces = dict(zip(subject.install_surfaces, install_roots, strict=True))
        return CutoverGuardSources(
            legacy=self._legacy,
            durable_state=self._durable_state,
            writer_fence=self._writer_fence,
            ownership=self._ownership,
            compatibility=ProductionPathScanner(package_root),
            runtime=RuntimeConfigurationReader(self._runtime_configuration),
            packages=ReadOnlyPackageValidator(package_root, surfaces),
        )


def make_production_cutover_read_adapter_resolver(
    *,
    v61_legacy_read: LegacyReadPort,
    v8_store_read: DurableStateReadPort,
    writer_generation_read: WriterFenceReadPort,
    v8_ownership_read: OwnershipReadPort,
    runtime_configuration: RuntimeConfiguration,
) -> ProductionCutoverReadAdapterResolver:
    """Bind the four already-composed V3 reads to the Guard resolver."""

    return ProductionCutoverReadAdapterResolver(
        legacy=v61_legacy_read,
        durable_state=v8_store_read,
        writer_fence=writer_generation_read,
        ownership=v8_ownership_read,
        runtime_configuration=runtime_configuration,
    )


class ProductionCutoverGuardHost:
    def __init__(
        self,
        *,
        guard: CutoverGuard,
        sources: CutoverGuardSources,
    ) -> None:
        self._guard = guard
        self._sources = sources

    def check(self, subject: CutoverSubject) -> CutoverGuardReport:
        return self._guard.evaluate(subject)

    def validate_activation(
        self,
        subject: CutoverSubject,
        receipt: CutoverGuardReceipt,
    ) -> None:
        self._guard.validate_activation_token(subject, receipt)


def install_cutover_guard(*, sources: CutoverGuardSources) -> ProductionCutoverGuardHost:
    if type(sources) is not CutoverGuardSources:
        raise PlanControlError(
            "CUTOVER_GUARD_COMPOSITION_INVALID",
            "Guard composition must use one exact source value",
        )
    for field in fields(CutoverGuardSources):
        source = getattr(sources, field.name)
        surface = _declared_surface(source)
        if "read" not in surface or _FORBIDDEN_SOURCE_NAMES.intersection(surface):
            raise PlanControlError(
                "CUTOVER_GUARD_COMPOSITION_INVALID",
                f"{field.name} is not a read-only Guard adapter",
            )
        if not callable(getattr(source, "read", None)):
            raise PlanControlError(
                "CUTOVER_GUARD_COMPOSITION_INVALID",
                f"{field.name}.read is not callable",
            )
    return ProductionCutoverGuardHost(
        guard=CutoverGuard(sources),
        sources=sources,
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
        human_source: object | None = None,
        _gateway_builder: _GatewayBuilder | None = None,
        cutover_read_adapter_resolver: ProductionCutoverReadAdapterResolver | None = None,
    ):
        if type(cutover_read_adapter_resolver) is not ProductionCutoverReadAdapterResolver:
            raise PlanControlError(
                "CUTOVER_GUARD_COMPOSITION_INVALID",
                "the start host requires one resolver-backed V3 read composition",
            )
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
        if human_source is not None and not callable(
            getattr(human_source, "read", None)
        ):
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "human_source must expose the read-only human approval source",
            )
        self._human_source = human_source
        self._gateway_builder = _gateway_builder or _production_gateway_builder
        self._cutover_read_adapter_resolver = cutover_read_adapter_resolver

    def resolve_cutover_guard_sources(
        self,
        request: CutoverGuardRequest,
    ) -> CutoverGuardSources:
        resolver = getattr(self, "_cutover_read_adapter_resolver", None)
        if type(resolver) is not ProductionCutoverReadAdapterResolver:
            raise PlanControlError(
                "CUTOVER_GUARD_COMPOSITION_INVALID",
                "the installed start host has no exact Guard resolver",
            )
        return resolver.resolve(
            subject=request.subject,
            package_root=request.package_root,
            install_roots=request.install_roots,
        )

    def install_cutover_guard(
        self,
        *,
        sources: CutoverGuardSources,
    ) -> ProductionCutoverGuardHost:
        return install_cutover_guard(sources=sources)

    def _control_for(
        self,
        *,
        handle: CampaignHandle,
        assertion: CampaignStartRuntimeOverrides | None,
        configuration: RuntimeConfiguration,
    ) -> PlanControl:
        try:
            effect_dispatch_factory = getattr(
                self._repository,
                "planning_effect_dispatch",
                None,
            )
            gateway = self._gateway_builder(
                gateway_store_path=self._gateway_store_path,
                configuration=configuration,
                repository_contexts=self._repository_contexts,
                artifacts=self._artifacts,
                planning_effect_dispatch=(
                    effect_dispatch_factory()
                    if callable(effect_dispatch_factory)
                    else None
                ),
            )
        except PlanControlError:
            raise
        except (TypeError, RuntimeGatewayError, ValueError) as error:
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "RuntimeGateway host composition rejected the Campaign assertion",
            ) from error
        control = PlanControl(
            source=self._source,
            artifacts=self._artifacts,
            gateway=_PlanControlGateway(
                gateway=gateway,
            ),
            repository=self._repository,
            max_snapshot_bytes=self._max_snapshot_bytes,
        )
        if self._human_source is not None:
            # Keep the source as a host-owned read-only adapter.  PlanControl
            # never receives a writer or a mutable source callback.
            control._human_source = self._human_source
        return control

    def _runtime_configuration_for(
        self,
        handle: CampaignHandle,
        assertion: CampaignStartRuntimeOverrides | None,
    ) -> RuntimeConfiguration:
        assertions = dict(self._configuration.campaign_assertions)
        assertion_key = (
            handle.repository,
            handle.campaign_key,
            _handle_ref(handle),
        )
        if assertion is not None:
            assertions[assertion_key] = assertion
        return RuntimeConfiguration(
            profiles=dict(self._configuration.profiles),
            host_mappings=dict(self._configuration.host_mappings),
            repository_mappings={
                name: dict(mappings)
                for name, mappings in self._configuration.repository_mappings.items()
            },
            campaign_assertions=assertions,
        )

    def _existing_control(self, handle: CampaignHandle) -> PlanControl:
        """Recompose one existing Campaign without replacing its assertion."""

        assertion_key = (
            handle.repository,
            handle.campaign_key,
            _handle_ref(handle),
        )
        assertion = self._configuration.campaign_assertions.get(assertion_key)
        return self._control_for(
            handle=handle,
            assertion=assertion,
            configuration=self._runtime_configuration_for(handle, assertion),
        )

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

        requested: CampaignStartRuntimeOverrides | None = None
        if options is not None:
            requested = _runtime_overrides(options, refs)
            _assert_profiles_are_composed(requested, self._configuration)

        assertion_key = (
            handle.repository,
            handle.campaign_key,
            _handle_ref(handle),
        )
        configured = self._configuration.campaign_assertions.get(assertion_key)
        selected_assertion = requested or configured
        if configured is not None and configured != selected_assertion:
            raise PlanControlError(
                "START_OPTIONS_CONFLICT",
                "Campaign assertion conflicts with host Runtime configuration",
            )
        return self._control_for(
            handle=handle,
            assertion=selected_assertion,
            configuration=self._runtime_configuration_for(
                handle,
                selected_assertion,
            ),
        ).start(
            repository,
            refs,
            campaign_key=campaign_key,
        )

    def start_successor(
        self,
        handle: CampaignHandle,
        ready_refs: Sequence[str],
        *,
        expected_previous_revision_digest: str,
    ) -> CampaignHandle:
        """Publish one successor Revision through the installed host boundary.

        Runtime overrides are intentionally absent: the existing Campaign's
        durable #111 assertion is recovered rather than replaced.
        """

        if type(handle) is not CampaignHandle:
            raise PlanControlError(
                "START_SUCCESSOR_INVALID",
                "successor start requires the exact existing CampaignHandle",
            )
        fixed_repository = getattr(self._repository, "repository", None)
        if (
            type(fixed_repository) is str
            and fixed_repository
            and handle.repository != fixed_repository
        ):
            raise PlanControlError(
                "START_SUCCESSOR_INVALID",
                "successor CampaignHandle belongs to another configured repository",
            )
        if (
            type(expected_previous_revision_digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", expected_previous_revision_digest)
            is None
        ):
            raise PlanControlError(
                "START_SUCCESSOR_INVALID",
                "successor start requires the exact previous Plan Revision digest",
            )
        # Observe and prove the exact predecessor before hydration, source
        # reads, Artifact writes, #111 preflight, or a PlanControl mutation.
        observe = getattr(self._repository, "observe_campaign", None)
        if callable(observe):
            observation = observe(handle, expected_previous_revision_digest)
            hydrate = getattr(self._repository, "hydrate_campaign_artifacts", None)
            if callable(hydrate):
                hydrate(self._artifacts, observation)
        else:
            active = self._repository.active_receipt(handle)
            replay = (
                None
                if active is None
                else self._repository.read_attempt(
                    handle,
                    expected_previous_revision_digest,
                )
            )
            if active is None or (
                active.revision_digest != expected_previous_revision_digest
                and (
                    replay is None
                    or getattr(replay, "revision", None) is None
                    or active.expected_previous_revision_digest
                    != expected_previous_revision_digest
                    or replay.revision.digest != active.revision_digest
                )
            ):
                raise PlanControlError(
                    "ACTIVATION_CAS_CONFLICT",
                    "successor start requires the exact existing active Campaign receipt",
                )
        raw_refs = _ready_refs(ready_refs)
        canonicalizer = getattr(self._source, "canonical_ready_refs", None)
        refs = (
            _ready_refs(canonicalizer(handle.repository, raw_refs))
            if callable(canonicalizer)
            else raw_refs
        )
        assertion_key = (
            handle.repository,
            handle.campaign_key,
            _handle_ref(handle),
        )
        configured = self._configuration.campaign_assertions.get(assertion_key)
        # RuntimeGateway reuses/authenticates its immutable initial assertion
        # by Campaign identity.  Never parse its old Ticket overrides against
        # a successor's changed selected set or persist them in PlanControl.
        assertion = configured
        return self._control_for(
            handle=handle,
            assertion=assertion,
            configuration=self._runtime_configuration_for(handle, assertion),
        ).start(
            handle.repository,
            refs,
            campaign_key=handle.campaign_key,
            expected_previous_revision_digest=expected_previous_revision_digest,
        )

    def read_active(self, handle: CampaignHandle) -> ActivePlanReadback:
        """Expose the sole #110 active-reader seam for V3 execution.

        This recreates only the exact PlanControl composition needed to verify
        #109's immutable Plan Revision, Activation Receipt, and Ticket claims.
        It performs no Planning, publication, claim, or Runtime effect.
        """

        if type(handle) is not CampaignHandle:
            raise PlanControlError(
                "ACTIVE_READBACK_INVALID",
                "active readback requires the exact CampaignHandle",
            )
        fixed_repository = getattr(self._repository, "repository", None)
        if (
            type(fixed_repository) is str
            and fixed_repository
            and handle.repository != fixed_repository
        ):
            raise PlanControlError(
                "ACTIVE_READBACK_INVALID",
                "CampaignHandle belongs to another configured repository",
            )
        return self._existing_control(handle).read_active(handle)

    def runtime_gateway_for(self, handle: CampaignHandle) -> RuntimeGateway:
        """Return the composed Gateway for one exact Campaign identity."""

        assertion_key = (
            handle.repository,
            handle.campaign_key,
            _handle_ref(handle),
        )
        assertion = self._configuration.campaign_assertions.get(assertion_key)
        effect_dispatch_factory = getattr(
            self._repository,
            "planning_effect_dispatch",
            None,
        )
        try:
            gateway = self._gateway_builder(
                gateway_store_path=self._gateway_store_path,
                configuration=self._runtime_configuration_for(handle, assertion),
                repository_contexts=self._repository_contexts,
                artifacts=self._artifacts,
                planning_effect_dispatch=(
                    effect_dispatch_factory()
                    if callable(effect_dispatch_factory)
                    else None
                ),
            )
        except (TypeError, RuntimeGatewayError, ValueError) as error:
            raise ProductionCompositionError(
                "PLAN_CONTROL_RUNTIME_GATEWAY_INVALID",
                "the host Runtime factory returned no exact RuntimeGateway",
            ) from error
        if not isinstance(gateway, RuntimeGateway):
            raise ProductionCompositionError(
                "PLAN_CONTROL_RUNTIME_GATEWAY_INVALID",
                "the host Runtime factory returned no exact RuntimeGateway",
            )
        return gateway

    def _read_planning_attempt(
        self,
        handle: CampaignHandle,
    ) -> _PlanningAttempt | None:
        active = self._repository.active_receipt(handle)
        expected = None if active is None else active.revision_digest
        attempt = self._repository.read_attempt(handle, expected)
        if attempt is None:
            return None
        if type(attempt) is not _PlanningAttempt or attempt.handle != handle:
            raise ProductionCompositionError(
                "PLANNING_CONTINUATION_INVALID",
                "the persisted planning attempt has the wrong Campaign identity",
            )
        if attempt.expected_previous_revision_digest != expected:
            raise ProductionCompositionError(
                "PLANNING_CONTINUATION_INVALID",
                "the persisted planning attempt has the wrong predecessor identity",
            )
        if attempt.revision is not None:
            # A Plan Revision can be durable before Activation publishes its
            # receipt.  When the active receipt is absent this is still the
            # same initial Planning continuation and must be recoverable.
            if attempt.compilation_record_artifact_digest is None:
                raise ProductionCompositionError(
                    "PLANNING_CONTINUATION_INVALID",
                    "the persisted Plan Revision has no compilation record",
                )
            if expected is not None:
                return None
        # This seam is only the initial Planning continuation.  Successor
        # replanning has its own explicit invalidation/Decision boundary and
        # must never be resumed as if it were the first Planning pass.
        if (
            attempt.expected_previous_revision_digest is not None
            or attempt.planning_protocol_id != PLANNING_OUTPUT_PROTOCOL_ID
        ):
            return None
        if (
            type(attempt.ready_refs) is not tuple
            or type(attempt.snapshot_bytes) is not bytes
            or type(attempt.subject) is not CampaignPlanningSubject
            or attempt.subject.repository != handle.repository
            or attempt.subject.campaign_key != handle.campaign_key
            or attempt.subject.campaign_handle != _handle_ref(handle)
            or attempt.subject.expected_previous_plan_revision_digest != expected
            or attempt.subject.snapshot_artifact_digest
            != attempt.snapshot_artifact_digest
            or attempt.subject.planning_request_artifact_digest
            != attempt.planning_request_artifact_digest
            or type(attempt.subject.stable_action_id) is not str
            or not attempt.subject.stable_action_id
        ):
            raise ProductionCompositionError(
                "PLANNING_CONTINUATION_INVALID",
                "the persisted planning attempt has a changed subject identity",
            )
        expected_action = "planning:" + digest_value(
            {
                "handle": handle.__dict__,
                "snapshot_digest": attempt.snapshot_artifact_digest,
                "policy_witness_digest": attempt.policy_witness_digest,
                "expected_previous_revision_digest": expected,
            }
        )
        if attempt.planning_protocol_id == PLANNING_OUTPUT_PROTOCOL_ID:
            action_valid = attempt.subject.stable_action_id == expected_action
        elif attempt.planning_protocol_id == REPLANNING_OUTPUT_PROTOCOL_ID:
            action_valid = attempt.subject.stable_action_id.startswith("replan:")
        else:
            action_valid = False
        if not action_valid:
            raise ProductionCompositionError(
                "PLANNING_CONTINUATION_INVALID",
                "the persisted planning attempt has a changed stable action",
            )
        try:
            if digest_bytes(attempt.snapshot_bytes) != attempt.snapshot_artifact_digest:
                raise ValueError("snapshot digest")
            snapshot = load_canonical_json(attempt.snapshot_bytes)
            if type(snapshot) is not dict:
                raise ValueError("snapshot")
            if self._artifacts.read_bytes(attempt.snapshot_artifact_digest) != attempt.snapshot_bytes:
                raise ValueError("snapshot artifact")
            policy_key = (
                "policy"
                if attempt.planning_protocol_id == PLANNING_OUTPUT_PROTOCOL_ID
                else "policy_witness"
            )
            policy = snapshot.get(policy_key)
            if type(policy) is not dict:
                raise ValueError("policy witness")
            witness = {key: value for key, value in policy.items() if key != "digest"}
            if digest_value(witness) != attempt.policy_witness_digest:
                raise ValueError("policy witness digest")
            if load_canonical_json(
                self._artifacts.read_bytes(attempt.policy_witness_digest)
            ) != witness:
                raise ValueError("policy witness artifact")
            request = load_canonical_json(
                self._artifacts.read_bytes(attempt.planning_request_artifact_digest)
            )
            prompt_builder = (
                planning_prompt
                if attempt.planning_protocol_id == PLANNING_OUTPUT_PROTOCOL_ID
                else replanning_prompt
            )
            expected_request = prompt_builder(
                subject_digest=attempt.subject.prompt_binding_digest,
                authority_digest=attempt.policy_witness_digest,
                snapshot_artifact_digest=attempt.snapshot_artifact_digest,
                policy_witness_artifact_digest=attempt.policy_witness_digest,
            )
            if request != expected_request:
                raise ValueError("planning request artifact")
            tickets = snapshot.get("tickets")
            if type(tickets) is not list:
                raise ValueError("tickets")
            source_refs = tuple(
                sorted(ticket["source"]["ref"] for ticket in tickets)
            )
            if attempt.ready_refs != source_refs:
                raise ValueError("ready-ref snapshot binding")
        except (
            AttributeError,
            KeyError,
            IndexError,
            RuntimeGatewayError,
            OSError,
            TypeError,
            ValueError,
        ) as error:
            raise ProductionCompositionError(
                "PLANNING_CONTINUATION_INVALID",
                "the persisted planning attempt artifacts do not read back exactly",
            ) from error
        return attempt

    def read_planning_continuation(
        self,
        handle: CampaignHandle,
    ) -> PlanningContinuation | None:
        from .production_host import PlanningContinuation

        attempt = self._read_planning_attempt(handle)
        if attempt is None:
            return None
        return PlanningContinuation(
            campaign=handle,
            ready_refs=attempt.ready_refs,
            expected_previous_revision_digest=(
                attempt.expected_previous_revision_digest
            ),
            snapshot_artifact_digest=attempt.snapshot_artifact_digest,
            planning_request_artifact_digest=(
                attempt.planning_request_artifact_digest
            ),
            stable_action_id=attempt.subject.stable_action_id,
            compilation_record_artifact_digest=(
                attempt.compilation_record_artifact_digest
            ),
        )

    def continue_start(
        self,
        handle: CampaignHandle,
        ready_refs: Sequence[str],
    ) -> CampaignHandle:
        continuation = self.read_planning_continuation(handle)
        raw_refs = _ready_refs(ready_refs)
        canonicalizer = getattr(self._source, "canonical_ready_refs", None)
        refs = (
            _ready_refs(canonicalizer(handle.repository, raw_refs))
            if callable(canonicalizer)
            else raw_refs
        )
        if continuation is None or refs != continuation.ready_refs:
            raise ProductionCompositionError(
                "PLANNING_CONTINUATION_MISMATCH",
                "a wake must resume the exact persisted ready-ref tuple",
            )
        return self._existing_control(handle).start(
            handle.repository,
            refs,
            campaign_key=handle.campaign_key,
        )

    def read_active_or_none(
        self,
        handle: CampaignHandle,
    ) -> ActivePlanReadback | None:
        if self.read_planning_continuation(handle) is not None:
            return None
        return self.read_active(handle)

    def classify_plan_invalidations(
        self,
        handle: CampaignHandle,
        invalidations: Sequence[object],
        execution_snapshot: Mapping[str, Any],
    ) -> PlanInvalidationClassification | None:
        return self._existing_control(handle).classify_plan_invalidations(
            handle,
            invalidations,
            execution_snapshot,
        )

    def require_human_decision(self, handle, classification):
        return self._existing_control(handle).require_human_decision(
            handle,
            classification,
        )

    def advance_human_decision(self, handle, decision, choice):
        return self._existing_control(handle).advance_human_decision(
            handle,
            decision,
            choice,
        )

    def read_human_decision_source(self, handle, decision, choice):
        """Read the authoritative human source without performing a mutation.

        ExecutionKernel uses this explicit host seam before it persists any
        approved successor intent.  Keeping the read operation separate from
        ``advance_human_decision`` prevents a caller from accidentally
        treating an unverified approval as permission to plan or activate.
        """

        return self._existing_control(handle).read_human_decision_source(
            handle,
            decision,
            choice,
        )

    def read_replan_budget_policy(self, handle):
        """Read the active Policy Witness replan budget without mutation."""

        return self._existing_control(handle).read_replan_budget_policy(handle)

    def read_human_gate_attempt(self, handle, decision_id, source_readback_digest):
        return self._existing_control(handle).read_human_gate_attempt(
            handle,
            decision_id,
            source_readback_digest,
        )

    def save_human_gate_attempt(self, attempt):
        return self._existing_control(attempt.campaign).save_human_gate_attempt(attempt)

    def activate_successor(
        self,
        handle: CampaignHandle,
        classification: PlanInvalidationClassification,
    ) -> ActivePlanReadback:
        return self._existing_control(handle).activate_successor(
            handle,
            classification,
        )

    def install_execution_kernel(
        self,
        *,
        store_path: Path,
        effects: "WorkRunEffects",
        configuration: "ExecutionKernelConfiguration | None" = None,
    ) -> "ExecutionKernel":
        """Compose the V3 public path as ``start`` then ``advance/inspect``.

        Only the typed effect port crosses this host boundary.  The Kernel
        receives this host solely as the #109 active reader; it cannot call a
        legacy driver, PlanControl mutation, Runtime provider, or V2 decoder.
        """

        from .execution_kernel import install_execution_kernel

        return install_execution_kernel(
            store_path=store_path,
            plan_control=self,
            effects=effects,
            configuration=configuration,
        )

    def install_campaign_watchdog(
        self,
        *,
        store_path: Path,
        execution_kernel: ExecutionKernel,
        hosted_check_source: WatchdogEventSource | None = None,
        _runtime_event_source: WatchdogEventSource | None = None,
    ) -> CampaignWatchdog:
        from .execution_kernel import ExecutionKernel

        if (
            type(execution_kernel) is not ExecutionKernel
            or execution_kernel._plan_control is not self
        ):
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "execution_kernel must be installed by this exact host",
            )
        for label, source in (
            ("runtime_gateway", _runtime_event_source),
            ("hosted_check", hosted_check_source),
        ):
            if source is not None and not callable(getattr(source, "read", None)):
                raise PlanControlError(
                    "PLAN_CONTROL_COMPOSITION_INVALID",
                    f"{label} source must expose read(after_cursor)",
                )

        runtime_source = _runtime_event_source
        if runtime_source is None:
            effect_dispatch_factory = getattr(
                self._repository,
                "planning_effect_dispatch",
                None,
            )
            try:
                gateway = self._gateway_builder(
                    gateway_store_path=self._gateway_store_path,
                    configuration=self._configuration,
                    repository_contexts=self._repository_contexts,
                    artifacts=self._artifacts,
                    planning_effect_dispatch=(
                        effect_dispatch_factory()
                        if callable(effect_dispatch_factory)
                        else None
                    ),
                )
            except (TypeError, RuntimeGatewayError, ValueError) as error:
                raise PlanControlError(
                    "PLAN_CONTROL_COMPOSITION_INVALID",
                    "RuntimeGateway Watchdog composition failed",
                ) from error
            runtime_source = RuntimeGatewayWatchdogEventSource(gateway)

        sources: dict[str, WatchdogEventSource] = {
            "runtime_gateway": runtime_source,
        }
        if hosted_check_source is not None:
            sources["hosted_check"] = hosted_check_source
        watchdog = CampaignWatchdog(
            store_path=store_path,
            event_sources=sources,
            campaign_source=execution_kernel,
            advancer=execution_kernel,
        )
        watchdog.rebuild_due_queue()
        return watchdog


def production_start_host_constructor(
    *,
    source: CampaignSnapshotSource,
    repository: PlanControlRepository,
    runtime_configuration: RuntimeConfiguration,
    repository_contexts: Mapping[str, RuntimeRepositoryContext],
    gateway_store_path: Path,
    artifact_root: Path,
    maximum_artifact_bytes: int,
    max_snapshot_bytes: int,
    human_source: object | None,
    gateway_builder: object | None,
    cutover_read_adapter_resolver: ProductionCutoverReadAdapterResolver,
) -> ProductionPlanControlStartHost:
    host = ProductionPlanControlStartHost(
        source=source,
        repository=repository,
        runtime_configuration=runtime_configuration,
        repository_contexts=repository_contexts,
        gateway_store_path=gateway_store_path,
        artifact_root=artifact_root,
        maximum_artifact_bytes=maximum_artifact_bytes,
        max_snapshot_bytes=max_snapshot_bytes,
        human_source=human_source,
        _gateway_builder=gateway_builder,
        cutover_read_adapter_resolver=cutover_read_adapter_resolver,
    )
    return host


def install_production_start_host(
    *,
    source: CampaignSnapshotSource,
    repository: PlanControlRepository,
    runtime_configuration: RuntimeConfiguration,
    repository_contexts: Mapping[str, RuntimeRepositoryContext],
    gateway_store_path: Path,
    artifact_root: Path,
    maximum_artifact_bytes: int,
    max_snapshot_bytes: int,
    human_source: object | None,
    gateway_builder: object | None,
    v61_legacy_read: LegacyReadPort,
    v8_store_read: DurableStateReadPort,
    writer_generation_read: WriterFenceReadPort,
    v8_ownership_read: OwnershipReadPort,
) -> ProductionPlanControlStartHost:
    resolver = make_production_cutover_read_adapter_resolver(
        v61_legacy_read=v61_legacy_read,
        v8_store_read=v8_store_read,
        writer_generation_read=writer_generation_read,
        v8_ownership_read=v8_ownership_read,
        runtime_configuration=runtime_configuration,
    )
    return production_start_host_constructor(
        source=source,
        repository=repository,
        runtime_configuration=runtime_configuration,
        repository_contexts=repository_contexts,
        gateway_store_path=gateway_store_path,
        artifact_root=artifact_root,
        maximum_artifact_bytes=maximum_artifact_bytes,
        max_snapshot_bytes=max_snapshot_bytes,
        human_source=human_source,
        gateway_builder=gateway_builder,
        cutover_read_adapter_resolver=resolver,
    )


def _installed_v3_start_host() -> ProductionPlanControlStartHost:
    from .plan_control import _default_start_host

    host = _default_start_host
    if type(host) is not ProductionPlanControlStartHost:
        raise PlanControlError(
            "CUTOVER_GUARD_COMPOSITION_INVALID",
            "the installed default host is not the composed V3 start host",
        )
    return host


def load_production_cutover_guard(
    request: CutoverGuardRequest,
) -> ProductionCutoverGuardHost:
    if type(request) is not CutoverGuardRequest:
        raise PlanControlError(
            "CUTOVER_GUARD_COMPOSITION_INVALID",
            "the live Guard request must be one exact CutoverGuardRequest",
        )
    labels = tuple(path.parent.name for path in request.install_roots)
    if labels != (".agents", ".codex", ".claude"):
        raise PlanControlError(
            "CUTOVER_GUARD_COMPOSITION_INVALID",
            "live install roots must be .agents, .codex, .claude in order",
        )
    start_host = _installed_v3_start_host()
    sources = start_host.resolve_cutover_guard_sources(request)
    return start_host.install_cutover_guard(sources=sources)


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
    human_source: object | None = None,
    _gateway_builder: _GatewayBuilder | None = None,
    cutover_read_adapter_resolver: ProductionCutoverReadAdapterResolver | None = None,
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
        human_source=human_source,
        _gateway_builder=_gateway_builder,
        cutover_read_adapter_resolver=cutover_read_adapter_resolver,
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
    _human_approval_client: GitHubHumanApprovalReadClient | None = None,
    _writer_control: WriterGenerationReadback | None = None,
    _gateway_builder: _GatewayBuilder | None = None,
    cutover_read_adapter_resolver: ProductionCutoverReadAdapterResolver | None = None,
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
    validate_github_plan_control_paths(
        policy_path=policy_path,
        state_path=state_path,
        object_prefix=".gwo-v8/plan-control-v3/objects",
        writer_control_path=".gwo-v8/writer-transition.json",
    )
    content_client = _content_client or GitHubCliContentClient()
    issue_client = _issue_client or GitHubCliIssueReadClient()
    approval_client = (
        _human_approval_client or GitHubCliHumanApprovalReadClient()
    )
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
    human_source = GitHubHumanApprovalSource(
        approval_client=approval_client,
        content_client=content_client,
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
        human_source=human_source,
        _gateway_builder=_gateway_builder,
        cutover_read_adapter_resolver=cutover_read_adapter_resolver,
    )
