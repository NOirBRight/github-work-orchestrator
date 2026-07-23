---
status: accepted
amends: ADR-0026, ADR-0031
---

# Separate Worker tiers from Runtime and role profiles

Worker tiers express four logical capability levels—light, standard, heavy,
and frontier—while Runtime Profiles contain concrete provider, model,
reasoning, mode, and features. Coordinator and Reviewer choices are independent
Role Bindings rather than invented Worker tiers.

Global host configuration supplies defaults and host-local repository sections
override them; versioned repository policy retains semantic risk, review, and
check requirements. New Admissions record the selected profile digest, and
configuration changes never rewrite existing Attempts or Plan Revisions.

Configured Worker, Reviewer, and Coordinator capacity counts only top-level
Agents managed by GWO. A managed parent may create Internal Subagents without
separate Admission, Attempt, Role Binding, or GWO capacity accounting. Those
children cannot exceed the parent's Effect Contract, and the parent remains
responsible for authoritative lifecycle facts, Evidence aggregation, and
Results.
