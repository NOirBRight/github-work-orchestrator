# GitHub/Paseo lifecycle

## Durable Issue states

Use exactly one repository lifecycle label on every open Issue:

- `needs-triage` — scope or ownership is unclear.
- `needs-info` — a named fact, policy, or decision is missing.
- `ready-for-agent` — a fresh Paseo Agent can execute the v3 contract.
- `ready-for-human` — a maintainer-only action is required.
- `wontfix` — no implementation is intended.

GitHub dependencies determine readiness for the frontier. Runtime Agent state
does not create a second Issue lifecycle.

## Campaign states

```text
PLANNED -> CLAIMED -> WAITING_HOTSET | ROOM_READY
WAITING_HOTSET -> ROOM_READY -> ORCHESTRATOR_CREATED -> ACTIVE
ACTIVE -> RESULT_POSTED -> VERIFIED -> WAITING_INTEGRATION -> MERGED -> ARCHIVED
ACTIVE -> BLOCKED | STOPPED -> durable handoff
```

Every transition needs readback from its owning system. Room messages announce
transitions but do not authorize them.

## Dispatch and continuation

The Repository Coordinator is the repository-resident root Agent and survives
every Campaign. Create one Campaign Orchestrator as its direct `subagent` for
each Campaign, then create Campaign-owned Workers as direct children of that
Campaign Orchestrator. Use `detached` only for an explicit handoff that must
survive its parent. A follow-up continues the same Agent only after it is idle;
a busy Agent receives room work for its next checkpoint.

Different Campaign Orchestrators may run concurrently with independent Provider
Bindings when admitted Hotsets do not overlap. Only the Campaign holding the
repository Integration Lease enters integration.

## Completion and cleanup

Completion requires accepted Issue behavior, locally green evidence, applicable
review/manual gates, a verified PR into `dev`, and durable GitHub readback.
Archive the Agent/worktree only after the cleanup guard passes. For delegated
work, archive the Agent first and read back both terminal archive state and an
unbound worktree before a second guard pass can authorize worktree cleanup. A
blocked or unsafe campaign remains visible with its room and worktree
preserved.

The Campaign Orchestrator never archives itself. After `CAMPAIGN_CLOSED`, the
Repository Coordinator may archive that direct child only after terminal Agent,
room, Git, and GitHub readback. `CAMPAIGN_CLOSED` never archives the Repository
Coordinator. Retiring the Repository Coordinator requires an external
supervisor and a durable repository-level handoff.
