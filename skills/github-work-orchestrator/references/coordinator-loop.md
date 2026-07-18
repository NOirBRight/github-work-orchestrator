# Event-driven Coordinator loop

Use this loop while any Campaign is non-terminal. It is a Skill workflow over
existing Paseo operations, not a daemon, schedule, or second task database.

## Repository loop

Run `BOOTSTRAP -> RECONCILE_REPOSITORY -> ADMIT_CAMPAIGNS -> WAIT_CAMPAIGNS ->
INTEGRATE -> CLOSE -> RECONCILE_REPOSITORY`.

- Bootstrap verifies the Repository Coordinator labels, `dev` Control Worktree,
  and uniqueness. A duplicate stops admission and integration.
- Reconciliation rebuilds state from GitHub, Paseo, Git/worktrees, pending
  permissions, and rooms. Memory and CHECKPOINT are accelerators only.
- Admission starts every already-planned Campaign whose Hotset and global
  capacity allow. Submit all Agent creates without waiting for another Campaign
  to finish; each retains its own Provider Binding.
- Integration orders verified candidates by their durable ready receipt, then
  Campaign ID. Grant one repository Integration Lease. Refresh an advanced
  `dev` base and rerun delta-affected evidence before merge.
- Close only after GitHub, PR, room, Agent, and worktree readback. The Repository
  Coordinator survives every Campaign.

## Campaign loop

Run `RECONCILE_CAMPAIGN -> PLAN_WAVE -> DISPATCH_WAVE -> WAIT_WORKERS ->
VERIFY_RESULTS -> REVIEW -> RETURN_CANDIDATE`.

1. Reconcile room events, Issue claims, Agent parentage/labels, branches,
   worktrees, provider modes, and pending permissions.
2. Build one schema-v1 scheduler snapshot and run:

   ```text
   python <skill>/scripts/campaign_scheduler.py plan-wave --snapshot <json>
   ```

3. For every returned action, re-read and claim the Issue. Create every
   successfully claimed Worker without waiting for another Worker to finish.
4. Read back every Agent. A failed claim/create affects only that item; preserve
   successful siblings and reconcile the freed slot.
5. Before waiting, post CHECKPOINT only when state changed. Wait at most 60
   seconds, then replay. A normal timeout does not inspect running Agents.
6. Treat WORKER_DONE as a candidate. Verify the exact Agent, branch/head,
   worktree, push/PR, changed paths, checks, acceptance, and hotset before review
   or COMPLETED.

## Wave rules

The snapshot carries exact control-plane readback, capacity, Campaign Hotset,
candidates, active Dispatches, external Campaign Hotsets, Review Agent state,
the worktree's `case_sensitive_paths` readback, and the retry limit. Every
candidate carries `dispatch_readback` with the exact
proposed Dispatch ID, active/archived match counts, and `read_back: true`. Any
existing match defers to reconciliation. An attempt after the first also
carries `previous_dispatch`: exact prior ID/attempt, accepted terminal signal,
Agent terminal state, and true readback/reconciliation/ownership/WIP evidence.
Any global identity/count contradiction returns zero actions and attaches each
global blocker to every deferred Issue as `global:<blocker>`.
`case_sensitive_paths` is required boolean evidence derived from the actual
worktree's Git `core.ignorecase` readback. Missing evidence fails closed; never
infer it from the Python host OS.
An external Campaign Hotset overlapping the admitted Campaign Hotset is also a
global scope contradiction: stop the whole wave and return
`campaign-hotset-conflict` instead of trying to salvage individual candidates.

```json
{
  "attempt": 2,
  "dispatch_readback": {
    "dispatch_id": "dispatch-issue-143-a2",
    "active_matches": 0,
    "archived_matches": 0,
    "read_back": true
  },
  "previous_dispatch": {
    "dispatch_id": "dispatch-issue-143-a1",
    "attempt": 1,
    "agent_id": "agent-issue-143-a1",
    "terminal_event": "STOPPED",
    "terminal_signal_id": "stopped-143-a1",
    "terminal_sender_agent_id": "agent-issue-143-a1",
    "terminal_read_back": true,
    "agent_status": "archived",
    "agent_reconciled": true,
    "ownership_unambiguous": true,
    "wip_durable": true
  }
}
```

Order candidates by explicit rank, then Issue number. A selected candidate is
ready, unclaimed, contract-valid, dependency-free, below the retry limit,
without another active Dispatch, within the Campaign Hotset, and disjoint from
active/external/already-selected Hotsets. Missing Hotset means repository-wide
exclusive execution.

The default limit is four Campaign Agents including its Orchestrator. Fast-only
waves may use three implementation slots. Reserve one slot when standard/strict
work needs a Review Agent and none exists, leaving two implementation slots
from an otherwise-empty child pool. A reusable Review Agent serves later
candidates. An existing non-reusable reviewer blocks new Dispatch until
reconciliation; it never causes a second reviewer reservation. Global capacity
includes the Repository Coordinator and every unarchived Campaign Agent.

Dispatch ID is `dispatch-issue-<number>-a<attempt>`. Repeating the same snapshot
returns the same ID. Search archived and active Agents before create. Increment
attempt only after a read-backed `STOPPED` predecessor whose Agent is
`error`, `closed`, or `archived`, ownership is unambiguous, and WIP is durable.
`BLOCKED`, `ESCALATION`, or an idle predecessor cannot authorize a successor.
The configured limit may be lower than three but never higher; attempt four is
never automatic.

## Runtime signals and stale recovery

HEARTBEAT is Worker liveness, never Orchestrator polling. The Worker posts at
safe phase boundaries and targets five minutes during long work. A long command
may miss the target. PROGRESS replaces HEARTBEAT when evidence materially
changes; neither authorizes completion or cleanup.

At 15 minutes without START, PROGRESS, or HEARTBEAT, inspect the affected Agent
once. Wait another 15 minutes before a second stale inspection unless new
evidence arrives:

| Readback | Action |
|---|---|
| running/initializing with activity | wait; no prompt |
| running without recent activity | CHECKPOINT suspected-stalled |
| idle without terminal event | send one recovery prompt referencing the last Signal-ID |
| pending permission | inspect and apply the permission contract |
| error/closed | preserve WIP and evaluate the next attempt |
| identity mismatch | BLOCKED; no successor |
| missing/ambiguous Agent | ESCALATION; human recovery |

Use `coordinator_loop.py` to resolve configuration and stale/heartbeat actions.
Its policy never authorizes cancel, replacement, archive, merge, or cleanup from
silence alone. Do not create recurring Paseo heartbeats by default.
