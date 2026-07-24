---
status: superseded
superseded-by: 0007-adopt-v5-lightweight-orchestration.md
---

# Separate Paseo supervision from Workspace visibility and review axes

Superseded by ADR 0008 for the V7 design (supervision and workspace
decisions); review-axis independence, the Coordinator Home / Integration
Control Worktree separation, and the provider-native exclusion carry forward
unchanged. Operative until roadmap Phases 2–3 land.

Keep the supervision tree `Repository Coordinator → Campaign →
Worker/Reviewer`, but give each new Campaign its own local Campaign Control
Workspace. Paseo parentage expresses notification and cleanup authority;
Workspace expresses the sidebar entry and file context. Keep Coordinator Home
separate from the explicitly addressed Integration Control Worktree so a dirty
conversation Workspace cannot stop implementation or invite destructive WIP
cleanup.

Route ordinary Tasks to an existing Coordinator through a bounded Operator
Relay and persistent Repository Room rather than creating another control
plane. A Relay never performs repository reconciliation.

Give every Campaign three dedicated Worker slots and two dedicated reusable
Review slots. Standard/strict review requires independent Spec and Quality
Paseo Reviewers locked to the same Campaign-issued, persisted candidate
evidence; Reviewer results cannot self-authorize the lock. Re-read capacity at
Reviewer creation time.

Compile room identity receipts from Paseo readbacks. Workers replay only their
exact Dispatch, so unrelated repository/Campaign history cannot require receipt
construction or block activation; Campaigns still perform full reconciliation.
Campaign retirement requires read-backed child enumeration and three separate
mutation/readback phases for Agent, control Workspace, and local branch.

Provider-native
Agent/Task/Swarm features remain outside GWO-owned trees. These decisions add no
Skill, host service, daemon change, or CodexHub/Paseo source dependency.
