# ADR 0009: Harden Coordinator Entry and Host-First Cleanup

## Status

Accepted for Orchestrator 6.0.1 on 2026-07-20.

## Context

V6.0 had pure policy for Workspace selection but its public mutation commands
did not consume that policy. They qualified the current Workspace only after a
GitHub snapshot and could not prove that the current collaboration mode was
write-capable. The adapter also treated a missing Worker cwd as an identity
failure. Paseo's supported “Archive merged PR workspaces” behavior can archive
the Agent and remove that worktree immediately after merge, before GWO cleanup.

Human Park was described but had no durable production transition. Together,
these gaps made a feature-Workspace invocation, Plan-mode write, interrupted
Park, and host-first archive impossible to distinguish safely at the real CLI
boundary.

## Decision

All state-changing CLI groups require an ephemeral Coordinator context made
from fresh Paseo MCP, collaboration-mode, and Git readback. The CLI validates
Actor ID, cwd, Workspace, write capability, and branch before constructing the
GitHub adapter. Plan/planning/unknown modes fail closed. Workspace choice is
current eligible, configured ID, then unique eligible. A non-stable caller
returns one forward/create-root action and ends. Without explicit configuration,
`dev` is inferred only from an existing remote ref and exactly one stable dev
Workspace; `main` is never guessed.

Park and resume use durable two-phase states (`parking -> blocked/parked` and
`resuming -> running`) with deterministic action IDs. Stop readback releases
Slot/Hotset; resume first revalidates contract hash, base, dependencies,
capacity, Hotset, and the original Worker identity. Reconcile continues an
interrupted transition from Agent readback instead of creating another Worker.

Cleanup classifies runtime evidence as `present`, `auto_archived`, or `invalid`.
Auto-archive is accepted only when one archived Dispatch-labeled Agent and the
durable Worker, Workspace, branch, and merged candidate records agree. Removed
cwd is then expected. A missing remote branch is already complete; an exact
candidate ref may be compare-and-swap deleted; ambiguity or a newer commit
remains blocked.

The three marker-delimited JSON records share one codec. New entry and runtime
evidence structures are typed. Splitting the remaining broad core module is
deferred because it is unrelated to this safety boundary.

## Consequences

- Paseo auto-archive stays enabled and needs no host modification.
- Every write caller must supply fresh context and execute returned forwarding
  or lifecycle actions through Paseo before ending the turn.
- Context carries the raw request transiently for forwarding only; it is never
  GitHub truth and its temporary file is removed.
- Existing installed 6.0.0 Workers may finish their self-contained contracts;
  new Dispatches and Coordinators use 6.0.1.
- The policy is stricter when evidence is absent, duplicated, stale, or changed.

## Alternatives rejected

- Disabling Paseo auto-archive: duplicates host lifecycle policy and loses the
  convenient merged-PR cleanup behavior.
- Guessing `main` or `dev` from naming alone: unsafe across repositories.
- Keeping Park only as prose or an in-memory action: loses crash recovery and
  can release concurrency protection before the Worker actually stops.
- Accepting any missing cwd as completed cleanup: could delete a branch whose
  Worker identity or merged candidate no longer matches.
