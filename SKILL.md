---
name: github-work-orchestrator
description: Align project direction, surface architecture decisions, standardize, reconcile, and orchestrate GitHub Issues into safe parallel, sidebar-visible Codex tasks backed by isolated worktrees. Use when Codex needs to run a long-lived execution campaign, discuss gray areas or architecture guardrails, normalize an issue backlog, repair labels or native dependencies, correct orchestration drift, compute the ready frontier, bind model profiles, dispatch visible worker tasks with explicit subagent boundaries, monitor issue/PR progress, or refill execution capacity across one or more repositories.
---

# GitHub Work Orchestrator

Use GitHub as the only persistent work-state source. Keep orchestration
stateless: reconstruct the current plan from Issues, native dependencies,
assignees, linked PRs, repository instructions, and visible Codex tasks.

## Operating boundaries

- Use one visible Codex task in an isolated worktree for every dispatched or
  claimed GitHub work item. Never substitute a subagent for that task's
  identity, ownership, branch, PR, or lifecycle state.
- The Orchestrator may use subagents for bounded research, inventory,
  dependency analysis, or model classification that is not itself a claimed
  GitHub work item. Keep this assistance read-only by default; an explicitly
  scoped research artifact remains owned and reviewed by the Orchestrator. A
  research subagent must not claim, mutate lifecycle state, or execute a real
  GitHub work item.
- A visible Worker may use subagents internally for bounded implementation
  slices, research, review, test analysis, or independent checks inside the
  same assigned Issue and worktree. The visible Worker owns write-set
  partitioning, integration, and final review, and must not turn a subagent into
  a hidden implementation stream for another GitHub Issue.
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

1. Re-run reconciliation immediately before the dispatch claim.
2. Claim the Issue using its assignee field. Keep `ready-for-agent` while it is
   active unless repository policy defines another transition.
3. Post one concise dispatch comment containing:
   - selected profile and concrete model;
   - canonical base branch and SHA;
   - proposed feature branch;
   - owned components or hot files;
   - accepted architecture invariants and decision references;
   - required verification;
   - known blockers and integration parent.
4. Create one sidebar-visible Codex task with an isolated worktree. Title it
   `[#<number>] <issue title>`.
5. Apply the selected model and reasoning level. If task creation cannot honor
   the binding, stop and report the mismatch.
6. Require the user-configured task host to provide the requested permission
   profile. When task creation exposes no permission argument, do not pretend a
   prompt can grant it; run the Worker Contract's permission preflight and stop
   if the effective profile is narrower or requests approval.
7. Send the Worker Contract plus the Issue URL and repository-specific rules.
   Include the Orchestrator task as the callback target and require the signals
   in the Worker communication protocol.

If visible-task creation tools are unavailable, stop after the GitHub preflight.
Do not fall back to a subagent as the work-item Worker, a hidden process, or a
shared working directory. Research assistance does not satisfy dispatch.

## Monitor and refill

Use native visible-task tools to list and read active workers. Derive state as
follows:

- ready: `ready-for-agent`, unassigned, and no open blocker;
- active: `ready-for-agent` and assigned;
- review/integration: linked open PR;
- human wait: `ready-for-human`;
- information wait: `needs-info`;
- complete: closed Issue after its intended merge and verification.

Require Workers to signal `DISCUSSION_REQUIRED`, `BLOCKED`, `PR_OPENED`,
`READY_FOR_REVIEW`, or `STOPPED` to the Orchestrator through native task
messaging when available.
Treat signals as prompts to verify, not as authoritative lifecycle changes.
Reverse delivery is not guaranteed, so poll visible tasks and GitHub as the
fallback. Keep callback task IDs out of GitHub.

When a Worker reports completion:

1. Verify scope, diff, tests, base, and PR target.
2. Route revisions back to the same visible task.
3. Serialize merges through declared hotsets.
4. Recompute the frontier after every merge, blocker change, or released slot.
5. Start the highest-priority ready non-conflicting task when authorized to
   continue dispatching.

When a Worker reports `DISCUSSION_REQUIRED`, verify that the trigger is
material, consolidate related choices into one discussion packet, and route it
through the decision gate. Do not turn routine progress updates into discussion
traffic.

Never close an Issue merely because a local commit exists. Prefer PR closing
keywords when the PR fully resolves the Issue.

## Model changes

Keep profile names stable. Change concrete bindings in
[references/model-profiles.md](references/model-profiles.md), not on every
Issue. Record the exact binding in the dispatch comment for auditability.

Promote third-party models gradually through shadow, evidence, mechanical,
standard, core, and finally Orchestrator eligibility. Fall back to the profile's
verified default when a candidate is unavailable or unstable.
