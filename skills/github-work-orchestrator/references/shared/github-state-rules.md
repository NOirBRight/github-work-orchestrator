# GitHub and Paseo state rules

GitHub owns Issue lifecycle, dependencies, claims, PRs, checks, decisions, and
completion. Paseo owns runtime Agent sessions, parentage, labels, workspaces,
worktrees, and campaign rooms. Neither is a substitute for the other.

## One owner per work item

One claimed Issue maps to one `dispatch_id`, one Paseo-managed Agent, one
`work/issue-<number>-<slug>` branch, one isolated worktree, and one editor.
Record `campaign_id`, `dispatch_id`, role, repository, Issue, and branch as
Agent labels. Provider-native subagent timelines are read-only evidence and
cannot own a dispatch.

Claim and read back the Issue before dispatch. Agent creation success is not a
GitHub claim; an assignee or room message is not proof that an Agent exists.
Validate all three surfaces before edits.

## Integration flow

Pin every dispatch to an exact `dev` SHA. Feature branches use the `work/issue-`
prefix and PRs target `dev`. `main` accepts only an explicit verified release
merge from `dev`.

## Runtime state and recovery

The campaign room is a replayable runtime mailbox, not an authorization ledger.
Chat authorship can be supplied by a client and must be verified against Agent
labels, lifecycle, Git state, and GitHub evidence.

On missing callback, parent restart, or daemon restart:

1. replay the bounded room and deduplicate Signal-IDs;
2. list and inspect campaign Agents;
3. verify exact branch/worktree/Issue ownership;
4. continue the existing idle Agent or preserve an active/ambiguous one; and
5. create a successor only after terminal proof and durable WIP evidence.

## Capacity

Keep one Orchestrator per repository/activity and at most four active delegated
Paseo Agents per campaign by default. Every role counts against the same budget.
Recompute only after material dispatch, terminal event, merge, stop, recovery,
or explicit operator request.

## Safe cleanup

Cleanup is event-triggered with a five-minute target. Archive an Agent and its
Paseo worktree only when the Agent is idle, work is committed/pushed or safely
integrated, the worktree is clean, ownership is unambiguous, and the branch is
merged when applicable. Preserve dirty, unpushed, active, ambiguous, or foreign
work. Never reset or force-clean useful WIP.
