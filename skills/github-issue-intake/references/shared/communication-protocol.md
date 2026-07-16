# Cross-role communication protocol

Use native Codex messaging for material Visible Worker transitions and GitHub
for durable work state. Inline work stays in the Orchestrator. Subagents return
through the parent collaboration channel and do not create a project ledger.

## Delivery handshake

Every Visible Worker signal uses a stable `Signal-ID`:

1. Build the complete envelope below.
2. Call native `send_message_to_thread` with the exact Orchestrator callback
   before the sender's final response.
3. A final answer in the Worker task is not callback delivery.
4. A successful tool result is the receipt `SIGNAL_RECEIVED <Signal-ID>`; do
   not resend it.
5. On transport error, retry once with the identical envelope. After another
   failure, record `CALLBACK_DELIVERY_FAILED <Signal-ID>` and stop cleanly.

Keep callback IDs, Task/Subagent IDs, worktree paths, owner tokens, credentials,
and private machine details out of GitHub.

## Intake signals

Use the Intake package's signal formatter and send one material state:

```text
INTAKE_SIGNAL
- Signal-ID: intake-<issue-or-draft>-<state>-<sequence>
- State: ISSUE_READY | DUPLICATE | NEEDS_INFO | DISCUSSION_REQUIRED
- Issue/topic: #<number> | <draft/topic>
- Repository: <owner/repository>
- Evidence: <validated URL, duplicate owner, missing fact, or decision>
- Next action: <concise Orchestrator or maintainer action>
```

Routine search, drafting, and progress remain in the Intake task.

## Visible Worker signals

Use the Worker package's formatter:

```text
WORKER_SIGNAL
- Signal-ID: worker-<issue>-<state>-<commit-or-sequence>
- State: DISCUSSION_REQUIRED | BLOCKED | PR_OPENED | READY_FOR_REVIEW | STOPPED
- Issue: #<number>
- Branch: <branch>
- Commit: <sha or none>
- PR: <URL or none>
- Verification-Class: fast | standard | strict
- Verification: <passed, failed, pending, or not run>
- Phase-Timings: plan=<duration>; implementation=<duration>; verification=<duration>; waiting=<duration>
- Full-Suite-Runs: <count>
- Review-Runs: 0
- Scope-Delta: none | <new boundary requiring approval>
- Hotset: <owned files/components>
- Blocker/next action: <concise request or handoff>
```

For `DISCUSSION_REQUIRED`, provide one decision, options, recommendation, and
safe work. `PR_OPENED` follows the locally green candidate immediately so CI,
formal review, and safe candidate evidence can run in parallel.

`Review-Runs` stays zero because formal review belongs to the Orchestrator.

## Inline and Subagent results

Inline work records candidate evidence directly. A Subagent receives one
bounded prompt with exact worktree/write boundaries and returns outcome,
changed paths, checks, and blockers to its parent. The Orchestrator verifies and
publishes the result. Do not use a GitHub comment as a hidden Subagent callback
or let a Subagent spawn a second work item.

## Orchestrator verification

On a material result:

1. Verify the Issue, owner, branch, commit, PR, checks, and hotset.
2. Apply only changes within Orchestrator authority.
3. Send revisions to the same lane owner.
4. Recompute capacity after completion, merge, stop, blocker change, or released
   slot.
5. Trigger safe cleanup after merge/stop.

Delivery never implies permission to merge, close, unassign, reprioritize, or
start another work item.

## Signal-driven monitoring

Prefer callbacks and GitHub events over polling:

- Obtain at most one normalized Task-list snapshot per material event.
- Read an individual Visible Worker after a signal, relevant GitHub transition,
  declared deadline, recovery action, or explicit maintainer request.
- When reverse delivery is unavailable, keep ordinary fallback reads at least ten minutes apart.
- Permit one bounded creation/recovery read only where the Visible Worker
  contract requires it.
- Report material transitions only; do not relay unchanged status or verbose
  tool output.

The five-minute cleanup deadline is event-triggered and does not authorize a
new polling loop.
