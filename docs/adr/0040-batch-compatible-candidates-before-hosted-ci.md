---
status: accepted
amends: ADR-0031, ADR-0036, ADR-0039
---

# Batch compatible Candidates before hosted CI

Parallel Work Attempts produce, check, and review immutable local Candidates
independently. They do not publish, run hosted CI, or mutate the target branch
independently. After the admitted compatible frontier has drained and no
immediately admissible Work Node is merely waiting for a freed Worker slot,
the Kernel closes one Integration Batch.

An Integration Batch contains the sorted Node keys, exact Candidate SHAs,
their shared target-base SHA, Result digests, and local Check/Review manifest
digests. The Kernel composes those Candidates in an isolated Git worktree and
records one immutable Batch SHA behind a content-derived local ref. Preparing
the same member set is idempotent. A one-member Batch reuses that Candidate
SHA.

The Batch is a Kernel-owned runtime aggregate, not another PlanSpec entity or
Agent lifecycle. PlanSpec keeps one Integration Node per Work Item so
dependencies and acceptance ownership remain explicit. Several such
Integration Nodes may be satisfied by the same Batch Evidence.

The Batch SHA is published once, all required hosted workflow names are
deduplicated into one Batch Check Manifest, hosted CI runs once on that exact
SHA, and the target branch fast-forwards once after success. Per-Candidate
local Check and Review Evidence remains authoritative because composition does
not change any member diff. Batch Evidence maps every reviewed Candidate to
the combined SHA and hosted result; it does not pretend that hosted CI ran on
each member SHA.

Classified hosted infrastructure failure may rerun the same Batch SHA at most
twice. A code or contract failure blocks the Batch for targeted diagnosis; it
does not blindly send every Worker through another implementation, Review,
push, and CI cycle. A composition conflict or changed target head similarly
blocks for deterministic conflict handling or replanning. Any resolution that
changes a member diff requires new Candidate Evidence only for the affected
member.

This replaces the earlier per-Candidate interpretation of “push once and run
hosted CI once.” The invariant now applies once per Integration Batch, while
local Candidate validation and Review remain parallel and exact-SHA.
