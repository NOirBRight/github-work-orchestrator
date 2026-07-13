# Risk-tiered verification policy

Product acceptance comes only from the GitHub Issue and explicitly accepted
decision records. Repository verification commands come only from the target
repository's documented verification policy. Skills select and execute those
requirements; they do not invent additional product acceptance.

## Execution contract

New or materially rewritten Issues use `Execution-Contract: v2` and include:

```text
Verification-Class: fast | standard | strict
Verification-Commands: <repository commands or explicit not-applicable reason>
Manual-Evidence: none | <one explicit requirement>
Architecture-Decision: resolved | discussion-required
Review-Owner: orchestrator
```

The dispatch contract repeats these fields and adds the pinned repository,
base SHA, branch, hotset, requested model binding, `model_binding_status`,
sanitized `model_binding_evidence`, permission profile, callback, and PR target.
`model_binding_status` must be `verified`; a requested UI/API value alone is
not effective-runtime evidence. An unverified binding or a
`discussion-required` contract is not dispatchable. Existing active work may
migrate incrementally; do not restart or discard useful WIP merely to add v2
metadata.

## Verification classes

| Class | Use when | Worker verification | Integration review |
|---|---|---|---|
| `fast` | Documentation, copy, isolated UI, metadata, or small deterministic logic with no shared lifecycle or public contract | Targeted checks and diff hygiene; no local full suite | One lightweight Orchestrator scope/acceptance check; no review subagent |
| `standard` | A reversible feature or bug contained to a clear local boundary | Targeted checks during implementation, then one repository-defined relevant full suite for the candidate | One Orchestrator-owned Standards/Spec review |
| `strict` | Protocol, routing, transport, auth, permissions, persistence, migration, release/update, security/privacy, concurrency/cancellation, or nondeterministic production evidence | Targeted checks, one repository-defined relevant full suite, and only the Issue's explicit harness/manual evidence | One Orchestrator-owned Standards/Spec review; later review is delta-only |

Classify the highest applicable risk. Upgrade before broadening scope when a
change crosses a public or persisted contract, shared lifecycle, security
boundary, or additional subsystem. Do not downgrade a mandatory strict trigger
without an accepted maintainer decision recorded in the Issue.

File count is a diagnostic, not an acceptance rule. A purported `fast` task
that introduces a shared abstraction, lifecycle policy, public API, or a second
subsystem must send `DISCUSSION_REQUIRED` before expanding.

## Execution limits

- Repeat targeted tests as needed while implementing.
- For `standard` and `strict`, run the relevant local full suite once at the
  candidate commit. Do not run every language suite unless repository policy
  maps the changed boundary to each one.
- After review fixes, run affected targeted checks and rely on CI. Repeat a
  local full suite only when the fix crosses a new boundary or changes the
  repository-defined full-suite mapping.
- The Worker performs diff hygiene and acceptance self-checks. It must not run
  the generic `code-review` Skill or create Standards/Spec review subagents.
- The Orchestrator owns the only formal review. Review `fast` work directly;
  use one parallel Standards/Spec pass for `standard` and `strict`. Re-review
  only the changed delta.
- Manual or live evidence is required only when the Issue names behavior that
  deterministic checks cannot prove. A clean run does not create a new gate,
  and a retry requires a new hypothesis or materially changed environment.
- CI remains the final automated gate. Report-only tools remain non-blocking.

## Model selection

Verification class and model profile are independent. Use the lowest qualified
binding that can safely execute the work. A qualified third-party model may
replace Luna or Terra centrally; retain an official fallback. Reserve Sol Max
for an explicit architecture/direction escalation, not routine execution,
research, or review.

## Time and scope signals

Targets are diagnostic rather than hard timeouts:

- `fast`: candidate PR within 30 minutes;
- `standard`: candidate PR within 90 minutes;
- `strict`: milestone checkpoints with a named hypothesis and next gate.

When a target is exceeded, report the measured phase and concrete blocker; do
not add speculative verification. Worker material signals include:

```text
Verification-Class: <class>
Phase-Timings: plan=<duration>; implementation=<duration>; verification=<duration>; waiting=<duration>
Full-Suite-Runs: <count>
Review-Runs: 0
Scope-Delta: none | <new boundary requiring approval>
```

The Orchestrator records its formal review count separately. Success means no
duplicate full-suite run, no duplicate formal review, no acceptance drift, and
no regression hidden by the faster path.
