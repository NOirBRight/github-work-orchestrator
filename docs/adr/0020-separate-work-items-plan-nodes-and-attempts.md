---
status: superseded by ADR-0055
---

# Separate Work Items, Plan Nodes, and Attempts

One GitHub Issue is one Work Item, which may be decomposed into several Plan
Nodes across immutable Plan Revisions. Each execution or retry is a distinct
Attempt permanently bound to its original revision. Retrying the same contract
creates an Attempt; changing topology, dependencies, the Effect Contract,
or the output contract creates a Plan Revision instead of rewriting execution
history. An unchanged node keeps a stable Node Key across revisions. Resuming
the same runtime binding, including one formal findings-driven Repair Round,
continues its Attempt; replacing the Agent or workspace creates another
Attempt. Updating prompt guidance does not.

A verified Result from an older revision may be adopted by a newer revision
only when the stable Node Key and contract digest are unchanged. The Attempt
remains bound to its original revision; the newer revision records a reference
rather than copying or rebinding execution history. If the Node Key changes,
the old Artifact or Evidence may inform new work but cannot satisfy the new
node automatically.

An Attempt explicitly stopped because an authorized revision made its contract
obsolete becomes superseded rather than failed or rejected. Its immutable
history, Artifacts, and Evidence remain available to later work.
