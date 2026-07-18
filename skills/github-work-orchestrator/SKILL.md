---
name: github-work-orchestrator
description: Orchestrate GitHub execution campaigns through provider-neutral Paseo Agents, campaign rooms, isolated worktrees, review, integration, recovery, and cleanup. Use when an agent must reconcile a GitHub frontier, dispatch implementation, coordinate cross-provider work, or arbitrate integration.
---

# GitHub Work Orchestrator

Keep GitHub as durable work state and Paseo as the only Agent runtime. Keep one
Repository Coordinator per repository and one Campaign Orchestrator per
Campaign. Never hardcode a Provider or model.

## Load the control plane

Before dispatch:

1. Read repository instructions and the installed `paseo` Skill completely.
2. Read [GitHub state rules](references/shared/github-state-rules.md),
   [lifecycle](references/shared/lifecycle.md),
   [verification](references/shared/verification-policy.md),
   [communication](references/shared/communication-protocol.md), and the
   [Issue contract](references/shared/issue-contract.md).
3. Read `~/.paseo/orchestration-preferences.json`, validate its `orchestration`
   limits through `coordinator_loop.py`, then discover available
   providers/models through Paseo. Resolve by explicit override, then role
   preference; fail closed when the configured selector is unavailable. Resolve
   runtime mode by explicit Campaign override, configured
   `unattended_modes[provider]`, then one unambiguous `isUnattended: true` mode.
   Never infer unattended execution from Provider name or generic mode prose.
   The stale threshold and stale recheck cooldown are each at least 900 seconds;
   the retry limit is at most three. Invalid values block new Dispatches without
   abandoning existing Workers.
4. Read [Coordinator loop](references/coordinator-loop.md) before Campaign
   admission, [Paseo Worker contract](references/worker-contract.md) before delegated
   work, [cleanup safety policy](references/cleanup-safety-policy.md) before
   cleanup, and [recovery](references/communication.md) only after a failure.

Role categories are fixed: Orchestrator `planning`, Intake `research`, Worker
`impl` (or `ui` for UI-only work), and Review/Monitor `audit`.

## Establish the two-tier control plane

The Repository Coordinator is the repository-resident root Agent in a dedicated
`dev` control worktree. Read back its repository/coordinator labels and confirm
that no second Coordinator exists. Unlabeled root Agents are foreign and must
not be adopted, edited, or archived. If two Coordinators exist, stop admission
and integration and ask a human operator to select one canonical Agent through
the existing Paseo UI or CLI after durable handoff.

The Campaign Orchestrator is a direct `subagent` of the Repository Coordinator
and owns exactly one `campaign_id`. Its Provider Binding is resolved per
Campaign: explicit Campaign override first, then the `planning` preference.
Different Campaigns may therefore use different Providers and models without
changing repository policy.

## Reconcile the frontier

Run the validator and GraphQL-backed ready frontier. Preview deterministic corrections with
`reconcile_issue_state.py`, apply only authorized unambiguous actions, then
revalidate. A ready item must have a complete v3 execution contract. New or
rewritten Issues use strict backticked Expected Hotset bullets. A legacy Issue
without that evidence takes a repository-wide exclusive Dispatch.

## Route and admit work

Use `execution_policy.py mode`. Small same-boundary work may remain inline;
other implementation uses a Paseo Agent. Inline work still uses an isolated
`work/issue-*` worktree; the Repository Coordinator must not author feature
commits directly on `dev`.

Use `execution_policy.py capacity` for the target Campaign and actual host-wide
capacity. Keep one Campaign Orchestrator per Campaign and at most four Campaign
Agents including that Orchestrator by default. Different Campaigns may execute
concurrently when their Hotsets do not overlap.

The Repository Coordinator admits every eligible already-planned Campaign in
one wave. The Campaign Orchestrator runs `campaign_scheduler.py plan-wave` and
creates the whole eligible Worker wave without waiting for the first Worker to
finish. Fast-only work may use three child implementation slots. Reserve one
slot for a not-yet-created Review Agent when standard/strict work is ready or
active. Integration Lease availability never serializes implementation.

Use `execution_policy.py concurrency` against all active Hotsets and the current
repository-scoped Integration Lease. A Hotset conflict waits without creating
another editor. Record Hotsets as canonical repository-relative paths; absolute
paths, empty components, `.` and `..` fail closed. Only the lease holder may
integrate; if `dev` advanced, refresh its pinned `dev` base and rerun
verification affected by the delta.

Pin dispatch to exact `dev` SHA. Claim and read back the Issue. Use
`work/issue-<number>-<slug>`, an isolated Paseo worktree, and PR target `dev`.

## Open the campaign room

Create one room before any child Agent:

```text
python <skill>/scripts/paseo_room.py create \
  --campaign-id <id> --purpose <bounded-purpose>
```

Post `CAMPAIGN_OPENED`. Every child contract carries the room, campaign and
dispatch IDs, exact worktree/branch, hotset, permissions, verification, and
`done_when`.

## Create and coordinate Paseo Agents

The Repository Coordinator creates the Campaign Orchestrator with relationship
`{ kind: "subagent" }`, role `orchestrator`, category `planning`, the
Campaign-local Provider Binding, and exact Campaign labels. The Campaign
Orchestrator creates campaign-owned Workers as its own direct `subagent`
children with `notifyOnFinish: true`, the resolved high-autonomy `modeId`, and a
Paseo worktree from the pinned `dev` base.

Add campaign, dispatch, role, repository, Issue, and branch labels. Read back
exact parent Agent ID, mode, worktree, Provider Binding, and labels before
`START`; fail closed on mismatch. Use `detached` only for an explicit handoff
whose lifetime must outlive its parent.

Paseo returns an unexpected permission request to the parent. Inspect the
request and child timeline. Allow only non-destructive work already authorized
by the v3 permission profile and hotset; otherwise deny, post `BLOCKED`, and
request human direction. During recovery, list pending permission requests
because the parent notification can be lost across restart.

Room messages are the primary communication. Replay and deduplicate by
Signal-ID after every wait/wake. Pass `paseo_room.py replay|wait` an
`--identity-receipts` JSON array built from exact Paseo Agent, parentage, role,
static labels, and role-aware Campaign/Dispatch authority readback; room claims
cannot create that receipt. Keep Coordinator labels stable across child
Dispatches and prove direct-child/admitted-Campaign authority separately.
Do not mention or prompt a busy Agent. Record work in the room; after the Agent
is verified idle, a follow-up may reference the exact room message UUID through
`send_agent_prompt`.

Finish notifications accelerate wake-up only. Verify every material result
against Paseo Agent state, Git/worktree, GitHub Issue/PR/checks, and the v3
contract before review, merge, or cleanup.

HEARTBEAT is Worker-to-Orchestrator liveness at safe phase boundaries with a
five-minute target, not Orchestrator polling or terminal evidence. Wait at most
60 seconds per `chat wait`; ordinary timeout only replays the room. After 15
minutes without a runtime signal, use `coordinator_loop.py` for one stale
inspection. Silence never authorizes replacement, cancellation, or cleanup.

## Review and integrate

Treat WORKER_DONE as candidate evidence, not completion. Start CI, applicable
manual evidence, and one Orchestrator-owned review in
parallel after a locally green candidate. `fast` is reviewed directly;
`standard`/`strict` use one reusable Campaign Review Agent with role category
`audit`. Send fixes to the same idle owner and review only the delta.

The Campaign Orchestrator returns the verified candidate. The Repository
Coordinator acquires the Integration Lease, reads back the current `dev` SHA,
and merges the feature PR only when all gates still pass. `main` accepts only an
explicit verified release merge from `dev`.

## Recover and clean

After missed callback or restart, replay the room, find Agents by campaign,
dispatch and `paseo.parent-agent-id`, inspect lifecycle, then reconcile GitHub
and worktrees. Never create a replacement without terminal predecessor proof.

Use `execution_policy.py cleanup-plan` with caller, parentage, lifecycle,
worktree binding, exact repository/campaign/dispatch identity, valid terminal
room receipt, and Repository Coordinator control-worktree evidence read back
through the existing Paseo Skill. HEARTBEAT, CHECKPOINT, and WORKER_DONE are not
cleanup receipts. Archive only an idle Agent with clean, durable, unambiguously
owned work. A merged event also requires `branch_merged: true`. The Campaign Orchestrator may target only its
direct children; after durable `CAMPAIGN_CLOSED`, the Repository Coordinator may
target that direct Campaign child. Neither may archive itself, a root/sibling
Agent, or either the repository control worktree or its own control worktree.

Campaign child retirement uses `event=campaign-closed` with explicit Agent-only
evidence and no feature worktree/branch target. Its archived readback completes
the cleanup; never fabricate a `work/issue-*` resource for a planning Agent.

Delegated cleanup is two-phase. The first eligible plan contains only the Agent
archive. Execute it through the existing Paseo operations, read back that the
Agent is archived and the worktree has no Agent binding, then call
`cleanup-plan` again before considering the worktree or merged branch actions.
Never treat a planned Agent archive as proof that its worktree binding is gone.

The package v4.2 `cleanup-plan` keeps output `schema_version: 2` and returns
`automatic_execution: true` only for an eligible, nonempty action list. Execute
exactly those actions in order through the existing Paseo Skill and read back
each mutation. A protected plan always returns false with no actions. Delete
completed rooms only after readback; retain blocked/handoff rooms and preserve
dirty, unpushed, active, ambiguous, or foreign state. `CAMPAIGN_CLOSED` never
archives the Repository Coordinator.
