---
status: superseded by ADR-0034
---

# Stage V8 cutover with shadow and canary execution

One repository has exactly one lifecycle writer. V7 and V8 may read the same
durable facts during evaluation, but they must never both create execution,
admit work, or mutate the target integration branch.

V8 begins in shadow mode: it reads real GitHub and Runtime state, compiles
PlanSpec v2, and computes the ready frontier and proposed Admissions without
creating Agents, changing lifecycle state, or integrating code. Its output is
compared with expected behavior and any applicable V7 DAG before write
execution is enabled.

Cutover requires every V7 Dispatch to be terminal or proven unmaterialized, the
V7 Integration Lease to be released, all retained Runtime and worktree
ownership to be unambiguous, the V7.1 frozen commit to be fixed, the Paseo
Adapter contract suite to pass, the host Goal Driver to sustain
`reconcile --once`, the initial Plan Revision to be published, read back, and
activated, and a fresh V8 Store generation to exist. Failure of any gate
prevents V8 Worker creation.

The first live canary is one bounded, low-risk Task Group Goal with three to
five independent work nodes. It must exercise parallel Admission, parked CI
waiting, automatic capacity refill, review, and serial Integration. After the
canary passes, V8 opens directly to the configured Worker capacity rather than
remaining indefinitely in an artificially low-concurrency mode.

Rollback never converts a V8 Store back into V7 lifecycle rows. Before any V8
Attempt begins, rollback may withdraw activation and abandon the new Store
generation. After execution begins, new Admissions stop first; existing
Attempts must reach terminal readback or be explicitly superseded, and all
Runtime resources and claims must be reconciled before another writer starts.
V8 Artifacts may be adopted manually but cannot be represented as historical V7
Dispatches. The rollback decision is published as a durable GitHub fact.

The V7 integration line completes the Issue 27 freeze audit, merges to `main`,
and may be tagged `v7.1-frozen`. V8 implementation begins from that commit on
`v8-integration`; the `design/gwo-v8` decisions are incorporated there rather
than extending `v7-integration`.
