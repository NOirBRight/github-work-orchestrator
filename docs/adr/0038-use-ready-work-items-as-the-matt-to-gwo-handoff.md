---
status: accepted
amends: ADR-0020, ADR-0021
---

# Use Ready Work Items as the Matt-to-GWO handoff

Human-facing Matt planning workflows remain upstream of GWO. `to-spec` is
optional when a canonical specification already exists; `to-tickets` and
`triage` converge on `ready-for-agent`, and only those Ready Work Items may
enter executable Plan Intent. GWO never impersonates the human decisions in
grilling, ticket approval, or triage.

Matt `/implement` remains the unchanged single-ticket flow.
`/implement-gwo` is the explicit durable-campaign entry for one ready ticket,
a parent Goal/spec, or a ready ticket set. The former `/orchestrator` command
is a one-release compatibility alias and is removed in V8.1 rather than
retained as a second shallow workflow.

Inside GWO, workflow commands are not valid Plan Node Skill References.
Workers use focused execution guidance, transient parent Reviewers use
`code-review`, and the Kernel retains authority over Admission, Evidence,
publication, recovery, and Integration. This preserves the Matt workflow while
preventing recursive orchestration and duplicate Review.
