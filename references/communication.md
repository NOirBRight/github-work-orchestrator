# Worker communication protocol

Treat a visible Worker as an independent user-owned Codex task. Do not assume
that its final answer is automatically returned to the Orchestrator. Combine
explicit Worker signals with the conditional fallback in
[Monitoring cadence](#monitoring-cadence).

## Contents

- [Dispatch setup](#dispatch-setup)
- [Required signals](#required-signals)
- [Delivery and fallback](#delivery-and-fallback)
- [Task-host recovery](#task-host-recovery)
- [Orchestrator responsibilities](#orchestrator-responsibilities)
- [Monitoring cadence](#monitoring-cadence)

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

The [Worker Contract](worker-contract.md) is the sole authority for queued
materialization, activation, claimed-Worker recovery, and succession. This
section owns recovery evidence, safe orphan handling, WIP disposition, and
monitoring; it does not recreate an activation or succession sequence.

### Creation failure: no Worker exists

This branch begins only when the materialization flow routes the exact original
queued request to a no-real-Task outcome. Absence from a general native task
list is not terminal ownership of that request, an orphan, or a replacement.

1. Record the exact original queued-request identity privately. Obtain a
   supported native cancellation or terminal-failure result for that original
   request, then immediately verify that no real Task belonging to it appeared.
   If either fact is absent or ambiguous, record ambiguous ownership, leave the
   worktree untouched, and stop this recovery. A late original Task aborts this
   branch; do not bind it to a recovery path or create another editor.
2. Record the exact terminal/cancelled result and no-original-Task read in the
   visible Orchestrator task with the orphan evidence: literal and resolved
   absolute paths, expected base SHA, actual `HEAD`, exact `git status
   --porcelain` result, matching `git worktree list --porcelain` repository
   entry, and native/GitHub ownership and admission facts. Record `missing` or
   a command error as the measured value. Never publish request identities,
   this evidence, or local paths to GitHub.
3. Diagnose an orphan only from that recorded literal path and expected base.
   Reuse or cleanup requires the directory to exist, paths to match, actual
   `HEAD` to equal the expected base, status to be empty, the worktree entry to
   identify the expected repository, and ownership to be unambiguous. A dirty,
   wrong-base, missing, or ambiguous directory stops safely without inferred
   paths or retries on that path.
4. Immediately before adoption, reuse, or cleanup, re-read the original
   request's exact terminal/cancelled result and native Task state, then append
   both results to the private record. If the original is not proven terminal
   with no real Task, leave the path untouched and stop that action.
5. Record an exact recovery candidate Task identity and its source-request
   identity separately from the original request. Prove the candidate is not a
   late original Task. A newly created candidate must complete the authoritative
   [materialization flow](worker-contract.md#reliable-task-materialization)
   before it reaches activation. A safe existing candidate must, immediately
   before adoption, be proven real and sidebar-visible, native-idle with no
   active editing turn, admitted for this exact Issue and mapped to no other
   GitHub work item, branch, PR, or write boundary, and bound to no worktree
   other than the exact orphan path or none. Record those observable admission
   facts and its exact path
   boundary privately. Then and only then route it to the
   [Worker activation handoff](worker-contract.md#worker-activation-handoff).
   Any failed or ambiguous fact stops without reassignment.
6. Clean a rejected orphan only after the step-4 recheck and the same exact
   path, base, cleanliness, worktree, and ownership checks pass. Invoke a
   supported native task/worktree cleanup action against that literal absolute
   path only; never use a glob, parent path, or Codex internal database. If
   native cleanup is unavailable, record the defect and leave the directory
   untouched.
7. A replacement is governed solely by the
   [Replacement gate](worker-contract.md#replacement-gate), which rechecks the
   original request immediately before any replacement path. Rename only a
   materialized Task so there is never a second canonical
   `[#<number>] <issue title>` Worker.

This branch completes only when the authoritative
[Worker activation handoff](worker-contract.md#worker-activation-handoff)
reaches its own completion criteria for a real Worker, or when the unclaimed
dispatch safely leaves the orphan untouched or cleans it after all terminality
checks. Do not declare completion from `CLAIM_CONFIRMED` or a local
unique-editor assertion alone.

### Existing Worker failure: a real Worker exists

This branch supplies recovery evidence; the Worker Contract alone chooses the
activation, recovery, succession, or safe-stop transition.

- For a real but unclaimed Task, record the exact failed prior turn and require
  it to be terminal, then re-read the Task as native-idle. The
  [Worker activation handoff](worker-contract.md#worker-activation-handoff)
  determines whether its fresh no-edit re-entry is admitted or it must take the
  unclaimed safe stop. No recovery evidence here authorizes an edit.
- For an already-claimed Worker, record this private evidence during its
  recovery-validation turn: exact visible Task and turn identities; the Issue
  claim's mapping to that Task; exact branch, worktree, and write-boundary
  ownership; actual `HEAD`, exact status, and WIP disposition; effective
  task-host permissions; and preserved model/reasoning binding, callback,
  hotset, and authority boundaries.
- The literal `RECOVERY_READY` means only that the Worker recorded evidence it
  can observe during its own turn. It must not assert a future native-idle
  state. The scheduled post-turn verification of the completed non-error turn,
  marker, and native idle state belongs to the
  [Claimed-Worker recovery handoff](worker-contract.md#claimed-worker-recovery-handoff).
- Treat WIP as useful only when `git status --porcelain` contains staged or
  unstaged changes, or when a commit is not present on the verified remote
  branch. Record one disposition: no WIP (clean status and remote SHA
  verified), or preserved WIP (a scoped checkpoint is pushed and its exact
  remote SHA verified). Verify the checkpoint has no transient or sensitive
  artifacts. If no disposition can be verified, leave the task and worktree
  intact and report the recovery requirement.

An already-claimed Worker resumes only through the
[Claimed-Worker recovery handoff](worker-contract.md#claimed-worker-recovery-handoff).
If that handoff cannot finish, use only the
[Claimed-Worker succession handoff](worker-contract.md#claimed-worker-succession-handoff).
Those handoffs preserve the Issue claim, branch, PR, callback, model binding,
and authority boundaries; no other continuation authorizes edits.

## Orchestrator responsibilities

- Follow [Monitoring cadence](#monitoring-cadence) for all task and GitHub
  reads; it is the sole authority for signals, events, and fallback polling.
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

This section governs Orchestrator monitoring of a real, preflight-passed
Worker. It is not a generic permission to poll a quiet task or to replace the
creation and recovery branches above.

- Prefer Worker signals and GitHub events. During materialization, follow the
  [materialization flow](worker-contract.md#reliable-task-materialization),
  including its bootstrap gate. Only after that flow routes a real Task to the
  [Worker activation handoff](worker-contract.md#worker-activation-handoff)
  does the authoritative preflight handshake begin.
- Once preflight passes, do not read the same Worker for ordinary progress just
  because no new event has arrived. A missing progress event alone is not
  evidence that reverse callback delivery is unavailable.
- Use a fallback read no more often than every ten minutes only after one
  observable reverse-delivery trigger: the host reports callback capability is
  unsupported, a native delivery call returns an error, the callback target is
  confirmed absent or inactive, or the Worker records a delivery failure.
  A missing progress event or general suspicion is not a trigger. Otherwise
  wait for a material signal or GitHub event.
- The bootstrap-materialization and claimed-Worker-recovery exceptions require
  their pre-recorded deadline or maximum check count and their exact next-read
  schedule: either stated UTC read times or a stated cadence plus the maximum
  check count. Read only on that schedule and take the bound's documented exit
  when it expires; never turn either exception into a busy poll.
- One immediate verification read is permitted after a Worker signal, explicit
  maintainer status request, declared command/test deadline, recovery action,
  or a GitHub PR, check, push, or merge transition.
- Report only material transitions. Do not relay routine reasoning, file edits,
  unchanged active status, or minute-by-minute test progress. A user-facing
  progress update may use the last verified state.

The ten-minute floor does not apply to a scheduled short bootstrap read, a
scheduled bounded recovery read, or one verification read triggered by a new
signal or GitHub event.
