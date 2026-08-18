# Task 1 fix report — strict Ticket manifest contract parity

## Finding addressed

The strict manifest loader accepted an empty `contract.body` and normalized
non-canonical ticket states such as `OPEN` before validation. The fix mirrors
the existing v2 PlanControl rules: the body must be non-empty text and the
state is validated without case-folding, so only canonical lowercase `open`
can pass the ready check.

## TDD evidence

- **RED:** `py -3.13 -m pytest tests/test_v8_root_canary_tickets.py -k "empty_contract_body or uppercase_open" -q`
  — **2 failed, 42 deselected**. Both regressions failed because the current
  loader did not raise for a digest-consistent empty body or uppercase `OPEN`.
- **GREEN:** `py -3.13 -m pytest tests/test_v8_root_canary_tickets.py -k "empty_contract_body or uppercase_open" -q`
  — **2 passed, 42 deselected**.
- **Full Ticket suite:** `py -3.13 -m pytest tests/test_v8_root_canary_tickets.py -q`
  — **44 passed**.
- **Ruff:** `ruff check scripts/provision_v8_root_canary.py tests/test_v8_root_canary_tickets.py`
  — **All checks passed**.

Pytest emitted an environment-specific Windows atexit cleanup
`PermissionError` after the successful runs; the test results themselves
completed with no failures.

## Changed files

- `scripts/provision_v8_root_canary.py` — require non-empty contract body and
  preserve exact state spelling during manifest validation.
- `tests/test_v8_root_canary_tickets.py` — add digest-consistent regressions
  for empty body and uppercase `OPEN`.
- `.superpowers/sdd/2026-08-18-gwo-v8-local-only-root-canary-contract/task-1-fix-report.md`
  — record RED/GREEN evidence, findings, scope, and verification.

Pre-existing changes in the Task 2/4 files were left untouched and are not
part of this fix.

## Final status

**PASS — Task 1 Important finding fixed with the scoped changes above.**
