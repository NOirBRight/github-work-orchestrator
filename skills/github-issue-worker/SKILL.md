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

Read [GitHub state rules](references/shared/github-state-rules.md),
[lifecycle](references/shared/lifecycle.md),
[model profiles](references/shared/model-profiles.md), and
[communication protocol](references/shared/communication-protocol.md).

Stop with `BLOCKED` when the assignment is missing a required identity or when
the requested model, permissions, base, branch, or worktree cannot be honored.
Do not claim another Issue or silently broaden this one.

The contract is accepted when every identity and authority field is explicit
and the task owns exactly one Issue.

## Pass preflight before edits

Run the permission and repository preflight in the shared GitHub state rules.
Then read applicable `AGENTS.md`, domain context, ADRs, the Issue and comments,
linked PR state, and the base diff. Post a short plan naming expected writes,
verification, and collision evidence before editing.

Preserve unrelated user work. If the worktree or branch contains unexpected
changes, stop before cleanup and report the exact ownership conflict.

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

Commit intentionally, push the assigned branch without rewriting protected
history, and open or update the PR against the documented integration branch.
Use closing keywords only when the PR satisfies the whole Issue. Do not merge,
close or relabel the Issue, reprioritize, or alter the Milestone without
authorization.

Before the final response, call native `send_message_to_thread` to the exact
Orchestrator callback with `PR_OPENED` after publication and
`READY_FOR_REVIEW` after required verification. A final answer in this Worker
task is not callback delivery. Reuse the same `Signal-ID` for the one permitted
transport retry; record `CALLBACK_DELIVERY_FAILED` if the retry fails.

Use `BLOCKED`, `DISCUSSION_REQUIRED`, or `STOPPED` for incomplete outcomes.
Include Issue, branch, commit, PR, verification, hotset, blocker, and next
action as defined by the shared protocol.

Worker completion requires a clean or intentionally preserved worktree, a
reviewable remote commit/PR when publication was assigned, complete evidence,
and a recorded callback delivery outcome.
