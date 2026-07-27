---
status: amended by ADR-0039, ADR-0040, ADR-0043, and ADR-0048
amends: ADR-0022, ADR-0024, ADR-0025, ADR-0028
---

# Adopt local-first exact-Candidate delivery

Executable code becomes eligible for delivery only after CandidateGate reads
back one immutable Candidate, audits its complete diff against the Ticket
contract and frozen authority, runs required Candidate checks, and satisfies
its Assurance Requirement for that exact SHA.

Valid Check and Formal Review Evidence is consumed rather than repeated. A
changed Candidate creates a new Review Subject; an unchanged rejected
Candidate cannot obtain another Review. CandidateGate preserves the complete
Review Finding ledger and issues one consolidated repair request. The Work Run
may submit at most three distinct Candidate SHAs total across its initial and
optional replacement binding.

BatchIntegrator may compose an accepted unchanged Candidate through Clean Base
Advance only when the original
[`PatchIdentityV1`](../design/gwo-v8-lean-architecture.md#patchidentityv1-and-clean-base-advance)
for that member equals the identity recomputed after applying that member alone
to the exact advanced target. The comparison is never against the whole Batch
or a cumulative multi-member tree. This does not create a new Candidate,
Review Subject, or reusable Review Evidence for another SHA.

The exact composed Batch SHA must pass the repository-equivalent local suite,
become the pushed and pull-request head, and be the exact hosted-CI head before
integration. Batch Evidence records the original and recomputed member
identities, the exact trees from which they were computed, the algorithm
version, and every exact-Batch check.
