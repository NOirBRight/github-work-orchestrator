---
status: amended by ADR-0050, ADR-0051, ADR-0056, ADR-0057, and ADR-0058
amends: ADR-0031, ADR-0037, ADR-0039
---

# Use Campaign-scoped Worker Slots

V8 exposes one semantic execution-capacity setting: concurrent Work Runs per
Campaign. The default is four, configured host-globally with repository
override. It is absent from Tickets, PlanControl-private planning output, and
PlanSpec.

An active Work Run consumes one Worker Slot and retains it through Candidate
checks, Formal Review Internal Subagents, and immediate consolidated repair.
Internal Subagents consume no additional Worker Slot. A Runtime proven parked
and an accepted Candidate waiting for delivery release the Slot; resumption
reacquires capacity first.

Each Campaign also has one fixed Coordinator semantic-control capacity. It is
not another Worker Slot and cannot be starved by Worker saturation.
ExecutionKernel reconciliation, CI readback, and deterministic integration use
neither capacity.

V8 has no Review Slot pool, configurable Coordinator pool, or provider quota.
RuntimeGateway remains authoritative for physical Runtime availability.
