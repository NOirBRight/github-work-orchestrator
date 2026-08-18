# P1 host safety fix — rounds 1–2

Date: 2026-08-18
Scope: host safety only. Factory files were not modified in these rounds and
no production mutation was performed.

## Round 1 baseline

Round 1 implementation commit: `30af6ec70045ab1dadbb15dd3b95264916df15b5`.
It isolated legacy/Guard cycles per thread, bounded abandoned snapshots, and
asserted lease stability throughout the four-read snapshot. The round 1 TDD
record remains below; round 2 adds the cleanup-failure evidence and fix.

## Round 2 review finding and fix

### Lease cleanup failure paths

The actual production lease is `BootstrapLease`. Its `close()` marks the lease
closed before attempting every closer, runs all closers, and raises an aggregate
error if any closer fails. Therefore a close failure is terminal for that lease;
the host must not retry it as though the lease were reusable.

The host now:

- closes a closable lease before rejecting a snapshot whose lease lacks
  `assert_stable()`; a cleanup failure is surfaced as a typed host error;
- records any state-close failure, including timer cleanup failure, instead of
  silently discarding it; and
- fails closed on every later read after a cleanup failure, so it cannot acquire
  another snapshot while cleanup status is uncertain.

Normal completion, read failure, legacy-cycle restart, timer expiry, and
concurrent evaluation isolation retain their existing exactly-once behavior.

Regression tests:

- `test_live_attestation_cycle_closes_lease_rejected_for_missing_stability_contract`
  proves a closable lease without `assert_stable()` is closed before the host
  raises.
- `test_live_attestation_cycle_fails_closed_after_timer_close_failure` proves a
  timer close is attempted once, its failure is not silently ignored, and no
  later snapshot is captured.

### Stability evidence

`test_live_attestation_cycle_asserts_stability_after_a_successful_snapshot`
now consumes all four ordered fields and verifies five stability assertions:
one before each field and the final post-read assertion. The existing
`test_live_attestation_cycle_asserts_lease_stability_before_each_read` remains
the separate drift regression.

## Lease lifecycle

1. The first `legacy` read for a thread captures one snapshot and obtains its
   lease.
2. The lease is asserted before each ordered field read; the final field gets a
   post-read assertion as well.
3. The state is removed and the lease is closed exactly once after the fourth
   field, or on ordering, repository, readback, or stability failure.
4. A later `legacy` read on the same thread closes any unfinished prior state
   and starts a new snapshot. A different thread has an independent state.
5. A 30-second daemon timer bounds an abandoned state. If its close fails, the
   state is retired, the failure is recorded, and all later reads fail closed.
6. A rejected but closable lease is closed before the contract error is raised;
   a close failure permanently disables that cycle.

## Round 2 TDD evidence

The two cleanup regressions were written before the round 2 implementation.
RED was run against the current pre-fix host implementation:

```powershell
$base = Join-Path $PWD '.pytest-basetemp-p1-round2-red'; if (Test-Path -LiteralPath $base) { Remove-Item -LiteralPath $base -Recurse -Force }; python -m pytest -vv --basetemp $base tests/test_v8_live_guard_host.py::test_live_attestation_cycle_holds_each_lease_and_isolates_evaluations tests/test_v8_live_guard_host.py::test_live_attestation_cycle_restarts_after_a_one_shot_legacy_read tests/test_v8_live_guard_host.py::test_live_attestation_cycle_releases_an_abandoned_thread_snapshot tests/test_v8_live_guard_host.py::test_live_attestation_cycle_asserts_lease_stability_before_each_read tests/test_v8_live_guard_host.py::test_live_attestation_cycle_closes_lease_rejected_for_missing_stability_contract tests/test_v8_live_guard_host.py::test_live_attestation_cycle_fails_closed_after_timer_close_failure tests/test_v8_live_guard_host.py::test_live_attestation_cycle_asserts_stability_after_a_successful_snapshot; $exit=$LASTEXITCODE; if (Test-Path -LiteralPath $base) { Remove-Item -LiteralPath $base -Recurse -Force }; exit $exit
```

RED result: **2 failed, 5 passed in 0.34s**. The intended failures were the
unclosed rejected lease and the timer close failure being ignored.

GREEN was the same focused test set on the round 2 implementation:

```powershell
$base = Join-Path $PWD '.pytest-basetemp-p1-round2-green'; if (Test-Path -LiteralPath $base) { Remove-Item -LiteralPath $base -Recurse -Force }; python -m pytest -vv --basetemp $base tests/test_v8_live_guard_host.py::test_live_attestation_cycle_holds_each_lease_and_isolates_evaluations tests/test_v8_live_guard_host.py::test_live_attestation_cycle_restarts_after_a_one_shot_legacy_read tests/test_v8_live_guard_host.py::test_live_attestation_cycle_releases_an_abandoned_thread_snapshot tests/test_v8_live_guard_host.py::test_live_attestation_cycle_asserts_lease_stability_before_each_read tests/test_v8_live_guard_host.py::test_live_attestation_cycle_closes_lease_rejected_for_missing_stability_contract tests/test_v8_live_guard_host.py::test_live_attestation_cycle_fails_closed_after_timer_close_failure tests/test_v8_live_guard_host.py::test_live_attestation_cycle_asserts_stability_after_a_successful_snapshot; $exit=$LASTEXITCODE; if (Test-Path -LiteralPath $base) { Remove-Item -LiteralPath $base -Recurse -Force }; exit $exit
```

GREEN result: **7 passed in 0.22s**.

Canonical round 2 implementation commit: **`4b3b4e951dab955e7da7dc8f2e4b85fece921943`**.

## Final focused verification

```text
python -m pytest -q --basetemp .pytest-basetemp-p1-round2-host tests/test_v8_live_guard_host.py
13 passed in 0.27s

ruff check --isolated --no-cache scripts/gwo_v8_live_guard_host.py tests/test_v8_live_guard_host.py
All checks passed!

python -m py_compile scripts/gwo_v8_live_guard_host.py tests/test_v8_live_guard_host.py
exit 0

git diff --check
exit 0
```

No broad or production test was claimed. No production activation, writer
transition, Store mutation, push, or tag was performed.

## Changed files

- `scripts/gwo_v8_live_guard_host.py`
- `tests/test_v8_live_guard_host.py`
- `.superpowers/sdd/2026-08-11-gwo-v8-phase1-5-production/task-p1-host-safety-fix-round1-report.md`

## Unresolved P1

None known within the requested host scope. Factory files remain untouched.
The report is finalized in the evidence commit and its SHA is returned with
the task result.
