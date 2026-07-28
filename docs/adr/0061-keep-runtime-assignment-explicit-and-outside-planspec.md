---
status: accepted
amends: ADR-0037, ADR-0042, ADR-0044, ADR-0055, ADR-0059
---

# Keep Runtime assignment explicit and outside PlanSpec

V8 separates Runtime assignment from semantic planning. Runtime assignment
uses these exact selectors:

- Campaign-scoped `coordinator`;
- Ticket-scoped `worker`;
- Ticket-scoped `recovery_worker`;
- Ticket-scoped `review_primary`;
- Ticket-scoped `review_strong`; and
- Ticket-scoped `specialist:<policy-id>`.

The `coordinator` selector resolves Campaign-start Coordinator override,
repository `coordinator` role mapping, then host-global `coordinator` role
mapping. Every Ticket-scoped selector resolves exact Campaign-start
`(ticket_key, role)` override, repository role mapping, then host-global role
mapping. There is no Ticket-wide shorthand and no Ticket override for
Coordinator.

Each mapping names one required primary Runtime Profile and at most one
optional availability fallback. Permanent missing or invalid required
configuration before identity returns `Blocked(RuntimeConfigurationInvalid)`
without fallback or transport retry. Different selectors and primary/fallback
positions may intentionally resolve to the same Profile.

Campaign-start overrides are persisted with the Campaign. For each stable
Runtime action, RuntimeGateway records selector, configuration source, resolved
Profile digest, and whether the optional fallback was selected. Retries,
resume, readback, and same-binding recovery reuse that assignment.

For the initial Planning Pass, RuntimeGateway resolves only the Campaign-scoped
`coordinator` selector during a mechanically read-only preflight of the exact
pre-Plan `CampaignPlanningSubject`. PlanControl obtains the bound preflight
receipt after its immutable source snapshot and before a Ticket claim or
semantic action; neither the preflight nor assignment persistence creates an
Agent, session, workspace, provider action, or capacity reservation. The
subsequent Artifact-backed planning receipt is opaque to PlanControl, so the
persisted source/Profile/fallback facts remain RuntimeGateway-private.

Availability fallback is permitted only before any Agent identity may exist
for the stable action. After identity, RuntimeGateway recovers the same
binding. A replacement Worker requires terminal-binding Evidence and uses the
already resolved `recovery_worker` assignment.

A durably selected fallback remains selected even if the primary later
recovers. Cached availability is advisory; live provider, configuration, and
transport behavior follows the canonical
[`Runtime failure taxonomy`](../design/gwo-v8-lean-architecture.md#runtime-failure-taxonomy).

PlanControl may declare factual Runtime capabilities such as required tools or
execution features. It cannot infer difficulty, assign a model, rank profiles,
or convert a semantic opinion into a stronger Runtime. Provider, model,
reasoning setting, CLI, selector, Profile, configuration source, and fallback
never enter PlanSpec.

V8 therefore supports different models for different roles and explicit
different models for selected Tickets without adding an LLM router, risk
score, difficulty tier, evaluation system, label convention, or dependency on
the external `/to-tickets` skill emitting GWO-specific fields.
