# GitHub Work Orchestration

This context turns selected GitHub Tickets into verified repository Results
through concurrent semantic work and deterministic control.

## Language

**Ticket**:
An independently executable behavioral contract produced by the upstream
ticketing workflow and stored as a GitHub Issue. A Ticket marked
`ready-for-agent` is suitable for planning, although its blockers may still be
open.
_Avoid_: raw Issue, Plan Node, task prompt

**Campaign**:
One `/implement-gwo` execution over a selected set of Tickets, with one stable
objective, one active Plan Revision, and Campaign-scoped capacity.
_Avoid_: Goal, Task Group, Campaign Agent

**Plan Revision**:
An immutable, digest-addressed snapshot of a Campaign's selected Ticket
contracts, dependencies, capabilities, authority, and repository policy.
_Avoid_: mutable plan, Agent DAG, one plan per Ticket

**PlanSpec**:
The Runtime-neutral Ticket Manifest serialized inside a Plan Revision.
_Avoid_: execution snapshot, model assignment, lifecycle graph

**Work Run**:
The bounded execution lifecycle of one Ticket inside one Plan Revision,
including its Worker activity, Runtime identity, Candidate submissions, waits,
and terminal outcome.
_Avoid_: Plan Node, top-level Review Task, unbounded retry loop

**Candidate**:
An immutable repository Artifact submitted by a Work Run for verification.
Edits, diagnostics, and uncommitted workspace state are not Candidates.
_Avoid_: working tree, intermediate patch, Worker self-report

**Result**:
The verified terminal outcome of one Ticket. A code-producing Result is not
complete until its exact Candidate is integrated and read back from the target
branch.
_Avoid_: Agent finished, PR opened, Candidate submitted

**Integration Batch**:
One immutable delivery aggregate of compatible accepted Candidates that share
an exact local-verification, pull-request, hosted-CI, and target-integration
boundary.
_Avoid_: wave, merge queue, Batch Agent

**Coordinator**:
The semantic role that performs one Campaign Planning Pass per Plan Revision
and handles explicit semantic Decisions. It does not schedule, monitor, relay,
Review, or integrate normal work.
_Avoid_: resident supervisor, workflow driver, repository writer

**Worker**:
The semantic role that implements one Ticket inside an isolated workspace and
repairs its own rejected Candidate within bounded Work Run limits.
_Avoid_: Coordinator, Reviewer, integration writer

**Formal Review**:
An independent, Candidate-scoped semantic observation launched internally
after deterministic Candidate checks pass. It is Evidence, not a Task or
approval conversation.
_Avoid_: Worker self-review, Batch review, external `code-review` lifecycle

**Evidence**:
A typed, digest-addressed observation bound to an exact subject, source, and
observer identity. Worker assertions and free-form completion messages are not
Evidence.
_Avoid_: claim, log text, reported pass

**Review Finding**:
A stable, Evidence-backed Formal Review observation that remains traceable
across changed Candidates until explicitly resolved, still open, regressed, or
superseded.
_Avoid_: transient comment, truncated prompt item, approval

**Assurance Policy**:
The repository policy that derives required Candidate checks, Formal Review,
specialist observation, and human Decisions from the actual Candidate change
surface and authority.
_Avoid_: input risk score, difficulty tier, model selector

**Wait**:
A non-terminal Campaign or Work Run state naming the exact observable event or
due time required for deterministic continuation.
_Avoid_: inactivity, silent stop, failure

**Decision**:
A durable semantic or human choice required before scope, authority,
acceptance meaning, or exhausted recovery may change.
_Avoid_: routine retry, permission timeout, chat-only approval

**Worker Slot**:
One Campaign-scoped lease for an actively executing Work Run. A Campaign has
four by default; Internal Subagents never add Worker Slots.
_Avoid_: provider quota, Review Slot, Agent identity

**Exclusive Resource**:
A resource whose concurrent use is explicitly unsafe and therefore constrains
Worker Admission.
_Avoid_: ordinary file overlap, predicted write path, Runtime capacity

**Interaction Key**:
A deterministic fact derived from an actual Candidate change that identifies a
high-coupling semantic surface and constrains Integration Batch compatibility.
_Avoid_: predicted file lock, universal path lock, Ticket dependency

**Runtime Profile**:
A user-configured operational choice of provider, model, reasoning, mode, and
features for one semantic role or explicit Ticket override.
_Avoid_: PlanSpec field, difficulty tier, inferred model strength

**Runtime Binding**:
The exact Runtime, Agent, session, and workspace identity observed for active
semantic work.
_Avoid_: desired profile, capability requirement, live process guess

**Permission Request**:
A structured request from one Runtime Binding for one exact operation,
resource, and authority. Unmatched authority requires a Decision rather than a
blanket grant.
_Avoid_: popup text, `--all`, implicit full access
