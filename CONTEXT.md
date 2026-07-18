# GitHub Work Orchestration

This context coordinates concurrent GitHub execution while keeping repository
ownership, campaign ownership, and runtime parentage unambiguous.

## Language

**Repository Coordinator**:
The single repository-labeled root Agent that arbitrates Campaigns and owns
integration decisions for one repository. Its conversation home and integration
worktree are separate resources.
_Avoid_: Father, Repository Orchestrator

**Campaign**:
A bounded execution effort and the user-visible name of its coordinating Paseo
Agent. The Agent is a direct child of the Repository Coordinator, owns exactly
one Campaign ID, and has one Campaign Control Workspace.
_Avoid_: Activity, run, Campaign Orchestrator in UI, Father, Orchestrator without qualification

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

**Coordinator Loop**:
The event-driven Repository/Campaign reconciliation cycle that rebuilds state,
plans a ready wave, waits on Paseo signals, verifies evidence, and integrates.
_Avoid_: Poller, daemon, watchdog service

**Heartbeat**:
A best-effort Worker room signal at safe execution boundaries with a five-minute
target. It reports liveness only and cannot authorize completion or cleanup.
_Avoid_: Orchestrator poll, timer SLA, terminal receipt

**Coordinator Home Workspace**:
The long-lived Coordinator conversation location. It may be dirty or not on
`dev` and is never used as an implicit integration target.
_Avoid_: Father worktree, control branch

**Integration Control Worktree**:
The explicitly addressed `dev` worktree used only for repository integration.
It must be clean immediately before merge and is permanently protected from
Campaign cleanup.
_Avoid_: Coordinator Home, execution worktree

**Campaign Control Workspace**:
The dedicated sidebar entry and local `gwo/campaign/*` worktree for one new
Campaign. It carries coordination context, no feature changes, no push, and no
PR.
_Avoid_: Worker worktree, shared repository root

**Operator Relay**:
A one-shot ordinary Task that durably forwards a sanitized request to the
existing Coordinator through the Repository Room, optionally wakes it by
Signal-ID, records a receipt, then idles.
_Avoid_: Temporary Coordinator, Campaign

**Spec Reviewer**:
The reusable Campaign-owned Paseo Reviewer for Issue, decision, scope, Hotset,
and acceptance conformity.
_Avoid_: General reviewer, implementation Worker

**Quality Reviewer**:
The reusable Campaign-owned Paseo Reviewer for standards, architecture,
security, tests, and maintainability.
_Avoid_: General reviewer, implementation Worker

**Candidate Lock Receipt**:
The Campaign-issued, persisted and read-backed immutable identity of one review
round: Dispatch, candidate/base SHA, diff/acceptance digests, scope, and prior
round lineage. Reviewer claims cannot create or replace it.
_Avoid_: Reviewer lock claim, matching hashes alone

**Review Assignment**:
The read-backed dynamic binding from one reusable Reviewer and static axis label
to one Campaign parent, Dispatch, and Candidate Lock Receipt. It changes between
candidates without relabeling the Reviewer.
_Avoid_: Reviewer Dispatch label, permanent Worker identity

**Dispatch-scoped Replay**:
A Worker view of one Campaign Room that ignores all other Dispatch and Campaign
lifecycle events before identity lookup. Full unscoped replay remains a
Campaign responsibility.
_Avoid_: Incomplete Campaign reconciliation, sender filtering after rejection

**Cleanup Guard**:
The GWO-owned policy that authorizes exact cleanup actions from observed Paseo,
Git, and worktree evidence without requiring host or runtime source changes.
_Avoid_: Daemon guard, host cleanup service
