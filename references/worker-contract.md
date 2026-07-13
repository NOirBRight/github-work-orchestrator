# Visible Worker Contract

Create one sidebar-visible Codex task per work item in an isolated worktree.
Never use a subagent as the Worker or dispatch target for a real GitHub work
item. The visible task is the auditable owner of the Issue, branch, PR, and
completion evidence.

The visible Worker may use subagents internally for bounded implementation
slices, research, parallel review, test analysis, or independent verification
within the same assigned Issue and worktree. The visible Worker partitions
write sets, integrates and reviews all results, and remains the only Worker of
record. Subagents do not own GitHub work items or create a separate lifecycle.
If discovered work is itself a distinct GitHub Issue, return it to the
Orchestrator for a new visible task instead of assigning it to a subagent.

## Required task identity

- Title: `[#<number>] <issue title>`
- Base: documented canonical integration branch and exact SHA
- Branch: repository convention, default
  `codex/issue-<number>-<short-slug>`
- Target: repository integration branch, not the release branch
- Scope: one Issue or one explicitly approved tightly coupled unit

## Reliable task materialization

This section is authoritative for creation, materialization, preflight, and
claim ordering. Read [creation-failure recovery](communication.md#creation-failure-no-worker-exists)
for orphan-worktree diagnosis, reuse, and cleanup after a creation failure.

Keep the candidate unassigned until a real Worker has passed preflight. That
makes the lifecycle's assignee-derived Active state truthful rather than a
placeholder for a queued client request.

1. Reconcile the candidate and record its exact base SHA, branch, hotset, and
   expected verification while it remains unassigned.
2. Create the isolated-worktree task at that exact base with a fast, low-cost,
   verified bootstrap binding. Send only
   `[#<number>] Bootstrap only. Reply exactly READY. Do not use tools.`
3. Wait boundedly for a real task ID in the native task list and a completed
   bootstrap turn. A client-side creation ID or a created worktree alone is
   only diagnostic evidence, not a Worker. Do not title an unmaterialized stub.
4. Rename the real task to `[#<number>] <issue title>`, then send the full
   Worker Contract to that same task with the selected Worker model and
   reasoning level. This first full turn establishes the recorded binding.
   It may perform only the permission preflight and must wait for claim
   confirmation before editing.
5. Use the one permitted post-contract read to confirm that the real task
   completed the preflight with the required permissions. Only then add the
   assignee claim, post one concise dispatch comment, and tell that Worker it
   may begin scoped work. The comment records the binding, base/branch, hotset,
   verification, blockers, and PR target without private task IDs or paths.

If bootstrap or materialization fails before a real task exists, do not add the
assignee or dispatch comment; preserve the client ID privately and follow the
linked creation-failure branch. If a full-contract or preflight turn fails
after a real task exists, leave the Issue unclaimed and follow the
existing-Worker failure branch. Make at most one replacement attempt only after
a concrete startup cause has been removed or isolated.

If an interrupted dispatch ever leaves its newly written assignee claim without
a real, preflight-passed Worker, immediately re-read the Issue and roll back
only the assignee added by that dispatch. The candidate was required to be
unassigned, so any changed ownership or other ambiguity stops the rollback for
maintainer review. Verify the release before another attempt; never leave a
queued ID or failed preflight as an Active claim.

Materialization is complete only when one real Worker has the full contract,
has passed preflight, and has received the exact claim and work boundaries.
Keep the bootstrap prompt short and uniquely Issue-scoped. If an optional
startup service is suspected, use a bounded A/B test and disable it only after
proof and separate authorization for a reversible change.

## Initial Worker message

Include:

1. Issue URL and full acceptance criteria.
2. Applicable repository instructions.
3. Selected model profile and concrete binding.
4. Base branch/SHA and branch name.
5. Owned components, expected files, and prohibited hotsets.
6. Accepted direction, architecture invariants, decision references, and the
   Worker's local decision authority.
7. Known dependencies and required integration parent.
8. Targeted and full verification commands.
9. Required PR target and closing semantics.
10. The Orchestrator callback task and the required Worker signals from the
   [communication protocol](communication.md).

Require the Worker to post a short implementation or investigation plan before
editing. A plan must identify expected writes, flag collisions, and state
whether any material decision gate is already visible.

## Task-host permission preflight

Treat permissions as task-host state, not as authority that a Worker prompt can
grant. Before editing, publishing, or running an expensive suite:

1. Report the effective sandbox and approval profile exposed to the task.
2. Run `git status --short --branch` in the assigned worktree.
3. When GitHub access is required, run one read-only identity or repository
   query such as `gh api user` or `gh repo view`.
4. Continue only when these commands run without an approval prompt and the
   effective profile satisfies the dispatch contract. Otherwise send one
   `BLOCKED` signal and stop before doing work.

Do not use destructive commands, credential changes, or writes outside the
worktree merely to prove that a broad permission profile exists. If the task
creation API has no permission field, inherit the user's current project or
environment setting and verify it through this preflight.

## Worker behavior

- Preserve unrelated and pre-existing changes.
- Do not merge, reset, force-push, publish, or change Issue state without
  explicit authority.
- Stay inside the assigned Issue and worktree.
- Keep all subagent work subordinate to this task's Issue, branch, worktree, and
  final review. Do not delegate a second GitHub work item through a subagent.
- Rebase or merge an upstream seam only when the assigned work semantically
  depends on it or a merge-base comparison proves a real write-set collision.
  An advanced integration branch by itself is not a blocker; evidence work may
  intentionally preserve its pinned base when the PR remains mergeable.
- Before reporting an upstream write-set collision, compute one common base and
  compare each side independently:

  ```text
  git merge-base HEAD origin/<integration-branch>
  git diff --name-only <merge-base>..HEAD
  git diff --cached --name-only
  git diff --name-only
  git diff --name-only <merge-base>..origin/<integration-branch>
  ```

  The Worker-side set is the union of committed, staged, and unstaged paths.
  Intersect that set with the upstream-only set. Never use
  `HEAD..origin/<integration-branch>` or a two-branch aggregate diff as proof
  that independently added files overlap.
- Use report-only quality gates as reports when repository policy says they are
  non-blocking.
- Decide reversible local implementation details inside the accepted contract.
  Do not silently choose project direction, redefine a durable architecture
  seam, or change public compatibility, security, or migration policy. Send
  `DISCUSSION_REQUIRED` with a decision packet when those choices arise.
- Stop and report when acceptance criteria conflict, a blocker is discovered,
  the model binding is unavailable, or required authority is missing.
- For investigations, keep production behavior unchanged unless implementation
  was explicitly assigned.

## Worker signals

Send `DISCUSSION_REQUIRED`, `BLOCKED`, `PR_OPENED`, `READY_FOR_REVIEW`, and
`STOPPED` signals to the Orchestrator as defined in
[communication.md](communication.md). Use native visible-task messaging when
available and keep the current model settings. Always leave the full evidence
in this visible task; a callback is only a concise notification.

## Completion report

Return:

- outcome and remaining gaps;
- changed files and important decisions;
- commits and PR URL, if created;
- targeted and full verification with exact results;
- newly discovered blockers, follow-up Issues, and hot-file ownership;
- whether the Issue can close.

The Orchestrator reviews this evidence before merging or releasing the slot.
