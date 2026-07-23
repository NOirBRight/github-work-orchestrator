---
status: superseded by ADR-0034
---

# Start V8 with a new store generation

V8 starts a new store generation only after every V7 Dispatch is terminal. It
rebuilds from durable GitHub facts and verified runtime readback instead of
reinterpreting V7 task and dispatch rows in place; the V7 database remains
read-only audit evidence. This accepts a controlled cutover to avoid carrying
ambiguous V7 lifecycle semantics into the new domain model.

A V7 Dispatch that never acquired a Worker or workspace is reconciled as
unmaterialized and terminal before cutover without being counted as an
execution Attempt.

V7 and V8 are never concurrent lifecycle writers for one repository. The fresh
V8 generation becomes writable only after the remaining cutover gates and
durable Plan Activation succeed.
