---
status: accepted
amends: ADR-0055, ADR-0056, ADR-0057, ADR-0058, ADR-0059
---

# Bound Campaign replanning on Plan Revision invalidation

V8 freezes one Plan Revision before Work Runs begin, but implementation, Formal
Review, verification of a repaired Candidate, or CandidateGate scope audit can
prove a fact the selected Ticket contracts and dependency graph did not
anticipate. That fact may be valid and necessary—a local catalog change may
expose a legitimate atomic-persistence obligation—but the reporting semantic
role sees only its own Ticket and workspace. It does not have the complete
Campaign Ticket graph, active Work Runs, canonical blockers, adjacent Ticket
ownership, or repository policy needed to decide the global route.

This ADR freezes the shared contract for bounded Campaign replanning before the
five implementation Tickets (#133–#137) deliver it. It changes no Runtime,
Campaign, repository writer, Candidate, or GitHub execution state. It amends
the five accepted ADRs governing PlanSpec v3, one Campaign Planning Pass per
Plan Revision, CandidateGate, ExecutionKernel, and RuntimeGateway by one
coherent decision record rather than silently reinterpreting them.

## The invalidation observation

A Worker, Formal Review, verification of a repaired Candidate, or CandidateGate
scope audit may submit one typed, digest-addressed observation that identifies
a newly discovered fact and explains why the active Plan Revision cannot safely
satisfy the affected Ticket as written. The observation is **Plan
Invalidation** Evidence, not a replacement plan and not authority to widen a
Candidate.

It binds the exact Campaign, active Plan Revision, Ticket, Work Run, Runtime
Binding, authority-subtree digest, reporter role, Evidence digest, and a stable
deduplication identity. It contains only:

- discovered facts;
- reproducible Evidence or a minimal reproduction;
- the invalidated Ticket obligation;
- newly required effects, interfaces, or state; and
- current workspace or Candidate identity.

It cannot carry an authoritative Issue ownership decision, dependency mutation,
Campaign order, or replacement PlanSpec.

## Component ownership

The contract assigns each transition to exactly one component.

### RuntimeGateway — authoritative observation readback

RuntimeGateway reads one typed, Artifact-backed report bound to the exact
Campaign, Plan Revision, Ticket, Work Run, stable semantic action, Runtime
Binding, authority-subtree digest, reporter role, and Evidence digest. It owns
effective capability-policy readback that proves the Worker and Coordinator
cannot create or edit Issues, change blockers, activate a Plan Revision, merge,
expand authority, or invoke global planning. Inability to prove that policy
fails closed. RuntimeGateway does not decide whether or how the Campaign is
replanned; it transports the observation and proves the capability boundary.

### ExecutionKernel — Work Run quiescence and public status

ExecutionKernel authoritatively reads and persists the observation under a
stable deduplication identity before changing Work Run state. It deduplicates
replay so one discovery cannot create repeated Coordinator work or successor
revisions. It quiesces the affected Work Run: no further Worker, Candidate,
Review, Repair, or delivery effect may occur under the invalidated revision. It
releases the Worker Slot only after the quiescent state is read back, so
unrelated eligible Tickets can continue while replanning waits. Workspace and
diagnostic Evidence remain attributable and read-only until disposition.

Unaffected Work Runs continue only when their Ticket contracts, dependencies,
claims, authority roots, and required shared facts remain valid under the active
revision. An invalidation bound to another Campaign, Plan Revision, Ticket, Work
Run, Runtime Binding, or authority digest cannot stop current work.

Status derivation remains the five existing values in their existing order. A
due Coordinator action contributes `Running`; an explicit product, Ticket,
Campaign-membership, or authority choice is `Decision`; tracker or
observable-event readback is `Wait`. Replanning introduces no sixth public
status.

### PlanControl — bounded Campaign snapshot and successor compilation

PlanControl constructs one bounded replanning snapshot from:

- the active Plan Revision;
- all approved Tickets within the Campaign source;
- their complete native blocker graph;
- active and terminal Work Runs and claims;
- accepted Results;
- all pending valid invalidation observations for that active revision;
- the Policy Witness; and
- any explicitly referenced external dependency.

An external Ticket may inform ownership or blocking analysis but is not silently
admitted to the Campaign. All pending valid observations for one active revision
are coalesced into one Planning Pass; a later observation against a successor
revision requires a new, independently bounded revision.

A fresh Coordinator performs exactly one Campaign Planning Pass over that
snapshot. The Coordinator output is typed and limited to:

- resume under the unchanged contract;
- defer a non-blocking concern;
- identify an approved existing-Ticket dependency, admission change, or
  ownership transfer;
- request a named human Decision; or
- reject invalid Evidence.

The Coordinator cannot rewrite acceptance, create Tickets, add arbitrary work,
change Campaign membership outside the approved snapshot, expand authority,
select Runtime identities, write repository content, or mutate GitHub. A
validated unchanged-contract or defer disposition resumes only after
ExecutionKernel reads it back and reacquires a Worker Slot; no successor Plan
Revision is invented when the frozen contract and authority remain sufficient.

When the discovery requires a new Ticket, changed acceptance, changed Campaign
membership, broader authority, or a product/release decision, PlanControl
returns a human Decision. The upstream ticketing workflow performs any approved
Ticket creation or contract change. Only after PlanControl reads back the
updated durable tracker state may it deterministically compile, publish,
activate, and read back a successor Plan Revision through the existing
compare-and-swap and readback path.

### CandidateGate — scope audit and Review entry routing

CandidateGate distinguishes an ordinary unauthorized Candidate change from
Evidence that the frozen Ticket itself cannot be satisfied safely; only the
latter enters Campaign replanning. A deterministic scope, protected-effect,
authority, or affected-Check audit that proves plan invalidation stops before
any Formal Review invocation and persists the same typed Evidence contract used
by the Worker path. A Formal Review Finding that proves a required persistent
protocol, deep-module owner, Campaign dependency, or authority lies outside the
frozen Ticket is preserved as Evidence and is not converted into an impossible
ordinary repair obligation. CandidateGate verification of a repaired Candidate
that discovers work outside the consolidated repair request's allowed scope
invalidates the repair lineage, emits the bounded Evidence, and cannot reopen a
full exploratory Formal Review.

## Worker and Reviewer role boundaries

Worker and Reviewer roles may report discovered facts and bounded alternatives
in the invalidation observation. They cannot:

- choose Ticket ownership;
- mutate tracker state;
- rewrite acceptance;
- expand authority;
- activate a Plan Revision;
- merge work; or
- invoke a global planning workflow.

Runtime capability readback must prove this restriction is effective; inability
to prove the policy fails closed.

## Legal dispositions

Each invalidation observation receives exactly one stable disposition:

1. **Reject invalid Evidence and resume** — the observation does not prove the
   Plan Revision is insufficient; the Work Run continues under the unchanged
   contract.
2. **Defer a non-blocking concern** — the discovery is real but not required for
   the frozen acceptance; it does not enter the Candidate or block completion.
3. **Use approved Campaign Tickets in a successor revision** — an approved
   existing Ticket owns the newly required behavior, or a Coordinator-justified
   dependency addition among already approved Tickets is valid; PlanControl
   compiles and activates a successor Plan Revision.
4. **Require a human-approved tracker or authority change** — the discovery
   changes product scope, Ticket acceptance, Campaign membership, or authority;
   GWO returns Decision, performs zero such mutations, and continues only after
   authoritative tracker readback.
5. **Stop after a finite replan budget** — repository policy bounds successor
   revisions and repeated invalidation of the same Ticket obligation;
   exhaustion returns Decision with complete lineage rather than launching
   another Coordinator pass.

## Lineage and adoption

Old Work Runs, workspaces, and Candidates remain diagnostic lineage only.
Separately produced Evidence may reference their exact identities, but neither a
workspace nor a Candidate becomes Evidence or a Result. They are never adopted,
submitted, reviewed, or integrated under a successor Plan Revision. Accepted
Results and unaffected exact Evidence survive only when their complete Ticket
contract, subject, dependencies, authority, policy, and target facts remain
identical and valid. Cross-Plan Candidate adoption remains outside this release
and stays distinct from #69.

## Budgets

Repository policy defines finite bounds:

- successor revisions per Campaign; and
- repeated invalidation of the same Ticket obligation.

Duplicate Evidence does not consume either bound. Exhausting either bound
returns one human Decision with the complete revision and invalidation lineage.

## Public interface

The behavior uses the existing public `start`, `advance`, and `inspect`
operations. No new public workflow operation is introduced. `advance` processes
the authoritative observation, the bounded Coordinator action, tracker
Decisions, and successor activation. `inspect` exposes the invalidated
assumption, Evidence identity, affected Work Run, Slot and claim state,
retained diagnostic identity, outstanding Decision or Wait, Coordinator action
identity, and active or successor Plan Revision without requiring a model
transcript.

The public statuses remain only Complete, Running, Decision, Wait, and Blocked.

## Amendment of governing ADRs

This ADR amends the five governing ADRs by adding the bounded replanning path as
their coherent composition, without redefining their existing decisions:

- **ADR-0055 (PlanSpec v3)**: a successor Plan Revision activated through the
  replanning path is a new immutable PlanSpec v3 compiled by PlanControl from
  the bounded snapshot; it cannot adopt old Candidate lineage and binds new Work
  Run and Evidence identities.
- **ADR-0056 (one Planning Pass per revision)**: the replanning Coordinator pass
  is the one Campaign Planning Pass for the successor revision; it is transient,
  receives the complete bounded snapshot, and cannot become a resident LLM loop.
- **ADR-0057 (CandidateGate)**: scope audit and Formal Review may produce the
  same invalidation observation; they do not issue a broader consolidated repair
  request.
- **ADR-0058 (ExecutionKernel)**: Work Run quiescence, Worker Slot release,
  idempotency, and five-status derivation own the replanning transition; no
  sixth status or driver is introduced.
- **ADR-0059 (RuntimeGateway)**: authoritative observation readback and
  capability-policy proof are RuntimeGateway responsibilities; it does not
  decide the replanning route.

## Relationship to existing deliveries

This contract does not expand the #110, #112, #114, or #115 deliveries. Those
Tickets integrate through the new seam where relevant:

- #133 (quiesce) extends #110 ExecutionKernel and #112 RuntimeGateway.
- #134 (classify) extends PlanControl.
- #135 (activate successor) extends #109 PlanControl activation.
- #136 (human gate) extends #112 authority Decisions and tracker readback.
- #137 (late escape) extends #114 CandidateGate and #115 Review/Repair.

#118 is additionally blocked by #136 and #137 so V8 cannot cut over before both
the successor/human-gate and late-discovery paths converge. #69 remains deferred
because this contract deliberately preserves old work only as diagnostic
evidence and does not adopt Candidate lineage across Plan Revisions.
