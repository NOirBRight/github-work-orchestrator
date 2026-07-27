---
status: amended by ADR-0038
---

# Keep Skill guidance outside execution authority

A Skill is prompt guidance resolved from the installed Skill catalog when an
initial Worker binding accepts the Artifact-backed Prompt. That prompt is fixed
for the stable Runtime action and reused by its transport retries. A
replacement binding may resolve the then-current Skill again. This is an
execution snapshot, not a Plan Revision lock.

GWO does not mirror, version, or update Skill packages, and a prompt change does
not create a Plan Revision. A Runtime action may record the observed Skill name
and content digest for diagnostics, but all authority comes from the PlanSpec
authority subtree, its Authority Grants, and its Policy Witness. Acceptance
comes from the Ticket contract and verified Evidence. A missing optional Skill
reference produces a warning and falls back to the base Prompt. A mandatory
working method must be expressed through the Ticket contract, Assurance
Requirement, or repository policy rather than prompt availability.
