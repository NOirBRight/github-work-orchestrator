---
status: accepted
amends: ADR-0037, ADR-0042, ADR-0044, ADR-0055, ADR-0059
---

# Keep Runtime assignment explicit and outside PlanSpec

V8 separates model choice from semantic planning. Host-global configuration
defines role defaults for Coordinator, Worker, Recovery Worker, Review
`primary`, Review `strong`, and optional specialists. Repository configuration
may override those defaults. One Campaign start may additionally provide an
exact Ticket-to-Runtime-Profile override for selected Tickets.

Resolution order is exact Ticket override, repository role configuration, then
host-global role configuration. Missing required configuration fails closed.
The selected Runtime Profile and configuration provenance are recorded as
Runtime facts, but provider, model, reasoning, and selector names never enter
Ticket, Plan Intent, PlanSpec, or Candidate identity.

PlanControl may declare factual Runtime capabilities such as required tools or
execution features. It cannot infer difficulty, assign a model, rank profiles,
or convert a semantic opinion into a stronger Runtime. RuntimeGateway applies
only the user's deterministic configuration. Primary and fallback, or Review
`primary` and `strong`, may intentionally resolve to the same Profile.

V8 therefore supports different models for different roles and explicit
different models for selected Tickets without adding an LLM router, risk
score, difficulty tier, evaluation system, label convention, or dependency on
the external `/to-tickets` skill emitting GWO-specific fields.
