# P1 factory fix round 2 TDD report

Task base for the honest RED reproduction: `da8a58c`.

The host-safety task remains separate. This round changes only the factory
identity task files and this SDD report; it does not modify host files or add
factory-side network/evidence readback.

## RED against the isolated `da8a58c` parent

The previous round's RED claim of four failures was not valid: its positive
missing-host test supplied the old implementation's code-only fallback
condition and passed on the parent. This round copied the current factory test
file into an isolated detached worktree at `da8a58c`, then ran the same
behavior selector that includes the positive missing-host case and the
negative non-missing-detail case.

```powershell
$parent = Join-Path $env:TEMP ('gwo-p1-round2-parent-' + $PID); git worktree add --detach $parent da8a58c; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; Copy-Item -LiteralPath 'tests/test_v8_production_factory.py' -Destination (Join-Path $parent 'tests/test_v8_production_factory.py') -Force; Set-Location $parent; $base = Join-Path $env:TEMP ('gwo-p1-round2-parent-basetemp-' + $PID); Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue; python -m pytest -vv --tb=short --basetemp $base tests/test_v8_production_factory.py -k "guard_receipt_bound_to_another_subject or guard_subject_for_another_target_branch or noncanonical_canary_refs or bootstraps_live_host_only_for_missing_installed_host or does_not_bootstrap_for_non_missing_installed_host_error"
```

Result: exit code `1`.

```text
collected 25 items / 18 deselected / 7 selected
6 failed, 1 passed, 18 deselected in 1.07s
```

The exact missing-host positive test passed on `da8a58c`, while the stale
receipt, target-branch, three Canary-ref, and non-missing-host negative tests
failed. The non-missing-host failure observed the parent bootstrapping despite
the different detail, proving the old code-only predicate was the cause.

## GREEN on the current head

The same behavior selector was run on the current head after the test fixes.
The stale-subject fixture now recomputes `receipt_digest` after mutating
`subject_digest`, so the rejection is causally about subject binding. A
target-branch mismatch test is also included.

```powershell
$base = Join-Path $env:TEMP ('gwo-p1-round2-green-' + $PID); Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue; python -m pytest -q --basetemp $base tests/test_v8_production_factory.py -k "guard_receipt_bound_to_another_subject or guard_subject_for_another_target_branch or noncanonical_canary_refs or bootstraps_live_host_only_for_missing_installed_host or does_not_bootstrap_for_non_missing_installed_host_error"
```

Result: exit code `0`.

```text
7 passed, 18 deselected in 0.30s
```

## Focused factory suite

```powershell
$base = Join-Path $env:TEMP ('gwo-p1-round2-suite-' + $PID); Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue; python -m pytest -q --basetemp $base tests/test_v8_production_factory.py
```

Result: exit code `0`.

```text
25 passed in 0.58s
```

The exact fallback code/detail match and durable-ref validation remain in the
factory. Durable manifest/evidence readback remains in
`ProductionActivationFacade`; no synthetic or network readback was added to
the factory, and no production mutation was performed.
