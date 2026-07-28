---
status: amended by ADR-0059
---

# Define Runtime adapters by execution model

RuntimeGateway owns one private provider-neutral adapter contract rather than a
provider-shaped public interface:

```text
prepare(RuntimeActionSpec) -> PrepareReceipt | RuntimeFailure
observe(stable_action_id) -> RuntimeObservation | RuntimeFailure
command(stable_action_id, RuntimeTransition) -> CommandReceipt | RuntimeFailure
events(after_cursor) -> RuntimeEventPage
```

`prepare` idempotently resolves or creates Agent, session, and workspace
identity and stages the Artifact-backed Prompt; it cannot begin semantic
execution. `observe` proves the stable action, selected Profile digest, all
identities, Prompt acceptance, lifecycle, permissions, and fencing.
`RuntimeCommand` is the closed union `start`, `resume`, `park`, `interrupt`,
`fence`, and `retire`; the seventh semantic transition is the separately typed
`PermissionResponse(request_id, allow|deny)`. `start` or `resume` is legal only
after authoritative observation of the complete binding and Prompt receipt.
Events are wake hints, never authoritative state.

Every production adapter and the deterministic in-memory adapter passes the
same contract and failure suite. The in-memory implementation is not a looser
fake. Profile selection, permission policy, and fallback remain RuntimeGateway
policy. Retry bounds and semantic budgets remain ExecutionKernel policy. None
of them is adapter policy.

Every materialized resource round-trips repository, Campaign, Plan Revision,
Work Run, Runtime action, Agent, session, and workspace identity. Events
accelerate wake-up but never replace authoritative readback. Adapter
capabilities cannot grant authority: exact permissions remain covered by the
Work Run's Authority Grants and Policy Witness.

The integrated contract is
[`RuntimeGateway adapter contract`](../design/gwo-v8-lean-architecture.md#runtimegateway-adapter-contract);
all failure handling follows the single
[`Runtime failure taxonomy`](../design/gwo-v8-lean-architecture.md#runtime-failure-taxonomy).
