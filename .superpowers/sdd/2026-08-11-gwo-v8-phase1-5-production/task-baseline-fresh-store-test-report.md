# GWO V8 cutover fresh-local receipt baseline repair

- Date: 2026-08-18 (Asia/Shanghai)
- Repository: `D:\Workstation\github-work-orchestrator`
- Branch HEAD before the test-only commit: `23abca356758ebcd29037516554c9a52e57a38c1`
- Exact `origin/main`: `c2a3805ed3fad1667f7917f9b50063117b5277b4`
- Contract implementation: `850f60677eaada5228561af8705d633faf888f86`
- RED baseline (`850f606^`): `a770784dd6d7c76f4eb7726dfbd6261198d3911e`
- Scope: update only `tests/test_v8_cutover_fresh_local_receipt.py` and this SDD report.

## Contract baseline

The `850f606` transition implementation treats a fresh local Store as a
recoverable cutover state when the Store has no active, pending, or unrelated
lineage, its optional `store_generation` genesis row matches the Guard subject,
and the authoritative durable Activation Receipt and Plan read back together.
The transition reconstructs the local active Plan from that durable readback,
then completes the normal V6.1-to-V8 cutover. The matching provisioned genesis
row is accepted without mutation and the transition owns the later lineage step,
as documented in `docs/operations/gwo-v8-production-activation.md` (the
`store_generation`/genesis section).

The repaired test therefore checks observable behavior rather than changing one
status assertion: successful `cut_over`, V6.1 stop, local active-Plan readback,
current Receipt linkage to the historical Plan digest, immutable historical
Receipt preservation, exactly two durable Activation Receipts, a read-back
cutover record, exactly one local active row and writer-generation row, and no
pending activation row.

## Stale baseline

Before changing the tracked test, the existing stale test was run on the current
branch:

```powershell
py -3.13 -m pytest -q tests/test_v8_cutover_fresh_local_receipt.py
```

Result: exit code `1`; `1 failed`. The old test expected `outcome.status ==
"blocked"`, while the current transition returned `"cut_over"`.

## TDD RED

A temporary copy of the intended contract test was run in an isolated checkout
at the parent of `850f606`; no production implementation was copied or changed.

Working directory:

```text
C:\Users\noirb\AppData\Local\Temp\gwo-v8-red-parent-2e7c147b942d4f3ba48d631d60dbb16a
```

Command:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q -p no:cacheprovider tests/test_v8_cutover_fresh_local_receipt_red.py
```

Result: exit code `1`; `1 failed in 1.14s`. The intended first contract
assertion failed because the parent implementation returned `blocked` instead
of `cut_over`. This is the genuine RED for the fresh-store reconstruction
contract.

## TDD GREEN

The updated tracked test was run on current HEAD with an isolated pytest
`basetemp`:

```powershell
$base = C:\Users\noirb\AppData\Local\Temp\gwo-v8-green-fresh-e8aab3d550e34abfbd5993fd8e996e74
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q -p no:cacheprovider --basetemp $base tests/test_v8_cutover_fresh_local_receipt.py
```

Result: exit code `0`; `1 passed in 0.37s`.

## Focused cutover verification

Command:

```powershell
$base = C:\Users\noirb\AppData\Local\Temp\gwo-v8-cutover-suite-0bdf9ee57b0e47cdbcf4e993c9104b97
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q -p no:cacheprovider --basetemp $base tests/test_v8_cutover_activation.py tests/test_v8_cutover_fresh_local_receipt.py tests/test_v8_cutover_guard_api_boundary.py tests/test_v8_cutover_guard_cli.py tests/test_v8_cutover_guard_host.py tests/test_v8_cutover_guard_static.py tests/test_v8_cutover_guard.py tests/test_v8_cutover_historical_activation.py tests/test_v8_cutover_pending_digest.py tests/test_v8_cutover_pending_identity.py
```

Result: exit code `0`; `86 passed, 16 skipped in 8.59s`.

Diff check:

```powershell
git diff --check -- tests/test_v8_cutover_fresh_local_receipt.py .superpowers/sdd/2026-08-11-gwo-v8-phase1-5-production/task-baseline-fresh-store-test-report.md
```

Result: no output; exit code `0`.

## Safety and unresolved P1s

No production code was modified and no production Store, GitHub control branch,
Guard execute path, tag, or writer mutation was performed. The test uses
`tmp_path` and the existing in-memory durable control. The pre-existing
unrelated working-tree change in `tests/test_v8_production_effects.py` and the
untracked `.codex-tmp/` directory were left untouched and are not part of the
commit.

No P1s were introduced or remain unresolved for this narrowly scoped test-only
baseline repair.

Commit SHA: recorded in the final task response.
