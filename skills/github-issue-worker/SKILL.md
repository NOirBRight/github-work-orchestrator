---
name: github-issue-worker
description: Execute one assigned GitHub Issue in one sidebar-visible Codex task and isolated worktree, honoring the pinned base SHA, branch, hotset, model binding, permission profile, acceptance criteria, verification, commit, push, and PR target. Use when an Orchestrator supplies an explicit assigned-Issue execution contract.
---

# GitHub Issue Worker

Own one assigned Issue, worktree, branch, PR, and evidence trail. The visible
task is the Worker of record; bounded subagents may assist inside this Issue but
do not own another Issue, branch, PR, or lifecycle.

## Accept one execution contract

Read the Orchestrator message and identify:

1. Issue URL and acceptance criteria.
2. Applicable repository instructions.
3. Model profile and concrete binding.
4. Exact base branch/SHA, feature branch, and PR target.
5. Owned components/hotset and prohibited writes.
6. Accepted decisions and local decision authority.
7. Dependencies, verification commands, closing semantics, and callback task.

Read lifecycle [role ownership](references/shared/lifecycle.md#role-ownership)
and the model-profile [selection order](references/shared/model-profiles.md#selection-order).

Stop with `BLOCKED` when the assignment is missing a required identity or when
the requested model, permissions, base, branch, or worktree cannot be honored.
Do not claim another Issue or silently broaden this one.

The contract is accepted when every identity and authority field is explicit
and the task owns exactly one Issue.

## Pass preflight before edits

Run the shared
[permission and repository preflight](references/shared/github-state-rules.md#permission-and-repository-preflight),
then apply its [visible ownership](references/shared/github-state-rules.md#visible-ownership)
and [collision evidence](references/shared/github-state-rules.md#branch-and-collision-evidence)
rules. Read applicable `AGENTS.md`, domain context, ADRs, the Issue and comments,
linked PR state, and the base diff. Post a short plan naming expected writes,
verification, and collision evidence before editing. Read
[recovery and WIP preservation](references/shared/github-state-rules.md#recovery-and-wip-preservation)
only when task-host failure or succession makes that branch relevant.

Preflight is complete when effective permissions are sufficient, GitHub access
works without approval prompts, the branch matches the assigned base, and the
expected write set is collision-safe.

## Build a red-capable execution loop

For a bug, reproduce the reported symptom at a public seam before the fix when
applicable. Convert the smallest faithful reproduction into a regression test.
For a feature, use the narrowest acceptance example as the first verification
slice. Work vertically: one observable behavior, its minimal implementation,
then the next.

Escalate with `DISCUSSION_REQUIRED` when evidence exposes a product-direction,
durable architecture, public contract, compatibility, security/privacy,
migration, or cross-Issue choice outside the accepted authority. Continue only
safe independent work while waiting.

The loop is ready when it can distinguish the requested behavior from the
reported failure without relying on private implementation details.

## Implement and verify the assigned scope

Make the smallest coherent change that satisfies the Issue. Keep subagent
work bounded to this Issue and integrate every result in this visible task.
Compare upstream changes from the merge base before claiming a collision; an
advanced integration branch alone is not a blocker.

Run targeted verification during implementation and the full required suite at
the end. Review the final diff for hotset, generated artifacts, credentials,
private task IDs, local paths, and unrelated changes.

Implementation is complete when acceptance criteria pass, required checks are
recorded, and the final diff contains only authorized work.

## Publish and signal

Commit, push, and open or update the PR under lifecycle
[role ownership](references/shared/lifecycle.md#role-ownership) and
[completion](references/shared/lifecycle.md#claim-branch-pr-and-completion)
rules.

Before the final response, emit the applicable shared
[Worker signals](references/shared/communication-protocol.md#worker-signals)
and complete the shared
[delivery handshake](references/shared/communication-protocol.md#delivery-handshake)
to the exact Orchestrator callback.

Worker completion requires a clean or intentionally preserved worktree, a
reviewable remote commit/PR when publication was assigned, complete evidence,
and a recorded callback delivery outcome.
