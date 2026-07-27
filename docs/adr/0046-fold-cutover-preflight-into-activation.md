---
status: accepted
amends: ADR-0034
---

# Fold cutover Preflight into V8 activation

V8 has no separate mandatory Preflight or long-lived Shadow phase. The one
`activate V8` operation performs a fail-closed, read-only Cutover Guard before
changing writer authority. It verifies that the old writer is quiescent, no
conflicting Integration Lease exists, durable state is compatible, and
required Runtime selectors and repository configuration resolve. If any
assertion fails, activation changes nothing and returns named blockers.

Activation publishes and reads back the new writer generation atomically with
its existing durable fencing protocol. V6 and V8 are never simultaneous
writers. Shadow remains an optional diagnostic mode rather than a release
gate.

After activation, this root repository runs one real Campaign with four
independent Work Runs. The Canary proves Worker parallelism, Formal Review
Internal Subagents, Candidate and repair bounds, restart/readback, one
Campaign-scoped Integration Batch, one pull-request and hosted-CI boundary,
and serial target integration. Failure stops new admissions and preserves
durable state; executing V8 work is never silently handed to V6.

Deterministic tests cover failure paths. V8 cutover does not require a model
evaluation, scorecard, metrics service, deliberately failing live Ticket, or
fixed observation window. Durable execution counters remain diagnostic facts,
not release thresholds.
