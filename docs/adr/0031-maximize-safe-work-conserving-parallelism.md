---
status: amended by ADR-0037
---

# Maximize safe work-conserving parallelism

V8 uses its state machine to increase safe concurrency rather than to create
one-node waves. Every Kernel Reconciliation pass processes the complete ready
frontier and admits every compatible Plan Node until a real dependency,
exclusive claim, configured active-turn ceiling, or observed Runtime limit is
reached. Queue ordering chooses among contenders for a scarce claim; it never
limits a pass to one or two Workers. GWO has no hard-coded low Worker limit.

Runtime Policy supplies bounded pools with initial maxima of eight Worker
turns, four Reviewer turns, and one reserved Coordinator turn, further limited
by observable provider and Runtime availability. Review does not consume the
eight Worker counters but remains bounded. A live reasoning or tool-execution
turn consumes an Active Turn Slot. An Attempt waiting on CI or another named
external condition may retain its Agent, session, workspace, and other
necessary claims while releasing that slot. Completion of the Wait Condition
makes it eligible to reacquire a slot. Reaching the ceiling pauses only new
Admissions; it does not interrupt running Attempts, and capacity release wakes
the Goal Driver to run another pass and fill the frontier again.

Ordinary overlapping file Hotsets are advisory conflict risk in V8 rather than
hard admission locks. Isolated worktrees prevent concurrent edits from
corrupting one another, and serial Integration detects or resolves merge
conflicts. Hard exclusion is limited to the Integration branch, the same Node
Key, a non-shareable Agent or session, explicitly non-concurrent external
environments, and resources declared exclusive by PlanSpec or Runtime Policy.

Materialization fans out independently for all admitted nodes, bounded only by
a separate control-plane concurrency limit that protects the Runtime API. Its
per-Admission idempotency and readback rules remain unchanged. Independent
work, reviews, hosted checks, and verification may all proceed concurrently.
The only repository-wide default serialization point is target-branch
Integration under the Integration Lease; typed Plan Edges and explicit
exclusive resources may add local ordering.

Coordinator execution uses a separate control capacity pool, so a saturated
Worker pool cannot prevent Goal continuation, diagnosis, or replanning. Kernel
Reconciliation itself is non-Agent control-plane work and consumes no Agent
slot.

V8.0 acceptance tests must exercise concurrency as a contract: one pass admits
`min(ready, capacity)` independent nodes; parked CI Attempts release Active
Turn Slots; released slots are refilled on the next pass; ordinary Hotset
overlap does not block Admission; Integration remains singular; and saturated
Workers cannot consume Coordinator control capacity.
