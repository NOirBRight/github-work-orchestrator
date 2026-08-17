# Task 6 GA release-contract review evidence

Branch: `codex/phase5-ga-release-contract`
Worktree: `D:\Workstation\github-work-orchestrator\.gwo-worktrees\phase5-ga-release-contract`

## TDD RED

Before changing either release script, the new Task 6 regressions were run with:

```powershell
$base = Join-Path $env:TEMP 'gwo-ga-release-contract-red-20260817'; py -3.13 -m pytest tests/test_v8_release_metadata.py -q --basetemp $base
```

Result: `11 failed, 17 passed in 0.80s`. The failures covered the missing
default-v8 `campaign_key: null` acceptance, origin/main stabilization, explicit
checkout/remote binding, canonical JSON rejection, recursive `commitHash`
rejection, non-finite metadata, renderer identity binding, durable publication
sync, and pre-tag commit/tree binding for post-release evidence.

## GREEN and required checks

```powershell
$base = Join-Path $env:TEMP 'gwo-ga-release-contract-green5'; py -3.13 -m pytest tests/test_v8_release_metadata.py tests/test_v8_clean_install.py -q --basetemp $base
```

Result: `32 passed in 7.42s`.

```powershell
py -3.13 -m ruff check scripts/render_v8_ga_metadata.py scripts/verify_v8_ga_release.py tests/test_v8_release_metadata.py tests/test_v8_clean_install.py
```

Result: `All checks passed!`

```powershell
py -3.13 -m compileall -q scripts/render_v8_ga_metadata.py scripts/verify_v8_ga_release.py tests/test_v8_release_metadata.py tests/test_v8_clean_install.py
git diff --check
```

Result: compileall exited 0; `git diff --check` exited 0. No GitHub, tag,
release, production, or real release-state command was run.

## Task 6 re-review TDD

The three re-review fixes were covered by four focused regressions. The tests
were added before changing either release script.

### RED

```powershell
py -3.13 -m pytest tests/test_v8_release_metadata.py -q -k 'post_release_rejects_pre_tag_receipt_not_bound_to_static_record or post_release_rechecks_pre_tag_commit_tree_invariants_before_archive or post_release_archives_tag_subject_by_immutable_commit_sha or renderer_rejects_symlinked_output_target_before_backup_or_replace' --basetemp (Join-Path $env:TEMP 'gwo-ga-release-contract-red-re-review2-20260817')
```

Exit code: `1`.

```text
4 failed, 30 deselected in 0.65s
```

The failures were the unbound pre-tag receipt being accepted, the post-release
gate reaching archive after a changed candidate tree, archiving by the mutable
tag name instead of the captured commit SHA, and the renderer following a
symlinked changelog target.

### GREEN

The focused post-release regressions passed after the verifier fix:

```text
4 passed, 30 deselected in 0.33s
```

The renderer regression then passed after the publication-boundary fix:

```text
1 passed, 33 deselected in 0.33s
```

The full Task 6 focused suite passed with:

```powershell
py -3.13 -m pytest tests/test_v8_release_metadata.py tests/test_v8_clean_install.py -q --basetemp (Join-Path $env:TEMP 'gwo-ga-release-contract-green-final-20260817')
```

Result: exit code `0`; `36 passed in 7.12s`.

No GitHub, tag, release, production, or real release-state command was run.
