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
