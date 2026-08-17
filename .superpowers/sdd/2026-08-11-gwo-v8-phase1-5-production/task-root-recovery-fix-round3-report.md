# Root Canary recovery slice — fix round 3 report

## Scope and guardrails

- Worktree: `D:\Workstation\github-work-orchestrator\.gwo-worktrees\root-canary-recovery-fix-round1`
- Branch: `codex/root-canary-recovery-fix-round1`
- Starting commit: `6545fd5`
- No changes were made to the main checkout, activation, writer transition, release metadata, or production state.
- No production mutation, Activation Receipt, writer cutover, or GA tag was executed.

## Findings closed

### 1. Operation-boundary root replacement TOCTOU

`scripts/v8_root_canary_fault_proxy.py` now binds the actual journal and replacement operations to held directory identity rather than relying on a final path check. POSIX operations use descriptor-relative access; Windows operations use the held-handle relative path/rename path. Cleanup is identity-bound as well.

The regression in `tests/test_v8_root_canary_recovery.py` swaps the approved parent immediately before the actual journal-lock open. It verifies fail-closed behavior and verifies that neither the replacement root nor the renamed original receives a journal.

### 2. Unmarked provider-exception recovery

`skills/orchestrator/scripts/gwo_v8/production_effects.py` now persists an exception without an explicit `provider_dispatched` marker as unknown (`NULL`). On restart, unknown claims attempt exact terminal Runtime readback. A proven terminal result is recovered without a second external provider dispatch; ambiguous/unavailable readback remains fenced. Explicit `False` still releases the claim and explicit `True` still retains it.

The regression in `tests/test_v8_production_effects.py` verifies terminal recovery and no duplicate provider call for an unmarked exception.

## TDD evidence

The tests were committed before the implementation:

- Test commit: `f8a998e` (`test: cover round-three recovery boundaries`)
- Implementation commit: `04f949f` (`fix: close round-three recovery boundaries`)

### RED — against `6545fd5`

The two new regressions failed for the intended pre-fix reasons:

```text
unmarked provider claim: (0,) != (None,)
operation-boundary parent swap: did not raise ValueError
```

### GREEN — recorded before the final small source tidy/commit

```text
effects + recovery focused suite: 34 passed, 1 warning
adjacent suites: 32 passed
targeted effects regression: 1 passed
targeted root operation-boundary regression: 1 passed
compileall: exit 0
git diff check: exit 0
```

Ruff was not installed in the current Python environment. The fallback invocation `uv run --with ruff` exited `1` and reported 30 existing/format issues, of which 20 were auto-fixable. No unrelated Ruff cleanup was applied.

## Final state

- Final source/test commit: `04f949f`
- The round-three report is recorded in this file.
- Existing pytest temporary directories were not regenerated or removed during this final reporting step.
- Per the explicit instruction to stop running tests once the final green temporary directory was present, verification was **not rerun after commit `04f949f`**. The GREEN outputs above are the captured outputs from the completed round-three verification run, before that final tidy/commit.

