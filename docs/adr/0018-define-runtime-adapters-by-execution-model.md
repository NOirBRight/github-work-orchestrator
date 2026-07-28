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
Events are wake hints, never authoritative state. One `events` call performs
at most one authoritative action readback. A durable fair-scan cursor advances
before that readback, so one stale or failing action cannot block the others;
terminal actions emit at most one stored terminal wake and leave the scan set.
A state-changing `fence` or `retire` claim atomically re-arms that action, so
the later fenced-completed or retired state emits its own wake. Proven
non-dispatch restores the previous terminal marker with the rest of the claim.

The production adapter persists an effect claim only after all local argument
and file validation succeeds. It may restore that claim only when process
creation proves the provider call was not dispatched. Timeout, oversized
output, malformed output, receipt mismatch, and any other post-dispatch failure
retain the exact pending claim for readback-first recovery and cannot authorize
a duplicate provider effect.

The production Workspace reserves `.gwo` for Gateway-owned files. The pinned
base commit must not contain any casefold-equivalent top-level path, including
`.GWO`. Before Workspace creation, prepare persists a random ownership nonce;
after exact registry and Git identity readback it creates or recovers a
nonce-bound ownership marker and only the fixed artifact, schema, result, and
resume paths. Marker creation uses one deterministic nonce-owned temporary
name; restart accepts and removes that orphan only after containment,
regular-file, non-reparse, and single-link validation. Every parent and leaf is
checked with `lstat`, reparse-point and link rejection, regular-file or
directory type checks, resolved containment, and exact path recomputation
before each read or atomic replacement. This is a non-racing filesystem threat
model: portable Python and current Windows APIs do not provide a
descriptor-relative, no-follow primitive for every operation, so the adapter
does not claim protection against an attacker racing those check/use
sequences. Read-only Agent and Workspace registry discovery may occur before an
unrecorded Workspace path is known; an unsafe path still fails before any
provider-mutating effect.

Verified canonical output dominates every non-retired provider lifecycle,
including idle, running, and busy, and any stale park or resume flags are
cleared atomically when completion is adopted.
Completed and retired bindings reject new permission decisions without calling
the provider. Only an exact replay of a durably completed same-request,
same-decision effect is idempotent. Its request and provider-receipt digests
must recompute, its stable action, subject, and binding must match the
observation, and the exact request must remain absent from outstanding
permissions.

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
