---
status: accepted
---

# Adopt workspace-scoped rolling orchestration

V4.x showed that Campaign Agents, rooms, acknowledgements, heartbeats, standing
Reviewers, and staged cleanup could consume an hour before implementation
started. V5 flattened that hierarchy but still coupled coordination to one
permanent Agent, duplicated native GitHub states, bundled Issues at runtime, and
made optional management concerns part of the core harness.

We will make Orchestrator a portable repository Skill rather than an Agent.
Any qualifying root Agent in the stable integration-branch Workspace may use
its current runtime to coordinate. A short OS advisory mutex serializes only an
individual state-changing command; it creates no durable owner, transfer, TTL,
or recovery protocol. GitHub Issues, pull requests, checks, branches, and three
`orch:*` labels remain the durable business truth. Work is one Issue per
disposable Worker and PR, admitted through rolling, Hotset-aware Wave
Generations with repository WIP three by default and five at most.

## Considered options

- A permanent provider-bound Coordinator was rejected because identity and
  runtime choice are independent.
- A long-lived Lease was rejected because holder transfer and failure recovery
  recreated the control plane this design removes.
- Campaign Agents and automatic Work Packages were rejected because planning
  groups do not need runtime identity, workspaces, supervision, or cleanup.
- Mandatory Projects, rooms, receipts, and periodic polling were rejected
  because GitHub facts plus reconciliation are sufficient and cheaper.

## Consequences

- Paseo parentage controls finish notification and direct-child cleanup only;
  it does not grant repository coordination authority.
- The Coordinator Workspace stays on the integration branch, never becomes a
  feature PR head, and the Coordinator never authors tracked changes.
- Difficulty, urgency, and review risk are independent; concrete runtime choices
  remain replaceable local mappings.
- Project and Milestone absence or drift cannot stop dispatch or integration.
- Normal communication is one Issue design, one PR, one best-effort wake, and
  an optional PR review.

The locked behavior is maintained in `docs/orchestrator-v6-living-design.md`.
Release gates passed on 2026-07-20: TDD and adapter tests, private rolling-wave
E2E, Standards/Spec review, three-surface install drift checks, and a fresh
installed-Agent smoke all agreed. ADR 0007 is superseded.
