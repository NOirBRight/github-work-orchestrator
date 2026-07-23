# GWO V8 architecture

Status: accepted design; implementation complete through Phase 2.

`CONTEXT.md` is the normative vocabulary. The governing ADR chain preserves
earlier V7-oriented decisions as superseded history; V8 starts from the actual
V6.1 production line.

## Goals

- Represent authorized work as immutable, durable Plan Revisions instead of
  mutable Agent-created execution state.
- Separate Work Item, Plan Node, Admission, Attempt, Runtime Binding, Artifact,
  Evidence, Verification, and Result lifecycles without exposing one shallow
  module per noun.
- Admit the complete compatible ready frontier and keep bounded Worker
  capacity productively occupied while Review fans out inside those Attempts.
- Continue a Task Group Goal until verified completion or an explicit blocker
  without requiring a Coordinator Agent to remain alive.
- Make completion exact-candidate and Evidence based while avoiding routine
  retesting or re-review by every consuming role.
- Converge external Runtime effects through idempotent actions and
  authoritative readback.
- Cut over from V6.1 with one durable writer generation and a fresh Store,
  without V7 execution-state migration.

## V8.0 scope

V8.0 delivers:

- the domain model and PlanSpec v2;
- deterministic compilation and durable activation;
- `ready-for-agent` intake from the Matt workflow and an explicit
  `/implement-gwo` execution entry;
- optional Skill binding and Prompt snapshots;
- atomic Admission and idempotent Materialization;
- typed Evidence, local-first verification, review, and bounded recovery;
- `/goal`-like continuation, high parallel utilization, serial Integration,
  shadow, canary, and V6.1 cutover.

## V8.0 non-goals

- Semantic Planner implementation.
- AI-selected dynamic routing or self-learning policy.
- Direct Codex CLI or Claude Code Runtime Adapters.
- A universal cross-runtime interface frozen before a second production
  Adapter exists.
- A GWO daemon, internal event bus, global polling scanner, or multi-primary
  Kernel.
- Automatically running HITL planning commands such as `grill-with-docs`,
  `to-spec`, `to-tickets`, or `triage` inside a campaign.
- Changing the original Matt `/implement` single-ticket workflow.
- Backlog prediction, critical-path scheduling, or learned throttling.
- In-place reinterpretation of V6.1 or V7 execution rows.
- Mirroring or versioning installed Skills.
- Hostile-host security or credential-broker enforcement.

## System overview

```text
                 Matt planning and inbound-request workflow
             grill / optional spec / tickets / triage
                               │
                     ready-for-agent Work Items
                               ▼
                           GitHub durable truth
          Plan records / activation receipts / decisions / manifests
                               ▲          │
                               │          ▼
Host Goal Driver ───────► Kernel: reconcile_once(repository)
  (/goal-like)                    │
       ▲                          ├── Plan Compiler
       │ ReconcileOutcome         ├── Publication & Activation
       │                          ├── Evidence Verifier
       │                          ├── rebuildable SQLite Store
       │                          ├── Git / GitHub / CI readback
       │                          └── Runtime Adapter seam
       │                                      │
       └──── event or next_check_at ◄──── Paseo Adapter
                                              │
                                    Agents / sessions / worktrees
```

The Goal Driver owns liveness. It invokes one deterministic reconciliation
pass, follows the returned directive, sleeps without LLM sampling on named
external waits, and invokes another pass. The Kernel has no permanent loop.

GitHub is durable business truth. SQLite is a transactional, reconstructable
control-plane Store. External I/O never occurs inside a Store transaction.

## Matt workflow handoff

Matt workflow commands remain human-facing upstream processes, not Plan Nodes
or Kernel actions:

```text
idea -> grill-with-docs -> optional to-spec -> to-tickets ─┐
raw Issue or external PR -> triage ────────────────────────┤
                                                           ▼
                                                ready-for-agent Work Items
                                                           │
                                                     Plan Intent
```

`to-spec` is optional when an accepted canonical specification already exists.
Tickets produced by `to-tickets` are already agent-ready and are never sent
through `triage` again. `triage` is for raw inbound Issues and external PRs; its
Agent Brief becomes the authoritative behavioral contract when it moves an
item to `ready-for-agent`.

The `ready-for-agent` state is the only automatic GWO intake gate. A source
snapshot normalizes the Work Item identity, behavioral contract, acceptance
criteria, scope exclusions, source digest, and tracker blocking relationships.
The Plan Compiler rejects raw, `needs-triage`, `needs-info`,
`ready-for-human`, `wontfix`, or conflicting state inputs. Blocking
relationships become the single typed Plan Edge graph; they are not retained
as a parallel scheduler.

Planning commands are never invoked automatically by a campaign because they
contain HITL decisions. A Coordinator may propose a new Work Item within an
existing Goal, but uncertain scope or acceptance creates a Decision Gate
rather than impersonating the maintainer.

Execution has two explicit user-facing entries:

- `/implement` remains Matt's unchanged single-ticket, current-context flow;
- `/implement-gwo` accepts one Ready Work Item, a parent Goal/spec, or an
  explicit set of Ready Work Items and creates or resumes a durable Task Group
  Goal.

V8 keeps `/orchestrator` as a compatibility alias for `/implement-gwo` for one
release and removes the alias in V8.1. It is not a second workflow. An
unready input fails closed with the appropriate next command instead of
silently falling back or starting planning.

Once inside GWO, neither `implement` nor `implement-gwo` is a valid Plan Node
Skill Reference. Work nodes use focused execution guidance such as `tdd`,
`diagnosing-bugs`, `research`, or `prototype`. A Candidate-producing Work
Attempt invokes `code-review` only when its compiled output contract requires
Review Evidence. This prevents recursive orchestration and duplicate Review.

## Configuration ownership

Configuration is deliberately split by meaning.

The versioned repository policy, expected at a path such as
`.gwo/policy.yaml`, owns:

- difficulty rules and risk floors;
- review and specialist policy;
- check definitions and Evidence requirements;
- Effect Contract and allowed capability constraints.

The host-local `~/.orch/config.json` owns concrete execution. It contains
global defaults plus `repositories.<owner/repo>` overrides for:

- Runtime Profiles and Worker-tier bindings;
- the auto-created Coordinator role binding and Review Profile selectors;
- provider, model, thinking, mode, and features;
- explicit same-or-higher fallbacks;
- Worker and Coordinator capacity.

Repository operational overrides replace complete profiles rather than
deep-merging model fields. Managed Worker and Coordinator resolution is:

```text
global host defaults
  -> host-local repository override
  -> validate against versioned repository policy and PlanSpec constraints
  -> bind at Admission
  -> record the actual profile and configuration digest in Runtime Binding
```

Configuration changes affect new Admissions only.

Review Profile resolution uses the same global-then-repository replacement
rule when a review child is launched. It is recorded in that axis observation
rather than a Plan Node Runtime Binding, and a change affects only later child
launches.

The initial host-local selector shape is:

```json
{
  "review_profiles": {
    "standard_axis": "reviewer_standard",
    "recovery_axis": "reviewer_recovery",
    "strict_specialist": "reviewer_strict"
  }
}
```

The values are Runtime Profile IDs. A repository may replace this mapping in
its host-local override without placing provider or model names in PlanSpec.

## Deep module layout

V8 keeps five deep modules. Store tables, migrations, receipts, and internal
state handlers remain implementation details behind these interfaces.

### Plan Compiler module

```text
compile(plan_intent, source_snapshot, policy_snapshot) -> CompileResult
```

The pure implementation owns schema validation, semantic normalization, typed
edge construction, cycle checks, stable Node Keys, contract digests,
difficulty floors, risk-to-review-requirement compilation, serial Integration
structure, Skill-usage validation, and canonical PlanSpec serialization. It
accepts only Ready Work Item source snapshots and rejects workflow-command
names used as execution Skill References.

`CompileResult` is either a `CompiledPlan` containing canonical bytes, digest,
and Compilation Record or deterministic errors. Nothing else canonicalizes or
redigests PlanSpec.

### Plan Publication and Activation module

```text
publish_and_activate(
  compiled_plan,
  expected_active_digest,
  writer_generation
) -> ActivationOutcome
```

The implementation hides the cross-system protocol:

1. CAS a Store `pending` reservation against the expected active digest and
   freeze new repository Admissions.
2. Publish the immutable compiled Plan record to the dedicated GitHub control
   branch (default `gwo-control`) and read it back.
3. CAS the control-branch `active-plan.json` record by expected Git commit or
   blob identity.
4. Read back the durable Activation Receipt.
5. Finalize the active digest in the Store.

The minimal receipt contains:

```text
schema_version
repository
writer_generation
activation_id
plan_digest
expected_previous_digest
plan_record_ref
created_at
```

The durable receipt is the commit point. Before it exists, a proven unchanged
control record permits clearing `pending`; after it is read back, recovery may
only roll forward to Store finalization. GitHub unavailability or ambiguity is
a Wait Condition. A conflicting active digest returns `ActivationConflict`
with the new active identity and a compact semantic delta; it is never
auto-merged or queued. No Admission is possible while activation is pending or
unfinalized.

The control branch does not run product CI. Git history retains prior Plan and
activation records.

### Kernel module

```text
reconcile_once(repository) -> ReconcileOutcome
```

One pass owns readiness, atomic Admission, claims, Materialization
reconciliation, Attempt transitions, Wait Conditions, Goal continuation,
verification orchestration, recovery, Integration eligibility, writer fencing,
and cleanup authorization. It drains all currently due mechanical work and
may fan out a bounded set of idempotent Runtime actions before returning.

`ReconcileOutcome` directs the host to:

- invoke or resume the Coordinator for semantic work;
- wait for a named event or `next_check_at`;
- run another pass immediately because mechanical work remains;
- report the Goal complete or explicitly blocked.

Tests use a real temporary SQLite Store through this interface. V8 exposes no
repository interface for each table.

### Runtime Adapter seam

V8.0 defines only the Paseo-shaped capabilities it actually consumes:

- idempotent create or adopt;
- repository, Plan Revision, Node Key, Admission, Agent, session, and workspace
  identity round-trip;
- Prompt acceptance and readback;
- lifecycle and active-turn observation;
- resumable session, safe interrupt, and read-backed retirement;
- explicit and classifiable errors.

Capacity, permission, token, and event observations are optional when Paseo
truthfully exposes them. Names never imply capability, and missing required
capability fails the affected node closed.

The in-memory implementation is a contract-test fake and supports the first
walking skeleton. It does not prove that this interface is universal. The seam
remains intentionally evolvable until a real direct Codex CLI or Claude Code
Adapter enters the same lifecycle and exposes its differences.

Runtime events are wake hints. The Kernel changes state only after readback.

### Evidence Verifier module

```text
verify(result_claim, output_contract, evidence_set) -> VerificationDecision
```

The implementation validates Evidence envelopes, exact subjects, provenance,
definition and environment digests, durable source binding, risk-required
Evidence, base sensitivity, candidate invalidation, and acceptance
invalidation. It returns accepted, rejected with findings, or waiting for a
named Evidence source. Worker assertions never become Evidence merely by
crossing this interface.

## PlanSpec v2

The canonical top level is:

```text
schema_version
repository
parent_plan_digest
goals
work_items
nodes
edges
```

Goal and Work Item entries contain stable identity and the normalized semantic
snapshot used by the revision.

A Plan Node contains:

- stable Node Key and kind: `work`, `decision`, or `integration`;
- Goal and Work Item relationship;
- inputs and the one authoritative typed-edge graph;
- output contract, required Evidence, and any compiled
  `review_requirement`;
- Effect Contract and resource claims;
- Runtime Requirements;
- difficulty, risk, and recovery policy;
- optional Skill Reference.

Concrete Agent, provider, model, Runtime Profile, session, workspace, live
capacity, token use, elapsed time, and publication state never enter PlanSpec.

For each Candidate-bearing Work Node, the Compiler emits the minimal semantic
review contract:

```text
review_requirement:
  mode: none | dual_axis | strict
  axes: [] | [standards, spec]
  specialist_requirements: [stable repository-policy IDs]
  human_decision_required: true | false
```

`mode: none` adds no Review Evidence requirement. Other modes require typed
Review Evidence and exact Candidate and acceptance binding. Provider, model,
thinking, retry counters, and Review Profile names remain Runtime facts. When
`human_decision_required` is true, the Compiler also emits the appropriate
Decision Gate and typed dependency at the repository-policy boundary; it never
models the human as a Reviewer Agent.

The Plan Revision digest covers the canonical PlanSpec and parent digest. The
Compilation Record retains source references, source digests, and edge
provenance without becoming a second execution graph.

The Coordinator declares difficulty using the repository rubric. The Compiler
may raise but never lower it when deterministic facts such as concurrency,
migration, cross-module effects, or ambiguous root cause require more
capability. V8.0 uses no model call to classify difficulty.

## Runtime Policy and profiles

Worker Tier is a logical capability level, not a model identity:

```text
light < standard < heavy < frontier
```

The initial bindings are:

| Binding | Provider/model | Thinking | Mode |
| --- | --- | --- | --- |
| Worker light | Kimi CLI `kimi-code/kimi-for-coding` | high | yolo |
| Worker standard | Kimi CLI `kimi-code/kimi-for-coding` | max | yolo |
| Worker heavy | Kimi CLI `kimi-code/k3` | high | yolo |
| Worker frontier | Codex `gpt-5.6-sol` | xhigh | full-access |
| Auto-created Coordinator | Kimi CLI `kimi-code/k3` | max | yolo |
| Standard review axis | Codex `gpt-5.6-sol` | high | full-access |
| Recovery/strict review | Codex `gpt-5.6-sol` | max | full-access |

Codex fast mode is disabled by default.

Worker routing takes the highest required level:

```text
max(difficulty base, risk floor, role floor, recovery floor)
```

The initial policy is:

| Difficulty | Low risk | Standard risk | Strict risk |
| --- | --- | --- | --- |
| routine | light | standard | heavy |
| standard | standard | standard | heavy |
| complex | heavy | heavy | heavy |
| frontier | frontier | frontier | frontier |

The Coordinator profile is a Role Binding, while review-axis profiles are
selectors for Internal Subagents rather than managed roles or extra Worker
tiers. Manual Coordinators retain their actual runtime. Only a missing or
irrecoverable Coordinator is auto-created from the configured role profile.

One primary profile may name at most one explicit same-or-higher fallback.
Fallback occurs only before a new Admission while the primary operation
circuit is open; it is recorded and never changes an active Attempt.

Routing Evidence records the inputs, selected profile digest, outcome,
candidate and Repair counts, and available usage data. V8.0 does not learn or
change routing from those observations automatically.

### Internal subagent delegation

Worker and Coordinator parents may delegate bounded work to Internal
Subagents. GWO manages only the parent: an Internal Subagent receives no Plan
Node, Admission, Attempt, Role Binding, or GWO Active Turn Slot. Configured 8/1
capacity therefore counts top-level managed parents only; native Runtime and
provider limits still apply to internal delegation.

Internal Subagents cannot exceed the parent's Effect Contract. They may assist
with analysis, tests, or scoped workspace changes, but only the parent may
author authoritative lifecycle transitions or Result Claims. Review-axis
children are read-only, cannot delegate further, and are observed by the
Runtime Adapter; the parent may only assemble their separate observations into
Review Evidence without suppressing, merging, or reranking findings. Child
failure remains internal to the parent execution and does not consume a
semantic Attempt or Recovery Ladder step.

## Plan and execution lifecycle

### Plan lifecycle

```text
Plan Intent
  -> CompiledPlan: canonical PlanSpec + digest
  -> immutable GitHub Plan record
  -> durable Activation Receipt
  -> Store-finalized active Plan Revision
  -> new Admissions permitted
```

One repository has one active Plan Revision. A new revision affects only new
Admissions. Existing Admissions and Attempts remain pinned until explicitly
reconciled or superseded.

### Replanning and Result Adoption

A changed Goal, acceptance condition, or executable plan places only the
affected Goal on Replan Hold for new Admissions. Existing Attempts continue
under their original contracts unless an authorized revision explicitly
supersedes them.

A newer Plan Revision may adopt an existing verified Result only when the Node
Key and Node contract digest are unchanged. Adoption references the historical
Result; it never copies the Result or rebinds its Attempt. Base-sensitive
Evidence is refreshed when required.

A replacement node never runs beside a non-terminal predecessor or ambiguous
Admission for the same work. Deliberate parallel exploration uses distinct
Plan Nodes rather than hidden replacement concurrency.

### Admission and Materialization

```text
ready Plan Node
  -> one Store transaction rechecks and reserves every claim
  -> Admission
  -> idempotent Materialization and readback
  -> Runtime Binding + Prompt acceptance confirmed
  -> Attempt begins and claims transfer atomically
```

The Admission ID is the idempotency root. One unchanged Materialization action
is executed at most three times across reconciliation passes. Every retry
performs authoritative readback first. A safe configured alternate profile may
be selected only for a new Admission; otherwise exhausted Materialization is
blocked. Ambiguity retains claims and forbids blind replacement.

Runtime circuits are keyed by Adapter, operation, and normalized failure class.
Permanent authentication, configuration, or certificate rejection opens on
the first occurrence. A transient class opens after two consecutive equivalent
failures. Only one probe is allowed while open; unrelated operations and
providers remain usable.

The installed optional Skill is resolved when the Admission compiles its
initial Prompt. Delivery retries reuse the same Prompt snapshot. A later
Admission reads the current Skill. Missing Skill warns and falls back to the
base Prompt; Skill text is neither authority nor lifecycle identity.

### Attempt, candidate, and recovery

An Agent may edit and run tests repeatedly inside one turn. These are not
Candidate submissions or Repair Rounds.

An immutable local commit becomes a Candidate only when the Attempt submits a
Result Claim. Verification rejection permits one formal Repair Round in the
same runtime binding. A second rejected Candidate ends the Attempt with reason
`rejected`.

Attempt terminal reasons are explicit:

- `rejected`: the authorized Candidate and Repair Round were rejected;
- `no_result`: a healthy execution ended without a Result Claim;
- `runtime_lost`: the accepted runtime became irrecoverable;
- `superseded`: an authorized replan ended obsolete execution.

`rejected` and `no_result` consume the semantic Recovery Ladder. The ladder is
one primary Attempt followed directly by one fresh `worker_frontier` Attempt;
each receives at most one Repair Round. An initial frontier Attempt is followed
by a fresh frontier session rather than another tier.

Repeated `runtime_lost` is `Blocked(RuntimeUnavailable)`, not semantic node
failure. A Plan Node becomes failed only after two semantic Attempts are
exhausted under a valid and reachable acceptance contract. Plan Node failure
wakes the Coordinator for replan and does not fail the Goal automatically.

A diagnostic second opinion, when evidence is genuinely contradictory, is an
ordinary read-only work Plan Node created by the Coordinator. V8 has no Advisor
entity or automatic Advisor stage.

## Goal continuation

A Task Group Goal outlives Coordinator turns and Plan Revisions. Kernel
completion requires every in-scope Work Item and required review, decision,
Evidence, and Integration condition.

Every Coordinator turn must produce a durable outcome:

- Plan Revision or executable work;
- Wait Condition;
- Blocked Goal or Decision Gate;
- repair, replan, or Integration decision;
- completion proposal.

The Store records only a compact turn observation: Goal, semantic-input digest,
Coordinator session, outcome kind, referenced durable facts, and whether
recovery was used.

The semantic-input digest covers the active plan, Goal and Work Item semantics,
relevant node and Evidence states, decision inputs, capability configuration,
acceptance, and base identity. It excludes clock time, heartbeat, token and
tool counts, CI progress noise, liveness, and log growth.

If one turn produces no durable outcome for unchanged semantic input, the Goal
Driver resumes the same session once with a compact corrective delta. A second
zero-outcome turn for that same digest creates an explicit Decision Gate rather
than sampling indefinitely or silently leaving an active Goal stranded. New
semantic input changes the digest and reactivates continuation.

Every non-human external wait names an event and a targeted `next_check_at`.
The backstop performs Kernel readback only and consumes no LLM token. A human
Decision Gate is Blocked, not a polling Wait Condition.

Time, Agent liveness, token consumption, and tool activity never prove
progress, failure, completion, or safe cancellation.

## Parallel execution

Each reconciliation pass is work-conserving. It admits every compatible ready
node until a real dependency, exclusive claim, configured pool, or observed
Runtime/provider limit is reached.

Initial bounded pools are:

- eight active Worker turns;
- one reserved Coordinator turn.

These pools count only top-level Agents managed by GWO. Internal Subagents
created by a parent are not separately admitted or counted. Each standard
Review has a fixed fan-out of two axes and strict Review may add one
specialist. A parent retains its Worker Active Turn Slot while its internal
review children execute, so eight Worker slots bound concurrent review fan-out;
actual Runtime and provider capacity supplies further backpressure without a
separate Reviewer pool. Diagnostic work uses Worker capacity. Kernel
verification, hosted CI, and deterministic Integration consume no Agent turn.

A parked Attempt may retain its Agent, session, workspace, and necessary
claims while releasing its Active Turn Slot. Capacity release wakes the Goal
Driver to refill the frontier.

Ordinary Write Scope overlap is advisory because worktrees are isolated. Hard
exclusion is limited to the same Node Key, a non-shareable Agent/session/
workspace, an explicit external resource, and the target Integration branch.
Integration remains serial under one repository lease.

## Evidence, review, and local-first publication

V8.0 Evidence kinds are runtime, candidate, check, review, integration, and
decision. Each carries a common envelope with type, subject, observer,
observation time, source reference, payload, and digest.

Repository-owned versioned check definitions map stable check IDs to local
commands, hosted names, definition digest, environment requirements, input
selector, base sensitivity, and risk.

The executable-code path is:

```text
internal edit and cheap affected-test loop
  -> immutable local commit + Result Claim
  -> one repository-equivalent full local suite
     || one exact-SHA local Review
  -> derived publication eligibility
  -> first push of that candidate
  -> exact-SHA hosted CI
  -> Verification and serial Integration
```

Publication eligibility is a predicate, not a lifecycle entity. It requires
one candidate SHA, all required valid local Check Evidence, required Review
Evidence, and no blocker.

The edit loop's latest cheap affected tests, lint, and type checks must pass and
their observed tree digest must match the immutable Candidate before it spends
Review tokens; they are not rerun merely to enter Review. The one full local
suite and Review may then start concurrently while the Candidate-producing
parent is parked from further editing. Review consumes valid Check Evidence and
never reruns it. When the repository-equivalent suite is decomposable, it also
consumes already valid check components instead of repeating them; a monolithic
authoritative command runs only once. The Runtime Adapter may observe local
checks directly; otherwise the Coordinator or Kernel runs the command. A local
result becomes final Check Evidence only when its exact candidate, definition,
environment, input projection, outcome, observer, and log digest or reference
can be proven.

Each valid Review axis is stored locally as soon as it finishes, so recovery
can retain it for the same Candidate. Compact successful Check and Review
records become GitHub-durable after publication without rerunning their work.
Full raw logs are retained only for failures, strict risk, nondeterminism,
contract requirements, or expensive results that cannot be reproduced.

Cross-SHA check reuse requires equivalent definition, environment, declared
input projection, base, acceptance, observer, and durable record. Path overlap
alone is insufficient.

Hosted CI begins only after the first eligible push. A classified runner,
network, rate, TLS, registry, or platform failure may retry the same SHA twice
after the initial run. Candidate, test, lint, or build failure never
auto-retries remotely; it returns to the local implementation loop.

Review policy is deterministic:

- low risk: objective allowlist and required checks, with no LLM Review;
- standard risk: the Candidate-producing Work Attempt invokes `code-review`
  and runs read-only Standards and Spec Internal Subagents in parallel;
- strict risk: the standard axes plus the configured specialist or human.

The Compiler may add review requirements but no planner or Agent may remove
them. The Runtime Adapter captures each review child's history-free fixed
input, provider, model, session, output, and digests. Valid axis observations
are stored separately and the parent mechanically assembles one typed Review
Evidence envelope; it cannot merge, suppress, or rerank findings. A hard
Standards or Spec finding rejects the implementation Candidate, while smells
and other judgment calls are advisory unless repository policy promotes them.
Axis Internal Subagents cannot mutate the repository, publish, change tracker
state, integrate, or delegate further.

The GWO binding reuses `code-review`'s fixed-point, Standards, Spec, and
no-reranking guidance, but each axis emits its own schema-valid observation
directly. The minimal axis payload contains the axis, fixed-input digest,
runtime source reference, output digest, and findings labelled hard or
advisory with source and location. Human-readable rendering is a view of those
records; the parent never parses prose into authority.

Only an invalid or absent axis invokes same-Candidate recovery. A valid axis is
retained while only the missing axis is rerun once in a fresh Sol Max session.
Transient pre-ID or transport failure receives the initial execution and at
most two retries without consuming an Attempt or Repair Round; deterministic
configuration rejection blocks immediately. Spawn settings are validated
before dispatch. Each child action key derives from Attempt ID, Candidate SHA,
axis, and recovery ordinal, so readback-first retry adopts a matching child
instead of creating a duplicate. No running child record exists until Agent
identity is read back.

A changed Candidate SHA or diff invalidates both axes. Each axis may receive
the prior findings and old-to-new delta to reduce prompt size, but it remains
able to inspect the new complete diff. V8.0 does not add cross-SHA
unaffected-axis proof. Both axes share the parked clean exact-SHA worktree;
HEAD and cleanliness are verified before and after Review.

Serial Integration consumes the compiled Review requirement and the exact
Review Evidence directly; it never invokes another Review merely because it is
a different consumer. A clean application may reuse the evidence when its
candidate, diff, acceptance, and base-sensitive inputs remain valid. Rebase,
conflict resolution, or any other diff change requires a new Review Gate.

The Review Profile selectors map standard axes to Sol High and recovery or
strict specialists to Sol Max. Missing canonical Spec input fails compilation
or creates a Decision Gate rather than silently skipping the Spec axis.
The parent submits bounded axis requests through the Runtime Adapter, which
resolves the selector and may create a cross-provider Paseo child; a Kimi
Worker therefore does not need a provider-native Codex subagent facility.

The low-risk allowlist is deterministic and excludes production code,
workflows, dependencies, schema, authorization, concurrency, public
interfaces, and other contract-affecting changes.

## Token control

Token conservation comes from workflow structure, not from timing out healthy
work:

- same-session Repair receives only the new findings delta;
- review axes receive a fixed packet rather than the parent transcript, and a
  changed Candidate receives prior findings plus the compact SHA delta;
- a fresh frontier Attempt receives a structured Recovery Packet, normally no
  more than 16k input tokens;
- the packet contains Goal and acceptance summary, exact SHAs, changed files,
  check excerpts, Review blockers, attempted approaches, and durable
  references—not complete transcripts or logs;
- Coordinator continuation uses semantic snapshots and deltas;
- external waits consume no LLM turns;
- valid Check and Review Evidence is consumed, not regenerated.

The 16k limit constrains injected history only. It does not stop the Agent from
reading the repository or performing legitimate work.

## Durability and recovery

Store reconstruction combines:

- GitHub Plan records, Activation Receipts, writer generation, Goals, Work
  Items, decisions, and Evidence Manifests;
- Runtime identity, Prompt, lifecycle, and binding readback;
- Git commits and worktree state;
- hosted-check state.

Unique matches are adopted. Ambiguity freezes only the affected node and
cannot authorize duplicate work.

The writer generation is a GitHub durable fact. Privileged V8 mutations require
the active generation. A replaced Coordinator receives the current semantic
Goal snapshot under a new `coordinator_epoch`; late writes carrying an older
epoch are rejected.

Kernel-only privileged mutations include Plan publication and activation,
writer cutover, Admission and claims, Attempt replacement or supersession,
terminal node and Goal state, target-branch Integration, authoritative
Evidence/Result acceptance, and destructive Runtime retirement. Workers may
edit and commit locally, produce Artifacts and Result Claims, run checks, and
request first candidate publication under their Effect Contract.

## V6.1 transition

V7 is not a migration source.

Cutover performs:

1. stop V6.1 drivers, schedules, and heartbeats;
2. read back no active V6.1 execution or Integration lease;
3. retire or explicitly retain owned Runtime resources;
4. publish and read back the V8 writer generation;
5. create a fresh V8 Store;
6. activate the initial native V8 Plan Revision;
7. run the dedicated low-risk canary before opening full capacity.

Only durable GitHub, Git, Runtime, and CI facts are reused. V6.1 Store rows,
Dispatch identities, and Attempt identities are not imported. Unfinished
business is recompiled into native V8 Plan Nodes.

Shadow acceptance uses real repository snapshots for idle state, a ready
frontier, CI waiting, and Integration conflict. It has zero mutation and is
measured by acceptance coverage rather than elapsed time.

The first live canary uses a dedicated lightweight repository with three to
five independent work nodes and roughly two-minute hosted CI. It proves
parallel Admission, parking, refill, review, and serial Integration before the
configured eight-Worker capacity opens.

Rollback never deletes a durable Activation Receipt. Before execution it may
abandon only the new Store generation through a new durable compensating
record. After execution begins, it stops new Admissions and reconciles all
claims and Runtime resources before another writer generation starts.

## Cross-cutting invariants

- The Plan Compiler is the only owner of canonical PlanSpec bytes and digest.
- A durable GitHub Activation Receipt precedes Store-finalized activation and
  every Admission.
- One repository has one active Plan Revision and one writer generation.
- One Node Key has at most one non-terminal Admission or Attempt.
- One Agent has at most one active Attempt.
- Review is a Candidate output-contract gate and typed Evidence kind, never a
  Plan Node, Admission, Attempt, or Result.
- Internal Subagents have no independent Plan Node, Admission, Attempt, Role
  Binding, or GWO capacity slot.
- Review-axis observations are Runtime-Adapter-observed, kept separate, and
  cannot be suppressed or reranked by their parent.
- An Attempt begins only after Runtime Binding and Prompt acceptance readback.
- Materialization is idempotent, read back before retry, and does not consume
  an Attempt before Prompt acceptance.
- Worker Result Claims do not self-authenticate Evidence.
- A Plan Node accepts at most one Result.
- Valid Evidence is reused rather than rerun by role.
- Target-branch Integration is serialized.
- Time passage alone never proves failure, completion, or safe cleanup.
- A ready compatible frontier fills bounded configured and observed capacity
  in one pass.

## Implementation choices intentionally left open

- exact Python package and private file layout inside the five modules;
- exact installed-Skill discovery and logical-name configuration behind the
  locked workflow-command versus execution-guidance distinction;
- exact Paseo typed-action algebra behind the current seam;
- SQLite table and index layout;
- compact GitHub record encoding beyond the locked receipt fields;
- host-specific Goal Driver integration details;
- exact fixture implementation inside the dedicated canary repository.
