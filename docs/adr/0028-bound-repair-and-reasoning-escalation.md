---
status: amended by ADR-0036 and ADR-0043
---

# Bound repair and reasoning escalation before node failure

V8.0 counts independently verified candidate submissions, not an Agent's
internal edits, commands, or test runs. A primary implementation Attempt may
receive at most one formal, findings-driven Repair Round in the same runtime
binding. If it ends `rejected` or `no_result`, the pre-authorized Recovery
Ladder creates one fresh `worker_frontier` Attempt. That Attempt also receives
at most one Repair Round. If the primary profile is already frontier,
escalation changes Agent and session rather than inventing another tier.

When evidence is genuinely contradictory, the Coordinator may create one
ordinary read-only diagnostic work Plan Node before the frontier Attempt. V8
does not add an Advisor entity or automatic Advisor stage. Agent self-report
does not determine failure. A Plan Node becomes failed only when the semantic
ladder is exhausted; unresolved dependency, decision, or runtime availability
instead leaves it blocked. A failed Plan Node does not fail its Task Group Goal
automatically.
