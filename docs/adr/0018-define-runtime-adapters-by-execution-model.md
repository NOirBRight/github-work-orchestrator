---
status: amended by ADR-0059
---

# Define Runtime adapters by execution model

RuntimeGateway owns one private provider-neutral adapter contract rather than a
provider-shaped public interface:

```text
prepare(RuntimeActionSpec) -> PrepareReceipt | RuntimeFailure
observe(stable_action_id) -> PreparedRuntimeObservation | BoundRuntimeObservation | RuntimeFailure
command(stable_action_id, RuntimeTransition) -> CommandReceipt | RuntimeFailure
events(after_cursor) -> RuntimeEventPage
```

`prepare` idempotently resolves or creates only the action-owned Workspace and
stages every governed Artifact-backed input, including the Prompt. It creates
no Agent, session, Runtime Binding, or semantic execution. A Prepared
observation proves the stable action, selected Profile digest, authority,
Workspace, Prompt, and boolean fence state while Agent, session, and binding
are explicitly absent. A Bound observation additionally proves the complete
Agent/session/binding identity, Prompt acceptance, lifecycle, permissions, and
fencing.
`RuntimeCommand` is the closed union `start`, `resume`, `park`, `interrupt`,
`fence`, and `retire`; the seventh semantic transition is the separately typed
`PermissionResponse(request_id, allow|deny)`. `start` is legal only after an
authoritative Prepared observation; `resume` is legal only after an
authoritative parked Bound observation.
Events are wake hints, never authoritative state.

Every production adapter and the deterministic in-memory adapter passes the
same contract and failure suite. The in-memory implementation is not a looser
fake. Profile selection, permission policy, and fallback remain RuntimeGateway
policy. Retry bounds and semantic budgets remain ExecutionKernel policy. None
of them is adapter policy.

Every Bound materialized resource round-trips repository, Campaign, Plan
Revision, Work Run, Runtime action, Agent, session, and workspace identity.
Prepared state round-trips the same semantic action and Workspace while
explicitly proving that Agent, session, and binding do not yet exist. Events
accelerate wake-up but never replace authoritative readback. Adapter
capabilities cannot grant authority: exact permissions remain covered by the
Work Run's Authority Grants and Policy Witness.

The integrated contract is
[`RuntimeGateway adapter contract`](../design/gwo-v8-lean-architecture.md#runtimegateway-adapter-contract);
all failure handling follows the single
[`Runtime failure taxonomy`](../design/gwo-v8-lean-architecture.md#runtime-failure-taxonomy).
