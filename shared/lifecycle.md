# GitHub-native lifecycle

Reconstruct lifecycle state from GitHub on every run. Keep no control Issue,
database, duplicate status ledger, model label, or execution-lane label.

## Canonical labels

Use the repository's documented mapping. When none exists, use exactly one:

| Label | Meaning |
|---|---|
| `needs-triage` | Scope or ownership is not ready |
| `needs-info` | A named fact, policy, or decision is missing |
| `ready-for-agent` | The contract is fully specified for execution |
| `ready-for-human` | A maintainer-only action or decision is required |
| `wontfix` | The work is intentionally not scheduled |

Represent runtime state with native fields:

| Derived state | GitHub representation |
|---|---|
| Ready | `ready-for-agent`, unassigned, no open blocker |
| Active | `ready-for-agent` plus assignee and one execution owner |
| Blocked | Open native dependency, or `needs-info` |
| Human gate | `ready-for-human` |
| Review/integration | Linked open PR |
| Complete | Closed Issue after intended merge and verification |

## Dependencies and frontier

Use native blocked-by edges for hard dependencies and sub-issues for real
decomposition. When native dependencies are unavailable, put
`Blocked by: #<n>, #<n>` near the top of the body.

A candidate belongs to the ready frontier only when it is open, has exactly
`ready-for-agent`, is unassigned, has no open blocker, and does not collide with
active hotset ownership.

## Role ownership

Issue Intake may create/update a report, choose one canonical lifecycle label
and one repository type label, and add an unambiguous dependency. It leaves
priority, Milestone, assignee, capacity, and merge order unchanged.

The Orchestrator reconciles contracts, priority, labels, dependencies, claims,
lane selection, publication, review, integration, and cleanup within an
authorized campaign. Inline and Subagent lanes remain owned visibly by the
Orchestrator; a Subagent does not create a second lifecycle.

A Visible Worker owns only its assigned execution, branch, PR, and evidence. It
does not claim another Issue, merge, close, reprioritize, or broaden scope.

## Claim, lane, branch, and PR

Keep a candidate unassigned while lane eligibility, exact-base worktree,
permissions, model request, and single-editor ownership are validated. Then add
the intended assignee and read it back before edits.

One claimed Issue maps to one lane and one isolated worktree:

- Inline: the Orchestrator edits directly.
- Subagent: the Orchestrator dispatches one bounded write set and integrates it.
- Visible Worker: one materialized Task receives `START` after claim and
  preflight.

Public comments contain only sanitized execution contract and evidence; keep
Task IDs, Subagent IDs, owner tokens, local paths, and private receipts out of
GitHub. Open/update one PR against the documented integration branch. Use
`Closes #<n>` only for the full Issue and `Refs #<n>` for partial delivery.

## Completion and cleanup

Merge only within explicit authority and serialized hotset order. Close after
the fixing commit reaches its intended branch and acceptance passes. On merge
or stop, preserve useful evidence, release the claim as appropriate, and
trigger safe cleanup within five minutes. Delete only merged local branches;
remove only clean durable inactive worktrees; and report the exact corresponding
Visible Worker for human-owned archive. Never invoke native Task archive
automatically or edit Codex SQLite. The five-minute deadline covers eligible
repository cleanup and surfacing the archive request, not the human action.

## Invalid states

Stop and report:

- multiple canonical lifecycle labels;
- ready combined with needs-info, ready-for-human, or wontfix;
- two editors for one Issue/worktree;
- more than one visible Orchestrator for one repository/activity;
- more than three visible Workers globally;
- a closed Issue presented as ready work; or
- a branch/PR target that differs from its execution contract.
