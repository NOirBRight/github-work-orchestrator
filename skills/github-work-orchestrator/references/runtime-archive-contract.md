# Paseo runtime archive authorization contract

This contract is the daemon-side defense for archive operations. The packaged
`archive_policy.py` is the provider-neutral reference implementation; a Paseo
adapter supplies observed lifecycle, parentage, workspace bindings, and an
agent-scoped caller context that callers cannot replace with a claimed ID.

## Agent archive

An Agent actor may archive only its direct idle child. It cannot use force and
cannot archive itself, a root Agent, a sibling, or a foreign Agent. A Campaign
Orchestrator therefore cleans its Workers, while the Repository Coordinator
cleans a terminal Campaign Orchestrator after durable Campaign readback.

Only an external supervisor or human UI may archive a root Agent or force an
active Agent archive. Denial returns one of these stable errors:

- `SELF_ARCHIVE_FORBIDDEN`
- `ROOT_ARCHIVE_REQUIRES_SUPERVISOR`
- `ARCHIVE_TARGET_NOT_DIRECT_CHILD`
- `FORCE_REQUIRES_SUPERVISOR`
- `AGENT_NOT_IDLE`

## Worktree archive

Reject the request with `WORKTREE_IN_USE` while any unarchived Agent is bound to
the target worktree. The adapter supplies the Repository Coordinator's trusted
protected control worktree independently of the actor's request. Return
`CONTROL_WORKTREE_PROTECTED` when the target matches that repository control
worktree or an Agent actor's own control worktree. An Agent archive must be read
back, including removal of its worktree binding, before a subsequent worktree
archive is authorized.

## Mutation rule

Authorization is evaluated before interruption, lifecycle changes, branch
deletion, or filesystem removal. A denied request has no partial side effects.
The daemon returns the decision and stable error before invoking any mutating
adapter.
