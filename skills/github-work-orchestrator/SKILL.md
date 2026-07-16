---
name: github-work-orchestrator
description: Orchestrate GitHub execution campaigns by aligning direction, reconciling Issues and dependencies, routing work across Inline, Subagent, or Visible Worker lanes, binding implementation Workers, reviewing PRs, integrating results, and cleaning completed work. Use when Codex must run a GitHub campaign, compute/refill a ready frontier, dispatch implementation, or arbitrate integration.
---

# GitHub Work Orchestrator

Keep GitHub as the persistent work-state source. Keep one visible Orchestrator
per repository/activity and use the lightest safe implementation lane.

## Load the control plane

Before planning or writing:

1. Read applicable `AGENTS.md` and repository issue, Git-flow, testing, and
   release instructions.
2. Read [GitHub state rules](references/shared/github-state-rules.md),
   [lifecycle](references/shared/lifecycle.md), and
   [model profiles](references/shared/model-profiles.md).
3. Read [verification policy](references/shared/verification-policy.md),
   [communication protocol](references/shared/communication-protocol.md),
   [issue contract](references/shared/issue-contract.md), and
   [reconciliation](references/reconciliation.md).
4. Load [Visible Worker contract](references/worker-contract.md) only after the
   router selects Visible Worker. Load detailed
   [Visible Worker recovery](references/communication.md) only after a failure.

Repository policy overrides defaults. Do not edit Codex SQLite.

## Separate intake and execution

Route ordinary reports, screenshots, and rough ideas to `github-issue-intake`
when available. The Orchestrator reconciles priority, labels, dependencies,
direction, capacity, and integration. It may implement through Inline or a
bounded Subagent; it no longer creates a visible Task merely because an Issue
exists.

## Reconcile the frontier

Run:

```text
python <skill>/scripts/validate_issue_state.py --cwd <repository>
python <skill>/scripts/ready_frontier.py --cwd <repository> --json
```

Preview deterministic corrections with `reconcile_issue_state.py`, apply only
unambiguous authorized changes, then revalidate. Recompute after merge, stop,
accepted decision, blocker change, or released capacity.

## Route the execution lane

Use the deterministic router:

```text
python <skill>/scripts/execution_policy.py lane \
  --expected-minutes <n> [--same-boundary] \
  [--restart-persistence] [--manual-ui-or-login] \
  [--prolonged-observation] [--independent-visible-context]
```

- **Inline** — small same-boundary work expected in about 15 minutes. The
  Orchestrator keeps its GPT binding and works in an isolated worktree.
- **Subagent** — default bounded implementation. The Orchestrator owns the
  Issue, branch, publication, evidence, and review; one Subagent receives a
  non-overlapping worktree/write set and returns its result.
- **Visible Worker** — only for restart persistence, manual UI/login, prolonged
  observation, or independently visible context.

Every lane preserves exact base, isolated worktree, one editor, permissions,
hotset, verification, and durable evidence. Do not turn exhausted Subagent
capacity into a Visible Worker unless a Visible criterion independently holds.

## Enforce host capacity

Keep one visible Orchestrator per repository/activity, three visible Workers globally
at most, and four implementation Subagents per Orchestrator at most, further
bounded by actual host slots. The normal visible-Worker count is zero.

Use `execution_policy.py capacity` before admission. Recompute on material
events only; do not poll for capacity.

All independently bound implementation Subagents and Visible Workers use
`ollama-cloud/glm-5.2` with explicit reasoning `max`. No silent GPT
fallback is allowed. Validate/install the canonical custom `worker` agent with:

```text
python <skill>/scripts/install_worker_agent.py --check
python <skill>/scripts/install_worker_agent.py --install
```

A binding rejection stops that lane for an explicit maintainer decision.

## Execute Inline or Subagent work

Create one isolated worktree at the pinned base. Claim and read back the Issue
before edits. For Inline, implement directly. For Subagent, provide one bounded
task containing exact worktree, branch, hotset, acceptance, verification, model
binding, and return format. The Subagent does not own GitHub lifecycle or
publish independently; the Orchestrator integrates and verifies its result.

If a Subagent cannot use GLM-5.2, report the rejection. Do not reroute it to GPT.

## Keep worktrees execution-only

Treat every Inline, Subagent, and Visible Worker worktree as an execution-only
CWD. Never open, switch to, or persist it as a Codex Saved Project or Saved
Workspace, and never use a repository or worktree as a Skill installation root.
Pass the exact absolute CWD only through the supported native execution
contract.

If a lane cannot run at that CWD without saving or registering the path as a
project, fail it with a sanitized platform limitation. Do not change projects
or edit private desktop state as a workaround. Another lane may proceed only
when the router independently selects it and no competing editor exists.

Follow the canonical [workspace and Skill topology rules](references/shared/github-state-rules.md#execution-cwd-and-skill-installation-topology).
Never read-modify-write `.codex-global-state.json`,
`electron-saved-workspace-roots`, Codex SQLite, or equivalent private state.
Never copy, install, junction, symlink, or generate a role Skill inside a
repository/worktree, and never turn per-Issue state into a dynamic `SKILL.md`,
plugin, or project-local Skill.

## Create a Visible Worker

Before the one native creation call, reserve the host-wide creation singleflight
with a caller-generated private owner token:

```text
python <skill>/scripts/task_creation_lease.py reserve \
  --repository <owner/repository> --issue <number> --branch <feature-branch> \
  --owner-token <private-token>
```

Follow the [Visible Worker contract](references/worker-contract.md). Persist only
`creating`/`uncertain`; release the guard after exact real Task identity is
established. Prefer the owner token; after a recorded queued receipt, its exact
native request identity is the narrow recovery capability for a materialized
Task only. A guard failure blocks only Visible Worker creation. Inline or
Subagent may proceed only when the router independently selects it and no other
editor exists.

Require the exact Orchestrator callback task ID and the
[delivery handshake](references/shared/communication-protocol.md#delivery-handshake).

## Review and integrate

On a locally green candidate, start CI, review, and safe manual evidence in
parallel. For `fast`, review directly. For `standard` or `strict`, run exactly one Orchestrator-owned
parallel Standards/Spec review. Route revisions to the
same owner and review only the changed delta.

Merge only when applicable gates pass. Compare candidate and integrated Git trees;
repeat evidence only for a tree delta, repository identity requirement,
or release-artifact acceptance. Recompute the frontier after integration.

Follow [signal-driven monitoring](references/shared/communication-protocol.md#signal-driven-monitoring);
do not narrate or poll unchanged state.

## Clean completed work

Merge and stop events trigger cleanup; complete eligible cleanup within five minutes.
The deadline is event-triggered, not a five-minute polling loop.

Run `execution_policy.py cleanup-plan` with the exact absolute worktree path,
merged branch name when applicable, and exact Visible Worker Task ID. Then:

1. verify the editor is idle, the worktree is clean, ownership is unambiguous,
   and work is pushed or integrated;
2. remove the exact isolated worktree;
3. delete only a merged local branch; and
4. report the exact Visible Worker Task as ready for human archive.

Task archiving is human-owned. Never call the native Task archive action from
the Orchestrator: native descendant cleanup can cross a delegation boundary.
The five-minute deadline covers eligible worktree/branch cleanup and surfacing
the archive request, not completion of the human action.

Preserve and report dirty, unpushed, ambiguous, active, or externally owned
state. Never force-clean or modify Codex's database.
