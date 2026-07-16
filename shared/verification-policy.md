# Risk-tiered verification policy

Product acceptance comes only from the GitHub Issue and accepted decision
records. Repository verification commands come only from the target
repository's documented policy. Execution lane, model, and verification class
are separate choices.

## Execution contract

New or materially rewritten Issues use `Execution-Contract: v2` and include:

```text
Verification-Class: fast | standard | strict
Verification-Commands: <repository commands or explicit not-applicable reason>
Manual-Evidence: none | <one explicit requirement>
Architecture-Decision: resolved | discussion-required
Review-Owner: orchestrator
```

The private execution contract adds:

```text
execution_lane: inline | subagent | visible-worker
model_profile: <stable profile>
model_binding: <concrete model>
model_reasoning_effort: <supported value, none, or omitted>
model_binding_requirement: best-effort | exact-runtime
model_binding_status: request-accepted | runtime-verified | rejected | unknown
model_binding_evidence: <sanitized evidence or limitation>
```

It also carries the pinned repository/base SHA, branch, isolated worktree,
hotset, permission profile, PR target, and callback only when the selected lane
requires one. `best-effort + request-accepted` is allowed when effective
readback is unavailable. `exact-runtime` requires `runtime-verified`. Rejected,
unknown, contradictory, omitted, or silently substituted bindings fail closed.

## Verification classes

| Class | Use when | Candidate verification | Integration review |
|---|---|---|---|
| `fast` | Documentation, copy, isolated UI, metadata, or small deterministic logic with no shared lifecycle or public contract | Targeted checks and diff hygiene; no local full suite | Direct Orchestrator scope/acceptance check |
| `standard` | A reversible feature or bug contained to a clear local boundary | Targeted checks, then one repository-defined relevant full suite | One Orchestrator-owned Standards/Spec review |
| `strict` | Protocol, routing, auth, persistence, release, security, concurrency, or nondeterministic production evidence | Targeted checks, one relevant full suite, and only explicit manual evidence | One Orchestrator-owned Standards/Spec review; later review is delta-only |

Classify the highest applicable risk. File count is a diagnostic, not an
acceptance rule. Upgrade before crossing a public/persisted contract, shared
lifecycle, security boundary, or additional subsystem.

## Execution limits

- Repeat targeted tests as needed while implementing.
- Run the relevant local full suite once at the candidate for `standard` and
  `strict`; do not run unrelated language suites.
- After same-boundary review fixes, run affected checks and rely on replacement
  CI. Repeat a local full suite only after a new verification boundary.
- The implementation lane performs diff hygiene and self-checks but must not run
  the generic formal review. The Orchestrator owns that review.
- Manual/live evidence is required only when the Issue names behavior that
  deterministic checks cannot prove.
- CI remains the final automated gate. Report-only tools remain non-blocking.

## Candidate-first integration pipeline

1. Finish affected local checks before publishing the first candidate. Do not
   push intermediate WIP merely to obtain CI; a recovery checkpoint or truly
   remote-only seam is the exception.
2. Publish one locally green candidate and start one CI run. Notify the
   Orchestrator immediately.
3. Run CI, the one Orchestrator review, and safe candidate manual evidence in
   parallel.
4. Merge only after every applicable gate is green. A same-boundary correction
   preserves full-suite and formal-review counts.
5. Compare candidate and integrated Git trees after merge; when the trees are identical,
   carry forward evidence. Rebuild only for a tree delta, repository
   identity requirement, or release-artifact acceptance.

## Lane-independent guarantees

Inline, Subagent, and Visible Worker lanes all require an exact base, isolated
worktree, one editor, explicit hotset, applicable permissions, scoped
publication, and durable evidence. The lane changes context persistence and
coordination cost; it never weakens safety or verification.

## Time and scope signals

Targets diagnose friction rather than authorize skipped checks:

- Inline same-boundary candidate: about 15 minutes.
- `fast`: candidate PR within 30 minutes.
- `standard`: candidate PR within 90 minutes.
- Same-boundary review/manual correction: locally green replacement within 15 minutes,
  excluding remote CI and human wait.

Visible Worker signals retain:

```text
Verification-Class: <class>
Phase-Timings: plan=<duration>; implementation=<duration>; verification=<duration>; waiting=<duration>
Full-Suite-Runs: <count>
Review-Runs: 0
Scope-Delta: none | <new boundary requiring approval>
```

Inline and Subagent results report the equivalent evidence directly in the
Orchestrator. Success means no duplicate suite, review, CI, or manual gate.
