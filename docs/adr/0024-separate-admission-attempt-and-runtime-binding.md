---
status: amended by ADR-0036, ADR-0058, and ADR-0059
---

# Separate Work Run admission from Runtime Binding

ExecutionKernel atomically admits an eligible Work Run and reserves its Worker
Slot and genuine Exclusive Resources before asking RuntimeGateway to
materialize anything. It rechecks the active Plan Revision, dependency
satisfaction, disjoint Ticket claim, authority-subtree digest, capacity, and
exclusive claims. The transaction either creates the admission record and
reserves every claim or changes nothing.

External effects never occur inside that transaction. Each Runtime action uses
a stable action identity. RuntimeGateway calls `prepare`, then `observe`; it
cannot command semantic `start` or `resume` until observation proves the Agent,
session, workspace, selected Profile digest, Prompt acceptance, and Runtime
Binding identity. A failed create is read back by stable action identity before
retry.

Only a proven action-owned empty workspace may be removed. Identity or content
ambiguity fences attributable resources, retains claims, and requests a named
human Decision; it never creates a possibly duplicate Agent or adopts
unproven content.

A Runtime Binding is the observed Profile, adapter, Agent, session, workspace,
and stable action identity for one Work Run binding. Availability fallback is
allowed only before any Agent identity may exist. After identity, recovery
targets the same binding. A replacement is permitted only after
terminal-binding Evidence and does not reset the Work Run's Candidate limit.

ExecutionKernel is work-conserving: it admits every dependency-eligible Work
Run until Worker Slots, a genuine Exclusive Resource, or observed Runtime
availability blocks more work. Waiting for a Slot creates no Runtime identity.
Elapsed time alone cannot release claims; authoritative readback followed by a
parked, terminal, or reconciled transition is required.

Provider, configuration, and transport failures follow the canonical
[`Runtime failure taxonomy`](../design/gwo-v8-lean-architecture.md#runtime-failure-taxonomy).
