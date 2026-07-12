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
| `BLOCKED` | Progress requires an Orchestrator decision, new authority, a broader task-host permission profile, a proven required upstream merge, missing evidence, or resolution of a merge-base-confirmed write-set collision |
| `PR_OPENED` | The branch is pushed and a PR exists; checks may still be running |
| `READY_FOR_REVIEW` | The assigned scope and required verification are complete enough for Orchestrator review |
| `STOPPED` | The Worker is ending without a reviewable result or must hand the work back |

Use this compact payload:

```text
WORKER_SIGNAL
- State: BLOCKED | PR_OPENED | READY_FOR_REVIEW | STOPPED
- Issue: #<number>
- Branch: <branch>
- Commit: <sha or none>
- PR: <URL or none>
- Verification: <passed, failed, pending, or not run>
- Hotset: <owned files/components>
- Blocker/next action: <concise request or handoff>
```

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

1. Read the task and GitHub state, then attempt one normal continuation when
   the branch/worktree remains intact.
2. If the same task fails again before producing a meaningful response, stop
   retrying that session. Use a native visible-task fork or handoff to create
   one successor on the same worktree/branch when supported.
3. Archive or otherwise stop the failed predecessor before the successor edits.
   Verify that exactly one visible task remains active for the Issue.
4. Tell the successor to inspect `git status` and the current diff before
   continuing so interrupted staged or unstaged work is preserved and reviewed.
5. Keep the existing Issue claim, branch, PR, callback, model profile, and
   authority boundaries. Do not publish task IDs or host errors to GitHub.

If same-worktree succession is unavailable or unsafe, preserve the branch
remotely and create one replacement worktree from that branch only after the
predecessor is inactive. Never run two Workers against one worktree.

## Orchestrator responsibilities

- Poll visible tasks and GitHub because reverse delivery can fail or arrive
  late. Use task status/unread state, task reads, assignees, PRs, and CI.
- Verify every signal against the Worker task and GitHub before changing
  lifecycle state, merging, or releasing capacity.
- Send revisions and decisions back to the same visible task.
- Decide whether a `BLOCKED` or `STOPPED` task releases its capacity and whether
  its GitHub claim remains. The Worker does not decide this implicitly.
- Recompute the ready frontier after a verified completion, merge, blocker
  change, or released slot. Keep no separate orchestration database.
