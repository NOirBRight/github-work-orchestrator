---
name: github-work-orchestrator
description: Orchestrate GitHub execution campaigns by aligning direction and architecture, reconciling priority, labels, and native dependencies after intake, computing the ready frontier, binding models, dispatching sidebar-visible Workers, reviewing PRs, and refilling capacity. Use when Codex must run a multi-Issue GitHub campaign, compute or refill its ready frontier, dispatch visible Workers, or arbitrate their integration.
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
4. Read [verification policy](references/shared/verification-policy.md) before
   classifying work, dispatching, or reviewing a PR.
5. Read [communication protocol](references/shared/communication-protocol.md)
   before dispatching, steering, or monitoring a visible task.
6. Read [issue contract](references/shared/issue-contract.md) and
   [reconciliation](references/reconciliation.md) before repairing Issue state.

Read the detailed [Worker contract](references/worker-contract.md) only when
materializing, activating, recovering, or replacing a Worker. Read detailed
[task-host recovery](references/communication.md) only when a Task-host failure
actually occurs. Do not load these low-frequency references for inventory,
frontier computation, or routine PR review.

Repository policy overrides this Skill's defaults. The control plane is loaded
when the repository, integration branch, label vocabulary, and authority
envelope are explicit.

## Keep intake and execution separate

Send routine bug reports, enhancement requests, screenshots, logs, and rough
ideas to a persistent task using `github-issue-intake` when it is available.
Consume only its material signals; routine drafting and duplicate-search
updates stay in the Intake task.

The Orchestrator may reconcile priority, labels, and native dependencies after
intake. Return incomplete Issue bodies to Intake instead of publishing routine
contract edits here. The Orchestrator does not implement production Issues. Every
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
ownership, the v2 verification class/commands are explicit, architecture is
resolved, and the model profile is explicit. Legacy active work may migrate
incrementally without restarting.

The frontier is safe when it contains only unassigned, fully specified Issues
whose blockers are closed and whose expected write sets can run concurrently.

## Dispatch visible Workers

1. Reconcile while the Issue remains unassigned and validate the normalized
   dispatch payload:

   ```text
   python <skill>/scripts/validate_execution_contract.py --input <json-file>
   ```

2. Follow the detailed
   [Worker contract](references/worker-contract.md) for exact materialization,
   preflight, claim, dispatch-comment, and edit-authorization order. Do not
   shorten that safety sequence in prose.
3. The dispatch payload includes verification class/commands, manual evidence,
   architecture decision, `Review-Owner: orchestrator`, requested model,
   verified effective-binding evidence/status, base branch and SHA, feature
   branch, ownership/hotset, blockers, callback, and PR target.
4. Materialize one isolated-worktree task using the two-stage flow. Send the
   full assigned-Issue contract only after a real task ID exists, and require
   `github-issue-worker`, the selected binding, its deterministic permission
   preflight, and the exact Orchestrator callback task ID.
5. Require the Worker to complete the shared
   [delivery handshake](references/shared/communication-protocol.md#delivery-handshake)
   before its final response.

If task materialization or permission preflight fails, follow the shared
recovery rules. A subagent, hidden process, or shared directory is not a Worker
fallback.

Dispatch is complete when one materialized visible task owns the Issue,
worktree, branch, callback, model binding, and verification contract.

## Review signals and refill

Process material Intake and Worker states with the shared
[Orchestrator verification](references/shared/communication-protocol.md#orchestrator-verification)
rules. Follow the shared
[signal-driven monitoring](references/shared/communication-protocol.md#signal-driven-monitoring)
cadence, including its GitHub-event recovery path when callback delivery is
missing.

When a Worker is ready:

1. On a locally green `PR_OPENED`, verify scope, base, diff, tests, PR target,
   accepted decisions, verification class, full-suite count, and the Worker's
   `Review-Runs: 0` report. Start eligible gates immediately; do not wait for
   `READY_FOR_REVIEW` merely to serialize independent work.
2. For `fast`, perform one direct scope/acceptance review without a review
   subagent. For `standard` or `strict`, run exactly one Orchestrator-owned
   parallel Standards/Spec review using the review model profiles. Treat an
   already completed in-flight formal review as that one review; do not repeat
   it solely because this policy was adopted mid-task.
3. Run CI observation, the one review, and safe candidate artifact/manual
   evidence concurrently. Prefer pre-merge manual evidence so a red behavior
   gate does not enter the integration branch.
4. Route revisions to the same visible task. After fixes, review only the
   changed delta and require targeted checks plus CI. Require another local
   full suite only when the fix crosses a new repository verification boundary.
5. Merge only after every applicable gate is green. Compare candidate and
   integrated Git trees after merge; rebuild or repeat behavioral evidence only
   for a tree delta, an explicit integrated-revision requirement, or a release
   artifact acceptance gate.
6. Serialize integration through declared hotsets.
7. Recompute the frontier after every material event.
8. Refill the highest-priority non-conflicting lane while authorization remains
   active.

Review and refill are complete when every received signal is verified once,
every integration collision is serialized, and all free authorized capacity is
either filled from the safe frontier or explained.
