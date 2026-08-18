# SDD ledger — plan: docs/superpowers/plans/2026-08-18-gwo-v8-local-only-root-canary-contract.md

## Setup

- Worktree: `D:\Workstation\gwo-worktrees\gwo-v8-local-only-contract`
- Base: `b3a307fb514dd01581c4a4a04a021464541f141e`
- Process: TDD + fresh Luna Max implementer and reviewer per task.

Ruling: Keep the existing hosted BatchIntegrator implementation and legacy verifier fixture path while adding a separate local-only acceptance projection — because repository release acceptance and product Hosted-CI delivery are distinct contracts, and changing the production BatchIntegrator would broaden this #119 gate beyond the approved scope. Cost if wrong: local evidence could still depend on an internal harness implementation that needs a later dedicated refactor; the emitted contract will nevertheless contain no Hosted-CI claim.

## Pre-flight scan

| Scope | Interface/write-set relationship | Result / ruling |
|---|---|---|
| Task 1 → Task 2 | Task 1 `load_ticket_manifest(..., require_real_root_numbers=True)` and the checked-in #195–#198 fixture feed Task 2's root layout; write sets are disjoint. | Compatible; Task 2 must fail closed before creating its isolated root. |
| Task 2 → Task 3 | Task 2 emits `gwo.v8.local-root-acceptance.v1`, `acceptance_mode=local-only-v1`, and `local_evidence`; Task 3 consumes exactly that projection. | Compatible; Task 3 must not fall through to the legacy hosted parser for explicit local mode. |
| Task 2 ↔ Task 4 | Task 2's product acceptance evidence and Task 4's repository release manifest are separate contracts; write sets are disjoint. | Keep Hosted-CI product types unchanged; local release wording must not be used as product delivery evidence. |
| Task 3 ↔ Task 4 | Both add local-only validation but touch different verifier modules and tests. | Require canonical mode/digest binding in both boundaries; preserve each legacy compatibility boundary. |
| Task 1 self-check | Loader, tests, fixture, and v2 contract parity are in one bounded change. | Compatible; exact lowercase `open` and non-empty body are required for ready tickets. |
| Task 2 self-check | Producer API/CLI, tests, and manifest-backed root behavior are in one write set. | Compatible; root requires a real manifest; single/wait/blocked/failure remain local. |
| Task 3 self-check | Local receipt schema, validator, CLI round-trip, and tests are specified together. | Compatible; local evidence must validate local Batch/Lease/target cross-links and reject forbidden aliases recursively. |
| Task 4 self-check | Release verifier, renderer, tests, and docs align one repository-only contract. | Compatible; `local-only-v1` requires explicit disabled-workflow and full-pytest readback, while Hosted-CI remains separate. |
| Task 5 self-check | Ledger/evidence/integration consume Tasks 1–4 only after their scoped reviews. | Must remain serial; no activation, writer cutover, merge, push, or GA tag is part of this plan. |

Ruling: Treat the Task 4 review's two fail-open findings (partial pytest accepted as full; incomplete normalized forbidden-field rejection) as load-bearing and require a scoped fix/re-review before Task 4 can complete. Cost if wrong: the repository release gate could accept incomplete or Hosted/CI-contaminated evidence.

## Whole-branch review adjudication

Ruling: Keep internal Hosted-CI-shaped implementation observations outside the
selected `local_evidence` projection for this plan. The approved architecture
explicitly preserves the production BatchIntegrator/Hosted delivery contract;
the local acceptance boundary is the separate projection and verifier path.
Cost if wrong: a later harness-only cleanup may be needed, but changing the
production delivery contract here would broaden the #119 gate and risk
regressing Hosted-CI product coverage.

Ruling: Treat incomplete local recovery/readback validation, unbound suite
definition/receipt, unbound lease release, weak result cross-links, and
candidate-parent/manifest fallback as load-bearing. The design requires the
root evidence to retain and digest-bind these facts, so the local verifier
must fail closed instead of synthesizing the receipt flags. Cost if wrong:
the root gate could accept a self-consistent but incomplete evidence bundle.

Ruling: Treat the authoritative manifest-loader bypass, ambient Git
configuration/hooks, and local-release canary mode omission as load-bearing
boundary defects. The runner/verifier must use the Task 1 loader, isolate Git
hooks/configuration, and require `local-only-v1` before a local GA pre-tag
decision. Cost if wrong: untrusted or Hosted-shaped evidence could cross the
local-only release boundary.

Ruling: Defer general diagnostic JSON canonicalization hardening and exact
Ticket title/body pinning. They are useful hardening but are not required by
the approved Task 5 contract and would expand this fix wave without changing
the accepted observable gate.

## Tasks

- [x] Task 1: strict real Ticket manifest adapter (implementation + two fixes; scoped review clean)
- [x] Task 2: manifest-backed local root producer (implementation + review clean)
- [x] Task 3: local-only root verifier (implementation + fix; scoped review clean)
- [x] Task 4: repository release contract alignment (implementation + two fixes; scoped review clean)
- [ ] Task 5: integration, review package, and local gate (local gate green; whole-branch review pending)

## Completed task records

- Task 1: complete (commits `109640f..1028f77`, review clean after two scoped fix rounds; final review `task-1-fix2-review.md`).
- Task 1 original review finding: body/state parity fixed in `dbe3983`; fix review found two further v2 parity gaps, exact-int `contract.number` and ascending comment IDs, fixed in `1028f77` and re-reviewed clean.
- Task 2: complete (commit `6efa8c9`, review clean in `task-2-review.md`).
- Task 4: complete (commits `101b66b..714ea05`; two P1 fail-open findings fixed in `eb2a3ce` and `714ea05`, final scoped review clean in `task-4-fix2-review.md`).
- Task 3: complete (implementation `d612883`, fix `8f35b74`; local v2 receipt no longer serializes nullable Hosted/PR fields; focused fix review clean).

## Task 5 integration record

- Two independent producer roots with the fixed run id emitted byte-identical canonical records: SHA-256 `7ec26c6ae4ed4329355159f195b0af0e38a711a2bedf959459b4a125833a2cff`.
- Read-only verifier round-tripped both records to byte-identical `gwo-v8-root-canary-acceptance.v2` receipts: output SHA-256 `738ec2851424c32c577bc0ff471c15277e4a412b15a77d8eb88ad5c02aee1728`; receipt digest `159846bb6ed6b7d599148491cabbef4f772118c1731124d3700947b0e927e562`.
- Both records are `gwo.v8.local-root-acceptance.v1`, `acceptance_mode=local-only-v1`, `gate=LOCAL_ROOT_CANARY_GO`, and `status=Complete`; the local proof partitions are Standard #195/#196/#197 multi and Strict #198 singleton.
- Local evidence and receipt scans found no forbidden Hosted/PR/remote/URL keys and no null placeholders.
- Focused Tasks 1–4 command: `186 passed`; root local-only regression selection after the test-isolation repair: `19 passed`.
- Targeted Ruff, `compileall`, and `git diff --check` pass. Whole-repository Ruff remains a pre-existing baseline failure (`159` findings outside this change set); see `task-5-report.md`.
- Broad release review found three load-bearing local-release boundary gaps; a TDD fix wave is active in `scripts/verify_v8_ga_release.py`, `scripts/render_v8_ga_metadata.py`, and `tests/test_v8_release_metadata.py`. The RED set was five verifier failures plus two renderer failures; the GREEN set is `19 passed`, and the full release suite is now `71 passed`.
- No GitHub/Paseo access, production Store mutation, writer transition, Activation Receipt, merge, push, or GA tag was performed.

## Task 5 completion update

- Task 5: complete (commits `c598b54..f074960`, local gate green; controller review clean; no Critical/Important findings observed).
- Final focused local gate: `328 passed`; changed-file Ruff, compileall, and diff-check passed.
- Fresh deterministic roots with run id `phase5-local-root-final` produced identical record SHA-256 `2d2cde7a0874c909b2d1ebdd64046bd9e61444499995257f7e5b509bd8372551`; read-only receipts were identical SHA-256 `67a0d9017a49033af55573af7904130d7fb02048443a65a0f01cbb4e50a90317`, with `LOCAL_ROOT_CANARY_GO` and `local-only-v1`.
- Full local repository pytest: `3161 passed, 52 skipped, 1 failed, 3 warnings`; the only failure is the pre-existing canonical-checkout-path-bound Beta3 provenance test in the isolated worktree.
- The Luna Max review dispatches were attempted but did not return within bounded waits; the controller performed the scoped review and recorded the verification results above. No GitHub/Paseo access, production Store mutation, writer transition, Activation Receipt, merge, push, or GA tag was performed.
