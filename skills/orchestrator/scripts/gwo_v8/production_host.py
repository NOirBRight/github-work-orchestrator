"""Beta2 isolated-preview composition for the V8 public Campaign host."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, Sequence

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
from .production_effects import ProductionCompositionError, ProductionWorkRunEffects


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


class WriterGenerationReader(Protocol):
    def read(self) -> str: ...


class ProductionGwoHost:
    def __init__(
        self,
        *,
        start_host: ProductionPlanControlStartHost,
        kernel: ExecutionKernel,
        watchdog: CampaignWatchdog,
        writer_generation_reader: WriterGenerationReader,
        target_path: Path,
    ) -> None:
        self._start_host = start_host
        self._kernel = kernel
        self._watchdog = watchdog
        self._writer_generation_reader = writer_generation_reader
        self._target_path = target_path.resolve()

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
        writer_generation_reader.read()
        kernel = start_host.install_execution_kernel(
            store_path=store_path,
            effects=effects,
            configuration=configuration,
        )
        return cls(
            start_host=start_host,
            kernel=kernel,
            watchdog=watchdog,
            writer_generation_reader=writer_generation_reader,
            target_path=target,
        )

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
        return self._kernel.advance(campaign_handle, wake_ref)

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
        return self._kernel.watchdog_snapshot(campaign_handle)

    def run_watchdog_once(self, now: str) -> tuple[CampaignOutcome, ...]:
        return self._watchdog.run_once(now)


__all__ = [
    "PlanningContinuation",
    "ProductionGwoHost",
    "ProductionHostConfiguration",
    "WriterGenerationReader",
]
