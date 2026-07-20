---
status: accepted
superseded-by: 0008-flatten-supervision-into-a-dynamic-task-dag.md
---

# Use two-tier campaign orchestration

Superseded by ADR 0008 for the V7 design; operative until roadmap Phases 2–3
land and for every active V6 Campaign lifecycle.

Keep one Repository Coordinator per repository and create one independently
provider-bound coordinating Campaign Agent (runtime role `orchestrator`) per
Campaign. Campaigns may execute in
parallel when their Hotsets do not overlap, while a repository-scoped
Integration Lease serializes updates to `dev`; this preserves provider freedom
and concurrency without giving multiple Agents competing integration authority.
