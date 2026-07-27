---
status: amended by ADR-0048, ADR-0049, and ADR-0060
amends: ADR-0031, ADR-0040, ADR-0041
---

# Parallelize independent Tickets and guard Batch interactions

V8 admits every selected Ticket whose explicit blockers are satisfied until the
Campaign's Worker Slots or a genuinely Exclusive Resource are exhausted.
Ordinary or predicted file and path overlap never blocks Worker execution.
Each Worker uses an isolated workspace, so optimistic execution cannot
overwrite another Worker's edits.

Candidate readback mechanically audits the complete diff against the Ticket
contract, Authority Grants, and Policy Witness, then derives protected
surfaces and stable Interaction Keys. These facts constrain delivery rather
than retroactively inventing Ticket dependencies. Same-file overlap is
advisory: it can indicate interaction but does not prove one, while Candidates
in different files may still share an API, schema, authorization,
state-machine, build, or generated-source interaction.

A deterministic BatchIntegrator partitions locally accepted Candidates within
the same Campaign by target and base identity or Clean Base Advance, Policy
Witness digest, delivery identity, Assurance Requirement, check environment,
Interaction Keys, and protected-surface policy. Ordinary same-file Candidates
may share a Batch when all pairwise compatibility checks pass. Strict
Assurance and repository-policy classifications for non-decomposable,
high-coupling, or protected Interaction Keys require a Singleton Batch.

Every composed Batch SHA runs the repository-equivalent local suite before its
first push, then crosses one pull-request and hosted-CI boundary on that exact
SHA. V8.0 adds no Batch-level LLM Review. Formal Review remains
Candidate-scoped.

Composition or exact-Batch check failure preserves unaffected Candidate,
Check, and Review Evidence and isolates affected members through the single
bounded Singleton Batch Fallback. A discovered semantic contract conflict
requests a named Decision only after deterministic composition, checks, and
that fallback cannot resolve it.
