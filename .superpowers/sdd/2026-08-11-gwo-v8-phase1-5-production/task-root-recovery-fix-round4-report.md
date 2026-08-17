# Root Canary recovery slice — fix round 4 report

## Scope and guardrails

- Worktree: `D:\Workstation\github-work-orchestrator\.gwo-worktrees\root-canary-recovery-fix-round1`
- Round-three implementation baseline: `04f949f`.
- The isolated worktree was at `ba38a95` at task start; that commit is the round-three report-only child of `04f949f`.
- Round-four source/test commit: `f8a9878e2c50ef82783ede37de553e1cc46fe132`.
- Only the allowed recovery test and the requested report were changed for round four. The round-three source files were not behaviorally changed.
- No main checkout, production activation, writer transition, release metadata, or production state was mutated.

## Finding closed: genuine operation-boundary RED

`tests/test_v8_root_canary_recovery.py::test_fault_proxy_fails_closed_when_parent_swaps_at_temp_create` now treats `_open_lock_file` as an optional seam (`getattr(..., None)` and `raising=False`). Therefore the pre-fix `6545fd5` run does not error while looking up a missing helper. It reaches the old behavior and fails for the expected reason: the old implementation did not raise `ValueError`.

The corrected test was edited before the RED run. The RED and GREEN were run from temporary archives, so neither changed the isolated worktree checkout.

### RED — corrected test against `6545fd5`

Command shape:

```powershell
git archive 6545fd5 | tar -xf - -C $red
Copy-Item tests\test_v8_root_canary_recovery.py $red\tests\test_v8_root_canary_recovery.py
py -3.13 -B -m pytest -q --basetemp $red\.pytest-basetemp tests/test_v8_root_canary_recovery.py::test_fault_proxy_fails_closed_when_parent_swaps_at_temp_create
```

Result:

```text
FAILED tests/test_v8_root_canary_recovery.py::test_fault_proxy_fails_closed_when_parent_swaps_at_temp_create
E   Failed: DID NOT RAISE <class 'ValueError'>
1 failed, 1 warning
RED_EXIT_CODE=1
```

This is the recorded RED evidence; no prior missing-attribute failure is used as RED evidence.

### GREEN — corrected test against `04f949f`

Command shape:

```powershell
git archive 04f949f | tar -xf - -C $green
Copy-Item tests\test_v8_root_canary_recovery.py $green\tests\test_v8_root_canary_recovery.py
py -3.13 -B -m pytest -q --basetemp $green\.pytest-basetemp tests/test_v8_root_canary_recovery.py::test_fault_proxy_fails_closed_when_parent_swaps_at_temp_create
```

Result:

```text
1 passed, 1 warning
GREEN_EXIT_CODE=0
```

## Ruff gate

Local Ruff: `ruff 0.15.12`.

Command:

```powershell
$files = @(
  'scripts/v8_root_canary_fault_proxy.py',
  'skills/orchestrator/scripts/gwo_v8/production_effects.py',
  'tests/test_v8_root_canary_recovery.py',
  'tests/test_v8_production_effects.py'
)
py -3.13 -B -m ruff check --no-cache @files
```

Result:

```text
All checks passed!
RUFF_EXIT_CODE=0
```

No slice-attributable Ruff findings remain.

## Verification

Focused effects/recovery suites:

```powershell
py -3.13 -B -m pytest -q tests/test_v8_production_effects.py tests/test_v8_root_canary_recovery.py --basetemp <short-isolated-temp>
```

Result: `34 passed, 1 warning; exit 0`.

Adjacent suites:

```powershell
py -3.13 -B -m pytest -q tests/test_v8_execution_kernel.py tests/test_v8_production_host.py --basetemp <short-isolated-temp>
```

Result: `32 passed; exit 0`.

Compileall:

```powershell
py -3.13 -B -m compileall -q scripts/v8_root_canary_fault_proxy.py skills/orchestrator/scripts/gwo_v8/production_effects.py tests/test_v8_root_canary_recovery.py tests/test_v8_production_effects.py
```

Result: `COMPILEALL_EXIT_CODE=0`.

Diff check:

```powershell
git diff --check
```

Result: `DIFF_CHECK_EXIT_CODE=0`.

Residual: the focused suite retains one existing `PytestAssertRewriteWarning` for `v8_production_test_support`; there are no test failures, Ruff findings, compile errors, or diff-check findings.

## Historical aggregate diff-check residual

A final aggregate command was also run:

```powershell
git diff --check 04f949f..HEAD
```

It reported `exit 2` for the pre-existing round-three report file at `.superpowers/sdd/2026-08-11-gwo-v8-phase1-5-production/task-root-recovery-fix-round3-report.md:60` (`new blank line at EOF`). This is inherited from `ba38a95`, outside the round-four slice, and was not changed. The round-four scoped checks (`git diff --check`, the round-four code commit range, and the round-four report commit range) all exited `0`.
## Final status

- Source/test commit: `f8a9878e2c50ef82783ede37de553e1cc46fe132`.
- Preceding round-three report commit: `ba38a95dc9684a7333407111b750da0f6861c3ff`.
- The round-four report is committed in the following documentation commit.
- No production mutation was performed.