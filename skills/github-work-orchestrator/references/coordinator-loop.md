# Event-driven Coordinator loop

This is a GWO workflow over public Paseo/GitHub operations, not a daemon,
schedule, sidecar, or second task database.

## Repository loop

Run `BOOTSTRAP -> REPLAY_REPOSITORY_ROOM -> RECONCILE_REPOSITORY ->
ADMIT_CAMPAIGNS -> WAIT_CAMPAIGNS -> INTEGRATE -> CLOSE`.

1. **BOOTSTRAP** — run `entry_policy.py`. Verify one Coordinator and separate
   Coordinator Home from the explicitly addressed Integration Control Worktree.
   Duplicate Coordinators stop admission/integration without cleanup.
2. **REPLAY_REPOSITORY_ROOM** — replay at startup, before every wait, and before
   ending the turn. Accept/reject each new Operator request durably.
3. **RECONCILE_REPOSITORY** — one batch GitHub frontier/dependency/PR/check read,
   one active-Agent/capacity read, pending permissions, active Campaign
   checkpoints, current Integration Lease, and explicit `dev` control readback.
4. **ADMIT_CAMPAIGNS** — admit all planned Campaigns whose Hotsets are disjoint
   and capacity permits. Run `campaign_workspace.py create-plan`; submit creates
   consecutively without waiting for an earlier Campaign. Each keeps its own
   Provider Binding.
5. **WAIT_CAMPAIGNS** — wait for room, finish, permission, or operator events.
   Campaign material events remain pending until this Coordinator posts
   `DELIVERY_ACK`. Do not poll or prompt busy Campaigns.
6. **INTEGRATE** — order verified-ready candidates by durable timestamp then
   Campaign ID and grant one Integration Lease. A dirty/missing Integration
   Control Worktree leaves candidates `WAITING_INTEGRATION`; preserve user WIP.
   Refresh advanced `dev` and rerun delta-affected evidence before merge.
7. **CLOSE** — accept `CAMPAIGN_CLOSED` only after GitHub, room, Agent,
   Worker/Reviewer, branch, and Workspace readback. The Coordinator remains.

## Campaign loop

Run `RECONCILE_CAMPAIGN -> PLAN_WAVE -> DISPATCH_WAVE -> WAIT_WORKERS ->
VERIFY_RESULTS -> REVIEW -> RETURN_CANDIDATE`.

1. Batch-read only Campaign scope: room cursor, Issue claims, Agent parentage,
   labels, branches/worktrees, Provider modes, Review Pair, and permissions.
2. Validate the Campaign Control Workspace. Tracked changes, unique commits, or
   a published control branch preserve the scene and stop new Dispatch.
3. Run `campaign_scheduler.py plan-wave` with exact typed capacity: Campaign,
   active Workers, Reviewers, global Agents, and limits.
4. Re-read/claim every selected Issue, then create the complete Worker wave
   without waiting for another Worker. Apply the scheduler's exact Agent and
   Workspace names and read back each create. One failed item does not roll
   back successful siblings; reconcile the freed slot.
5. Post CHECKPOINT only on state change. `chat wait` at most 60 seconds, then
   replay. Workers compile readback receipts and use Dispatch-scoped replay;
   they declare `worker-dispatch` authority plus `--consumer-role worker`, so
   formal Review results stay in the Campaign consumer view. Campaign
   reconciliation is unscoped. Normal timeout does not inspect running Agents.
   For one pending Material Delivery only, replay after the ACK wait, re-read
   that exact sender/recipient pair, and run `material_delivery.py
   delivery-plan`; this does not authorize general Agent polling.
6. Verify every `WORKER_DONE` against Agent/Git/worktree/GitHub/contract facts.
7. Run `review_policy.py plan-review` with fresh read-backed Campaign/global
   capacity. Fast stays inline; standard/strict uses the reusable independent
   Spec/Quality pair. Persist/read back the immutable candidate lock before
   dispatch, persist both dynamic Review assignments, compile
   `review-dispatch` identity receipts, and require those receipts plus both
   room results.
8. Return the fully verified candidate to the Coordinator. Waiting for the
   Integration Lease does not block other disjoint implementation.

## Material Delivery loop

Use the same loop at all four supervision boundaries: Coordinator→Campaign,
Campaign→Worker/Reviewer, Worker/Reviewer→Campaign, and Campaign→Coordinator.

1. Publish the addressed business event with `paseo_room.py post-material` and
   its identity-plan authority scope and compiled identity receipts.
2. Run `material_delivery.py delivery-plan` with the returned publish receipt
   and fresh direct-relative Agent readbacks.
3. Send only the returned `GWO_WAKE` prompt when the recipient is idle. Record
   an accepted send using `wake-receipt-plan` and plain Room `post`.
4. A running/initializing recipient is not prompted. The sender performs a
   bounded Room wait, replays, re-reads that exact recipient status, and
   re-plans.
5. The recipient posts `ack-plan` output immediately after identity-verified
   replay. It then records the source Signal-ID in the next CHECKPOINT and
   reconciles the business event.
6. `wake-sent` followed by an idle recipient without ACK is protected and
   escalated; never create a replacement or resend automatically.

`DELIVERY_WAKE` and `DELIVERY_ACK` are transport receipts. They cannot satisfy
verification, integration, terminal, or cleanup gates, and a malformed receipt
cannot block the source Dispatch.

## Wave rules

Candidate order is explicit rank then Issue number. It must be ready,
contract-valid, unassigned, dependency-free, within Campaign Hotset, below
retry limit, and disjoint from active/external/selected claims. Missing Hotset
is repository-wide exclusive. `case_sensitive_paths` comes from actual Git
readback; missing evidence fails closed.

The default Campaign budget is six active Agents: one Campaign, three dedicated
Workers, and two dedicated Reviewers. Standard/strict work never reserves a
Worker slot. The global default is thirteen, exactly two full Campaigns plus
one Coordinator. Foreign active Paseo Agents reduce available global slots;
empty UI drafts, archived Agents, and terminal idle Relays do not count.
Missing Reviewers keep their dedicated share of the Campaign/global totals, so
foreign load may shrink a standard/strict Worker wave without consuming Review
capacity.

Every candidate carries exact Dispatch readback. Dispatch ID is
`dispatch-issue-<issue>-a<attempt>` and is stable for the snapshot. A successor
requires exact read-backed `STOPPED` predecessor, terminal Agent state,
unambiguous ownership, and durable WIP. `BLOCKED`, `ESCALATION`, idle state, or
silence cannot authorize a successor. Attempt four is never automatic.

## HEARTBEAT and stale recovery

HEARTBEAT is Worker liveness, never Coordinator polling. Post at safe phase
boundaries with a five-minute target; PROGRESS replaces it when evidence
changed. Long commands may miss the target. Neither signal changes Issue,
Dispatch, completion, merge, or cleanup state.

At 15 minutes without START/PROGRESS/HEARTBEAT, inspect the affected Agent once.
Wait another 15 minutes before another stale inspection unless new evidence
arrives:

| Readback | Coordinator action |
|---|---|
| running/initializing with activity | wait; no prompt |
| running without activity | CHECKPOINT suspected-stalled |
| idle without terminal event | one recovery prompt referencing last Signal-ID |
| permission pending | apply permission contract or block |
| error/closed | preserve WIP and evaluate successor proof |
| identity mismatch | BLOCKED; no successor |
| missing/ambiguous | ESCALATION to human |

`coordinator_loop.py` never authorizes cancellation, replacement, archive,
merge, or cleanup from silence alone. Do not create recurring Paseo heartbeats
for normal Campaign correctness.
