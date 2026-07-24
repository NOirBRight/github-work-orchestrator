---
status: superseded by ADR-0034
---

# Freeze V7 without the original Runtime Port

The current Issue 26 Dispatch remained in `claiming` without a Worker,
workspace, worktree, branch, or candidate. It therefore never materialized an
execution and consumes no execution Attempt. Transition cleanup preserves that
audit record, moves it out of active claiming state, and does not fabricate a
failure or retry.

V7 does not implement Issue 26's proposed fixed five-operation Runtime Port.
The present Python Kernel has no concrete Paseo execution calls to extract, and
an unused `spawn`, `status`, `deliver-prompt`, `worktree`, and `archive`
facade would be replaced immediately by V8's capability-oriented Runtime
Boundary. The existing Issue should retain its history and be closed as
superseded when GitHub changes are separately authorized. Runtime Boundary v2
becomes new V8 work rather than a prerequisite for freezing V7.

Issue 27 is redefined as the V7.1 frozen-baseline audit and no longer depends on
the original Issue 26 implementation. It records commit-bound package, test,
reference, sandbox, migration, and installation evidence together with the
known limits: no implemented Runtime Port, no `/goal`-like autonomous
continuation, possible manual recovery after runtime creation failure,
cooperative trust, isolated V7/V8 Store generations, and no claim that V7 is a
complete standalone product release.

After this decision, V7.1 accepts only correctness or reproducibility blockers
discovered by the freeze audit. Runtime extraction, scheduling features, and
other architectural expansion belong to V8.
