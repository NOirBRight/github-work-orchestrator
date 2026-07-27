---
status: amended by ADR-0051, ADR-0053, ADR-0054, ADR-0055, ADR-0056, and ADR-0058
amends: ADR-0019, ADR-0029, ADR-0038, ADR-0045
---

# Compile Ready Sets directly and invoke Coordination lazily

The installed external `/to-tickets` skill publishes approved Tickets with
the `ready-for-agent` state by default. That state means the Ticket's durable
behavioral contract is suitable for an Agent; it does not mean every blocker
is closed. The executable frontier is the subset whose blockers are satisfied
and which no other Campaign owns.

`/implement-gwo` snapshots the complete selected Ticket contracts and their
canonical blocker relationships, validates them mechanically, and compiles the
whole selected set into one Plan Revision. It does not serialize one Plan
Revision per Ticket. Tickets without explicit dependency edges are eligible
for optimistic concurrent Admission up to the Campaign's Worker Slots.

The initial path requires one Campaign-level Coordinator Planning Pass over the
complete frozen Ready Set. It emits a narrow typed Plan Intent; the
deterministic Compiler remains the only plan authority. After Activation,
ExecutionKernel owns Admission, Materialization, waits, bounded Repair,
Batch-action ordering, and lifecycle readback. CandidateGate owns Formal
Review, while the delivery boundary owns local and hosted Batch checks and
Integration. Each boundary exchanges typed, digest-addressed Artifacts or
receipts rather than a Coordinator's natural-language relay.

`/triage` remains available for raw or questionable Issues and for an explicit
independent audit. It is not a mandatory second pass over Tickets already
approved and published by `/to-tickets`. GWO still fails closed on an invalid
state label, missing durable contract, unresolvable blocker, dependency cycle,
or conflicting Campaign claim. The Planning Pass may report a semantic
contract problem, but neither it nor the Compiler invents the missing
requirement.

After its one required Planning Pass per Plan Revision, the Coordinator is an
exception-path semantic authority. ExecutionKernel requests or resumes it only
for an unresolved contract contradiction, requested scope expansion, exhausted
execution budget, semantic interaction that deterministic recovery cannot
resolve, or a human/semantic Decision Gate. A routine failure does not itself
require Coordination: Runtime and transport recovery, Review Repair Packets,
infrastructure retry, Singleton Batch Fallback, and Clean Base Advance remain
Kernel rules.

Each Campaign retains one fixed Coordinator control slot so the required
Planning Pass and later semantic decisions cannot be starved by Workers. The
slot is capacity, not a resident Agent. A valid manually created Coordinator
may be resumed when needed; otherwise the configured Coordinator role is
materialized for the bounded semantic turn.
