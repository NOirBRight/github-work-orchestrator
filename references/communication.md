# Worker communication protocol

Treat a visible Worker as an independent user-owned Codex task. Do not assume
that its final answer is automatically returned to the Orchestrator. Combine
explicit Worker signals with Orchestrator polling.

## Dispatch setup

- Put the Orchestrator task ID in the private Worker prompt as the callback
  target. Use the delegation `source_thread_id` when it identifies the current
  Orchestrator.
- Never publish callback or Worker task IDs, local worktree paths, credentials,
  or private machine details to GitHub.
- Tell the Worker to keep its current model and reasoning settings when sending
  a callback.
- Keep the full plan, evidence, and completion report in the Worker task. A
  callback is a short notification, not a second state ledger.

## Required signals

Send each signal once per meaningful transition. Re-send only when its payload
materially changes.

| Signal | Send when |
|---|---|
| `DISCUSSION_REQUIRED` | A material product-direction, architecture, compatibility, security, migration, or cross-Issue choice exceeds the Worker's accepted authority |
| `BLOCKED` | Progress requires an Orchestrator decision, new authority, a broader task-host permission profile, a proven required upstream merge, missing evidence, or resolution of a merge-base-confirmed write-set collision |
| `PR_OPENED` | The branch is pushed and a PR exists; checks may still be running |
| `READY_FOR_REVIEW` | The assigned scope and required verification are complete enough for Orchestrator review |
| `STOPPED` | The Worker is ending without a reviewable result or must hand the work back |

Use this compact payload:

```text
WORKER_SIGNAL
- State: DISCUSSION_REQUIRED | BLOCKED | PR_OPENED | READY_FOR_REVIEW | STOPPED
- Issue: #<number>
- Branch: <branch>
- Commit: <sha or none>
- PR: <URL or none>
- Verification: <passed, failed, pending, or not run>
- Hotset: <owned files/components>
- Blocker/next action: <concise request or handoff>
```

For `DISCUSSION_REQUIRED`, replace the final line with:

```text
- Decision: <one precise choice>
- Options: <A/B/C with tradeoffs>
- Recommendation: <preferred path and why>
- Safe work: <what may continue while waiting>
```

Use this signal only after checking the accepted Issue, Milestone, domain docs,
and ADRs. Bundle related choices, recommend a path, and send it once. Do not use
it for routine implementation details or status updates.

Do not include secrets, raw traces, user content, or local paths. Put detailed
test output and evidence in the Worker task or authorized PR artifacts.

For a claimed upstream collision, the Worker task must retain the merge-base,
the Worker-side path set (committed plus staged plus unstaged), the upstream-only
path set, and their exact intersection. If the intersection is empty, continue
without a `BLOCKED` signal unless the work has a separate semantic dependency
on the upstream change.

## Delivery and fallback

1. Use native visible-task messaging, such as `send_message_to_thread`, to send
   the payload to the callback target. Omit model and reasoning overrides.
2. If reverse messaging is unavailable, write the full final or blocked report
   in the Worker task and stop cleanly. Do not create a duplicate Orchestrator
   task or substitute a subagent channel.
3. Update GitHub only within the Worker's existing authority. Do not add Issue
   comments merely to emulate task messaging unless the dispatch authorized it.
4. Do not treat callback delivery failure as permission to merge, close,
   unassign, or start another GitHub work item.

## Task-host recovery

A task-level `systemError`, host disconnect, or failed continuation does not
change the GitHub claim and is not a repository blocker.

Creation failure happens before a Worker exists. When task creation returns a
client-side ID or creates a worktree but no real task/rollout materializes:

1. Confirm absence through the native task list; do not infer success from the
   worktree alone and do not mark the Issue active.
2. Preserve the client-side ID privately for diagnosis. Never publish it or a
   local worktree path to GitHub.
3. Stop after the first failure and identify a concrete host-startup cause.
   Make at most one bounded replacement attempt after the cause is removed or
   isolated, using the bootstrap flow in `worker-contract.md`.
4. Clean up failed stubs only through a supported native client action, such as
   the Task archive API or `codex delete`. If both reject a stub because no
   real session exists, record the client cleanup defect and do not edit
   Codex's internal databases or claim cleanup.
5. Ensure the replacement is renamed only after materialization so exactly one
   canonical `[#<number>] <issue title>` task is active.

1. Read the task and GitHub state, then attempt one normal continuation when
   the branch/worktree remains intact.
2. If the same task fails again before producing a meaningful response, stop
   retrying that session. Use a native visible-task fork or handoff to create
   one successor on the same worktree/branch when supported.
3. Before archiving any task that is the sole durable owner of uncommitted
   work, protect that work from client cleanup. A same-directory fork is not a
   durable backup when archiving either task may remove the shared worktree.
   While every Worker is idle and no concurrent edit can occur, create a
   scoped checkpoint commit on the existing feature branch, verify it contains
   no transient or sensitive artifacts, and push it to the existing remote
   branch. If the effective permissions or network cannot create and verify
   that checkpoint, do not archive or clean up the task; stop and report the
   recovery requirement.
4. Archive or otherwise stop the failed predecessor only after the worktree is
   clean or the remote checkpoint is verified. Then allow the successor to
   edit and verify that exactly one visible task remains active for the Issue.
5. Tell the successor to inspect `git status` and the current diff before
   continuing so interrupted staged or unstaged work is preserved and reviewed.
6. Keep the existing Issue claim, branch, PR, callback, model profile, and
   authority boundaries. Do not publish task IDs or host errors to GitHub.

If same-worktree succession is unavailable or unsafe, or if its inherited
permission profile is too narrow, preserve and verify the branch remotely
before archiving the sole worktree owner. Create one replacement worktree from
that branch only after the predecessor is inactive. Never run two Workers
against one worktree.

## Orchestrator responsibilities

- Poll visible tasks and GitHub because reverse delivery can fail or arrive
  late. Use task status/unread state, task reads, assignees, PRs, and CI.
- Verify every signal against the Worker task and GitHub before changing
  lifecycle state, merging, or releasing capacity.
- Send revisions and decisions back to the same visible task.
- Consolidate related `DISCUSSION_REQUIRED` signals into one maintainer packet,
  record the accepted decision in the authoritative GitHub or architecture
  source, and update affected Worker contracts before resuming.
- Decide whether a `BLOCKED` or `STOPPED` task releases its capacity and whether
  its GitHub claim remains. The Worker does not decide this implicitly.
- Recompute the ready frontier after a verified completion, merge, blocker
  change, or released slot. Keep no separate orchestration database.

## Monitoring cadence

Prefer Worker signals over polling. Monitoring is a recovery fallback, not a
live transcript of a Worker's implementation steps.

- During reliable task materialization, poll only as needed to distinguish a
  real task ID and completed bootstrap from a client-side stub. After sending
  the full contract, allow one read to verify the permission preflight.
- Once preflight passes, do not read the same active Worker again for ordinary
  progress until it sends a material signal. If reverse delivery is suspected
  to be unavailable, wait at least ten minutes between fallback reads.
- Treat an explicit maintainer status request, a Worker signal, a declared
  command/test completion deadline, a CI state transition, or a handoff/task
  recovery operation as an event that permits one immediate verification read.
- When a Worker states that a long test suite or CI run is in progress, wait for
  its signal or the stated/normal completion window. Do not poll its reasoning
  stream, commentary, file edits, or unchanged active status minute by minute.
- Report only material transitions: permission preflight passed/failed,
  discussion required, blocker, checkpoint/PR publication, review readiness,
  CI failure/root cause, merge, or stopped work. Do not relay routine internal
  hypotheses or every test command to the maintainer.
- A user-facing progress update does not require another Worker read. Use the
  last verified state and clearly label it as such.

The ten-minute floor does not apply to the short bootstrap materialization
check, a bounded handoff status wait, or verification immediately triggered by
a new signal. It also does not require waiting when GitHub itself has already
recorded a new PR/check/merge transition.
