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

## Initial Worker message

Include:

1. Issue URL and full acceptance criteria.
2. Applicable repository instructions.
3. Selected model profile and concrete binding.
4. Base branch/SHA and branch name.
5. Owned components, expected files, and prohibited hotsets.
6. Known dependencies and required integration parent.
7. Targeted and full verification commands.
8. Required PR target and closing semantics.
9. The Orchestrator callback task and the required Worker signals from the
   [communication protocol](communication.md).

Require the Worker to post a short implementation or investigation plan before
editing. A plan must identify expected writes and flag collisions.

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
- Stop and report when acceptance criteria conflict, a blocker is discovered,
  the model binding is unavailable, or required authority is missing.
- For investigations, keep production behavior unchanged unless implementation
  was explicitly assigned.

## Worker signals

Send `BLOCKED`, `PR_OPENED`, `READY_FOR_REVIEW`, and `STOPPED` signals to the
Orchestrator as defined in [communication.md](communication.md). Use native
visible-task messaging when available and keep the current model settings.
Always leave the full evidence in this visible task; a callback is only a
concise notification.

## Completion report

Return:

- outcome and remaining gaps;
- changed files and important decisions;
- commits and PR URL, if created;
- targeted and full verification with exact results;
- newly discovered blockers, follow-up Issues, and hot-file ownership;
- whether the Issue can close.

The Orchestrator reviews this evidence before merging or releasing the slot.
