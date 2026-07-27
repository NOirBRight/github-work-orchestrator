---
status: amended by ADR-0050, ADR-0051, and ADR-0056
amends: ADR-0031, ADR-0037, ADR-0039
---

# Use Campaign-scoped Worker Slots

V8 exposes one configurable execution-capacity value: the number of concurrent
Worker Tasks in each Campaign. The default is four. Host-global configuration
and repository override select that per-Campaign value; Ticket, Plan Intent,
and PlanSpec do not.

One actively executing Work Task consumes one Worker Slot. The Task retains
that same Slot while its Worker, Candidate Checks, Formal Review Internal
Subagents, specialist Internal Subagent, or Repair is active. Subagents never
consume an additional GWO Slot. A named parked Wait releases the Slot, and the
Task must reacquire one before active Work resumes. An accepted Candidate
waiting for Integration Batch delivery also releases its Slot.

Each Campaign additionally has exactly one fixed Coordinator control slot. It
is not configurable and remains independent of Worker saturation so Campaign
planning, diagnosis, and Decision handling cannot be starved. Kernel
reconciliation, CI readback, and deterministic Integration use neither slot.

V8 has no Review slot pool, configurable Coordinator pool, provider quota, or
parallel `execution_slots` setting. Paseo and Runtime Adapters remain
authoritative for physical CLI and provider capacity. Worker scheduling is
work-conserving inside each Campaign: when a Slot becomes free, the oldest
eligible Work action may acquire it.
