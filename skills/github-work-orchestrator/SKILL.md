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
3. Read `~/.paseo/orchestration-preferences.json`, then discover available
   providers/models through Paseo. Resolve by explicit override, then role
   preference; fail closed when the configured selector is unavailable. Inspect
   advertised modes and choose the highest unattended execution mode. Never
   infer a mode from the Provider name.
4. Read [Paseo Worker contract](references/worker-contract.md) before delegated
   work, [runtime archive contract](references/runtime-archive-contract.md)
   before cleanup, and [recovery](references/communication.md) only after a
   failure.

Role categories are fixed: Orchestrator `planning`, Intake `research`, Worker
`impl` (or `ui` for UI-only work), and Review/Monitor `audit`.

## Establish the two-tier control plane

The Repository Coordinator is the repository-resident root Agent in a dedicated
`dev` control worktree. Read back its repository/coordinator labels and confirm
that no second Coordinator exists. Unlabeled root Agents are foreign and must
not be adopted, edited, or archived. If two Coordinators exist, stop admission
and integration and ask an external supervisor to select one canonical Agent
after durable handoff.

The Campaign Orchestrator is a direct `subagent` of the Repository Coordinator
and owns exactly one `campaign_id`. Its Provider Binding is resolved per
Campaign: explicit Campaign override first, then the `planning` preference.
Different Campaigns may therefore use different Providers and models without
changing repository policy.

## Reconcile the frontier

Run the validator and ready frontier. Preview deterministic corrections with
`reconcile_issue_state.py`, apply only authorized unambiguous actions, then
revalidate. A ready item must have a complete v3 execution contract.

## Route and admit work

Use `execution_policy.py mode`. Small same-boundary work may remain inline;
other implementation uses a Paseo Agent. Inline work still uses an isolated
`work/issue-*` worktree; the Repository Coordinator must not author feature
commits directly on `dev`.

Use `execution_policy.py capacity` for the target Campaign and actual host-wide
capacity. Keep one Campaign Orchestrator per Campaign and at most four Campaign
Agents including that Orchestrator by default. Different Campaigns may execute
concurrently when their Hotsets do not overlap.

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
Signal-ID after every wait/wake. Do not mention or prompt a busy Agent. Record
work in the room; after the Agent is verified idle, a follow-up may reference
the exact room message UUID through `send_agent_prompt`.

Finish notifications accelerate wake-up only. Verify every material result
against Paseo Agent state, Git/worktree, GitHub Issue/PR/checks, and the v3
contract before review, merge, or cleanup.

## Review and integrate

Start CI, applicable manual evidence, and one Orchestrator-owned review in
parallel after a locally green candidate. `fast` is reviewed directly;
`standard`/`strict` use one Review Agent with role category `audit`. Send fixes
to the same idle owner and review only the delta.

The Campaign Orchestrator returns the verified candidate. The Repository
Coordinator acquires the Integration Lease, reads back the current `dev` SHA,
and merges the feature PR only when all gates still pass. `main` accepts only an
explicit verified release merge from `dev`.

## Recover and clean

After missed callback or restart, replay the room, find Agents by campaign,
dispatch and `paseo.parent-agent-id`, inspect lifecycle, then reconcile GitHub
and worktrees. Never create a replacement without terminal predecessor proof.

Use `execution_policy.py cleanup-plan` with the runtime-observed caller identity
and a separately trusted absolute path for the Repository Coordinator's control
worktree. Archive only an idle Agent with clean, durable, unambiguously owned
work. The Campaign Orchestrator may target only its direct children; after
durable `CAMPAIGN_CLOSED`, the Repository Coordinator may target that direct
Campaign child. Neither may archive itself, a root/sibling Agent, or either the
repository control worktree or its own control worktree.

Delegated cleanup is two-phase. The first eligible plan contains only the Agent
archive. Execute it externally, read back that the Agent is archived and the
worktree has no Agent binding, then call `cleanup-plan` again before considering
the worktree or merged branch actions. Never treat a planned Agent archive as
proof that its worktree binding is gone.

`cleanup-plan` v4 returns `automatic_execution: false` until the Paseo daemon
implements the runtime archive contract. Surface candidate actions to an
external supervisor instead of executing them. Delete completed rooms only
after readback; retain blocked/handoff rooms and preserve dirty, unpushed,
active, ambiguous, or foreign state. `CAMPAIGN_CLOSED` never archives the
Repository Coordinator.
