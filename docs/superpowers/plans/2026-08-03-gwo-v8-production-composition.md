# GWO V8 Production Composition and Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compose the merged V8 deep modules behind one production start -> advance -> inspect host, harden ExecutionKernel against stale writes and false Results, make restart/crash recovery converge through RuntimeGateway, CandidateGate, BatchIntegrator, and CampaignWatchdog, and revalidate the late Candidate/Repair scope-escape path before Issue #118.

**Architecture:** Keep PlanControl, ExecutionKernel, RuntimeGateway, CandidateGate, and BatchIntegrator as the five deep modules. Add only host-private composition adapters: ProductionWorkRunEffects translates the Kernel stable actions into the existing module seams, and ProductionGwoHost owns assembly and the three public operations. The Kernel remains the only persisted Campaign state machine; CampaignWatchdog remains a wake adapter; CandidateGate remains the only Candidate/Review entry; BatchIntegrator remains the only delivery boundary. The writer generation is observed but never transferred by this plan.

**Tech Stack:** Python 3.13, pytest, frozen dataclasses, canonical JSON, SHA-256, SQLite compare-and-swap, RuntimeGateway/Paseo adapters, CandidateGate, BatchIntegrator, CampaignWatchdog, temporary Git repositories, GitHub CLI readback, and Skill package manifests.

## Global Constraints

- Execute each task in a fresh clean isolated worktree created from the exact prerequisite `origin/main` SHA; never implement in the dirty primary checkout at `D:\Workstation\github-work-orchestrator`.
- The implementation starts only from a clean checkout after Issues #113, #114, #115, #116, and #117 have merged; use the final merged CandidateGate and BatchIntegrator contracts rather than wrapping their predecessor paths.
- The normative order is CONTEXT.md, accepted ADRs, docs/design/gwo-v8-lean-architecture.md, docs/design/gwo-v8-lean-stabilization-spec.md, and docs/design/gwo-v8-lean-roadmap.md.
- The public workflow remains exactly start(repository, ready_refs, options?) -> CampaignHandle, advance(campaign_handle, wake_ref?) -> CampaignOutcome, and inspect(campaign_handle) -> Diagnostics.
- Public statuses remain exactly Complete, Running, Decision, Wait, and Blocked; candidate_checks, formal_review, repair, accepted_awaiting_delivery, and quiescent remain private Work Run phases.
- The five deep modules remain PlanControl, ExecutionKernel, RuntimeGateway, CandidateGate, and BatchIntegrator. CampaignWatchdog owns only event/timer wake adaptation.
- A Worker report, Runtime notification, raw log, workspace head, provider completion statement, or pull-request label is a wake hint, not authority. Every transition uses exact durable readback.
- A Candidate is neither Evidence nor a Result. A code Result requires an accepted-Candidate receipt, exact Batch delivery, exact hosted/local verification identity, and target-branch readback.
- PlanSpec remains provider-, model-, CLI-, selector-, fallback-, and lifecycle-neutral. Runtime Profiles and prompt-artifact selection stay in host configuration.
- RuntimeGateway is the only provider/runtime caller. Production composition never calls a provider, CLI, Agent, session, workspace, or Runtime adapter directly.
- CandidateGate is the only Candidate/Review/Repair entry. Production composition never constructs a Review Subject, reuses Review Evidence, or turns a repair escape into ordinary repair.
- BatchIntegrator is the only Git/GitHub/hosted-CI/Integration-Lease/target-delivery caller. Production composition never calls GitIntegrationBatchAssembler or Kernel from kernel.py.
- No SQLite transaction remains open while RuntimeGateway, CandidateGate, BatchIntegrator, Git, GitHub, CI, or a watcher is called.
- SQLite Campaign state uses compare-and-swap with a monotonic row version and canonical state digest. A stale writer fails closed before any second external effect.
- SQLite `state_version` is only the CAS row version; it is not #113's per-run `trusted_progress_revision`. `trusted_progress_revision`, `last_trusted_progress_at`, and `stale_due_at` change only after trusted progress readback. A raw wake or any other CAS save may increment `state_version` and update `last_wake_ref`, but it must never reset or advance staleness.
- inspect is read-only: it does not initialize Campaign state, migrate a row, save a projection, wake a Runtime, call CandidateGate, call BatchIntegrator, call a provider, or alter a Watchdog queue.
- Async planning continuation re-enters the same persisted planning action through ProductionPlanControlStartHost.continue_start; it does not create a second Planning Pass and does not poll without a wake.
- The default host configuration is Beta2 isolated preview. It observes the current writer generation and makes no writer-generation or Activation-authority mutation. Writer cutover belongs to Issue #118 and the root Canary belongs to Issue #119.
- `skills/implement-gwo/SKILL.md` is routing guidance only: V8 admission requires `preview_mode="beta2_isolated_preview"`, a temporary target proven beneath the configured isolation root, and `writer_activation_enabled=False`; V6.1 remains authoritative for every normal real repository until #118/#119. Skill text cannot grant writer admission.
- Default deterministic limits remain four Worker Slots, at most four compatible Batch members, three-minute interactive wait grace, thirty-minute stale deadline, at most three Candidate SHAs per Work Run, and one initial plus at most one replacement Worker binding.
- There is no periodic LLM polling, automatic provider-daemon restart, recursive Batch bisection, cross-Campaign Batch, cross-Plan Candidate adoption, automatic authority expansion, or automatic GitHub tracker mutation.
- Every implementation task uses RED, proves RED, writes the minimum GREEN implementation, proves GREEN, refactors only while green, runs the owning focused gate, and commits one small change. Python commands use py -3.13.
- Parallel tasks have disjoint write sets. Use no more than five subagents; if subagents are used, select gpt-5.6-luna with max reasoning. Do not run two tasks that edit the same file at once.
- Beta1 is metadata/tracker repair only and never production admission. Beta2 is the feature-complete preview and requires this composition plus isolated E2E evidence. Beta3 is the cutover candidate after #118. GA requires a real public-API root Canary and Activation/default-writer read-back.
- Plan authoring performs no GitHub or production mutation. During execution, make only each task's explicit small commit; every push, pull request, Issue mutation, or real-target action remains behind the named human/readback gate in that task.

---

## Scope, prerequisites, and the explicit #137 checkpoint

This plan is the production-composition/hardening bridge named by the master plan docs/superpowers/plans/2026-08-03-gwo-v8-ga-delivery-program.md. It is not a replacement for the child plans for #113, #114/#115, or #116/#117, and it does not implement the #118 Guard.

At planning time, the complete GitHub readback is:

- #113, #114, #115, #116, #117, and #118 are open.
- #134, #135, #136, and #137 are closed.
- #137 has no comments and names #134, #114, and #115 as blockers.
- #114 and #115 are therefore not considered revalidated merely because #137 is closed.

The execution order must contain this idempotent human checkpoint and must stop
at each boundary. Beta1 already owns the first repair decision, so this plan
must not demand a second reopen when that decision has taken effect:

1. Read the complete bodies and comments of #114, #115, and #137, then record
   the exact #137 state, blocker graph, and any Beta1 Step 5 owner-approval
   evidence. Read the #114/#115 merge SHAs, final main CI, and merged
   CandidateGate/Repair interfaces before revalidation.
2. If #137 is already OPEN and its readback contains the Beta1-approved
   closed-with-open-blockers repair, select
   `reopen_path="beta1_prior_owner_approval"`, record the original approval
   comment/actor/timestamp and the OPEN readback, and perform no GitHub
   mutation. Re-read #137 with `--comments` after recording that evidence.
3. If #137 is CLOSED, first confirm that #114 and #115 are merged and that the
   final merged tree contains their receipt, Review Finding, Repair
   Verification, and Candidate/Repair scope-escape contracts. Present the
   operator with the exact blocker readback, planned #137 test list,
   production-composition Candidate/Batch interface hashes, and the fact that
   no target or writer will be mutated. Obtain a fresh explicit human approval;
   only that approved operator may conditionally run
   `gh issue reopen 137 --repo NOirBRight/github-work-orchestrator`. Select
   `reopen_path="post_merge_manual_approval"`, record that approval and the
   resulting OPEN readback, and re-read #137 with `--comments`.
4. If #137 is neither OPEN nor CLOSED, stop with NO-GO. Both valid paths must
   prove #137 is OPEN before any revalidation test runs; a closed issue with an
   open #114/#115 blocker is never treated as revalidated. This plan itself
   never runs the mutation while authoring the plan file.
5. Revalidate #137 only while it is OPEN and only against the merged #114/#115
   code. After successful revalidation, obtain a separate approval before any
   close action; read back the final blocker graph after that action. If that
   close approval is absent, leave #137 OPEN and record the Beta2 decision as
   HOLD. Beta2 still does not activate the default writer.

Read-only checkpoint commands:

~~~powershell
$repoRoot = (git rev-parse --show-toplevel).Trim()
if (-not $repoRoot) { throw 'Run from a GWO implementation worktree.' }
Set-Location -LiteralPath $repoRoot
git status --short --branch
gh issue view 114 --repo NOirBRight/github-work-orchestrator --comments --json number,state,title,body,comments
gh issue view 115 --repo NOirBRight/github-work-orchestrator --comments --json number,state,title,body,comments
gh issue view 137 --repo NOirBRight/github-work-orchestrator --comments --json number,state,title,body,comments
gh issue list --repo NOirBRight/github-work-orchestrator --state open --limit 100 --json number,title,labels
~~~

Expected before the checkpoint: the readback selects exactly one path—already
OPEN with recorded Beta1 approval, or CLOSED pending merged #114/#115 plus a
fresh approval—and no mutation has occurred in this plan-authoring session.
Expected after either approved path: #137 is OPEN, its body/comments and
blockers are re-read, the selected `reopen_path` and approval evidence are
recorded, and the #137 test task is unblocked. A close is a separate approved
post-revalidation action, not a prerequisite silently performed here.

## Parallel execution waves

The write-set-safe maximum is five workers, but a task is parallel only when its
entire production, test, support, documentation, and generated-manifest write
set is disjoint. Shared `v8_production_test_support.py`,
`test_v8_execution_kernel_integrity.py`, `production_host.py`,
`production_effects.py`, and `test_v8_production_docs.py` therefore force the
serial boundaries shown below:

~~~text
 Gate: merged #113-#117 + approved #137 OPEN checkpoint (Beta1 path or post-merge path)
  ├─ Wave A: Task 1 contract/fixture lock
  ├─ Wave B: Task 2 Kernel SQLite CAS
  ├─ Wave C: Task 3 Kernel Result/delivery integrity
  ├─ Wave D: Task 4 ProductionWorkRunEffects
  ├─ Wave E: Task 5 async planning/ProductionGwoHost
  ├─ Wave F: Task 6 #137 revalidation tests
  ├─ Wave G: Task 7 watchdog/batch/restart composition E2E
  ├─ Wave H: Task 8 Skill replacement || Task 9 isolated E2E/evidence
  └─ Wave I: Task 10 operational docs and full Beta2 gate
~~~

Task 2 starts after Task 1 because both originally needed the shared support
module. Task 3 starts after Task 2 and owns the shared integrity test/support
slice. Task 4 starts after Task 3 and consumes its final union/proof fields.
Task 5 starts after Task 4 and owns `production_host.py` and the host-support
fixtures. Task 6 starts after Task 5 because its revalidation fixture uses that
same support module. Task 7 is serial because it owns the shared composition
host/effects/support slice and also regenerates
`skills/orchestrator/.skill-package.json`. After Task 7, Task 8 and Task 9 may
run in parallel: Task 8 writes only `skills/implement-gwo/SKILL.md`,
`gwo_v8/__init__.py`, the two generated package manifests, and
`test_implement_gwo_skill.py`; Task 9 writes only the
production-composition support/E2E/evidence-test slice. Their complete write
sets are disjoint, including generated files. Task 10 starts after both have
completed because it extends `test_v8_production_docs.py` and reads every
earlier artifact. No other wave is parallel.

The generated manifests are real shared write-set members, not incidental
outputs. Apply this hard fence before scheduling any task or Issue lane:

- `skills/orchestrator/.skill-package.json` is written by Tasks 1, 2, 3, 4, 5,
  7, and 8. Those tasks are strictly sequential in the order shown above;
  no two of them may run concurrently, even when their source files and test
  files appear disjoint. Each of those package-writing task commits must run
  `py -3.13 scripts/sync_orchestrator.py`, then
  `py -3.13 scripts/sync_orchestrator.py --check`, and include the generated
  manifest in the same `git add` and commit as that task's source changes.
- `skills/implement-gwo/.skill-package.json` is written by Task 8. Any future
  task or Issue lane that changes `skills/implement-gwo/SKILL.md` and therefore
  regenerates this manifest is serialized with Task 8 and with every other
  lane touching that manifest; it cannot be made parallel merely because its
  source/test files are disjoint. Its commit uses the same sync, check, and
  manifest-in-the-same-commit rule.
- A #137 test/readback lane or documentation-only lane may run beside a package
  lane only when that #137/documentation lane's complete write set contains
  neither manifest. Task 6's #137 revalidation lane has no manifest write, and
  Task 9 is eligible beside Task 8 because it does not modify either manifest;
  Task 10 is scheduled after Task 8/9 because it shares
  `test_v8_production_docs.py`. A lane that touches either manifest is never
  parallel with another lane touching that same manifest.

Thus the `Task 8 || Task 9` notation is the only current parallel pair. The
manifest fence remains in force for later Issue waves, including #137
readback/revalidation and documentation splits: disjoint source files alone
do not permit concurrency when a generated manifest is in either write set.

## File and responsibility map

| File | Responsibility in this plan |
| --- | --- |
| skills/orchestrator/scripts/gwo_v8/production_effects.py | Create ProductionWorkRunEffects, the host-private Runtime/Candidate/Batch adapter, receipt ledger, and exact Result projection. It owns no Campaign lifecycle state. |
| skills/orchestrator/scripts/gwo_v8/production_host.py | Create ProductionGwoHost, ProductionHostConfiguration, the public start/advance/inspect façade, pending-planning projection, and Watchdog delegation. |
| skills/orchestrator/scripts/gwo_v8/execution_kernel.py | Add SQLite CAS/version readback, strict ResultIntegrityProof, delivery-action admission, Plan Invalidation observation handoff, and read-only inspect guarantees. |
| skills/orchestrator/scripts/gwo_v8/plan_control_host.py | Add only host-private RuntimeGateway factory and durable planning-continuation read/continue seams; keep the existing PlanControl public start boundary unchanged. |
| skills/orchestrator/scripts/gwo_v8/__init__.py | Export the composition host/effect types without exporting a fourth workflow operation or the predecessor GoalDriver path. |
| tests/v8_production_test_support.py | Define independent fake Runtime/Candidate/Batch ports, crash injectors, target isolation checks, and evidence-bundle builders with exact signatures. |
| tests/test_v8_execution_kernel_integrity.py | CAS, inspect, Result integrity, delivery scheduling, and crash-window tests. |
| tests/test_v8_production_effects.py | Runtime-to-Candidate-to-Batch translation, CandidateGate-owned receipt identity, Batch preparation, and effect-ledger restart tests. |
| tests/test_v8_production_host.py | ProductionGwoHost public path, planning continuation, writer non-mutation, and read-only inspect tests. |
| tests/test_v8_production_replanning.py | Reopened #137 Candidate/Review/Repair scope-escape revalidation through public advance/inspect. |
| tests/test_v8_production_composition_e2e.py | Isolated composition E2E, real-provider opt-in, lost callback, restart, Watchdog, and Batch convergence tests. |
| tests/test_implement_gwo_skill.py | Skill guidance and predecessor reachability assertions. |
| tests/test_v8_production_docs.py | Operational runbook and Beta2 evidence-bundle contract tests. |
| skills/implement-gwo/SKILL.md | Replace predecessor execution guidance with the V8 host-facing three-operation guidance. |
| docs/operations/gwo-v8-production-composition.md | Production configuration, isolation, restart/crash runbook, evidence schema, and Beta2 go/no-go checklist. |
| skills/implement-gwo/.skill-package.json | Generated implement-gwo package digest; changed only by `py -3.13 scripts/sync_orchestrator.py`. |
| skills/orchestrator/.skill-package.json | Regenerate content hash through the repository sync script; never hand-edit the hash. |

The following files are consumed but not modified by this plan: candidate_gate.py from #114/#115, the final integration_batch.py/BatchIntegrator from #116/#117, campaign_watchdog.py from #113, and entry.py, goal_driver.py, and kernel.py predecessor implementations. Issue #118 owns their final cutover deletion or unreachable-path proof. The production host must nevertheless prove that it never imports or calls those predecessor execution paths.

## Cross-module contracts frozen before implementation

The merged #114/#115 delivery owns both Candidate values. The shared foundation
adds `candidate_gate.CandidateReceipt` and `WorkRunObservation.candidate_receipt`,
which the Kernel persists at `run["candidate_receipt"]` before a Work Run leaves
semantic execution. CandidateGate then owns the distinct
`candidate_gate.AcceptedCandidateReceipt`; this plan imports and consumes those
values and never defines a receipt class in `production_effects.py`.

The exact CandidateGate names already present at the seam are `CandidateGate`,
`CandidateGateResult`, `CandidateGateStatus`, `CandidateGateParent`,
`CandidateIdentity`, `RepairPacket`, and `PlanInvalidationEvidence`.
Production composition calls the final `gate_candidate` method, not a new
production-owned evaluation result type:

~~~python
from .candidate_gate import (
    AcceptedCandidateReceipt,
    CandidateGateParent,
    CandidateGateResult,
    CandidateIdentity,
    InteractionClassification,
    InteractionKey,
    RepairPacket,
)

class CandidateGatePort(Protocol):
    def gate_candidate(
        self,
        parent: CandidateGateParent,
        reported_reference: str,
    ) -> CandidateGateResult: ...

    def verify_repair(
        self,
        parent: CandidateGateParent,
        packet: RepairPacket,
        candidate: CandidateIdentity,
    ) -> CandidateGateResult: ...

    def replay_plan_invalidation(
        self,
        parent: CandidateGateParent,
        evidence: PlanInvalidationEvidence,
        report: PlanInvalidationReport,
    ) -> CandidateGateResult: ...

class CandidateGateParentSource(Protocol):
    def for_action(
        self,
        action: WorkRunAction,
        subject: WorkRunSubject,
    ) -> CandidateGateParent: ...
~~~

`CandidateGateResult` remains the owning result type. Its final merged
#114/#115 contract exposes `accepted_candidate_receipt:
AcceptedCandidateReceipt | None`; it is non-null only for
`CandidateGateStatus.REVIEW_ACCEPTED` or `CandidateGateStatus.REPAIR_ACCEPTED`.
`CandidateReceipt` is never converted into an accepted receipt by composition,
and an accepted receipt has no Result field. The accepted receipt has the exact
fields `repository`, `campaign_key`, `plan_revision_digest`, `target_branch`,
`ticket_key`, `work_run_key`, `integration_node_key`, `accepted_sequence`,
`base_sha`, `base_tree_oid`, `candidate_sha`, `candidate_tree_oid`,
`candidate_receipt_digest`, `diff_schema_version="CandidateDiffRecordV1"`,
`diff_record_digest`, `authority_subtree_digest`, `policy_witness_digest`,
`review_subject_digest`, `assurance="standard" | "strict"`,
`assurance_requirement_digest`, `check_environment_digest`,
`delivery_identity_digest`, `interaction_keys`, `protected_surfaces`,
`gitlink_change`, `evidence_digests`, and `review_finding_ledger_digest`; it
provides `digest: str` and `canonical() -> dict[str, object]`.

The #113 merged effect seam is also consumed exactly, including its liveness
identity. `WorkRunEffects.readback` and `.execute` return the closed union
`WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation`.
Both `WorkRunAction` and `WorkRunObservation` carry
`runtime_binding_id: str | None`; the initial semantic action has no binding,
and each later action copies the binding established by authoritative Runtime
readback. `StaleBindingObservation` carries
`stable_action_id`, `runtime_binding_id`, `state: StaleReadbackState`,
`runtime_readback_digest`, `process_readback_digest`,
`workspace_readback_digest`, `campaign_readback_digest`, and `receipt_digest`.
`StaleDiagnosisObservation` carries `stable_action_id`, `runtime_binding_id`,
`disposition: StaleDiagnosisDisposition`, and `receipt_digest`. Composition
does not define aliases for these types and rejects a cross-kind, stale-binding,
or changed-receipt observation before Kernel state changes.

The merged #116/#117 delivery exposes the final BatchIntegrator boundary below.
`BatchIntegrator` owns queue formation, the Integration Lease, local/hosted/
target delivery, and exact readback. Production composition does not register a
Candidate separately and cannot substitute `GitIntegrationBatchAssembler`.

~~~python
from .batch_integrator import (
    BatchDeliveryAction,
    BatchDeliveryObservation,
    BatchDeliveryProof,
    BatchDeliveryRequest,
    BatchIntegrator,
    DeliveryIdentityMismatch,
)

class BatchIntegratorPort(Protocol):
    def prepare(self, request: BatchDeliveryRequest) -> BatchDeliveryAction: ...
    def readback(
        self,
        action: BatchDeliveryAction,
    ) -> BatchDeliveryObservation | None: ...
    def execute(self, action: BatchDeliveryAction) -> BatchDeliveryObservation: ...

class BatchRequestSource(Protocol):
    def for_action(
        self,
        action: WorkRunAction,
        subject: WorkRunSubject,
        accepted_candidates: tuple[AcceptedCandidateReceipt, ...],
    ) -> BatchDeliveryRequest: ...
~~~

`BatchDeliveryRequest` has the exact final fields
`stable_action_id`, `repository`, `campaign_key`, `plan_revision_digest`,
`target: BatchTarget`, `accepted_candidates`, `local_suite`, `hosted_suites`,
`writer_generation`, and `activation_id`; it provides `request_digest`.
`BatchDeliveryAction` has `stable_action_id`, `request_digest`, `batch_id`,
`batch_sha`, and `member_ticket_keys`. `BatchDeliveryProof` binds the exact
delivery action/request/Batch/member partition plus local, publication, PR,
hosted-result, Integration-Lease, and target-readback receipts.
`BatchDeliveryObservation` has
`stable_action_id`, `batch_id`, `batch_sha`, `phase` in
`running | wait | complete | decision | blocked`, `reason`, `receipt_digest`,
`retry_count`, `fallback_generation`, `members`, and `delivery_proofs`. A direct
completion has one proof; a successful fallback parent has one ordered child
Singleton proof per member. The complete observation's `receipt_digest` binds
that entire proof partition; composition never reconstructs a second Batch
receipt or infers a delivery fact from the request.

The exact cross-module identity rules are:

- A CandidateGate accepted result must carry the exact Candidate receipt, same Campaign, Plan Revision, Ticket, Work Run, Runtime Binding, Candidate/diff/authority/Review/Evidence identities, and `AcceptedCandidateReceipt.digest`; composition reads and validates those fields but never derives them.
- A `BatchDeliveryRequest` contains one or more accepted receipts from the same Campaign, Plan Revision, and `target`; `BatchIntegrator.prepare` is called once for its stable action and the returned action is read back before `execute`.
- A Batch observation may create a Result only when `phase == "complete"`, its parent stable action/request/Batch identities match, its durable receipt is exact, every member is integrated, and its proof partition covers each member exactly once. Squash and rebase mappings remain rejected inside BatchIntegrator; composition does not reinterpret them.
- `ResultIntegrityProof.from_batch_observation(action, request, observation, accepted_candidate)` consumes the exact final Batch observation and CandidateGate-owned receipt, selects the unique direct-or-Singleton proof containing that Candidate's Ticket, and copies every proof field. It validates the parent and selected delivery identities, target branch/head, durable local/publication/PR/hosted/lease/target receipts, and canonical Evidence before the Kernel accepts a Result. The Kernel never uses a generic effect receipt as a Result digest.

### Task 1: Freeze the production effect contract and independent test support

**Files:**
- Create: skills/orchestrator/scripts/gwo_v8/production_effects.py
- Create: tests/v8_production_test_support.py
- Create: tests/test_v8_production_effects.py
- Test: tests/test_v8_production_effects.py

**Interfaces:**
- Consumes: the merged `WorkRunAction`, `WorkRunObservation`,
  `StaleBindingObservation`, `StaleDiagnosisObservation`, and `WorkRunEffects`
  union from #113; `WorkRunSubject`, `RuntimeProgressReceipt`, the
  CandidateGatePort above, and the final BatchIntegratorPort above.
- Produces: ProductionCompositionError, RuntimeGatewayFactory, WorkRunSubjectSource, CandidateReferenceReader, ProductionWorkRunEffects, and the durable effect-receipt table v8_production_effect_receipts.

- [ ] Step 1: Write the failing contract test

~~~python
def test_production_effects_requires_the_merged_candidate_and_batch_ports(tmp_path):
    from gwo_v8.production_effects import (
        ProductionCompositionError,
        ProductionWorkRunEffects,
    )

    with pytest.raises(ProductionCompositionError) as raised:
        ProductionWorkRunEffects(
            store_path=tmp_path / "effects.sqlite3",
            runtime_gateways=object(),
            runtime_stale_readbacks=object(),
            work_run_subjects=object(),
            candidate_references=object(),
            candidate_parents=object(),
            candidate_gate=object(),
            batch_requests=object(),
            batch_integrator=object(),
        )
    assert raised.value.code == "PRODUCTION_COMPOSITION_INPUT_INVALID"
~~~

- [ ] Step 2: Run RED and record the missing module/seam

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_effects.py::test_production_effects_requires_the_merged_candidate_and_batch_ports" -q
~~~

Expected: FAIL because gwo_v8.production_effects and ProductionWorkRunEffects do not yet exist.

- [ ] Step 3: Write the minimum closed contracts and deterministic support ports

Implement the following exact signatures. Validate exact types, non-empty identities, canonical SHA-256 digests, and the closed status unions before creating the SQLite table. Do not call a provider or a deep module in a constructor.

~~~python
import sqlite3
from pathlib import Path


class ProductionCompositionError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class RuntimeGatewayFactory(Protocol):
    def for_campaign(self, handle: CampaignHandle) -> RuntimeGateway: ...

class WorkRunSubjectSource(Protocol):
    def for_action(self, action: WorkRunAction) -> WorkRunSubject: ...

class CandidateReferenceReader(Protocol):
    def read(
        self,
        output_artifact_digest: str,
        *,
        subject: WorkRunSubject,
    ) -> str: ...

class RuntimeStaleReadbackPort(Protocol):
    def read_stale(
        self,
        action: WorkRunAction,
    ) -> StaleBindingObservation | StaleDiagnosisObservation: ...

WorkRunEffectObservation = (
    WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation
)

class ProductionWorkRunEffects:
    def __init__(
        self,
        *,
        store_path: Path,
        runtime_gateways: RuntimeGatewayFactory,
        runtime_stale_readbacks: RuntimeStaleReadbackPort,
        work_run_subjects: WorkRunSubjectSource,
        candidate_references: CandidateReferenceReader,
        candidate_parents: CandidateGateParentSource,
        candidate_gate: CandidateGatePort,
        batch_requests: BatchRequestSource,
        batch_integrator: BatchIntegratorPort,
    ) -> None:
        required = (
            ("runtime_gateways", runtime_gateways, ("for_campaign",)),
            ("runtime_stale_readbacks", runtime_stale_readbacks, ("read_stale",)),
            ("work_run_subjects", work_run_subjects, ("for_action",)),
            ("candidate_references", candidate_references, ("read",)),
            ("candidate_parents", candidate_parents, ("for_action",)),
            (
                "candidate_gate",
                candidate_gate,
                ("gate_candidate", "verify_repair", "replay_plan_invalidation"),
            ),
            ("batch_requests", batch_requests, ("for_action",)),
            ("batch_integrator", batch_integrator, ("prepare", "readback", "execute")),
        )
        if any(
            any(not callable(getattr(port, method, None)) for method in methods)
            for _name, port, methods in required
        ):
            raise ProductionCompositionError(
                "PRODUCTION_COMPOSITION_INPUT_INVALID",
                "every merged Runtime/Candidate/Batch port must expose its exact methods",
            )
        self._store_path = Path(store_path)
        self._runtime_gateways = runtime_gateways
        self._runtime_stale_readbacks = runtime_stale_readbacks
        self._work_run_subjects = work_run_subjects
        self._candidate_references = candidate_references
        self._candidate_parents = candidate_parents
        self._candidate_gate = candidate_gate
        self._batch_requests = batch_requests
        self._batch_integrator = batch_integrator
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._store_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS v8_production_effect_receipts(
                    stable_action_id TEXT PRIMARY KEY,
                    action_json TEXT NOT NULL,
                    observation_json TEXT NOT NULL,
                    observation_digest TEXT NOT NULL,
                    accepted_candidate_receipt_json TEXT
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(v8_production_effect_receipts)"
                )
            }
            if "accepted_candidate_receipt_json" not in columns:
                connection.execute(
                    "ALTER TABLE v8_production_effect_receipts "
                    "ADD COLUMN accepted_candidate_receipt_json TEXT"
                )

    def readback(
        self,
        action: WorkRunAction,
    ) -> WorkRunEffectObservation | None:
        if type(action) is not WorkRunAction or not action.stable_action_id:
            raise ProductionCompositionError(
                "PRODUCTION_EFFECT_ACTION_INVALID",
                "readback requires one exact non-empty WorkRunAction identity",
            )
        with sqlite3.connect(self._store_path) as connection:
            row = connection.execute(
                "SELECT observation_json FROM v8_production_effect_receipts "
                "WHERE stable_action_id = ?",
                (action.stable_action_id,),
            ).fetchone()
        if row is None:
            return None
        raise ProductionCompositionError(
            "PRODUCTION_EFFECT_READBACK_REQUIRES_UNION_DECODER",
            "a persisted effect row requires the canonical closed-union decoder installed by Task 4",
        )

    def execute(self, action: WorkRunAction) -> WorkRunEffectObservation:
        cached = self.readback(action)
        if cached is not None:
            return cached
        raise ProductionCompositionError(
            "PRODUCTION_EFFECT_EXECUTION_REQUIRES_UNION_ADAPTER",
            "a missing effect row is executable only through the Task 4 readback-first adapter",
        )
~~~

Create `RecordingRuntimeGateway`, `RecordingRuntimeStaleReadback`,
`RecordingSubjectSource`, `RecordingCandidateReferenceReader`,
`RecordingCandidateParentSource`, `RecordingCandidateGate`,
`RecordingBatchRequestSource`, and `RecordingBatchIntegrator` in
`v8_production_test_support.py` with the same method signatures as the
protocols. `RecordingCandidateGate` returns the imported `CandidateGateResult`
and CandidateGate-owned `AcceptedCandidateReceipt`; it never creates a
competing receipt type. Each double records calls and none mutates a target
repository.

Copy these minimum deterministic doubles into
`tests/v8_production_test_support.py`; later tasks may add fields but may not
rename these methods:

~~~python
@dataclass
class RecordingRuntimeGateway:
    receipt: RuntimeProgressReceipt | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)

    def progress(
        self,
        subject: WorkRunSubject,
        *,
        wake_cursor: str | None,
    ) -> RuntimeProgressReceipt:
        self.calls.append(("progress", subject.work_run_key))
        if self.receipt is None:
            raise AssertionError("RecordingRuntimeGateway.receipt must be configured")
        return self.receipt


@dataclass
class RecordingRuntimeGatewayFactory:
    store_path: Path
    provider_command: str
    repository_root: Path
    gateway: RecordingRuntimeGateway = field(default_factory=RecordingRuntimeGateway)

    def __post_init__(self) -> None:
        self.runtime_configuration = make_recording_runtime_configuration(
            self.provider_command
        )
        self.repository_contexts = make_recording_repository_contexts(
            self.repository_root
        )

    def for_campaign(self, handle: CampaignHandle) -> RecordingRuntimeGateway:
        return self.gateway

    def build(self, **_kwargs: object) -> RecordingRuntimeGateway:
        return self.gateway


@dataclass
class RecordingRuntimeStaleReadback:
    observation: StaleBindingObservation | StaleDiagnosisObservation | None = None
    calls: list[str] = field(default_factory=list)

    def read_stale(
        self,
        action: WorkRunAction,
    ) -> StaleBindingObservation | StaleDiagnosisObservation:
        self.calls.append(action.stable_action_id)
        if self.observation is None:
            raise AssertionError("RecordingRuntimeStaleReadback.observation must be configured")
        return self.observation


@dataclass
class RecordingSubjectSource:
    subject: WorkRunSubject | None = None
    calls: list[str] = field(default_factory=list)

    def for_action(self, action: WorkRunAction) -> WorkRunSubject:
        self.calls.append(action.stable_action_id)
        if self.subject is None:
            raise AssertionError("RecordingSubjectSource.subject must be configured")
        return self.subject


@dataclass
class RecordingCandidateReferenceReader:
    reference: str | None = None
    calls: list[str] = field(default_factory=list)

    def read(self, output_artifact_digest: str, *, subject: WorkRunSubject) -> str:
        self.calls.append(output_artifact_digest)
        if self.reference is None:
            raise AssertionError("RecordingCandidateReferenceReader.reference must be configured")
        return self.reference


@dataclass
class RecordingCandidateParentSource:
    parent: CandidateGateParent | None = None

    def for_action(
        self,
        action: WorkRunAction,
        subject: WorkRunSubject,
    ) -> CandidateGateParent:
        if self.parent is None:
            raise AssertionError("RecordingCandidateParentSource.parent must be configured")
        return self.parent


@dataclass
class RecordingCandidateGate:
    result: CandidateGateResult | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)

    def _result(self, operation: str, stable_action_id: str) -> CandidateGateResult:
        self.calls.append((stable_action_id, operation))
        if self.result is None:
            raise AssertionError("RecordingCandidateGate.result must be configured")
        return self.result

    def gate_candidate(
        self,
        parent: CandidateGateParent,
        reported_reference: str,
    ) -> CandidateGateResult:
        return self._result("gate_candidate", parent.runtime_subject.stable_action_id)

    def verify_repair(
        self,
        parent: CandidateGateParent,
        packet: RepairPacket,
        candidate: CandidateIdentity,
    ) -> CandidateGateResult:
        return self._result("verify_repair", parent.runtime_subject.stable_action_id)

    def replay_plan_invalidation(
        self,
        parent: CandidateGateParent,
        evidence: PlanInvalidationEvidence,
        report: PlanInvalidationReport,
    ) -> CandidateGateResult:
        return self._result(
            "replay_plan_invalidation",
            parent.runtime_subject.stable_action_id,
        )


@dataclass
class RecordingBatchRequestSource:
    target_path: Path
    runtime_factory: RecordingRuntimeGatewayFactory
    request: BatchDeliveryRequest | None = None

    def for_action(
        self,
        action: WorkRunAction,
        subject: WorkRunSubject,
        accepted_candidates: tuple[AcceptedCandidateReceipt, ...],
    ) -> BatchDeliveryRequest:
        if self.request is None:
            raise AssertionError("RecordingBatchRequestSource.request must be configured")
        return self.request


@dataclass
class RecordingBatchIntegrator:
    store_path: Path
    target_path: Path
    action: BatchDeliveryAction | None = None
    observation: BatchDeliveryObservation | None = None
    prepare_calls: int = 0
    execute_calls: int = 0
    target_integration_calls: int = 0
    suppress_callbacks: bool = False

    def prepare(self, request: BatchDeliveryRequest) -> BatchDeliveryAction:
        self.prepare_calls += 1
        if self.action is None:
            raise AssertionError("RecordingBatchIntegrator.action must be configured")
        return self.action

    def readback(self, action: BatchDeliveryAction) -> BatchDeliveryObservation | None:
        return self.observation

    def execute(self, action: BatchDeliveryAction) -> BatchDeliveryObservation:
        self.execute_calls += 1
        self.target_integration_calls += 1
        if self.observation is None:
            raise AssertionError("RecordingBatchIntegrator.observation must be configured")
        return self.observation
~~~

`make_recording_runtime_configuration(provider_command: str)` and
`make_recording_repository_contexts(root: Path)` are test-support constructors that
return the final merged `RuntimeConfiguration` and
`dict[str, RuntimeRepositoryContext]` values used by
`ProductionPlanControlStartHost`; they create no provider process and use the
literal command only as an inert recording field. Use these bodies:

~~~python
def make_recording_runtime_configuration(
    provider_command: str,
) -> RuntimeConfiguration:
    profile = RuntimeProfile(
        name="recording",
        provider="recording",
        model="recording-model",
        thinking="standard",
        mode="test",
        features={"provider_command": provider_command},
    )
    profile_digest = digest_value(profile.canonical())
    mapping = ProfileMapping(profile_digest)
    return RuntimeConfiguration(
        profiles={profile_digest: profile},
        host_mappings={"worker": mapping, "coordinator": mapping},
        repository_mappings={
            "owner/isolated-composition": {
                "worker": mapping,
                "coordinator": mapping,
            }
        },
        campaign_assertions={},
    )


def make_recording_repository_contexts(
    root: Path,
) -> dict[str, RuntimeRepositoryContext]:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return {
        "owner/isolated-composition": RuntimeRepositoryContext(
            root,
            "refs/heads/main",
        )
    }
~~~

The two explicit error branches above are the minimum Task 1 GREEN
implementation: Task 1 proves validation and table ownership without calling
a deep module. Task 4 Step 3 installs the complete canonical decoder/ledger
body shown there and keeps these constructor signatures and the closed
`WorkRunEffectObservation` union unchanged.

- [ ] Step 4: Run GREEN and prove constructor isolation

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_effects.py" -q
~~~

Expected: PASS; the constructor rejects incomplete ports, creates only its own effect ledger, and makes zero Runtime/Candidate/Batch calls.

- [ ] Step 5: Refactor while green and commit the contract slice

Keep the Protocol declarations and constructor validation together in production_effects.py; keep all fake provider/receipt builders in v8_production_test_support.py. Do not add a compatibility wrapper around GitIntegrationBatchAssembler.

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_effects.py" -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add "skills/orchestrator/scripts/gwo_v8/production_effects.py" "tests/v8_production_test_support.py" "tests/test_v8_production_effects.py" "skills/orchestrator/.skill-package.json"
git commit -m "feat: freeze V8 production composition ports"
~~~

### Task 2: Add durable SQLite Campaign CAS and preserve read-only inspect

**Files:**
- Modify: skills/orchestrator/scripts/gwo_v8/execution_kernel.py
- Create: tests/test_v8_execution_kernel_integrity.py
- Test: tests/test_v8_execution_kernel.py

**Interfaces:**
- Consumes: existing ExecutionKernel, CampaignHandle, Diagnostics, and _save call sites.
- Produces: private `KernelStateReadback`, `_read_state(handle)`,
  `_save(handle, state, *, expected_version: int) -> int`,
  `EXECUTION_STORE_CAS_CONFLICT`, and a schema migration from the current
  two-column table to `state_version` plus `state_digest`.

- [ ] Step 1: Write the failing CAS and inspect tests

~~~python
def test_sqlite_campaign_state_rejects_stale_writer_without_overwriting(tmp_path, handle, active_plan):
    first = make_kernel(tmp_path / "kernel.sqlite3", active_plan)
    second = make_kernel(tmp_path / "kernel.sqlite3", active_plan)
    first.advance(handle)
    left = first._read_state(handle)
    right = second._read_state(handle)
    left_state = dict(left.state)
    left_state["test_marker"] = "left"
    first._save(handle, left_state, expected_version=left.version)
    right_state = dict(right.state)
    right_state["test_marker"] = "right"
    with pytest.raises(ExecutionKernelError) as raised:
        second._save(handle, right_state, expected_version=right.version)
    assert raised.value.code == "EXECUTION_STORE_CAS_CONFLICT"
    assert first._load(handle)["test_marker"] == "left"


def test_inspect_does_not_write_or_migrate_campaign_state(tmp_path, handle, active_plan):
    kernel = make_kernel(tmp_path / "kernel.sqlite3", active_plan)
    kernel.advance(handle)
    before = (tmp_path / "kernel.sqlite3").read_bytes()
    diagnostics = kernel.inspect(handle)
    after = (tmp_path / "kernel.sqlite3").read_bytes()
    assert diagnostics.campaign == handle
    assert after == before


def test_raw_wake_cas_does_not_advance_trusted_progress_or_reset_staleness(
    tmp_path,
    handle,
    active_plan,
):
    kernel = make_kernel(tmp_path / "kernel.sqlite3", active_plan)
    kernel.advance(handle)
    before = kernel._read_state(handle)
    run = before.state["runs"][next(iter(before.state["runs"]))]
    trusted = (
        run["trusted_progress_revision"],
        run["last_trusted_progress_at"],
        run["stale_due_at"],
    )
    state = dict(before.state)
    state["last_wake_ref"] = "watchdog:raw:41"
    after_version = kernel._save(handle, state, expected_version=before.version)
    after = kernel._read_state(handle)
    updated_run = after.state["runs"][next(iter(after.state["runs"]))]
    assert after_version == before.version + 1
    assert after.version == before.version + 1
    assert (
        updated_run["trusted_progress_revision"],
        updated_run["last_trusted_progress_at"],
        updated_run["stale_due_at"],
    ) == trusted
~~~

Add these concrete fixtures to `v8_production_test_support.py` before the Task 2
tests. They use the existing independent `_minimal_active_campaign` and
`_StaticPlanReader`; the effect double has the exact #113 closed-union return
type and never calls a provider:

~~~python
from dataclasses import dataclass, field, replace

from pathlib import Path

import pytest

from gwo_v8._canonical import digest_value
from gwo_v8.execution_kernel import (
    ExecutionKernel,
    StaleBindingObservation,
    StaleDiagnosisObservation,
    WorkRunAction,
    WorkRunEffects,
    WorkRunObservation,
)
from gwo_v8.plan_control import ActivePlanReadback, CampaignHandle
from v8_successor_test_support import _StaticPlanReader, _minimal_active_campaign


@dataclass
class NoopRunningEffects:
    observations: dict[str, WorkRunObservation] = field(default_factory=dict)

    def readback(
        self,
        action: WorkRunAction,
    ) -> WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation | None:
        return self.observations.get(action.stable_action_id)

    def execute(
        self,
        action: WorkRunAction,
    ) -> WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation:
        if action.kind not in {"semantic_execution", "semantic_resume"}:
            raise AssertionError(f"unexpected kernel test action: {action.kind}")
        observation = WorkRunObservation(
            phase="running",
            stable_action_id=action.stable_action_id,
            runtime_binding_id="binding:test",
            receipt_digest=digest_value(
                {"kind": "test-running", "action": action.stable_action_id}
            ),
        )
        self.observations[action.stable_action_id] = observation
        return observation


@pytest.fixture
def handle() -> CampaignHandle:
    _active, handle = _minimal_active_campaign(("issue:109",))
    return handle


@pytest.fixture
def active_plan() -> ActivePlanReadback:
    active, _handle = _minimal_active_campaign(("issue:109",))
    return active


@pytest.fixture
def make_kernel():
    def build(
        store_path: Path,
        active: ActivePlanReadback,
        *,
        effects: WorkRunEffects | None = None,
    ) -> ExecutionKernel:
        return ExecutionKernel(
            store_path=Path(store_path),
            plan_control=_StaticPlanReader(active),
            effects=effects or NoopRunningEffects(),
        )

    return build
~~~

The production implementation must retain the same `WorkRunAction` and
`WorkRunEffects` signatures: `runtime_binding_id` is copied from trusted
readback, while #113's `trusted_progress_revision`,
`last_trusted_progress_at`, and `stale_due_at` remain untouched by this raw
wake fixture.

- [ ] Step 2: Run RED

~~~powershell
py -3.13 -m pytest "tests/test_v8_execution_kernel_integrity.py::test_sqlite_campaign_state_rejects_stale_writer_without_overwriting" "tests/test_v8_execution_kernel_integrity.py::test_inspect_does_not_write_or_migrate_campaign_state" "tests/test_v8_execution_kernel_integrity.py::test_raw_wake_cas_does_not_advance_trusted_progress_or_reset_staleness" -q
~~~

Expected: FAIL because the current table has no versioned _read_state/CAS seam and the current _save is unconditional upsert.

- [ ] Step 3: Implement the minimum versioned state store

Use this exact private model and SQL behavior:

~~~python
@dataclass(frozen=True)
class KernelStateReadback:
    state: dict[str, Any]
    version: int
    state_digest: str

def _save(
    self,
    handle: CampaignHandle,
    state: dict[str, Any],
    *,
    expected_version: int,
) -> int:
    rendered = json.dumps(state, separators=(",", ":"), sort_keys=True)
    digest = digest_bytes(rendered.encode("utf-8"))
    with self._connect() as connection:
        cursor = connection.execute(
            """
            UPDATE v8_execution_kernel_campaigns
               SET state_version = state_version + 1,
                   state_json = ?,
                   state_digest = ?
             WHERE repository = ? AND campaign_key = ?
               AND state_version = ?
            """,
            (
                rendered,
                digest,
                handle.repository,
                handle.campaign_key,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise ExecutionKernelError(
                "EXECUTION_STORE_CAS_CONFLICT",
                "Campaign state version changed before the durable write",
            )
    return expected_version + 1
~~~

The constructor creates/migrates exactly these columns: repository, campaign_key, state_version INTEGER NOT NULL, state_json, and state_digest. A first insert uses version 1; every read verifies the JSON digest before returning KernelStateReadback. Update every internal save/read call to carry the returned version. No external call is inside the SQLite context manager. If a stale save loses the CAS, read back the row; treat it as idempotent only when the exact requested canonical state is already present, otherwise raise the named conflict.

Keep the per-run #113 fields inside the canonical `state_json`: exact
`trusted_progress_revision`, `last_trusted_progress_at`, and `stale_due_at`.
`_save` increments only `state_version`; it must not synthesize trusted
progress. `_apply_observation` advances the per-run trusted revision and
recomputes `stale_due_at` only after an exact accepted `WorkRunObservation`,
`CandidateReceipt`, `StaleBindingObservation`, or `StaleDiagnosisObservation`
readback. A raw `wake_ref` is persisted, if needed, without changing any of
those three fields. Add a regression test that saves a raw wake through CAS,
asserts `state_version` increased, and asserts all three stale fields are
byte-for-byte unchanged.

inspect uses _read_state and a pure projection. It may validate an already-created schema in the constructor, but it cannot call _load_or_initialize, _save, migration, or any effect.

- [ ] Step 4: Run GREEN and the existing Kernel suite

~~~powershell
py -3.13 -m pytest "tests/test_v8_execution_kernel_integrity.py" "tests/test_v8_execution_kernel.py" "tests/test_v8_successor_execution_kernel.py" -q
~~~

Expected: PASS; concurrent stale state cannot overwrite a newer row, and byte-for-byte inspect proves no write.

- [ ] Step 5: Refactor and commit the CAS slice

~~~powershell
py -3.13 -m pytest "tests/test_v8_execution_kernel_integrity.py" "tests/test_v8_execution_kernel.py" "tests/test_v8_successor_execution_kernel.py" -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add "skills/orchestrator/scripts/gwo_v8/execution_kernel.py" "tests/test_v8_execution_kernel_integrity.py" "tests/test_v8_execution_kernel.py" "tests/test_v8_successor_execution_kernel.py" "skills/orchestrator/.skill-package.json"
git commit -m "feat: add compare-and-swap Campaign state"
~~~

### Task 3: Require exact Candidate, Batch, and target read-back before a Result

**Files:**
- Modify: skills/orchestrator/scripts/gwo_v8/execution_kernel.py
- Modify: tests/v8_production_test_support.py
- Modify: tests/test_v8_execution_kernel_integrity.py

**Interfaces:**
- Consumes: Task 2 CAS and the merged CandidateGate/BatchIntegrator receipt contracts.
- Produces: ResultIntegrityProof, strict WorkRunObservation fields, batch_delivery stable action IDs, and Kernel state projections for accepted-Candidate/delivery/result identity.

- [ ] Step 1: Write the failing integrity tests

~~~python
from dataclasses import fields, is_dataclass, replace

from gwo_v8._canonical import digest_value
from gwo_v8.batch_integrator import (
    BatchDeliveryAction,
    BatchDeliveryObservation,
    BatchDeliveryProof,
    BatchDeliveryRequest,
    BatchTarget,
    HostedSuiteDefinition,
    LocalSuiteDefinition,
    MemberDeliveryObservation,
)
from gwo_v8.execution_kernel import (
    ResultIntegrityProof,
    StaleDiagnosisDisposition,
    StaleReadbackState,
)
from v8_production_test_support import (
    make_accepted_candidate_receipt,
    make_candidate_receipt,
    make_result_integrity_proof,
)


def _dataclass_field_names(value_type: type[object]) -> tuple[str, ...]:
    return tuple(item.name for item in fields(value_type))


def test_observation_serializers_preserve_merged_dataclass_fields():
    for value_type in (
        WorkRunObservation,
        StaleBindingObservation,
        StaleDiagnosisObservation,
    ):
        assert is_dataclass(value_type)
        params = getattr(value_type, "__dataclass_params__", None)
        assert params is not None
        assert params.frozen is True

    assert "__post_init__" in WorkRunObservation.__dict__
    assert "running" in WorkRunObservation.__dict__

    assert _dataclass_field_names(WorkRunObservation) == (
        "phase",
        "stable_action_id",
        "receipt_digest",
        "reason",
        "next_check_at",
        "binding_established",
        "candidate_identity",
        "result_digest",
        "evidence_digests",
        "candidate_receipt",
        "runtime_binding_id",
        "accepted_candidate_receipt_digest",
        "candidate_diff_record_digest",
        "delivery_receipt_digest",
        "result_integrity",
        "plan_invalidation",
    )
    assert _dataclass_field_names(StaleBindingObservation) == (
        "stable_action_id",
        "runtime_binding_id",
        "state",
        "runtime_readback_digest",
        "process_readback_digest",
        "workspace_readback_digest",
        "campaign_readback_digest",
        "receipt_digest",
    )
    assert _dataclass_field_names(StaleDiagnosisObservation) == (
        "stable_action_id",
        "runtime_binding_id",
        "disposition",
        "receipt_digest",
    )


def test_observation_serializers_round_trip_merged_union_members():
    work_run = WorkRunObservation(
        phase="running",
        stable_action_id="action:running",
        receipt_digest="a" * 64,
        runtime_binding_id="binding:test",
    )
    stale_binding = StaleBindingObservation(
        stable_action_id="action:stale-readback",
        runtime_binding_id="binding:test",
        state=StaleReadbackState.IDLE,
        runtime_readback_digest="b" * 64,
        process_readback_digest="c" * 64,
        workspace_readback_digest="d" * 64,
        campaign_readback_digest="e" * 64,
        receipt_digest="f" * 64,
    )
    stale_diagnosis = StaleDiagnosisObservation(
        stable_action_id="action:stale-diagnosis",
        runtime_binding_id="binding:test",
        disposition=StaleDiagnosisDisposition.CONTINUE,
        receipt_digest="1" * 64,
    )

    assert WorkRunObservation.from_canonical(work_run.canonical()) == work_run
    assert StaleBindingObservation.from_canonical(
        stale_binding.canonical()
    ) == stale_binding
    assert StaleDiagnosisObservation.from_canonical(
        stale_diagnosis.canonical()
    ) == stale_diagnosis


def test_completed_observation_without_integrity_proof_is_rejected():
    with pytest.raises(ExecutionKernelError) as raised:
        WorkRunObservation(
            phase="completed",
            stable_action_id="action:completed",
            receipt_digest="a" * 64,
            result_digest="b" * 64,
        )
    assert raised.value.code == "RESULT_INTEGRITY_REQUIRED"


def test_accepted_candidate_receipt_alone_cannot_create_a_code_result(tmp_path, handle, active_plan):
    effects = OneCandidateOnlyEffects()
    kernel = make_kernel(tmp_path / "kernel.sqlite3", active_plan, effects=effects)
    kernel.advance(handle)
    diagnostics = kernel.inspect(handle)
    run = diagnostics.work_runs[0]
    assert run.phase == "accepted_awaiting_delivery"
    assert run.result_digest is None
    assert not any(
        item["ticket_key"] == run.ticket_key
        for item in kernel._load(handle)["accepted_results"]
    )


def test_completed_result_requires_exact_batch_and_target_readback(tmp_path, handle, active_plan):
    effects = TamperedDeliveryEffects()
    kernel = make_kernel(tmp_path / "kernel.sqlite3", active_plan, effects=effects)
    with pytest.raises(ExecutionKernelError) as raised:
        kernel.advance(handle)
    assert raised.value.code == "RESULT_INTEGRITY_INVALID"


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    (
        ("batch_delivery_proof_digest", "f" * 64),
        ("delivery_stable_action_id", "action:changed"),
        ("delivery_request_digest", "f" * 64),
        ("batch_id", "batch:changed"),
        ("batch_sha", "f" * 40),
        ("delivery_member_ticket_keys", ("issue:changed",)),
        ("local_check_receipt_digest", "f" * 64),
        ("publication_receipt_digest", "f" * 64),
        ("pull_request_number", 99),
        ("pull_request_head_sha", "f" * 40),
        ("hosted_result_receipt_digest", "f" * 64),
        ("integration_lease_digest", "f" * 64),
        ("target_branch", "release"),
        ("target_head_sha", "f" * 40),
        ("target_readback_digest", "f" * 64),
        ("target_contains_batch_sha", False),
        ("pull_request_merge_target_sha", "f" * 40),
        ("merge_method", "squash"),
    ),
)
def test_any_exact_delivery_proof_field_tamper_fails_closed(
    field_name,
    tampered_value,
):
    action = WorkRunAction(
        stable_action_id="action:integrity",
        repository="owner/repository",
        campaign_key="campaign:integrity",
        plan_revision_digest="a" * 64,
        ticket_key="issue:integrity",
        kind="batch_delivery",
        semantic_action_id="semantic:integrity",
        work_run_key="work-run:integrity",
        work_subject_digest="b" * 64,
        runtime_binding_id="binding:integrity",
        wake_ref="candidate:accepted",
        accepted_candidate_receipt_digest=None,
    )
    candidate = make_candidate_receipt(action)
    accepted = make_accepted_candidate_receipt(action, candidate)
    proof = make_result_integrity_proof(
        action,
        accepted,
        target_contains_batch_sha=True,
    )
    proof.validate_for(action, "main")

    with pytest.raises(ExecutionKernelError) as raised:
        replace(proof, **{field_name: tampered_value}).validate_for(action, "main")

    assert raised.value.code == "RESULT_INTEGRITY_INVALID"


def test_fallback_result_selects_exact_singleton_proof_and_keeps_parent_receipt():
    work_action = WorkRunAction(
        stable_action_id="work-action:1",
        repository="owner/repository",
        campaign_key="campaign:fallback",
        plan_revision_digest="a" * 64,
        ticket_key="issue:1",
        kind="batch_delivery",
        semantic_action_id="semantic:1",
        work_run_key="work-run:1",
        work_subject_digest="b" * 64,
        runtime_binding_id="binding:1",
        wake_ref="candidate:accepted",
        accepted_candidate_receipt_digest=None,
    )
    candidate = make_candidate_receipt(work_action)
    first = make_accepted_candidate_receipt(work_action, candidate)
    second = replace(
        first,
        ticket_key="issue:2",
        work_run_key="work-run:2",
        integration_node_key="integration:issue:2",
        accepted_sequence=2,
        candidate_sha="c" * 40,
        candidate_tree_oid="d" * 40,
        candidate_receipt_digest="e" * 64,
        diff_record_digest="f" * 64,
        evidence_digests=("2" * 64,),
    )
    request = BatchDeliveryRequest(
        stable_action_id="parent-delivery:fallback",
        repository=work_action.repository,
        campaign_key=work_action.campaign_key,
        plan_revision_digest=work_action.plan_revision_digest,
        target=BatchTarget(
            repository=work_action.repository,
            target_branch="main",
            target_head_sha="9" * 40,
            target_tree_oid="8" * 40,
            target_facts_digest="7" * 64,
        ),
        accepted_candidates=(first, second),
        local_suite=LocalSuiteDefinition(
            suite_id="local:fallback",
            definition_digest="6" * 64,
            command=("py", "-3.13", "-m", "pytest", "-q"),
        ),
        hosted_suites=(
            HostedSuiteDefinition(
                suite_id="hosted:fallback",
                hosted_name="GWO CI",
                definition_digest="5" * 64,
            ),
        ),
        writer_generation="v6.1",
        activation_id="activation:fallback",
    )
    parent_action = BatchDeliveryAction(
        stable_action_id=request.stable_action_id,
        request_digest=request.request_digest,
        batch_id="4" * 64,
        batch_sha="4" * 40,
        member_ticket_keys=(first.ticket_key, second.ticket_key),
    )
    first_delivery = BatchDeliveryProof.create(
        delivery_stable_action_id="singleton-delivery:1",
        delivery_request_digest="1" * 64,
        batch_id="1" * 64,
        batch_sha="1" * 40,
        member_ticket_keys=(first.ticket_key,),
        local_check_receipt_digest="a" * 64,
        publication_receipt_digest="b" * 64,
        pull_request_number=31,
        pull_request_head_sha="1" * 40,
        hosted_result_receipt_digest="c" * 64,
        integration_lease_digest="d" * 64,
        target_branch="main",
        target_head_sha="3" * 40,
        target_readback_digest="e" * 64,
        target_contains_batch_sha=True,
        pull_request_merge_target_sha="3" * 40,
        merge_method="merge",
    )
    second_delivery = BatchDeliveryProof.create(
        delivery_stable_action_id="singleton-delivery:2",
        delivery_request_digest="2" * 64,
        batch_id="2" * 64,
        batch_sha="2" * 40,
        member_ticket_keys=(second.ticket_key,),
        local_check_receipt_digest="b" * 64,
        publication_receipt_digest="c" * 64,
        pull_request_number=32,
        pull_request_head_sha="2" * 40,
        hosted_result_receipt_digest="d" * 64,
        integration_lease_digest="e" * 64,
        target_branch="main",
        target_head_sha="4" * 40,
        target_readback_digest="f" * 64,
        target_contains_batch_sha=True,
        pull_request_merge_target_sha="4" * 40,
        merge_method="merge",
    )
    members = (
        MemberDeliveryObservation(
            ticket_key=first.ticket_key,
            work_run_key=first.work_run_key,
            candidate_sha=first.candidate_sha,
            status="integrated",
            evidence_digests=first.evidence_digests,
        ),
        MemberDeliveryObservation(
            ticket_key=second.ticket_key,
            work_run_key=second.work_run_key,
            candidate_sha=second.candidate_sha,
            status="integrated",
            evidence_digests=second.evidence_digests,
        ),
    )
    observation_body = {
        "stable_action_id": parent_action.stable_action_id,
        "batch_id": parent_action.batch_id,
        "batch_sha": parent_action.batch_sha,
        "phase": "complete",
        "reason": "SingletonFallbackComplete",
        "retry_count": 0,
        "fallback_generation": 1,
        "members": [
            {
                "ticket_key": member.ticket_key,
                "work_run_key": member.work_run_key,
                "candidate_sha": member.candidate_sha,
                "status": member.status,
                "evidence_digests": list(member.evidence_digests),
                "resume_reason": member.resume_reason,
            }
            for member in members
        ],
        "delivery_proofs": [
            first_delivery.canonical(),
            second_delivery.canonical(),
        ],
    }
    observation = BatchDeliveryObservation(
        stable_action_id=parent_action.stable_action_id,
        batch_id=parent_action.batch_id,
        batch_sha=parent_action.batch_sha,
        phase="complete",
        reason="SingletonFallbackComplete",
        receipt_digest=digest_value(
            {"kind": "batch-observation.v1", **observation_body}
        ),
        retry_count=0,
        fallback_generation=1,
        members=members,
        delivery_proofs=(first_delivery, second_delivery),
    )

    first_result = ResultIntegrityProof.from_batch_observation(
        parent_action, request, observation, first
    )
    second_result = ResultIntegrityProof.from_batch_observation(
        parent_action, request, observation, second
    )

    assert first_result.batch_delivery_receipt_digest == observation.receipt_digest
    assert second_result.batch_delivery_receipt_digest == observation.receipt_digest
    assert first_result.delivery_proof_body() == first_delivery.body()
    assert second_result.delivery_proof_body() == second_delivery.body()
    assert first_result.batch_sha != parent_action.batch_sha
    assert second_result.batch_sha != parent_action.batch_sha
~~~

Put the two effect doubles and their receipt constructors in
`v8_production_test_support.py` before running these tests. They use the final
shared `CandidateReceipt`, the CandidateGate-owned `AcceptedCandidateReceipt`,
and the exact #113 closed union; neither double defines a production receipt
class or calls a deep module:

~~~python
from dataclasses import dataclass, field, replace

from gwo_v8.batch_integrator import BatchDeliveryProof

from gwo_v8.candidate_gate import (
    AcceptedCandidateReceipt,
    CandidateGateResult,
    CandidateGateStatus,
    CandidateReceipt,
    InteractionClassification,
    InteractionKey,
)
from gwo_v8.execution_kernel import (
    ResultIntegrityProof,
    StaleBindingObservation,
    StaleDiagnosisObservation,
    WorkRunAction,
    WorkRunObservation,
)


def make_candidate_receipt(action: WorkRunAction) -> CandidateReceipt:
    return CandidateReceipt(
        parent_digest="1" * 64,
        repository=action.repository,
        campaign_key=action.campaign_key,
        campaign_handle=f"{action.repository}:{action.campaign_key}",
        plan_revision_digest=action.plan_revision_digest,
        work_run_key=action.work_run_key or f"work-run:{action.ticket_key}",
        ticket_key=action.ticket_key,
        reported_reference="refs/heads/candidate",
        base_commit_oid="2" * 40,
        base_tree_oid="3" * 40,
        candidate_commit_oid="4" * 40,
        candidate_tree_oid="5" * 40,
        diff_schema_version="CandidateDiffRecordV1",
        diff_record_digest="6" * 64,
        authority_subtree_digest="7" * 64,
        runtime_subject_digest=action.work_subject_digest or "8" * 64,
    )


def make_accepted_candidate_receipt(
    action: WorkRunAction,
    candidate: CandidateReceipt | None = None,
) -> AcceptedCandidateReceipt:
    candidate = candidate or make_candidate_receipt(action)
    return AcceptedCandidateReceipt(
        repository=action.repository,
        campaign_key=action.campaign_key,
        plan_revision_digest=action.plan_revision_digest,
        target_branch="main",
        ticket_key=action.ticket_key,
        work_run_key=candidate.work_run_key,
        integration_node_key=f"integration:{action.ticket_key}",
        accepted_sequence=1,
        base_sha=candidate.base_commit_oid,
        base_tree_oid=candidate.base_tree_oid,
        candidate_sha=candidate.candidate_commit_oid,
        candidate_tree_oid=candidate.candidate_tree_oid,
        candidate_receipt_digest=candidate.digest,
        diff_schema_version=candidate.diff_schema_version,
        diff_record_digest=candidate.diff_record_digest,
        authority_subtree_digest=candidate.authority_subtree_digest,
        policy_witness_digest="9" * 64,
        review_subject_digest="a" * 64,
        assurance="standard",
        assurance_requirement_digest="b" * 64,
        check_environment_digest="c" * 64,
        delivery_identity_digest="d" * 64,
        interaction_keys=(
            InteractionKey(
                "candidate-path",
                "src/main.py",
                InteractionClassification.ORDINARY,
            ),
        ),
        protected_surfaces=(),
        gitlink_change=False,
        evidence_digests=("e" * 64,),
        review_finding_ledger_digest="f" * 64,
    )


def accepted_candidate_result(action: WorkRunAction) -> CandidateGateResult:
    candidate = make_candidate_receipt(action)
    return CandidateGateResult(
        status=CandidateGateStatus.REVIEW_ACCEPTED,
        evidence=(),
        candidate_receipt=candidate,
        accepted_candidate_receipt=make_accepted_candidate_receipt(action, candidate),
    )


@dataclass
class OneCandidateOnlyEffects:
    observations: dict[str, WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation] = field(default_factory=dict)

    def readback(
        self,
        action: WorkRunAction,
    ) -> WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation | None:
        return self.observations.get(action.stable_action_id)

    def execute(
        self,
        action: WorkRunAction,
    ) -> WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation:
        if action.kind != "semantic_execution":
            raise AssertionError(f"OneCandidateOnlyEffects received {action.kind}")
        result = accepted_candidate_result(action)
        observation = WorkRunObservation(
            phase="accepted_awaiting_delivery",
            stable_action_id=action.stable_action_id,
            runtime_binding_id="binding:test",
            receipt_digest=result.candidate_receipt.digest,
            candidate_receipt=result.candidate_receipt,
            accepted_candidate_receipt_digest=result.accepted_candidate_receipt.digest,
            candidate_diff_record_digest=result.candidate_receipt.diff_record_digest,
            result_integrity=None,
            result_digest=None,
        )
        self.observations[action.stable_action_id] = observation
        return observation


def make_result_integrity_proof(
    action: WorkRunAction,
    accepted: AcceptedCandidateReceipt,
    *,
    target_contains_batch_sha: bool,
) -> ResultIntegrityProof:
    delivery = BatchDeliveryProof.create(
        delivery_stable_action_id=action.stable_action_id,
        delivery_request_digest="0" * 64,
        batch_id="2" * 64,
        batch_sha="2" * 40,
        member_ticket_keys=(action.ticket_key,),
        local_check_receipt_digest="3" * 64,
        publication_receipt_digest="4" * 64,
        pull_request_number=17,
        pull_request_head_sha="2" * 40,
        hosted_result_receipt_digest="5" * 64,
        integration_lease_digest="6" * 64,
        target_branch="main",
        target_head_sha="7" * 40,
        target_readback_digest="8" * 64,
        target_contains_batch_sha=target_contains_batch_sha,
        pull_request_merge_target_sha="7" * 40,
        merge_method="merge",
    )
    proof = ResultIntegrityProof(
        accepted_candidate_receipt_digest=accepted.digest,
        candidate_commit_oid=accepted.candidate_sha,
        candidate_tree_oid=accepted.candidate_tree_oid,
        candidate_diff_record_digest=accepted.diff_record_digest,
        batch_delivery_receipt_digest="1" * 64,
        batch_delivery_stable_action_id=action.stable_action_id,
        batch_delivery_request_digest="0" * 64,
        batch_delivery_batch_id=delivery.batch_id,
        batch_delivery_batch_sha=delivery.batch_sha,
        batch_delivery_proof_digest=delivery.proof_digest,
        delivery_stable_action_id=delivery.delivery_stable_action_id,
        delivery_request_digest=delivery.delivery_request_digest,
        batch_id=delivery.batch_id,
        batch_sha=delivery.batch_sha,
        delivery_member_ticket_keys=delivery.member_ticket_keys,
        local_check_receipt_digest=delivery.local_check_receipt_digest,
        publication_receipt_digest=delivery.publication_receipt_digest,
        pull_request_number=delivery.pull_request_number,
        pull_request_head_sha=delivery.pull_request_head_sha,
        hosted_result_receipt_digest=delivery.hosted_result_receipt_digest,
        integration_lease_digest=delivery.integration_lease_digest,
        target_branch=delivery.target_branch,
        target_head_sha=delivery.target_head_sha,
        target_readback_digest=delivery.target_readback_digest,
        target_contains_batch_sha=delivery.target_contains_batch_sha,
        pull_request_merge_target_sha=delivery.pull_request_merge_target_sha,
        merge_method=delivery.merge_method,
        result_digest="",
        evidence_digests=accepted.evidence_digests,
    )
    return replace(proof, result_digest=proof.expected_result_digest())


@dataclass
class TamperedDeliveryEffects(OneCandidateOnlyEffects):
    def execute(
        self,
        action: WorkRunAction,
    ) -> WorkRunObservation | StaleBindingObservation | StaleDiagnosisObservation:
        if action.kind != "batch_delivery":
            return super().execute(action)
        candidate = make_candidate_receipt(action)
        accepted = make_accepted_candidate_receipt(action, candidate)
        proof = make_result_integrity_proof(
            action,
            accepted,
            target_contains_batch_sha=False,
        )
        observation = WorkRunObservation(
            phase="completed",
            stable_action_id=action.stable_action_id,
            runtime_binding_id=action.runtime_binding_id,
            receipt_digest="9" * 64,
            candidate_receipt=candidate,
            accepted_candidate_receipt_digest=accepted.digest,
            candidate_diff_record_digest=accepted.diff_record_digest,
            delivery_receipt_digest="1" * 64,
            result_digest=proof.result_digest,
            result_integrity=proof,
        )
        self.observations[action.stable_action_id] = observation
        return observation
~~~

`OneCandidateOnlyEffects` stops at the accepted-Candidate phase, so the Kernel
cannot append `accepted_results`; `TamperedDeliveryEffects` supplies a typed
but false target read-back and relies on `ResultIntegrityProof.validate_for`
to raise `RESULT_INTEGRITY_INVALID`. The test file imports these two concrete
classes from `v8_production_test_support.py`; there is no unnamed fixture or
second receipt type.

- [ ] Step 2: Run RED

~~~powershell
py -3.13 -m pytest "tests/test_v8_execution_kernel_integrity.py::test_observation_serializers_preserve_merged_dataclass_fields" "tests/test_v8_execution_kernel_integrity.py::test_observation_serializers_round_trip_merged_union_members" "tests/test_v8_execution_kernel_integrity.py::test_completed_observation_without_integrity_proof_is_rejected" "tests/test_v8_execution_kernel_integrity.py::test_accepted_candidate_receipt_alone_cannot_create_a_code_result" "tests/test_v8_execution_kernel_integrity.py::test_completed_result_requires_exact_batch_and_target_readback" "tests/test_v8_execution_kernel_integrity.py::test_any_exact_delivery_proof_field_tamper_fails_closed" "tests/test_v8_execution_kernel_integrity.py::test_fallback_result_selects_exact_singleton_proof_and_keeps_parent_receipt" -q
~~~

Expected: FAIL because the merged dataclasses do not yet have the Task 3
serializer methods or strict Result fields, and the current observation accepts
receipt_digest as a fallback Result digest. A replacement partial class would
also fail the exact field/decorator assertions instead of hiding the merged
CandidateReceipt or #113 stale fields.

- [ ] Step 3: Implement the strict proof and delivery action

Extend the existing typing import with `Literal`, and import
`BatchDeliveryAction`, `BatchDeliveryObservation`, `BatchDeliveryRequest`, and
`DeliveryIdentityMismatch` from `gwo_v8.batch_integrator`. Add this Kernel-owned
projection and require it for `phase == "completed"`:

~~~python
import re
from dataclasses import replace
from typing import Literal


@dataclass(frozen=True)
class ResultIntegrityProof:
    accepted_candidate_receipt_digest: str
    candidate_commit_oid: str
    candidate_tree_oid: str
    candidate_diff_record_digest: str
    batch_delivery_receipt_digest: str
    batch_delivery_stable_action_id: str
    batch_delivery_request_digest: str
    batch_delivery_batch_id: str
    batch_delivery_batch_sha: str
    batch_delivery_proof_digest: str
    delivery_stable_action_id: str
    delivery_request_digest: str
    batch_id: str
    batch_sha: str
    delivery_member_ticket_keys: tuple[str, ...]
    local_check_receipt_digest: str
    publication_receipt_digest: str
    pull_request_number: int
    pull_request_head_sha: str
    hosted_result_receipt_digest: str
    integration_lease_digest: str
    target_branch: str
    target_head_sha: str
    target_readback_digest: str
    target_contains_batch_sha: bool
    pull_request_merge_target_sha: str
    merge_method: Literal["merge"]
    result_digest: str
    evidence_digests: tuple[str, ...]

    def canonical(self) -> dict[str, object]:
        return {
            "accepted_candidate_receipt_digest": self.accepted_candidate_receipt_digest,
            "candidate_commit_oid": self.candidate_commit_oid,
            "candidate_tree_oid": self.candidate_tree_oid,
            "candidate_diff_record_digest": self.candidate_diff_record_digest,
            "batch_delivery_receipt_digest": self.batch_delivery_receipt_digest,
            "batch_delivery_stable_action_id": self.batch_delivery_stable_action_id,
            "batch_delivery_request_digest": self.batch_delivery_request_digest,
            "batch_delivery_batch_id": self.batch_delivery_batch_id,
            "batch_delivery_batch_sha": self.batch_delivery_batch_sha,
            "batch_delivery_proof_digest": self.batch_delivery_proof_digest,
            "delivery_stable_action_id": self.delivery_stable_action_id,
            "delivery_request_digest": self.delivery_request_digest,
            "batch_id": self.batch_id,
            "batch_sha": self.batch_sha,
            "delivery_member_ticket_keys": list(self.delivery_member_ticket_keys),
            "local_check_receipt_digest": self.local_check_receipt_digest,
            "publication_receipt_digest": self.publication_receipt_digest,
            "pull_request_number": self.pull_request_number,
            "pull_request_head_sha": self.pull_request_head_sha,
            "hosted_result_receipt_digest": self.hosted_result_receipt_digest,
            "integration_lease_digest": self.integration_lease_digest,
            "target_branch": self.target_branch,
            "target_head_sha": self.target_head_sha,
            "target_readback_digest": self.target_readback_digest,
            "target_contains_batch_sha": self.target_contains_batch_sha,
            "pull_request_merge_target_sha": self.pull_request_merge_target_sha,
            "merge_method": self.merge_method,
            "result_digest": self.result_digest,
            "evidence_digests": list(self.evidence_digests),
        }

    def delivery_proof_body(self) -> dict[str, object]:
        return {
            "delivery_stable_action_id": self.delivery_stable_action_id,
            "delivery_request_digest": self.delivery_request_digest,
            "batch_id": self.batch_id,
            "batch_sha": self.batch_sha,
            "member_ticket_keys": list(self.delivery_member_ticket_keys),
            "local_check_receipt_digest": self.local_check_receipt_digest,
            "publication_receipt_digest": self.publication_receipt_digest,
            "pull_request_number": self.pull_request_number,
            "pull_request_head_sha": self.pull_request_head_sha,
            "hosted_result_receipt_digest": self.hosted_result_receipt_digest,
            "integration_lease_digest": self.integration_lease_digest,
            "target_branch": self.target_branch,
            "target_head_sha": self.target_head_sha,
            "target_readback_digest": self.target_readback_digest,
            "target_contains_batch_sha": self.target_contains_batch_sha,
            "pull_request_merge_target_sha": self.pull_request_merge_target_sha,
            "merge_method": self.merge_method,
        }

    def canonical_without_result_digest(self) -> dict[str, object]:
        value = self.canonical()
        value.pop("result_digest")
        return value

    @classmethod
    def from_batch_observation(
        cls,
        action: BatchDeliveryAction,
        request: BatchDeliveryRequest,
        observation: BatchDeliveryObservation,
        accepted_candidate: AcceptedCandidateReceipt,
    ) -> "ResultIntegrityProof":
        if observation.phase != "complete":
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "only a terminal complete Batch observation can prove a Result",
            )
        try:
            observation.canonical()
        except DeliveryIdentityMismatch as error:
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "Batch observation failed its exact proof-partition receipt",
            ) from error
        if observation.receipt_digest != digest_value(
            {"kind": "batch-observation.v1", **observation.body()}
        ):
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "Batch observation receipt does not cover its exact proof partition",
            )
        if (
            action.stable_action_id != request.stable_action_id
            or action.request_digest != request.request_digest
            or observation.stable_action_id != action.stable_action_id
            or observation.batch_id != action.batch_id
            or observation.batch_sha != action.batch_sha
            or tuple(action.member_ticket_keys)
            != tuple(member.ticket_key for member in observation.members)
        ):
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "Batch parent action, request, or observation identity changed",
            )
        proof_ticket_keys = tuple(
            ticket_key
            for delivery_proof in observation.delivery_proofs
            for ticket_key in delivery_proof.member_ticket_keys
        )
        if (
            proof_ticket_keys != tuple(action.member_ticket_keys)
            or len(set(proof_ticket_keys)) != len(proof_ticket_keys)
            or any(member.status != "integrated" for member in observation.members)
            or len(
                {
                    delivery_proof.delivery_stable_action_id
                    for delivery_proof in observation.delivery_proofs
                }
            )
            != len(observation.delivery_proofs)
            or len(
                {delivery_proof.batch_id for delivery_proof in observation.delivery_proofs}
            )
            != len(observation.delivery_proofs)
            or len(
                {delivery_proof.batch_sha for delivery_proof in observation.delivery_proofs}
            )
            != len(observation.delivery_proofs)
        ):
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "Batch delivery proofs do not partition integrated members exactly",
            )
        request_matches = tuple(
            candidate
            for candidate in request.accepted_candidates
            if candidate.ticket_key == accepted_candidate.ticket_key
        )
        if len(request_matches) != 1 or request_matches[0] != accepted_candidate:
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "accepted Candidate is not the exact member of this Batch request",
            )
        member_matches = tuple(
            member
            for member in observation.members
            if member.ticket_key == accepted_candidate.ticket_key
        )
        if (
            len(member_matches) != 1
            or member_matches[0].work_run_key != accepted_candidate.work_run_key
            or member_matches[0].candidate_sha != accepted_candidate.candidate_sha
            or member_matches[0].status != "integrated"
            or member_matches[0].evidence_digests
            != accepted_candidate.evidence_digests
        ):
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "Batch member readback changed Candidate or Evidence identity",
            )
        selected = tuple(
            delivery_proof
            for delivery_proof in observation.delivery_proofs
            if accepted_candidate.ticket_key in delivery_proof.member_ticket_keys
        )
        if len(selected) != 1:
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "Candidate does not map to exactly one delivery proof",
            )
        delivery_proof = selected[0]
        try:
            delivery_proof.canonical()
        except DeliveryIdentityMismatch as error:
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "selected Batch delivery proof failed canonical validation",
            ) from error
        if observation.fallback_generation == 0:
            if (
                delivery_proof.delivery_stable_action_id != action.stable_action_id
                or delivery_proof.delivery_request_digest != action.request_digest
                or delivery_proof.batch_id != action.batch_id
                or delivery_proof.batch_sha != action.batch_sha
            ):
                raise ExecutionKernelError(
                    "RESULT_INTEGRITY_INVALID",
                    "direct delivery proof differs from the parent Batch action",
                )
        elif observation.fallback_generation == 1:
            if (
                delivery_proof.delivery_stable_action_id == action.stable_action_id
                or delivery_proof.member_ticket_keys
                != (accepted_candidate.ticket_key,)
            ):
                raise ExecutionKernelError(
                    "RESULT_INTEGRITY_INVALID",
                    "fallback Result did not select its exact Singleton proof",
                )
        else:
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "completed Batch has an invalid fallback generation",
            )
        members_by_ticket = {member.ticket_key: member for member in observation.members}
        proof_members = tuple(
            members_by_ticket[ticket_key]
            for ticket_key in delivery_proof.member_ticket_keys
        )
        evidence_digests = tuple(
            sorted(
                digest
                for member in proof_members
                for digest in member.evidence_digests
            )
        )
        if not evidence_digests:
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "selected delivery proof has no canonical member Evidence",
            )
        proof = cls(
            accepted_candidate_receipt_digest=accepted_candidate.digest,
            candidate_commit_oid=accepted_candidate.candidate_sha,
            candidate_tree_oid=accepted_candidate.candidate_tree_oid,
            candidate_diff_record_digest=accepted_candidate.diff_record_digest,
            batch_delivery_receipt_digest=observation.receipt_digest,
            batch_delivery_stable_action_id=observation.stable_action_id,
            batch_delivery_request_digest=action.request_digest,
            batch_delivery_batch_id=observation.batch_id,
            batch_delivery_batch_sha=observation.batch_sha,
            batch_delivery_proof_digest=delivery_proof.proof_digest,
            delivery_stable_action_id=delivery_proof.delivery_stable_action_id,
            delivery_request_digest=delivery_proof.delivery_request_digest,
            batch_id=delivery_proof.batch_id,
            batch_sha=delivery_proof.batch_sha,
            delivery_member_ticket_keys=delivery_proof.member_ticket_keys,
            local_check_receipt_digest=delivery_proof.local_check_receipt_digest,
            publication_receipt_digest=delivery_proof.publication_receipt_digest,
            pull_request_number=delivery_proof.pull_request_number,
            pull_request_head_sha=delivery_proof.pull_request_head_sha,
            hosted_result_receipt_digest=delivery_proof.hosted_result_receipt_digest,
            integration_lease_digest=delivery_proof.integration_lease_digest,
            target_branch=delivery_proof.target_branch,
            target_head_sha=delivery_proof.target_head_sha,
            target_readback_digest=delivery_proof.target_readback_digest,
            target_contains_batch_sha=delivery_proof.target_contains_batch_sha,
            pull_request_merge_target_sha=(
                delivery_proof.pull_request_merge_target_sha
            ),
            merge_method=delivery_proof.merge_method,
            result_digest="",
            evidence_digests=evidence_digests,
        )
        return replace(proof, result_digest=proof.expected_result_digest())

    def expected_result_digest(self) -> str:
        return digest_value(
            {
                "kind": "gwo.result.v1",
                **self.canonical_without_result_digest(),
            }
        )

    def expected_batch_delivery_proof_digest(self) -> str:
        return digest_value(
            {
                "kind": "batch-delivery-proof.v1",
                **self.delivery_proof_body(),
            }
        )

    def validate_for(self, action: WorkRunAction, target_branch: str) -> None:
        digest_pattern = re.compile(r"[0-9a-f]{64}\Z")
        object_pattern = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
        for name, value in self.canonical().items():
            if name.endswith("digest") and (
                not isinstance(value, str) or digest_pattern.fullmatch(value) is None
            ):
                raise ExecutionKernelError(
                    "RESULT_INTEGRITY_INVALID",
                    f"{name} is not a lowercase SHA-256 digest",
                )
        for name in (
            "candidate_commit_oid",
            "candidate_tree_oid",
            "batch_delivery_batch_sha",
            "batch_sha",
            "pull_request_head_sha",
            "target_head_sha",
            "pull_request_merge_target_sha",
        ):
            if object_pattern.fullmatch(getattr(self, name)) is None:
                raise ExecutionKernelError(
                    "RESULT_INTEGRITY_INVALID",
                    f"{name} is not a lowercase Git object identity",
                )
        if (
            not isinstance(self.batch_delivery_stable_action_id, str)
            or not self.batch_delivery_stable_action_id
            or not isinstance(self.delivery_stable_action_id, str)
            or not self.delivery_stable_action_id
            or not isinstance(self.batch_delivery_batch_id, str)
            or not self.batch_delivery_batch_id
            or not isinstance(self.batch_id, str)
            or not self.batch_id
            or digest_pattern.fullmatch(self.batch_delivery_batch_id) is None
            or digest_pattern.fullmatch(self.batch_id) is None
            or type(self.pull_request_number) is not int
            or self.pull_request_number <= 0
            or type(self.delivery_member_ticket_keys) is not tuple
            or not self.delivery_member_ticket_keys
            or any(
                not isinstance(ticket_key, str) or not ticket_key
                for ticket_key in self.delivery_member_ticket_keys
            )
            or len(set(self.delivery_member_ticket_keys))
            != len(self.delivery_member_ticket_keys)
            or action.ticket_key not in self.delivery_member_ticket_keys
        ):
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "Result delivery action, PR, Batch, or member identity is invalid",
            )
        if (
            self.batch_delivery_proof_digest
            != self.expected_batch_delivery_proof_digest()
        ):
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "Result changed a field copied from the Batch delivery proof",
            )
        if self.target_branch != target_branch:
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "Result target branch differs from the active PlanSpec target",
            )
        if self.target_contains_batch_sha is not True:
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "target readback does not prove the Batch SHA",
            )
        if self.pull_request_head_sha != self.batch_sha:
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "pull request head is not the exact Batch SHA",
            )
        if self.pull_request_merge_target_sha != self.target_head_sha:
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "pull request merge target is not the target readback head",
            )
        if self.merge_method != "merge":
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "Result delivery did not use an identity-preserving merge",
            )
        if not self.evidence_digests or tuple(self.evidence_digests) != tuple(
            sorted(self.evidence_digests)
        ):
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "Result Evidence is empty or not canonical",
            )
        if any(digest_pattern.fullmatch(digest) is None for digest in self.evidence_digests):
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "Result Evidence contains a non-SHA-256 digest",
            )
        if self.result_digest != self.expected_result_digest():
            raise ExecutionKernelError(
                "RESULT_INTEGRITY_INVALID",
                "Result digest does not cover the exact delivery proof",
            )
~~~

`from_batch_observation` accepts only `observation.phase == "complete"`, proves
the parent action/request/observation identities, and selects the unique proof
whose member partition contains the Candidate's Ticket. It copies—without
substitution—the CandidateGate Candidate SHA/tree/diff, parent observation
receipt, child-or-direct action/request/Batch identity, local-check receipt,
publication receipt, PR number/head, hosted-result receipt, Integration-Lease
receipt, target branch/head/readback, merge mapping, proof digest, and Evidence.
It derives only `result_digest` after all delivery fields are copied. It never
derives a delivery fact from `request.target`, `action.batch_sha`, or the parent
receipt. A missing, duplicated, reordered, or tampered proof partition raises
`RESULT_INTEGRITY_INVALID`.

`validate_for` requires every digest to be lowercase SHA-256, every Git object
identity to be lowercase SHA-1 or SHA-256, the selected Ticket to belong to
`delivery_member_ticket_keys`, the PR number to be positive, and
`batch_delivery_proof_digest` to recompute from every copied proof field. It
also requires target_branch to equal the active PlanSpec target branch,
target_contains_batch_sha to be True, pull_request_head_sha == batch_sha,
pull_request_merge_target_sha == target_head_sha, merge_method == "merge", and
result_digest == expected_result_digest(). It rejects empty Evidence,
non-SHA-256 Evidence, and noncanonical Evidence ordering. Preserve the merged
`candidate_receipt` field on `WorkRunObservation`, then
append `accepted_candidate_receipt_digest`, `candidate_diff_record_digest`,
`delivery_receipt_digest`, `result_integrity`, and `plan_invalidation` after
the merged #113 `runtime_binding_id` field. `candidate_receipt` is the exact
`CandidateReceipt | None` persisted at `run["candidate_receipt"]` by the shared
foundation. Completed observations require the accepted receipt, diff digest,
delivery receipt, and exact proof; `accepted_awaiting_delivery` requires the
Candidate receipt and diff digest but no Result.

For the durable effect ledger, edit the three already-merged frozen dataclasses
in place. The merged field order is normative: Candidate Assurance appends
`candidate_receipt` to `WorkRunObservation`, then #113 appends
`runtime_binding_id`; #113 owns every field of `StaleBindingObservation` and
`StaleDiagnosisObservation`. Do not add another class statement, subclass and
rebind an owner type, assign module-level functions onto a class, or create an
alias. Those approaches replace or monkeypatch the merged owner instead of
editing its class body.

Extend the existing `from dataclasses import dataclass, replace` import with
`fields`; `Enum` and `Mapping` are already imported. Add only these two helpers
at module scope:

~~~python
def _canonical_value(value: object) -> object:
    if hasattr(value, "canonical") and callable(value.canonical):
        return value.canonical()
    if isinstance(value, Enum):
        return value.value
    if type(value) is tuple:
        return [_canonical_value(item) for item in value]
    if type(value) is list:
        return [_canonical_value(item) for item in value]
    if type(value) is dict:
        return {key: _canonical_value(item) for key, item in sorted(value.items())}
    return value


def _dataclass_fields(value: object) -> dict[str, object]:
    return {
        item.name: _canonical_value(getattr(value, item.name))
        for item in fields(value)
    }
~~~

Inside the existing `@dataclass(frozen=True) class WorkRunObservation` field
block, preserve the merged fields and their order exactly. Immediately after
the merged `runtime_binding_id: str | None = None` field, insert these Task 3
fields; do not repeat `candidate_receipt`, which is already owned by the
Candidate Assurance foundation:

~~~python
    accepted_candidate_receipt_digest: str | None = None
    candidate_diff_record_digest: str | None = None
    delivery_receipt_digest: str | None = None
    result_integrity: ResultIntegrityProof | None = None
    plan_invalidation: PlanInvalidationObservation | None = None
~~~

Inside that same existing `WorkRunObservation` class body, after its complete
merged `__post_init__` and before its existing `running` constructor, insert
these actual methods with the shown indentation:

~~~python
    def canonical(self) -> dict[str, object]:
        return {"kind": "work_run_observation.v1", **_dataclass_fields(self)}

    @classmethod
    def from_canonical(
        cls,
        value: Mapping[str, object],
    ) -> "WorkRunObservation":
        if value.get("kind") != "work_run_observation.v1":
            raise ExecutionKernelError(
                "WORK_RUN_OBSERVATION_INVALID",
                "WorkRun observation kind is not exact",
            )
        data = dict(value)
        data.pop("kind")
        expected = {item.name for item in fields(cls)}
        if set(data) != expected:
            raise ExecutionKernelError(
                "WORK_RUN_OBSERVATION_INVALID",
                "Work Run observation fields are not exact",
            )
        if isinstance(data.get("candidate_receipt"), dict):
            data["candidate_receipt"] = CandidateReceipt.from_canonical(
                data["candidate_receipt"]
            )
        if isinstance(data.get("plan_invalidation"), dict):
            data["plan_invalidation"] = PlanInvalidationObservation.from_canonical(
                data["plan_invalidation"]
            )
        if isinstance(data.get("result_integrity"), dict):
            proof = dict(data["result_integrity"])
            proof["evidence_digests"] = tuple(proof["evidence_digests"])
            proof["delivery_member_ticket_keys"] = tuple(
                proof["delivery_member_ticket_keys"]
            )
            data["result_integrity"] = ResultIntegrityProof(**proof)
        if isinstance(data.get("evidence_digests"), list):
            data["evidence_digests"] = tuple(data["evidence_digests"])
        return cls(**data)
~~~

Inside the existing #113 `@dataclass(frozen=True) class
StaleBindingObservation` body, after its exact eight fields, insert these
methods without changing the decorator or fields:

~~~python
    def canonical(self) -> dict[str, object]:
        return {"kind": "stale_binding_observation.v1", **_dataclass_fields(self)}

    @classmethod
    def from_canonical(
        cls,
        value: Mapping[str, object],
    ) -> "StaleBindingObservation":
        if value.get("kind") != "stale_binding_observation.v1":
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "Stale binding observation kind is not exact",
            )
        data = dict(value)
        data.pop("kind")
        expected = {item.name for item in fields(cls)}
        if set(data) != expected:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "Stale binding observation fields are not exact",
            )
        data["state"] = StaleReadbackState(data["state"])
        return cls(**data)
~~~

Inside the existing #113 `@dataclass(frozen=True) class
StaleDiagnosisObservation` body, after its exact four fields, insert these
methods without changing the decorator or fields:

~~~python
    def canonical(self) -> dict[str, object]:
        return {"kind": "stale_diagnosis_observation.v1", **_dataclass_fields(self)}

    @classmethod
    def from_canonical(
        cls,
        value: Mapping[str, object],
    ) -> "StaleDiagnosisObservation":
        if value.get("kind") != "stale_diagnosis_observation.v1":
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "Stale diagnosis observation kind is not exact",
            )
        data = dict(value)
        data.pop("kind")
        expected = {item.name for item in fields(cls)}
        if set(data) != expected:
            raise ExecutionKernelError(
                "EFFECT_READBACK_INVALID",
                "Stale diagnosis observation fields are not exact",
            )
        data["disposition"] = StaleDiagnosisDisposition(data["disposition"])
        return cls(**data)
~~~

These concrete methods cover the exact dataclass fields (including
`runtime_binding_id`, the #113 stale digests/disposition, CandidateReceipt,
ResultIntegrityProof, and Plan Invalidation observation); they reject unknown
keys and changed nested digests. They are not new types or aliases and remain
inside `execution_kernel.py`.

The full-plan class-statement audit must find no other partial redeclaration of
a merged owner type. After the three declarations above are converted to
in-class insertions, every remaining class statement in this plan is either a
new production-composition type owned here, a Protocol, or a concrete test
support type with its complete body. `WorkRunAction` is likewise extended in
its existing class body; CandidateGate, BatchIntegrator, CampaignWatchdog,
CandidateReceipt, AcceptedCandidateReceipt, and PlanInvalidationObservation
are imported and consumed without redeclaration.

Change WorkRunAction to carry the merged #113
`runtime_binding_id: str | None`, plus `wake_ref: str | None` and
`accepted_candidate_receipt_digest: str | None`. An initial semantic action
has no binding; every later action copies the exact persisted binding. Change
`_effect_action_id` to use:

~~~python
if run["phase"] == "accepted_awaiting_delivery":
    return digest_value({
        "kind": "work-run.batch-delivery.v1",
        "repository": active.handle.repository,
        "campaign_key": active.handle.campaign_key,
        "plan_revision_digest": active.current_revision_digest,
        "ticket_key": ticket_key,
        "work_run_key": run["work_run_key"],
        "accepted_candidate_receipt_digest": run["accepted_candidate_receipt_digest"],
    })
~~~

The Kernel completed branch constructs AcceptedResultBinding only from proof.result_digest, proof.evidence_digests, the current Work Subject digest, and the current target-facts digest. Delete the existing observation.result_digest or observation.receipt_digest fallback. The Kernel persists accepted-Candidate and delivery receipt digests in WorkRunSummary; inspect exposes them without exposing a provider transcript.

When an observation carries plan_invalidation, call the existing _apply_plan_invalidation path before accepting any Candidate or delivery phase. A scope escape therefore persists the exact typed observation, quiesces the Work Run, releases the Slot only after quiescent readback, and cannot continue to Batch delivery.

- [ ] Step 4: Run GREEN and all successor identity tests

~~~powershell
py -3.13 -m pytest "tests/test_v8_execution_kernel_integrity.py" "tests/test_v8_execution_kernel.py" "tests/test_v8_successor_execution_kernel.py" "tests/test_v8_candidate_gate_public.py" -q
~~~

Expected: PASS; a Candidate receipt without an exact accepted Batch/target proof never creates accepted_results, and old-revision proof cannot satisfy a successor action.

- [ ] Step 5: Refactor and commit the integrity slice

~~~powershell
py -3.13 -m pytest "tests/test_v8_execution_kernel_integrity.py" "tests/test_v8_successor_execution_kernel.py" -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add "skills/orchestrator/scripts/gwo_v8/execution_kernel.py" "tests/v8_production_test_support.py" "tests/test_v8_execution_kernel_integrity.py" "skills/orchestrator/.skill-package.json"
git commit -m "feat: require exact delivery proof for Results"
~~~

### Task 4: Implement ProductionWorkRunEffects without bypassing deep modules

**Files:**
- Modify: skills/orchestrator/scripts/gwo_v8/production_effects.py
- Modify: tests/v8_production_test_support.py
- Modify: tests/test_v8_production_effects.py

**Interfaces:**
- Consumes: Task 1 ports, Task 3 `WorkRunAction`/`WorkRunObservation` and
  `ResultIntegrityProof`, RuntimeGateway `progress` plus the #113 stale
  readback union, `CandidateGate.gate_candidate`, and the final
  `BatchIntegrator.prepare/readback/execute` boundary.
- Produces: one readback-first effect ledger and exact mappings for semantic
  execution/resume, Candidate acceptance, Batch preparation/delivery, Runtime
  waits, stale binding diagnosis, and Plan Invalidation.

- [ ] Step 1: Write failing Runtime/Candidate/Batch composition tests

~~~python
from dataclasses import replace


def test_runtime_completion_enters_candidate_gate_not_completed_result(tmp_path, action, support):
    support.runtime.receipt = support.runtime_completed_receipt(action)
    support.candidate.result = support.accepted_candidate_result(action)
    effects = make_production_effects(tmp_path, support)

    observation = effects.execute(action)

    assert observation.phase == "accepted_awaiting_delivery"
    assert (
        observation.accepted_candidate_receipt_digest
        == support.candidate.result.accepted_candidate_receipt.digest
    )
    assert observation.candidate_receipt == support.candidate.result.candidate_receipt
    assert observation.result_integrity is None
    assert support.runtime.calls == [("progress", action.stable_action_id)]
    assert support.candidate.calls == [(action.stable_action_id, "gate_candidate")]
    assert support.batch.prepare_calls == 0


def test_batch_delivery_maps_only_exact_complete_receipt_to_completed(tmp_path, delivery_action, support):
    semantic_action = replace(
        delivery_action,
        stable_action_id="action:109",
        kind="semantic_execution",
        runtime_binding_id=None,
        wake_ref="runtime:completed",
        accepted_candidate_receipt_digest=None,
    )
    support.runtime.receipt = support.runtime_completed_receipt(semantic_action)
    support.candidate.result = support.accepted_candidate_result(semantic_action)
    support.batch.observation = support.complete_batch_observation(delivery_action)
    effects = make_production_effects(tmp_path, support)
    effects.execute(semantic_action)

    observation = effects.execute(delivery_action)

    assert observation.phase == "completed"
    assert observation.result_integrity is not None
    assert observation.result_digest == observation.result_integrity.result_digest
    assert observation.delivery_receipt_digest == support.batch.observation.receipt_digest
    assert len(support.batch.observation.delivery_proofs) == 1
    delivered = support.batch.observation.delivery_proofs[0]
    assert observation.result_integrity.delivery_proof_body() == delivered.body()
    assert (
        observation.result_integrity.batch_delivery_proof_digest
        == delivered.proof_digest
    )
    assert (
        observation.result_integrity.target_head_sha
        != support.batch_requests.request.target.target_head_sha
    )
    assert support.batch.prepare_calls == 1
    assert support.batch.execute_calls == 1


def test_effect_readback_does_not_call_runtime_candidate_or_batch(tmp_path, action, support):
    effects = make_production_effects(tmp_path, support)
    assert effects.readback(action) is None
    assert support.all_calls == []


def test_scope_escape_returns_plan_invalidation_observation_without_delivery(tmp_path, action, support):
    support.runtime.receipt = support.runtime_completed_receipt(action)
    support.candidate.result = support.plan_invalidation_result(action)
    effects = make_production_effects(tmp_path, support)

    observation = effects.execute(action)

    assert observation.phase == "quiescent"
    assert observation.plan_invalidation is not None
    assert support.batch.prepare_calls == 0
    assert support.batch.execute_calls == 0
~~~

Add the exact Task 4 fixtures and factories to
`v8_production_test_support.py`. The fixture owns all port construction, so
the tests do not depend on an unnamed `action`, `delivery_action`, or
`support` object:

~~~python
from dataclasses import dataclass
from pathlib import Path

import pytest

from gwo_v8.batch_integrator import (
    BatchDeliveryAction,
    BatchDeliveryObservation,
    BatchDeliveryProof,
    BatchDeliveryRequest,
    BatchTarget,
    HostedSuiteDefinition,
    LocalSuiteDefinition,
    MemberDeliveryObservation,
)
from gwo_v8.candidate_gate import CandidateGateParent, PlanInvalidationEvidence
from gwo_v8.execution_kernel import WorkRunAction
from gwo_v8.runtime_gateway import (
    CapabilityPolicy,
    CapabilityPolicyProof,
    PlanInvalidationReceipt,
    PlanInvalidationReport,
    RuntimeProgressReceipt,
    WorkRunPurpose,
    WorkRunSubject,
)


def make_test_subject(action: WorkRunAction) -> WorkRunSubject:
    return WorkRunSubject(
        repository=action.repository,
        campaign_key=action.campaign_key,
        campaign_handle=f"{action.repository}:{action.campaign_key}",
        plan_revision_digest=action.plan_revision_digest,
        work_run_key=action.work_run_key or f"work-run:{action.ticket_key}",
        ticket_key=action.ticket_key,
        purpose=WorkRunPurpose.implementation(),
        prompt_artifact_digest="1" * 64,
        authority_subtree_digest="2" * 64,
        stable_action_id=action.stable_action_id,
    )


@dataclass
class ProductionEffectsSupport:
    root: Path

    def __post_init__(self) -> None:
        self.runtime = RecordingRuntimeGateway()
        self.runtime_factory = RecordingRuntimeGatewayFactory(
            store_path=self.root / "runtime.sqlite3",
            provider_command="recording-provider --no-dispatch",
            repository_root=self.root,
            gateway=self.runtime,
        )
        self.runtime_stale = RecordingRuntimeStaleReadback()
        self.subjects = RecordingSubjectSource()
        self.references = RecordingCandidateReferenceReader(
            reference="refs/heads/candidate"
        )
        self.parents = RecordingCandidateParentSource()
        self.candidate = RecordingCandidateGate()
        self.batch_requests = RecordingBatchRequestSource(
            target_path=self.root / "target",
            runtime_factory=self.runtime_factory,
        )
        self.batch = RecordingBatchIntegrator(
            store_path=self.root / "batch.sqlite3",
            target_path=self.root / "target",
        )

    @property
    def all_calls(self) -> list[object]:
        return [
            *self.runtime.calls,
            *self.runtime_stale.calls,
            *self.subjects.calls,
            *self.references.calls,
            *self.candidate.calls,
        ]

    def runtime_completed_receipt(
        self,
        action: WorkRunAction,
    ) -> RuntimeProgressReceipt:
        subject = make_test_subject(action)
        return RuntimeProgressReceipt(
            subject_digest=subject.digest,
            stable_action_id=action.stable_action_id,
            status="completed",
            receipt_digest="3" * 64,
            runtime_binding_id="binding:test",
            output_artifact_digest="4" * 64,
        )

    def accepted_candidate_result(self, action: WorkRunAction) -> CandidateGateResult:
        return accepted_candidate_result(action)

    def complete_batch_observation(
        self,
        action: WorkRunAction,
    ) -> BatchDeliveryObservation:
        candidate = make_candidate_receipt(action)
        accepted = make_accepted_candidate_receipt(action, candidate)
        request = BatchDeliveryRequest(
            stable_action_id=action.stable_action_id,
            repository=action.repository,
            campaign_key=action.campaign_key,
            plan_revision_digest=action.plan_revision_digest,
            target=BatchTarget(
                repository=action.repository,
                target_branch="main",
                target_head_sha="5" * 40,
                target_tree_oid="6" * 40,
                target_facts_digest="7" * 64,
            ),
            accepted_candidates=(accepted,),
            local_suite=LocalSuiteDefinition(
                suite_id="local:production",
                definition_digest="8" * 64,
                command=("py", "-3.13", "-m", "pytest", "-q"),
            ),
            hosted_suites=(
                HostedSuiteDefinition(
                    suite_id="hosted:ci",
                    hosted_name="GWO CI",
                    definition_digest="9" * 64,
                ),
            ),
            writer_generation="v6.1",
            activation_id="activation:test",
        )
        batch_action = BatchDeliveryAction(
            stable_action_id=action.stable_action_id,
            request_digest=request.request_digest,
            batch_id="1" * 64,
            batch_sha=accepted.candidate_sha,
            member_ticket_keys=(accepted.ticket_key,),
        )
        members = (
            MemberDeliveryObservation(
                ticket_key=accepted.ticket_key,
                work_run_key=accepted.work_run_key,
                candidate_sha=accepted.candidate_sha,
                status="integrated",
                evidence_digests=accepted.evidence_digests,
            ),
        )
        delivery_proof = BatchDeliveryProof.create(
            delivery_stable_action_id=batch_action.stable_action_id,
            delivery_request_digest=batch_action.request_digest,
            batch_id=batch_action.batch_id,
            batch_sha=batch_action.batch_sha,
            member_ticket_keys=batch_action.member_ticket_keys,
            local_check_receipt_digest="a" * 64,
            publication_receipt_digest="b" * 64,
            pull_request_number=19,
            pull_request_head_sha=batch_action.batch_sha,
            hosted_result_receipt_digest="c" * 64,
            integration_lease_digest="d" * 64,
            target_branch=request.target.target_branch,
            target_head_sha="e" * 40,
            target_readback_digest="f" * 64,
            target_contains_batch_sha=True,
            pull_request_merge_target_sha="e" * 40,
            merge_method="merge",
        )
        observation_body = {
            "stable_action_id": batch_action.stable_action_id,
            "batch_id": batch_action.batch_id,
            "batch_sha": batch_action.batch_sha,
            "phase": "complete",
            "reason": "target integrated",
            "retry_count": 0,
            "fallback_generation": 0,
            "members": [
                {
                    "ticket_key": member.ticket_key,
                    "work_run_key": member.work_run_key,
                    "candidate_sha": member.candidate_sha,
                    "status": member.status,
                    "evidence_digests": list(member.evidence_digests),
                    "resume_reason": member.resume_reason,
                }
                for member in members
            ],
            "delivery_proofs": [delivery_proof.canonical()],
        }
        observation = BatchDeliveryObservation(
            stable_action_id=batch_action.stable_action_id,
            batch_id=batch_action.batch_id,
            batch_sha=batch_action.batch_sha,
            phase="complete",
            reason="target integrated",
            receipt_digest=digest_value(
                {"kind": "batch-observation.v1", **observation_body}
            ),
            retry_count=0,
            fallback_generation=0,
            members=members,
            delivery_proofs=(delivery_proof,),
        )
        self.batch_requests.request = request
        self.batch.action = batch_action
        self.batch.observation = observation
        return observation

    def plan_invalidation_result(self, action: WorkRunAction) -> CandidateGateResult:
        subject = make_test_subject(action)
        evidence = PlanInvalidationEvidence(
            runtime_subject=subject,
            parent_digest="c" * 64,
            candidate_digest="d" * 64,
            source_kind="scope_audit",
            source_evidence_digest="e" * 64,
            invalidated_obligation="ticket scope",
            required_effects=("read tracker",),
            workspace_identity="workspace:test",
            discovered_facts=("scope=outside",),
            reproduction="deterministic scope escape",
        )
        report = PlanInvalidationReport(
            repository=action.repository,
            campaign_key=action.campaign_key,
            plan_revision_digest=action.plan_revision_digest,
            ticket_key=action.ticket_key,
            work_run_key=action.work_run_key or f"work-run:{action.ticket_key}",
            runtime_binding_id="binding:test",
            authority_subtree_digest="2" * 64,
            reporter_role="worker",
            evidence_digest=evidence.digest,
            dedup_identity="invalidation:test",
            invalidated_obligation="ticket scope",
            required_effects=("read tracker",),
            workspace_identity="workspace:test",
        )
        proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="f" * 64,
        )
        receipt = PlanInvalidationReceipt(
            report_digest=report.digest,
            receipt_digest="0" * 64,
            capability_policy_proof=proof,
            observation={
                "kind": "plan_invalidation_observation.v1",
                "repository": report.repository,
                "campaign_key": report.campaign_key,
                "plan_revision_digest": report.plan_revision_digest,
                "ticket_key": report.ticket_key,
                "work_run_key": report.work_run_key,
                "runtime_binding_id": report.runtime_binding_id,
                "authority_subtree_digest": report.authority_subtree_digest,
                "reporter_role": report.reporter_role,
                "report_digest": report.digest,
                "evidence_digest": report.evidence_digest,
                "dedup_identity": report.dedup_identity,
                "invalidated_obligation": report.invalidated_obligation,
                "required_effects": list(report.required_effects),
                "workspace_identity": report.workspace_identity,
            },
        )
        return CandidateGateResult(
            status=CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
            evidence=(evidence,),
            plan_invalidation_receipt=receipt,
            plan_invalidation_report=report,
        )


@pytest.fixture
def action() -> WorkRunAction:
    return WorkRunAction(
        stable_action_id="action:109",
        repository="owner/repository",
        campaign_key="campaign:successor-kernel",
        plan_revision_digest="a" * 64,
        ticket_key="issue:109",
        kind="semantic_execution",
        semantic_action_id="semantic:109",
        work_run_key="work-run:issue:109",
        work_subject_digest="b" * 64,
        runtime_binding_id=None,
        wake_ref="runtime:initial",
        accepted_candidate_receipt_digest=None,
    )


@pytest.fixture
def delivery_action(action: WorkRunAction) -> WorkRunAction:
    return WorkRunAction(
        stable_action_id="action:109:batch",
        repository=action.repository,
        campaign_key=action.campaign_key,
        plan_revision_digest=action.plan_revision_digest,
        ticket_key=action.ticket_key,
        kind="batch_delivery",
        semantic_action_id=action.semantic_action_id,
        work_run_key=action.work_run_key,
        work_subject_digest=action.work_subject_digest,
        runtime_binding_id="binding:test",
        wake_ref="candidate:accepted",
        accepted_candidate_receipt_digest=make_accepted_candidate_receipt(action).digest,
    )


@pytest.fixture
def support(tmp_path: Path) -> ProductionEffectsSupport:
    return ProductionEffectsSupport(tmp_path)


def make_production_effects(
    tmp_path: Path,
    support: ProductionEffectsSupport,
) -> ProductionWorkRunEffects:
    support.subjects.subject = make_test_subject(
        WorkRunAction(
            stable_action_id="subject:seed",
            repository="owner/repository",
            campaign_key="campaign:successor-kernel",
            plan_revision_digest="a" * 64,
            ticket_key="issue:109",
            kind="semantic_execution",
            semantic_action_id="semantic:109",
            work_run_key="work-run:issue:109",
            work_subject_digest="b" * 64,
            runtime_binding_id=None,
            wake_ref=None,
            accepted_candidate_receipt_digest=None,
        )
    )
    support.parents.parent = CandidateGateParent(
        runtime_subject=support.subjects.subject,
        ticket_contract_digest="c" * 64,
        policy_witness_digest="d" * 64,
        workspace_identity="workspace:test",
    )
    return ProductionWorkRunEffects(
        store_path=tmp_path / "effects.sqlite3",
        runtime_gateways=support.runtime_factory,
        runtime_stale_readbacks=support.runtime_stale,
        work_run_subjects=support.subjects,
        candidate_references=support.references,
        candidate_parents=support.parents,
        candidate_gate=support.candidate,
        batch_requests=support.batch_requests,
        batch_integrator=support.batch,
    )
~~~

The `make_production_effects` body above creates only recording ports and an
SQLite ledger under `tmp_path`; it cannot touch a real repository. Task 4's
production implementation uses the exact `runtime_binding_id`,
`WorkRunObservation.candidate_receipt`, `StaleBindingObservation`, and
`StaleDiagnosisObservation` types from #113 rather than parallel aliases.

- [ ] Step 2: Run RED

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_effects.py::test_runtime_completion_enters_candidate_gate_not_completed_result" "tests/test_v8_production_effects.py::test_batch_delivery_maps_only_exact_complete_receipt_to_completed" -q
~~~

Expected: FAIL because ProductionWorkRunEffects.execute has no runtime-to-Candidate-to-Batch implementation.

- [ ] Step 3: Implement the readback-first effect ledger and semantic path

Create table v8_production_effect_receipts(stable_action_id PRIMARY KEY,
action_json, observation_json, observation_digest,
accepted_candidate_receipt_json). The final column is nullable and stores the
CandidateGate-owned `AcceptedCandidateReceipt` separately from the
`WorkRunObservation`; it is never a Result field and is never reconstructed
from a `CandidateReceipt`. `readback` loads and digest-validates one exact row
and returns the decoded member of the closed `WorkRunEffects` union; it calls
no deep module. `execute` first calls readback, then performs only the effect
for the missing stable action, and saves/read-backs its observation before
returning.

For semantic_execution and semantic_resume, use this exact sequence. The
`CandidateGateParentSource` supplies the existing `CandidateGateParent`; it
does not read Git, validate a Candidate, or manufacture an accepted receipt.

~~~python
subject = self._work_run_subjects.for_action(action)
gateway = self._runtime_gateways.for_campaign(
    CampaignHandle(action.repository, action.campaign_key)
)
runtime = gateway.progress(subject, wake_cursor=action.wake_ref)
self._validate_runtime_receipt(runtime, subject, action)
if runtime.status in {"running", "parked"}:
    return self._record(action, self._observation_from_runtime(runtime))
if runtime.status != "completed" or runtime.output_artifact_digest is None:
    raise ProductionCompositionError(
        "PRODUCTION_RUNTIME_RECEIPT_INVALID",
        "RuntimeGateway did not return a closed running/parked/completed receipt",
    )
reported_reference = self._candidate_references.read(
    runtime.output_artifact_digest,
    subject=subject,
)
parent = self._candidate_parents.for_action(
    action,
    subject,
)
result = self._candidate_gate.gate_candidate(parent, reported_reference)
~~~

Map `CandidateGateResult` exactly: `REVIEW_ACCEPTED` and `REPAIR_ACCEPTED`
require `result.accepted_candidate_receipt`, persist that CandidateGate-owned
receipt in the effect ledger, and return `accepted_awaiting_delivery`; they do
not call BatchIntegrator yet. `PLAN_INVALIDATION_REPORTED` converts its exact
`plan_invalidation_receipt` through
`PlanInvalidationObservation.from_receipt` and returns `quiescent`.
`REPAIR_REQUIRED`, `REPAIR_REJECTED`, and `ORDINARY_REJECTED` retain the exact
CandidateGate Evidence and map to their corresponding private phase without
delivery. Runtime `completed` is never itself a Kernel completed observation.
The exact shared `result.candidate_receipt` is passed through the
`WorkRunObservation.candidate_receipt` field and persisted at
`run["candidate_receipt"]`; composition never rebuilds it.

- [ ] Step 4: Implement exact Batch delivery translation

For a `batch_delivery` action, require the accepted-Candidate receipt digest
persisted by the preceding observation. `BatchRequestSource.for_action` must
construct the final `BatchDeliveryRequest` with the exact `BatchTarget`, local
suite, hosted suites, writer-generation readback, and Activation ID; it must
not read Git/GitHub/CI itself. Call `prepare(request)`, then
`readback(batch_action)` before `execute(batch_action)`.

Use this exact validation boundary:

~~~python
batch_action = self._batch_integrator.prepare(request)
batch_observation = self._batch_integrator.readback(batch_action)
if batch_observation is None:
    batch_observation = self._batch_integrator.execute(batch_action)
if batch_observation.phase == "complete":
    proof = ResultIntegrityProof.from_batch_observation(
        batch_action,
        request,
        batch_observation,
        accepted_candidate,
    )
    proof.validate_for(action, request.target.target_branch)
    observation = WorkRunObservation(
        phase="completed",
        stable_action_id=action.stable_action_id,
        receipt_digest=batch_observation.receipt_digest,
        accepted_candidate_receipt_digest=accepted_candidate.digest,
        delivery_receipt_digest=batch_observation.receipt_digest,
        result_digest=proof.result_digest,
        evidence_digests=proof.evidence_digests,
        result_integrity=proof,
    )
else:
    observation = self._observation_from_batch(batch_observation, action)
return self._record(action, observation)
~~~

The effect ledger records the final `BatchDeliveryObservation` before returning.
`decision` from `DeliveryIdentityMismatch` or `DeliveryAttributionAmbiguous` is
preserved by BatchIntegrator; it never triggers Singleton fallback here. A
crash after RuntimeGateway, CandidateGate, or BatchIntegrator has durably
recorded its stable action but before Kernel state save is recovered by effect
ledger or owner readback; no second provider, Candidate receipt, Batch action,
or delivery identity is created.

Use these complete private ledger bodies in `ProductionWorkRunEffects`; the
Task 3 union types expose the exact `canonical()` and `from_canonical()`
methods used below, with tags `work_run_observation.v1`,
`stale_binding_observation.v1`, and `stale_diagnosis_observation.v1`. The
methods are concrete and are not Protocol declarations:

~~~python
def _action_json(self, action: WorkRunAction) -> str:
    return json.dumps(
        {
            "stable_action_id": action.stable_action_id,
            "repository": action.repository,
            "campaign_key": action.campaign_key,
            "plan_revision_digest": action.plan_revision_digest,
            "ticket_key": action.ticket_key,
            "kind": action.kind,
            "semantic_action_id": action.semantic_action_id,
            "work_run_key": action.work_run_key,
            "work_subject_digest": action.work_subject_digest,
            "runtime_binding_id": action.runtime_binding_id,
            "wake_ref": action.wake_ref,
            "accepted_candidate_receipt_digest": action.accepted_candidate_receipt_digest,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_effect_observation(
    self,
    payload: dict[str, object],
) -> WorkRunEffectObservation:
    kind = payload.get("kind")
    if kind == "work_run_observation.v1":
        return WorkRunObservation.from_canonical(payload)
    if kind == "stale_binding_observation.v1":
        return StaleBindingObservation.from_canonical(payload)
    if kind == "stale_diagnosis_observation.v1":
        return StaleDiagnosisObservation.from_canonical(payload)
    raise ProductionCompositionError(
        "EFFECT_READBACK_INVALID",
        "effect ledger row has no exact member of the #113 closed union",
    )


def _record(
    self,
    action: WorkRunAction,
    observation: WorkRunEffectObservation,
    *,
    accepted_candidate: AcceptedCandidateReceipt | None = None,
) -> WorkRunEffectObservation:
    action_json = self._action_json(action)
    observation_json = json.dumps(
        observation.canonical(),
        separators=(",", ":"),
        sort_keys=True,
    )
    observation_digest = digest_bytes(observation_json.encode("utf-8"))
    accepted_json = (
        None
        if accepted_candidate is None
        else json.dumps(
            accepted_candidate.canonical(),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    with sqlite3.connect(self._store_path) as connection:
        connection.execute(
            """
            INSERT INTO v8_production_effect_receipts(
                stable_action_id, action_json, observation_json, observation_digest,
                accepted_candidate_receipt_json
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(stable_action_id) DO NOTHING
            """,
            (
                action.stable_action_id,
                action_json,
                observation_json,
                observation_digest,
                accepted_json,
            ),
        )
    saved = self.readback(action)
    if saved is None:
        raise ProductionCompositionError(
            "EFFECT_READBACK_INVALID",
            "effect observation disappeared after its durable insert",
        )
    return saved


def readback(
    self,
    action: WorkRunAction,
) -> WorkRunEffectObservation | None:
    if type(action) is not WorkRunAction or not action.stable_action_id:
        raise ProductionCompositionError(
            "PRODUCTION_EFFECT_ACTION_INVALID",
            "readback requires one exact non-empty WorkRunAction identity",
        )
    expected_action_json = self._action_json(action)
    with sqlite3.connect(self._store_path) as connection:
        row = connection.execute(
            """
            SELECT action_json, observation_json, observation_digest,
                   accepted_candidate_receipt_json
              FROM v8_production_effect_receipts
             WHERE stable_action_id = ?
            """,
            (action.stable_action_id,),
        ).fetchone()
    if row is None:
        return None
    action_json, observation_json, observation_digest, _accepted_json = row
    if action_json != expected_action_json:
        raise ProductionCompositionError(
            "EFFECT_READBACK_INVALID",
            "effect ledger action identity changed for the stable action",
        )
    if digest_bytes(observation_json.encode("utf-8")) != observation_digest:
        raise ProductionCompositionError(
            "EFFECT_READBACK_INVALID",
            "effect ledger observation digest changed",
        )
    try:
        payload = json.loads(observation_json)
    except json.JSONDecodeError as error:
        raise ProductionCompositionError(
            "EFFECT_READBACK_INVALID",
            "effect ledger observation is not JSON",
        ) from error
    if type(payload) is not dict:
        raise ProductionCompositionError(
            "EFFECT_READBACK_INVALID",
            "effect ledger observation is not a JSON object",
        )
    return self._decode_effect_observation(payload)


def execute(
    self,
    action: WorkRunAction,
) -> WorkRunEffectObservation:
    cached = self.readback(action)
    if cached is not None:
        return cached
    if action.kind in {"stale_readback", "stale_diagnosis"}:
        observation = self._runtime_stale_readbacks.read_stale(action)
        accepted_candidate = None
    elif action.kind in {"semantic_execution", "semantic_resume"}:
        observation, accepted_candidate = self._execute_semantic(action)
    elif action.kind == "batch_delivery":
        observation = self._execute_batch(action)
        accepted_candidate = None
    else:
        raise ProductionCompositionError(
            "PRODUCTION_EFFECT_ACTION_INVALID",
            f"unsupported WorkRunAction kind: {action.kind}",
        )
    return self._record(
        action,
        observation,
        accepted_candidate=accepted_candidate,
    )
~~~

The Task 3 canonical methods reject unknown keys, changed nested Candidate or
Result proof digests, cross-kind stale records, and any changed
`runtime_binding_id` before `_decode_effect_observation` returns. The ledger
uses `ON CONFLICT DO NOTHING` plus exact read-back, so a restart cannot replace
an existing effect with a different observation.

Use these complete dispatcher bodies for the two non-stale action kinds. They
are the only methods allowed to call the merged Runtime/Candidate/Batch ports:

~~~python
def _execute_semantic(
    self,
    action: WorkRunAction,
) -> tuple[WorkRunObservation, AcceptedCandidateReceipt | None]:
    subject = self._work_run_subjects.for_action(action)
    gateway = self._runtime_gateways.for_campaign(
        CampaignHandle(action.repository, action.campaign_key)
    )
    runtime = gateway.progress(subject, wake_cursor=action.wake_ref)
    self._validate_runtime_receipt(runtime, subject, action)
    if runtime.status in {"running", "parked"}:
        return self._observation_from_runtime(runtime, action), None
    if runtime.status != "completed" or runtime.output_artifact_digest is None:
        raise ProductionCompositionError(
            "PRODUCTION_RUNTIME_RECEIPT_INVALID",
            "RuntimeGateway did not return a closed running/parked/completed receipt",
        )
    reported_reference = self._candidate_references.read(
        runtime.output_artifact_digest,
        subject=subject,
    )
    parent = self._candidate_parents.for_action(action, subject)
    result = self._candidate_gate.gate_candidate(parent, reported_reference)
    if result.status in {
        CandidateGateStatus.REVIEW_ACCEPTED,
        CandidateGateStatus.REPAIR_ACCEPTED,
    }:
        candidate = result.candidate_receipt
        accepted = result.accepted_candidate_receipt
        if candidate is None or accepted is None:
            raise ProductionCompositionError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "an accepted CandidateGateResult lacks both exact receipt values",
            )
        return WorkRunObservation(
            phase="accepted_awaiting_delivery",
            stable_action_id=action.stable_action_id,
            runtime_binding_id=self._runtime_binding_id(runtime),
            receipt_digest=candidate.digest,
            candidate_receipt=candidate,
            accepted_candidate_receipt_digest=accepted.digest,
            candidate_diff_record_digest=accepted.diff_record_digest,
            result_digest=None,
            result_integrity=None,
        ), accepted
    if result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED:
        receipt = result.plan_invalidation_receipt
        if receipt is None:
            raise ProductionCompositionError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "Plan Invalidation result lacks its authoritative receipt",
            )
        invalidation = PlanInvalidationObservation.from_receipt(receipt)
        return WorkRunObservation(
            phase="quiescent",
            stable_action_id=action.stable_action_id,
            runtime_binding_id=self._runtime_binding_id(runtime),
            receipt_digest=invalidation.digest,
            plan_invalidation=invalidation,
        ), None
    phase = {
        CandidateGateStatus.REPAIR_REQUIRED: "repair",
        CandidateGateStatus.REPAIR_REJECTED: "decision",
        CandidateGateStatus.ORDINARY_REJECTED: "decision",
    }.get(result.status)
    if phase is None:
        raise ProductionCompositionError(
            "CANDIDATE_GATE_READBACK_INVALID",
            f"unknown CandidateGateResult status: {result.status}",
        )
    evidence_digests = tuple(
        sorted(
            digest
            for item in result.evidence
            for digest in (getattr(item, "digest", None),)
            if digest is not None
        )
    )
    return WorkRunObservation(
        phase=phase,
        stable_action_id=action.stable_action_id,
        runtime_binding_id=self._runtime_binding_id(runtime),
        receipt_digest=digest_value(
            {"action": action.stable_action_id, "status": result.status.value}
        ),
        evidence_digests=evidence_digests,
    ), None


def _execute_batch(
    self,
    action: WorkRunAction,
) -> WorkRunObservation:
    accepted_digest = action.accepted_candidate_receipt_digest
    if not accepted_digest:
        raise ProductionCompositionError(
            "BATCH_ACCEPTED_RECEIPT_MISSING",
            "batch delivery requires the exact accepted-Candidate digest",
        )
    accepted_candidate = self._read_accepted_candidate_receipt(action, accepted_digest)
    subject = self._work_run_subjects.for_action(action)
    request = self._batch_requests.for_action(action, subject, (accepted_candidate,))
    batch_action = self._batch_integrator.prepare(request)
    batch_observation = self._batch_integrator.readback(batch_action)
    if batch_observation is None:
        batch_observation = self._batch_integrator.execute(batch_action)
    if batch_observation.phase == "complete":
        proof = ResultIntegrityProof.from_batch_observation(
            batch_action,
            request,
            batch_observation,
            accepted_candidate,
        )
        proof.validate_for(action, request.target.target_branch)
        return WorkRunObservation(
            phase="completed",
            stable_action_id=action.stable_action_id,
            runtime_binding_id=action.runtime_binding_id,
            receipt_digest=batch_observation.receipt_digest,
            candidate_receipt=self._read_candidate_receipt(action),
            accepted_candidate_receipt_digest=accepted_candidate.digest,
            candidate_diff_record_digest=accepted_candidate.diff_record_digest,
            delivery_receipt_digest=batch_observation.receipt_digest,
            result_digest=proof.result_digest,
            evidence_digests=proof.evidence_digests,
            result_integrity=proof,
        )
    return self._observation_from_batch(batch_observation, action)
~~~

Use these concrete bodies for every private helper called above:

~~~python
def _read_accepted_candidate_receipt(
    self,
    action: WorkRunAction,
    expected_digest: str,
) -> AcceptedCandidateReceipt:
    with sqlite3.connect(self._store_path) as connection:
        rows = connection.execute(
            """
            SELECT action_json, accepted_candidate_receipt_json
              FROM v8_production_effect_receipts
             WHERE accepted_candidate_receipt_json IS NOT NULL
            """
        ).fetchall()
    for action_json, receipt_json in rows:
        recorded_action = json.loads(action_json)
        if (
            recorded_action.get("repository") != action.repository
            or recorded_action.get("campaign_key") != action.campaign_key
            or recorded_action.get("plan_revision_digest") != action.plan_revision_digest
            or recorded_action.get("ticket_key") != action.ticket_key
            or recorded_action.get("work_run_key") != action.work_run_key
        ):
            continue
        receipt = self._accepted_receipt_from_canonical(json.loads(receipt_json))
        if receipt.digest != expected_digest:
            raise ProductionCompositionError(
                "EFFECT_READBACK_INVALID",
                "stored AcceptedCandidateReceipt digest differs from the action",
            )
        return receipt
    raise ProductionCompositionError(
        "BATCH_ACCEPTED_RECEIPT_MISSING",
        "no stored CandidateGate-owned AcceptedCandidateReceipt matches the batch",
    )


def _accepted_receipt_from_canonical(
    self,
    value: dict[str, object],
) -> AcceptedCandidateReceipt:
    expected = {
        "kind",
        "repository",
        "campaign_key",
        "plan_revision_digest",
        "target_branch",
        "ticket_key",
        "work_run_key",
        "integration_node_key",
        "accepted_sequence",
        "base_sha",
        "base_tree_oid",
        "candidate_sha",
        "candidate_tree_oid",
        "candidate_receipt_digest",
        "diff_schema_version",
        "diff_record_digest",
        "authority_subtree_digest",
        "policy_witness_digest",
        "review_subject_digest",
        "assurance",
        "assurance_requirement_digest",
        "check_environment_digest",
        "delivery_identity_digest",
        "interaction_keys",
        "protected_surfaces",
        "gitlink_change",
        "evidence_digests",
        "review_finding_ledger_digest",
        "receipt_digest",
    }
    if type(value) is not dict or set(value) != expected:
        raise ProductionCompositionError(
            "EFFECT_READBACK_INVALID",
            "stored AcceptedCandidateReceipt has an unknown field set",
        )
    interaction_keys = tuple(
        InteractionKey(
            item["namespace"],
            item["value"],
            InteractionClassification(item["classification"]),
        )
        for item in value["interaction_keys"]
    )
    payload = dict(value)
    payload.pop("kind")
    stored_digest = payload.pop("receipt_digest")
    payload["interaction_keys"] = interaction_keys
    payload["protected_surfaces"] = tuple(payload["protected_surfaces"])
    payload["evidence_digests"] = tuple(payload["evidence_digests"])
    receipt = AcceptedCandidateReceipt(**payload)
    if receipt.digest != stored_digest:
        raise ProductionCompositionError(
            "EFFECT_READBACK_INVALID",
            "stored AcceptedCandidateReceipt digest changed",
        )
    return receipt


def _read_candidate_receipt(self, action: WorkRunAction) -> CandidateReceipt:
    with sqlite3.connect(self._store_path) as connection:
        rows = connection.execute(
            """
            SELECT action_json, observation_json
              FROM v8_production_effect_receipts
            """
        ).fetchall()
    for action_json, observation_json in rows:
        recorded_action = json.loads(action_json)
        if (
            recorded_action.get("repository") == action.repository
            and recorded_action.get("campaign_key") == action.campaign_key
            and recorded_action.get("plan_revision_digest") == action.plan_revision_digest
            and recorded_action.get("ticket_key") == action.ticket_key
            and recorded_action.get("work_run_key") == action.work_run_key
        ):
            payload = json.loads(observation_json)
            observation = WorkRunObservation.from_canonical(payload)
            if observation.candidate_receipt is not None:
                return observation.candidate_receipt
    raise ProductionCompositionError(
        "CANDIDATE_RECEIPT_MISSING",
        "batch delivery has no exact persisted shared CandidateReceipt",
    )


def _validate_runtime_receipt(
    self,
    runtime: RuntimeProgressReceipt,
    subject: WorkRunSubject,
    action: WorkRunAction,
) -> None:
    if (
        type(runtime) is not RuntimeProgressReceipt
        or runtime.subject_digest != subject.digest
        or runtime.stable_action_id != action.stable_action_id
        or type(runtime.receipt_digest) is not str
        or re.fullmatch(r"[0-9a-f]{64}", runtime.receipt_digest) is None
    ):
        raise ProductionCompositionError(
            "PRODUCTION_RUNTIME_RECEIPT_INVALID",
            "RuntimeProgressReceipt is not bound to the exact subject/action",
        )


def _runtime_binding_id(
    self,
    runtime: RuntimeProgressReceipt,
) -> str:
    binding_id = getattr(runtime, "runtime_binding_id", None)
    if type(binding_id) is not str or not binding_id:
        raise ProductionCompositionError(
            "PRODUCTION_RUNTIME_RECEIPT_INVALID",
            "trusted Runtime readback omitted runtime_binding_id",
        )
    return binding_id


def _observation_from_runtime(
    self,
    runtime: RuntimeProgressReceipt,
    action: WorkRunAction,
) -> WorkRunObservation:
    phase = {
        "running": "running",
        "parked": "parked",
    }.get(runtime.status)
    if phase is None:
        raise ProductionCompositionError(
            "PRODUCTION_RUNTIME_RECEIPT_INVALID",
            "non-terminal Runtime status is outside the closed mapping",
        )
    return WorkRunObservation(
        phase=phase,
        stable_action_id=action.stable_action_id,
        runtime_binding_id=self._runtime_binding_id(runtime),
        receipt_digest=runtime.receipt_digest,
        next_check_at=getattr(runtime, "next_check_at", None),
    )


def _observation_from_batch(
    self,
    batch_observation: BatchDeliveryObservation,
    action: WorkRunAction,
) -> WorkRunObservation:
    phase = {
        "running": "wait",
        "wait": "wait",
        "decision": "decision",
        "blocked": "blocked",
    }.get(batch_observation.phase)
    if phase is None:
        raise ProductionCompositionError(
            "BATCH_READBACK_INVALID",
            "Batch phase is not a non-terminal closed mapping",
        )
    return WorkRunObservation(
        phase=phase,
        stable_action_id=action.stable_action_id,
        runtime_binding_id=action.runtime_binding_id,
        receipt_digest=batch_observation.receipt_digest,
        reason=batch_observation.reason,
        evidence_digests=tuple(
            sorted(
                digest
                for member in batch_observation.members
                for digest in member.evidence_digests
            )
        ),
    )
~~~

These bodies load only the exact canonical receipt already persisted in the
effect ledger or the exact Batch observation, validate Campaign/Revision/Work
Run/binding identity, and raise `ProductionCompositionError` on a mismatch.
They never call Git, GitHub, CI, a provider, or a second deep module. The
Task 4 implementation must not use an unbound helper name or a generic
receipt-to-Result fallback.

For #113 stale actions, call only `RuntimeStaleReadbackPort.read_stale(action)`
and return the exact `StaleBindingObservation` or `StaleDiagnosisObservation`.
Reject a cross-kind, changed `runtime_binding_id`, changed stable action, or
noncanonical receipt as `EFFECT_READBACK_INVALID` before recording it. The
normal WorkRun observation path and the two stale observation types are one
closed `WorkRunEffects` union, not three independent effect APIs.

- [ ] Step 5: Run GREEN and restart/idempotency tests

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_effects.py" "tests/test_v8_execution_kernel_integrity.py" -q
~~~

Expected: PASS; one stable Runtime action produces at most one Runtime call and
one CandidateGate-owned receipt, while one stable Batch action produces at most
one Batch delivery identity across repeated reads and process reconstruction.

- [ ] Step 6: Refactor and commit the production effect adapter

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_effects.py" -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add "skills/orchestrator/scripts/gwo_v8/production_effects.py" "tests/v8_production_test_support.py" "tests/test_v8_production_effects.py" "skills/orchestrator/.skill-package.json"
git commit -m "feat: compose Runtime Candidate and Batch effects"
~~~

### Task 5: Add async planning continuation and ProductionGwoHost

**Files:**
- Modify: skills/orchestrator/scripts/gwo_v8/plan_control_host.py
- Create: skills/orchestrator/scripts/gwo_v8/production_host.py
- Create: tests/test_v8_production_host.py
- Modify: tests/v8_production_test_support.py

**Interfaces:**
- Consumes: ProductionPlanControlStartHost.start, start_successor, read_active, the final PlanControl repository attempt readback, Task 2 Kernel, Task 4 effects, and ExecutionKernelConfiguration.
- Produces: host-private PlanningContinuation, ProductionPlanControlStartHost.runtime_gateway_for, read_planning_continuation, continue_start, and ProductionGwoHost.start/advance/inspect/run_watchdog_once.

Freeze these exact host-private seams:

~~~python
from pathlib import Path
from typing import Literal, Protocol, Sequence

@dataclass(frozen=True)
class PlanningContinuation:
    campaign: CampaignHandle
    ready_refs: tuple[str, ...]
    expected_previous_revision_digest: str | None
    snapshot_artifact_digest: str
    planning_request_artifact_digest: str
    stable_action_id: str
    compilation_record_artifact_digest: str | None

class ProductionPlanControlStartHost:
    def runtime_gateway_for(self, handle: CampaignHandle) -> RuntimeGateway:
        gateway = self._runtime_gateway_factory.for_campaign(handle)
        if not isinstance(gateway, RuntimeGateway):
            raise ProductionCompositionError(
                "PLAN_CONTROL_RUNTIME_GATEWAY_INVALID",
                "the host Runtime factory returned no exact RuntimeGateway",
            )
        return gateway

    def read_planning_continuation(
        self,
        handle: CampaignHandle,
    ) -> PlanningContinuation | None:
        record = self._planning_continuation_store.read(handle)
        if record is None:
            return None
        if type(record) is not PlanningContinuation or record.campaign != handle:
            raise ProductionCompositionError(
                "PLANNING_CONTINUATION_INVALID",
                "the persisted planning continuation has the wrong Campaign identity",
            )
        return record

    def continue_start(
        self,
        handle: CampaignHandle,
        ready_refs: Sequence[str],
    ) -> CampaignHandle:
        continuation = self.read_planning_continuation(handle)
        refs = tuple(ready_refs)
        if continuation is None or refs != continuation.ready_refs:
            raise ProductionCompositionError(
                "PLANNING_CONTINUATION_MISMATCH",
                "a wake must resume the exact persisted ready-ref tuple",
            )
        return self._plan_control.start(
            handle.repository,
            refs,
            campaign_key=handle.campaign_key,
        )

    def read_active_or_none(
        self,
        handle: CampaignHandle,
    ) -> ActivePlanReadback | None:
        if self.read_planning_continuation(handle) is not None:
            return None
        return self.read_active(handle)

@dataclass(frozen=True)
class ProductionHostConfiguration:
    worker_slots: int = 4
    batch_member_limit: int = 4
    preview_mode: Literal["beta2_isolated_preview"] = "beta2_isolated_preview"
    target_isolation_root: Path | None = None
    writer_activation_enabled: Literal[False] = False

class WriterGenerationReader(Protocol):
    def read(self) -> str: ...

class ProductionGwoHost:
    def __init__(
        self,
        *,
        start_host: ProductionPlanControlStartHost,
        kernel: ExecutionKernel,
        watchdog: CampaignWatchdog,
        writer_generation_reader: WriterGenerationReader,
        target_path: Path,
    ) -> None:
        self._start_host = start_host
        self._kernel = kernel
        self._watchdog = watchdog
        self._writer_generation_reader = writer_generation_reader
        self._target_path = target_path.resolve()

    @classmethod
    def install(
        cls,
        *,
        start_host: ProductionPlanControlStartHost,
        store_path: Path,
        effects: ProductionWorkRunEffects,
        configuration: ExecutionKernelConfiguration | None,
        host_configuration: ProductionHostConfiguration,
        target_path: Path,
        watchdog_store_path: Path,
        watchdog: CampaignWatchdog,
        writer_generation_reader: WriterGenerationReader,
    ) -> "ProductionGwoHost":
        root = host_configuration.target_isolation_root
        target = target_path.resolve()
        if (
            host_configuration.preview_mode != "beta2_isolated_preview"
            or host_configuration.writer_activation_enabled is not False
            or root is None
            or target == root.resolve()
            or root.resolve() not in target.parents
        ):
            raise ProductionCompositionError(
                "V8_ISOLATED_PREVIEW_REQUIRED",
                "target is not an isolated Beta2 target",
            )
        writer_generation_reader.read()
        kernel = start_host.install_execution_kernel(
            store_path=store_path,
            effects=effects,
            configuration=configuration,
        )
        return cls(
            start_host=start_host,
            kernel=kernel,
            watchdog=watchdog,
            writer_generation_reader=writer_generation_reader,
            target_path=target,
        )

    def start(
        self,
        repository: str,
        ready_refs: Sequence[str],
        options: object = None,
    ) -> CampaignHandle:
        return self._start_host.start(repository, tuple(ready_refs), options)

    def advance(
        self,
        campaign_handle: CampaignHandle,
        wake_ref: str | None = None,
    ) -> CampaignOutcome:
        continuation = self._start_host.read_planning_continuation(campaign_handle)
        if (
            continuation is not None
            and self._start_host.read_active_or_none(campaign_handle) is None
        ):
            if wake_ref is None:
                return CampaignOutcome(
                    CampaignStatus.WAIT,
                    "PlanningContinuationPending",
                )
            self._start_host.continue_start(campaign_handle, continuation.ready_refs)
        return self._kernel.advance(campaign_handle, wake_ref)

    def inspect(self, campaign_handle: CampaignHandle) -> Diagnostics:
        continuation = self._start_host.read_planning_continuation(campaign_handle)
        if (
            continuation is not None
            and self._start_host.read_active_or_none(campaign_handle) is None
        ):
            return Diagnostics(
                campaign=campaign_handle,
                status=CampaignStatus.WAIT,
                reason="PlanningContinuationPending",
                plan_revision_digest="",
                work_runs=(),
                outstanding_effect_ids=(continuation.stable_action_id,),
            )
        return self._kernel.inspect(campaign_handle)

    def watchdog_snapshot(
        self,
        campaign_handle: CampaignHandle,
    ) -> WatchdogCampaignSnapshot:
        return self._kernel.watchdog_snapshot(campaign_handle)

    def run_watchdog_once(self, now: str) -> tuple[CampaignOutcome, ...]:
        return self._watchdog.run_once(now)
~~~

- [ ] Step 1: Write failing continuation and host-boundary tests

~~~python
def test_pending_planning_is_not_polled_by_advance_without_a_wake(tmp_path, planning_host):
    handle = planning_host.start("owner/repository", ("issue:108",))
    before = planning_host.planning_gateway_calls()
    outcome = planning_host.advance(handle)
    after = planning_host.planning_gateway_calls()
    assert outcome == CampaignOutcome(
        CampaignStatus.WAIT,
        "PlanningContinuationPending",
    )
    assert after == before


def test_wake_continues_the_same_persisted_planning_action_after_restart(
    tmp_path,
    planning_host,
):
    handle = planning_host.start("owner/repository", ("issue:108",))
    continuation = planning_host.start_host.read_planning_continuation(handle)
    assert continuation is not None
    restarted = reinstall_production_host(tmp_path, planning_host)
    restarted.advance(handle, wake_ref="runtime:planning:41")
    assert restarted.planning_action_ids() == [continuation.stable_action_id]
    assert restarted.planning_pass_count() == 1


def test_pending_planning_inspect_is_read_only(tmp_path, planning_host):
    handle = planning_host.start("owner/repository", ("issue:108",))
    before = planning_host.store_bytes()
    diagnostics = planning_host.inspect(handle)
    assert diagnostics.status is CampaignStatus.WAIT
    assert diagnostics.reason == "PlanningContinuationPending"
    assert diagnostics.work_runs == ()
    assert planning_host.store_bytes() == before


def test_normal_real_repository_stays_on_v61_authority(tmp_path, planning_host):
    arguments = planning_host.install_arguments()
    arguments["host_configuration"] = ProductionHostConfiguration(
        target_isolation_root=tmp_path,
        writer_activation_enabled=False,
    )
    arguments["target_path"] = Path("D:/Workstation/github-work-orchestrator")
    with pytest.raises(ProductionCompositionError) as raised:
        ProductionGwoHost.install(**arguments)
    assert raised.value.code == "V8_ISOLATED_PREVIEW_REQUIRED"
~~~

Define the planning fixture and every helper used by the tests in
`v8_production_test_support.py`:

~~~python
import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from gwo_v8.execution_kernel import (
    CampaignOutcome,
    Diagnostics,
    ExecutionKernel,
    ExecutionKernelConfiguration,
    WorkRunEffects,
)
from gwo_v8.plan_control import ActivePlanReadback, CampaignHandle
from gwo_v8.production_host import (
    PlanningContinuation,
    ProductionGwoHost,
    ProductionHostConfiguration,
)


@dataclass(frozen=True)
class PlanningWriterGenerationReader:
    generation: str = "v6.1"

    def read(self) -> str:
        return self.generation


@dataclass
class DelayedPlanningStartHost:
    root: Path

    def __post_init__(self) -> None:
        self._continuations: dict[CampaignHandle, PlanningContinuation] = {}
        self._active: dict[CampaignHandle, ActivePlanReadback] = {}
        self._planning_action_ids: list[str] = []
        self._planning_passes = 0
        self._planning_store = self.root / "planning-continuation.json"
        self._planning_gateway_calls = 0

    def start(
        self,
        repository: str,
        ready_refs: tuple[str, ...],
        options: object = None,
    ) -> CampaignHandle:
        handle = CampaignHandle(repository, "campaign:successor-kernel")
        continuation = PlanningContinuation(
            campaign=handle,
            ready_refs=tuple(ready_refs),
            expected_previous_revision_digest=None,
            snapshot_artifact_digest="1" * 64,
            planning_request_artifact_digest="2" * 64,
            stable_action_id="planning:campaign:planning",
            compilation_record_artifact_digest=None,
        )
        self._continuations[handle] = continuation
        self._planning_store.write_text(
            json.dumps(
                {
                    "campaign": {
                        "repository": handle.repository,
                        "campaign_key": handle.campaign_key,
                    },
                    "ready_refs": list(continuation.ready_refs),
                    "stable_action_id": continuation.stable_action_id,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return handle

    def read_planning_continuation(
        self,
        handle: CampaignHandle,
    ) -> PlanningContinuation | None:
        return self._continuations.get(handle)

    def read_active_or_none(
        self,
        handle: CampaignHandle,
    ) -> ActivePlanReadback | None:
        return self._active.get(handle)

    def read_active(self, handle: CampaignHandle) -> ActivePlanReadback:
        active = self._active.get(handle)
        if active is None:
            raise AssertionError("active Plan was read before planning continuation")
        return active

    def continue_start(
        self,
        handle: CampaignHandle,
        ready_refs: tuple[str, ...],
    ) -> CampaignHandle:
        continuation = self._continuations[handle]
        if tuple(ready_refs) != continuation.ready_refs:
            raise AssertionError("planning wake changed ready refs")
        self._planning_gateway_calls += 1
        self._planning_passes += 1
        self._planning_action_ids.append(continuation.stable_action_id)
        active, _old_handle = _minimal_active_campaign(("issue:109",))
        active = replace(
            active,
            activation_receipt=replace(
                active.activation_receipt,
                ready_refs=continuation.ready_refs,
                ticket_keys=continuation.ready_refs,
            ),
        )
        self._active[handle] = active
        self._continuations.pop(handle)
        return handle

    def install_execution_kernel(
        self,
        *,
        store_path: Path,
        effects: WorkRunEffects,
        configuration: ExecutionKernelConfiguration | None,
    ) -> ExecutionKernel:
        return ExecutionKernel(
            store_path=store_path,
            plan_control=self,
            effects=effects,
            configuration=configuration,
        )

    def planning_gateway_calls(self) -> int:
        return self._planning_gateway_calls

    def planning_action_ids(self) -> list[str]:
        return list(self._planning_action_ids)

    def planning_pass_count(self) -> int:
        return self._planning_passes


@dataclass
class PlanningWatchdog:
    def run_once(self, now: str) -> tuple[CampaignOutcome, ...]:
        return ()


@dataclass
class PlanningHostFixture:
    root: Path
    start_host: DelayedPlanningStartHost
    host: ProductionGwoHost
    arguments: dict[str, object]

    def start(self, repository: str, ready_refs: tuple[str, ...]) -> CampaignHandle:
        return self.host.start(repository, ready_refs)

    def advance(
        self,
        handle: CampaignHandle,
        wake_ref: str | None = None,
    ) -> CampaignOutcome:
        return self.host.advance(handle, wake_ref)

    def inspect(self, handle: CampaignHandle) -> Diagnostics:
        return self.host.inspect(handle)

    def planning_gateway_calls(self) -> int:
        return self.start_host.planning_gateway_calls()

    def planning_action_ids(self) -> list[str]:
        return self.start_host.planning_action_ids()

    def planning_pass_count(self) -> int:
        return self.start_host.planning_pass_count()

    def store_bytes(self) -> bytes:
        return Path(self.arguments["store_path"]).read_bytes()

    def install_arguments(self) -> dict[str, object]:
        return dict(self.arguments)

    def reinstall(self, root: Path) -> "PlanningHostFixture":
        start_host = self.start_host
        arguments = dict(self.arguments)
        arguments["start_host"] = start_host
        host = ProductionGwoHost.install(**arguments)
        return PlanningHostFixture(root, start_host, host, arguments)


def make_pending_planning_host(root: Path) -> PlanningHostFixture:
    target = root / "isolated-target"
    target.mkdir(parents=True, exist_ok=True)
    start_host = DelayedPlanningStartHost(root)
    store_path = root / "execution-kernel.sqlite3"
    effects = NoopRunningEffects()
    configuration = ProductionHostConfiguration(
        preview_mode="beta2_isolated_preview",
        target_isolation_root=root,
        writer_activation_enabled=False,
    )
    arguments: dict[str, object] = {
        "start_host": start_host,
        "store_path": store_path,
        "effects": effects,
        "configuration": None,
        "host_configuration": configuration,
        "target_path": target,
        "watchdog_store_path": root / "watchdog.sqlite3",
        "watchdog": PlanningWatchdog(),
        "writer_generation_reader": PlanningWriterGenerationReader(),
    }
    host = ProductionGwoHost.install(**arguments)
    return PlanningHostFixture(root, start_host, host, arguments)


@pytest.fixture
def planning_host(tmp_path: Path) -> PlanningHostFixture:
    return make_pending_planning_host(tmp_path)


def reinstall_production_host(
    root: Path,
    planning_host: PlanningHostFixture,
) -> PlanningHostFixture:
    return planning_host.reinstall(root)
~~~

`planning_host`, `reinstall_production_host`, `planning_action_ids`,
`planning_pass_count`, and `store_bytes` now have copyable bodies. The delayed
planning double records exactly one stable Planning action, never starts a
provider, and shares only the durable test store during reconstruction.

- [ ] Step 2: Run RED

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_host.py::test_pending_planning_is_not_polled_by_advance_without_a_wake" "tests/test_v8_production_host.py::test_wake_continues_the_same_persisted_planning_action_after_restart" -q
~~~

Expected: FAIL because no host-private continuation readback or ProductionGwoHost exists and the current caller cannot distinguish a pending Planning attempt from an active Plan Revision.

- [ ] Step 3: Implement the minimum continuation seams

read_planning_continuation reads the exact durable _PlanningAttempt through the configured repository, projects only the fields in PlanningContinuation, validates the Campaign/ready-ref/action/artifact identities, and performs no save. continue_start canonicalizes ready_refs, requires exact equality with the persisted continuation, and invokes the same PlanControl.start(handle.repository, refs, campaign_key=handle.campaign_key) recovery path. It never calls a second Planning API.

runtime_gateway_for reuses the existing host configuration, shared ArtifactStore, and same gateway_store_path; it returns a RuntimeGateway instance for the exact Campaign without starting a Runtime action. It must not expose provider or CLI facts to PlanControl or the Kernel.

ProductionGwoHost.advance follows this exact order:

~~~python
continuation = self._start_host.read_planning_continuation(campaign_handle)
if (
    continuation is not None
    and self._start_host.read_active_or_none(campaign_handle) is None
):
    if wake_ref is None:
        return CampaignOutcome(
            CampaignStatus.WAIT,
            "PlanningContinuationPending",
        )
    self._start_host.continue_start(
        campaign_handle,
        continuation.ready_refs,
    )
    if self._start_host.read_active_or_none(campaign_handle) is None:
        return CampaignOutcome(
            CampaignStatus.WAIT,
            "PlanningContinuationPending",
        )
return self._kernel.advance(campaign_handle, wake_ref)
~~~

read_active_or_none is a host-private read-only helper with exact signature read_active_or_none(handle: CampaignHandle) -> ActivePlanReadback | None; it returns None only for an explicitly pending initial Planning attempt and re-raises all malformed/stale authority errors. ProductionGwoHost.inspect projects a pending Planning attempt as Diagnostics(status=Wait, reason="PlanningContinuationPending", plan_revision_digest="", work_runs=(), outstanding_effect_ids=(stable_action_id,)); the empty revision field is explicitly marked by the reason and does not claim an active Plan Revision. Once an Activation Receipt exists, inspect delegates only to ExecutionKernel.inspect.

`ProductionGwoHost.install` must fail closed before installing the Kernel when
`host_configuration.preview_mode != "beta2_isolated_preview"`,
`host_configuration.writer_activation_enabled is not False`,
`target_isolation_root is None`, or `target_path.resolve()` is equal to or
outside `target_isolation_root.resolve()`. Raise
`ProductionCompositionError("V8_ISOLATED_PREVIEW_REQUIRED", "target is not an isolated Beta2 target")` for those
cases. A successful Beta2 install reads `writer_generation_reader.read()` for
evidence only, keeps V6.1 as the authoritative writer, and never writes an
Activation record or changes the writer generation. This admission check is
independent of Skill text; a normal real repository therefore remains on V6.1
until #118's Guard and #119's real root Canary authorize the later transfer.

- [ ] Step 4: Run GREEN and writer non-mutation tests

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_host.py" "tests/test_v8_plancontrol_production.py" "tests/test_v8_successor_host.py" -q
~~~

Expected: PASS; pending Planning waits without a wake, a wake re-enters one stable Planning action, restart does not create a second Planning Pass, the isolation fence rejects a normal real repository, and start/advance/inspect do not call writer activation.

- [ ] Step 5: Refactor and commit the host continuation slice

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_host.py" -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add "skills/orchestrator/scripts/gwo_v8/plan_control_host.py" "skills/orchestrator/scripts/gwo_v8/production_host.py" "tests/v8_production_test_support.py" "tests/test_v8_production_host.py" "skills/orchestrator/.skill-package.json"
git commit -m "feat: compose the V8 production host"
~~~

### Task 6: Revalidate Issue #137 after the approved OPEN checkpoint

**Files:**
- Create: tests/test_v8_production_replanning.py
- Modify: tests/v8_production_test_support.py
- Test: tests/test_v8_production_replanning.py

**Interfaces:**
- Consumes: merged #114/#115 CandidateGate/Repair Verification receipts, Task 3 Kernel Plan Invalidation handoff, #134–#136 bounded replanning, the selected `reopen_path`, recorded owner approval, and the OPEN #137 tracker body/comments.
- Produces: no new replanning authority; only public-seam evidence that CandidateGate routes the four required cases into the existing #133–#136 path.

This task is not allowed to run before the human checkpoint in the scope
section. It accepts either the already-OPEN Beta1 path or the post-merge
manual-reopen path; it must not reopen or close GitHub itself.

- [ ] Step 1: Write failing public-seam revalidation tests

~~~python
def test_reopened_137_deterministic_scope_invalidation_uses_zero_reviewer_calls(
    tmp_path,
    reopened_137_host,
):
    reopened_137_host.submit_candidate(
        "issue:109",
        "refs/heads/candidate",
    )
    reopened_137_host.advance(
        reopened_137_host.handle,
        wake_ref="candidate:137:deterministic",
    )
    result = reopened_137_host.result_for("issue:109")
    run = reopened_137_host.run_for("issue:109")
    assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    assert reopened_137_host.reviewer_calls == 0
    assert run.phase == "quiescent"
    assert run.slot_held is False
    evidence = next(
        item for item in result.evidence if isinstance(item, PlanInvalidationEvidence)
    )
    assert run.plan_invalidation.source_evidence_digests == evidence.source_evidence_digests


def test_reopened_137_formal_review_scope_escape_keeps_complete_evidence_lineage(
    tmp_path,
    reopened_137_host,
):
    reopened_137_host.submit_formal_review_scope_escape("issue:109")
    reopened_137_host.advance(
        reopened_137_host.handle,
        "candidate:137:review",
    )
    result = reopened_137_host.result_for("issue:109")
    run = reopened_137_host.run_for("issue:109")
    assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    assert result.repair_packet is None
    assert reopened_137_host.formal_review_calls == 1
    evidence = next(
        item for item in result.evidence if isinstance(item, PlanInvalidationEvidence)
    )
    assert run.plan_invalidation.source_evidence_digests == evidence.source_evidence_digests


def test_reopened_137_repair_scope_escape_does_not_reopen_exploratory_review(
    tmp_path,
    reopened_137_host,
):
    reopened_137_host.submit_repair_scope_escape("issue:109")
    reopened_137_host.advance(
        reopened_137_host.handle,
        "candidate:137:repair",
    )
    result = reopened_137_host.result_for("issue:109")
    assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    assert reopened_137_host.formal_review_calls == 0
    assert reopened_137_host.repair_verification_calls == 1
    assert reopened_137_host.run_for("issue:109").phase == "quiescent"


def test_reopened_137_ordinary_unauthorized_candidate_never_enters_campaign_replanning(
    tmp_path,
    reopened_137_host,
):
    before = reopened_137_host.inspect(reopened_137_host.handle)
    reopened_137_host.submit_ordinary_unauthorized_candidate("issue:109")
    reopened_137_host.advance(
        reopened_137_host.handle,
        "candidate:137:ordinary",
    )
    result = reopened_137_host.result_for("issue:109")
    after = reopened_137_host.inspect(reopened_137_host.handle)
    assert result.status is CandidateGateStatus.ORDINARY_REJECTED
    assert after.plan_revision_digest == before.plan_revision_digest
    assert after.invalidation_classification is None


def test_reopened_137_restart_replay_is_idempotent_and_preserves_unaffected_work(
    tmp_path,
    reopened_137_host,
):
    reopened_137_host.submit_candidate(
        "issue:109",
        "refs/heads/candidate",
    )
    first = reopened_137_host.advance(
        reopened_137_host.handle,
        "candidate:137:replay",
    )
    receipt = reopened_137_host.result_for(
        "issue:109"
    ).plan_invalidation_receipt
    assert receipt is not None
    unaffected_before = reopened_137_host.run_for("issue:108")
    restarted = reopened_137_host.restart()
    second = restarted.advance(
        restarted.handle,
        "candidate:137:replay",
    )
    assert first == second
    assert restarted.reporter_calls == 1
    assert restarted.run_for("issue:108") == unaffected_before
    assert (
        restarted.run_for("issue:109").plan_invalidation.report_digest
        == receipt.report_digest
    )
~~~

Define the fixture and its construction path in
`v8_production_test_support.py` as follows. The fake RuntimeGateway returns a
bound receipt for the exact subject it receives; the merged CandidateGate
double creates only the existing `CandidateGateResult` and counts each
review/repair/report call; the Batch port fails if the scope-escape path tries
to deliver:

~~~python
@dataclass
class ReopenedRuntimeGateway:
    calls: list[str] = field(default_factory=list)

    def progress(
        self,
        subject: WorkRunSubject,
        *,
        wake_cursor: str | None,
    ) -> RuntimeProgressReceipt:
        self.calls.append(subject.stable_action_id)
        return RuntimeProgressReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            status="completed",
            receipt_digest=digest_value(
                {"subject": subject.digest, "wake": wake_cursor}
            ),
            runtime_binding_id=f"binding:{subject.ticket_key}",
            output_artifact_digest="4" * 64,
        )


@dataclass
class ReopenedCandidateGate:
    support_root: Path
    mode: str = "ordinary"
    reviewer_calls: int = 0
    formal_review_calls: int = 0
    repair_verification_calls: int = 0
    reporter_calls: int = 0

    def _action(self, subject: WorkRunSubject) -> WorkRunAction:
        return WorkRunAction(
            stable_action_id=subject.stable_action_id,
            repository=subject.repository,
            campaign_key=subject.campaign_key,
            plan_revision_digest=subject.plan_revision_digest,
            ticket_key=subject.ticket_key,
            kind="semantic_execution",
            semantic_action_id=subject.stable_action_id,
            work_run_key=subject.work_run_key,
            work_subject_digest=subject.digest,
            runtime_binding_id=None,
            wake_ref=None,
            accepted_candidate_receipt_digest=None,
        )

    def _result(self, subject: WorkRunSubject) -> CandidateGateResult:
        action = self._action(subject)
        if self.mode == "ordinary":
            return CandidateGateResult(
                status=CandidateGateStatus.ORDINARY_REJECTED,
                evidence=(),
            )
        support = ProductionEffectsSupport(self.support_root)
        result = support.plan_invalidation_result(action)
        self.reporter_calls += 1
        return result

    def gate_candidate(
        self,
        parent: CandidateGateParent,
        reported_reference: str,
    ) -> CandidateGateResult:
        self.reviewer_calls += 1
        return self._result(parent.runtime_subject)

    def verify_repair(
        self,
        parent: CandidateGateParent,
        packet: RepairPacket,
        candidate: CandidateIdentity,
    ) -> CandidateGateResult:
        self.repair_verification_calls += 1
        return self._result(parent.runtime_subject)

    def replay_plan_invalidation(
        self,
        parent: CandidateGateParent,
        evidence: PlanInvalidationEvidence,
        report: PlanInvalidationReport,
    ) -> CandidateGateResult:
        self.reporter_calls += 1
        return self._result(parent.runtime_subject)


@dataclass
class ReopenedSubjectSource:
    def for_action(self, action: WorkRunAction) -> WorkRunSubject:
        return make_test_subject(action)


@dataclass
class ReopenedParentSource:
    def for_action(
        self,
        action: WorkRunAction,
        subject: WorkRunSubject,
    ) -> CandidateGateParent:
        return CandidateGateParent(
            runtime_subject=subject,
            ticket_contract_digest="c" * 64,
            policy_witness_digest="d" * 64,
            workspace_identity=f"workspace:{action.ticket_key}",
        )


@dataclass
class ReopenedReferenceReader:
    def read(self, output_artifact_digest: str, *, subject: WorkRunSubject) -> str:
        return "refs/heads/candidate"


class ReopenedNoDeliveryBatch:
    def prepare(self, request: BatchDeliveryRequest) -> BatchDeliveryAction:
        raise AssertionError("scope-escape revalidation must not prepare a Batch")

    def readback(self, action: BatchDeliveryAction) -> BatchDeliveryObservation | None:
        raise AssertionError("scope-escape revalidation must not read a Batch")

    def execute(self, action: BatchDeliveryAction) -> BatchDeliveryObservation:
        raise AssertionError("scope-escape revalidation must not execute a Batch")


@dataclass
class ReopenedStartHost:
    active: ActivePlanReadback

    def start(
        self,
        repository: str,
        ready_refs: tuple[str, ...],
        options: object = None,
    ) -> CampaignHandle:
        return self.active.handle

    def read_planning_continuation(self, handle: CampaignHandle) -> None:
        return None

    def read_active_or_none(self, handle: CampaignHandle) -> ActivePlanReadback:
        if handle != self.active.handle:
            raise AssertionError(handle)
        return self.active

    def read_active(self, handle: CampaignHandle) -> ActivePlanReadback:
        return self.read_active_or_none(handle)

    def install_execution_kernel(
        self,
        *,
        store_path: Path,
        effects: WorkRunEffects,
        configuration: ExecutionKernelConfiguration | None,
    ) -> ExecutionKernel:
        return ExecutionKernel(
            store_path=store_path,
            plan_control=self,
            effects=effects,
            configuration=configuration,
        )


@dataclass
class ReopenedWatchdog:
    def run_once(self, now: str) -> tuple[CampaignOutcome, ...]:
        return ()


@dataclass
class Reopened137HostFixture:
    host: ProductionGwoHost
    handle: CampaignHandle
    start_host: ReopenedStartHost
    gate: ReopenedCandidateGate
    arguments: dict[str, object]

    @property
    def reviewer_calls(self) -> int:
        return self.gate.reviewer_calls

    @property
    def formal_review_calls(self) -> int:
        return self.gate.formal_review_calls

    @property
    def repair_verification_calls(self) -> int:
        return self.gate.repair_verification_calls

    @property
    def reporter_calls(self) -> int:
        return self.gate.reporter_calls

    def _preview_action(self, ticket_key: str) -> WorkRunAction:
        return WorkRunAction(
            stable_action_id=f"candidate:137:{ticket_key}",
            repository=self.handle.repository,
            campaign_key=self.handle.campaign_key,
            plan_revision_digest=self.start_host.active.current_revision_digest,
            ticket_key=ticket_key,
            kind="semantic_execution",
            semantic_action_id=f"semantic:{ticket_key}",
            work_run_key=f"work-run:{ticket_key}",
            work_subject_digest="b" * 64,
            runtime_binding_id=None,
            wake_ref=None,
            accepted_candidate_receipt_digest=None,
        )

    def _submit(self, mode: str, ticket_key: str) -> CandidateGateResult:
        self.gate.mode = mode
        action = self._preview_action(ticket_key)
        if mode == "ordinary":
            return CandidateGateResult(
                status=CandidateGateStatus.ORDINARY_REJECTED,
                evidence=(),
            )
        return ProductionEffectsSupport(
            Path(self.arguments["store_path"]).parent
        ).plan_invalidation_result(action)

    def submit_candidate(
        self,
        ticket_key: str,
        reported_reference: str,
    ) -> CandidateGateResult:
        return self._submit("deterministic", ticket_key)

    def submit_formal_review_scope_escape(self, ticket_key: str) -> CandidateGateResult:
        self.gate.formal_review_calls += 1
        return self._submit("formal_review", ticket_key)

    def submit_repair_scope_escape(self, ticket_key: str) -> CandidateGateResult:
        self.gate.repair_verification_calls += 1
        return self._submit("repair", ticket_key)

    def submit_ordinary_unauthorized_candidate(self, ticket_key: str) -> CandidateGateResult:
        return self._submit("ordinary", ticket_key)

    def advance(self, handle: CampaignHandle, wake_ref: str | None = None) -> CampaignOutcome:
        return self.host.advance(handle, wake_ref)

    def inspect(self, handle: CampaignHandle) -> Diagnostics:
        return self.host.inspect(handle)

    def restart(self) -> "Reopened137HostFixture":
        arguments = dict(self.arguments)
        arguments["start_host"] = self.start_host
        host = ProductionGwoHost.install(**arguments)
        return Reopened137HostFixture(host, self.handle, self.start_host, self.gate, arguments)


def make_reopened_137_host(root: Path) -> Reopened137HostFixture:
    active, handle = _minimal_active_campaign(("issue:108", "issue:109"))
    start_host = ReopenedStartHost(active)
    gate = ReopenedCandidateGate(root)
    runtime = ReopenedRuntimeGateway()
    runtime_factory = RecordingRuntimeGatewayFactory(
        store_path=root / "runtime.sqlite3",
        provider_command="recording-provider --no-dispatch",
        repository_root=root,
        gateway=runtime,
    )
    subjects = ReopenedSubjectSource()
    parents = ReopenedParentSource()
    effects = ProductionWorkRunEffects(
        store_path=root / "production-effects.sqlite3",
        runtime_gateways=runtime_factory,
        runtime_stale_readbacks=RecordingRuntimeStaleReadback(),
        work_run_subjects=subjects,
        candidate_references=ReopenedReferenceReader(),
        candidate_parents=parents,
        candidate_gate=gate,
        batch_requests=RecordingBatchRequestSource(
            target_path=root,
            runtime_factory=runtime_factory,
        ),
        batch_integrator=ReopenedNoDeliveryBatch(),
    )
    target = root / "target"
    target.mkdir(parents=True, exist_ok=True)
    configuration = ProductionHostConfiguration(
        preview_mode="beta2_isolated_preview",
        target_isolation_root=root,
        writer_activation_enabled=False,
    )
    arguments: dict[str, object] = {
        "start_host": start_host,
        "store_path": root / "execution.sqlite3",
        "effects": effects,
        "configuration": None,
        "host_configuration": configuration,
        "target_path": target,
        "watchdog_store_path": root / "watchdog.sqlite3",
        "watchdog": ReopenedWatchdog(),
        "writer_generation_reader": PlanningWriterGenerationReader(),
    }
    host = ProductionGwoHost.install(**arguments)
    return Reopened137HostFixture(host, handle, start_host, gate, arguments)


@pytest.fixture
def reopened_137_host(tmp_path: Path) -> Reopened137HostFixture:
    return make_reopened_137_host(tmp_path)
~~~

`reopened_137_host` now has a concrete construction body. It installs
ProductionGwoHost, the merged CandidateGate/Repair double returning the
existing `CandidateGateResult`, and the Task 3 Kernel. It exposes only the
public methods used by the tests and never mutates GitHub.

- [ ] Step 2: Run RED only after #114/#115 merge and the approved #137 OPEN checkpoint

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_replanning.py" -q
~~~

Expected before this plan routing is complete: FAIL at the missing production handoff or missing exact receipt lineage, never at a missing GitHub mutation.

- [ ] Step 3: Implement the minimum test-only composition wiring

Replace the provisional `ReopenedRuntimeGateway`, `ReopenedCandidateGate`,
`Reopened137HostFixture`, and construction functions from Step 1 with these
complete test-support bodies. Every queued case is consumed by the existing
`CandidateGatePort` method, and every result is built only after the Gate sees
the real `CandidateGateParent.runtime_subject`; no preview action or duplicate
Candidate receipt type is used:

~~~python
@dataclass
class ReopenedRuntimeGateway:
    calls: list[str] = field(default_factory=list)

    def progress(
        self,
        subject: WorkRunSubject,
        *,
        wake_cursor: str | None,
    ) -> RuntimeProgressReceipt:
        self.calls.append(subject.stable_action_id)
        return RuntimeProgressReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            status="completed",
            receipt_digest=digest_value(
                {
                    "kind": "reopened-137-runtime.v1",
                    "subject_digest": subject.digest,
                    "wake_cursor": wake_cursor,
                }
            ),
            runtime_binding_id="binding:test",
            output_artifact_digest="4" * 64,
        )


def _reopened_invalidation_receipt(
    report: PlanInvalidationReport,
) -> PlanInvalidationReceipt:
    proof = CapabilityPolicyProof(
        capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
        authority_record_digest="f" * 64,
    )
    observation = {
        "kind": "plan_invalidation_observation.v1",
        "repository": report.repository,
        "campaign_key": report.campaign_key,
        "plan_revision_digest": report.plan_revision_digest,
        "ticket_key": report.ticket_key,
        "work_run_key": report.work_run_key,
        "runtime_binding_id": report.runtime_binding_id,
        "authority_subtree_digest": report.authority_subtree_digest,
        "reporter_role": report.reporter_role,
        "report_digest": report.digest,
        "evidence_digest": report.evidence_digest,
        "dedup_identity": report.dedup_identity,
        "invalidated_obligation": report.invalidated_obligation,
        "required_effects": list(report.required_effects),
        "workspace_identity": report.workspace_identity,
    }
    return PlanInvalidationReceipt(
        report_digest=report.digest,
        receipt_digest=digest_value(observation),
        capability_policy_proof=proof,
        observation=observation,
    )


def make_reopened_plan_invalidation_result(
    subject: WorkRunSubject,
    *,
    source_kind: str,
    reproduction: str,
) -> CandidateGateResult:
    if source_kind not in {
        "scope_audit",
        "formal_review",
        "repair_verification",
    }:
        raise AssertionError(f"unsupported reopened #137 source: {source_kind}")
    source_digest = digest_value(
        {
            "kind": "reopened-137-source.v1",
            "ticket_key": subject.ticket_key,
            "source_kind": source_kind,
            "reproduction": reproduction,
        }
    )
    evidence = PlanInvalidationEvidence(
        runtime_subject=subject,
        parent_digest="c" * 64,
        candidate_digest="d" * 64,
        source_kind=source_kind,
        source_evidence_digest=source_digest,
        invalidated_obligation="ticket scope",
        required_effects=("read tracker",),
        workspace_identity=f"workspace:{subject.ticket_key}",
        discovered_facts=("scope=outside",),
        reproduction=reproduction,
    )
    report = PlanInvalidationReport(
        repository=subject.repository,
        campaign_key=subject.campaign_key,
        plan_revision_digest=subject.plan_revision_digest,
        ticket_key=subject.ticket_key,
        work_run_key=subject.work_run_key,
        runtime_binding_id="binding:test",
        authority_subtree_digest=subject.authority_subtree_digest,
        reporter_role="worker",
        evidence_digest=evidence.digest,
        dedup_identity=(
            f"reopened:137:{subject.ticket_key}:{source_kind}"
        ),
        invalidated_obligation=evidence.invalidated_obligation,
        required_effects=evidence.required_effects,
        workspace_identity=evidence.workspace_identity,
    )
    return CandidateGateResult(
        status=CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
        evidence=(evidence,),
        plan_invalidation_receipt=_reopened_invalidation_receipt(report),
        plan_invalidation_report=report,
    )


@dataclass
class ReopenedCandidateGate:
    queued_cases: dict[str, tuple[str, str]] = field(default_factory=dict)
    results: dict[str, CandidateGateResult] = field(default_factory=dict)
    reviewer_calls: int = 0
    formal_review_calls: int = 0
    repair_verification_calls: int = 0
    reporter_calls: int = 0

    def queue(
        self,
        ticket_key: str,
        *,
        mode: str,
        reported_reference: str = "refs/heads/candidate",
    ) -> None:
        if mode not in {
            "deterministic",
            "formal_review",
            "repair",
            "ordinary",
        }:
            raise AssertionError(f"unsupported reopened #137 mode: {mode}")
        if ticket_key in self.queued_cases:
            raise AssertionError(f"case already queued for {ticket_key}")
        self.queued_cases[ticket_key] = (mode, reported_reference)

    def _invalidation(
        self,
        subject: WorkRunSubject,
        *,
        source_kind: str,
        reproduction: str,
    ) -> CandidateGateResult:
        self.reporter_calls += 1
        return make_reopened_plan_invalidation_result(
            subject,
            source_kind=source_kind,
            reproduction=reproduction,
        )

    def _record(
        self,
        ticket_key: str,
        result: CandidateGateResult,
    ) -> CandidateGateResult:
        existing = self.results.get(ticket_key)
        if existing is not None and existing != result:
            raise AssertionError(f"changed CandidateGate result for {ticket_key}")
        self.results[ticket_key] = result
        return result

    def gate_candidate(
        self,
        parent: CandidateGateParent,
        reported_reference: str,
    ) -> CandidateGateResult:
        ticket_key = parent.runtime_subject.ticket_key
        mode, expected_reference = self.queued_cases.pop(
            ticket_key,
            ("ordinary", reported_reference),
        )
        if reported_reference != expected_reference:
            raise AssertionError(
                f"candidate reference changed for {ticket_key}: "
                f"{reported_reference!r}"
            )
        if mode == "ordinary":
            result = CandidateGateResult(
                status=CandidateGateStatus.ORDINARY_REJECTED,
                evidence=(),
            )
        elif mode == "deterministic":
            result = self._invalidation(
                parent.runtime_subject,
                source_kind="scope_audit",
                reproduction="deterministic scope escape",
            )
        elif mode == "formal_review":
            self.reviewer_calls += 1
            self.formal_review_calls += 1
            result = self._invalidation(
                parent.runtime_subject,
                source_kind="formal_review",
                reproduction="formal Review scope escape",
            )
        else:
            self.repair_verification_calls += 1
            result = self._invalidation(
                parent.runtime_subject,
                source_kind="repair_verification",
                reproduction="bounded Repair Verification scope escape",
            )
        return self._record(ticket_key, result)

    def verify_repair(
        self,
        parent: CandidateGateParent,
        packet: RepairPacket,
        candidate: CandidateIdentity,
    ) -> CandidateGateResult:
        self.repair_verification_calls += 1
        result = self._invalidation(
            parent.runtime_subject,
            source_kind="repair_verification",
            reproduction=(
                "bounded Repair Verification scope escape for "
                f"{packet.digest}:{candidate.digest}"
            ),
        )
        return self._record(parent.runtime_subject.ticket_key, result)

    def replay_plan_invalidation(
        self,
        parent: CandidateGateParent,
        evidence: PlanInvalidationEvidence,
        report: PlanInvalidationReport,
    ) -> CandidateGateResult:
        if evidence.runtime_subject.digest != parent.runtime_subject.digest:
            raise AssertionError("replayed invalidation changed Runtime Subject")
        if report.evidence_digest != evidence.digest:
            raise AssertionError("replayed invalidation changed Evidence lineage")
        self.reporter_calls += 1
        result = CandidateGateResult(
            status=CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
            evidence=(evidence,),
            plan_invalidation_receipt=_reopened_invalidation_receipt(report),
            plan_invalidation_report=report,
        )
        return self._record(parent.runtime_subject.ticket_key, result)

    def result_for(self, ticket_key: str) -> CandidateGateResult:
        try:
            return self.results[ticket_key]
        except KeyError as error:
            raise AssertionError(
                f"CandidateGate has no result for {ticket_key}"
            ) from error


def make_reopened_137_effects(
    root: Path,
    gate: ReopenedCandidateGate,
) -> ProductionWorkRunEffects:
    runtime_factory = RecordingRuntimeGatewayFactory(
        store_path=root / "runtime.sqlite3",
        provider_command="recording-provider --no-dispatch",
        repository_root=root,
        gateway=ReopenedRuntimeGateway(),
    )
    return ProductionWorkRunEffects(
        store_path=root / "production-effects.sqlite3",
        runtime_gateways=runtime_factory,
        runtime_stale_readbacks=RecordingRuntimeStaleReadback(),
        work_run_subjects=ReopenedSubjectSource(),
        candidate_references=ReopenedReferenceReader(),
        candidate_parents=ReopenedParentSource(),
        candidate_gate=gate,
        batch_requests=RecordingBatchRequestSource(
            target_path=root / "target",
            runtime_factory=runtime_factory,
        ),
        batch_integrator=ReopenedNoDeliveryBatch(),
    )


@dataclass
class Reopened137HostFixture:
    host: ProductionGwoHost
    handle: CampaignHandle
    start_host: ReopenedStartHost
    gate: ReopenedCandidateGate
    root: Path

    @property
    def reviewer_calls(self) -> int:
        return self.gate.reviewer_calls

    @property
    def formal_review_calls(self) -> int:
        return self.gate.formal_review_calls

    @property
    def repair_verification_calls(self) -> int:
        return self.gate.repair_verification_calls

    @property
    def reporter_calls(self) -> int:
        return self.gate.reporter_calls

    def submit_candidate(
        self,
        ticket_key: str,
        reported_reference: str,
    ) -> None:
        self.gate.queue(
            ticket_key,
            mode="deterministic",
            reported_reference=reported_reference,
        )

    def submit_formal_review_scope_escape(self, ticket_key: str) -> None:
        self.gate.queue(ticket_key, mode="formal_review")

    def submit_repair_scope_escape(self, ticket_key: str) -> None:
        self.gate.queue(ticket_key, mode="repair")

    def submit_ordinary_unauthorized_candidate(self, ticket_key: str) -> None:
        self.gate.queue(ticket_key, mode="ordinary")

    def result_for(self, ticket_key: str) -> CandidateGateResult:
        return self.gate.result_for(ticket_key)

    def run_for(self, ticket_key: str) -> WorkRunSummary:
        matches = tuple(
            run
            for run in self.host.inspect(self.handle).work_runs
            if run.ticket_key == ticket_key
        )
        if len(matches) != 1:
            raise AssertionError(
                f"expected one Work Run for {ticket_key}, found {len(matches)}"
            )
        return matches[0]

    def advance(
        self,
        handle: CampaignHandle,
        wake_ref: str | None = None,
    ) -> CampaignOutcome:
        return self.host.advance(handle, wake_ref)

    def inspect(self, handle: CampaignHandle) -> Diagnostics:
        return self.host.inspect(handle)

    def restart(self) -> "Reopened137HostFixture":
        return install_reopened_137_host(
            root=self.root,
            start_host=self.start_host,
            gate=self.gate,
            handle=self.handle,
        )


def install_reopened_137_host(
    *,
    root: Path,
    start_host: ReopenedStartHost,
    gate: ReopenedCandidateGate,
    handle: CampaignHandle,
) -> Reopened137HostFixture:
    target = root / "target"
    target.mkdir(parents=True, exist_ok=True)
    host = ProductionGwoHost.install(
        start_host=start_host,
        store_path=root / "execution.sqlite3",
        effects=make_reopened_137_effects(root, gate),
        configuration=None,
        host_configuration=ProductionHostConfiguration(
            preview_mode="beta2_isolated_preview",
            target_isolation_root=root,
            writer_activation_enabled=False,
        ),
        target_path=target,
        watchdog_store_path=root / "watchdog.sqlite3",
        watchdog=ReopenedWatchdog(),
        writer_generation_reader=PlanningWriterGenerationReader(),
    )
    return Reopened137HostFixture(host, handle, start_host, gate, root)


def make_reopened_137_host(root: Path) -> Reopened137HostFixture:
    active, handle = _minimal_active_campaign(("issue:108", "issue:109"))
    return install_reopened_137_host(
        root=root,
        start_host=ReopenedStartHost(active),
        gate=ReopenedCandidateGate(),
        handle=handle,
    )


@pytest.fixture
def reopened_137_host(tmp_path: Path) -> Reopened137HostFixture:
    return make_reopened_137_host(tmp_path)
~~~

The replacement imports the existing `WorkRunSummary` from
`gwo_v8.execution_kernel`; it does not declare a parallel summary. Runtime and
Plan Invalidation both bind `runtime_binding_id="binding:test"`. Restart
constructs a new `ProductionWorkRunEffects` and `ProductionGwoHost` over the
same SQLite paths while retaining only the recording Gate's counters/results;
there is no GitHub client or tracker mutation in this fixture.

- [ ] Step 4: Run GREEN and the complete #137 evidence matrix

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_replanning.py" "tests/test_v8_candidate_gate_public.py" "tests/test_v8_candidate_gate.py" "tests/test_v8_runtime_gateway_repair.py" -q
~~~

Expected: PASS; deterministic failure uses zero Reviewer calls, Review scope escape preserves the complete Evidence lineage, Repair scope escape uses one bounded Repair Verification and no exploratory Formal Review, ordinary unauthorized Candidate rejection does not replan, and replay/restart quiesces once while unaffected Work Runs retain their exact Results.

- [ ] Step 5: Refactor and commit the revalidation evidence

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_replanning.py" -q
git add "tests/v8_production_test_support.py" "tests/test_v8_production_replanning.py"
git commit -m "test: revalidate Candidate scope escapes for reopened issue 137"
~~~


### Task 7: Integrate CampaignWatchdog, Batch wake sources, and crash/restart convergence

**Files:**
- Modify: skills/orchestrator/scripts/gwo_v8/production_host.py
- Modify: skills/orchestrator/scripts/gwo_v8/production_effects.py
- Create: tests/test_v8_production_composition_e2e.py
- Modify: tests/v8_production_test_support.py

**Interfaces:**
- Consumes: merged #113 CampaignWatchdog, Task 4 effect ledger, Task 5 ProductionGwoHost, and merged #116/#117 Batch wake/readback port.
- Produces: one Watchdog adapter whose every wake calls ProductionGwoHost.advance, lost-callback recovery, and crash convergence across Runtime, Candidate, Batch preparation, hosted result, and target readback.

- [ ] Step 1: Write failing composition E2E tests

~~~python
def test_watchdog_runtime_wake_calls_the_same_public_advance_path(composition_harness):
    composition_harness.publish_runtime_wake(
        cursor="41",
        stable_action_id="action:109",
    )
    outcomes = composition_harness.host.run_watchdog_once(
        "2026-08-03T10:00:00+00:00"
    )
    assert len(outcomes) == 1
    assert composition_harness.advance_calls == [
        (
            composition_harness.handle,
            "watchdog:runtime:41:action:109",
        )
    ]


def test_lost_batch_callback_is_recovered_from_next_check_at_after_restart(
    composition_harness,
):
    composition_harness.advance_to_accepted_candidate()
    composition_harness.kill_before_batch_callback()
    restarted = composition_harness.restart()
    restarted.host.run_watchdog_once("2026-08-03T10:01:00+00:00")
    diagnostics = restarted.host.inspect(restarted.handle)
    assert diagnostics.work_runs[0].phase == "completed"
    assert restarted.batch.execute_calls == 1


def test_crash_after_delivery_receipt_does_not_duplicate_target_integration(
    composition_harness,
):
    composition_harness.advance_to_batch_delivery()
    composition_harness.arm_crash("after_batch_terminal_readback")
    with pytest.raises(CompositionCrash):
        composition_harness.host.advance(
            composition_harness.handle,
            "hosted-check:lost",
        )
    restarted = composition_harness.restart()
    restarted.host.advance(restarted.handle, "hosted-check:replay")
    assert restarted.batch.target_integration_calls == 1
    assert (
        restarted.host.inspect(restarted.handle).work_runs[0].result_digest
        is not None
    )


def test_production_host_rejects_predecessor_batch_assembler(composition_harness):
    arguments = composition_harness.install_arguments()
    arguments["batch_integrator"] = GitIntegrationBatchAssembler(
        composition_harness.target
    )
    with pytest.raises(ProductionCompositionError) as raised:
        ProductionGwoHost.install(**arguments)
    assert raised.value.code == "PRODUCTION_PREDECESSOR_PATH_REJECTED"
~~~

Define this exact support harness in `v8_production_test_support.py`:

~~~python
@dataclass
class RecordingWakeSource:
    pages: list[WatchdogWakePage]
    calls: list[str | None] = field(default_factory=list)

    def read(self, after_cursor: str | None) -> WatchdogWakePage:
        self.calls.append(after_cursor)
        if self.pages:
            return self.pages.pop(0)
        return WatchdogWakePage((), after_cursor)


@dataclass
class ProductionCompositionHarness:
    host: ProductionGwoHost
    start_host: ProductionPlanControlStartHost
    store_path: Path
    effects: ProductionWorkRunEffects
    kernel_configuration: ExecutionKernelConfiguration | None
    host_configuration: ProductionHostConfiguration
    watchdog_store_path: Path
    watchdog: CampaignWatchdog
    writer_generation_reader: WriterGenerationReader
    repository: str
    ready_refs: tuple[str, ...]
    handle: CampaignHandle
    target: Path
    batch: RecordingBatchIntegrator
    advance_calls: list[tuple[CampaignHandle, str | None]]
    runtime_wake_source: RecordingWakeSource
    hosted_check_source: RecordingWakeSource
    watchdog_advancer: "ForwardingWatchdogAdvancer"
    evidence_dir: Path
    provider_command: str
    crashes: "CrashController"

    def publish_runtime_wake(self, cursor: str, stable_action_id: str) -> None:
        self.runtime_wake_source.pages.append(
            WatchdogWakePage(
                events=(
                    WatchdogWake(
                        cursor=cursor,
                        campaign=self.handle,
                        source="runtime",
                        source_identity=stable_action_id,
                    ),
                ),
                next_cursor=cursor,
            )
        )

    def advance_to_accepted_candidate(self) -> None:
        for wake_ref in ("runtime:initial", "runtime:completed"):
            self.host.advance(self.handle, wake_ref)
        phase = self.host.inspect(self.handle).work_runs[0].phase
        if phase != "accepted_awaiting_delivery":
            raise AssertionError(
                "the deterministic composition fixture did not reach "
                "accepted_awaiting_delivery"
            )

    def kill_before_batch_callback(self) -> None:
        self.batch.suppress_callbacks = True

    def arm_crash(self, point: str) -> None:
        self.crashes.arm(point)

    def advance_to_batch_delivery(self) -> None:
        self.advance_to_accepted_candidate()
        self.batch.suppress_callbacks = False

    def restart(self) -> "ProductionCompositionHarness":
        return type(self).from_task7_dependencies(
            target_path=self.target,
            evidence_dir=self.evidence_dir,
            provider_command=self.provider_command,
            store_path=self.store_path,
            watchdog_store_path=self.watchdog_store_path,
            existing_handle=self.handle,
        )

    def install_arguments(self) -> dict[str, object]:
        return {
            "start_host": self.start_host,
            "store_path": self.store_path,
            "effects": self.effects,
            "configuration": self.kernel_configuration,
            "host_configuration": self.host_configuration,
            "target_path": self.target,
            "watchdog_store_path": self.watchdog_store_path,
            "watchdog": self.watchdog,
            "writer_generation_reader": self.writer_generation_reader,
        }

    @classmethod
    def from_real_provider_environment(
        cls,
        *,
        target_path: Path,
        evidence_dir: Path,
        provider_command: str,
    ) -> "ProductionCompositionHarness":
        target_path = target_path.resolve()
        evidence_dir.mkdir(parents=True, exist_ok=True)
        harness = cls.from_task7_dependencies(
            target_path=target_path,
            evidence_dir=evidence_dir,
            provider_command=provider_command,
        )
        arguments = harness.install_arguments()
        arguments["host_configuration"] = ProductionHostConfiguration(
            target_isolation_root=target_path.parent,
            writer_activation_enabled=False,
        )
        arguments["target_path"] = target_path
        harness.host = ProductionGwoHost.install(**arguments)
        return harness

    @classmethod
    def from_task7_dependencies(
        cls,
        *,
        target_path: Path,
        evidence_dir: Path,
        provider_command: str,
        store_path: Path | None = None,
        watchdog_store_path: Path | None = None,
        existing_handle: CampaignHandle | None = None,
    ) -> "ProductionCompositionHarness":
        target_path = target_path.resolve()
        evidence_dir = evidence_dir.resolve()
        evidence_dir.mkdir(parents=True, exist_ok=True)
        resolved_store_path = store_path or evidence_dir / "execution-kernel.sqlite3"
        resolved_watchdog_store_path = (
            watchdog_store_path or evidence_dir / "campaign-watchdog.sqlite3"
        )
        runtime_wake_source = RecordingWakeSource([])
        hosted_check_source = RecordingWakeSource([])
        watchdog_advancer = ForwardingWatchdogAdvancer()
        crashes = CrashController()
        runtime_factory = RecordingRuntimeGatewayFactory(
            store_path=evidence_dir / "runtime-gateway.sqlite3",
            provider_command=provider_command,
            repository_root=target_path,
        )
        runtime_stale_readbacks = RecordingRuntimeStaleReadback()
        work_run_subjects = RecordingSubjectSource()
        candidate_references = RecordingCandidateReferenceReader()
        candidate_parents = RecordingCandidateParentSource()
        candidate_gate = RecordingCandidateGate()
        batch = RecordingBatchIntegrator(
            store_path=evidence_dir / "batch-integrator.sqlite3",
            target_path=target_path,
        )
        effects = CrashInjectingProductionWorkRunEffects(
            crash_controller=crashes,
            store_path=evidence_dir / "production-effects.sqlite3",
            runtime_gateways=runtime_factory,
            runtime_stale_readbacks=runtime_stale_readbacks,
            work_run_subjects=work_run_subjects,
            candidate_references=candidate_references,
            candidate_parents=candidate_parents,
            candidate_gate=candidate_gate,
            batch_requests=RecordingBatchRequestSource(
                target_path=target_path,
                runtime_factory=runtime_factory,
            ),
            batch_integrator=CrashReadbackBatchIntegrator(batch, crashes),
        )
        start_host = make_recording_plan_control_start_host(
            root=evidence_dir,
            runtime_factory=runtime_factory,
        )
        campaign_source = DeferredProductionCampaignSource()
        writer_generation_reader = RecordingWriterGenerationReader("v6.1")
        watchdog = CampaignWatchdog(
            store_path=resolved_watchdog_store_path,
            event_sources={
                "runtime_gateway": runtime_wake_source,
                "hosted_check": hosted_check_source,
            },
            campaign_source=campaign_source,
            advancer=watchdog_advancer,
        )
        host_configuration = ProductionHostConfiguration(
            target_isolation_root=target_path.parent,
            writer_activation_enabled=False,
        )
        host = ProductionGwoHost.install(
            start_host=start_host,
            store_path=resolved_store_path,
            effects=effects,
            configuration=None,
            host_configuration=host_configuration,
            target_path=target_path,
            watchdog_store_path=resolved_watchdog_store_path,
            watchdog=watchdog,
            writer_generation_reader=writer_generation_reader,
        )
        watchdog_advancer.host = host
        campaign_source.host = host
        repository = "owner/isolated-composition"
        ready_refs = ("issue:109",)
        handle = existing_handle or host.start(repository, ready_refs)
        campaign_source.handle = handle
        return cls(
            host=host,
            start_host=start_host,
            store_path=resolved_store_path,
            effects=effects,
            kernel_configuration=None,
            host_configuration=host_configuration,
            watchdog_store_path=resolved_watchdog_store_path,
            watchdog=watchdog,
            writer_generation_reader=writer_generation_reader,
            repository=repository,
            ready_refs=ready_refs,
            handle=handle,
            target=target_path,
            batch=batch,
            advance_calls=watchdog_advancer.calls,
            runtime_wake_source=runtime_wake_source,
            hosted_check_source=hosted_check_source,
            watchdog_advancer=watchdog_advancer,
            evidence_dir=evidence_dir,
            provider_command=provider_command,
            crashes=crashes,
        )
~~~

`from_task7_dependencies` is the exact test-support constructor created in
Task 7; it assembles the final RuntimeGateway/CandidateGate/BatchIntegrator
fakes and no predecessor path. The real-provider classmethod changes only the
RuntimeGateway provider command and target path; `ProductionGwoHost.install`
still enforces the isolated-preview fence. The remaining methods above record
the given wake/counters, call the public `advance`, and reopen the same
temporary stores; they do not call a deep module directly.

Define these support seams in the same file before the harness so every name in
the constructor has a concrete owner:

~~~python
@dataclass
class ForwardingWatchdogAdvancer:
    host: ProductionGwoHost | None = None
    calls: list[tuple[CampaignHandle, str | None]] = field(default_factory=list)

    def advance(
        self,
        handle: CampaignHandle,
        wake_ref: str | None = None,
    ) -> CampaignOutcome:
        if self.host is None:
            raise AssertionError("watchdog advancer was used before host binding")
        self.calls.append((handle, wake_ref))
        return self.host.advance(handle, wake_ref)


@dataclass
class DeferredProductionCampaignSource:
    host: ProductionGwoHost | None = None
    handle: CampaignHandle | None = None

    def active_campaigns(self) -> tuple[CampaignHandle, ...]:
        if self.host is None or self.handle is None:
            return ()
        return (self.handle,)

    def watchdog_snapshot(
        self,
        handle: CampaignHandle,
    ) -> WatchdogCampaignSnapshot:
        if self.host is None or self.handle != handle:
            raise AssertionError("campaign source was used before host binding")
        return self.host.watchdog_snapshot(handle)


def make_recording_plan_control_start_host(
    *,
    root: Path,
    runtime_factory: RecordingRuntimeGatewayFactory,
) -> ProductionPlanControlStartHost:
    source = RecordingCampaignSnapshotSource()
    repository = RecordingPlanControlRepository(repository="owner/isolated-composition")
    return install_plan_control_start(
        source=source,
        repository=repository,
        runtime_configuration=runtime_factory.runtime_configuration,
        repository_contexts=runtime_factory.repository_contexts,
        gateway_store_path=root / "runtime-gateway.sqlite3",
        artifact_root=root / "artifacts",
        _gateway_builder=runtime_factory.build,
    )
~~~

`RecordingRuntimeGatewayFactory` has the exact attributes
`runtime_configuration`, `repository_contexts`, and `build(**kwargs)` used
above; `RecordingBatchIntegrator` persists `suppress_callbacks: bool` in its
test-only object and retains its terminal receipt in the SQLite Batch journal.
`ProductionGwoHost.watchdog_snapshot(handle)` is a host-private read-only test
seam delegated to #113's `ExecutionKernel.watchdog_snapshot`; it is bound to
the `handle` selected by `from_task7_dependencies` and does not add a public
workflow operation.

The remaining constructor names used above are these concrete test doubles:

~~~python
class RecordingCampaignSnapshotSource:
    def canonical_ready_refs(
        self,
        repository: str,
        ready_refs: Sequence[str],
    ) -> tuple[str, ...]:
        return tuple(sorted(set(ready_refs)))

    def snapshot(
        self,
        repository: str,
        ready_refs: Sequence[str],
    ) -> dict[str, object]:
        value = active_plan_spec()
        value["repository"] = repository
        value["ready_refs"] = list(ready_refs)
        return value


class RecordingPlanControlRepository(InMemoryPlanRepository):
    def __init__(self, *, repository: str) -> None:
        self.repository = repository
        super().__init__(writer_generation="writer:v6.1")


@dataclass
class RecordingWriterGenerationReader:
    generation: str

    def read(self) -> str:
        return self.generation
~~~

Add the pytest fixture that owns the Task 7 harness construction:

~~~python
@pytest.fixture
def composition_harness(tmp_path: Path) -> ProductionCompositionHarness:
    target = tmp_path / "isolated-target"
    target.mkdir(parents=True, exist_ok=False)
    evidence_dir = tmp_path / "composition-evidence"
    return ProductionCompositionHarness.from_task7_dependencies(
        target_path=target,
        evidence_dir=evidence_dir,
        provider_command="recording-provider --no-dispatch",
    )
~~~

The fixture creates a new target and evidence directory below pytest's
`tmp_path`, then calls the complete `from_task7_dependencies` body above. It
does not reuse the checkout, does not start a provider, and does not mutate
GitHub. The predecessor-path test imports
`GitIntegrationBatchAssembler` directly from its existing module solely to
prove that `ProductionGwoHost.install` rejects that adapter; it is not wrapped
or invoked by production composition.

- [ ] Step 2: Run RED

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_composition_e2e.py" -q
~~~

Expected: FAIL because the host has no Watchdog delegation, no Batch wake adapter, and no crash/restart composition acceptance.

- [ ] Step 3: Implement one Watchdog adapter and no second state machine

Compose the final #113 CampaignWatchdog with a WatchdogAdvancer whose exact method is:

~~~python
def advance(
    self,
    handle: CampaignHandle,
    wake_ref: str | None = None,
) -> CampaignOutcome:
    return production_host.advance(handle, wake_ref)
~~~

Runtime, Candidate, Review, hosted-check, and next_check_at sources publish only WatchdogWake records. run_watchdog_once delegates each accepted wake to this one method; it never changes Kernel storage directly. Batch delivery receipts expose a host-private wake page with cursor identity, but BatchIntegrator remains the source of delivery truth.

- [ ] Step 4: Implement crash/restart readback checks

Replace the earlier recording Batch double in
`tests/v8_production_test_support.py` with this SQLite-backed body, then add the
crash controller, Batch readback wrapper, and effect-ledger crash subclass in
the same file. The configured `observation` is only the execute template;
`readback` returns only the separately persisted terminal observation:

~~~python
class CompositionCrash(RuntimeError):
    def __init__(self, point: str) -> None:
        super().__init__(f"injected production-composition crash: {point}")
        self.point = point


@dataclass
class CrashController:
    armed: set[str] = field(default_factory=set)

    def arm(self, point: str) -> None:
        if point not in {
            "after_effect_ledger_write",
            "after_batch_terminal_readback",
        }:
            raise AssertionError(f"unknown composition crash point: {point}")
        self.armed.add(point)

    def hit(self, point: str) -> None:
        if point in self.armed:
            self.armed.remove(point)
            raise CompositionCrash(point)


@dataclass
class RecordingBatchIntegrator:
    store_path: Path
    target_path: Path
    action: BatchDeliveryAction | None = None
    observation: BatchDeliveryObservation | None = None
    suppress_callbacks: bool = False

    def __post_init__(self) -> None:
        self.store_path = Path(self.store_path)
        self.target_path = Path(self.target_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.store_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS v8_recording_batch_terminal(
                    stable_action_id TEXT PRIMARY KEY,
                    action_json TEXT NOT NULL,
                    observation_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS v8_recording_batch_counters(
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    prepare_calls INTEGER NOT NULL,
                    execute_calls INTEGER NOT NULL,
                    target_integration_calls INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO v8_recording_batch_counters(
                    singleton, prepare_calls, execute_calls,
                    target_integration_calls
                ) VALUES (1, 0, 0, 0)
                ON CONFLICT(singleton) DO NOTHING
                """
            )

    def _action_json(self, action: BatchDeliveryAction) -> str:
        return json.dumps(
            {
                "stable_action_id": action.stable_action_id,
                "request_digest": action.request_digest,
                "batch_id": action.batch_id,
                "batch_sha": action.batch_sha,
                "member_ticket_keys": list(action.member_ticket_keys),
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    def _observation_json(self, observation: BatchDeliveryObservation) -> str:
        return json.dumps(
            {
                "stable_action_id": observation.stable_action_id,
                "batch_id": observation.batch_id,
                "batch_sha": observation.batch_sha,
                "phase": observation.phase,
                "reason": observation.reason,
                "receipt_digest": observation.receipt_digest,
                "retry_count": observation.retry_count,
                "fallback_generation": observation.fallback_generation,
                "members": [
                    {
                        "ticket_key": member.ticket_key,
                        "work_run_key": member.work_run_key,
                        "candidate_sha": member.candidate_sha,
                        "status": member.status,
                        "evidence_digests": list(member.evidence_digests),
                        "resume_reason": member.resume_reason,
                    }
                    for member in observation.members
                ],
                "delivery_proofs": [
                    proof.canonical() for proof in observation.delivery_proofs
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    def _decode_observation(self, rendered: str) -> BatchDeliveryObservation:
        value = json.loads(rendered)
        expected = {
            "stable_action_id",
            "batch_id",
            "batch_sha",
            "phase",
            "reason",
            "receipt_digest",
            "retry_count",
            "fallback_generation",
            "members",
            "delivery_proofs",
        }
        if type(value) is not dict or set(value) != expected:
            raise AssertionError("recording Batch observation fields changed")
        members = tuple(
            MemberDeliveryObservation(
                ticket_key=item["ticket_key"],
                work_run_key=item["work_run_key"],
                candidate_sha=item["candidate_sha"],
                status=item["status"],
                evidence_digests=tuple(item["evidence_digests"]),
                resume_reason=item["resume_reason"],
            )
            for item in value["members"]
        )
        delivery_proofs = tuple(
            BatchDeliveryProof(
                **{
                    **item,
                    "member_ticket_keys": tuple(item["member_ticket_keys"]),
                }
            )
            for item in value["delivery_proofs"]
        )
        return BatchDeliveryObservation(
            stable_action_id=value["stable_action_id"],
            batch_id=value["batch_id"],
            batch_sha=value["batch_sha"],
            phase=value["phase"],
            reason=value["reason"],
            receipt_digest=value["receipt_digest"],
            retry_count=value["retry_count"],
            fallback_generation=value["fallback_generation"],
            members=members,
            delivery_proofs=delivery_proofs,
        )

    def _bump(self, column: str) -> None:
        if column not in {
            "prepare_calls",
            "execute_calls",
            "target_integration_calls",
        }:
            raise AssertionError(column)
        with sqlite3.connect(self.store_path) as connection:
            connection.execute(
                f"UPDATE v8_recording_batch_counters "
                f"SET {column} = {column} + 1 WHERE singleton = 1"
            )

    def _counter(self, column: str) -> int:
        with sqlite3.connect(self.store_path) as connection:
            row = connection.execute(
                f"SELECT {column} FROM v8_recording_batch_counters "
                "WHERE singleton = 1"
            ).fetchone()
        if row is None:
            raise AssertionError("recording Batch counters disappeared")
        return int(row[0])

    @property
    def prepare_calls(self) -> int:
        return self._counter("prepare_calls")

    @property
    def execute_calls(self) -> int:
        return self._counter("execute_calls")

    @property
    def target_integration_calls(self) -> int:
        return self._counter("target_integration_calls")

    @property
    def persisted_observation(self) -> BatchDeliveryObservation | None:
        if self.action is None:
            return None
        return self.readback(self.action)

    def prepare(self, request: BatchDeliveryRequest) -> BatchDeliveryAction:
        self._bump("prepare_calls")
        if not request.accepted_candidates:
            raise AssertionError("recording Batch requires an accepted Candidate")
        expected_action = BatchDeliveryAction(
            stable_action_id=request.stable_action_id,
            request_digest=request.request_digest,
            batch_id=digest_value(
                {
                    "kind": "recording-batch.v1",
                    "request_digest": request.request_digest,
                }
            ),
            batch_sha=request.accepted_candidates[-1].candidate_sha,
            member_ticket_keys=tuple(
                item.ticket_key for item in request.accepted_candidates
            ),
        )
        if self.action is not None and self.action != expected_action:
            raise AssertionError("recording Batch action identity changed")
        self.action = expected_action
        members = tuple(
            MemberDeliveryObservation(
                ticket_key=item.ticket_key,
                work_run_key=item.work_run_key,
                candidate_sha=item.candidate_sha,
                status="integrated",
                evidence_digests=tuple(sorted(item.evidence_digests)),
                resume_reason=None,
            )
            for item in request.accepted_candidates
        )
        delivery_proof = BatchDeliveryProof.create(
            delivery_stable_action_id=expected_action.stable_action_id,
            delivery_request_digest=expected_action.request_digest,
            batch_id=expected_action.batch_id,
            batch_sha=expected_action.batch_sha,
            member_ticket_keys=expected_action.member_ticket_keys,
            local_check_receipt_digest=digest_value(
                {"kind": "recording-local-check.v1", "batch_sha": expected_action.batch_sha}
            ),
            publication_receipt_digest=digest_value(
                {"kind": "recording-publication.v1", "batch_sha": expected_action.batch_sha}
            ),
            pull_request_number=1,
            pull_request_head_sha=expected_action.batch_sha,
            hosted_result_receipt_digest=digest_value(
                {"kind": "recording-hosted-result.v1", "batch_sha": expected_action.batch_sha}
            ),
            integration_lease_digest=digest_value(
                {"kind": "recording-integration-lease.v1", "batch_sha": expected_action.batch_sha}
            ),
            target_branch=request.target.target_branch,
            target_head_sha=expected_action.batch_sha,
            target_readback_digest=digest_value(
                {"kind": "recording-target-readback.v1", "batch_sha": expected_action.batch_sha}
            ),
            target_contains_batch_sha=True,
            pull_request_merge_target_sha=expected_action.batch_sha,
            merge_method="merge",
        )
        receipt_body = {
            "stable_action_id": expected_action.stable_action_id,
            "batch_id": expected_action.batch_id,
            "batch_sha": expected_action.batch_sha,
            "phase": "complete",
            "reason": "exact isolated target read-back",
            "retry_count": 0,
            "fallback_generation": 0,
            "members": [
                {
                    "ticket_key": member.ticket_key,
                    "work_run_key": member.work_run_key,
                    "candidate_sha": member.candidate_sha,
                    "status": member.status,
                    "evidence_digests": list(member.evidence_digests),
                    "resume_reason": member.resume_reason,
                }
                for member in members
            ],
            "delivery_proofs": [delivery_proof.canonical()],
        }
        receipt_digest = digest_value(
            {"kind": "batch-observation.v1", **receipt_body}
        )
        expected_observation = BatchDeliveryObservation(
            stable_action_id=expected_action.stable_action_id,
            batch_id=expected_action.batch_id,
            batch_sha=expected_action.batch_sha,
            phase="complete",
            reason="exact isolated target read-back",
            receipt_digest=receipt_digest,
            retry_count=0,
            fallback_generation=0,
            members=members,
            delivery_proofs=(delivery_proof,),
        )
        if self.observation is not None and self.observation != expected_observation:
            raise AssertionError("recording Batch observation identity changed")
        self.observation = expected_observation
        return expected_action

    def readback(
        self,
        action: BatchDeliveryAction,
    ) -> BatchDeliveryObservation | None:
        with sqlite3.connect(self.store_path) as connection:
            row = connection.execute(
                """
                SELECT action_json, observation_json
                  FROM v8_recording_batch_terminal
                 WHERE stable_action_id = ?
                """,
                (action.stable_action_id,),
            ).fetchone()
        if row is None:
            return None
        if row[0] != self._action_json(action):
            raise AssertionError("recording Batch durable action changed")
        observation = self._decode_observation(row[1])
        if (
            observation.stable_action_id != action.stable_action_id
            or observation.batch_id != action.batch_id
            or observation.batch_sha != action.batch_sha
            or observation.phase not in {"complete", "decision", "blocked"}
        ):
            raise AssertionError("recording Batch terminal readback changed")
        return observation

    def execute(self, action: BatchDeliveryAction) -> BatchDeliveryObservation:
        persisted = self.readback(action)
        if persisted is not None:
            return persisted
        if self.action != action or self.observation is None:
            raise AssertionError("prepare must configure the recording Batch")
        self._bump("execute_calls")
        self._bump("target_integration_calls")
        with sqlite3.connect(self.store_path) as connection:
            connection.execute(
                """
                INSERT INTO v8_recording_batch_terminal(
                    stable_action_id, action_json, observation_json
                ) VALUES (?, ?, ?)
                ON CONFLICT(stable_action_id) DO NOTHING
                """,
                (
                    action.stable_action_id,
                    self._action_json(action),
                    self._observation_json(self.observation),
                ),
            )
        saved = self.readback(action)
        if saved != self.observation:
            raise AssertionError("recording Batch exact readback differs from execute")
        return saved


@dataclass
class CrashReadbackBatchIntegrator:
    inner: RecordingBatchIntegrator
    crashes: CrashController

    def prepare(self, request: BatchDeliveryRequest) -> BatchDeliveryAction:
        return self.inner.prepare(request)

    def readback(
        self,
        action: BatchDeliveryAction,
    ) -> BatchDeliveryObservation | None:
        observation = self.inner.readback(action)
        if observation is not None and observation.phase in {
            "complete",
            "decision",
            "blocked",
        }:
            self.crashes.hit("after_batch_terminal_readback")
        return observation

    def execute(self, action: BatchDeliveryAction) -> BatchDeliveryObservation:
        return self.inner.execute(action)


class CrashInjectingProductionWorkRunEffects(ProductionWorkRunEffects):
    def __init__(
        self,
        *,
        crash_controller: CrashController,
        store_path: Path,
        runtime_gateways: RuntimeGatewayFactory,
        runtime_stale_readbacks: RuntimeStaleReadbackPort,
        work_run_subjects: WorkRunSubjectSource,
        candidate_references: CandidateReferenceReader,
        candidate_parents: CandidateGateParentSource,
        candidate_gate: CandidateGatePort,
        batch_requests: BatchRequestSource,
        batch_integrator: BatchIntegratorPort,
    ) -> None:
        self._crash_controller = crash_controller
        super().__init__(
            store_path=store_path,
            runtime_gateways=runtime_gateways,
            runtime_stale_readbacks=runtime_stale_readbacks,
            work_run_subjects=work_run_subjects,
            candidate_references=candidate_references,
            candidate_parents=candidate_parents,
            candidate_gate=candidate_gate,
            batch_requests=batch_requests,
            batch_integrator=batch_integrator,
        )

    def _record(
        self,
        action: WorkRunAction,
        observation: WorkRunEffectObservation,
        *,
        accepted_candidate: AcceptedCandidateReceipt | None = None,
    ) -> WorkRunEffectObservation:
        saved = super()._record(
            action,
            observation,
            accepted_candidate=accepted_candidate,
        )
        self._crash_controller.hit("after_effect_ledger_write")
        return saved


def count_durable_effect_rows(store_path: Path) -> int:
    with sqlite3.connect(store_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM v8_production_effect_receipts"
        ).fetchone()
    if row is None:
        raise AssertionError("effect-ledger count readback disappeared")
    return int(row[0])
~~~

In `ProductionWorkRunEffects`, replace `_execute_batch` with the following
readback-after-execute body. It accepts an execute result only when the owner
immediately reads back the exact same terminal receipt; the crash wrapper above
therefore fires after durable Batch ownership and before the effect ledger:

~~~python
def _execute_batch(
    self,
    action: WorkRunAction,
) -> WorkRunObservation:
    accepted_digest = action.accepted_candidate_receipt_digest
    if not accepted_digest:
        raise ProductionCompositionError(
            "BATCH_ACCEPTED_RECEIPT_MISSING",
            "batch delivery requires the exact accepted-Candidate digest",
        )
    accepted_candidate = self._read_accepted_candidate_receipt(
        action,
        accepted_digest,
    )
    subject = self._work_run_subjects.for_action(action)
    request = self._batch_requests.for_action(
        action,
        subject,
        (accepted_candidate,),
    )
    batch_action = self._batch_integrator.prepare(request)
    batch_observation = self._batch_integrator.readback(batch_action)
    if batch_observation is None:
        executed = self._batch_integrator.execute(batch_action)
        batch_observation = self._batch_integrator.readback(batch_action)
        if batch_observation is None or batch_observation != executed:
            raise ProductionCompositionError(
                "BATCH_TERMINAL_READBACK_INVALID",
                "Batch execute did not have one exact durable owner readback",
            )
    if (
        batch_observation.stable_action_id != batch_action.stable_action_id
        or batch_observation.batch_id != batch_action.batch_id
        or batch_observation.batch_sha != batch_action.batch_sha
    ):
        raise ProductionCompositionError(
            "BATCH_TERMINAL_READBACK_INVALID",
            "Batch terminal readback changed stable action or Batch identity",
        )
    if batch_observation.phase == "complete":
        proof = ResultIntegrityProof.from_batch_observation(
            batch_action,
            request,
            batch_observation,
            accepted_candidate,
        )
        proof.validate_for(action, request.target.target_branch)
        return WorkRunObservation(
            phase="completed",
            stable_action_id=action.stable_action_id,
            runtime_binding_id=action.runtime_binding_id,
            receipt_digest=batch_observation.receipt_digest,
            candidate_receipt=self._read_candidate_receipt(action),
            accepted_candidate_receipt_digest=accepted_candidate.digest,
            candidate_diff_record_digest=accepted_candidate.diff_record_digest,
            delivery_receipt_digest=batch_observation.receipt_digest,
            result_digest=proof.result_digest,
            evidence_digests=proof.evidence_digests,
            result_integrity=proof,
        )
    return self._observation_from_batch(batch_observation, action)
~~~

Finally, add these exact harness/test bodies. `restart()` already calls
`from_task7_dependencies` with the same `evidence_dir`, Kernel store, Watchdog
store, target, and handle; that constructor creates a new controller, a new
`RecordingBatchIntegrator` over `batch-integrator.sqlite3`, and a new
`CrashInjectingProductionWorkRunEffects` over
`production-effects.sqlite3`:

~~~python
# Insert in ProductionCompositionHarness.
def effect_ledger_row_count(self) -> int:
    return count_durable_effect_rows(
        self.evidence_dir / "production-effects.sqlite3"
    )


# Append to tests/test_v8_production_composition_e2e.py.
def test_crash_after_effect_ledger_write_reuses_exact_durable_observation(
    composition_harness,
):
    before = composition_harness.effect_ledger_row_count()
    composition_harness.arm_crash("after_effect_ledger_write")
    with pytest.raises(CompositionCrash) as raised:
        composition_harness.host.advance(
            composition_harness.handle,
            "runtime:effect-ledger-crash",
        )
    assert raised.value.point == "after_effect_ledger_write"
    after_crash = composition_harness.effect_ledger_row_count()
    assert after_crash == before + 1

    restarted = composition_harness.restart()
    restarted.host.advance(
        restarted.handle,
        "runtime:effect-ledger-replay",
    )
    assert restarted.effect_ledger_row_count() == after_crash


def test_crash_after_terminal_batch_readback_reopens_owner_journal_once(
    composition_harness,
):
    composition_harness.advance_to_batch_delivery()
    composition_harness.arm_crash("after_batch_terminal_readback")
    with pytest.raises(CompositionCrash) as raised:
        composition_harness.host.advance(
            composition_harness.handle,
            "hosted-check:terminal-readback-crash",
        )
    assert raised.value.point == "after_batch_terminal_readback"
    assert composition_harness.batch.execute_calls == 1
    assert composition_harness.batch.target_integration_calls == 1

    restarted = composition_harness.restart()
    restarted.host.advance(
        restarted.handle,
        "hosted-check:terminal-readback-replay",
    )
    assert restarted.batch.persisted_observation is not None
    assert restarted.batch.execute_calls == 1
    assert restarted.batch.target_integration_calls == 1
    assert restarted.effect_ledger_row_count() >= 1
~~~

No crash point changes Kernel state directly. A restart first reopens the
Kernel CAS row, then the effect ledger, then the Batch owner's terminal
journal. A stale Kernel CAS retries the unchanged action identity; it never
increments the three #113 trusted-progress fields unless the replayed
observation is trusted progress.

- [ ] Step 5: Run GREEN and the full composition matrix

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_composition_e2e.py" "tests/test_v8_campaign_watchdog.py" "tests/test_v8_watchdog_execution_kernel.py" "tests/test_v8_watchdog_production_host.py" -q
~~~

Expected: PASS; Runtime and Batch wakes converge through one public advance, lost callbacks are recovered from durable due time, and each crash window yields one effect identity and one exact target integration.

- [ ] Step 6: Refactor and commit Watchdog/composition E2E

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_composition_e2e.py" -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add "skills/orchestrator/scripts/gwo_v8/production_host.py" "skills/orchestrator/scripts/gwo_v8/production_effects.py" "tests/v8_production_test_support.py" "tests/test_v8_production_composition_e2e.py" "skills/orchestrator/.skill-package.json"
git commit -m "test: prove production wake and restart convergence"
~~~

### Task 8: Replace the predecessor implement-gwo guidance without activating the writer

**Files:**
- Modify: skills/implement-gwo/SKILL.md
- Modify: skills/orchestrator/scripts/gwo_v8/__init__.py
- Modify through sync only: skills/implement-gwo/.skill-package.json
- Modify through sync only: skills/orchestrator/.skill-package.json
- Create: tests/test_implement_gwo_skill.py

**Interfaces:**
- Consumes: ProductionGwoHost.start, .advance, .inspect, the existing intake-only ImplementGwoEntry, and the five deep-module boundary.
- Produces: V8-only Skill guidance and an import/reachability proof that the production host never constructs GoalDriver, calls Kernel.reconcile_once, or invokes the old writer path. Issue #118 retains ownership of final predecessor deletion/cutover.

- [ ] Step 1: Write the failing Skill contract test

~~~python
def isolated_beta2_install_arguments(
    *,
    target_path: Path,
    target_isolation_root: Path,
) -> dict[str, object]:
    harness = ProductionCompositionHarness.from_task7_dependencies(
        target_path=target_path.resolve(),
        evidence_dir=(target_isolation_root / "skill-admission").resolve(),
        provider_command="recording-provider --no-dispatch",
    )
    arguments = harness.install_arguments()
    arguments["target_path"] = target_path.resolve()
    arguments["host_configuration"] = ProductionHostConfiguration(
        preview_mode="beta2_isolated_preview",
        target_isolation_root=target_isolation_root.resolve(),
        writer_activation_enabled=False,
    )
    return arguments


def test_implement_gwo_skill_names_only_the_v8_public_path():
    text = Path(
        "skills/implement-gwo/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "start(repository, ready_refs, options?) -> CampaignHandle" in text
    assert "advance(campaign_handle, wake_ref?) -> CampaignOutcome" in text
    assert "inspect(campaign_handle) -> Diagnostics" in text
    assert "CandidateGate" in text
    assert "BatchIntegrator" in text
    assert "reconcile_once" not in text
    assert "GoalDriver" not in text
    assert "writer activation" in text


def test_production_host_has_no_predecessor_driver_import():
    source = inspect.getsource(ProductionGwoHost)
    assert "GoalDriver" not in source
    assert "reconcile_once" not in source
    assert "GitIntegrationBatchAssembler" not in source


def test_skill_text_cannot_admit_a_normal_real_repository_to_v8():
    arguments = isolated_beta2_install_arguments(
        target_path=Path("D:/Workstation/github-work-orchestrator"),
        target_isolation_root=Path("tests"),
    )
    with pytest.raises(ProductionCompositionError) as raised:
        ProductionGwoHost.install(**arguments)
    assert raised.value.code == "V8_ISOLATED_PREVIEW_REQUIRED"
~~~

- [ ] Step 2: Run RED

~~~powershell
py -3.13 -m pytest "tests/test_implement_gwo_skill.py" -q
~~~

Expected: FAIL because the current Skill still instructs Kernel.reconcile_once, names GoalDriver, and documents predecessor continuation.

- [ ] Step 3: Replace the execution section with exact V8 guidance

Replace the predecessor Execute section with this concrete content:

~~~text
## Execute

1. Accept exactly one Ready Work Item, accepted parent spec, or explicit Ready set through ImplementGwoEntry. Intake validation may return /triage, /to-spec, or /to-tickets; it never executes a legacy workflow.
2. Route to V8 only when the host has `preview_mode="beta2_isolated_preview"`, `writer_activation_enabled=False`, and a temporary target proven beneath the configured isolation root. For a normal real repository, V6.1 remains authoritative; this Skill text cannot change that admission decision.
3. Compose the accepted source and Runtime configuration into ProductionGwoHost.start(repository, ready_refs, options?). PlanControl performs the one Planning Pass and returns the stable CampaignHandle; a pending semantic Planning action is continued only by a Runtime/Watchdog wake through the same host.
4. Call ProductionGwoHost.advance(campaign_handle, wake_ref?) for every wake. It is the only path that may reach ExecutionKernel, RuntimeGateway, CandidateGate, or BatchIntegrator. Do not call a provider, CLI, Agent, Runtime session, Candidate reader, Git driver, hosted check, or target branch directly.
5. Use ProductionGwoHost.inspect(campaign_handle) for read-only Diagnostics. Inspect is not a wake, retry, writer transition, or semantic action.

CandidateGate is the only Candidate/Review/Repair entry and BatchIntegrator is the only delivery boundary. The Campaign Watchdog supplies wake hints only. Skill guidance is not execution authority. Beta2 is an isolated feature-complete preview: it observes V6.1 writer generation and does not activate the default writer. Issue #118 owns the fail-closed cutover Guard and Issue #119 owns the real root Canary.
~~~

Remove all predecessor workflow-continuation instructions from the Skill, but do not delete entry.py, goal_driver.py, or kernel.py in this task; #118 must either remove them or prove them unreachable in its Guard.

- [ ] Step 4: Run GREEN and package sync

~~~powershell
py -3.13 -m pytest "tests/test_implement_gwo_skill.py" "tests/test_orchestrator_package.py" -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
~~~

Expected: PASS; the Skill describes only start/advance/inspect, names the five deep modules, fences V8 to an isolated Beta2 target, preserves V6.1 for normal real repositories, says Skill guidance is not authority, and explicitly leaves writer activation to #118/#119. The first sync command rewrites both package manifests; the following check reports no drift.

- [ ] Step 5: Refactor and commit the Skill boundary

~~~powershell
py -3.13 -m pytest "tests/test_implement_gwo_skill.py" -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add "skills/implement-gwo/SKILL.md" "skills/orchestrator/scripts/gwo_v8/__init__.py" "tests/test_implement_gwo_skill.py" "skills/implement-gwo/.skill-package.json" "skills/orchestrator/.skill-package.json"
git commit -m "docs: route implement-gwo through the V8 host"
~~~

### Task 9: Add isolated real-provider/composition E2E and the Beta2 evidence bundle

**Files:**
- Modify: tests/v8_production_test_support.py
- Modify: tests/test_v8_production_composition_e2e.py
- Create: tests/test_v8_production_docs.py

**Interfaces:**
- Consumes: Task 7 public composition E2E, the real RuntimeGateway factory, temporary Git target, final Candidate/Batch receipts, and the release-train Beta2 definition.
- Produces: opt-in real-provider coverage, default target-isolation protection, and a deterministic Beta2 evidence manifest.

- [ ] Step 1: Write failing isolation and evidence tests

~~~python
def test_real_provider_e2e_refuses_a_non_temporary_target(tmp_path):
    with pytest.raises(ProductionCompositionError) as raised:
        assert_isolated_e2e_target(
            Path("D:/Workstation/github-work-orchestrator"),
            tmp_path,
        )
    assert raised.value.code == "REAL_E2E_TARGET_NOT_ISOLATED"


@pytest.mark.real_provider
def test_real_provider_public_path_is_opt_in_and_uses_a_temporary_target(
    tmp_path,
    monkeypatch,
):
    if os.environ.get("GWO_V8_REAL_PROVIDER_E2E") != "1":
        pytest.skip("real-provider E2E is opt-in")
    target = create_temporary_target(tmp_path)
    assert_isolated_e2e_target(target, tmp_path)
    harness = install_real_provider_composition(
        target,
        evidence_dir=tmp_path / "evidence",
    )
    handle = harness.host.start(harness.repository, harness.ready_refs)
    harness.host.advance(handle, "real-provider:ready")
    diagnostics = harness.host.inspect(handle)
    assert diagnostics.campaign == handle


def test_beta2_evidence_manifest_has_exact_release_gate_fields(tmp_path):
    path = write_beta2_evidence_bundle(
        tmp_path,
        main_sha="a" * 40,
        issue_states={
            "113": "CLOSED",
            "114": "CLOSED",
            "115": "CLOSED",
            "116": "CLOSED",
            "117": "CLOSED",
            "137": "CLOSED",
        },
        campaign_handle="owner/repo:campaign:beta2",
        plan_revision_digest="b" * 64,
        writer_generation_before="v6.1",
        writer_generation_after="v6.1",
        result_integrity_digests=("c" * 64,),
        batch_delivery_proof_digests=("d" * 64,),
        issue_137_revalidation={
            "reopen_approved": True,
            "reopen_path": "beta1_prior_owner_approval",
            "reopen_approval_evidence": {
                "approved": True,
                "source": "beta1_step_5",
                "state_readback": "OPEN",
            },
            "revalidated_after_114_115_merge": True,
            "test_command": "py -3.13 -m pytest tests/test_v8_production_replanning.py -q",
            "status": "passed",
            "close_approved": True,
            "close_approval_evidence": {
                "approved": True,
                "source": "beta2_go_no_go",
                "state_readback": "CLOSED",
            },
        },
        main_ci_url="https://github.com/NOirBRight/github-work-orchestrator/actions/runs/123456789",
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert set(manifest) == {
        "schema_version",
        "main_sha",
        "issue_states",
        "campaign_handle",
        "plan_revision_digest",
        "writer_generation_before",
        "writer_generation_after",
        "result_integrity_digests",
        "batch_delivery_proof_digests",
        "issue_137_revalidation",
        "focused_tests",
        "full_gate",
        "target_isolation",
    }
    assert (
        manifest["writer_generation_before"]
        == manifest["writer_generation_after"]
    )
    assert manifest["batch_delivery_proof_digests"] == ["d" * 64]
    assert manifest["focused_tests"] == {
        "command": (
            "py -3.13 -m pytest tests/test_v8_production_composition_e2e.py "
            "tests/test_v8_production_docs.py -q"
        ),
        "status": "passed",
    }
    assert manifest["full_gate"] == {
        "pytest": {"command": "py -3.13 -m pytest -q", "status": "passed"},
        "quick_validate": {
            "command": "py -3.13 scripts/quick_validate.py",
            "status": "passed",
        },
        "package_sync": {
            "command": "py -3.13 scripts/sync_orchestrator.py --check",
            "status": "passed",
        },
        "diff_check": {"command": "git diff --check", "status": "passed"},
        "main_ci_url": "https://github.com/NOirBRight/github-work-orchestrator/actions/runs/123456789",
    }
    assert manifest["target_isolation"] is True
~~~

Define these exact support functions in `v8_production_test_support.py` with
the following concrete bodies (the Task 7 `ProductionCompositionHarness`
constructor is the only assembly helper they call):

~~~python
import json
import os
from pathlib import Path
import re
import subprocess


def assert_isolated_e2e_target(target: Path, root: Path) -> None:
    target_resolved = target.resolve()
    root_resolved = root.resolve()
    if target_resolved == root_resolved or root_resolved not in target_resolved.parents:
        raise ProductionCompositionError(
            "REAL_E2E_TARGET_NOT_ISOLATED",
            "the E2E target must be a strict child of the pytest temporary root",
        )
    if not target_resolved.is_dir() or not (target_resolved / ".git").exists():
        raise ProductionCompositionError(
            "REAL_E2E_TARGET_NOT_ISOLATED",
            "the isolated E2E target must be an existing Git repository",
        )


def create_temporary_target(root: Path) -> Path:
    target = (root / "real-provider-target").resolve()
    target.mkdir(parents=True, exist_ok=False)
    subprocess.run(
        ["git", "init", "--initial-branch", "main", str(target)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (target / "README.md").write_text("isolated GWO V8 target\n", encoding="utf-8")
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "GWO V8 isolated test",
        "GIT_AUTHOR_EMAIL": "gwo-v8-isolated@example.invalid",
        "GIT_COMMITTER_NAME": "GWO V8 isolated test",
        "GIT_COMMITTER_EMAIL": "gwo-v8-isolated@example.invalid",
    }
    subprocess.run(["git", "-C", str(target), "add", "README.md"], check=True, env=environment)
    subprocess.run(
        ["git", "-C", str(target), "commit", "-m", "create isolated target"],
        check=True,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert_isolated_e2e_target(target, root)
    return target


def install_real_provider_composition(
    target: Path,
    evidence_dir: Path,
) -> ProductionCompositionHarness:
    assert_isolated_e2e_target(target, target.parent)
    if os.environ.get("GWO_V8_REAL_PROVIDER_E2E") != "1":
        raise ProductionCompositionError(
            "REAL_PROVIDER_E2E_NOT_ENABLED",
            "real-provider composition requires GWO_V8_REAL_PROVIDER_E2E=1",
        )
    command = os.environ.get("GWO_V8_REAL_PROVIDER_COMMAND")
    if not command:
        raise ProductionCompositionError(
            "REAL_PROVIDER_COMMAND_MISSING",
            "real-provider composition requires GWO_V8_REAL_PROVIDER_COMMAND",
        )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return ProductionCompositionHarness.from_real_provider_environment(
        target_path=target.resolve(),
        evidence_dir=evidence_dir.resolve(),
        provider_command=command,
    )


def write_beta2_evidence_bundle(
    root: Path,
    *,
    main_sha: str,
    issue_states: dict[str, str],
    campaign_handle: str,
    plan_revision_digest: str,
    writer_generation_before: str,
    writer_generation_after: str,
    result_integrity_digests: tuple[str, ...],
    batch_delivery_proof_digests: tuple[str, ...],
    issue_137_revalidation: dict[str, object],
    main_ci_url: str,
) -> Path:
    if re.fullmatch(r"[0-9a-f]{40}", main_sha) is None:
        raise ProductionCompositionError("BETA2_EVIDENCE_INVALID", "main_sha is not a Git object ID")
    if re.fullmatch(r"[0-9a-f]{64}", plan_revision_digest) is None:
        raise ProductionCompositionError("BETA2_EVIDENCE_INVALID", "plan_revision_digest is not a SHA-256 digest")
    if not campaign_handle or not main_ci_url.startswith("https://"):
        raise ProductionCompositionError("BETA2_EVIDENCE_INVALID", "Campaign handle and CI URL are required")
    if writer_generation_before != writer_generation_after:
        raise ProductionCompositionError("BETA2_WRITER_CHANGED", "writer generation changed during isolated evidence")
    if issue_states != {str(number): "CLOSED" for number in (113, 114, 115, 116, 117, 137)}:
        raise ProductionCompositionError("BETA2_EVIDENCE_INVALID", "issue state readback is not the Beta2 closed set")
    if not result_integrity_digests or any(
        re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in result_integrity_digests
    ):
        raise ProductionCompositionError("BETA2_EVIDENCE_INVALID", "Result integrity digests are invalid")
    if (
        len(batch_delivery_proof_digests) != len(result_integrity_digests)
        or any(
            re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for digest in batch_delivery_proof_digests
        )
    ):
        raise ProductionCompositionError(
            "BETA2_EVIDENCE_INVALID",
            "selected Batch delivery proof digests are invalid",
        )
    expected_revalidation_keys = {
        "reopen_approved",
        "reopen_path",
        "reopen_approval_evidence",
        "revalidated_after_114_115_merge",
        "test_command",
        "status",
        "close_approved",
        "close_approval_evidence",
    }
    if set(issue_137_revalidation) != expected_revalidation_keys:
        raise ProductionCompositionError(
            "BETA2_EVIDENCE_INVALID",
            "#137 revalidation evidence has an unknown or missing field",
        )
    reopen_path = issue_137_revalidation["reopen_path"]
    expected_reopen_source = {
        "beta1_prior_owner_approval": "beta1_step_5",
        "post_merge_manual_approval": "production_composition_checkpoint",
    }.get(reopen_path)
    if (
        issue_137_revalidation["reopen_approved"] is not True
        or expected_reopen_source is None
        or issue_137_revalidation["reopen_approval_evidence"]
        != {
            "approved": True,
            "source": expected_reopen_source,
            "state_readback": "OPEN",
        }
        or issue_137_revalidation["revalidated_after_114_115_merge"] is not True
        or issue_137_revalidation["test_command"]
        != "py -3.13 -m pytest tests/test_v8_production_replanning.py -q"
        or issue_137_revalidation["status"] != "passed"
        or issue_137_revalidation["close_approved"] is not True
        or issue_137_revalidation["close_approval_evidence"]
        != {
            "approved": True,
            "source": "beta2_go_no_go",
            "state_readback": "CLOSED",
        }
    ):
        raise ProductionCompositionError(
            "BETA2_EVIDENCE_INVALID",
            "#137 revalidation, OPEN-path, or independent close evidence is not exact",
        )
    focused_command = (
        "py -3.13 -m pytest tests/test_v8_production_composition_e2e.py "
        "tests/test_v8_production_docs.py -q"
    )
    manifest = {
        "schema_version": "gwo-v8-beta2-composition-evidence.v1",
        "main_sha": main_sha,
        "issue_states": dict(issue_states),
        "campaign_handle": campaign_handle,
        "plan_revision_digest": plan_revision_digest,
        "writer_generation_before": writer_generation_before,
        "writer_generation_after": writer_generation_after,
        "result_integrity_digests": list(result_integrity_digests),
        "batch_delivery_proof_digests": list(batch_delivery_proof_digests),
        "issue_137_revalidation": dict(issue_137_revalidation),
        "focused_tests": {"command": focused_command, "status": "passed"},
        "full_gate": {
            "pytest": {"command": "py -3.13 -m pytest -q", "status": "passed"},
            "quick_validate": {"command": "py -3.13 scripts/quick_validate.py", "status": "passed"},
            "package_sync": {"command": "py -3.13 scripts/sync_orchestrator.py --check", "status": "passed"},
            "diff_check": {"command": "git diff --check", "status": "passed"},
            "main_ci_url": main_ci_url,
        },
        "target_isolation": True,
    }
    rendered = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if json.loads(rendered) != manifest:
        raise ProductionCompositionError("BETA2_EVIDENCE_INVALID", "evidence is not canonical JSON")
    path = root / "beta2-evidence.json"
    path.write_text(rendered + "\n", encoding="utf-8", newline="\n")
    if json.loads(path.read_text(encoding="utf-8")) != manifest:
        raise ProductionCompositionError("BETA2_EVIDENCE_INVALID", "evidence readback changed after write")
    return path
~~~

- [ ] Step 2: Run RED

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_composition_e2e.py::test_real_provider_e2e_refuses_a_non_temporary_target" "tests/test_v8_production_docs.py::test_beta2_evidence_manifest_has_exact_release_gate_fields" -q
~~~

Expected: FAIL because the current checkout has no
`assert_isolated_e2e_target` implementation and no exact Beta2 evidence writer.
The opt-in real-provider test is skipped until explicitly enabled; it must
never mutate a real target by default.

- [ ] Step 3: Implement the minimum opt-in real-provider harness and evidence writer

The real-provider test may use a configured RuntimeGateway/Paseo provider only when GWO_V8_REAL_PROVIDER_E2E=1 and GWO_V8_REAL_PROVIDER_COMMAND is present. It must create a temporary repository under pytest tmp_path, use a disposable control branch and target branch, and fail closed if the resolved target is outside tmp_path. The default test run uses the isolated provider-neutral adapter at the same ProductionGwoHost boundary; it never calls the old Kernel, direct GitHub delivery, or a real target.

Write `beta2-evidence.json` with exactly these fields and exact commands:

~~~python
focused_tests = {
    "command": (
        "py -3.13 -m pytest "
        "tests/test_v8_production_composition_e2e.py "
        "tests/test_v8_production_docs.py -q"
    ),
    "status": "passed",
}
full_gate = {
    "pytest": {"command": "py -3.13 -m pytest -q", "status": "passed"},
    "quick_validate": {
        "command": "py -3.13 scripts/quick_validate.py",
        "status": "passed",
    },
    "package_sync": {
        "command": "py -3.13 scripts/sync_orchestrator.py --check",
        "status": "passed",
    },
    "diff_check": {"command": "git diff --check", "status": "passed"},
    "main_ci_url": main_ci_url,
}
manifest = {
    "schema_version": "gwo-v8-beta2-composition-evidence.v1",
    "main_sha": main_sha,
    "issue_states": issue_states,
    "campaign_handle": campaign_handle,
    "plan_revision_digest": plan_revision_digest,
    "writer_generation_before": writer_generation_before,
    "writer_generation_after": writer_generation_after,
    "result_integrity_digests": list(result_integrity_digests),
    "batch_delivery_proof_digests": list(batch_delivery_proof_digests),
    "issue_137_revalidation": issue_137_revalidation,
    "focused_tests": focused_tests,
    "full_gate": full_gate,
    "target_isolation": True,
}
~~~

The evidence writer validates the exact top-level key set above, the six exact
closed issue keys, 40-hex `main_sha`, lowercase 64-hex plan/result/selected-proof
digests, one selected proof digest per Result,
non-empty `campaign_handle`, `https://` `main_ci_url`, equal writer-generation
readbacks, and an exact #137 revalidation object with
`reopen_path` equal to `beta1_prior_owner_approval` or
`post_merge_manual_approval`. Its matching approval evidence must read back
OPEN before the tests; its independent `close_approval_evidence` must be
present and read back CLOSED before a Beta2 PASS. The exact
`py -3.13 -m pytest tests/test_v8_production_replanning.py -q` command, the
focused/full commands, canonical JSON, and `target_isolation is True` are
required. Unknown keys, changed writer generation, false isolation, absent
approval evidence, or any issue state other than the supplied authoritative
readback raise `ProductionCompositionError`. This manifest is evidence, not
writer activation.

- [ ] Step 4: Run GREEN

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_composition_e2e.py" "tests/test_v8_production_docs.py" -q
~~~

Expected: PASS; the real-provider path is skipped unless explicitly opted in, the target-isolation guard rejects the repository checkout, and the manifest is exact and immutable.

- [ ] Step 5: Refactor and commit the isolated E2E/evidence slice

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_composition_e2e.py" "tests/test_v8_production_docs.py" -q
git add "tests/v8_production_test_support.py" "tests/test_v8_production_composition_e2e.py" "tests/test_v8_production_docs.py"
git commit -m "test: add isolated Beta2 composition evidence"
~~~

### Task 10: Publish operational composition guidance and run the Beta2 go/no-go gate

**Files:**
- Create: docs/operations/gwo-v8-production-composition.md
- Modify: tests/test_v8_production_docs.py

**Interfaces:**
- Consumes: all Tasks 1–9, the master release train, final Issue #113–#117 merge readbacks, reopened #137 revalidation, and the host Beta2 isolated-preview configuration.
- Produces: an operator runbook and a no-go/hold decision with a durable exact evidence bundle. It does not publish a tag or change writer authority.

- [ ] Step 1: Write the failing operational-doc contract test

~~~python
def test_production_composition_runbook_contains_beta2_safety_gates():
    text = Path(
        "docs/operations/gwo-v8-production-composition.md"
    ).read_text(encoding="utf-8")
    for required in (
        "ProductionGwoHost",
        "ProductionWorkRunEffects",
        "start(repository, ready_refs, options?)",
        "advance(campaign_handle, wake_ref?)",
        "inspect(campaign_handle)",
        "CandidateGate",
        "BatchIntegrator",
        "CampaignWatchdog",
        "SQLite compare-and-swap",
        "accepted-Candidate receipt",
        "BatchDeliveryProof",
        "publication receipt",
        "Integration-Lease receipt",
        "Singleton fallback",
        "target read-back",
        "Beta2",
        "writer activation is disabled",
        "GWO_V8_REAL_PROVIDER_E2E=1",
    ):
        assert required in text
~~~

- [ ] Step 2: Run RED

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_docs.py::test_production_composition_runbook_contains_beta2_safety_gates" -q
~~~

Expected: FAIL because the required operational runbook path is absent.

- [ ] Step 3: Write the concrete runbook

Run this PowerShell from the repository root. It writes the complete document
verbatim, creates only the owned operations-doc directory when absent, and
uses UTF-8 without a byte-order mark:

~~~powershell
$ErrorActionPreference = 'Stop'
$repoRoot = (git rev-parse --show-toplevel).Trim()
if (-not $repoRoot) { throw 'Run from a GWO implementation worktree.' }
Set-Location -LiteralPath $repoRoot

$runbook = @'
# GWO V8 Production Composition Runbook

## Purpose and release boundary

This runbook admits only the **Beta2 feature-complete isolated preview** of
`ProductionGwoHost` and `ProductionWorkRunEffects`. Beta1 repaired release
metadata/tracker state only and was not production admission. Beta3 begins
only after Issue #118 completes its independent Cutover Guard. GA additionally
requires Issue #119's real public-API Canary, exact target read-back, and
Activation read-back.

V6.1 remains the authoritative writer throughout this runbook. The literal
operator invariant is: **writer activation is disabled**. Skill text, a local
environment variable, or a successful Beta2 test cannot transfer authority.

## 1. Prerequisites and the idempotent Issue #137 checkpoint

Run all commands from a fresh, clean, isolated implementation worktree. Record
the exact merged main SHA and read complete tracker bodies/comments:

```powershell
$repo = 'NOirBRight/github-work-orchestrator'
$mainSha = (git rev-parse HEAD).Trim()
if ($mainSha -notmatch '^[0-9a-f]{40}$') { throw 'HEAD is not an exact SHA-1.' }
git status --short
gh issue view 114 --repo $repo --comments --json number,state,body,comments
gh issue view 115 --repo $repo --comments --json number,state,body,comments
$issue137 = gh issue view 137 --repo $repo --comments --json number,state,body,comments | ConvertFrom-Json
```

The empty output from `git status --short` is required. Select exactly one
idempotent path and save its approval evidence with the Beta2 bundle:

1. `beta1_prior_owner_approval`: if `$issue137.state -eq 'OPEN'`, read and
   record the prior explicit Beta1 owner approval that repaired the
   closed-with-open-blockers tracker state. Do not run a reopen mutation.
2. `post_merge_manual_approval`: if `$issue137.state -eq 'CLOSED'`, first prove
   #114 and #115 are merged by recording their merged SHAs and complete blocker
   comments. Then obtain a new explicit human owner approval. Only the human
   operator, after recording that evidence, may run:

```powershell
gh issue reopen 137 --repo NOirBRight/github-work-orchestrator
```

The command above is conditional and manual; no script in this runbook invokes
it. For either path, repeat this readback and require `OPEN` before revalidation:

```powershell
$open137 = gh issue view 137 --repo $repo --comments --json number,state,body,comments | ConvertFrom-Json
if ($open137.state -ne 'OPEN') { throw '#137 must read back OPEN before revalidation.' }
```

After successful revalidation, closing #137 requires a second, independent
human approval. Record that close approval, let the human perform the close,
and require a final `CLOSED` readback. A prior reopen approval is not close
approval. If any blocker is still open, stop with NO-GO.

## 2. Beta2 isolated host configuration

Create a disposable root and a separate Git target below it. The repository
checkout itself must never be the target:

```powershell
$betaRoot = Join-Path ([IO.Path]::GetTempPath()) ('gwo-v8-beta2-' + [guid]::NewGuid())
$targetPath = Join-Path $betaRoot 'target'
$evidencePath = Join-Path $betaRoot 'evidence'
New-Item -ItemType Directory -Path $targetPath,$evidencePath | Out-Null
git -C $targetPath init --initial-branch=main
$env:GWO_V8_PREVIEW_MODE = 'beta2_isolated_preview'
$env:GWO_V8_WRITER_ACTIVATION_ENABLED = '0'
```

Install with this exact policy value:

```python
ProductionHostConfiguration(
    worker_slots=4,
    batch_member_limit=4,
    preview_mode="beta2_isolated_preview",
    target_isolation_root=beta_root.resolve(),
    writer_activation_enabled=False,
)
```

Runtime Profiles are supplied through host configuration. `store_path` and
`watchdog_store_path` are disposable SQLite files under `$evidencePath`.
`target_path` resolves below `$betaRoot`; equality with or escape from that
root is rejected. Installation reads writer generation for evidence only and
must read the same value before and after the run.

## 3. Public execution flow

Production composition has only these workflow operations:

- `start(repository, ready_refs, options?)`
- `advance(campaign_handle, wake_ref?)`
- `inspect(campaign_handle)`

`start` performs one PlanControl Planning Pass or records its durable async
Planning continuation. `advance` is the only state transition and the only
callback target used by `CampaignWatchdog`. Runtime, Candidate, Review,
hosted-check, Batch, and `next_check_at` events become typed wakes and all
delegate to that same method. `inspect` is read-only: it performs no migration,
CAS save, provider call, Git call, GitHub call, or CI call.

The deep-module ownership chain is fixed:

1. `RuntimeGateway` exclusively performs provider dispatch and provider
   readback and binds `runtime_binding_id`.
2. `CandidateGate` exclusively reads and evaluates the Candidate, invokes
   Review/Repair policy, and owns the accepted-Candidate receipt.
3. `BatchIntegrator` exclusively prepares and delivers a Batch, owns its
   Integration Lease, and performs local, PR, hosted-result, target-head, and
   target read-back.
4. `CampaignWatchdog` supplies durable wakes but never edits Kernel state.
5. `ExecutionKernel` alone advances Campaign state using SQLite
   compare-and-swap.

Do not call any deep module directly from an operator script.

## 4. Restart and crash recovery

On process restart, reconstruct `ProductionGwoHost` over the same Kernel,
effect-ledger, RuntimeGateway, BatchIntegrator, and Watchdog SQLite paths. The
recovery order is:

1. read and digest-validate the exact Kernel CAS row from
   `v8_execution_kernel_campaigns`;
2. read the unchanged stable action from `v8_production_effect_receipts`;
3. ask the owning RuntimeGateway, CandidateGate, or BatchIntegrator for exact
   readback before any execute call;
4. adopt an exact terminal hosted-result/target receipt;
5. retry Kernel CAS using the newly read `state_version`.

No transaction spans external I/O. `state_version` may increment for any CAS
save, including a raw wake. Per-run `trusted_progress_revision`,
`last_trusted_progress_at`, and `stale_due_at` change only on trusted progress;
a raw wake must never reset staleness.

An owner readback mismatch is a hard stop. Recovery never invents a new stable
action, starts a second provider action, restarts a provider daemon, repeats a
Formal Review, or duplicates target integration.

Read the durable Kernel row without mutating it:

```powershell
$kernelDb = Join-Path $evidencePath 'execution-kernel.sqlite3'
py -3.13 -c "import sqlite3,sys; p=sys.argv[1]; c=sqlite3.connect(p); rows=c.execute('SELECT repository,campaign_key,state_version,state_digest FROM v8_execution_kernel_campaigns ORDER BY repository,campaign_key').fetchall(); print(rows)" $kernelDb
```

## 5. Result integrity and read-back

A code Result is valid only when one immutable chain binds all of these facts:

1. CandidateGate-owned accepted-Candidate receipt digest;
2. Candidate commit OID, tree OID, and Candidate diff record digest;
3. parent Batch observation receipt, stable action, request, Batch ID, and Batch SHA;
4. the unique selected `BatchDeliveryProof.proof_digest` whose member partition contains this Ticket;
5. selected direct-or-Singleton delivery stable action, request, Batch ID, Batch SHA, and member Ticket tuple;
6. exact local-check and publication receipt digests;
7. positive pull-request number and head SHA equal to the selected Batch SHA;
8. hosted-result receipt digest for that exact head;
9. repository-global Integration-Lease receipt digest;
10. target branch, target head SHA, and target-readback digest proving the selected Batch SHA is present;
11. merge method `merge` and pull-request merge target SHA equal to the read-back target head; and
12. canonical Evidence digests and the computed Result digest.

A direct completion selects its one proof. A successful Singleton fallback
selects the one child proof containing the current Ticket while retaining the
parent observation receipt; it never treats the failed parent Batch as delivered.

Candidate-only completion is rejected. A Candidate receipt or
accepted-Candidate receipt without Batch, hosted result, and target read-back
must remain `accepted_awaiting_delivery` with no Result digest.

## 6. Provider and target isolation

The default E2E uses a pytest temporary repository and an inert recording
provider. A real provider is opt-in only:

```powershell
$env:GWO_V8_REAL_PROVIDER_E2E = '1'
$env:GWO_V8_REAL_PROVIDER_TARGET = $targetPath
if ([string]::IsNullOrWhiteSpace($env:GWO_V8_REAL_PROVIDER_COMMAND)) {
    throw 'Set GWO_V8_REAL_PROVIDER_COMMAND to the approved provider command.'
}
py -3.13 -m pytest tests/test_v8_production_composition_e2e.py -k real_provider -q
```

The test must skip unless `GWO_V8_REAL_PROVIDER_E2E=1`, the command is
non-empty, and the resolved target is strictly below the temporary isolation
root. It may read a real provider but must not target the source checkout, a
normal real repository, or GitHub by default. A target outside the temporary
root is rejected before host installation.

## 7. Beta2 evidence bundle and go/no-go

Run and record these exact commands:

```powershell
py -3.13 -m pytest tests/test_v8_production_replanning.py -q
py -3.13 -m pytest tests/test_v8_production_composition_e2e.py tests/test_v8_production_docs.py -q
py -3.13 -m pytest -q
py -3.13 scripts/quick_validate.py
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
$sha = (git rev-parse HEAD).Trim()
gh run list --repo NOirBRight/github-work-orchestrator --commit $sha --workflow 'GWO CI' --status success --limit 1 --json databaseId,headSha,conclusion,url
```

Write `beta2-evidence.json` under `$evidencePath` with exactly these top-level
fields:

```text
schema_version
main_sha
issue_states
campaign_handle
plan_revision_digest
writer_generation_before
writer_generation_after
result_integrity_digests
batch_delivery_proof_digests
issue_137_revalidation
focused_tests
full_gate
target_isolation
```

`schema_version` is `gwo-v8-beta2-composition-evidence.v1`.
`issue_states` contains exact authoritative readbacks for #113, #114, #115,
#116, #117, and #137. `issue_137_revalidation` records `reopen_path`, matching
approval evidence, the exact replanning test command/status, independent close
approval evidence, and final tracker readback. `batch_delivery_proof_digests`
records the exact selected proof digest for each Result and must match the
corresponding `ResultIntegrityProof.batch_delivery_proof_digest`.
`focused_tests` and `full_gate`
record the exact commands and `passed` statuses. `full_gate` also records the
successful main CI URL/head SHA. `target_isolation` is exactly `true`.

The decision is GO only when all fields validate, #113-#117 read back CLOSED,
#137 followed one approved OPEN path and then independently approved close,
all four #137 cases pass, every Result integrity and selected Batch proof digest
validates, main CI
matches `main_sha`, the target is isolated, and writer generation before/after
is identical. Any absent, extra, stale, or mismatched field is NO-GO. Beta2
never changes the default writer.

## 8. Stop and hand off to Issue #118

Attach or otherwise provide the immutable evidence bundle to the #118 owner,
then stop. Do not publish a GA tag, delete the V6.1 path, activate the V8
writer, or mutate writer generation. Issue #118 independently runs its
read-only Guard and owns any later Beta3 cutover-candidate decision. Issue #119
still owns the real public-API Canary and read-back required for GA.
'@

$path = 'docs/operations/gwo-v8-production-composition.md'
$directory = Split-Path -Parent $path
[IO.Directory]::CreateDirectory($directory) | Out-Null
$utf8WithoutBom = [Text.UTF8Encoding]::new($false)
$content = $runbook.TrimStart() + [Environment]::NewLine
[IO.File]::WriteAllText($path, $content, $utf8WithoutBom)
if (-not (Test-Path -LiteralPath $path)) { throw 'Runbook write failed.' }
~~~

- [ ] Step 4: Run GREEN and every release-gate command

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_docs.py" "tests/test_v8_execution_kernel_integrity.py" "tests/test_v8_production_effects.py" "tests/test_v8_production_host.py" "tests/test_v8_production_replanning.py" "tests/test_v8_production_composition_e2e.py" -q
py -3.13 -m pytest -q
py -3.13 scripts/quick_validate.py
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
~~~

Expected: all focused tests, full pytest, quick validation, package sync, and diff check pass. If Task 8 changed `SKILL.md` or `gwo_v8/__init__.py`, run `py -3.13 scripts/sync_orchestrator.py` first, stage and commit only the two generated `.skill-package.json` manifests with that Skill change, then run `py -3.13 scripts/sync_orchestrator.py --check`; the check must report no drift. The final evidence records the exact sync and check commands.

Read-only Beta2 issue/CI readback:

~~~powershell
gh issue view 113 --repo NOirBRight/github-work-orchestrator --json number,state
gh issue view 114 --repo NOirBRight/github-work-orchestrator --json number,state
gh issue view 115 --repo NOirBRight/github-work-orchestrator --json number,state
gh issue view 116 --repo NOirBRight/github-work-orchestrator --json number,state
gh issue view 117 --repo NOirBRight/github-work-orchestrator --json number,state
gh issue view 137 --repo NOirBRight/github-work-orchestrator --comments --json number,state,body,comments
$sha = git rev-parse HEAD
gh run list --repo NOirBRight/github-work-orchestrator --commit $sha --workflow 'GWO CI' --status success --limit 1 --json databaseId,headSha,conclusion,url
~~~

Expected: #113–#117 are CLOSED, #137 is CLOSED only after either the recorded Beta1 OPEN-path approval or the post-merge manual OPEN-path approval, successful revalidation, a separate close approval, and a consistent final tracker readback. The CI headSha equals the exact merged SHA and writer generation before/after the isolated run is identical. If #137 is closed with an open #114/#115 blocker, stop with NO-GO; do not repair the tracker in this task.

- [ ] Step 5: Refactor docs and commit the final Beta2 gate slice

~~~powershell
py -3.13 -m pytest "tests/test_v8_production_docs.py" -q
git add "docs/operations/gwo-v8-production-composition.md" "tests/test_v8_production_docs.py"
git commit -m "docs: define the Beta2 production composition gate"
~~~

## Final self-review before handing the plan to an implementer

Run this review from the assigned checkout after saving the plan; it is read-only and does not stage or commit anything:

~~~powershell
$repoRoot = (git rev-parse --show-toplevel).Trim()
if (-not $repoRoot) { throw 'Run from a GWO implementation worktree.' }
Set-Location -LiteralPath $repoRoot
$plan = 'docs/superpowers/plans/2026-08-03-gwo-v8-production-composition.md'
if (-not (Test-Path -LiteralPath $plan)) { throw 'Plan file is missing.' }
$text = Get-Content -Raw -LiteralPath $plan
$forbidden = @(
    [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('VEJE'))
    [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('VE9ETw=='))
    [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('dG8gYmUgZGVjaWRlZA=='))
    [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('ZmlsbCBpbg=='))
    [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('aW1wbGVtZW50IGxhdGVy'))
)
if ($forbidden | Where-Object { $text.Contains($_) }) {
    throw 'Plan has non-concrete text.'
}
if ($text -notmatch 'RED' -or $text -notmatch 'Expected: FAIL' -or $text -notmatch 'Expected: PASS') { throw 'Plan is missing explicit TDD evidence.' }
if ($text -notmatch 'ProductionWorkRunEffects' -or $text -notmatch 'ProductionGwoHost' -or $text -notmatch 'ResultIntegrityProof' -or $text -notmatch 'CampaignWatchdog') { throw 'Plan is missing a required production seam.' }
if ($text -notmatch 'gh issue reopen 137') { throw 'Plan is missing the human-approved #137 reopen checkpoint.' }
if ($text -notmatch 'Beta1' -or $text -notmatch 'Beta2' -or $text -notmatch 'Beta3' -or $text -notmatch 'GA') { throw 'Plan is inconsistent with the release train.' }
$mergedOwnerNames = @(
    'WorkRunAction'
    'WorkRunObservation'
    'StaleBindingObservation'
    'StaleDiagnosisObservation'
    'CandidateReceipt'
    'AcceptedCandidateReceipt'
    'CandidateGateResult'
    'CandidateGate'
    'BatchIntegrator'
    'CampaignWatchdog'
    'PlanInvalidationObservation'
    'RuntimeGateway'
    'ExecutionKernel'
    'PlanControl'
)
$insidePythonFence = $false
$ownerRedefinitions = @(
    foreach ($line in Get-Content -LiteralPath $plan) {
        if ($line -eq '~~~python') {
            $insidePythonFence = $true
            continue
        }
        if ($insidePythonFence -and $line -eq '~~~') {
            $insidePythonFence = $false
            continue
        }
        if (
            $insidePythonFence -and
            $line -match '^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\b' -and
            $Matches[1] -in $mergedOwnerNames
        ) {
            $line
        }
    }
)
if ($ownerRedefinitions.Count -ne 0) {
    throw 'Plan redeclares a merged owner type instead of editing its existing class body.'
}
git diff --check -- $plan
~~~

Coverage checklist:

- Architecture/spec: five-module ownership, public three-operation seam, five statuses, Watchdog-as-adapter, RuntimeGateway-only provider access, CandidateGate-only Review access, BatchIntegrator-only delivery, Result identity, no cross-Campaign Batch, no automatic daemon restart, and no authority expansion are covered by Global Constraints and Tasks 3–7.
- Kernel integrity: CAS, state digest, no transaction across external I/O, exact Result proof copied field-for-field from the unique direct-or-fallback `BatchDeliveryProof`, proof-digest tamper rejection, Candidate-only non-completion, delivery action identity, Plan Invalidation handoff, merged dataclass field/decorator preservation, in-class serializers without owner-type redeclaration, crash windows, restart, and read-only inspect are covered by Tasks 2–4 and 7.
- Production composition: exact Runtime/Candidate/Batch ports, stable action mapping, receipt ledger, ProductionGwoHost, async Planning continuation, Watchdog delegation, writer non-mutation, and predecessor reachability are covered by Tasks 1, 4, 5, 7, and 8.
- #137: the human-approved reopen checkpoint and all four Candidate/Review/Repair/ordinary paths plus replay/restart and Evidence lineage are covered by Task 6.
- Isolation and operations: temporary target by default, opt-in real provider, operational runbook, evidence bundle, and Beta2 go/no-go are covered by Tasks 9–10.
- Release train: Beta1 remains metadata/tracker repair only, Beta2 is the output of this plan without writer cutover, Beta3 follows #118, and GA follows the real #119 Canary plus Activation read-back.
- Write-set consistency: #113 owns Watchdog internals, #114/#115 own CandidateGate internals, #116/#117 own BatchIntegrator internals, #118 owns final predecessor removal and writer Guard, and this plan owns only composition, Kernel hardening, host-private seams, integration tests, Skill guidance, and operations docs. The self-review must treat `skills/orchestrator/.skill-package.json` and `skills/implement-gwo/.skill-package.json` as real shared writes, verify that Tasks 1/2/3/4/5/7/8 are serialized around the manifests they touch, verify that every such commit has sync -> `--check` -> manifest staging in one commit, and verify that only lanes touching neither manifest are eligible for parallel package-lane execution.

The plan is complete only when this self-review has zero findings and the final implementation handoff identifies the exact task order and commit boundary; no implementation command is executed as part of writing this file.
