# GitHub Work Orchestrator (GWO)

Turn selected GitHub tickets into verified repository results through
concurrent multi-agent work and deterministic control.

## What it is

GWO is a concurrent GitHub ticket execution engine. LLM agents perform bounded
semantic work (planning, implementation, review); deterministic modules own
scheduling, persistence, recovery, verification orchestration, and repository
delivery. The framework succeeds when it removes coordination work from LLMs —
agent count and protocol sophistication are not objectives.

## How it works

```text
Ticket (GitHub Issue)
  -> Campaign (one execution over a selected ticket set)
  -> Plan Revision (immutable, digest-addressed plan snapshot)
  -> Work Run (one ticket's bounded execution lifecycle)
  -> Candidate (immutable artifact, exact commit + tree)
  -> Formal Review (independent, evidence-backed observation)
  -> Integration Batch (compatible candidates behind one exact PR/CI boundary)
  -> Result (verified terminal outcome, integrated and read back)
```

Key properties:

- **Concurrent by default**: independent tickets run in parallel, four Worker
  Slots per campaign by default, with exclusive resources and integration
  leases for admission control.
- **Provider-neutral**: semantic roles use independently configured Runtime
  Profiles; no provider, model, or CLI facts leak into the frozen plan.
  Portable across Codex, Claude Code, Paseo, and compatible runtimes.
- **Bounded everything**: every semantic loop and infrastructure retry has a
  bound; waits name the observable event or due time required to continue.
- **Verifiable delivery**: a code-producing result is not complete until its
  exact candidate is integrated and read back, behind one exact pull-request
  and hosted-CI boundary.

## Repository layout

- `CONTEXT.md` — normative ubiquitous language (Ticket, Campaign, Candidate,
  Evidence, Decision, ...). Read this first.
- `docs/design/gwo-v8-lean-architecture.md` — the integrated current V8
  mechanics contract.
- `docs/adr/` — accepted architecture decision records.
- `docs/e2e/` — end-to-end acceptance and closure ledgers.
- `skills/orchestrator/` — installable agent skill package, including the
  `gwo_v8` runtime modules (compiler, kernel, execution kernel, planning
  protocol, candidate gate, integration batch, runtime gateway, human gate).
- `scripts/` — entry points such as `run_v8_canary.py` (live smoke / batch
  acceptance canary) and `sync_orchestrator.py`.
- `tests/` — unit and contract tests.

## Status

V8 is the active architecture line. Implementation and production cutover are
in progress; see `docs/design/gwo-v8-lean-roadmap.md` for sequencing and exit
criteria. Work is tracked in GitHub Issues.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
