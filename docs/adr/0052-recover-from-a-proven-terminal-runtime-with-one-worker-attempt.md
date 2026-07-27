---
status: amended by ADR-0059
amends: ADR-0043, ADR-0044
---

# Recover from a proven terminal Runtime with one Worker Attempt

Availability fallback remains a pre-identity RuntimeGateway decision. When a
primary Runtime reports `unavailable` or `capacity_exhausted` before any Agent
identity may exist, the Gateway may bind the configured availability fallback
without consuming a Worker Attempt.

After an Agent identity exists, RuntimeGateway targets the same Runtime Binding
and stable action key for transient errors, missing callbacks, stale
observation, and hung-turn diagnosis. Time alone, a permission wait, or an
ambiguous lifecycle cannot authorize a replacement.

ExecutionKernel may select the configured Recovery Worker as the second and
final Worker Attempt only after RuntimeGateway produces a Terminal Binding
Receipt. The receipt binds the exact action, Agent, session, workspace, and
last observed state; proves that the old binding is terminal and fenced from
further execution; and preserves a read-backed Workspace checkpoint. If the
old binding cannot be proven terminal, the Work Task waits or requests a
Decision instead of creating a possibly concurrent Agent.

Runtime-terminal recovery consumes the second Worker Attempt because a new
Agent context is created. It consumes no Candidate Budget unless the Recovery
Worker submits a changed Candidate. Existing Candidate, Check, Review,
Finding-Ledger, and Workspace evidence remains attributable and reusable under
its existing identity. Changing Worker never resets Candidate Budget.

Permission denial or delay never selects another CLI to bypass authority.
Capacity pressure after identity never selects a fallback. Selection is a
deterministic ExecutionKernel and RuntimeGateway action; a Coordinator does not
choose models or providers. Exhausting the Worker Attempt bound requests
Decision/Replan.
