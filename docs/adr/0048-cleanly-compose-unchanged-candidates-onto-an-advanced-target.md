---
status: amended by ADR-0049 and ADR-0060
amends: ADR-0036, ADR-0040, ADR-0041, ADR-0047
---

# Cleanly compose unchanged Candidates onto an advanced target

V8 retains immediate micro-batching: when the Integration Lease is free,
BatchIntegrator freezes up to the configured number of oldest compatible
Candidates eligible at that moment. It does not wait for running Workers, use
a timer, or predict future completion merely to enlarge a Batch.

An unchanged Candidate does not become semantically stale merely because
another Batch advanced the target branch. BatchIntegrator may use Clean Base
Advance when the Candidate's original base remains an ancestor of the exact
current target, Candidate identity and Evidence are unchanged, the target
delta shares no protected Interaction Key with the Candidate, and Git composes
the histories without manual resolution. The resulting exact Batch SHA must
pass its repository-equivalent local suite and hosted CI.

Clean Base Advance reuses the original Candidate and Review Subject; it does
not pretend that a different Candidate SHA was reviewed. Batch Evidence binds
the unchanged Candidate, the exact target head, the composed Batch SHA, and
the Checks that observed that composition.

If ancestry, identity, Interaction Key, clean-composition, or exact-Batch Check
conditions fail, only the affected work must form a new Candidate. A changed
Candidate creates a new Review Subject and follows the existing Candidate
Budget. Unrelated Candidates retain their Evidence.
