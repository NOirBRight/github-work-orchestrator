# P1 factory fix round 1 TDD report

Task base: `da8a58c`

The host safety commit remains the task base. This round changes only the
production factory behavior and its factory tests; it does not change the
host implementation or add factory-side network/evidence readback.

## RED

Focused tests were added for the exact installed-host-missing detail and for
non-durable or non-canonically ordered Canary references. The pre-fix
implementation intentionally failed these tests:

```powershell
$base = Join-Path $env:TEMP 'gwo-p1-round1-red'; Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue; python -m pytest -vv --tb=short --basetemp $base tests/test_v8_production_factory.py -k "noncanonical_canary_refs or bootstraps_live_host_only_for_missing_installed_host"
```

Result: exit code `1`.

```text
4 failed, 20 deselected in 0.41s
```

The three Canary cases reached `_validate_store`, and the exact host detail
did not trigger fallback under the pre-fix predicate.

## GREEN

The factory now uses the existing `_durable_canary_ref` contract, requires
sorted unique evidence refs before Store validation or live-host fallback, and
matches the exact `CUTOVER_GUARD_COMPOSITION_INVALID` detail emitted by the
installed-host resolver.

```powershell
$base = Join-Path $env:TEMP 'gwo-p1-round1-green'; Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue; python -m pytest -q --basetemp $base tests/test_v8_production_factory.py -k "noncanonical_canary_refs or bootstraps_live_host_only_for_missing_installed_host"
```

Result: exit code `0`.

```text
4 passed, 20 deselected in 0.24s
```

## Focused suite

```powershell
$base = Join-Path $env:TEMP 'gwo-p1-round1-focused-suite'; Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue; python -m pytest -q --basetemp $base tests/test_v8_production_factory.py
```

Result: exit code `0`.

```text
24 passed in 0.62s
```

The durable manifest/evidence readback remains in
`ProductionActivationFacade`; the factory performs no synthetic or network
readback.
