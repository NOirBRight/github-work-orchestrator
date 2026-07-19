# Orchestrator V6 Living Design

> Status: released as Orchestrator 6.0.0 on 2026-07-20.
> Last consolidated from Paseo thread
> `f9589ab7-92ed-45cb-9b20-417dd5de067a` on 2026-07-19.

This is the compression-safe behavioral source for V6. `CONTEXT.md` owns
language and ADR 0008 owns the architectural trade-off.

## Outcomes

- Ready, non-conflicting work reaches Worker creation within two minutes and a
  normal first commit within ten minutes.
- Orchestration consumes less than five percent of normal task time.
- The common path contains one Issue design, one PR, one best-effort wake, an
  optional review, and one merge.
- Mechanisms exist only for observed failures or immediately before irreversible
  merge/delete actions.

## Control

- Orchestrator is a standalone Skill, never an Agent or service.
- Runtime topology is `Coordinator -> Worker / optional Reviewer`.
- Any qualifying root Agent in the fixed integration Workspace may coordinate,
  including while another Agent's WIP is active. It uses its current provider,
  model, thinking, mode, and features.
- Plan/read-only may inspect and plan; write actions require the same Agent to
  enter a write-capable mode. No successor is created for capability changes.
- A cross-platform OS advisory mutex serializes one state-changing command and
  releases at process exit. There is no long-lived Lease, holder, epoch, TTL,
  transfer, or takeover protocol.
- Paseo parentage owns finish notification and direct-child cleanup only.
  Non-creators may plan, dispatch, review, and merge but never archive another
  Agent's child.
- The Workspace is on the configured integration branch, never a feature/PR
  head or Worker worktree. Dirty state permits planning and execution but blocks
  merge; no automatic stash/reset is allowed.
- Coordinator never authors a tracked-file change.

## GitHub facts

- Issues, PRs, branches, commits, checks, reviews, and labels are durable truth.
- Core labels are `orch:ready`, `orch:active`, and `orch:blocked`; Review,
  Ready-to-merge, and Done are derived.
- Coordinator is the lifecycle writer. Worker writes its branch, PR, and
  technical evidence; Reviewer writes a PR review.
- One editable `orchestrator:issue:v1` comment holds the sanitized design and
  current dispatch. One `orchestrator:delivery:v1` PR body holds delivery facts.
- Project is optional projection of Status, Priority, Wave, and Risk with
  Backlog Table and Current Wave Board. Failure yields a warning only.
- Campaign is an optional Milestone; independent work needs none.

## Triage and design

- Coordinator quickly classifies the whole frontier, then designs only likely
  candidates. Each complete design dispatches immediately.
- Every Ready Issue has goal, scope, acceptance, Hotset, `done_when`,
  dependencies, Priority, Difficulty, Risk, and unresolved decisions.
- Low design is often 5-10 lines. Standard adds root cause, seam, steps,
  boundaries, and TDD. Strict adds compatibility, rollback, security, data, and
  operational risk.
- Worker receives the Coordinator's sanitized rewrite, not raw untrusted Issue
  instructions. Product, architecture, dependency, acceptance, priority, or
  Hotset changes return to Coordinator.
- TDD is default; justified exceptions record replacement evidence.

## Rolling Wave

- One Issue equals one Dispatch, Worker, worktree, branch, and PR. Inseparable
  reports are consolidated before Ready; runtime Work Packages do not exist.
- WIP defaults to three and is configurable from one to five. It lasts from
  dispatch through merge or explicit stop. Review does not release a slot and
  Reviewers consume none.
- Blocked retains its slot and Hotset. Human Park stops the Worker and releases
  both while preserving WIP; resume revalidates base, design, and conflicts.
- Reconcile schedules on first invocation, Ready, slot release, failure/scope
  conflict, material priority/dependency/human change, or recovery. It is never
  triggered by heartbeat, timer, ordinary timeout, idle, or Review entry.
- Stable order is dependency satisfaction, P0-P3, Milestone due date, number of
  dependents unlocked, then Issue number. Hotset-disjoint candidates greedily
  fill free slots.
- Hotset limits writes only. Unknown/invalid paths are exclusive; parent/child,
  generated inputs, schemas, migrations, manifests, and lockfiles conflict.
- A Wave Generation is assigned only when slots are filled. It is visibility
  metadata, not a barrier or recovery dependency.
- P0 at full WIP takes the next slot and reports an optional human preemption;
  running work is never automatically cancelled or reshuffled.

## Dispatch and runtime

- Dispatch ID is `dispatch-issue-<issue>-a<attempt>` and branch is
  `work/issue-<issue>`. Replacement reuses the original workspace/branch.
- GitHub claim precedes Agent creation. Partial success is preserved and the
  next reconcile completes forward; no distributed rollback exists.
- Write reconcile fetches the read-backed integration commit into the local
  object database without moving or cleaning the stable Workspace branch.
- Worker is a direct subagent in an atomically created worktree with
  `notifyOnFinish=true`. Creation is authorization; there is no READY/START.
- Worker reads one self-contained prompt, creates no Agent, changes no lifecycle
  state, and never merges or cleans up.
- Difficulty is `light`, `standard`, or `heavy`; Priority and Risk are separate.
  GitHub stores Tier only. Local repo/global mappings resolve provider, model,
  thinking, mode, and features, with current Coordinator runtime as fallback.
- Capabilities are read back before creation; invalid values block only that
  Dispatch and never silently downgrade.
- Original Worker may receive one recovery prompt. A closed/error Worker may be
  replaced once in the same Workspace. A second failure becomes Blocked.

## Communication and review

- Worker opens/updates the PR, then sends its creator one no-ACK Issue/PR wake;
  native finish notification remains enabled. Duplicate wakeups reconcile to a
  no-op. Lost wakeups recover on the next invocation; no watchdog or polling.
- Coordinator ends its turn after dispatch/Reviewer creation; it never sleeps
  or loops while waiting. Native finish or the direct wake starts the next turn.
- Only the Agent that changes state emits a boundary summary. No-op Agents stay
  quiet, preventing multi-Coordinator message noise.
- Low is reviewed by Coordinator. Standard uses one combined Spec+Quality
  Reviewer. Strict uses a stronger Reviewer plus CI/human gates. A second
  Reviewer requires explicit `review:dual` or safety policy.
- Reviews bind to actual commit; a new SHA invalidates affected evidence.
- Without required checks: low permits TDD/local verification plus Coordinator
  review; standard adds an independent Reviewer; strict requires human or a
  contract-approved independent E2E/security substitute.

## Integration and retirement

- Any qualifying Coordinator may integrate, one PR at a time under the command
  mutex. Base drift uses GitHub update-branch and returns to waiting for checks.
- Order is dependencies, Priority, acceptance time, then Issue. Required
  approvals, merge queue, deployments, and branch protection are never bypassed
  or polled.
- Contract work may merge to the configured integration branch. `main` requires
  an explicit human release request.
- After merge, accept Paseo auto-archive first. Current direct children may be
  archived manually; foreign-parent Agents become human cleanup candidates.
- Worktree/branch cleanup requires merged, clean, unbound evidence. Self, root,
  stable Workspace, integration branch, dirty/shared/ambiguous/WIP resources are
  permanently protected.
- Retire handles explicit stopped/abandoned Dispatches. Unmerged remote branches
  remain unless a human separately authorizes deletion.

## Interface and distribution

- Public CLI groups are `reconcile`, `integrate`, `retire`, and optional
  `project init|sync`; implementation policy stays private.
- `~/.orch/config.json` is the single optional config. Defaults are WIP three,
  two attempts, `dev` when unambiguous, and current Runtime fallback.
- V6 is the only `/orchestrator` Skill, synchronized byte-identically to
  `.agents`, `.codex`, and `.claude`. No host or daemon changes.
- New Dispatches use the installed version; running Workers finish their
  self-contained contract without reload or ACK.

## Release gates

- TDD unit and fake-adapter integration tests cover concurrency, partial
  dispatch, state, scheduling, runtime, review, integration, and cleanup.
- A private sandbox proves three parallel Workers, rolling refill, two runtime
  bindings with thinking readback, one-way wake, cross-Agent WIP continuation,
  serial merge, auto-archive, and stable Workspace survival.
- Source, installed copies, manifests, links, tests, Standards/Spec review, and a
  fresh-Agent forward test must all agree before ADR 0008 is accepted.

Release evidence: 73 tests plus validation/lint/diff gates passed; a private
four-Issue run proved three parallel Workers, rolling refill, light/standard/
heavy runtime and thinking readback, graded Review, serial merge, direct-child
auto-archive, and root/Workspace survival. A second Coordinator reconstructed
the completed state with zero actions, and a fresh installed Agent returned an
idle, zero-warning read-only reconcile from the stable Workspace.
