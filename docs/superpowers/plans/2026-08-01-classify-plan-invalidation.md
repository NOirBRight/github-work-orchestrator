# Classify Plan Revision Invalidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Classify all valid Plan Invalidation Evidence for one active Plan Revision through one bounded, read-only Coordinator action and make the typed result visible through the existing `start -> advance -> inspect` seam.

**Architecture:** PlanControl will build a digest-addressed Campaign replanning snapshot by cross-checking the active PlanSpec, the authoritative Ticket source, ExecutionKernel readback, claims, pending Evidence, Policy Witness, and external blocker references. It will invoke the existing RuntimeGateway planning boundary with a distinct invalidation-classification protocol, validate the closed disposition union, and return a durable typed classification without publishing a successor or changing GitHub. ExecutionKernel will persist/read back that classification, resume only the unchanged/deferred paths after the readback, and retain quiescence for successor or human-Decision paths.

**Tech Stack:** Python 3, dataclasses/enums, canonical JSON digests, SQLite ExecutionKernel state, existing RuntimeGateway Artifact and readback seams, pytest.

## Global Constraints

- `start`, `advance`, and `inspect` remain the only public workflow operations.
- The public status union remains exactly Complete, Running, Decision, Wait, and Blocked.
- The Coordinator receives a complete bounded Campaign snapshot and has read-only repository/Tracker authority with delegation disabled.
- External Tickets may inform ownership/blocking analysis but are never silently admitted.
- The classification output cannot mutate Tickets, blockers, Campaign membership, acceptance, authority, Runtime selection, repository content, or successor Plan Revisions.
- All valid invalidation Evidence for one active revision is coalesced into one stable semantic action; duplicate wake, restart, and readback reuse that action.
- A changed-plan or human-Decision disposition remains quiescent; #135/#136 perform later activation or tracker readback.

### Task 1: Add the closed invalidation-classification protocol and types

**Files:**
- Modify: `skills/orchestrator/scripts/gwo_v8/planning_protocol.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/runtime_gateway.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/plan_control.py`
- Test: `tests/test_v8_plan_invalidation_classification.py`

**Interfaces:**
- Produces `PlanInvalidationDisposition`, `PlanInvalidationDependency`, `PlanInvalidationClassification`, and `CoordinatorCapabilityProof`.
- Produces `replanning_prompt`, `replanning_output_payload_schema`, and protocol recognition used by both Runtime adapters.
- Preserves the existing initial Campaign Planning protocol and all existing subject canonical fields.

- [ ] **Step 1: Write the failing protocol/type tests**

  Add tests asserting that a classification accepts only the five legal disposition shapes, rejects unsupported fields, rejects missing coalesced Evidence, rejects unapproved Ticket keys/dependency edges, and rejects a capability proof that enables delegation or writes.

- [ ] **Step 2: Run the focused tests and verify the expected missing-symbol failures**

  Run `pytest tests/test_v8_plan_invalidation_classification.py -q`.
  Expected: collection or assertion failures because the new protocol symbols do not yet exist.

- [ ] **Step 3: Implement the closed protocol and immutable result types**

  Add a separate `campaign.plan-invalidation-output.v1` payload with exact fields `evidence_digests`, `disposition`, `reason`, `successor`, and `decision`. Use exact disposition values `resume_unchanged`, `defer_non_blocking`, `use_approved_successor`, `require_human_decision`, and `reject_invalid_evidence`. Make `successor` and `decision` mutually exclusive and required only for their corresponding dispositions; make Evidence digests equal the complete pending set.

  Add a `CoordinatorCapabilityProof` with explicit repository-read-only, tracker-read-only, no-plan-activation, no-authority-expansion, and delegation-disabled booleans plus a digest. RuntimeGateway must return the all-safe proof through a non-workflow readback method and fail closed for any other proof.

- [ ] **Step 4: Teach RuntimeGateway and its in-memory/production adapters to recognize the new prompt/output schema**

  Keep `CampaignPlanningSubject` unchanged and distinguish the protocol by its immutable request Artifact and stable `replan:` action identity. The adapters must stage/read the same bounded snapshot and Policy Witness inputs, and the deterministic in-memory adapter must emit one valid `reject_invalid_evidence` payload for a classification action. Existing initial-planning prompts must retain their exact old schema.

- [ ] **Step 5: Run the protocol tests and the existing RuntimeGateway matrix**

  Run `pytest tests/test_v8_plan_invalidation_classification.py tests/test_v8_runtime_gateway.py tests/test_v8_runtime_gateway_repair.py -q`.
  Expected: all protocol and pre-existing RuntimeGateway tests pass.

### Task 2: Build the authoritative bounded Campaign snapshot and one idempotent PlanControl action

**Files:**
- Modify: `skills/orchestrator/scripts/gwo_v8/plan_control.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/plan_control_github.py` only if a durable PlanControl record is required by the existing CAS repository contract
- Test: `tests/test_v8_plan_invalidation_classification.py`

**Interfaces:**
- Consumes `CampaignHandle`, active `PlanSpec` readback, pending invalidation canonical records, and an ExecutionKernel-owned read-only execution snapshot.
- Produces `PlanControl.classify_plan_invalidations(handle, invalidations, execution_snapshot) -> PlanInvalidationClassification | None`.
- The stable action identity is derived only from Campaign, active revision, and the sorted unique pending Evidence identities.

- [ ] **Step 1: Write failing snapshot/coalescing tests**

  Assert the Coordinator input Artifact contains the active Plan Revision, every approved Ticket contract and native blocker, active/terminal Work Runs, claims, accepted Results, pending Evidence, Policy Witness, and external blocker references; assert the external Ticket is not in the approved/admitted set. Invoke classification twice and assert one stable action/progress invocation and one identical result.

- [ ] **Step 2: Run the tests and verify they fail before PlanControl has the classification seam**

  Run `pytest tests/test_v8_plan_invalidation_classification.py -k 'snapshot or coalesc' -q`.
  Expected: missing `classify_plan_invalidations` or missing replanning protocol behavior.

- [ ] **Step 3: Implement bounded snapshot normalization and cross-binding**

  Reuse the existing Ticket/Policy canonical validators with a replanning-only mode that preserves open external blockers as `external_dependencies` instead of admitting them. Cross-check source contracts and source digests against every active PlanSpec work item; cross-check claims and execution state against the active revision; retain only typed Work Run/Result facts.

- [ ] **Step 4: Implement the stable readback-first Coordinator action**

  Persist the exact preflight reservation, read `CoordinatorCapabilityProof`, build the replan prompt Artifact, call RuntimeGateway `progress` once for the stable action, read the output Artifact, normalize the closed disposition, and release only the non-executable Planning reservation after completed output. A running/parked receipt returns `None`; a replay reads the same Runtime action and never starts another semantic action.

- [ ] **Step 5: Add validation tests for rejected Coordinator output**

  Cover invented work, omitted Evidence, acceptance/membership/authority/runtime fields, detailed Worker instructions, unsupported fields, unapproved Ticket keys, and dependency edges absent from the frozen native graph. Each case must fail closed before any Plan Revision or tracker mutation.

- [ ] **Step 6: Run the PlanControl focused tests and existing PlanControl suites**

  Run `pytest tests/test_v8_plan_invalidation_classification.py tests/test_v8_plancontrol_rebuild.py tests/test_v8_plancontrol_production.py -q`.
  Expected: all pass, including existing initial/successor PlanControl behavior.

### Task 3: Integrate classification with ExecutionKernel `advance` and `inspect`

**Files:**
- Modify: `skills/orchestrator/scripts/gwo_v8/execution_kernel.py`
- Test: `tests/test_v8_plan_invalidation_classification.py`
- Test: `tests/test_v8_execution_kernel.py` only if a compatibility assertion needs the existing #133 behavior made explicit

**Interfaces:**
- Consumes the private PlanControl classifier when present; legacy read-only PlanControl doubles continue to expose #133 quiescence when no classifier is composed.
- Produces persisted classification lineage and an inspect-facing typed classification.

- [ ] **Step 1: Write failing `start -> advance -> inspect` tests**

  Cover unchanged resume, defer, successor/approved dependency, named Decision, invalid Evidence, duplicate wake, restart/replay, unaffected Work Run progress, and capability-policy failure. Assert resume/defer read back the classification before changing the affected run to pending and reacquiring a Worker Slot; assert successor/Decision leave the run quiescent and expose exact diagnostic data.

- [ ] **Step 2: Run the tests and verify they fail on the missing Kernel integration**

  Run `pytest tests/test_v8_plan_invalidation_classification.py -k 'advance or inspect or replay or resume or defer or decision' -q`.
  Expected: quiescent runs remain at the old #133 Decision or no classification is exposed.

- [ ] **Step 3: Persist and read back the classification before disposition application**

  Add only backward-compatible default fields to the Kernel state. Deduplicate by the stable action/evidence set, reject a changed same-revision Evidence set after classification, and retain the original invalidation diagnostic lineage.

- [ ] **Step 4: Apply legal dispositions deterministically**

  Resume/defer/reject paths clear the private quiescent fence only after exact state readback, then admit the affected Work Run through the normal Worker Slot/resource checks. Approved-successor and human-Decision paths remain quiescent; unrelated pending/active Work Runs continue through the normal fair scan.

- [ ] **Step 5: Expose typed classification through `inspect` without a transcript**

  Add optional diagnostics fields for action identity, snapshot digest, Evidence identities, disposition, approved successor Tickets/dependencies, and named Decision. Preserve the five-status derivation order.

- [ ] **Step 6: Run focused Kernel tests and the existing execution suite**

  Run `pytest tests/test_v8_plan_invalidation_classification.py tests/test_v8_execution_kernel.py -q`.
  Expected: all pass, including the original #133 receipt and crash-recovery tests.

### Task 4: Export the types, synchronize the packaged skill, and verify the repository

**Files:**
- Modify: `skills/orchestrator/scripts/gwo_v8/__init__.py`
- Modify: `skills/orchestrator/.skill-package.json`
- Test: `tests/test_v8_plan_invalidation_classification.py`

- [ ] **Step 1: Add public read-only type exports**

  Export the classification/disposition/dependency/Decision diagnostic types and capability proof while keeping the workflow operation count unchanged.

- [ ] **Step 2: Run package and manifest validation**

  Run `python scripts/sync_orchestrator.py` followed by `pytest tests/test_orchestrator_package.py tests/test_v8_canary_runner.py -q`.
  Expected: the manifest content digest and package checks pass.

- [ ] **Step 3: Run the full verification commands**

  Run `pytest -q` and `python skills/orchestrator/scripts/quick_validate.py` from the repository root. Inspect `git diff --check`, `git status --short`, and the complete diff before reporting completion.
