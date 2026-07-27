---
status: amended by ADR-0059 and ADR-0060
supersedes: ADR-0039
amends: ADR-0041, ADR-0042, ADR-0043, ADR-0045, ADR-0049
---

# Make CandidateGate the only Formal Review entry

Formal Review occurs after a Worker reports a Candidate reference,
CandidateGate authoritatively reads back the immutable Candidate,
ExecutionKernel persists its Candidate receipt, and deterministic verification
succeeds. It occurs before that Candidate becomes eligible for an Integration
Batch. It is an internal action of CandidateGate, not a top-level workflow
unit, Worker action, Coordinator action, or Batch action.

CandidateGate evaluates one exact Candidate in this order:

1. read back the exact Candidate commit/tree from the reported reference;
2. construct `CandidateDiffRecordV1` and produce a private Candidate receipt
   for ExecutionKernel to persist;
3. audit scope, Authority Grants, and Policy Witness over that exact record;
4. run affected deterministic Candidate Checks over the same record;
5. derive the Candidate's Assurance Requirement from the same record;
6. when Assurance requires it, launch the Formal Review Internal Subagent with
   a Review Subject bound to that record;
7. accept the Candidate, return one consolidated repair request, request a
   Decision, or wait on a named observable condition.

Deterministic failure stops before Formal Review, avoiding an unnecessary LLM
call. A complete no-Review allowlist match skips the Review action by explicit
Assurance Evidence; it is not an implicit approval. Standard and strict
Assurance use the bounded Formal Review protocol defined by ADR-0041.

CandidateGate is the only component allowed to create a Review Subject, launch
a Formal Review child, validate Review Evidence, reconcile the Review Finding
ledger, or issue the authoritative Review-derived repair request. A Worker may run
tests and self-check its implementation, but that work never becomes Formal
Review Evidence. The Worker cannot invoke the external `code-review` skill as
a second gate. The Coordinator cannot approve a Candidate.

The private
[`CandidateDiffRecordV1`](../design/gwo-v8-lean-architecture.md#candidatediffrecordv1)
Artifact is the one complete diff consumed by scope and authority audit,
Checks, Assurance, protected surfaces, Interaction Keys, and Formal Review.
Missing, truncated, or digest-mismatched content fails before Reviewer
invocation. Review Evidence reuse requires the identical complete Review
Subject digest plus readable, digest-revalidated diff content. Base, Candidate,
diff schema, diff digest, or Review protocol change creates a fresh subject.

The parent Work Run retains its Worker Slot while CandidateGate checks,
Review, or immediate Repair are active. RuntimeGateway launches Review children
through the Runtime Profile resolved by the required review or specialist
selector; they may use a different CLI and consume no additional Worker Slot.
Acceptance parks or retires the Worker binding as appropriate, releases the
Worker Slot, and emits a compact accepted-Candidate receipt binding the
persisted Candidate receipt, exact commit/tree, and diff schema/digest for
BatchIntegrator.

BatchIntegrator consumes only accepted-Candidate receipts. It verifies
composition and exact-Batch repository behavior locally and through hosted CI;
it never launches another LLM Review. If Repair changes the Candidate SHA, the
new Candidate re-enters CandidateGate from diff readback and receives a fresh
Review Subject. The complete Artifact-backed Review Finding ledger is
preserved, and every earlier Review Finding receives a typed disposition.
Unchanged valid Candidate Evidence is not repeated.

This is a deep internal module boundary, not an additional workflow ceremony.
The top-level interface remains `start`, `advance`, and `inspect`.
