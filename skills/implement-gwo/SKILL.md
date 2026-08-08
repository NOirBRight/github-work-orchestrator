---
name: implement-gwo
description: "Run the GWO V8 beta2 isolated preview for a ready Work Item set."
---

# Implement with GWO V8

This entry is guidance for the beta2 isolated preview. It describes the V8
public host and does not activate repository writing.

## Public path

Accept only a Work Item already in `ready-for-agent`, an accepted parent
Goal/spec with ready Work Items, or an explicit non-empty ready set. If the
input is not ready, return the exact next action and never fall back to
`/implement`.

The public path is composed from these deep modules:

- `PlanControl` freezes the Campaign start request and ready references.
- `ExecutionKernel` owns the bounded Campaign lifecycle.
- `RuntimeGateway` owns Runtime admission and recovery boundaries.
- `CandidateGate` owns Candidate and Review evidence gates.
- `BatchIntegrator` owns verified delivery observations.

The host exposes exactly these operations:

```text
start(repository, ready_refs, options?)
advance(campaign_handle, wake_ref?)
inspect(campaign_handle)
```

Start with ready Work Item references. Continue only from an observable wake,
and use `inspect` for read-only diagnostics. Treat the returned Campaign
status and Evidence as authoritative; a provider turn ending is not
completion.

## Isolated preview admission

Install `ProductionGwoHost` only with a temporary target contained by the
configured isolation root:

```text
preview_mode="beta2_isolated_preview"
writer_activation_enabled=False
```

The resolved target must be a descendant of `target_isolation_root` and must
not be the canonical repository. A normal repository is rejected with
`V8_ISOLATED_PREVIEW_REQUIRED`. The preview keeps the writer disabled and
does not change the target repository or the writer generation.

Resolve the five module boundaries above before a Campaign starts. Preserve
their exact identities, readbacks, and Evidence across wakes and restarts.

Retry one unchanged Materialization action at most three executions, with
readback before retry. Do not poll Agents, treat token use as progress, or
use elapsed time to fail, cancel, or replace work. At a host wake, wait on the named condition without an LLM turn.
Prefer a valid manually created Coordinator;
auto-create only from the configured `coordinator_auto` Role Profile.
