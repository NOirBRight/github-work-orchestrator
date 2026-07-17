---
name: github-work-orchestrator
description: Orchestrate GitHub execution campaigns through provider-neutral Paseo Agents, campaign rooms, isolated worktrees, review, integration, recovery, and cleanup. Use when an agent must reconcile a GitHub frontier, dispatch implementation, coordinate cross-provider work, or arbitrate integration.
---

# GitHub Work Orchestrator

Keep GitHub as durable work state and Paseo as the only Agent runtime. Keep one
Orchestrator per repository/activity. Never hardcode a Provider or model.

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
   work and [recovery](references/communication.md) only after a failure.

Role categories are fixed: Orchestrator `planning`, Intake `research`, Worker
`impl` (or `ui` for UI-only work), and Review/Monitor `audit`.

## Reconcile the frontier

Run the validator and ready frontier. Preview deterministic corrections with
`reconcile_issue_state.py`, apply only authorized unambiguous actions, then
revalidate. A ready item must have a complete v3 execution contract.

## Route and admit work

Use `execution_policy.py mode`. Small same-boundary work may remain inline;
other implementation uses a Paseo Agent. Keep at most four active delegated
Agents per campaign by default, with every role sharing the budget.

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

Create campaign-owned work with `relationship: { kind: "subagent" }`,
`notifyOnFinish: true`, the resolved high-autonomy `modeId`, and a Paseo
worktree from the pinned `dev` base. Add campaign, dispatch, role, repository,
Issue, and branch labels. Read back exact parent Agent ID, mode, worktree, and
labels before `START`; fail closed on mismatch. Use `detached` only for an
explicit handoff whose lifetime must outlive this campaign.

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

Merge the feature PR into `dev` only when all gates pass. `main` accepts only an
explicit verified release merge from `dev`.

## Recover and clean

After missed callback or restart, replay the room, find Agents by campaign,
dispatch and `paseo.parent-agent-id`, inspect lifecycle, then reconcile GitHub
and worktrees. Never create a replacement without terminal predecessor proof.

Use `execution_policy.py cleanup-plan`. Archive only an idle Agent with clean,
durable, unambiguously owned work. Write/read back the GitHub summary before
`CAMPAIGN_CLOSED`; delete completed rooms, but retain blocked/handoff rooms.
Preserve dirty, unpushed, active, ambiguous, or foreign state.
