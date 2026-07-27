---
status: accepted
amends: ADR-0040, ADR-0047, ADR-0048, ADR-0049, ADR-0057, ADR-0058
---

# Make BatchIntegrator the only delivery boundary

BatchIntegrator is V8's sole repository-delivery deep module. It consumes only
accepted-Candidate receipts from CandidateGate and owns the complete boundary
from Candidate queueing through target-branch readback.

When the Integration Lease is free, BatchIntegrator immediately freezes up to
the configured number of oldest compatible Candidates available at that
moment. The default Batch size is four with host-global configuration and
repository override. It does not wait for running Workers, use a timer, predict
completion, or call an LLM to optimize membership. Strict, non-decomposable,
or protected-interaction Candidates use a one-member Batch.

BatchIntegrator hides:

- Batch Compatibility from actual Diff Manifests, Assurance, Interaction Keys,
  protected surfaces, target identity, and check environment;
- exact target readback and Clean Base Advance;
- isolated composition and immutable Batch SHA creation;
- the repository-equivalent exact-Batch local suite;
- one push and pull-request boundary;
- hosted CI observation of that same exact Batch SHA;
- the repository-scoped Integration Lease, serial target mutation, and durable
  target-branch readback; and
- the one bounded Singleton Batch Fallback.

Git, GitHub, pull-request, check-run, and Integration-Lease clients are private
implementation drivers behind BatchIntegrator, not public workflow modules.
Keeping them together enforces one invariant at the module boundary: local
verification, hosted CI, and target Integration all refer to the same immutable
Batch SHA.

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

Several Tickets may therefore share one PR and one hosted-CI execution without
losing per-Ticket Work Identity, Candidate identity, Evidence, or Result
attribution. Target Integration remains singular even while Worker execution
is fully concurrent.
