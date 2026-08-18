# Task 1 fix 2 report — strict Ticket manifest contract parity

## Findings addressed

This fix closes the two remaining Important v2-parity gaps identified in
`task-1-fix-review.md`, without broadening the manifest contract:

- `contract.number` now uses the loader's exact-positive-integer rule before
  projection and digest validation, so a digest-consistent `195.0` is
  rejected.
- Comment IDs are now required to be unique and in the existing v2 canonical
  ascending order, matching PlanControl's `_normalize_ticket_contract` rule.

## TDD evidence

- **RED:**
  `py -3.13 -m pytest tests/test_v8_root_canary_tickets.py -k "float_contract_number or reordered_comment_ids" -q`
  — **2 failed, 44 deselected**, exit 1. Both failures were the intended
  missing rejections: the current loader accepted the digest-consistent
  float number and reordered comments.
- **GREEN:** The same focused command — **2 passed, 44 deselected**, exit 0.
- **Full Ticket suite:**
  `py -3.13 -m pytest tests/test_v8_root_canary_tickets.py -q`
  — **46 passed**, exit 0.
- **Ruff:**
  `ruff check scripts/provision_v8_root_canary.py tests/test_v8_root_canary_tickets.py`
  — **All checks passed**, exit 0.
- `git diff --check` — clean.

The pytest runs emitted the known Windows atexit temporary-directory cleanup
`PermissionError` after the successful results; the pytest commands returned
exit 0 and reported no test failures.

## Changed files

- `scripts/provision_v8_root_canary.py` — enforce exact contract number type
  and canonical ascending comment IDs.
- `tests/test_v8_root_canary_tickets.py` — add digest-consistent regressions
  for a float contract number and reordered comments.
- `.superpowers/sdd/2026-08-18-gwo-v8-local-only-root-canary-contract/task-1-fix2-report.md`
  — record RED/GREEN evidence and verification results.

Pre-existing Task 2/3/4 worktree changes were left untouched and are excluded
from the focused commit.

## Final status

**PASS — both remaining Task 1 Important v2-parity gaps are fixed within the
requested scope.**
