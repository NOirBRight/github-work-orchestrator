---
status: amended by ADR-0052 and ADR-0059
amends: ADR-0026, ADR-0031, ADR-0037, ADR-0042
---

# Make Runtime failover explicit and pre-Materialization

Host-global configuration and repository override map each execution selector
to one required primary Runtime Profile and at most one optional availability
fallback. No configured fallback means wait. V8 does not maintain price
tables, rank models, run an LLM router, or infer that a more expensive profile
should be used.

An availability fallback is eligible only when RuntimeGateway
authoritatively reports `unavailable` or `capacity_exhausted` before any Agent
identity exists for the stable action key. Once an identity may exist,
Materialization must read back or retry that same action. It cannot switch CLI,
provider, model, session, or workspace and risk creating a second live Agent.
If the primary and optional fallback are both unavailable, the action enters a
Wait Condition while work-conserving scheduling continues other eligible
work.

Availability fallback and semantic recovery are different. An explicit
fallback realizes the same logical execution role and does not consume another
Worker Attempt. The separately configured recovery Worker role is selected
only after a semantic terminal condition and consumes the second authorized
Worker Attempt. V8 has no timeout-based escalation, fallback chain, or implicit
reuse of the recovery role for temporary capacity pressure.

The selected Profile and whether fallback was used are durable Runtime facts.
They remain outside Ticket, Plan Intent, and PlanSpec.
