---
status: amended by ADR-0052, ADR-0059, and ADR-0061
amends: ADR-0026, ADR-0031, ADR-0037, ADR-0042
---

# Make Runtime fallback explicit and pre-identity

Every exact Runtime selector mapping resolves one required primary Runtime
Profile and at most one optional availability fallback. If the primary is
unavailable and no fallback is configured, the action follows the bounded
provider-unavailable Wait and terminal result in the canonical Runtime failure
taxonomy. V8 does not rank models, infer strength, maintain price tables, or
run an LLM router.

Availability fallback is eligible only when RuntimeGateway authoritatively
reports `unavailable` or `capacity_exhausted` before any Agent identity may
exist for the stable action. Once identity may exist, RuntimeGateway must read
back or recover that same action and cannot switch provider, model, CLI,
session, or workspace.

A cached provider-unavailable snapshot is advisory and triggers one live
observation without consuming fallback, replacement, or budget. Once a
fallback is durably selected it remains selected; later primary recovery does
not switch the action back. Provider, configuration, and transport outcomes
otherwise follow the canonical
[`Runtime failure taxonomy`](../design/gwo-v8-lean-architecture.md#runtime-failure-taxonomy).

Fallback and Work Run replacement are different. Fallback realizes the same
selector and consumes no replacement-binding allowance. Only
terminal-binding Evidence can permit the configured `recovery_worker` selector
to create the one replacement Worker binding.

For each stable Runtime action, RuntimeGateway persists the selector,
configuration source, resolved Profile digest, and fallback choice. Retries and
recovery reuse that assignment. These facts remain outside Ticket,
PlanControl-private planning output, and PlanSpec.
