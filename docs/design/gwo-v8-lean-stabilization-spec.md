# GWO V8 lean stabilization successor specification

Status: accepted requirement source. External `/to-tickets` published the
approved successor graph as GitHub Issues #108–#119 on 2026-07-27 and read back
its native blocker relations. This document defines the required V8 outcome;
it does not by itself authorize further tracker, branch, pull-request, or
production writer changes.

The target architecture is
[`gwo-v8-lean-architecture.md`](gwo-v8-lean-architecture.md). The accepted
landing constraints and historical Issue disposition are in
[`gwo-v8-lean-roadmap.md`](gwo-v8-lean-roadmap.md).

## Purpose

Replace the current production-shaped V8 design with a small concurrent
execution engine that:

- uses LLMs for semantic planning, implementation, bounded diagnosis, and
  required Formal Review;
- uses deterministic code for scheduling, persistence, recovery, verification
  orchestration, repository delivery, and continuation;
- separates Coordinator, Worker, Recovery Worker, and Reviewer Runtime
  assignment so inexpensive or different CLIs can perform suitable roles;
- keeps four independent Tickets moving concurrently by default;
- shares one pull-request and hosted-CI boundary across compatible accepted
  Candidates; and
- makes the framework cheaper and more reliable than manually coordinating
  the same work.

V8 is unsuccessful if normal execution requires a resident Coordinator,
repeated whole-context handoffs, repeated Review of an unchanged Candidate, or
manual repair of its control-plane records.

## Why the predecessor plan is replaced

The predecessor architecture distributes one workflow across Plan Nodes,
GoalDriver, Kernel reconciliation, Review axes, Worker tiers, and delivery
controllers. Mechanical transitions frequently return to an LLM. Formal
Review can be initiated or partially represented in more than one place, while
findings and context can be truncated. Per-Ticket delivery boundaries multiply
pull requests and hosted CI.

Those properties increase communication and token use without improving
semantic coverage. The stabilization is therefore a contract replacement, not
another patch series on rejected Candidate SHAs.

## Work-item and configuration inputs

The external `/to-tickets` skill remains the Ticket adapter. It converts an
approved requirement or Issue into internal work-item Tickets represented by
GitHub Issues and publishes their dependency relations. GWO consumes selected
ready Ticket references; it does not replace `/to-tickets` or maintain a second
Ticket-authoring protocol.

Ticket contracts express required behavior and acceptance. They do not need
`risk`, `difficulty`, model, provider, CLI, or predicted-file fields. Missing
values for those fields are not inferred because V8 does not route on them.

Runtime assignment is explicit configuration with this precedence:

1. exact Ticket override supplied at Campaign start;
2. repository role configuration; and
3. host-global role configuration.

Coordinator, Worker, Recovery Worker, Review `primary`, Review `strong`, and
specialists are independent roles. Profiles may intentionally be identical.
PlanSpec never embeds the resolved profiles.

## Required external contract

The orchestration surface is exactly:

```text
start(repository, ready_refs, options?) -> CampaignHandle
advance(campaign_handle, wake_ref?) -> Running | Wait | Decision | Complete | Blocked
inspect(campaign_handle) -> Diagnostics
```

`options` may provide exact Ticket-to-Runtime-Profile overrides. Internal
records, adapters, lifecycle steps, and vendor commands must not become
additional public workflow APIs.

## Required ownership model

Implementation must converge on five deep modules:

| Owner | Sole responsibility |
| --- | --- |
| PlanControl | Ready Ticket readback, one Campaign Planning Pass, PlanSpec v3 compilation, publication, activation, and readback |
| ExecutionKernel | Persisted state transitions, capacity, budgets, waits, decisions, and next-action selection |
| RuntimeGateway | Role-profile resolution, all CLI/Runtime operations, identity, permissions, transport, fallback, recovery, and retirement |
| CandidateGate | Candidate audit, affected deterministic Checks, Assurance, Formal Review, Finding reconciliation, and Repair |
| BatchIntegrator | Compatibility, composition, exact verification, PR, hosted CI, serial target integration, and delivery recovery |

Campaign Watchdog is a non-LLM event-and-timer wake adapter to
`ExecutionKernel.advance`, not a sixth domain module. Git, GitHub, filesystem,
CI, Paseo, and other CLI integrations are private adapters inside the module
that owns their policy.

An implementation may retain private storage records while migrating. It must
not expose forwarding services or competing state machines merely to preserve
the old shape.

## Plan and planning requirements

One complete selected Ready Set produces one immutable Plan Revision. Before
publication, one bounded Coordinator Planning Pass may emit only:

- admitted Ticket work;
- justified dependency additions;
- genuine Exclusive Resources;
- factual Runtime capability requirements; and
- unresolved Decision findings.

The deterministic compiler remains authoritative. The Coordinator cannot
rewrite Ticket acceptance, add scope, grant authority, select models, predict
files, or prescribe Worker steps. Invalid output is rejected. Retrying
compilation or publication reuses the same validated Plan Intent and never
causes a second Planning Pass.

Planning input has an explicit byte budget. Oversized input returns a typed
split-Campaign Decision; it is never silently truncated or expanded into an
automatic multi-call planning tree.

PlanSpec v3 is a Runtime-neutral Ticket Manifest. It contains repository and
target identity, Campaign and policy identity, complete frozen Ticket
contracts, dependencies, genuine Exclusive Resources, and capabilities. It
contains no generic Agent DAG, lifecycle nodes, Checks, Review instructions,
recovery ladders, risk, difficulty, models, Runtime bindings, capacity,
timeouts, permission decisions, integration nodes, or predicted paths.

## Scheduling and continuation requirements

Each Campaign has four Worker Slots by default, configurable at host-global
level with repository override. Review Internal Subagents do not consume a
GWO Worker Slot. The Campaign has one fixed Coordinator semantic-control
capacity; it is not a general scheduling Slot.

Admission is optimistic. Only an unsatisfied dependency, a genuine Exclusive
Resource, the Campaign Slot limit, or observed Runtime unavailability may
block a ready Work Run. Predicted same-file overlap is not a hard blocker
because Workers use isolated workspaces. Actual Candidate diffs later derive
protected surfaces and Interaction Keys for delivery compatibility.

A Work Run retains its Slot through deterministic Checks, Formal Review, and
immediate Repair. An accepted Candidate waiting for delivery or a proven
parked wait releases the Slot. Normal start, refill, waiting, callback
continuation, Review completion, batching, CI observation, integration, and
cleanup invoke no Coordinator.

Every persisted state has a deterministic next action or terminal outcome.
Every external effect has stable action identity and authoritative readback
before retry. No local transaction remains open during external I/O.

## Runtime, permission, and recovery requirements

All semantic Runtime activity passes through RuntimeGateway. No caller knows
provider names or constructs vendor-specific commands. Prompt-file transport
or an equivalent bounded transport must preserve complete contracts and
findings without relying on a short command-line argument.

Availability fallback is allowed only before any Agent identity may exist.
After identity, recovery remains on the same binding. A replacement Worker is
allowed only after a Terminal Binding Receipt proves the old binding cannot
continue, and replacement consumes the second Worker Attempt.

RuntimeGateway may automatically approve one exact structured permission
request only when frozen authority and repository policy fully cover it.
Blanket approval and authority expansion are prohibited. An unmatched request
retains its Worker Slot for a three-minute Interactive Wait Grace. A
Coordinator may propose a lower-authority alternative but cannot approve
higher authority. After grace, the Slot is released only after the binding is
proven parked; later authorization resumes the same binding when capacity is
available.

Campaign Watchdog consumes trusted Runtime and hosted-check events and
persisted `next_check_at` timers. Events are wake hints, so restart reconstructs
due work from durable state. After thirty minutes without trusted progress,
zero-LLM Runtime and workspace readback occurs first. Only genuinely ambiguous
state may receive one Coordinator stale diagnosis for the entire Worker
Attempt. Periodic LLM monitoring and automatic Paseo daemon restart are
prohibited.

## Candidate and Review requirements

CandidateGate is the only Formal Review entry. Its order is:

1. read back one immutable base and Candidate;
2. load the complete diff and audit scope and protected effects;
3. derive and run affected deterministic Checks;
4. derive Assurance from policy and observed change;
5. run only the required Formal Review Internal Subagent;
6. reconcile the complete Finding ledger; and
7. return Accepted, Repair, Decision, or Wait.

Deterministic failure stops before LLM Review. Worker self-checks are useful
implementation activity but are not Formal Review Evidence. Neither Workers
nor BatchIntegrator may launch Formal Review. An external `code-review` skill
may provide review heuristics, but V8 owns subject completeness, coverage,
typed output, transport, identity, budgets, and lifecycle.

Standard Assurance uses one fresh complete `primary` observation. Strict
Assurance adds at most one required specialist or human Decision. A complete
no-Review allowlist match may use zero Reviewer calls. An invalid or incomplete
review payload may retry once through `strong`; a valid rejection is not
re-reviewed against the unchanged Candidate.

A Review Subject binds exact base, Candidate SHA, Ticket contract, standards,
Check Evidence, Assurance, and protocol version. Changed code creates a new
subject and one fresh complete Review. Every prior Finding must receive a typed
disposition. Approval never crosses Candidate SHAs, and findings or repair
context are never silently truncated.

One WorkSpec within one Plan Revision permits at most two Worker Attempts and
three changed Candidate submissions. Switching Worker does not reset either
budget. A retry caused only by invalid Review transport does not consume a
Candidate submission.

## Batch and integration requirements

When the repository Integration Lease is free, BatchIntegrator immediately
freezes up to four oldest currently compatible accepted Candidates. It does
not wait for running Workers, use a timer to grow the Batch, predict
completion, or call an LLM. High-coupling or protected work defaults to a
Singleton Batch.

The same immutable composed Batch SHA must:

1. pass the repository-equivalent exact local suite;
2. cross one push and pull-request boundary;
3. be observed by hosted CI; and
4. be integrated serially and read back from the target branch.

Infrastructure failure may retry the unchanged SHA at most twice. A
composition, exact-local, or code-class hosted failure may dissolve a
multi-Candidate Batch once into Singleton Batches. Recursive bisection and LLM
fault attribution are prohibited. Only a failing Singleton resumes its Worker;
changed code returns through CandidateGate. Unaffected implementation and
unchanged Formal Review Evidence are not repeated.

An unchanged Candidate may advance to a clean newer target only when
deterministic ancestry, patch identity, interaction, and exact-verification
conditions all hold. Otherwise it must be recomposed and verified as a new
delivery fact; Review approval is never silently transferred to changed code.

## LLM-call bounds

The healthy first-pass path contains:

- one Coordinator Planning Pass for the Plan Revision;
- one Worker context for each Ticket;
- zero or one standard Reviewer, with at most one additional strict specialist;
  and
- zero LLM calls for scheduling, waiting, Runtime readback, permission
  matching, deterministic Checks, batching, PR/CI observation, integration,
  and cleanup.

Repair continues the same Worker binding. Coordinator calls outside Planning
are exceptional and typed: a scope or contract Decision, one lower-authority
alternative, or one stale diagnosis per Attempt. There is no resident
Coordinator and no Batch-level Reviewer.

## Migration and cutover

Implementation starts from a fresh current `origin/main` base after the
contract documentation is landed. It must not continue from the closed Issue
#54 branch or reuse a rejected historical Candidate SHA.

New Campaigns write only PlanSpec v3. A V2 decoder may remain temporarily for
read-only completion of terminal or proven-quiescent historical work. No V2
state is reinterpreted as V3, and temporary V2-to-V3 projections must be
deleted before Canary.

Cutover uses one fail-closed, read-only Guard and then one real root-repository
Canary Campaign with four independent Tickets. Passing the Canary makes V8 the
default for new Campaigns in this repository. Downstream repositories retain
their current workflow until the root version is published.

The Canary must prove, without manual control-plane edits:

- one bounded Coordinator Planning Pass;
- four concurrent Workers with independently configurable Runtime Profiles;
- deterministic refill and continuation after a lost callback;
- exact permission handling and a parked interactive wait;
- deterministic Candidate checks before Formal Review;
- one standard Review and one strict/specialist path;
- bounded Repair and terminal Runtime replacement;
- one compatible multi-Candidate Batch, one PR, and one hosted-CI boundary;
- serialized target integration and exact readback; and
- Runtime and workspace retirement.

## Acceptance standard

Each implementation slice is accepted through the public or owning deep-module
interface, not private call ordering. Its tests must prove observable
transition, identity, idempotency, retry bound, and failure outcome where
relevant.

The completed stabilization must additionally prove:

- four independent Tickets can start in one Campaign and occupy four Slots;
- normal progress reaches Complete without a Coordinator continuation turn;
- no unchanged valid Review Subject is reviewed twice;
- a changed Candidate receives one complete Review and reconciles all earlier
  Findings;
- no deterministic Candidate failure consumes a Reviewer turn;
- four compatible accepted Candidates can share one exact Batch, PR, and CI;
- restart or duplicate callbacks cannot duplicate a semantic or external
  action; and
- an operator can explain any Wait, Decision, Repair, or Blocked result through
  `inspect()` without reading a model transcript.

Fixture changes, migration, cleanup, and interface-level tests belong to the
slice whose module owns them. A slice must redirect or delete the predecessor
caller path as part of delivering the replacement; it may not leave a
permanent parallel workflow.

## Historical tracker transition

The successor breakdown replaces the executable plan of Epic #82. The
following intent must be preserved when the tracker is later changed:

- replace #51 and #85–#87 with the unified Guard and Canary outcome;
- defer #69 beyond V8.0;
- replace #79 with terminal-binding recovery and bounded second Attempt;
- close #93 as superseded by explicit Runtime assignment;
- replace #94 and #99 with CandidateGate-derived checks;
- absorb #95 and #102 into their owning module slices;
- replace #98 and #101 with ExecutionKernel and Campaign Watchdog;
- preserve the useful invariants of #100 inside the owning deep modules;
- preserve #103 and #104 inside BatchIntegrator;
- preserve #105 inside RuntimeGateway and track a separate upstream Paseo
  blocker only if repository evidence proves one; and
- keep #35 independent unless it becomes a proven RuntimeGateway blocker.

No existing Issue is closed, relabeled, or rewritten until the replacement
breakdown is explicitly approved, published with native blocker relations, and
read back successfully.

## Deliberate exclusions

V8.0 does not include automatic risk or difficulty scoring, model evaluation,
price routing, learned scheduling, a general Agent DAG, a resident
Coordinator, periodic LLM monitoring, Formal Review as a top-level Task,
repeated Worker or Batch Review, per-Ticket PR/CI by default, recursive Batch
optimization, cross-SHA Review approval reuse, automatic authority expansion,
a permanent GWO daemon, or a long-lived shadow-execution phase.

## Instructions for external `/to-tickets`

Treat this document as the approved requirement source and the lean
architecture as its detailed contract. Before publishing anything:

1. explore the current implementation only enough to identify true
   prerequisites and migration seams;
2. propose numbered vertical tracer-bullet Tickets that each fit one fresh
   implementation context;
3. keep contract or storage prefactors minimal and attach their first consumer;
4. include interface-level tests, fixtures, cleanup, and old-path removal in
   the owning behavior Ticket rather than standalone cleanup Tickets;
5. use expand–migrate–contract only where a wide refactor cannot safely land as
   one vertical slice;
6. preserve this dependency skeleton: contract and PlanControl precede both
   ExecutionKernel and RuntimeGateway; those two may then proceed in parallel;
   CandidateGate requires their relevant interfaces; BatchIntegrator requires
   ExecutionKernel and CandidateGate; Cutover requires RuntimeGateway and
   BatchIntegrator;
7. do not assume each roadmap Delivery is exactly one Ticket, and do not create
   Tickets whose only output is an internal abstraction;
8. state observable acceptance and blockers without prescribing file paths,
   code snippets, model choices, or a Worker implementation plan; and
9. quiz the user on the numbered breakdown and blocker graph, revise it as
   needed, and wait for explicit publication approval.

After approval, publish in dependency order, apply the canonical
`ready-for-agent` handoff used by `/to-tickets`, set native blocker relations,
and read back every Issue. Tracker cleanup remains a separate, explicitly
authorized operation after that readback.
