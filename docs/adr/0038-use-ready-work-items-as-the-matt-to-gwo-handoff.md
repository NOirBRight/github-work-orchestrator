---
status: amended by ADR-0039, ADR-0050, and ADR-0055
amends: ADR-0020, ADR-0021
---

# Use approved Tickets as the upstream-to-GWO handoff

Human-facing planning workflows remain upstream of GWO. `/to-tickets` and
`/triage` may converge on the canonical `ready-for-agent` state, but GWO
executes only the Ticket references explicitly selected at `start`. It never
impersonates the human decisions in grilling, Ticket approval, or triage.

PlanControl snapshots the complete selected Ticket contracts and canonical
blockers into one Campaign Plan Revision, obtains one Campaign Planning Pass,
and deterministically compiles PlanSpec. Its planning output and compilation
record remain private. Selected Tickets without dependency edges may execute
concurrently.

Workflow commands are not PlanSpec fields or execution authority. Workers use
focused guidance; CandidateGate alone owns Formal Review; the deterministic
modules retain authority over admission, Evidence, delivery, recovery, and
integration. This prevents recursive orchestration and duplicate Review.
