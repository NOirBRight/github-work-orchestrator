# P1 host safety fix — round 1

Date: 2026-08-18
Scope: host safety only. Factory files were not modified and no production
mutation was performed.

## Findings addressed

### I-1 — one-shot legacy read and Guard cycle isolation

`_LegacyExecutionReadback` and the Guard both consume `sources.legacy`. The
adapter now keeps cycle state per calling thread. A new `legacy` read closes
that thread's unfinished prior state before capturing a fresh snapshot, so the
one-shot transition read cannot poison the following Guard evaluation. Other
threads do not wait for or consume that state. Guard reads remain ordered as
`legacy`, `durable_state`, `writer_fence`, and `ownership`, all from one fresh
snapshot.

Regressions: `test_live_attestation_cycle_restarts_after_a_one_shot_legacy_read`
and `test_live_attestation_cycle_holds_each_lease_and_isolates_evaluations`.

### I-2 — abandoned owner lifecycle

The old shared owner/condition could retain a lease when its owner thread
terminated mid-cycle. Each cycle state now has a bounded 30-second daemon
timer. If a thread abandons its snapshot, the timer removes that state and
closes its lease; subsequent reads cannot reuse the abandoned snapshot. State
close is idempotent, so normal completion, read failure, restart, and timeout
invoke lease close at most once.

Regression: `test_live_attestation_cycle_releases_an_abandoned_thread_snapshot`
(shortens the bound to 0.05 seconds).

### I-3 — lease stability boundary

The cycle requires the lease's `assert_stable()` and `close()` contract. It
asserts stability after capture and before every read, asserts once more after
the final field is read, and closes on drift or any read failure. The lease
therefore remains held through the complete four-read snapshot and closes once
only after the final stability boundary or a fail-closed error.

Regression: `test_live_attestation_cycle_asserts_lease_stability_before_each_read`.

## Lease lifecycle

1. The first `legacy` read for a thread captures one snapshot and obtains its
   lease.
2. The lease is asserted before each ordered field read; the final field gets a
   post-read assertion as well.
3. The state is removed and the lease is closed exactly once after the fourth
   field, or on ordering, repository, readback, or stability failure.
4. A later `legacy` read on the same thread closes any unfinished prior state
   and starts a new snapshot. A different thread has an independent state.
5. A 30-second daemon timer bounds an abandoned state and closes it fail-closed.

## TDD evidence

The regression tests were written before the host implementation was changed.
The following focused command was run against the pre-fix implementation:

```powershell
$base = Join-Path $PWD '.pytest-basetemp-p1-round1'; if (Test-Path -LiteralPath $base) { Remove-Item -LiteralPath $base -Recurse -Force }; python -m pytest -vv --basetemp $base tests/test_v8_live_guard_host.py::test_live_attestation_cycle_holds_each_lease_and_isolates_evaluations tests/test_v8_live_guard_host.py::test_live_attestation_cycle_restarts_after_a_one_shot_legacy_read tests/test_v8_live_guard_host.py::test_live_attestation_cycle_releases_an_abandoned_thread_snapshot tests/test_v8_live_guard_host.py::test_live_attestation_cycle_asserts_lease_stability_before_each_read
```

RED result: **4 failed**. The pre-fix shared owner blocked the concurrent
second evaluation, the one-shot legacy read left the next Guard cycle out of
order, an abandoned owner blocked the survivor, and lease drift was not
asserted.

The same focused command was rerun after the minimal host change:

```text
============================== 4 passed in 0.13s ==============================
```

## Final focused verification

Commands were run with explicit workspace `--basetemp` directories so pytest's
Windows temporary-directory cleanup was not part of the test result.

```text
python -m pytest -q --basetemp .pytest-basetemp-p1-final-host tests/test_v8_live_guard_host.py
10 passed in 0.21s

python -m pytest -q --basetemp .pytest-basetemp-p1-final-factory tests/test_v8_production_factory.py
24 passed in 0.52s

ruff check --isolated --no-cache scripts/gwo_v8_live_guard_host.py tests/test_v8_live_guard_host.py
All checks passed!

python -m py_compile scripts/gwo_v8_live_guard_host.py tests/test_v8_live_guard_host.py
exit 0

git diff --check
exit 0
```

A repository-wide pytest run was intentionally interrupted at the user's
request before completion; it is not claimed as a passing check. No production
activation, writer transition, Store mutation, push, or tag was performed.

## Changed files

- `scripts/gwo_v8_live_guard_host.py`
- `tests/test_v8_live_guard_host.py`
- `.superpowers/sdd/2026-08-11-gwo-v8-phase1-5-production/task-p1-host-safety-fix-round1-report.md`

## Unresolved P1

None known within the requested host scope. Factory files remain untouched.
The final commit SHA is returned with the task result.
