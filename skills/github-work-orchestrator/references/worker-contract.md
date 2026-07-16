# Visible Worker contract

Load this contract only after the execution router selects Visible Worker for
restart persistence, manual UI/login, prolonged observation, or independently
visible context. Inline and Subagent lanes do not use it.

## Identity

- Title: `[#<number>] <issue title>`
- Base: documented integration branch and exact SHA
- Branch: repository convention, default `codex/issue-<number>-<slug>`
- Target: documented integration branch
- Scope: one Issue or one explicitly approved coupled unit
- Model: `ollama-cloud/glm-5.2`, reasoning `max`

## Host-wide creation singleflight

The guard protects visible-Task creation only. It does not own activation,
implementation, the Issue, or the worktree, and it does not patch the Codex Desktop binary.

Generate and retain the private owner token before reserve. New durable records
contain only `creating` and `uncertain` states plus the identity/digests needed
for one creation call. Call the native creation API only when a fresh reserve
returns `creation_authorized: true`; an idempotent read returns
`creation_authorized: false` and never authorizes another call.

After a queued receipt, record its exact request identity. A receipt or orphan
worktree is not a Worker. Retain both the owner token and request identity until
release. Release the guard immediately after native discovery proves one exact
real Task identity and owned worktree:

```text
python <skill>/scripts/task_creation_lease.py release \
  --owner-token <private-token> --outcome task-materialized \
  --task-id <exact-task-id> --worktree-state owned \
  --evidence <private-exact-readback-reference>
```

If the caller turn lost only the owner token after recording the queued
receipt, release a materialized Task with the exact request identity instead:

```text
python <skill>/scripts/task_creation_lease.py release \
  --request-id <exact-native-request-id> --outcome task-materialized \
  --task-id <exact-task-id> --worktree-state owned \
  --evidence <private-exact-readback-reference>
```

This request-authenticated path cannot cancel creation or assert terminal
no-Task. Those outcomes still require the original owner token and applicable
reconciliation evidence.

If the native outcome is ambiguous, mark `uncertain`, issue no replacement, and
enter the recovery reference. A conflict blocks only the visible-Worker lane;
it does not block independently eligible Inline or Subagent work.

Never use another window, project, `CODEX_HOME`, or `--state-dir` to evade the
guard. Never steal an owner token or edit Codex SQLite.

## Materialize one Worker

1. Reconcile the unassigned Issue and pin base, branch, hotset, verification,
   permission, callback, and model contract.
2. Reserve the creation guard with the caller-owned token.
3. Create one worktree Task with the complete private contract, explicit
   execution-only absolute CWD, and `ollama-cloud/glm-5.2` binding plus
   reasoning `max`. The supported native contract must not save or register
   the worktree as a Codex project.
4. Record a returned client request receipt. Wait event-first for native Task
   discovery; do not poll every few seconds.
5. When the exact Task/worktree materializes, release the guard. Rename only
   this real Task.

A rejected GLM binding fails closed. Do not retry with GPT.
If the native surface cannot use the assigned CWD without persisting it as a
Saved Project or Saved Workspace, fail the Visible Worker lane with a sanitized
platform limitation. Do not register the path, switch projects, install a Skill
there, or edit Codex private state as a workaround.

## Activate with one full turn

Use one callback-first activation, not a model-less READY bootstrap:

1. The Worker receives the full contract and sends `WORKER_BOOTED` before
   repository or GitHub writes.
2. It runs only deterministic read-only preflight against the pinned base,
   clean isolated worktree, permissions, GitHub identity, and future branch.
3. The Orchestrator verifies exact Task identity, claims the Issue, and reads
   the claim back while the Worker remains read-only.
4. The Worker sends `PREFLIGHT_READY` and becomes idle.
5. Only after claim and preflight verification does the Orchestrator send the
   literal `START`. Only its exact native receipt authorizes branch creation,
   edits, commits, pushes, and PR writes.

Use one absolute cold-activation deadline and at most one authoritative read
when a callback is missing. Ordinary monitoring follows the ten-minute fallback
floor.

## Worker contract payload

Include:

1. Issue URL and acceptance criteria.
2. Repository instructions and accepted decisions.
3. Exact base branch/SHA, isolated worktree, future branch, PR target, and
   closing semantics.
4. Hotset, prohibited writes, and one-editor boundary.
5. Verification class/commands and explicit manual evidence.
6. `Review-Owner: orchestrator`.
7. GLM profile, concrete binding, reasoning applicability, binding requirement,
   status, and sanitized evidence.
8. Effective permission profile.
9. Exact Orchestrator callback and required signals.

## Behavior

- Preserve unrelated work and stay inside the assigned Issue/worktree.
- Do not merge, close, reprioritize, or create another Task.
- Do not run the generic formal review; the Orchestrator owns it.
- Send `DISCUSSION_REQUIRED` before changing durable architecture, public
  compatibility, security/privacy, migration, or cross-Issue scope.
- Publish one locally green candidate and notify without waiting for CI.
- Return exact verification, timings, full-suite count, `Review-Runs: 0`, scope
  delta, changed paths, and next action.

## Failure and recovery

On Task-host failure, stop editing and load
[Visible Worker recovery](communication.md). Preserve useful WIP through a
scoped pushed checkpoint when possible. Do not create a successor until the
predecessor is terminal/idle and single-editor ownership is proven.

Creation ambiguity permits one evidence-backed reconciliation. A recorded
request identity can release only a proven materialized Task; terminal no-Task
and legacy records still require the original owner token. A mismatch leaves
the guard and paths untouched. Legacy lease records may be read and drained
only through this reconciliation path.

## Completion

After merge or stop, trigger event-based cleanup. Within five minutes, remove
only a clean durable inactive worktree and delete only a merged local branch.
Report this exact Visible Worker as ready for human-owned archive; never invoke
the native Task archive action automatically. Preserve anything dirty,
unpushed, active, or ambiguous.
