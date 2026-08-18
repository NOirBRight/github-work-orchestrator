# Task 5 fix report — release-gate section

## Release-gate fix wave

- **Status:** release parser and local pre-tag canary checks are GREEN.
- **Worktree:** `D:\Workstation\gwo-worktrees\gwo-v8-local-only-contract`
- **Scope:** only the release verifier, metadata renderer, release metadata
  tests, and this release report were included. The dirty root verifier,
  local runner, and their tests were left untouched.
- **Files:** scripts/verify_v8_ga_release.py, scripts/render_v8_ga_metadata.py, tests/test_v8_release_metadata.py, and this report.

The release gate now rejects the remaining pytest early-stop, selection,
configuration, and help aliases, including short and `--option=value`
spellings, while still rejecting conflicting `command`/`arguments`/`argv`
representations. Local pre-tag verification requires the canary payload to
declare exactly `acceptance_mode: local-only-v1` and recursively rejects
Hosted-CI, PR, remote, publication, and workflow fields. Compact forbidden
field aliases are covered and rejected. Existing renderer rejection of
Hosted/PR evidence remains in the release write set.

## TDD evidence

### RED

```text
py -3.13 -m pytest tests/test_v8_release_metadata.py -q
7 failed, 84 passed

py -3.13 -m pytest tests/test_v8_release_metadata.py -k "remaining_pytest_gate_options or extended_forbidden_field_aliases or local_pre_tag" -q
48 failed, 51 passed, 65 deselected

py -3.13 -m pytest tests/test_v8_release_metadata.py -k extended_forbidden_field_aliases -q
11 failed, 20 passed, 133 deselected

py -3.13 -m pytest tests/test_v8_release_metadata.py -k remaining_pytest_gate_options -q
4 failed, 64 passed, 100 deselected
```

The failures were the intended missing mode/recursive canary checks, pytest
spellings, and compact aliases.

### GREEN

```text
py -3.13 -m pytest tests/test_v8_release_metadata.py -k "remaining_pytest_gate_options or extended_forbidden_field_aliases or local_pre_tag or local_verification_subject_tree" -q
100 passed, 64 deselected

py -3.13 -m pytest tests/test_v8_release_metadata.py -k "remaining_pytest_gate_options or extended_forbidden_field_aliases" -q
99 passed, 69 deselected

py -3.13 -m pytest tests/test_v8_release_metadata.py -q
168 passed

py -3.13 -m ruff check scripts/verify_v8_ga_release.py scripts/render_v8_ga_metadata.py tests/test_v8_release_metadata.py
All checks passed

git diff --check
PASS
```

## Deferred findings and concerns

- The approved ledger's general diagnostic JSON canonicalization and exact
  Ticket title/body pinning remain deferred.
- Pytest emits a Windows `PermissionError` from its atexit temporary-directory
  cleanup after successful runs; all listed pytest commands returned exit 0.
- No root verifier/runner changes, production mutation, activation, tag,
  merge, push, or agent dispatch was performed.

## Bounded GA parser conflict fix

The reviewed release-parser P1 was that canonical local verification collected
`full_suite`, `full_pytest`, and designated full-command counts but returned the
last count, allowing conflicting evidence to pass based on field order. The
minimum fix now rejects any disagreement with the existing
`GA_LOCAL_VERIFICATION_PYTEST_COUNT_MISMATCH` rule and returns the shared count
only when all candidates agree. Hosted-CI parsing and the existing per-result,
summary/log, chunk, and command-representation conflict rules are unchanged.

### TDD evidence

**RED** — before the parser change:

```text
py -3.13 -m pytest tests/test_v8_release_metadata.py -k conflicting_full_pytest_counts -q
1 failed, 168 deselected
```

The regression failed because conflicting counts were accepted instead of
raising `GA_LOCAL_VERIFICATION_PYTEST_COUNT_MISMATCH`.

**GREEN and focused checks**:

```text
py -3.13 -m pytest tests/test_v8_release_metadata.py -k conflicting_full_pytest_counts -q
1 passed, 168 deselected

py -3.13 -m pytest tests/test_v8_release_metadata.py -q
169 passed

py -3.13 -m ruff check --isolated --no-cache scripts/verify_v8_ga_release.py tests/test_v8_release_metadata.py
All checks passed!

git diff --check -- scripts/verify_v8_ga_release.py tests/test_v8_release_metadata.py
PASS
```

Only the release parser, release metadata test, and this report are in the
bounded fix scope. No root verifier/runner files, production state, merge,
push, or tag were changed.
