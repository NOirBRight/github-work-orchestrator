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
its existing durable fencing protocol and Activation Receipt. That durable
commit is the only authority-transfer point. Before Guard success, all
V3-composition and V2-projection compatibility adapters, their callers, and
their write paths are absent or unreachable. V8 never projects V2 into V3,
resumes a V2 execution, interprets V2 state, or writes V2. Active V2 execution
must finish through its original decoder or be proven quiescent/read-only. A
failed Guard leaves the V6.1 writer generation unchanged. V6 and V8 are never
simultaneous writers. Shadow remains an optional diagnostic mode rather than a
release gate. The canonical rules are
[`Cutover`](../design/gwo-v8-lean-architecture.md#cutover).

After activation, this root repository runs one real Campaign with four
independent Work Runs. The Canary proves Worker parallelism, Formal Review
Internal Subagents, Candidate and repair bounds, restart/readback, one
Campaign-scoped Integration Batch, one pull-request and hosted-CI boundary,
serial target integration, recovery from a lost callback, and the bounded
zero-LLM-readback-plus-one-diagnosis stale-binding path. Failure stops new
admissions and preserves durable state; executing V8 work is never silently
handed to V6.

Deterministic tests cover failure paths. V8 cutover does not require a model
evaluation, scorecard, metrics service, deliberately failing live Ticket, or
fixed observation window. Durable execution counters remain diagnostic facts,
not release thresholds.
