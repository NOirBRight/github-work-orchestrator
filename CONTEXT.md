# GitHub Work Orchestration

This context coordinates concurrent GitHub execution while keeping repository
ownership, Task Group ownership, and runtime parentage unambiguous.

## Language

**Repository Coordinator**:
The single repository-labeled root Agent that arbitrates Task Groups and owns
integration decisions for one repository. Its conversation home and integration
worktree are separate resources.
_Avoid_: Father, Repository Orchestrator, Campaign

**Task Group**:
A label on tasks that names a bounded effort. It is not an Agent, Workspace, or
room.
_Avoid_: Campaign Agent, Campaign Control Workspace, Campaign room

**Dispatch**:
One assignment to one Agent, branch, worktree, and editor.
_Avoid_: Task, lane

**Provider Binding**:
The runtime-local choice of provider and model for an Agent role.
_Avoid_: Global model, fixed model

**Hotset**:
The files or modules a Dispatch claims for exclusive editing while it is active.
_Avoid_: Write set, scope

**Integration Lease**:
The repository-scoped exclusive right to update the integration branch.
_Avoid_: Merge lock

**Coordinator Loop**:
The event-driven repository reconciliation cycle that rebuilds state, plans a
ready wave, waits on Paseo signals, verifies evidence, and integrates.
_Avoid_: Poller, daemon, watchdog service

**Heartbeat**:
A best-effort Worker signal at safe execution boundaries with a five-minute
target. It reports liveness only and cannot authorize completion or cleanup.
_Avoid_: Orchestrator poll, timer SLA, terminal receipt

**Coordinator Home Workspace**:
The long-lived Coordinator conversation location. It may be dirty or not on
`dev` and is never used as an implicit integration target.
_Avoid_: Father worktree, control branch

**Integration Control Worktree**:
The explicitly addressed `dev` worktree used only for repository integration.
It must be clean immediately before merge and is permanently protected from
Task Group cleanup.
_Avoid_: Coordinator Home, execution worktree

**Spec Reviewer**:
The reusable Reviewer for Issue, decision, scope, Hotset, and acceptance
conformity.
_Avoid_: General reviewer, implementation Worker, Campaign-owned

**Quality Reviewer**:
The reusable Reviewer for standards, architecture, security, tests, and
maintainability.
_Avoid_: General reviewer, implementation Worker, Campaign-owned

**Review Lock**:
The Coordinator-issued immutable identity of one review round: dispatch,
candidate/base SHA, diff/acceptance digests, scope, and prior-round lineage.
Reviewer claims cannot create or replace it.
_Avoid_: Reviewer lock claim, matching hashes alone

**Review Assignment**:
The read-backed dynamic binding from one reusable Reviewer and fixed axis label
to one Coordinator parent, Dispatch, and Review Lock. It changes between
candidates without relabeling the Reviewer.
_Avoid_: Reviewer Dispatch label, permanent Worker identity

**Dispatch-scoped Replay**:
A Worker view of the store mailbox that ignores all other Dispatch and Task
Group lifecycle events before identity lookup. Full unscoped replay remains a
Coordinator responsibility.
_Avoid_: Incomplete reconciliation, sender filtering after rejection

**Material Delivery**:
The gwo transaction that carries one explicitly addressed store mailbox event
from its publish UUID through an idle-only signal wake to a recipient-authored
delivery ACK. Progress and heartbeat events do not enter it.
_Avoid_: Finish callback, mention, unacknowledged room post

**Wake Receipt**:
The non-authoritative `DELIVERY_WAKE` store record written after Paseo accepts
a signal-only wake. It prevents unlimited retries and never proves the recipient
processed the source event.
_Avoid_: Completion receipt, Agent liveness

**Delivery ACK**:
The non-authoritative `DELIVERY_ACK` written by the exact recipient after an
identity-verified replay of the source message. It proves receipt, not business
completion, merge, or cleanup eligibility.
_Avoid_: Result evidence, terminal receipt

**Cleanup Guard**:
The GWO-owned policy that authorizes exact cleanup actions from observed Paseo,
Git, and worktree evidence without requiring host or runtime source changes.
_Avoid_: Daemon guard, host cleanup service

**GWO Kernel**:
The packaged `gwo.py` CLI and SQLite store that enforce coordination invariants
at write time. The store is a rebuildable cache; GitHub remains the only durable
business truth.
_Avoid_: Second orchestrator, business state
