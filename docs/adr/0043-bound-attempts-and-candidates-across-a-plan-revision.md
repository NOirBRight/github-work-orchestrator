---
status: amended by ADR-0052, ADR-0055, and ADR-0057
amends: ADR-0020, ADR-0022, ADR-0028, ADR-0030, ADR-0036, ADR-0039
---

# Bound Worker bindings and Candidates within a Work Run

Within one Plan Revision, each Work Run permits at most three distinct
Candidate SHAs submitted in total, one initial Worker binding, and at most one
replacement Worker binding. Replacing a binding never resets the Candidate
limit or Review Finding ledger.

These are fixed ExecutionKernel bounds, not Ticket, PlanControl-private
planning output, PlanSpec, model, or Runtime Profile settings. The normal repair
path continues the same binding with one consolidated repair request containing
the complete Review Finding ledger. An unchanged rejected Candidate cannot
obtain another Formal Review.

Invalid Review transport may retry once through `review_strong` for the same
Review Subject and consumes no Candidate submission or binding allowance.
Transient Runtime, transport, and hosted-infrastructure recovery likewise does
not masquerade as semantic rejection.

A replacement binding requires terminal-binding Evidence for the initial
binding. Exhausting either bound returns a named Decision; ExecutionKernel
does not silently create another semantic loop.
