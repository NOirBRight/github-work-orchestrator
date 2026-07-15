# Worker execution core

This compact role reference contains only the rules an assigned Worker must
load. The GitHub Issue owns product acceptance; the target repository owns the
verification commands. A Worker executes those authorities and does not add
new gates.

## Contract gate

Accept exactly one Issue, visible Task, worktree, branch, PR, callback, and
hotset. Require the v2 verification fields, pinned base SHA, permission profile,
requested model, `model_binding_status: verified`, and sanitized effective
binding evidence. A requested UI/API setting without effective-runtime evidence
is unverified. Missing identity, an open architecture decision, an unverified
binding, or insufficient authority produces one `BLOCKED` signal before edits.

## Deterministic preflight

Run the packaged `scripts/preflight.py` before edits. It checks the effective
filesystem/network/approval profile, exact HEAD and integration ref, branch,
clean status, and—when required—GitHub identity/repository. Every child command
has a bounded timeout. One timeout or failure is terminal for that preflight;
do not loop or change credentials.

Preserve unrelated work. Treat WIP as present only when status is dirty or a
commit is absent from the verified remote branch. On task-host recovery, keep
the existing worktree and either verify no WIP or push one scoped checkpoint;
never reset, overwrite, or create a successor implicitly. Prove a collision
from a merge base and the intersection of each side's changed paths.

## Assigned verification class

Read and apply only the assigned row:

| Class | Candidate verification | Formal review |
|---|---|---|
| `fast` | Targeted checks and diff hygiene; no local full suite | Worker runs none; Orchestrator checks directly |
| `standard` | Targeted checks, then one repository-defined relevant full suite | Worker runs none; Orchestrator owns one Standards/Spec pass |
| `strict` | Targeted checks, one relevant full suite, and only `Manual-Evidence` explicitly named by the Issue | Worker runs none; Orchestrator owns one Standards/Spec pass |

After review fixes, run affected targeted checks and CI. Repeat a full suite
only when the delta crosses a new repository verification boundary. Never use
the generic `code-review` Skill or create Standards/Spec review subagents.
Manual/live evidence is required when and only when `Manual-Evidence` is not
`none`; the verification class alone never creates it.

## Candidate publication loop

Complete the assigned local candidate verification before the first push. Do
not publish intermediate WIP only to start CI; a scoped recovery checkpoint or
an explicitly remote-only gate is the exception. Publish one locally green
candidate, open/update its PR, and send `PR_OPENED` immediately without waiting
for CI.

The Orchestrator runs CI observation, formal review, and safe candidate
artifact/manual evidence concurrently. After a review or manual-gate fix,
preserve the prior full-suite count, run affected checks, and publish one new
candidate. Do not wait for one independent gate before starting another.

Manual behavior evidence normally runs before merge. After merge, repeat a
build or behavior gate only when the integrated Git tree differs from the
accepted candidate, repository policy requires integrated revision identity,
or the release artifact itself is the acceptance object.

## Direction and scope

Implement the smallest coherent accepted scope. Send `DISCUSSION_REQUIRED`
before choosing a durable architecture, public/persisted contract,
compatibility, security/privacy, migration, cross-Issue, or new-system-boundary
decision. Continue only independent safe work. Do not merge, close, unassign,
reprioritize, or broaden hotsets without authority.

## Publish and callback

Commit, push, and open/update only the assigned PR target. Send `PR_OPENED`
immediately after the locally green candidate exists so integration gates can
run in parallel. Before the final response, build the canonical signal with
`scripts/worker_signal.py`, then send that exact envelope to the Orchestrator
callback through native Task messaging.
A successful native tool result is the receipt. On transport error retry once
with the identical envelope and stable generated Signal-ID; after a second
failure record `CALLBACK_DELIVERY_FAILED` and stop. GitHub comments are not a
hidden callback channel.

Every material signal includes verification class, phase timings,
full-suite count, `Review-Runs: 0`, scope delta, verification, hotset, and next
action. A same-boundary review/manual correction targets a locally green
replacement candidate within 15 minutes, excluding remote CI and explicit
human wait. The 30-minute `fast` and 90-minute `standard` targets diagnose
friction; they never authorize skipped acceptance.
