# GWO V8 Campaign Watchdog — Task 5 Report

- **Status:** `DONE_WITH_CONCERNS`
- **Worktree:** `D:\Workstation\gwo-worktrees\issue-136`
- **Branch:** `codex/gwo-v8-ga-plan`
- **Implemented scope:** Steps 1–25 only. RuntimeGateway host-private Watchdog page/cursor readback and Subject/source mapping are composed with `CampaignWatchdog` and `ProductionPlanControlStartHost`; restart due-queue rebuild, reordered-callback no-op, malformed-cursor/missing-action/tampered-Subject fences, and public-surface acceptance are covered.
- **Explicitly not implemented:** #116 hosted-check beyond the optional seam; no `gwo_v8` public workflow API growth.

## Commits

1. `90e4493` — `feat: add private Runtime Watchdog page readback`
2. `d09e54e` — `feat: map Runtime Watchdog sources from Subjects`
3. `ca01ef5` — `test: fence Runtime Watchdog cursor and Subject drift`
4. `ef3927d` — `feat: compose Campaign Watchdog in the production host`
5. `2e70d01` — `feat: prove restart-safe Watchdog host composition`

The report artifact is committed separately; its final commit SHA is included in the completion response.

Every package-changing commit ran, before the commit:

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
```

`skills/orchestrator/.skill-package.json` was staged with each package-changing commit.

## Focused and cumulative tests run

All commands below were run with Python 3.13 and passed:

```text
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_passes_cursor_once_and_returns_page_cursor -q
1 passed

py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_maps_source_from_subject_and_event -q
4 passed

py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_rejects_bad_cursor_without_publication tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_rejects_tampered_subject tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_rejects_missing_persisted_action -q
7 passed

py -3.13 -m pytest tests/test_v8_watchdog_production_host.py -q
12 passed  (Step 14 cumulative)

py -3.13 -m pytest tests/test_v8_watchdog_production_host.py -q
13 passed  (Step 19 host-composition cumulative)

py -3.13 -m pytest tests/test_v8_campaign_watchdog.py tests/test_v8_watchdog_execution_kernel.py tests/test_v8_watchdog_production_host.py -q
78 passed, 2 warnings

py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_reinstall_rebuilds_lost_due_work_without_native_callback tests/test_v8_watchdog_production_host.py::test_older_runtime_callback_after_newer_is_a_noop tests/test_v8_watchdog_production_host.py::test_watchdog_composition_adds_no_public_workflow_operation -q
3 passed

py -3.13 -m pytest tests/test_v8_watchdog_production_host.py -q
16 passed  (Step 24 final Task 5 file)

py -3.13 -m pytest tests/test_orchestrator_package.py -q
15 passed
```

## Concerns / verification boundary

- The Task 5 final full gate (`py -3.13 -m pytest -q`, brief Step 26) was **not run**, per instruction.
- A later combined cumulative run was stopped before completion; it produced only partial output and is not counted as a passing result.
- The existing `public_successor` fixture contains multiple Work Runs; the focused host assertions use the Work Run that actually receives the wake, and the due-advance assertion uses the test-specific stale-readback setup. This is test-fixture adaptation only; production ownership/invariants remain unchanged.

## Fix round 1 (independent review findings I-1, I-2, M-1)

### Changes

- `RuntimeGateway._read_watchdog_events` now reuses the existing
  `_runtime_event_cursor_value` contract before any refresh or provider
  `events()` call. This preserves `None` as the origin cursor while enforcing
  ASCII canonical decimal text in the bounded `1..2**63-1` range and prevents
  `ValueError` leakage from oversized decimal input.
- Added regression coverage for overflow (`str(2**63)`), a 5000-digit decimal,
  and full-width Unicode decimal text (`"１２"`). Each asserts
  `RUNTIME_EVENT_CURSOR_INVALID`, zero provider calls, and zero refresh calls.
- Added a default-composition host test with `_runtime_event_source=None` and a
  strict fake RuntimeGateway page. It spies on the real
  `RuntimeGatewayWatchdogEventSource.read` and asserts the event reaches the
  exact already-installed `ExecutionKernel.advance(handle, wake_ref)` once;
  the Watchdog campaign source and advancer are both that same Kernel.
- Strengthened the injected-source host test with the same exact advance spy.
  `plan_control_host.py` required no production change: its existing
  composition was already correct, and the new test now covers that default
  branch.

### TDD evidence

Cursor-boundary RED, before the production change:

```text
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_rejects_cursor_contract_values_before_adapter_io -q
2 failed, 1 passed in 1.02s
```

The overflow case reached the adapter once; the 5000-digit case leaked
`ValueError: Exceeds the limit (4300 digits) for integer string conversion`;
the non-ASCII case was already rejected by the old canonical-text check.

The default-composition and strengthened exact-advance tests were coverage
regressions for an already-correct composition path and passed once their
strict fake accepted the host's repeated gateway-builder calls used by normal
Kernel readback:

```text
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_host_runtime_event_wakes_the_same_installed_advance -q
1 passed

py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_host_default_runtime_gateway_source_wakes_the_same_installed_advance -q
1 passed
```

### Fix-round verification

```text
py -3.13 -m pytest tests/test_v8_watchdog_production_host.py::test_read_watchdog_events_rejects_cursor_contract_values_before_adapter_io -q
3 passed

py -3.13 -m pytest tests/test_v8_watchdog_production_host.py -q
20 passed

py -3.13 -m pytest tests/test_v8_campaign_watchdog.py tests/test_v8_watchdog_execution_kernel.py tests/test_v8_watchdog_production_host.py -q
85 passed, 2 warnings

py -3.13 -m pytest tests/test_orchestrator_package.py -q
15 passed

py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
PASS (both commands)

git diff --check
PASS
```

The first package-test attempt reported `2 failed, 13 passed` because the
manifest was stale after the source/test edits; the required sync and check
then restored the fixed point and the rerun was `15 passed`.

The Task 5 full release gate remains intentionally unrun. The fix-round
commit SHA is included in the completion response.
