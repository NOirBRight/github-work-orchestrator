# Cross-role communication protocol

Use native Codex task messaging for material transitions and GitHub for durable
work state. A callback is a wake-up signal, not a second project ledger.

## Contents

- [Addressing](#addressing)
- [Delivery handshake](#delivery-handshake)
- [Intake signals](#intake-signals)
- [Worker signals](#worker-signals)
- [Orchestrator verification](#orchestrator-verification)
- [Signal-driven monitoring](#signal-driven-monitoring)

## Addressing

The Orchestrator supplies its exact task ID privately in every Intake or Worker
contract. Keep callback IDs, sender task IDs, local worktree paths, credentials,
and private machine details out of GitHub.

The sender keeps its current model and reasoning settings when messaging the
callback. It does not create a replacement Orchestrator task when the callback
is unavailable.

## Delivery handshake

Every material signal uses a stable `Signal-ID` and this sequence:

1. Build the complete role-specific envelope below.
2. Call native `send_message_to_thread` with the exact callback task ID before
   writing the sender's final response.
3. A final answer in the Worker task is not callback delivery. The same rule
   applies to an Intake task; writing signal words only in its own final answer
   does not wake the Orchestrator.
4. Treat a successful native tool result as the transport receipt
   `SIGNAL_RECEIVED <Signal-ID>`. Do not resend after success.
5. On a transport error, make at most one retry with the same `Signal-ID` and
   identical material state. If that retry fails, write
   `CALLBACK_DELIVERY_FAILED <Signal-ID>` in the sender's final response and
   leave the full evidence in the visible task and authorized GitHub artifacts.

The Orchestrator deduplicates by `Signal-ID`, verifies the signal against the
sender task and GitHub, and processes it once. A transport receipt proves only
that the callback was queued; it does not authorize lifecycle, merge, or
scheduling changes.

If native reverse messaging is unavailable, record the delivery failure and
stop cleanly. Do not use an Issue comment as a hidden callback channel unless
the execution contract explicitly authorized that comment.

## Intake signals

Send exactly one signal after a material intake outcome:

```text
INTAKE_SIGNAL
- Signal-ID: intake-<issue-or-draft>-<state>-<sequence>
- State: ISSUE_READY | DUPLICATE | NEEDS_INFO | DISCUSSION_REQUIRED
- Issue/topic: #<number> | <draft/topic>
- Repository: <owner/repository>
- Evidence: <validated URL, duplicate owner, missing fact, or decision>
- Next action: <concise Orchestrator or maintainer action>
```

Use:

| State | Material outcome |
|---|---|
| `ISSUE_READY` | A published Issue passed readback and is ready for orchestration reconciliation |
| `DUPLICATE` | An existing Issue owns the same outcome and scope |
| `NEEDS_INFO` | A named missing fact prevents a truthful ready contract |
| `DISCUSSION_REQUIRED` | Direction, durable architecture, compatibility, security/privacy, or priority ambiguity requires an owner |

Routine search, drafting, and diagnostic progress remains in the Intake task.

## Worker signals

Send each state once per material transition. Reuse a state only when its
payload materially changes, and then use a new `Signal-ID`.

```text
WORKER_SIGNAL
- Signal-ID: worker-<issue>-<state>-<commit-or-sequence>
- State: DISCUSSION_REQUIRED | BLOCKED | PR_OPENED | READY_FOR_REVIEW | STOPPED
- Issue: #<number>
- Branch: <branch>
- Commit: <sha or none>
- PR: <URL or none>
- Verification: <passed, failed, pending, or not run>
- Hotset: <owned files/components>
- Blocker/next action: <concise request or handoff>
```

For `DISCUSSION_REQUIRED`, replace the last line with:

```text
- Decision: <one precise choice>
- Options: <A/B/C with concrete tradeoffs>
- Recommendation: <preferred path and why>
- Safe work: <what may continue while waiting>
```

Use:

| State | Material transition |
|---|---|
| `DISCUSSION_REQUIRED` | A decision exceeds the Worker's accepted authority |
| `BLOCKED` | Progress requires new authority, permissions, evidence, an upstream merge, or resolution of a proven write-set collision |
| `PR_OPENED` | The branch is pushed and a PR exists; checks may still run |
| `READY_FOR_REVIEW` | Assigned scope and required verification are ready for Orchestrator review |
| `STOPPED` | The Worker ends without a reviewable result or hands ownership back |

Do not include secrets, raw private traces, user content, or local paths. Keep
detailed output in the sender task or authorized PR artifacts.

## Orchestrator verification

On a new signal:

1. Read the sender task once.
2. Verify the Issue, assignee, branch, commit, PR, checks, and hotset in GitHub.
3. Apply only changes within the Orchestrator's authority.
4. Send revisions or accepted decisions back to the same visible task.
5. Recompute the frontier after completion, merge, blocker change, or released
   capacity.

Signal delivery never implies permission to merge, close, unassign, reprioritize,
or start another work item.

## Signal-driven monitoring

Prefer signals and GitHub events over polling. After reliable task
materialization and permission preflight:

- Read the same active Worker for ordinary progress only after a material
  signal.
- When reverse delivery is absent, keep fallback reads at least ten minutes
  apart.
- Permit one immediate verification read after an explicit maintainer status
  request, declared command/test deadline, recovery operation, or GitHub state
  transition such as a new PR, check completion, push, or merge.
- During two-stage materialization, poll only enough to distinguish a real task
  and completed bootstrap from a client-side stub. Allow one read after the
  full contract to verify permission preflight.
- Report only material transitions; do not relay unchanged reasoning, file
  edits, or minute-by-minute test status.

The ten-minute floor does not apply to bootstrap materialization, one bounded
recovery wait, or verification triggered by a new signal or GitHub event.
