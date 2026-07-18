---
status: accepted
---

# Use an event-driven Paseo Coordinator loop inside GWO

Keep orchestration in `github-work-orchestrator`; do not add another Skill,
daemon, sidecar, or local task database. GitHub remains durable business state
and Paseo remains the Agent/worktree/chat runtime.

Repository and Campaign Coordinators reconstruct observed state, plan complete
eligible waves, submit independent Agent creates without waiting for sibling
completion, and serialize only `dev` integration. A pure scheduler makes wave
selection deterministic and idempotent.

Worker HEARTBEAT is advisory liveness at safe phase boundaries with a
five-minute target. Coordinators wait on room/finish/permission events and do
not poll running Agents. Fifteen minutes of silence permits one recovery
inspection but never cancellation, replacement, archive, merge, or cleanup.
