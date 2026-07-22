# Cleanup safety policy

GWO owns cleanup authorization. Cleanup is staged and fail-closed. An adapter only executes a
read-backed plan produced by `cleanup-plan` v4.3 with explicit `target_kind`
and `resource_kind`.

## Protected targets

Protected plans contain no actions. Never target the Coordinator, a
root/sibling/foreign/detached Agent, Coordinator Home, Integration Control
Worktree, a dirty/active/ambiguous resource, or `main`/`dev`.

## Cleanup order

Archive the Agent first, read back `archived + unbound`, then authorize
worktree actions. For a Worker with an Issue worktree use
`worker / issue-worktree`; for a Reviewer with no worktree use `worker / none`.
Self-archive is forbidden; only a direct idle child may be archived. After all
direct children are terminal and `TASK_GROUP_CLOSED` is durable, archive the
Task Group label. There is no Campaign Agent or Campaign Control Workspace. Read
back the removal of all worktree bindings before any resource action.

## Probe cleanup

For forward tests: `ephemeral / none`, with lifecycle label
`gwo.lifecycle=ephemeral` and captured result readback.

## Evidence

`worker_done`, heartbeat, `review_result`, `DELIVERY_WAKE`, and `DELIVERY_ACK`
are never terminal cleanup evidence. Only an explicit `branch_merged: true`
GitHub/Git readback or durable terminal Agent state authorizes cleanup. The
terminal cleanup evidence shape is `event=merged` with `branch_merged: true`;
these events alone do not authorize completion.

## Failure mode

Ambiguous ownership, dirty state, missing readback, or force-required actions
produce a protected plan with no actions. Never use force.
