"""Host composition for the public PlanControl Campaign start boundary.

This module alone translates host-owned Campaign start assertions into
RuntimeGateway configuration.  PlanControl continues to see only its semantic
planning subject and opaque Gateway receipts, and PlanSpec never receives
these assignment facts.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from .plan_control import (
    ActivePlanReadback,
    CampaignHandle,
    CampaignSnapshotSource,
    PlanControl,
    PlanControlError,
    PlanInvalidationClassification,
    PlanControlRepository,
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
    RuntimeRepositoryContext,
    build_runtime_gateway,
)
from ._canonical import digest_value
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

if TYPE_CHECKING:
    from .execution_kernel import (
        ExecutionKernel,
        ExecutionKernelConfiguration,
        WorkRunEffects,
    )


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
        if human_source is not None and not callable(
            getattr(human_source, "read", None)
        ):
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "human_source must expose the read-only human approval source",
            )
        self._human_source = human_source
        self._gateway_builder = _gateway_builder or _production_gateway_builder

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
    )
