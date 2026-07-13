---
name: github-work-orchestrator
description: Align project direction, surface architecture decisions, standardize, reconcile, and orchestrate GitHub Issues into safe parallel, sidebar-visible Codex tasks backed by isolated worktrees. Use when Codex needs to run a long-lived execution campaign, discuss gray areas or architecture guardrails, normalize an issue backlog, repair labels or native dependencies, correct orchestration drift, compute the ready frontier, bind model profiles, dispatch visible worker tasks with explicit subagent boundaries, monitor issue/PR progress, or refill execution capacity across one or more repositories.
---

# GitHub Work Orchestrator

Use GitHub as the only persistent work-state source. Keep orchestration
stateless: reconstruct the current plan from Issues, native dependencies,
assignees, linked PRs, repository instructions, and visible Codex tasks.

## Operating boundaries

- Map each claimed GitHub work item to one visible Codex task in one isolated
  worktree. Use subagents only for bounded, non-owning assistance: pre-claim
  research or work inside the parent Worker's Issue. Never give a subagent a
  GitHub work item, branch/PR/lifecycle identity, or separate Worker role.
- Treat Issues, implementation tasks, bugs, investigations, reviews, incidents,
  and releases as work items.
- Keep Skill source and releases outside the target repository.
- Do not create an orchestration database, control Issue, duplicate status
  ledger, dashboard, MCP server, or background daemon.
- Reuse the repository's canonical labels. Do not create new label vocabulary
  or modify repository policy unless the user explicitly authorizes it.
- Preserve dirty working trees. Create new worktrees from the repository's
  documented canonical integration branch and SHA.
- Use read-only preflight for inspect, plan, review, or audit requests.
- Treat start, dispatch, continue, run, orchestrate, standardize, reconcile, or
  repair requests as authorization to apply in-scope GitHub corrections.
- Keep project direction, durable architecture, compatibility policy, and
  irreversible choices in a human-visible discussion loop. Workers may decide
  local reversible implementation details, not silently set project policy.

## Load project policy

Before querying work:

1. Read every applicable `AGENTS.md`.
2. Read the repository's issue-tracker, label, Git-flow, testing, and release
   instructions when present.
3. Infer the GitHub repository from `git remote`; use `--repo` only when the
   target differs from the current checkout.
4. Treat repository policy as an override of this Skill's defaults.

Read [references/lifecycle.md](references/lifecycle.md) before changing Issue
state. Read [references/model-profiles.md](references/model-profiles.md) before
selecting a worker model. Read
[references/decision-gates.md](references/decision-gates.md) before starting a
new long-running campaign or resolving a direction or architecture gray area.
Read
[references/worker-contract.md](references/worker-contract.md) before creating
or messaging a worker task. Read
[references/communication.md](references/communication.md) before dispatching,
steering, or monitoring visible tasks. Read
[references/reconciliation.md](references/reconciliation.md) before
standardizing Issues or applying drift repairs.

## Align direction before sustained execution

At the start of a project, a new Milestone, or a resumed campaign whose prior
direction is missing or stale, run the direction checkpoint in
[references/decision-gates.md](references/decision-gates.md). Present the
maintainer with a concise execution charter and the material unresolved choices
before the first new scheduling refill. Reuse accepted direction instead of
asking again on every turn.

Open a discussion gate when a choice would change product direction, a durable
architecture seam, a public or persisted contract, compatibility policy,
security posture, or multiple downstream work items. Do not open one for an
ordinary reversible implementation detail inside a clear Issue contract.

Pause only the affected hotset. Continue independent, already-clear work while
the discussion is resolved. Record accepted durable decisions in the project's
existing authoritative Issue, Milestone, domain document, or ADR; do not create
a second project ledger.

## Reconcile before scheduling

Run the state validator:

```text
python <skill>/scripts/validate_issue_state.py --cwd <repository>
```

Run the ready-frontier query:

```text
python <skill>/scripts/ready_frontier.py --cwd <repository> --json
```

Both scripts are read-only. Report contradictory state before dispatch. Do not
silently reinterpret an invalid Issue.

On an authorized orchestration run:

1. Infer the intended dependency graph and Issue contract from Issue bodies,
   native sub-issues, repository policy, accepted plans, and maintainer
   instructions.
2. Build an ephemeral reconciliation command. Do not store another state file
   in the target repository.
3. Preview deterministic corrections:

```text
python <skill>/scripts/reconcile_issue_state.py --cwd <repository> \
  --repair-safe <explicit status and dependency arguments>
```

4. Review the preview for semantic ambiguity or destructive edits.
5. Re-run with `--apply` for unambiguous corrections.
6. Re-run the validator and frontier. Continue only when there are no errors.

Automatically repeat reconciliation before every scheduling refill. Apply
safe, idempotent corrections without requesting confirmation again during an
already-authorized orchestration run. Route ambiguity to `needs-triage` or
`needs-info`; never conceal it.

For each ready candidate:

1. Confirm every native or textual blocker is closed.
2. Confirm the Issue is unassigned.
3. Extract acceptance criteria, verification commands, affected components, and
   likely hot files.
4. Classify it as orchestration, core, evidence, standard, mechanical, or
   light work.
5. Select a model profile.
6. Check it against active worktree ownership and the integration queue.

Return a proposed frontier without writes when the request is planning,
inspection, review, or preflight only.

## Select parallel work

Prefer independent tasks whose expected write sets do not overlap. Default
capacity, unless repository policy overrides it:

- one Orchestrator;
- up to three production-code workers;
- up to two evidence-only workers;
- one serialized queue for each shared integration hotset.

Evidence-only workers may exceed code capacity only when they do not modify
production routes or shared handlers. A blocked or human-waiting task releases
its capacity.

For bugs and incidents, assign one Debug Owner. Do not launch duplicate
investigations for the same symptom. Other tasks may reproduce or collect
evidence but must send it to that owner.

## Dispatch work

Dispatch only after explicit authorization.

Before creating or claiming a Worker, follow the authoritative
[materialization and claim order](references/worker-contract.md#reliable-task-materialization)
and [task-host recovery](references/communication.md#task-host-recovery).

A dispatch completes only when reconciliation passes; one real task has
completed the documented materialization/preflight handoff; the GitHub claim
and dispatch comment have been written; and the Worker has the documented
branch, hotset, verification, and callback boundaries. If that state is not
reached, complete the reference's release/rollback path before considering a
replacement.

## Monitor and refill

Use [communication.md](references/communication.md#monitoring-cadence) as the
authoritative monitoring cadence and
[task-host recovery](references/communication.md#task-host-recovery) for
failure handling.

On a material Worker signal, verify the visible task and GitHub state before an
integration action. Monitoring completes when the reported transition has been
verified, revisions have been routed to the same Worker when needed, and the
frontier has been recomputed after a merge, blocker change, or released slot.
Do not close an Issue merely because a local commit exists; use closing keywords
only for a PR that fully resolves it.

## Model changes

Keep profile names stable. Change concrete bindings in
[references/model-profiles.md](references/model-profiles.md), not on every
Issue. Record the exact binding in the dispatch comment for auditability.

Promote third-party models gradually through shadow, evidence, mechanical,
standard, core, and finally Orchestrator eligibility. Fall back to the profile's
verified default when a candidate is unavailable or unstable.
