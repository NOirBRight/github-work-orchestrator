# GWO V8 Phase 3 Release Candidate

## Disposition

**RC HOLD.** Candidate-local verification is green. The integrated four-axis
review verdicts and Phase 4 workspace-convergence/exact-merged-main rehearsal
are still pending. No Guard `--execute`, production mutation, GitHub CI, or
production evidence generation was run.

## Candidate identity

- Worktree: `D:\Workstation\github-work-orchestrator\.codex-tmp\ga-phase1-5-fix`
- Branch: `codex/ga-phase1-5-fix`
- Verified source HEAD: `5efdf76f2905aa97d5b183c0a55c5bbca4149001`
- Verified source HEAD tree: `d06106e6312d6d1186de64afe3a6bc52ff32e451`
- Runtime: Python 3.13, gpt-5.6-luna max configuration already in force
- Verification date: 2026-08-11 (Asia/Shanghai)

The source and test changes are the committed contents of the verified HEAD;
the only candidate-local tracked working-tree change during verification was
the assigned provenance manifest.

## Candidate-local provenance evidence

`scripts/beta3_reviewed_provenance.json` was regenerated as canonical JSON for
this worktree and the current runner/attestor bytes. The exact canonical paths
and hashes are:

| Item | Canonical path | SHA-256 |
| --- | --- | --- |
| runner | `D:\Workstation\github-work-orchestrator\.codex-tmp\ga-phase1-5-fix\scripts\run_beta3_live_guard.py` | `0be40a0ee9577b3d7b9cb4e1607495137fd9c14ee59c736fd04a240b7893da5e` |
| `beta3_bootstrap_model` | `D:\Workstation\github-work-orchestrator\.codex-tmp\ga-phase1-5-fix\scripts\beta3_bootstrap_model.py` | `37898e0b75168da83a542f7ef8ee53e91f1518a48f66565d23437b997e75050a` |
| `beta3_control_ownership_attestor` | `D:\Workstation\github-work-orchestrator\.codex-tmp\ga-phase1-5-fix\scripts\beta3_control_ownership_attestor.py` | `9675bd4d94dc09f5da181bfe7d0505e0c5299f41c89b6e44b52247ee464f5065` |
| `beta3_legacy_attestor` | `D:\Workstation\github-work-orchestrator\.codex-tmp\ga-phase1-5-fix\scripts\beta3_legacy_attestor.py` | `221097effb9ae559f1f949bd09cab4a9770e8482b0791b20ba5c9a072a7e8ed9` |
| `beta3_replay_guard` | `D:\Workstation\github-work-orchestrator\.codex-tmp\ga-phase1-5-fix\scripts\beta3_replay_guard.py` | `c4c5711987c83f89d622ab9344acad9db691ed9beea58fbcfb93742bda5617a3` |

Attestor bundle SHA-256:
`ed54180bd5207a39fea339536c37ba8554139f2b016e103c32de031b0e06ea18`.

The unmodified provenance functions were called directly; no in-memory hash
override or dependency overlay was used:

```powershell
@'
from pathlib import Path
import hashlib, importlib.util, sys
root = Path.cwd().resolve()
scripts = root / 'scripts'
core_scripts = root / 'skills' / 'orchestrator' / 'scripts'
sys.path.insert(0, str(scripts))
sys.path.insert(0, str(core_scripts))
path = scripts / 'run_beta3_live_guard.py'
spec = importlib.util.spec_from_file_location('run_beta3_live_guard_provenance_check', path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
manifest = module._reviewed_provenance()
runner_hash = module._runbook_hash()
attestor_hash = module._attestor_source_sha256()
assert runner_hash == hashlib.sha256(path.read_bytes()).hexdigest()
assert attestor_hash == manifest['attestor_bundle_sha256']
print('PROVENANCE_GREEN')
print('runner_sha256=' + runner_hash)
print('attestor_bundle_sha256=' + attestor_hash)
'@ | py -3.13 -B -
```

Result:

```text
PROVENANCE_GREEN
runner_sha256=0be40a0ee9577b3d7b9cb4e1607495137fd9c14ee59c736fd04a240b7893da5e
attestor_bundle_sha256=ed54180bd5207a39fea339536c37ba8554139f2b016e103c32de031b0e06ea18
```

## Verification commands and results

### Focused Beta3 and public acceptance suites

Exact command:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_bootstrap_model.py tests/test_beta3_control_ownership_attestor.py tests/test_beta3_legacy_attestor.py tests/test_beta3_replay_guard.py tests/test_beta3_live_guard_runner.py tests/test_v8_local_acceptance.py
```

Result:

```text
505 passed in 493.94s (0:08:13)
```

### Ruff

Exact command:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; ruff check --no-cache scripts/beta3_bootstrap_model.py scripts/beta3_control_ownership_attestor.py scripts/beta3_legacy_attestor.py scripts/beta3_replay_guard.py scripts/run_beta3_live_guard.py tests/test_beta3_bootstrap_model.py tests/test_beta3_control_ownership_attestor.py tests/test_beta3_legacy_attestor.py tests/test_beta3_replay_guard.py tests/test_beta3_live_guard_runner.py tests/test_v8_local_acceptance.py
```

Result: `All checks passed!`

### AST

Exact command:

```powershell
@'
import ast
from pathlib import Path
files = [
    Path('scripts/beta3_bootstrap_model.py'),
    Path('scripts/beta3_control_ownership_attestor.py'),
    Path('scripts/beta3_legacy_attestor.py'),
    Path('scripts/beta3_replay_guard.py'),
    Path('scripts/run_beta3_live_guard.py'),
    Path('tests/test_beta3_bootstrap_model.py'),
    Path('tests/test_beta3_control_ownership_attestor.py'),
    Path('tests/test_beta3_legacy_attestor.py'),
    Path('tests/test_beta3_replay_guard.py'),
    Path('tests/test_beta3_live_guard_runner.py'),
    Path('tests/test_v8_local_acceptance.py'),
]
for path in files:
    ast.parse(path.read_bytes(), filename=str(path))
print(f'AST_OK ({len(files)} files)')
'@ | py -3.13 -B -
```

Result: `AST_OK (11 files)`.

### Forbidden production call graph

Exact static graph command:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_v8_cutover_guard_static.py
```

Result:

```text
5 passed in 0.92s
```

### Diff check

Exact command: `git diff --check`

Result: `DIFF_CHECK_OK` (the expected Git LF-to-CRLF working-copy warning was
the only warning).

### Full repository pytest

The first foreground attempt reached 96% but the session command ended without
a pytest summary, so it is not counted as evidence. The full command was then
rerun in a background PowerShell process with its stdout retained at
`C:\tmp\gwo-task6-full-rerun-20260811-1.out.log` and its exit marker.

Exact command:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; $env:GWO_CONVERGENCE_ARCHIVE_ROOT='D:\gwo-convergence-archive\20260804T185544Z'; py -3.13 -B -m pytest -q -p no:cacheprovider --basetemp 'C:\tmp\gwo-phase3-full-repository-20260811-rerun'
```

Result:

```text
2468 passed, 1 skipped, 3 warnings in 1588.21s (0:26:28)
EXIT_CODE=0
```

The three warnings were pytest assert-rewrite warnings for already-imported
`v8_candidate_assurance_test_support`, `v8_successor_test_support`, and
`v8_production_test_support`.

## Evidence and gates

| Gate/evidence | State |
| --- | --- |
| Candidate-local provenance | GREEN |
| Five Beta3 suites + local acceptance | PASS, 505 passed |
| Ruff / AST / diff check | PASS |
| Forbidden production call graph | PASS, 5 passed |
| Full local repository pytest | PASS, 2468 passed / 1 skipped |
| SPEC GO | PENDING four-axis review |
| QUALITY GO | PENDING four-axis review |
| TDD VALID | PENDING integrated audit |
| OPEN 0 | PENDING adjudication |
| BETA3_CUTOVER_REHEARSAL_GO | PENDING Phase 4 |
| Release candidate | **HOLD** |

The targeted fix review already recorded `APPROVE — SPEC PASS / QUALITY PASS`
for the Windows sharing fix, with the prior manifest caveat now resolved only
for this candidate-local worktree. That targeted review is not a substitute
for the requested integrated four-axis review. Phase 4 must regenerate the
manifest on exact merged `main`, produce the merged-main evidence package, and
run the read-only Beta3 rehearsal before this RC can move from HOLD.
