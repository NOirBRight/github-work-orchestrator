---
status: amended by ADR-0050, ADR-0053, ADR-0054, ADR-0056, and ADR-0058
amends: 0003-use-an-event-driven-paseo-coordinator-loop.md
---

# Drive mechanical reconciliation outside Coordinator Agents

V8 does not rely on a Coordinator Agent to wake and supervise its own loop.
ExecutionKernel exposes deterministic, idempotent `advance` behavior that
reads authoritative Campaign, Runtime, repository, and hosted-check state,
performs every currently due bounded action, persists transitions, and returns
one public outcome.

The Coordinator performs one Campaign Planning Pass for each Plan Revision and
bounded semantic diagnosis or Decision work when explicitly requested.
PlanControl, ExecutionKernel, RuntimeGateway, CandidateGate, and
BatchIntegrator retain their own deterministic authority; normal scheduling,
Review orchestration, delivery, and liveness never require a resident
Coordinator.

Campaign Watchdog is an event-and-due-time wake adapter, not another workflow
driver. Each event is a hint that invokes `advance`; state changes only after
readback. Every Wait names its observable event or due time. Restart rebuilds
subscriptions and timers from persisted Campaign state.

SQLite remains rebuildable control state and GitHub remains durable business
truth. Recovery uses stable Campaign, Plan Revision, Work Run, Runtime action,
Candidate, Batch, and Evidence identities. A uniquely matching external fact
is adopted; ambiguity freezes only the affected Work Run and never authorizes
a duplicate Runtime action.
