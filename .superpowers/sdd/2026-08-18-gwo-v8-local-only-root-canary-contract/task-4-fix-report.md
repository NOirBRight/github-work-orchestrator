# Task 4 fix report — release verifier P1 findings

## Findings fixed

- **P1 full pytest boundary:** canonical `local-only-v1` manifests now require
  a designated `full`, `full-suite`, or `full_pytest` result with an actual
  pytest command, no selector or positional package path, `exit_code == 0`, a
  successful `status`, and an explicit positive count. The legacy `local-only`
  and `Local Verification Only` parser boundary remains unchanged.
- **P1 forbidden fields:** recursive local evidence validation now rejects
  normalized Hosted/CI/PR/publication/remote-target prefixes and their
  camel-case/acronym aliases, including `hosted_ci_suite`, `ci_run`,
  `pull_request_merge_mapping`, `publication_receipt_digest`, and
  `remote_target_sha`.

## TDD evidence

### RED

```text
py -3.13 -m pytest tests/test_v8_release_metadata.py -k "non_full_pytest_commands or full_pytest_result_fields or extended_forbidden_field_aliases" -q
```

Result: **14 failed, 48 deselected**, exit code 1. The failures were the
expected missing rejections for selector/package-only commands, absent result
fields, and the extended forbidden aliases.

### GREEN

```text
py -3.13 -m pytest tests/test_v8_release_metadata.py -k "non_full_pytest_commands or full_pytest_result_fields or extended_forbidden_field_aliases" -q
```

Result: **14 passed, 48 deselected**, exit code 0.

```text
py -3.13 -m pytest tests/test_v8_release_metadata.py -q
```

Result: **62 passed**, exit code 0.

```text
py -3.13 -m ruff check scripts/verify_v8_ga_release.py tests/test_v8_release_metadata.py
git diff --check
```

Both checks passed.

## Changed files

- `scripts/verify_v8_ga_release.py`
- `tests/test_v8_release_metadata.py`
- `.superpowers/sdd/2026-08-18-gwo-v8-local-only-root-canary-contract/task-4-fix-report.md`

Unrelated Task 2/3 working-tree edits were left untouched and are excluded
from the focused fix commit.

## Final status

**PASS — both Task 4 P1 findings fixed within the requested scope.**
