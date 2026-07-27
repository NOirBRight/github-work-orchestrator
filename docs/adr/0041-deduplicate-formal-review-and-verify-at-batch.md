---
status: amended by ADR-0047, ADR-0048, ADR-0049, ADR-0057, and ADR-0060
amends: ADR-0022, ADR-0039, ADR-0040
---

# Deduplicate Formal Review and verify repository behavior at the Batch

CandidateGate is the sole Formal Review entry. One Review Subject digest binds
the exact base, Candidate, Ticket contract, standards, Check Evidence,
Assurance Requirement, Policy Witness, and protocol version. Workers may
self-check but cannot invoke Formal Review or produce Review Evidence.

A complete no-Review allowlist match requires explicit Assurance Evidence.
Standard Assurance uses one complete `review_primary` observation. Strict
Assurance adds at most one policy-selected `specialist:<policy-id>` observation
or human Decision. Invalid or incomplete Review transport may retry once
through `review_strong`; a valid rejection is not repeated against an unchanged
Review Subject.

Changing Candidate SHA creates a new Review Subject. The Artifact-backed Review
Finding ledger remains complete, and every prior Review Finding receives a
typed disposition. CandidateGate emits one compact accepted-Candidate receipt;
BatchIntegrator cannot launch another Formal Review.

When the repository-global Integration Lease is free, BatchIntegrator
immediately freezes a same-Campaign Integration Batch using the configured
member limit of one through four, default four. Strict Assurance and
policy-classified non-decomposable, high-coupling, or protected work use a
Singleton Integration Batch. The exact composed Batch SHA runs the
repository-equivalent local suite, becomes the pushed and PR head, and is the
head observed by hosted CI.

This trades routine per-Candidate repository-suite execution and dual-axis
Review for complete Candidate-scoped Review, lower cost, and exact verification
of the code submitted for integration.
