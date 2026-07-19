# Paseo campaign communication protocol

Paseo room messages are the primary coordination surface. GitHub Issues, PRs,
commits, checks, and repository decisions remain the durable business truth.
Agent finish and permission notifications are wake-up accelerators, never
proof. Do not use recurring Paseo heartbeats for normal Campaign correctness.

## Repository Room

Use one repository mailbox named `gwo-repo-<slug>-<digest>` with schema v1; the
digest prevents slug collisions between repositories:

```text
schema_version, repository, signal_id, sequence, event_type,
sender_agent_id, sender_role, in_reply_to, payload
```

Allowed events are `OPERATOR_REQUEST`, `REQUEST_ACCEPTED`, and
`REQUEST_REJECTED`. Operator Relay publishes the request; only the Repository
Coordinator replies with exact `in_reply_to`. Request payload contains a
sanitized summary of at most 500 characters and SHA-256 of the original message,
not the full message. Reject credentials, private prompts, absolute paths,
unknown payload fields, author/label mismatch, conflicting Signal-ID, and
non-monotonic sequence. A conflicting request poisons its Signal-ID: correlated
responses are filtered whether they arrived before or after the conflict.

The Repository Room is a persistent mailbox, not business truth. A Relay posts
once, reads Coordinator state once, wakes only an idle Coordinator using the
Signal-ID alone, and then idles. The Coordinator replays at startup, before
waiting, and before ending its turn so a busy/idle race cannot lose a request.

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
Paseo readback, not from room claims. Use `paseo_room.py identity-plan` with an
explicit `authority_scope` of `worker-dispatch`, `review-dispatch`,
`campaign-control`, or `campaign-admission`; never infer control authority from
Dispatch-ID spelling and do not hand-author `authority` fields. Each
receipt separates the Agent's static
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

A Worker always passes `--consumer-role worker --dispatch-id
<its-exact-dispatch>` to replay/wait.
Events for Repository/Campaign lifecycle or sibling Dispatches are ignored
before receipt lookup, so unrelated historical senders cannot block Worker
activation. `REVIEW_RESULT` is also excluded from the Worker consumer view;
formal review aggregation belongs to the Campaign and cannot poison Worker
review-fix replay when the Worker has no review-lock file. The Campaign performs
unscoped reconciliation and therefore owns the larger receipt set.

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

Reusable Reviewers likewise omit `dispatch_id` from static labels. A
`review-dispatch` identity plan requires exactly one read-backed assignment for
each fixed axis. The assignment binds Agent, Campaign parent, axis, full
candidate lock, and current Dispatch; its authority kind is
`reusable-reviewer`. The Campaign's direct-child authority may contain both
assigned Reviewers, so a shared review Dispatch never requires relabeling or
hand-authored duplicate Campaign receipts. The named parent must itself appear
as a read-backed same-repository/same-Campaign Orchestrator; matching child and
assignment strings without that parent receipt are invalid. Worker-dispatch
identity compilation applies the same parent rule.

`campaign-control` scope also requires the Campaign's exact parent to appear as
a read-backed root `repository-coordinator` for the same repository. A Campaign
label plus an arbitrary parent string cannot mint lifecycle/CHECKPOINT authority.

The chat message UUID is a publish receipt. It proves that the daemon stored a
message, not that the claimed author or evidence is true. Re-read Agent state,
GitHub, Git, the worktree, and verification evidence before acting.

## Material delivery and wake-up

Every explicitly addressed event except `PROGRESS`, `HEARTBEAT`,
`DELIVERY_WAKE`, and `DELIVERY_ACK` uses the mandatory Material Delivery
transaction. Do not end a terminal Worker/Reviewer/Campaign handoff merely
because `chat post` returned a UUID.
Do not mention or send a prompt to a busy Agent.

1. Publish through `paseo_room.py post-material` with the exact identity-plan
   authority scope and `--identity-receipts <compiled-json-file>`. Publication
   fails before writing unless the sender, recipient, direct relationship, and
   authority scope match those receipts. Its output contains the publish UUID
   and normalized pending `delivery` object. Plain `post` remains for visibility
   and delivery-control events.
2. Add fresh sender and recipient Paseo readbacks to that object and run
   `material_delivery.py delivery-plan`. Self, sibling, foreign, archived,
   wrong-scope, unverified, or non-direct relationships fail closed.
3. If the exact recipient is idle, execute the returned `send-signal-only`
   action once. The prompt has only `GWO_WAKE room=<room> signal=<signal-id>
   message=<uuid>`; it contains no evidence or task prompt. After Paseo accepts
   the send, run `wake-receipt-plan` and post the deterministic
   `DELIVERY_WAKE` event.
4. If the recipient is running or initializing, do not send a prompt. Wait for
   an ACK with `chat wait` for at most 60 seconds, replay, then read only that
   exact recipient's status and re-run `delivery-plan`. This narrow delivery
   readback is not Worker polling and never inspects the timeline. Never prompt
   a running Agent.
5. After the recipient identity-verifies and replays the source event, it runs
   `ack-plan` and posts `DELIVERY_ACK` before processing the source. The sender
   replays until the delivery state is `acknowledged`; ACK means accepted into
   reconciliation, not that the requested work is finished.

`DELIVERY_WAKE` and `DELIVERY_ACK` are deterministic, correlated by source
Signal-ID, publish UUID, sender/recipient, authority scope, and delivery digest.
Exact retries deduplicate. Invalid delivery metadata is rejected separately
and cannot poison the valid business event or Dispatch. Neither event changes
GitHub state, proves liveness, authorizes merge, or counts as cleanup terminal
evidence. `delivery-plan` rejects a caller-claimed `wake-sent` or
`acknowledged` state unless replay supplied the matching deterministic Signal-ID
and Room message UUID.

A `wake-sent` delivery whose recipient becomes idle again without ACK is
protected with `wake-unacknowledged-recipient-idle` and escalated; GWO does not
spam a second prompt. A crash between the
native send and Wake Receipt can cause an at-least-once retry, but the source
Signal-ID makes repeated consumption idempotent. Finish notifications remain
best-effort accelerators only.

Outside one pending Material Delivery, an ordinary wait timeout causes only
room replay. Do not poll a running Agent. A full Agent/timeline inspection is
allowed after 15 minutes without a valid runtime signal, after a
finish/permission notification, or during explicit restart recovery. A stale
signal alone never authorizes cancel, replacement, archive, merge, or cleanup.

## Event states

Use material states plus the bounded coordination events `HEARTBEAT`, `ASK`,
`REPLY`, `DECISION_GATE`, `WORKER_DONE`, and `ESCALATION`. `DELIVERY_WAKE` and
`DELIVERY_ACK` are transport receipts, not lifecycle states.

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

`REVIEW_RESULT` is Review-role-only and includes `axis: spec|quality`, candidate
and base SHA, diff and acceptance SHA-256, review round, `full|delta` scope,
previous candidate SHA, pass/fail verdict, and findings. A Review identity
receipt has a static `review_axis` label matching the payload. Both axes for one
round must have an identical lock. Missing one axis remains incomplete;
duplicate axes, different locks, forged/cross-Campaign evidence, or conflicting
Signal-IDs block the Dispatch. Matching Reviewer claims do not authorize their
own lock: replay also requires a read-backed Campaign-issued
`campaign-verified-candidate` lock receipt for that campaign, Dispatch, and
round. Delta receipts require the prior round's receipt and exact prior
candidate lineage. REVIEW_RESULT never authorizes cleanup.

`CHECKPOINT` is Orchestrator-owned recovery metadata: replay cursor, active
Dispatch IDs, and pending Signal-IDs. It is not Agent liveness or business
truth. `ESCALATION` records retry exhaustion or ambiguous recovery.

Worker terminal evidence includes branch, commit/PR, verification class,
commands and outcomes, phase timings, changed paths, scope delta, blockers, and
next action. Formal review remains Orchestrator-owned.

A direct user message to a Worker first becomes one `ASK`. The Campaign may
reply only within the existing contract. Scope, architecture, Hotset,
compatibility, security, or integration changes use a durable GitHub decision
gate. Cross-Campaign/Integration/`dev` instructions received by a Campaign are
relayed to the Repository Coordinator.

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
private prompts in either room or GitHub messages.
