# GWO communication protocol

The gwo store mailbox is the primary coordination surface, with each addressed
event identified by its `publish_uuid`. GitHub Issues, PRs, commits, checks, and
repository decisions remain the durable business truth. Agent finish and
permission notifications are wake-up accelerators, never proof. Do not use
recurring Paseo heartbeats for normal coordination correctness.

## Coordinator mailbox

The Coordinator claims a repository lock in the gwo store. All addressed
requests flow through `gwo send --to coordinator`. A caller that is not the
Coordinator sends at most one sanitized summary of 500 characters plus a
SHA-256 of the original message, not the full message, then idles. Reject
credentials, private prompts, absolute paths, unknown payload fields, and
non-monotonic sequence.

The store mailbox is a persistent coordination cache, not business truth. The
Coordinator replays it at startup, before waiting, and before ending every turn.

## Task Group mailbox

All participating Paseo-managed Agents must have `PASEO_AGENT_ID` and
`GWO_AGENT_ID` available. Each material event is one row in the gwo store with
schema v1:

```text
schema_version, repository, signal_id, sequence, event_type,
sender_agent_id, sender_role, in_reply_to, payload
```

Allowed event types are `status`, `ask`, `reply`, `worker_done`, `review_result`,
`escalation`, `decision_gate`, and `heartbeat` (resident-agent model only). Role
entitlement per type is enforced by the gwo CLI at write time; Agents do not
filter events themselves.

`in_reply_to` is required on `reply`. Sequence increases per sender/dispatch.
Retries reuse the same `signal_id` and identical payload; a conflicting
duplicate or non-monotonic sequence blocks the Dispatch. Every write identity
comes from the spawn-injected `GWO_AGENT_ID`; callers cannot supply identity
columns. Room claims can no longer create authority because there is no way to
author a row with someone else's identity through the CLI.

A Worker always scopes `gwo inbox` to its exact `--dispatch-id`, so unrelated
Task Group lifecycle, sibling history, and Coordinator-owned review results
cannot block activation or review-fix work. Coordinator reconciliation remains
unscoped.

Every addressed material event uses the packaged delivery transaction. Publish
with `gwo send --to <recipient> --type <type> --signal-id <id>`. The returned
`publish_uuid` proves the daemon stored a message, not that the claimed author or
evidence is true. Combine the publish output with fresh exact sender/recipient
readbacks, then run `material_delivery.py delivery-plan` only when the recipient
is idle.
Execute only the returned action: send the exact signal-only prompt to an idle
recipient, or wait without prompting a running/initializing recipient. After an
accepted send, post the deterministic `DELIVERY_WAKE`. The recipient posts the
deterministic `DELIVERY_ACK` immediately after identity-verified replay and
before processing. A terminal sender does not claim successful handoff before
ACK. Invalid delivery metadata cannot poison the valid business event; Wake/ACK
never authorizes completion, merge, cleanup, or replacement.

HEARTBEAT is Worker-to-Coordinator liveness at safe boundaries with a
five-minute target. It is not Coordinator polling, completion, merge, or
cleanup evidence. Wait through `gwo inbox --wait` for at most 60 seconds.
Ordinary timeout only replays the mailbox. At 15 minutes without a valid runtime
signal, inspect once; silence never authorizes cancel, archive, or replacement.
Do not mention or send a prompt to a busy Agent; never prompt a running Agent;
prompt an idle non-terminal Agent once. The only shorter
status readback is for one already-pending Material Delivery: after its bounded
ACK wait, re-read that exact recipient and re-plan. If a recorded wake returns
idle without ACK (wake-unacknowledged), protect and escalate instead of sending again.

A Worker receiving direct user instructions first posts `ask`. A clarification
inside its contract may receive correlated `reply`; scope, architecture, Hotset,
compatibility, security, or integration changes enter a durable GitHub decision
gate. A Task Group may answer only within its scope. Cross-Task-Group, Hotset,
Integration Lease, or `dev` requests relay to the Coordinator.

## Event states

Use material states plus the bounded coordination events `status`, `ask`,
`reply`, `decision_gate`, `worker_done`, and `escalation`. `DELIVERY_WAKE` and
`DELIVERY_ACK` are transport receipts, not lifecycle states.

`HEARTBEAT` is a Worker-to-Coordinator liveness signal, not Orchestrator
polling. `heartbeat` is Worker liveness, never Coordinator polling. Post one at
safe phase boundaries and, during a long phase, target one after five minutes
without `status` or `HEARTBEAT`. The target is advisory:
a long test or blocking command may prevent posting. Evidence contains `phase`,
`last_completed_step`, `next_step`, `head_sha`, `worktree_dirty`, and
`blocking: false`. Use `status` for material progress. Reject HEARTBEAT after
`worker_done`, `BLOCKED`, or `STOPPED`.

`ask` carries one blocking question. `reply` references an already accepted ask
from the same Task Group and Dispatch, is sent by that ask's recipient, and is
addressed back to its sender. A maintainer-only answer uses `decision_gate`,
changes the Issue to `ready-for-human`, includes the durable GitHub decision
URL, and resumes only after that decision is read back.

Event authority is role-bound and enforced by the gwo CLI. The `implementation`
Agent role alone publishes `HEARTBEAT`, `ask`, and `worker_done`; Intake
publishes its bounded material result, Review publishes `review_result`, and
Monitor cannot impersonate an implementation Worker. Coordinator roles alone
publish `reply`, `decision_gate`, `escalation`, and integration lifecycle
events. Invalid events are rejected at write time, not filtered by consumers.

`worker_done` carries the candidate head, PR, changed paths, and verification
results. It never authorizes completion. The Coordinator cross-checks Paseo,
Git, worktree, GitHub, and the execution contract before marking complete.

`review_result` is Review-role-only and includes `axis: spec|quality|combined`,
candidate and base SHA, diff and acceptance SHA-256, review round, `full|delta`
scope, previous candidate SHA, pass/fail verdict, and findings. A Review
identity comes from `GWO_AGENT_ID`. Both axes for one round must reference the
same lock. Missing one axis remains incomplete; duplicate axes, different locks,
forged/cross-Task-Group evidence, or conflicting Signal-IDs block the Dispatch.
`review_result` never authorizes cleanup. `worker_done`, `heartbeat`,
`review_result`, `DELIVERY_WAKE`, and `DELIVERY_ACK` are never terminal cleanup
evidence. worker_done, heartbeat, review_result, delivery_wake, and delivery_ack
are never terminal cleanup evidence.

`escalation` records retry exhaustion or ambiguous recovery.

Worker terminal evidence includes branch, commit/PR, verification class,
commands and outcomes, phase timings, changed paths, scope delta, blockers, and
next action. Formal review remains Coordinator-owned.

A direct user message to a Worker first becomes one `ask`. The Coordinator may
reply only within the existing contract. Scope, architecture, Hotset,
compatibility, security, or integration changes use a durable GitHub decision
gate. Cross-Task-Group/Integration/`dev` instructions received by a Worker are
relayed to the Coordinator.

## Recovery

Finish callbacks are process-local convenience signals. After a daemon or parent
restart (daemon restart), replay the store mailbox, list Agents by Task
Group/dispatch and `paseo.parent-agent-id` labels, inspect their lifecycle, then
reconcile GitHub and worktrees. Also list pending Paseo permissions: a `permission_requested`
event notifies the parent when the Worker is a Paseo `subagent`, but that
notification can be lost across restart. The parent may approve only a
non-destructive request already authorized by the v3 permission profile and
hotset; deny and block every ambiguous or expanded request. Never create a
successor until the predecessor is proven terminal and ownership is
unambiguous. At 15 minutes without a runtime signal, inspect once. A running
Agent is left alone; an idle non-terminal Agent receives at most one recovery
prompt pointing to the last signal_id. Wait another 15 minutes before a second
stale inspection unless new evidence arrives.

## Completion

Write the durable result to GitHub before posting `TASK_GROUP_CLOSED`. Preserve
mailbox rows for blocked or handed-off Task Groups. Never place credentials,
provider tokens, local paths, or private prompts in either the store mailbox or
GitHub messages.
