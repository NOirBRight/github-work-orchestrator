---
status: amended by ADR-0036, ADR-0039, and ADR-0043
---

# Require verified evidence before completion

An Attempt submitting a typed result reaches `result_submitted`, not
completion. Independently checked evidence advances it to `verified`; a Work
Item becomes `completed` or `integrated` only when its acceptance and
integration conditions are satisfied. Worker self-report never collapses these
states. Artifacts are produced objects, Evidence is independently observed,
and Verification is the acceptance decision that connects them. A Plan Node
accepts at most one Result; other Attempts retain their records without becoming
co-equal outcomes. Objective Evidence checks precede any semantic review. A
terminal Attempt does not make its Plan Node failed until the node's
authorized recovery policy is exhausted.

Result Adoption across Plan Revisions requires an exact unchanged Node Key and
contract digest. Evidence that depends on the current target branch must be
refreshed before the adopted Result can contribute to Work Item completion or
integration.

Typed Evidence uses one common envelope containing its kind, subject,
observer, observation time, source reference, payload, and content digest.
V8.0 defines six evidence kinds: `runtime`, `candidate`, `check`, `review`,
`integration`, and `decision`. Local commands and hosted CI are both `check`
Evidence with different sources. An Agent's submission is a Result Claim and
Artifact reference, not self-authenticating Evidence; Kernel, Runtime Adapter,
GitHub/CI, or a durable human decision must observe the supporting fact.

Each Plan Node's output contract declares required Evidence. The deterministic
risk compiler may add requirements, but an Agent, Skill, or Semantic Planner
cannot remove them. Evidence binds to the relevant candidate, base, and
acceptance digests. A changed base expires only base-sensitive Evidence, while
a changed candidate diff or acceptance digest invalidates the Evidence and
review that depended on it.

GitHub stores a compact Evidence Manifest containing required digests and
durable source references. Large logs and Artifacts remain in CI, artifact, or
runtime storage; an acceptance-critical reference cannot point only to an
ephemeral local file.

Check Evidence is validated rather than repeated by default. A reusable check
binds its exact candidate SHA or tree digest, canonical command or hosted check
definition, execution-environment identity, timestamps, outcome, observer, and
durable log digest or reference. A Runtime Adapter may directly observe a local
command; GitHub readback observes a hosted check even when the Worker triggered
it. Risk tiers determine the required check set and acceptable evidence sources,
not the number of executions of the same valid check. Rerun occurs only when
the subject changed, provenance or readback is missing, the check or environment
does not meet the contract, or the contract explicitly requires repetition for
a concrete risk such as flakiness, migration rehearsal, or release
qualification. A Worker may use narrow checks for feedback, but an executable
Candidate must produce the repository-equivalent full local Check Evidence
once before publication unless a check is explicitly hosted-only. Expensive
authoritative CI may therefore run once.

A Runtime Adapter may produce local Check Evidence only when it directly
observes the executed tool or process, its outcome, and its binding to the
candidate. Without that capability, an Agent's reported command remains a
Result Claim and another source such as hosted CI must supply the Evidence.

A failed Verification produces `candidate_rejected`; it does not end the
Attempt while an authorized Repair Round remains. Exhausting that repair ends
the Attempt with terminal reason `rejected`. A healthy execution ending
without a candidate records `no_result`; irrecoverable runtime loss records
`runtime_lost`. Unavailable CI or another missing Evidence source is a Wait
Condition rather than a candidate verdict. Runtime loss cannot make the Plan
Node semantically failed.
