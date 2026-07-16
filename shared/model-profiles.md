# Model profiles

Keep role and profile names stable. Change concrete bindings here so a model
replacement is a one-file policy edit rather than a rewrite of every Skill or
Issue.

## Default bindings

| Role/profile | Default binding | Reasoning | Use for |
|---|---|---|---|
| `orchestrator` | `gpt-5.6-terra / high` | `high` | Routine direction, scheduling, integration, and Inline work |
| `architecture` | `gpt-5.6-sol / max` | `max` | Explicit durable architecture escalation only |
| `intake-evidence` | `gpt-5.6-terra / high` | `high` | Difficult diagnosis, protocol evidence, and security-sensitive intake |
| `intake-standard` | `gpt-5.6-luna / medium` | `medium` | Clear intake, deduplication, and routine publication |
| `core` | `ollama-cloud/glm-5.2` | `max` | Cross-cutting implementation under explicit architecture |
| `debug` | `ollama-cloud/glm-5.2` | `max` | Hard-bug implementation and deterministic recovery work |
| `evidence` | `ollama-cloud/glm-5.2` | `max` | Bounded protocol and runtime evidence work |
| `standard` | `ollama-cloud/glm-5.2` | `max` | Reversible feature or bug implementation |
| `mechanical` | `ollama-cloud/glm-5.2` | `max` | Bounded refactors, fixtures, migrations, and test expansion |
| `light` | `ollama-cloud/glm-5.2` | `max` | Documentation, metadata, and routine hygiene implementation |
| `review-standard` | `gpt-5.6-luna / high` | `high` | Standards or Spec review for standard work |
| `review-strict` | `gpt-5.6-terra / high` | `high` | Protocol, security, release, or cross-boundary review |

The visible Orchestrator remains on `orchestrator`; switch to `architecture`
only for an explicit decision that changes durable direction. Inline work is an
Orchestrator lane, so it keeps the Orchestrator's GPT binding and must not be
reported as a GLM Worker.

Every implementation lane with an independent binding—Subagent and Visible
Worker—uses `ollama-cloud/glm-5.2` with explicit `max`. No silent GPT fallback
is allowed. A bounded real Task probe verified the native turn context as
`ollama-cloud/glm-5.2 / max` and completed one exact tool call. An explicit
rejection or unavailable binding stops that lane and returns the evidence to
the maintainer for a deliberate choice.

## Binding evidence

Use one requirement and one status:

| Field | Values | Rule |
|---|---|---|
| `model_binding_requirement` | `best-effort`, `exact-runtime` | Use exact-runtime only when model identity is itself acceptance-critical |
| `model_binding_status` | `request-accepted`, `runtime-verified`, `rejected`, `unknown` | Rejection and unknown fail closed |

`best-effort` may proceed with `request-accepted` when the native host exposes
no effective readback. Record that limitation privately. `exact-runtime`
requires `runtime-verified`. A requested UI value alone is not runtime proof.

## Selection order

1. Apply an explicit repository override from `AGENTS.md`.
2. Apply an explicit maintainer binding for the work item.
3. Select the role/profile from risk and ambiguity; verification class does not
   select a provider.
4. Record lane, profile, concrete model, reasoning applicability, requirement,
   status, and sanitized evidence in the private execution contract.
5. On any explicit rejection or contradiction, stop without fallback.

Security, OAuth, credentials, destructive migrations, shared routing, and
public compatibility still require explicit architecture and strict GPT review
even though the bounded implementation Worker uses GLM-5.2.

## Custom Worker agent

The canonical personal-agent template is
`skills/github-work-orchestrator/assets/worker.toml`. It overrides Codex's
built-in `worker` agent with GLM-5.2. Validate and install it through
`scripts/install_worker_agent.py`; never overwrite a different existing file
without the operator's explicit `--replace` decision.
