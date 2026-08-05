# GWO V8 C1 Beta1 Core Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Re-freeze C1 as a metadata and release-control campaign that is
executable with Local Verification Only. C1 does not admit production work,
activate the default writer, move the protected GA ref, or close #113-#119.

**Mode:** Local Verification Only. GitHub Actions acceptance is disabled.
GWO product Hosted CI remains a product-layer delivery mechanism only; it is
not repository acceptance evidence.

**Architecture:** C1 audits one immutable Beta1 successor, exact first-parent
history, external evidence, and live repository policy; runs exact local
Python 3.13 gates; obtains three independent owner-scoped leases; then
performs one exact-head Draft PR, one squash integration, separate tracker
follow-up, and separate immutable publication. Every effect is read back and
bound to a v2 evidence state. C2 owns implementation work after the frozen
boundaries below.

**Tech Stack:** Git 2.x, GitHub CLI, PowerShell 7, Python 3.13.11, pytest,
SHA-256, JSON, GitHub Issues/PRs/milestones/releases, and clean detached
temporary worktrees.

## Global Constraints

- Edit only this plan file for this authoring task. Do not edit runtime code,
  tests, release metadata, the Beta1 branch, protected GA, main, or any
  GitHub remote object. Do not push from the authoring task.
- The authoring worktree starts at
  ca1eb9a6f485576e30616fd1afde7353ba252cbf on the C1 control-branch
  lineage. Execute the committed plan from
  codex/gwo-v8-c1-beta1-plan, not from the authoring worktree.
- C1 never performs production admission, default-writer activation,
  protected-GA movement, or issue closure for #113-#119.
- Never force-push, use --no-verify, git clean, wildcard deletion, or
  removal of a user/C0-retained worktree. A successful clean temporary
  checkout made by a local gate may be removed without --force.
- Repository release acceptance is Local Verification Only. Do not wait for
  GitHub Actions, pull-request status checks, or provider-run output.
- Remote mutations are serial. The three independent owner gates are:
  PR/integration, tracker/milestones, and publication/tag-release. Each gate
  has its own owner-controlled lease receipt. The coordinator never invents,
  sets, or infers approval values or lease IDs.
- The PR gate includes an Integration Lease bound to the exact base. The
  tracker gate includes a tracker writer lease. The publication gate includes
  a publication writer lease and may include local-writer authorization for
  the final canonical-main fast-forward; that is not a fourth owner gate.
- At most five read-only reviewers run concurrently. Every reviewer is exactly
  gpt-5.6-luna with max reasoning, returns text only, and cannot write to the
  coordinator worktree. Never run two implementation writers concurrently.
- Any identity mismatch, malformed receipt, dirty checkout, unresolved
  review thread, missing lease, conflicting object, or missing approval stops
  the current gate without deleting or overwriting evidence.
- Every PowerShell fence below resolves its own Git root, reloads v2 state,
  validates coordinator root/branch/head and all frozen identities, and
  defines every helper it uses inside that fence. No fence uses a variable or
  helper from another fence.
- Every native command checks LASTEXITCODE immediately after it runs. A
  failing command is captured before any later command overwrites its exit
  code.
- Evidence is external to the repository. Empty logs still receive a
  SHA-256. Mutable state is written to a same-directory temporary file,
  parsed, atomically replaced, and parsed again. Before-snapshots, approvals,
  and effect receipts are immutable.
- An absent evidence root starts a fresh run. An existing matching
  gwo-v8-c1-state.v2 file is readback-only resume input. A conflicting or
  malformed root stops before any mutation.

## Files and Interfaces

**Repository file:** only
docs/superpowers/plans/2026-08-05-gwo-v8-c1-beta1-core-preview.md is edited.

**External evidence roots:**

- C1 root:
  D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview
- main/Actions-disable evidence:
  D:/gwo-release-evidence/2026-08-05-disable-github-ci
- Beta1 successor evidence:
  D:/gwo-release-evidence/2026-08-05-gwo-v8-beta1-successor
- retained C0 archive:
  D:/gwo-convergence-archive/20260804T185544Z

**Consumed documents:** CONTEXT.md, ADR-0036, ADR-0040, ADR-0060,
the Beta1 branch release train, the Beta1 GA delivery/release programs, the
C0 receipt/report, and the external JSON/log evidence above.

**Produced external artifacts:** state.json, raw policy/API readbacks,
local-verification manifests, command logs, five review reports, three
owner receipts, PR/merge/tracker/publication receipts, and closure.json.

## Frozen Identity Contract

| Surface | Exact identity |
| --- | --- |
| Base ref | refs/heads/main |
| Base SHA | 2c72d9a153dac07e507c746548258efc44b62875 |
| Base tree | 1905079fa3cd0d90dd9b1930ed5dd726fad9f114 |
| Base parents | [a48c7d6142ae3538725cb876a8782f4ca804cd22] |
| Beta1 ref | refs/heads/codex/gwo-v8-beta1 |
| Beta1 SHA | 70eaa70d5e87ff4f7a6791facd254abab8ff1377 |
| Beta1 tree | 663c5b12502554890bdd92fad6bffc5d6aa9c5f1 |
| Beta1 parents | [3fe3bb829f844627cac82a2d5a24bac8e58564b9] |
| Integration merge SHA | 3fe3bb829f844627cac82a2d5a24bac8e58564b9 |
| Integration tree | 5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10 |
| Integration parents, ordered | [e081e39054b7f9f0a49824eed8354a8a33378ea3, 2c72d9a153dac07e507c746548258efc44b62875] |
| Protected GA ref | refs/heads/codex/gwo-v8-ga-plan |
| Protected GA SHA | 2cd6c46e1484ca140c3a197bbdeb171191d70c20 |
| Protected GA tree | d59a7414cf7f4873d0e1fc03cc2be8a9f18a6577 |
| Protected GA parents | [3b7097213ac482b3a9dcc31320e7bd84191bf2c0] |
| Implementation boundary | e58c596998df90e65349bdb4b5f25d3d9dc1f7e2 |
| Beta1 boundary | ddc1785f84b6a82a7b5c34d5928b046d4e9a781d |

The old a48c7d6 and e081e390 objects are historical parents only: a48c7d6 is
the exact parent of the frozen base, and e081e390 is the first parent of the
exact integration merge. Neither is the active base or active Beta1 subject.

The exact main-to-Beta1 diff is exactly these 17 paths:

1. .superpowers/sdd/2026-08-03-gwo-v8-ga-delivery-program/task-1-report.md
2. `CONTRIBUTING.md`
3. docs/design/gwo-v8-lean-roadmap.md
4. docs/releases/gwo-v8-release-train.md
5. docs/releases/gwo-v8-workspace-convergence.md
6. docs/releases/v8.0.0-beta.1.md
7. docs/superpowers/plans/2026-08-03-gwo-v8-batch-delivery.md
8. docs/superpowers/plans/2026-08-03-gwo-v8-campaign-watchdog.md
9. docs/superpowers/plans/2026-08-03-gwo-v8-candidate-assurance.md
10. docs/superpowers/plans/2026-08-03-gwo-v8-cutover-guard.md
11. docs/superpowers/plans/2026-08-03-gwo-v8-ga-delivery-program.md
12. docs/superpowers/plans/2026-08-03-gwo-v8-production-composition.md
13. docs/superpowers/plans/2026-08-03-gwo-v8-root-canary-ga.md
14. docs/superpowers/plans/2026-08-04-gwo-v8-ga-release-program.md
15. docs/superpowers/plans/2026-08-04-gwo-v8-workspace-convergence-gate.md
16. scripts/quick_validate.py
17. tests/test_orchestrator_package.py

## First-Parent Scope Contract

Do not audit a reachable commit range as one aggregate. Audit this exact
first-parent Beta1 chain after ddc1785:

| Commit | Kind | Authorized non-merge paths |
| --- | --- | --- |
| bda3ede710339100e3c12eb4bea176be0d029e34 | non-merge | the two 2026-08-04 plan files |
| a60371e4b6bcb111ea7183d73db6b743c0f47da4 | non-merge | the two 2026-08-04 plan files |
| e081e39054b7f9f0a49824eed8354a8a33378ea3 | non-merge | release train, convergence receipt, quick validator, package test |
| 3fe3bb829f844627cac82a2d5a24bac8e58564b9 | exact integration merge | verify only SHA/tree/ordered parents; do not use parent diff as an allowlist |
| 70eaa70d5e87ff4f7a6791facd254abab8ff1377 | non-merge | CONTRIBUTING.md, roadmap, release train, Beta1 notes, GA release program, package test |

For each non-merge commit compare its direct parent diff to its row. For the
integration merge compare only exact SHA, tree, and ordered parent identity.

## External Hash Contract

- Beta1 manifest:
  413dd208f18ff6d82d4a64491e03dbfbf06f82712f71b8990d6e95716ecef024
- Beta1 push receipt:
  9bee5bd4f6b3a95236b7125cec2f8549fac8914941f8b104582466901a2f26ca
- main manifest:
  1f01205bc9846bebfd8e767744a60d4d1e4c185f081f6083606047cd37e9d4a3
- main attestation:
  689ccbdf84667d9931b83f18b4234816a853ca61ba6cca8382117f2179e15818
- ci-disable closure:
  dd5dd6724567fee050fe42deecc8bd91baaae674ecba15c0a07cfae474ee386d
- C0 manifest:
  e6939fbd27eedca2198b87f17de0d14bd3e367a65a37fc51542aa87ade889409
- C0 pre-clean bundle:
  5eb64cffaed0ac2fd2748a575cb9cd041b2f7463d4d46d7dbfabf9dbdc0e8530
- C0 post-clean bundle:
  9c91a126003e867a3c5736a4e4a69f5c3c079ce1adf5667c1108351181ac4f40
- C0 remote-GA readback:
  9b0152f0553f18c1ac6a9aac0c5c2ec3b4ecdb4491835d3ebe0318d2d031c1ea
- Beta1 GA-release-program blob:
  189236cb189ca990ee550ea01d047bdf9fc8f36c
- Beta1 convergence-gate blob and protected-GA convergence-gate blob:
  731efda241693ee9d73e1979e9d0c5b339d96e3b
- requirements:
  ee3c9f14db38950f5869759a5a94347197c9d4db3f138147b614ad6c4d862534

## C0 Closure Record

Re-read the receipt at the exact Beta1 SHA and preserve the approved
verification-subject exception. Require source e58c596, protected GA
2cd6c46, refs_deleted false, kept worktrees canonical-main and active-ga,
and the four C0 archive hashes above. Re-read the existing C0 report at
D:/Workstation/gwo-worktrees/issue-136/.superpowers/sdd/2026-08-04-gwo-v8-workspace-convergence-gate/task-8-report.md
and require exactly one line matching the approved Phase 1 PASS under the
verification-subject exception. Do not rerun C0 cleanup.

## Persistent v2 State Contract

The evidence root is
D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview.
state.json uses schema gwo-v8-c1-state.v2 and includes:

- mode Local Verification Only;
- coordinator_root, coordinator_branch, coordinator_head;
- base, Beta1, integration, protected-GA SHA/tree/parents arrays and both
  boundaries;
- the exact 17-path list and first-parent chain;
- all external evidence and policy-readback digests/semantics;
- beta1 and merged-main local-verification manifest/log digests;
- five reports, each verdict and report hash;
- independent PR, tracker, and publication approval/lease receipts;
- PR repository/head/base/path identity and squash merge identity;
- tracker before/after state and mutation receipts;
- tag object/type/peel, release id/URL, normalized notes/body hashes;
- closure and C2 handoff fields.

The state has no hosted acceptance run fields. Every update checks schema and
frozen identity, refuses a conflicting existing field, writes a same-directory
temporary JSON, parses it, replaces state atomically, and parses state again.

## Fence Preamble

Every fence after the first snapshot begins with this complete preamble. It is
repeated rather than relying on process-local state.

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$statePath = Join-Path $evidence 'state.json'
if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { throw 'STATE_MISSING' }
try { $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_MALFORMED' }
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_SCHEMA_OR_MODE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim()
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'COORDINATOR_MUST_BE_ATTACHED' }
$head = (git rev-parse HEAD).Trim()
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'COORDINATOR_HEAD_UNAVAILABLE' }
if ($root -ne $state.coordinator_root -or $branch -ne $state.coordinator_branch -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_IDENTITY_DRIFTED' }
if ($state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or (@($state.identities.base.parents) -join ',') -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22') { throw 'BASE_IDENTITY_DRIFTED' }
if ($state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or (@($state.identities.beta1.parents) -join ',') -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9') { throw 'BETA1_IDENTITY_DRIFTED' }
if ($state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.integration.tree -ne '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -or (@($state.identities.integration.parents) -join ',') -ne 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875') { throw 'INTEGRATION_IDENTITY_DRIFTED' }
if ($state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577' -or (@($state.identities.protected_ga.parents) -join ',') -ne '3b7097213ac482b3a9dcc31320e7bd84191bf2c0') { throw 'PROTECTED_GA_IDENTITY_DRIFTED' }
if ($state.identities.boundaries.implementation -ne 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2' -or $state.identities.boundaries.beta1 -ne 'ddc1785f84b6a82a7b5c34d5928b046d4e9a781d') { throw 'BOUNDARY_IDENTITY_DRIFTED' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
~~~

## Task 0: Freeze coordinator, evidence, and live policy

**Files:** read-only CONTEXT.md, ADR-0036/0040/0060, Beta1 GA program and
release train, C0 receipt/report, external evidence; create only external
state/readbacks/logs.

**Interfaces:** consumes the committed coordinator checkout and frozen Git
objects; produces immutable v2 state, external evidence digest bindings, and
initial live policy readback.

- [ ] **0.1 Start fresh or resume without overwriting evidence.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE
if ($exit -ne 0 -or $branch -ne 'codex/gwo-v8-c1-beta1-plan') { throw 'WRONG_COORDINATOR_BRANCH' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'COORDINATOR_HEAD_UNAVAILABLE' }
git merge-base --is-ancestor ca1eb9a6f485576e30616fd1afde7353ba252cbf HEAD
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'CONTROL_BRANCH_NOT_FROM_REQUIRED_START' }
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$statePath = Join-Path $evidence 'state.json'
if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    try { $old = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_MALFORMED' }
    if ($old.schema -ne 'gwo-v8-c1-state.v2' -or $old.mode -ne 'Local Verification Only' -or $old.coordinator_root -ne $root -or $old.coordinator_branch -ne $branch -or $old.coordinator_head -ne $head) { throw 'CONFLICTING_RESUME_STATE' }
    Write-Output 'MATCHING_V2_STATE_READBACK_ONLY'
    exit 0
}
if (Test-Path -LiteralPath $evidence) { throw 'EVIDENCE_ROOT_HAS_NO_MATCHING_STATE' }
New-Item -ItemType Directory -Path $evidence -ErrorAction Stop | Out-Null
$state = [ordered]@{
    schema = 'gwo-v8-c1-state.v2'
    mode = 'Local Verification Only'
    repository = 'NOirBRight/github-work-orchestrator'
    coordinator_root = $root
    coordinator_branch = $branch
    coordinator_head = $head
    identities = [ordered]@{
        base = [ordered]@{ ref = 'refs/heads/main'; sha = '2c72d9a153dac07e507c746548258efc44b62875'; tree = '1905079fa3cd0d90dd9b1930ed5dd726fad9f114'; parents = @('a48c7d6142ae3538725cb876a8782f4ca804cd22') }
        beta1 = [ordered]@{ ref = 'refs/heads/codex/gwo-v8-beta1'; sha = '70eaa70d5e87ff4f7a6791facd254abab8ff1377'; tree = '663c5b12502554890bdd92fad6bffc5d6aa9c5f1'; parents = @('3fe3bb829f844627cac82a2d5a24bac8e58564b9') }
        integration = [ordered]@{ sha = '3fe3bb829f844627cac82a2d5a24bac8e58564b9'; tree = '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10'; parents = @('e081e39054b7f9f0a49824eed8354a8a33378ea3','2c72d9a153dac07e507c746548258efc44b62875') }
        protected_ga = [ordered]@{ ref = 'refs/heads/codex/gwo-v8-ga-plan'; sha = '2cd6c46e1484ca140c3a197bbdeb171191d70c20'; tree = 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577'; parents = @('3b7097213ac482b3a9dcc31320e7bd84191bf2c0') }
        boundaries = [ordered]@{ implementation = 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2'; beta1 = 'ddc1785f84b6a82a7b5c34d5928b046d4e9a781d' }
    }
    scope = [ordered]@{ main_to_beta1_paths = @(); first_parent_chain = @() }
    external_evidence = [ordered]@{}
    policy_readbacks = [ordered]@{}
    local_verification = [ordered]@{}
    reviews = [ordered]@{}
    approvals = [ordered]@{}
    pr = [ordered]@{}
    tracker = [ordered]@{}
    publication = [ordered]@{}
    closure = [ordered]@{}
    c2_handoff = [ordered]@{}
}
$tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
[IO.File]::WriteAllText($tmp,($state | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
[IO.File]::Move($tmp,$statePath)
try { $check = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_INITIAL_READBACK_FAILED' }
if ($check.schema -ne 'gwo-v8-c1-state.v2') { throw 'STATE_SCHEMA_READBACK_FAILED' }
~~~

Expected: the first run creates one v2 state at the frozen coordinator
identity; a resume reads the matching state and performs no overwrite.

- [ ] **0.2 Re-read, parse, and hash every external evidence file.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE
if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE
if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
function Hash-Expected([string]$path,[string]$expected) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "MISSING:$path" }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "HASH_MISMATCH:$path" }
    return $actual
}
function Read-Json([string]$path) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "MISSING:$path" }
    try { return Get-Content -Raw -LiteralPath $path | ConvertFrom-Json } catch { throw "BAD_JSON:$path" }
}
$disable = 'D:/gwo-release-evidence/2026-08-05-disable-github-ci'
$successor = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-beta1-successor'
$mainManifestPath = Join-Path $disable 'manifest.json'; $mainAttestationPath = Join-Path $disable 'main-attestation.json'; $closurePath = Join-Path $disable 'closure.json'
$betaManifestPath = Join-Path $successor 'manifest.json'; $pushReceiptPath = Join-Path $successor 'push-receipt.json'
$mainManifestHash = Hash-Expected $mainManifestPath '1f01205bc9846bebfd8e767744a60d4d1e4c185f081f6083606047cd37e9d4a3'
$mainAttestationHash = Hash-Expected $mainAttestationPath '689ccbdf84667d9931b83f18b4234816a853ca61ba6cca8382117f2179e15818'
$closureHash = Hash-Expected $closurePath 'dd5dd6724567fee050fe42deecc8bd91baaae674ecba15c0a07cfae474ee386d'
$betaManifestHash = Hash-Expected $betaManifestPath '413dd208f18ff6d82d4a64491e03dbfbf06f82712f71b8990d6e95716ecef024'
$pushReceiptHash = Hash-Expected $pushReceiptPath '9bee5bd4f6b3a95236b7125cec2f8549fac8914941f8b104582466901a2f26ca'
$mainManifest = Read-Json $mainManifestPath; $mainAttestation = Read-Json $mainAttestationPath; $closure = Read-Json $closurePath; $betaManifest = Read-Json $betaManifestPath; $pushReceipt = Read-Json $pushReceiptPath
if ($mainAttestation.source_ref -ne 'refs/heads/main' -or $mainAttestation.source_sha -ne $state.identities.base.sha -or $mainAttestation.source_tree -ne $state.identities.base.tree -or (@($mainAttestation.parent_shas) -join ',') -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -or $mainAttestation.github.merge_method -ne 'squash' -or $mainAttestation.github.actions_enabled -ne $false -or $mainAttestation.verification_manifest_sha256 -ne $mainManifestHash) { throw 'MAIN_ATTESTATION_INVALID' }
if ($closure.main_sha -ne $state.identities.base.sha -or $closure.main_tree -ne $state.identities.base.tree -or $closure.merge_method -ne 'squash' -or $closure.actions_enabled -ne $false -or $closure.workflow_count -ne 0 -or $closure.required_status_rules -ne 0 -or $closure.bypass_actor_count -ne 0) { throw 'CI_DISABLE_CLOSURE_INVALID' }
$rules = @('deletion','non_fast_forward','pull_request','required_linear_history')
if (@(Compare-Object ($rules | Sort-Object) (@($closure.preserved_rule_types) | Sort-Object)).Count -ne 0) { throw 'PRESERVED_RULES_INVALID' }
if ($betaManifest.subject_sha -ne $state.identities.beta1.sha -or $betaManifest.subject_tree -ne $state.identities.beta1.tree -or $betaManifest.main_sha -ne $state.identities.base.sha -or $betaManifest.integration_merge_sha -ne $state.identities.integration.sha -or (@($betaManifest.integration_parent_shas) -join ',') -ne 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875' -or $betaManifest.python_version -ne 'Python 3.13.11' -or $betaManifest.requirements_sha256 -ne 'ee3c9f14db38950f5869759a5a94347197c9d4db3f138147b614ad6c4d862534' -or $betaManifest.final_outcome -ne 'pass' -or $betaManifest.workflow_count -ne 0 -or $betaManifest.diff_checks -ne 'clean' -or $betaManifest.worktree_status -ne 'clean') { throw 'BETA1_MANIFEST_INVALID' }
foreach ($item in @($betaManifest.logs)) {
    if ([IO.Path]::IsPathRooted([string]$item.name) -or ([string]$item.name).Contains('..')) { throw 'LOG_PATH_INVALID' }
    Hash-Expected (Join-Path $successor ([string]$item.name)) ([string]$item.sha256) | Out-Null
}
if ($pushReceipt.branch -ne 'refs/heads/codex/gwo-v8-beta1' -or $pushReceipt.old_sha -ne 'e081e39054b7f9f0a49824eed8354a8a33378ea3' -or $pushReceipt.new_sha -ne $state.identities.beta1.sha -or $pushReceipt.main_sha -ne $state.identities.base.sha -or $pushReceipt.protected_ga_sha -ne $state.identities.protected_ga.sha -or $pushReceipt.verification_manifest_sha256 -ne $betaManifestHash -or $pushReceipt.actions_enabled -ne $false) { throw 'PUSH_RECEIPT_INVALID' }
$archive = 'D:/gwo-convergence-archive/20260804T185544Z'
$c0ManifestHash = Hash-Expected (Join-Path $archive 'convergence-manifest.json') 'e6939fbd27eedca2198b87f17de0d14bd3e367a65a37fc51542aa87ade889409'
$c0PreHash = Hash-Expected (Join-Path $archive 'pre-clean.bundle') '5eb64cffaed0ac2fd2748a575cb9cd041b2f7463d4d46d7dbfabf9dbdc0e8530'
$c0PostHash = Hash-Expected (Join-Path $archive 'post-clean.bundle') '9c91a126003e867a3c5736a4e4a69f5c3c079ce1adf5667c1108351181ac4f40'
$c0RemoteHash = Hash-Expected (Join-Path $archive 'inventory/remote-ga-ref-after.txt') '9b0152f0553f18c1ac6a9aac0c5c2ec3b4ecdb4491835d3ebe0318d2d031c1ea'
$receipt = @(git show "$($state.identities.beta1.sha):docs/releases/gwo-v8-workspace-convergence.md"); $exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'C0_RECEIPT_READ_FAILED' }
$matches = [regex]::Matches(($receipt -join [Environment]::NewLine),'(?ms)^~~~json\s*\r?\n(?<json>\{.*?\})\s*\r?\n~~~\s*(?:\r?\n|$)')
if ($matches.Count -ne 1) { throw 'C0_RECEIPT_JSON_INVALID' }
$c0 = $matches[0].Groups['json'].Value | ConvertFrom-Json
if ($c0.source_sha -ne $state.identities.boundaries.implementation -or $c0.protected_remote_sha -ne $state.identities.protected_ga.sha -or $c0.refs_deleted -ne $false -or $c0.archive_manifest_sha256 -ne $c0ManifestHash -or $c0.pre_clean_bundle_sha256 -ne $c0PreHash -or $c0.post_clean_bundle_sha256 -ne $c0PostHash) { throw 'C0_RECEIPT_FIELDS_INVALID' }
$report = 'D:/Workstation/gwo-worktrees/issue-136/.superpowers/sdd/2026-08-04-gwo-v8-workspace-convergence-gate/task-8-report.md'
if (@(Select-String -LiteralPath $report -Pattern '^Phase 1 is \*\*PASS\*\* under the approved verification-subject exception\.' -CaseSensitive).Count -ne 1) { throw 'C0_APPROVAL_MISSING' }
$betaProgram = (git rev-parse "$($state.identities.beta1.sha):docs/superpowers/plans/2026-08-04-gwo-v8-ga-release-program.md").Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $betaProgram -ne '189236cb189ca990ee550ea01d047bdf9fc8f36c') { throw 'BETA_PROGRAM_BLOB_INVALID' }
$convProgram = (git rev-parse "$($state.identities.beta1.sha):docs/superpowers/plans/2026-08-04-gwo-v8-workspace-convergence-gate.md").Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $convProgram -ne '731efda241693ee9d73e1979e9d0c5b339d96e3b') { throw 'BETA_CONVERGENCE_BLOB_INVALID' }
$gaConv = (git rev-parse "$($state.identities.protected_ga.sha):docs/superpowers/plans/2026-08-04-gwo-v8-workspace-convergence-gate.md").Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $gaConv -ne '731efda241693ee9d73e1979e9d0c5b339d96e3b') { throw 'GA_CONVERGENCE_BLOB_INVALID' }
$state.external_evidence = [ordered]@{ main_manifest_sha256 = $mainManifestHash; main_attestation_sha256 = $mainAttestationHash; ci_disable_closure_sha256 = $closureHash; beta1_manifest_sha256 = $betaManifestHash; beta1_push_receipt_sha256 = $pushReceiptHash; c0_archive = [ordered]@{ manifest_sha256 = $c0ManifestHash; pre_bundle_sha256 = $c0PreHash; post_bundle_sha256 = $c0PostHash; remote_ga_ref_sha256 = $c0RemoteHash }; beta1_plan_blobs = [ordered]@{ ga_release_program = $betaProgram; workspace_convergence_gate = $convProgram; protected_ga_workspace_convergence_gate = $gaConv } }
Save-State $state
~~~

Expected: the real JSON files parse, every successor log and C0 archive file
rehashes to its recorded value, the C0 exception remains approved, and the
Beta1 plan blobs are distinguished correctly.

- [ ] **0.3 Save and parse initial live policy readback.**

Use these exact API calls and save each raw response before parsing:

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
$repo = $state.repository; $dir = Join-Path $evidence 'policy-initial'
if (Test-Path -LiteralPath $dir) { throw 'POLICY_SNAPSHOT_EXISTS' }; New-Item -ItemType Directory -Path $dir -ErrorAction Stop | Out-Null
$actions = @(gh api "repos/$repo/actions/permissions" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'ACTIONS_READBACK_FAILED' }
$workflows = @(gh api "repos/$repo/actions/workflows" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'WORKFLOW_READBACK_FAILED' }
$ruleset = @(gh api "repos/$repo/rulesets/20160628" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'RULESET_READBACK_FAILED' }
[IO.File]::WriteAllText((Join-Path $dir 'actions.json'),($actions -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $dir 'workflows.json'),($workflows -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $dir 'ruleset.json'),($ruleset -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false))
$a = ($actions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($workflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($ruleset -join [Environment]::NewLine) | ConvertFrom-Json
if ($a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0) { throw 'POLICY_SEMANTICS_INVALID' }
$types = @($r.rules | ForEach-Object type); if (@(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0) { throw 'RULESET_TYPES_INVALID' }
$pull = @($r.rules | Where-Object type -eq 'pull_request')[0]; if (@($pull.parameters.allowed_merge_methods) -notcontains 'squash') { throw 'SQUASH_NOT_ALLOWED' }
$hashes = [ordered]@{}; foreach ($name in @('actions.json','workflows.json','ruleset.json')) { $hashes[$name] = (Get-FileHash -LiteralPath (Join-Path $dir $name) -Algorithm SHA256).Hash.ToLowerInvariant() }
$summary = [ordered]@{ actions_enabled = $a.enabled; workflow_count = $w.total_count; required_status_rule_count = @($r.rules | Where-Object type -eq 'required_status_checks').Count; preserved_rule_types = @($types | Sort-Object); bypass_actor_count = @($r.bypass_actors).Count; allowed_merge_methods = @($pull.parameters.allowed_merge_methods); files = $hashes }
[IO.File]::WriteAllText((Join-Path $dir 'summary.json'),($summary | ConvertTo-Json -Depth 20),[Text.UTF8Encoding]::new($false)); $summaryHash = (Get-FileHash -LiteralPath (Join-Path $dir 'summary.json') -Algorithm SHA256).Hash.ToLowerInvariant()
$state.policy_readbacks = [ordered]@{ initial = [ordered]@{ directory = $dir.Replace('\','/'); summary_sha256 = $summaryHash; semantics = $summary } }; Save-State $state
~~~

Expected: Actions are disabled, workflow_count is zero, ruleset 20160628 is
active on the default branch, there is no required status rule, the four
preserved rule types and zero bypass actors are present, and squash is allowed.

## Task 1: Audit exact Git history, paths, refs, and C0 boundaries

**Files:** read-only frozen Git objects, the exact 17 paths, the protected GA
ref, C0 archive, and state; create only external audit JSON.

**Interfaces:** consumes Task 0 state/evidence/policy readbacks and produces
an exact first-parent scope audit without changing any branch or remote object.

- [ ] **1.1 Verify object identity, ancestry, remote refs, exact paths, and
  first-parent allowlists.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
function Git-Text([string[]]$args) { $out = @(git -C $root @args); $code = $LASTEXITCODE; if ($code -ne 0) { throw ('GIT_FAILED:' + ($args -join ' ')) }; return @($out) }
function Tree([string]$sha) { return (Git-Text @('rev-parse',($sha + '^{tree}')))[0].Trim() }
function Parents([string]$sha) { $row = (Git-Text @('show','-s','--format=%P',$sha))[0]; return @($row -split '\s+' | Where-Object { $_ }) }
if ((Tree $state.identities.base.sha) -ne $state.identities.base.tree -or (@(Parents $state.identities.base.sha) -join ',') -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22') { throw 'BASE_OBJECT_INVALID' }
if ((Tree $state.identities.beta1.sha) -ne $state.identities.beta1.tree -or (@(Parents $state.identities.beta1.sha) -join ',') -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9') { throw 'BETA1_OBJECT_INVALID' }
if ((Tree $state.identities.integration.sha) -ne $state.identities.integration.tree -or (@(Parents $state.identities.integration.sha) -join ',') -ne 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875') { throw 'INTEGRATION_OBJECT_INVALID' }
if ((Tree $state.identities.protected_ga.sha) -ne $state.identities.protected_ga.tree -or (@(Parents $state.identities.protected_ga.sha) -join ',') -ne '3b7097213ac482b3a9dcc31320e7bd84191bf2c0') { throw 'PROTECTED_GA_OBJECT_INVALID' }
$base = $state.identities.base.sha; $beta = $state.identities.beta1.sha
$mb = (Git-Text @('merge-base',$base,$beta))[0].Trim(); if ($mb -ne $base) { throw 'MERGE_BASE_INVALID' }
git -C $root merge-base --is-ancestor $state.identities.boundaries.beta1 $beta; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'BETA1_BOUNDARY_NOT_ANCESTOR' }
git -C $root merge-base --is-ancestor $state.identities.boundaries.implementation $state.identities.protected_ga.sha; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'IMPLEMENTATION_BOUNDARY_NOT_GA_ANCESTOR' }
$remoteRows = @(git -C $root ls-remote --heads origin 'refs/heads/main' 'refs/heads/codex/gwo-v8-beta1' 'refs/heads/codex/gwo-v8-ga-plan'); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REMOTE_READ_FAILED' }
$remote = @{}; foreach ($row in $remoteRows) { $parts = $row -split '\s+'; if ($parts.Count -ne 2) { throw 'REMOTE_ROW_INVALID' }; $remote[$parts[1]] = $parts[0] }
if ($remote['refs/heads/main'] -ne $base -or $remote['refs/heads/codex/gwo-v8-beta1'] -ne $beta -or $remote['refs/heads/codex/gwo-v8-ga-plan'] -ne $state.identities.protected_ga.sha) { throw 'FROZEN_REMOTE_MOVED' }
$paths = @('.superpowers/sdd/2026-08-03-gwo-v8-ga-delivery-program/task-1-report.md','CONTRIBUTING.md','docs/design/gwo-v8-lean-roadmap.md','docs/releases/gwo-v8-release-train.md','docs/releases/gwo-v8-workspace-convergence.md','docs/releases/v8.0.0-beta.1.md','docs/superpowers/plans/2026-08-03-gwo-v8-batch-delivery.md','docs/superpowers/plans/2026-08-03-gwo-v8-campaign-watchdog.md','docs/superpowers/plans/2026-08-03-gwo-v8-candidate-assurance.md','docs/superpowers/plans/2026-08-03-gwo-v8-cutover-guard.md','docs/superpowers/plans/2026-08-03-gwo-v8-ga-delivery-program.md','docs/superpowers/plans/2026-08-03-gwo-v8-production-composition.md','docs/superpowers/plans/2026-08-03-gwo-v8-root-canary-ga.md','docs/superpowers/plans/2026-08-04-gwo-v8-ga-release-program.md','docs/superpowers/plans/2026-08-04-gwo-v8-workspace-convergence-gate.md','scripts/quick_validate.py','tests/test_orchestrator_package.py')
$diff = @(git -C $root diff --name-only "$base..$beta"); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'DIFF_READ_FAILED' }; $diff = @($diff | ForEach-Object { $_.Replace('\','/') } | Sort-Object)
if ($diff.Count -ne 17 -or @(Compare-Object ($paths | Sort-Object) $diff).Count -ne 0) { throw 'EXACT_17_PATHS_FAILED' }
$chainExpected = @('bda3ede710339100e3c12eb4bea176be0d029e34','a60371e4b6bcb111ea7183d73db6b743c0f47da4','e081e39054b7f9f0a49824eed8354a8a33378ea3','3fe3bb829f844627cac82a2d5a24bac8e58564b9','70eaa70d5e87ff4f7a6791facd254abab8ff1377')
$chain = @(git -C $root rev-list --first-parent --reverse "$($state.identities.boundaries.beta1)..$beta"); $exit = $LASTEXITCODE; if ($exit -ne 0 -or @(Compare-Object $chainExpected $chain).Count -ne 0) { throw 'FIRST_PARENT_CHAIN_FAILED' }
$allow = @{}
$allow[$chainExpected[0]] = @('docs/superpowers/plans/2026-08-04-gwo-v8-ga-release-program.md','docs/superpowers/plans/2026-08-04-gwo-v8-workspace-convergence-gate.md')
$allow[$chainExpected[1]] = $allow[$chainExpected[0]]
$allow[$chainExpected[2]] = @('docs/releases/gwo-v8-release-train.md','docs/releases/gwo-v8-workspace-convergence.md','scripts/quick_validate.py','tests/test_orchestrator_package.py')
$allow[$chainExpected[4]] = @('CONTRIBUTING.md','docs/design/gwo-v8-lean-roadmap.md','docs/releases/gwo-v8-release-train.md','docs/releases/v8.0.0-beta.1.md','docs/superpowers/plans/2026-08-04-gwo-v8-ga-release-program.md','tests/test_orchestrator_package.py')
foreach ($commit in $chain) {
    $parents = @(Parents $commit)
    if ($commit -eq $state.identities.integration.sha) { if (@($parents) -join ',' -ne 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875' -or (Tree $commit) -ne $state.identities.integration.tree) { throw 'INTEGRATION_READBACK_FAILED' }; continue }
    if ($parents.Count -ne 1) { throw 'NON_MERGE_PARENT_COUNT_FAILED' }
    $touched = @(git -C $root diff-tree --no-commit-id --name-only -r $parents[0] $commit); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'COMMIT_DIFF_FAILED' }; $touched = @($touched | ForEach-Object { $_.Replace('\','/') } | Sort-Object -Unique)
    if (@(Compare-Object ($allow[$commit] | Sort-Object) $touched).Count -ne 0) { throw "COMMIT_SCOPE_FAILED:$commit" }
}
$state.scope = [ordered]@{ main_to_beta1_paths = $paths; first_parent_chain = $chain; remote_refs = $remote }
[IO.File]::WriteAllText((Join-Path $evidence 'scope-audit.json'),($state.scope | ConvertTo-Json -Depth 20),[Text.UTF8Encoding]::new($false)); Save-State $state
~~~

Expected: all exact SHA/tree/parent checks, ancestry, unchanged remote refs,
exactly 17 paths, and every non-merge allowlist pass. The integration merge is
validated only as its own ordered identity.

- [ ] **1.2 Verify the three expected C0/C1 clean worktrees.**

The execution registry must contain exactly:
D:/Workstation/github-work-orchestrator on main,
D:/Workstation/gwo-worktrees/issue-136 on codex/gwo-v8-ga-plan, and the
current coordinator root on codex/gwo-v8-c1-beta1-plan. Require all three
clean. Do not include the authoring c1-plan-writer worktree in an execution
run, and do not remove it as part of C1.

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
$rows = @(git -C $root worktree list --porcelain); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'WORKTREE_LIST_FAILED' }
$roots = @($rows | Where-Object { $_ -like 'worktree *' } | ForEach-Object { ([IO.Path]::GetFullPath($_.Substring(9)).Replace('\','/')).TrimEnd('/') } | Sort-Object -Unique)
$expected = @('D:/Workstation/github-work-orchestrator','D:/Workstation/gwo-worktrees/issue-136',$root) | ForEach-Object { $_.Replace('\','/').TrimEnd('/') } | Sort-Object -Unique
if (@(Compare-Object $expected $roots).Count -ne 0) { throw 'WORKTREE_SET_INVALID' }
foreach ($path in $expected) { $dirty = @(git -C $path status --porcelain=v1 --untracked-files=all); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $dirty.Count -ne 0) { throw "DIRTY_WORKTREE:$path" } }
$state.scope.worktrees = $expected; Save-State $state
~~~

Expected: exactly three clean execution worktrees are read back; no C0
retained/user worktree is deleted.

## Task 2: Run exact Beta1 local verification and five review lanes

**Files:** read-only exact Beta1 tree and hash-locked requirements; create only
external logs, a local verification manifest, and five report receipts.

**Interfaces:** consumes the exact scope/policy state and produces a local
manifest with a SHA-256 plus five independent read-only verdicts.

- [ ] **2.1 Create a dedicated Python 3.13 environment and run the Beta1 gate.**

Use an external venv at
D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview/python313.
Verify Python 3.13.11 and the exact requirements hash before installing. A
failed detached checkout remains for diagnosis; only a successful clean
checkout is removed.
The full-suite command is python -m pytest -q.

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
if ($state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1') { throw 'BETA1_IDENTITY_INVALID' }
function Run-Log([string]$name,[string]$exe,[string[]]$args,[string]$cwd,[string]$log) {
    Push-Location $cwd
    try { & $exe @args *> $log; $code = $LASTEXITCODE } finally { Pop-Location }
    $tail = @(Get-Content -LiteralPath $log -ErrorAction Stop | Select-Object -Last 20) -join [Environment]::NewLine
    $hash = (Get-FileHash -LiteralPath $log -Algorithm SHA256).Hash.ToLowerInvariant()
    return [ordered]@{ name = $name; log = $log.Replace('\','/'); exit_code = $code; summary = $tail; sha256 = $hash }
}
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true); try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
$venv = Join-Path $evidence 'python313'; $python = Join-Path $venv 'Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { & py -3.13 -m venv $venv; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'VENV_CREATE_FAILED' } }
$version = (& $python --version 2>&1) -join ' '; $exit = $LASTEXITCODE; if ($exit -ne 0 -or $version -ne 'Python 3.13.11') { throw 'PYTHON_VERSION_INVALID' }
$requirements = Join-Path $root '.github/requirements-ci-win-py313.txt'; if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) { throw 'REQUIREMENTS_MISSING' }
$reqHash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash.ToLowerInvariant(); if ($reqHash -ne 'ee3c9f14db38950f5869759a5a94347197c9d4db3f138147b614ad6c4d862534') { throw 'REQUIREMENTS_HASH_INVALID' }
$installLog = Join-Path $evidence 'beta1-pip-install.log'; & $python -m pip install --require-hashes -r $requirements *> $installLog; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PIP_INSTALL_FAILED' }
$checkout = Join-Path $evidence 'worktrees/beta1-local'; if (Test-Path -LiteralPath $checkout) { throw 'BETA1_CHECKOUT_EXISTS' }; New-Item -ItemType Directory -Path (Split-Path $checkout) -ErrorAction Stop | Out-Null
git -C $root worktree add --detach $checkout $state.identities.beta1.sha; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'BETA1_CHECKOUT_CREATE_FAILED' }
$logs = Join-Path $evidence 'logs/beta1'; New-Item -ItemType Directory -Path $logs -ErrorAction Stop | Out-Null; $records = @()
$previous = $env:GWO_CONVERGENCE_ARCHIVE_ROOT
try {
    $env:GWO_CONVERGENCE_ARCHIVE_ROOT = 'D:/gwo-convergence-archive/20260804T185544Z'
    $records += Run-Log 'package' $python @('-m','pytest','tests/test_orchestrator_package.py','-q') $checkout (Join-Path $logs 'package.log'); if ($records[-1].exit_code -ne 0) { throw 'PACKAGE_GATE_FAILED' }
    $records += Run-Log 'full' $python @('-m','pytest','-q') $checkout (Join-Path $logs 'full.log'); if ($records[-1].exit_code -ne 0) { throw 'FULL_GATE_FAILED' }
    $records += Run-Log 'quick' $python @('scripts/quick_validate.py') $checkout (Join-Path $logs 'quick.log'); if ($records[-1].exit_code -ne 0) { throw 'QUICK_GATE_FAILED' }
    $records += Run-Log 'sync' $python @('scripts/sync_orchestrator.py','--check') $checkout (Join-Path $logs 'sync.log'); if ($records[-1].exit_code -ne 0) { throw 'SYNC_GATE_FAILED' }
    $records += Run-Log 'diff' 'git' @('-C',$checkout,'diff','--check',"$($state.identities.base.sha)...$($state.identities.beta1.sha)") $checkout (Join-Path $logs 'diff.log'); if ($records[-1].exit_code -ne 0) { throw 'DIFF_GATE_FAILED' }
    $records += Run-Log 'status' 'git' @('-C',$checkout,'status','--porcelain=v1','--untracked-files=all') $checkout (Join-Path $logs 'status.log'); if ($records[-1].exit_code -ne 0 -or $records[-1].summary.Trim().Length -ne 0) { throw 'STATUS_NOT_CLEAN' }
} finally {
    if ($null -eq $previous) { Remove-Item -LiteralPath Env:\GWO_CONVERGENCE_ARCHIVE_ROOT -ErrorAction SilentlyContinue } else { $env:GWO_CONVERGENCE_ARCHIVE_ROOT = $previous }
}
$workflows = @(git -C $checkout ls-tree -r --name-only HEAD .github/workflows); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $workflows.Count -ne 0) { throw 'SUBJECT_WORKFLOW_PRESENT' }
$parents = ((git -C $checkout show -s --format=%P HEAD)); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PARENT_READ_FAILED' }
$manifest = [ordered]@{ schema = 'gwo-c1-local-verification.v2'; mode = 'Local Verification Only'; subject_sha = $state.identities.beta1.sha; subject_tree = $state.identities.beta1.tree; parent_shas = @($parents -split '\s+' | Where-Object { $_ }); base_sha = $state.identities.base.sha; base_tree = $state.identities.base.tree; python_version = $version; requirements_sha256 = $reqHash; commands = $records; workflow_count = 0; final_outcome = 'pass' }
$manifestPath = Join-Path $evidence 'beta1-local-verification.json'; [IO.File]::WriteAllText($manifestPath,($manifest | ConvertTo-Json -Depth 30),[Text.UTF8Encoding]::new($false)); $manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
$clean = @(git -C $checkout status --porcelain=v1 --untracked-files=all); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $clean.Count -ne 0) { throw 'CHECKOUT_NOT_CLEAN' }
git -C $root worktree remove $checkout; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'CLEAN_CHECKOUT_REMOVE_FAILED' }
$state.local_verification.beta1 = [ordered]@{ manifest = $manifestPath.Replace('\','/'); manifest_sha256 = $manifestHash; requirements_sha256 = $reqHash; command_count = $records.Count }
Save-State $state
~~~

Expected: a clean detached Beta1 checkout passes the focused package test,
full pytest, quick validation, sync check, diff check, and clean status. The
manifest lists and re-hashes all six logs, records its own SHA-256 in state,
requires Python 3.13.11 and the locked requirements, and the environment is
restored or removed in finally. workflow_files must be empty. A failed
checkout remains.

- [ ] **2.2 Run five read-only review lanes concurrently.**

Dispatch exactly five read-only jobs, all gpt-5.6-luna with max reasoning:

1. standards: repository instructions, ADR language, executable fences,
   atomic state, Local Verification Only, and stale-contract removal;
2. release/spec: Beta1 release train, GA program, C0 exception, notes schema,
   non-goals, and C2 sequencing;
3. Git/history/scope: all SHA/tree/parent identities, first-parent chain,
   authorized paths, exact 17 paths, and immutable source/GA refs;
4. tracker: #113-#119/#137 state, native blockers, exact milestones,
   conditional #137 rule, ETag behavior, and no issue closure;
5. local/publication safety: evidence/log hashes, policy, local gates, three
   leases, PR repository identity, squash/tree readback, tag peel, body, and
   closure.

Each prompt binds base SHA/tree, Beta1 SHA/tree, and Beta1 manifest digest
413dd208f18ff6d82d4a64491e03dbfbf06f82712f71b8990d6e95716ecef024. Each
report must end with exactly the non-empty line Verdict: PASS. Any other final
line blocks. The coordinator saves each returned report once under
reviews/<lane>.md, computes Get-FileHash SHA256, and records the report hash,
bound identities, and verdict in state. Reviewers do not write to this
worktree. The five lanes may overlap the single full local runner above.

**Expected:** five independent reports and hashes are present, all bind the
same exact subject, and no remote mutation occurs during review.

## Task 3: PR owner gate, exact Draft PR, and squash integration

**Files:** read-only state, policy/evidence/local manifests/reports/refs and
PR list; create only external PR and merge receipts. Do not push Beta1.

**Interfaces:** consumes the exact Beta1 evidence and five PASS reports;
produces one Draft PR and one exact squash result with immediate readback.

- [ ] **3.1 Read the PR owner approval and Integration Lease.**

The owner supplies approvals/pr-owner.json. The coordinator only parses it.
Require schema gwo-v8-c1-pr-owner-approval.v1, repository
NOirBRight/github-work-orchestrator, base ref main and base SHA
2c72d9a153dac07e507c746548258efc44b62875, head ref codex/gwo-v8-beta1 and
head SHA 70eaa70d5e87ff4f7a6791facd254abab8ff1377, action scope exactly
create/ready/merge one squash PR, a non-empty owner identity, a
non-empty owner-controlled integration_lease_id, and an owner receipt hash.
The receipt must bind the lease window from the last base/head/policy
readback through squash and immediate readback. Missing, expired, reused, or
conflicting receipts stop. Never set an approval or lease environment value.

- [ ] **3.2 Re-read live policy immediately before PR creation/reuse.**

Every remote mutation in Tasks 3, 5, and 6 first saves new raw responses for
these exact calls, parses them, and checks all semantics. The helper is
redeclared in each fence.
The exact unquoted API forms are gh api repos/$repo/actions/permissions,
gh api repos/$repo/actions/workflows, and gh api repos/$repo/rulesets/20160628.

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
if ($state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377') { throw 'FROZEN_IDENTITY_INVALID' }
$repo = $state.repository; $approvalPath = Join-Path $evidence 'approvals/pr-owner.json'; if (-not (Test-Path -LiteralPath $approvalPath -PathType Leaf)) { throw 'PR_APPROVAL_MISSING' }
$approval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; if ($approval.schema -ne 'gwo-v8-c1-pr-owner-approval.v1' -or $approval.base_sha -ne $state.identities.base.sha -or $approval.head_sha -ne $state.identities.beta1.sha -or [string]::IsNullOrWhiteSpace([string]$approval.integration_lease_id)) { throw 'PR_APPROVAL_INVALID' }
$policyDir = Join-Path $evidence 'policy-before-pr'; if (Test-Path -LiteralPath $policyDir) { throw 'POLICY_READBACK_EXISTS' }; New-Item -ItemType Directory -Path $policyDir -ErrorAction Stop | Out-Null
$actions = @(gh api "repos/$repo/actions/permissions" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'ACTIONS_READBACK_FAILED' }
$workflows = @(gh api "repos/$repo/actions/workflows" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'WORKFLOW_READBACK_FAILED' }
$ruleset = @(gh api "repos/$repo/rulesets/20160628" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'RULESET_READBACK_FAILED' }
[IO.File]::WriteAllText((Join-Path $policyDir 'actions.json'),($actions -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $policyDir 'workflows.json'),($workflows -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $policyDir 'ruleset.json'),($ruleset -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false))
$a = ($actions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($workflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($ruleset -join [Environment]::NewLine) | ConvertFrom-Json
if ($a.enabled -ne $false -or $w.total_count -ne 0 -or $r.enforcement -ne 'active' -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0) { throw 'POLICY_CHANGED' }
$types = @($r.rules | ForEach-Object type); if (@(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0) { throw 'RULESET_CHANGED' }
$pull = @($r.rules | Where-Object type -eq 'pull_request')[0]; if (@($pull.parameters.allowed_merge_methods) -notcontains 'squash') { throw 'SQUASH_NOT_ALLOWED' }
~~~

- [ ] **3.3 Create/reuse exactly one Draft PR and verify repository identity.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
$repo = $state.repository; $rows = @(gh api "repos/$repo/pulls?state=all&head=NOirBRight:codex/gwo-v8-beta1&base=main&per_page=100" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_LIST_FAILED' }
$prs = ($rows -join [Environment]::NewLine) | ConvertFrom-Json; $matching = @($prs | Where-Object { $_.head.repo.full_name -eq $repo -and $_.base.repo.full_name -eq $repo -and $_.head.ref -eq 'codex/gwo-v8-beta1' -and $_.base.ref -eq 'main' })
if ($matching.Count -gt 1) { throw 'MULTIPLE_EXACT_PRS' }
if ($matching.Count -eq 0) {
    $created = @(gh api -X POST "repos/$repo/pulls" -f title='GWO V8 Beta1 Core Preview' -f head='codex/gwo-v8-beta1' -f base='main' -F draft=true 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_CREATE_FAILED' }; $pr = ($created -join [Environment]::NewLine) | ConvertFrom-Json
} else { $pr = $matching[0]; if ($pr.draft -ne $true) { throw 'EXISTING_PR_NOT_DRAFT' } }
if ($pr.head.repo.full_name -ne $repo -or $pr.base.repo.full_name -ne $repo -or $pr.head.ref -ne 'codex/gwo-v8-beta1' -or $pr.base.ref -ne 'main' -or $pr.head.sha -ne $state.identities.beta1.sha) { throw 'PR_IDENTITY_INVALID' }
$files = @(gh api "repos/$repo/pulls/$($pr.number)/files?per_page=100" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_FILES_FAILED' }; $fileObjects = ($files -join [Environment]::NewLine) | ConvertFrom-Json
$actual = @($fileObjects | ForEach-Object { $_.filename.Replace('\','/') } | Sort-Object); $expected = @($state.scope.main_to_beta1_paths | Sort-Object)
if ($actual.Count -ne 17 -or @(Compare-Object $expected $actual).Count -ne 0) { throw 'PR_EXACT_17_PATHS_FAILED' }
[IO.File]::WriteAllText((Join-Path $evidence 'pr-draft.json'),($pr | ConvertTo-Json -Depth 30),[Text.UTF8Encoding]::new($false))
$state.pr = [ordered]@{ number = $pr.number; head_repository = $pr.head.repo.full_name; base_repository = $pr.base.repo.full_name; head_ref = $pr.head.ref; base_ref = $pr.base.ref; head_sha = $pr.head.sha; paths = $actual; draft = $pr.draft; integration_lease_id = $approval.integration_lease_id }
Save-State $state
~~~

Expected: one Draft PR from codex/gwo-v8-beta1 to main exists or is created,
both repository identities are bound, head/base SHAs are exact, and all 17
paths match. There is no redundant Beta1 branch push.

- [ ] **3.4 Resolve review threads, mark ready, and hold the lease.**

Read the PR again with REST and GraphQL for the exact owner/repository/number.
The readback must include headRepositoryOwner, headRepositoryName,
baseRepositoryOwner, and baseRepositoryName, not only branch names.
Require every reviewThreads node to have isResolved true. reviewDecision may
be empty because the ruleset has zero required approvals; it is not approval
evidence. Save new policy readbacks immediately before the ready mutation and
repeat all policy assertions from 3.2. Then run:

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
if ($state.pr.number -le 0 -or $state.pr.head_sha -ne $state.identities.beta1.sha) { throw 'PR_STATE_INVALID' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
$repo = $state.repository; $pr = @(gh api "repos/$repo/pulls/$($state.pr.number)" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_READ_FAILED' }; $pr = ($pr -join [Environment]::NewLine) | ConvertFrom-Json
if ($pr.head.repo.full_name -ne $repo -or $pr.base.repo.full_name -ne $repo -or $pr.head.sha -ne $state.identities.beta1.sha -or $pr.base.ref -ne 'main') { throw 'PR_MOVED' }
$query = 'query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewDecision reviewThreads(first:100){nodes{isResolved}}}}}'
$review = @(gh api graphql -f query=$query -F owner=NOirBRight -F name=github-work-orchestrator -F number=$state.pr.number 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'THREAD_READ_FAILED' }; $review = ($review -join [Environment]::NewLine) | ConvertFrom-Json
if (@($review.data.repository.pullRequest.reviewThreads.nodes | Where-Object isResolved -ne $true).Count -ne 0) { throw 'UNRESOLVED_REVIEW_THREAD' }
gh pr ready $state.pr.number --repo $repo; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_READY_FAILED' }
$state.pr.ready_at = [DateTime]::UtcNow.ToString('o'); Save-State $state
~~~

Keep the Integration Lease from this final readback through the merge and
immediate readback. No provider check or status wait is part of this gate.

- [ ] **3.5 Merge exactly by squash and read back the one-parent/tree gate.**

Immediately before the mutation repeat policy, PR, path, repository, thread,
source-ref, and protected-GA readbacks inside the owner-controlled lease. Then
run exactly:

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
$repo = $state.repository
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
gh pr merge $state.pr.number --repo $repo --squash --match-head-commit 70eaa70d5e87ff4f7a6791facd254abab8ff1377
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'SQUASH_MERGE_FAILED' }
$pr = @(gh api "repos/$repo/pulls/$($state.pr.number)" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MERGED_PR_READ_FAILED' }; $pr = ($pr -join [Environment]::NewLine) | ConvertFrom-Json
$ref = @(gh api "repos/$repo/git/ref/heads/main" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MAIN_REF_READ_FAILED' }; $ref = ($ref -join [Environment]::NewLine) | ConvertFrom-Json
$oid = $ref.object.sha; $commit = @(gh api "repos/$repo/git/commits/$oid" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MAIN_COMMIT_READ_FAILED' }; $commit = ($commit -join [Environment]::NewLine) | ConvertFrom-Json
if ($pr.merge_commit.oid -ne $oid -or $pr.head.sha -ne $state.identities.beta1.sha -or $pr.head.repo.full_name -ne $repo -or $pr.base.repo.full_name -ne $repo) { throw 'MERGE_PR_IDENTITY_FAILED' }
if (@($commit.parents).Count -ne 1 -or $commit.parents[0].sha -ne $state.identities.base.sha -or $commit.tree.sha -ne $state.identities.beta1.tree) { throw 'SQUASH_TREE_PARENT_FAILED' }
$betaRemote = @(git -C $root ls-remote --heads origin refs/heads/codex/gwo-v8-beta1); $exit = $LASTEXITCODE; if ($exit -ne 0 -or (($betaRemote -split '\s+')[0] -ne $state.identities.beta1.sha)) { throw 'BETA1_REMOTE_CHANGED' }
$gaRemote = @(git -C $root ls-remote --heads origin refs/heads/codex/gwo-v8-ga-plan); $exit = $LASTEXITCODE; if ($exit -ne 0 -or (($gaRemote -split '\s+')[0] -ne $state.identities.protected_ga.sha)) { throw 'PROTECTED_GA_REMOTE_CHANGED' }
$state.pr.merge = [ordered]@{ method = 'squash'; merge_sha = $oid; tree = $commit.tree.sha; parents = @($commit.parents | ForEach-Object sha); source_sha = $state.identities.beta1.sha; integration_lease_id = $state.pr.integration_lease_id }
[IO.File]::WriteAllText((Join-Path $evidence 'merge-effect.json'),($state.pr.merge | ConvertTo-Json -Depth 20),[Text.UTF8Encoding]::new($false)); Save-State $state
~~~

Expected: the remote main commit equals PR mergeCommit.oid, has exactly one
parent equal to the frozen base and tree equal to the Beta1 tree. Beta1 and
protected GA are unchanged. The lease is released only after all readbacks
are saved.

## Task 4: Verify exact squash-merged main locally

**Files:** read-only remote squash result and exact merged tree; create only
external merged-main logs and manifest.

**Interfaces:** consumes merge-effect.json and unchanged source/GA readbacks;
produces merged-main-local-verification.json and its SHA-256 in state.

- [ ] **4.1 Run a fresh detached six-command gate against merged main.**

The merged-main diff check uses base..HEAD, not the three-dot Beta1 form. The
checkout is removed only after every command, log hash, manifest, and clean
status passes. The full-suite command is python -m pytest -q.

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
$merged = $state.pr.merge.merge_sha; if ([string]::IsNullOrWhiteSpace($merged)) { throw 'MERGE_SHA_MISSING' }
$python = Join-Path $evidence 'python313/Scripts/python.exe'; if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'PYTHON_ENV_MISSING' }
$version = (& $python --version 2>&1) -join ' '; $exit = $LASTEXITCODE; if ($exit -ne 0 -or $version -ne 'Python 3.13.11') { throw 'PYTHON_VERSION_INVALID' }
$requirements = Join-Path $root '.github/requirements-ci-win-py313.txt'; $reqHash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash.ToLowerInvariant(); if ($reqHash -ne 'ee3c9f14db38950f5869759a5a94347197c9d4db3f138147b614ad6c4d862534') { throw 'REQUIREMENTS_HASH_INVALID' }
function Run-Log([string]$name,[string]$exe,[string[]]$args,[string]$cwd,[string]$log) { Push-Location $cwd; try { & $exe @args *> $log; $code = $LASTEXITCODE } finally { Pop-Location }; $tail = @(Get-Content -LiteralPath $log -ErrorAction Stop | Select-Object -Last 20) -join [Environment]::NewLine; $hash = (Get-FileHash -LiteralPath $log -Algorithm SHA256).Hash.ToLowerInvariant(); return [ordered]@{ name = $name; log = $log.Replace('\','/'); exit_code = $code; summary = $tail; sha256 = $hash } }
function Save-State([object]$value) { $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp'); [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false)); if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }; try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }; [IO.File]::Replace($tmp,$statePath,$null,$true); $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json }
$checkout = Join-Path $evidence 'worktrees/merged-main'; if (Test-Path -LiteralPath $checkout) { throw 'MERGED_CHECKOUT_EXISTS' }; New-Item -ItemType Directory -Path (Split-Path $checkout) -ErrorAction Stop | Out-Null
git -C $root worktree add --detach $checkout $merged; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MERGED_CHECKOUT_CREATE_FAILED' }
$tree = (git -C $checkout rev-parse 'HEAD^{tree}').Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $tree -ne $state.identities.beta1.tree) { throw 'MERGED_TREE_INVALID' }
$parents = ((git -C $checkout show -s --format=%P HEAD)); $exit = $LASTEXITCODE; if ($exit -ne 0 -or @($parents -split '\s+' | Where-Object { $_ }).Count -ne 1 -or ($parents -split '\s+')[0] -ne $state.identities.base.sha) { throw 'MERGED_PARENT_INVALID' }
$logs = Join-Path $evidence 'logs/merged-main'; New-Item -ItemType Directory -Path $logs -ErrorAction Stop | Out-Null; $records = @(); $previous = $env:GWO_CONVERGENCE_ARCHIVE_ROOT
try {
    $env:GWO_CONVERGENCE_ARCHIVE_ROOT = 'D:/gwo-convergence-archive/20260804T185544Z'
    $records += Run-Log 'package' $python @('-m','pytest','tests/test_orchestrator_package.py','-q') $checkout (Join-Path $logs 'package.log'); if ($records[-1].exit_code -ne 0) { throw 'PACKAGE_GATE_FAILED' }
    $records += Run-Log 'full' $python @('-m','pytest','-q') $checkout (Join-Path $logs 'full.log'); if ($records[-1].exit_code -ne 0) { throw 'FULL_GATE_FAILED' }
    $records += Run-Log 'quick' $python @('scripts/quick_validate.py') $checkout (Join-Path $logs 'quick.log'); if ($records[-1].exit_code -ne 0) { throw 'QUICK_GATE_FAILED' }
    $records += Run-Log 'sync' $python @('scripts/sync_orchestrator.py','--check') $checkout (Join-Path $logs 'sync.log'); if ($records[-1].exit_code -ne 0) { throw 'SYNC_GATE_FAILED' }
    $records += Run-Log 'diff' 'git' @('-C',$checkout,'diff','--check',"$($state.identities.base.sha)..HEAD") $checkout (Join-Path $logs 'diff.log'); if ($records[-1].exit_code -ne 0) { throw 'DIFF_GATE_FAILED' }
    $records += Run-Log 'status' 'git' @('-C',$checkout,'status','--porcelain=v1','--untracked-files=all') $checkout (Join-Path $logs 'status.log'); if ($records[-1].exit_code -ne 0 -or $records[-1].summary.Trim().Length -ne 0) { throw 'STATUS_NOT_CLEAN' }
} finally { if ($null -eq $previous) { Remove-Item -LiteralPath Env:\GWO_CONVERGENCE_ARCHIVE_ROOT -ErrorAction SilentlyContinue } else { $env:GWO_CONVERGENCE_ARCHIVE_ROOT = $previous } }
$workflowFiles = @(git -C $checkout ls-tree -r --name-only HEAD .github/workflows); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $workflowFiles.Count -ne 0) { throw 'SUBJECT_WORKFLOW_PRESENT' }
$manifest = [ordered]@{ schema = 'gwo-c1-local-verification.v2'; mode = 'Local Verification Only'; subject_sha = $merged; subject_tree = $tree; parent_shas = @($parents -split '\s+' | Where-Object { $_ }); base_sha = $state.identities.base.sha; base_tree = $state.identities.base.tree; python_version = $version; requirements_sha256 = $reqHash; commands = $records; workflow_count = 0; final_outcome = 'pass' }
$manifestPath = Join-Path $evidence 'merged-main-local-verification.json'; [IO.File]::WriteAllText($manifestPath,($manifest | ConvertTo-Json -Depth 30),[Text.UTF8Encoding]::new($false)); $manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
$clean = @(git -C $checkout status --porcelain=v1 --untracked-files=all); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $clean.Count -ne 0) { throw 'MERGED_CHECKOUT_NOT_CLEAN' }
git -C $root worktree remove $checkout; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MERGED_CLEAN_CHECKOUT_REMOVE_FAILED' }
$state.local_verification.merged_main = [ordered]@{ manifest = $manifestPath.Replace('\','/'); manifest_sha256 = $manifestHash; requirements_sha256 = $reqHash; command_count = $records.Count }; Save-State $state
~~~

Expected: the exact squash tree passes the complete local gate, all six
commands and hashes are in the manifest, no workflow file is in the subject,
and only the successful clean temporary checkout is removed.

## Task 5: Independent tracker and milestone owner gate

**Files:** read-only merged-main identity, issue/milestone/native-blocker
readbacks, owner receipt, policy, and PR evidence; create only external
tracker snapshots and mutation receipts.

**Interfaces:** consumes the merged-main SHA and produces the prescribed
milestone mapping without issue closure or content rewrite.

- [ ] **5.1 Read owner approval, tracker lease, and immutable before snapshot.**

The owner supplies approvals/tracker-owner.json. Require schema
gwo-v8-c1-tracker-owner-approval.v1, exact merged-main SHA, mutation set
limited to:

- #113, #114, #115, #116, #117, and #137 -> GWO V8 Beta2;
- #118 -> GWO V8 Beta3;
- #119 -> GWO V8 GA;
- conditional reopen of #137 only when #137 is CLOSED and #114 or #115 is
  OPEN.

Require a non-empty owner-controlled tracker_lease_id and a before-snapshot
digest. The receipt does not authorize issue closure, title/body/label/comment
edits, blocker edits, or a different milestone. Never infer or set the
approval or lease.

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
$approvalPath = Join-Path $evidence 'approvals/tracker-owner.json'; if (-not (Test-Path -LiteralPath $approvalPath -PathType Leaf)) { throw 'TRACKER_APPROVAL_MISSING' }
$approval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json
if ($approval.schema -ne 'gwo-v8-c1-tracker-owner-approval.v1' -or $approval.merged_main_sha -ne $state.pr.merge.merge_sha -or [string]::IsNullOrWhiteSpace([string]$approval.tracker_lease_id) -or [string]::IsNullOrWhiteSpace([string]$approval.before_snapshot_sha256)) { throw 'TRACKER_APPROVAL_INVALID' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
$repo = $state.repository; $snapshotPath = Join-Path $evidence 'tracker-before.json'; if (Test-Path -LiteralPath $snapshotPath) { throw 'TRACKER_SNAPSHOT_EXISTS' }
$items = @(); foreach ($number in 113,114,115,116,117,118,119,137) { $json = @(gh api "repos/$repo/issues/$number" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw "ISSUE_READ_FAILED:$number" }; $items += (($json -join [Environment]::NewLine) | ConvertFrom-Json) }
$milestones = @(gh api "repos/$repo/milestones?state=all&per_page=100" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MILESTONE_READ_FAILED' }
$before = [ordered]@{ captured_at = [DateTime]::UtcNow.ToString('o'); issues = $items; milestones = ($milestones -join [Environment]::NewLine) | ConvertFrom-Json }
[IO.File]::WriteAllText($snapshotPath,($before | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false)); $beforeHash = (Get-FileHash -LiteralPath $snapshotPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($beforeHash -ne $approval.before_snapshot_sha256) { throw 'TRACKER_SNAPSHOT_DIGEST_INVALID' }
$state.tracker = [ordered]@{ before_snapshot = $snapshotPath.Replace('\','/'); before_snapshot_sha256 = $beforeHash; tracker_lease_id = $approval.tracker_lease_id; merged_main_sha = $state.pr.merge.merge_sha; mutation_set = $approval.mutation_set }; Save-State $state
~~~

Expected: full issue JSON, URLs, bodies, labels, comments, states,
milestones, and native blockers are preserved; conflicts stop before any
mutation.

- [ ] **5.2 Apply only approved idempotent milestone effects with policy and
  ETag readback.**

For every missing milestone, save new policy responses for
actions/permissions, actions/workflows, and rulesets/20160628, parse the
disabled/zero-workflow/active-ruleset/squash semantics, then POST only the
missing named milestone. Check LASTEXITCODE immediately. For every issue
assignment, re-read the issue and its ETag immediately before PATCH, send
If-Match with that ETag, check the exit code, and read back immediately after.
Never overwrite a concurrent milestone assignment.

The only state mutation permitted beyond milestone assignment is the explicit
conditional #137 reopen. If #137 is CLOSED and #114 or #115 is OPEN, perform
that reopen only when the owner receipt names exactly that mutation; if #137
is OPEN, preserve it. Do not close any issue or alter content.

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
$repo = $state.repository; if ($state.tracker.merged_main_sha -ne $state.pr.merge.merge_sha) { throw 'TRACKER_SHA_INVALID' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
$map = [ordered]@{ 'GWO V8 Beta2' = @(113,114,115,116,117,137); 'GWO V8 Beta3' = @(118); 'GWO V8 GA' = @(119) }
$milestones = @(gh api "repos/$repo/milestones?state=all&per_page=100" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MILESTONE_READ_FAILED' }; $milestones = ($milestones -join [Environment]::NewLine) | ConvertFrom-Json
foreach ($title in $map.Keys) { $found = @($milestones | Where-Object title -eq $title); if ($found.Count -gt 1) { throw "MILESTONE_CONFLICT:$title" }; if ($found.Count -eq 0) { throw "MILESTONE_MISSING_UNDER_OWNER_GATE:$title" } }
foreach ($title in $map.Keys) {
    $milestone = @($milestones | Where-Object title -eq $title)[0]
    foreach ($number in $map[$title]) {
        $issue = @(gh api "repos/$repo/issues/$number" -i 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw "ISSUE_READ_FAILED:$number" }
        $body = ($issue -join [Environment]::NewLine); $etag = ([regex]::Match($body,'(?im)^etag:\s*(?<v>.+)$')).Groups['v'].Value.Trim(); if ([string]::IsNullOrWhiteSpace($etag)) { throw "ETAG_MISSING:$number" }
        $json = ($body -split '\r?\n\r?\n',2)[-1] | ConvertFrom-Json; if ($json.milestone -and $json.milestone.title -ne $title) { throw "MILESTONE_CONFLICT:$number" }
        if (-not $json.milestone) {
            $result = @(gh api -X PATCH "repos/$repo/issues/$number" -H "If-Match: $etag" -F milestone=$milestone.number 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw "MILESTONE_PATCH_FAILED:$number" }
            [IO.File]::WriteAllText((Join-Path $evidence "tracker-$number-$($milestone.number)-after.json"),($result -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false))
        }
        $after = @(gh api "repos/$repo/issues/$number" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw "ISSUE_AFTER_READ_FAILED:$number" }; $afterObject = ($after -join [Environment]::NewLine) | ConvertFrom-Json
        if ($afterObject.milestone.title -ne $title) { throw "MILESTONE_READBACK_FAILED:$number" }
    }
}
$state.tracker.after_captured_at = [DateTime]::UtcNow.ToString('o'); $state.tracker.mutation_set_verified = $true; Save-State $state
~~~

Expected: only the prescribed mapping is read back, #137 obeys the approved
conditional rule, issue content/native blockers remain unchanged, and every
write is protected by an immediate ETag/policy/readback sequence.

## Task 6: Independent publication, tag, and Release owner gate

**Files:** read-only merged-main manifest/logs, tracker receipts, policy,
review reports, PR/merge receipts, and exact notes from merged main; create
only external publication receipts and immutable release objects under owner
approval.

**Interfaces:** consumes the exact squash commit and all earlier gates;
produces annotated v8.0.0-beta.1, peeled target readback, prerelease body, and
Release URL.

- [ ] **6.1 Read publication approval, tag/release state, and exact notes.**

The owner supplies approvals/publication-owner.json. Require schema
gwo-v8-c1-publication-owner-approval.v1, exact merged-main SHA,
mutation set exactly tag v8.0.0-beta.1 plus prerelease, a non-empty
publication_lease_id, and any local-writer authorization separately scoped to
the optional canonical-main fast-forward. Never set an approval value or
invent a lease ID.

Read notes from exact merged main and parse exactly one fenced JSON object with
schema gwo-beta1-release-evidence.v2. Require verification_mode local-only,
core_baseline_sha 2c72d9a153dac07e507c746548258efc44b62875,
core_baseline_tree 1905079fa3cd0d90dd9b1930ed5dd726fad9f114,
Python 3.13.11, requirements digest
ee3c9f14db38950f5869759a5a94347197c9d4db3f138147b614ad6c4d862534,
main manifest digest 1f01205bc9846bebfd8e767744a60d4d1e4c185f081f6083606047cd37e9d4a3,
main attestation digest
689ccbdf84667d9931b83f18b4234816a853ca61ba6cca8382117f2179e15818,
issues #113-#119 read back OPEN, and non_goal Lean V8 production cutover.
The prose also states no production admission and no default-writer
activation. Save exact notes and normalized SHA-256.

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
$approvalPath = Join-Path $evidence 'approvals/publication-owner.json'; if (-not (Test-Path -LiteralPath $approvalPath -PathType Leaf)) { throw 'PUBLICATION_APPROVAL_MISSING' }
$approval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; if ($approval.schema -ne 'gwo-v8-c1-publication-owner-approval.v1' -or $approval.merged_main_sha -ne $state.pr.merge.merge_sha -or [string]::IsNullOrWhiteSpace([string]$approval.publication_lease_id)) { throw 'PUBLICATION_APPROVAL_INVALID' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
$notesLines = @(git show "$($state.pr.merge.merge_sha):docs/releases/v8.0.0-beta.1.md"); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOTES_READ_FAILED' }
$notes = $notesLines -join [Environment]::NewLine; $blocks = [regex]::Matches($notes,'(?ms)^~~~json\s*\r?\n(?<json>\{.*?\})\s*\r?\n~~~\s*(?:\r?\n|$)'); if ($blocks.Count -ne 1) { throw 'NOTES_JSON_COUNT_INVALID' }
$releaseEvidence = $blocks[0].Groups['json'].Value | ConvertFrom-Json
if ($releaseEvidence.schema -ne 'gwo-beta1-release-evidence.v2' -or $releaseEvidence.verification_mode -ne 'local-only' -or $releaseEvidence.core_baseline_sha -ne $state.identities.base.sha -or $releaseEvidence.core_baseline_tree -ne $state.identities.base.tree -or $releaseEvidence.python_version -ne 'Python 3.13.11' -or $releaseEvidence.requirements_sha256 -ne 'ee3c9f14db38950f5869759a5a94347197c9d4db3f138147b614ad6c4d862534' -or $releaseEvidence.local_verification_manifest_sha256 -ne '1f01205bc9846bebfd8e767744a60d4d1e4c185f081f6083606047cd37e9d4a3' -or $releaseEvidence.main_attestation_sha256 -ne '689ccbdf84667d9931b83f18b4234816a853ca61ba6cca8382117f2179e15818' -or $releaseEvidence.non_goal -ne 'Lean V8 production cutover') { throw 'RELEASE_EVIDENCE_INVALID' }
foreach ($number in 113,114,115,116,117,118,119) { if ($releaseEvidence.issues.$number -ne 'OPEN') { throw "RELEASE_ISSUE_STATE_INVALID:$number" } }
if ($notes -notmatch 'no production admission' -or $notes -notmatch 'default-writer') { throw 'RELEASE_NON_GOALS_MISSING' }
$notesPath = Join-Path $evidence 'release-notes-from-merged-sha.md'; [IO.File]::WriteAllText($notesPath,$notes,[Text.UTF8Encoding]::new($false)); $normalizedNotes = $notes -replace '\r\n', ([char]10); $notesHash = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($normalizedNotes)); $notesHash = ([BitConverter]::ToString($notesHash) -replace '-','').ToLowerInvariant()
$repo = $state.repository; $tagRows = @(git -C $root ls-remote --tags origin refs/tags/v8.0.0-beta.1 'refs/tags/v8.0.0-beta.1^{}'); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'TAG_READ_FAILED' }
$releaseProbe = @(gh api "repos/$repo/releases/tags/v8.0.0-beta.1" 2>&1); $releaseExit = $LASTEXITCODE; if ($releaseExit -ne 0 -and (($releaseProbe -join [Environment]::NewLine) -notmatch 'HTTP 404')) { throw 'RELEASE_READ_FAILED' }
if (($tagRows.Count -gt 0) -xor ($releaseExit -eq 0)) { throw 'PARTIAL_PUBLICATION' }
$state.publication = [ordered]@{ owner_receipt = $approvalPath.Replace('\','/'); publication_lease_id = $approval.publication_lease_id; notes_path = $notesPath.Replace('\','/'); notes_sha256 = $notesHash; release_evidence = $releaseEvidence; initial_tag_present = ($tagRows.Count -gt 0); initial_release_present = ($releaseExit -eq 0) }; Save-State $state
~~~

If a matching tag and Release already exist, read them back and do not
recreate them. A partial or conflicting pair stops.

- [ ] **6.2 Create/read back the annotated tag under a fresh policy readback.**

Immediately before the tag mutation, save and parse all three policy API
responses again, verify disabled Actions, zero workflows, active ruleset,
required_linear_history, pull_request, deletion, non_fast_forward, zero
bypass actors, no required status rule, and squash allowed. Re-read source,
protected GA, merged main, notes, and owner lease. Then create the annotated
tag from the exact squash SHA and push only the approved tag:

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
if ($state.pr.merge.method -ne 'squash' -or $state.pr.merge.tree -ne $state.identities.beta1.tree) { throw 'MERGE_STATE_INVALID' }
git tag -a v8.0.0-beta.1 $state.pr.merge.merge_sha -m 'GWO V8 Beta1 - Core Preview'
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'TAG_CREATE_FAILED' }
git push origin refs/tags/v8.0.0-beta.1
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'TAG_PUSH_FAILED' }
~~~

Read direct and peeled tag refs, tag object API, and annotated tag content.
Require object type tag, tag object SHA, and peeled target exactly equal to
the squash commit. Persist tag object/type/peel and all response hashes.
Never move, delete, overwrite, or recreate a conflicting object.

- [ ] **6.3 Create/read back the prerelease with exact body equality.**

Re-read policy and tag/release state immediately before this mutation. Run:

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
$notesPath = $state.publication.notes_path; if (-not (Test-Path -LiteralPath $notesPath -PathType Leaf)) { throw 'NOTES_FILE_MISSING' }
gh release create v8.0.0-beta.1 --repo NOirBRight/github-work-orchestrator --verify-tag --prerelease --title 'GWO V8 Beta1 - Core Preview' --notes-file $notesPath
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'RELEASE_CREATE_FAILED' }
$release = @(gh api "repos/$($state.repository)/releases/tags/v8.0.0-beta.1" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'RELEASE_READBACK_FAILED' }; $release = ($release -join [Environment]::NewLine) | ConvertFrom-Json
if ($release.tag_name -ne 'v8.0.0-beta.1' -or $release.prerelease -ne $true -or $release.draft -ne $false -or $null -eq $release.html_url) { throw 'RELEASE_FIELDS_INVALID' }
$body = ([string]$release.body) -replace '\r\n', ([char]10); $notes = (Get-Content -Raw -LiteralPath $notesPath) -replace '\r\n', ([char]10); if ($body -ne $notes) { throw 'RELEASE_BODY_NOT_EXACT_NOTES' }
$bodyHash = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($body)); $bodyHash = ([BitConverter]::ToString($bodyHash) -replace '-','').ToLowerInvariant()
$state.publication.release = [ordered]@{ id = $release.id; url = $release.html_url; tag_name = $release.tag_name; prerelease = $release.prerelease; draft = $release.draft; notes_sha256 = $state.publication.notes_sha256; body_sha256 = $bodyHash; merged_sha = $state.pr.merge.merge_sha }; Save-State $state
~~~

The exact target identity is the annotated tag's peeled SHA, never only an
API targetCommitish representation. The publication lease remains held
through tag/Release readback and is released only after the receipt is saved.

## Task 7: Closure and C2 handoff

**Files:** read-only all external evidence, refs/API readbacks, local
manifests/logs, reports, three owner receipts, tracker, tag/Release, worktree
registry, and protected GA; create only external closure evidence.

**Interfaces:** consumes all exact release gates and produces closure.json
plus a precise C2 scope handoff without moving protected GA or closing issues.

- [ ] **7.1 Freshly re-verify every identity and evidence digest.**

Use a new fence with the full preamble. Re-read remote refs and require main
equals the recorded squash commit, one parent equals
2c72d9a153dac07e507c746548258efc44b62875, and tree equals
663c5b12502554890bdd92fad6bffc5d6aa9c5f1. Re-read Beta1
70eaa70d5e87ff4f7a6791facd254abab8ff1377 and protected GA
2cd6c46e1484ca140c3a197bbdeb171191d70c20 with their exact trees/parents.

Freshly call and parse:

- repos/NOirBRight/github-work-orchestrator/actions/permissions;
- repos/NOirBRight/github-work-orchestrator/actions/workflows;
- repos/NOirBRight/github-work-orchestrator/rulesets/20160628.

Require Actions disabled, workflow_count zero, active default-branch ruleset,
required_linear_history, pull_request, deletion, non_fast_forward, zero
bypass actors, no required status rule, and squash allowed. Re-hash every
file listed by both local manifests and every review report. Require all five
reports to end exactly Verdict: PASS, all report hashes to match state, all
three approval/lease receipts to bind the exact action scope and SHA, tracker
state to match mapping/conditional #137, tag to be annotated and peeled to
the squash commit, and Release id/URL/body/notes hashes to match.

Require C0 receipt/archive hashes and the approved exception again. Require
exactly three clean execution worktrees: canonical main, active GA, and this
coordinator. A failed temporary checkout is preserved, not deleted. Save a
new closure-preflight directory and digest; do not replace prior evidence.

- [ ] **7.2 Fast-forward canonical local main only with explicit
  local-writer authorization.**

The publication owner receipt may contain local-writer authorization scoped
to a fast-forward from the recorded canonical old SHA to the exact squash
SHA. If absent, record canonical_main_action = read-only and leave canonical
main unchanged. If present, re-read canonical root, current SHA, remote main,
policy, and all gates inside that authorization window, then run:

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
$auth = $state.publication.local_writer_authorization; if ($null -eq $auth) { $state.closure = [ordered]@{ canonical_main_action = 'read-only'; canonical_main_sha = (git -C D:/Workstation/github-work-orchestrator rev-parse HEAD).Trim() }; Save-State $state; exit 0 }
if ($auth.target_sha -ne $state.pr.merge.merge_sha -or $auth.from_sha -ne $state.identities.base.sha) { throw 'LOCAL_WRITER_SCOPE_INVALID' }
git -C D:/Workstation/github-work-orchestrator merge --ff-only $state.pr.merge.merge_sha; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'CANONICAL_FAST_FORWARD_FAILED' }
$canonical = (git -C D:/Workstation/github-work-orchestrator rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $canonical -ne $state.pr.merge.merge_sha) { throw 'CANONICAL_READBACK_FAILED' }
$remote = @(git -C $root ls-remote --heads origin refs/heads/main); $exit = $LASTEXITCODE; if ($exit -ne 0 -or (($remote -split '\s+')[0] -ne $canonical)) { throw 'REMOTE_CANONICAL_MISMATCH' }
$state.closure = [ordered]@{ canonical_main_action = 'authorized-fast-forward'; canonical_main_sha = $canonical; remote_main_sha = $canonical }; Save-State $state
~~~

This is a local branch update only. It is never a remote push. A failure
preserves the branch and records the readback.

- [ ] **7.3 Verify C2 boundaries and write closure/handoff.**

Re-read current Tickets #113-#119 and #137 with their states, milestones,
native blockers, URLs, and preserved #137 content before writing the handoff.
Verify every supplied Git object and ancestry:

| C2 item | Existing exact boundary |
| --- | --- |
| foundation | 77ac3e3 |
| #113 | 07086ce |
| #114 | 657bf23 |
| #115 | a0f6976 |
| #116 WIP | e58c596 |
| C2/Beta2 scope | #113-#117 plus #137 |
| Beta3 | #118 |
| GA | #119 |

The #117 completion and final #137 revalidation entries are implementation
scope with no completed exact SHA. They must never be labeled completed
evidence. Persist closure.json with exact digests, not provider-run fields,
then atomically append closure and c2_handoff to v2 state.

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
$boundaries = [ordered]@{ foundation = '77ac3e3'; issue_113 = '07086ce'; issue_114 = '657bf23'; issue_115 = 'a0f6976'; issue_116_wip = 'e58c596'; beta2_scope = @('#113','#114','#115','#116','#117','#137'); beta3 = '#118'; ga = '#119'; unfinished = @('#117 completion','final #137 revalidation') }
foreach ($name in @('foundation','issue_113','issue_114','issue_115','issue_116_wip')) { git -C $root cat-file -e ($boundaries[$name] + '^{commit}'); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw "BOUNDARY_OBJECT_MISSING:$name" } }
$closure = [ordered]@{ schema = 'gwo-v8-c1-closure.v2'; mode = 'Local Verification Only'; merged_sha = $state.pr.merge.merge_sha; merged_manifest_sha256 = $state.local_verification.merged_main.manifest_sha256; beta1_manifest_sha256 = $state.local_verification.beta1.manifest_sha256; external_evidence = $state.external_evidence; policy_readbacks = $state.policy_readbacks; reviews = $state.reviews; approvals = $state.approvals; tracker = $state.tracker; publication = $state.publication; worktrees = $state.scope.worktrees; protected_ga_sha = $state.identities.protected_ga.sha; c2_handoff = $boundaries; completed_at = [DateTime]::UtcNow.ToString('o') }
$closurePath = Join-Path $evidence 'closure.json'; [IO.File]::WriteAllText($closurePath,($closure | ConvertTo-Json -Depth 50),[Text.UTF8Encoding]::new($false)); $closureHash = (Get-FileHash -LiteralPath $closurePath -Algorithm SHA256).Hash.ToLowerInvariant()
$state.closure = [ordered]@{ path = $closurePath.Replace('\','/'); sha256 = $closureHash; completed_at = $closure.completed_at }; $state.c2_handoff = $boundaries
$tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp'); [IO.File]::WriteAllText($tmp,($state | ConvertTo-Json -Depth 50),[Text.UTF8Encoding]::new($false)); if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }; try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }; [IO.File]::Replace($tmp,$statePath,$null,$true); $check = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($check.closure.sha256 -ne $closureHash) { throw 'CLOSURE_STATE_READBACK_FAILED' }
~~~

Expected: closure contains exact SHA-256 evidence, the three independent
owner gates, exact tracker/tag/Release readbacks, protected-GA identity, and
the C2 handoff. C1 leaves implementation Tickets and writer authority alone.

## Stop Rules

Stop before the next effect if any condition is true:

- state schema/mode/root/branch/head or any frozen SHA/tree/parent/boundary
  identity differs;
- any external JSON, C0 archive file, recorded log, report, or manifest hash
  differs;
- a subject tree contains a workflow, a local command is not clean, Python
  or requirements identity differs, or a local manifest omits a log/hash;
- Actions are enabled, workflows are nonzero, the ruleset is inactive, a
  required status rule appears, a preserved rule disappears, a bypass actor
  appears, or squash is not allowed;
- any review report does not end exactly Verdict: PASS or lacks the exact
  base/Beta1/tree/manifest binding;
- an approval or lease is missing, expired, reused, conflicting, or broader
  than its gate, or the coordinator would need to invent a value;
- PR repository/head/base/path/thread readback changes, the merge is not
  squash with the exact head, the result has a parent other than frozen base,
  or the result tree differs from the Beta1 tree;
- a milestone conflicts, an ETag changes before a conditional write, #137
  does not satisfy the approved reopen condition, or issue content/closure is
  requested;
- a tag or Release is partial/conflicting, the tag is not annotated, the
  peeled SHA differs, the body differs from exact merged-SHA notes, or target
  identity relies only on targetCommitish;
- a failed checkout would be deleted, canonical main would move without
  explicit local-writer authorization, or source/main/protected-GA moves.

## Completion Checklist

- [ ] Only this plan file is changed in the repository.
- [ ] Local Verification Only, no production admission, no default-writer
  activation, no protected-GA movement, and no #113-#119 closure are explicit.
- [ ] State schema gwo-v8-c1-state.v2 contains exact SHA/tree/parent arrays,
  external digests, policy semantics, local manifest/log digests, five
  verdicts, three independent approval/lease receipts, PR/squash identity,
  tracker state, tag peel, Release URL, closure, and C2 fields.
- [ ] Real external JSON/log/archive files were parsed and re-hashed.
- [ ] Exactly 17 paths and the exact first-parent chain passed; the integration
  merge was checked by SHA/tree/ordered parents independently.
- [ ] Both local gates used clean detached worktrees, Python 3.13.11, the
  requirements digest, six logs, SHA-256 manifests, bounded environment
  restoration, and no workflow files.
- [ ] Five read-only gpt-5.6-luna/max reports ran concurrently and each ended
  exactly Verdict: PASS.
- [ ] PR Integration Lease, tracker writer lease, and publication writer
  lease remained independent and serial; no approval was inferred.
- [ ] PR readback bound both repositories and all 17 paths; merge used
  --squash --match-head-commit; result had one parent equal to base and the
  Beta1 tree.
- [ ] Tracker mapping and conditional #137 behavior were read back without
  closing #113-#119.
- [ ] Annotated v8.0.0-beta.1 and prerelease peel/body/notes read back to the
  exact squash commit.
- [ ] Closure freshly verified refs, policy, reviews, approvals, tracker,
  tag/Release, all log hashes, three clean worktrees, and protected GA.
- [ ] C2 boundaries 77ac3e3, 07086ce, 657bf23, a0f6976, and e58c596 are
  verified; unfinished #117/#137 work is scope, not completed evidence.

## TDD and Final Verification

The controller supplied this RED result before editing:

AssertionError: missing required C1 contracts: ['state schema', 'local mode',
'base sha', 'base tree', 'beta1 sha', 'beta1 tree', 'integration merge',
'beta1 manifest digest', 'main manifest digest', 'main attestation digest',
'ci-disable closure digest', 'beta1 evidence root', 'squash merge',
'actions readback', 'workflow readback', 'hash readback', 'new path']

After writing, run the copied exact contract:

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'
if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { throw 'STATE_MISSING' }
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
python .superpowers/sdd/2026-08-05-gwo-v8-c1-plan-hardening/test_plan_contract.py
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'C1_PLAN_CONTRACT_GREEN_FAILED' }
~~~

Then run exactly:

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'
if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { throw 'STATE_MISSING' }
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
git diff --check
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'DIFF_CHECK_FAILED' }
python -m pytest tests/test_orchestrator_package.py -q
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'PACKAGE_TEST_FAILED' }
python scripts/quick_validate.py
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'QUICK_VALIDATE_FAILED' }
python scripts/sync_orchestrator.py --check
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'SYNC_CHECK_FAILED' }
~~~

Before commit, self-review the plan text for zero forbidden hosted-acceptance
fields/commands, zero planning-time hosted gates, zero incompatible-mode
language, zero non-squash/multi-parent integration claims, and no obsolete
path-count claims. Verify every executable fence resolves root, reloads state, checks
frozen identities and LASTEXITCODE, and restores bounded environment state.
Verify no placeholder, stale exact identity, omitted helper, cross-fence
variable, unbound approval/lease, or missing exact path remains.

Commit only this plan file with:

docs: re-freeze C1 for local verification

No push is part of this task.
