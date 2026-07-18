# GitHub Work Orchestration

This context coordinates concurrent GitHub execution while keeping repository
ownership, campaign ownership, and runtime parentage unambiguous.

## Language

**Repository Coordinator**:
The single repository-resident root Agent that arbitrates campaigns and owns
integration decisions for one repository.
_Avoid_: Father, Repository Orchestrator

**Campaign**:
A bounded execution effort with one identity, room, lifecycle, and integration
candidate.
_Avoid_: Activity, run

**Campaign Orchestrator**:
The Agent that owns planning and coordination for exactly one Campaign and is a
child of the Repository Coordinator.
_Avoid_: Father, Orchestrator without qualification

**Dispatch**:
One Campaign-owned assignment to one Agent, branch, worktree, and editor.
_Avoid_: Task, lane

**Provider Binding**:
The Campaign-local runtime choice of provider and model for an Agent role.
_Avoid_: Global model, fixed model

**Hotset**:
The files or modules a Campaign claims for exclusive editing while it is active.
_Avoid_: Write set, scope

**Integration Lease**:
The repository-scoped exclusive right for one Campaign to update the integration
branch.
_Avoid_: Merge lock

**Control Worktree**:
The Repository Coordinator's persistent integration context, which is never a
Campaign cleanup target.
_Avoid_: Father worktree, execution worktree

**Cleanup Guard**:
The GWO-owned policy that authorizes exact cleanup actions from observed Paseo,
Git, and worktree evidence without requiring host or runtime source changes.
_Avoid_: Daemon guard, host cleanup service
