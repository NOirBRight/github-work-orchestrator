---
name: orchestrator
description: Lightweight GitHub work orchestration (V6.0). Act as project manager for one repository: clarify and prioritize Issues, plan rolling Hotset-disjoint waves, dispatch up to five disposable Paseo Workers through local runtime tiers, grade review by risk, merge serially into the integration branch, and retire only proven-safe resources. Use for end-to-end GitHub Issue execution and parallel work coordination.
---

# Orchestrator V6.0

Orchestrator is a portable harness used by the current Agent. It is not a
permanent Agent, Campaign, room, daemon, lease, or task database.

```text
qualifying root Agent in fixed integration Workspace
├─ disposable Worker · one Issue / worktree / branch / PR
└─ optional one-shot Reviewer · one PR revision
```

GitHub Issues, PRs, branches, commits, checks, reviews, and the three labels
`orch:ready`, `orch:active`, and `orch:blocked` are durable truth. Project and
Milestone are optional views. Paseo stores runtime facts only.

## Start

1. Read `~/.orch/config.json` when present. If only
   `~/.orch/providers.json` exists, the CLI migrates it atomically and keeps
   `providers.v5.backup.json`. See [runtime config](references/runtime-config.md).
2. Before creating any Agent, read `~/.paseo/orchestration-preferences.json`
   and load the `/paseo` Skill. Query Paseo provider/model/mode/thinking/feature
   capabilities immediately before creation.
3. Inspect the current Agent, Git remote, branch, and Workspace.

A state-changing Coordinator must be root/detached, match the repository, and
be in the configured integration Workspace. The Workspace must not be a PR
head, `work/issue-*`, archived, or ephemeral. Dirty is allowed for design,
dispatch, execution, and review; it blocks merge. Never stash, reset, or author
tracked changes from the Coordinator Workspace.

Workspace choice is current eligible Workspace, configured `workspace_id`,
then a unique eligible integration Workspace. If none or several remain, stop
with `WORKSPACE_SELECTION_REQUIRED`.

When invoked from a non-stable Workspace, do not create a Relay, Draft, room,
or nested Orchestrator. Forward the original request to the sole active root
Agent in the target Workspace. If none exists, create one detached root Agent
there with the caller's exact current runtime. If several exist, ask the user
which one.

## Design the frontier

Be the project manager, not an Issue queue consumer:

- classify related work and dependencies across the frontier;
- rank urgency `P0` through `P3` independently of difficulty and risk;
- identify narrow write Hotsets and implicit schema/generated/manifest/lockfile
  conflicts;
- design only candidates likely to enter the rolling wave now.

One Ready Issue has one editable comment containing
`orchestrator:issue:v1`. Its JSON record is:

```json
{
  "contract": {
    "design": ["sanitized decision-complete steps"],
    "acceptance": ["observable result"],
    "hotset": ["repository/relative/path"],
    "done_when": ["exact verification command"],
    "dependencies": [],
    "priority": "P0|P1|P2|P3",
    "difficulty": "light|standard|heavy",
    "risk": "low|standard|strict",
    "unresolved_decisions": [],
    "sha256": "canonical contract hash"
  },
  "dispatch": null
}
```

Rewrite raw reporter text; never copy credentials, private prompts, or absolute
local paths. Use `orch_core.contract_hash` and `render_issue_record` to produce
the exact record, then create or update its single GitHub comment. Duplicate
markers fail closed. Add exactly one of the three `orch:*` labels; Ready
requires a valid hash and no unresolved decision.

Design depth is proportional: low may be 5–10 lines; standard adds root cause,
seam, boundaries, steps, and TDD; strict adds compatibility, rollback, data,
security, and operational evidence. One Issue is never bundled with another at
runtime—consolidate inseparable reports before Ready.

## Reconcile and dispatch

Run:

```text
python <skill>/scripts/orch.py reconcile --repo owner/repo --read-only
python <skill>/scripts/orch.py reconcile --repo owner/repo
```

The write command takes an OS advisory mutex for at most five seconds, reads
one frontier snapshot, fetches the exact integration commit without moving the
local branch, repairs observations, claims free work, then releases the lock.
It never holds a long Lease. The JSON envelope is always
`schema_version/status/actions/warnings/summary`.

WIP defaults to three and may be configured from one to five. Active, Review,
Ready-to-merge, and unparked Blocked occupy slots until merge or explicit stop.
Reviewers do not. Ready work is ordered by satisfied dependencies, Priority,
Milestone due date, dependents unlocked, then Issue number; disjoint Hotsets
greedily fill every free slot. A Wave Generation is visibility metadata, never
a batch barrier. P0 at full WIP waits for the next slot and reports optional
human preemption; never auto-cancel running work.

Execute every returned `create_worker` action without waiting between siblings:

1. Verify its exact `runtime_request` against fresh Paseo capabilities. Missing
   model, thinking option, mode, or feature blocks only that Dispatch; never
   substitute silently. The Paseo `create_agent.provider` value is the verified
   `<provider>/<settings.model>` pair; pass thinking/mode/features through its
   `settings` object.
2. Call Paseo `create_agent` with direct `subagent` relationship,
   `notifyOnFinish=true`, the supplied labels/title/prompt, and one atomic
   `workspace.source.kind=worktree` branch-off using its exact branch and base
   SHA. Before creation, read back both matching Agents and worktrees: attach to
   a unique branch worktree left by partial creation; never create a duplicate.
   A replacement uses the supplied existing Workspace.
3. Read back Agent ID, parent, Workspace ID, branch, labels, runtime, and mode.
   Use Paseo MCP `get_agent_status` for Workspace ID; the public CLI may report
   `Worktree: null` even when the MCP readback has a valid `workspaceId`.
4. Submit only this observation shape to the next reconcile:

```json
[{"action_id":"...","status":"succeeded|failed","agent_id":"...",
  "workspace_id":"...","branch":"work/issue-N","error":null}]
```

```text
python <skill>/scripts/orch.py reconcile --repo owner/repo --observations file.json
```

The GitHub claim is written before Agent creation. Under two minutes, a missing
Agent is treated as in flight. Afterwards the same deterministic action may be
returned; search active and archived Agents for its `orch.dispatch` label
before creating, and feed the existing identity back instead of duplicating it.
Partial GitHub/worktree/Agent success always moves forward—never roll it back.

The generated Worker prompt is the whole contract and stays under 60 lines.
Workers must not load this Skill, create Agents, change labels/lifecycle, merge,
or clean up. They use TDD, commit/push, maintain one PR body record marked
`orchestrator:delivery:v1`, and send their creator one no-ACK wake containing
only Issue/PR after delivery. Native finish notification is also enabled.
End the current turn after dispatch or Reviewer creation when no immediate
state change remains. Never sleep, loop, or poll while waiting for an Agent;
the native finish or direct wake starts the next invocation.

Do not poll busy Workers or add heartbeat/watchdog machinery. On an objective
idle recovery action, send one prompt. After a confirmed closed/error terminal,
one replacement may continue the same Workspace/branch; the second failure
becomes Blocked. Human Park preserves branch/WIP but releases the slot and
Hotset; resume revalidates base, design, and conflicts.

## Review

Reconcile derives Review from the delivered PR and returns Reviewer actions:

- `low`: Coordinator checks specification and quality, then posts one
  commit-bound native PR review record.
- `standard`: one combined Spec+Quality Reviewer.
- `strict`: one stronger combined Reviewer plus CI/human gate.
- `review:dual` or an explicit safety policy: independent Spec and Quality
  Reviewers for that candidate only.

Create returned Reviewers as direct subagents in the current Workspace with the
supplied prompt/runtime/labels. Before creation, search active and archived
Agents for the exact `orch.action` label; reuse/wait instead of duplicating it.
They are read-only and one-shot. Their native PR
review must contain `orchestrator:review:v1`. A new candidate SHA invalidates
stale evidence; review only the affected delta. Without required checks, low
needs TDD/local verification plus Coordinator review, standard additionally
needs its Reviewer, and strict needs human approval or a contract-defined
independent E2E/security substitute.

## Integrate and retire

Run only the selected ready PR:

```text
python <skill>/scripts/orch.py integrate --repo owner/repo --pr N
```

Integration re-reads head/base/checks/review/contract/Workspace under the short
mutex. Dependency, Priority, acceptance time, then Issue number determine
serial order. A behind PR receives GitHub update-branch and returns immediately;
do not wait on CI. Never bypass approval, merge queue, deployment, or branch
protection. Contract work may auto-merge only to the configured integration
branch; `main` requires a separate explicit human release.

After merge readback, close the Issue and accept Paseo auto-archive first. Only
the current Agent's idle direct child may be archived. A foreign-parent Agent is
a manual candidate. Worktree/branch deletion requires merged, clean, unbound
evidence. Self, root, stable/integration Workspace, dirty/shared/ambiguous WIP,
and unknown identity are permanently protected.

For an explicitly `stopped` or `abandoned` Dispatch:

```text
python <skill>/scripts/orch.py retire --repo owner/repo --dispatch dispatch-id
```

Retirement preserves every unmerged remote branch and refuses unpushed or dirty
WIP. Discarding WIP always needs separate human authorization.

`project init|sync` is optional projection only. Permission or drift failures
return `project-sync-degraded` and never block the core flow.

## Communication discipline

Only the Agent that caused a material state change sends a concise Coordinator
summary. A duplicate/no-op reconcile stays quiet. Product, architecture,
acceptance, dependency, Priority, or Hotset changes go through the Coordinator
and durable Issue Design; technical implementation stays in the Worker PR.
