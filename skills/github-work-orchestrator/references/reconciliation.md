# Post-intake Issue reconciliation

Reconcile before computing capacity and after every merge, accepted decision,
blocker change, Worker failure, or released slot. Reconstruct intended state
from GitHub; keep no orchestration database or committed state manifest.

Use the synchronized [Issue contract](shared/issue-contract.md) as the single
definition of fresh-worker readiness and [lifecycle](shared/lifecycle.md) as the
single label/dependency authority. Routine report diagnosis, duplicate search,
and first publication belong to `github-issue-intake` when it is available.

## Reconciliation authority

During an authorized orchestration run, repair priority, labels, and native
dependencies after intake when intent is unambiguous. Return an incomplete body
to Intake; edit its contract here only when the maintainer explicitly delegates
that Intake work. Preserve reporter evidence and re-read each Issue immediately
before an edit.

Use `needs-triage` when scope or ownership is ambiguous, `needs-info` for a
named missing fact/policy/decision, `ready-for-human` for a maintainer-only
action, and `ready-for-agent` for a fresh-worker-ready contract. An open
dependency prevents frontier selection, not the ready label.

## Automatic correction classes

Apply only unambiguous, idempotent corrections:

| Class | Automatic action |
|---|---|
| Closed Issue retains lifecycle labels | Remove those lifecycle labels |
| Explicit `Blocked by:` edge lacks native dependency | Add the native edge |
| Accepted plan names an unambiguous dependency | Add the native edge |
| Open Issue has no lifecycle label | Classify from its contract; use `needs-triage` if ambiguous |
| Open Issue has conflicting lifecycle labels | Keep the semantic one; use `needs-triage` if ambiguous |
| Issue lacks an unambiguous existing type label | Add the existing repository type |
| Orchestrator-owned claim has no live task or PR | Preserve evidence, then release the claim |

Require a decision before removing a native dependency without an exact desired
graph, unassigning a human or foreign claim, deleting reporter evidence,
closing an Issue, or inferring security, release, credential, product, or
compatibility policy.

## Deterministic repair tool

Preview:

```text
python <skill>/scripts/reconcile_issue_state.py --cwd <repo> \
  --repair-safe \
  --status 12=ready-for-agent \
  --add-label 12=enhancement \
  --dependency 13=12
```

Apply the identical reviewed plan:

```text
python <skill>/scripts/reconcile_issue_state.py --cwd <repo> \
  --repair-safe \
  --status 12=ready-for-agent \
  --add-label 12=enhancement \
  --dependency 13=12 \
  --apply
```

Options:

- `--repair-safe`: remove closed lifecycle labels and materialize textual
  `Blocked by:` edges.
- `--status ISSUE=LABEL`: set exactly one canonical lifecycle label.
- `--add-label ISSUE=LABEL` / `--remove-label ISSUE=LABEL`: reconcile existing
  non-lifecycle taxonomy.
- `--dependency ISSUE=BLOCKER`: add one native blocker.
- `--exact-dependencies ISSUE=B1,B2`: reconcile an explicitly complete blocker
  set, including removals.

The script performs deterministic writes; the Orchestrator supplies semantic
decisions. If GitHub changes between preview and apply, rebuild the plan.

## Completion gate

Rerun the validator and frontier. Reconciliation is complete when there are no
invalid lifecycle combinations, every intended hard blocker has one native or
documented edge, incomplete contracts are routed to Intake, and every remaining
ambiguity is visible in `needs-triage`, `needs-info`, or a discussion gate.
