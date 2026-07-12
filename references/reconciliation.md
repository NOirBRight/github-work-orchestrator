# Issue reconciliation

Run reconciliation before computing work capacity and again after every merge,
human decision, or worker failure. Reconstruct intended state each time; keep no
orchestration database or committed state manifest.

## Standard Issue contract

An actionable Issue must provide enough information for a fresh Worker:

1. outcome or problem statement;
2. bounded scope and explicit exclusions;
3. acceptance criteria;
4. verification or evidence requirements;
5. native blockers and sub-issues when applicable;
6. exactly one canonical lifecycle label;
7. one existing repository type label when its taxonomy requires one.

Preserve reporter context, logs, decisions, and prior discussion. Add missing
sections or reorganize content only when the meaning is clear. Never replace a
body from stale data: re-read the Issue immediately before editing.

Use `needs-triage` when scope or priority is ambiguous, `needs-info` when a named
fact or policy is missing, `ready-for-human` for a maintainer-only decision, and
`ready-for-agent` when a fresh Worker can execute the contract. An open native
dependency does not prevent `ready-for-agent`; it prevents frontier selection.

## Automatic correction classes

Apply during an authorized orchestration run:

| Class | Automatic action |
|---|---|
| Closed Issue retains lifecycle labels | Remove those lifecycle labels |
| Explicit `Blocked by:` edge lacks native dependency | Add the native edge |
| Accepted plan names an unambiguous dependency | Add the native edge |
| Open Issue has no lifecycle label | Classify it from its contract; use `needs-triage` if ambiguous |
| Open Issue has conflicting lifecycle labels | Keep the semantically correct one; use `needs-triage` if ambiguous |
| Required contract section is missing but derivable | Add it while preserving existing content |
| Tracking Issue declares concrete missing children | Create standard child Issues and native sub-issue links |
| Issue lacks an unambiguous existing type label | Add the repository's existing type label |
| Skill-owned claim has no live task or PR | Preserve evidence, then release the claim |

Do not automatically:

- remove a native dependency unless an exact desired graph is explicit;
- unassign a human or a claim not created by this Skill;
- rewrite or delete reporter evidence;
- close an Issue without merged delivery or an explicit disposition;
- infer security, credential, release, or product policy decisions.

## Deterministic repair tool

Preview by default:

```text
python <skill>/scripts/reconcile_issue_state.py --cwd <repo> \
  --repair-safe \
  --status 12=ready-for-agent \
  --add-label 12=enhancement \
  --dependency 13=12
```

Apply the same plan:

```text
python <skill>/scripts/reconcile_issue_state.py --cwd <repo> \
  --repair-safe \
  --status 12=ready-for-agent \
  --dependency 13=12 \
  --apply
```

Options:

- `--repair-safe`: remove lifecycle labels from closed Issues and materialize
  textual `Blocked by:` edges as native dependencies.
- `--status ISSUE=LABEL`: make one open Issue use exactly that lifecycle label.
- `--add-label ISSUE=LABEL` / `--remove-label ISSUE=LABEL`: reconcile existing
  non-lifecycle taxonomy such as `bug` or `enhancement`.
- `--dependency ISSUE=BLOCKER`: add a missing native dependency.
- `--exact-dependencies ISSUE=B1,B2`: reconcile an Issue to an explicitly
  complete blocker set, including removals.

The command is idempotent. The Orchestrator supplies semantic decisions as
arguments; the script performs and verifies deterministic GitHub mutations.

When a tracking Issue explicitly enumerates missing children, create those
children with `gh issue create`, add the native sub-issue links, and then use
the reconciliation command to materialize their dependency order. Do not create
children from vague roadmap prose.

## Drift loop

```text
read policy and Issues
→ infer intended contracts and graph
→ preview reconciliation
→ apply unambiguous repairs
→ validate
→ compute frontier
→ dispatch/monitor
→ repeat
```

If the target changes between preview and apply, rebuild the plan rather than
forcing stale writes.
