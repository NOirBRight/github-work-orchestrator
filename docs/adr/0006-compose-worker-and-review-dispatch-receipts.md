---
status: accepted
---

# Compose independently compiled Worker and Review dispatch receipts

Keep one Issue Dispatch across implementation and formal review. Compile
Worker and reusable Review Pair authority independently with the existing
`worker-dispatch` and `review-dispatch` identity-plan seams, then concatenate
their generated receipt arrays for Campaign reconciliation.

The room helper composes only duplicate Campaign receipts with identical
static identity and compatible read-backed `direct-child-dispatch` authority.
It rejects conflicting subjects and derives each Material Delivery scope from
the addressed child's role. This preserves full unscoped Campaign replay,
Worker-scoped replay, and two-axis review without hand-authored authority, a
new Dispatch namespace, or Paseo/daemon changes.
