"""Shared deterministic support for Campaign Watchdog contract tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.campaign_watchdog import (  # noqa: E402
    CampaignWatchdog,
    WatchdogCampaignSnapshot,
    WatchdogWake,
    WatchdogWakePage,
)
from gwo_v8.execution_kernel import CampaignOutcome, CampaignStatus  # noqa: E402
from gwo_v8.plan_control import CampaignHandle  # noqa: E402


NOW = "2026-08-03T10:00:00+00:00"
TEST_HANDLE = CampaignHandle("owner/repo", "campaign:watchdog-test")


def handle() -> CampaignHandle:
    return TEST_HANDLE


def runtime_wake(
    cursor: str,
    source_identity: str,
    *,
    campaign: CampaignHandle = TEST_HANDLE,
) -> WatchdogWake:
    return WatchdogWake(cursor, campaign, "runtime", source_identity)


def wake(
    cursor: str,
    *,
    source: str = "runtime",
    source_identity: str = "semantic:issue:113",
    campaign: CampaignHandle = TEST_HANDLE,
) -> WatchdogWake:
    return WatchdogWake(cursor, campaign, source, source_identity)


def page(*events: WatchdogWake, next_cursor: str | None = None) -> WatchdogWakePage:
    return WatchdogWakePage(
        events=tuple(events),
        next_cursor=(events[-1].cursor if events and next_cursor is None else next_cursor),
    )


@dataclass
class RecordingWakeSource:
    pages: list[WatchdogWakePage]
    calls: list[str | None] = field(default_factory=list)

    def read(self, after_cursor: str | None) -> WatchdogWakePage:
        self.calls.append(after_cursor)
        if self.pages:
            return self.pages.pop(0)
        return WatchdogWakePage((), after_cursor)


@dataclass
class RecordingCampaignSource:
    snapshots: dict[CampaignHandle, WatchdogCampaignSnapshot]

    def active_campaigns(self) -> tuple[CampaignHandle, ...]:
        return tuple(sorted(self.snapshots, key=lambda item: (item.repository, item.campaign_key)))

    def watchdog_snapshot(self, campaign: CampaignHandle) -> WatchdogCampaignSnapshot:
        return self.snapshots[campaign]


@dataclass
class RecordingAdvancer:
    outcome: CampaignOutcome = CampaignOutcome(CampaignStatus.RUNNING, "TestRunning")
    calls: list[tuple[CampaignHandle, str | None]] = field(default_factory=list)

    def advance(self, campaign: CampaignHandle, wake_ref: str | None = None) -> CampaignOutcome:
        self.calls.append((campaign, wake_ref))
        return self.outcome


def make_snapshot(
    *,
    campaign: CampaignHandle = TEST_HANDLE,
    next_check_at: str | None = None,
    trusted_progress_digest: str = "1" * 64,
    status: CampaignStatus = CampaignStatus.RUNNING,
) -> WatchdogCampaignSnapshot:
    return WatchdogCampaignSnapshot(
        campaign=campaign,
        status=status,
        trusted_progress_digest=trusted_progress_digest,
        next_check_at=next_check_at,
        active_binding_ids=("binding:initial",),
        diagnosed_binding_ids=(),
        candidate_receipt_digests=(),
        last_wake_refs=(),
    )


def make_watchdog(
    tmp_path: Path,
    *,
    source: RecordingWakeSource,
    campaign_source: RecordingCampaignSource | None = None,
    advancer: RecordingAdvancer | None = None,
) -> CampaignWatchdog:
    return CampaignWatchdog(
        store_path=tmp_path / "watchdog.db",
        event_sources={"runtime_gateway": source},
        campaign_source=campaign_source or RecordingCampaignSource(
            {TEST_HANDLE: make_snapshot()}
        ),
        advancer=advancer or RecordingAdvancer(),
    )
