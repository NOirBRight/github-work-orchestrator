"""Beta2 isolated-preview composition for the V8 public Campaign host."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Literal, Protocol, Sequence

from .campaign_watchdog import CampaignWatchdog, WatchdogCampaignSnapshot
from .execution_kernel import (
    CampaignOutcome,
    CampaignStatus,
    Diagnostics,
    ExecutionKernel,
    ExecutionKernelConfiguration,
)
from .plan_control import CampaignHandle
from .plan_control_host import ProductionPlanControlStartHost
from .production_effects import (
    ProductionCompositionError,
    ProductionWorkRunEffects,
    _ProductionReplayDeferred,
)


@dataclass(frozen=True)
class PlanningContinuation:
    campaign: CampaignHandle
    ready_refs: tuple[str, ...]
    expected_previous_revision_digest: str | None
    snapshot_artifact_digest: str
    planning_request_artifact_digest: str
    stable_action_id: str
    compilation_record_artifact_digest: str | None


@dataclass(frozen=True)
class ProductionHostConfiguration:
    worker_slots: int = 4
    batch_member_limit: int = 4
    preview_mode: Literal["beta2_isolated_preview"] = "beta2_isolated_preview"
    target_isolation_root: Path | None = None
    writer_activation_enabled: Literal[False] = False
    fault_admission_mode: Literal["named_canary"] | None = None
    approved_run_root: Path | None = None
    fault_plan_path: Path | None = None
    fault_journal_path: Path | None = None


class WriterGenerationReader(Protocol):
    def read(self) -> str: ...


_IMMEDIATE_BATCH_RECOVERY_DUE_AT = "1970-01-01T00:00:00+00:00"


class _ForwardingWatchdogAdvancer:
    def __init__(self, host: "ProductionGwoHost") -> None:
        self._host = host

    def advance(
        self,
        handle: CampaignHandle,
        wake_ref: str | None = None,
    ) -> CampaignOutcome:
        return self._host.advance(handle, wake_ref)


def _is_predecessor_batch_integrator(value: object) -> bool:
    value_type = type(value)
    return (
        value_type.__name__ == "GitIntegrationBatchAssembler"
        and value_type.__module__ == "gwo_v8.integration_batch"
    )


def _validate_batch_integrator(value: object | None) -> None:
    if value is None:
        return
    if _is_predecessor_batch_integrator(value):
        raise ProductionCompositionError(
            "PRODUCTION_PREDECESSOR_PATH_REJECTED",
            "the predecessor GitIntegrationBatchAssembler is not a V8 production port",
        )
    if any(
        not callable(getattr(value, method, None))
        for method in ("prepare", "readback", "execute")
    ):
        raise ProductionCompositionError(
            "PRODUCTION_COMPOSITION_INPUT_INVALID",
            "the production Batch integrator must expose prepare, readback, and execute",
        )


class ProductionGwoHost:
    def __init__(
        self,
        *,
        start_host: ProductionPlanControlStartHost,
        kernel: ExecutionKernel,
        watchdog: CampaignWatchdog,
        writer_generation_reader: WriterGenerationReader,
        target_path: Path,
        effects: object | None = None,
        fault_plan_path: Path | None = None,
        journal_path: Path | None = None,
        worker_command: Callable[[object], object] | None = None,
        review_command: Callable[[object], object] | None = None,
        delivery_command: Callable[[object], object] | None = None,
        wake_command: Callable[[object], object] | None = None,
        permission_command: Callable[[object], object] | None = None,
        runtime_command: Callable[[object], object] | None = None,
    ) -> None:
        self._start_host = start_host
        self._kernel = kernel
        self._watchdog = watchdog
        self._effects = effects
        self._writer_generation_reader = writer_generation_reader
        self._target_path = target_path.resolve()
        self._fault_plan_path = (
            None if fault_plan_path is None else Path(fault_plan_path).resolve()
        )
        self._journal_path = (
            None if journal_path is None else Path(journal_path).resolve()
        )
        self.worker_command = worker_command
        self.review_command = review_command
        self.delivery_command = delivery_command
        self.wake_command = wake_command
        self.permission_command = permission_command
        self.runtime_command = runtime_command

    @property
    def fault_plan_path(self) -> Path | None:
        return self._fault_plan_path

    @property
    def journal_path(self) -> Path | None:
        return self._journal_path

    @classmethod
    def install(
        cls,
        *,
        start_host: ProductionPlanControlStartHost,
        store_path: Path,
        effects: ProductionWorkRunEffects,
        configuration: ExecutionKernelConfiguration | None,
        host_configuration: ProductionHostConfiguration,
        target_path: Path,
        watchdog_store_path: Path,
        watchdog: CampaignWatchdog,
        writer_generation_reader: WriterGenerationReader,
        batch_integrator: object | None = None,
        fault_admission_mode: Literal["named_canary"] | None = None,
        admission_mode: Literal["named_canary"] | None = None,
        approved_run_root: Path | None = None,
        fault_plan_path: Path | None = None,
        journal_path: Path | None = None,
        fault_journal_path: Path | None = None,
        worker_command: Callable[[object], object] | None = None,
        review_command: Callable[[object], object] | None = None,
        delivery_command: Callable[[object], object] | None = None,
        wake_command: Callable[[object], object] | None = None,
        permission_command: Callable[[object], object] | None = None,
        runtime_command: Callable[[object], object] | None = None,
    ) -> "ProductionGwoHost":
        root = host_configuration.target_isolation_root
        try:
            root_path = None if root is None else root.resolve()
            target = target_path.resolve()
        except (AttributeError, OSError, TypeError) as error:
            raise ProductionCompositionError(
                "V8_ISOLATED_PREVIEW_REQUIRED",
                "target is not an isolated Beta2 target",
            ) from error
        if (
            host_configuration.preview_mode != "beta2_isolated_preview"
            or host_configuration.writer_activation_enabled is not False
            or root_path is None
            or target == root_path
            or root_path not in target.parents
        ):
            raise ProductionCompositionError(
                "V8_ISOLATED_PREVIEW_REQUIRED",
                "target is not an isolated Beta2 target",
            )
        configured_batch_integrator = (
            batch_integrator
            if batch_integrator is not None
            else getattr(effects, "_batch_integrator", None)
        )
        _validate_batch_integrator(configured_batch_integrator)
        if batch_integrator is not None:
            effects._batch_integrator = batch_integrator
        configured_fault_mode = (
            fault_admission_mode
            if fault_admission_mode is not None
            else admission_mode
            if admission_mode is not None
            else host_configuration.fault_admission_mode
        )
        configured_run_root = (
            approved_run_root
            if approved_run_root is not None
            else host_configuration.approved_run_root
        )
        configured_fault_plan = (
            fault_plan_path
            if fault_plan_path is not None
            else host_configuration.fault_plan_path
        )
        configured_journal = (
            journal_path
            if journal_path is not None
            else fault_journal_path
            if fault_journal_path is not None
            else host_configuration.fault_journal_path
        )
        writer_generation_reader.read()
        kernel = start_host.install_execution_kernel(
            store_path=store_path,
            effects=effects,
            configuration=configuration,
        )
        host = cls(
            start_host=start_host,
            kernel=kernel,
            watchdog=watchdog,
            effects=effects,
            writer_generation_reader=writer_generation_reader,
            target_path=target,
            fault_plan_path=None,
            journal_path=None,
            worker_command=worker_command,
            review_command=review_command,
            delivery_command=delivery_command,
            wake_command=wake_command,
            permission_command=permission_command,
            runtime_command=runtime_command,
        )
        host._bind_fault_proxy(
            admission_mode=configured_fault_mode,
            approved_run_root=configured_run_root,
            fault_plan_path=configured_fault_plan,
            journal_path=configured_journal,
        )
        bind_advancer = getattr(watchdog, "bind_advancer", None)
        if callable(bind_advancer):
            bind_advancer(_ForwardingWatchdogAdvancer(host))
        elif getattr(watchdog, "_advancer", None) is kernel:
            watchdog._advancer = _ForwardingWatchdogAdvancer(host)
        return host

    def _bind_fault_proxy(
        self,
        *,
        admission_mode: Literal["named_canary"] | None,
        approved_run_root: Path | None,
        fault_plan_path: Path | None,
        journal_path: Path | None,
    ) -> None:
        if (
            admission_mode is None
            and approved_run_root is None
            and fault_plan_path is None
            and journal_path is None
        ):
            return
        if admission_mode != "named_canary" or approved_run_root is None or fault_plan_path is None:
            raise ProductionCompositionError(
                "ROOT_CANARY_FAULT_CONFIGURATION_INVALID",
                "named Canary fault injection requires an approved run root and plan",
            )
        try:
            from scripts.v8_root_canary_fault_proxy import FaultProxy, _require_child
        except ModuleNotFoundError:
            from v8_root_canary_fault_proxy import FaultProxy, _require_child

        try:
            plan_path = _require_child(Path(fault_plan_path), Path(approved_run_root))
            proxy_journal_path = _require_child(
                Path(journal_path)
                if journal_path is not None
                else Path(approved_run_root) / "fault-proxy-journal.json",
                Path(approved_run_root),
            )
        except (OSError, TypeError, ValueError) as error:
            code = (
                str(error)
                if str(error) == "ROOT_CANARY_FAULT_PATH_OUTSIDE_RUN_ROOT"
                else "ROOT_CANARY_FAULT_PATH_INVALID"
            )
            raise ProductionCompositionError(code, "fault plan and journal must be under the run root") from error

        try:
            proxy = FaultProxy.from_files(
                plan_path,
                proxy_journal_path,
                run_root=Path(approved_run_root),
            )
        except ValueError as error:
            raise ProductionCompositionError(
                "ROOT_CANARY_FAULT_PATH_INVALID",
                "fault plan or journal durable record is invalid",
            ) from error
        self._fault_plan_path = plan_path
        self._journal_path = proxy_journal_path
        bind_effect_proxy = getattr(self._effects, "bind_fault_proxy", None)
        if callable(bind_effect_proxy):
            bind_effect_proxy(proxy)
        for role, name in (
            ("worker", "worker_command"),
            ("review", "review_command"),
            ("delivery", "delivery_command"),
            ("wake", "wake_command"),
            ("permission", "permission_command"),
            ("runtime", "runtime_command"),
        ):
            command = getattr(self, name)
            if command is None:
                continue

            def invoke(request: object, *, _role=role, _command=command):
                effective = replace(request, role=_role)
                return proxy.execute(
                    effective,
                    run_command=lambda _argv: _command(effective),
                )

            setattr(self, name, invoke)

    def start(
        self,
        repository: str,
        ready_refs: Sequence[str],
        options: object = None,
    ) -> CampaignHandle:
        return self._start_host.start(repository, tuple(ready_refs), options)

    def advance(
        self,
        campaign_handle: CampaignHandle,
        wake_ref: str | None = None,
    ) -> CampaignOutcome:
        continuation = self._start_host.read_planning_continuation(campaign_handle)
        if (
            continuation is not None
            and self._start_host.read_active_or_none(campaign_handle) is None
        ):
            if wake_ref is None:
                return CampaignOutcome(
                    CampaignStatus.WAIT,
                    "PlanningContinuationPending",
                )
            self._start_host.continue_start(
                campaign_handle,
                continuation.ready_refs,
            )
            if self._start_host.read_active_or_none(campaign_handle) is None:
                return CampaignOutcome(
                    CampaignStatus.WAIT,
                    "PlanningContinuationPending",
                )
        begin_public_advance = getattr(self._effects, "_begin_public_advance", None)
        if callable(begin_public_advance):
            begin_public_advance()
        end_public_advance = getattr(self._effects, "_end_public_advance", None)
        try:
            return self._kernel.advance(campaign_handle, wake_ref)
        except _ProductionReplayDeferred:
            diagnostics = self._kernel.inspect(campaign_handle)
            return CampaignOutcome(diagnostics.status, diagnostics.reason)
        finally:
            if callable(end_public_advance):
                end_public_advance()

    def inspect(self, campaign_handle: CampaignHandle) -> Diagnostics:
        continuation = self._start_host.read_planning_continuation(campaign_handle)
        if (
            continuation is not None
            and self._start_host.read_active_or_none(campaign_handle) is None
        ):
            return Diagnostics(
                campaign=campaign_handle,
                status=CampaignStatus.WAIT,
                reason="PlanningContinuationPending",
                plan_revision_digest="",
                worker_slots={},
                work_runs=(),
                outstanding_effect_ids=(continuation.stable_action_id,),
            )
        return self._kernel.inspect(campaign_handle)

    def watchdog_snapshot(
        self,
        campaign_handle: CampaignHandle,
    ) -> WatchdogCampaignSnapshot:
        snapshot = self._kernel.watchdog_snapshot(campaign_handle)
        if (
            snapshot.next_check_at is not None
            or not snapshot.candidate_receipt_digests
        ):
            return snapshot
        diagnostics = self._kernel.inspect(campaign_handle)
        if (
            snapshot.status is not CampaignStatus.COMPLETE
            and diagnostics.outstanding_effect_ids
            and any(
                run.phase == "accepted_awaiting_delivery"
                and run.slot_held
                and run.accepted_candidate_receipt_digest is not None
                and run.candidate_diff_record_digest is not None
                for run in diagnostics.work_runs
            )
        ):
            return replace(
                snapshot,
                next_check_at=_IMMEDIATE_BATCH_RECOVERY_DUE_AT,
            )
        return snapshot

    def run_watchdog_once(self, now: str) -> tuple[CampaignOutcome, ...]:
        return self._watchdog.run_once(now)


__all__ = [
    "PlanningContinuation",
    "ProductionGwoHost",
    "ProductionHostConfiguration",
    "WriterGenerationReader",
]
