# GWO V8 Beta2 Production Composition Runbook

This runbook describes the Beta2 isolated-preview composition. It is a local
verification procedure, not a production cutover procedure. Beta2 admits no
production work, does not activate the default writer, and does not publish a
hosted release gate.

The composed host is ProductionGwoHost with
ProductionWorkRunEffects. The literal invariant is: writer activation is
disabled. Beta2 is not a production admission or writer-authority transfer.

## Public boundary and module ownership

The public workflow has exactly three operations:

    start(repository, ready_refs, options?)
    advance(campaign_handle, wake_ref?)
    inspect(campaign_handle)

start returns a stable Campaign handle. Every runtime, watchdog, provider,
Candidate, and delivery wake is routed through advance. inspect is read-only
and returns the durable Diagnostics projection. Public statuses are Complete,
Running, Decision, Wait, and Blocked.

The five deep module owners are:

| Module | Owns |
| --- | --- |
| PlanControl | source snapshots, Plan Revisions, planning, and activation receipts |
| ExecutionKernel | the persisted Campaign state machine and next action |
| RuntimeGateway | provider/runtime calls and authoritative Runtime readback |
| CandidateGate | Candidate, Formal Review, Repair Verification, and scope escape |
| BatchIntegrator | Batch delivery, Git/GitHub boundaries, checks, and target readback |

CampaignWatchdog is only a wake adapter. It never becomes a second workflow
driver. RuntimeGateway is the only provider boundary, CandidateGate is the only
Candidate/Review/Repair entry, and BatchIntegrator is the only delivery
boundary.

## Safe composition configuration

Every Beta2 host must use a temporary Git target strictly below a temporary
isolation root:

    target = create_temporary_target(root)
    assert_isolated_e2e_target(target, root)

    ProductionHostConfiguration(
        preview_mode="beta2_isolated_preview",
        target_isolation_root=root,
        writer_activation_enabled=False,
    )

target_isolation_root and target are resolved before installation. The target
must be a strict child of the root and must contain .git. A normal checkout,
a real repository, the root itself, or a path outside the root is rejected with
REAL_E2E_TARGET_NOT_ISOLATED before the host is installed. The source checkout
must never be used as the E2E target. create_temporary_target also rejects an
explicit isolation root that is itself a canonical or linked Git worktree
before calling mkdir or creating a child, and does not mutate that checkout.

The default E2E uses the recording provider and the temporary target. The real
provider path is opt-in only and fail-closed: this repository has no safe real
provider subprocess adapter. The implementation fails closed. It skips unless both
GWO_V8_REAL_PROVIDER_E2E=1 and a non-empty GWO_V8_REAL_PROVIDER_COMMAND are
present:

    $env:GWO_V8_REAL_PROVIDER_E2E = '1'
    $env:GWO_V8_REAL_PROVIDER_COMMAND = '<approved provider command>'
    py -3.13 -m pytest tests/test_v8_production_composition_e2e.py -k real_provider -q

An opt-in request still uses create_temporary_target. When opt-in is enabled,
the installer rejects the request with REAL_PROVIDER_UNSUPPORTED and fails
closed before creating an evidence directory or installing a host; it never
executes an arbitrary command. The opt-in case is a diagnostic unsupported-
path check, not a claim of provider identity or effect readback. It must not
target the source checkout, a normal real repository, GitHub, or a production
writer. Provider completion text is not Evidence.

## CAS, external effects, and restart

The restart order is effect-first and readback-first:

1. Read the persisted Campaign state and its SQLite CAS version.
2. Reconstruct the pending stable action from the durable state and receipts.
3. Read back the external effect by its stable identity before creating or
   retrying anything.
4. Validate the exact action, receipt, and target identity; never infer success
   from a callback, log, workspace head, or provider statement.
5. Persist the resulting state with SQLite compare-and-swap.
6. Perform an immediate readback of the CAS row and the effect receipt.

If a callback is lost, restart repeats this order. A durable terminal receipt is
adopted once; it does not cause a second provider, Git, hosted-check, or target
effect. inspect remains read-only during restart and never repairs state.

## Result acceptance

A Candidate is neither Evidence nor a Result. A code Result is accepted only
when the exact Candidate receipt, accepted-Candidate receipt, immutable Batch
delivery proof, local/provider-isolation verification, and target readback all
bind to the same Campaign, Plan Revision, Candidate, and delivery action.

The Batch delivery proof is represented by BatchDeliveryProof. The
BatchDeliveryProof must identify the exact Batch and member set and carry
the local check, publication receipt, hosted-result observation,
Integration-Lease receipt, target branch/head, target read-back digest, and
target containment proof. The Result digest is computed from that exact proof
and its Evidence digests. A failed multi-member Batch has at most one
Singleton fallback; it is never treated as delivered.
Candidate-only or accepted-Candidate-only completion remains
accepted_awaiting_delivery and cannot create a Result.

## #137 approval sequence

The #137 boundary is separate from ordinary composition:

1. Obtain the approved reopen path and perform the open approval readback.
2. Confirm #114/#115 are merged, then run the #137 revalidation cases:
   Candidate, Formal Review, Repair, ordinary rejection, replay, and restart.
3. Persist each readback and its digest in the nine-key
   issue_137_revalidation object.
4. Obtain an independent close approval, read back CLOSED, and only then
   include #137 in the closed issue-state set.

No prior close state substitutes for the open approval or the post-merge
revalidation readback.

## Local Verification Only evidence

The evidence writer emits
gwo-v8-beta2-composition-evidence.v2 with verification_mode equal to Local
Verification Only. It binds the exact subject sha, tree, and parents, the
Campaign handle, Plan Revision digest, equal writer-generation readbacks,
Result and Batch proof digests, the #137 revalidation digests, and the local
verification manifest digest. workflow_count is exactly zero and
writer_activation_enabled is exactly false. The full_gate keys are pytest,
quick_validate, package_sync, diff_check, and clean_status; their fixture
statuses do not prove that the repository-wide gates ran.

The bundle is rendered as canonical sorted JSON with a final newline. The
writer parses a same-directory temporary file before replacement, reloads the
final file, and compares both the parsed object and SHA-256. It emits no CI
URL, hosted repository-check field, or workflow-run field. There is no CI URL
in the Beta2 bundle. The output filename is
beta2-composition-fixture.json and identifies a partial composition artifact;
it is not Task 10 GO evidence and is not writer activation.

Run the following Local Verification Only commands from the isolated worktree:

    py -3.13 -m pytest tests/test_v8_production_composition_e2e.py tests/test_v8_production_docs.py -q
    py -3.13 -m pytest -q
    py -3.13 scripts/quick_validate.py
    py -3.13 scripts/sync_orchestrator.py --check
    git diff --check

Record the focused and regression results, the local-manifest digest, the
subject SHA/tree readback, the Result/Batch/target readbacks, and clean Git
status in the local evidence report. No hosted CI URL is required or accepted
by the Beta2 evidence schema.

## Handoff boundary

Task 9 is a no-production, no-writer Beta2 preview: no production and no writer
are admitted. It does not activate the default writer, change writer generation,
admit a root production target,
close #118, or perform a cutover. Issue #118 owns the later fail-closed
cutover Guard; the real root Canary and writer activation are outside this
runbook. Do not use this runbook to bypass that handoff.
