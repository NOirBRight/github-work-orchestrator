# Paseo campaign communication protocol

Paseo room messages are the primary coordination surface. GitHub Issues, PRs,
commits, checks, and repository decisions remain the durable business truth.
Agent finish and permission notifications are wake-up accelerators, never
proof. Do not use recurring Paseo heartbeats for normal Campaign correctness.

## Campaign room

Create exactly one room named `gwo-<campaign-id>` before dispatch. Every
participating Paseo-managed Agent must pass a read/post preflight through the
packaged `paseo_room.py` helper and must have `PASEO_AGENT_ID` available.

Each material event is a v1 JSON envelope containing:

```text
schema_version, signal_id, campaign_id, dispatch_id, sequence, event_type,
issue, sender_agent_id, recipient_agent_id, evidence, next_action
```

`in_reply_to` is required on `REPLY`. Sequence increases per sender/dispatch.
Retries reuse the same Signal-ID and identical payload; a conflicting duplicate
or non-monotonic sequence blocks the Dispatch. Post and replay both reject an
event whose Campaign does not match the `gwo-<campaign-id>` room.

Agent-authored post fails when `PASEO_AGENT_ID` is absent or differs from
`sender_agent_id`. Before replay/wait, build an identity-receipt JSON array from
Paseo readback, not from room claims. Each receipt separates the Agent's static
identity from its event authority. Worker labels own one exact campaign and
Dispatch (`authority.kind: dispatch-owner`). A Campaign Orchestrator keeps one
static Campaign identity and supplies read-backed `campaign-control` or
`direct-child-dispatch` authority; it does not relabel itself for every child.
The Repository Coordinator keeps repository-level labels and supplies
`admitted-campaign` authority for its direct Campaign child. Every authority
records the event campaign/Dispatch, subject Agent/parentage/labels where
applicable, and `read_back: true`. Pass the array with `--identity-receipts`.
Replay requires both the chat author and receipt Agent to match
`sender_agent_id`; missing or contradictory evidence rejects the event and
blocks its Dispatch.
Reuse receipts across ordinary wait timeouts; refresh them on Agent creation,
runtime notification, recovery, or an unknown sender so waiting does not become
polling.

The top-level campaign/Dispatch is the authority lookup key, not a demand to
mutate Coordinator labels. A Worker receipt has this shape:

```json
[
  {
    "agent_id": "agent-worker-143",
    "campaign_id": "campaign-20260718",
    "dispatch_id": "dispatch-issue-143-a1",
    "role": "implementation",
    "parent_agent_id": "agent-campaign",
    "relationship": "subagent",
    "labels": {
      "repository": "owner/repo",
      "campaign_id": "campaign-20260718",
      "dispatch_id": "dispatch-issue-143-a1",
      "role": "implementation"
    },
    "authority": {
      "kind": "dispatch-owner",
      "campaign_id": "campaign-20260718",
      "dispatch_id": "dispatch-issue-143-a1",
      "subject_agent_id": "agent-worker-143",
      "read_back": true
    },
    "read_back": true
  }
]
```

For a Campaign Orchestrator, omit `dispatch_id` from static labels and use
`direct-child-dispatch` authority whose subject is the read-backed child with
`subject_parent_agent_id` equal to the Orchestrator. Use `campaign-control` with
the Orchestrator itself as subject for Campaign lifecycle/CHECKPOINT events. A
Repository Coordinator likewise keeps only repository/role labels and uses
`admitted-campaign` authority whose subject is its direct Campaign child.
For `START`, the event recipient must be that authority subject: the Repository
Coordinator may activate its direct Campaign Orchestrator, while only the
Campaign Orchestrator may activate one of its direct Workers.

The chat message UUID is a publish receipt. It proves that the daemon stored a
message, not that the claimed author or evidence is true. Re-read Agent state,
GitHub, Git, the worktree, and verification evidence before acting.

## Delivery and wake-up

1. Publish the complete event to the campaign room.
2. Consumers replay the bounded room and deduplicate by `signal_id`.
3. Use `chat wait` for at most 60 seconds; after every wake or timeout, replay
   the room so the CLI read/wait race cannot lose an event.
4. Do not mention or send a prompt to a busy Agent. A prompt may replace its
   active run. Record the work in the room and let the Agent read it at its next
   safe checkpoint.
5. When the target is verified idle, `send_agent_prompt` may point it to the
   exact room message UUID. The room remains the authoritative communication.

An ordinary wait timeout causes only room replay. Do not poll a running Agent.
A full Agent/timeline inspection is allowed after 15 minutes without a valid
runtime signal, after a finish/permission notification, or during explicit
restart recovery. A stale signal alone never authorizes cancel, replacement,
archive, merge, or cleanup.

## Event states

Use material states plus the bounded coordination events `HEARTBEAT`, `ASK`,
`REPLY`, `DECISION_GATE`, `WORKER_DONE`, and `ESCALATION`.

`HEARTBEAT` is a Worker-to-Campaign-Orchestrator liveness signal, not
Orchestrator polling. Post one at safe phase boundaries and, during a long
phase, target one after five minutes without `PROGRESS` or `HEARTBEAT`. The
target is advisory: a long test or blocking command may prevent posting.
Evidence contains `phase`, `last_completed_step`, `next_step`, `head_sha`,
`worktree_dirty`, and `blocking: false`. Use `PROGRESS` for material progress.
Reject HEARTBEAT after `WORKER_DONE`, `BLOCKED`, or `STOPPED`.

`ASK` carries one blocking question. `REPLY` references an already accepted ASK
from the same Campaign and Dispatch, is sent by that ASK's recipient, and is
addressed back to its sender. A
maintainer-only answer uses `DECISION_GATE`, changes the Issue to
`ready-for-human`, includes the durable GitHub decision URL, and resumes only
after that decision is read back.

Event authority is role-bound. The `implementation` Agent role alone publishes
`HEARTBEAT`, `ASK`, `PR_OPENED`, and `WORKER_DONE`; Intake publishes its bounded
material result, Review publishes `REVIEW_RESULT`, and Monitor cannot impersonate
an implementation Worker. Coordinator roles alone publish `REPLY`,
`DECISION_GATE`, `ESCALATION`, and `CHECKPOINT`. The helper also enforces the
corresponding authority for START, completion, and Campaign lifecycle events.

`WORKER_DONE` carries the candidate head, PR, changed paths, and verification
results. It never authorizes completion. The Campaign Orchestrator cross-checks
Paseo, Git, worktree, GitHub, and the execution contract before posting
`READY_FOR_REVIEW` or `COMPLETED`.

`CHECKPOINT` is Orchestrator-owned recovery metadata: replay cursor, active
Dispatch IDs, and pending Signal-IDs. It is not Agent liveness or business
truth. `ESCALATION` records retry exhaustion or ambiguous recovery.

Worker terminal evidence includes branch, commit/PR, verification class,
commands and outcomes, phase timings, changed paths, scope delta, blockers, and
next action. Formal review remains Orchestrator-owned.

## Recovery

Finish callbacks are process-local convenience signals. After a daemon or
parent restart, replay the campaign room, list Agents by campaign/dispatch and
`paseo.parent-agent-id` labels, inspect their lifecycle, then reconcile GitHub
and worktrees. Also list pending Paseo permissions: a `permission_requested`
event notifies the parent when the Worker is a Paseo `subagent`, but that
notification can be lost across restart. The parent may approve only a
non-destructive request already authorized by the v3 permission profile and
hotset; deny and block every ambiguous or expanded request. Never create a
successor until the predecessor is proven terminal and ownership is
unambiguous. At 15 minutes without a runtime signal, inspect once. A running
Agent is left alone; an idle non-terminal Agent receives at most one recovery
prompt pointing to the last room Signal-ID. Wait another 15 minutes before a
second stale inspection unless new evidence arrives.

## Completion

Write the durable result to GitHub before posting `CAMPAIGN_CLOSED`. Delete a
completed room only after that readback succeeds. Preserve rooms for blocked or
handed-off campaigns. Never place credentials, provider tokens, local paths, or
private prompts in room or GitHub messages.
