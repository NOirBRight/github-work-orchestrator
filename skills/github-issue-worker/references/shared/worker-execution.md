# Worker execution core

Load this compact reference for an assigned Codex sidebar-visible Worker or a
ZCode Subagent whose resolved surface is `subagent`. The GitHub Issue owns
product acceptance; the repository owns verification commands; the
Orchestrator owns review and lifecycle.

## Contract gate

Accept exactly one Issue, isolated worktree, future branch, PR target, and
hotset. A Codex Visible Worker also requires one exact Task and callback. A ZCode
Subagent instead requires `execution_host: zcode`, the resolved `subagent`
surface, and its exact parent-owned Agent identity after launch; it does not
require a Task UUID, callback Task, creation lease, or archive. Require the v2
verification fields, pinned base SHA, permission profile, and explicit model
contract:

```text
execution_lane: subagent | visible-worker
model_binding: ollama-cloud/glm-5.2
model_reasoning_effort: max
model_binding_requirement: best-effort | exact-runtime
model_binding_status: request-accepted | profile-verified | runtime-verified
```

A ZCode Subagent also requires the exact `haiku` request alias and separate
accepted or runtime-verified reasoning evidence for explicit `max`. Do not
silently switch to GPT. A rejection, unknown or contradictory status, missing
identity, open architecture choice, or insufficient authority produces one
`BLOCKED` result before edits.

## Activation and preflight

For a Codex Visible Worker, the initial full turn sends `WORKER_BOOTED` before
repository writes. Run the packaged read-only preflight against the exact base
and permissions. Keep the worktree clean and make no branch, source, GitHub, or
PR write before the Orchestrator's literal `START` continuation. After
preflight, send `PREFLIGHT_READY` and become idle. The Orchestrator claims and
reads back the Issue, verifies one editor, then sends `START`. Only that receipt
authorizes implementation.

For a ZCode Subagent, the Orchestrator completes the claim, readback, exact-base,
one-editor, model, reasoning, and permission gates before the `Agent` call. The
initial full prompt is the authorization to implement. Do not add a second
`WORKER_BOOTED`/`PREFLIGHT_READY`/`START` state machine.

## Execution-only CWD

Treat the assigned worktree as an execution-only CWD, never as a Codex Saved
Project, Saved Workspace, or Skill installation root. Use the exact absolute
path supplied by the supported native execution contract. Never open or
register the worktree as a project, switch projects, or persist it as a
workaround. If the native lane cannot use the assigned CWD without persistence,
send one sanitized platform limitation in a `BLOCKED` signal before edits.

Do not read-modify-write `.codex-global-state.json`,
`electron-saved-workspace-roots`, Codex SQLite, or equivalent private desktop
state. Never copy, install, junction, symlink, or generate a Skill in the
repository or worktree, and never create a dynamic `SKILL.md`, plugin, or
project-local Skill for execution state. GitHub and the native Task or Agent
contract remain the only execution-state carriers. Each role Skill resolves from exactly
one repository-documented canonical installation. Version-controlled package
sources and lazy `references/`, `scripts/`, and `assets/` are not extra runtime
Skill installations.

## Assigned verification class

| Class | Candidate verification | Formal review |
|---|---|---|
| `fast` | Targeted checks and diff hygiene; no local full suite | Worker runs none; Orchestrator checks directly |
| `standard` | Targeted checks, then one relevant full suite | Worker runs none; Orchestrator owns one Standards/Spec pass |
| `strict` | Targeted checks, one relevant full suite, and explicit manual evidence only | Worker runs none; Orchestrator owns one Standards/Spec pass |

After same-boundary review fixes, run affected checks and CI. Repeat a full
suite only after a new verification boundary. Never invoke the generic formal
review from the Worker.

## Direction and scope

Implement the smallest accepted scope. Stay inside the assigned worktree and
hotset. Send `DISCUSSION_REQUIRED` before choosing durable architecture, public
compatibility, security/privacy policy, migration, or cross-Issue scope. Do not
merge, close, unassign, reprioritize, or create another Task or Agent.

## Publish and callback

Finish local candidate checks before the first push. Commit and push the one
assigned branch, open/update the assigned PR, and report `PR_OPENED`
immediately. CI, Orchestrator review, and safe evidence then run in parallel.

A Codex Visible Worker builds the canonical signal with
`scripts/worker_signal.py` and sends it through native Task messaging to the
exact Orchestrator callback. Retry one transport failure with the same Signal-ID;
after a second failure record `CALLBACK_DELIVERY_FAILED` and stop.

A ZCode Subagent returns its final result through the parent collaboration
channel. Parent revisions use `SendMessage` with the exact `agentId`; when a
semantic receipt matters, use the same Signal-ID once and retry only an
identical envelope after one transport failure.

Every material report includes verification class, timings, full-suite count,
`Review-Runs: 0`, scope delta, changed paths, and next action.

## Recovery

On Task or Agent host failure, stop editing. Preserve useful WIP through one
scoped checkpoint and verified remote SHA when possible. Never reset,
force-clean, or activate a second editor. The Orchestrator loads its detailed
Visible Worker recovery reference only for a Codex Task failure. A ZCode parent
restart does not prove Agent survival or terminality; if no supported readback
proves the predecessor terminal, preserve the worktree and block a successor.
