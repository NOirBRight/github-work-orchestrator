# Model profiles

Bind work classes to stable profiles. Concrete models are defaults and may be
changed centrally without relabeling Issues.

## Default bindings

| Profile | Default binding | Use for |
|---|---|---|
| `orchestrator` | `gpt-5.6-sol / xhigh` | Dependency planning, dispatch, cross-task arbitration, final integration decisions |
| `core` | `gpt-5.6-sol / high` | Cross-cutting architecture, security, risky core behavior, difficult integration |
| `evidence` | `gpt-5.6-terra / xhigh` | Protocol research, runtime traces, hard diagnosis, reconciliation |
| `standard` | `gpt-5.6-terra / high` | Isolated feature or bug implementation with clear acceptance criteria |
| `mechanical` | `gpt-5.6-luna / high` | Bounded refactors, test expansion, fixture generation, migrations |
| `light` | `gpt-5.6-luna / medium` | Documentation, routine cleanup, simple metadata or release hygiene |

The Orchestrator remains `gpt-5.6-sol / xhigh` unless the maintainer explicitly
changes the policy. Select the lowest-cost profile that safely handles the work.
Escalate when evidence shows the selected profile cannot complete the task.

## Selection order

1. Apply an explicit repository override from `AGENTS.md`.
2. Apply an explicit maintainer instruction for the work item.
3. Classify by risk and ambiguity using the table above.
4. Record the exact profile, model, and reasoning level in the dispatch comment.
5. Do not encode concrete model names as GitHub labels.

Security, OAuth, credentials, destructive migrations, and shared routing
changes require `core` review even when a lower profile performs mechanical
implementation.

## Third-party promotion

Promote a new binding only with recorded evidence:

```text
candidate
→ shadow
→ evidence-only
→ mechanical-worker
→ standard-worker
→ core-worker
→ orchestrator-eligible
```

At each stage compare instruction following, patch scope, test discipline,
tool reliability, hallucinated state changes, and recovery from failures.
Retain a verified fallback for every profile.

Project-specific overrides belong in repository instructions. Organization-wide
binding changes belong in this Skill's released policy.
