# GitHub/Paseo lifecycle

## Durable Issue states

Use exactly one lifecycle label on each open Issue:

- `needs-triage` — scope/ownership unclear.
- `needs-info` — named fact/policy/decision missing.
- `ready-for-agent` — fresh Paseo Agent can execute the v3 contract.
- `ready-for-human` — maintainer-only action required.
- `wontfix` — no implementation intended.

GitHub dependencies determine frontier readiness. Runtime Agent state never
creates a second Issue lifecycle.

## Campaign states

```text
PLANNED -> ROOM_READY -> CONTROL_CREATING -> CONTROL_VERIFIED -> ACTIVE
ACTIVE -> RESULT_POSTED -> VERIFIED -> REVIEWING -> WAITING_INTEGRATION
WAITING_INTEGRATION -> MERGED -> CLOSING -> ARCHIVED
ACTIVE | REVIEWING -> BLOCKED | STOPPED -> durable handoff
```

`CONTROL_CREATING` is a preserve-and-reconcile admission transaction. A
partial Agent/Workspace create is never blindly retried or automatically
deleted. New v4.3 Campaigns become ACTIVE only after exact parent, Workspace,
Provider/mode, labels, branch/head, local-only branch, clean state, and zero
unique commits are read back. Legacy active Campaigns remain Agent-only.

While ACTIVE, run:

```text
RECONCILE_CAMPAIGN -> PLAN_WAVE -> DISPATCH_WAVE -> WAIT_WORKERS
WAIT_WORKERS -> VERIFY_RESULTS -> REVIEW -> RETURN_CANDIDATE
RETURN_CANDIDATE -> WAITING_INTEGRATION
```

Plan all eligible Workers together, up to three dedicated Worker slots. A
finish notification accelerates wake only. `chat wait` is bounded to 60 seconds;
timeout replays without polling. HEARTBEAT changes visibility only. Fifteen
minutes of silence permits one inspection, never cancellation/replacement.
Every addressed material transition uses `post-material` and remains pending
until the exact recipient posts `DELIVERY_ACK`. During that handshake only, the
sender may re-read the one recipient after a bounded ACK wait; it never prompts
a running Agent. `DELIVERY_WAKE`/`DELIVERY_ACK` do not advance these lifecycle
states.

## Parentage and direct messages

The Coordinator is the repository root and survives all Campaigns. Each
Campaign is its direct Paseo `subagent`; Workers, Spec Reviewer, and Quality
Reviewer are direct Campaign subagents. Use `detached` only for explicit
handoff outside GWO ownership. Provider-native subagents never enter this tree.

A direct user message to Campaign is accepted only inside Campaign scope.
Cross-Campaign/Hotset/Integration/`dev` changes relay to Coordinator. A Worker
first posts ASK. In-contract clarification may receive REPLY; durable scope or
architecture changes require a GitHub decision gate.

## Review and integration

Fast review stays in Campaign. Standard/strict creates/reuses exactly two
independent Reviewers. Both lock the same candidate/base/diff/acceptance and
both results are required. The Campaign-issued lock is persisted/read back;
Reviewer claims cannot authorize it. Recovery retains the full lock and both
Reviewer IDs. A failed axis returns to the same Worker; the next round is delta
for both axes with prior-lock lineage.

Different Campaign Provider Bindings may run concurrently. Only the Integration
Lease holder can merge. Dirty/missing Integration Control leaves the candidate
`WAITING_INTEGRATION` without stopping Workers or mutating user WIP.

## Completion and cleanup

Completion requires accepted behavior, green evidence, both applicable review
axes/manual gates, verified PR into `dev`, and durable readback. After three
terminal failed attempts, move to `ready-for-human`; attempt four is never
automatic.

Cleanup order is Workers and Reviewers, Campaign Agent, Campaign Control
Workspace, then local branch. The direct-child list and each phase require new
readback; worktree archive and branch deletion are never one action wave. Legacy v4.2 Campaigns
without control worktree retire Agent-only. Blocked/unsafe state stays visible.
The Campaign never archives itself; `CAMPAIGN_CLOSED` never archives the
Coordinator. task_group_closed never archives the Coordinator. Coordinator
retirement is human-only after durable handoff.
