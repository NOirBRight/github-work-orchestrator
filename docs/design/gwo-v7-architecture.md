# GWO V7 architecture: a stateful kernel with dynamic orchestration

Status: accepted design, pre-implementation. Governing decisions: ADR 0007
(stateful gwo CLI), ADR 0008 (dynamic task DAG), ADR 0009 (Runtime Port).
Implementation is phased in `gwo-v7-roadmap.md`; V6 mechanisms remain
operative until the replacing phase lands.

## Goals and non-goals

Goals:

- Enforce coordination invariants in a runtime (`gwo` CLI + local store), not
  in prose that Agents must follow step by step.
- Let the Coordinator compose the orchestration shape at runtime as a task
  DAG, with deterministic guards instead of a prescribed supervision tree.
- Confine runtime coupling behind a five-operation Runtime Port so the kernel
  never references Paseo, and a headless adapter is a config change.
- Cut the operating protocol the Agent must read to under one third of V6.

Non-goals:

- No daemon, host service, or Paseo/provider source change (unchanged from V6).
- No second source of business truth: GitHub stays authoritative; the gwo
  store is a rebuildable cache.
- No headless adapter implementation yet; it is specified here as the port's
  validation case.

## Language

New terms; existing terms keep their `CONTEXT.md` meanings unless a mechanism
is retired by this design.

**gwo CLI**: The packaged, stdlib-only Python entry point
(`python <skill>/scripts/gwo.py`) that owns the gwo store and executes
runtime operations through the active adapter.
_Avoid_: daemon, service, second orchestrator

**gwo store**: The SQLite database under `GWO_HOME` (default
`~/.gwo/<repo-slug>/state.db`, WAL mode) holding mailbox, tasks, dispatches,
review rounds, leases, and the coordinator claim. Rebuildable from GitHub plus
adapter readback; never business truth.
_Avoid_: task database (in the ADR 0003 prohibited sense), business state

**Task Group**: A label on tasks that names a bounded effort. Replaces the
Campaign Agent, Campaign Control Workspace, and Campaign room.
_Avoid_: Campaign (as an Agent), campaign workspace

**Runtime Port**: The five-operation interface (`spawn`, `status`,
`deliver-prompt`, `worktree`, `archive`) between the gwo kernel and an Agent
runtime.
_Avoid_: provider API, Paseo wrapper

**Adapter**: One Runtime Port implementation. `paseo` (resident-agent) is the
only implemented adapter; `headless` (session-process) is specified.

**Execution model**: How an adapter realizes Agents. *Resident-agent*: long-
lived Agents with idle/running states (Paseo ACP container). *Session-
process*: one turn is one OS process; identity is a persistent session ID;
resuming the session is how a prompt is delivered.

**Event Journal**: The per-Agent JSONL file captured by a session-process
adapter from the CLI's structured output stream
(`GWO_HOME/agents/<agent-id>/events.jsonl`). Liveness and terminal evidence.
_Avoid_: log scraping, timeline inspection

**Stalled**: The `status` value for a live process/Agent whose Event Journal
or runtime activity has not advanced past the configured threshold. Advisory;
never authorizes a destructive action.

## System overview

```text
Coordinator conversation (any harness that can run shell commands)
  │  gwo task/dispatch/inbox/guard/lease/cleanup ...
  ▼
gwo CLI ──────────── gwo store (SQLite, GWO_HOME)
  │ Runtime Port (spawn/status/deliver-prompt/worktree/archive)
  ▼
Adapter: paseo (resident-agent)   [specified: headless (session-process)]
  ▼
Workers / Reviewers … each spawned with GWO_AGENT_ID injected,
each reports through `gwo send` / `gwo done` into the same store.

GitHub (issues, PRs, checks, decisions) = durable business truth, unchanged.
```

One Coordinator per repository claims a lock row in the store. All
inter-Agent communication is store-mediated; no Agent ever messages another
CLI directly, which is what makes the scheme identical across runtimes.

## Command surface

| Command | Purpose |
|---|---|
| `gwo coordinator claim/release` | Single-Coordinator lock row; a second claimant is refused and told the holder |
| `gwo task create/list/update` | Task rows: issue, Task Group, risk tier, Hotset, deps |
| `gwo dispatch <task>` | Create Worker via adapter, inject preamble + `GWO_AGENT_ID`, record dispatch row |
| `gwo send --to <agent> --type <t>` | Post one mailbox event; identity and role checked at write time |
| `gwo ask` | Blocking question sugar over `send`; waits for the correlated reply |
| `gwo inbox [--wait <s>] [--ack-on-read]` | Read/wait for events; acknowledgement is recorded by the CLI |
| `gwo done --status done\|blocked\|stopped` | Worker terminal event; CLI verifies caller identity and dispatch |
| `gwo agent status <id>` | Adapter readback: `running` / `stalled` / `exited` + terminal evidence |
| `gwo gate create/resolve` | Durable decision gates (mirrors the GitHub decision URL) |
| `gwo guard check-dag` | Deterministic validation of a submitted DAG plan |
| `gwo lease acquire/release` | Serial repository Integration Lease |
| `gwo cleanup plan` | Fail-closed cleanup planning; performs its own adapter/Git readbacks |
| `gwo doctor rebuild` / `gwo config check` | Rebuild store from GitHub + adapter readback; validate config |

Events shrink from 21 types to 8: `status`, `ask`, `reply`, `worker_done`,
`review_result`, `escalation`, `decision_gate`, `heartbeat` (resident-agent
model only). Role entitlement per type is enforced by the CLI, not documented
for Agents to obey.

## State schema

```text
coordinator(repo, agent_id, claimed_at, released_at)
agents(agent_id, adapter, runtime_ref, session_id, pid, role,
       group_label, created_at, archived_at)
tasks(task_id, repo, issue, group_label, risk, hotset_json, deps_json,
      status, created_at)              -- pending|ready|dispatched|done|failed|blocked
dispatches(dispatch_id, task_id, agent_id, attempt, worktree, branch,
           status, terminal_evidence_json)
messages(msg_id, signal_id, seq, from_agent, to_agent, type, payload_json,
         created_at, acked_at, acked_by)
review_rounds(round_id, dispatch_id, round, candidate_sha, base_sha,
              diff_digest, acceptance_digest, scope, prior_round_id, issued_at)
review_results(round_id, axis, agent_id, verdict, findings_json)
leases(lease_id, scope, holder_agent, acquired_at, released_at)
```

Identity columns are filled by the CLI from `GWO_AGENT_ID` plus adapter
readback; callers cannot supply them. `signal_id` retries deduplicate exactly
as in V6. Every state transition is a single SQLite transaction, which
replaces V6 `CHECKPOINT` events: the store *is* the checkpoint.

## Identity model

The adapter injects `GWO_AGENT_ID` into each spawned Agent's environment. The
gwo CLI resolves the caller's identity from that variable on every write and
refuses events the identity/role pair is not entitled to (the Orca lesson:
"the runtime ignores worker_done sent from a different pane"). In the
session-process model the adapter fully controls the child environment, so
identity is strictly stronger than V6's replay-time receipt checking. Room
claims can no longer create authority because there is no way to author a row
with someone else's identity through the CLI.

## Task DAG and guards

The Coordinator reconciles the GitHub frontier once per cycle (batch read of
issues, native dependencies, assignees, v3 contracts, PRs, checks — unchanged
from V6) and emits a declarative plan:

```json
{
  "schema_version": 1,
  "repository": "owner/repo",
  "nodes": [
    {"id": "t-143", "kind": "issue", "issue": 143, "group": "g-auth",
     "risk": "standard", "hotset": ["src/auth/"]},
    {"id": "r-143-q", "kind": "review", "target": "t-143", "axis": "quality"},
    {"id": "i-143", "kind": "integration", "target": "t-143"}
  ],
  "edges": [["t-143", "r-143-q"], ["r-143-q", "i-143"]]
}
```

`gwo guard check-dag` deterministically rejects a plan unless:

- the graph is acyclic and edges are consistent with GitHub native
  dependencies and the v3 contract;
- Hotsets of concurrently runnable issue nodes are disjoint; a node with no
  reliable Hotset is repository-wide exclusive (unchanged);
- capacity is feasible against configured limits (per-group and global Agent
  caps; the fixed 1+3+2 slot split becomes configuration);
- every issue node carries the review nodes its risk tier requires;
- integration nodes form one serial chain (the Integration Lease).

The wave is simply the DAG's ready frontier. Dispatch remains claim-first and
non-blocking across siblings; one failed create never rolls back others.

## Risk-tiered review

`fast` = no reviewer nodes (Coordinator checks inline). `standard` = one
reviewer node (combined axis). `strict` = two independent reviewer nodes,
`spec` and `quality`, separate Agents that do not communicate (ADR 0004's
axis independence, preserved). The CLI issues each `review_rounds` row —
candidate SHA, base SHA, digests, scope, prior-round lineage — and reviewers
can only reference it, never author it, which retires the Candidate Lock
Receipt ceremony while keeping its guarantee. Delta rounds require the prior
`round_id`. `worker_done` stays candidate evidence only: the Coordinator
verifies Agent, Git head, dirty state, push/PR, checks, changed paths,
Hotset, and acceptance before any review node runs (unchanged from V6).

## Runtime Port

```text
spawn(role, workspace, prompt, permission_profile) -> agent_id
status(agent_id) -> running | stalled | exited (+ terminal evidence)
deliver-prompt(agent_id, prompt) -> accepted | refused(reason)
worktree(action, path, branch) -> readback
archive(agent_id) -> readback
```

| Port operation | paseo adapter (resident-agent) | headless adapter (session-process, specified) |
|---|---|---|
| spawn | `create_agent` (subagent, workspace, provider binding, unattended mode) | background `claude -p --output-format stream-json --session-id <id>` or `codex exec --json`; record session ID + PID; capture stdout as Event Journal |
| status | `get_agent_status` readback | PID liveness + Event Journal growth; exit code + last event on exit |
| deliver-prompt | `send_agent_prompt`, idle only | `claude -p --resume <id>` / `codex exec resume <id>`; prior process exit is what "idle" means |
| worktree | Paseo worktree operations | native `git worktree add/remove` |
| archive | `archive_agent` + readback | confirm process terminal + mark archived in store |

Adapter selection: `PASEO_AGENT_ID` present → paseo; otherwise the configured
role→command template (templates are configuration, keeping the kernel
provider-neutral). Permission profiles compile to spawn flags — Paseo's
advertised unattended mode, Claude Code `--permission-mode`/`--allowedTools`,
Codex `--sandbox` plus approval policy — because a session-process runtime
fails interactive permission prompts by design.

### Observability and no-response handling

`gwo agent status` returns exactly three states. Resident-agent: from runtime
readback. Session-process: `running` = PID alive and Event Journal growing;
`stalled` = PID alive, journal frozen past the configured threshold (the V6
15-minute rule, computed by the CLI); `exited` = PID gone, classified by exit
code and the final journal event (`turn.completed` versus
`turn.failed`/`error`). Process exit is a deterministic signal — the
session-process model has no analogue of the resident-runtime zombie row in
`docs/evidence/paseo-provider-native-zombie-subagent.md`.

Problems reach the Coordinator on two channels: the contract channel (Workers
call `gwo ask` / `gwo done --status blocked`) and the runtime channel —
`gwo inbox --wait` also supervises every active dispatch and synthesizes a
`worker_exited_abnormally` event (exit code, journal tail, permission
denials) when a Worker process dies without a `gwo done` row. The Coordinator
never polls.

No response: while the journal advances, wait. On `stalled`, inspect the
journal tail once; past a hard threshold, escalate to a human. Kill requires
explicit contract authorization, never default. On abnormal exit, WIP is
intact in the worktree and context is intact in the persistent session, so
the Coordinator chooses: resume the same session with a "report state and
continue" prompt (a recovery primitive resident runtimes lack), or mark the
dispatch failed and follow the existing successor-proof rules. Silence never
authorizes cancel, archive, replacement, merge, or cleanup — unchanged.

## Mechanism replacement table

Every V6 mechanism maps to a V7 replacement or is kept. "Kept" rows are the
stability floor; replacements preserve the guarantee while deleting the
ceremony.

| V6 mechanism | V7 | Guarantee preserved by |
|---|---|---|
| entry_policy promote/relay/duplicate handling | `gwo coordinator claim` lock row + GitHub cross-check | store refuses a second claim and names the holder |
| Operator Relay + Repository Room + OPERATOR_* events, Signal-ID poisoning | any conversation runs `gwo send --to coordinator` (sanitization rule kept: ≤500-char summary + SHA-256, no credentials/paths) | CLI-validated envelope; no dedicated Relay Agent or second room |
| Coordinator Home vs Integration Control Worktree separation | **kept unchanged** | dirty conversation home can never invite WIP cleanup |
| Campaign Agent + Campaign Control Workspace + create-plan/validate-readback | Task Group label; per-issue worktrees kept | flat tree (ADR 0008); nothing to validate or clean |
| Frontier batch reconciliation, v3 Issue contract, Expected Hotset | **kept unchanged**, feeds the DAG planner | |
| campaign_scheduler plan-wave, fixed 1+3+2 slots | DAG planner + `guard check-dag`; slot split becomes config; wave = DAG ready frontier | same capacity/Hotset/dependency checks, applied to any shape |
| Provider Binding + unattended-mode resolution | **kept**, moves into adapter `spawn` | fail-closed on ambiguous mode, unchanged |
| Campaign room per Campaign (`gwo-<id>`) | one store mailbox per repository | flat tree; group label scopes queries |
| 21 event types + role matrix in prose | 8 types, entitlement enforced by CLI at write | invalid events rejected, not filtered by consumers |
| Identity receipts / identity-plan / authority scopes / composed receipts (ADR 0006) | write-time identity from spawn-injected `GWO_AGENT_ID` | callers cannot author identity columns |
| Dispatch-scoped replay (`--consumer-role worker`) | `gwo inbox` defaults to the caller's own dispatch scope | CLI filters; sibling history cannot block activation |
| Material Delivery WAKE/ACK (ADR 0005) | `gwo send` + `inbox --ack-on-read`; CLI checks recipient status and only wakes idle/exited recipients via `deliver-prompt` | at-least-once + Signal-ID idempotency inside the CLI; busy Agents never prompted |
| HEARTBEAT 5-minute target | resident: optional `heartbeat` event; session-process: process supervision over the Event Journal | liveness ≠ completion, unchanged |
| 15-minute stale inspection table | CLI computes `last_activity`; `inbox --wait` returns structured `stalled` notices; thresholds in config | silence never authorizes destructive action — **kept** |
| AGENT_READY / room START activation gate | dispatch row turns `active` on read-back spawn; preamble carries the dispatch ID | activation is a store transition, not a message exchange |
| ASK/REPLY correlation | `gwo ask` (blocking, CLI-correlated) | |
| GitHub decision gates for scope/architecture/security changes | **kept unchanged** (`gwo gate` mirrors the durable GitHub URL) | durable decisions stay on GitHub |
| WORKER_DONE is candidate evidence; independent verification before review | **kept unchanged** | |
| Candidate Lock Receipt + Review Assignment + dual-axis pair | CLI-issued `review_rounds` row; risk-tiered review nodes (fast 0 / standard 1 / strict 2 independent axes); delta rounds carry `prior_round_id` | reviewers reference, never author, the lock |
| Integration Lease + concurrency policy + dev refresh/rerun | `gwo lease` + serial integration chain in the DAG; refresh-and-rerun rule kept | one integrator at a time, unchanged |
| cleanup-plan v4.3 staged cleanup, protected targets | **kept philosophy**; `gwo cleanup plan` performs its own adapter/Git readbacks | fail-closed, staged, never force, never target Coordinator or Integration Control Worktree |
| CHECKPOINT recovery events | the store itself (transactional state + cursors) | `gwo doctor rebuild` reconstructs from GitHub + adapter readback |
| Permission relay to parent | resident: kept via adapter; session-process: profiles compiled at spawn, interactive requests fail fast and surface on the runtime channel | never broaden scope beyond the contract, unchanged |
| `~/.paseo/orchestration-preferences.json` + resolve-config | `GWO_HOME/config.json` + `gwo config check` | invalid config blocks new dispatch, never abandons work |
| Room deletion after completion | mailbox rows archived with the Task Group after GitHub readback | GitHub-first completion, unchanged |

## Stability floor (kept invariants)

GitHub is the only durable business truth. Issue contract v3 unchanged.
`worker_done` is candidate evidence only; independent verification precedes
review; review precedes integration. One serial Integration Lease; `main`
only receives explicit verified release merges. Fail-closed, staged cleanup;
never force; never target the Coordinator, Coordinator Home, or the
Integration Control Worktree. Silence, stalled status, or a missing ACK never
authorize cancel, archive, replacement, merge, or cleanup. Provider and model
are never hardcoded. Store loss degrades to reconciliation, never to data
loss.

## Installation and distribution

The three skills stay self-contained SKILL.md packages (open Agent Skills
standard), so one repo serves every harness:

1. `npx skills add <github-repo>` (skills.sh installs into multiple
   harnesses).
2. Manual: clone, then copy/symlink `skills/*` into `~/.claude/skills/`,
   `~/.codex/skills/`, or repo-level `.agents/skills/` (Codex follows
   symlinks). The existing `.skill-package.json` +
   `sync_skill_references.py` vendoring keeps each package self-contained.
3. Optional later: a `.claude-plugin/` manifest for `claude plugin install`;
   an `openai.yaml` for Codex UI metadata.

The gwo CLI needs no separate install: stdlib-only, packaged in the skill's
`scripts/`, invoked as `python <skill>/scripts/gwo.py`. State lives in
`GWO_HOME` (default `~/.gwo/<repo-slug>/`); harnesses that install the same
skill share the same store, which is exactly the cross-CLI communication
channel. Paseo-hosted ACP Agents read their harness's skill directory and
reuse the same installation.

## Recovery

The Coordinator restarts by reading the store and reconciling against GitHub
and adapter readback — the V6 replay discipline with the cursor kept
transactionally. If the store is lost or corrupt, `gwo doctor rebuild`
reconstructs tasks from GitHub (issues, labels, PRs), Agents from adapter
listing (Paseo Agent list, or session/PID scan for headless), and worktrees
from Git; anything unreconcilable is surfaced for human adjudication, never
cleaned automatically.
