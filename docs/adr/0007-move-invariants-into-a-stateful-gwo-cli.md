---
status: accepted
supersedes: 0005-require-acked-material-room-delivery.md, 0006-compose-worker-and-review-dispatch-receipts.md
amends: 0003-use-an-event-driven-paseo-coordinator-loop.md
---

# Move coordination invariants into a stateful gwo CLI

Replace prose-enforced coordination invariants with a packaged, stdlib-only
`gwo` CLI that owns a local state store (SQLite under `GWO_HOME`). Sender
identity, message delivery acknowledgement, and the task/dispatch/review state
machines are enforced by the CLI at write time, not by Agents following
protocol documents. An event an Agent is not entitled to write is rejected by
the CLI instead of being filtered by replaying consumers.

This makes hand-compiled identity receipts, the two-phase
`DELIVERY_WAKE`/`DELIVERY_ACK` transaction, composed dispatch receipts, and
the per-event role matrix unnecessary: identity comes from a spawn-injected
`GWO_AGENT_ID`, delivery acknowledgement is `gwo inbox --ack-on-read` with
Signal-ID idempotency, and lifecycle transitions are validated against the
store. ADR 0005 and ADR 0006 are superseded when roadmap Phase 1 lands.

ADR 0003's event-driven loop is retained: Coordinators still wait on events
(`gwo inbox --wait`) and never poll running Agents. Its "no local task
database" constraint is amended, not violated: that prohibition targeted a
second source of business truth. The gwo store is a rebuildable coordination
cache — GitHub remains the only durable business state, and losing the store
requires reconciliation, never data recovery.

See `docs/design/gwo-v7-architecture.md` for the command surface, state
schema, and the mechanism-by-mechanism replacement table. V6 mechanisms remain
operative until the replacing roadmap phase lands.
