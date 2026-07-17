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
PLANNED -> CLAIMED -> ROOM_READY -> AGENT_CREATED -> ACTIVE
ACTIVE -> RESULT_POSTED -> VERIFIED -> MERGED -> ARCHIVED
ACTIVE -> BLOCKED | STOPPED -> durable handoff
```

Every transition needs readback from its owning system. Room messages announce
transitions but do not authorize them.

## Dispatch and continuation

Create only Paseo-managed Agents with `relationship: subagent` for work whose
lifetime belongs to the campaign. Use `detached` only for an explicit handoff
that must survive the Orchestrator. A follow-up continues the same Agent only
after it is idle; a busy Agent receives room work for its next checkpoint.

## Completion and cleanup

Completion requires accepted Issue behavior, locally green evidence, applicable
review/manual gates, a verified PR into `dev`, and durable GitHub readback.
Archive the Agent/worktree only after the cleanup guard passes. A blocked or
unsafe campaign remains visible with its room and worktree preserved.
