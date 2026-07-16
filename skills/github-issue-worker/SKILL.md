---
name: github-issue-worker
description: Execute one assigned GitHub Issue as a sidebar-visible Worker in one isolated worktree, honoring the pinned base, future branch, hotset, GLM-5.2 binding, permissions, acceptance, verification, callback, publication, and PR target. Use only when an Orchestrator explicitly selects the Visible Worker lane and supplies a complete contract.
---

# GitHub Issue Worker

Own one assigned execution, worktree, branch, PR, and evidence trail. Do not
claim another Issue or spawn a second visible Task.

## Accept one contract

Require:

1. Issue URL and acceptance criteria.
2. Repository instructions and accepted decisions.
3. Exact base branch/SHA, isolated worktree, future feature branch, and PR
   target.
4. Hotset, prohibited writes, and one-editor boundary.
5. Verification class/commands, manual evidence, architecture decision, and
   `Review-Owner: orchestrator`.
6. `execution_lane: visible-worker`.
7. `model_binding: ollama-cloud/glm-5.2`, reasoning `max`, binding
   requirement/status, and sanitized evidence.
8. Effective permission profile and exact Orchestrator callback.

Read [Visible Worker execution core](references/shared/worker-execution.md).
Stop with `BLOCKED` before edits for missing identity, unavailable GLM binding,
unknown/rejected status, insufficient permissions, wrong base/worktree, open
architecture decision, or conflicting ownership. Never silently use GPT.

## Boot and pass preflight

Send `WORKER_BOOTED` before repository or GitHub writes. Run the packaged
deterministic preflight from the exact assigned worktree:

```text
python <skill>/scripts/preflight.py --cwd <worktree> \
  --expected-base <sha> --integration-ref origin/<branch> \
  --filesystem <effective-value> --network <effective-value> \
  --approval <effective-value> --require-github
```

Send `PREFLIGHT_READY` and become idle. Do not create a branch, edit source,
write GitHub, commit, push, or open a PR until the Orchestrator sends literal
`START` and its native receipt identifies this Task.

## Keep the worktree execution-only

The assigned worktree is an execution-only CWD, not a Codex Saved Project,
Saved Workspace, or Skill installation root. Use only its exact absolute path
from the native execution contract. Never open or register it as a project,
switch projects, or persist it as a workaround. If the platform cannot use that
CWD without persistence, send a sanitized platform limitation in a `BLOCKED`
signal before edits.

Follow the packaged
[execution-only CWD rules](references/shared/worker-execution.md#execution-only-cwd).
Do not read-modify-write `.codex-global-state.json`,
`electron-saved-workspace-roots`, Codex SQLite, or equivalent private state.
Never copy, install, junction, symlink, or generate a Skill in the repository
or worktree, and never create a dynamic `SKILL.md`, plugin, or project-local
Skill for runtime state. Each role Skill resolves from exactly one
repository-documented canonical installation.

## Implement the assigned scope

After `START`, post a short plan naming expected writes, verification, and
collisions. Reproduce a bug at a public seam and add the smallest faithful
regression when applicable. Implement vertically and stay inside the assigned
hotset.

Send `DISCUSSION_REQUIRED` before choosing durable architecture, public or
persisted compatibility, security/privacy policy, migration, or cross-Issue
scope. Continue only safe independent work.

Do not invoke the generic `code-review` Skill or create Standards/Spec review
Subagents. The Orchestrator owns the formal review.

## Verify

- `fast`: targeted checks and diff hygiene; no local full suite.
- `standard`: targeted checks, then one relevant full suite.
- `strict`: targeted checks, one relevant full suite, and explicit manual
  evidence only.

After same-boundary review fixes, run affected checks and rely on CI. Repeat a
local full suite only after a new verification boundary. Inspect the final diff
for hotset, credentials, private IDs/paths, generated artifacts, and unrelated
changes.

## Publish and signal

Finish local candidate checks before the first push. Commit, push, and
open/update only the assigned PR. Do not wait for CI before sending the locally
green `PR_OPENED` signal.

Use the compact
[publish and callback](references/shared/worker-execution.md#publish-and-callback)
rules and `scripts/worker_signal.py`. Send the exact envelope to the exact
Orchestrator callback before the final response. Retry one transport failure
with the same Signal-ID; after another failure record
`CALLBACK_DELIVERY_FAILED` and stop.

Return outcome, changed files, commit/PR, targeted/full verification, timings,
full-suite count, `Review-Runs: 0`, scope delta, blockers, and whether the Issue
can close.

## Stop and cleanup handoff

On host failure, stop editing and preserve WIP through one scoped pushed
checkpoint when possible. Never reset or activate a second editor. On merge or
stop, report the exact durable/clean state so the Orchestrator can remove the
eligible worktree and delete a merged local branch within five minutes. Task
archiving is human-owned; the Orchestrator reports this exact Task as ready for
archive and never invokes the native archive action automatically.
