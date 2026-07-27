---
status: amended by ADR-0052, ADR-0059, and ADR-0061
amends: ADR-0026, ADR-0031, ADR-0037, ADR-0042
---

# Make Runtime fallback explicit and pre-identity

Every exact Runtime selector mapping resolves one required primary Runtime
Profile and at most one optional availability fallback. If the primary is
unavailable and no fallback is configured, the action enters a named
availability Wait. V8 does not rank models, infer strength, maintain price
tables, or run an LLM router.

Availability fallback is eligible only when RuntimeGateway authoritatively
reports `unavailable` or `capacity_exhausted` before any Agent identity may
exist for the stable action. Once identity may exist, RuntimeGateway must read
back or recover that same action and cannot switch provider, model, CLI,
session, or workspace.

Fallback and Work Run replacement are different. Fallback realizes the same
selector and consumes no replacement-binding allowance. Only
terminal-binding Evidence can permit the configured `recovery_worker` selector
to create the one replacement Worker binding.

For each stable Runtime action, RuntimeGateway persists the selector,
configuration source, resolved Profile digest, and fallback choice. Retries and
recovery reuse that assignment. These facts remain outside Ticket,
PlanControl-private planning output, and PlanSpec.
