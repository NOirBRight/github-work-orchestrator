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
materialization, bootstrap gates, and activation. This section owns recovery
evidence, safe orphan handling, and succession; it does not recreate the
activation sequence.

### Creation failure: no Worker exists

This branch begins only when the materialization flow routes a no-real-Task
outcome here. Absence from the native task list is not terminal ownership of a
queued request, a worktree, or a replacement path.

1. Obtain a supported native cancellation or terminal-failure result for the
   exact queued request/client ID, then immediately verify that no real Task
   appeared. Keep the ID and result private. If either fact cannot be proven,
   record ownership as ambiguous, leave the worktree untouched, and prohibit
   adoption, reuse, cleanup, or a replacement on that path. Never bind a
   recovery Task to or remove an ambiguous path: a late materialization must not
   create a second editor or inherit a removed worktree.
2. Record the exact terminal/cancelled result and the no-Task read privately in
   the visible Orchestrator task with the orphan evidence: literal and resolved
   absolute paths, expected base SHA, actual `HEAD`, exact `git status
   --porcelain` result, matching `git worktree list --porcelain` repository
   entry, and native/GitHub ownership and admission facts. Record `missing` or
   a command error as the measured value. Never publish client IDs, this
   evidence, or local paths to GitHub.
3. Diagnose an orphan only from that recorded literal path and expected base.
   Reuse or cleanup requires the directory to exist, paths to match, actual
   `HEAD` to equal the expected base, status to be empty, the worktree entry to
   identify the expected repository, and ownership to be unambiguous. A dirty,
   wrong-base, missing, or ambiguous directory stops safely without inferred
   paths or retries on that path.
4. Immediately before any adoption, reuse, cleanup, or replacement on that
   path, re-read the exact queued request's terminal/cancelled result and the
   native task list, and append the exact terminal result and no-Task result to
   the private record. If the request is no longer terminal, the result is
   absent or ambiguous, or any real Task now appears, leave the path untouched
   and stop that action.
5. Admit a recovery Task only after step 4 passes. Immediately before adoption,
   re-read its native Task and GitHub ownership. The positive gate must prove
   that the Task is real and sidebar-visible; idle and not editing; owns no
   other GitHub work item, branch, PR, or write boundary; has no worktree
   binding other than the exact orphan path or none; and can become the unique
   editor under the Orchestrator's explicit recovery contract. That contract
   names the exact absolute path as its only write boundary. A branch name,
   parent directory, client ID, or inferred location is not a boundary. Append
   that immediate ownership/admission evidence to the private record, then
   route the Task to the shared
   [Worker activation handoff](worker-contract.md#worker-activation-handoff).
   Any failed or ambiguous fact stops without reassignment.
6. Clean a rejected orphan only after the step-4 recheck and the same exact
   path, base, cleanliness, worktree, and ownership checks pass. Invoke a
   supported native task/worktree cleanup action against that literal absolute
   path only; never use a glob, parent path, or Codex internal database. If
   native cleanup is unavailable, record the defect and leave the directory
   untouched.
7. Make at most one replacement attempt only under the materialization flow's
   recorded cause/remediation/probe gate. Rename only a materialized task so
   there is never a second canonical `[#<number>] <issue title>` Worker.

This branch completes only when the authoritative
[Worker activation handoff](worker-contract.md#worker-activation-handoff)
reaches its own completion criteria for a real Worker, or when the unclaimed
dispatch safely leaves the orphan untouched or cleans it after all terminality
checks. Do not declare completion from `CLAIM_CONFIRMED` or a local
unique-editor assertion alone.

### Existing Worker failure: a real Worker exists

This branch applies after native discovery identifies a real visible Task.

1. If that Task is real but unclaimed because its bootstrap, full-contract, or
   preflight gate failed, send it back to the
   [Worker activation handoff](worker-contract.md#worker-activation-handoff).
   It may not edit until that handoff completes its normal
   `CLAIM_CONFIRMED` continuation. Do not use the claimed-Worker recovery path
   for this case.
2. For an already-claimed Worker, record a distinct recovery-validation bound
   in the visible Orchestrator task before one recovery continuation: an
   absolute UTC deadline or a maximum number of native turn-state/content
   checks, plus exact UTC read times or a stated cadence and that maximum. Do
   not extend the bound or schedule.
3. Require the literal `RECOVERY_READY` marker in a completed, non-error native
   turn only after the Worker records this private evidence checklist:
   - exact visible Task identity and an idle state after that turn;
   - the Issue's current claim state still matches the already-claimed Worker;
   - branch, exact worktree, and write-boundary ownership;
   - actual `HEAD`, exact status, and a no-WIP or preserved-WIP remote-SHA
     disposition;
   - the effective task-host permission result; and
   - the original model/reasoning binding, callback, hotset, and authority
     boundaries still preserved.
4. At the recorded read, verify that marker and every evidence item, then send
   an explicit `RECOVERY_CONFIRMED` continuation. Only that continuation
   authorizes the already-claimed Worker to resume edits.
5. If the bound expires, the turn errors, the marker/evidence is incomplete, or
   the Task is not idle, do not authorize edits. Stop retrying that session and
   use one supported native visible-task fork or handoff on the same
   worktree/branch only when it preserves a single editing owner.
6. Treat WIP as useful only when `git status --porcelain` contains staged or
   unstaged changes, or when a commit is not present on the verified remote
   branch. Before archiving, record one disposition: no WIP (clean status and
   remote SHA verified), or preserved WIP (a scoped checkpoint is pushed and
   its exact remote SHA verified). Ensure every Worker on that worktree is idle
   and verify the checkpoint has no transient or sensitive artifacts. If no
   disposition can be verified, leave the task and worktree intact and report
   the recovery requirement.
7. Archive or stop the predecessor only after the worktree is clean or the
   remote checkpoint is verified. Tell the successor to inspect `git status`
   and the current diff before editing. Keep the Issue claim, branch, PR,
   callback, model profile, and authority boundaries intact. When same-worktree
   succession is unsafe or unavailable, preserve and verify the branch remotely,
   deactivate the predecessor, then create one replacement worktree from that
   branch. Never run two Workers against one worktree.

An already-claimed Worker resumes only after the validated `RECOVERY_READY`
marker and `RECOVERY_CONFIRMED` continuation. A real but unclaimed Task reaches
an editing state only through the linked activation handoff. Otherwise this
branch ends through the safe stop/succession path with WIP preserved or the
worktree left intact.

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
  when it expires; never turn either exception into a busy poll. Executable AC2
  polling remains residual work.
- One immediate verification read is permitted after a Worker signal, explicit
  maintainer status request, declared command/test deadline, recovery action,
  or a GitHub PR, check, push, or merge transition.
- Report only material transitions. Do not relay routine reasoning, file edits,
  unchanged active status, or minute-by-minute test progress. A user-facing
  progress update may use the last verified state.

The ten-minute floor does not apply to a scheduled short bootstrap read, a
scheduled bounded recovery read, or one verification read triggered by a new
signal or GitHub event.
