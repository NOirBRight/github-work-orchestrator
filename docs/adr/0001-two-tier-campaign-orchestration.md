---
status: accepted
---

# Use two-tier campaign orchestration

Keep one Repository Coordinator per repository and create one independently
provider-bound coordinating Campaign Agent (runtime role `orchestrator`) per
Campaign. Campaigns may execute in
parallel when their Hotsets do not overlap, while a repository-scoped
Integration Lease serializes updates to `dev`; this preserves provider freedom
and concurrency without giving multiple Agents competing integration authority.
