---
status: accepted
supersedes: ADR-0040
amends: ADR-0041, ADR-0047, ADR-0048, ADR-0049, ADR-0057, ADR-0058
---

# Make BatchIntegrator the only delivery boundary

BatchIntegrator is V8's sole repository-delivery deep module. It consumes only
accepted-Candidate receipts from CandidateGate and owns the complete boundary
from Candidate queueing through target-branch readback.

When the Integration Lease is free, BatchIntegrator immediately freezes up to
the configured number of oldest compatible Candidates from one Campaign
available at that moment. The member limit is an integer from one through four,
default four, with host-global configuration and repository override. The
oldest eligible Candidate is the seed; remaining Candidates are scanned oldest
first and added only when pairwise compatible with every frozen member.
BatchIntegrator freezes when the scan ends or the limit is reached. It does not
wait for running Work Runs, use a timer, predict completion, or call an LLM.

Eligibility requires the same Campaign and compatible:

- target and base identity, or a valid Clean Base Advance;
- check environment;
- Policy Witness digest;
- delivery identity;
- Assurance Requirement;
- protected surfaces; and
- pairwise Interaction Keys.

Strict Assurance always requires a Singleton Batch. Repository-policy
classifications for a non-decomposable, high-coupling, or protected
Interaction Key also require a Singleton Batch. Other accepted Candidates may
share a Batch only when all pairwise checks pass.

Clean Base Advance is permitted only when the original base is an ancestor of
the current target, Candidate and Evidence are unchanged, the target delta has
no protected interaction with the Candidate, and Git composes without manual
resolution. Every member must independently satisfy the canonical
[`PatchIdentityV1`](../design/gwo-v8-lean-architecture.md#patchidentityv1-and-clean-base-advance)
proof against the same exact advanced target before multi-member composition.
Batch Evidence binds the algorithm version, original and advanced tree pairs,
both per-member digests, final Batch SHA, and exact checks. Gitlink changes are
protected Singleton work.

BatchIntegrator then owns isolated composition, immutable Batch SHA creation,
the repository-equivalent exact-Batch local suite, one push and pull-request
boundary, hosted CI, the repository-global Integration Lease, serial target
mutation, durable target readback, and the single bounded Singleton Batch
Fallback.

Git, GitHub, pull-request, check-run, and Integration-Lease clients are private
implementation drivers behind BatchIntegrator, not public workflow modules.
Keeping them together enforces one invariant at the module boundary: local
verification, the pushed branch and pull-request head, and hosted CI all refer
to the same immutable Batch SHA. Integration names that SHA.

The target may advance to a merge commit rather than equal the Batch SHA.
Readback therefore proves the Batch SHA is an ancestor of the observed target
head and that GitHub's pull-request merge mapping connects the pull-request
head to that target head. Squash or rebase integration rewrites the reviewed
identity and fails closed.

BatchIntegrator never launches Formal Review, consumes a Worker Slot, invokes a
Coordinator on the normal path, mutates a member Candidate, or asks a Worker to
repair code. It returns typed delivery outcomes to ExecutionKernel. An
infrastructure failure retries the unchanged Batch SHA at most twice. A
composition, exact-local-check, or code-class hosted failure may dissolve the
Batch once into Singleton Batches, preserving every unaffected Candidate and
its Evidence.

Only a failing Singleton produces a delivery failure receipt that can cause
ExecutionKernel to reacquire a Worker Slot and resume that member's parked
Worker. Changed code becomes a new Candidate and re-enters CandidateGate.
BatchIntegrator never repeats Review for an unchanged Candidate.

BatchIntegrator durably adopts a terminal hosted result through the receipt
defined by
[`Durable hosted result adoption`](../design/gwo-v8-lean-architecture.md#durable-hosted-result-adoption).
Once that integrity-validated receipt is persisted, restart recovery uses it
without rereading the provider. Batch, suite, provider-check, or attribution
mismatch returns `DeliveryIdentityMismatch`; ambiguous attribution returns
`DeliveryAttributionAmbiguous`. Both preserve every Candidate observation and
Evidence and forbid Singleton Batch Fallback and Worker resume. Only the
composition, exact-local, and code-class hosted failures named above may take
the Singleton path.

Several Tickets may therefore share one PR and one hosted-CI execution without
losing per-Ticket Work Run identity, Candidate identity, Evidence, or Result
attribution. Target Integration remains singular even while Worker execution
is fully concurrent.
