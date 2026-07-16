# Visible Worker execution core

Load this compact reference only for an assigned sidebar-visible Worker. The
GitHub Issue owns product acceptance; the repository owns verification
commands; the Orchestrator owns review and lifecycle.

## Contract gate

Accept exactly one Issue, Task, isolated worktree, future branch, PR target,
callback, and hotset. Require the v2 verification fields, pinned base SHA,
permission profile, and explicit model contract:

```text
execution_lane: visible-worker
model_binding: ollama-cloud/glm-5.2
model_reasoning_effort: max
model_binding_requirement: best-effort | exact-runtime
model_binding_status: request-accepted | runtime-verified
```

Do not silently switch to GPT. A rejection, unknown status, contradictory
evidence, missing identity, open architecture choice, or insufficient authority
produces one `BLOCKED` signal before edits.

## Activation and preflight

The initial full turn sends `WORKER_BOOTED` before repository writes. Run the
packaged read-only preflight against the exact base and permissions. Keep the
worktree clean and make no branch, source, GitHub, or PR write before the
Orchestrator's literal `START` continuation.

After preflight, send `PREFLIGHT_READY` and become idle. The Orchestrator claims
and reads back the Issue, verifies one editor, then sends `START`. Only that
receipt authorizes implementation.

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
merge, close, unassign, reprioritize, or create another Task.

## Publish and callback

Finish local candidate checks before the first push. Commit and push the one
assigned branch, open/update the assigned PR, and send `PR_OPENED` immediately.
CI, Orchestrator review, and safe evidence then run in parallel.

Build the canonical signal with `scripts/worker_signal.py`, then send it through
native Task messaging to the exact Orchestrator callback. Retry one transport
failure with the same Signal-ID; after a second failure record
`CALLBACK_DELIVERY_FAILED` and stop.

Every material report includes verification class, timings, full-suite count,
`Review-Runs: 0`, scope delta, changed paths, and next action.

## Recovery

On Task-host failure, stop editing. Preserve useful WIP through one scoped
checkpoint and verified remote SHA when possible. Never reset, force-clean, or
activate a second editor. The Orchestrator loads its detailed Visible Worker
recovery reference only after this failure.
