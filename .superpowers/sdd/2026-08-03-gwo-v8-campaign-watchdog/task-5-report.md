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
