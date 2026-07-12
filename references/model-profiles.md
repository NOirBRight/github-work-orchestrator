# Model profiles

Bind work classes to stable profiles. Concrete models are defaults and may be
changed centrally without relabeling Issues.

## Default bindings

| Profile | Default binding | Use for |
|---|---|---|
| `orchestrator` | `gpt-5.6-sol / max` | Dependency planning, dispatch, cross-task arbitration, and the most important integration decisions |
| `core` | `gpt-5.6-terra / max` | Cross-cutting architecture, security, risky core behavior, difficult integration |
| `evidence` | `gpt-5.6-terra / max` | Protocol research, runtime traces, hard diagnosis, reconciliation |
| `standard` | `gpt-5.6-luna / max` | Isolated feature or bug implementation with clear acceptance criteria |
| `mechanical` | `gpt-5.6-luna / xhigh` | Bounded refactors, test expansion, fixture generation, migrations |
| `light` | `gpt-5.6-luna / xhigh` | Documentation, routine cleanup, simple metadata or release hygiene |

The Orchestrator remains `gpt-5.6-sol / max` unless the maintainer explicitly
changes the policy. These defaults intentionally concentrate dispatch on four
effective tiers: Luna XHigh, Luna Max, Terra Max, and Sol Max. Do not select Sol
below Max, Terra below Max, or Luna below XHigh by default. Select the
lowest-cost profile that safely handles the work and escalate only when evidence
shows that it cannot complete the task.

Reserve Sol Max for orchestration, maintainer-designated critical work, or an
explicit escalation after Terra Max fails. Prefer Luna Max for ordinary coding
and Luna XHigh for bounded or mechanical work. Prefer Terra Max when ambiguity,
cross-cutting risk, or difficult evidence synthesis materially raises the cost
of a wrong answer.

This policy is informed by the corrected Artificial Analysis Coding Agent Index
comparison summarized on 2026-07-12, not treated as an immutable benchmark:
https://x.com/aistarjp/status/2076010978236604839. Re-evaluate the bindings when
model behavior, pricing, provider stability, or repository evidence changes.

## Selection order

1. Apply an explicit repository override from `AGENTS.md`.
2. Apply an explicit maintainer instruction for the work item.
3. Classify by risk and ambiguity using the table above.
4. Record the exact profile, model, and reasoning level in the dispatch comment.
5. Do not encode concrete model names as GitHub labels.

Security, OAuth, credentials, destructive migrations, and shared routing
changes require `core` review even when a lower profile performs mechanical
implementation.

## Route identity

A profile binding selects a model and reasoning effort; it does not classify an
upstream route as official or third-party. Provider keys such as `custom` may
intentionally contain both official and third-party nodes so a conversation can
keep one stable provider identity. Determine route provenance from the concrete
catalog entry, Gateway routing metadata, and upstream binding. Never infer it
from the provider key alone.

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
