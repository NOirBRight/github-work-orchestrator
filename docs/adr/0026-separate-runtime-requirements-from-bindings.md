---
status: amended by ADR-0037
---

# Separate Runtime Requirements from Runtime Bindings

PlanSpec records the capabilities and logical Worker level required by a Plan
Node, not a fabricated Agent identity. Admission and routing produce
an observed Runtime Binding for each Attempt, preserving both plan portability
and an audit of what actually executed.

V8.0 does not require semantic dynamic routing. At Admission, the Kernel
applies deterministic Runtime Policy to Runtime Requirements, Worker Tier,
role, and recovery stage. Worker tiers and Coordinator or Reviewer Role
Bindings resolve to concrete Runtime Profiles in host configuration.
Materialization asks the selected Adapter to realize that choice.
Authoritative provider, model, Agent, session, and workspace identities exist
only after readback and are recorded in Runtime Binding. Replacing the runtime
or escalating the profile creates a new Attempt and binding without rewriting
PlanSpec.

A manually created Coordinator retains its actual runtime. Auto-creation or
replacement uses the configured Coordinator Role Binding. Other nodes declare
only the capabilities their contracts need; the Adapter advertises observed
support independently of provider or model naming.
