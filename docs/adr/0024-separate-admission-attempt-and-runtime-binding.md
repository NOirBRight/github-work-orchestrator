---
status: amended by ADR-0036
---

# Separate Admission, Attempt, and Runtime Binding

V8 retires Dispatch as its execution entity. Admission atomically reserves the
claims of a ready node and authorizes Materialization. Materialization
is reconciled by the GWO Kernel rather than by a Coordinator Agent: it
idempotently realizes and reads back the runtime binding and acceptance of the
initial prompt, surfacing only exhausted or ambiguous outcomes for semantic
judgment. Only successful readback begins an Attempt; launch, delivery, or
partial-runtime failures before it are Admission recovery and do not consume
an execution Attempt. Runtime Binding is the concrete adapter, model, Agent,
workspace, and session observed for that Attempt. Work Item, Plan Node, and
Attempt own separate lifecycles, with at most one active Attempt per stable Node
Key across revisions and per Agent in V8.0.

Atomicity is local to one GWO Store transaction rather than a distributed
transaction across GWO, GitHub, and a Runtime Adapter. That transaction
rechecks the active Plan Revision, active and non-held Goal, node readiness,
typed dependency satisfaction, absence of a non-terminal Admission or Attempt
for the Node Key, and availability of every required claim. It either creates
the Admission and reserves all claims or changes nothing. Subsequent external
effects converge through stable idempotency keys and authoritative readback.
No Store transaction remains open while calling GitHub, Git, CI, or a Runtime
Adapter; committed Admissions fan out their external Materialization actions
independently.

Admission evaluates live availability rather than a capacity snapshot embedded
in PlanSpec. Plan Nodes declare resource claims, repository Runtime Policy
defines operational concurrency limits, and the Kernel derives current
occupancy from Store and Runtime Adapter readback. A ready node for which no
slot or exclusive lease is currently available remains in a capacity Wait
Condition without creating an Admission or Attempt. Capacity release wakes the
Goal Driver, whose next Kernel Reconciliation pass checks admission again;
waiting consumes no Agent turn and is neither compilation failure nor execution
failure.

Kernel Reconciliation is work-conserving: one pass continues admitting every
compatible ready node until no eligible claim or live slot remains. Within one
resource pool, oldest `ready_since` wins a contested claim and Node Key breaks
ties, but ordering never limits a pass to one node. Strict business ordering is
expressed by typed Plan Edges rather than hidden scheduling heuristics.

The immutable Admission ID is the idempotency root. Each external
Materialization step derives a stable action key from that ID and step name, so
retries never become another create, bind, or Prompt-delivery request. After a
timeout or transient error, the Kernel reads back before retrying and adopts
matching partial resources instead of creating duplicates. Retry
exhaustion is counted per unchanged external action: the initial execution and
at most two readback-first retries are allowed across reconciliation passes.
Successful progress moves to the next missing action. Deterministic provider,
model, mode, or configuration rejection blocks immediately.

If readback cannot determine whether a partial runtime began execution, the
Admission becomes `materialization_ambiguous` and retains its capacity and work
claims; the Kernel neither destroys it nor admits a replacement blindly. After
runtime recovery, the same Admission resumes from confirmed readback. A new
Admission is permitted only after the prior Admission is explicitly abandoned
and its resources and claims are reconciled. Once Runtime Binding and initial
Prompt acceptance are read back, one Store transaction creates the Attempt and
transfers the Admission's claims to it without a release-and-reacquire gap. The
Admission is then consumed. A subsequent runtime exit records an explicit
Attempt terminal reason such as `runtime_lost` or `no_result`; repeated runtime
loss blocks runtime availability rather than failing the Plan Node.

Admission and Attempt claims have no wall-clock expiry. Passage of time alone
cannot release them; authoritative runtime readback followed by an explicit
terminal, abandon, or reconciliation transition is required. Kernel restart
therefore resumes reconciliation from durable claims rather than risking
duplicate execution.
