---
status: accepted
supersedes: 0029 (cleanup completion sentence only)
---

# Require read-backed post-Integration retirement before Goal completion

ADR-0029 established Kernel-owned reconciliation and stated:
“Resource cleanup is Kernel-owned follow-up and does not hold the Goal open.”
This ADR supersedes that sentence for V8 Candidate and Review workspaces.
The rest of ADR-0029 remains accepted.

A Goal whose Candidate has been integrated remains active until every
in-scope Worker retirement is complete. The Kernel may issue a destructive
retirement authorization only after exact Integration readback. The
authorization binds repository, Plan Node, Admission, Attempt, Agent,
Workspace, Candidate SHA, integrated SHA, target branch, and temporary branch.
Runtime performs Agent archival, native Paseo worktree archival, exact
temporary-branch retirement, prune, and readback behind the single
`retire_after_integration` seam. Pending or failed retirement is durable,
typed, and idempotently retried after Store reconstruction. Goal completion is
authorized only after every required retirement complete readback is present.

Review Evidence convergence similarly authorizes identity-bound Review child
retirement. An independent disposable Review worktree is removed only after
its Evidence is accepted. A child sharing the Candidate workspace archives
only its Agent identity and preserves the shared directory and branch.
Coordinator, Integration, stable, dirty, ambiguous, or otherwise shared
workspaces fail closed.

Neither the Kernel nor durable Evidence carries an absolute workspace path or
a Paseo-native worktree name. Runtime resolves the exact native name from
Paseo Agent identity, canonical path, and worktree-list readback.
