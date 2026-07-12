# Visible Worker Contract

Create one sidebar-visible Codex task per work item in an isolated worktree.
Never use a subagent as a Worker.

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

Require the Worker to post a short implementation or investigation plan before
editing. A plan must identify expected writes and flag collisions.

## Worker behavior

- Preserve unrelated and pre-existing changes.
- Do not merge, reset, force-push, publish, or change Issue state without
  explicit authority.
- Stay inside the assigned Issue and worktree.
- Rebase after required upstream seams merge.
- Use report-only quality gates as reports when repository policy says they are
  non-blocking.
- Stop and report when acceptance criteria conflict, a blocker is discovered,
  the model binding is unavailable, or required authority is missing.
- For investigations, keep production behavior unchanged unless implementation
  was explicitly assigned.

## Completion report

Return:

- outcome and remaining gaps;
- changed files and important decisions;
- commits and PR URL, if created;
- targeted and full verification with exact results;
- newly discovered blockers, follow-up Issues, and hot-file ownership;
- whether the Issue can close.

The Orchestrator reviews this evidence before merging or releasing the slot.
