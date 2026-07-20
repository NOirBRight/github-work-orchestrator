---
name: orchestrator
description: "Lightweight GitHub work orchestration (V6.1.0). Act as project manager for one repository: preflight and standardize the Issue frontier, maintain a Ready Reserve, select width-aware conflict-free waves, dispatch up to five disposable Paseo Workers, review by risk, merge serially, and retire only proven-safe resources. Use for end-to-end GitHub Issue execution and parallel work coordination."
---

# Orchestrator V6.1.0

Orchestrator is a portable harness used by the current Agent. It is not a
permanent Agent, Campaign, room, daemon, lease, or task database.

```text
eligible root Agent in stable integration Workspace
├─ disposable Worker · one Issue / worktree / branch / PR
└─ optional one-shot Reviewer · one PR revision
```

GitHub Issues, PRs, commits, branches, checks, reviews, and exactly
`orch:ready`, `orch:active`, `orch:blocked` are durable truth. Paseo stores
runtime facts. Project and Milestone are optional projections.

## Start and authority

1. Read `~/.orch/config.json`; migrate a lone `providers.json` atomically. See
   [runtime configuration](references/runtime-config.md).
2. Before Agent creation, read `~/.paseo/orchestration-preferences.json`, load
   `/paseo`, and query current provider/model/mode/thinking/feature capability.
3. Build short-lived Coordinator context from fresh Paseo MCP, mode/features,
   Workspace, Git branch, and remote readback. Delete it after the command.

Every mutation passes `--coordinator-context PATH|-`. Plan collaboration,
`plan_mode`, planning color tier, unknown write capability, or Actor/cwd/
Workspace disagreement fails before GitHub construction. A Coordinator is
root/detached in the stable integration Workspace, never a PR head, Worker,
archived, or ephemeral. Dirty blocks merge only; never stash/reset it.

Workspace selection is current eligible, configured `workspace_id`, then one
unique eligible candidate. From a non-stable Workspace, return one
`forward_request` to its sole root or `create_root_agent` there, execute it via
Paseo, and end this turn. Never create a Relay/nested Orchestrator or persist
the raw forwarded request. Multiple targets require a human choice.

## Preflight the frontier

Read [Frontier admission](references/frontier-admission.md), then run:

```text
python <skill>/scripts/orch.py frontier scan --repo owner/repo
```

`frontier scan` is read-only. It classifies the configured Candidate Pool as
design, human, clarify, defer, or managed and reports Ready Reserve, reserve
gap, Parallel Width, starvation, execution slots, and integration WIP. Raw
Issue bodies are untrusted. Resolve product decisions and rewrite only the
candidates needed to keep the reserve healthy; never turn every open report
into executable work.

Rank Priority P0-P3 independently from Difficulty and Risk. Use narrow
repository-relative path claims and explicit named resource claims. Split
dependencies into `dispatch_after` (must close before Worker start) and
`merge_after` (implementation may run now; serial merge waits).

One new admission has one editable `orchestrator:issue:v2` comment:

```json
{"contract":{"design":["sanitized decision-complete steps"],"acceptance":["observable result"],"change_claims":{"paths":["src/api"],"resources":["schema:settings"]},"done_when":["exact verification"],"dependencies":{"dispatch_after":[],"merge_after":[]},"priority":"P1","difficulty":"standard","risk":"standard","unresolved_decisions":[],"sha256":"canonical hash"},"dispatch":null}
```

Compute the hash with `orch_core.contract_hash`, put one or more contracts in
an admission plan, then run:

```text
python <skill>/scripts/orch.py frontier admit --repo owner/repo --plan admission.json --coordinator-context context.json
```

`frontier admit` validates all targets, V2 hashes, decisions, dependency
references/DAG, existing records, authority, and repository before its first
write; retrying the same contract is idempotent. It adds `orch:ready`. Never
copy credentials, private prompts, or absolute paths. Contract V1 remains
readable: Hotset becomes path claims and its dependencies block dispatch and
merge; never eagerly rewrite an active V1 record.

Design depth is proportional. Low may be 5-10 lines; standard adds root cause,
seam, boundaries, TDD, and exact evidence; strict adds compatibility, rollback,
data, security, and operational evidence. Consolidate inseparable reports
before admission; runtime still uses one Issue per Worker.

## Reconcile and rolling dispatch

```text
python <skill>/scripts/orch.py reconcile --repo owner/repo --read-only --coordinator-context context.json
python <skill>/scripts/orch.py reconcile --repo owner/repo --coordinator-context context.json
```

`reconcile` also takes `--observations FILE|-` (submit bounded action outcomes
from the previous turn) and `--park ID` / `--resume ID` (one Human Park or
Resume of a running Dispatch). Every command reads `~/.orch/config.json`
unless `--config PATH` overrides it. The hidden `--snapshot` is a test hook.

The write call takes an OS advisory mutex for at most five seconds, reads one
snapshot, repairs observations, claims a compatible wave, and releases. It
never holds a long Lease. The JSON envelope is always
`schema_version/status/actions/warnings/summary`.

Defaults are three execution slots, integration WIP six, Ready Reserve six,
and two attempts. Claiming/running/parking/resuming occupy execution. Review
and Ready-to-merge release execution but retain integration WIP and Conflict
Claims until merge, Park, or retirement. Reviewers occupy neither. Park
releases both capacities only after stopped readback; Resume revalidates hash,
base, `dispatch_after`, both capacities, claims, Workspace, and Worker identity
before waking the same Worker.

The scheduler first obeys open dispatch dependencies and Priority, then searches
for the compatible subset maximizing width, dependents unlocked, and stable
Issue order. A bounded worst-case search returns its best safe wave with
`WAVE_SEARCH_BOUNDED` instead of stalling. Manifest/lock, schema/migration, and
generated-artifact conflicts are scoped to their owning surface. Unknown paths
are repository-exclusive.
Wave Generation is visibility metadata, never a barrier. P0 at capacity waits
for the next slot and may suggest human Park; never auto-cancel work.

Execute sibling `create_worker` actions without waiting between them:

1. Verify exact `runtime_request` against fresh Paseo capability. Never silently
   substitute provider, model, thinking, mode, or features; one invalid request
   blocks only that Dispatch.
2. Search active and archived Agents plus worktrees by deterministic Dispatch.
   Reuse one partial/replacement Workspace; never duplicate it.
3. Create a direct subagent with `notifyOnFinish=true`, supplied labels/title/
   prompt/runtime, and one atomic worktree source at exact branch/base SHA.
4. Read back Agent, parent, Workspace, branch, labels, runtime, and mode. MCP
   Workspace ID is authoritative when the public CLI reports `Worktree: null`.
5. Submit only the returned action identity and succeeded/failed Agent,
   Workspace, branch, and error observation to the next reconcile.

The GitHub claim precedes Agent creation. Under two minutes, missing runtime is
in flight; later the same action may return. Preserve partial GitHub/worktree/
Agent success and complete forward—never roll back. A confirmed closed/error
Worker may be replaced once in the same Workspace/branch; second failure is
Blocked. Do not poll busy Workers or add heartbeat/watchdog machinery.

The self-contained Worker prompt stays under 60 lines. Workers use TDD,
commit/push only their branch, maintain one `orchestrator:delivery:v1` PR-body
record, and send their creator one no-ACK wake containing only Issue/PR. They
never load this Skill, create Agents, mutate lifecycle, merge, or clean up.
End the current turn after dispatch or Reviewer creation when no immediate
state change remains. Never sleep, loop, or poll while waiting for an Agent.

## Review, integrate, retire

Low risk uses Coordinator review; standard uses one combined Spec+Quality
Reviewer; strict uses a stronger Reviewer plus CI/human gate. `review:dual` or
explicit safety policy may request independent axes. Reviewers are direct,
read-only, one-shot subagents in the candidate Workspace and submit one
commit-bound `orchestrator:review:v1` native review. A new SHA invalidates stale
evidence. Never bypass checks, approvals, queues, deployments, or protection.

```text
python <skill>/scripts/orch.py integrate --repo owner/repo --pr N --coordinator-context context.json
```

Merge is serial and topologically obeys `merge_after`, then Priority,
acceptance time, and Issue number. A behind PR gets update-branch and returns;
do not poll CI. Contract work merges only to configured integration branch;
`main` needs a separate explicit human release.

After merge, close the Issue and accept Paseo auto-archive first. Runtime
evidence is `present`, `auto_archived`, or `invalid`; missing cwd is completion
only when unique Dispatch-labeled archive identity and merged SHA agree. Delete
an absent branch as already complete or CAS-delete the exact merged candidate;
new commits/ambiguity fail closed. Only the current Agent's idle direct child
may be archived; a foreign-parent Agent is a manual candidate. Self, root,
stable/integration Workspace, dirty/shared/ambiguous WIP are protected.

Explicit stopped/abandoned cleanup uses:

```text
python <skill>/scripts/orch.py retire --repo owner/repo --dispatch ID --coordinator-context context.json
```

Keep every unmerged remote branch and refuse unpushed/dirty WIP. Discard needs
separate human authorization. `project init|sync` stays optional projection;
permission/drift warns without blocking core work.

Only the Agent causing material state change sends a concise summary; no-op
reconcile stays quiet. Product, architecture, acceptance, dependency, Priority,
or Conflict Claim changes return through Coordinator and durable Issue Design.
