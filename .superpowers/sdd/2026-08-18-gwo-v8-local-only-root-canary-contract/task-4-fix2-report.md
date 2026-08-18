# Task 4 fix round 2 report — top-level full pytest evidence

## Finding fixed

Canonical `local-only-v1` `full_suite` and `full_pytest` mappings now fail
closed unless they contain command-bearing `command`, `arguments`, or `argv`
evidence. The existing canonical command validator still proves a full pytest
command, with the top-level field providing the designation, and rejects
selectors and positional package paths. Legacy local verification modes remain
on their existing parser path.

## TDD evidence

### RED

```text
py -3.13 -m pytest tests/test_v8_release_metadata.py -k commandless_full_pytest -q
```

Result: **2 failed, 62 deselected**, exit code 1. Both commandless
`full_suite` and `full_pytest` mappings were accepted instead of raising the
expected `GA_LOCAL_VERIFICATION_PYTEST_FAILED` error.

### GREEN

```text
py -3.13 -m pytest tests/test_v8_release_metadata.py -k commandless_full_pytest -q
```

Result: **2 passed, 62 deselected**, exit code 0.

```text
py -3.13 -m pytest tests/test_v8_release_metadata.py -q
```

Result: **64 passed**, exit code 0.

```text
py -3.13 -m ruff check scripts/verify_v8_ga_release.py tests/test_v8_release_metadata.py
git diff --check
```

Both checks passed.

## Changed files

- `scripts/verify_v8_ga_release.py`
- `tests/test_v8_release_metadata.py`
- `.superpowers/sdd/2026-08-18-gwo-v8-local-only-root-canary-contract/task-4-fix2-report.md`

The pre-existing Task 2/3 working-tree edits were left untouched.
