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

Delegated cleanup is two-phase. First authorize and execute only the child Agent
archive. Read back the archived state and removal of its worktree binding, then
run `cleanup-plan` again before authorizing the worktree or merged branch
actions. Never infer the second phase from the first plan.

## Execution rule

An eligible nonempty plan sets `automatic_execution: true`; GWO executes exactly
the returned actions in order through existing Paseo operations and reads back
each mutation. A protected plan sets it to false and returns no actions. Any
missing, contradictory, active, dirty, shared, or foreign evidence fails closed
without partial GWO actions.
