# GitHub-native lifecycle

Reconstruct lifecycle state from GitHub on every run. Keep no control Issue,
database, duplicate status ledger, or model label.

## Canonical labels

Use the repository's documented mapping. When none exists, use exactly one:

| Label | Meaning |
|---|---|
| `needs-triage` | Scope or ownership is not ready |
| `needs-info` | A named fact, policy, or decision is missing |
| `ready-for-agent` | The contract is fully specified for agent execution |
| `ready-for-human` | A maintainer-only action or decision is required |
| `wontfix` | The work is intentionally not scheduled |

Represent runtime states with native fields:

| Derived state | GitHub representation |
|---|---|
| Ready | `ready-for-agent`, unassigned, no open blocker |
| Active | `ready-for-agent` plus assignee |
| Blocked | Open native dependency, or `needs-info` |
| Human gate | `ready-for-human` |
| Review/integration | Linked open PR |
| Complete | Closed Issue after intended merge and verification |

Do not invent `active`, `review`, `merge-queued`, `blocked`, discussion, or
model labels by default.

## Dependencies and frontier

Use native blocked-by edges for hard dependencies and native sub-issues for
real decomposition. A fully specified Issue may remain `ready-for-agent` while
a blocker is open; the frontier excludes it until every blocker closes.

When native dependencies are unavailable, put
`Blocked by: #<n>, #<n>` near the top of the body. Keep tracking Issues open
until required children and decision gates are complete.

A candidate belongs to the ready frontier only when it is open, has exactly
`ready-for-agent`, is unassigned, has no open blocker, and does not collide with
active hotset ownership.

## Role ownership

Issue Intake may create or update a report, select exactly one canonical
lifecycle label and one existing repository type label, and add an unambiguous
native dependency. It leaves priority, Milestone, assignee, capacity, and merge
order unchanged.

The Orchestrator may reconcile Issue contracts, priority, labels, dependencies,
claims, dispatch comments, and integration order within an authorized campaign.
It verifies delivery before closing or releasing ownership.

The Worker owns only its assigned branch, PR, implementation, and evidence. It
does not claim another Issue, change lifecycle state or priority, alter a
Milestone, merge, or close without explicit authorization.

## Claim, branch, PR, and completion

The first dispatch write is the assignee claim. Post one concise dispatch
comment with profile/model, base SHA, branch, hotset, verification, blockers,
and PR target. Keep private task IDs and local paths out of GitHub.

One claimed Issue maps to one visible Worker task and isolated worktree. The
Worker commits and pushes its assigned branch and opens or updates a PR against
the documented integration branch. Use `Closes #<n>` only when the PR satisfies
the entire Issue; use `Refs #<n>` for partial delivery.

Merge only within explicit authority and serialized hotset order. Close an
Issue after the fixing commit reaches its intended branch and acceptance
criteria are verified. When work is abandoned or handed back, preserve useful
evidence before releasing the claim.

## Decision gates and invalid states

For a persistent decision, use `needs-info` when policy/evidence/choice is
missing or `ready-for-human` for a maintainer-only action. Restore the canonical
status only after the decision is recorded and the contract is updated.

Stop dispatch and report:

- multiple canonical labels on one open Issue;
- `ready-for-agent` combined with `needs-info`, `ready-for-human`, or `wontfix`;
- duplicate visible owners for one Issue or worktree;
- a closed Issue presented as ready work; or
- a Worker branch or PR whose target differs from its dispatch contract.
