# GWO V8 Campaign Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Issue #113 so Runtime, Candidate, Review, hosted-check, and persisted due-time hints reliably wake the one `ExecutionKernel.advance` path without LLM polling or a second lifecycle authority.

**Architecture:** Add a rebuildable CampaignWatchdog adapter with a small event-source/schedule-source/advancer interface. It persists only event cursors and a derived due queue, reconstructs that queue from read-only ExecutionKernel diagnostics, treats callbacks as hints, and delegates every transition to `advance`; RuntimeGateway and later BatchIntegrator provide host-private wake sources.

**Tech Stack:** Python 3.13, frozen dataclasses, SQLite derived queue, ExecutionKernel Diagnostics, RuntimeGateway host-private event readback, pytest.

## Global Constraints

- Issue #113 and ADR-0053/ADR-0054/ADR-0058/ADR-0059 are the acceptance source; the integrated architecture wins on conflict.
- CampaignWatchdog owns no Work Run phase, Slot, claim, Candidate, Result, retry budget, permission decision, or delivery state.
- It exposes no public workflow operation. `rebuild_due_queue()` and `run_once(now)` are host-adapter methods, not package workflow APIs.
- Every wake calls the installed `advance(handle, wake_ref)` function; no callback handler mutates ExecutionKernel storage directly.
- Event payloads are never trusted progress. A stale deadline resets only from a new durable Kernel transition, authoritative normalized permission observation, or exact persisted Candidate receipt.
- Restart reconstructs due work; duplicate, reordered, unavailable, and lost callbacks cannot duplicate effects or leave a non-terminal Campaign asleep indefinitely.
- Stale diagnosis runs after the configured thirty-minute default only after zero-LLM Runtime/process/workspace/Campaign readback, and at most once per exact Runtime Binding.
- Candidate Assurance Task 1 lands first as a small shared foundation: it defines `candidate_gate.CandidateReceipt`, adds `WorkRunObservation.candidate_receipt`, and makes ExecutionKernel persist and exact-read that canonical receipt before changing phase. #113 starts from that merged foundation; it consumes the receipt and does not recreate or modify CandidateGate.
- All changes follow RED-GREEN-REFACTOR and use a clean worktree from merged #110/#112.
- Package-changing work uses one serialized manifest lane. Every such commit
  must run `scripts/sync_orchestrator.py`, then
  `scripts/sync_orchestrator.py --check`, and stage the resulting
  `skills/orchestrator/.skill-package.json` in that same commit. Disjoint Python
  files do not make #113 and the remaining #114 implementation parallel-safe
  because both regenerate that manifest.

---

## File and Responsibility Map

| File | Responsibility |
| --- | --- |
| `skills/orchestrator/scripts/gwo_v8/campaign_watchdog.py` | Wake records, source protocols, SQLite cursor/due queue, rebuild and one-step wake loop |
| `skills/orchestrator/scripts/gwo_v8/execution_kernel.py` | Pure read-only watchdog snapshot and trusted-progress identity |
| `skills/orchestrator/scripts/gwo_v8/runtime_gateway.py` | One host-private, cursor-based Campaign wake projection over the existing provider `events` readback; no fourth public operation |
| `skills/orchestrator/scripts/gwo_v8/plan_control_host.py` | Host-private registration/composition; no new public workflow API |
| `tests/v8_watchdog_test_support.py` | Recording event sources, clock, advancer, restart helpers |
| `tests/test_v8_campaign_watchdog.py` | Queue, cursor, duplicate/lost/reordered callback tests |
| `tests/test_v8_watchdog_execution_kernel.py` | Read-only diagnostics, trusted progress, stale diagnosis and public-advance integration |
| `tests/test_v8_watchdog_production_host.py` | Runtime wake source and restart composition acceptance |

## Interfaces

```python
from datetime import datetime
import sqlite3


@dataclass(frozen=True)
class WatchdogWake:
    cursor: str
    campaign: CampaignHandle
    source: str                 # runtime | candidate | review | hosted_check
    source_identity: str

    @property
    def wake_ref(self) -> str:
        return f"watchdog:{self.source}:{self.cursor}:{self.source_identity}"

@dataclass(frozen=True)
class WatchdogCampaignSnapshot:
    campaign: CampaignHandle
    status: CampaignStatus
    trusted_progress_digest: str
    next_check_at: str | None
    active_binding_ids: tuple[str, ...]
    diagnosed_binding_ids: tuple[str, ...]
    candidate_receipt_digests: tuple[str, ...]
    last_wake_refs: tuple[str, ...]

@dataclass(frozen=True)
class WatchdogWakePage:
    events: tuple[WatchdogWake, ...]
    next_cursor: str | None

class WatchdogEventSource(Protocol):
    def read(self, after_cursor: str | None) -> WatchdogWakePage: ...

class WatchdogCampaignSource(Protocol):
    def active_campaigns(self) -> tuple[CampaignHandle, ...]: ...
    def watchdog_snapshot(self, handle: CampaignHandle) -> WatchdogCampaignSnapshot: ...

class WatchdogAdvancer(Protocol):
    def advance(self, handle: CampaignHandle, wake_ref: str | None = None) -> CampaignOutcome: ...

# The following four types live in execution_kernel.py, not campaign_watchdog.py.
class StaleReadbackState(str, Enum):
    TERMINAL = "terminal"
    IDLE = "idle"
    PERMISSION_WAITING = "permission_waiting"
    CANDIDATE_RECEIVED = "candidate_received"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    AMBIGUOUS_RUNNING = "ambiguous_running"

@dataclass(frozen=True)
class StaleBindingObservation:
    stable_action_id: str
    runtime_binding_id: str
    state: StaleReadbackState
    runtime_readback_digest: str
    process_readback_digest: str
    workspace_readback_digest: str
    campaign_readback_digest: str
    receipt_digest: str

class StaleDiagnosisDisposition(str, Enum):
    CONTINUE = "continue"
    GUIDE_SAME_WORKER = "guide_same_worker"
    RECOVER_SAME_BINDING = "recover_same_binding"
    DECISION = "decision"

@dataclass(frozen=True)
class StaleDiagnosisObservation:
    stable_action_id: str
    runtime_binding_id: str
    disposition: StaleDiagnosisDisposition
    receipt_digest: str

# These two private records live in runtime_gateway.py and are not exported.
@dataclass(frozen=True)
class _RuntimeCampaignWake:
    cursor: str
    repository: str
    campaign_key: str
    source: Literal["runtime", "candidate", "review"]
    stable_action_id: str
    kind: str

@dataclass(frozen=True)
class _RuntimeCampaignWakePage:
    events: tuple[_RuntimeCampaignWake, ...]
    next_cursor: str | None

class CampaignWatchdogError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class CampaignWatchdog:
    def __init__(
        self,
        *,
        store_path: Path,
        event_sources: Mapping[str, WatchdogEventSource],
        campaign_source: WatchdogCampaignSource,
        advancer: WatchdogAdvancer,
    ) -> None:
        if type(store_path) is not Path or type(event_sources) is not dict or not event_sources:
            raise CampaignWatchdogError("WATCHDOG_SOURCE_INVALID", "exact store path and event source mapping are required")
        if any(type(name) is not str or not name for name in event_sources):
            raise CampaignWatchdogError("WATCHDOG_SOURCE_INVALID", "stream names must be non-empty text")
        self._store_path = store_path
        self._event_sources = dict(event_sources)
        self._campaign_source = campaign_source
        self._advancer = advancer
        with sqlite3.connect(self._store_path) as connection:
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS v8_watchdog_sources "
                "(stream TEXT PRIMARY KEY, cursor TEXT, page_digest TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS v8_watchdog_due "
                "(repository TEXT NOT NULL, campaign_key TEXT NOT NULL, next_check_at TEXT NOT NULL, "
                "progress_digest TEXT NOT NULL, PRIMARY KEY(repository, campaign_key));"
                "CREATE TABLE IF NOT EXISTS v8_watchdog_wakes "
                "(wake_ref TEXT PRIMARY KEY, stream TEXT NOT NULL, cursor TEXT NOT NULL, repository TEXT NOT NULL, "
                "campaign_key TEXT NOT NULL, source TEXT NOT NULL, source_identity TEXT NOT NULL);"
            )

    def rebuild_due_queue(self) -> None:
        with sqlite3.connect(self._store_path) as connection:
            connection.execute("DELETE FROM v8_watchdog_due")
            for handle in self._campaign_source.active_campaigns():
                snapshot = self._campaign_source.watchdog_snapshot(handle)
                if snapshot.status is CampaignStatus.COMPLETE or snapshot.next_check_at is None:
                    continue
                connection.execute(
                    "INSERT INTO v8_watchdog_due(repository, campaign_key, next_check_at, progress_digest) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(repository, campaign_key) DO UPDATE SET "
                    "next_check_at=excluded.next_check_at, progress_digest=excluded.progress_digest",
                    (handle.repository, handle.campaign_key, snapshot.next_check_at, snapshot.trusted_progress_digest),
                )

    def run_once(self, now: str) -> tuple[CampaignOutcome, ...]:
        datetime.fromisoformat(now)
        outcomes: list[CampaignOutcome] = []
        for stream, source in sorted(self._event_sources.items()):
            after_cursor = self.read_cursor(stream)
            page = source.read(after_cursor)
            page_digest = digest_value(page)
            with sqlite3.connect(self._store_path) as connection:
                saved = connection.execute(
                    "SELECT cursor, page_digest FROM v8_watchdog_sources WHERE stream=?", (stream,)
                ).fetchone()
                if saved is not None and (page.next_cursor, page_digest) == saved:
                    continue
                if saved is not None and page.next_cursor == saved[0]:
                    raise CampaignWatchdogError("WATCHDOG_CURSOR_CONFLICT", "cursor was reused with changed page")
                for wake in page.events:
                    connection.execute(
                        "INSERT OR IGNORE INTO v8_watchdog_wakes "
                        "(wake_ref, stream, cursor, repository, campaign_key, source, source_identity) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (wake.wake_ref, stream, wake.cursor, wake.campaign.repository, wake.campaign.campaign_key, wake.source, wake.source_identity),
                    )
                connection.execute(
                    "INSERT INTO v8_watchdog_sources(stream, cursor, page_digest) VALUES (?, ?, ?) "
                    "ON CONFLICT(stream) DO UPDATE SET cursor=excluded.cursor, page_digest=excluded.page_digest",
                    (stream, page.next_cursor, page_digest),
                )
            for wake in page.events:
                outcomes.append(self._advancer.advance(wake.campaign, wake.wake_ref))
        self.rebuild_due_queue()
        with sqlite3.connect(self._store_path) as connection:
            due = connection.execute(
                "SELECT repository, campaign_key FROM v8_watchdog_due WHERE next_check_at <= ? "
                "ORDER BY next_check_at, repository, campaign_key", (now,)
            ).fetchall()
        for repository, campaign_key in due:
            handle = CampaignHandle(repository, campaign_key)
            snapshot = self._campaign_source.watchdog_snapshot(handle)
            if snapshot.status is not CampaignStatus.COMPLETE and snapshot.next_check_at is not None and snapshot.next_check_at <= now:
                outcomes.append(self._advancer.advance(handle, None))
        self.rebuild_due_queue()
        return tuple(outcomes)

    def read_cursor(self, stream: str) -> str | None:
        if type(stream) is not str or not stream:
            raise CampaignWatchdogError("WATCHDOG_SOURCE_INVALID", "stream must be non-empty text")
        with sqlite3.connect(self._store_path) as connection:
            row = connection.execute("SELECT cursor FROM v8_watchdog_sources WHERE stream=?", (stream,)).fetchone()
        return None if row is None else row[0]
```

Extend `WorkRunEffects.readback` and `execute` to return the closed union
`WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation`.
Add `runtime_binding_id: str | None = None` to both `WorkRunAction` and
`WorkRunObservation`; an initial semantic action has no binding in its intent,
and its authoritative observation establishes the non-empty binding ID.
Every later action copies the persisted binding ID into its intent.
ExecutionKernel accepts a stale observation only when its exact type matches
`WorkRunAction.kind` (`stale_readback` or `stale_diagnosis`), stable action,
Work Run, and current Runtime Binding. Any cross-kind, stale-binding, unknown
disposition, or changed receipt fails with `EFFECT_READBACK_INVALID` before a
state transition.

`event_sources` maps independent cursor streams, normally `runtime_gateway`
and later `hosted_check`. One RuntimeGateway stream may emit wakes whose
closed semantic `WatchdogWake.source` is `runtime`, `candidate`, or `review`;
do not poll that one provider stream three times. Tests may install a strict
subset. All constructors reject
subclasses, unknown source values, empty identities, noncanonical UTC
timestamps, non-increasing page cursors other than an exact digest-identical
replay, and malformed Campaign identities before a store write. `read_cursor` is a read-only host diagnostic used by
restart tests, not a workflow operation or package export.

## Shared Test-Support Contract

Create these exact helpers in `tests/v8_watchdog_test_support.py`; every test
below imports them instead of relying on an unnamed fixture:

```python
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gwo_v8._canonical import digest_value
from gwo_v8.campaign_watchdog import (
    CampaignWatchdog,
    WatchdogCampaignSnapshot,
    WatchdogWake,
    WatchdogWakePage,
)
from gwo_v8.execution_kernel import (
    CampaignOutcome,
    CampaignStatus,
    ExecutionKernel,
    StaleBindingObservation,
    StaleDiagnosisDisposition,
    StaleDiagnosisObservation,
    StaleReadbackState,
    WorkRunAction,
    WorkRunObservation,
)
from gwo_v8.plan_control import CampaignHandle
from v8_successor_test_support import _StaticPlanReader, _minimal_active_campaign


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
    next_check_at: str | None = None,
    trusted_progress_digest: str = "1" * 64,
    status: CampaignStatus = CampaignStatus.RUNNING,
) -> WatchdogCampaignSnapshot:
    return WatchdogCampaignSnapshot(
        campaign=TEST_HANDLE,
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
        campaign_source=campaign_source or RecordingCampaignSource({TEST_HANDLE: make_snapshot()}),
        advancer=advancer or RecordingAdvancer(),
    )


@dataclass
class MutableUtcClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, minutes: int) -> None:
        self.value += timedelta(minutes=minutes)


class StaleRecordingEffects:
    def __init__(self) -> None:
        self.readbacks: dict[str, object] = {}
        self.stale_state = StaleReadbackState.AMBIGUOUS_RUNNING
        self.zero_llm_readbacks = 0
        self.coordinator_diagnoses = 0

    def readback(self, action: WorkRunAction) -> object | None:
        return self.readbacks.get(action.stable_action_id)

    def execute(self, action: WorkRunAction) -> object:
        if action.kind in {"semantic_execution", "semantic_resume"}:
            observation: object = WorkRunObservation(
                phase="running",
                stable_action_id=action.stable_action_id,
                receipt_digest=digest_value({"action": action.stable_action_id}),
                runtime_binding_id="binding:initial",
            )
        elif action.kind == "stale_readback":
            self.zero_llm_readbacks += 1
            observation = StaleBindingObservation(
                stable_action_id=action.stable_action_id,
                runtime_binding_id="binding:initial",
                state=self.stale_state,
                runtime_readback_digest="2" * 64,
                process_readback_digest="3" * 64,
                workspace_readback_digest="4" * 64,
                campaign_readback_digest="5" * 64,
                receipt_digest=digest_value({
                    "action": action.stable_action_id,
                    "state": self.stale_state.value,
                    "runtime": "2" * 64,
                    "process": "3" * 64,
                    "workspace": "4" * 64,
                    "campaign": "5" * 64,
                }),
            )
        elif action.kind == "stale_diagnosis":
            self.coordinator_diagnoses += 1
            observation = StaleDiagnosisObservation(
                stable_action_id=action.stable_action_id,
                runtime_binding_id="binding:initial",
                disposition=StaleDiagnosisDisposition.CONTINUE,
                receipt_digest=digest_value({
                    "action": action.stable_action_id,
                    "disposition": "continue",
                }),
            )
        else:
            raise AssertionError(f"unexpected WorkRunAction kind: {action.kind}")
        self.readbacks[action.stable_action_id] = observation
        return observation


@dataclass
class StaleKernelHarness:
    kernel: ExecutionKernel
    effects: StaleRecordingEffects
    handle: CampaignHandle
    clock: MutableUtcClock

    @property
    def now(self) -> str:
        return self.clock.value.isoformat()

    def advance_clock(self, *, minutes: int) -> None:
        self.clock.advance(minutes=minutes)


@pytest.fixture
def stale_kernel(tmp_path: Path) -> StaleKernelHarness:
    active, campaign = _minimal_active_campaign(("issue:109",))
    clock = MutableUtcClock(datetime.fromisoformat("2026-08-03T09:30:00+00:00"))
    effects = StaleRecordingEffects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "stale-kernel.db",
        plan_control=_StaticPlanReader(active),
        effects=effects,
        _clock=clock,
    )
    kernel.advance(campaign)
    return StaleKernelHarness(kernel, effects, campaign, clock)
```

`_minimal_active_campaign` and `_StaticPlanReader` are test authorities only;
production code does not import from `tests`.

## Execution Baseline and Shared-Receipt Gate

Do not start #113 from the planning baseline alone. Candidate Assurance Task 1
must first merge its narrow shared foundation without closing #114. Verify the
exact symbols and focused tests from fresh `origin/main`:

```powershell
git fetch origin main
git show origin/main:skills/orchestrator/scripts/gwo_v8/candidate_gate.py | Select-String 'class CandidateReceipt'
git show origin/main:skills/orchestrator/scripts/gwo_v8/execution_kernel.py | Select-String 'candidate_receipt: CandidateReceipt | None'
py -3.13 -m pytest tests/test_v8_candidate_receipt_kernel.py -q
```

Expected: all three checks pass. If any symbol/test is absent, stop rather than
implementing a second receipt type in #113. Create the #113 execution worktree
from that exact merged main SHA. After the foundation merge, #113 takes the
serialized package-manifest lane; the remaining package-changing #114 work
waits for #113 to merge and then rebases before resuming. A bypass lane is
allowed only for work whose complete write set excludes
`skills/orchestrator/.skill-package.json`, requires no package sync, and does
not overlap any #113-owned file.

### Task 1: Freeze Watchdog Contracts and Derived Store

**Files:** Create `campaign_watchdog.py`, `v8_watchdog_test_support.py`, and `test_v8_campaign_watchdog.py`.

**Consumes:** `CampaignHandle`, `CampaignOutcome`, `CampaignStatus`, canonical digest helper.

**Produces:** Exact interfaces above and a derived SQLite store containing source cursors and due entries only.

- [ ] **Step 1: Write the failing contract test**

```python
def test_wake_ref_binds_source_cursor_and_identity():
    wake = WatchdogWake(
        cursor="41",
        campaign=CampaignHandle("owner/repo", "campaign:alpha"),
        source="runtime",
        source_identity="stable-action:worker-a",
    )
    assert wake.wake_ref == "watchdog:runtime:41:stable-action:worker-a"
```

- [ ] **Step 2: Run RED**

```powershell
py -3.13 -m pytest tests/test_v8_campaign_watchdog.py::test_wake_ref_binds_source_cursor_and_identity -q
```

Expected: FAIL because `gwo_v8.campaign_watchdog` does not exist.

- [ ] **Step 3: Write the minimum GREEN frozen contracts and closed validation**

```python
class CampaignWatchdogError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class WatchdogWake:
    cursor: str
    campaign: CampaignHandle
    source: str
    source_identity: str

    @property
    def wake_ref(self) -> str:
        return f"watchdog:{self.source}:{self.cursor}:{self.source_identity}"
```

Add `WATCHDOG_INPUT_INVALID`, `WATCHDOG_STORE_INVALID`,
`WATCHDOG_CURSOR_CONFLICT`, and `WATCHDOG_SOURCE_INVALID`; reject invalid
values before a store write.

- [ ] **Step 4: Write RED tests for store replay and cursor conflict**

```python
def test_changed_event_reusing_cursor_fails_without_advancing_cursor(tmp_path):
    source = RecordingWakeSource(pages=[page(runtime_wake("7", "action:a"))])
    watchdog = make_watchdog(tmp_path, source=source)
    watchdog.run_once("2026-08-03T10:00:00+00:00")
    source.pages = [page(runtime_wake("7", "action:b"))]
    with pytest.raises(CampaignWatchdogError) as raised:
        watchdog.run_once("2026-08-03T10:01:00+00:00")
    assert raised.value.code == "WATCHDOG_CURSOR_CONFLICT"
    assert watchdog.read_cursor("runtime_gateway") == "7"
```

- [ ] **Step 5: Run RED for the store behavior only**

```powershell
py -3.13 -m pytest tests/test_v8_campaign_watchdog.py::test_changed_event_reusing_cursor_fails_without_advancing_cursor -q
```

Expected: `FAIL` because the source cursor/page digest transaction is absent;
do not implement before this specific failure is recorded.

- [ ] **Step 6: Write the minimum GREEN store transaction**

Replace the provisional `CampaignWatchdog.rebuild_due_queue` and
`CampaignWatchdog.run_once` bodies with these directly executable methods. The
source row update is a compare-and-swap against the cursor and digest read in
the same transaction. Due rows are a rebuildable projection: active scheduled
Campaigns are upserted, while terminal, unscheduled, and no-longer-active rows
are deleted.

```python
def rebuild_due_queue(self) -> None:
    connection = sqlite3.connect(self._store_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        active_keys: set[tuple[str, str]] = set()
        for handle in self._campaign_source.active_campaigns():
            snapshot = self._campaign_source.watchdog_snapshot(handle)
            key = (handle.repository, handle.campaign_key)
            active_keys.add(key)
            if snapshot.status is CampaignStatus.COMPLETE or snapshot.next_check_at is None:
                connection.execute(
                    "DELETE FROM v8_watchdog_due WHERE repository=? AND campaign_key=?",
                    key,
                )
                continue
            connection.execute(
                "INSERT INTO v8_watchdog_due"
                "(repository, campaign_key, next_check_at, progress_digest) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(repository, campaign_key) DO UPDATE SET "
                "next_check_at=excluded.next_check_at, "
                "progress_digest=excluded.progress_digest",
                (*key, snapshot.next_check_at, snapshot.trusted_progress_digest),
            )

        for repository, campaign_key in connection.execute(
            "SELECT repository, campaign_key FROM v8_watchdog_due"
        ).fetchall():
            if (repository, campaign_key) not in active_keys:
                connection.execute(
                    "DELETE FROM v8_watchdog_due WHERE repository=? AND campaign_key=?",
                    (repository, campaign_key),
                )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def run_once(self, now: str) -> tuple[CampaignOutcome, ...]:
    datetime.fromisoformat(now)
    outcomes: list[CampaignOutcome] = []
    for stream, source in sorted(self._event_sources.items()):
        after_cursor = self.read_cursor(stream)
        page = source.read(after_cursor)
        page_digest = digest_value(page)
        connection = sqlite3.connect(self._store_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            saved = connection.execute(
                "SELECT cursor, page_digest FROM v8_watchdog_sources WHERE stream=?",
                (stream,),
            ).fetchone()

            if saved == (page.next_cursor, page_digest):
                connection.rollback()
                continue
            if saved is not None and saved[0] == page.next_cursor:
                raise CampaignWatchdogError(
                    "WATCHDOG_CURSOR_CONFLICT",
                    "cursor was reused with a changed page",
                )
            if saved is not None and saved[0] != after_cursor:
                raise CampaignWatchdogError(
                    "WATCHDOG_CURSOR_CONFLICT",
                    "source cursor changed before page publication",
                )
            if saved is None and after_cursor is not None:
                raise CampaignWatchdogError(
                    "WATCHDOG_CURSOR_CONFLICT",
                    "source cursor disappeared before page publication",
                )

            for wake in page.events:
                connection.execute(
                    "INSERT OR IGNORE INTO v8_watchdog_wakes "
                    "(wake_ref, stream, cursor, repository, campaign_key, source, source_identity) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        wake.wake_ref,
                        stream,
                        wake.cursor,
                        wake.campaign.repository,
                        wake.campaign.campaign_key,
                        wake.source,
                        wake.source_identity,
                    ),
                )

            if saved is None:
                connection.execute(
                    "INSERT INTO v8_watchdog_sources(stream, cursor, page_digest) "
                    "VALUES (?, ?, ?)",
                    (stream, page.next_cursor, page_digest),
                )
            else:
                published = connection.execute(
                    "UPDATE v8_watchdog_sources SET cursor=?, page_digest=? "
                    "WHERE stream=? AND cursor IS ? AND page_digest=?",
                    (
                        page.next_cursor,
                        page_digest,
                        stream,
                        saved[0],
                        saved[1],
                    ),
                )
                if published.rowcount != 1:
                    raise CampaignWatchdogError(
                        "WATCHDOG_CURSOR_CONFLICT",
                        "source cursor compare-and-swap failed",
                    )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

        for wake in page.events:
            outcomes.append(self._advancer.advance(wake.campaign, wake.wake_ref))

    self.rebuild_due_queue()
    return tuple(outcomes)
```

The explicit rollback covers validation, insert, and CAS failures, so neither
wake rows nor a new source cursor survive a rejected page. Exact replay rolls
back the source transaction and performs no event dispatch; the independent
derived due projection may still be rebuilt from authoritative snapshots.

- [ ] **Step 7: Run the focused store test to PASS**

```powershell
py -3.13 -m pytest tests/test_v8_campaign_watchdog.py::test_changed_event_reusing_cursor_fails_without_advancing_cursor -q
```

Expected: `PASS`; changed cursor reuse raises `WATCHDOG_CURSOR_CONFLICT` and
leaves the saved cursor unchanged.

- [ ] **Step 8: Synchronize the package**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
```

- [ ] **Step 9: Commit the smallest store behavior**

```powershell
git add skills/orchestrator/scripts/gwo_v8/campaign_watchdog.py skills/orchestrator/.skill-package.json tests/v8_watchdog_test_support.py tests/test_v8_campaign_watchdog.py
git commit -m "feat: define rebuildable Campaign Watchdog"
```

### Task 2: Add a Read-Only ExecutionKernel Watchdog Snapshot

**Files:** Modify `execution_kernel.py`; create `test_v8_watchdog_execution_kernel.py`.

**Consumes:** Task 1 `WatchdogCampaignSnapshot`; existing Diagnostics and persisted Work Run state.

**Produces:** `ExecutionKernel.active_campaigns() -> tuple[CampaignHandle, ...]`,
`ExecutionKernel.watchdog_snapshot(handle) -> WatchdogCampaignSnapshot`, and
`WorkRunSummary.last_wake_ref: str | None`. All three are read-only projections;
only `last_wake_ref` is added to existing public Diagnostics, and no Watchdog
operation is exported.

Keep `WatchdogCampaignSnapshot` owned by `campaign_watchdog.py`. In
`execution_kernel.py`, annotate the return as the string
`"WatchdogCampaignSnapshot"`, import it under `TYPE_CHECKING`, and construct it
through a method-local import. Do not add a top-level ExecutionKernel ->
CampaignWatchdog import, because CampaignWatchdog already consumes
`CampaignOutcome`/`CampaignStatus` from ExecutionKernel.

- [ ] **Step 1: Write RED proving snapshot is read-only**

```python
import json
import sqlite3

import pytest

from gwo_v8.execution_kernel import ExecutionKernelError
from v8_successor_test_support import kernel_with_one_ticket


def test_watchdog_snapshot_does_not_create_or_migrate_kernel_state(
    kernel_with_one_ticket,
):
    kernel, _effects, campaign = kernel_with_one_ticket
    kernel.advance(campaign)
    before = kernel._store_path.read_bytes()
    snapshot = kernel.watchdog_snapshot(campaign)
    after = kernel._store_path.read_bytes()
    assert after == before
    assert snapshot.campaign == campaign
```

- [ ] **Step 2: Run RED**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_execution_kernel.py::test_watchdog_snapshot_does_not_create_or_migrate_kernel_state -q
```

Expected: FAIL because the method is missing.

- [ ] **Step 3: Write the minimum GREEN pure projection**

Read authoritative active Plan and existing Kernel state without calling
`_load_or_initialize`, migration, `_save`, an effect, or RuntimeGateway.
`active_campaigns` executes one ordered `SELECT repository, campaign_key` and
filters terminal rows from already persisted state; it does not hydrate a Plan.
Derive `trusted_progress_digest` only from the persisted Kernel transition
revision, normalized permission receipt digest, terminal delivery receipt
digest, and the complete shared `CandidateReceipt.canonical()` read back from
Kernel state. Recompute `CandidateReceipt.digest` and reject changed SHA, tree,
diff schema/digest, Work Run, revision, or authority bytes with
`EXECUTION_STORE_INVALID`. Raw wake refs and logs do not participate.

Name the per-run monotonic counter `trusted_progress_revision`; it is distinct
from the production-composition plan's SQLite `state_version`. Increment it
only in the same save that adopts a new authoritative lifecycle, normalized
permission, Candidate, delivery receipt, or Kernel-owned transition. A raw
wake may require a CAS save and therefore change `state_version`, but it must
not change `trusted_progress_revision`, `last_trusted_progress_at`, or
`stale_due_at`.

```python
def watchdog_snapshot(self, handle: CampaignHandle) -> "WatchdogCampaignSnapshot":
    state = self._read_persisted_campaign_without_migration(handle)
    from .campaign_watchdog import WatchdogCampaignSnapshot
    return WatchdogCampaignSnapshot(
        campaign=handle,
        status=self._status_from_persisted_state(state),
        trusted_progress_digest=self._trusted_progress_digest(state),
        next_check_at=state.get("next_check_at"),
        active_binding_ids=tuple(sorted(state.get("active_binding_ids", ()))),
        diagnosed_binding_ids=tuple(sorted(state.get("diagnosed_binding_ids", ()))),
        candidate_receipt_digests=tuple(sorted(state.get("candidate_receipt_digests", ()))),
        last_wake_refs=tuple(sorted(state.get("last_wake_refs", ()))),
    )
```

- [ ] **Step 4: Run the snapshot test to PASS**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_execution_kernel.py::test_watchdog_snapshot_does_not_create_or_migrate_kernel_state -q
```

Expected: `PASS`; the SQLite bytes are unchanged.

- [ ] **Step 5: Synchronize and commit the snapshot behavior**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/execution_kernel.py skills/orchestrator/.skill-package.json tests/test_v8_watchdog_execution_kernel.py
git commit -m "feat: expose read-only Watchdog snapshot"
```

- [ ] **Step 6: Add RED tests for false progress**

```python
@pytest.mark.parametrize("hint", ["worker-report", "workspace-head", "raw-log", "duplicate-callback"])
def test_hint_does_not_change_trusted_progress_digest(kernel_with_one_ticket, hint):
    kernel, _effects, campaign = kernel_with_one_ticket
    kernel.advance(campaign)
    before = kernel.watchdog_snapshot(campaign)
    kernel.advance(campaign, f"hint:{hint}")
    after = kernel.watchdog_snapshot(campaign)
    assert after.trusted_progress_digest == before.trusted_progress_digest
```

- [ ] **Step 7: Run RED for false progress only**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_execution_kernel.py::test_hint_does_not_change_trusted_progress_digest -q
```

Expected: `FAIL` because raw wake refs still participate in the trusted
progress revision.

- [ ] **Step 8: Write the minimum GREEN trusted-progress digest**

```python
return digest_value({
    "kernel_transition_revision": state["trusted_progress_revision"],
    "permission_receipts": state["normalized_permission_receipts"],
    "candidate_receipts": state["candidate_receipts"],
    "delivery_receipts": state["delivery_receipts"],
})
```

- [ ] **Step 9: Run the exact false-progress test to PASS**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_execution_kernel.py::test_hint_does_not_change_trusted_progress_digest -q
```

Expected: `4 passed`; none of the four hints resets trusted progress.

- [ ] **Step 10: Synchronize and commit the false-progress behavior**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/execution_kernel.py skills/orchestrator/.skill-package.json tests/test_v8_watchdog_execution_kernel.py
git commit -m "test: exclude raw wakes from trusted progress"
```

- [ ] **Step 11: Write RED for Candidate-receipt trust and wake diagnostics**

```python
from v8_candidate_assurance_test_support import kernel_with_candidate_receipt


def test_exact_persisted_candidate_receipt_is_a_trusted_progress_input(
    kernel_with_candidate_receipt,
):
    kernel, _effects, campaign, receipt = kernel_with_candidate_receipt
    snapshot = kernel.watchdog_snapshot(campaign)
    assert snapshot.candidate_receipt_digests == (receipt.digest,)


def test_changed_persisted_candidate_receipt_fails_closed(
    kernel_with_candidate_receipt,
):
    kernel, _effects, campaign, _receipt = kernel_with_candidate_receipt
    with sqlite3.connect(kernel._store_path) as connection:
        row = connection.execute(
            "SELECT state_json FROM v8_execution_kernel_campaigns WHERE repository=? AND campaign_key=?",
            (campaign.repository, campaign.campaign_key),
        ).fetchone()
        state = json.loads(row[0])
        run = next(iter(state["runs"].values()))
        run["candidate_receipt"]["candidate_tree_oid"] = "f" * 40
        connection.execute(
            "UPDATE v8_execution_kernel_campaigns SET state_json=? WHERE repository=? AND campaign_key=?",
            (json.dumps(state, separators=(",", ":"), sort_keys=True), campaign.repository, campaign.campaign_key),
        )
    with pytest.raises(ExecutionKernelError) as raised:
        kernel.watchdog_snapshot(campaign)
    assert raised.value.code == "EXECUTION_STORE_INVALID"


def test_last_wake_ref_is_diagnostic_but_not_trusted_progress(kernel_with_one_ticket):
    kernel, _effects, campaign = kernel_with_one_ticket
    kernel.advance(campaign)
    before = kernel.watchdog_snapshot(campaign)
    kernel.advance(campaign, "watchdog:runtime:7:semantic:issue:113")
    after = kernel.watchdog_snapshot(campaign)
    run = kernel.inspect(campaign).work_runs[0]
    assert run.last_wake_ref == "watchdog:runtime:7:semantic:issue:113"
    assert after.last_wake_refs == (run.last_wake_ref,)
    assert after.trusted_progress_digest == before.trusted_progress_digest
```

Candidate Assurance Task 1 owns the imported fixture and must return the exact
`CandidateReceipt` sent through `WorkRunObservation.candidate_receipt`; #113
does not manufacture or write one. `candidate_receipt_digests` is a host-private
read-only projection used to prove which canonical receipts participate in the
trusted-progress digest.

- [ ] **Step 12: Run RED for Candidate receipt trust and wake diagnostics only**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_execution_kernel.py::test_exact_persisted_candidate_receipt_is_a_trusted_progress_input tests/test_v8_watchdog_execution_kernel.py::test_changed_persisted_candidate_receipt_fails_closed tests/test_v8_watchdog_execution_kernel.py::test_last_wake_ref_is_diagnostic_but_not_trusted_progress -q
```

Expected: `FAIL` at the missing receipt projection or changed-receipt
`EXECUTION_STORE_INVALID`; do not implement in this step.

- [ ] **Step 13: Write the minimum GREEN for receipt and wake projection**

```python
receipt = CandidateReceipt.from_canonical(run["candidate_receipt"])
if receipt.digest != run["candidate_receipt_digest"]:
    raise ExecutionKernelError("EXECUTION_STORE_INVALID", "Candidate receipt digest changed")
trusted_receipts.append(receipt.digest)
state["last_wake_refs"] = tuple(sorted(set(state.get("last_wake_refs", ())) | {wake_ref}))
```

The receipt is the exact shared Candidate Assurance value; `wake_ref` is
diagnostic only and does not increment `trusted_progress_revision`.

- [ ] **Step 14: Run the exact receipt/wake tests to PASS**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_execution_kernel.py::test_exact_persisted_candidate_receipt_is_a_trusted_progress_input tests/test_v8_watchdog_execution_kernel.py::test_changed_persisted_candidate_receipt_fails_closed tests/test_v8_watchdog_execution_kernel.py::test_last_wake_ref_is_diagnostic_but_not_trusted_progress -q
```

Expected: `3 passed`, including the tampered-receipt fail-closed assertion.

- [ ] **Step 15: Synchronize the package**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
```

- [ ] **Step 16: Commit only this behavior**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/execution_kernel.py skills/orchestrator/.skill-package.json tests/test_v8_watchdog_execution_kernel.py
git commit -m "feat: expose read-only Campaign liveness snapshots"
```

### Task 3: Rebuild Due Work and Converge Event Hints Through Advance

**Files:** Modify `campaign_watchdog.py` and its tests.

**Consumes:** Task 2 snapshot and Task 1 source/advancer protocols.

**Produces:** restart-safe `rebuild_due_queue` and deterministic `run_once`.

- [ ] **Step 1: Write RED for restart and a lost timer callback**

```python
def test_restart_rebuilds_overdue_campaign_and_calls_advance_once(tmp_path):
    campaigns = RecordingCampaignSource({
        handle(): make_snapshot(next_check_at="2026-08-03T09:59:00+00:00")
    })
    source = RecordingWakeSource([])
    advancer = RecordingAdvancer()
    first = make_watchdog(
        tmp_path,
        source=source,
        campaign_source=campaigns,
        advancer=advancer,
    )
    first.rebuild_due_queue()
    restarted = make_watchdog(
        tmp_path,
        source=source,
        campaign_source=campaigns,
        advancer=advancer,
    )
    outcomes = restarted.run_once("2026-08-03T10:00:00+00:00")
    assert len(outcomes) == 1
    assert advancer.calls == [(handle(), None)]
```

- [ ] **Step 2: Run RED for restart/due dispatch only**

```powershell
py -3.13 -m pytest tests/test_v8_campaign_watchdog.py::test_restart_rebuilds_overdue_campaign_and_calls_advance_once -q
```

Expected: `FAIL` because restart does not yet reconstruct `v8_watchdog_due`;
stop after recording this failure.

- [ ] **Step 3: Write the minimum GREEN for due reconstruction**

```python
for handle in self._campaign_source.active_campaigns():
    snapshot = self._campaign_source.watchdog_snapshot(handle)
    if snapshot.status is not CampaignStatus.COMPLETE and snapshot.next_check_at:
        connection.execute(
            "INSERT INTO v8_watchdog_due(repository, campaign_key, next_check_at, progress_digest) VALUES (?, ?, ?, ?)",
            (handle.repository, handle.campaign_key, snapshot.next_check_at, snapshot.trusted_progress_digest),
        )
```

- [ ] **Step 4: Run the same restart test to PASS**

```powershell
py -3.13 -m pytest tests/test_v8_campaign_watchdog.py::test_restart_rebuilds_overdue_campaign_and_calls_advance_once -q
```

Expected: `PASS` with one `advance(handle, None)` call.

- [ ] **Step 5: Synchronize the package**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
```

- [ ] **Step 6: Commit the restart behavior**

```powershell
git add skills/orchestrator/scripts/gwo_v8/campaign_watchdog.py skills/orchestrator/.skill-package.json tests/test_v8_campaign_watchdog.py
git commit -m "feat: rebuild Watchdog due work after restart"
```

- [ ] **Step 7: Write RED for oldest-due-first/post-readback ordering**

```python
def test_due_work_orders_by_timestamp_then_campaign_and_rebuilds_after_advance(tmp_path):
    first = CampaignHandle("owner/repo", "campaign:a")
    second = CampaignHandle("owner/repo", "campaign:b")
    campaigns = RecordingCampaignSource({
        first: make_snapshot(next_check_at="2026-08-03T09:58:00+00:00"),
        second: make_snapshot(next_check_at="2026-08-03T09:59:00+00:00"),
    })
    advancer = RecordingAdvancer()
    make_watchdog(tmp_path, source=RecordingWakeSource([]), campaign_source=campaigns, advancer=advancer).run_once(NOW)
    assert [call[0].campaign_key for call in advancer.calls] == ["campaign:a", "campaign:b"]
```

- [ ] **Step 8: Run RED for ordering only**

```powershell
py -3.13 -m pytest tests/test_v8_campaign_watchdog.py::test_due_work_orders_by_timestamp_then_campaign_and_rebuilds_after_advance -q
```

Expected: `FAIL` because the due query is not yet ordered and the post-advance
snapshot is not yet checked.

- [ ] **Step 9: Write the minimum GREEN ordering/readback code**

```python
due = connection.execute(
    "SELECT repository, campaign_key FROM v8_watchdog_due WHERE next_check_at <= ? ORDER BY next_check_at, repository, campaign_key",
    (now,),
).fetchall()
before = self._campaign_source.watchdog_snapshot(handle)
outcome = self._advancer.advance(handle, None)
after = self._campaign_source.watchdog_snapshot(handle)
if after.status is CampaignStatus.COMPLETE or after.next_check_at != before.next_check_at:
    connection.execute("DELETE FROM v8_watchdog_due WHERE repository=? AND campaign_key=?", (handle.repository, handle.campaign_key))
```

- [ ] **Step 10: Run the ordering test to PASS**

```powershell
py -3.13 -m pytest tests/test_v8_campaign_watchdog.py::test_due_work_orders_by_timestamp_then_campaign_and_rebuilds_after_advance -q
```

Expected: `PASS` with timestamp, repository, Campaign-key ordering.

- [ ] **Step 11: Synchronize the package**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
```

- [ ] **Step 12: Commit the ordering behavior**

```powershell
git add skills/orchestrator/scripts/gwo_v8/campaign_watchdog.py skills/orchestrator/.skill-package.json tests/test_v8_campaign_watchdog.py
git commit -m "feat: order Watchdog due work deterministically"
```

- [ ] **Step 13: Write RED for an exact callback replay after cursor-save loss**

```python
def test_exact_event_replay_reuses_wake_ref_and_advances_cursor_once(tmp_path):
    replay = page(wake("9"))
    source = RecordingWakeSource([replay, replay])
    advancer = RecordingAdvancer()
    watchdog = make_watchdog(tmp_path, source=source, advancer=advancer)

    watchdog.run_once(NOW)
    watchdog.run_once(NOW)

    expected = "watchdog:runtime:9:semantic:issue:113"
    assert advancer.calls == [(handle(), expected)]
    assert watchdog.read_cursor("runtime_gateway") == "9"
```

- [ ] **Step 14: Run RED for event replay and stable-action readback only**

```powershell
py -3.13 -m pytest tests/test_v8_campaign_watchdog.py::test_exact_event_replay_reuses_wake_ref_and_advances_cursor_once tests/test_v8_watchdog_execution_kernel.py::test_replayed_wake_ref_does_not_repeat_read_back_effect -q
```

Expected: `FAIL` because a digest-identical page is dispatched twice or the
Kernel does not yet read back the identical stable action before an effect.

- [ ] **Step 15: Write the minimum GREEN replay/readback implementation**

```python
if saved is not None and page.next_cursor == saved[0] and page_digest == saved[1]:
    return ()
if wake.wake_ref in self._persisted_wake_refs(wake.campaign):
    continue
outcome = self._advancer.advance(wake.campaign, wake.wake_ref)
```

The Kernel, not the Watchdog, owns the final stable-action effect readback.

```python
from v8_successor_test_support import kernel_with_one_ticket


def test_replayed_wake_ref_does_not_repeat_read_back_effect(kernel_with_one_ticket):
    kernel, effects, campaign = kernel_with_one_ticket
    wake_ref = "watchdog:runtime:9:semantic:issue:113"
    kernel.advance(campaign, wake_ref)
    kernel.advance(campaign, wake_ref)
    assert len(effects.executed) == 1
```

- [ ] **Step 16: Run the exact replay tests to PASS**

```powershell
py -3.13 -m pytest tests/test_v8_campaign_watchdog.py::test_exact_event_replay_reuses_wake_ref_and_advances_cursor_once tests/test_v8_watchdog_execution_kernel.py::test_replayed_wake_ref_does_not_repeat_read_back_effect -q
```

Expected: `2 passed` and one external effect.

- [ ] **Step 17: Synchronize the package**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
```

- [ ] **Step 18: Commit only the replay behavior**

```powershell
git add skills/orchestrator/scripts/gwo_v8/campaign_watchdog.py skills/orchestrator/scripts/gwo_v8/execution_kernel.py skills/orchestrator/.skill-package.json tests/test_v8_campaign_watchdog.py tests/test_v8_watchdog_execution_kernel.py
git commit -m "feat: make replayed Watchdog wakes idempotent"
```

### Task 4: Bound Stale Diagnosis to One Per Binding

**Files:** Modify `campaign_watchdog.py`, `execution_kernel.py`, and focused tests.

**Consumes:** RuntimeGateway authoritative readback result and exact binding IDs.

**Produces:** zero-LLM readback first; one `stale_diagnosis` WorkRunAction per binding maximum.

Extend `ExecutionKernelConfiguration` with
`host_stale_after_seconds: int = 1800` and
`repository_stale_after_seconds: dict[str, int] | None = None`, plus
`stale_after_seconds_for(repository) -> int`. Reject booleans, zero/negative
values, non-text/empty repository keys, and caller-retained mutable mappings. Add the
private constructor injection `_clock: Callable[[], datetime] = _utc_now` for
deterministic tests; `install_execution_kernel` does not expose a clock option.
Each active run persists canonical `last_trusted_progress_at` and
`stale_due_at`. A raw wake save may update `last_wake_ref` but neither field.

- [ ] **Step 1: Write RED for thirty-minute classification**

```python
from v8_watchdog_test_support import stale_kernel


def test_stale_binding_uses_zero_llm_readback_before_one_diagnosis(stale_kernel):
    stale_kernel.advance_clock(minutes=30)
    stale_kernel.effects.stale_state = StaleReadbackState.AMBIGUOUS_RUNNING
    stale_kernel.kernel.advance(stale_kernel.handle)
    assert stale_kernel.effects.zero_llm_readbacks == 1
    assert stale_kernel.effects.coordinator_diagnoses == 1
    stale_kernel.kernel.advance(stale_kernel.handle)
    assert stale_kernel.effects.coordinator_diagnoses == 1
```

- [ ] **Step 2: Run RED for stable stale action identity only**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_execution_kernel.py::test_stale_binding_uses_zero_llm_readback_before_one_diagnosis -q
```

Expected: `FAIL` because the zero-LLM readback and diagnosis intents are not
yet persisted under exact stable action IDs; stop after this failure.

- [ ] **Step 3: Write the minimum GREEN stable action implementation**

```python
readback_id = f"stale-readback:{campaign_key}:{work_run_key}:{binding_id}:{trusted_progress_digest}"
diagnosis_id = f"stale-diagnosis:{campaign_key}:{work_run_key}:{binding_id}"
readback = self._read_or_execute_once(WorkRunAction(kind="stale_readback", stable_action_id=readback_id, runtime_binding_id=binding_id))
if readback.state is StaleReadbackState.AMBIGUOUS_RUNNING and binding_id not in diagnosed_binding_ids:
    self._persist_action_intent(WorkRunAction(kind="stale_diagnosis", stable_action_id=diagnosis_id, runtime_binding_id=binding_id))
    self._record_diagnosed_binding(binding_id)
    self._execute_and_readback_diagnosis(diagnosis_id)
```

Persist each intent before invocation and read it back before retry. A
replacement binding receives a new ID and its own one-diagnosis bound.

- [ ] **Step 4: Run the exact stale identity test to PASS**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_execution_kernel.py::test_stale_binding_uses_zero_llm_readback_before_one_diagnosis -q
```

Expected: `PASS`; one zero-LLM readback and one diagnosis occur for the initial
binding, even across repeated `advance` calls.

- [ ] **Step 5: Synchronize the package**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
```

- [ ] **Step 6: Commit the stable identity behavior**

```powershell
git add skills/orchestrator/scripts/gwo_v8/campaign_watchdog.py skills/orchestrator/scripts/gwo_v8/execution_kernel.py skills/orchestrator/.skill-package.json tests/test_v8_watchdog_execution_kernel.py
git commit -m "feat: persist bounded stale action identities"
```

- [ ] **Step 7: Add RED cases for classified outcomes**

```python
@pytest.mark.parametrize(
    "state",
    (
        StaleReadbackState.TERMINAL,
        StaleReadbackState.IDLE,
        StaleReadbackState.PERMISSION_WAITING,
        StaleReadbackState.CANDIDATE_RECEIVED,
        StaleReadbackState.PROVIDER_UNAVAILABLE,
    ),
)
def test_classified_stale_readback_uses_no_coordinator(stale_kernel, state):
    stale_kernel.advance_clock(minutes=30)
    stale_kernel.effects.stale_state = state
    stale_kernel.kernel.advance(stale_kernel.handle)
    assert stale_kernel.effects.zero_llm_readbacks == 1
    assert stale_kernel.effects.coordinator_diagnoses == 0
```

Add structural assertions that `StaleRecordingEffects` exposes no transcript
reader or daemon restart method and that production `WorkRunEffects` receives
only the exact bounded WorkRunAction identity and frozen Ticket/authority
references already present on that action.

- [ ] **Step 8: Run RED for classified outcomes only**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_execution_kernel.py::test_classified_stale_readback_uses_no_coordinator -q
```

Expected: `FAIL` if any mechanically classified state reaches a Coordinator;
stop after recording this failure.

- [ ] **Step 9: Write the minimum GREEN classified routing**

```python
if stale_readback.state in {
    StaleReadbackState.TERMINAL,
    StaleReadbackState.IDLE,
    StaleReadbackState.PERMISSION_WAITING,
    StaleReadbackState.CANDIDATE_RECEIVED,
    StaleReadbackState.PROVIDER_UNAVAILABLE,
}:
    return self._apply_mechanical_stale_readback(stale_readback)
return self._request_one_stale_diagnosis(stale_readback)
```

- [ ] **Step 10: Run the classified matrix to PASS**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_execution_kernel.py::test_classified_stale_readback_uses_no_coordinator -q
```

Expected: `5 passed`; no transcript reader or daemon restart is present.

- [ ] **Step 11: Synchronize the package**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
```

- [ ] **Step 12: Commit the classified-state behavior**

```powershell
git add skills/orchestrator/scripts/gwo_v8/campaign_watchdog.py skills/orchestrator/scripts/gwo_v8/execution_kernel.py skills/orchestrator/.skill-package.json tests/test_v8_watchdog_execution_kernel.py
git commit -m "feat: keep classified stale states deterministic"
```

- [ ] **Step 13: Write RED for the four closed dispositions**

```python
@pytest.mark.parametrize("disposition", tuple(StaleDiagnosisDisposition))
def test_stale_diagnosis_covers_all_closed_dispositions(stale_kernel, disposition):
    stale_kernel.advance_clock(minutes=30)
    stale_kernel.effects.diagnosis_disposition = disposition
    stale_kernel.kernel.advance(stale_kernel.handle)
    assert stale_kernel.effects.coordinator_diagnoses == 1
```

- [ ] **Step 14: Run RED for the four dispositions**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_execution_kernel.py::test_stale_diagnosis_covers_all_closed_dispositions -q
```

Expected: `FAIL` because `CONTINUE`, `GUIDE_SAME_WORKER`,
`RECOVER_SAME_BINDING`, and `DECISION` are not yet a closed validated union.

- [ ] **Step 15: Write the minimum GREEN disposition routing**

```python
if observation.disposition is StaleDiagnosisDisposition.CONTINUE:
    state = state.with_stale_suppressed(binding_id)
elif observation.disposition is StaleDiagnosisDisposition.GUIDE_SAME_WORKER:
    state = state.with_same_worker_guidance(binding_id)
elif observation.disposition is StaleDiagnosisDisposition.RECOVER_SAME_BINDING:
    state = state.with_same_binding_recovery(binding_id)
elif observation.disposition is StaleDiagnosisDisposition.DECISION:
    state = state.require_decision("RuntimeBindingStale")
else:
    raise ExecutionKernelError("EFFECT_READBACK_INVALID", "unknown stale diagnosis disposition")
```

- [ ] **Step 16: Run the four-disposition test to PASS**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_execution_kernel.py::test_stale_diagnosis_covers_all_closed_dispositions -q
```

Expected: `4 passed`; no other disposition is accepted.

- [ ] **Step 17: Synchronize the package**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
```

- [ ] **Step 18: Commit the disposition behavior**

```powershell
git add skills/orchestrator/scripts/gwo_v8/execution_kernel.py skills/orchestrator/.skill-package.json tests/test_v8_watchdog_execution_kernel.py
git commit -m "feat: route all stale diagnosis dispositions"
```

### Task 5: Compose Runtime Wake Sources and Close #113

**Files:** Modify `runtime_gateway.py` and `plan_control_host.py`; create
`test_v8_watchdog_production_host.py`; update only
`skills/orchestrator/.skill-package.json` through sync.

**Consumes:** CampaignWatchdog and installed PlanControl/ExecutionKernel. Uses only a host-private Runtime event adapter; no new RuntimeGateway public operation.

**Produces:**
`RuntimeGateway._read_watchdog_events(after_cursor) -> _RuntimeCampaignWakePage`,
`ProductionPlanControlStartHost.install_campaign_watchdog(...) -> CampaignWatchdog`,
and restart acceptance. Both new operations are host-private and absent from
`gwo_v8.__init__`.

- [ ] **Step 1: Write RED for the real RuntimeGateway cursor/page contract**

```python
def test_read_watchdog_events_passes_cursor_once_and_returns_page_cursor(tmp_path):
    gateway, _store, adapter = _gateway(tmp_path)
    adapter.events = Mock(return_value=_RuntimeEventPage(
        events=(),
        next_cursor="11",
    ))
    page = gateway._read_watchdog_events("11")
    adapter.events.assert_called_once_with("11")
    assert page.next_cursor == "11"
    assert page.events == ()
```

- [ ] **Step 2: Run only the cursor/page RED test**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_passes_cursor_once_and_returns_page_cursor -q
```

Expected: `FAIL` because `_read_watchdog_events` is absent; `_wake_hints` is
not a substitute and must not be renamed into a public operation.

- [ ] **Step 3: Write the minimum GREEN cursor/page implementation**

```python
def _read_watchdog_events(self, after_cursor: str | None) -> _RuntimeCampaignWakePage:
    self._refresh_before_adapter_io()
    raw_page = self._adapter.events(after_cursor)
    verdict = _RuntimeEventPageProtocol.validate(raw_page, after_cursor=after_cursor)
    if verdict.kind != "page":
        assert verdict.failure is not None
        self._raise_failure(verdict.failure)
    assert verdict.page is not None
    return _RuntimeCampaignWakePage(
        events=tuple(self._watchdog_wake_for_event(event) for event in verdict.page.events),
        next_cursor=verdict.page.next_cursor,
    )
```

The method calls the existing provider `events(after_cursor)` exactly once,
does not save a Watchdog cursor, and validates the closed page protocol first.

- [ ] **Step 4: Run the exact cursor/page test to PASS**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_passes_cursor_once_and_returns_page_cursor -q
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py -q
```

Expected: `PASS`; the adapter receives `"11"` once and the valid empty page
returns the unchanged cursor `"11"` without requiring source mapping.

- [ ] **Step 5: Synchronize and commit the cursor/page behavior**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/runtime_gateway.py skills/orchestrator/.skill-package.json tests/test_v8_watchdog_production_host.py
git commit -m "feat: add private Runtime Watchdog page readback"
```

- [ ] **Step 6: Write RED for source mapping from cursor and persisted Subject**

```python
@pytest.mark.parametrize(
    ("purpose", "event_kind", "expected_source"),
    (
        (WorkRunPurpose.implementation(), "state:running", "runtime"),
        (WorkRunPurpose.implementation(), "candidate:reference", "candidate"),
        (WorkRunPurpose.formal_review(), "state:running", "review"),
        (WorkRunPurpose.specialist_review("policy-1"), "state:completed", "review"),
    ),
)
def test_read_watchdog_events_maps_source_from_subject_and_event(tmp_path, purpose, event_kind, expected_source):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, replace(_subject(purpose=purpose), stable_action_id=f"action:{expected_source}"))
    _prepare_and_start(gateway, subject)
    adapter.events = Mock(return_value=_RuntimeEventPage(
        events=(_RuntimeEvent("13", subject.stable_action_id, event_kind),), next_cursor="13"
    ))
    wake = gateway._read_watchdog_events("12").events[0]
    assert wake.source == expected_source
    assert (wake.repository, wake.campaign_key) == (subject.repository, subject.campaign_key)
    assert wake.stable_action_id == subject.stable_action_id
```

- [ ] **Step 7: Run only the source-mapping RED test**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_maps_source_from_subject_and_event -q
```

Expected: `FAIL` because no private source classifier reads the exact
persisted Subject or derives the Campaign mapping.

- [ ] **Step 8: Write the minimum GREEN source/Subject mapper**

```python
def _watchdog_wake_for_event(self, event: _RuntimeEvent) -> _RuntimeCampaignWake:
    record = self._data["actions"].get(event.stable_action_id)
    if type(record) is not dict:
        raise RuntimeGatewayError("RUNTIME_PROVIDER_PROTOCOL_INVALID", "event action has no persisted record")
    subject = _subject_from_canonical(record.get("subject"))
    if subject.stable_action_id != event.stable_action_id:
        raise RuntimeGatewayError("RUNTIME_PROVIDER_PROTOCOL_INVALID", "event action and Subject differ")
    if event.kind == "candidate:reference" or record.get("candidate_reference_emitted") is True:
        source = "candidate"
    elif type(subject) is WorkRunSubject and subject.purpose.kind in {"formal_review", "invalid_review_payload_retry", "specialist_review"}:
        source = "review"
    else:
        source = "runtime"
    return _RuntimeCampaignWake(event.cursor, subject.repository, subject.campaign_key, source, subject.stable_action_id, event.kind)
```

The host-private adapter constructs `CampaignHandle(repository, campaign_key)`
from these fields; it never parses callback text.

- [ ] **Step 9: Run the exact source-mapping test to PASS**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_maps_source_from_subject_and_event -q
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py -q
```

Expected: `PASS` for runtime, Candidate-reference, and Formal Review wakes.

- [ ] **Step 10: Synchronize and commit the source-mapping behavior**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/runtime_gateway.py skills/orchestrator/.skill-package.json tests/test_v8_watchdog_production_host.py
git commit -m "feat: map Runtime Watchdog sources from Subjects"
```

- [ ] **Step 11: Write RED for malformed cursor and tampered Subject fences**

```python
@pytest.mark.parametrize("bad_cursor", ("0", "01", "-1", 1, True))
def test_read_watchdog_events_rejects_bad_cursor_without_publication(tmp_path, bad_cursor):
    gateway, _store, adapter = _gateway(tmp_path)
    with pytest.raises(RuntimeGatewayError) as raised:
        gateway._read_watchdog_events(bad_cursor)
    assert raised.value.code == "RUNTIME_EVENT_CURSOR_INVALID"
    adapter.events.assert_not_called()


def test_read_watchdog_events_rejects_tampered_subject(tmp_path):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    _prepare_and_start(gateway, subject)
    gateway._data["actions"][subject.stable_action_id]["subject"]["campaign_key"] = "campaign:tampered"
    adapter.events = Mock(return_value=_RuntimeEventPage(
        events=(_RuntimeEvent("14", subject.stable_action_id, "state:running"),), next_cursor="14"
    ))
    with pytest.raises(RuntimeGatewayError):
        gateway._read_watchdog_events("13")


def test_read_watchdog_events_rejects_missing_persisted_action(tmp_path):
    gateway, _store, adapter = _gateway(tmp_path)
    adapter.events = Mock(return_value=_RuntimeEventPage(
        events=(_RuntimeEvent("15", "action:missing", "state:running"),),
        next_cursor="15",
    ))
    with pytest.raises(RuntimeGatewayError) as raised:
        gateway._read_watchdog_events("14")
    assert raised.value.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
```

- [ ] **Step 12: Run the exact failure-fence RED tests**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_rejects_bad_cursor_without_publication tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_rejects_tampered_subject tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_rejects_missing_persisted_action -q
```

Expected: `FAIL` until malformed cursors, missing action records, and changed
Subject bytes are rejected before any Watchdog cursor publication.

- [ ] **Step 13: Write the minimum GREEN failure fence**

```python
def _read_watchdog_events(
    self,
    after_cursor: str | None,
) -> _RuntimeCampaignWakePage:
    if after_cursor is not None and (
        type(after_cursor) is not str
        or not after_cursor.isdecimal()
        or int(after_cursor) <= 0
        or str(int(after_cursor)) != after_cursor
    ):
        raise RuntimeGatewayError(
            "RUNTIME_EVENT_CURSOR_INVALID",
            "after_cursor must be one canonical positive decimal cursor",
        )
    self._refresh_before_adapter_io()
    raw_page = self._adapter.events(after_cursor)
    verdict = _RuntimeEventPageProtocol.validate(
        raw_page,
        after_cursor=after_cursor,
    )
    if verdict.kind != "page":
        assert verdict.failure is not None
        self._raise_failure(verdict.failure)
    assert verdict.page is not None
    wakes = tuple(
        self._watchdog_wake_for_event(event)
        for event in verdict.page.events
    )
    return _RuntimeCampaignWakePage(
        events=wakes,
        next_cursor=verdict.page.next_cursor,
    )


def _watchdog_wake_for_event(
    self,
    event: _RuntimeEvent,
) -> _RuntimeCampaignWake:
    record = self._data["actions"].get(event.stable_action_id)
    if type(record) is not dict:
        raise RuntimeGatewayError(
            "RUNTIME_PROVIDER_PROTOCOL_INVALID",
            "event action has no persisted record",
        )
    subject = _subject_from_canonical(record.get("subject"))
    if (
        record.get("subject_digest") != subject.digest
        or subject.stable_action_id != event.stable_action_id
    ):
        raise RuntimeGatewayError(
            "RUNTIME_PROVIDER_PROTOCOL_INVALID",
            "event action and exact persisted Subject differ",
        )
    if (
        event.kind == "candidate:reference"
        or record.get("candidate_reference_emitted") is True
    ):
        source = "candidate"
    elif (
        type(subject) is WorkRunSubject
        and subject.purpose.kind
        in {
            "formal_review",
            "invalid_review_payload_retry",
            "specialist_review",
        }
    ):
        source = "review"
    else:
        source = "runtime"
    return _RuntimeCampaignWake(
        event.cursor,
        subject.repository,
        subject.campaign_key,
        source,
        subject.stable_action_id,
        event.kind,
    )
```

Build every wake before returning the page; one malformed Subject returns no
partial page and the Watchdog source does not advance its cursor.

- [ ] **Step 14: Run the exact failure-fence tests to PASS**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_rejects_bad_cursor_without_publication tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_rejects_tampered_subject tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_rejects_missing_persisted_action -q
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py -q
```

Expected: `7 passed`; no malformed cursor reaches the provider, and no partial
page or Watchdog cursor is published for a missing/tampered Subject.

- [ ] **Step 15: Synchronize and commit the failure-fence behavior**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/runtime_gateway.py skills/orchestrator/.skill-package.json tests/test_v8_watchdog_production_host.py
git commit -m "test: fence Runtime Watchdog cursor and Subject drift"
```

- [ ] **Step 16: Write the production-host composition RED test**

```python
from v8_successor_test_support import public_successor
from v8_watchdog_test_support import NOW, RecordingWakeSource, page, runtime_wake


def test_host_runtime_event_wakes_the_same_installed_advance(
    tmp_path,
    public_successor,
):
    kernel = public_successor._kernel
    source = RecordingWakeSource([
        page(runtime_wake(
            "1",
            "implementation:issue:113",
            campaign=public_successor.handle,
        ))
    ])
    watchdog = public_successor.host.install_campaign_watchdog(
        store_path=tmp_path / "watchdog.db",
        execution_kernel=kernel,
        _runtime_event_source=source,
    )
    watchdog.run_once(NOW)
    assert kernel.inspect(public_successor.handle).work_runs[0].last_wake_ref == (
        "watchdog:runtime:1:implementation:issue:113"
    )
```

- [ ] **Step 17: Run RED for exact host composition only**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_host_runtime_event_wakes_the_same_installed_advance -q
```

Expected: `FAIL` because `ProductionPlanControlStartHost` has no
`install_campaign_watchdog` method; stop before adding the adapter or host
body.

- [ ] **Step 18: Write the minimum GREEN exact host composition**

Add these imports, the concrete Runtime-page adapter, and the exact host method
body. `RuntimeGatewayWatchdogEventSource` is the already named host-private
adapter, not a new package operation.

```python
from .campaign_watchdog import (
    CampaignWatchdog,
    WatchdogEventSource,
    WatchdogWake,
    WatchdogWakePage,
)


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
    return CampaignWatchdog(
        store_path=store_path,
        event_sources=sources,
        campaign_source=execution_kernel,
        advancer=execution_kernel,
    )
```

Candidate and Review events retain the source assigned by the Runtime
projection. The same installed `execution_kernel` object fills both Watchdog
interfaces; the method neither calls the module-level predecessor Kernel nor
constructs another state machine.

- [ ] **Step 19: Run the exact host composition tests to PASS**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py -q
py -3.13 -m pytest tests/test_v8_campaign_watchdog.py tests/test_v8_watchdog_execution_kernel.py tests/test_v8_watchdog_production_host.py -q
```

Expected: `PASS`; the private injected source and the real Runtime-page adapter
both wake the same installed Kernel.

- [ ] **Step 20: Synchronize and commit only host composition**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/plan_control_host.py skills/orchestrator/.skill-package.json tests/test_v8_watchdog_production_host.py
git commit -m "feat: compose Campaign Watchdog in the production host"
```

- [ ] **Step 21: Write RED tests for restart, lost/reordered callbacks, and no public API growth**

```python
from datetime import datetime, timedelta
import sqlite3
from unittest.mock import call, patch

import gwo_v8


def _watchdog_due_row(store_path, handle):
    with sqlite3.connect(store_path) as connection:
        return connection.execute(
            "SELECT next_check_at, progress_digest FROM v8_watchdog_due "
            "WHERE repository=? AND campaign_key=?",
            (handle.repository, handle.campaign_key),
        ).fetchone()


def test_reinstall_rebuilds_lost_due_work_without_native_callback(
    tmp_path,
    public_successor,
):
    watchdog_store = tmp_path / "watchdog.db"
    kernel_store = public_successor._kernel._store_path
    runtime_store = public_successor.host._gateway_store_path
    first = public_successor.host.install_campaign_watchdog(
        store_path=watchdog_store,
        execution_kernel=public_successor._kernel,
        _runtime_event_source=RecordingWakeSource([]),
    )
    first.rebuild_due_queue()
    expected_due = _watchdog_due_row(watchdog_store, public_successor.handle)
    assert expected_due is not None

    with sqlite3.connect(watchdog_store) as connection:
        connection.execute(
            "DELETE FROM v8_watchdog_due WHERE repository=? AND campaign_key=?",
            (
                public_successor.handle.repository,
                public_successor.handle.campaign_key,
            ),
        )

    public_successor.reinstall()
    assert public_successor._kernel._store_path == kernel_store
    assert public_successor.host._gateway_store_path == runtime_store
    restarted = public_successor.host.install_campaign_watchdog(
        store_path=watchdog_store,
        execution_kernel=public_successor._kernel,
        _runtime_event_source=RecordingWakeSource([]),
    )
    assert restarted._store_path == watchdog_store
    assert _watchdog_due_row(watchdog_store, public_successor.handle) == expected_due

    due_now = datetime.fromisoformat(expected_due[0]) + timedelta(seconds=1)
    public_successor._kernel._clock = lambda: due_now
    with patch.object(
        public_successor._kernel,
        "advance",
        wraps=public_successor._kernel.advance,
    ) as advance_spy:
        outcomes = restarted.run_once(due_now.isoformat())
    assert len(outcomes) == 1
    advance_spy.assert_called_once_with(public_successor.handle, None)


def test_older_runtime_callback_after_newer_is_a_noop(
    tmp_path,
    public_successor,
):
    identity = "implementation:issue:113"
    source = RecordingWakeSource([
        page(runtime_wake(
            "22",
            identity,
            campaign=public_successor.handle,
        )),
        page(runtime_wake(
            "21",
            identity,
            campaign=public_successor.handle,
        )),
    ])
    watchdog = public_successor.host.install_campaign_watchdog(
        store_path=tmp_path / "reordered-watchdog.db",
        execution_kernel=public_successor._kernel,
        _runtime_event_source=source,
    )
    snapshot = public_successor._kernel.watchdog_snapshot(public_successor.handle)
    assert snapshot.next_check_at is not None
    before_due = (
        datetime.fromisoformat(snapshot.next_check_at) - timedelta(seconds=1)
    ).isoformat()

    with patch.object(
        public_successor._kernel,
        "advance",
        wraps=public_successor._kernel.advance,
    ) as advance_spy:
        watchdog.run_once(before_due)
        actions_after_newer = tuple(
            action.stable_action_id for action in public_successor.effects.executed
        )
        watchdog.run_once(before_due)

    assert advance_spy.call_args_list == [
        call(
            public_successor.handle,
            f"watchdog:runtime:22:{identity}",
        )
    ]
    assert watchdog.read_cursor("runtime_gateway") == "22"
    assert tuple(
        action.stable_action_id for action in public_successor.effects.executed
    ) == actions_after_newer


def test_watchdog_composition_adds_no_public_workflow_operation():
    possible_operations = {
        "start",
        "advance",
        "inspect",
        "run_once",
        "rebuild_due_queue",
        "install_campaign_watchdog",
    }
    assert set(gwo_v8.__all__) & possible_operations == {
        "start",
        "advance",
        "inspect",
    }
    for private_name in (
        "CampaignWatchdog",
        "RuntimeGatewayWatchdogEventSource",
        "install_campaign_watchdog",
    ):
        assert private_name not in gwo_v8.__all__
        assert not hasattr(gwo_v8, private_name)
```

- [ ] **Step 22: Run the exact restart/reorder/public-surface tests to RED**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_reinstall_rebuilds_lost_due_work_without_native_callback tests/test_v8_watchdog_production_host.py::test_older_runtime_callback_after_newer_is_a_noop tests/test_v8_watchdog_production_host.py::test_watchdog_composition_adds_no_public_workflow_operation -q
```

Expected: `FAIL` because a fresh host installation does not yet rebuild the
deleted derived due row and an older page can replace the newer saved cursor.
The public-surface assertion must already pass; stop with the two behavior
failures recorded before editing production code.

- [ ] **Step 23: Write the minimum GREEN restart/reorder composition**

After the existing page validation and exact-replay check, ignore a validated
older cursor without dispatch or publication. Then rebuild due work before the
host returns a newly installed Watchdog:

```python
# campaign_watchdog.py, inside run_once's source transaction
if (
    saved is not None
    and saved[0] is not None
    and page.next_cursor is not None
    and int(page.next_cursor) < int(saved[0])
):
    connection.rollback()
    continue

# plan_control_host.py, replacing the direct CampaignWatchdog return
watchdog = CampaignWatchdog(
    store_path=store_path,
    event_sources=sources,
    campaign_source=execution_kernel,
    advancer=execution_kernel,
)
watchdog.rebuild_due_queue()
return watchdog
```

Cursor inputs have already passed the canonical positive-decimal validation,
so numeric ordering is closed and deterministic. Pass the same installed
Kernel object as both interfaces; never construct a predecessor Kernel or
invoke a legacy reconciliation driver.

- [ ] **Step 24: Run restart/reorder/public-surface GREEN and cumulative tests**

```powershell
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_reinstall_rebuilds_lost_due_work_without_native_callback tests/test_v8_watchdog_production_host.py::test_older_runtime_callback_after_newer_is_a_noop tests/test_v8_watchdog_production_host.py::test_watchdog_composition_adds_no_public_workflow_operation -q
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py -q
py -3.13 -m pytest tests/test_orchestrator_package.py -q
```

Expected: `3 passed`, followed by cumulative `PASS`; reinstall restores the due
row before the first loop, no callback is needed for one due `advance`, the
older callback causes no second dispatch or external action, and only
`start`/`advance`/`inspect` remain public workflow operations.

- [ ] **Step 25: Synchronize, check, and commit the restart/reorder behavior**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/campaign_watchdog.py skills/orchestrator/scripts/gwo_v8/plan_control_host.py skills/orchestrator/.skill-package.json tests/test_v8_watchdog_production_host.py
git commit -m "feat: prove restart-safe Watchdog host composition"
```

- [ ] **Step 26: Run the final release gate**

```powershell
py -3.13 -m pytest -q
py -3.13 scripts/quick_validate.py
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/orchestrator/scripts/gwo_v8/campaign_watchdog.py skills/orchestrator/scripts/gwo_v8/execution_kernel.py skills/orchestrator/scripts/gwo_v8/runtime_gateway.py skills/orchestrator/scripts/gwo_v8/plan_control_host.py tests/v8_watchdog_test_support.py tests/test_v8_campaign_watchdog.py tests/test_v8_watchdog_execution_kernel.py tests/test_v8_watchdog_production_host.py skills/orchestrator/.skill-package.json
git commit -m "feat: resume V8 Campaigns without LLM polling (#113)"
```

- [ ] **Step 27: Open PR, run independent Standards/Spec review, merge only exact green SHA, and read back Issue #113 CLOSED plus post-merge main CI success.**

## #113 Acceptance Coverage

| Acceptance item | Planned proof |
| --- | --- |
| Runtime, Candidate-reference, Review, and hosted-check events are hints through one `advance` | Tasks 1, 3, and 5 source mapping, identical wake-reference replay, and installed-Kernel host test |
| CandidateGate authoritative readback and Kernel receipt persistence | Candidate Assurance foundation gate plus Task 2 canonical receipt projection/revalidation |
| Only Kernel transition, normalized permission, or exact Candidate receipt resets stale progress | Task 2 digest-input and false-progress matrix |
| Reports, logs, workspace head, checkpoint text, and duplicates are non-authoritative | Task 2 parametrized hint test and Task 3 replay test |
| Persisted due times reconstruct after restart/lost callback | Task 3 rebuild/restart test and Task 5 no-callback host test |
| Thirty-minute zero-LLM readback before one diagnosis | Task 4 four-digest `StaleBindingObservation` and ordering counters |
| At most one diagnosis per initial/replacement binding; no transcript/daemon restart | Task 4 stable IDs, diagnosed-binding projection, classified-state matrix, and structural capability assertions |
| Watchdog owns no workflow state or public operation | Tasks 1/2 read-only store/projection tests and Task 5 package export regression |

## Serialized Package-Manifest Handoff

- Shared Candidate Assurance Task 1 owns the only pre-#113 edits to
  `candidate_gate.py` and the Candidate-receipt portion of
  `execution_kernel.py`.
- After that foundation merges, #113 owns
  `campaign_watchdog.py`, the liveness/stale portions of
  `execution_kernel.py`, Runtime wake projection, Watchdog host composition,
  and the four Watchdog test files listed above.
- Do not run the remaining package-changing #114 implementation concurrently
  with #113, even when its Python/test write set appears disjoint: every
  Watchdog package commit regenerates
  `skills/orchestrator/.skill-package.json`. Finish and merge each owner in the
  serialized manifest lane, then rebase the next owner before its first
  package mutation.
- Only a true bypass task may run in parallel: its entire write set must be
  non-overlapping, it must not run package sync, and it must not modify or
  require regeneration of `skills/orchestrator/.skill-package.json`. Otherwise
  stop and queue it behind the current manifest-lane owner.
- For every #113 package commit, preserve the executable order
  `sync_orchestrator.py` -> `sync_orchestrator.py --check` -> stage the changed
  package files plus `.skill-package.json` -> commit them together.
- #116 later supplies `hosted_check_source`; #113 leaves that typed optional
  seam empty rather than implementing BatchIntegrator early.
