# GWO V8 roadmap

Status: accepted direction, pre-implementation.

This roadmap starts from the production reality: V6.1 is the current writer
and V7 was never adopted. V8 does not complete or migrate V7. It introduces a
fresh writer generation and proves one usable vertical path before widening
the feature and failure surface.

Every phase has one observable end-to-end result. Acceptance uses the same
candidate and Evidence; a new role does not rerun a valid check merely because
it consumes the result.

## Phase 0 — Correct the contract and prepare the V6.1 base

Scope:

- Update `CONTEXT.md`, the V8 architecture, roadmap, and governing ADRs to
  remove the V7.1 freeze and V7 DAG migration path.
- Supersede the V7 transition ADRs without deleting their history.
- Record V6.1 as the operational source and define the writer-generation
  fencing and fresh-Store cutover contract.
- Create the implementation line from the production `dev`/V6.1 commit and
  port only the accepted V8 documents onto it. Do not merge the
  `design/gwo-v8` or `v7-integration` branch histories into the V8
  implementation line.
- Backport the fourth `frontier` tier and the minimal named Reviewer
  Role-Binding resolution to V6.1 in one narrow change: validation,
  configuration, resolution tests, and documentation only. The V6.1
  Coordinator remains the current manually created session.
- Keep the concrete profile configuration global with repository overrides;
  keep semantic risk, review, and check policy versioned with the repository.
- Record `ready-for-agent` as the Matt workflow handoff, keep `to-spec`
  optional when a canonical specification already exists, and exclude all
  other triage states from executable Plan Intent.
- Fix one V8 base commit after the V6.1 patch and documentation correction are
  reproducible.

Exit criteria:

- no active design document describes V7 as a required product lineage,
  compiler input, Store source, or shadow oracle;
- superseded and amended ADR relationships are explicit;
- V6.1 accepts `light`, `standard`, `heavy`, and `frontier` without changing
  existing running work;
- one canonical local validation command covers the Phase 0 change; no
  duplicate freeze audit or repeated full-suite role handoff remains.
- the tracker configuration maps the canonical `ready-for-agent` state and
  documents whether external PRs are an intake surface.

## Phase 1 — Single-node walking skeleton

Scope:

- Implement the smallest forms of the five deep modules:
  Plan Compiler, Plan Publication and Activation, Kernel, Runtime Adapter seam,
  and Evidence Verifier.
- Use one temporary Git repository, a real temporary SQLite Store, and the
  in-memory Runtime Adapter.
- Enter through a synthetic Ready Work Item source snapshot and execute one
  fixed scenario:

  ```text
  one ready-for-agent Work Item
    -> one Goal
    -> one work Plan Node
    -> one Admission
    -> one Attempt
    -> one candidate commit
    -> one local Check Evidence
    -> one verified Result
    -> one serial Integration
    -> Goal completed
  ```

- Implement only the happy path and the minimum activation protocol needed by
  that path.
- Test each deep module through its external interface. Replace overlapping
  shallow tests once the new interface test covers their behavior.

Exit criteria:

- the scenario completes through `reconcile_once` without tests reaching into
  SQLite tables or internal state handlers;
- canonical PlanSpec bytes and digest come only from the Plan Compiler;
- Plan Publication and Activation consume the compiled result rather than
  recanonicalizing it;
- a Worker assertion alone cannot create Evidence or complete the node;
- a non-ready Work Item cannot compile into executable work;
- workflow commands such as `implement` and `implement-gwo` cannot be bound
  recursively to a Plan Node;
- the whole test is deterministic, local, and fast enough for every candidate.

## Phase 2 — Real Paseo, atomic Admission, and Goal continuation

Scope:

- Implement the production Paseo Adapter for the capabilities V8.0 actually
  needs: idempotent create/adopt, identity round-trip, Prompt acceptance,
  lifecycle and turn readback, resume, safe interrupt, and read-backed
  retirement.
- Publish `/implement-gwo` as the V8 execution entry for one ready ticket, a
  parent Goal/spec, or an explicit ready set. Keep `/orchestrator` as a
  compatibility alias for this release only; leave Matt `/implement`
  unchanged.
- Keep the in-memory Adapter as a test fake, not as proof of a universal
  cross-runtime interface.
- Implement atomic Admission and idempotent Materialization.
- Retry one unchanged Materialization action at most three executions across
  reconciliation passes, always with readback before retry.
- Implement operation-and-failure-class Runtime circuits, backoff, and one
  single-flight probe.
- Compile the Prompt once per Admission, resolve the then-current optional
  Skill, and reuse the Prompt snapshot for delivery retries.
- Add the host `/goal`-like Goal Driver, semantic-input digest, durable
  Coordinator outcomes, Wait Conditions, and Decision Gates.
- Prefer a manually created Coordinator. Auto-create Kimi K3 Max only when no
  usable Coordinator exists or the prior Coordinator session is irrecoverable.

Exit criteria:

- create or delivery errors before Prompt acceptance do not consume an
  Attempt;
- ambiguous Runtime state protects the affected Admission and does not create
  a replacement;
- a Kernel restart adopts the same Admission and Runtime identity;
- an incomplete Goal with no active work or Wait Condition produces a
  Coordinator continuation directive;
- unchanged semantic input is not sampled indefinitely;
- external waiting consumes no LLM turn.
- an unready `/implement-gwo` input fails closed with the correct planning or
  triage next action and never falls back to plain `/implement`.

## Phase 3 — Local-first delivery, review, and bounded recovery

Scope:

- Add versioned repository check definitions and typed local and hosted Check
  Evidence.
- Require one repository-equivalent full local suite for executable code
  before publication, except checks explicitly marked hosted-only.
- Freeze an immutable local candidate only by Result Claim; internal edits and
  test runs do not count as Candidate submissions.
- Run the full local suite and local exact-SHA review concurrently after the
  candidate is immutable.
- Derive publication eligibility from one candidate SHA, valid required local
  checks, and blocker-free required review. Do not add another lifecycle state.
- Push an eligible candidate once, then run hosted CI once against that exact
  SHA. Retry only classified infrastructure failure, at most two retries after
  the initial run.
- Publish local Review and compact Check Evidence after the candidate becomes
  durable; do not repeat the analysis merely to publish it.
- Have one transient parent Reviewer invoke `code-review`, run read-only
  Standards and Spec Internal Subagents in parallel, and aggregate one typed
  Review Result with per-axis identity and digests.
- If one review axis is invalid or absent, retain the valid axis and rerun only
  the missing axis in a fresh Sol Max session.
- Implement one Repair Round in the primary Attempt and one fresh
  `worker_frontier` Attempt with one Repair Round.
- Use explicit Attempt terminal reasons: `rejected`, `no_result`,
  `runtime_lost`, and `superseded`. Only `rejected` and `no_result` consume the
  semantic Recovery Ladder.
- Treat a valid Reviewer verdict with blockers as a successful Review Result
  and a rejected implementation candidate, not a failed Reviewer.
- Implement serial Integration and exact target-branch readback.
- Implement Replan Hold, unchanged-contract Result Adoption, explicit
  supersession, and replacement exclusion.

Exit criteria:

- intermediate SHA values are never pushed merely to obtain feedback;
- a Reviewer or Coordinator consumes valid Check Evidence without rerunning
  the command;
- invalid or absent provenance causes a targeted rerun, not a role-based rerun;
- one invalid review axis recovers without repeating the other axis;
- hosted infrastructure failure remains the same candidate and cannot reject
  it;
- two exhausted semantic Attempts may fail the Plan Node, while repeated
  runtime loss blocks it as runtime unavailable;
- Plan Node failure wakes the Coordinator for replan and does not
  automatically fail the Goal.
- unchanged Node Key and contract digest adopt the historical verified Result
  without rebinding or rerunning its Attempt.

## Phase 4 — Parallel capacity, shadow, canary, and cutover

Scope:

- Admit the full compatible ready frontier in each reconciliation pass.
- Configure bounded Active Turn pools:
  eight Worker turns, four Reviewer turns, and one reserved Coordinator turn,
  further limited by observed provider and Runtime availability.
- Count only top-level GWO-managed parents in those pools. Internal Subagents
  delegated by Worker, Reviewer, or Coordinator parents receive no separate
  Admission or capacity slot.
- Release Active Turn capacity while Attempts are parked on named external
  waits; refill newly available capacity on the next pass.
- Keep ordinary Write Scope overlap advisory. Hard-exclude only the same Node
  Key, non-shareable runtime resources, explicit external resources, and the
  target Integration branch.
- Rebuild a fresh V8 Store generation from GitHub, Runtime, Git, and check
  readback.
- Run read-only shadow acceptance against real repository snapshots: idle
  repository, ready frontier, CI wait, and integration conflict.
- Use a dedicated lightweight GitHub canary repository whose hosted CI
  completes in roughly two minutes and whose three to five independent modules
  can safely exercise failure and conflict scenarios.
- Prove parallel Admission, CI parking, refill, review, and serial Integration
  with three to five canary nodes.
- Fence the V6.1 writer, publish and read back the durable V8 writer generation
  and activation receipt, then open configured capacity to eight Workers.

Exit criteria:

- one pass admits `min(compatible ready work, configured and observed
  capacity)`;
- Reviewer work is bounded without consuming the eight Worker counters;
- parked CI does not occupy an Agent turn;
- target-branch mutation remains singular;
- shadow mode performs no lifecycle, Runtime, or integration mutation;
- the canary passes contract, failure, concurrency, parking, refill, and
  rollback acceptance;
- V6.1 and V8 are never simultaneous writers;
- rollback is a new durable compensating action and never erases an activation
  receipt or rewrites V8 execution as V6.1 state.

## Acceptance ownership

Each phase uses the same ownership pattern:

- the implementation Worker produces one immutable candidate and Result Claim;
- Runtime or Kernel captures the required local Check Evidence once;
- an independent parent Reviewer reviews that exact candidate, may use internal
  axis Subagents, and consumes existing Check Evidence;
- the Coordinator evaluates the phase exit criteria from those records;
- hosted CI is the final external check after first publication, not the
  development loop.

There is no separate Committer, phase Test Runner, or phase Auditor that repeats
the same test. Failure injection is added only at a seam already exercised by
the vertical path.

## Suggested implementation issue sequence

1. Supersede the V7 transition decisions and update the normative documents.
2. Configure the tracker handoff and add the V6.1 fourth-tier Role-Binding
   backport.
3. Implement the ready-ticket-to-Integration in-memory walking skeleton.
4. Implement durable activation receipt and deterministic recovery.
5. Implement `/implement-gwo`, Paseo identity, Prompt acceptance, and
   Materialization readback.
6. Implement Goal Driver directives, Wait Conditions, and semantic-input
   continuation.
7. Implement local check definitions, candidate Evidence, and publication
   eligibility.
8. Implement transient review, hosted exact-SHA CI, and serial Integration.
9. Implement Repair Round, frontier recovery, and terminal-reason
   classification.
10. Implement bounded 8/4/1 capacity, parking, and refill.
11. Implement Store reconstruction, shadow fixtures, and writer fencing.
12. Run the dedicated canary and cut over from V6.1.

Issues may be split for reviewability, but acceptance stays aligned with these
vertical outcomes rather than internal tables or pass-through modules.

## Deferred after V8.0

- Semantic Planner.
- AI-driven dynamic routing.
- Self-learning from execution history.
- Direct Codex CLI and Claude Code Runtime Adapters.
- Critical-path or learned scheduling beyond work-conserving Admission.
- Backlog-based Admission throttling.
- Multi-host and hostile-host trust.
- Multi-primary Kernel operation.
- Automatic campaign invocation of `grill-with-docs`, `to-spec`, `to-tickets`,
  or `triage`.
