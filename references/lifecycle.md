# GitHub-native lifecycle

GitHub is the only persistent state store. Reconstruct orchestration state on
every run; do not maintain a second ledger.

## Canonical status labels

Use the repository's documented label mapping. When none exists, use:

| Label | Meaning |
|---|---|
| `needs-triage` | Scope or priority is not ready |
| `needs-info` | Missing evidence, policy, or a decision |
| `ready-for-agent` | Fully specified for agent execution |
| `ready-for-human` | Requires a maintainer-only action or decision |
| `wontfix` | Intentionally not scheduled |

Do not invent `active`, `review`, `merge-queued`, `blocked`, or model labels by
default. Use native fields:

| Derived state | GitHub representation |
|---|---|
| Ready | `ready-for-agent`, unassigned, no open blocker |
| Active | `ready-for-agent` plus assignee |
| Blocked | Open native dependency, or `needs-info` |
| Human gate | `ready-for-human` |
| Review/integration | Linked open PR |
| Complete | Closed Issue after intended merge and verification |

## Dependencies and decomposition

Use native issue dependencies for hard blocking edges. A candidate is ready
only when all blockers are closed.

Use sub-issues for real decomposition, not ordinary checklists. Keep a tracking
Issue open until its required children and decision gates are complete.

If native dependencies are unavailable, put `Blocked by: #<n>, #<n>` near the
top of the Issue body. The frontier script recognizes this fallback.

## Claim and dispatch

The first orchestration write is the claim:

```text
gh issue edit <number> --add-assignee @me
```

Post one dispatch comment after claiming. Suggested shape:

```text
Dispatch
- Profile: evidence
- Model: gpt-5.6-terra / xhigh
- Base: origin/dev@<sha>
- Branch: codex/issue-<n>-<slug>
- Ownership: <components or hot files>
- Verification: <commands>
- Blockers/integration parent: <none or links>
```

Do not publish local worktree paths, Codex task IDs, secrets, credentials, or
private machine details.

## Completion

Use `Closes #<n>` only when the PR satisfies the full Issue. Use `Refs #<n>` for
partial work. Close after the fixing commit reaches its intended branch and the
acceptance criteria are verified.

When work is abandoned or handed back, remove the orchestration claim only
after preserving useful evidence in the Issue or PR.

## Invalid states

Stop dispatch and report:

- multiple canonical status labels on one open Issue;
- `ready-for-agent` combined with `needs-info`, `ready-for-human`, or
  `wontfix`;
- a ready Issue with unresolved blockers;
- duplicate active workers for one Issue;
- a closed Issue still presented as ready work.
