---
status: accepted
supersedes: 0001-two-tier-campaign-orchestration.md, 0004-separate-supervision-from-workspace-and-review-axes.md
---

# Flatten supervision into a dynamic task DAG

Keep one Repository Coordinator per repository and remove the intermediate
Campaign Agent tier. The Coordinator plans a declarative task DAG at runtime
from the GitHub frontier: issue nodes, risk-tiered review nodes, and one
serial integration chain. A Campaign becomes a task-group label on tasks, not
an Agent, a Workspace, or a room. Workers and Reviewers are direct children of
the Coordinator (or direct sessions in a session-process runtime).

The DAG's shape is free; its safety is checked deterministically. `gwo guard
check-dag` validates any submitted plan for acyclicity, Hotset disjointness
across concurrently runnable issue nodes, capacity feasibility, review nodes
matching each node's risk tier, dependency consistency with GitHub, and a
serial Integration Lease chain. Invariants live in the guards, not in a
prescribed supervision shape.

This retires the Campaign Control Workspace, per-Campaign rooms, the Operator
Relay, and Material Delivery at four supervision boundaries: one Coordinator
mailbox serves all participants. From ADR 0004, three decisions carry
forward unchanged: Coordinator Home stays separate from the explicitly
addressed Integration Control Worktree; review axes stay independent
(`strict` runs spec and quality as separate Agents that do not communicate);
and provider-native Agent/Task/Swarm features stay outside GWO-owned trees.

ADR 0001 and the supervision/workspace decisions of ADR 0004 are superseded
when roadmap Phases 2–3 land. Active V6 Campaigns finish under the V6
lifecycle. See `docs/design/gwo-v7-architecture.md`.
