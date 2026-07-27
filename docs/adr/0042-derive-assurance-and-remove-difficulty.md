---
status: amended by ADR-0044, ADR-0055, ADR-0057, and ADR-0061
amends: ADR-0026, ADR-0030, ADR-0037, ADR-0039
---

# Derive Candidate assurance and remove difficulty from PlanSpec

An upstream Ticket carries the V8 behavioral contract. `/to-tickets` adapts a requirement
into executable Tickets; GWO consumes that contract without requiring
GWO-specific routing metadata.

`difficulty`, model tier, and input `risk` are absent from Tickets, the
PlanControl-private planning output, and PlanSpec. They are ungrounded
predictions and do not select Runtime Profiles. PlanSpec records factual
capability needs plus provider-neutral Authority Grants and a frozen Policy
Witness.

After an immutable Candidate exists, Assurance Policy derives the Assurance
Requirement from its complete diff record, Authority Grants, Policy Witness,
protected surfaces, and observed effects. The requirement names affected
checks, Formal Review mode, specialist observation, human Decision, and reason
codes. A no-Review result requires a complete allowlist match; strict
Assurance follows repository policy; all other Candidates use standard
Assurance.

The resulting Assurance Requirement is a durable Candidate fact. Runtime
selectors, Profiles, providers, models, reasoning, sessions, and recovery state
remain Runtime facts outside PlanSpec.
