---
status: accepted
supersedes: ADR-0019
amends: ADR-0029, ADR-0045, ADR-0050, ADR-0055
---

# Require one semantic planning pass per Plan Revision

PlanControl uses a hybrid boundary. It automates source readback, structural
validation, deterministic compilation, publication, activation, and durable
readback, but it does not compile a selected Ticket set without semantic
inspection.
Every initial or successor Plan Revision receives exactly one Campaign-level
LLM Planning Pass before compilation.

PlanControl first snapshots the complete selected Ticket contracts, canonical
blockers, Campaign source, and repository policy. It rejects mechanically
invalid input before spending an LLM turn. The Coordinator then inspects the
whole snapshot in one pass, not one Ticket at a time, using the Campaign's
fixed Coordinator semantic-control capacity.

The Planning Pass emits one typed, non-authoritative output private to
PlanControl containing
only:

- `admitted_work`;
- `dependency_additions`, with a reason tied to the frozen contracts;
- `exclusive_resources`;
- `capability_requirements`; and
- `decision_requirements`.

It may identify an incomplete or contradictory Ticket, but it cannot silently
drop selected work, rewrite a Ticket's acceptance contract, invent new work,
expand authority, choose a provider, model, CLI, Prompt, or Runtime Profile,
author lifecycle policy, predict implementation files, assign risk or
difficulty, or generate detailed Worker steps. A required contract correction,
scope change, or unresolved semantic choice becomes a named Decision.

PlanControl deterministically validates the private output against the frozen
inputs and repository policy. It keeps accepted semantic additions and their
provenance in a private compilation record, produces the canonical PlanSpec v3
bytes, and rejects any unsupported field or authority expansion. The LLM never
publishes or activates a Plan Revision.

After activation, dependency admission, Worker scheduling, waits, Formal
Review, bounded repair, Batch formation, local checks, hosted CI, integration,
and cleanup are deterministic Kernel lifecycle. The Coordinator is invoked
again only for an explicit semantic exception or to produce the one Planning
Pass for a replacement Plan Revision. It does not supervise normal progress.

This replaces the earlier zero-Coordinator initial path with a bounded cost:
one semantic planning turn for the complete Campaign revision, independent of
Ticket count. Compilation retry and publication/readback recovery reuse the
same accepted private output and do not trigger another Planning Pass.
