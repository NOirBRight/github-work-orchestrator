---
status: amended by ADR-0051, ADR-0053, ADR-0054, ADR-0055, ADR-0056, and ADR-0058
amends: ADR-0019, ADR-0029, ADR-0038, ADR-0045
---

# Compile selected Ticket sets directly and invoke Coordination lazily

The installed external `/to-tickets` skill publishes approved Tickets with
the `ready-for-agent` state by default. That state means the Ticket's durable
behavioral contract is suitable for an Agent; it does not mean every blocker
is closed. The executable frontier is the subset whose blockers are satisfied
and which no other Campaign owns.

`start` snapshots the complete selected Ticket contracts and their
canonical blocker relationships, validates them mechanically, and compiles the
whole selected set into one Plan Revision. It does not serialize one Plan
Revision per Ticket. Tickets without explicit dependency edges are eligible
for optimistic concurrent admission up to the Campaign's Worker Slots.

The initial path requires one Campaign-level Coordinator Planning Pass over the
complete frozen selected Ticket set. It emits a narrow typed planning output
private to PlanControl; deterministic compilation remains the only plan
authority. After activation, ExecutionKernel owns admission, Runtime-action
ordering, waits, bounded repair, Batch-action ordering, and lifecycle readback.
RuntimeGateway performs materialization. CandidateGate owns Formal Review,
while BatchIntegrator owns local and hosted Batch checks and integration. Each
boundary exchanges typed, digest-addressed Artifacts or receipts rather than a
Coordinator's natural-language relay.

`/triage` remains available for raw or questionable Issues and for an explicit
independent audit. It is not a mandatory second pass over Tickets already
approved and published by `/to-tickets`. GWO still fails closed on an invalid
state label, missing durable contract, unresolvable blocker, dependency cycle,
or conflicting Campaign claim. The Planning Pass may report a semantic
contract problem, but neither it nor PlanControl's deterministic compilation
invents the missing requirement.

After its one required Planning Pass per Plan Revision, the Coordinator is an
exception-path semantic authority. ExecutionKernel requests or resumes it only
for an unresolved contract contradiction, requested scope expansion, exhausted
execution budget, semantic interaction that deterministic recovery cannot
resolve, or a named human or semantic Decision. A routine failure does not itself
require Coordination: Runtime and transport recovery, consolidated repair
requests,
infrastructure retry, Singleton Batch Fallback, and Clean Base Advance remain
rules of their owning deep modules.

Each Campaign retains one fixed Coordinator semantic-control capacity so the
required Planning Pass and later semantic Decisions cannot be starved by
Workers. It is capacity, not a resident Agent or general scheduling Slot.
RuntimeGateway resumes or materializes each bounded semantic action using the
Campaign's persisted `coordinator` assignment.
