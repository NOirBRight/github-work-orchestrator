# ADR 0010: Adopt Parallel Frontier Admission

## Status

Accepted for Orchestrator 6.1.0 on 2026-07-20.

## Context

V6.0.1 could safely execute already-managed Ready Issues, but its GitHub
snapshot intentionally saw only the three orchestration labels. It therefore
could not preflight the ordinary backlog, maintain a Ready Reserve, or explain
why free Worker capacity stayed unused. Its greedy first-fit scheduler could
choose one broad Hotset before two narrower compatible Issues. Untyped
dependencies blocked both execution and merge even when only integration order
was required, and one WIP limit coupled live model capacity to the slower PR
review/merge queue.

These behaviors made projects appear single-threaded even with three configured
Workers. Increasing the slot number alone would only increase contention and
review backlog.

## Decision

Add a separate Frontier interface. `frontier scan` reads the configured
Candidate Pool and current scheduler state, assigns non-mutating Candidate
Assessments, and reports Ready Reserve, reserve gap, free capacities, Parallel
Width, and starvation. `frontier admit` accepts an explicit batch of sanitized
contracts. It requires the same Coordinator entry guard as every write command,
validates the entire batch and its cross-frontier dependency DAG before any
GitHub mutation, then idempotently creates the managed record and Ready label.

New admissions use Contract V2. `change_claims.paths` bounds delivery writes;
`change_claims.resources` captures non-path conflicts. Manifest/lock,
schema/migration, and generated input/output conflicts are inferred within
their owning directory or artifact family instead of globally.
`dependencies.dispatch_after` blocks Worker creation; `merge_after` permits
parallel execution and topologically orders serial integration. Existing V1
records remain readable and project their Hotset and dependency list into both
V2 semantics without an eager repository rewrite.

Replace first-fit scheduling with a bounded, width-aware compatible-subset
search. Priority remains lexicographically dominant; within the same priority
surface, the scheduler favors the combination that fills more free capacity,
then dependents unlocked and stable Issue order. The 100-candidate frontier is
bounded by an optimistic score and a deterministic search budget; if proof of
a wider combination exhausts that budget, the best safe wave is returned with
`WAVE_SEARCH_BOUNDED`. Execution capacity remains at most five.

Split capacity into `execution_slots` and `integration_wip_limit`. Claiming,
running, parking, and resuming occupy execution. Review and Ready-to-merge
release execution but retain integration WIP and Conflict Claims until merge,
Park, or retirement. Defaults are three execution slots, six integration WIP,
and a six-Issue Ready Reserve.

## Consequences

- Issue pre-screening and standardization become an explicit, auditable stage;
  raw reporter text never becomes an executable contract automatically.
- Independent implementation can refill while earlier PRs wait for review or
  serial merge, without allowing overlapping writes.
- Repositories need intake labels or an explicit intake policy to populate the
  Candidate Pool; ambiguity remains visible rather than guessed.
- A larger integration limit can create a review queue, so starvation and both
  capacities are reported separately.
- New code and documentation use the domain language in `docs/CONTEXT.md`.

## Alternatives rejected

- Raising the old Worker slot count: keeps backlog blindness, greedy selection,
  and execution/review coupling.
- Automatically converting every open Issue to Ready: makes untrusted,
  undecided reports executable and destroys product triage.
- Eagerly rewriting all V1 records: creates noisy GitHub mutations and risks
  changing active Dispatch hashes.
- Parallel merge: conflicts with protected-branch evidence and deterministic
  integration ordering; only implementation is parallelized.
