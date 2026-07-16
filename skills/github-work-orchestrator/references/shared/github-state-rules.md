# GitHub and execution-owner state rules

GitHub Issues, native dependencies, assignees, linked PRs, and repository policy
are the only persistent work-state source. Codex tasks and Subagents are runtime
execution surfaces, not a second project ledger. Never edit Codex SQLite.

## Contents

- [Execution ownership](#execution-ownership)
- [Execution CWD and Skill installation topology](#execution-cwd-and-skill-installation-topology)
- [Reliable task materialization](#reliable-task-materialization)
- [Permission and repository preflight](#permission-and-repository-preflight)
- [Branch and collision evidence](#branch-and-collision-evidence)
- [Recovery and WIP preservation](#recovery-and-wip-preservation)
- [Event-triggered cleanup](#event-triggered-cleanup)

## Execution ownership

Keep one visible Orchestrator per repository/activity. Every active work item has
one isolated worktree, one exact editor, and one execution lane:

- Inline: the Orchestrator is the editor and evidence owner.
- Subagent: the Orchestrator remains the GitHub/evidence owner; one bounded
  Subagent edits only its assigned worktree/write set and returns the result.
- Visible Worker: one sidebar-visible Task owns execution and callback evidence.

A Subagent is not a persistent project owner and does not claim another Issue,
branch, PR, or lifecycle. The Orchestrator publishes and integrates its work.
Discovered independent work returns to Intake or the frontier.

Preserve dirty working trees and unrelated user work. Never use a shared
working directory or two simultaneous editors as a fallback.

## Execution CWD and Skill installation topology

Every isolated worktree is an execution-only CWD. It is not a Codex Saved
Project, Saved Workspace, or Skill installation root. The Orchestrator,
Subagent, and Visible Worker must never open, switch to, or persist an execution
worktree as a saved project. Pass the exact absolute CWD only through a
supported native execution contract.

If a lane cannot materialize at the assigned CWD without saving or registering
that path as a project, fail that lane with a sanitized platform limitation. Do
not add the worktree to another project or Saved Workspace as a workaround. A
different lane may proceed only when the router independently selects it and
single-editor ownership remains proven.

Never read-modify-write `.codex-global-state.json`,
`electron-saved-workspace-roots`, Codex SQLite, or equivalent private desktop
state during dispatch, recovery, or cleanup. Do not bypass a creation or
ownership guard through another project, window, state directory, hidden Task,
or private-state edit.

Each role Skill resolves from exactly one repository-documented canonical
installation. Never copy, install, junction, symlink, or generate a Skill under
a repository or execution worktree, and never encode per-Issue or per-Worker
state in a dynamic `SKILL.md`, plugin, or project-local Skill. Keep that runtime
state in the GitHub Issue and the native Task contract.

Version-controlled package sources and lazy `references/`, `scripts/`, and
`assets/` are package contents, not additional runtime Skill installations.
The compatibility policy that selects a canonical install target is a separate
maintainer decision; cloning this repository alone does not create an active
Skill installation.

## Reliable task materialization

This section applies only to the Visible Worker lane. Reserve the host-wide
creation singleflight immediately before the native Task-creation call. Keep
only `creating` or `uncertain` state. A queued client receipt is not a Task.

Release the creation guard as soon as native discovery identifies the exact real
Task and its worktree. Prefer the retained owner token. If its caller turn was
lost after a queued receipt, the exact recorded native request identity may
authenticate only a `task-materialized` release with the exact Task and owned
worktree; it cannot prove terminal no-Task or cancellation. The Task, assignee,
worktree, branch, and PR then carry runtime ownership. If the call outcome is
ambiguous, mark it `uncertain`, issue no second creation call, and use at most
one evidence-backed reconciliation.

A creation conflict blocks only the Visible Worker lane. Inline or Subagent
work may continue only when the router independently selects that lane and no
competing editor exists. Never bypass the guard through another project,
window, state directory, or hidden Task.

## Permission and repository preflight

Before editing, publishing, or running an expensive suite:

1. Report the effective filesystem, network, and approval profile.
2. Verify exact base SHA and integration ref.
3. Verify the isolated worktree is clean and the assigned branch/write set has
   no conflicting owner.
4. When GitHub access is required, run one read-only identity/repository query.
5. Continue only when commands require no approval prompt and match the
   execution contract.

Use the packaged deterministic Worker preflight where applicable. A narrower
profile or unexpected approval fails before edits. Do not change credentials,
permissions, or external state merely to prove access.

## Branch and collision evidence

Use the repository branch convention, defaulting to
`codex/issue-<number>-<slug>`, and target the documented integration branch.
Rebase or merge upstream only for a semantic dependency or proven write-set
collision.

Capture:

```text
git merge-base HEAD origin/<integration-branch>
git diff --name-only <merge-base>..HEAD
git diff --cached --name-only
git diff --name-only
git diff --name-only <merge-base>..origin/<integration-branch>
```

The local set is committed plus staged plus unstaged paths. Intersect it with
the upstream-only set. An empty intersection is not a collision without a
separate semantic dependency.

## Recovery and WIP preservation

A Task or Subagent error does not change the GitHub claim. Before replacing an
editor or removing a worktree:

1. Prove the editor is idle/terminal.
2. Inspect exact status and branch ownership.
3. If useful WIP exists, create one scoped checkpoint, verify it contains no
   transient/sensitive artifacts, push it, and verify the remote SHA.
4. Activate a successor only after the predecessor cannot edit and ownership is
   unambiguous.

If evidence, permission, or network access is insufficient, leave the Task and
worktree intact and report the requirement. Never reset, force-clean, or infer
safe deletion from age alone.

## Event-triggered cleanup

After a verified merge or stop, trigger cleanup immediately; complete it within
five minutes. This is a deadline, not a polling interval.

Use the execution policy cleanup plan with the exact absolute worktree, merged
branch when applicable, and Visible Worker Task ID. Remove an isolated worktree
only when it is clean, durable (pushed or integrated), inactive, and
unambiguously owned.
Delete a local branch only when merged. Task archiving is human-owned: report
the exact corresponding Visible Worker as ready for archive, but never call the
native archive action automatically because descendant cleanup can cross a
delegation boundary. Preserve and report dirty, unpushed, ambiguous, active, or
externally owned state.
