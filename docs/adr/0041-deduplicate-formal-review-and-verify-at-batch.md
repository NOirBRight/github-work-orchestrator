---
status: amended by ADR-0047, ADR-0048, ADR-0049, and ADR-0057
amends: ADR-0022, ADR-0039, ADR-0040
---

# Deduplicate Formal Review and verify repository behavior at the Batch

V8 gives Formal Review one Kernel-owned entry and identifies it by a Review
Subject digest covering the exact Candidate and every contract input that can
change the verdict. Workers may self-check but cannot invoke Formal Review or
produce Review Evidence. V8 owns the small, versioned Review Protocol; an
externally installed `code-review` skill may supply optional heuristic guidance
but does not define Formal Review coverage, transport, output, or lifecycle
semantics. Complete valid Evidence is reused for an unchanged Review Subject.
A changed Candidate SHA creates a new Review Subject and invalidates the old
approval. A fresh child checks the complete current `base...Candidate`; the
old-to-new delta is only a navigation aid. The complete Artifact-backed Finding
Ledger carries stable Finding IDs forward, and each new observation must
disposition every prior finding as `resolved`, `still_open`, `regressed`, or
`superseded` while remaining able to add new findings. V8.0 does not reuse
approval across Candidate SHAs or attempt line-level unaffected-proof analysis.
Standard assurance uses one fresh `primary` child to cover both Standards and
Spec obligations. Strict assurance adds at most one independent specialist
child selected by specialist ID or `strong`, or a human Decision Gate; the
specialist observation also serves as the independent second observation.
Only an invalid or incomplete observation is retried once through `strong`.
Every required obligation must have a typed disposition and evidence location,
so incomplete coverage is invalid Reviewer output rather than a hidden partial
pass. The complete typed payload is retained as a digest-addressed Review
Evidence Artifact and the Kernel consumes a compact receipt. Transport is
byte-bounded and fails closed; findings and repair context are never silently
truncated by count or character slice.

Host-global configuration and repository override map `primary`, `strong`, and
optional specialist IDs to the shared Runtime Profile catalog. These selectors
are operational roles rather than model-strength assertions and may map to the
same Runtime Profile. V8 does not evaluate, rank, or dynamically select models;
it validates and uses the user's explicit configuration. Concrete provider,
model, reasoning, mode, features, session, and binding remain outside PlanSpec
and are recorded in Review Evidence.

CandidateGate is the only Formal Review entry. It reads back the exact
Candidate, runs affected deterministic checks, derives Assurance, and only
then launches the required Formal Review Internal Subagent. Deterministic
failure therefore consumes no Review turn. A complete no-Review allowlist
match skips Review only through an explicit Assurance decision. On acceptance,
CandidateGate emits one compact accepted-Candidate receipt; Workers and
BatchIntegrator cannot launch another Formal Review.

When the Integration Lease is free, the Kernel immediately freezes up to
`batch_size` oldest compatible Candidates that are eligible at that moment and
starts Integration without waiting for running work. The default size is four
with host-global and repository override. V8.0 has no Batch timer, diff
threshold, cost score, or completion prediction. The exact composed Batch SHA
runs the repository-equivalent local suite once, crosses one pull-request
boundary, and runs hosted CI once. Strict or non-decomposable work uses a
one-member Batch. A pre-publication composition or exact-Batch check failure
dissolves the Batch once and requeues its members as single-member Batches
rather than recursively optimizing a split. This trades per-Candidate
repository-suite isolation and routine dual-Reviewer independence for lower
cost, earlier complete finding coverage, and verification of the exact code
that will be integrated.
