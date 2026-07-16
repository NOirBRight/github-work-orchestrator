# Visible Worker recovery

Load this reference only after a Visible Worker creation, activation, or host
failure. Ordinary Inline and Subagent execution does not need it.

## Creation ambiguity

An asynchronous client receipt, missing sidebar row, or orphan worktree does not
prove success or failure. Normalize the native Task result, validate its schema,
and distinguish the exact original request from every later Task.

When the native call outcome is ambiguous:

1. Record the exact request digest when available and mark the creation guard
   `uncertain` with the original owner token.
2. Do not create a replacement, switch windows/projects, clean a worktree, or
   release the guard by inference.
3. After a genuine Codex host restart, perform one post-restart reconciliation
   using the original owner token.
4. Re-read the full native Task inventory and the literal worktree path/status.
   Match the exact request identity when one exists.
5. Prove exactly one outcome:
   - `task-materialized`: one exact real Task owns the exact worktree; or
   - `terminal-no-task`: the request is terminal/cancelled, no Task exists, and
     the worktree is absent or a clean orphan.
6. Reconcile and release the guard. Any mismatch or ambiguity leaves it and all
   paths untouched.

You must never steal an owner token, partition the production state directory, infer
ownership from age/expiry, or edit Codex SQLite.

## Activation failure before START

If the Worker fails before `START`, verify its latest turn is terminal/idle and
that it made no branch, source, GitHub, or PR write. Read the exact worktree,
base, cleanliness, Issue claim, and editor ownership.

- A corrected contract may be sent once to the same real idle Task when no
  preflight/repository write began.
- Otherwise preserve the exact Task identity and report it for human-owned
  archive after releasing any unambiguous claim and preserving the worktree.
  Never invoke the native archive action automatically.
- Inline/Subagent fallback is allowed only when the router independently
  selects that lane and no competing editor exists.
- Permission denial, dirty/WIP state, wrong base, real ownership collision,
  architecture/human gate, or exact-runtime model failure remains blocked.

## Active Worker failure

A disconnect or `systemError` does not change the GitHub claim. Attempt one
normal continuation in the same Task. If it fails again:

1. Prove the predecessor is terminal/idle.
2. Inspect exact HEAD, branch, status, Issue claim, permissions, model binding,
   callback, hotset, and write boundary.
3. Treat WIP as durable only when clean and already on the verified remote, or
   after one scoped checkpoint is pushed and its SHA verified.
4. Activate at most one successor on the preserved branch/worktree after
   proving the predecessor cannot edit.

If WIP cannot be made durable, leave the Task and worktree intact. Never reset,
force-clean, or run two editors.

## Creation guard boundary

The host-wide guard ends at exact real Task identity. Later failures are owned
by Task/worktree/Issue/branch evidence and must not recreate or renew a creation
lease. The guard does not claim to fix unrelated Codex Desktop crashes.

## Monitoring and cleanup

Use material callbacks and GitHub events. After one declared deadline, make at
most one authoritative Task read; ordinary fallback reads stay at least ten
minutes apart.

After verified merge or stop, trigger safe repository cleanup immediately and
finish eligible worktree/branch removal within five minutes. Report the exact
Visible Worker for human-owned archive; never invoke the native archive action
automatically. Preserve dirty, unpushed, active, or ambiguous state and report
it.
