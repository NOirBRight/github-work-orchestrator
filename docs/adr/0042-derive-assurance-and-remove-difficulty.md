---
status: amended by ADR-0055
amends: ADR-0026, ADR-0030, ADR-0037, ADR-0039
---

# Derive Candidate assurance and remove difficulty from PlanSpec

An upstream Ticket is the V8 Work Item. `/to-tickets` adapts a source Issue,
Spec, or conversation into executable Tickets; a configured tracker may store
each Ticket as a GitHub Issue. GWO consumes that Ticket contract without
requiring `/to-tickets` to emit GWO-specific execution metadata.

`difficulty`, Worker Tier, and input `risk` are removed from Ticket, Plan
Intent, and PlanSpec. Difficulty was an ungrounded model-strength prediction
and did not drive the V8 Runtime. PlanSpec records only explicit Runtime
capability requirements. Host-global configuration and repository override map
the initial and bounded-recovery execution roles to concrete Runtime Profiles.

After an exact Candidate exists, deterministic Assurance Policy resolves its
required Checks, Formal Review mode, specialist observations, human decisions,
and reason codes from the Candidate change surface, Effect Contract, and
versioned repository policy. A no-Review decision requires a complete
deterministic allowlist match; configured protected surfaces or effects require
strict assurance; all other Candidates require standard assurance. Check
selection uses affected inputs and effects rather than comparing scalar risk
tiers.

The compiled Plan binds the versioned Assurance Policy digest rather than an
Agent-proposed risk score. The resulting Assurance Requirement and its reasons
are durable Candidate facts. Concrete Reviewer provider, model, reasoning,
session, and recovery state remain Runtime facts outside PlanSpec.
