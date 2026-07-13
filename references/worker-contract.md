# Visible Worker Contract

Create one sidebar-visible Codex task per work item in an isolated worktree.
Never use a subagent as the Worker or dispatch target for a real GitHub work
item. The visible task is the auditable owner of the Issue, branch, PR, and
completion evidence.

The visible Worker may use subagents internally for bounded implementation
slices, research, parallel review, test analysis, or independent verification
within the same assigned Issue and worktree. The visible Worker partitions
write sets, integrates and reviews all results, and remains the only Worker of
record. Subagents do not own GitHub work items or create a separate lifecycle.
If discovered work is itself a distinct GitHub Issue, return it to the
Orchestrator for a new visible task instead of assigning it to a subagent.

## Contents

- [Required task identity](#required-task-identity)
- [Reliable task materialization](#reliable-task-materialization)
- [Worker activation handoff](#worker-activation-handoff)
- [Initial Worker message](#initial-worker-message)
- [Task-host permission preflight](#task-host-permission-preflight)
- [Worker behavior](#worker-behavior)
- [Worker signals](#worker-signals)
- [Completion report](#completion-report)

## Required task identity

- Title: `[#<number>] <issue title>`
- Base: documented canonical integration branch and exact SHA
- Branch: repository convention, default
  `codex/issue-<number>-<short-slug>`
- Target: repository integration branch, not the release branch
- Scope: one Issue or one explicitly approved tightly coupled unit

## Reliable task materialization

This section is the single authority for queued-request creation, bootstrap
eligibility, and no-real-task discovery. Normal materialization enters the
shared [Worker activation handoff](#worker-activation-handoff) only after a
real Task passes its bootstrap gate. A real unclaimed Task sent back from
Existing Worker failure enters that handoff as a fresh no-edit recovery gate;
it never treats a prior failed bootstrap as passed. Read
[creation-failure recovery](communication.md#creation-failure-no-worker-exists)
for orphan-worktree diagnosis, reuse, and cleanup after a creation failure.

Keep the candidate unassigned until a real Worker has passed preflight. That
makes the lifecycle's assignee-derived Active state truthful rather than a
placeholder for a queued client request.

1. Reconcile the candidate and record its exact base SHA, branch, hotset, and
   expected verification while it remains unassigned.
2. Select the bootstrap binding through the
   [model-profile selection order](model-profiles.md#selection-order). Before
   queueing it, record the exact model and reasoning effort plus one concrete
   eligibility observation: the task-host catalog or creation API lists that
   model/effort as supported, or the same host completed an exact `READY`
   bootstrap with that binding in the current run. Without either observation,
   do not queue the request.
3. Record one concrete, per-run discovery bound in the visible Orchestrator
   task before waiting: an absolute UTC deadline or a maximum number of native
   discovery checks, plus either the exact UTC read times or a stated cadence
   and that maximum. Do not infer a hidden universal timeout or extend the
   recorded bound or read schedule.
4. Create the isolated-worktree task at that exact base with the recorded
   eligible bootstrap binding. Send only
   `[#<number>] Bootstrap only. Reply exactly READY. Do not use tools.`
5. Read only at that recorded schedule for a real task ID in the native task
   list. A client-side creation ID or a created worktree alone is only
   diagnostic evidence, not a Worker. Do not title an unmaterialized stub.
6. If no real Task appears before the discovery bound expires, exit only to
   Creation failure: do not add the assignee or dispatch comment, and preserve
   the client ID privately. Before that branch may adopt, reuse, clean, or
   replace its worktree, it must obtain a supported native cancellation or
   terminal-failure result for that exact queued request and then verify that no
   real Task appeared. Without that proof, classify ownership as ambiguous and
   leave the path untouched; a late materialization must remain the only
   possible editor.
7. When a real Task appears, record a distinct post-materialization
   bootstrap-turn bound before reading its completion: an absolute UTC deadline
   or maximum number of native turn-state/content checks, plus exact UTC read
   times or a stated cadence and that maximum. Do not extend it or reuse the
   discovery bound.
8. Within that bound and schedule, require the bootstrap turn to complete
   without error, emit the exact `READY` reply, and leave the real Task idle.
   Do not rename or send a full contract while the turn is pending, errors, or
   omits that exact reply.
9. If a real Task's bootstrap turn errors, omits `READY`, or does not reach
   idle before the post-materialization bound expires, leave the Issue
   unclaimed and exit only to Existing Worker failure.
10. Only after the successful bootstrap gate, rename the real task to
    `[#<number>] <issue title>` and enter the shared
    [Worker activation handoff](#worker-activation-handoff).

Before the single permitted replacement, record in the visible Orchestrator
task the failed observation, the hypothesized startup cause, the authorized
reversible isolation or remediation, and a named post-remediation probe with
the result that proves the cause removed or isolated. Do not attempt the
replacement until that probe produces its recorded proving result. This is a
procedural gate; the executable alternate-model guard remains residual AC4.

## Worker activation handoff

This is the sole authority for activating either a normally materialized Task
or an admitted recovery Task. An unclaimed Task returning after a failed
bootstrap, full-contract, or preflight gate starts this fresh no-edit
full-contract/preflight gate; only the final claim-confirmation continuation
authorizes editing.

1. Record a distinct, concrete full-contract/preflight-turn bound in the
   visible Orchestrator task before sending the contract: an absolute UTC
   deadline or a maximum number of native turn-state checks, plus exact UTC
   read times or a stated cadence and that maximum. Do not extend it or its
   schedule. Its expiration exits only to Existing Worker failure.
2. Send the full Initial Worker message and Worker Contract to the exact
   sidebar-visible Task with the selected model binding and reasoning level.
   For a recovery Task, make the adopted absolute path its only write boundary
   and require the permission/repository preflight to run from that path.
3. Require a preflight-only turn: the Task runs no edits and emits the exact
   marker `PREFLIGHT_READY` only after the required preflight succeeds; it then
   becomes idle. If it cannot run the full contract or preflight, it reports
   the failure without the marker and without editing.
4. Within the full-contract/preflight-turn bound and its schedule, wait for
   native idle/turn completion, then make one authoritative content read to
   confirm the marker and required preflight. An earlier read is not this
   completion read and does not consume it or deadlock activation.
5. Immediately before the first GitHub lifecycle write, re-run/re-read the
   Issue, the native Task, and worktree ownership. Require the Issue to remain
   open, `ready-for-agent`, unblocked, and unassigned; the exact idle,
   preflight-passed Task to be its unique visible mapping; and the recorded
   branch, base SHA, worktree, and write boundary to be unchanged.
6. Only after that revalidation, add the GitHub assignee claim. Immediately
   re-read the Issue and verify that the exact intended assignee is the only
   claim and that no other pre-claim condition drifted before posting one
   concise dispatch comment or sending a `CLAIM_CONFIRMED` continuation with
   the branch, hotset, verification, callback, and write boundaries. Only this
   continuation authorizes scoped edits.

If the full-contract/preflight-turn bound expires, the turn errors, the marker
is absent, or the Task does not become idle, leave the Issue unclaimed and exit
only to Existing Worker failure. Do not post a dispatch comment. On any
pre-claim drift, make no GitHub write. On a post-claim race or failed
verification, re-read the Issue and remove only the exact assignee added by this
dispatch when that removal is unambiguous and cannot disturb another owner;
verify the release. Otherwise stop for maintainer review. In every race outcome,
do not post a dispatch comment, send `CLAIM_CONFIRMED`, or authorize editing.

Activation is complete only when one real Worker has the full contract, has
sent `PREFLIGHT_READY` and become idle, has passed the authoritative preflight
read and the pre/post-claim race checks, and has received the
`CLAIM_CONFIRMED` continuation and exact work boundaries. Never leave a queued
ID or failed preflight as an Active claim.
Keep the bootstrap prompt short and uniquely Issue-scoped. If an optional
startup service is suspected, use a bounded A/B test and disable it only after
proof and separate authorization for a reversible change.

## Initial Worker message

Include:

1. Issue URL and full acceptance criteria.
2. Applicable repository instructions.
3. Selected model profile and concrete binding.
4. Base branch/SHA and branch name.
5. Owned components, expected files, and prohibited hotsets.
6. Accepted direction, architecture invariants, decision references, and the
   Worker's local decision authority.
7. Known dependencies and required integration parent.
8. Targeted and full verification commands.
9. Required PR target and closing semantics.
10. The Orchestrator callback task and the required Worker signals from the
   [communication protocol](communication.md).
11. During activation, the recorded full-contract/preflight-turn bound and the
    preflight-only `PREFLIGHT_READY`/idle handshake; editing begins only after
    the `CLAIM_CONFIRMED` continuation.

Require the Worker to post a short implementation or investigation plan before
editing. A plan must identify expected writes, flag collisions, and state
whether any material decision gate is already visible.

## Task-host permission preflight

Treat permissions as task-host state, not as authority that a Worker prompt can
grant. Before editing, publishing, or running an expensive suite:

1. Report the effective sandbox and approval profile exposed to the task.
2. Run `git status --short --branch` in the assigned worktree.
3. When GitHub access is required, run one read-only identity or repository
   query such as `gh api user` or `gh repo view`.
4. Continue only when these commands run without an approval prompt and the
   effective profile satisfies the dispatch contract. Otherwise send one
   `BLOCKED` signal and stop before doing work.

Do not use destructive commands, credential changes, or writes outside the
worktree merely to prove that a broad permission profile exists. If the task
creation API has no permission field, inherit the user's current project or
environment setting and verify it through this preflight.

## Worker behavior

- Preserve unrelated and pre-existing changes.
- Do not merge, reset, force-push, publish, or change Issue state without
  explicit authority.
- Stay inside the assigned Issue and worktree.
- Keep all subagent work subordinate to this task's Issue, branch, worktree, and
  final review. Do not delegate a second GitHub work item through a subagent.
- Rebase or merge an upstream seam only when the assigned work semantically
  depends on it or a merge-base comparison proves a real write-set collision.
  An advanced integration branch by itself is not a blocker; evidence work may
  intentionally preserve its pinned base when the PR remains mergeable.
- Before reporting an upstream write-set collision, compute one common base and
  compare each side independently:

  ```text
  git merge-base HEAD origin/<integration-branch>
  git diff --name-only <merge-base>..HEAD
  git diff --cached --name-only
  git diff --name-only
  git diff --name-only <merge-base>..origin/<integration-branch>
  ```

  The Worker-side set is the union of committed, staged, and unstaged paths.
  Intersect that set with the upstream-only set. Never use
  `HEAD..origin/<integration-branch>` or a two-branch aggregate diff as proof
  that independently added files overlap.
- Use report-only quality gates as reports when repository policy says they are
  non-blocking.
- Decide reversible local implementation details inside the accepted contract.
  Do not silently choose project direction, redefine a durable architecture
  seam, or change public compatibility, security, or migration policy. Send
  `DISCUSSION_REQUIRED` with a decision packet when those choices arise.
- Stop and report when acceptance criteria conflict, a blocker is discovered,
  the model binding is unavailable, or required authority is missing.
- For investigations, keep production behavior unchanged unless implementation
  was explicitly assigned.

## Worker signals

Send `DISCUSSION_REQUIRED`, `BLOCKED`, `PR_OPENED`, `READY_FOR_REVIEW`, and
`STOPPED` signals to the Orchestrator as defined in
[communication.md](communication.md). Use native visible-task messaging when
available and keep the current model settings. Always leave the full evidence
in this visible task; a callback is only a concise notification.

## Completion report

Return:

- outcome and remaining gaps;
- changed files and important decisions;
- commits and PR URL, if created;
- targeted and full verification with exact results;
- newly discovered blockers, follow-up Issues, and hot-file ownership;
- whether the Issue can close.

The Orchestrator reviews this evidence before merging or releasing the slot.
