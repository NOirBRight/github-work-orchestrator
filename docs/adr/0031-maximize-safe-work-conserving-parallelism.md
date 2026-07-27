---
status: amended by ADR-0037, ADR-0039, ADR-0040, ADR-0044, ADR-0045, ADR-0047, and ADR-0058
---

# Maximize safe work-conserving parallelism

ExecutionKernel processes the complete dependency-eligible frontier and admits
every compatible Work Run until a genuine dependency, Exclusive Resource,
Campaign Worker Slot limit, or observed Runtime unavailability blocks more
work. Queue order resolves contention; it never limits a pass to one or two
Workers.

Each Campaign has four Worker Slots by default, configured host-globally with a
repository override. Formal Review Internal Subagents consume no Worker Slot.
A Work Run retains its Slot through affected checks, Formal Review, and
immediate consolidated repair. An accepted Candidate waiting for delivery or a
Runtime proven parked releases the Slot; resumption reacquires capacity before
semantic execution continues.

The Coordinator has one fixed Campaign semantic-control capacity that is not a
general scheduling Slot. Deterministic module work consumes no Agent capacity.
Ordinary predicted file overlap is advisory because Work Runs use isolated
workspaces. Hard exclusion is limited to genuine Exclusive Resources and the
repository-global Integration Lease, which serializes target mutation without
serializing Worker execution.
