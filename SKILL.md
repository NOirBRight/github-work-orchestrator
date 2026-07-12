---
name: github-work-orchestrator
description: Orchestrate GitHub Issues into safe parallel, sidebar-visible Codex tasks backed by isolated worktrees. Use when Codex needs to inspect or validate an issue queue, compute the ready frontier, classify work and bind model profiles, dispatch visible worker tasks without subagents, monitor issue/PR progress, or refill execution capacity across one or more repositories.
---

# GitHub Work Orchestrator

Use GitHub as the only persistent work-state source. Keep orchestration
stateless: reconstruct the current plan from Issues, native dependencies,
assignees, linked PRs, repository instructions, and visible Codex tasks.

## Operating boundaries

- Use visible Codex tasks in isolated worktrees. Never substitute subagents.
- Treat Issues, implementation tasks, bugs, investigations, reviews, incidents,
  and releases as work items.
- Keep Skill source and releases outside the target repository.
- Do not create an orchestration database, control Issue, duplicate status
  ledger, dashboard, MCP server, or background daemon.
- Do not add labels or modify repository policy unless the user explicitly
  authorizes it.
- Preserve dirty working trees. Create new worktrees from the repository's
  documented canonical integration branch and SHA.
- Use read-only preflight unless the user explicitly asks to start, dispatch,
  continue, or run work.

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
[references/worker-contract.md](references/worker-contract.md) before creating
or messaging a worker task.

## Run preflight

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

1. Re-run preflight immediately before the first write.
2. Claim the Issue using its assignee field. Keep `ready-for-agent` while it is
   active unless repository policy defines another transition.
3. Post one concise dispatch comment containing:
   - selected profile and concrete model;
   - canonical base branch and SHA;
   - proposed feature branch;
   - owned components or hot files;
   - required verification;
   - known blockers and integration parent.
4. Create one sidebar-visible Codex task with an isolated worktree. Title it
   `[#<number>] <issue title>`.
5. Apply the selected model and reasoning level. If task creation cannot honor
   the binding, stop and report the mismatch.
6. Send the Worker Contract plus the Issue URL and repository-specific rules.

If visible-task creation tools are unavailable, stop after the GitHub preflight.
Do not fall back to a subagent, hidden process, or shared working directory.

## Monitor and refill

Use native visible-task tools to list and read active workers. Derive state as
follows:

- ready: `ready-for-agent`, unassigned, and no open blocker;
- active: `ready-for-agent` and assigned;
- review/integration: linked open PR;
- human wait: `ready-for-human`;
- information wait: `needs-info`;
- complete: closed Issue after its intended merge and verification.

When a Worker reports completion:

1. Verify scope, diff, tests, base, and PR target.
2. Route revisions back to the same visible task.
3. Serialize merges through declared hotsets.
4. Recompute the frontier after every merge, blocker change, or released slot.
5. Start the highest-priority ready non-conflicting task when authorized to
   continue dispatching.

Never close an Issue merely because a local commit exists. Prefer PR closing
keywords when the PR fully resolves the Issue.

## Model changes

Keep profile names stable. Change concrete bindings in
[references/model-profiles.md](references/model-profiles.md), not on every
Issue. Record the exact binding in the dispatch comment for auditability.

Promote third-party models gradually through shadow, evidence, mechanical,
standard, core, and finally Orchestrator eligibility. Fall back to the profile's
verified default when a candidate is unavailable or unstable.
