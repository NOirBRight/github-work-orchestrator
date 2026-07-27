---
status: amended by ADR-0057 and ADR-0060
amends: ADR-0041, ADR-0047, ADR-0048
---

# Bound Batch failure recovery to one singleton fallback

V8 has one deterministic Batch failure fallback. An infrastructure failure
retries the same exact Batch SHA at most twice and cannot create a Candidate,
Review Subject, or new semantic execution attempt.

A composition conflict, exact-Batch local-check failure, or code-class hosted
check failure preserves every member's Candidate, Candidate-scoped Check, and
Review Evidence, then dissolves the failed Batch once into a queue of
Singleton Batches. V8.0 does not recursively bisect the members, search
their combinations, or invoke an LLM to attribute the failure.

Each single-member Batch is composed onto the exact current target using Clean
Base Advance only after its per-member
[`PatchIdentityV1`](../design/gwo-v8-lean-architecture.md#patchidentityv1-and-clean-base-advance)
proof still holds, then runs the exact local and hosted checks for that
delivery SHA. A passing member can integrate without reimplementation or
Formal Review. A failing member alone receives one consolidated repair request
containing its Review Finding ledger. Only a changed Candidate creates a new
Review Subject and counts toward the Work Run's limit of at most three
distinct Candidate SHAs.

If every member passes alone but the original combination failed,
BatchIntegrator records an interaction conflict. The oldest eligible Candidate
integrates first, and the remaining members are reconsidered against the new
target through Clean Base Advance. A remaining conflict that deterministic
composition and checks cannot resolve requests a named Decision.

The Worker binding, isolated workspace, and repair context remain durably
parked until the member reaches a Batch verdict. Parking consumes no Campaign
Worker Slot. Passing work retires its binding; only the affected parked Worker
is resumed after reacquiring a Slot. This keeps the normal path to one Batch
verification, bounds the exceptional path to one singleton split, and avoids
repeating unaffected implementation or Review.

`DeliveryIdentityMismatch` and `DeliveryAttributionAmbiguous` are integrity
failures, not code-class or infrastructure failures. BatchIntegrator preserves
all Candidate provider observations and Evidence and may neither use Singleton
Batch Fallback nor resume a Worker for either outcome. The authoritative
hosted-result rules are defined by
[`Durable hosted result adoption`](../design/gwo-v8-lean-architecture.md#durable-hosted-result-adoption).
