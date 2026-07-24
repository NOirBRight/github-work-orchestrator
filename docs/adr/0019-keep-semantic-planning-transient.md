---
status: accepted
---

# Keep semantic planning transient and non-authoritative

When introduced after V8.0, Semantic planning is a one-shot role invoked by the Coordinator, not a
long-lived Planner Agent with its own durable lifecycle. It emits Plan Intent;
only the deterministic Plan Compiler may produce a valid Plan Revision, and
only the kernel may activate or execute it. This preserves the flat Agent
topology. V8.0 itself leaves this role deferred and accepts
Coordinator-authored Plan Intent.
