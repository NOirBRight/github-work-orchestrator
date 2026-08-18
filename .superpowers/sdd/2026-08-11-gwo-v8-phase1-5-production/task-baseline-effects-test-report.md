# GWO V8 production-effects baseline test repair

- Date: 2026-08-18 (Asia/Shanghai)
- Repository: `D:\Workstation\github-work-orchestrator`
- Baseline branch commit: `23abca3`
- `origin/main`: `c2a3805`
- Scope: one test-only timing adjustment for
  `test_restart_recovers_an_unmarked_runtime_effect_without_duplicate_provider_call`.

## TDD evidence

### RED / baseline

The reported baseline reproduction was the intermittent
`EFFECT_EXECUTION_IN_PROGRESS` failure: the test's `0.01` second claim wait
could expire before the restarted probe observed the stale claim. The supplied
10-run baseline was 4 failures and 6 passes on the branch, and the same test
also failed at exact `origin/main`. That establishes a baseline test timing
defect rather than a production change in this task.

The same focused command happened to pass 20/20 times during this session;
that is consistent with the reported intermittent failure and is not treated
as evidence that the original timing bound was reliable.

### GREEN

The only code change is in
`tests/test_v8_production_effects.py`: the target test now monkeypatches
`_EFFECT_CLAIM_WAIT_SECONDS` to `0.05` instead of `0.01`. The poll interval and
all recovery assertions are unchanged.

## Why the timing bound is non-contractual

The test-specific monkeypatch is only an execution-time acceleration of the
stale-claim transition. The behavior under test is the durable readback-first
recovery and exactly one provider dispatch, not completion within 10 ms. The
new 50 ms bound remains finite: if recovery evidence is absent, the production
code still fails closed with `EFFECT_EXECUTION_IN_PROGRESS`; it does not turn a
real recovery failure into a pass. Production defaults and production
semantics were not changed.

## Verification

Focused repeated run, ten independent pytest processes with unique temporary
directories:

```powershell
for($i=1;$i -le 10;$i++) {
  $base=Join-Path $env:TEMP ("gwo-v8-final-focused-{0}-{1}" -f $PID,$i)
  py -3.13 -B -m pytest -q tests/test_v8_production_effects.py `
    -k test_restart_recovers_an_unmarked_runtime_effect_without_duplicate_provider_call `
    --basetemp $base
}
```

Result: **10 passed, 0 failed** (each run exited 0).

Whole production-effects suite:

```powershell
$base=Join-Path $env:TEMP ("gwo-v8-final-suite-{0}" -f $PID)
py -3.13 -B -m pytest -q tests/test_v8_production_effects.py --basetemp $base
```

Result: **13 passed, 1 pre-existing `PytestAssertRewriteWarning`**; exit 0.

Diff check:

```powershell
git diff --cached --check
```

Result: no output; exit 0.

The staged commit contains only:

- `tests/test_v8_production_effects.py`
- this report

No production code, other tests/reports, or production mutation was performed.

## Unresolved P1s

None identified in this scoped test-only repair.
