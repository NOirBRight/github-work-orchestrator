# Task 8 implementation report

## Foundation boundary

The composed branch was kept at the local #113 merge baseline. The required
execution-kernel boundary check was empty:

```powershell
git diff 07086ce..15f8bf7 -- execution_kernel.py
```

The foundation CandidateReceipt and #113 watchdog baseline passed before the
Task 8 implementation:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_receipt_kernel.py tests/test_v8_watchdog_execution_kernel.py -q
```

Result: `53 passed, 2 warnings`.

## RED

The three candidate-budget regressions were added before changing the Kernel:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_budget_kernel.py -q
```

Result: `3 failed` because the run had no candidate history fields and the
fourth distinct Candidate remained `Running` instead of producing the durable
Decision.

## GREEN

The focused budget tests passed after adding the serialized histories,
canonical receipt readback, three-distinct-OID bound, and restart-preserved
state:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_budget_kernel.py -q
```

Result: `3 passed, 1 warning`.

The required regression suites passed:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_receipt_kernel.py tests/test_v8_watchdog_execution_kernel.py -q
py -3.13 -m pytest tests/test_v8_execution_kernel.py tests/test_v8_successor_execution_kernel.py -q
```

Results: `53 passed, 2 warnings`; `70 passed`.

The generated package and validation checks passed:

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/quick_validate.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
```

## Changes

- Added `candidate_commit_oids` and `candidate_receipt_digests` to new and
  migrated Work Run state without overwriting historical repair, replacement,
  or #113 fields.
- Added the serialized CandidateReceipt budget adapter at the beginning of
  `_perform_due_effect`; the fourth distinct bound Candidate persists its
  receipt digest and releases the slot into `CandidateBudgetExhausted` before
  any external effect.
- Added the three real kernel regressions and synchronized the generated
  manifest.

Task 9 was not started and no remote mutation or push was performed.
