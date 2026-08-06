# ADR-0063: Candidate Review and Repair Boundary

- Status: Accepted
- Date: 2026-08-03
- Amends: ADR-0041, ADR-0043, and ADR-0057
- Issues: #114 and #115

## Context

Worker output is not authoritative Candidate identity, Evidence, or a
delivery Result. Lean V8 needs one fail-closed boundary that rereads the
exact Git base and reported Candidate reference, proves the complete tree
delta, selects bounded Assurance, and preserves Review and Repair lineage
without granting a Reviewer mutation, delegation, merge, tracker, or
global-planning authority.

Campaign Watchdog work in #113 needs only an immutable persisted Candidate
receipt. Batch delivery in #116 consumes only an accepted-Candidate
receipt. Neither consumer may invent a competing Candidate identity.

## Decision

CandidateGate is the sole Formal Review entry. GitCandidateReader resolves
the frozen base through CandidateBasePort and resolves the reported
reference to exact base_commit_oid, base_tree_oid, candidate_commit_oid,
and candidate_tree_oid values. It emits one immutable
CandidateDiffRecordV1. Rename and copy inference are disabled; raw Git path
bytes use unpadded base64url tokens and a rename is delete plus add.

CandidateReceipt is private Work Run identity. ExecutionKernel persists
CandidateReceipt.canonical() directly at
state["runs"][ticket_key]["candidate_receipt"], reads it back before the
phase transition, and exposes read-only receipt access. candidate_tree_oid
remains a root canonical field. #113 imports this receipt and owns every
Watchdog or liveness projection; it does not construct or repair receipts.

The exact CandidateDiffRecordV1 instance and digest are reused by scope,
protected-surface, authority, affected-check, AssuranceRequirement,
ReviewSubject, InteractionKey, RepairDelta, and AcceptedCandidateReceipt
construction. A deterministic failure returns before any Reviewer call.

AssuranceMode.NO_REVIEW performs zero Reviewer actions but still emits an
exact ReviewSubject and accepted-Candidate receipt. STANDARD performs one
formal_review. STRICT performs one formal_review followed by at most one
policy-selected specialist_review; absence of the required specialist
returns a typed human Decision. InvalidReviewTransport permits exactly one
review_strong retry over the identical ReviewSubject.digest. A valid result
for an unchanged Subject is not reviewed again.

ReviewFindingLedger retains every hard and advisory ReviewFinding with one
typed ReviewFindingDisposition. RepairPacket binds the complete ledger,
required disposition IDs, allowed raw-path tokens, required checks,
protocol version, and repair instructions. repair_verify rereads the
repaired Candidate, computes RepairDelta from the prior and repaired
CandidateDiffRecordV1 values, rejects path escape before RepairVerifier,
requires a disposition for every prior Finding, reruns the exact required
checks, and invokes only RepairVerifier.verify. It never reopens Formal
Review for a changed Candidate.

One Work Run records at most three effect-admitted distinct Candidate
commit OIDs. A fourth distinct persisted receipt produces
CandidateBudgetExhausted: concatenated with the exact ticket_key as a
durable Decision before the next
external effect and releases the Worker Slot. Exact replay and repeated
SHA are idempotent. Repair, restart, resume, and the single
terminal-binding-Evidence-authorized replacement do not reset Candidate or
binding bounds.

Formal Reviewer and Repair Verifier capability readback must prove a
read-only, non-delegating boundary with no tracker mutation, merge,
authority expansion, or global planning. A proved Ticket-unsatisfiable
scope escape invokes only the existing PlanInvalidationReporter seam owned
by #137. CandidateGate does not classify the Campaign, edit Issues, change
membership, or create a successor Plan Revision.

AcceptedCandidateReceipt binds CandidateReceipt.digest, exact base and
Candidate commit/tree identity, CandidateDiffRecordV1 digest,
AssuranceRequirement, ReviewSubject, Policy Witness, Evidence digests,
complete ReviewFindingLedger digest, protected surfaces, and concrete
InteractionKey values. It has no result_digest. A code Result exists only
after exact Batch integration and target read-back.

## Release admission

Beta1 is metadata and tracker repair only and grants no production
admission. Beta2 is the feature-complete preview after #113 through #117
and #137 merge with exact Candidate-assurance evidence. Beta3 is the
cutover candidate and still requires Guard and Activation read-back. GA
requires a real public-API root Canary plus exact target, Activation, and
default-writer read-back.

## Consequences

Candidate identity has one owner and one canonical digest chain. Watchdog,
CandidateGate, and Batch delivery can evolve without redefining receipt
fields. Review and Repair calls are bounded and replayable. Failures remain
auditable through immutable Evidence, while production admission remains
outside Beta1 and outside CandidateGate itself.
