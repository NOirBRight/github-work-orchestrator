# GitHub Work Orchestration

This context defines the language of a lightweight, repository-scoped harness
that plans GitHub work and coordinates disposable execution Agents without
turning the orchestration layer into a runtime hierarchy.

## Control

**Orchestrator**:
The portable Skill and policy harness that coordinates work for one repository.
It is not an Agent, daemon, or long-running service.
_Avoid_: Father, Orchestrator Agent, orchestration daemon

**Coordinator Workspace**:
The stable repository workspace on the configured integration branch from which
coordination is performed. It is never a feature-branch or disposable Worker
workspace.
_Avoid_: Father worktree, Campaign workspace, Worker workspace

**Coordinator**:
Any qualifying root Agent currently coordinating one repository from its
Coordinator Workspace. Provider, model, reasoning level, and mode are runtime
attributes, not identity or ownership.
_Avoid_: permanent Father, provider-bound Coordinator, Campaign Orchestrator

**Reconcile**:
The idempotent act of rebuilding the repository's orchestration view from
GitHub and current runtime facts before choosing the next safe actions.
_Avoid_: polling loop, heartbeat cycle, room replay

## Planning

**Triage**:
The Coordinator's project-management work of clarifying, prioritizing,
relating, and preparing an Issue before execution.
_Avoid_: Intake Agent, scheduling-only pass

**Issue Design**:
The decision-complete, risk-proportional execution contract attached to one
Issue. It states the goal, boundaries, acceptance, Hotset, and validation
needed by a Worker.
_Avoid_: raw Issue body, mandatory full template, implementation transcript

**Priority**:
The urgency dimension used to decide what should run first, expressed as
`P0` through `P3`.
_Avoid_: Difficulty, Risk, model tier

**Difficulty Tier**:
The stable `light`, `standard`, or `heavy` classification of how capable and
costly a Worker runtime should be. Concrete providers and models remain local
runtime choices.
_Avoid_: Priority, Risk, provider name, model binding

**Risk**:
The `low`, `standard`, or `strict` verification rigor required before
integration.
_Avoid_: Difficulty, Priority, Reviewer count

**Campaign**:
An optional GitHub Milestone that groups Issues sharing a business outcome.
It has no Agent, workspace, lifecycle protocol, or execution authority.
_Avoid_: Campaign Agent, Campaign Orchestrator, mandatory grouping

**Wave Generation**:
The Issues admitted into currently free WIP Slots by one reconcile decision.
It is a rolling scheduling snapshot, not a batch, barrier, or runtime entity.
_Avoid_: fixed cohort, Campaign run, completion gate

**Hotset**:
The repository-relative paths one Dispatch may modify. It constrains writes,
while the Worker may read the whole repository.
_Avoid_: read scope, runtime lock, best-effort changed-path list

## Execution

**Dispatch**:
One assignment binding exactly one Issue, Worker, workspace, branch, and pull
request. Creating the Worker authorizes execution without a handshake.
_Avoid_: Work Package, multi-Issue bundle, Campaign lane

**Worker**:
A disposable Agent that implements one Dispatch and delivers its result through
one pull request. It owns no scheduling, integration, or cleanup authority.
_Avoid_: standing Agent, nested Orchestrator, multi-Issue Worker

**Reviewer**:
An optional, disposable Agent that evaluates one pull-request revision against
both the Issue Design and repository quality expectations.
_Avoid_: standing review pool, mandatory dual-axis pair

**WIP Slot**:
One repository-level allowance for an unfinished Dispatch from creation until
merge or explicit retirement. Review does not release it; a confirmed Human
Park temporarily does while preserving the Dispatch and WIP.
_Avoid_: running-Agent count, machine-wide Agent capacity

**Project Projection**:
An optional GitHub Project view of orchestration facts already owned by Issues,
pull requests, checks, and labels. Projection failure cannot block core work.
_Avoid_: control plane, task database, required Project template

**Retirement**:
The guarded cleanup of a completed or explicitly stopped Dispatch's disposable
runtime resources. A Coordinator and its Workspace are never retirement
targets.
_Avoid_: self-archive, campaign close, unconditional cleanup

**NEEDS-HUMAN**:
An explicit escalation for a product decision, unsafe state, or external gate
outside the Coordinator's authority.
_Avoid_: silent retry, guessed decision, indefinite prompting
