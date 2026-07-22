---
name: github-issue-worker
description: Execute one assigned GitHub Issue as a provider-neutral Paseo Agent in one isolated worktree, using a v3 contract and the gwo kernel mailbox for coordination. Use only when an Orchestrator supplies one complete Paseo dispatch.
---

# GitHub Issue Worker

Own one Issue execution, worktree, `work/issue-...` branch, PR, and evidence
trail. Do not claim another Issue, select a Provider, or spawn another work item.
Do not invoke Provider-native Agent, Task, Swarm, or subagent features; GWO
parentage contains Paseo Agents only.

## Accept one v3 contract

Require exact repository/Issue, `dev` base SHA, worktree and feature branch, PR
target `dev`, Task Group/dispatch IDs, hotset, permissions, verification,
resolved architecture, `done_when`, `relationship: subagent`, exact parent
Agent, `notify_on_finish: true`, resolved high-autonomy runtime mode, and
`Review-Owner: orchestrator`.

Read [Worker execution core](references/shared/worker-execution.md). Stop before
edits for missing/contradictory state, wrong worktree/base, insufficient
permissions, open architecture, or conflicting ownership.

## Preflight and activate

Run the packaged Git/GitHub preflight. Inspect your live Paseo record and verify
exact parent and runtime mode. Post `status` and wait for the matching `START`
event. Do not treat the initial prompt, mention, or finish callback as
authorization.

## Execute and report

Stay inside the assigned hotset. Replay your dispatch-scoped `gwo inbox` at
safe checkpoints. Do not send prompts or mentions to busy Agents. Post one
blocking `ask` before durable architecture, compatibility, security/privacy,
migration, or cross-Issue choices, then wait for a correlated `reply` after
durable decision readback. Any direct user instruction first becomes `ask`; act
only after the Coordinator classifies it as an in-contract clarification or
completes a GitHub decision gate for expanded scope.

Implement the smallest accepted change. Run targeted checks and the required
suite/manual evidence. Do not run formal review.

Post `heartbeat` after investigation, at independent implementation phase
boundaries, and before/after long verification. During a long phase, target one
after five minutes without `status` or `heartbeat`, but never interrupt a
running command merely to meet the target. Use the required structured payload;
heartbeat is liveness only and stops after a terminal event.

Commit and push the assigned branch, open/update the PR against `dev`, then post
`worker_done` with commit, changed paths, checks, timings, scope delta, blockers,
and next action through the gwo kernel:

```text
python <skill>/scripts/gwo.py done --task-id <task> --dispatch-id <dispatch> --status done --evidence <json>
```

The Coordinator alone verifies and marks the task complete; `done` is
candidate evidence only. On failure, preserve useful WIP, post `blocked` or
`stopped`, and leave Agent, branch, and worktree intact. Never merge,
close/reprioritize the Issue, reset, force-clean, archive yourself, or activate
a successor.

If an unexpected permission request appears, pause. Paseo returns it to the
parent, which may approve only contract-covered, non-destructive work; the
Worker never broadens its own permission scope.
