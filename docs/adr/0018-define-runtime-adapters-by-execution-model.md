---
status: amended by ADR-0059
---

# Define Runtime adapters by execution model

RuntimeGateway owns one private provider-neutral adapter contract rather than a
provider-shaped public interface:

```text
prepare(RuntimeActionSpec) -> PrepareReceipt | RuntimeFailure
read_observation(stable_action_id) -> ObservationRead
command(stable_action_id, RuntimeTransition, ObservationReadToken) -> CommandReceipt | RuntimeFailure
events(after_cursor) -> RuntimeEventPage
```

`ObservationRead` is a sealed, closed envelope. It binds the requested and
selected stable action, complete subject/Profile/Prompt/input/spec identity,
Workspace and optional Agent/session/binding identity, one exact
Prepared/Bound observation or closed failure, Artifact read evidence, and a
causal token. The Adapter mints that token from the selected action record at
the readback/reconciliation linearization point; consumers never sample the
action again to invent causality. One pure, total validator accepts this same
envelope for progress, acknowledgement-loss recovery, command gating, and
events. Exact classes, fields, tuples, nested permission/completion evidence,
failure codes, receipts, event pages, and cross-action identity are closed;
malformed values become typed protocol failure rather than exceptions.
Its verdict kind is exactly `prepared`, `bound`, `authoritative_absence`,
`fairness_advance`, `failure`, or `invalid`. The protocol alone classifies a
valid transport, same-action binding-missing, or same-action
materialization-pending failure as `fairness_advance`; event callers never
reinterpret a raw failure code. Authoritative Gateway and Adapter paths retain
that verdict and branch only on its kind; only the external compatibility
`observe` projection may unwrap it at the final edge. Closed result scalars
use one exact field table before comparison or set membership. It covers the
read, identity, token, Artifact evidence and proofs, and Prepared/Bound
lifecycles; proof lengths are exact bounded non-negative integers. Subclasses
and objects with hostile equality, hashing, attribute access, or integer
conversion therefore cannot escape the typed boundary.
Any failure that carries a stable action ID must name the selected action.
Action-bound absence, binding-missing, materialization-pending,
prepare/command acknowledgement-loss, and effect-ambiguity failures require
that ID even when the read has no materialized identity.

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
Permission request and decision fields are exact non-empty strings before any
effect. `None` is the sole event-cursor origin. Every concrete cursor is the
canonical ASCII decimal form `[1-9][0-9]{0,18}` in `1..2^63-1`; zero,
leading-zero aliases, booleans, integers, subclasses, Unicode digits,
overflows, and coercible objects are rejected without advancing or publishing
event state. A non-empty page returns its last event cursor and an empty page
echoes the requested cursor. Events must be strictly newer than that request.
The durable ring is at most 64 consecutive canonical events and is never
silently normalized. A state change requiring publication after cursor
`2^63-1` fails as `RUNTIME_EVENT_CURSOR_EXHAUSTED` before scan or event
mutation.
Events are wake hints, never authoritative state. One `events` call performs
at most one authoritative action readback. It first captures, without
mutation, the fair-scan cursor, ordered eligible identity, and selected action.
After complete observation validation, one final CAS also binds the exact
post-readback action record before atomically advancing the cursor and
publishing any wake. Protocol-owned `authoritative_absence` and
`fairness_advance` verdicts may advance the cursor without an event so one
stale action cannot block the others; malformed evidence and CAS misses
advance nothing. The event-page protocol separately returns
`transient_failure` for exact transport failure, so Gateway wake handling also
branches only on kind. Terminal actions
emit at most one stored terminal wake and leave the scan set. A state-changing
`fence` or `retire` claim atomically re-arms that action, so the later
fenced-completed or retired state emits its own wake. Proven non-dispatch
restores the previous terminal marker with the rest of the claim.
Every Gateway command carries the accepted read token. The Adapter compares it
to the current selected-record and complete-identity digests before any
provider effect, so a concurrent retire, rebind, or reconciliation makes the
old command stale without dispatch.
Production performs that comparison again inside the same durable transaction
that grants the provider-effect claim; the claim cannot resample a newer
record after validating an older read. The in-memory implementation validates
the complete sealed read and token under the same re-entrant lock as its
effect.

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
Completion also requires one exact closed
`gwo.runtime.output.v1` Artifact proof—digest and canonical bytes plus exact
schema, subject, stable action, and authority. Missing, corrupt,
cross-action, or extra-field output fails before observation or receipt
publication. Paseo, the in-memory Adapter, and RuntimeGateway use the same
proof operation.
Completed and retired bindings reject new permission decisions without calling
the provider. Only an exact replay of a durably completed same-request,
same-decision effect is idempotent. Its request and provider-receipt digests
must recompute, its stable action, subject, and binding must match the
observation, and the exact request must remain absent from outstanding
permissions. A native Paseo receipt name is first verified against the
provider-namespaced operation identity; retained evidence then stores that
normalized operation ID in `name`, and every ingestion, restart, and readback
requires `receipt.name == request.operation_id`.

Only same-action `RUNTIME_PREPARE_ACK_LOST` and
`RUNTIME_EFFECT_AMBIGUOUS` prepare failures permit follow-up readback
recovery. Configuration, protocol, unknown, transport, and other permanent
failures remain their original typed result even if an action is subsequently
observable.

Canonical JSON strings and object keys contain Unicode scalar values only.
Lone high or low surrogate code points are rejected recursively at Profile,
Artifact, journal, and provider ingress; a valid escaped surrogate pair that
decodes to a scalar and ordinary supplementary characters remain valid.

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
