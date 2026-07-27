---
status: amended by ADR-0060
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
independent Tickets. Its three Standard-Assurance accepted Candidates form one
compatible multi-member Integration Batch. Its Strict-Assurance accepted
Candidate forms a separate Singleton Batch and is never co-batched. Each of
those two Batches has its own immutable exact Batch SHA,
repository-equivalent local verification, pull-request, hosted-CI,
Integration-Lease-serialized target-integration, and target-readback boundary.

The Canary proves Worker parallelism and Formal Review Internal Subagents; exact
readback of the frozen Authority Grants, Policy Witness, and PlanSpec
authority-root digest; and the complete bounded repair contract: at most three
distinct Candidate SHAs, one initial binding plus at most one replacement
authorized by terminal-binding Evidence, and a complete Review
Finding ledger in which every prior Finding has a typed disposition. Neither a
repair nor a replacement resets those bounds or that ledger. Restart rebuilds
the Campaign from durable Campaign state, receipts, and timers, and duplicate
or lost callbacks cause no duplicate semantic or external effect. The Canary
also proves the bounded zero-LLM-readback-plus-one-diagnosis stale-binding
path. Failure stops new admissions and preserves durable state; executing V8
work is never silently handed to V6.

Deterministic tests cover failure paths. V8 cutover does not require a model
evaluation, scorecard, metrics service, deliberately failing live Ticket, or
fixed observation window. Durable execution counters remain diagnostic facts,
not release thresholds.
