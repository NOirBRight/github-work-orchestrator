---
status: amended by ADR-0059
amends: ADR-0043, ADR-0044
---

# Recover from a proven terminal Runtime with one replacement binding

Availability fallback remains a pre-identity RuntimeGateway decision. When a
primary Runtime reports `unavailable` or `capacity_exhausted` before any Agent
identity may exist, the Gateway may bind the configured availability fallback
without consuming the one allowed replacement binding.

After an Agent identity exists, RuntimeGateway targets the same Runtime Binding
and stable action key for transient errors, missing callbacks, stale
observation, and hung-turn diagnosis. Time alone, a permission wait, or an
ambiguous lifecycle cannot authorize a replacement.

ExecutionKernel may select the configured `recovery_worker` assignment as the
one replacement binding only after RuntimeGateway produces terminal-binding
Evidence. That Evidence binds the exact action, Agent, session, workspace, and
last observed state; proves that the old binding is terminal and fenced from
further execution; and preserves a read-backed workspace checkpoint. If the
old binding cannot be proven terminal, the Work Run waits or requests a
Decision instead of creating a possibly concurrent Agent.

Runtime-terminal recovery consumes the single replacement binding because a
new Agent context is created. It does not count toward the limit of at most
three distinct Candidate SHAs unless the Recovery Worker submits a changed
Candidate. Existing Candidate, Check, Review, Review Finding ledger, and
workspace Evidence remains attributable and reusable under its existing
identity. Changing bindings never resets the Candidate-submission limit.

Permission denial or delay never selects another CLI to bypass authority.
Capacity pressure after identity never selects a fallback. Selection is a
deterministic ExecutionKernel and RuntimeGateway action; a Coordinator does not
choose models or providers. Exhausting the binding bound requests a named
Decision.

Post-identity provider recovery and transport readback always target the same
binding. Permanent configuration failure preserves that binding and requires
human `Decision(RuntimeConfigurationRepairRequired)` unless terminal-binding
Evidence permits the one replacement. The complete behavior is defined once
in the
[`Runtime failure taxonomy`](../design/gwo-v8-lean-architecture.md#runtime-failure-taxonomy).
