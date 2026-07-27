---
status: amended by ADR-0060
amends: ADR-0024, ADR-0029, ADR-0031, ADR-0045, ADR-0050, ADR-0053
---

# Make ExecutionKernel the only workflow driver

V8 has one persisted deterministic state machine and next-action authority:
ExecutionKernel. The public `advance(goal_handle, wake_ref?)` operation loads
the Campaign state, confirms authoritative readback, performs every currently
due bounded transition or effect, persists the results, and returns exactly
one of `Running`, `Wait`, `Decision`, `Complete`, or `Blocked`.

ExecutionKernel owns lifecycle state, idempotency keys, budgets, Worker Slot
accounting, Wait conditions, due-action ordering, and transition validity. It
does not infer semantics, inspect free-form conversations, choose models,
render provider commands, perform Formal Review itself, or implement
repository delivery. Any LLM work is an explicit typed action delegated to the
appropriate semantic boundary; it is never reasoning hidden inside
`advance`.

Campaign Watchdog is a non-LLM wake mechanism, not another driver. Runtime,
permission, Review, and hosted-check events call the same `advance` operation
with a wake reference. `next_check_at` timers call it without inventing an
event. Events remain hints, and ExecutionKernel changes state only after
authoritative readback.

GoalDriver and Kernel Reconciliation are not separate V8 modules. A legacy
host callback or `reconcile --once` command may remain temporarily as a thin
compatibility wrapper around `ExecutionKernel.advance`, but it owns no durable
state, scheduling rule, retry budget, or semantic decision. New code does not
introduce a GoalDriver service or a second reconciliation state machine.

This leaves one understandable control path:

```text
event | due timer | manual call
  -> Watchdog or direct caller
  -> ExecutionKernel.advance
  -> typed deep-module action
  -> persisted transition
  -> Running | Wait | Decision | Complete | Blocked
```

Crash recovery replays the same idempotent operation from persisted Campaign
state. Removing the forwarding layer does not remove liveness recovery; it
prevents GoalDriver, Watchdog, and Kernel from competing for ownership of it.
