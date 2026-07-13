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

### Creation failure: no Worker exists

This branch applies only when native task discovery confirms that no real task
exists. Use [the materialization flow](worker-contract.md#reliable-task-materialization)
for claim ordering and release; a returned client ID, a bootstrap request, or a
created worktree does not create an Active Worker.

1. Confirm the absence through the native task list and keep the client ID and
   local path private. If a real task exists but its full-contract or preflight
   turn failed, use the existing-Worker branch below; it remains unclaimed
   under the materialization order.
2. Diagnose an orphan worktree only from its recorded, exact absolute path and
   expected base SHA. Before reusing or cleaning it, require all of the
   following: the exact directory exists; its resolved path matches the
   recorded absolute path; `git -C <path> rev-parse HEAD` equals the expected
   base; `git -C <path> status --porcelain` is empty; `git worktree list
   --porcelain` identifies that exact path in the expected repository; and no
   native task or dispatch record can also own it.
3. If the directory is dirty, wrong-base, missing, or ambiguously owned, stop
   safely. Do not reuse, clean, infer another path, or retry the same creation
   path from that evidence.
4. Reuse is permitted only after every check passes and the recovery Task is
   explicitly given that exact absolute path as its write boundary. A branch
   name, parent directory, client ID, or inferred worktree location is not a
   write boundary.
5. Clean a rejected orphan only after the same checks prove it is clean and
   uniquely owned by the failed creation and no recovery Task has adopted it.
   Invoke a supported native task/worktree cleanup action against that literal
   absolute path only; never use a glob, parent path, or Codex internal
   database. If native cleanup is unavailable, record the defect and leave the
   directory untouched.
6. Make at most one replacement attempt after a concrete startup cause has
   been removed or isolated. Rename only a materialized task so there is never
   a second canonical `[#<number>] <issue title>` Worker.

This branch completes only when either a real Worker resumes the materialization
flow and reaches its preflight gate, or the unclaimed dispatch is safely stopped
with the orphan explicitly adopted, cleaned, or left untouched because a safety
check failed. In either outcome, no queued client ID is an Active claim.

### Existing Worker failure: a real Worker exists

This branch applies only after native discovery identifies the real visible
Worker. Read its task and GitHub state, then attempt one normal continuation
while its branch and worktree remain intact.

1. If the same task fails again before a meaningful response, stop retrying
   that session. Use one supported native visible-task fork or handoff on the
   same worktree/branch only when it preserves a single editing owner.
2. Before archiving the sole durable owner of uncommitted work, ensure every
   Worker on that worktree is idle; create a scoped checkpoint on the existing
   branch; verify it has no transient or sensitive artifacts; and push and
   verify its remote SHA. If that cannot be done, leave the task and worktree
   intact and report the recovery requirement.
3. Archive or stop the predecessor only after the worktree is clean or the
   remote checkpoint is verified. Tell the successor to inspect `git status`
   and the current diff before editing. Keep the Issue claim, branch, PR,
   callback, model profile, and authority boundaries intact.
4. When same-worktree succession is unsafe or unavailable, preserve and verify
   the branch remotely, deactivate the predecessor, then create one replacement
   worktree from that branch. Never run two Workers against one worktree.

This branch completes only when useful WIP is durable and exactly one visible
Worker has revalidated its branch, worktree, permissions, and ownership. A
failed initial full-contract/preflight turn is an existing-Worker failure, but
it leaves the Issue unclaimed instead of leaving a false Active Issue.

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

This maintainer-requested addendum governs Orchestrator monitoring of a real,
preflight-passed Worker. It is not a generic permission to poll a quiet task or
to replace the creation and recovery branches above.

- Prefer Worker signals and GitHub events. During materialization, read only
  enough to distinguish a real task and completed bootstrap, plus one
  post-contract preflight verification read.
- Once preflight passes, do not read the same Worker for ordinary progress just
  because no new event has arrived. A missing progress event alone is not
  evidence that reverse callback delivery is unavailable.
- Use a fallback read no more often than every ten minutes only when reverse
  callback delivery is absent, has failed, or is reasonably suspected to be
  unavailable. Otherwise wait for a material signal or GitHub event.
- One immediate verification read is permitted after a Worker signal, explicit
  maintainer status request, declared command/test deadline, recovery action,
  or a GitHub PR, check, push, or merge transition.
- Report only material transitions. Do not relay routine reasoning, file edits,
  unchanged active status, or minute-by-minute test progress. A user-facing
  progress update may use the last verified state.

The ten-minute floor does not apply to short bootstrap materialization, one
bounded recovery wait, or a verification read triggered by a new signal or
GitHub event.
