---
status: accepted
---

# Keep cleanup authorization inside GWO

Use `cleanup-plan` as the sole cleanup interface. GWO derives eligible actions
from observed Paseo, Git, and worktree evidence and executes them through
existing Paseo operations in two read-backed phases. No CodexHub module, sidecar,
or Paseo source change is required. Operations performed outside GWO remain
outside this policy.
