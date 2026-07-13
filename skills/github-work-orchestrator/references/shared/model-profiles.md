# Model profiles

Keep role and profile names stable. Change concrete bindings here so a provider
or model replacement is a one-file policy edit rather than a rewrite of every
Skill or Issue.

## Default bindings

| Role/profile | Default binding | Use for |
|---|---|---|
| `orchestrator` | `gpt-5.6-terra / high` | Routine dependency planning, dispatch, arbitration, and integration review |
| `architecture` | `gpt-5.6-sol / max` | Explicit direction or durable architecture escalation only |
| `intake-evidence` | `gpt-5.6-terra / high` | Ambiguous reports, difficult diagnosis, protocol/runtime evidence, and security-sensitive intake |
| `intake-standard` | `gpt-5.6-luna / medium` | Clear reports, deduplication, contract drafting, and routine publication |
| `core` | `gpt-5.6-terra / max` | Cross-cutting architecture, risky core behavior, security, and difficult integration |
| `debug` | `gpt-5.6-terra / max` | Hard bugs, nondeterministic failures, performance regressions, and recovery |
| `evidence` | `gpt-5.6-terra / max` | Protocol research, runtime traces, qualification, and reconciliation evidence |
| `standard` | `gpt-5.6-luna / high` | Isolated feature or bug work with a clear contract |
| `mechanical` | `gpt-5.6-luna / high` | Bounded refactors, fixtures, migrations, and test expansion |
| `light` | `gpt-5.6-luna / medium` | Documentation, metadata, and routine hygiene |
| `review-standard` | `gpt-5.6-luna / high` | Bounded Standards or Spec review for standard work |
| `review-strict` | `gpt-5.6-terra / high` | Protocol, security, release, or cross-boundary review |

Use `orchestrator` for routine campaign control. Switch to `architecture` only
for an explicit gray-area decision that changes durable direction or multiple
downstream work items; return to the routine binding after the decision.
Intake selects Terra for difficult evidence and Luna for clear reports.
`GLM-5.2` is the preferred future Intake/Worker candidate only after
qualification reaches the relevant stage below; until then use the verified
defaults.

Workers use the lowest-cost profile that safely handles the assigned risk and
ambiguity. Worker-internal research and test-analysis subagents use the lowest
qualified model appropriate to that bounded subtask. Formal Standards/Spec
review belongs to the Orchestrator and uses the review profiles above. Final
direction or architecture synthesis uses `architecture` only when explicitly
escalated.

## Selection order

1. Apply an explicit repository override from `AGENTS.md`.
2. Apply an explicit maintainer binding for the work item.
3. Read the assigned verification class, then classify risk and ambiguity with
   the table above. Verification class does not hard-code a provider.
4. Record profile, concrete model, and reasoning level in the private task
   contract and public dispatch comment when repository policy permits it.
5. Escalate only when evidence shows the lower profile cannot safely finish.

Security, OAuth, credentials, destructive migrations, shared routing, and
public compatibility require `core` execution or `review-strict` integration
review even when a lower profile performs mechanical implementation. Do not
encode model names as GitHub labels. Max reasoning is not a routine default;
reserve it for strict core/debug/evidence work or an architecture escalation.

## Route identity and promotion

A profile selects a model and reasoning effort; it does not prove provider
provenance. Determine provenance from the concrete catalog entry, gateway
metadata, and upstream binding rather than a generic provider key.

Promote a new binding with recorded evidence:

```text
candidate
→ shadow
→ evidence-only
→ mechanical-worker
→ standard-worker
→ core-worker
→ orchestrator-eligible
```

At each stage compare instruction following, scope control, test discipline,
tool reliability, hallucinated state changes, callback delivery, and failure
recovery. Retain a verified fallback for every profile. Repository-specific
overrides live in repository policy; organization-wide binding changes live in
this file.
