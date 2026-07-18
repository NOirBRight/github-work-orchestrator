# GWO cleanup safety policy

GWO owns cleanup authorization. This portable policy evaluates observed Paseo,
Git, and worktree evidence before invoking existing Paseo operations; it does
not require a host application, sidecar, or runtime source extension.

## Agent cleanup

An Agent may automatically archive only its direct idle child. It cannot use
force and cannot archive itself, a root Agent, a sibling, a detached Agent, or a
foreign Agent. A Campaign Orchestrator therefore cleans its Workers, while the
Repository Coordinator cleans a terminal Campaign Orchestrator only after
durable `CAMPAIGN_CLOSED` readback.

Campaign Orchestrator retirement is Agent-only cleanup. Call `cleanup-plan`
with `event=campaign-closed`, `agent_only: true`, no target worktree/branch or
bindings, exact Campaign identity, and the `CAMPAIGN_CLOSED` receipt. The first
eligible plan archives only that direct child; archived readback completes the
cleanup without inventing a `work/issue-*` branch or attempting to delete the
Repository Coordinator's shared control worktree.

Root retirement is out-of-band GWO maintenance performed by a human operator
through the existing Paseo UI or CLI after durable repository handoff. The
stable denial reasons remain:

- `SELF_ARCHIVE_FORBIDDEN`
- `ROOT_ARCHIVE_REQUIRES_SUPERVISOR`
- `ARCHIVE_TARGET_NOT_DIRECT_CHILD`
- `FORCE_REQUIRES_SUPERVISOR`
- `AGENT_NOT_IDLE`

`SUPERVISOR` in these stable legacy reasons names a human-authorized caller
class; it does not require a GWO supervisor module or service.

## Worktree cleanup

Return `WORKTREE_IN_USE` while any Agent is bound to the target worktree. Return
`CONTROL_WORKTREE_PROTECTED` when the target matches the GWO-observed trusted
protected control worktree or the actor's own control worktree.

Worker delegated cleanup is two-phase. First authorize and execute only the child Agent
archive. Read back the archived state and removal of its worktree binding, then
run `cleanup-plan` again before authorizing the worktree or merged branch
actions. Never infer the second phase from the first plan.

## Execution rule

`cleanup_policy.py` owns the complete GWO cleanup evidence and two-phase action
plan. `archive_policy.py` remains the smaller pure Agent/worktree authorization
primitive; neither module is a Paseo daemon adapter.

An eligible nonempty plan sets `automatic_execution: true`; GWO executes exactly
the returned actions in order through existing Paseo operations and reads back
each mutation. A protected plan sets it to false and returns no actions. Any
missing, contradictory, active, dirty, shared, or foreign evidence fails closed
without partial GWO actions.

Actor, target, execution, and terminal receipt carry exact matching repository,
campaign, and dispatch identity. Worker cleanup accepts only read-backed
`COMPLETED` or `STOPPED`; Campaign Orchestrator cleanup accepts only read-backed
`CAMPAIGN_CLOSED`. HEARTBEAT, CHECKPOINT, and WORKER_DONE are never terminal
cleanup evidence. `event=merged` additionally requires `branch_merged: true`.
