# Visible Worker Contract

Create one sidebar-visible Codex task per work item in an isolated worktree.
Never use a subagent as the Worker or dispatch target for a real GitHub work
item. The visible task is the auditable owner of the Issue, branch, PR, and
completion evidence.

The visible Worker may use subagents internally for bounded implementation
slices, research, implementation critique, test analysis, or independent
verification within the same assigned Issue and worktree. The visible Worker
partitions write sets and integrates all results, while the Orchestrator owns
the one formal Standards/Spec review. The visible task remains the only Worker
of record. Subagents do not own GitHub work items or create a separate lifecycle.
If discovered work is itself a distinct GitHub Issue, return it to the
Orchestrator for a new visible task instead of assigning it to a subagent.

## Contents

- [Required task identity](#required-task-identity)
- [Host-wide Task-creation lease](#host-wide-task-creation-lease)
- [Reliable task materialization](#reliable-task-materialization)
- [Replacement gate](#replacement-gate)
- [Worker activation handoff](#worker-activation-handoff)
- [Claimed-Worker recovery handoff](#claimed-worker-recovery-handoff)
- [Claimed-Worker succession handoff](#claimed-worker-succession-handoff)
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

## Host-wide Task-creation lease

Serialize every native action that can create a sidebar-visible Worker Task,
including initial materialization, a replacement, or a successor. The lease is
host-wide across repositories, Codex windows, projects, and alternate
`CODEX_HOME` profiles for the same OS user. It is an ephemeral admission guard,
not a second project ledger.

This guard prevents the Orchestrator from issuing concurrent native creation
calls. It does not patch the Codex Desktop binary or prove that unrelated
client crashes are eliminated; report that boundary truthfully in every
incident or acceptance result.

Before calling `create_thread`, a worktree `fork_thread`, or an equivalent
creation surface, run:

```text
python <skill>/scripts/task_creation_lease.py reserve \
  --repository <owner/repository> --issue <number> --branch <feature-branch>
```

The idempotency key is exactly normalized `repository + issue + branch`. Keep
the returned owner token, lease ID, key, and any queued request identity only
in the private materialization record. Call the native creation surface only
when a fresh reservation returns `creation_authorized: true`. A repeat by the
same owner and key returns `creation_authorized: false`; it is a read of the
existing lease, not permission for a second native call. Any other active
lease, transaction-lock conflict, or expired lease is a fail-closed stop: do
not call the creation surface, wait-and-retry, create from another
project/window, or substitute a different Issue. Use the default OS-user host
state location; `--state-dir` exists only for deterministic tests and must not
partition production admission.

Advance the lease after each observed boundary with
`task_creation_lease.py transition --state <state>`. Use this ordered state
vocabulary:

```text
reserved -> invoking -> queued -> worktree-creating -> task-materialized
                    -> bootstrap-ready -> preflight-ready -> activated
```

Each valid observed-progress transition renews the bounded inactivity interval;
ordinary time passage without a transition does not.

Transition from `reserved` to `invoking` immediately before the one admitted
native creation call. This closes the crash window before a client request ID
exists. On a returned receipt, transition to `queued` with that exact ID; the
lease stores its digest and requires the same identity during reconciliation.
The direct `queued -> task-materialized` path is allowed when no separate
worktree-creating observation exists. `queued` is not a real Task. If the call
has no response or its native ownership becomes uncertain, leave `invoking` for
post-restart reconciliation or transition a still-running owner to
`creation-unknown`. Do not issue a replacement, auto-adopt a later Task, or
release the lease from either uncertain state.

Only the exact owner token may transition or release a lease. Release requires
one terminal state: `activated`, `failed`, or `cancelled`. Mark `activated` only
after the successful `CLAIM_CONFIRMED` delivery that completes activation, then
release. Mark `failed` or `cancelled` only from authoritative terminal evidence,
then release. Expiration blocks every nonterminal transition, including an
attempt to manufacture a terminal result; it never makes a lease stealable.
A terminal state established before expiry is immutable and may still be
released only by its original owner. Otherwise use the Task-host reconciliation
below after restart and exact request/Task/worktree readback; successful
reconciliation opens one new bounded lease interval.

## Reliable task materialization

This section is the single authority for queued-request creation, bootstrap
eligibility, and no-real-task discovery. Record one
private materialization record that distinguishes the exact original queued
request from every later recovery candidate Task or request. Never publish
those identities or any local path to GitHub. Normal materialization enters the
shared [Worker activation handoff](#worker-activation-handoff) only after a
real Task passes its bootstrap gate. Read
[creation-failure recovery](communication.md#creation-failure-no-worker-exists)
for orphan-worktree diagnosis, reuse, and cleanup after a creation failure.

Keep the candidate unassigned until a real Worker has passed preflight. That
makes the lifecycle's assignee-derived Active state truthful rather than a
placeholder for a queued client request.

1. Reconcile the candidate and record its exact base SHA, branch, hotset, and
   expected verification while it remains unassigned.
2. Select the bootstrap binding through the
   [model-profile selection order](shared/model-profiles.md#selection-order). Before
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
4. Transition the host-wide lease to `invoking`, then create the
   isolated-worktree task at that exact base with the recorded eligible
   bootstrap binding. On a returned receipt, record its exact queued-request
   identity as the original request and transition the lease to `queued` with
   that identity. Send only
   `[#<number>] Bootstrap only. Reply exactly READY. Do not use tools.`
5. Read only at that recorded schedule for a real Task belonging to the exact
   original request. A client-side creation ID or a created worktree alone is
   only diagnostic evidence, not a Worker. Do not title an unmaterialized stub.
6. If no real Task for the original request appears before the discovery bound
   expires, exit only to Creation failure: do not add the assignee or dispatch
   comment, and preserve the original-request identity privately. That branch
   must prove the original request terminal or cancelled and then prove no real
   Task for it exists before it touches an orphan. Without that proof, classify
   ownership as ambiguous and leave the path untouched; a late materialization
   must remain the only possible editor.
7. When a real Task appears, record a distinct post-materialization
   bootstrap-turn bound and transition the lease to `task-materialized` before
   reading its completion: an absolute UTC deadline or maximum number of native
   turn-state/content checks, plus exact UTC read times or a stated cadence and
   that maximum. Do not extend it or reuse the discovery bound.
8. Within that bound and schedule, require the bootstrap turn to complete
   without error, emit the exact `READY` reply, and leave the real Task idle.
   Transition to `bootstrap-ready` only after those facts are verified. Do not
   rename or send a full contract while the turn is pending, errors, or omits
   that exact reply.
9. If the original Task's bootstrap turn errors, omits `READY`, or does not
   reach idle before the post-materialization bound expires, leave the Issue
   unclaimed and exit only to Existing Worker failure. Its next safe transition
   is governed solely by the activation handoff.
10. Only after the successful bootstrap gate, rename the real task to
    `[#<number>] <issue title>`. A normal or replacement dispatch enters the
    shared [Worker activation handoff](#worker-activation-handoff). A newly
    created claimed-Worker successor keeps the same creation lease at
    `bootstrap-ready` and enters the
    [succession handoff](#claimed-worker-succession-handoff) instead.

## Replacement gate

This is the sole authority for a replacement after an original request produced
no real Task. It applies to every replacement for that dispatch and Issue,
including one proposed on a fresh worktree path.

1. Before the one permitted replacement, record in the visible Orchestrator
   task the failed observation, hypothesized startup cause, authorized
   reversible isolation or remediation, and a named post-remediation probe with
   the result that proves the cause removed or isolated. Do not attempt a
   replacement until that probe produces its recorded proving result.
2. Immediately before every replacement attempt, re-read the supported native
   terminal or cancelled result for the exact original request and the native
   task list for a real Task belonging to that original request. Append both
   exact results to the private materialization record. This request-wide gate
   applies regardless of the proposed replacement path.
3. If either result is absent, ambiguous, or no longer terminal, or if the
   original request has materialized a real Task, make no replacement anywhere.
   Leave every affected path untouched and route the real Task through the
   applicable recovery or safe-stop path; never race it with a second editor.
4. A replacement that passes these gates still must pass normal materialization
   before it can be a Worker. A failed or completed replacement consumes the
   one permitted attempt; do not retry the same dispatch path again.

## Worker activation handoff

This is the sole authority for activating either a normally materialized Task
or an admitted recovery Task. Record the activation mode and exact Task,
branch, base, worktree, and write-boundary identities privately before entry.
The modes are normal materialization, admitted recovery Task, and one fresh
unclaimed re-entry. Only the final successful claim-confirmation continuation
authorizes editing.

1. For an unclaimed re-entry, require the prior failed turn to be terminal and
   the exact Task to be native-idle before entering. Record one fresh no-edit
   re-entry for that real Task privately. A Task with a recorded re-entry may
   never re-enter this handoff after another failure.
2. Record a distinct, concrete full-contract/preflight-turn bound in the
   visible Orchestrator task before sending the contract: an absolute UTC
   deadline or a maximum number of native turn-state checks, plus exact UTC
   read times or a stated cadence and that maximum. Do not extend it or its
   schedule. Its expiration exits only to Existing Worker failure.
3. Send the full Initial Worker message and Worker Contract to the exact
   sidebar-visible Task with the selected model binding and reasoning level.
   For a recovery Task, make the adopted absolute path its only write boundary
   and require the permission/repository preflight to run from that path.
4. Require a preflight-only turn: the Task runs no edits and emits the exact
   marker `PREFLIGHT_READY` only after the required preflight succeeds; it then
   becomes idle. If it cannot run the full contract or preflight, it reports
   the failure without the marker and without editing.
5. Within the full-contract/preflight-turn bound and its schedule, wait for
   native idle/turn completion, then make one authoritative content read to
   confirm the marker and required preflight. For a creation-backed activation,
   transition its lease to `preflight-ready`: normal materialization advances
   from `bootstrap-ready`; an admitted/reconciled Task or the one fresh
   unclaimed re-entry may advance from `task-materialized` only with the
   explicit `--recovery-path` guard. A pre-existing Task with no creation lease
   records that fact and invents no lease transition. An earlier read is not
   this completion read and does not consume it or deadlock activation.
6. Immediately before the first GitHub lifecycle write, re-run/re-read the
   Issue, the native Task, and worktree ownership. Require the Issue to remain
   open, `ready-for-agent`, unblocked, and unassigned; the exact idle,
   preflight-passed Task to be its unique visible mapping; and the recorded
   branch, base SHA, worktree, and write boundary to be unchanged.
7. Only after that revalidation, add the GitHub assignee claim.
8. Immediately after the claim, re-read and verify all of the following before
   any dispatch comment: the Issue is still open, ready, unblocked, and claimed
   only by the intended assignee; the exact native Task still has the recorded
   completed non-error preflight turn, is idle, and has no conflicting active
   turn; and the exact worktree, branch, base SHA, write boundary, and unique
   ownership are unchanged. This is the post-claim full-state verification.
9. Only after post-claim full-state verification succeeds, write one concise
   dispatch comment. Record the returned durable comment identity privately,
   then immediately re-read that exact record to verify the write and its
   dispatch contents. A write response alone is not success.
10. Only after that comment readback succeeds, send the exact
    `CLAIM_CONFIRMED` continuation with the branch, hotset, verification,
    callback, and write boundaries. Require its native delivery receipt to
    identify the exact Task. Only this final successful continuation authorizes
    scoped edits. For a creation-backed activation, transition its lease to
    `activated` and release it with the same owner token only after that receipt
    succeeds; a pre-existing Task with no creation lease performs no release.

If the full-contract/preflight-turn bound expires, the turn errors, the marker
is absent, or the Task does not become idle, leave the Issue unclaimed and exit
to Existing Worker failure. A consumed unclaimed re-entry instead exits to the
unclaimed safe stop: do not re-enter, authorize no edits, and use a supported
native deactivation only when it can preserve the exact worktree safely;
otherwise leave the worktree intact and the Issue unclaimed. On pre-claim drift,
make no GitHub write and take the same failure exit. On post-claim Task or
worktree drift before the comment, remove only this dispatch's exact assignee
when that release is unambiguous, verify the release, and then take the same
failure exit; otherwise stop for maintainer review.

For a dispatch-comment write/readback failure with an exact durable record, a
definitively undelivered `CLAIM_CONFIRMED` continuation, or Task/worktree drift
after comment readback, use this one shared abort routine. Never add a second
comment:

1. Amend that same exact record to `DISPATCH_ABORTED: claim release pending; no
   edits authorized` and verify its readback.
2. Only after that verified pending record, remove this dispatch's exact
   assignee when unambiguous and verify the Issue release.
3. Only after verified release, amend that same record to
   `DISPATCH_ABORTED: claim released; no edits authorized` and verify its
   readback.

If assignee removal or release verification fails, do not state that the claim
was released. Keep the verified pending record, or reconcile that same record to
`DISPATCH_ABORTED: claim release failed; maintainer reconciliation required; no
edits authorized`, then stop for maintainer review. If pending-comment
reconciliation is ambiguous or fails, do not remove the assignee, add a second
comment, or authorize edits; stop for maintainer review. If release succeeds
but final-comment reconciliation fails, leave the verified pending record rather
than claiming release, add no second comment, authorize no edits, and stop for
maintainer review.

For a dispatch-comment failure with no exact durable record, roll back only this
dispatch's unambiguous claim and verify the release; on ambiguity or failure,
stop for maintainer review with edits unauthorized. For a failed
`CLAIM_CONFIRMED` continuation, first determine whether its native delivery
receipt identifies the exact Task. If it definitively does not, use the shared
abort routine above; if the result is ambiguous, stop for maintainer review
without a duplicate continuation or edit authorization.

Activation is complete only when one real Worker has the full contract, has
sent `PREFLIGHT_READY` and become idle, has passed the authoritative preflight
read and the pre/post-claim full-state checks, has a successfully written and
read-back dispatch comment, and has received the successful
`CLAIM_CONFIRMED` continuation and exact work boundaries. Never leave a queued
request or failed preflight as an Active claim.
Keep the bootstrap prompt short and uniquely Issue-scoped. If an optional
startup service is suspected, use a bounded A/B test and disable it only after
proof and separate authorization for a reversible change.

## Claimed-Worker recovery handoff

This is the sole authority for resuming the already-claimed Worker before it
edits again. The recovery evidence and WIP disposition it consumes are defined
in [Existing Worker failure](communication.md#existing-worker-failure-a-real-worker-exists).

1. Record a distinct recovery-validation bound and scheduled native reads in
   the visible Orchestrator task before one no-edit recovery continuation.
2. Require the Worker to record its recovery evidence observable during that
   turn and emit the literal `RECOVERY_READY`. The marker is not an assertion
   that the Task will later be idle.
3. At the scheduled post-turn read, independently verify the exact recovery
   turn completed without error, the exact marker and evidence are present, and
   the native Task is then idle. Re-read the Issue claim and exact Task,
   worktree, branch, and write-boundary ownership before proceeding.
4. Only after that read succeeds, send `RECOVERY_CONFIRMED` and require its
   native delivery receipt to identify the same Task. Only that successful
   continuation authorizes the claimed Worker to resume edits.
5. If any bound, marker, evidence, Task state, ownership check, or continuation
   fails, leave edits unauthorized and enter the
   [Claimed-Worker succession handoff](#claimed-worker-succession-handoff).

## Claimed-Worker succession handoff

This is the sole authority for a successor after a claimed Worker cannot resume.
It preserves the existing Issue claim; it does not create a second claim or
dispatch comment. A failed succession handoff ends in a safe stop with edits
unauthorized.

1. Record a concrete succession-handoff bound and scheduled native reads before
   taking action. Require the predecessor's latest turn to be terminal, the
   predecessor to be inactive and unable to edit, and the verified WIP
   disposition from the recovery evidence before creating or activating a
   successor.
2. Establish exactly one successor sidebar-visible Task and worktree owner. It
   must own no other GitHub work item, branch, PR, or write boundary, and no
   second successor or editor may remain. Preserve the exact path boundary when
   the worktree is reused; otherwise preserve and verify the branch before the
   successor receives its new exact path. If this step creates a new Task,
   reserve the host-wide lease and reuse the normal creation/bootstrap gates
   through `bootstrap-ready` while preserving the existing Issue claim; do not
   enter ordinary activation. An admitted pre-existing successor either retains
   its reconciled originating lease or records that it has no creation lease;
   never invent a lease after the fact.
3. Send that successor the preserved full contract: original model/reasoning
   binding, Issue claim, callback, hotset, branch, base SHA, PR target, and
   exact worktree/write boundary. It first runs permission and repository
   preflight without edits.
4. During that preflight-only turn, require the successor to record the
   observable recovery evidence and emit the literal `SUCCESSOR_READY`. The
   marker does not assert a future idle state. After its authoritative
   completion read, transition a creation-backed successor lease from
   `bootstrap-ready` to `preflight-ready`; a pre-existing successor performs no
   lease transition.
5. At the scheduled post-turn read, independently verify the exact successor
   turn completed without error, its marker and evidence, native idle state,
   predecessor inactivity, the Issue claim's unique mapping to this successor,
   and exact Task/worktree/write ownership.
6. Only after that read succeeds, send `RECOVERY_CONFIRMED` and require its
   native delivery receipt to identify the successor. Only this final
   continuation authorizes successor edits. For a creation-backed successor,
   transition to `activated` and release the lease with its original owner
   token only after that receipt succeeds.

If any gate fails, do not authorize edits, create another successor, or change
the Issue claim implicitly. Preserve or leave the worktree according to its
verified WIP disposition and stop safely for maintainer review when a supported
deactivation cannot complete.

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
8. Execution-contract version, verification class/commands, manual evidence,
   architecture decision, `Review-Owner: orchestrator`, requested model,
   `model_binding_status: verified`, and sanitized effective-binding evidence.
9. Required PR target and closing semantics.
10. The Orchestrator callback task and the required Worker signals from the
   [communication protocol](communication.md).
11. During activation, the recorded full-contract/preflight-turn bound and the
    preflight-only handshake; editing begins only after the activation
    handoff's final successful continuation.

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

Prefer the packaged Worker `scripts/preflight.py` for these repeatable checks.
Pass task-host permission metadata explicitly; the script validates it but
cannot grant or infer a broader profile.

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
  evidence. Do not delegate a second GitHub work item through a subagent, invoke
  the generic `code-review` Skill, or create Standards/Spec review subagents;
  the Orchestrator owns the one formal review.
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
- Follow the sole class and post-review rules in
  [the shared verification policy](shared/verification-policy.md); do not
  restate or extend them in the dispatch.

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
- verification class, phase timings, targeted/full verification with exact
  results, full-suite count, `Review-Runs: 0`, and scope delta;
- newly discovered blockers, follow-up Issues, and hot-file ownership;
- whether the Issue can close.

The Orchestrator reviews this evidence before merging or releasing the slot.
