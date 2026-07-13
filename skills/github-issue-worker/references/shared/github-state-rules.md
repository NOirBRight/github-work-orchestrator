# GitHub and visible-task state rules

GitHub Issues, native dependencies, assignees, linked PRs, and repository policy
are the only persistent work-state source. Codex tasks provide visible runtime
ownership; they are not a second project ledger.

## Visible ownership

Map each claimed GitHub work item to exactly one sidebar-visible Codex task and
one isolated worktree. The task is the auditable owner of the Issue, branch,
PR, callback, and completion evidence.

An Orchestrator or Worker may use subagents for bounded research, review, test
analysis, or implementation slices inside the same owned Issue. A subagent is
never the visible owner, dispatch target, lifecycle record, or hidden Worker for
another GitHub work item. Discovered independent work returns to Intake or the
Orchestrator for its own Issue and visible task.

Preserve dirty working trees and unrelated user work. Create a Worker from the
documented integration branch and exact assigned SHA. Never use a shared
working directory as a dispatch fallback.

## Reliable task materialization

Use two stages when the task host can create a worktree before its conversation
or rollout is ready:

1. Create the isolated-worktree task at the exact base using a fast, verified,
   low-cost bootstrap binding. Send only
   `[#<number>] Bootstrap only. Reply exactly READY. Do not use tools.`
2. Wait for a real task ID in the native task list and for the bootstrap turn
   to complete. A client-side creation ID or created worktree alone is not a
   Worker.
3. Rename the materialized task to `[#<number>] <issue title>`.
4. Send the full `github-issue-worker` contract to that same task with the
   selected Worker model and reasoning level. This first full turn establishes
   the recorded binding.
5. Run permission preflight before repository work.

Keep the bootstrap uniquely Issue-scoped but omit the full Issue title and
contract so a failed stub cannot masquerade as a second Worker. If startup
services are suspected, use a bounded A/B test and disable only a proven,
non-required service through an authorized reversible change.

If creation returns only a client ID or worktree, confirm absence through the
native task list, keep IDs private, identify a concrete startup cause, and make
at most one replacement attempt after that cause is removed or isolated. Clean
failed stubs only through a supported native archive/delete action; do not edit
Codex internal databases.

## Permission and repository preflight

Treat permissions as task-host state, not authority granted by a prompt. Before
editing, publishing, or running an expensive suite:

1. Report the effective filesystem sandbox, approval policy, and network
   profile exposed to the task.
2. Run `git status --short --branch` in the assigned worktree.
3. When GitHub access is required, run one read-only identity or repository
   query such as `gh api user` or `gh repo view`.
4. Continue only when the commands require no approval prompt and the effective
   profile satisfies the dispatch contract.

On a narrower profile or approval request, send one `BLOCKED` signal and stop
before work. Do not use destructive commands, credential changes, or writes
outside the worktree to prove permissions.

## Branch and collision evidence

Use the repository branch convention, defaulting to
`codex/issue-<number>-<slug>`, and target the documented integration branch.
Rebase or merge upstream only when the work semantically depends on it or a
merge-base comparison proves a write-set collision.

Before reporting a collision, capture:

```text
git merge-base HEAD origin/<integration-branch>
git diff --name-only <merge-base>..HEAD
git diff --cached --name-only
git diff --name-only
git diff --name-only <merge-base>..origin/<integration-branch>
```

The Worker-side set is committed plus staged plus unstaged paths. Intersect it
with the upstream-only set. An empty intersection is not a collision unless a
separate semantic dependency exists.

## Recovery and WIP preservation

A task-level `systemError`, disconnect, or failed continuation does not change
the GitHub claim. Attempt one normal continuation while branch and worktree
remain intact. If the same task fails again before a meaningful response, use
one native visible-task fork or handoff on the same branch/worktree when
supported.

Before archiving the sole durable owner of uncommitted work:

1. Ensure every Worker on that worktree is idle so concurrent edits cannot
   occur.
2. Create a scoped checkpoint commit on the existing feature branch.
3. Verify the checkpoint contains no transient or sensitive artifacts.
4. Push it to the existing remote branch and verify the remote SHA.
5. Archive or clean the predecessor only after the worktree is clean or the
   remote checkpoint is verified.

A same-directory fork is not a durable WIP backup when archiving either task
may remove the shared worktree. If checkpoint permission or network access is
insufficient, leave the task/worktree intact and report the recovery
requirement.

Tell the successor to inspect status and the current diff before editing. Keep
the Issue claim, branch, PR, callback, model profile, and authority boundaries.
Exactly one visible task may edit a worktree. When same-worktree succession is
unsafe, preserve and verify the branch remotely, deactivate the predecessor,
then create one replacement worktree from that branch.

Recovery is complete when useful WIP is durable, only one visible owner remains,
and the replacement has revalidated identity, permissions, and repository
state.
