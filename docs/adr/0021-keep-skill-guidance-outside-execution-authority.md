---
status: amended by ADR-0038
---

# Keep Skill guidance outside execution authority

A Skill is prompt guidance resolved from the installed Skill catalog when an
Admission first compiles the initial prompt for a prospective Attempt. That
compiled prompt is fixed for the Admission and reused by all of its delivery
retries; a later Admission, including an escalation Attempt, resolves the then
current Skill again. This is an execution snapshot, not a Plan Revision lock.

GWO does not mirror, version, or update Skill packages, and a prompt change does
not create a Plan Revision. An Attempt may record the observed Skill name and
content digest for diagnostics, but all authority comes from the Plan Node's
Effect Contract and all acceptance comes from its output contract and
verified Evidence. A missing Skill Reference produces a warning and falls back
to the base prompt; V8.0 has no required Skill. A mandatory working method must
be expressed through output and Evidence contracts rather than prompt
availability.
