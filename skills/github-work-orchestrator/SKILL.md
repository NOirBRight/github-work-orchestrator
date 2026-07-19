---
name: github-work-orchestrator
description: Orchestrate GitHub execution campaigns through provider-neutral Paseo Agents, campaign workspaces, rooms, parallel Workers, dual-axis review, integration, recovery, and cleanup. Use when an agent must route repository work, reconcile a GitHub frontier, dispatch implementation, coordinate cross-provider work, or arbitrate integration.
---

# GitHub Work Orchestrator

Keep GitHub as durable work state and Paseo as the only Agent runtime. GWO is a
portable Skill/Plugin: do not modify or depend on Paseo, a daemon, a host
repository application, or provider-native orchestration internals. Never
hardcode a Provider or model.
Keep all orchestration in this existing Skill; do not add another Skill,
sidecar, supervisor, or task database.

The runtime supervision tree is fixed:

```text
Coordinator · <repo>
└─ Campaign · <campaign-id> · <purpose>
   ├─ Worker · #<issue> · a<attempt>       (maximum three)
   ├─ Spec Reviewer                         (one reusable Agent)
   └─ Quality Reviewer                      (one reusable Agent)
```

Paseo parentage expresses supervision, notifications, and cleanup authority.
Workspace expresses the sidebar entry and file context. A Campaign is both the
bounded work effort and the user-visible name of its coordinating Paseo Agent;
its internal runtime role remains `orchestrator`.

## Load the control plane

Before routing or dispatch:

1. Read repository instructions and the installed `paseo` Skill completely.
2. Read [GitHub state rules](references/shared/github-state-rules.md),
   [lifecycle](references/shared/lifecycle.md),
   [verification](references/shared/verification-policy.md),
   [communication](references/shared/communication-protocol.md), and the
   [Issue contract](references/shared/issue-contract.md).
3. Read `~/.paseo/orchestration-preferences.json` and validate it with
   `coordinator_loop.py resolve-config`. Defaults are six active Agents per
   Campaign, three Worker slots, two Review slots, and thirteen active Agents
   globally. Invalid values block new Dispatches but do not abandon existing
   work.
4. Discover Provider/model/mode through Paseo. Resolve an explicit Campaign
   override, then the role preference. Resolve unattended mode by explicit
   override, configured `unattended_modes[provider]`, then exactly one
   advertised `isUnattended: true` mode. Never infer it from names or prose.
5. Read [entry and Workspace routing](references/entry-and-workspaces.md) before
   bootstrap/Relay, [Coordinator loop](references/coordinator-loop.md) before
   admission, [Worker contract](references/worker-contract.md) before Dispatch,
   [dual-axis review](references/review-pair.md) before formal review, and
   [cleanup safety](references/cleanup-safety-policy.md) before cleanup.

Role categories stay provider-neutral: Coordinator/Campaign `planning`, Intake
`research`, Worker `impl` or `ui`, and Review/Monitor `audit`.

## Route the current conversation first

Build a schema-v1 snapshot and run:

```text
python <skill>/scripts/entry_policy.py entry-plan --snapshot <json-file>
```

- With no Coordinator, a root Agent in a stable repository Workspace may be
  promoted in place even when its home is dirty or not on `dev`. Never promote
  an Agent in `work/issue-*`, `gwo/campaign/*`, or an existing Dispatch.
- If promotion is unsafe, create the Coordinator only in one uniquely
  read-backed stable `dev` Workspace. Ambiguity fails closed.
- With one existing Coordinator, rename this ordinary Task to
  `Relay · <repo> → Coordinator`. Post one sanitized `OPERATOR_REQUEST` to the
  Repository Room, read Coordinator status once, and run `wake-plan`. Send only
  the Signal-ID when idle; do not disturb running/initializing state. Leave a
  durable receipt, then idle and tell the operator the Relay may be closed.
- With duplicate Coordinators, stop admission, integration, and Relay delivery;
  preserve every Agent for human adjudication.

A Relay performs at most five external actions and never reads the GitHub
frontier, worktrees, or full Campaign rooms. The Coordinator replays the
Repository Room at startup, before waiting, and before ending every turn.

## Separate home, integration, and Campaign Workspaces

Use these exact meanings and titles:

- **Coordinator Home Workspace** — long-lived conversation location; title
  `Repo · <repo> · dev`. It may be dirty or not on `dev`.
- **Integration Control Worktree** — the explicitly addressed `dev` worktree
  used only for integration commands.
- **Campaign Control Workspace** — one new local worktree/sidebar entry per new
  Campaign; title `Campaign · <campaign-id> · <purpose>`.
- Worker Workspace — isolated `work/issue-*`; title
  `WT · #<issue> · <slug>`.

All integration Git commands explicitly target the Integration Control
Worktree. Require it clean only immediately before integration. If it is dirty
or unavailable, preserve user WIP, keep Campaigns/Workers running, and hold
verified candidates in `WAITING_INTEGRATION`; never stash, reset, or force-clean.

Create each new Campaign with `campaign_workspace.py create-plan`: relationship
`subagent`, Workspace `create/worktree`, branch
`gwo/campaign/<campaign-id>`, exact read-backed `dev` base, and a Campaign-local
Provider Binding. The control branch stays local, has no PR, and carries no
feature commit. Run `validate-readback` for parent, Workspace, Provider/mode,
labels, branch, and head before admission. Tracked changes, unique commits, or
a published control branch stop new Dispatches and preserve the scene.

Do not migrate legacy active Campaigns such as c-016. A v4.2 Campaign without a
control worktree remains an Agent-only legacy lifecycle until it closes.

## Reconcile and dispatch parallel waves

Batch-read the GitHub frontier, native dependencies, assignees, contracts, PRs,
and checks once per repository reconciliation. A ready Issue has a complete v3
contract and canonical Expected Hotset. Missing reliable Hotset means a
repository-wide exclusive Dispatch.

The Coordinator admits all planned, capacity-safe, Hotset-disjoint Campaigns in
one wave without waiting for a prior Campaign. Each Campaign batch-reads only
its scope and runs:

```text
python <skill>/scripts/campaign_scheduler.py plan-wave --snapshot <json-file>
```

Create every selected Worker after re-reading and claiming its Issue; do not
wait for another Worker. Read back exact parent, Provider/model/mode, labels,
branch, Workspace, and worktree before `START`. A single claim/create failure
does not roll back successful siblings.
Use the scheduler's exact `Worker · #<issue> · a<attempt>` Agent name and
`WT · #<issue> · <slug>` Workspace title, then include those values in runtime
readback.

Worker and Reviewer capacity are independent. A Campaign has one Campaign +
three Worker slots + two Review slots. `standard`/`strict` never reduce Worker
slots from three. Foreign active Paseo Agents consume global capacity; empty UI
drafts, archived Agents, and terminal idle Relays do not. Integration Lease
availability never serializes implementation.
When standard/strict work still lacks Reviewers, preserve the missing Review
slots in shared Campaign/global totals; foreign load may shrink that Worker wave
but never converts a Review slot into a Worker slot.

Only use Paseo Agents created through the installed `paseo` Skill inside the
GWO tree. Provider-native Agent/Task/Swarm features must not appear inside a
GWO-owned Agent; when explicitly requested, route that work to a separate,
non-GWO top-level Task with no Campaign ownership.

## Communicate and wait by event

Create `gwo-<campaign-id>` before child Dispatch. Room messages are primary;
finish/permission notifications only wake the loop. Replay with exact Paseo
identity receipts after every wait/wake. A room claim never creates authority.
Compile receipts from normalized Paseo readbacks with `paseo_room.py
identity-plan` and an explicit authority scope; never hand-author authority
fields or infer control scope from an ID. Workers always replay/wait with
`--consumer-role worker` and their exact `--dispatch-id`, so unrelated Campaign
lifecycle, sibling history, and Campaign-owned Review results cannot block
activation or review-fix work. Campaign reconciliation remains unscoped.

Every addressed material event uses the packaged delivery transaction. Publish
with `paseo_room.py post-material --authority-scope <scope>
--identity-receipts <compiled-json-file>`, combine its
`delivery` output with fresh exact sender/recipient readbacks, then run
`material_delivery.py delivery-plan`. Execute only the returned action: send
the exact signal-only prompt to an idle recipient, or wait without prompting a
running/initializing recipient. After an accepted send, post the deterministic
`DELIVERY_WAKE` from `wake-receipt-plan`. The recipient posts the deterministic
`DELIVERY_ACK` from `ack-plan` immediately after identity-verified replay and
before processing. A terminal sender does not claim successful handoff before
ACK. Invalid delivery metadata never poisons its business event; Wake/ACK never
authorizes completion, merge, cleanup, or replacement.

HEARTBEAT is Worker-to-Campaign liveness at safe boundaries with a five-minute
target. It is not Coordinator polling, completion, merge, or cleanup evidence.
Wait through `chat wait` for at most 60 seconds. Ordinary timeout only replays
the room. At 15 minutes without START/PROGRESS/HEARTBEAT, inspect once; silence
never authorizes cancel, archive, or replacement. Prompt an idle non-terminal
Agent once, never a busy Agent.
The only shorter status readback is for one already-pending Material Delivery:
after its bounded ACK wait, re-read that exact recipient and re-plan. If a
recorded wake returns idle without ACK, protect and escalate instead of sending
again.

A Worker receiving direct user instructions first posts `ASK`. A clarification
inside its contract may receive correlated `REPLY`; scope, architecture, Hotset,
compatibility, security, or integration changes enter a durable GitHub decision
gate. A Campaign may answer only within its Campaign scope. Cross-Campaign,
Hotset, Integration Lease, or `dev` requests relay to the Coordinator.

## Verify with two independent axes

`WORKER_DONE` is candidate evidence only. Verify exact Agent, Git head, dirty
state, push/PR, checks, commands, changed paths, Hotset, and acceptance before
review.

- `fast`: the Campaign performs both axes directly.
- `standard`/`strict`: run `review_policy.py plan-review`. Lazily create exactly
  one `Spec Reviewer` and one `Quality Reviewer` as direct Campaign subagents
  and reuse them sequentially. Re-read global/Campaign capacity before each
  creation; a stale reservation never permits exceeding the cap. They do not
  communicate.
- Both receive the same candidate SHA, base SHA, diff digest, acceptance digest,
  round, and scope. `Spec Reviewer` checks Issue/decisions/scope/Hotset/
  acceptance. `Quality Reviewer` checks standards/architecture/security/tests/
  maintainability.
- The Campaign only aggregates. Two valid `REVIEW_RESULT` events are required;
  missing, duplicate, forged, cross-Campaign, or lock-mismatched evidence cannot
  form a verdict. Persist/read back the Campaign-issued candidate lock and pass
  it as `--review-locks`; Reviewer claims cannot authorize their own lock.
  Either failure returns work to the same Worker. The next round makes both
  Reviewers inspect only the delta and carries exact prior-lock lineage.

One pair serves one candidate at a time. Queue by verified-ready time, then
Issue number. Partial pair creation keeps the successful Reviewer and creates
only the missing axis; no final verdict exists until both are read back.

## Integrate and close safely

The Campaign returns a verified candidate. The Coordinator grants one
repository-scoped Integration Lease in durable ready order, then Campaign ID.
Run `execution_policy.py concurrency` with explicit Integration Control
availability/cleanliness. Refresh an advanced `dev` base and rerun affected
evidence. Only then merge into `dev`; `main` receives only an explicit verified
release merge.

Use `cleanup-plan` v4.3 with explicit `target_kind` and `resource_kind`:

- Worker with Issue worktree: `worker / issue-worktree`.
- Spec/Quality Reviewer: `worker / none`.
- New Campaign: `campaign / campaign-control`.
- Legacy v4.2 Campaign: `campaign / none`.
- Probe/forward-test: `ephemeral / none`, only with lifecycle label
  `gwo.lifecycle=ephemeral` and captured result readback.

Cleanup remains staged. Archive only a direct idle child with exact matching
repository/campaign/dispatch and terminal receipt. Read back archived + unbound
before worktree actions. A Campaign closes its Workers and both Reviewers
first, with an explicit read-backed direct-child enumeration; after
`CAMPAIGN_CLOSED`, the Coordinator archives the Campaign, reads back unbound,
archives its clean/unbound/no-unique-commit exact Campaign control worktree,
reads back absence, and only then deletes its exact local control branch.
Protected plans have no actions.

Never target the Coordinator, a root/sibling/foreign/detached Agent,
Coordinator Home, Integration Control Worktree, a dirty/ambiguous resource, or
use force. The Coordinator survives every Campaign; only a human may retire it
after durable handoff. GWO does not clean provider-native zombie timelines or
the Paseo UI's empty `New Agent` draft tab; retain the packaged upstream-ready
evidence for those host issues.
