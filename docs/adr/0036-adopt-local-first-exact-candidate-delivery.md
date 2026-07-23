---
status: accepted
amends: ADR-0022, ADR-0025, ADR-0028
---

# Adopt local-first exact-candidate delivery

Executable code becomes publishable only after one immutable local Candidate
has its required repository-equivalent local checks and exact-SHA review.
Publication eligibility is a derived predicate, not another lifecycle state;
the eligible SHA is pushed once and hosted CI is the final external check
rather than the iterative debugging loop.

Valid Check and Review Evidence is consumed instead of repeated by each role.
One transient parent Reviewer may run Standards and Spec through two read-only
Internal Subagents and aggregate one typed Review Result. If one axis is
invalid or absent, Reviewer recovery preserves the valid axis and reruns only
the missing axis in a fresh Sol Max session.

One primary Attempt and one fresh frontier Attempt each receive at most one
Repair Round. Attempt termination records an explicit reason, and runtime loss
can block execution but cannot make a Plan Node semantically failed.
