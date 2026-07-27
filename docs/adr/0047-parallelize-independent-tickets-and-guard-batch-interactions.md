---
status: amended by ADR-0048, ADR-0049, and ADR-0060
amends: ADR-0031, ADR-0040, ADR-0041
---

# Parallelize independent Tickets and guard Batch interactions

V8 admits every Ready Ticket whose explicit blockers are satisfied until the
Campaign's Worker Slots or a genuinely Exclusive Resource are exhausted.
Ordinary file and Write Scope overlap never blocks Worker execution. Each
Worker uses an isolated workspace, so optimistic execution cannot overwrite
another Worker's edits.

Candidate readback mechanically audits the actual diff against the Effect
Contract and derives protected surfaces and stable Interaction Keys. These
facts constrain Integration rather than retroactively inventing Ticket
dependencies. Same-file overlap is advisory: it can indicate interaction but
does not prove one, while Candidates in different files may still share an
API, schema, authorization, state-machine, build, or generated-source
interaction.

A deterministic BatchIntegrator partitions locally accepted
Candidates by target and base identity, Assurance Requirement, check
environment, Interaction Keys, and protected-surface policy. Ordinary
same-file Candidates may share a Batch when composition is clean. Strict,
non-decomposable, public-API/protocol, schema/migration, authorization,
state-machine/concurrency, build/workflow, and generated-source ownership
surfaces use one-member Batches unless repository policy supplies a narrower
safe rule.

Every composed Batch SHA runs the repository-equivalent local suite before its
first push, then crosses one pull-request and hosted-CI boundary on that exact
SHA. V8.0 adds no default Batch-level LLM Review. Formal Review remains
Candidate-scoped.

Composition or exact-Batch check failure must preserve unaffected Candidate,
Check, and Review Evidence and isolate affected members. It cannot reject every
member or restart the whole implementation and Review lifecycle merely because
they shared a Batch. A discovered semantic contract conflict invokes
Coordinator/Replan only after deterministic composition, checks, and
attribution cannot resolve it.
