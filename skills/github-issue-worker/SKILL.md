---
name: github-issue-worker
description: Execute one assigned GitHub Issue as a provider-neutral Paseo Agent in one isolated worktree, using a v3 contract and a campaign room for coordination. Use only when an Orchestrator supplies one complete Paseo dispatch.
---

# GitHub Issue Worker

Own one Issue execution, worktree, `work/issue-...` branch, PR, and evidence
trail. Do not claim another Issue, select a Provider, or spawn another work item.

## Accept one v3 contract

Require exact repository/Issue, `dev` base SHA, worktree and feature branch, PR
target `dev`, campaign/dispatch IDs, room, hotset, permissions, verification,
resolved architecture, `done_when`, `relationship: subagent`, exact parent
Agent, `notify_on_finish: true`, resolved high-autonomy runtime mode, and
`Review-Owner: orchestrator`.

Read [Paseo Worker core](references/shared/worker-execution.md). Stop before
edits for missing/contradictory state, wrong worktree/base, insufficient
permissions, open architecture, or conflicting ownership.

## Preflight and activate

Run the packaged Git/GitHub preflight and room preflight. Inspect your live
Paseo record and verify exact parent and runtime mode. Post `AGENT_READY` and
wait for the matching room `START`. Do not treat the initial prompt, mention,
or finish callback as authorization.

## Execute and report

Stay inside the assigned hotset. Replay the room at safe checkpoints. Do not
send prompts or mentions to busy Agents. Post `DISCUSSION_REQUIRED` before
durable architecture, compatibility, security/privacy, migration, or
cross-Issue choices.

Implement the smallest accepted change. Run targeted checks and the required
suite/manual evidence. Do not run formal review.

Commit and push the assigned branch, open/update the PR against `dev`, then post
`PR_OPENED` and `READY_FOR_REVIEW` or `COMPLETED` with commit, changed paths,
checks, timings, scope delta, blockers, and next action.

On failure, preserve useful WIP, post `BLOCKED` or `STOPPED`, and leave Agent,
branch, and worktree intact. Never merge, close/reprioritize the Issue, reset,
force-clean, archive yourself, or activate a successor.

If an unexpected permission request appears, pause. Paseo returns it to the
parent, which may approve only contract-covered, non-destructive work; the
Worker never broadens its own permission scope.
