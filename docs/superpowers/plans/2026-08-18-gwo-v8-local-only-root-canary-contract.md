# GWO V8 Local-Only Root Canary Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Each task is independently testable and uses TDD.

**Goal:** Make #119 a fail-closed `local-only-v1` root acceptance over the real #195–#198 Ticket manifest, with local Git Batch/Lease/target evidence and no PR or Hosted-CI claim.

**Architecture:** Keep the public `gwo_v8.start/advance/inspect` harness and production BatchIntegrator contracts unchanged for product delivery. Add a manifest-backed root layout and a separate local-only evidence projection for the acceptance runner; the projection has its own local Batch proof schema and never serializes internal PR/Hosted-CI fields. Extend the root verifier with an explicit local-only branch while retaining the old hosted fixture path as legacy parser coverage.

**Tech Stack:** Python 3.13, pytest, canonical JSON/SHA-256, temporary Git repositories, SQLite journals, existing V8 public API and PlanControl/ExecutionKernel harness.

**Spec:** `docs/superpowers/specs/2026-08-18-gwo-v8-local-only-root-canary-contract-design.md`

## Global Constraints

- The canonical root acceptance mode is exactly `local-only-v1`.
- The authoritative root Ticket manifest is `gwo-v8-root-canary-tickets.v2` and must contain real Issues #195, #196, #197, and #198 in explicit alpha/beta/gamma/delta order.
- Local evidence contains no PR number/head, PR merge mapping, Hosted-CI suite/run/check, publication receipt, workflow URL, or nullable placeholder for those concepts.
- The local runner uses only an isolated temporary Git repository, temporary SQLite, deterministic local Runtime/Review doubles, and local readback; it performs no GitHub/Paseo/production mutation.
- Production Activation, Activation Receipt, writer transition, default-writer readback, and `v8.0.0` publication are out of scope.
- Existing production BatchIntegrator and hosted-delivery tests remain valid; local-only acceptance must not weaken them.
- Every behavior change follows TDD: write one focused failing test, run it and record the expected RED, implement the minimum change, then run the focused GREEN suite.

## File Map

- Modify `scripts/provision_v8_root_canary.py`: expose strict read-only manifest loading/validation for the local runner and require the real #195–#198 layout when requested.
- Modify `tests/test_v8_root_canary_tickets.py`: cover the real-number manifest adapter and fail-closed malformed/reordered/tampered inputs.
- Modify `scripts/run_v8_local_acceptance.py`: accept the validated manifest, run the root scenario with actual Issue refs, and emit the local-only projection.
- Modify `tests/test_v8_local_acceptance.py`: assert #195–#198, explicit mode, local proof fields, no Hosted/PR leakage, and restart/idempotency behavior.
- Modify `scripts/verify_v8_root_canary.py`: add local-only mode, local Batch proof validation, recursive forbidden Hosted/PR rejection, and digest binding while retaining legacy hosted fixture parsing.
- Modify `tests/test_v8_root_canary_acceptance.py`: cover local valid/tampered/missing-mode/Hosted-field cases and local CLI round-trip.
- Modify `scripts/render_v8_ga_metadata.py`: remove repository-release wording that claims exact CI and render `local-only-v1` consistently.
- Modify `scripts/verify_v8_ga_release.py`: make the canonical repository release verification mode `local-only-v1` and require explicit workflow-disabled evidence without changing production Hosted-CI support.
- Modify `tests/test_v8_release_metadata.py`: cover missing/false workflow-disabled proof and renderer/contract wording.
- Modify `docs/releases/gwo-v8-ga-release-contract.md`, `docs/releases/gwo-v8-release-train.md`, `docs/e2e/gwo-v8-root-canary.md`, and `docs/design/gwo-v8-lean-roadmap.md`: align the written #119 and repository-release boundary.
- Create `docs/superpowers/specs/2026-08-18-gwo-v8-local-only-root-canary-contract-design.md` and this plan.
- Create the SDD ledger under `.superpowers/sdd/2026-08-18-gwo-v8-local-only-root-canary-contract/`.

---

### Task 1: Strict real Ticket manifest adapter

**Files:**
- Modify: `scripts/provision_v8_root_canary.py`
- Test: `tests/test_v8_root_canary_tickets.py`
- Create: `tests/fixtures/gwo-v8-root-canary-tickets-195-198.json`

**Interfaces:**
- Produce `load_ticket_manifest(path: Path, *, require_real_root_numbers: bool = False) -> dict[str, object]`.
- `require_real_root_numbers=True` accepts exactly `ready_refs == ["issue:195", "issue:196", "issue:197", "issue:198"]`, validates every complete v2 Ticket object and source digest, and returns a detached mapping.
- Preserve the existing writer and read-only provisioning APIs.

- [ ] **Step 1: Write the failing tests.** Add tests for the exact four real refs, duplicate ref rejection, changed `source.digest` rejection, reordered refs rejection, and repository mismatch rejection.

- [ ] **Step 2: Run the focused tests to verify RED.**

  ```powershell
  py -3.13 -m pytest tests/test_v8_root_canary_tickets.py -k "real_manifest or manifest_loader" -q
  ```

  Expected: FAIL because the loader and real-number option do not exist.

- [ ] **Step 3: Implement the minimum loader.** Reuse the module's canonical JSON and existing v2 validation rules; reject non-canonical or non-object input, validate all four complete entries, and enforce the explicit four-number set only when the option is true. Do not infer assurance or batch from Issue numbers.

- [ ] **Step 4: Add the checked-in fixture from an authoritative read-only readback.** The fixture contains the complete #195–#198 Issue contracts, current labels, comments, blockers, `ready_refs`, and digests. Do not add a write token or mutation command.

- [ ] **Step 5: Run GREEN and the existing ticket suite.**

  ```powershell
  py -3.13 -m pytest tests/test_v8_root_canary_tickets.py -q
  ```

- [ ] **Step 6: Commit the task.**

  ```powershell
  git add scripts/provision_v8_root_canary.py tests/test_v8_root_canary_tickets.py tests/fixtures/gwo-v8-root-canary-tickets-195-198.json
  git commit -m "test: add strict real root canary ticket manifest adapter"
  ```

### Task 2: Manifest-backed local root producer

**Files:**
- Modify: `scripts/run_v8_local_acceptance.py`
- Test: `tests/test_v8_local_acceptance.py`

**Interfaces:**
- Extend `run_local_acceptance(*, root: Path, run_id: str, scenario: str, tickets: Path | Mapping[str, object] | None = None) -> dict[str, Any]`.
- Root mode requires/loads the Task 1 manifest and uses its four `issue:` refs as PlanControl ready refs.
- Emit `schema_version = "gwo.v8.local-root-acceptance.v1"` and `acceptance_mode = "local-only-v1"` for the local root record.
- Emit `local_evidence` with `local_batch_proof.v1` records. Each record contains local suite, Batch ref/SHA, Lease, target before/after, ancestry, and proof digest only.

- [ ] **Step 1: Write failing producer tests.** Add assertions that a root run with the fixture uses `issue:195`–`issue:198`, has the explicit mode, has two local Batch proofs with the expected member partitions, and has no recursively forbidden Hosted/PR field names.

- [ ] **Step 2: Run the tests to verify RED.**

  ```powershell
  py -3.13 -m pytest tests/test_v8_local_acceptance.py -k "local_only or real_root_ticket" -q
  ```

  Expected: FAIL because the runner has no manifest input and emits the old hosted-shaped root readback.

- [ ] **Step 3: Implement the manifest-backed layout.** Thread a small immutable root layout through the existing snapshot, CandidateGate, reviewer, Batch grouping, and evidence helpers. Use the manifest contract digest for the PlanControl snapshot and Candidate parent instead of synthesizing a second Ticket contract. Keep deterministic Candidate variants and local Git commits, but bind them to the actual Ticket refs.

- [ ] **Step 4: Implement the local evidence projection.** Project internal observations into a separate local-only mapping. Do not copy `pull_request`, `pr`, `hosted_*`, `publication`, workflow, or remote-target fields; do not emit `None` placeholders. Preserve local suite receipt, local batch ref/SHA, Lease digest, target CAS/readback, Candidate/Review/Result links, and all recovery facts.

- [ ] **Step 5: Add CLI manifest input and fail closed.** Add `--tickets`; root mode without a valid real manifest returns a named error and does not create production files. Single/wait/blocked/failure scenarios remain local and unchanged.

- [ ] **Step 6: Run GREEN and the complete local acceptance suite.**

  ```powershell
  py -3.13 -m pytest tests/test_v8_local_acceptance.py -q
  py -3.13 scripts/run_v8_local_acceptance.py --root "$env:TEMP\gwo-v8-local-root" --tickets tests/fixtures/gwo-v8-root-canary-tickets-195-198.json --run-id local-root-contract --scenario root
  ```

- [ ] **Step 7: Commit the task.**

  ```powershell
  git add scripts/run_v8_local_acceptance.py tests/test_v8_local_acceptance.py
  git commit -m "feat: emit manifest-backed local root acceptance evidence"
  ```

### Task 3: Local-only root verifier

**Files:**
- Modify: `scripts/verify_v8_root_canary.py`
- Test: `tests/test_v8_root_canary_acceptance.py`

**Interfaces:**
- Add explicit `acceptance_mode` to the immutable acceptance receipt and its canonical digest payload.
- Add a local Batch validation path that returns `VerifiedBatch` without PR/Hosted-CI fields and validates local suite, local Lease, local target CAS/readback, Batch ref, and ancestry.
- Keep legacy hosted fixture parsing available only for existing historical tests; an explicit `local-only-v1` bundle can never fall through to the hosted path.

- [ ] **Step 1: Write failing verifier tests.** Add a valid local fixture, missing-mode rejection, wrong-mode rejection, local bundle containing `hosted_ci`/`pull_request` rejection, local target SHA mismatch, local Lease mismatch, and receipt digest change when mode/evidence changes.

- [ ] **Step 2: Run RED.**

  ```powershell
  py -3.13 -m pytest tests/test_v8_root_canary_acceptance.py -k "local_only or local_batch" -q
  ```

  Expected: FAIL because the current verifier only understands the hosted Batch shape and does not bind an acceptance mode.

- [ ] **Step 3: Implement explicit mode dispatch.** Read `acceptance_mode` before Batch validation. For `local-only-v1`, reject forbidden fields recursively in the selected local evidence, require the local Batch schema, and use local diagnostics. Do not accept a missing Hosted field or a standalone Hosted receipt digest as local proof.

- [ ] **Step 4: Bind mode and complete local authoritative evidence.** Add the mode and local Batch proofs to the receipt digest payload and preserve them in `authoritative_evidence`. Emit `gwo-v8-root-canary-acceptance.v2` for local receipts; keep v1 wording for legacy hosted fixtures.

- [ ] **Step 5: Add end-to-end CLI round-trip.** Run the producer with the real fixture, write its JSON, run `verify_v8_root_canary.py --tickets ... --diagnostics ... --output ...`, and assert a canonical local receipt with the exact mode and both local Batch partitions.

- [ ] **Step 6: Run GREEN and all root verifier tests.**

  ```powershell
  py -3.13 -m pytest tests/test_v8_root_canary_acceptance.py tests/test_v8_local_acceptance.py -q
  ```

- [ ] **Step 7: Commit the task.**

  ```powershell
  git add scripts/verify_v8_root_canary.py tests/test_v8_root_canary_acceptance.py
  git commit -m "feat: verify the local-only root canary contract"
  ```

### Task 4: Repository release contract alignment

**Files:**
- Modify: `scripts/verify_v8_ga_release.py`
- Modify: `scripts/render_v8_ga_metadata.py`
- Test: `tests/test_v8_release_metadata.py`
- Modify: `docs/releases/gwo-v8-ga-release-contract.md`
- Modify: `docs/releases/gwo-v8-release-train.md`
- Modify: `docs/e2e/gwo-v8-root-canary.md`
- Modify: `docs/design/gwo-v8-lean-roadmap.md`

**Interfaces:**
- Canonical repository release verification mode is `local-only-v1` (accept legacy spelling only at the existing explicit compatibility boundary).
- Local verification input must explicitly prove `workflow_count == 0`, Actions disabled, successful full pytest, exact subject SHA/tree, and no recursively nested Hosted/CI fields.
- Rendered metadata and committed contract must both say Local Verification Only; product Hosted-CI remains explicitly separate and is not satisfied by repository release evidence.

- [ ] **Step 1: Write failing tests.** Cover missing workflow-disabled proof, enabled Actions, nonzero workflow count, Hosted/CI field injection, renderer text containing “exact CI”, and contract/template mismatch.

- [ ] **Step 2: Run RED.**

  ```powershell
  py -3.13 -m pytest tests/test_v8_release_metadata.py -k "workflow or renderer or local_only" -q
  ```

- [ ] **Step 3: Implement strict local release readback.** Require the explicit disabled-workflow fields for the canonical v1 manifest and preserve the existing production Hosted-CI compatibility types without allowing the CLI local release path to consume them.

- [ ] **Step 4: Align renderer and documents.** Replace “exact CI” release claims with `local-only-v1`; add a boundary matrix stating repository verification is local-only while product Batch Hosted-CI is a separate concern.

- [ ] **Step 5: Run GREEN.**

  ```powershell
  py -3.13 -m pytest tests/test_v8_release_metadata.py -q
  ```

- [ ] **Step 6: Commit the task.**

  ```powershell
  git add scripts/verify_v8_ga_release.py scripts/render_v8_ga_metadata.py tests/test_v8_release_metadata.py docs/releases/gwo-v8-ga-release-contract.md docs/releases/gwo-v8-release-train.md docs/e2e/gwo-v8-root-canary.md docs/design/gwo-v8-lean-roadmap.md
  git commit -m "docs: align V8 release acceptance with local-only verification"
  ```

### Task 5: Integration, review package, and local gate

**Files:**
- Modify: `.superpowers/sdd/2026-08-18-gwo-v8-local-only-root-canary-contract/progress.md`
- Create: `docs/evidence/gwo-v8-local-root-canary-2026-08-18.json` only if the final evidence is intentionally retained; otherwise keep it under a temporary directory.

- [ ] **Step 1: Run focused suites from Tasks 1–4 and inspect the diff.**

  ```powershell
  py -3.13 -m pytest tests/test_v8_root_canary_tickets.py tests/test_v8_local_acceptance.py tests/test_v8_root_canary_acceptance.py tests/test_v8_release_metadata.py -q
  ```

- [ ] **Step 2: Run static and forbidden-field checks.**

  ```powershell
  py -3.13 -m ruff check scripts tests
  py -3.13 -m compileall -q scripts skills tests
  rg -n "hosted_ci|hosted_check|pull_request|workflow_run|ci_run_id|publication_receipt" "$env:TEMP\gwo-v8-local-root\local-record.json"
  ```

  Expected: no forbidden field names in the local acceptance record; existing production Hosted-CI code may still contain its own contract fields.

- [ ] **Step 3: Execute the local root gate and verifier.** Generate a fresh temporary record using the #195–#198 fixture, verify it with the read-only verifier, and compare two independent roots for deterministic canonical output. Record `LOCAL_ROOT_CANARY_GO` and the receipt digest in the ledger.

- [ ] **Step 4: Run the full local pytest suite.** GitHub Actions is not part of the decision.

  ```powershell
  py -3.13 -m pytest -q
  ```

- [ ] **Step 5: Dispatch the broad whole-branch review.** Review spec compliance, quality/security, TDD RED/GREEN evidence, and all deferred findings. Fix only through one reviewed SDD fix wave if needed.

- [ ] **Step 6: Commit the final evidence/ledger update.** Do not merge, push, activate production, create an Activation Receipt, or create the `v8.0.0` tag in this plan.

