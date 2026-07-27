# GWO V8 lean stabilization successor specification

Status: accepted dated requirement, successor-Ticket source, and acceptance
record. External `/to-tickets` published Issues #108–#119 and their native
blockers on 2026-07-27.

This document is subordinate to
[`gwo-v8-lean-architecture.md`](gwo-v8-lean-architecture.md), the sole
integrated current V8 mechanics contract. `CONTEXT.md` owns language, accepted
ADRs own individual decisions, and
[`gwo-v8-lean-roadmap.md`](gwo-v8-lean-roadmap.md) owns sequencing and exit
criteria. This record does not authorize further tracker mutation, branch
publication, production writer change, or cutover.

## Requirement purpose

Replace the production-shaped predecessor design with a small concurrent
GitHub Ticket execution engine that:

- uses LLMs only for bounded semantic planning, implementation, diagnosis,
  repair, and required Formal Review;
- uses deterministic code for scheduling, persistence, recovery, verification
  orchestration, repository delivery, and continuation;
- assigns Runtime Profiles explicitly by semantic selector without putting
  provider, model, or CLI facts in PlanSpec;
- freezes provider-neutral Authority Grants with the accepted Ticket
  contracts;
- keeps four independent Work Runs moving concurrently by default;
- shares one exact pull-request and hosted-CI boundary across compatible
  same-Campaign Candidates; and
- makes the framework cheaper and more reliable than manual coordination.

V8 is unsuccessful if healthy progress requires a resident Coordinator,
repeated whole-context handoffs, repeated Review of an unchanged Review
Subject, or manual control-plane repair.

## Origin and inputs

The predecessor architecture distributed one workflow across generic graph
records, separate workflow-driver and reconciliation layers, multi-axis
Review, model tiers, and delivery controllers. Mechanical transitions
repeatedly returned to an LLM, Formal Review had overlapping entrypoints, and
delivery multiplied PR and CI boundaries. Stabilization therefore replaced
that contract rather than continuing rejected historical Candidate lineages.

The external `/to-tickets` skill remains the Ticket adapter. It converts an
approved requirement into independently executable GitHub Tickets and
publishes canonical blockers. GWO consumes selected Ticket references and
does not maintain a competing Ticket-authoring protocol.

Ticket contracts contain required behavior and acceptance. They do not need
provider, model, CLI, risk, difficulty, or predicted-file fields. Runtime
assignment is a Campaign-start and configuration input governed exclusively by
[`Runtime assignment`](gwo-v8-lean-architecture.md#runtime-assignment).
Campaign-start options cannot rewrite Ticket acceptance or expand frozen
authority.

## Required public result

The completed successor exposes only:

```text
start(repository, ready_refs, options?) -> CampaignHandle
advance(campaign_handle, wake_ref?) -> Running | Wait | Decision | Complete | Blocked
inspect(campaign_handle) -> Diagnostics
```

It converges on five owning deep modules: PlanControl, ExecutionKernel,
RuntimeGateway, CandidateGate, and BatchIntegrator. Campaign Watchdog remains a
non-LLM wake adapter.

The integrated definitions are intentionally not duplicated here:

| Requirement area | Current mechanics |
| --- | --- |
| Public types and five-status derivation | [`External API and status`](gwo-v8-lean-architecture.md#external-api-and-status) |
| Planning and private semantic output | [`PlanControl and Campaign planning`](gwo-v8-lean-architecture.md#plancontrol-and-campaign-planning) |
| Runtime-neutral PlanSpec and frozen authority | [`PlanSpec v3 and frozen authority`](gwo-v8-lean-architecture.md#planspec-v3-and-frozen-authority) |
| One active revision per Campaign and activation receipts | [`Campaign and Plan Revision activation`](gwo-v8-lean-architecture.md#campaign-and-plan-revision-activation) |
| Exact Runtime selectors and assignment persistence | [`Runtime assignment`](gwo-v8-lean-architecture.md#runtime-assignment) |
| Private provider-neutral Runtime adapter | [`RuntimeGateway adapter contract`](gwo-v8-lean-architecture.md#runtimegateway-adapter-contract) |
| Worker Slots and Work Run bounds | [`Worker Slots and Work Run bounds`](gwo-v8-lean-architecture.md#worker-slots-and-work-run-bounds) |
| Permissions, waits, stale diagnosis, and replacement | [`Runtime permissions, waits, and recovery`](gwo-v8-lean-architecture.md#runtime-permissions-waits-and-recovery) |
| Runtime failure outcomes and retry bounds | [`Runtime failure taxonomy`](gwo-v8-lean-architecture.md#runtime-failure-taxonomy) |
| Event wakes, due timers, and lost-callback recovery | [`Persistence and liveness`](gwo-v8-lean-architecture.md#persistence-and-liveness) |
| Candidate checks, Assurance, Review Findings, and repair | [`CandidateGate`](gwo-v8-lean-architecture.md#candidategate) |
| Authoritative Candidate diff identity | [`CandidateDiffRecordV1`](gwo-v8-lean-architecture.md#candidatediffrecordv1) |
| Same-Campaign Batch formation and exact delivery identity | [`BatchIntegrator`](gwo-v8-lean-architecture.md#batchintegrator) |
| Tree-delta identity and per-member Clean Base Advance | [`PatchIdentityV1 and Clean Base Advance`](gwo-v8-lean-architecture.md#patchidentityv1-and-clean-base-advance) |
| Terminal hosted-check restart adoption | [`Durable hosted result adoption`](gwo-v8-lean-architecture.md#durable-hosted-result-adoption) |
| Writer-generation migration boundary | [`Cutover`](gwo-v8-lean-architecture.md#cutover) |
| Fixed and configurable defaults | [`Defaults`](gwo-v8-lean-architecture.md#defaults) |

## Stabilization acceptance

The successor is accepted only when repository evidence proves:

- `start`, `advance`, and `inspect` are the only public workflow operations;
- `advance` and `inspect` agree on the deterministic five-status derivation;
- one Campaign can admit four independent Work Runs and refill released Worker
  Slots without Coordinator continuation;
- one active Plan Revision per Campaign is durably activated through the exact
  Campaign compare-and-swap and read-backed receipt;
- PlanSpec v3 freezes Ticket contracts, Policy Witness, and provider-neutral
  Authority Grants while remaining provider-, model-, CLI-, selector-, and
  fallback-neutral;
- all semantic Runtime activity uses the exact persisted selector assignment;
- production and deterministic in-memory Runtime adapters satisfy the same
  conformance suite, and no semantic action starts before full identity,
  staged-Prompt, lifecycle, permission, and fence observation;
- a Worker Candidate-reference report and Runtime notification are wake hints;
  restart, duplicate callbacks, and replay cannot adopt a Candidate or
  duplicate a semantic, receipt, or external action;
- only a Candidate receipt covering an exact commit/tree from CandidateGate's
  authoritative readback and a `CandidateDiffRecordV1` constructed and
  revalidated over the exact base and Candidate objects, then durably persisted
  by ExecutionKernel, may advance state or count as trusted Candidate liveness
  progress; raw reports, logs, workspace heads, and unread-back completion text
  do not;
- permission handling cannot exceed both the frozen Authority Grant and Policy
  Witness;
- an operation or resource beyond frozen authority, or any changed authority
  root, requires an explicitly recorded human Decision, deterministic
  Authority Grant recompilation, and a successor Plan Revision;
- Runtime failure recovery produces the canonical named outcomes and fixed
  retry bounds without post-identity provider switching, pre-Prompt semantic
  budget consumption, or daemon restart;
- for live provider unavailability after identity, authoritative observations
  one and two produce `Wait(RuntimeProviderUnavailable, next_check_at)` and
  observation three produces human
  `Decision(RuntimeProviderRecoveryRequired)` under the
  [`Runtime failure taxonomy`](gwo-v8-lean-architecture.md#runtime-failure-taxonomy);
  conformance and restart replay retain the exact `stable_action_id`, Runtime
  Binding, Profile, provider, CLI, Agent, session, workspace, accepted Prompt,
  and authority, count only unique live observations, and keep transport
  accounting independent;
- that episode releases no Slot or claim, consumes no semantic, Candidate, or
  replacement budget, and authorizes no fallback, new `prepare` or create,
  Profile/provider/CLI switch, daemon restart, or replacement; only
  authoritative same-binding readback or resume after provider recovery closes
  it, and only independent terminal-binding Evidence may enter the existing
  one-replacement path;
- deterministic Candidate failure consumes no Reviewer turn;
- CandidateGate persists one canonical `CandidateDiffRecordV1` and uses that
  identical Artifact for scope/authority audit, Checks, Assurance, protected
  surfaces, Interaction Keys, and Formal Review without substituting
  `PatchIdentityV1`;
- unchanged valid Review Evidence is never repeated;
- Review Evidence is reused only for an identical Review Subject digest with a
  readable, digest-revalidated diff Artifact; changed base, Candidate, diff
  schema/digest, or protocol creates a fresh subject and missing, truncated, or
  mismatched diff content fails before Reviewer invocation;
- a fresh Review Subject dispositions every earlier Review Finding;
- the limit of three distinct Candidate SHAs and initial plus at most one
  replacement binding survives repair and replacement;
- up to four compatible accepted Candidates in one Campaign can share one
  exact Batch, PR, and hosted-CI boundary;
- every Clean Base Advance member independently reproduces its original
  `PatchIdentityV1` when applied alone to the same advanced target before
  multi-member composition;
- local verification, PR head, and hosted CI name the same Batch SHA, while
  target readback proves that SHA reachable through GitHub's PR merge mapping;
- restart adopts a valid persisted terminal hosted-result receipt without a
  provider reread, while identity mismatch or ambiguous attribution preserves
  evidence and permits neither Singleton fallback nor Worker resume;
- V8 activation occurs only after all V2-to-V3 compatibility paths are absent
  or unreachable, never interprets or writes V2 state, and leaves V6.1
  authoritative after a failed Guard;
- restart and delivery recovery preserve Campaign and Work Run identity;
- a Candidate is neither Evidence nor a Result, and a code-producing Result
  exists only after the exact accepted Candidate is integrated and read back;
- an operator can explain any Wait, Decision, or Blocked result from
  `Diagnostics` without reading a model transcript.

Each implementation slice is accepted through the public API or its owning
deep-module interface, not private call ordering. Tests must prove observable
transition, identity, idempotency, retry bound, and failure outcome.

## Published successor Ticket record

The approved breakdown and native blocker graph were published and read back
before historical tracker cleanup:

| Ticket | Published contract | Blocked by |
| --- | --- | --- |
| #108 | Land the accepted execution contract | none |
| #109 | Start one immutable PlanSpec v3 Campaign | #108 |
| #110 | Advance four Work Runs without Coordinator continuation | #109 |
| #111 | Route semantic roles through one RuntimeGateway | #109 |
| #112 | Bound permission waits and terminal Runtime recovery | #111 |
| #113 | Resume Campaigns without LLM polling | #110, #112 |
| #114 | Accept standard Candidates through one CandidateGate | #110, #111 |
| #115 | Bound strict Review and Review Finding repair | #112, #114 |
| #116 | Deliver compatible Candidates through one exact Batch | #110, #114 |
| #117 | Recover Batch failures without repeating unaffected work | #115, #116 |
| #118 | Cut over new Campaigns through a fail-closed Guard | #113, #117 |
| #119 | Prove and enable V8 with a four-Ticket root Canary | #118 |

Issue #121 later established the dedicated self-hosted acceptance runner. It is
a repository-base prerequisite already merged into `main`, not a successor V8
mechanics Ticket.

The implementation and exit sequence for the published graph is maintained
only in
[`Published successor sequence`](gwo-v8-lean-roadmap.md#published-successor-sequence).

## Historical tracker transition

The published graph replaced the executable plan of Epic #82. The accepted
transition preserved this intent:

- replace #51 and #85–#87 with the unified Guard and Canary outcome;
- defer #69 beyond V8.0;
- replace #79 with terminal-binding Evidence and bounded replacement;
- close #93 as superseded by explicit Runtime assignment;
- replace #94 and #99 with CandidateGate-derived checks;
- absorb #95 and #102 into their owning implementation slices;
- replace #98 and #101 with ExecutionKernel and Campaign Watchdog;
- preserve the useful invariants of #100 inside owning deep modules;
- preserve #103 and #104 inside BatchIntegrator;
- preserve #105 inside RuntimeGateway, with a separate upstream Paseo blocker
  only if repository evidence proves one; and
- keep #35 independent unless it becomes a proven RuntimeGateway blocker.

Historical Issues retain their bodies, Candidate facts, Review Evidence, and
disposition comments for audit. They are not executable recovery instructions
for the successor.

## `/to-tickets` publication record

The following rules produced the approved Ticket graph:

1. inspect the predecessor only enough to identify true prerequisites and
   migration seams;
2. create vertical behavior Tickets that fit one fresh implementation context;
3. attach minimal contract or storage prefactors to their first consumer;
4. include fixtures, cleanup, old-path removal, and interface tests in the
   owning behavior Ticket;
5. use expand–migrate–contract only when one vertical slice cannot land safely;
6. place PlanControl before ExecutionKernel and RuntimeGateway, CandidateGate
   after their required interfaces, BatchIntegrator after ExecutionKernel and
   CandidateGate, and cutover after RuntimeGateway and BatchIntegrator;
7. do not make every roadmap stage exactly one Ticket and do not create a
   Ticket whose only output is an internal abstraction;
8. state observable acceptance and blockers without prescribing file paths,
   code snippets, model choices, or a Worker plan; and
9. obtain explicit approval before publication, publish in dependency order,
   set native blockers, and read back every Ticket.

Those instructions are retained as publication provenance. Future mechanics
changes must follow the normative hierarchy rather than recovering decisions
from this dated record or from chat history.
