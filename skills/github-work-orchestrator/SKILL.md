---
name: github-work-orchestrator
description: Orchestrate GitHub work through the gwo kernel. Use for repository coordination, frontier reconciliation, task dispatch, review gating, serial integration, and safe cleanup.
---

# GitHub Work Orchestrator

Use the packaged `gwo.py` CLI for every coordination invariant. This Skill is the
gwo kernel user interface. GitHub remains the only durable business truth; the
SQLite store under `GWO_HOME` is a rebuildable cache. Never hardcode a Provider
or model. Keep all orchestration in this Skill; do not add another Skill,
sidecar, supervisor, or second task database. Do not modify or depend on Paseo
runtime internals. This Skill fails closed.

The runtime shape is a dynamic task DAG planned by the Coordinator, not a fixed
supervision tree. A **Task Group** is only a label on tasks; it is not an
Agent, a Workspace, or a room.

## Load the control plane

Before routing or dispatch:

1. Read repository instructions and the installed `paseo` Skill completely.
2. Read [GitHub state rules](references/shared/github-state-rules.md),
   [lifecycle](references/shared/lifecycle.md),
   [verification](references/shared/verification-policy.md),
   [Issue contract](references/shared/issue-contract.md), and
   [Worker execution](references/shared/worker-execution.md).
   (If the packaged copy is absent, read the canonical `shared/worker-execution.md`
   file directly; the link target is optional.)
3. Read `GWO_HOME/config.json` and validate it with `gwo config check`.
   Invalid values block new dispatches but do not abandon existing work.
4. Discover Provider/model/mode through Paseo. Resolve an explicit override,
   then the role preference (Provider Binding). Resolve unattended mode by
   explicit override, configured `unattended_modes[provider]`, then exactly one
   advertised `isUnattended: true` mode. Never infer it from names or prose.
5. Read [cleanup safety](references/cleanup-safety-policy.md).

Role categories stay provider-neutral: Coordinator `planning`, Intake
`research`, Worker `impl` or `ui`, Review/Monitor `audit`.

## Claim coordination

Run:

```text
python <skill>/scripts/gwo.py coordinator claim --repository <owner/repo>
```

The store refuses a second claim and names the holder. If a Coordinator already
holds the lock, become an Operator Relay: sanitize the request to at most 500
characters plus a SHA-256, then use:

```text
python <skill>/scripts/gwo.py send --to <coordinator> --type ask --signal-id <id> --payload <json>
```

Do not read the GitHub frontier or worktrees; never promote an Agent in
`work/issue-*` or an existing Dispatch. Duplicate Coordinators stop admission
and integration; preserve every Agent for human adjudication.

## Separate home and Integration Control Workspaces

Use these exact meanings and titles:

- **Coordinator Home Workspace** — long-lived conversation location; title
  `Repo · <repo> · dev`. It may be dirty or not on `dev`.
- **Integration Control Worktree** — the explicitly addressed `dev` worktree
  used only for integration commands.
- Worker Workspace — isolated `work/issue-*`; title
  `WT · #<issue> · <slug>`.

All integration Git commands explicitly target the Integration Control
Worktree. Require it clean only immediately before integration. If it is dirty
or unavailable, preserve user WIP, keep tasks running, and hold verified
candidates in `WAITING_INTEGRATION`; never stash, reset, or force-clean.

## Reconcile, plan, and dispatch

Heartbeat is Worker liveness, never Coordinator polling. Batch-read the GitHub
frontier, native dependencies, assignees, contracts, PRs, and checks once per
repository reconciliation. A ready Issue has a
complete v3 contract and canonical Expected Hotset. Missing reliable Hotset
means a repository-wide exclusive Dispatch.

Create tasks and dispatches with:

```text
python <skill>/scripts/gwo.py task create --issue <n> --group <label> --risk <fast|standard|strict> --hotset '["src/..."]'
python <skill>/scripts/gwo.py dispatch create --task <task> --agent-name <name>
```

These are the `gwo task` and `gwo dispatch` commands.

Build a DAG plan schema v1 and validate it with:

```text
python <skill>/scripts/gwo.py guard check-dag --plan <plan.json>
```

This invokes the `gwo guard check-dag` validator.

The guard rejects acyclicity violations, Hotset overlap across concurrent issue
nodes, infeasible capacity, missing risk-tier review nodes, dependency
inconsistency, and a non-serial integration chain.

Admit all planned, capacity-safe, Hotset-disjoint tasks in one complete Worker
wave. Create every selected Worker after re-reading and claiming its Issue; do
not wait for another Worker. Read back exact parent, Provider/model/mode, labels,
branch, Workspace, and worktree before `START`. A single claim/create failure does
not roll back successful siblings.

Use the exact `Worker · #<issue> · a<attempt>` Agent name and
`WT · #<issue> · <slug>` Workspace title, then include those values in runtime
readback. Worker and Reviewer capacity are independent: a Task Group has three
Worker slots and two Review slots; `standard`/`strict` never reduce Worker slots
from three. Foreign active Paseo Agents consume global capacity; empty UI
drafts, archived Agents, and terminal idle Relays do not. Integration Lease
availability never serializes implementation.

Only use Paseo Agents created through the installed `paseo` Skill inside the
GWO tree. Provider-native Agent/Task/Swarm features must not appear inside a
GWO-owned Agent; when explicitly requested, route that work to a separate,
non-GWO top-level Task with no GWO ownership.

## Communicate through the gwo send / gwo inbox store mailbox

All addressed events flow through the gwo store. The Coordinator, Workers, and
Reviewers call:

```text
python <skill>/scripts/gwo.py send --to <agent> --type <type> --signal-id <id>
python <skill>/scripts/gwo.py inbox --agent-id <self> [--wait <s>] [--ack-on-read]
python <skill>/scripts/gwo.py ask --to <agent> --signal-id <id>
```

`gwo inbox --wait` is bounded; timeout only replays the mailbox. Event types
are `status`, `ask`, `reply`, `worker_done`, `review_result`,
`escalation`, `decision_gate`, and `heartbeat` (resident-agent model only).
Role entitlement is enforced by the CLI at write time from the spawn-injected
`GWO_AGENT_ID`; no Agent filters events itself. Use `signal_id` idempotency for
retries; never prompt a running Agent. A Worker's `inbox` defaults to its own
dispatch scope, so sibling history cannot block activation. Fifteen minutes of
silence permits one inspection; silence never authorizes cancellation or
replacement.

`worker_done` is candidate evidence only. The Coordinator verifies exact Agent,
Git head, dirty state, push/PR, checks, commands, changed paths, Hotset, and
acceptance before review. When a Worker is done, it calls the `gwo done` command:

```text
python <skill>/scripts/gwo.py done --dispatch <dispatch> --status done --evidence <json>
```

## Verify with risk-tiered review

- `fast`: the Coordinator performs both axes directly.
- `standard`/`strict`: issue one `review` node per required axis through the
  CLI. Lazily create exactly one `Spec Reviewer` and one `Quality Reviewer` as
direct Coordinator subagents and reuse them sequentially. Re-read global/Task
Group capacity before each creation; a stale reservation never permits
exceeding the cap. They do not communicate.
- Both receive the same candidate SHA, base SHA, diff digest, acceptance digest,
  round, and scope. `Spec Reviewer` checks Issue/decisions/scope/Hotset/
acceptance. `Quality Reviewer` checks standards/architecture/security/tests/
maintainability.
- The Coordinator only aggregates. Two valid `review_result` events are required;
  missing, duplicate, forged, cross-Task-Group, or lock-mismatched evidence
cannot form a verdict. Issue the lock with `gwo review round-create`; Reviewer
claims cannot authorize their own lock. Either failure returns work to the
same Worker. The next round makes both Reviewers inspect only the delta and
carries exact prior-lock lineage.

One pair serves one candidate at a time. Queue by verified-ready time, then Issue
number. Partial pair creation keeps the successful Reviewer and creates only
the missing axis; no final verdict exists until both are read back.

## Integrate and close safely

The Coordinator grants one repository-scoped Integration Lease (the `gwo lease`)
in durable ready order:

```text
python <skill>/scripts/gwo.py lease acquire --scope repo:<owner>/<repo>:integration
```

Run `execution_policy.py concurrency` with explicit Integration Control
availability/cleanliness. Refresh an advanced `dev` base and rerun affected
evidence. Only then merge into `dev`; `main` receives only an explicit verified
release merge. After three terminal failed attempts, move to `ready-for-human`;
attempt four is never automatic. `15 minutes` of silence permits one inspection.

Use `cleanup-plan` v4.3 with explicit `target_kind` and `resource_kind`:

- Worker with Issue worktree: `worker / issue-worktree`.
- Spec/Quality Reviewer: `worker / none`.
- Probe/forward-test: `ephemeral / none`, only with lifecycle label
  `gwo.lifecycle=ephemeral` and captured result readback.

Cleanup remains staged. Archive only a direct idle child with exact matching
repository/Task Group/dispatch and terminal receipt. Read back archived + unbound
before worktree actions. Protected plans have no actions.

Never target the Coordinator, a root/sibling/foreign/detached Agent, Coordinator
Home, Integration Control Worktree, a dirty/ambiguous resource, or use force.
The Coordinator survives every Task Group; only a human may retire it after
durable handoff. GWO does not clean provider-native zombie timelines or the Paseo
UI's empty `New Agent` draft tab; retain the packaged upstream-ready evidence
for those host issues.
