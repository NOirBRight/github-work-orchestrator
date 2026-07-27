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

After CandidateGate authoritatively reads back an immutable Candidate,
Assurance Policy derives the Assurance Requirement from the same persisted
[`CandidateDiffRecordV1`](../design/gwo-v8-lean-architecture.md#candidatediffrecordv1)
used for scope and authority audit, affected Checks, protected surfaces,
Interaction Keys, and Formal Review. No consumer may reconstruct a weaker or
independently interpreted diff, and CandidateGate does not substitute
BatchIntegrator's later `PatchIdentityV1`. The requirement also binds
Authority Grants, Policy Witness, and observed effects and names affected
checks, Formal Review mode, specialist observation, human Decision, and reason
codes. A no-Review result requires a complete allowlist match; strict
Assurance follows repository policy; all other Candidates use standard
Assurance.

The resulting Assurance Requirement is a durable Candidate fact. Runtime
selectors, Profiles, providers, models, reasoning, sessions, and recovery state
remain Runtime facts outside PlanSpec.
