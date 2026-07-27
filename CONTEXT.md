# GitHub Work Orchestration

This context turns selected GitHub Tickets into verified repository Results
through concurrent semantic work and deterministic control.

This file is normative only for ubiquitous language. Accepted ADRs own
individual decisions, and
[`docs/design/gwo-v8-lean-architecture.md`](docs/design/gwo-v8-lean-architecture.md)
is the integrated current V8 mechanics contract.

## Language

**Ticket**:
An independently executable behavioral contract produced by the upstream
ticketing workflow and stored as a GitHub Issue.
_Avoid_: raw Issue, generic graph node, task prompt

**Campaign**:
One execution created by `start(...)` over a selected set of Tickets, with one
stable identity and one active Plan Revision.
_Avoid_: generic objective record, coordinating Agent

**Plan Revision**:
An immutable, digest-addressed snapshot of a Campaign's selected Ticket
contracts, dependencies, capabilities, Authority Grants, and Policy Witness.
_Avoid_: mutable plan, Agent DAG, one plan per Ticket

**PlanSpec**:
The provider-, model-, and CLI-neutral Ticket Manifest serialized inside a
Plan Revision and serving as the root of its frozen semantic authority.
_Avoid_: execution snapshot, model assignment, lifecycle graph

**Work Run**:
The bounded execution lifecycle of one Ticket inside one Plan Revision,
including its Runtime bindings, Candidate submissions, waits, and outcome.
_Avoid_: generic graph node, unbounded execution record

**Candidate**:
An immutable repository Artifact submitted by a Work Run for verification.
Edits, diagnostics, and uncommitted workspace state are not Candidates.
_Avoid_: working tree, intermediate patch, Worker self-report

**Artifact**:
A produced object with stable identity, such as a Plan Revision, Candidate,
Review payload, or Integration Batch.
_Avoid_: Evidence, Result

**Result**:
The verified terminal outcome of one Ticket. A code-producing Result is not
complete until its exact Candidate is integrated and read back.
_Avoid_: Agent finished, PR opened, Candidate submitted

**Integration Batch**:
One immutable Campaign-scoped delivery aggregate of compatible accepted
Candidates that share an exact verification, pull-request, hosted-CI, and
target-integration boundary.
_Avoid_: wave, merge queue, Batch Agent

**Coordinator**:
The semantic role that performs one Campaign Planning Pass per Plan Revision
and handles explicit semantic Decisions.
_Avoid_: resident supervisor, workflow driver, repository writer

**Worker**:
The semantic role that implements one Ticket in an isolated workspace and
continues a consolidated repair request within Work Run bounds.
_Avoid_: Coordinator, Reviewer, integration writer

**Internal Subagent**:
A bounded internal semantic action with no separate Ticket, Work Run, or Worker
Slot. Review Internal Subagents are read-only, cannot delegate, and produce
Evidence rather than approval authority.
_Avoid_: managed top-level Agent, hidden Work Run

**Formal Review**:
An independent, Candidate-scoped semantic observation launched internally
after deterministic Candidate checks pass.
_Avoid_: Worker self-review, Batch review, external `code-review` lifecycle

**Review Subject**:
The immutable identity binding an exact base, Candidate, Ticket contract,
standards, Check Evidence, Assurance Requirement, Policy Witness, and protocol
version.
_Avoid_: mutable review conversation, pull-request review

**Evidence**:
A typed, digest-addressed observation bound to an exact subject, source, and
observer identity.
_Avoid_: claim, log text, reported pass

**Review Finding**:
A stable, Evidence-backed Formal Review observation that remains traceable
across changed Candidates until dispositioned.
_Avoid_: transient comment, truncated prompt item, approval

**Assurance Policy**:
The versioned repository policy that derives Candidate checks, Formal Review,
specialist observation, and human Decisions from the observed change.
_Avoid_: input risk score, difficulty tier, model selector

**Assurance Requirement**:
The durable, reasoned set of checks and observations derived by Assurance
Policy for one exact Candidate.
_Avoid_: risk label, Reviewer profile

**Wait**:
A non-terminal Campaign or Work Run state naming the observable event or due
time required for deterministic continuation.
_Avoid_: inactivity, silent stop, failure

**Decision**:
A durable semantic or human choice required before scope, authority,
acceptance meaning, or exhausted recovery may change.
_Avoid_: routine retry, permission timeout, chat-only approval

**Worker Slot**:
One Campaign-scoped lease for an actively executing Work Run.
_Avoid_: provider quota, Review Slot, Agent identity

**Exclusive Resource**:
A resource whose concurrent use is explicitly unsafe and therefore constrains
Work Run admission.
_Avoid_: ordinary file overlap, predicted write path, Runtime capacity

**Interaction Key**:
A deterministic fact derived from a complete Candidate diff record that names
a high-coupling semantic surface and constrains Batch compatibility.
_Avoid_: predicted file lock, universal path lock, Ticket dependency

**Integration Lease**:
The repository-global exclusive right to perform target delivery and
integration actions.
_Avoid_: repository-wide Worker lock, Campaign Slot

**Authority Grant**:
A frozen, provider-neutral set of versioned operation and resource identifiers
authorizing one semantic Runtime scope inside PlanSpec.
_Avoid_: provider permission string, blanket approval, mutable effect boundary

**Policy Witness**:
The frozen repository-policy reference and digest used to compile and evaluate
Authority Grants and other derived requirements.
_Avoid_: mutable policy lookup, provider configuration

**Runtime Profile**:
A user-configured operational choice of provider, model, reasoning, mode, and
features for one Runtime selector.
_Avoid_: PlanSpec field, difficulty tier, inferred model strength

**Runtime Selector**:
The exact semantic role key used to resolve a Runtime Profile for a Campaign
or one Ticket.
_Avoid_: difficulty tier, model guess, Ticket-wide override

**Runtime Binding**:
The exact Runtime, Agent, session, and workspace identity observed for one
semantic action.
_Avoid_: desired profile, capability requirement, live process guess

**Permission Request**:
A normalized exact request from one Runtime Binding for one operation,
resource, and request identity.
_Avoid_: popup text, `--all`, implicit full access
