---
name: github-issue-worker
description: Execute one assigned GitHub Issue in one sidebar-visible Codex task and isolated worktree, honoring the pinned base SHA, branch, hotset, model binding, permission profile, acceptance criteria, verification, commit, push, and PR target. Use when an Orchestrator supplies an explicit assigned-Issue execution contract.
---

# GitHub Issue Worker

Own one assigned Issue, worktree, branch, PR, and evidence trail. The visible
task is the Worker of record; bounded subagents may assist inside this Issue but
do not own another Issue, branch, PR, or lifecycle.

## Accept one execution contract

Read the Orchestrator message and identify:

1. Issue URL and acceptance criteria.
2. Applicable repository instructions.
3. Model profile and concrete binding.
4. Exact base branch/SHA, feature branch, and PR target.
5. Owned components/hotset and prohibited writes.
6. Accepted decisions and local decision authority.
7. Verification class/commands, manual evidence, architecture decision,
   `Review-Owner: orchestrator`, and dependencies.
8. Requested model plus `model_binding_status: verified` and sanitized
   effective-binding evidence.
9. Closing semantics and callback task.

Read the compact [Worker execution core](references/shared/worker-execution.md).

Stop with `BLOCKED` when the assignment is missing a required identity or when
the requested model, permissions, base, branch, or worktree cannot be honored.
Do not claim another Issue or silently broaden this one.

The contract is accepted when every identity and authority field is explicit
and the task owns exactly one Issue.

## Pass preflight before edits

Apply the compact core's
[deterministic preflight](references/shared/worker-execution.md#deterministic-preflight).
Read applicable `AGENTS.md`, domain context, ADRs, the Issue and comments,
linked PR state, and the base diff. Post a short plan naming expected writes,
verification, and collision evidence before editing. Read the recovery paragraph
in the same compact reference only when task-host failure or succession makes
that branch relevant.

When the packaged preflight script is available, run it instead of manually
reconstructing the same Git/GitHub checks:

```text
python <skill>/scripts/preflight.py --cwd <worktree> \
  --expected-base <sha> --integration-ref origin/<branch> \
  --expected-branch <feature-branch> \
  --filesystem <effective-value> --network <effective-value> \
  --approval <effective-value> --require-github
```

Permission values come from task-host metadata; the script validates and
records them but cannot grant a broader profile.

Preflight is complete when effective permissions are sufficient, GitHub access
works without approval prompts, the branch matches the assigned base, and the
expected write set is collision-safe.

## Build a red-capable execution loop

For a bug, reproduce the reported symptom at a public seam before the fix when
applicable. Convert the smallest faithful reproduction into a regression test.
For a feature, use the narrowest acceptance example as the first verification
slice. Work vertically: one observable behavior, its minimal implementation,
then the next.

Escalate with `DISCUSSION_REQUIRED` when evidence exposes a product-direction,
durable architecture, public contract, compatibility, security/privacy,
migration, or cross-Issue choice outside the accepted authority. Continue only
safe independent work while waiting.

The loop is ready when it can distinguish the requested behavior from the
reported failure without relying on private implementation details.

## Implement and verify the assigned scope

Make the smallest coherent change that satisfies the Issue. Keep subagent
work bounded to research or test analysis inside this Issue and integrate every
result in this visible task. Do not invoke the generic `code-review` Skill or
create Standards/Spec review subagents; the Orchestrator owns the one formal
review.
Compare upstream changes from the merge base before claiming a collision; an
advanced integration branch alone is not a blocker.

Run targeted verification during implementation. Apply only the assigned row
in the compact [verification-class table](references/shared/worker-execution.md#assigned-verification-class):

- `fast`: targeted checks and diff hygiene; no local full suite.
- `standard`: one repository-defined relevant full suite at the candidate.
- `strict`: one relevant full suite plus only the Issue's explicit harness or
  manual evidence.

After review fixes, run affected targeted checks and rely on CI. Repeat a local
full suite only when the fix crosses a new repository verification boundary.
Review the final diff for hotset, generated artifacts, credentials, private
task IDs, local paths, and unrelated changes.

Finish the affected local checks before the first candidate push. Do not push
intermediate WIP merely to obtain CI unless preserving recovery WIP or using an
explicitly remote-only gate. Publish one locally green candidate and send
`PR_OPENED` immediately; CI, Orchestrator review, and safe candidate artifact
or manual evidence then run in parallel. Preserve previous full-suite and
review counts for a same-boundary correction.

Implementation is complete when acceptance criteria pass, required checks are
recorded, and the final diff contains only authorized work.

## Publish and signal

Commit, push, and open or update only the assigned PR under the compact
[publication rules](references/shared/worker-execution.md#publish-and-callback).
Do not wait for CI before sending the locally green candidate's `PR_OPENED`
signal.

Before the final response, build the canonical signal with
`scripts/worker_signal.py` and complete the compact
[callback handshake](references/shared/worker-execution.md#publish-and-callback)
to the exact Orchestrator callback.

Worker completion requires a clean or intentionally preserved worktree, a
reviewable remote commit/PR when publication was assigned, complete evidence,
and a recorded callback delivery outcome.

Every material signal records verification class, phase timings, full-suite
count, `Review-Runs: 0`, and scope delta. The 30-minute `fast` and 90-minute
`standard` targets diagnose friction; they do not authorize skipped acceptance
or an automatic stop. If scope crosses a new public/shared boundary, send
`DISCUSSION_REQUIRED` before expanding.
