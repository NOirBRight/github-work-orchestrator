# GWO V8 Phase 3 Release Candidate

## Disposition

**RC HOLD.** Task 7 candidate-local integration is green, but the integrated
four-axis review verdicts, Phase 4 workspace convergence, exact merged-main
subject freeze, and read-only cutover rehearsal remain pending. No Guard
`--execute`, production mutation, GitHub/CI action, activation, transition,
tag, push, or release was run.

## Candidate identity

- Worktree: `D:\Workstation\github-work-orchestrator\.codex-tmp\ga-phase1-5-fix`
- Branch: `codex/ga-phase1-5-fix`
- Verified source HEAD: `12db7168cf3c465a59768e53637b63f149281348`
- Verified source HEAD tree: `6c0fe9213762afa2be368731c5869013c3a38341`
- Runtime: Python 3.13, gpt-5.6-luna max configuration already in force
- Verification date: 2026-08-12 (Asia/Shanghai)

The candidate HEAD contains the Task 7 provenance-fixture and integration-test
commit. The external production ReleaseSubject has not been generated: its
exact identity is intentionally deferred until Phase 4 freezes the merged
canonical `main` tree.

## Identity boundary and output contract

The external manifest is schema `gwo-v8-release-subject.v1` and, in
production, has the fixed path:

```text
EVIDENCE_ROOT / gwo-v8-release-subject.json
```

Its `subject_digest` is the SHA-256 digest of the canonical manifest body with
only the top-level `subject_digest` excluded: UTF-8 JSON, sorted keys,
`ensure_ascii=False`, compact separators, and one LF. There is no CLI override
for the subject, path, SHA, Git tree, evidence root, or run identity.

The runner and the RC evidence keep these identities separate:

| Field | Meaning |
| --- | --- |
| `subject_digest` | Existing `CutoverSubject` canonical digest in report/evidence and the `AttemptIdentity` binding |
| `release_subject_digest` | External `ReleaseSubject.subject_digest` |
| `merged_main_sha` | 40-character Git commit identity |
| `merged_main_git_tree` | 40-character Git root-tree identity |
| `audited_source_tree_digest` / `CutoverSubject.source_tree_digest` | 64-character audited source digest, not the Git tree |
| `release_subject_path` | Held external subject manifest path |

The runner publishes `release_subject_digest`, `release_subject_path`,
`merged_main_sha`, and `merged_main_git_tree` in both report and evidence.
The existing `subject_digest` remains the CutoverSubject digest. The Task 7
fixture integration test asserts all four external fields and asserts that the
two digest domains are not substituted for one another.

The reviewed-provenance file remains the closed four-field manifest
`gwo-beta3-reviewed-provenance.v1`; it does not contain or depend on the
external subject digest. The held observer order is unchanged: runner, then
`beta3_bootstrap_model`, `beta3_control_ownership_attestor`,
`beta3_legacy_attestor`, `beta3_replay_guard`, followed by the reviewed
provenance bytes.

## Candidate-local provenance evidence

`D:\Workstation\github-work-orchestrator\.codex-tmp\ga-phase1-5-fix\scripts\beta3_reviewed_provenance.json`
was regenerated after the final observer bytes in this candidate were fixed.
The manifest is canonical JSON and contains no external ReleaseSubject field.

| Item | Canonical candidate path | SHA-256 |
| --- | --- | --- |
| runner | `D:\Workstation\github-work-orchestrator\.codex-tmp\ga-phase1-5-fix\scripts\run_beta3_live_guard.py` | `3f734cad9fd2260df9db15569ee3691521d893847fc4648e01983dc6db52e4da` |
| `beta3_bootstrap_model` | `D:\Workstation\github-work-orchestrator\.codex-tmp\ga-phase1-5-fix\scripts\beta3_bootstrap_model.py` | `37898e0b75168da83a542f7ef8ee53e91f1518a48f66565d23437b997e75050a` |
| `beta3_control_ownership_attestor` | `D:\Workstation\github-work-orchestrator\.codex-tmp\ga-phase1-5-fix\scripts\beta3_control_ownership_attestor.py` | `675f75aff2bf2808e5fbe3528950c1166ca62bb38f95112c95ae6366874e3a8e` |
| `beta3_legacy_attestor` | `D:\Workstation\github-work-orchestrator\.codex-tmp\ga-phase1-5-fix\scripts\beta3_legacy_attestor.py` | `221097effb9ae559f1f949bd09cab4a9770e8482b0791b20ba5c9a072a7e8ed9` |
| `beta3_replay_guard` | `D:\Workstation\github-work-orchestrator\.codex-tmp\ga-phase1-5-fix\scripts\beta3_replay_guard.py` | `c4c5711987c83f89d622ab9344acad9db691ed9beea58fbcfb93742bda5617a3` |

Attestor bundle SHA-256:
`5e446b86974ec351107758fa52b941970bce1a916661167211fac5068cf5506b`.

The raw-byte provenance proof was run after regeneration with the existing
runner functions; no in-memory hash override or dependency overlay was used:

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
runner_sha256=3f734cad9fd2260df9db15569ee3691521d893847fc4648e01983dc6db52e4da
attestor_bundle_sha256=5e446b86974ec351107758fa52b941970bce1a916661167211fac5068cf5506b
```

## Task 7 TDD evidence

### RED

Before regenerating the reviewed-provenance fixture, the new raw-byte binding
test was run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_live_guard_runner.py -k reviewed_provenance_hashes_match_current_observer_bytes
```

Result: **RED**, `1 failed, 180 deselected in 1.97s`. The expected failure was
the stale runner hash in the reviewed manifest:

```text
expected 3f734cad9fd2260df9db15569ee3691521d893847fc4648e01983dc6db52e4da
observed 0be40a0ee9577b3d7b9cb4e1607495137fd9c14ee59c736fd04a240b7893da5e
```

### GREEN

The minimal fix was to regenerate the closed reviewed-provenance manifest from
the current raw observer bytes, retaining its schema and ordered bundle. The
same test then passed:

```text
.                                                                        [100%]
1 passed, 180 deselected in 1.02s
```

The existing Task 2 root-canary RED/GREEN evidence is carried forward from the
corrected Task 6 report; it is not appended as a second historical Task 7 fix
wave. Each cycle recorded `5 failed, 17 deselected` against detached
`2feeaa6`, followed by `5 passed, 17 deselected`:

1. `root_candidate_readback_uses_real_git_commit_tree_and_diff`
2. `root_batch_delivery_uses_real_batch_integrator_and_git_readback`
3. `root_watchdog_callback_lost_wake_duplicate_and_restart_are_public_advance`
4. `root_worker_slots_release_and_strict_resource_is_exclusive`
5. `root_acceptance_is_canonical_across_independent_roots`

The source report is:
`D:\Workstation\github-work-orchestrator\.codex-tmp\ga-phase1-5-fix\.superpowers\sdd\2026-08-11-gwo-v8-release-subject-fix-wave\task-6-report.md`.

## Task 7 focused integration verification

Exact command:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_live_guard_runner.py tests/test_beta3_control_ownership_attestor.py tests/test_beta3_release_subject.py tests/test_beta3_release_subject_generator.py tests/test_v8_local_acceptance.py
```

Result:

```text
442 passed, 4 skipped in 918.39s (0:15:18)
```

The focused suite covers the runner, control attestor, external subject
schema/generator, and public single/root local acceptance. The fixture path
does not fall back to production Git or dependencies.

Additional checks run after the focused suite:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; ruff check --no-cache tests/test_beta3_live_guard_runner.py
```

Result: `All checks passed!`

```powershell
git diff --check
```

Result: clean; Git emitted only the expected LF-to-CRLF working-copy warning
for the JSON manifest.

The prior candidate-local full repository run remains recorded for the source
before this package-only test/manifest commit: `2468 passed, 1 skipped` with
exit code zero. Task 8 must rerun the full repository suite on the exact final
combined candidate and must not treat this prior run as the final release gate.

## Evidence and gates

| Gate/evidence | State |
| --- | --- |
| Reviewed-provenance raw-byte binding | GREEN |
| External subject schema/path/digest contract | Implemented; exact production digest pending Phase 4 exact-main freeze |
| Runner report/evidence external identity fields | GREEN in local fixture integration |
| Five Task 6 root-canary RED/GREEN cycles | Recorded and carried forward |
| Task 7 focused integration | PASS, 442 passed / 4 skipped |
| Ruff / diff check | PASS |
| Full local repository pytest on exact final candidate | PENDING Task 8 |
| SPEC GO | PENDING four-axis review |
| QUALITY GO | PENDING four-axis review |
| TDD VALID | PENDING integrated audit |
| OPEN 0 | PENDING adjudication |
| BETA3_CUTOVER_REHEARSAL_GO | PENDING Phase 4 |
| Release candidate | **HOLD** |

## Commit(s)

- `12db7168cf3c465a59768e53637b63f149281348` — `test: bind reviewed provenance to observer bytes`
- This RC package document is committed as the accompanying documentation update.

## Concerns and safe boundary

1. The external `gwo-v8-release-subject.json` was not generated in Task 7.
   Phase 4 must freeze exact merged `main`, regenerate candidate-independent
   reviewed provenance, generate the fixed subject exactly once, and validate
   its digest and all raw observer hashes.
2. The candidate-local reviewed-provenance paths intentionally point to this
   isolated worktree. They are not production provenance and must not be reused
   after workspace convergence.
3. V6.1 remains the only production writer. No Activation Receipt, writer
   transition, rollback, tag, push, release, GitHub/CI, or Guard `--execute`
   occurred.
