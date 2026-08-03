# Task 1 — Traceable Beta1 baseline and tracker semantics

## Scope

Implemented only the Beta1 metadata/release-contract write set in the
designated worktree:

- `docs/releases/gwo-v8-release-train.md`
- `docs/releases/v8.0.0-beta.1.md`
- `docs/design/gwo-v8-lean-roadmap.md`
- `tests/test_orchestrator_package.py`

The release-train record defines the Beta1/Beta2/Beta3/GA sequence, exact exit
gates, immutable tag rules, the executable blocker graph, tracker-mutation
checkpoint, rollback ownership, and the boundary that package publication is
not writer activation. The Beta1 note contains one structured JSON evidence
object with exactly the five required keys and preserves the baseline evidence
semantics for `origin/main`.

## Worktree boundary

All commands and writes for this task used:

```text
D:\Workstation\gwo-worktrees\issue-136
```

The final read-only identity check returned:

```text
PWD=D:\Workstation\gwo-worktrees\issue-136
TOP=D:/Workstation/gwo-worktrees/issue-136
```

The primary checkout `D:\Workstation\github-work-orchestrator` was not used
for implementation or verification. During the final check, unrelated
CandidateReceipt-lane changes appeared in this worktree:

- `skills/orchestrator/scripts/gwo_v8/candidate_gate.py`
- `tests/test_v8_candidate_receipt_foundation.py`
- `tests/test_v8_candidate_receipt_kernel.py`
- `tests/v8_candidate_assurance_test_support.py`

Those files are outside this task's write set and were not staged or changed
by this task.

## TDD evidence

### RED

Added the two release-contract tests before adding the release documents:

```text
py -3.13 -m pytest tests/test_orchestrator_package.py::test_beta1_release_contract_has_structured_baseline_ci_dynamic_issue_and_nongoal -q
```

Observed the expected failure: 1 failed with `FileNotFoundError` because the
Beta1 evidence note did not exist.

### GREEN

Added the minimum release-train document, Beta1 note, roadmap section, and
structured tests. The focused contract passed:

```text
py -3.13 -m pytest tests/test_orchestrator_package.py::test_v8_release_train_names_exact_gates tests/test_orchestrator_package.py::test_beta1_release_contract_has_structured_baseline_ci_dynamic_issue_and_nongoal -q
```

Result: 2 passed.

The release-train phrase assertion initially exposed one missing exact phrase;
the document was minimally corrected while the focused test remained green.

## Verification

```text
py -3.13 -m pytest tests/test_orchestrator_package.py -q
13 passed in 5.28s

py -3.13 scripts/quick_validate.py
quick validation passed

py -3.13 scripts/sync_orchestrator.py --check
implement-gwo 8.0.0, orchestrator 8.0.0 packages synchronized

py -3.13 -m pytest -q
1523 passed in 912.21s (0:15:12)
```

`git diff --check` also passed before staging. No package files were modified
by this task.

After the full-suite run, unrelated CandidateReceipt-lane edits appeared in
the same worktree. A final post-commit rerun of the package validation exposed
that external lane's stale generated manifest:

```text
py -3.13 -m pytest tests/test_orchestrator_package.py -q
2 failed, 11 passed — stale manifest: skills/orchestrator/.skill-package.json

py -3.13 scripts/quick_validate.py
error: stale manifest: skills/orchestrator/.skill-package.json

py -3.13 scripts/sync_orchestrator.py --check
error: stale manifest: skills/orchestrator/.skill-package.json
```

The two new release-contract tests still pass (`2 passed`). The manifest and
CandidateReceipt files are outside this task's write set, so they were not
regenerated, staged, reverted, or otherwise touched.

## Read-only remote evidence

The execution-time reads returned:

- `origin/main`: `a48c7d6142ae3538725cb876a8782f4ca804cd22`
- Successful exact-SHA GWO CI:
  `https://github.com/NOirBRight/github-work-orchestrator/actions/runs/30778312688`
- Dynamic log summary: `1521 passed in 704.60s (0:11:44)`
- Issues #113, #114, #115, #116, #117, #118, and #119: `OPEN`
- Issue #137: `CLOSED`
- `refs/tags/v8.0.0-beta.1`: absent
- GitHub Release `v8.0.0-beta.1`: not found

The note records only #113–#119 in its required `issues` object. The separate
#137 state and tracker-semantic concern are called out here and in the release
documents rather than being inferred into the required evidence object.

## Deferred remote actions and concerns

No owner-approval gate, #137 tracker repair/reopen, milestone creation or
assignment, tag creation, tag push, or GitHub Release publication was executed.
The coordinator/program owner must perform the named owner approval/readback
gate, preserve #137's native blockers/body/comments, perform the idempotent
milestone operations, then re-read the merged metadata SHA and its successful
main CI before creating or publishing any immutable Beta1 release object. Any
existing tag or Release must be verified rather than moved or recreated.

The unrelated CandidateReceipt-lane modifications listed above remain
unstaged for the coordinator to handle separately.

## Commit

The implementation commit uses the scoped message:

```text
docs: define the GWO V8 release train
```

The final commit SHA is reported in the task response; this report is included
in that commit.
