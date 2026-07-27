---
status: amended by ADR-0052 and ADR-0055
amends: ADR-0020, ADR-0022, ADR-0028, ADR-0030, ADR-0036, ADR-0039
---

# Bound Worker Attempts and Candidates across a Plan Revision

V8.0 applies two fixed Kernel-owned bounds to each Work Plan Node in one Plan
Revision: at most two Worker Attempts and at most three changed Candidate
submissions. The Attempt bound permits one primary Runtime Binding and, when
needed, one recovery Runtime Binding. The Candidate bound spans both Attempts
and never resets when the Worker, model, provider, session, or workspace
changes.

These bounds are Runtime safety policy. Ticket, Plan Intent, and PlanSpec do not
contain `recovery_policy`, `semantic_attempts`, or `repair_rounds`. The
configured primary and recovery roles still map to host-local Runtime Profiles,
but model configuration cannot enlarge the execution bounds.

The normal successful-recovery path is an initial Candidate, one consolidated
same-binding Repair using the complete blocker ledger, and, only if still
needed, one final changed Candidate from the recovery Worker. A healthy
`no_result` closes its Worker Attempt without consuming Candidate Budget.
Resubmitting an unchanged rejected Candidate creates no new Review Subject and
cannot obtain another Formal Review.

An invalid or incomplete Reviewer observation is protocol recovery for the
same Review Subject. Its one `strong` retry consumes neither Worker Attempt nor
Candidate Budget. Transient Runtime, transport, and hosted-infrastructure
failures remain operational recovery and do not masquerade as Candidate
rejection.

Every changed Candidate creates a new Review Subject. Its fresh Review uses the
complete current Candidate plus the complete Finding Ledger; changing Worker
or Candidate never resets or truncates that ledger. Review approval is not
reused across Candidate SHAs in V8.0.

The counters are durable Runtime facts reconstructed with the Plan Node state,
not semantic authorization inside PlanSpec. Once the applicable bound is
exhausted, the Kernel records the complete attempt, Candidate, check, and
finding ledger and requests Decision/Replan. It does not reset a per-Attempt
Repair allowance or start another automatic Worker loop.
