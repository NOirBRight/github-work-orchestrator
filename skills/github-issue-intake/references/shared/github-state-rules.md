# GitHub and Paseo state rules

GitHub owns Issue lifecycle, dependencies, claims, PRs, checks, decisions, and
completion. Paseo owns runtime Agent sessions, parentage, labels, workspaces,
worktrees, and campaign rooms. Neither is a substitute for the other.

## Two-tier ownership

The Repository Coordinator is the repository-resident root Agent. Keep exactly
one per repository in a dedicated `dev` control worktree and label it with the
repository and the repository-coordinator role. Unlabeled root Agents are
foreign and protected; never adopt or archive them by inference.

Each Campaign has exactly one Campaign Orchestrator. The Campaign Orchestrator
is a direct `subagent` of the Repository Coordinator, carries the exact
`campaign_id`, and may use a Provider Binding different from every other
Campaign. A Provider Binding is resolved per Campaign by explicit override,
then the `planning` preference, and is runtime evidence rather than GitHub work
state.

If more than one Repository Coordinator is observed, stop new dispatch and
integration. Preserve both Agents and require a human operator to select one
canonical Coordinator through the existing Paseo UI or CLI after durable
handoff; neither Coordinator may archive the other.

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

The Repository Coordinator must not author feature commits directly on `dev`.
Inline implementation still uses an isolated `work/issue-*` worktree. Campaign
Orchestrators and their children never use the control worktree as an execution
worktree.

Different Campaigns may execute concurrently when their Hotsets do not overlap.
Hotset entries are canonical repository-relative paths; reject absolute paths,
empty components, `.` and `..` instead of guessing their targets.
The Repository Coordinator admits Hotsets and holds one repository-scoped
Integration Lease, so only one Campaign may update `dev` at a time. Before
integration, a Campaign whose base SHA is no longer current must refresh its
pinned `dev` base and rerun verification affected by that delta.

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

Keep one Repository Coordinator per repository and one Campaign Orchestrator per
Campaign. At most four active Agents per Campaign, including its Orchestrator,
are allowed by default; every Agent also counts against the host-wide capacity
reported by Paseo. This includes the Repository Coordinator. Reject
contradictory or already-over-limit counts rather than admitting around them.
Recompute after material dispatch, terminal event, merge, stop, recovery, or
explicit operator request.

## Safe cleanup

Cleanup is event-triggered with a five-minute target. Archive an Agent and its
Paseo worktree only when the Agent is idle, work is committed/pushed or safely
integrated, the worktree is clean, ownership is unambiguous, and the branch is
merged when applicable. Preserve dirty, unpushed, active, ambiguous, or foreign
work. Never reset or force-clean useful WIP.

A Campaign Orchestrator may clean only its direct child Agents. The Repository
Coordinator may clean a terminal Campaign Orchestrator after durable Campaign
readback. No Agent archives itself, a root Agent, a sibling, the Repository
Coordinator's trusted control worktree, or its own control worktree. Delegated
cleanup archives the child first, reads back both its archived state and removal
of the worktree binding, and only then evaluates worktree and branch cleanup in
a second authorization pass. GWO automatically executes only a nonempty
eligible plan through existing Paseo operations; every protected plan has no
actions. `CAMPAIGN_CLOSED` never archives the Repository Coordinator.
