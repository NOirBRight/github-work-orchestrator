# GWO V7 roadmap

Phased migration from V6.1.0 to the architecture in
`gwo-v7-architecture.md`. Each phase lands independently behind the existing
test suite; V6 mechanisms stay operative until the phase that replaces them
lands. Active V6 Campaigns always finish under the V6 lifecycle (the c-016
rule generalized).

Phases 1–2 are strictly additive: they build the V7 kernel and guards
alongside V6, whose packaged scripts, skill documents, and tests stay
unchanged and fully operative. Phase 3 is the only cutover, and it cuts over
per repository and per Campaign, never mid-flight. Phase 4 is internal to V7.

## Phase 1 — gwo CLI kernel

Scope: `gwo.py` with the store (SQLite, `GWO_HOME`), `coordinator`,
`task`, `dispatch` (store side), `send`, `ask`, `inbox`, `done`,
`agent status`, `config check`, `doctor rebuild`; write-time identity from
`GWO_AGENT_ID`; ack-on-read delivery with Signal-ID idempotency; the
8-event model with CLI-enforced role entitlement.

Functionally supersedes `paseo_room.py` (2013 lines),
`material_delivery.py` (479), `repository_room.py` (457), and the ADR
0005/0006 mechanisms — but those V6 scripts stay packaged and referenced by
the V6 skill documents until Phase 3 removes the references; Phase 1 deletes
nothing.

Exit criteria: kernel test suite green (ported invariants from
`test_material_delivery*.py`, `test_repository_room.py`,
`test_orchestration_scripts.py`); a scripted two-Agent conversation through
one store demonstrates send/ack/done with an impersonation attempt rejected
at write time.

## Phase 2 — DAG planner and guards

Scope: DAG plan schema v1; `gwo guard check-dag`; `gwo lease`; build the
guards as new modules that reuse the wave/Hotset/capacity logic of
`campaign_scheduler.py`, `hotset_policy.py`, `execution_policy.py`, and
`ready_frontier.py`; risk-tiered review nodes and CLI-issued `review_rounds`
covering what `review_policy.py` locks guarantee. The V6 planner entry
points keep working unchanged until Phase 3.

Exit criteria: guard tests cover acyclicity, Hotset disjointness,
capacity, review-tier completeness, serial integration chain; property test:
every wave V6 `plan-wave` accepts is accepted as a DAG ready frontier.

## Phase 3 — skill flattening

Scope: rewrite the three SKILL.md packages against the kernel; retire the
Campaign tier (Campaign Control Workspace, per-Campaign rooms, Operator
Relay, entry_policy promotion ceremony); Task Groups as labels; update
`CONTEXT.md` terminology; refresh `shared/` protocol docs.

Target: orchestrator SKILL.md plus its references under one third of the V6
volume. `shared/github-state-rules.md` and the v3 Issue contract are
explicitly out of scope (unchanged).

Exit criteria: `quick_validate.py` and `test_skill_packages.py` green; a
full fast-tier issue lifecycle (dispatch → done → verify → integrate →
cleanup) executed end-to-end on a sandbox repository via the Paseo adapter.

## Phase 4 — Runtime Port extraction

Scope: isolate all Paseo calls into `adapters/paseo.py` behind the
five-operation port; adapter auto-detection (`PASEO_AGENT_ID` → paseo,
otherwise configured role→command templates); permission-profile
compilation at spawn. The headless adapter (Claude Code / Codex
session-process model) stays specification-only until a real need exists;
its spec in `gwo-v7-architecture.md` is the port's design validation.

Exit criteria: kernel and guards import no Paseo-specific code; adapter
contract tests run against a fake adapter implementing both execution
models.

## Compatibility notes

- V6 and V7 never share state: V6 rooms live in Paseo chats, the V7 mailbox
  in the gwo store. A repository migrates when its last V6 Campaign closes.
- `~/.paseo/orchestration-preferences.json` is read once by a migration
  helper and written to `GWO_HOME/config.json`; thereafter only the latter
  is authoritative.
- `docs/evidence/*` upstream reports remain retained; the zombie-subagent
  and draft-tab issues are resident-runtime concerns that the session-process
  model sidesteps but the Paseo adapter still documents.
