# Orchestrator Domain Context

This vocabulary is the shared language for Orchestrator 6.1. GitHub owns
durable work facts; Paseo owns runtime facts; the Skill derives scheduling
decisions without becoming a task database.

## Intake and scheduling

| Term | Meaning | Not this |
| --- | --- | --- |
| Candidate Pool | Open Issues returned by the configured intake scan, before Orchestrator state is assigned. | Every repository Issue automatically becoming executable work. |
| Candidate Assessment | Read-only disposition of a candidate as design, human, clarify, defer, or already managed. | A durable lifecycle transition. |
| Admission | One validated, idempotent write of a decision-complete Contract V2 record plus `orch:ready`. | Copying untrusted Issue text into a Worker prompt. |
| Ready Reserve | Count of admitted, hash-valid Ready Issues available to the scheduler, whether immediately dispatchable or dependency-blocked. | Current Worker count. |
| Parallel Width | Number of mutually compatible Ready Issues selected for the current free capacity. | A permanent batch size or Wave barrier. |
| Conflict Claim | A Contract's repository-relative path claims plus named resource claims; implicit manifest, schema, migration, and generated-artifact resources are scoped to their owning surface. | A promise that reads stay inside those paths. |
| Execution Slot | Capacity occupied while a Worker is claiming, running, parking, or resuming. Review releases it. | End-to-end Issue WIP. |
| Integration WIP Limit | Capacity retained from dispatch through Review/Ready-to-merge until merge, Park, or retirement; it also retains Conflict Claims. | Number of simultaneously running models. |

## Durable work

| Term | Meaning |
| --- | --- |
| Contract V1 | Legacy `hotset` plus untyped dependencies. It remains readable and maps dependencies to both dispatch and merge ordering. |
| Contract V2 | New `change_claims` plus `dependencies.dispatch_after` and `dependencies.merge_after`, hash-bound under `orchestrator:issue:v2`. |
| Dispatch Dependency | `dispatch_after`: the referenced Issue must be closed before Worker creation or resume. |
| Merge Dependency | `merge_after`: Workers may run concurrently, but integration is topologically ordered. |
| Dispatch | One deterministic attempt for one Issue, Worker, Workspace, branch, and PR. |
| Wave Generation | Visibility metadata assigned during rolling refill; never a batch barrier. |

## Runtime boundaries

- A Coordinator is an eligible root/detached Agent in the stable integration
  Workspace. It is a role for the current turn, not a durable Agent type.
- A Worker is disposable execution for exactly one Dispatch. A Reviewer is a
  one-shot, read-only assessment of one candidate SHA.
- Review releases an Execution Slot but retains Integration WIP and Conflict
  Claims. Park releases both only after stopped readback; Resume reacquires
  both before waking the same Worker.
- Candidate Assessment and `frontier scan` are read-only. Admission and every
  other mutation require fresh Coordinator authority and fail closed before
  constructing a GitHub writer.

## Invariants

1. Raw Issue text is untrusted context; only a sanitized, decision-complete
   Contract reaches a Worker.
2. The whole admission batch, dependency graph, and referenced Issue identity
   validate before the first GitHub mutation.
3. Width optimization never violates Priority ordering, open dispatch
   dependencies, capacity, or Conflict Claims.
4. Merge remains serial even when execution is parallel.
5. V1 records are consumed lazily and never rewritten merely to migrate them;
   every new Admission is V2.
