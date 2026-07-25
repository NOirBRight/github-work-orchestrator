---
name: implement-gwo
description: "Execute one Ready Work Item, an accepted Goal/spec, or an explicit Ready set through the durable GWO V8 workflow."
---

# Implement with GWO V8

Use this entry for durable multi-Agent execution. Matt `/implement` remains a
separate single-ticket workflow and is never called as a fallback.

## Intake

Accept exactly one of:

- one Work Item already in `ready-for-agent`;
- an accepted parent Goal/spec with its Ready Work Items;
- an explicit non-empty set of `ready-for-agent` Work Items.

Route the normalized input through
`orchestrator/scripts/gwo_v8/entry.py::ImplementGwoEntry`. If it is not ready,
stop and return the exact `/triage`, `/to-spec`, or `/to-tickets` next action;
never fall back to `/implement` or run those HITL commands automatically.

## Execute

1. Load the accepted Goal and Ready Work Item contracts from durable GitHub
   truth. The Coordinator may propose Plan Intent; it cannot make it executable.
2. Compile through `PlanCompiler`. Preserve its canonical PlanSpec bytes and
   digest unchanged.
3. Activate through `LocalPlanPublication` backed by
   `GitHubDurablePlanControl`: durable publish, durable readback, then Store CAS.
   Any pending, ambiguous, or writer-fenced activation blocks Admission.
4. Resolve Runtime Policy and pass the selected Runtime Profile to `Kernel`.
   Paseo Materialization is Admission-idempotent. Prompt acceptance, Agent,
   session, Workspace, parent, runtime settings, and GWO identity must read back
   before an Attempt begins.
5. Drive one `Kernel.reconcile_once` per host wake. Follow its typed directive:
   continue the Coordinator, wait on the named condition without an LLM turn,
   request a decision, run another mechanical pass, or finish.
6. Keep `batch_ready`, Review/CI wait, superseded, and failed Runtime Bindings
   parked or interrupted. Only after exact target-branch Integration readback
   may the Kernel issue a bound retirement authorization. Reconcile
   `Agent archive -> paseo worktree archive <name> --json -> exact temporary
   branch CAS/prune -> Agent/path/Git-worktree readback`; retry typed
   pending/error retirement idempotently.
   Resolve the native archive name inside the Runtime Adapter from exact Agent
   and Paseo worktree-list canonical-path readback; never treat a `wks_*`
   Workspace ID as that name. Apply ADR-0041 to Reviewer retirement as well:
   remove an independent disposable Reviewer worktree only after accepted
   Review Evidence, while a child sharing the Candidate workspace archives
   identity only.
7. Continue until every in-scope Work Item is verified and every integrated
   Batch member has complete Runtime retirement readback, or the Task Group
   Goal is explicitly
   blocked. A Coordinator turn ending is not completion.

The installed optional Skill named by a work Plan Node contributes Prompt
guidance only. Resolve it when the Admission freezes its Prompt snapshot.
Effect Contract and output/Evidence contracts remain authoritative.

## Boundaries

- `implement`, `implement-gwo`, and `orchestrator` are invalid Plan Node Skill
  References.
- Do not create a second Agent for an ambiguous Admission.
- Creation or Prompt-delivery errors before accepted readback do not consume an
  Attempt.
- Retry one unchanged Materialization action at most three executions, with
  readback before retry.
- Do not poll Agents, treat token use as progress, or use elapsed time to fail,
  cancel, or replace work.
- Never place absolute repository or worktree paths in durable retirement
  Evidence.
- Prefer a valid manually created Coordinator. Auto-create only from the
  configured `coordinator_auto` Role Profile when none is usable.
