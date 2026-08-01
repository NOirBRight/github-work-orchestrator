---
status: amended by ADR-0060 and ADR-0062
amends: ADR-0024, ADR-0029, ADR-0031, ADR-0045, ADR-0050, ADR-0053
---

# Make ExecutionKernel the only workflow driver

V8 has one persisted deterministic state machine and next-action authority:
ExecutionKernel. The complete public surface is:

```text
start(repository, ready_refs, options?) -> CampaignHandle
advance(campaign_handle, wake_ref?) -> Running | Wait | Decision | Complete | Blocked
inspect(campaign_handle) -> Diagnostics
```

`advance` loads Campaign state, confirms authoritative readback, performs every
currently due bounded transition or effect, persists the results, and returns
exactly one public outcome.

After readback, status derivation is deterministic and ordered:

1. `Complete` when all required work and delivery are terminal and accepted;
2. otherwise `Running` when any semantic or deterministic action is active or
   currently due;
3. otherwise `Decision` when a named durable choice is required;
4. otherwise `Wait` when a named observable event or due time can continue the
   Campaign; and
5. otherwise `Blocked` when no authorized action, Decision, or wake remains.

Repair is a nested Work Run phase and therefore contributes `Running`; it is
not a sixth public status.

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

Legacy driver and reconciliation entrypoints are removed. New code does not
introduce a second driver service, public compatibility operation, or
reconciliation state machine.

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
prevents wrappers, Watchdog, and Kernel from competing for ownership of it.
