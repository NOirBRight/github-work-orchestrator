---
name: github-work-orchestrator
description: Orchestrate GitHub execution campaigns by aligning direction and architecture, reconciling priority, labels, and native dependencies after intake, computing the ready frontier, binding models, dispatching sidebar-visible Workers, reviewing PRs, and refilling capacity. Use for project planning, scheduling, dispatch, cross-Issue arbitration, Worker or PR review, and signal-driven campaign monitoring.
---

# GitHub Work Orchestrator

Keep GitHub as the only persistent work-state source. Reconstruct the current
plan from Issues, native dependencies, assignees, linked PRs, repository policy,
and visible Codex tasks.

## Load the control plane

Before planning or writing:

1. Read every applicable `AGENTS.md` and the repository's issue-tracker,
   label, Git-flow, testing, and release instructions.
2. Read [GitHub state rules](references/shared/github-state-rules.md) and
   [lifecycle](references/shared/lifecycle.md).
3. Read [model profiles](references/shared/model-profiles.md) before selecting
   a model.
4. Read [communication protocol](references/shared/communication-protocol.md)
   before dispatching, steering, or monitoring a visible task.
5. Read [issue contract](references/shared/issue-contract.md) and
   [reconciliation](references/reconciliation.md) before repairing Issue state.

Repository policy overrides this Skill's defaults. The control plane is loaded
when the repository, integration branch, label vocabulary, and authority
envelope are explicit.

## Keep intake and execution separate

Send routine bug reports, enhancement requests, screenshots, logs, and rough
ideas to a persistent task using `github-issue-intake` when it is available.
Consume only its material signals; routine drafting and duplicate-search
updates stay in the Intake task.

The Orchestrator may reconcile priority, labels, contracts, and native
dependencies after intake. It does not implement production Issues. Every
claimed Issue executes in one sidebar-visible Worker task using
`github-issue-worker`; bounded subagents may assist analysis or review but do
not own a GitHub work item.

This boundary is complete when every active work item has exactly one visible
owner and routine intake is outside the Orchestrator task.

## Align direction

Read [decision gates](references/decision-gates.md) at the start of a project,
a new Milestone, or a campaign whose accepted direction is stale. Present an
execution charter and material unresolved choices before the first refill.

Open a discussion gate for product direction, durable architecture, public or
persisted contracts, compatibility, security, irreversible migrations, or a
choice that changes multiple downstream work items. Pause only the affected
hotset and record the accepted decision in the project's existing
authoritative source.

Direction is aligned when the target outcome, non-goals, architecture
invariants, authority envelope, and every material open choice are visible.

## Reconcile and compute the frontier

Run the read-only checks:

```text
python <skill>/scripts/validate_issue_state.py --cwd <repository>
python <skill>/scripts/ready_frontier.py --cwd <repository> --json
```

For an authorized orchestration run, preview deterministic corrections before
applying them:

```text
python <skill>/scripts/reconcile_issue_state.py --cwd <repository> \
  --repair-safe <explicit status and dependency arguments>
```

Review the preview, add `--apply` only for unambiguous corrections, then rerun
the validator and frontier. Reconcile again after every merge, accepted
decision, blocker change, Worker failure, or released slot.

For each candidate, confirm blockers are closed, the Issue is unassigned, the
contract is fresh-worker-ready, the expected hotset is compatible with active
ownership, and the model profile is explicit. Return a proposal without writes
for planning, inspection, review, or preflight requests.

The frontier is safe when it contains only unassigned, fully specified Issues
whose blockers are closed and whose expected write sets can run concurrently.

## Dispatch visible Workers

Dispatch only after authorization:

1. Reconcile immediately before the claim.
2. Claim the Issue through its assignee field and post one dispatch comment
   with profile/model, base branch and SHA, feature branch, ownership/hotset,
   decisions, verification, blockers, and PR target.
3. Materialize one isolated-worktree task using the two-stage flow in
   [GitHub state rules](references/shared/github-state-rules.md#reliable-task-materialization).
4. Send the full assigned-Issue contract only after a real task ID exists.
   Require `github-issue-worker`, the selected binding, permission preflight,
   and the exact Orchestrator callback task ID.
5. Require the Worker to call native `send_message_to_thread` with the shared
   signal envelope before its final response. A final answer in the Worker task
   is not callback delivery.

If task materialization or permission preflight fails, follow the shared
recovery rules. A subagent, hidden process, or shared directory is not a Worker
fallback.

Dispatch is complete when one materialized visible task owns the Issue,
worktree, branch, callback, model binding, and verification contract.

## Review signals and refill

Treat `ISSUE_READY`, `DUPLICATE`, `NEEDS_INFO`, `DISCUSSION_REQUIRED`,
`BLOCKED`, `PR_OPENED`, `READY_FOR_REVIEW`, and `STOPPED` as prompts to verify,
not as authoritative state changes. Deduplicate by `Signal-ID`, verify against
the sender task and GitHub, then act inside the Orchestrator's authority.

Use signal-driven monitoring. After materialization and permission preflight,
fallback reads of the same active Worker remain at least ten minutes apart
unless a signal, explicit maintainer request, declared deadline, recovery
operation, or GitHub state transition permits one immediate read. A PR/check
transition is a valid fallback event when callback delivery is missing.

When a Worker is ready:

1. Verify scope, base, diff, tests, PR target, and accepted decisions.
2. Route revisions to the same visible task.
3. Serialize integration through declared hotsets.
4. Recompute the frontier after every material event.
5. Refill the highest-priority non-conflicting lane while authorization remains
   active.

Review and refill are complete when every received signal is verified once,
every integration collision is serialized, and all free authorized capacity is
either filled from the safe frontier or explained.
