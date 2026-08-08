# GWO V8 C2 Beta2 Feature Complete Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consume the completed C1 closure, land and close #113–#117, revalidate and close #137, compose the five V8 deep modules behind the isolated `start`/`advance`/`inspect` host, and publish `v8.0.0-beta.2` with exact local evidence while transferring no writer authority.

**Architecture:** C2 is a release campaign with one serialized package-manifest implementation lane, one post-host parallel fork for Skill guidance versus isolated E2E/docs, and one serialized release-control lane. It consumes exact C1 and protected-GA evidence, advances the already-reviewed #113–#115 slices, completes BatchIntegrator and recovery, revalidates the late scope-escape seam, then executes the existing Production V3 composition plan with a Local Verification Only amendment. All repository and tracker mutations remain behind effect-specific owner approval, lease, immutable authorization, immediate readback, and idempotent resume.

**Tech Stack:** Python 3.13, pytest, PowerShell 7, Git, GitHub CLI readback, canonical JSON, SHA-256, SQLite compare-and-swap, PlanControl, ExecutionKernel, RuntimeGateway, CandidateGate, BatchIntegrator, CampaignWatchdog, and temporary isolated Git targets.

## Global Constraints

- C2 starts only after the separately executed C1 R3 predecessor has actually produced a valid `gwo-v8-c1-closure.v2` and `gwo-v8-c2-handoff.v1`; a plan commit or proposed C1 state is not release evidence.
- At plan-authoring time, `origin/main` is `4c18210490e7cd6c79b626ac516c8dd6d10790f8` (tree `17ff8fd2527140131f6004552942f507ddf10e4e`, parent `928789c2c0d559d14894b8cfdab8bff3b41acc3d`). It contains the merged C1 planning documents, not the executed C1 Beta1 closure.
- The existing `D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview/state.json` is not a valid C2 predecessor: it has empty `closure`/`c2_handoff` objects and null local-verification/approval slots. The merged C1 R2 plan is also stale because it froze `928789c2c0d559d14894b8cfdab8bff3b41acc3d` before the `4c18210490e7cd6c79b626ac516c8dd6d10790f8` plan merge. Re-freeze and execute C1 from the current `origin/main` as R3, writing the exact predecessor root below, before Task 0 can pass.
- Verification mode is exactly **Local Verification Only**. GitHub Actions acceptance remains disabled. No remote provider check, workflow run, or status rollup is a C2 release gate.
- Beta2 admits no production work and does not activate the default writer. Every executable V8 host uses `preview_mode="beta2_isolated_preview"` and `writer_activation_enabled=False` against a target beneath a temporary isolation root.
- The public workflow remains exactly `start(repository, ready_refs, options?)`, `advance(campaign_handle, wake_ref?)`, and `inspect(campaign_handle)`. Public statuses remain Complete, Running, Decision, Wait, and Blocked.
- The five deep modules remain PlanControl, ExecutionKernel, RuntimeGateway, CandidateGate, and BatchIntegrator. CampaignWatchdog is a wake adapter, not a sixth state machine.
- CandidateGate remains the only Candidate, Formal Review, and Repair Verification entry. BatchIntegrator remains the only delivery boundary. ExecutionKernel remains the only persisted Campaign workflow driver.
- A Candidate is neither Evidence nor a Result. A code Result requires the exact Candidate receipt, accepted-Candidate receipt, immutable Batch delivery proof, local/provider-isolation verification evidence, and target readback.
- The protected GA branch `refs/heads/codex/gwo-v8-ga-plan` remains fixed at `2cd6c46e1484ca140c3a197bbdeb171191d70c20`. C2 does not rewrite or advance it.
- C2 treats these as candidate implementation boundaries named by the C1 contract, not as current-main or release evidence: foundation `77ac3e3ef14241d1840150b22cb227d2e5088fb4`, #113 `07086ce1036198a41547ca1d9a9a506acfb8fcf7`, #114 `657bf236d765735cdee117910a5939c6c2cd3292`, #115 `a0f697656be6471bed601103c169185988a9e4ac`, and #116 WIP `e58c596998df90e65349bdb4b5f25d3d9dc1f7e2`. Task 0/1 must resolve and re-hash them from the validated C1 R3 handoff before any implementation lane starts.
- Dynamic C1 and C2 SHA values come only from digest-validated state/readback. Do not copy the historical C1 baseline into a C2 mutation command.
- The authoritative child plans are read from protected GA commit `2cd6c46e1484ca140c3a197bbdeb171191d70c20` with `git show <sha>:docs/superpowers/plans/2026-08-03-gwo-v8-batch-delivery.md` and `git show <sha>:docs/superpowers/plans/2026-08-03-gwo-v8-production-composition.md`. They need not exist in the C1 checkout and their broad protected-GA tree is never merged. This plan supersedes only their remote acceptance assumptions; their product contracts and TDD cases remain binding.
- Product-level hosted-result and retry semantics remain tested through local deterministic drivers and an isolated provider adapter. Disabling remote acceptance does not delete those BatchIntegrator contracts.
- Every source change follows RED, observed RED, minimum GREEN, observed GREEN, refactor while green, focused verification, package synchronization, and a small commit.
- Use at most five concurrent subagents. Every subagent uses `gpt-5.6-luna` with max reasoning. Tasks sharing any source, test-support, generated manifest, evidence writer, or release-state file are serialized.
- The five-subagent scheduler has one repository/evidence writer slot and at most four read-only review/readback slots. Tasks 4–6 have one implementation writer only; Tasks 8 and 9 may use two disjoint implementation writers after the production gate, while their reviews are queued within the remaining slots. Every worker uses its own worktree and may write only its named files; no worker writes C2 `state.json`, owner approvals/leases, policy snapshots, authorizations, receipts, tracker snapshots, or publication files.
- `skills/orchestrator/.skill-package.json` and `skills/implement-gwo/.skill-package.json` are generated write-set members. A lane touching either manifest is serialized with every other lane touching the same manifest.
- The coordinator is the sole writer of `D:/gwo-release-evidence/2026-08-06-gwo-v8-c2-beta2-feature-complete/state.json`, `policy/`, `authorizations/`, `receipts/`, and publication/closure state. Each child may create only a uniquely named review or local-test evidence file beneath its assigned lane directory; the coordinator hashes and adopts it after the child exits.
- No SQLite transaction stays open across RuntimeGateway, CandidateGate, BatchIntegrator, Git, GitHub, provider, or watcher calls.
- No push, PR mutation, merge, Issue mutation, tag, Release, or canonical-main fast-forward occurs without an effect-specific owner approval and exclusive writer lease whose original bytes and SHA-256 are persisted and reloaded immediately before the effect.
- Every remote effect is effect-first on resume: discover the visible effect, validate its immutable authorization and policy receipt, then adopt it; never create a second effect after an ambiguous timeout.
- C2 does not start #118, create an Activation Receipt, change writer generation, admit a real production target, close #119, or publish `v8.0.0`.

---

## Current Planning Baseline and C1 R3 Entry

This file is a C2 implementation plan, not proof that C1 is complete. The
only repository mutation already observed for the current baseline is the
documentation-only squash at `origin/main=4c18210490e7cd6c79b626ac516c8dd6d10790f8`.
It does not satisfy the C1 closure gate. A separate C1 R3 run must first be
re-frozen from that exact SHA and must write this exact external input:

```text
D:/gwo-release-evidence/2026-08-06-gwo-v8-c1-beta1-core-preview-r3/state.json
```

The R3 state must contain a digest-valid `gwo-v8-c1-closure.v2` and
`gwo-v8-c2-handoff.v1`, a merged-main identity descended from the current
`origin/main`, and the unfinished scope exactly `issue_117_completion` and
`final_issue_137_revalidation`. The old 2026-08-05 state and the unexecuted
R2 plan are historical inputs only. Until R3 exists and passes Task 0, C2
creates no state, dispatches no implementation worker, mutates no Issue, and
publishes no Beta2 object.

The live tracker readback at authoring time is also only a precondition
observation: #113–#117 are OPEN, #137 is CLOSED, #136 is CLOSED, and #118/#119
are OPEN. C1 R3 must acquire and validate its own owner authorization for the
conditional #137 reopen effect when the preserved #114/#115 state requires it;
that authorization does not exist merely because the C1 plan names the effect.
C2 Task 5 never invents a reopen, and C2 Task 10 closes #137 only after its
separate revalidation and close approval.

---

## Inherited State and Evidence Contract

C1 R3 evidence is read from:

```text
D:/gwo-release-evidence/2026-08-06-gwo-v8-c1-beta1-core-preview-r3/state.json
```

The path is a required predecessor output, not an existing authoring fixture.
If it is absent, malformed, hash-inconsistent, or still contains empty
`closure`/`c2_handoff` objects, Task 0 returns HOLD and performs no C2 write.

C2 writes only beneath:

```text
D:/gwo-release-evidence/2026-08-06-gwo-v8-c2-beta2-feature-complete/
```

The C2 state root is `gwo-v8-c2-state.v1` and contains:

```json
{
  "schema": "gwo-v8-c2-state.v1",
  "mode": "Local Verification Only",
  "c1_closure": {},
  "c1_handoff": {},
  "identities": {},
  "feature_slices": {},
  "local_verification": {},
  "reviews": {},
  "approvals": {},
  "mutation_policy": {},
  "mutation_authorization": {},
  "tracker": {},
  "publication": {},
  "closure": {},
  "c3_handoff": {}
}
```

Every stored path uses forward slashes. Every evidence reference includes SHA-256 and schema. Mutable state is written to a fresh temporary file, parsed, atomically replaced, reloaded, and compared before the next effect.

Every gated mutation uses these exact external paths beneath the C2 evidence root:

```text
approvals/repository-owner.json       leases/repository-writer.json
approvals/tracker-owner.json          leases/tracker-writer.json
approvals/publication-owner.json      leases/publication-writer.json
policy/<effect-key>.json
authorizations/<effect-key>.json
receipts/<effect-key>.json
```

Owner-supplied approval/lease bytes use schema `gwo-v8-c2-owner-gate.v1`. The coordinator produces `gwo-v8-c2-policy-readback.v1`, `gwo-v8-c2-effect-authorization.v1`, and `gwo-v8-c2-effect-receipt.v1`. `<effect-key>` is the canonical operation plus immutable target identity, for example `pr-<number>-ready`, `pr-<number>-merge-squash`, `issue-137-close`, `tag-v8.0.0-beta.2`, or `release-v8.0.0-beta.2`. Authorization binds operation, repository, exact before identity, expected after identity, approval/lease/policy digests, owner, validity window, and subject SHA/tree. Receipt binds the authorization digest, command/API readback, actual after identity, URL/object ID where applicable, and readback time. A producer writes only its named schema: owners supply approvals/leases; the coordinator snapshots policy, writes authorization before the effect, and writes the receipt only from immediate readback.

## File and Responsibility Map

| File | C2 responsibility |
| --- | --- |
| `skills/orchestrator/scripts/gwo_v8/_batch_integrator_drivers.py` | Exact local publication/provider/target drivers behind BatchIntegrator. |
| `skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py` | Durable action, lease, retry, fallback, and hosted-result receipt CAS. |
| `skills/orchestrator/scripts/gwo_v8/batch_integrator.py` | #116/#117 immutable Batch action and recovery loop. |
| `skills/orchestrator/scripts/gwo_v8/batch_patch_identity.py` | Exact content-derived Batch patch/tree identity and clean-base advance proof. |
| `skills/orchestrator/scripts/gwo_v8/integration_batch.py` | Direct-import-only predecessor compatibility surface. |
| `skills/orchestrator/scripts/gwo_v8/campaign_watchdog.py` | #113 wake/timer adapter landed as a source slice; it owns no workflow state machine. |
| `skills/orchestrator/scripts/gwo_v8/candidate_gate.py` | #137 Candidate/Review/Repair Plan Invalidation reporting seam; no Campaign disposition authority. |
| `skills/orchestrator/scripts/gwo_v8/candidate_git.py` | #114/#115 authoritative Candidate Git readback and diff identity. |
| `skills/orchestrator/scripts/gwo_v8/runtime_gateway.py` | #113 Runtime wake/recovery contract consumed by the production host. |
| `skills/orchestrator/scripts/gwo_v8/execution_kernel.py` | Campaign CAS, quiescence, exact Result integrity, and Plan Invalidation handoff. |
| `skills/orchestrator/scripts/gwo_v8/production_effects.py` | Host-private Runtime/Candidate/Batch effect adapter and effect receipt ledger. |
| `skills/orchestrator/scripts/gwo_v8/production_host.py` | Isolated ProductionGwoHost assembly and three public operations. |
| `skills/orchestrator/scripts/gwo_v8/plan_control_host.py` | Host-private planning continuation and RuntimeGateway factory. |
| `skills/implement-gwo/SKILL.md` | Beta2 isolated-preview guidance only; no default-writer authority. |
| `tests/v8_batch_test_support.py` | Batch deterministic drivers, crash injection, and evidence fixtures. |
| `tests/v8_candidate_assurance_test_support.py` | #114/#115 Candidate, Review, Repair, and Finding-ledger fixtures. |
| `tests/v8_watchdog_test_support.py` | #113 watchdog wake/timer fixtures. |
| `tests/v8_production_test_support.py` | Composition ports, isolated targets, restart fixtures, and evidence builder. |
| `tests/test_v8_batch_integrator.py` | #116 exact Batch boundary. |
| `tests/test_v8_batch_recovery.py` | #117 retry, fallback, attribution, and restart behavior. |
| `tests/test_v8_batch_beta2.py` | Four-member Beta2 Batch evidence contract. |
| `tests/test_v8_production_replanning.py` | #137 public-seam revalidation. |
| `tests/test_v8_production_composition_e2e.py` | Isolated composition, lost-wake, restart, and local provider/target E2E. |
| `tests/test_v8_production_docs.py` | Beta2 local evidence and runbook contract. |
| `scripts/write_v8_batch_evidence.py` | Deterministic Batch evidence generator/checker. |
| `docs/e2e/gwo-v8-batch-integrator.md` | #116/#117 exact local Beta2 evidence. |
| `docs/operations/gwo-v8-production-composition.md` | Beta2 isolated-preview runbook and go/no-go contract. |
| `docs/releases/v8.0.0-beta.2.md` | Exact merged-SHA release notes and machine-readable evidence block. |

## Dependency and Parallelism DAG

```text
Task 0 C1 closure gate
  -> Task 1 exact lineage and state freeze
  -> Task 2 validate/land #113-#115
  -> Task 3 freeze completion write sets
       -> Task 4 #116 completion
            -> Task 5 #137 CandidateGate repair
                 -> Task 6 #117 + Batch evidence
                      -> G137_OPEN_RECHECK
                           -> Task 7 Production Tasks 1-7 (serial child tasks)
                                -> Task 8 Skill lane -----+
                                -> Task 9 isolated E2E ---+   Task 8 || Task 9
                                                         -> Task 10 Beta2 merge/local-go gate
                                                              -> Task 11 tag/Release/tracker closure/C3 handoff
```

Tasks 4, 5, and 6 are one serial implementation lane: all three regenerate
`skills/orchestrator/.skill-package.json`, and Tasks 4/6 also share Batch source
and support files. Their read-only WIP/code review and focused baseline test
runs may use separate workers concurrently, but no source commit or generated
manifest is shared concurrently. `G137_OPEN_RECHECK` is a fresh tracker and
receipt readback after #114/#115 merge and before Production child Task 6; an
earlier C1 or Task 5 readback is not reusable. Inside Task 7, child Tasks 1–7
remain serial because they share `execution_kernel.py`,
`production_effects.py`, `production_host.py`, shared support, and the
orchestrator manifest. Only Tasks 8 and 9 are implementation-write parallel.

### Task 0: Require the completed C1 closure and freeze the C2 coordinator

**Files:**
- Read: `D:/gwo-release-evidence/2026-08-06-gwo-v8-c1-beta1-core-preview-r3/state.json`
- Create externally: `D:/gwo-release-evidence/2026-08-06-gwo-v8-c2-beta2-feature-complete/state.json`
- Create externally: `D:/gwo-release-evidence/2026-08-06-gwo-v8-c2-beta2-feature-complete/c1-entry-readback.json`

**Interfaces:**
- Consumes: C1 `gwo-v8-c1-closure.v2`, `gwo-v8-c2-handoff.v1`, merged main SHA/tree, tracker-after digest, Beta1 tag/Release receipts, and protected-GA identity.
- Produces: immutable C2 base identity and state; no repository or GitHub mutation.

- [ ] **Step 1: Run the fail-closed read-only entry test**

```powershell
$c1 = 'D:/gwo-release-evidence/2026-08-06-gwo-v8-c1-beta1-core-preview-r3/state.json'
if (-not (Test-Path -LiteralPath $c1 -PathType Leaf)) { throw 'C1_CLOSURE_REQUIRED' }
$state = Get-Content -Raw -LiteralPath $c1 | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only' -or $state.closure.schema -ne 'gwo-v8-c1-closure.v2' -or $state.c2_handoff.schema -ne 'gwo-v8-c2-handoff.v1') { throw 'C1_HANDOFF_INVALID' }
if ($null -eq $state.closure.path -or $null -eq $state.closure.sha256 -or $null -eq $state.c2_handoff.ticket_readback_path -or $null -eq $state.c2_handoff.ticket_readback_sha256) { throw 'C1_HANDOFF_INCOMPLETE' }
if (@($state.c2_handoff.unfinished_scope | Where-Object { $_.status -ne 'unfinished' }).Count -ne 0) { throw 'C1_SCOPE_CLOSED_EARLY' }
```

Expected before C1 execution: HOLD with `C1_CLOSURE_REQUIRED` when the R3
file is absent, or `C1_HANDOFF_INVALID`/`C1_HANDOFF_INCOMPLETE` when the file
exists in the current pre-closure shape; perform zero mutation in both cases.
Expected after C1 R3 closure: PASS.

This is a terminal entry fence: on the missing-file result, schema failure, hash failure, coordinator mismatch, or remote-ref mismatch, write no C2 state and do not dispatch Task 1.

- [ ] **Step 2: Re-hash every consumed C1 artifact**

For every state-referenced closure, C2 ticket readback, tracker-after snapshot, local-verification manifest/attestation/log, review-state file, policy snapshot, approval, lease, mutation authorization, PR/merge receipt, tag receipt, Release receipt, and canonical-main receipt, require a normalized path, schema, SHA-256, and parseable bytes. Recompute every digest and require state -> artifact and closure -> state references to agree. Bind the recorded C1 coordinator root, branch, HEAD, merged-main SHA/tree/ordered parent, repository/default branch, Beta1 tag peel, Release ID/body digest, tracker digest, and protected-GA identity. Require remote `main` to equal the dynamic C1 merged SHA and the protected GA remote to remain `2cd6c46e1484ca140c3a197bbdeb171191d70c20`.

Use this read-only assertion before creating C2 state; all values except the
protected-GA identity come from the reloaded C1 state:

```powershell
$root = (git rev-parse --show-toplevel).Trim()
function Hash-File([string]$path) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "EVIDENCE_MISSING:$path" }
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}
$closurePath = ([string]$state.closure.path).Replace('\','/')
if ((Hash-File $closurePath) -ne [string]$state.closure.sha256) { throw 'C1_CLOSURE_HASH_INVALID' }
$closure = Get-Content -Raw -LiteralPath $closurePath | ConvertFrom-Json
if ($closure.schema -ne 'gwo-v8-c1-closure.v2' -or $closure.merged_sha -ne $state.pr.merge.merge_sha -or $closure.protected_ga_sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20') { throw 'C1_CLOSURE_IDENTITY_INVALID' }
$remoteMain = (git -C $root ls-remote origin refs/heads/main).Trim().Split("`t")[0]
if ($LASTEXITCODE -ne 0 -or $remoteMain -ne $state.pr.merge.merge_sha) { throw 'C1_REMOTE_MAIN_INVALID' }
$commit = git -C $root cat-file -p $state.pr.merge.merge_sha
if ($LASTEXITCODE -ne 0 -or @($commit | Where-Object { $_ -like 'tree *' }).Count -ne 1) { throw 'C1_MERGED_COMMIT_INVALID' }
$handoffPath = ([string]$state.c2_handoff.ticket_readback_path).Replace('\','/')
if ((Hash-File $handoffPath) -ne [string]$state.c2_handoff.ticket_readback_sha256) { throw 'C1_TICKET_READBACK_HASH_INVALID' }
```

The complete fence also verifies the ordered parent list, actual tree object,
Beta1 tag peel and Release body digest, tracker-after digest, every local log
digest, and the protected-GA remote ref. Any failed artifact read is HOLD, not
a synthesized replacement fixture.

- [ ] **Step 3: Create and read back C2 state**

Write `gwo-v8-c2-state.v1` atomically. Set C2 base SHA/tree from the validated C1 closure, never from a plan literal. Persist repository/default-branch readback and local policy mode.

### Task 1: Audit exact feature lineage, current main drift, and remaining work

**Files:**
- Read with `git show` from `2cd6c46e1484ca140c3a197bbdeb171191d70c20`: `docs/superpowers/plans/2026-08-03-gwo-v8-campaign-watchdog.md`
- Read with `git show` from `2cd6c46e1484ca140c3a197bbdeb171191d70c20`: `docs/superpowers/plans/2026-08-03-gwo-v8-candidate-assurance.md`
- Read with `git show` from `2cd6c46e1484ca140c3a197bbdeb171191d70c20`: `docs/superpowers/plans/2026-08-03-gwo-v8-batch-delivery.md`
- Create externally: `feature-lineage.json`, `feature-test-readback.json`

**Interfaces:**
- Consumes: Task 0 base and the five exact inherited boundaries.
- Produces: per-Issue commit ranges, path sets, trees, test receipts, and remaining child-task map.

- [ ] **Step 1: Write the failing lineage contract**

```python
import json
from pathlib import Path

state = json.loads(
    Path("D:/gwo-release-evidence/2026-08-06-gwo-v8-c1-beta1-core-preview-r3/state.json")
    .read_text(encoding="utf-8")
)
handoff = state["c2_handoff"]

def test_c2_handoff_has_exact_boundaries_and_unfinished_scope():
    assert handoff["existing_completed_boundaries"] == {
        "foundation": "77ac3e3ef14241d1840150b22cb227d2e5088fb4",
        "issue_113": "07086ce1036198a41547ca1d9a9a506acfb8fcf7",
        "issue_114": "657bf236d765735cdee117910a5939c6c2cd3292",
        "issue_115": "a0f697656be6471bed601103c169185988a9e4ac",
        "issue_116_wip": "e58c596998df90e65349bdb4b5f25d3d9dc1f7e2",
    }
    assert handoff["unfinished_scope"] == [
        {"item": "issue_117_completion", "status": "unfinished", "completed_boundary_sha": None},
        {"item": "final_issue_137_revalidation", "status": "unfinished", "completed_boundary_sha": None},
    ]
    assert handoff["unfinished_scope"][0]["completed_boundary_sha"] is None
```

- [ ] **Step 2: Prove RED against any handoff that treats `e58c596` as completed #116**

Expected: FAIL because `e58c596` is recorded only under the explicit
`issue_116_wip` key and is not a Result receipt or a closed Issue state. Derive
the C2 work item `issue_116_completion` from this WIP boundary; do not rewrite
the C1 handoff's two-entry unfinished list.

- [ ] **Step 3: Build the exact lineage record**

Record tree/parents and path ranges for foundation, #113, #114, #115, and #116 WIP. Confirm all are ancestors of the protected GA branch and that none is an ancestor of the validated C1 R3 merged-main SHA unless the C1 handoff explicitly records that boundary as present. Compute overlap between the dynamic C2 base delta and feature paths; any overlap outside the reviewed package/metadata paths stops. The source allowlists must be materialized in `slice-113.json`, `slice-114.json`, and `slice-115.json`; do not treat a raw reachable commit range as an Issue allowlist.

- [ ] **Step 4: Re-run accepted-slice local gates**

Run the Campaign Watchdog and CandidateGate focused suites from clean exact-boundary worktrees, plus:

```powershell
py -3.13 -m pytest tests/test_orchestrator_package.py -q
py -3.13 scripts/quick_validate.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
```

Persist command, executable, arguments, exit code, output digest, subject SHA/tree, and manifest digest.

### Task 2: Land the reviewed #113, #114, and #115 slices in dependency order

**Files:**
- Modify only through reviewed slice PRs: the materialized allowlists for the exact `a48c7d6..07086ce`, `07086ce..657bf23`, and `657bf23..a0f6976` source ranges
- Create externally: `slice-113.json`, `slice-114.json`, `slice-115.json`

**Interfaces:**
- Consumes: Task 1 receipts and C2 base.
- Produces: three exact merged-main readbacks, without Issue closure yet.

- [ ] **Step 1: Preview each squash result locally**

For each slice, read the exact protected-GA source objects and path allowlist,
create a temporary clean worktree at the dynamic current C2 target from the
validated C1 R3 merged-main SHA, and apply only the fixed source range
(`a48c7d6..07086ce`, `07086ce..657bf23`, then `657bf23..a0f6976`) with
`git merge --squash`. The first source range intentionally includes the
foundation boundary `77ac3e3`: current C1 R3 main is not allowed to be assumed
to contain that implementation, so the range parent `a48c7d6` is source
provenance, not the mutation target. The historical range parent is a source
identity only; never use `a48c7d6`, `2c72d9a`, `928789c`, or `4c18210` as a
mutation target literal. Record source commit/tree/parents, dynamic
target-before SHA/tree, patch digest, ordered changed paths, expected squash
tree, and focused manifest. The current protected-GA source path counts are
26 for #113's stacked foundation/watchdog slice, 14 for #114, and 21 for #115;
the ordered path lists must be stored in the three slice receipts and checked
against the issue-specific allowlist before applying them. A conflict,
context-dependent patch change, extra path, changed generated manifest outside
the allowlist, or different tree is HOLD; never rebase or silently edit a
historical slice to fit dynamic main.

- [ ] **Step 2: Acquire the repository-global PR writer lease**

The owner approval names exactly nine ordered effects: create/ready/merge for #113, then #114, then #115. Persist approval, lease, current repository policy, exact source/target refs, and immutable authorization before each effect.

- [ ] **Step 3: Create, verify, ready, and squash each PR serially**

Immediately before every effect, re-read main, source ref, protected GA, default branch, policy, approval, and lease. After merge, require one-parent squash, expected tree, exact changed paths, and fresh local verification before proceeding to the next slice.

- [ ] **Step 4: Persist integration receipts**

Record PR number/head, target-before/after, expected/actual tree, test manifest, and owner authorization digest. Leave #113–#115 open until Task 10's tracker gate.

### Task 3: Freeze completion write sets and serialize generated manifests

**Files:**
- Read: `candidate_gate.py`, `execution_kernel.py`, `batch_integrator.py`, Batch test support, package manifests
- Create externally: `lane-contracts.json`

**Interfaces:**
- Consumes: Task 2 merged CandidateGate/Repair code.
- Produces: exact branch bases, complete write-set receipts, and one serialized implementation order for Tasks 4–6.

- [ ] **Step 1: Write the failing parallel write-set contract**

```python
batch_paths = (
    "skills/orchestrator/.skill-package.json",
    "skills/orchestrator/scripts/gwo_v8/_batch_integrator_drivers.py",
    "skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py",
    "skills/orchestrator/scripts/gwo_v8/batch_integrator.py",
    "skills/orchestrator/scripts/gwo_v8/batch_patch_identity.py",
    "skills/orchestrator/scripts/gwo_v8/integration_batch.py",
    "skills/orchestrator/scripts/gwo_v8/__init__.py",
    "tests/v8_batch_test_support.py",
    "tests/test_v8_batch_integrator.py",
    "tests/test_orchestrator_v8_integration_batch.py",
)
replan_paths = (
    "skills/orchestrator/.skill-package.json",
    "skills/orchestrator/scripts/gwo_v8/candidate_gate.py",
    "tests/test_v8_repair_verification.py",
    "tests/test_v8_candidate_gate_public.py",
    "tests/test_v8_candidate_gate.py",
)
recovery_paths = (
    "skills/orchestrator/.skill-package.json",
    "skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py",
    "skills/orchestrator/scripts/gwo_v8/batch_integrator.py",
    "tests/v8_batch_test_support.py",
    "tests/test_v8_batch_recovery.py",
    "tests/test_v8_batch_beta2.py",
    "scripts/write_v8_batch_evidence.py",
    "docs/e2e/gwo-v8-batch-integrator.md",
)

def test_c2_source_lanes_have_exact_shared_write_sets():
    manifest = {"skills/orchestrator/.skill-package.json"}
    assert set(batch_paths) & set(replan_paths) == manifest
    assert set(batch_paths) & set(recovery_paths) == {
        *manifest,
        "skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py",
        "skills/orchestrator/scripts/gwo_v8/batch_integrator.py",
        "tests/v8_batch_test_support.py",
    }
    assert set(replan_paths) & set(recovery_paths) == manifest


def test_c2_subagent_scheduler_has_one_writer_and_four_review_slots():
    writer_slots = 1
    review_slots = 4
    assert writer_slots + review_slots == 5
    assert writer_slots == 1
```

- [ ] **Step 2: Prove RED if Tasks 4–6 are marked implementation-parallel**

Expected: FAIL because the exact shared sets include the generated
orchestrator manifest, Batch source, and Batch support. Record Tasks 4, 5, and
6 as serialized source/manifest writers. Up to five separate workers may
concurrently perform read-only lineage review, focused baseline tests, and
tracker readback into distinct evidence files.

- [ ] **Step 3: Freeze lane bases**

Task 4 resolves `issue_116_wip` from the validated C1 R3 handoff, proves that
the object is a commit, is an ancestor of protected GA, and is not a Result or
closed Issue receipt, then cleanly advances that WIP content onto Task 2 main.
Task 5 starts from Task 4's merged main, and Task 6 starts from Task 5's
merged main. Persist every base SHA/tree and refuse branch drift. This order
keeps every source change and its regenerated manifest in the same reviewed
commit.

### Task 4: Complete and land #116 exact Batch delivery

**Files:**
- Modify: `skills/orchestrator/scripts/gwo_v8/_batch_integrator_drivers.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/batch_integrator.py`
- Create: `skills/orchestrator/scripts/gwo_v8/batch_patch_identity.py`
- Modify only for Task 7's minimum direct-import quarantine proof: `skills/orchestrator/scripts/gwo_v8/integration_batch.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/__init__.py`
- Modify: `tests/v8_batch_test_support.py`
- Modify: `tests/test_v8_batch_integrator.py`
- Modify: `tests/test_orchestrator_v8_integration_batch.py`
- Modify through sync: `skills/orchestrator/.skill-package.json`
- Create externally: `D:/gwo-release-evidence/2026-08-06-gwo-v8-c2-beta2-feature-complete/reviews/verify-batch-task5-review.py`
- Create externally: `D:/gwo-release-evidence/2026-08-06-gwo-v8-c2-beta2-feature-complete/reviews/batch-task5-review.json`

**Interfaces:**
- Consumes: accepted-Candidate receipts from merged #115 and WIP through Batch child Task 5.
- Produces: idempotent `BatchIntegrator.prepare/readback/execute`, immutable Batch SHA, local/provider/target proofs, and direct-import-only predecessor compatibility.

- [ ] **Step 1: Write the failing Task 5 completion/readback test**

```python
import json
from hashlib import sha256
from pathlib import Path

review_path = Path("D:/gwo-release-evidence/2026-08-06-gwo-v8-c2-beta2-feature-complete/reviews/batch-task5-review.json")
c1_state = json.loads(
    Path("D:/gwo-release-evidence/2026-08-06-gwo-v8-c1-beta1-core-preview-r3/state.json")
    .read_text(encoding="utf-8")
)
expected_wip_sha = c1_state["c2_handoff"]["existing_completed_boundaries"]["issue_116_wip"]
expected_task5_paths = [
    "skills/orchestrator/.skill-package.json",
    "skills/orchestrator/scripts/gwo_v8/_batch_integrator_drivers.py",
    "skills/orchestrator/scripts/gwo_v8/batch_integrator.py",
    "skills/orchestrator/scripts/gwo_v8/batch_patch_identity.py",
    "tests/test_v8_batch_integrator.py",
    "tests/v8_batch_test_support.py",
]
receipt = json.loads(Path(review_path).read_text(encoding="utf-8"))
assert receipt["schema"] == "gwo-v8-batch-task5-review.v1"
assert receipt["source_boundary_sha"] == expected_wip_sha
assert receipt["source_boundary_sha"] != receipt.get("result_sha")
assert receipt["review"] == "PASS"
assert receipt["changed_paths"] == expected_task5_paths
assert receipt["focused_manifest_digest"] == sha256(Path(receipt["focused_manifest_path"]).read_bytes()).hexdigest()
assert receipt["local_check_receipts"]
assert all(item["outcome"] == "passed" for item in receipt["local_check_receipts"])
```

The coordinator produces this canonical JSON only after reviewing the exact WIP range and re-running its focused suites. It also records source tree/parents, reviewer identity, review report digest, every `LocalCheckReceipt` field (`batch_sha`, `suite_id`, `definition_digest`, `outcome`, `observation_digest`, `source_ref`, `receipt_digest`), log paths/digests, and atomic write/readback digest. A missing field is RED/HOLD.

Save the snippet as `verify-batch-task5-review.py` and run it before the JSON exists:

```powershell
py -3.13 D:/gwo-release-evidence/2026-08-06-gwo-v8-c2-beta2-feature-complete/reviews/verify-batch-task5-review.py
```

Expected: `FileNotFoundError`.

- [ ] **Step 2: Observe RED and complete or repair child Task 5**

Expected: FAIL while no reviewed Task 5 receipt exists. Review the exact WIP range; fix only concrete findings with TDD. Atomically write/read back the review JSON, then rerun `verify-batch-task5-review.py` and require PASS before child Task 6.

- [ ] **Step 3: Write and observe the failing exact delivery-boundary tests**

Add the child Task 6 cases for one immutable Batch SHA through `LocalCheckReceipt`, `BatchPublicationReceipt`, `PullRequestReadback`, locally driven `HostedResultObservation`, `IntegrationLeaseReceipt`, `TargetIntegrationReadback`, and `BatchDeliveryProof`. Cover wrong publication SHA, wrong suite/provider identity, target merge mapping mismatch, target ancestry false, and squash/rebase identity rewriting.

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py::test_local_suite_publication_pr_hosted_ci_and_target_name_one_batch_sha tests/test_v8_batch_integrator.py::test_complete_observation_rejects_any_tampered_delivery_proof -q
```

Expected: FAIL because `readback`/`execute` do not yet produce an exact terminal delivery proof.

- [ ] **Step 4: Implement the exact boundary, prove GREEN, and commit child Task 6**

Extend the driver/store/integrator with the immutable receipt types above. Every driver call receives the same `batch_sha`; each returned identity is validated before the next call. Product hosted-result semantics are exercised by deterministic local drivers only. A complete direct observation has exactly one proof covering all member Ticket keys.

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/orchestrator/scripts/gwo_v8/_batch_integrator_drivers.py skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py skills/orchestrator/scripts/gwo_v8/batch_integrator.py tests/v8_batch_test_support.py tests/test_v8_batch_integrator.py skills/orchestrator/.skill-package.json
git commit -m "feat: deliver Batch SHAs through exact local and target boundaries"
```

- [ ] **Step 5: Write and observe the failing readback-first action-loop tests**

Add exact child Task 7 cases: duplicate `execute` performs one publication/hosted read/target mutation; reconstructed `readback` uses only the terminal journal; member Ticket/Work Run/Evidence identities survive; V3 imports/calls neither `integration_batch` nor `reconcile_once`.

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py::test_duplicate_execute_does_not_repeat_publication_hosted_ci_or_target_mutation tests/test_v8_batch_integrator.py::test_terminal_journal_readback_returns_without_runtime_candidategate_or_provider_call -q
```

Expected: FAIL until the terminal observation is persisted and returned before any driver call.

- [ ] **Step 6: Implement the V3 loop, quarantine the predecessor, and commit child Task 7**

`prepare` validates and persists one stable action; `readback` validates action identity and returns only terminal `complete/decision/blocked`; `execute` calls `readback` first and resumes the one journaled action. `integration_batch.py` remains import-compatible until #118; change it only for the minimum direct-import quarantine proof, never delete or cut it over.

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py tests/test_orchestrator_v8_integration_batch.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/orchestrator/scripts/gwo_v8/batch_integrator.py skills/orchestrator/scripts/gwo_v8/__init__.py skills/orchestrator/scripts/gwo_v8/integration_batch.py tests/v8_batch_test_support.py tests/test_v8_batch_integrator.py tests/test_orchestrator_v8_integration_batch.py skills/orchestrator/.skill-package.json
git commit -m "feat: install the idempotent V3 BatchIntegrator action loop"
```

- [ ] **Step 7: Review and squash-land the #116 Result**

Run independent spec/code reviews, create an exact-head Draft PR, resolve every actionable thread, acquire the repository owner approval/lease, mark ready, and squash merge with head matching. Read back one-parent main, expected tree/paths, local verification, and #116 Result receipt. Leave the Issue OPEN until Task 10.

### Task 5: Repair and land the #137 complete Repair scope-escape route

**Files:**
- Modify: `skills/orchestrator/scripts/gwo_v8/candidate_gate.py`
- Modify: `tests/test_v8_repair_verification.py`
- Modify: `tests/test_v8_candidate_gate_public.py`
- Modify: `tests/test_v8_candidate_gate.py`
- Modify through sync: `skills/orchestrator/.skill-package.json`

**Interfaces:**
- Consumes: merged #114/#115 authoritative Candidate identity, `CandidateDiffRecordV1`, `RepairPacket`, `RepairDelta`, and the approved OPEN #137 tracker readback.
- Produces: `CandidateGate.verify_repair(...) -> CandidateGateResult(status=PLAN_INVALIDATION_REPORTED)` for an exact repaired-Candidate delta outside `RepairPacket.allowed_path_tokens`, with bound `RepairVerificationEvidence`, `PlanInvalidationEvidence(source_kind="repair_verification")`, report, and receipt. It does not call `RepairVerifier.verify`, reopen Formal Review, prepare a Batch, or expand authority.

- [ ] **Step 1: Acquire and validate the OPEN checkpoint**

Task 0 must supply a C1 R3 tracker readback showing #137 `OPEN`; when the
live precondition began CLOSED, that readback must include C1's separately
authorized conditional reopen effect and immediate readback. If C1 R3 did not
need or did not perform that effect, the only alternative is a new
post-merge manual owner approval and reopen receipt after #114/#115 merge.
Validate and adopt one exact path. If the handoff says CLOSED or omits both
reopen paths, stop; this task never invents a reopen. Re-read #137 body,
comments, blockers, labels, milestone, state, and URL and require `OPEN` before
running the acceptance test.

- [ ] **Step 2: Replace the obsolete exception expectations with a failing result contract**

Update the complete-repair tests that currently expect `CANDIDATE_GATE_REPAIR_SCOPE_INVALID`:

```python
result = gate.verify_repair(parent=parent, packet=packet, candidate=repaired_candidate)
assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
repair_evidence = next(item for item in result.evidence if type(item) is RepairVerificationEvidence)
plan_evidence = next(item for item in result.evidence if type(item) is PlanInvalidationEvidence)
assert repair_evidence.scope_escape_paths == ("outside/allowed-scope.py",)
assert plan_evidence.source_kind == "repair_verification"
assert plan_evidence.source_evidence_digest == repair_evidence.digest
assert result.plan_invalidation_receipt.report_digest == result.plan_invalidation_report.digest
assert repair_verifier.calls == []
```

Also retain the negative case where a verifier claims an already-allowed path: that remains fail-closed and is not Campaign Plan Invalidation.

- [ ] **Step 3: Run the exact RED set**

```powershell
py -3.13 -m pytest tests/test_v8_repair_verification.py tests/test_v8_candidate_gate_public.py tests/test_v8_candidate_gate.py -q
```

Expected: FAIL because the complete `verify_repair` path raises before creating Evidence/report/receipt. Existing deterministic-audit, Formal-Review, and legacy compatibility cases remain green.

- [ ] **Step 4: Implement the minimum CandidateGate route**

Change only the complete receipt/finding-ledger branch of `verify_repair`; keep `_verify_legacy_repair` and its fail-closed cases unchanged. After authoritative Candidate readback, exact diff persistence, prior-artifact/base binding, and `RepairDelta.from_records(...)`, derive `escaped_paths`. For a non-empty escape, do not invoke the Repair Verifier. Build a canonical scope request with kind `complete-repair-scope-escape.v1`, parent digest, packet digest, repaired Candidate/receipt/diff digests, RepairDelta digest, and sorted escaped paths; its digest is `RepairVerificationEvidence.request_digest`. Set `accepted=False`, `scope_escape_paths=escaped_paths`, and details to the stable scope-escape message plus one `escaped_path=<path>` entry per path. Build `PlanInvalidationEvidence` with `source_kind="repair_verification"`, the Repair Evidence digest as its first source digest, invalidated obligation `apply the approved Repair Packet without changing paths outside allowed_path_tokens`, and required effects equal to the sorted union of `packet.required_effects` and one `replan_required_path:<path>` entry per escaped path. Bind the existing workspace identity and lineage artifacts for packet, prior/repaired receipts and diff records, RepairDelta, canonical scope request, readback, and Repair Evidence. Call the existing `_report_invalidation(...)` seam and return the bound report/receipt with `PLAN_INVALIDATION_REPORTED`. Do not change ExecutionKernel, Candidate budgets, packet scope, Review routing, or public operations.

- [ ] **Step 5: Run GREEN, synchronize, and commit**

```powershell
py -3.13 -m pytest tests/test_v8_repair_verification.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py tests/test_v8_runtime_gateway_repair.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/orchestrator/scripts/gwo_v8/candidate_gate.py tests/test_v8_repair_verification.py tests/test_v8_candidate_gate_public.py tests/test_v8_candidate_gate.py skills/orchestrator/.skill-package.json
git commit -m "fix: report repair scope escape as plan invalidation"
```

This task repairs the owner module only. Task 7 child Task 6 performs the final public `start -> advance -> inspect` revalidation and produces the evidence needed to close #137.

- [ ] **Step 6: Review and squash-land the CandidateGate repair**

Create an exact-head Draft PR from the Task 4 merged main, run independent spec/code reviews, acquire the repository owner approval/lease, and squash merge only the CandidateGate/test/manifest path set. Read back exact tree and focused local gates. Keep #137 OPEN; this merge is not final revalidation or close authority.

### Task 6: Complete and land #117 recovery and Batch Beta2 evidence

**Files:**
- Modify: `skills/orchestrator/scripts/gwo_v8/batch_integrator.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py`
- Modify: `tests/v8_batch_test_support.py`
- Modify: `tests/test_v8_batch_recovery.py`
- Create: `tests/test_v8_batch_beta2.py`
- Create: `scripts/write_v8_batch_evidence.py`
- Create: `docs/e2e/gwo-v8-batch-integrator.md`
- Modify through sync: `skills/orchestrator/.skill-package.json`

**Interfaces:**
- Consumes: Task 4 Batch action and #115 Finding-ledger digest.
- Produces: unchanged-SHA retry, durable terminal-result adoption, one Singleton fallback generation, unaffected-member preservation, and exact Beta2 proof partitions. Test support adds `BatchRecoveryHarness.run_outcomes(*outcomes: str) -> tuple[BatchDeliveryObservation, ...]`, `.retry_shas: tuple[str, ...]`, and `.run_successful_singleton_fallback() -> BatchDeliveryObservation`; the fixture name is `batch_harness`.

- [ ] **Step 1: Write the failing retry/fallback tests**

```python
def test_infrastructure_retry_keeps_one_batch_sha(batch_harness):
    observations = batch_harness.run_outcomes("infrastructure_failure", "infrastructure_failure", "infrastructure_failure")
    assert observations[-1].phase == "blocked"
    assert len(set(batch_harness.retry_shas)) == 1
    assert len(batch_harness.retry_shas) == 2

def test_fallback_has_one_singleton_proof_per_member(batch_harness):
    result = batch_harness.run_successful_singleton_fallback()
    assert result.fallback_generation == 1
    assert tuple(len(proof.member_ticket_keys) for proof in result.delivery_proofs) == (1, 1, 1)
```

- [ ] **Step 2: Observe RED, implement unchanged-SHA infrastructure recovery, and commit child Task 8**

Persist retry count, next-check identity, and terminal hosted-result receipt in the owner journal. Retry at most twice with the same stable action and Batch SHA. On restart, adopt an exact durable terminal receipt without another provider read; mismatched suite/provider/SHA remains identity failure, not retry.

```powershell
py -3.13 -m pytest tests/test_v8_batch_recovery.py::test_infrastructure_failure_retries_same_batch_sha_at_most_twice tests/test_v8_batch_recovery.py::test_terminal_hosted_receipt_is_adopted_after_restart_without_provider_reread -q
py -3.13 -m pytest tests/test_v8_batch_integrator.py tests/test_v8_batch_recovery.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/batch_integrator.py skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py tests/v8_batch_test_support.py tests/test_v8_batch_recovery.py skills/orchestrator/.skill-package.json
git commit -m "feat: adopt terminal hosted receipts and bound infrastructure retry"
```

- [ ] **Step 3: Implement one deterministic Singleton fallback and commit child Task 9**

Trigger fallback only for composition, exact-local code, or code-class hosted-result failure. Persist `fallback_generation=1`; create exactly one Singleton child per parent member; preserve unaffected Candidate/Check/Review Evidence; resume only the failing child; never fall back recursively. The complete parent proof partition contains every Ticket exactly once.

```powershell
py -3.13 -m pytest tests/test_v8_batch_recovery.py::test_multi_member_code_failure_dissolves_once_into_singletons tests/test_v8_batch_recovery.py::test_fallback_has_one_singleton_proof_per_member -q
py -3.13 -m pytest tests/test_v8_batch_integrator.py tests/test_v8_batch_recovery.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/batch_integrator.py skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py tests/v8_batch_test_support.py tests/test_v8_batch_recovery.py skills/orchestrator/.skill-package.json
git commit -m "feat: recover Batch failures with one Singleton fallback"
```

- [ ] **Step 4: Write the failing Batch Beta2 evidence contract**

`tests/test_v8_batch_beta2.py` requires schema `gwo-v8-batch-beta2-evidence.v1`, exact subject SHA/tree/parents, Local Verification Only mode, local command/log/manifest digests, direct and Singleton proof partitions, unchanged retry SHAs, restart adoption, isolated target readback, and the statement `Beta2 feature-complete preview; no V3 writer cutover and no GA admission.` Unknown keys or any remote repository-check URL fail.

```powershell
py -3.13 -m pytest tests/test_v8_batch_beta2.py -q
```

Expected: FAIL because the evidence writer and document do not exist.

- [ ] **Step 5: Implement child Task 10, prove GREEN, and commit**

`scripts/write_v8_batch_evidence.py` consumes already completed JUnit/local manifests and exact receipt digests; `--check` regenerates in memory and compares canonical bytes without mutation. `docs/e2e/gwo-v8-batch-integrator.md` documents direct/fallback/retry/restart proofs and the no-cutover boundary.

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py tests/test_v8_batch_recovery.py tests/test_v8_batch_beta2.py -q
py -3.13 scripts/write_v8_batch_evidence.py --check
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add tests/test_v8_batch_beta2.py scripts/write_v8_batch_evidence.py docs/e2e/gwo-v8-batch-integrator.md tests/v8_batch_test_support.py skills/orchestrator/.skill-package.json
git commit -m "test: record local Batch Beta2 evidence"
```

- [ ] **Step 6: Review and squash-land the #117 Result**

Create the exact-head Draft PR from Task 5 merged main, complete spec/code reviews, acquire the repository owner approval/lease, and squash merge. Read back the expected tree, exact #117 path set, focused/local package gates, and Result receipt. Leave #117 OPEN until the final Task 11 tracker gate.

### Gate: `G137_OPEN_RECHECK` before Production composition

**Files:**
- Read: `D:/gwo-release-evidence/2026-08-06-gwo-v8-c1-beta1-core-preview-r3/state.json`
- Read: Task 5 CandidateGate repair receipt and Task 2 #114/#115 merge receipts
- Create externally: `D:/gwo-release-evidence/2026-08-06-gwo-v8-c2-beta2-feature-complete/reviews/issue-137-open-recheck.json`

Re-read #137 body, comments, blockers, labels, milestone, URL, and state after
the #114/#115 and CandidateGate merges. Require the exact C1 conditional
reopen authorization/effect receipt (or the separately approved
`post_merge_manual_approval` path), a fresh `OPEN` readback, and exact
CandidateGate repair receipt digest. Hash and atomically write the gate
receipt. If #137 is CLOSED, content-drifted, missing its reopen evidence, or
the readback is older than the last relevant merge, HOLD and do not start
Production child Task 6 or any parallel Skill/E2E writer.

### Task 7: Execute Production V3 composition child Tasks 1–7 serially

**Files:**
- Create: `skills/orchestrator/scripts/gwo_v8/production_effects.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/execution_kernel.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/plan_control_host.py`
- Create: `skills/orchestrator/scripts/gwo_v8/production_host.py`
- Create: `tests/v8_production_test_support.py`
- Create: `tests/test_v8_production_effects.py`
- Create: `tests/test_v8_execution_kernel_integrity.py`
- Create: `tests/test_v8_production_host.py`
- Create: `tests/test_v8_production_replanning.py`
- Create: `tests/test_v8_production_composition_e2e.py`
- Modify through sync: `skills/orchestrator/.skill-package.json`

**Interfaces:**
- Consumes: merged #113–#117 and Task 5 #137 evidence.
- Produces: SQLite CAS, exact Result integrity, ProductionWorkRunEffects, ProductionGwoHost, planning continuation, #137 public route, Watchdog/Batch wake composition, and restart convergence.

This is a lane controller, not one giant implementation dispatch. Read the authoritative child plan with:

```powershell
git show 2cd6c46e1484ca140c3a197bbdeb171191d70c20:docs/superpowers/plans/2026-08-03-gwo-v8-production-composition.md
```

Execute child Tasks 1–7 one at a time. Every child gets a fresh implementer, RED/GREEN proof, package synchronization, spec review, code-quality review, and its own commit.

- [ ] **Step 1: Child Task 1 — freeze production effect ports**

Write the missing-module/port tests, observe RED, then add `production_effects.py`, independent deterministic support, and the closed Runtime/Candidate/Batch port union. Constructors perform zero external calls.

```powershell
py -3.13 -m pytest tests/test_v8_production_effects.py::test_production_effects_requires_the_merged_candidate_and_batch_ports -q
py -3.13 -m pytest tests/test_v8_production_effects.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/production_effects.py tests/v8_production_test_support.py tests/test_v8_production_effects.py skills/orchestrator/.skill-package.json
git commit -m "feat: freeze production effect ports"
```

- [ ] **Step 2: Child Task 2 — add durable Campaign CAS and preserve read-only inspect**

Write stale-writer, inspect-no-write, and raw-wake trusted-progress tests. Add `KernelStateReadback(state, version, state_digest)`, `_read_state(handle)`, and `_save(handle, state, expected_version=...)`; fail stale saves with `EXECUTION_STORE_CAS_CONFLICT`. Finish every SQLite transaction before external I/O.

```powershell
py -3.13 -m pytest tests/test_v8_execution_kernel_integrity.py::test_sqlite_campaign_state_rejects_stale_writer_without_overwriting tests/test_v8_execution_kernel_integrity.py::test_inspect_does_not_write_or_migrate_campaign_state tests/test_v8_execution_kernel_integrity.py::test_raw_wake_cas_does_not_advance_trusted_progress_or_reset_staleness -q
py -3.13 -m pytest tests/test_v8_execution_kernel_integrity.py tests/test_v8_execution_kernel.py tests/test_v8_successor_execution_kernel.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/execution_kernel.py tests/v8_production_test_support.py tests/test_v8_execution_kernel_integrity.py skills/orchestrator/.skill-package.json
git commit -m "feat: add Campaign state compare-and-swap"
```

- [ ] **Step 3: Child Task 3 — require exact Result integrity proof**

Write serializer round-trip, Candidate-only rejection, exact direct/fallback delivery selection, target-readback, and proof-tamper tests. Add frozen Kernel-owned `ResultIntegrityProof`; copy exact owner receipts without inferring any field from a request or action shortcut.

```powershell
py -3.13 -m pytest tests/test_v8_execution_kernel_integrity.py::test_completed_observation_without_integrity_proof_is_rejected tests/test_v8_execution_kernel_integrity.py::test_accepted_candidate_receipt_alone_cannot_create_a_code_result tests/test_v8_execution_kernel_integrity.py::test_any_exact_delivery_proof_field_tamper_fails_closed tests/test_v8_execution_kernel_integrity.py::test_fallback_result_selects_exact_singleton_proof_and_keeps_parent_receipt -q
py -3.13 -m pytest tests/test_v8_execution_kernel_integrity.py tests/test_v8_execution_kernel.py tests/test_v8_successor_execution_kernel.py tests/test_v8_candidate_gate_public.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/execution_kernel.py tests/v8_production_test_support.py tests/test_v8_execution_kernel_integrity.py skills/orchestrator/.skill-package.json
git commit -m "feat: require exact Result integrity proof"
```

- [ ] **Step 4: Child Task 4 — implement readback-first ProductionWorkRunEffects**

Persist one canonical row per stable action. `execute` reads back first; Runtime completion enters CandidateGate, accepted Candidates remain `accepted_awaiting_delivery`, Batch uses the stored accepted receipt and one stable identity, and only exact terminal target readback can produce a Result.

```powershell
py -3.13 -m pytest tests/test_v8_production_effects.py::test_runtime_completion_enters_candidate_gate_not_completed_result tests/test_v8_production_effects.py::test_batch_delivery_maps_only_exact_complete_receipt_to_completed -q
py -3.13 -m pytest tests/test_v8_production_effects.py tests/test_v8_execution_kernel_integrity.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/production_effects.py tests/v8_production_test_support.py tests/test_v8_production_effects.py skills/orchestrator/.skill-package.json
git commit -m "feat: compose production Work Run effects"
```

- [ ] **Step 5: Child Task 5 — add planning continuation and isolated ProductionGwoHost**

Write no-poll-without-wake and same-action restart tests. Add `PlanningContinuation`, the continuation methods on `ProductionPlanControlStartHost`, `ProductionHostConfiguration`, and `ProductionGwoHost.install/start/advance/inspect/watchdog_snapshot/run_watchdog_once`. Installation requires Beta2 preview, a strict temporary child target, and writer activation disabled.

```powershell
py -3.13 -m pytest tests/test_v8_production_host.py::test_pending_planning_is_not_polled_by_advance_without_a_wake tests/test_v8_production_host.py::test_wake_continues_the_same_persisted_planning_action_after_restart -q
py -3.13 -m pytest tests/test_v8_production_host.py tests/test_v8_plancontrol_production.py tests/test_v8_successor_host.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/plan_control_host.py skills/orchestrator/scripts/gwo_v8/production_host.py tests/v8_production_test_support.py tests/test_v8_production_host.py skills/orchestrator/.skill-package.json
git commit -m "feat: add isolated ProductionGwoHost"
```

- [ ] **Step 6: Child Task 6 — revalidate #137 through the public seam**

With the fresh `G137_OPEN_RECHECK` receipt, write the public deterministic-audit,
Formal-Review, repaired-Candidate escape, ordinary rejection, replay/restart,
and unaffected-Work-Run tests in `tests/test_v8_production_replanning.py`. The
repaired-Candidate case must consume Task 5's CandidateGate result, reach no
Batch, and persist one invalidation observation. Add negative assertions that
the public path does not classify Campaign disposition, reopen Review, create a
second Candidate, expand Authority, or mutate the Issue tracker.

```powershell
py -3.13 -m pytest tests/test_v8_production_replanning.py -q
py -3.13 -m pytest tests/test_v8_production_replanning.py tests/test_v8_candidate_gate_public.py tests/test_v8_candidate_gate.py tests/test_v8_runtime_gateway_repair.py -q
git add tests/v8_production_test_support.py tests/test_v8_production_replanning.py
git commit -m "test: prove production replanning route"
```

- [ ] **Step 7: Child Task 7 — integrate Watchdog/Batch wakes and crash convergence**

Bind Runtime, Candidate, Review, hosted-result, Batch, and due-time wakes to one `ForwardingWatchdogAdvancer` calling public `advance`. Write crash-after-effect-ledger, crash-after-terminal-Batch-readback, lost-callback, process reconstruction, exact-once target, and predecessor-path rejection tests.

```powershell
py -3.13 -m pytest tests/test_v8_production_composition_e2e.py -q
py -3.13 -m pytest tests/test_v8_production_composition_e2e.py tests/test_v8_campaign_watchdog.py tests/test_v8_watchdog_execution_kernel.py tests/test_v8_watchdog_production_host.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/production_host.py skills/orchestrator/scripts/gwo_v8/production_effects.py tests/v8_production_test_support.py tests/test_v8_production_composition_e2e.py skills/orchestrator/.skill-package.json
git commit -m "test: prove watchdog and Batch restart convergence"
```

- [ ] **Step 8: Run the complete owning gate before forking Tasks 8 and 9**

```powershell
py -3.13 -m pytest tests/test_v8_execution_kernel_integrity.py tests/test_v8_production_effects.py tests/test_v8_production_host.py tests/test_v8_production_replanning.py tests/test_v8_production_composition_e2e.py -q
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
```

- [ ] **Step 9: Review and squash-land Production Tasks 1–7**

The Production branch base is Task 6 merged main; its PR head is the final commit after child Task 7 and Step 8, not the base. Create one exact-head Production Composition Draft PR at that final head. Require seven child-task review receipts, resolved threads, the Step 8 gate, final tree/path set, generated-manifest digest, and an owner approval/lease. Squash merge, then read back one-parent main, expected tree/path set, package manifest, and the still-OPEN #137 tracker state. Fork Tasks 8 and 9 only from this merged main.

### Task 8: Replace predecessor Skill guidance without activating V8

**Files:**
- Modify: `skills/implement-gwo/SKILL.md`
- Modify: `skills/orchestrator/scripts/gwo_v8/__init__.py`
- Create: `tests/test_implement_gwo_skill.py`
- Modify through sync: both package manifests

**Interfaces:**
- Consumes: Task 7 ProductionGwoHost.
- Produces: isolated-preview guidance and predecessor unreachability proof; no writer activation. The install-argument helper below is test-local to `tests/test_implement_gwo_skill.py`, so this lane does not write shared `tests/v8_production_test_support.py` and can run in parallel with Task 9.

- [ ] **Step 1: Write the failing Skill contract**

Add `tests/test_implement_gwo_skill.py` with these exact assertions. The
helper must construct only the Task 7 host configuration; it must not start a
provider or touch a repository during test collection.

```python
import inspect
from pathlib import Path

import pytest

from gwo_v8.production_host import (
    ProductionCompositionError,
    ProductionGwoHost,
    ProductionHostConfiguration,
)
from v8_production_test_support import (
    ProductionCompositionHarness,
)


def test_implement_gwo_skill_names_only_the_v8_public_path():
    text = Path("skills/implement-gwo/SKILL.md").read_text(encoding="utf-8")
    assert "start(repository, ready_refs, options?)" in text
    assert "advance(campaign_handle, wake_ref?)" in text
    assert "inspect(campaign_handle)" in text
    for module in ("PlanControl", "ExecutionKernel", "RuntimeGateway", "CandidateGate", "BatchIntegrator"):
        assert module in text
    assert "preview_mode=\"beta2_isolated_preview\"" in text
    assert "writer_activation_enabled=False" in text
    assert "reconcile_once" not in text
    assert "GoalDriver" not in text


def test_production_host_has_no_predecessor_driver_import():
    source = inspect.getsource(ProductionGwoHost)
    assert "GoalDriver" not in source
    assert "reconcile_once" not in source
    assert "GitIntegrationBatchAssembler" not in source


def test_normal_repository_is_rejected_by_beta2_install(tmp_path):
    arguments = isolated_beta2_install_arguments(
        target_path=Path("D:/Workstation/github-work-orchestrator"),
        target_isolation_root=tmp_path,
    )
    with pytest.raises(ProductionCompositionError) as raised:
        ProductionGwoHost.install(**arguments)
    assert raised.value.code == "V8_ISOLATED_PREVIEW_REQUIRED"
```

Define the helper used by the tests with this exact construction; it only
rewrites the target and host-configuration values returned by the Task 7
fixture:

```python
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
```

- [ ] **Step 2: Observe RED, minimally replace guidance, and prove GREEN**

```powershell
py -3.13 -m pytest tests/test_implement_gwo_skill.py tests/test_orchestrator_package.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/implement-gwo/SKILL.md skills/orchestrator/scripts/gwo_v8/__init__.py tests/test_implement_gwo_skill.py skills/implement-gwo/.skill-package.json skills/orchestrator/.skill-package.json
git commit -m "docs: route implement-gwo to isolated V8 preview"
```

### Task 9: Add isolated provider/target E2E and local Beta2 evidence

**Files:**
- Modify: `tests/v8_production_test_support.py`
- Modify: `tests/test_v8_production_composition_e2e.py`
- Create: `tests/test_v8_production_docs.py`
- Create: `docs/operations/gwo-v8-production-composition.md`

**Interfaces:**
- Consumes: Task 7 public composition path and temporary Git targets.
- Produces: `assert_isolated_e2e_target(target: Path, root: Path) -> None`, `create_temporary_target(root: Path) -> Path`, the opt-in provider harness, and `write_beta2_evidence_bundle(root: Path, *, subject: dict[str, object], issue_states: dict[str, str], campaign_handle: str, plan_revision_digest: str, writer_generation_before: str, writer_generation_after: str, result_integrity_digests: tuple[str, ...], batch_delivery_proof_digests: tuple[str, ...], issue_137_revalidation: dict[str, object], local_verification_manifest_digest: str, workflow_count: int) -> Path`. The evidence schema is `gwo-v8-beta2-composition-evidence.v2`, binds exact subject SHA/tree, local-manifest digest, zero workflow count, exact Result/Batch/target readbacks, and has no CI URL or hosted repository-check field.

- [ ] **Step 1: Write the failing isolation/evidence tests**

```python
import json
from pathlib import Path
import pytest

from gwo_v8.production_host import ProductionCompositionError
from v8_production_test_support import write_beta2_evidence_bundle

def test_real_provider_e2e_refuses_a_non_temporary_target(tmp_path):
    with pytest.raises(ProductionCompositionError) as raised:
        assert_isolated_e2e_target(Path("D:/Workstation/github-work-orchestrator"), tmp_path)
    assert raised.value.code == "REAL_E2E_TARGET_NOT_ISOLATED"

def test_beta2_manifest_binds_local_subject(tmp_path):
    exact_subject = {"sha": "a" * 40, "tree": "b" * 40, "parents": ["c" * 40]}
    path = write_beta2_evidence_bundle(
        tmp_path,
        subject=exact_subject,
        issue_states={str(n): "CLOSED" for n in (113, 114, 115, 116, 117, 136, 137)},
        campaign_handle="owner/repo:campaign:beta2",
        plan_revision_digest="d" * 64,
        writer_generation_before="v6.1",
        writer_generation_after="v6.1",
        result_integrity_digests=("e" * 64,),
        batch_delivery_proof_digests=("f" * 64,),
        issue_137_revalidation={
            "open_approval_digest": "1" * 64,
            "open_readback_digest": "2" * 64,
            "candidate_route_digest": "3" * 64,
            "formal_review_route_digest": "4" * 64,
            "repair_route_digest": "5" * 64,
            "ordinary_rejection_digest": "6" * 64,
            "replay_restart_digest": "7" * 64,
            "close_approval_digest": "8" * 64,
            "closed_readback_digest": "9" * 64,
        },
        local_verification_manifest_digest="a" * 64,
        workflow_count=0,
    )
    evidence = json.loads(path.read_text(encoding="utf-8"))
    assert evidence["schema_version"] == "gwo-v8-beta2-composition-evidence.v2"
    assert evidence["verification_mode"] == "Local Verification Only"
    assert evidence["subject"] == exact_subject
    assert evidence["workflow_count"] == 0
    assert evidence["writer_generation_before"] == evidence["writer_generation_after"]
    assert evidence["writer_activation_enabled"] is False
    assert set(evidence["full_gate"]) == {
        "pytest", "quick_validate", "package_sync", "diff_check", "clean_status"
    }
```

- [ ] **Step 2: Observe RED**

```powershell
py -3.13 -m pytest tests/test_v8_production_composition_e2e.py::test_real_provider_e2e_refuses_a_non_temporary_target tests/test_v8_production_docs.py::test_beta2_evidence_manifest_has_exact_release_gate_fields -q
```

Expected: FAIL because the isolation helper and v2 Local Verification Only evidence writer do not exist. The opt-in provider case is skipped unless `GWO_V8_REAL_PROVIDER_E2E=1` and an approved command are both present.

- [ ] **Step 3: Implement the Local Verification Only evidence amendment**

Implement `write_beta2_evidence_bundle(...)` directly in
`tests/v8_production_test_support.py`; do not copy the protected plan's stale
hosted-repository acceptance field. Validate a lowercase 40-hex
`subject["sha"]`, a lowercase 40-hex `subject["tree"]`, a `parents` list of
40-hex IDs, a lowercase 64-hex `plan_revision_digest`, non-empty Campaign
handle, equal writer-generation readbacks, seven exact CLOSED issue keys
(#113–#117, #136, and #137), one or more lowercase 64-hex Result digests, one
Batch proof digest per Result, the
exact nine-key `issue_137_revalidation` object from the test above,
`workflow_count == 0`, and `writer_activation_enabled is False`. Render this
exact top-level shape with canonical sorted JSON and a final newline:

```python
manifest = {
    "schema_version": "gwo-v8-beta2-composition-evidence.v2",
    "verification_mode": "Local Verification Only",
    "preview_mode": "beta2_isolated_preview",
    "subject": subject,
    "issue_states": issue_states,
    "campaign_handle": campaign_handle,
    "plan_revision_digest": plan_revision_digest,
    "writer_generation_before": writer_generation_before,
    "writer_generation_after": writer_generation_after,
    "writer_activation_enabled": False,
    "result_integrity_digests": list(result_integrity_digests),
    "batch_delivery_proof_digests": list(batch_delivery_proof_digests),
    "issue_137_revalidation": issue_137_revalidation,
    "local_verification_manifest_digest": local_verification_manifest_digest,
    "workflow_count": 0,
    "full_gate": {
        "pytest": {"status": "passed"},
        "quick_validate": {"status": "passed"},
        "package_sync": {"status": "passed"},
        "diff_check": {"status": "passed"},
        "clean_status": {"status": "passed", "output": ""},
    },
    "target_isolation": True,
}
```

Write through a same-directory temporary file, parse before replacement,
reload the final file, and compare the parsed object and SHA-256. The default
path uses a recording provider and temporary Git target; the optional
real-provider result is diagnostic evidence, never a release gate. No CI URL,
hosted repository-check field, or workflow-run field may be emitted.

```powershell
py -3.13 -m pytest tests/test_v8_production_composition_e2e.py tests/test_v8_production_docs.py -q
```

- [ ] **Step 4: Write and observe the failing runbook contract**

Add `test_production_composition_runbook_contains_beta2_safety_gates` to require the exact public API, five owner modules, CAS/readback restart order, exact Result proof, isolated target configuration, opt-in provider behavior, v2 evidence schema, Local Verification Only commands, #137 approval sequence, and the no-production/no-writer/#118 handoff boundary.

```powershell
py -3.13 -m pytest tests/test_v8_production_docs.py::test_production_composition_runbook_contains_beta2_safety_gates -q
```

Expected: FAIL because `docs/operations/gwo-v8-production-composition.md` does not exist.

- [ ] **Step 5: Create the runbook, prove GREEN, and commit**

```powershell
py -3.13 -m pytest tests/test_v8_production_composition_e2e.py tests/test_v8_production_docs.py -q
git add tests/v8_production_test_support.py tests/test_v8_production_composition_e2e.py tests/test_v8_production_docs.py docs/operations/gwo-v8-production-composition.md
git commit -m "test: add isolated Beta2 composition evidence"
```

### Task 10: Merge C2 implementation and run the local go/no-go gate

**Files:**
- Create: `docs/releases/v8.0.0-beta.2.md`
- Modify: package manifests only through sync if merged implementation changed their sources
- Create externally: `D:/gwo-release-evidence/2026-08-06-gwo-v8-c2-beta2-feature-complete/test_beta2_release_evidence.py`
- Create externally: `beta2-local-go.json`, `tracker-before.json`, `tracker-after.json`

**Interfaces:**
- Consumes: Tasks 4–9, exact branch heads, all reviews, and owner gates.
- Produces: one merged main SHA/tree, a complete Local Verification Only GO/HOLD receipt, and prepared tracker-close authorization. It closes no Issue and creates no tag or Release.

- [ ] **Step 1: Write the failing release-evidence contract**

```python
import json
import sys
from pathlib import Path

evidence = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert evidence["schema"] == "gwo-v8-beta2-local-verification.v1"
assert evidence["verification_mode"] == "Local Verification Only"
assert evidence["phase"] in {"pre-publication", "final"}
if evidence["phase"] == "pre-publication":
    assert evidence["issues"] == {
        **{str(n): "OPEN" for n in (113, 114, 115, 116, 117, 137)},
        "136": "CLOSED",
        "118": "OPEN",
        "119": "OPEN",
    }
    assert evidence["tracker_closure_pending"] is True
else:
    assert evidence["issues"] == {
        **{str(n): "CLOSED" for n in (113, 114, 115, 116, 117, 136, 137)},
        "118": "OPEN",
        "119": "OPEN",
    }
    assert evidence["tracker_closure_pending"] is False
assert evidence["preview_mode"] == "beta2_isolated_preview"
assert evidence["writer_activation_enabled"] is False
assert evidence["v8_writer_activation_enabled"] is False
assert evidence["workflow_count"] == 0
assert evidence["python_version"] == "Python 3.13.11"
assert len(evidence["requirements_digest"]) == 64
assert set(evidence["subject"]) == {"sha", "tree", "parents"}
assert evidence["clean_status"]["output"] == ""
assert evidence["full_suite"]["exit_code"] == 0
assert all(len(item["log_digest"]) == 64 for item in evidence["focused_suites"])
issue_137 = evidence["issue_137_revalidation"]
assert set(issue_137) == {
    "open_approval_digest",
    "open_readback_digest",
    "candidate_route_digest",
    "formal_review_route_digest",
    "repair_route_digest",
    "ordinary_rejection_digest",
    "replay_restart_digest",
    "close_approval_digest",
    "closed_readback_digest",
}
assert all(len(value) == 64 for value in issue_137.values())
```

Save this exact script at the external path above. Run it before the evidence file exists and require `FileNotFoundError`; it must not pass on fabricated fixtures.

- [ ] **Step 2: Merge implementation PRs serially**

Tasks 4–7 are already squash-landed at their owner gates. Merge the parallel Task 8 Skill PR first, then refresh/review and merge the Task 9 isolated-E2E/docs PR. Use one repository-global Integration Lease. Re-read exact head, target, policy, paths, approvals, and authorizations immediately before each squash merge. Verify expected tree and local gates after each merge.

- [ ] **Step 3: Prepare and merge the release-notes PR**

Create `docs/releases/v8.0.0-beta.2.md` on a fresh branch from the post-Task-9 main. State exactly: Feature Complete Preview; Local Verification Only; Actions disabled; no production admission; no writer activation; #118/#119 not included. Review and squash it through the same repository owner gate.

```powershell
git add docs/releases/v8.0.0-beta.2.md
git commit -m "docs: prepare GWO V8 Beta2 release"
```

- [ ] **Step 4: Run the exact repository-wide Local Verification Only gate**

Run in a clean detached checkout at the exact final merged SHA. Capture each command, cwd, executable, argv, start/end time, exit code, complete log and SHA-256, subject SHA/tree/parents, Python version, locked-requirements digest, and local-manifest digest. Focused suites diagnose ownership; the full suite is still mandatory:

```powershell
py -3.13 --version
py -3.13 -m pytest tests/test_orchestrator_package.py -q
py -3.13 -m pytest tests/test_v8_batch_integrator.py tests/test_v8_batch_recovery.py tests/test_v8_batch_beta2.py -q
py -3.13 -m pytest tests/test_v8_execution_kernel_integrity.py tests/test_v8_production_effects.py tests/test_v8_production_host.py tests/test_v8_production_replanning.py tests/test_v8_production_composition_e2e.py -q
py -3.13 -m pytest -q --junitxml D:/gwo-release-evidence/2026-08-06-gwo-v8-c2-beta2-feature-complete/full-pytest.xml
py -3.13 scripts/quick_validate.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git status --porcelain=v1 --untracked-files=all
```

Require Python `3.13.11`, zero repository workflow files, the expected disabled-Actions policy readback, and no output from status. Every command must exit zero. A timeout, missing/unfinished JUnit record, skip outside the explicitly opt-in provider case, dirty status, or Issue drift is HOLD.

- [ ] **Step 5: Prepare, but do not execute, the separate tracker owner gate**

Validate or idempotently adopt the `GWO V8 Beta2` milestone and its exact
#113–#117/#137 assignments from C1; any missing assignment requires a
separately named tracker-owner effect before closure. Persist immutable
close authorizations for #113–#117 and #137, but execute no tracker mutation
in Task 10. Re-read bodies, comments, native blockers, milestones, states, and
URLs. Require #113–#117 and #137 to remain OPEN at this pre-publication
checkpoint, #136 to remain CLOSED without a C2 effect, and #118/#119 to remain
OPEN with their Beta3/GA assignments.

- [ ] **Step 6: Write and read back the pre-publication GO/HOLD evidence**

Write `beta2-local-go.json` atomically with `phase="pre-publication"` and
`tracker_closure_pending=true`,
the exact pre-publication Issue readbacks above, all local command/log
digests, final merged subject SHA/tree/parents, policy receipt, zero workflow
count, unchanged writer generation, `writer_activation_enabled=false`,
`v8_writer_activation_enabled=false`, and isolated-preview configuration. Run the
prepared verifier in pre-publication mode and hash the reloaded bytes. A
missing command, fabricated digest, remote CI field, dirty checkout, or
unexpected Issue state is HOLD.

```powershell
py -3.13 D:/gwo-release-evidence/2026-08-06-gwo-v8-c2-beta2-feature-complete/test_beta2_release_evidence.py D:/gwo-release-evidence/2026-08-06-gwo-v8-c2-beta2-feature-complete/beta2-local-go.json
```

### Task 11: Publish Beta2 and write C2 closure/C3 handoff

**Files:**
- Read: merged `docs/releases/v8.0.0-beta.2.md`
- Create externally: `tag-receipt.json`, `release-receipt.json`, `beta2-local-verification.json`, `tracker-after.json`, `closure.json`, `c3-handoff.json`

**Interfaces:**
- Consumes: Task 10 GO evidence and a publication owner approval/lease.
- Produces: annotated `v8.0.0-beta.2`, matching prerelease, final Issue readbacks, `gwo-v8-c2-closure.v1`, and `gwo-v8-c3-handoff.v1` for #118.

- [ ] **Step 1: Acquire the publication owner gate**

Approval names exactly the annotated tag and prerelease effects at the Task 10 merged SHA. Persist current policy, remote refs, tag absence/presence readback, release absence/presence readback, and immutable authorizations.

- [ ] **Step 2: Create or adopt the annotated tag**

The tag object must be annotated, have name `v8.0.0-beta.2`, message `GWO V8 v8.0.0-beta.2 - Feature Complete Preview`, and peel to the Task 10 merged SHA. If visible after a timeout, adopt only when the old authorization and all fields match.

- [ ] **Step 3: Create or adopt the GitHub prerelease**

Require exact tag, title `GWO V8 Beta2 - Feature Complete Preview`, `prerelease=true`, `draft=false`, and body bytes equal the merged release notes. Persist ID, URL, body digest, and API readback.

- [ ] **Step 4: Close the Beta2 Issues only after publication succeeds**

After the tag and Release have exact readbacks, acquire the prepared tracker
owner approval/lease again and execute the close effects serially. Close
#113–#117 only from their exact merged Result receipts and blocker readbacks;
close #137 only from the fresh `G137_OPEN_RECHECK`, public-seam revalidation,
and its independent close approval. Re-read full Issue JSON, body, comments,
labels, native blockers, milestone, state, and URL after every effect. Read
#136 as CLOSED and perform no #136 mutation. Keep #118 assigned to `GWO V8
Beta3` and OPEN, and #119 assigned to `GWO V8 GA` and OPEN.

- [ ] **Step 5: Write final evidence, closure, and C3 handoff**

Write `beta2-local-verification.json` atomically with `phase="final"` from the exact post-close
tracker readback, changing only the Issue states and adding each close effect
receipt/digest to the pre-publication GO evidence. Run the same verifier and
require the final map to be #113–#117/#136/#137 `CLOSED` with #118/#119 `OPEN`;
recompute every referenced log, Result, Batch, approval, effect receipt,
subject SHA/tree/parents, and local-policy digest. Then write:

`gwo-v8-c2-closure.v1` binds both the C1 closure path/SHA and C1 handoff
path/SHA, merged SHA/tree/ordered parent, all local command manifests, review
digests, complete #113–#119/#136/#137 tracker-before/after readbacks, all
close-effect receipts, tag peel, Release receipt, protected GA identity, and
non-goals. It must not use the C2 plan commit or a plan path as subject
evidence.

`gwo-v8-c3-handoff.v1` names #118 as the next scope, requires
#113/#114/#115/#116/#117/#136/#137 CLOSED, preserves #118 and #119 as OPEN
with their Beta3/GA assignments, records
`writer_activation_enabled=false`, `production_admission=false`,
`default_writer_authority=false`, an empty `activation_authority`, and no
Activation or default-writer authority. #136 is a readback-only prerequisite
in C2; C2 creates no #136 mutation.

- [ ] **Step 6: Verify final non-goals**

Read back that `v8_writer_activation_enabled=false`, no writer generation or
Activation Receipt changed, `production_admission=false`, no production target
was admitted, protected GA remains the frozen ref/SHA/tree/parents, #118/#119
remain OPEN, and default-writer authority remains unchanged. Repository PR,
tracker, tag, and Release leases are effect-specific remote writers and do not
count as V8 production-writer activation.

## Stop Rules

- C1 closure/handoff is missing, stale, or hash-inconsistent.
- Remote main differs from the validated C1 or current C2 target at any mutation boundary.
- Any inherited boundary is unavailable, has the wrong tree/parents, or is not in protected GA history.
- A completed-slice local preview conflicts, changes an unapproved path, or yields a different tree.
- A lane base or generated-manifest write set drifts after dispatch.
- #137 is not OPEN with the C1 owner receipt before revalidation, or its close approval is absent afterward.
- Any Candidate/Review/Repair invalidation creates an extra Review, Candidate submission, authority expansion, or tracker mutation.
- Any Batch retry changes SHA, fallback recurses, unaffected Candidate evidence changes, or result attribution is ambiguous.
- Any test group lacks a completed zero-exit result, local manifest digest, or exact subject SHA/tree.
- An approval, lease, policy, authorization, or effect receipt is absent, expired before an absent effect, malformed, conflicting, or not read back immediately.
- Tag/Release identity differs from merged local evidence.
- Any action attempts production admission, #118 execution, Activation, writer-generation mutation, default-writer change, protected-GA movement, #119 closure, or GA publication.

## Completion Checklist

- [ ] C1 closure and C2 handoff were re-hashed and C2 base was derived dynamically.
- [ ] #113, #114, and #115 exact slices were locally previewed, merged, and read back.
- [ ] #116 Task 5 has a clean review receipt; Tasks 6–7 pass.
- [ ] #117 Tasks 8–10 pass with direct and Singleton-fallback evidence.
- [ ] #137 is revalidated through Candidate, Review, and Repair paths and closed under a separate approval.
- [ ] Production composition Tasks 1–10 pass in isolated preview mode.
- [ ] Package, quick validation, sync, diff, status, grouped full tests, and evidence checks all pass locally.
- [ ] #113–#117 and #137 read back CLOSED; #118/#119 remain OPEN.
- [ ] `v8.0.0-beta.2` is annotated, peels to merged main, and its prerelease body equals merged notes.
- [ ] No production admission, writer activation, default-writer change, protected-GA movement, or GA claim occurred.
- [ ] C2 closure and C3 handoff are schema-valid, digest-bound, and independently readable.

## Plan Self-Review Commands

```powershell
py -3.13 .tmp/c2-planning/test_c2_plan_contract.py
py -3.13 -m pytest tests/test_orchestrator_package.py -q
py -3.13 scripts/quick_validate.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
```

Expected: every command exits zero; the only tracked change during plan authoring is this plan file.
