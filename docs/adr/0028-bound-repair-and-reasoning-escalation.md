---
status: amended by ADR-0036 and ADR-0043
---

# Bound Candidate repair and Worker replacement

V8 counts distinct Candidate SHAs, not a Worker's internal edits, commands, or
test runs. Each Work Run may submit at most three distinct Candidate SHAs
total. CandidateGate returns one consolidated repair request with the complete
Review Finding ledger; a changed Candidate re-enters the gate under a new
Review Subject. An unchanged rejected Candidate cannot obtain another Formal
Review.

Each Work Run has one initial Worker binding and at most one replacement
binding. Replacement requires terminal-binding Evidence for the initial
binding and uses the configured `recovery_worker` assignment. It does not reset
the Candidate limit or Review Finding ledger.

Invalid Review transport may retry once through `review_strong` without
counting as a Candidate submission. Exhausted semantic bounds yield a named
Decision; unresolved dependencies, external events, or Runtime availability
yield their existing deterministic Wait or Blocked outcome.
