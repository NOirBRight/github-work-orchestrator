---
status: superseded by ADR-0041, ADR-0042, ADR-0055, and ADR-0057
amends: ADR-0022, ADR-0025, ADR-0026, ADR-0030, ADR-0031, ADR-0036, ADR-0037, ADR-0038
---

# Model review as a Candidate Evidence gate

V8 review is an Evidence-producing gate inside the Candidate-producing Work
Attempt, not a Plan Node. PlanSpec v2 therefore has `work`, `decision`, and
`integration` node kinds. The deterministic risk compiler writes a
`review_requirement` into a Candidate-bearing node's output contract instead
of creating Review nodes or edges. Review Evidence remains a first-class typed
Evidence kind consumed by publication eligibility, Verification, and
Integration.

The canonical requirement contains only semantic mode (`none`, `dual_axis`, or
`strict`), required Standards and Spec axes, stable repository-policy
specialist IDs, and whether a human decision is required. It never contains
Review Profile, provider, model, thinking, session, capacity, or retry state.
A required human decision is represented by a compiled Decision Gate and typed
dependency, not a Reviewer Agent.

Low-risk work on the deterministic allowlist requires no LLM review. Standard
risk requires independent Standards and Spec axes. Strict risk adds the
specialist or human observation required by the concrete risk. A missing
canonical behavioral contract blocks compilation or creates a Decision Gate;
the Spec axis is never silently skipped for executable GWO work.

After the edit loop produces a clean immutable Candidate, the Runtime Adapter
captures the cheap affected checks once. The Worker never invokes
`code-review`, launches Reviewer children, or reruns repository-wide acceptance
suites. The Kernel launches the required read-only Review-axis children in
parallel under the Worker Runtime identity, with no Worker transcript: each
receives the base and Candidate SHAs, diff command, commit list, canonical Spec
and standards sources, and relevant Check Manifest.
Standards, Spec, and specialist subagents cannot mutate, publish, change
tracker state, integrate, or delegate further. The one repository-equivalent
local suite and Review may then run concurrently on the same clean, parked
exact-SHA worktree.

There is no managed parent Reviewer, Reviewer Admission, Reviewer Attempt,
Reviewer Role Binding, or Reviewer capacity pool. Host-local global defaults
and repository overrides instead map `standard_axis`, `recovery_axis`, and
`strict_specialist` Review Profiles to Runtime Profiles. The initial mappings
are Sol High for standard axes and Sol Max for recovery and strict specialists.
The Kernel requests those children through the Runtime Adapter, so the selected
Paseo child may use a different provider and model from its Worker parent.
Review children consume no separate GWO capacity slot; their fixed fan-out is
bounded to two standard axes plus at most one strict specialist. The Work
Attempt retains its Worker Active Turn Slot until those children finish or enter a
named external Wait Condition, and native Runtime or provider limits supply
additional backpressure. V6.1 keeps its existing one-shot Reviewer behavior
until V8 cutover.

The Runtime Adapter observes each child Prompt, fixed input digest, provider,
model, session, output, and output digest. Each valid axis observation is
stored independently as soon as it finishes. The deterministic Kernel only
assembles those observations into one Review Evidence envelope and cannot
merge, suppress, or rerank findings. Documented-standard hard violations and
hard Spec omissions, incorrect behavior, or scope creep block the Candidate;
smells and judgment calls remain advisory unless repository policy promotes
them.

The binding reuses `code-review` guidance but requires each child to emit the
typed axis observation itself. The Kernel never converts free-form prose into
Evidence authority. Human-readable Standards and Spec reports are projections
of the stored observations.

For one unchanged Candidate, a valid axis is retained while only a missing or
invalid axis is recovered. Transient pre-ID, TLS, limit, or transport failure
gets the initial execution plus at most two retries and consumes neither an
Attempt nor a Repair Round. Invalid output gets one fresh Sol Max recovery for
that axis. Deterministic configuration rejection blocks immediately. Runtime
actions validate fork and model combinations before dispatch. A stable action
key derived from Attempt ID, Candidate SHA, axis, and recovery ordinal makes
retry readback-first and adoptable; no running child record exists until Agent
identity is read back.

A changed Candidate SHA or diff invalidates both review axes. Both axes inspect
the new complete diff, but receive the prior findings and old-to-new delta to
reduce token use. V8.0 does not attempt cross-SHA unaffected-axis proof. A clean
application of the reviewed diff may reuse Review Evidence; rebase, conflict
resolution, or another diff-changing Integration action requires a new Review
Gate.

Local axis records are persisted before Batch publication. ADR-0040 combines
compatible reviewed Candidates and publishes their compact manifests with one
exact Integration Batch SHA without rerunning Review. This keeps independent
review and exact-Candidate authority while removing an entire scheduling,
capacity, materialization, retry, and Result lifecycle.
