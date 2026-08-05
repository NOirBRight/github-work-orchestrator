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
- Before any Git remote read or mutation, each fence binds `origin` to the
  exact repository URL `https://github.com/NOirBRight/github-work-orchestrator`
  (allowing the same URL with a trailing `.git`), checks the default branch is
  `main`, and rejects a different remote. Mutation fences repeat this guard;
  they do not rely on an earlier fence or on `$repo` alone.
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
- immutable per-mutation policy and pre-mutation authorization receipt paths,
  SHA-256 values, lease windows, and `authorized_at` timestamps; PR create,
  PR ready, PR merge, tag, tracker, and Release each have their own slot;
- closure and C2 handoff fields.

The state has no hosted acceptance run fields. Every update checks schema and
frozen identity, refuses a conflicting existing field, writes a same-directory
temporary JSON, parses it, replaces state atomically, and parses state again.

## Fence Preamble

Every fence that can mutate GitHub, a Git ref, or canonical main repeats the
complete guards shown in that fence: state schema/mode, coordinator
root/branch/head, frozen identities, and origin repository. A persistent-state
only or strictly read-only audit fence may use the smaller explicit guard set
printed in that fence; it still resolves its own root and reloads state, and
never inherits a variable or helper from another fence.

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
$origin = (git -C $root remote get-url origin).Trim()
$exit = $LASTEXITCODE
if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
$repoInfo = @(gh api repos/NOirBRight/github-work-orchestrator 2>&1)
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'REPOSITORY_READ_FAILED' }
$repoObject = ($repoInfo -join [Environment]::NewLine) | ConvertFrom-Json
if ($repoObject.full_name -ne 'NOirBRight/github-work-orchestrator' -or $repoObject.default_branch -ne 'main') { throw 'DEFAULT_BRANCH_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim()
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'COORDINATOR_MUST_BE_ATTACHED' }
$head = (git rev-parse HEAD).Trim()
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw 'COORDINATOR_HEAD_UNAVAILABLE' }
if ($root -ne $state.coordinator_root -or $branch -ne $state.coordinator_branch -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_IDENTITY_DRIFTED' }
if ($state.identities.base.ref -ne 'refs/heads/main' -or $state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or (@($state.identities.base.parents) -join ',') -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22') { throw 'BASE_IDENTITY_DRIFTED' }
if ($state.identities.beta1.ref -ne 'refs/heads/codex/gwo-v8-beta1' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or (@($state.identities.beta1.parents) -join ',') -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9') { throw 'BETA1_IDENTITY_DRIFTED' }
if ($state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.integration.tree -ne '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -or (@($state.identities.integration.parents) -join ',') -ne 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875') { throw 'INTEGRATION_IDENTITY_DRIFTED' }
if ($state.identities.protected_ga.ref -ne 'refs/heads/codex/gwo-v8-ga-plan' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577' -or (@($state.identities.protected_ga.parents) -join ',') -ne '3b7097213ac482b3a9dcc31320e7bd84191bf2c0') { throw 'PROTECTED_GA_IDENTITY_DRIFTED' }
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
    if ($old.schema -ne 'gwo-v8-c1-state.v2' -or $old.mode -ne 'Local Verification Only' -or $old.coordinator_root -ne $root -or $old.coordinator_branch -ne $branch -or $old.coordinator_head -ne $head -or $null -eq $old.PSObject.Properties['mutation_authorization'] -or $null -eq $old.pr.PSObject.Properties['created_by_plan']) { throw 'CONFLICTING_RESUME_STATE' }
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
    scope = [ordered]@{ main_to_beta1_paths = @(); main_to_beta1_status = [ordered]@{}; first_parent_chain = @(); remote_refs = [ordered]@{}; worktrees = @() }
    external_evidence = [ordered]@{}
    policy_readbacks = [ordered]@{}
    local_verification = [ordered]@{ beta1 = $null; merged_main = $null }
    reviews = [ordered]@{}
    approvals = [ordered]@{ pr = $null; tracker = $null; publication = $null }
    mutation_policy = [ordered]@{ pr_create = $null; pr_ready = $null; pr_merge = $null; tracker = @(); tag = $null; release = $null }
    mutation_authorization = [ordered]@{ pr_create = $null; pr_ready = $null; pr_merge = $null; tracker = @(); tag = $null; release = $null }
    pr = [ordered]@{ created_by_plan = $null; ready_at = $null; ready_approval_sha256 = $null; ready_lease_sha256 = $null; create_authorization_sha256 = $null; ready_authorization_sha256 = $null; merge = $null }
    tracker = [ordered]@{}
    publication = [ordered]@{ tag = $null; release = $null }
    closure_preflight = $null
    canonical_main = $null
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
if ($mainManifest.schema -ne 'gwo-local-verification.v1' -or $mainManifest.mode -ne 'local-only' -or $mainManifest.subject_tree -ne $state.identities.base.tree -or $mainManifest.base_sha -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -or $mainManifest.python_version -ne 'Python 3.13.11' -or $mainManifest.requirements_sha256 -ne 'ee3c9f14db38950f5869759a5a94347197c9d4db3f138147b614ad6c4d862534' -or $mainManifest.final_outcome -ne 'pass' -or $mainManifest.quick_summary -ne 'quick validation passed' -or $mainManifest.sync_summary -ne 'implement-gwo 8.0.0, orchestrator 8.0.0 packages synchronized' -or $mainManifest.diff_check -ne 'clean' -or $mainManifest.worktree_status -ne 'clean') { throw 'MAIN_MANIFEST_FIELDS_INVALID' }
if ($mainManifest.logs.Count -lt 1) { throw 'MAIN_MANIFEST_LOGS_MISSING' }
foreach ($log in @($mainManifest.logs)) { if ([string]::IsNullOrWhiteSpace([string]$log.name) -or [string]$log.sha256 -notmatch '^[0-9a-f]{64}$') { throw 'MAIN_MANIFEST_LOG_ENTRY_INVALID' } }
if ($mainAttestation.schema -ne 'gwo-local-verification-attestation.v1' -or $mainAttestation.source_ref -ne 'refs/heads/main' -or $mainAttestation.source_sha -ne $state.identities.base.sha -or $mainAttestation.source_tree -ne $state.identities.base.tree -or (@($mainAttestation.parent_shas) -join ',') -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -or $mainAttestation.github.merge_method -ne 'squash' -or $mainAttestation.github.actions_enabled -ne $false -or $mainAttestation.verification_manifest_sha256 -ne $mainManifestHash -or $mainAttestation.exact_main_checks.package_pytest -notmatch 'passed' -or $mainAttestation.exact_main_checks.quick_validate -ne 'quick validation passed' -or $mainAttestation.exact_main_checks.sync_check -ne 'implement-gwo 8.0.0, orchestrator 8.0.0 packages synchronized' -or $mainAttestation.exact_main_checks.worktree_status -ne 'clean') { throw 'MAIN_ATTESTATION_INVALID' }
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
$matches = [regex]::Matches(($receipt -join [Environment]::NewLine),'(?ms)^(?<fence>```|~~~)json\s*\r?\n(?<json>\{.*?\})\s*\r?\n\k<fence>\s*(?:\r?\n|$)')
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
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
if ($state.identities.base.ref -ne 'refs/heads/main' -or $state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or (@($state.identities.base.parents) -join ',') -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -or $state.identities.beta1.ref -ne 'refs/heads/codex/gwo-v8-beta1' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.integration.tree -ne '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577') { throw 'FROZEN_IDENTITY_INVALID' }
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
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
$repoInfo = @(gh api "repos/$repo" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REPOSITORY_READ_FAILED' }; $repoObject = ($repoInfo -join [Environment]::NewLine) | ConvertFrom-Json; if ($repoObject.full_name -ne $repo -or $repoObject.default_branch -ne 'main') { throw 'DEFAULT_BRANCH_INVALID' }
$actions = @(gh api repos/$repo/actions/permissions 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'ACTIONS_READBACK_FAILED' }
$workflows = @(gh api "repos/$repo/actions/workflows" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'WORKFLOW_READBACK_FAILED' }
$ruleset = @(gh api "repos/$repo/rulesets/20160628" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'RULESET_READBACK_FAILED' }
[IO.File]::WriteAllText((Join-Path $dir 'actions.json'),($actions -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $dir 'workflows.json'),($workflows -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $dir 'ruleset.json'),($ruleset -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false))
$a = ($actions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($workflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($ruleset -join [Environment]::NewLine) | ConvertFrom-Json
if ($a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0) { throw 'POLICY_SEMANTICS_INVALID' }
$includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; if (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main')) { throw 'RULESET_DEFAULT_BRANCH_NOT_APPLICABLE' }
$types = @($r.rules | ForEach-Object type); if (@(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0) { throw 'RULESET_TYPES_INVALID' }
$pull = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pull -and $null -ne $pull.parameters -and $null -ne $pull.parameters.allowed_merge_methods) { $allowed = @($pull.parameters.allowed_merge_methods) }; if ($allowed -notcontains 'squash') { throw 'SQUASH_NOT_ALLOWED' }
$hashes = [ordered]@{}; foreach ($name in @('actions.json','workflows.json','ruleset.json')) { $hashes[$name] = (Get-FileHash -LiteralPath (Join-Path $dir $name) -Algorithm SHA256).Hash.ToLowerInvariant() }
$summary = [ordered]@{ actions_enabled = $a.enabled; workflow_count = $w.total_count; required_status_rule_count = @($r.rules | Where-Object type -eq 'required_status_checks').Count; preserved_rule_types = @($types | Sort-Object); bypass_actor_count = @($r.bypass_actors).Count; allowed_merge_methods = @($allowed); files = $hashes }
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
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
$remoteRows = @(git -C $root ls-remote --heads origin 'refs/heads/main' 'refs/heads/codex/gwo-v8-beta1' 'refs/heads/codex/gwo-v8-ga-plan'); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REMOTE_READ_FAILED' }
$remote = @{}; foreach ($row in $remoteRows) { $parts = $row -split '\s+'; if ($parts.Count -ne 2) { throw 'REMOTE_ROW_INVALID' }; $remote[$parts[1]] = $parts[0] }
if ($remote['refs/heads/main'] -ne $base -or $remote['refs/heads/codex/gwo-v8-beta1'] -ne $beta -or $remote['refs/heads/codex/gwo-v8-ga-plan'] -ne $state.identities.protected_ga.sha) { throw 'FROZEN_REMOTE_MOVED' }
$paths = @('.superpowers/sdd/2026-08-03-gwo-v8-ga-delivery-program/task-1-report.md','CONTRIBUTING.md','docs/design/gwo-v8-lean-roadmap.md','docs/releases/gwo-v8-release-train.md','docs/releases/gwo-v8-workspace-convergence.md','docs/releases/v8.0.0-beta.1.md','docs/superpowers/plans/2026-08-03-gwo-v8-batch-delivery.md','docs/superpowers/plans/2026-08-03-gwo-v8-campaign-watchdog.md','docs/superpowers/plans/2026-08-03-gwo-v8-candidate-assurance.md','docs/superpowers/plans/2026-08-03-gwo-v8-cutover-guard.md','docs/superpowers/plans/2026-08-03-gwo-v8-ga-delivery-program.md','docs/superpowers/plans/2026-08-03-gwo-v8-production-composition.md','docs/superpowers/plans/2026-08-03-gwo-v8-root-canary-ga.md','docs/superpowers/plans/2026-08-04-gwo-v8-ga-release-program.md','docs/superpowers/plans/2026-08-04-gwo-v8-workspace-convergence-gate.md','scripts/quick_validate.py','tests/test_orchestrator_package.py')
$statusRows = @(git -C $root diff --name-status "$base..$beta"); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'DIFF_READ_FAILED' }
$expectedStatus = [ordered]@{ '.superpowers/sdd/2026-08-03-gwo-v8-ga-delivery-program/task-1-report.md' = 'A'; 'CONTRIBUTING.md' = 'M'; 'docs/design/gwo-v8-lean-roadmap.md' = 'M'; 'docs/releases/gwo-v8-release-train.md' = 'A'; 'docs/releases/gwo-v8-workspace-convergence.md' = 'A'; 'docs/releases/v8.0.0-beta.1.md' = 'A'; 'docs/superpowers/plans/2026-08-03-gwo-v8-batch-delivery.md' = 'A'; 'docs/superpowers/plans/2026-08-03-gwo-v8-campaign-watchdog.md' = 'A'; 'docs/superpowers/plans/2026-08-03-gwo-v8-candidate-assurance.md' = 'A'; 'docs/superpowers/plans/2026-08-03-gwo-v8-cutover-guard.md' = 'A'; 'docs/superpowers/plans/2026-08-03-gwo-v8-ga-delivery-program.md' = 'A'; 'docs/superpowers/plans/2026-08-03-gwo-v8-production-composition.md' = 'A'; 'docs/superpowers/plans/2026-08-03-gwo-v8-root-canary-ga.md' = 'A'; 'docs/superpowers/plans/2026-08-04-gwo-v8-ga-release-program.md' = 'A'; 'docs/superpowers/plans/2026-08-04-gwo-v8-workspace-convergence-gate.md' = 'A'; 'scripts/quick_validate.py' = 'M'; 'tests/test_orchestrator_package.py' = 'M' }
$actualStatus = [ordered]@{}; foreach ($row in $statusRows) { $parts = $row -split "`t",2; if ($parts.Count -ne 2) { throw 'NAME_STATUS_ROW_INVALID' }; $actualStatus[$parts[1].Replace('\','/')] = $parts[0] }
if ($actualStatus.Count -ne 17 -or @(Compare-Object ($expectedStatus.Keys | Sort-Object) ($actualStatus.Keys | Sort-Object)).Count -ne 0) { throw 'EXACT_17_PATHS_FAILED' }
foreach ($path in $expectedStatus.Keys) { if ($actualStatus[$path] -ne $expectedStatus[$path]) { throw "EXACT_17_STATUS_FAILED:$path" } }
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
$state.scope = [ordered]@{ main_to_beta1_paths = $paths; main_to_beta1_status = $expectedStatus; first_parent_chain = $chain; remote_refs = $remote; worktrees = @() }
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
    return [ordered]@{ name = $name; executable = $exe; arguments = @($args); log = $log.Replace('\','/'); exit_code = $code; summary = $tail; output_empty = ((Get-Item -LiteralPath $log).Length -eq 0); sha256 = $hash }
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
function Assert-CommandManifest([object]$manifest,[string]$diffArgument) {
    $expected = @(
        [ordered]@{ name = 'package'; executable = $python; arguments = @('-m','pytest','tests/test_orchestrator_package.py','-q') },
        [ordered]@{ name = 'full'; executable = $python; arguments = @('-m','pytest','-q') },
        [ordered]@{ name = 'quick'; executable = $python; arguments = @('scripts/quick_validate.py') },
        [ordered]@{ name = 'sync'; executable = $python; arguments = @('scripts/sync_orchestrator.py','--check') },
        [ordered]@{ name = 'diff'; executable = 'git'; arguments = @('-C','SUBJECT','diff','--check',$diffArgument) },
        [ordered]@{ name = 'status'; executable = 'git'; arguments = @('-C','SUBJECT','status','--porcelain=v1','--untracked-files=all') }
    )
    $commands = @($manifest.commands); if ($commands.Count -ne 6) { throw 'COMMAND_COUNT_INVALID' }
    for ($i = 0; $i -lt 6; $i++) { $record = $commands[$i]; $actualArgs = @($record.arguments | ForEach-Object { if ($_ -eq $record.arguments[1] -and $record.name -in @('diff','status')) { 'SUBJECT' } else { [string]$_ } }); if ($record.name -ne $expected[$i].name -or $record.executable -ne $expected[$i].executable) { throw "COMMAND_IDENTITY_INVALID:$i" }; if (@(Compare-Object $expected[$i].arguments $actualArgs -SyncWindow 0).Count -ne 0) { throw "COMMAND_ARGUMENTS_INVALID:$i" } }
    $status = $commands[5]; if ($status.output_empty -ne $true -or -not (Test-Path -LiteralPath $status.log -PathType Leaf) -or (Get-Item -LiteralPath $status.log).Length -ne 0) { throw 'STATUS_OUTPUT_NOT_EMPTY' }
}
$manifestPath = Join-Path $evidence 'beta1-local-verification.json'
if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
    try { $existingManifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json } catch { throw 'EXISTING_BETA1_MANIFEST_MALFORMED' }
    if ($existingManifest.schema -ne 'gwo-c1-local-verification.v2' -or $existingManifest.mode -ne 'Local Verification Only' -or $existingManifest.subject_sha -ne $state.identities.beta1.sha -or $existingManifest.subject_tree -ne $state.identities.beta1.tree -or $existingManifest.base_sha -ne $state.identities.base.sha -or $existingManifest.base_tree -ne $state.identities.base.tree -or $existingManifest.python_version -ne 'Python 3.13.11' -or $existingManifest.requirements_sha256 -ne 'ee3c9f14db38950f5869759a5a94347197c9d4db3f138147b614ad6c4d862534' -or $existingManifest.final_outcome -ne 'pass' -or $existingManifest.workflow_count -ne 0 -or @($existingManifest.commands).Count -ne 6) { throw 'EXISTING_BETA1_MANIFEST_IDENTITY_INVALID' }
    Assert-CommandManifest $existingManifest "$($state.identities.base.sha)...$($state.identities.beta1.sha)"
    foreach ($record in @($existingManifest.commands)) { if ($record.exit_code -ne 0 -or -not (Test-Path -LiteralPath $record.log -PathType Leaf)) { throw 'EXISTING_BETA1_LOG_INVALID' }; $actualLogHash = (Get-FileHash -LiteralPath $record.log -Algorithm SHA256).Hash.ToLowerInvariant(); if ($actualLogHash -ne $record.sha256) { throw 'EXISTING_BETA1_LOG_HASH_INVALID' } }
    $existingHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant(); $state.local_verification.beta1 = [ordered]@{ manifest = $manifestPath.Replace('\','/'); manifest_sha256 = $existingHash; requirements_sha256 = $existingManifest.requirements_sha256; command_count = @($existingManifest.commands).Count }; Save-State $state; exit 0
}
$requirements = Join-Path $root '.github/requirements-ci-win-py313.txt'; if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) { throw 'REQUIREMENTS_MISSING' }
$installLog = Join-Path $evidence 'beta1-pip-install.log'; if (Test-Path -LiteralPath $installLog -PathType Leaf) { throw 'EXISTING_INSTALL_LOG_REQUIRES_MANIFEST_RESUME' }
$checkout = Join-Path $evidence 'worktrees/beta1-local'; if (Test-Path -LiteralPath $checkout) { throw 'BETA1_CHECKOUT_EXISTS' }; New-Item -ItemType Directory -Path (Split-Path $checkout) -ErrorAction Stop | Out-Null
git -C $root worktree add --detach $checkout $state.identities.beta1.sha; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'BETA1_CHECKOUT_CREATE_FAILED' }
$subjectRequirements = Join-Path $checkout '.github/requirements-ci-win-py313.txt'; if (-not (Test-Path -LiteralPath $subjectRequirements -PathType Leaf)) { throw 'SUBJECT_REQUIREMENTS_MISSING' }
$reqHash = (Get-FileHash -LiteralPath $subjectRequirements -Algorithm SHA256).Hash.ToLowerInvariant(); if ($reqHash -ne 'ee3c9f14db38950f5869759a5a94347197c9d4db3f138147b614ad6c4d862534') { throw 'SUBJECT_REQUIREMENTS_HASH_INVALID' }
& $python -m pip install --require-hashes -r $subjectRequirements *> $installLog; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PIP_INSTALL_FAILED' }
$logs = Join-Path $evidence 'logs/beta1'; New-Item -ItemType Directory -Path $logs -ErrorAction Stop | Out-Null; if (@(Get-ChildItem -LiteralPath $logs -File -ErrorAction SilentlyContinue).Count -ne 0) { throw 'EXISTING_BETA1_LOG_REQUIRES_MANIFEST_RESUME' }; $records = @()
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
$manifest = [ordered]@{ schema = 'gwo-c1-local-verification.v2'; mode = 'Local Verification Only'; subject_sha = $state.identities.beta1.sha; subject_tree = $state.identities.beta1.tree; parent_shas = @($parents -split '\s+' | Where-Object { $_ }); base_sha = $state.identities.base.sha; base_tree = $state.identities.base.tree; python_version = $version; requirements_path = '.github/requirements-ci-win-py313.txt'; requirements_sha256 = $reqHash; commands = $records; workflow_count = 0; final_outcome = 'pass' }
Assert-CommandManifest $manifest "$($state.identities.base.sha)...$($state.identities.beta1.sha)"
if (Test-Path -LiteralPath $manifestPath -PathType Leaf) { throw 'MANIFEST_APPEARED_DURING_RUN' }; [IO.File]::WriteAllText($manifestPath,($manifest | ConvertTo-Json -Depth 30),[Text.UTF8Encoding]::new($false)); $manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
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
   conditional #137 rule, writer-lease conflict behavior, and no issue closure;
5. local/publication safety: evidence/log hashes, policy, local gates, three
   leases, PR repository identity, squash/tree readback, tag peel, body, and
   closure.

Dispatch these five payloads concurrently through the coordinator's native
agentic dispatch tool. These are tool payloads, not PowerShell commands. Each
worker receives the target plan, CONTEXT.md, ADR-0036/0040/0060, the exact
Beta1 GA program/release train, and the immutable subject lines below.
Each dispatch payload states its subject, expected output, and coordinator
persistence contract explicitly.

**Dispatch-ID: c1-standards**
Model: gpt-5.6-luna
Reasoning: max
Mode: read-only
Subject: repository standards and executable local-only release controls at
base/tree 2c72d9a153dac07e507c746548258efc44b62875 /
1905079fa3cd0d90dd9b1930ed5dd726fad9f114 and Beta1/tree
70eaa70d5e87ff4f7a6791facd254abab8ff1377 /
663c5b12502554890bdd92fad6bffc5d6aa9c5f1.
Expected output: text report with the five exact Subject lines and final
`Verdict: PASS`; otherwise return a blocking finding.
Coordinator persistence: write returned text once to
`reviews/standards.md`, then hash and validate it in the 2.2 fence.

**Dispatch-ID: c1-release-spec**
Model: gpt-5.6-luna
Reasoning: max
Mode: read-only
Subject: release/spec, C0 exception, notes evidence, non-goals, and C2 handoff
for the same exact base/Beta1 SHA/tree and manifest digest.
Expected output: text report with the five exact Subject lines and final
`Verdict: PASS`; otherwise return a blocking finding.
Coordinator persistence: write returned text once to
`reviews/release-spec.md`, then hash and validate it in the 2.2 fence.

**Dispatch-ID: c1-git-history-scope**
Model: gpt-5.6-luna
Reasoning: max
Mode: read-only
Subject: Git SHA/tree/parents, first-parent allowlists, exact A/M 17 paths,
and immutable source/protected-GA refs for the same subject.
Expected output: text report with the five exact Subject lines and final
`Verdict: PASS`; otherwise return a blocking finding.
Coordinator persistence: write returned text once to
`reviews/git-history-scope.md`, then hash and validate it in the 2.2 fence.

**Dispatch-ID: c1-tracker**
Model: gpt-5.6-luna
Reasoning: max
Mode: read-only
Subject: exact tracker mapping, writer lease, immediate semantic readback,
conditional #137 reopen, and no issue closure/content drift.
Expected output: text report with the five exact Subject lines and final
`Verdict: PASS`; otherwise return a blocking finding.
Coordinator persistence: write returned text once to
`reviews/tracker.md`, then hash and validate it in the 2.2 fence.

**Dispatch-ID: c1-local-publication-safety**
Model: gpt-5.6-luna
Reasoning: max
Mode: read-only
Subject: exact local manifests, mutation policy hashes, independent leases,
squash/tree, canonical tracker/tag/Release receipts, and closure drift gates.
Expected output: text report with the five exact Subject lines and final
`Verdict: PASS`; otherwise return a blocking finding.
Coordinator persistence: write returned text once to
`reviews/local-publication-safety.md`, then hash and validate it in 2.2.

Each prompt binds base SHA/tree, Beta1 SHA/tree, and Beta1 manifest digest
413dd208f18ff6d82d4a64491e03dbfbf06f82712f71b8990d6e95716ecef024. Each
report must end with exactly the non-empty line Verdict: PASS. Any other final
line blocks. The coordinator saves each returned report once under
the fixed `reviews/{standards,release-spec,git-history-scope,tracker,local-publication-safety}.md`
files, computes Get-FileHash SHA256, and records each report hash,
bound identities, and verdict in state. Reviewers do not write to this
worktree. The five lanes may overlap the single full local runner above.

The coordinator accepts a report only when it has exactly one each of these
literal subject lines:
`Subject-Base-SHA: 2c72d9a153dac07e507c746548258efc44b62875`,
`Subject-Base-Tree: 1905079fa3cd0d90dd9b1930ed5dd726fad9f114`,
`Subject-Beta1-SHA: 70eaa70d5e87ff4f7a6791facd254abab8ff1377`,
`Subject-Beta1-Tree: 663c5b12502554890bdd92fad6bffc5d6aa9c5f1`, and
`Subject-Beta1-Manifest-SHA256: 413dd208f18ff6d82d4a64491e03dbfbf06f82712f71b8990d6e95716ecef024`.
The report file,
its digest, the normalized five-report binding digest, and the receipt state
are immutable on resume.

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
if ($state.identities.base.ref -ne 'refs/heads/main' -or $state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or (@($state.identities.base.parents) -join ',') -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -or $state.identities.beta1.ref -ne 'refs/heads/codex/gwo-v8-beta1' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577') { throw 'FROZEN_IDENTITY_INVALID' }
if ($state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20') { throw 'FROZEN_IDENTITY_INVALID' }
function Save-State([object]$value) { $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp'); [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 50),[Text.UTF8Encoding]::new($false)); if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }; try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }; [IO.File]::Replace($tmp,$statePath,$null,$true); try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' } }
$reviewDir = Join-Path $evidence 'reviews'; if (-not (Test-Path -LiteralPath $reviewDir -PathType Container)) { throw 'REVIEW_DIRECTORY_MISSING' }
$lanes = @('standards','release-spec','git-history-scope','tracker','local-publication-safety'); $reportRows = @(); $reports = [ordered]@{}
foreach ($lane in $lanes) {
    $path = Join-Path $reviewDir ($lane + '.md'); if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "REVIEW_MISSING:$lane" }
    $raw = Get-Content -Raw -LiteralPath $path; $lines = @($raw -split '\r?\n' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }); if ($lines.Count -eq 0 -or $lines[-1] -cne 'Verdict: PASS') { throw "REVIEW_LAST_LINE_INVALID:$lane" }
    $baseLine = [regex]::Matches($raw,'(?m)^Subject-Base-SHA:\s*(?<v>[0-9a-f]{40})\s*$'); $baseTreeLine = [regex]::Matches($raw,'(?m)^Subject-Base-Tree:\s*(?<v>[0-9a-f]{40})\s*$'); $betaLine = [regex]::Matches($raw,'(?m)^Subject-Beta1-SHA:\s*(?<v>[0-9a-f]{40})\s*$'); $betaTreeLine = [regex]::Matches($raw,'(?m)^Subject-Beta1-Tree:\s*(?<v>[0-9a-f]{40})\s*$'); $manifestLine = [regex]::Matches($raw,'(?m)^Subject-Beta1-Manifest-SHA256:\s*(?<v>[0-9a-f]{64})\s*$')
    if ($baseLine.Count -ne 1 -or $baseLine[0].Groups['v'].Value -ne $state.identities.base.sha -or $baseTreeLine.Count -ne 1 -or $baseTreeLine[0].Groups['v'].Value -ne $state.identities.base.tree -or $betaLine.Count -ne 1 -or $betaLine[0].Groups['v'].Value -ne $state.identities.beta1.sha -or $betaTreeLine.Count -ne 1 -or $betaTreeLine[0].Groups['v'].Value -ne $state.identities.beta1.tree -or $manifestLine.Count -ne 1 -or $manifestLine[0].Groups['v'].Value -ne '413dd208f18ff6d82d4a64491e03dbfbf06f82712f71b8990d6e95716ecef024') { throw "REVIEW_SUBJECT_INVALID:$lane" }
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant(); $reports[$lane] = [ordered]@{ path = $path.Replace('\','/'); sha256 = $hash; verdict = 'PASS'; base_sha = $baseLine[0].Groups['v'].Value; base_tree = $baseTreeLine[0].Groups['v'].Value; beta1_sha = $betaLine[0].Groups['v'].Value; beta1_tree = $betaTreeLine[0].Groups['v'].Value; beta1_manifest_sha256 = $manifestLine[0].Groups['v'].Value }; $reportRows += ($lane + ':' + $hash)
}
$reviewStatePath = Join-Path $reviewDir 'review-state.json'; $reviewBinding = [ordered]@{ schema = 'gwo-v8-c1-review-state.v1'; subject_sha = $state.identities.beta1.sha; subject_tree = $state.identities.beta1.tree; beta1_manifest_sha256 = '413dd208f18ff6d82d4a64491e03dbfbf06f82712f71b8990d6e95716ecef024'; reports = $reports; all_verdicts = @('PASS','PASS','PASS','PASS','PASS') }; $reviewText = $reviewBinding | ConvertTo-Json -Depth 30
if (Test-Path -LiteralPath $reviewStatePath -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $reviewStatePath) -ne $reviewText) { throw 'REVIEW_STATE_CONFLICT' } } else { [IO.File]::WriteAllText($reviewStatePath,$reviewText,[Text.UTF8Encoding]::new($false)) }
$reviewStateHash = (Get-FileHash -LiteralPath $reviewStatePath -Algorithm SHA256).Hash.ToLowerInvariant(); $state.reviews = [ordered]@{ lanes = $reports; state_path = $reviewStatePath.Replace('\','/'); state_sha256 = $reviewStateHash; all_verdicts = @('PASS','PASS','PASS','PASS','PASS') }; Save-State $state
$check = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($check.reviews.state_sha256 -ne $reviewStateHash -or @($check.reviews.all_verdicts | Where-Object { $_ -ne 'PASS' }).Count -ne 0) { throw 'REVIEW_STATE_READBACK_INVALID' }
~~~

**Expected:** five independent reports and hashes are present, all bind the
same exact subject, and no remote mutation occurs during review.

## Task 3: PR owner gate, exact Draft PR, and squash integration

**Files:** read-only state, policy/evidence/local manifests/reports/refs and
PR list; create only external PR and merge receipts. Do not push Beta1.

**Interfaces:** consumes the exact Beta1 evidence and five PASS reports;
produces one Draft PR and one exact squash result with immediate readback.

- [ ] **3.1 Read the PR owner approval and Integration Lease.**

The owner supplies approvals/pr-owner.json and the separate
approvals/pr-integration-lease.json. The coordinator only parses those owner
files; it never supplies an approval, owner, lease id, or environment value.
Require schema gwo-v8-c1-pr-owner-approval.v1, `approved=true`, repository
NOirBRight/github-work-orchestrator, base ref main and base SHA
2c72d9a153dac07e507c746548258efc44b62875, head ref codex/gwo-v8-beta1 and
head SHA 70eaa70d5e87ff4f7a6791facd254abab8ff1377, action scope exactly
create/ready/merge one squash PR, a non-empty owner identity, a
non-empty owner-controlled integration_lease_id, and an owner receipt hash.
Require the lease file schema gwo-v8-c1-integration-lease.v1, the same owner,
repository, base/head SHA/tree, exact action scope, its immutable issued
`state=active`, a parseable ordered historical window,
`scope=repository-global-remote-writer`,
`exclusive_remote_writer_control=true`, and approval_sha256 equal to the hash
of pr-owner.json. This Integration Lease is the repository-global single-writer
serialization guarantee; it is not a GitHub base-SHA CAS. STOP
unless its owner can exclude every other remote writer for the entire final
base/head/policy/squash/readback window.
The repository-global Integration Lease must therefore prove a single remote writer
throughout that window; otherwise the coordinator stops.
Hash both files and persist/reload both hashes; a missing, reused, or
conflicting receipt stops. Expiry stops a still-absent mutation, but does not
invalidate an already-visible effect whose immutable pre-mutation
authorization fell inside that original window. The lease remains held from
the final policy/base/head readback through merge readback.

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
if ($state.identities.base.ref -ne 'refs/heads/main' -or $state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or $state.identities.beta1.ref -ne 'refs/heads/codex/gwo-v8-beta1' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577') { throw 'FROZEN_IDENTITY_INVALID' }
$fullIdentityValid = $root -eq $state.coordinator_root -and (@($state.identities.base.parents) -join ',') -eq 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -and (@($state.identities.beta1.parents) -join ',') -eq '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -and $state.identities.integration.sha -eq '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -and $state.identities.integration.tree -eq '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -and (@($state.identities.integration.parents) -join ',') -eq 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875' -and $state.identities.protected_ga.ref -eq 'refs/heads/codex/gwo-v8-ga-plan' -and (@($state.identities.protected_ga.parents) -join ',') -eq '3b7097213ac482b3a9dcc31320e7bd84191bf2c0' -and $state.identities.boundaries.implementation -eq 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2' -and $state.identities.boundaries.beta1 -eq 'ddc1785f84b6a82a7b5c34d5928b046d4e9a781d'; if (-not $fullIdentityValid) { throw 'FULL_FROZEN_IDENTITY_INVALID' }
function Save-State([object]$value) { $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp'); [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 50),[Text.UTF8Encoding]::new($false)); if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }; try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }; [IO.File]::Replace($tmp,$statePath,$null,$true); try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' } }
$approvalPath = Join-Path $evidence 'approvals/pr-owner.json'; $leasePath = Join-Path $evidence 'approvals/pr-integration-lease.json'; if (-not (Test-Path -LiteralPath $approvalPath -PathType Leaf) -or -not (Test-Path -LiteralPath $leasePath -PathType Leaf)) { throw 'PR_APPROVAL_OR_LEASE_MISSING' }
try { $approval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $lease = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json } catch { throw 'PR_APPROVAL_OR_LEASE_MALFORMED' }
$approvalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); $scope = @($approval.action_scope | ForEach-Object { [string]$_ } | Sort-Object); if ($approval.schema -ne 'gwo-v8-c1-pr-owner-approval.v1' -or $approval.approved -ne $true -or $approval.repository -ne $state.repository -or $approval.base_ref -ne 'main' -or $approval.base_sha -ne $state.identities.base.sha -or $approval.head_ref -ne 'codex/gwo-v8-beta1' -or $approval.head_sha -ne $state.identities.beta1.sha -or @(Compare-Object @('create','merge:squash','ready') $scope).Count -ne 0 -or [string]::IsNullOrWhiteSpace([string]$approval.owner) -or [string]::IsNullOrWhiteSpace([string]$approval.integration_lease_id)) { throw 'PR_APPROVAL_INVALID' }
$leaseScope = @($lease.action_scope | ForEach-Object { [string]$_ } | Sort-Object); $validFrom = [DateTime]::Parse([string]$lease.valid_from).ToUniversalTime(); $validUntil = [DateTime]::Parse([string]$lease.valid_until).ToUniversalTime(); if ($lease.schema -ne 'gwo-v8-c1-integration-lease.v1' -or $lease.state -ne 'active' -or $lease.id -ne $approval.integration_lease_id -or $lease.owner -ne $approval.owner -or $lease.repository -ne $state.repository -or $lease.scope -ne 'repository-global-remote-writer' -or $lease.exclusive_remote_writer_control -ne $true -or $lease.base_sha -ne $state.identities.base.sha -or $lease.base_tree -ne $state.identities.base.tree -or $lease.head_sha -ne $state.identities.beta1.sha -or $lease.head_tree -ne $state.identities.beta1.tree -or @(Compare-Object $scope $leaseScope).Count -ne 0 -or $lease.approval_sha256 -ne $approvalHash -or $validFrom -ge $validUntil) { throw 'PR_INTEGRATION_LEASE_INVALID' }
if ($null -ne $state.approvals.pr -and ($state.approvals.pr.approval_sha256 -ne $approvalHash -or $state.approvals.pr.lease_sha256 -ne $leaseHash)) { throw 'PR_APPROVAL_RESUME_CONFLICT' }
$state.approvals.pr = [ordered]@{ approval_path = $approvalPath.Replace('\','/'); approval_sha256 = $approvalHash; lease_path = $leasePath.Replace('\','/'); lease_sha256 = $leaseHash; owner = [string]$approval.owner; lease_id = [string]$lease.id; action_scope = @($scope); writer_scope = [string]$lease.scope; exclusive_remote_writer_control = [bool]$lease.exclusive_remote_writer_control; base_sha = $approval.base_sha; base_tree = $state.identities.base.tree; head_sha = $approval.head_sha; head_tree = $state.identities.beta1.tree; valid_from = $validFrom.ToString('o'); valid_until = $validUntil.ToString('o') }; Save-State $state
$check = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($check.approvals.pr.approval_sha256 -ne $approvalHash -or $check.approvals.pr.lease_sha256 -ne $leaseHash -or $check.approvals.pr.lease_id -ne $lease.id) { throw 'PR_APPROVAL_STATE_READBACK_INVALID' }
~~~

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
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
if ($state.identities.base.ref -ne 'refs/heads/main' -or $state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or (@($state.identities.base.parents) -join ',') -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -or $state.identities.beta1.ref -ne 'refs/heads/codex/gwo-v8-beta1' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.integration.tree -ne '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577') { throw 'FROZEN_IDENTITY_INVALID' }
$repo = $state.repository; $approvalPath = Join-Path $evidence 'approvals/pr-owner.json'; $leasePath = Join-Path $evidence 'approvals/pr-integration-lease.json'; if (-not (Test-Path -LiteralPath $approvalPath -PathType Leaf) -or -not (Test-Path -LiteralPath $leasePath -PathType Leaf)) { throw 'PR_APPROVAL_OR_LEASE_MISSING' }
$approval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $lease = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json; $approvalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); if ($approval.schema -ne 'gwo-v8-c1-pr-owner-approval.v1' -or $approval.approved -ne $true -or $approval.base_sha -ne $state.identities.base.sha -or $approval.head_sha -ne $state.identities.beta1.sha -or $lease.id -ne $approval.integration_lease_id -or $lease.approval_sha256 -ne $approvalHash -or $state.approvals.pr.approval_sha256 -ne $approvalHash -or $state.approvals.pr.lease_sha256 -ne $leaseHash) { throw 'PR_APPROVAL_INVALID' }
$policyDir = Join-Path $evidence 'policy-before-pr'; if (Test-Path -LiteralPath $policyDir) { throw 'POLICY_READBACK_EXISTS' }; New-Item -ItemType Directory -Path $policyDir -ErrorAction Stop | Out-Null
$actions = @(gh api "repos/$repo/actions/permissions" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'ACTIONS_READBACK_FAILED' }
$workflows = @(gh api "repos/$repo/actions/workflows" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'WORKFLOW_READBACK_FAILED' }
$ruleset = @(gh api "repos/$repo/rulesets/20160628" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'RULESET_READBACK_FAILED' }
$repoRaw = @(gh api "repos/$repo" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REPOSITORY_READ_FAILED' }
[IO.File]::WriteAllText((Join-Path $policyDir 'actions.json'),($actions -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $policyDir 'workflows.json'),($workflows -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $policyDir 'ruleset.json'),($ruleset -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $policyDir 'repository.json'),($repoRaw -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false))
$a = ($actions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($workflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($ruleset -join [Environment]::NewLine) | ConvertFrom-Json; $repoObject = ($repoRaw -join [Environment]::NewLine) | ConvertFrom-Json
if ($repoObject.full_name -ne $repo -or $repoObject.default_branch -ne 'main' -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0) { throw 'POLICY_CHANGED' }
$includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; if (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main')) { throw 'RULESET_DEFAULT_BRANCH_NOT_APPLICABLE' }
$types = @($r.rules | ForEach-Object type); if (@(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0) { throw 'RULESET_CHANGED' }
$pull = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pull -and $null -ne $pull.parameters -and $null -ne $pull.parameters.allowed_merge_methods) { $allowed = @($pull.parameters.allowed_merge_methods) }; if ($allowed -notcontains 'squash') { throw 'SQUASH_NOT_ALLOWED' }
~~~

- [ ] **3.3 Create/reuse exactly one Draft PR and verify repository identity.**

Discover the exact PR before requiring a current Integration Lease window. A
frozen `created_by_plan=true` resume that finds the PR already present must
read the exact `pr-create` policy and authorization receipts or STOP; only a
proven initial PR reuse is authorization-free. The absent-effect branch alone
may reload the active lease, persist its authorization, and POST.

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
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
if ($state.identities.base.ref -ne 'refs/heads/main' -or $state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or (@($state.identities.base.parents) -join ',') -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -or $state.identities.beta1.ref -ne 'refs/heads/codex/gwo-v8-beta1' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.integration.tree -ne '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577') { throw 'FROZEN_IDENTITY_INVALID' }
$fullIdentityValid = $root -eq $state.coordinator_root -and (@($state.identities.base.parents) -join ',') -eq 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -and (@($state.identities.beta1.parents) -join ',') -eq '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -and (@($state.identities.integration.parents) -join ',') -eq 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875' -and $state.identities.protected_ga.ref -eq 'refs/heads/codex/gwo-v8-ga-plan' -and (@($state.identities.protected_ga.parents) -join ',') -eq '3b7097213ac482b3a9dcc31320e7bd84191bf2c0' -and $state.identities.boundaries.implementation -eq 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2' -and $state.identities.boundaries.beta1 -eq 'ddc1785f84b6a82a7b5c34d5928b046d4e9a781d'; if (-not $fullIdentityValid) { throw 'FULL_FROZEN_IDENTITY_INVALID' }
$repo = $state.repository; $repoRaw = @(gh api "repos/$repo" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REPOSITORY_READ_FAILED' }; $repoObject = ($repoRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($repoObject.full_name -ne $repo -or $repoObject.default_branch -ne 'main') { throw 'DEFAULT_BRANCH_INVALID' }
$approvalPath = Join-Path $evidence 'approvals/pr-owner.json'; $leasePath = Join-Path $evidence 'approvals/pr-integration-lease.json'; $approval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $lease = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json; $approvalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); if ($approval.approved -ne $true -or $approval.base_sha -ne $state.identities.base.sha -or $approval.head_sha -ne $state.identities.beta1.sha -or $lease.id -ne $approval.integration_lease_id -or $state.approvals.pr.approval_sha256 -ne $approvalHash -or $state.approvals.pr.lease_sha256 -ne $leaseHash) { throw 'PR_APPROVAL_RELOAD_INVALID' }
$leaseEvidence = $null
$expectedPrScope = @('create','merge:squash','ready'); $approvalScope = @($approval.action_scope | ForEach-Object { [string]$_ } | Sort-Object); $leaseScope = @($lease.action_scope | ForEach-Object { [string]$_ } | Sort-Object); $leaseFrom = [DateTime]::Parse([string]$lease.valid_from).ToUniversalTime(); $leaseUntil = [DateTime]::Parse([string]$lease.valid_until).ToUniversalTime(); if ($approval.schema -ne 'gwo-v8-c1-pr-owner-approval.v1' -or $approval.repository -ne $repo -or $approval.owner -ne $lease.owner -or @(Compare-Object $expectedPrScope $approvalScope).Count -ne 0 -or $lease.schema -ne 'gwo-v8-c1-integration-lease.v1' -or $lease.state -ne 'active' -or $lease.repository -ne $repo -or $lease.scope -ne 'repository-global-remote-writer' -or $lease.exclusive_remote_writer_control -ne $true -or $lease.base_sha -ne $state.identities.base.sha -or $lease.base_tree -ne $state.identities.base.tree -or $lease.head_sha -ne $state.identities.beta1.sha -or $lease.head_tree -ne $state.identities.beta1.tree -or @(Compare-Object $expectedPrScope $leaseScope).Count -ne 0 -or $lease.approval_sha256 -ne $approvalHash) { throw 'PR_APPROVAL_LEASE_SCOPE_INVALID' }
function Assert-PrWriterLease([string]$failure) { $approvalNow = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $leaseNow = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json; $approvalHashNow = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseHashNow = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); $from = [DateTime]::Parse([string]$leaseNow.valid_from).ToUniversalTime(); $until = [DateTime]::Parse([string]$leaseNow.valid_until).ToUniversalTime(); $now = [DateTime]::UtcNow; if ($approvalHashNow -ne $approvalHash -or $leaseHashNow -ne $leaseHash -or $approvalNow.schema -ne 'gwo-v8-c1-pr-owner-approval.v1' -or $approvalNow.approved -ne $true -or $approvalNow.repository -ne $repo -or $approvalNow.base_sha -ne $state.identities.base.sha -or $approvalNow.head_sha -ne $state.identities.beta1.sha -or $leaseNow.schema -ne 'gwo-v8-c1-integration-lease.v1' -or $leaseNow.id -ne $approvalNow.integration_lease_id -or $leaseNow.owner -ne $approvalNow.owner -or $leaseNow.repository -ne $repo -or $leaseNow.scope -ne 'repository-global-remote-writer' -or $leaseNow.exclusive_remote_writer_control -ne $true -or $leaseNow.base_sha -ne $state.identities.base.sha -or $leaseNow.base_tree -ne $state.identities.base.tree -or $leaseNow.head_sha -ne $state.identities.beta1.sha -or $leaseNow.head_tree -ne $state.identities.beta1.tree -or $leaseNow.approval_sha256 -ne $approvalHashNow -or $now -lt $from -or $now -ge $until) { throw $failure }; return [ordered]@{ owner = [string]$approvalNow.owner; lease_id = [string]$leaseNow.id; approval_sha256 = $approvalHashNow; lease_sha256 = $leaseHashNow; valid_from = $from.ToString('o'); valid_until = $until.ToString('o') } }
function New-PolicyReceipt([string]$label,[string]$directory,[object]$actionsObject,[object]$workflowsObject,[object]$rulesetObject,[object]$repositoryObject) {
    $files = [ordered]@{}
    foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) { $path = Join-Path $directory $name; $files[$name] = [ordered]@{ path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() } }
    $receipt = [ordered]@{ schema = 'gwo-v8-c1-mutation-policy.v1'; label = $label; repository = $repositoryObject.full_name; default_branch = $repositoryObject.default_branch; actions_enabled = $actionsObject.enabled; workflow_count = $workflowsObject.total_count; ruleset_id = $rulesetObject.id; files = $files }
    $path = Join-Path $directory 'receipt.json'; $json = $receipt | ConvertTo-Json -Depth 30; if (Test-Path -LiteralPath $path -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $path) -ne $json) { throw 'POLICY_RECEIPT_CONFLICT' } } else { [IO.File]::WriteAllText($path,$json,[Text.UTF8Encoding]::new($false)) }
    return [ordered]@{ label = $label; path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
}
function Assert-PrCreatePolicyAttempt([object]$receipt) {
    if ($null -eq $receipt -or $receipt.label -ne 'pr-create' -or [string]::IsNullOrWhiteSpace([string]$receipt.path) -or [string]::IsNullOrWhiteSpace([string]$receipt.sha256) -or -not (Test-Path -LiteralPath $receipt.path -PathType Leaf)) { throw 'PR_CREATE_POLICY_ATTEMPT_INVALID' }
    if ((Get-FileHash -LiteralPath $receipt.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $receipt.sha256) { throw 'PR_CREATE_POLICY_ATTEMPT_INVALID' }
    try { $value = Get-Content -Raw -LiteralPath $receipt.path | ConvertFrom-Json } catch { throw 'PR_CREATE_POLICY_ATTEMPT_INVALID' }
    if ($value.schema -ne 'gwo-v8-c1-mutation-policy.v1' -or $value.label -ne 'pr-create' -or $value.repository -ne $repo -or $value.default_branch -ne 'main' -or $value.actions_enabled -ne $false -or $value.workflow_count -ne 0 -or $value.ruleset_id -ne 20160628) { throw 'PR_CREATE_POLICY_ATTEMPT_INVALID' }
    $directory = Split-Path -Path $receipt.path -Parent; $values = [ordered]@{}
    foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) { $property = $value.files.PSObject.Properties[$name]; if ($null -eq $property) { throw 'PR_CREATE_POLICY_ATTEMPT_INVALID' }; $item = $property.Value; $expectedPath = (Join-Path $directory $name).Replace('\','/'); if ([string]$item.path -cne $expectedPath -or -not (Test-Path -LiteralPath $item.path -PathType Leaf) -or (Get-FileHash -LiteralPath $item.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.sha256) { throw 'PR_CREATE_POLICY_ATTEMPT_INVALID' }; try { $values[$name] = Get-Content -Raw -LiteralPath $item.path | ConvertFrom-Json } catch { throw 'PR_CREATE_POLICY_ATTEMPT_INVALID' } }
    $a = $values['actions.json']; $w = $values['workflows.json']; $r = $values['ruleset.json']; $repository = $values['repository.json']; $types = @($r.rules | ForEach-Object type); $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }; if ($repository.full_name -ne $repo -or $repository.default_branch -ne 'main' -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0 -or @(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0 -or $allowed -notcontains 'squash' -or (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main')) ) { throw 'PR_CREATE_POLICY_ATTEMPT_INVALID' }
    return [ordered]@{ label = 'pr-create'; path = ([string]$receipt.path).Replace('\','/'); sha256 = [string]$receipt.sha256 }
}
function Assert-PrCreateCurrentPolicy([string]$failure) {
    $liveActions = @(gh api repos/$repo/actions/permissions 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_CREATE_CURRENT_POLICY_ACTIONS_READ_FAILED' }; $liveWorkflows = @(gh api repos/$repo/actions/workflows 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_CREATE_CURRENT_POLICY_WORKFLOWS_READ_FAILED' }; $liveRuleset = @(gh api repos/$repo/rulesets/20160628 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_CREATE_CURRENT_POLICY_RULESET_READ_FAILED' }; $liveRepository = @(gh api repos/$repo 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_CREATE_CURRENT_POLICY_REPOSITORY_READ_FAILED' }
    $a = ($liveActions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($liveWorkflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($liveRuleset -join [Environment]::NewLine) | ConvertFrom-Json; $repository = ($liveRepository -join [Environment]::NewLine) | ConvertFrom-Json; $types = @($r.rules | ForEach-Object type); $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }; if ($repository.full_name -ne $repo -or $repository.default_branch -ne 'main' -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0 -or @(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0 -or $allowed -notcontains 'squash' -or (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main')) ) { throw $failure }
    return $true
}
function Read-PrCreateAuthorization([bool]$required,[string]$candidatePath=$null) {
    $reference = $state.mutation_authorization.pr_create
    if ($null -ne $candidatePath) { $candidate = ([IO.Path]::GetFullPath($candidatePath)).Replace('\','/'); if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw 'PR_CREATE_AUTHORIZATION_ORPHAN_INVALID' }; $reference = [ordered]@{ effect_key = 'pr-create'; authorization_path = $candidate; authorization_sha256 = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant() } }
    if ($null -eq $reference) { if ($required) { throw 'PR_CREATE_EXISTING_EFFECT_WITHOUT_AUTHORIZATION' }; return $null }
    if ($reference.effect_key -ne 'pr-create' -or -not (Test-Path -LiteralPath $reference.authorization_path -PathType Leaf)) { throw 'PR_CREATE_AUTHORIZATION_REFERENCE_INVALID' }
    $authorizationHash = (Get-FileHash -LiteralPath $reference.authorization_path -Algorithm SHA256).Hash.ToLowerInvariant(); if ($authorizationHash -ne $reference.authorization_sha256) { throw 'PR_CREATE_AUTHORIZATION_HASH_INVALID' }
    try { $value = Get-Content -Raw -LiteralPath $reference.authorization_path | ConvertFrom-Json; $authorizedAt = [DateTime]::Parse([string]$value.authorized_at).ToUniversalTime(); $authFrom = [DateTime]::Parse([string]$value.valid_from).ToUniversalTime(); $authUntil = [DateTime]::Parse([string]$value.valid_until).ToUniversalTime() } catch { throw 'PR_CREATE_AUTHORIZATION_MALFORMED' }
    $expectedName = "pr-create-$($value.policy_sha256)-$($value.lease_sha256).json"
    if ($value.schema -ne 'gwo-v8-c1-mutation-authorization.v1' -or $value.operation -ne 'pr-create' -or $value.effect_key -ne 'pr-create' -or $value.repository -ne $repo -or $value.base_ref -ne 'main' -or $value.head_ref -ne 'codex/gwo-v8-beta1' -or $value.base_sha -ne $state.identities.base.sha -or $value.head_sha -ne $state.identities.beta1.sha -or $value.owner -ne $approval.owner -or $value.lease_id -ne $lease.id -or $value.approval_sha256 -ne $approvalHash -or $value.lease_sha256 -ne $leaseHash -or $value.policy_label -ne 'pr-create' -or [IO.Path]::GetFileName($reference.authorization_path) -cne $expectedName -or $authFrom -ne $leaseFrom -or $authUntil -ne $leaseUntil -or $authorizedAt -lt $authFrom -or $authorizedAt -ge $authUntil) { throw 'PR_CREATE_AUTHORIZATION_INVALID' }
    if (-not (Test-Path -LiteralPath $value.policy_path -PathType Leaf) -or (Get-FileHash -LiteralPath $value.policy_path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $value.policy_sha256) { throw 'PR_CREATE_AUTHORIZATION_POLICY_INVALID' }
    return [ordered]@{ reference = $reference; value = $value; policy = [ordered]@{ label = 'pr-create'; path = [string]$value.policy_path; sha256 = [string]$value.policy_sha256 } }
}
function Save-PrCreateAuthorization([object]$policy,[object]$leaseEvidence) {
    $directory = Join-Path $evidence 'authorizations'; $orphanPaths = @(); if (Test-Path -LiteralPath $directory -PathType Container) { $orphanPaths = @(Get-ChildItem -LiteralPath $directory -Filter 'pr-create-*.json' -File | Sort-Object FullName | Where-Object { $null -eq $state.mutation_authorization.pr_create -or $_.FullName.Replace('\','/') -ne $state.mutation_authorization.pr_create.authorization_path }) }; if ($orphanPaths.Count -gt 1 -or ($null -ne $state.mutation_authorization.pr_create -and $orphanPaths.Count -gt 0)) { throw 'PR_CREATE_AUTHORIZATION_ORPHAN_CONFLICT' }
    if ($null -ne $state.mutation_authorization.pr_create) { $existing = Read-PrCreateAuthorization $true; if ($existing.policy.path -ne $policy.path -or $existing.policy.sha256 -ne $policy.sha256 -or $existing.value.lease_sha256 -ne $leaseEvidence.lease_sha256) { throw 'PR_CREATE_AUTHORIZATION_IMMUTABLE_CONFLICT' }; return $existing }
    if ($orphanPaths.Count -eq 1) { $orphan = Read-PrCreateAuthorization $true $orphanPaths[0].FullName; if ($orphan.policy.path -ne $policy.path -or $orphan.policy.sha256 -ne $policy.sha256 -or $orphan.value.lease_sha256 -ne $leaseEvidence.lease_sha256) { throw 'PR_CREATE_AUTHORIZATION_ORPHAN_POLICY_CONFLICT' }; $state.mutation_authorization.pr_create = $orphan.reference; Save-State $state; $saved = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($saved.mutation_authorization.pr_create.authorization_sha256 -ne $orphan.reference.authorization_sha256) { throw 'PR_CREATE_AUTHORIZATION_ORPHAN_STATE_INVALID' }; return Read-PrCreateAuthorization $true }
    $authorizedAt = [DateTime]::UtcNow; $authFrom = [DateTime]::Parse([string]$leaseEvidence.valid_from).ToUniversalTime(); $authUntil = [DateTime]::Parse([string]$leaseEvidence.valid_until).ToUniversalTime(); if ($authorizedAt -lt $authFrom -or $authorizedAt -ge $authUntil) { throw 'PR_CREATE_AUTHORIZATION_OUTSIDE_LEASE' }
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { New-Item -ItemType Directory -Path $directory -ErrorAction Stop | Out-Null }; $path = Join-Path $directory ("pr-create-$($policy.sha256)-$($leaseEvidence.lease_sha256).json")
    $value = [ordered]@{ schema = 'gwo-v8-c1-mutation-authorization.v1'; operation = 'pr-create'; effect_key = 'pr-create'; repository = $repo; base_ref = 'main'; head_ref = 'codex/gwo-v8-beta1'; base_sha = $state.identities.base.sha; head_sha = $state.identities.beta1.sha; owner = [string]$leaseEvidence.owner; lease_id = [string]$leaseEvidence.lease_id; approval_sha256 = [string]$leaseEvidence.approval_sha256; lease_sha256 = [string]$leaseEvidence.lease_sha256; policy_label = 'pr-create'; policy_path = [string]$policy.path; policy_sha256 = [string]$policy.sha256; authorized_at = $authorizedAt.ToString('o'); valid_from = $authFrom.ToString('o'); valid_until = $authUntil.ToString('o') }
    $json = $value | ConvertTo-Json -Depth 30; if (Test-Path -LiteralPath $path -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $path) -ne $json) { throw 'PR_CREATE_AUTHORIZATION_CONFLICT' } } else { [IO.File]::WriteAllText($path,$json,[Text.UTF8Encoding]::new($false)) }
    $reference = [ordered]@{ effect_key = 'pr-create'; authorization_path = $path.Replace('\','/'); authorization_sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }; $state.mutation_authorization.pr_create = $reference; Save-State $state
    $saved = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($saved.mutation_authorization.pr_create.authorization_sha256 -ne $reference.authorization_sha256) { throw 'PR_CREATE_AUTHORIZATION_STATE_READBACK_INVALID' }; return Read-PrCreateAuthorization $true
}
function Save-PrCreateAuthorizationWithFreshLease([object]$policy) {
    $leaseEvidence = Assert-PrWriterLease 'PR_CREATE_LEASE_AUTHORIZATION_INVALID'; $createAuthorization = Save-PrCreateAuthorization $policy $leaseEvidence
    return [ordered]@{ lease = $leaseEvidence; authorization = $createAuthorization }
}
$rows = @(gh api "repos/$repo/pulls?state=all&head=NOirBRight:codex/gwo-v8-beta1&base=main&per_page=100" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_LIST_FAILED' }
$prs = ($rows -join [Environment]::NewLine) | ConvertFrom-Json; $matching = @($prs | Where-Object { $_.head.repo.full_name -eq $repo -and $_.base.repo.full_name -eq $repo -and $_.head.ref -eq 'codex/gwo-v8-beta1' -and $_.base.ref -eq 'main' }); if ($matching.Count -gt 1) { throw 'MULTIPLE_EXACT_PRS' }
$didCreate = $false; $createAuthorization = $null; $policyReceipt = $null; if ($matching.Count -eq 0) {
    $orphanDirectory = Join-Path $evidence 'authorizations'; $orphanPaths = @(); if (Test-Path -LiteralPath $orphanDirectory -PathType Container) { $orphanPaths = @(Get-ChildItem -LiteralPath $orphanDirectory -Filter 'pr-create-*.json' -File | Sort-Object FullName | Where-Object { $null -eq $state.mutation_authorization.pr_create -or $_.FullName.Replace('\','/') -ne $state.mutation_authorization.pr_create.authorization_path }) }; if ($orphanPaths.Count -gt 1 -or ($null -ne $state.mutation_authorization.pr_create -and $orphanPaths.Count -gt 0)) { throw 'PR_CREATE_AUTHORIZATION_ORPHAN_CONFLICT' }
    $createAuthorization = Read-PrCreateAuthorization $false
    if ($null -ne $createAuthorization) {
        if ($null -eq $state.mutation_policy.pr_create) { $policyReceipt = Assert-PrCreatePolicyAttempt $createAuthorization.policy; $state.mutation_policy.pr_create = $policyReceipt; Save-State $state } else { $policyReceipt = Assert-PrCreatePolicyAttempt $state.mutation_policy.pr_create; if ($policyReceipt.path -ne $createAuthorization.policy.path -or $policyReceipt.sha256 -ne $createAuthorization.policy.sha256) { throw 'PR_CREATE_POLICY_AUTHORIZATION_MISMATCH' } }
        $null = Assert-PrCreateCurrentPolicy 'PR_CREATE_CURRENT_POLICY_INVALID'; $leaseEvidence = Assert-PrWriterLease 'PR_CREATE_RESUME_LEASE_INVALID'; if ($createAuthorization.value.lease_sha256 -ne $leaseEvidence.lease_sha256) { throw 'PR_CREATE_RESUME_LEASE_CHANGED' }; Write-Output 'REUSE_RECOVERED_PR_CREATE_ATTEMPT'
    } elseif ($orphanPaths.Count -eq 1) {
        $createAuthorization = Read-PrCreateAuthorization $true $orphanPaths[0].FullName; $canonicalPolicy = $state.mutation_policy.pr_create
        if ($null -ne $canonicalPolicy) { $canonicalPolicy = Assert-PrCreatePolicyAttempt $canonicalPolicy; if ($createAuthorization.policy.path -ne $canonicalPolicy.path -or $createAuthorization.policy.sha256 -ne $canonicalPolicy.sha256) { throw 'PR_CREATE_AUTHORIZATION_ORPHAN_POLICY_CONFLICT' }; $policyReceipt = $canonicalPolicy } else { $policyReceipt = Assert-PrCreatePolicyAttempt $createAuthorization.policy }
        if ($createAuthorization.value.lease_sha256 -ne $leaseHash) { throw 'PR_CREATE_AUTHORIZATION_ORPHAN_LEASE_CONFLICT' }; $null = Assert-PrCreateCurrentPolicy 'PR_CREATE_CURRENT_POLICY_INVALID'
        if ($null -ne $state.mutation_authorization.pr_create) { throw 'PR_CREATE_AUTHORIZATION_ORPHAN_CANONICAL_CONFLICT' }; $state.mutation_authorization.pr_create = $createAuthorization.reference; if ($null -eq $state.mutation_policy.pr_create) { $state.mutation_policy.pr_create = $policyReceipt } elseif ($state.mutation_policy.pr_create.path -ne $policyReceipt.path -or $state.mutation_policy.pr_create.sha256 -ne $policyReceipt.sha256) { throw 'PR_CREATE_AUTHORIZATION_ORPHAN_POLICY_CONFLICT' }; $state.pr.created_by_plan = $true; Save-State $state; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; $createAuthorization = Read-PrCreateAuthorization $true; Write-Output 'ADOPTED_PR_CREATE_ORPHAN_ATTEMPT'
    } elseif ($null -ne $state.mutation_policy.pr_create) {
        $policyReceipt = Assert-PrCreatePolicyAttempt $state.mutation_policy.pr_create; $null = Assert-PrCreateCurrentPolicy 'PR_CREATE_CURRENT_POLICY_INVALID'; $leaseEvidence = Assert-PrWriterLease 'PR_CREATE_RESUME_LEASE_INVALID'; Write-Output 'REUSE_RECOVERED_PR_CREATE_POLICY'
    } else {
        $policyDir = Join-Path $evidence ('policy-before-pr-create-' + [guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $policyDir -ErrorAction Stop | Out-Null; $actions = @(gh api repos/$repo/actions/permissions 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'ACTIONS_READBACK_FAILED' }; $workflows = @(gh api repos/$repo/actions/workflows 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'WORKFLOW_READBACK_FAILED' }; $ruleset = @(gh api repos/$repo/rulesets/20160628 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'RULESET_READBACK_FAILED' }; [IO.File]::WriteAllText((Join-Path $policyDir 'actions.json'),($actions -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $policyDir 'workflows.json'),($workflows -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $policyDir 'ruleset.json'),($ruleset -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); $a = ($actions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($workflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($ruleset -join [Environment]::NewLine) | ConvertFrom-Json; if ($a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0) { throw 'POLICY_BEFORE_PR_CREATE_INVALID' }; $types = @($r.rules | ForEach-Object type); if (@(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0) { throw 'RULESET_BEFORE_PR_CREATE_INVALID' }; $pull = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pull -and $null -ne $pull.parameters -and $null -ne $pull.parameters.allowed_merge_methods) { $allowed = @($pull.parameters.allowed_merge_methods) }; if ($allowed -notcontains 'squash') { throw 'SQUASH_NOT_ALLOWED' }; $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; if (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main')) { throw 'RULESET_DEFAULT_BRANCH_NOT_APPLICABLE' }
        $repoPolicyRaw = @(gh api repos/$repo 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REPOSITORY_POLICY_READ_FAILED' }; $repoPolicy = ($repoPolicyRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($repoPolicy.full_name -ne $repo -or $repoPolicy.default_branch -ne 'main') { throw 'DEFAULT_BRANCH_POLICY_INVALID' }; [IO.File]::WriteAllText((Join-Path $policyDir 'repository.json'),($repoPolicyRaw -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); $policyReceipt = New-PolicyReceipt 'pr-create' $policyDir $a $w $r $repoPolicy; $state.mutation_policy.pr_create = $policyReceipt; $state.pr.created_by_plan = $true; Save-State $state; $policyState = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($policyState.mutation_policy.pr_create.sha256 -ne $policyReceipt.sha256 -or $policyState.pr.created_by_plan -ne $true) { throw 'PR_CREATE_INTENT_STATE_READBACK_INVALID' }
    }
    $authorizationResult = Save-PrCreateAuthorizationWithFreshLease $policyReceipt; $leaseEvidence = $authorizationResult.lease; $createAuthorization = $authorizationResult.authorization
    $finalCreateLease = Assert-PrWriterLease 'PR_CREATE_FINAL_IMMEDIATE_LEASE_INVALID'; $finalCreateApprovalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $finalCreateLeaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); $finalCreateNow = [DateTime]::UtcNow; $finalCreateFrom = [DateTime]::Parse([string]$finalCreateLease.valid_from).ToUniversalTime(); $finalCreateUntil = [DateTime]::Parse([string]$finalCreateLease.valid_until).ToUniversalTime(); if ($finalCreateApprovalHash -ne $approvalHash -or $finalCreateLeaseHash -ne $leaseHash -or $finalCreateNow -lt $finalCreateFrom -or $finalCreateNow -ge $finalCreateUntil) { throw 'PR_CREATE_FINAL_IMMEDIATE_LEASE_INVALID' }
    $created = @(gh api -X POST "repos/$repo/pulls" -f title='GWO V8 Beta1 Core Preview' -f head='codex/gwo-v8-beta1' -f base='main' -F draft=true 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_CREATE_FAILED' }; $didCreate = $true
}
if ($matching.Count -gt 0) {
    $prNumber = [int]$matching[0].number
    if ($state.pr.created_by_plan -eq $true) {
        if ($null -eq $state.mutation_policy.pr_create) { throw 'PR_CREATE_EXISTING_EFFECT_POLICY_MISSING' }; $createAuthorization = Read-PrCreateAuthorization $true; if ($createAuthorization.policy.path -ne $state.mutation_policy.pr_create.path -or $createAuthorization.policy.sha256 -ne $state.mutation_policy.pr_create.sha256) { throw 'PR_CREATE_EXISTING_EFFECT_POLICY_AUTHORIZATION_MISMATCH' }
    } elseif ($state.pr.created_by_plan -ne $false -and $null -ne $state.pr.created_by_plan) { throw 'PR_CREATE_PROVENANCE_INVALID' }
}
$postRows = @(gh api "repos/$repo/pulls?state=all&head=NOirBRight:codex/gwo-v8-beta1&base=main&per_page=100" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_POST_CREATE_LIST_FAILED' }; $postMatching = @((($postRows -join [Environment]::NewLine) | ConvertFrom-Json) | Where-Object { $_.head.repo.full_name -eq $repo -and $_.base.repo.full_name -eq $repo -and $_.head.ref -eq 'codex/gwo-v8-beta1' -and $_.base.ref -eq 'main' }); if ($postMatching.Count -ne 1) { throw 'PR_POST_CREATE_UNIQUENESS_FAILED' }; $pr = $postMatching[0]
if ($pr.head.repo.full_name -ne $repo -or $pr.base.repo.full_name -ne $repo -or $pr.head.ref -ne 'codex/gwo-v8-beta1' -or $pr.base.ref -ne 'main' -or $pr.head.sha -ne $state.identities.beta1.sha -or $pr.draft -ne $true) { throw 'PR_IDENTITY_INVALID' }
$prNumber = [int]$pr.number; $files = @(gh api "repos/$repo/pulls/$prNumber/files?per_page=100" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_FILES_FAILED' }; $fileObjects = ($files -join [Environment]::NewLine) | ConvertFrom-Json
$githubStatusToNameStatus = @{ added = 'A'; modified = 'M' }; $actualStatus = [ordered]@{}; foreach ($file in @($fileObjects)) { $apiStatus = [string]$file.status; if (-not $githubStatusToNameStatus.ContainsKey($apiStatus)) { throw "PR_FILE_STATUS_UNEXPECTED:$apiStatus" }; $actualStatus[$file.filename.Replace('\','/')] = $githubStatusToNameStatus[$apiStatus] }
$expectedStatus = $state.scope.main_to_beta1_status; $expectedPaths = @($expectedStatus.PSObject.Properties.Name | Sort-Object); $actualPaths = @($actualStatus.Keys | Sort-Object); if ($actualStatus.Count -ne 17 -or @(Compare-Object $expectedPaths $actualPaths).Count -ne 0) { throw 'PR_EXACT_17_PATHS_FAILED' }; foreach ($path in $expectedPaths) { if ($actualStatus[$path] -ne $expectedStatus.PSObject.Properties[$path].Value) { throw "PR_PATH_STATUS_FAILED:$path" } }
$prReceiptPath = Join-Path $evidence 'pr-draft.json'; $prReceiptText = $pr | ConvertTo-Json -Depth 40; if (Test-Path -LiteralPath $prReceiptPath -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $prReceiptPath) -ne $prReceiptText) { throw 'PR_RECEIPT_CONFLICT' } } else { [IO.File]::WriteAllText($prReceiptPath,$prReceiptText,[Text.UTF8Encoding]::new($false)) }
$createdByPlan = $didCreate -or ($state.pr.created_by_plan -eq $true); if ($createdByPlan -and $null -eq $state.mutation_policy.pr_create) { throw 'PR_CREATE_PROVENANCE_POLICY_MISSING' }
$state.pr = [ordered]@{ number = $prNumber; created_by_plan = [bool]$createdByPlan; head_repository = $pr.head.repo.full_name; base_repository = $pr.base.repo.full_name; head_ref = $pr.head.ref; base_ref = $pr.base.ref; head_sha = $pr.head.sha; paths = @($actualStatus.Keys | Sort-Object); path_status = $actualStatus; draft = $pr.draft; integration_lease_id = $lease.id; approval_sha256 = $approvalHash; lease_sha256 = $leaseHash; ready_at = $null; ready_approval_sha256 = $null; ready_lease_sha256 = $null; create_authorization_sha256 = if ($null -eq $createAuthorization) { $null } else { $createAuthorization.reference.authorization_sha256 }; ready_authorization_sha256 = $null; merge = $null }; if ($didCreate) { $state.mutation_policy.pr_create = $policyReceipt }; if ($didCreate -and $null -eq $state.mutation_authorization.pr_create) { throw 'PR_CREATE_AUTHORIZATION_STATE_MISSING' }
Save-State $state
~~~

Expected: one Draft PR from codex/gwo-v8-beta1 to main exists or is created,
both repository identities are bound, head/base SHAs are exact, and all 17
paths match. There is no redundant Beta1 branch push.

- [ ] **3.4 Resolve review threads, mark ready, and hold the lease.**

Read the PR again with REST and GraphQL for the exact owner/repository/number.
The GraphQL readback uses supported `headRepository{nameWithOwner}` and
`baseRepository{nameWithOwner}` fields; REST independently binds both full
repository identities, SHAs, and branch names.
Require every reviewThreads node to have isResolved true. reviewDecision may
be empty because the ruleset has zero required approvals; it is not approval
evidence. Save new policy readbacks immediately before the ready mutation and
repeat all policy assertions from 3.2. The PR readback establishes whether the
PR is still draft before any current-time lease requirement. PR-ready is always
bound to its own immutable policy and authorization receipt, including an
already-ready required effect. Then run:

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
if ($state.identities.base.ref -ne 'refs/heads/main' -or $state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or (@($state.identities.base.parents) -join ',') -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -or $state.identities.beta1.ref -ne 'refs/heads/codex/gwo-v8-beta1' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.integration.tree -ne '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577') { throw 'FROZEN_IDENTITY_INVALID' }
$fullIdentityValid = $root -eq $state.coordinator_root -and (@($state.identities.base.parents) -join ',') -eq 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -and (@($state.identities.beta1.parents) -join ',') -eq '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -and (@($state.identities.integration.parents) -join ',') -eq 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875' -and $state.identities.protected_ga.ref -eq 'refs/heads/codex/gwo-v8-ga-plan' -and (@($state.identities.protected_ga.parents) -join ',') -eq '3b7097213ac482b3a9dcc31320e7bd84191bf2c0' -and $state.identities.boundaries.implementation -eq 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2' -and $state.identities.boundaries.beta1 -eq 'ddc1785f84b6a82a7b5c34d5928b046d4e9a781d'; if (-not $fullIdentityValid) { throw 'FULL_FROZEN_IDENTITY_INVALID' }
if ($state.pr.number -le 0 -or $state.pr.head_sha -ne $state.identities.beta1.sha -or $state.pr.head_repository -ne $state.repository -or $state.pr.base_repository -ne $state.repository -or $state.pr.base_ref -ne 'main') { throw 'PR_STATE_INVALID' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
$repo = $state.repository; $prNumber = [int]$state.pr.number
$approvalPath = Join-Path $evidence 'approvals/pr-owner.json'; $leasePath = Join-Path $evidence 'approvals/pr-integration-lease.json'
if (-not (Test-Path -LiteralPath $approvalPath -PathType Leaf) -or -not (Test-Path -LiteralPath $leasePath -PathType Leaf)) { throw 'PR_APPROVAL_OR_LEASE_MISSING' }
try { $approval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $lease = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json } catch { throw 'PR_APPROVAL_OR_LEASE_MALFORMED' }
$approvalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant()
$expectedPrScope = @('create','merge:squash','ready'); $approvalScope = @($approval.action_scope | ForEach-Object { [string]$_ } | Sort-Object); $leaseScope = @($lease.action_scope | ForEach-Object { [string]$_ } | Sort-Object)
if ($approval.schema -ne 'gwo-v8-c1-pr-owner-approval.v1' -or $approval.approved -ne $true -or $approval.repository -ne $repo -or $approval.base_ref -ne 'main' -or $approval.base_sha -ne $state.identities.base.sha -or $approval.head_ref -ne 'codex/gwo-v8-beta1' -or $approval.head_sha -ne $state.identities.beta1.sha -or [string]::IsNullOrWhiteSpace([string]$approval.owner) -or [string]::IsNullOrWhiteSpace([string]$approval.integration_lease_id) -or @(Compare-Object $expectedPrScope $approvalScope).Count -ne 0) { throw 'PR_APPROVAL_RELOAD_INVALID' }
$leaseFrom = [DateTime]::Parse([string]$lease.valid_from).ToUniversalTime(); $leaseUntil = [DateTime]::Parse([string]$lease.valid_until).ToUniversalTime()
if ($lease.schema -ne 'gwo-v8-c1-integration-lease.v1' -or $lease.state -ne 'active' -or $lease.id -ne $approval.integration_lease_id -or $lease.owner -ne $approval.owner -or $lease.repository -ne $repo -or $lease.scope -ne 'repository-global-remote-writer' -or $lease.exclusive_remote_writer_control -ne $true -or $lease.base_sha -ne $state.identities.base.sha -or $lease.base_tree -ne $state.identities.base.tree -or $lease.head_sha -ne $state.identities.beta1.sha -or $lease.head_tree -ne $state.identities.beta1.tree -or @(Compare-Object $expectedPrScope $leaseScope).Count -ne 0 -or $lease.approval_sha256 -ne $approvalHash) { throw 'PR_LEASE_RELOAD_INVALID' }
$savedApproval = $state.approvals.pr; $savedScope = @($savedApproval.action_scope | ForEach-Object { [string]$_ } | Sort-Object)
if ($savedApproval.approval_sha256 -ne $approvalHash -or $savedApproval.lease_sha256 -ne $leaseHash -or $savedApproval.owner -ne $approval.owner -or $savedApproval.lease_id -ne $lease.id -or $savedApproval.writer_scope -ne 'repository-global-remote-writer' -or $savedApproval.exclusive_remote_writer_control -ne $true -or $savedApproval.base_sha -ne $approval.base_sha -or $savedApproval.base_tree -ne $lease.base_tree -or $savedApproval.head_sha -ne $approval.head_sha -or $savedApproval.head_tree -ne $lease.head_tree -or @(Compare-Object $expectedPrScope $savedScope).Count -ne 0) { throw 'PR_APPROVAL_STATE_BINDING_INVALID' }
function New-PolicyReceipt([string]$label,[string]$directory,[object]$actionsObject,[object]$workflowsObject,[object]$rulesetObject,[object]$repositoryObject) { $files = [ordered]@{}; foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) { $path = Join-Path $directory $name; $files[$name] = [ordered]@{ path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() } }; $receipt = [ordered]@{ schema = 'gwo-v8-c1-mutation-policy.v1'; label = $label; repository = $repositoryObject.full_name; default_branch = $repositoryObject.default_branch; actions_enabled = $actionsObject.enabled; workflow_count = $workflowsObject.total_count; ruleset_id = $rulesetObject.id; files = $files }; $path = Join-Path $directory 'receipt.json'; [IO.File]::WriteAllText($path,($receipt | ConvertTo-Json -Depth 30),[Text.UTF8Encoding]::new($false)); $policyReceipt = [ordered]@{ label = $label; path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }; return $policyReceipt }
$pr = @(gh api "repos/$repo/pulls/$prNumber" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_READ_FAILED' }; $pr = ($pr -join [Environment]::NewLine) | ConvertFrom-Json; if ($pr.head.repo.full_name -ne $repo -or $pr.base.repo.full_name -ne $repo -or $pr.head.ref -ne 'codex/gwo-v8-beta1' -or $pr.base.ref -ne 'main' -or $pr.head.sha -ne $state.identities.beta1.sha -or $pr.head.repo.owner.login -ne 'NOirBRight' -or $pr.head.repo.name -ne 'github-work-orchestrator' -or $pr.base.repo.owner.login -ne 'NOirBRight' -or $pr.base.repo.name -ne 'github-work-orchestrator') { throw 'PR_MOVED' }
$query = 'query($owner:String!,$name:String!,$number:Int!,$after:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewDecision headRepositoryOwner{login} headRepository{nameWithOwner} baseRepository{nameWithOwner} reviewThreads(first:100,after:$after){nodes{isResolved} pageInfo{hasNextPage endCursor}}}}}'
$allThreads = @(); $after = $null; do { $graphqlArgs = @('-f',"query=$query",'-F','owner=NOirBRight','-F','name=github-work-orchestrator','-F',"number=$prNumber"); if ($null -ne $after) { $graphqlArgs += @('-f',"after=$after") }; $reviewRaw = @(gh api graphql @graphqlArgs 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'THREAD_READ_FAILED' }; $review = ($reviewRaw -join [Environment]::NewLine) | ConvertFrom-Json; $pull = $review.data.repository.pullRequest; if ($pull.headRepositoryOwner.login -ne 'NOirBRight' -or $pull.headRepository.nameWithOwner -ne $repo -or $pull.baseRepository.nameWithOwner -ne $repo) { throw 'GRAPHQL_PR_IDENTITY_INVALID' }; $allThreads += @($pull.reviewThreads.nodes); $hasNext = [bool]$pull.reviewThreads.pageInfo.hasNextPage; $after = $pull.reviewThreads.pageInfo.endCursor; if ($hasNext -and [string]::IsNullOrWhiteSpace([string]$after)) { throw 'THREAD_PAGE_CURSOR_MISSING' } } while ($hasNext)
if (@($allThreads | Where-Object isResolved -ne $true).Count -ne 0) { throw 'UNRESOLVED_REVIEW_THREAD' }
function Assert-PrReadyLease([string]$failure) {
    $approvalHashNow = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseHashNow = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant()
    try { $approvalNow = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $leaseNow = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json; $from = [DateTime]::Parse([string]$leaseNow.valid_from).ToUniversalTime(); $until = [DateTime]::Parse([string]$leaseNow.valid_until).ToUniversalTime() } catch { throw $failure }
    if ($approvalHashNow -ne $approvalHash -or $leaseHashNow -ne $leaseHash -or $approvalNow.schema -ne 'gwo-v8-c1-pr-owner-approval.v1' -or $approvalNow.approved -ne $true -or $approvalNow.repository -ne $repo -or $approvalNow.base_sha -ne $state.identities.base.sha -or $approvalNow.head_sha -ne $state.identities.beta1.sha -or $leaseNow.schema -ne 'gwo-v8-c1-integration-lease.v1' -or $leaseNow.state -ne 'active' -or $leaseNow.id -ne $approvalNow.integration_lease_id -or $leaseNow.owner -ne $approvalNow.owner -or $leaseNow.repository -ne $repo -or $leaseNow.scope -ne 'repository-global-remote-writer' -or $leaseNow.exclusive_remote_writer_control -ne $true -or $leaseNow.base_sha -ne $state.identities.base.sha -or $leaseNow.base_tree -ne $state.identities.base.tree -or $leaseNow.head_sha -ne $state.identities.beta1.sha -or $leaseNow.head_tree -ne $state.identities.beta1.tree -or $leaseNow.approval_sha256 -ne $approvalHashNow -or [DateTime]::UtcNow -lt $from -or [DateTime]::UtcNow -ge $until) { throw $failure }
    return [ordered]@{ owner = [string]$approvalNow.owner; lease_id = [string]$leaseNow.id; approval_sha256 = $approvalHashNow; lease_sha256 = $leaseHashNow; valid_from = $from.ToString('o'); valid_until = $until.ToString('o') }
}
function Set-PrReadyPolicyIntent([object]$policy) {
    $property = $state.mutation_policy.PSObject.Properties['pr_ready']; if ($null -eq $property) { throw 'PR_READY_POLICY_SLOT_MISSING' }; $state.mutation_policy.pr_ready = $policy
}
$readyProbeRaw = @(gh api "repos/$repo/pulls/$prNumber" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_READY_EFFECT_READ_FAILED' }; $readyProbe = ($readyProbeRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($readyProbe.head.repo.full_name -ne $repo -or $readyProbe.base.repo.full_name -ne $repo -or $readyProbe.head.ref -ne 'codex/gwo-v8-beta1' -or $readyProbe.base.ref -ne 'main' -or $readyProbe.head.sha -ne $state.identities.beta1.sha) { throw 'PR_READY_EFFECT_IDENTITY_INVALID' }; $didReady = $readyProbe.draft -eq $true
function New-PolicyReceipt([string]$label,[string]$directory,[object]$actionsObject,[object]$workflowsObject,[object]$rulesetObject,[object]$repositoryObject) {
    $files = [ordered]@{}; foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) { $path = Join-Path $directory $name; $files[$name] = [ordered]@{ path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() } }
    $value = [ordered]@{ schema = 'gwo-v8-c1-mutation-policy.v1'; label = $label; repository = $repositoryObject.full_name; default_branch = $repositoryObject.default_branch; actions_enabled = $actionsObject.enabled; workflow_count = $workflowsObject.total_count; ruleset_id = $rulesetObject.id; files = $files }; $path = Join-Path $directory 'receipt.json'; $text = $value | ConvertTo-Json -Depth 30; if (Test-Path -LiteralPath $path -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $path) -ne $text) { throw 'POLICY_RECEIPT_CONFLICT' } } else { [IO.File]::WriteAllText($path,$text,[Text.UTF8Encoding]::new($false)) }; return [ordered]@{ label = $label; path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
}
function Assert-PrReadyPolicyAttempt([object]$receipt) {
    if ($null -eq $receipt -or $receipt.label -ne 'pr-ready' -or [string]::IsNullOrWhiteSpace([string]$receipt.path) -or [string]::IsNullOrWhiteSpace([string]$receipt.sha256) -or -not (Test-Path -LiteralPath $receipt.path -PathType Leaf)) { throw 'PR_READY_POLICY_ATTEMPT_INVALID' }; if ((Get-FileHash -LiteralPath $receipt.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $receipt.sha256) { throw 'PR_READY_POLICY_ATTEMPT_INVALID' }
    try { $value = Get-Content -Raw -LiteralPath $receipt.path | ConvertFrom-Json } catch { throw 'PR_READY_POLICY_ATTEMPT_INVALID' }; if ($value.schema -ne 'gwo-v8-c1-mutation-policy.v1' -or $value.label -ne 'pr-ready' -or $value.repository -ne $repo -or $value.default_branch -ne 'main' -or $value.actions_enabled -ne $false -or $value.workflow_count -ne 0 -or $value.ruleset_id -ne 20160628) { throw 'PR_READY_POLICY_ATTEMPT_INVALID' }
    $directory = Split-Path -Path $receipt.path -Parent; $values = [ordered]@{}; foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) { $property = $value.files.PSObject.Properties[$name]; if ($null -eq $property) { throw 'PR_READY_POLICY_ATTEMPT_INVALID' }; $item = $property.Value; $expectedPath = (Join-Path $directory $name).Replace('\','/'); if ([string]$item.path -cne $expectedPath -or -not (Test-Path -LiteralPath $item.path -PathType Leaf) -or (Get-FileHash -LiteralPath $item.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.sha256) { throw 'PR_READY_POLICY_ATTEMPT_INVALID' }; try { $values[$name] = Get-Content -Raw -LiteralPath $item.path | ConvertFrom-Json } catch { throw 'PR_READY_POLICY_ATTEMPT_INVALID' } }
    $a = $values['actions.json']; $w = $values['workflows.json']; $r = $values['ruleset.json']; $repository = $values['repository.json']; $types = @($r.rules | ForEach-Object type); $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }; if ($repository.full_name -ne $repo -or $repository.default_branch -ne 'main' -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0 -or @(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0 -or $allowed -notcontains 'squash' -or (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main'))) { throw 'PR_READY_POLICY_ATTEMPT_INVALID' }
    return [ordered]@{ label = 'pr-ready'; path = ([string]$receipt.path).Replace('\','/'); sha256 = [string]$receipt.sha256 }
}
function Assert-PrReadyCurrentPolicy([string]$failure) {
    $liveActions = @(gh api repos/$repo/actions/permissions 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_READY_CURRENT_POLICY_ACTIONS_READ_FAILED' }; $liveWorkflows = @(gh api repos/$repo/actions/workflows 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_READY_CURRENT_POLICY_WORKFLOWS_READ_FAILED' }; $liveRuleset = @(gh api repos/$repo/rulesets/20160628 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_READY_CURRENT_POLICY_RULESET_READ_FAILED' }; $liveRepository = @(gh api repos/$repo 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_READY_CURRENT_POLICY_REPOSITORY_READ_FAILED' }; $a = ($liveActions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($liveWorkflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($liveRuleset -join [Environment]::NewLine) | ConvertFrom-Json; $repository = ($liveRepository -join [Environment]::NewLine) | ConvertFrom-Json; $types = @($r.rules | ForEach-Object type); $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }; if ($repository.full_name -ne $repo -or $repository.default_branch -ne 'main' -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0 -or @(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0 -or $allowed -notcontains 'squash' -or (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main'))) { throw $failure }; return $true
}
function Read-PrReadyAuthorization([bool]$required,[string]$candidatePath=$null) {
    $reference = $state.mutation_authorization.pr_ready; if ($null -ne $candidatePath) { $candidate = ([IO.Path]::GetFullPath($candidatePath)).Replace('\','/'); if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw 'PR_READY_AUTHORIZATION_ORPHAN_INVALID' }; $reference = [ordered]@{ effect_key = 'pr-ready'; authorization_path = $candidate; authorization_sha256 = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant() } }; if ($null -eq $reference) { if ($required) { throw 'PR_READY_EXISTING_EFFECT_WITHOUT_AUTHORIZATION' }; return $null }
    if ($reference.effect_key -ne 'pr-ready' -or -not (Test-Path -LiteralPath $reference.authorization_path -PathType Leaf)) { throw 'PR_READY_AUTHORIZATION_REFERENCE_INVALID' }; $authorizationHash = (Get-FileHash -LiteralPath $reference.authorization_path -Algorithm SHA256).Hash.ToLowerInvariant(); if ($authorizationHash -ne $reference.authorization_sha256) { throw 'PR_READY_AUTHORIZATION_HASH_INVALID' }
    try { $value = Get-Content -Raw -LiteralPath $reference.authorization_path | ConvertFrom-Json; $authorizedAt = [DateTime]::Parse([string]$value.authorized_at).ToUniversalTime(); $authFrom = [DateTime]::Parse([string]$value.valid_from).ToUniversalTime(); $authUntil = [DateTime]::Parse([string]$value.valid_until).ToUniversalTime() } catch { throw 'PR_READY_AUTHORIZATION_MALFORMED' }; $expectedName = "pr-ready-$prNumber-$($value.policy_sha256)-$($value.lease_sha256).json"
    if ($value.schema -ne 'gwo-v8-c1-mutation-authorization.v1' -or $value.operation -ne 'pr-ready' -or $value.effect_key -ne 'pr-ready' -or $value.repository -ne $repo -or [int]$value.pr_number -ne $prNumber -or $value.base_sha -ne $state.identities.base.sha -or $value.head_sha -ne $state.identities.beta1.sha -or $value.owner -ne $approval.owner -or $value.lease_id -ne $lease.id -or $value.approval_sha256 -ne $approvalHash -or $value.lease_sha256 -ne $leaseHash -or $value.policy_label -ne 'pr-ready' -or [IO.Path]::GetFileName($reference.authorization_path) -cne $expectedName -or $authFrom -ne $leaseFrom -or $authUntil -ne $leaseUntil -or $authorizedAt -lt $authFrom -or $authorizedAt -ge $authUntil) { throw 'PR_READY_AUTHORIZATION_INVALID' }; if (-not (Test-Path -LiteralPath $value.policy_path -PathType Leaf) -or (Get-FileHash -LiteralPath $value.policy_path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $value.policy_sha256) { throw 'PR_READY_AUTHORIZATION_POLICY_INVALID' }; return [ordered]@{ reference = $reference; value = $value; policy = [ordered]@{ label = 'pr-ready'; path = [string]$value.policy_path; sha256 = [string]$value.policy_sha256 } }
}
function Save-PrReadyAuthorization([object]$policy,[object]$leaseEvidence) {
    $directory = Join-Path $evidence 'authorizations'; $orphanPaths = @(); if (Test-Path -LiteralPath $directory -PathType Container) { $orphanPaths = @(Get-ChildItem -LiteralPath $directory -Filter "pr-ready-$prNumber-*.json" -File | Sort-Object FullName | Where-Object { $null -eq $state.mutation_authorization.pr_ready -or $_.FullName.Replace('\','/') -ne $state.mutation_authorization.pr_ready.authorization_path }) }; if ($orphanPaths.Count -gt 1 -or ($null -ne $state.mutation_authorization.pr_ready -and $orphanPaths.Count -gt 0)) { throw 'PR_READY_AUTHORIZATION_ORPHAN_CONFLICT' }
    if ($null -ne $state.mutation_authorization.pr_ready) { $existing = Read-PrReadyAuthorization $true; if ($existing.policy.path -ne $policy.path -or $existing.policy.sha256 -ne $policy.sha256 -or $existing.value.lease_sha256 -ne $leaseEvidence.lease_sha256) { throw 'PR_READY_AUTHORIZATION_IMMUTABLE_CONFLICT' }; return $existing }
    if ($orphanPaths.Count -eq 1) { $orphan = Read-PrReadyAuthorization $true $orphanPaths[0].FullName; if ($orphan.policy.path -ne $policy.path -or $orphan.policy.sha256 -ne $policy.sha256 -or $orphan.value.lease_sha256 -ne $leaseEvidence.lease_sha256) { throw 'PR_READY_AUTHORIZATION_ORPHAN_POLICY_CONFLICT' }; $state.mutation_authorization.pr_ready = $orphan.reference; Save-State $state; $saved = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($saved.mutation_authorization.pr_ready.authorization_sha256 -ne $orphan.reference.authorization_sha256) { throw 'PR_READY_AUTHORIZATION_ORPHAN_STATE_INVALID' }; return Read-PrReadyAuthorization $true }
    $authorizedAt = [DateTime]::UtcNow; $authFrom = [DateTime]::Parse([string]$leaseEvidence.valid_from).ToUniversalTime(); $authUntil = [DateTime]::Parse([string]$leaseEvidence.valid_until).ToUniversalTime(); if ($authorizedAt -lt $authFrom -or $authorizedAt -ge $authUntil) { throw 'PR_READY_AUTHORIZATION_OUTSIDE_LEASE' }; if (-not (Test-Path -LiteralPath $directory -PathType Container)) { New-Item -ItemType Directory -Path $directory -ErrorAction Stop | Out-Null }; $path = Join-Path $directory ("pr-ready-$prNumber-$($policy.sha256)-$($leaseEvidence.lease_sha256).json")
    $value = [ordered]@{ schema = 'gwo-v8-c1-mutation-authorization.v1'; operation = 'pr-ready'; effect_key = 'pr-ready'; repository = $repo; pr_number = $prNumber; base_ref = 'main'; head_ref = 'codex/gwo-v8-beta1'; base_sha = $state.identities.base.sha; head_sha = $state.identities.beta1.sha; owner = [string]$leaseEvidence.owner; lease_id = [string]$leaseEvidence.lease_id; approval_sha256 = [string]$leaseEvidence.approval_sha256; lease_sha256 = [string]$leaseEvidence.lease_sha256; policy_label = 'pr-ready'; policy_path = [string]$policy.path; policy_sha256 = [string]$policy.sha256; authorized_at = $authorizedAt.ToString('o'); valid_from = $authFrom.ToString('o'); valid_until = $authUntil.ToString('o') }; $json = $value | ConvertTo-Json -Depth 30; if (Test-Path -LiteralPath $path -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $path) -ne $json) { throw 'PR_READY_AUTHORIZATION_CONFLICT' } } else { [IO.File]::WriteAllText($path,$json,[Text.UTF8Encoding]::new($false)) }; $reference = [ordered]@{ effect_key = 'pr-ready'; authorization_path = $path.Replace('\','/'); authorization_sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }; $state.mutation_authorization.pr_ready = $reference; Save-State $state; $saved = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($saved.mutation_authorization.pr_ready.authorization_sha256 -ne $reference.authorization_sha256) { throw 'PR_READY_AUTHORIZATION_STATE_READBACK_INVALID' }; return Read-PrReadyAuthorization $true
}
if ($didReady) {
    $orphanDirectory = Join-Path $evidence 'authorizations'; $orphanPaths = @(); if (Test-Path -LiteralPath $orphanDirectory -PathType Container) { $orphanPaths = @(Get-ChildItem -LiteralPath $orphanDirectory -Filter "pr-ready-$prNumber-*.json" -File | Sort-Object FullName | Where-Object { $null -eq $state.mutation_authorization.pr_ready -or $_.FullName.Replace('\','/') -ne $state.mutation_authorization.pr_ready.authorization_path }) }; if ($orphanPaths.Count -gt 1 -or ($null -ne $state.mutation_authorization.pr_ready -and $orphanPaths.Count -gt 0)) { throw 'PR_READY_AUTHORIZATION_ORPHAN_CONFLICT' }
    $readyAuthorization = Read-PrReadyAuthorization $false; $policyReceipt = $null; $leaseEvidence = $null
    if ($null -ne $readyAuthorization) { if ($null -eq $state.mutation_policy.pr_ready) { $policyReceipt = Assert-PrReadyPolicyAttempt $readyAuthorization.policy; Set-PrReadyPolicyIntent $policyReceipt; Save-State $state } else { $policyReceipt = Assert-PrReadyPolicyAttempt $state.mutation_policy.pr_ready; if ($policyReceipt.path -ne $readyAuthorization.policy.path -or $policyReceipt.sha256 -ne $readyAuthorization.policy.sha256) { throw 'PR_READY_POLICY_AUTHORIZATION_MISMATCH' } }; $null = Assert-PrReadyCurrentPolicy 'PR_READY_CURRENT_POLICY_INVALID'; $leaseEvidence = Assert-PrReadyLease 'PR_READY_RESUME_LEASE_INVALID'; Write-Output 'REUSE_RECOVERED_PR_READY_ATTEMPT'
    } elseif ($orphanPaths.Count -eq 1) {
        $readyAuthorization = Read-PrReadyAuthorization $true $orphanPaths[0].FullName; $canonicalPolicy = $state.mutation_policy.pr_ready
        if ($null -ne $canonicalPolicy) { $canonicalPolicy = Assert-PrReadyPolicyAttempt $canonicalPolicy; if ($readyAuthorization.policy.path -ne $canonicalPolicy.path -or $readyAuthorization.policy.sha256 -ne $canonicalPolicy.sha256) { throw 'PR_READY_AUTHORIZATION_ORPHAN_POLICY_CONFLICT' }; $policyReceipt = $canonicalPolicy } else { $policyReceipt = Assert-PrReadyPolicyAttempt $readyAuthorization.policy }
        if ($readyAuthorization.value.lease_sha256 -ne $leaseHash) { throw 'PR_READY_AUTHORIZATION_ORPHAN_LEASE_CONFLICT' }; $null = Assert-PrReadyCurrentPolicy 'PR_READY_CURRENT_POLICY_INVALID'
        if ($null -ne $state.mutation_authorization.pr_ready) { throw 'PR_READY_AUTHORIZATION_ORPHAN_CANONICAL_CONFLICT' }; $state.mutation_authorization.pr_ready = $readyAuthorization.reference; if ($null -eq $state.mutation_policy.pr_ready) { $adoptedPolicy = $policyReceipt; $state.mutation_policy.pr_ready = $adoptedPolicy } elseif ($state.mutation_policy.pr_ready.path -ne $policyReceipt.path -or $state.mutation_policy.pr_ready.sha256 -ne $policyReceipt.sha256) { throw 'PR_READY_AUTHORIZATION_ORPHAN_POLICY_CONFLICT' }; Save-State $state; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; $readyAuthorization = Read-PrReadyAuthorization $true; Write-Output 'ADOPTED_PR_READY_ORPHAN_ATTEMPT'
    } elseif ($null -ne $state.mutation_policy.pr_ready) { $policyReceipt = Assert-PrReadyPolicyAttempt $state.mutation_policy.pr_ready; $null = Assert-PrReadyCurrentPolicy 'PR_READY_CURRENT_POLICY_INVALID'; $leaseEvidence = Assert-PrReadyLease 'PR_READY_RESUME_LEASE_INVALID'; Write-Output 'REUSE_RECOVERED_PR_READY_POLICY'
    } else {
        $policyDir = Join-Path $evidence ('policy-before-ready-' + [guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $policyDir -ErrorAction Stop | Out-Null; $actions = @(gh api repos/$repo/actions/permissions 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'ACTIONS_READBACK_FAILED' }; $workflows = @(gh api repos/$repo/actions/workflows 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'WORKFLOW_READBACK_FAILED' }; $ruleset = @(gh api repos/$repo/rulesets/20160628 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'RULESET_READBACK_FAILED' }; [IO.File]::WriteAllText((Join-Path $policyDir 'actions.json'),($actions -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $policyDir 'workflows.json'),($workflows -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $policyDir 'ruleset.json'),($ruleset -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); $a = ($actions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($workflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($ruleset -join [Environment]::NewLine) | ConvertFrom-Json; if ($a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0) { throw 'POLICY_BEFORE_PR_READY_INVALID' }; $types = @($r.rules | ForEach-Object type); if (@(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0) { throw 'RULESET_BEFORE_PR_READY_INVALID' }; $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }; if ($allowed -notcontains 'squash') { throw 'SQUASH_NOT_ALLOWED' }; $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; if (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main')) { throw 'RULESET_BEFORE_PR_READY_NOT_APPLICABLE' }; $repoPolicyRaw = @(gh api repos/$repo 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REPOSITORY_POLICY_READ_FAILED' }; $repoPolicy = ($repoPolicyRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($repoPolicy.full_name -ne $repo -or $repoPolicy.default_branch -ne 'main') { throw 'DEFAULT_BRANCH_POLICY_INVALID' }; [IO.File]::WriteAllText((Join-Path $policyDir 'repository.json'),($repoPolicyRaw -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); $policyReceipt = New-PolicyReceipt 'pr-ready' $policyDir $a $w $r $repoPolicy; Set-PrReadyPolicyIntent $policyReceipt; Save-State $state; $policyState = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($policyState.mutation_policy.pr_ready.sha256 -ne $policyReceipt.sha256) { throw 'PR_READY_POLICY_STATE_READBACK_INVALID' }
    }
    if ($null -eq $leaseEvidence) { $leaseEvidence = Assert-PrReadyLease 'PR_READY_LEASE_AUTHORIZATION_INVALID' }; $readyAuthorization = Save-PrReadyAuthorization $policyReceipt $leaseEvidence
    $finalReadyLease = Assert-PrReadyLease 'PR_READY_FINAL_IMMEDIATE_LEASE_INVALID'; $finalReadyApprovalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $finalReadyLeaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); $finalReadyNow = [DateTime]::UtcNow; $finalReadyFrom = [DateTime]::Parse([string]$finalReadyLease.valid_from).ToUniversalTime(); $finalReadyUntil = [DateTime]::Parse([string]$finalReadyLease.valid_until).ToUniversalTime(); if ($finalReadyApprovalHash -ne $approvalHash -or $finalReadyLeaseHash -ne $leaseHash -or $finalReadyNow -lt $finalReadyFrom -or $finalReadyNow -ge $finalReadyUntil) { throw 'PR_READY_FINAL_IMMEDIATE_LEASE_INVALID' }; gh pr ready $prNumber --repo $repo; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_READY_FAILED' }
} else {
    $readyAuthorization = Read-PrReadyAuthorization $true; $policyReceipt = Assert-PrReadyPolicyAttempt $readyAuthorization.policy; if ($null -eq $state.mutation_policy.pr_ready) { Set-PrReadyPolicyIntent $policyReceipt; Save-State $state } elseif ($state.mutation_policy.pr_ready.path -ne $policyReceipt.path -or $state.mutation_policy.pr_ready.sha256 -ne $policyReceipt.sha256) { throw 'PR_READY_EXISTING_EFFECT_POLICY_AUTHORIZATION_MISMATCH' }
}
$ready = @(gh api "repos/$repo/pulls/$prNumber" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_READY_READBACK_FAILED' }; $ready = ($ready -join [Environment]::NewLine) | ConvertFrom-Json; if ($ready.draft -ne $false -or $ready.head.sha -ne $state.identities.beta1.sha -or $ready.base.ref -ne 'main') { throw 'PR_READY_IDENTITY_FAILED' }
if ($null -eq $readyAuthorization) { throw 'PR_READY_AUTHORIZATION_STATE_MISSING' }; if ($didReady) { $state.pr.ready_at = [DateTime]::UtcNow.ToString('o') }; $state.mutation_policy.pr_ready = $policyReceipt; $state.pr.ready_authorization_sha256 = $readyAuthorization.reference.authorization_sha256; $state.pr.ready_approval_sha256 = $approvalHash; $state.pr.ready_lease_sha256 = $leaseHash; Save-State $state; $check = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($check.pr.ready_lease_sha256 -ne $leaseHash -or $check.pr.ready_authorization_sha256 -ne $readyAuthorization.reference.authorization_sha256) { throw 'PR_READY_STATE_READBACK_INVALID' }
~~~

Keep the Integration Lease from this final readback through the merge and
immediate readback. No provider check or status wait is part of this gate.

- [ ] **3.5 Merge exactly by squash and read back the one-parent/tree gate.**

Immediately before the mutation repeat policy, PR, path, repository, thread,
source-ref, and protected-GA readbacks inside the owner-controlled lease. Then
persist the immutable merge authorization/intention with `authorized_at`
inside the just-reloaded lease window, reload its hash from state, and run
exactly. On resume, discover the merged effect first; require the exact old
authorization and policy hashes but never require the old lease to remain
current:

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
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
if ($state.identities.base.ref -ne 'refs/heads/main' -or $state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or (@($state.identities.base.parents) -join ',') -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -or $state.identities.beta1.ref -ne 'refs/heads/codex/gwo-v8-beta1' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.integration.tree -ne '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577') { throw 'FROZEN_IDENTITY_INVALID' }
$fullIdentityValid = $root -eq $state.coordinator_root -and (@($state.identities.base.parents) -join ',') -eq 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -and (@($state.identities.beta1.parents) -join ',') -eq '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -and (@($state.identities.integration.parents) -join ',') -eq 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875' -and $state.identities.protected_ga.ref -eq 'refs/heads/codex/gwo-v8-ga-plan' -and (@($state.identities.protected_ga.parents) -join ',') -eq '3b7097213ac482b3a9dcc31320e7bd84191bf2c0' -and $state.identities.boundaries.implementation -eq 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2' -and $state.identities.boundaries.beta1 -eq 'ddc1785f84b6a82a7b5c34d5928b046d4e9a781d'; if (-not $fullIdentityValid) { throw 'FULL_FROZEN_IDENTITY_INVALID' }
$prNumber = [int]$state.pr.number; $headSha = [string]$state.identities.beta1.sha
$approvalPath = Join-Path $evidence 'approvals/pr-owner.json'; $leasePath = Join-Path $evidence 'approvals/pr-integration-lease.json'
if (-not (Test-Path -LiteralPath $approvalPath -PathType Leaf) -or -not (Test-Path -LiteralPath $leasePath -PathType Leaf)) { throw 'PR_APPROVAL_OR_LEASE_MISSING' }
try { $approval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $lease = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json } catch { throw 'PR_APPROVAL_OR_LEASE_MALFORMED' }
$approvalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant()
$expectedPrScope = @('create','merge:squash','ready'); $approvalScope = @($approval.action_scope | ForEach-Object { [string]$_ } | Sort-Object); $leaseScope = @($lease.action_scope | ForEach-Object { [string]$_ } | Sort-Object)
if ($approval.schema -ne 'gwo-v8-c1-pr-owner-approval.v1' -or $approval.approved -ne $true -or $approval.repository -ne $repo -or $approval.base_ref -ne 'main' -or $approval.base_sha -ne $state.identities.base.sha -or $approval.head_ref -ne 'codex/gwo-v8-beta1' -or $approval.head_sha -ne $headSha -or [string]::IsNullOrWhiteSpace([string]$approval.owner) -or [string]::IsNullOrWhiteSpace([string]$approval.integration_lease_id) -or @(Compare-Object $expectedPrScope $approvalScope).Count -ne 0) { throw 'PR_APPROVAL_RELOAD_INVALID' }
$leaseFrom = [DateTime]::Parse([string]$lease.valid_from).ToUniversalTime(); $leaseUntil = [DateTime]::Parse([string]$lease.valid_until).ToUniversalTime()
if ($lease.schema -ne 'gwo-v8-c1-integration-lease.v1' -or $lease.state -ne 'active' -or $lease.id -ne $approval.integration_lease_id -or $lease.owner -ne $approval.owner -or $lease.repository -ne $repo -or $lease.scope -ne 'repository-global-remote-writer' -or $lease.exclusive_remote_writer_control -ne $true -or $lease.base_sha -ne $state.identities.base.sha -or $lease.base_tree -ne $state.identities.base.tree -or $lease.head_sha -ne $headSha -or $lease.head_tree -ne $state.identities.beta1.tree -or @(Compare-Object $expectedPrScope $leaseScope).Count -ne 0 -or $lease.approval_sha256 -ne $approvalHash -or $leaseFrom -ge $leaseUntil) { throw 'INTEGRATION_LEASE_RELOAD_INVALID' }
$savedApproval = $state.approvals.pr; $savedScope = @($savedApproval.action_scope | ForEach-Object { [string]$_ } | Sort-Object)
if ($savedApproval.approval_sha256 -ne $approvalHash -or $savedApproval.lease_sha256 -ne $leaseHash -or $savedApproval.owner -ne $approval.owner -or $savedApproval.lease_id -ne $lease.id -or $savedApproval.writer_scope -ne 'repository-global-remote-writer' -or $savedApproval.exclusive_remote_writer_control -ne $true -or $savedApproval.base_sha -ne $approval.base_sha -or $savedApproval.base_tree -ne $lease.base_tree -or $savedApproval.head_sha -ne $approval.head_sha -or $savedApproval.head_tree -ne $lease.head_tree -or @(Compare-Object $expectedPrScope $savedScope).Count -ne 0) { throw 'PR_APPROVAL_STATE_BINDING_INVALID' }
function New-PolicyReceipt([string]$label,[string]$directory,[object]$actionsObject,[object]$workflowsObject,[object]$rulesetObject,[object]$repositoryObject) { $files = [ordered]@{}; foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) { $path = Join-Path $directory $name; $files[$name] = [ordered]@{ path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() } }; $receipt = [ordered]@{ schema = 'gwo-v8-c1-mutation-policy.v1'; label = $label; repository = $repositoryObject.full_name; default_branch = $repositoryObject.default_branch; actions_enabled = $actionsObject.enabled; workflow_count = $workflowsObject.total_count; ruleset_id = $rulesetObject.id; files = $files }; $path = Join-Path $directory 'receipt.json'; $json = $receipt | ConvertTo-Json -Depth 30; if (Test-Path -LiteralPath $path -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $path) -ne $json) { throw 'POLICY_RECEIPT_CONFLICT' } } else { [IO.File]::WriteAllText($path,$json,[Text.UTF8Encoding]::new($false)) }; $policyReceipt = [ordered]@{ label = $label; path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }; return $policyReceipt }
function Assert-MergePolicyAttempt([object]$receipt) {
    try {
        if ($null -eq $receipt -or $receipt.label -ne 'pr-merge' -or [string]::IsNullOrWhiteSpace([string]$receipt.path) -or [string]::IsNullOrWhiteSpace([string]$receipt.sha256)) { throw 'MERGE_POLICY_ATTEMPT_INVALID' }
        $receiptPath = ([IO.Path]::GetFullPath([string]$receipt.path)).Replace([char]92,'/')
        if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) { throw 'MERGE_POLICY_ATTEMPT_INVALID' }
        if ((Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$receipt.sha256) { throw 'MERGE_POLICY_ATTEMPT_INVALID' }
        $value = Get-Content -Raw -LiteralPath $receiptPath | ConvertFrom-Json
        if ($null -eq $value -or $value.schema -ne 'gwo-v8-c1-mutation-policy.v1' -or $value.label -ne 'pr-merge' -or $value.repository -ne $repo -or $value.default_branch -ne 'main' -or $null -eq $value.PSObject.Properties['files'] -or @($value.files.PSObject.Properties).Count -ne 4) { throw 'MERGE_POLICY_ATTEMPT_INVALID' }
        $directory = Split-Path -Path $receiptPath -Parent; $values = [ordered]@{}
        foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) {
            $property = $value.files.PSObject.Properties[$name]; if ($null -eq $property -or $null -eq $property.Value) { throw 'MERGE_POLICY_ATTEMPT_INVALID' }
            $item = $property.Value; if ($null -eq $item.PSObject.Properties['path'] -or $null -eq $item.PSObject.Properties['sha256'] -or [string]::IsNullOrWhiteSpace([string]$item.path) -or [string]::IsNullOrWhiteSpace([string]$item.sha256)) { throw 'MERGE_POLICY_ATTEMPT_INVALID' }
            $expectedPath = ([IO.Path]::GetFullPath((Join-Path $directory $name))).Replace([char]92,'/'); $actualPath = ([IO.Path]::GetFullPath([string]$item.path)).Replace([char]92,'/')
            if ($actualPath -cne $expectedPath -or -not (Test-Path -LiteralPath $actualPath -PathType Leaf)) { throw 'MERGE_POLICY_ATTEMPT_INVALID' }
            if ((Get-FileHash -LiteralPath $actualPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$item.sha256) { throw 'MERGE_POLICY_ATTEMPT_INVALID' }
            try { $values[$name] = Get-Content -Raw -LiteralPath $actualPath | ConvertFrom-Json } catch { throw 'MERGE_POLICY_ATTEMPT_INVALID' }
        }
        $a = $values['actions.json']; $w = $values['workflows.json']; $r = $values['ruleset.json']; $repository = $values['repository.json']; $types = @($r.rules | ForEach-Object { [string]$_.type })
        $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }
        $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }
        if ($repository.full_name -ne $repo -or $repository.default_branch -ne 'main' -or $value.repository -ne $repository.full_name -or $value.default_branch -ne $repository.default_branch -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $value.actions_enabled -ne $a.enabled -or $value.workflow_count -ne $w.total_count -or $r.id -ne 20160628 -or $value.ruleset_id -ne $r.id -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0 -or @(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0 -or $allowed -notcontains 'squash' -or (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main'))) { throw 'MERGE_POLICY_ATTEMPT_INVALID' }
    } catch {
        throw 'MERGE_POLICY_ATTEMPT_INVALID'
    }
    return [ordered]@{ label = 'pr-merge'; path = $receiptPath; sha256 = [string]$receipt.sha256 }
}
function Assert-MergeCurrentPolicy([string]$failure) {
    $liveActions = @(gh api repos/$repo/actions/permissions 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MERGE_CURRENT_POLICY_ACTIONS_READ_FAILED' }
    $liveWorkflows = @(gh api repos/$repo/actions/workflows 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MERGE_CURRENT_POLICY_WORKFLOWS_READ_FAILED' }
    $liveRuleset = @(gh api repos/$repo/rulesets/20160628 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MERGE_CURRENT_POLICY_RULESET_READ_FAILED' }
    $liveRepository = @(gh api repos/$repo 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MERGE_CURRENT_POLICY_REPOSITORY_READ_FAILED' }
    $a = ($liveActions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($liveWorkflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($liveRuleset -join [Environment]::NewLine) | ConvertFrom-Json; $repository = ($liveRepository -join [Environment]::NewLine) | ConvertFrom-Json
    $types = @($r.rules | ForEach-Object type); $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }
    if ($repository.full_name -ne $repo -or $repository.default_branch -ne 'main' -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0 -or @(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0 -or $allowed -notcontains 'squash' -or (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main'))) { throw $failure }
}
function Read-MergeAuthorization([string]$candidatePath=$null) {
    $reference = $state.mutation_authorization.pr_merge; if ($null -ne $candidatePath) { $candidate = ([IO.Path]::GetFullPath($candidatePath)).Replace('\','/'); if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw 'MERGE_AUTHORIZATION_ORPHAN_INVALID' }; $reference = [ordered]@{ effect_key = 'pr-merge'; authorization_path = $candidate; authorization_sha256 = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant() } }; if ($null -eq $reference) { return $null }
    if ($reference.effect_key -ne 'pr-merge' -or -not (Test-Path -LiteralPath $reference.authorization_path -PathType Leaf)) { throw 'MERGE_AUTHORIZATION_REFERENCE_INVALID' }
    $authorizationHash = (Get-FileHash -LiteralPath $reference.authorization_path -Algorithm SHA256).Hash.ToLowerInvariant(); if ($authorizationHash -ne $reference.authorization_sha256) { throw 'MERGE_AUTHORIZATION_HASH_INVALID' }
    try { $value = Get-Content -Raw -LiteralPath $reference.authorization_path | ConvertFrom-Json; $authorizedAt = [DateTime]::Parse([string]$value.authorized_at).ToUniversalTime(); $authFrom = [DateTime]::Parse([string]$value.valid_from).ToUniversalTime(); $authUntil = [DateTime]::Parse([string]$value.valid_until).ToUniversalTime() } catch { throw 'MERGE_AUTHORIZATION_MALFORMED' }
    if ($value.schema -ne 'gwo-v8-c1-mutation-authorization.v1' -or $value.operation -ne 'squash-merge' -or $value.effect_key -ne 'pr-merge' -or $value.repository -ne $repo -or [int]$value.pr_number -ne $prNumber -or $value.base_sha -ne $state.identities.base.sha -or $value.head_sha -ne $headSha -or $value.owner -ne $approval.owner -or $value.lease_id -ne $lease.id -or $value.approval_sha256 -ne $approvalHash -or $value.lease_sha256 -ne $leaseHash -or $value.policy_label -ne 'pr-merge' -or $authFrom -ne $leaseFrom -or $authUntil -ne $leaseUntil -or $authorizedAt -lt $authFrom -or $authorizedAt -ge $authUntil) { throw 'MERGE_AUTHORIZATION_INVALID' }
    if (-not (Test-Path -LiteralPath $value.policy_path -PathType Leaf) -or (Get-FileHash -LiteralPath $value.policy_path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $value.policy_sha256) { throw 'MERGE_AUTHORIZATION_POLICY_INVALID' }
    return [ordered]@{ reference = $reference; value = $value; policy = [ordered]@{ label = 'pr-merge'; path = [string]$value.policy_path; sha256 = [string]$value.policy_sha256 } }
}
function Save-MergeAuthorization([object]$policy,[DateTime]$from,[DateTime]$until) {
    $directory = Join-Path $evidence 'authorizations'; $orphanPaths = @(); if (Test-Path -LiteralPath $directory -PathType Container) { $orphanPaths = @(Get-ChildItem -LiteralPath $directory -Filter 'pr-merge-*.json' -File | Sort-Object FullName | Where-Object { $null -eq $state.mutation_authorization.pr_merge -or $_.FullName.Replace([char]92,'/') -ne $state.mutation_authorization.pr_merge.authorization_path }) }; if ($orphanPaths.Count -gt 1 -or ($null -ne $state.mutation_authorization.pr_merge -and $orphanPaths.Count -gt 0)) { throw 'MERGE_AUTHORIZATION_ORPHAN_CONFLICT' }
    if ($null -ne $state.mutation_authorization.pr_merge) { $existing = Read-MergeAuthorization; if ($existing.policy.path -ne $policy.path -or $existing.policy.sha256 -ne $policy.sha256 -or $existing.value.lease_sha256 -ne $leaseHash) { throw 'MERGE_AUTHORIZATION_IMMUTABLE_CONFLICT' }; return $existing }
    if ($orphanPaths.Count -eq 1) { $orphan = Read-MergeAuthorization $orphanPaths[0].FullName; if ($orphan.policy.path -ne $policy.path -or $orphan.policy.sha256 -ne $policy.sha256 -or $orphan.value.lease_sha256 -ne $leaseHash) { throw 'MERGE_AUTHORIZATION_ORPHAN_POLICY_CONFLICT' }; $state.mutation_authorization.pr_merge = $orphan.reference; Save-State $state; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; return Read-MergeAuthorization }
    $authorizedAt = [DateTime]::UtcNow; if ($authorizedAt -lt $from -or $authorizedAt -ge $until) { throw 'MERGE_AUTHORIZATION_OUTSIDE_LEASE' }
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { New-Item -ItemType Directory -Path $directory -ErrorAction Stop | Out-Null }
    $path = Join-Path $directory ("pr-merge-$($policy.sha256).json"); $value = [ordered]@{ schema = 'gwo-v8-c1-mutation-authorization.v1'; operation = 'squash-merge'; effect_key = 'pr-merge'; repository = $repo; pr_number = $prNumber; base_sha = $state.identities.base.sha; head_sha = $headSha; owner = [string]$approval.owner; lease_id = [string]$lease.id; approval_sha256 = $approvalHash; lease_sha256 = $leaseHash; policy_label = 'pr-merge'; policy_path = [string]$policy.path; policy_sha256 = [string]$policy.sha256; authorized_at = $authorizedAt.ToString('o'); valid_from = $from.ToString('o'); valid_until = $until.ToString('o') }
    $text = $value | ConvertTo-Json -Depth 30; if (Test-Path -LiteralPath $path -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $path) -ne $text) { throw 'MERGE_AUTHORIZATION_CONFLICT' } } else { [IO.File]::WriteAllText($path,$text,[Text.UTF8Encoding]::new($false)) }
    $reference = [ordered]@{ effect_key = 'pr-merge'; authorization_path = $path.Replace('\','/'); authorization_sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }; $state.mutation_authorization.pr_merge = $reference; Save-State $state
    $saved = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($saved.mutation_authorization.pr_merge.authorization_sha256 -ne $reference.authorization_sha256) { throw 'MERGE_AUTHORIZATION_STATE_READBACK_INVALID' }
    return Read-MergeAuthorization
}
$baseRefRaw = @(gh api "repos/$repo/git/ref/heads/main" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'BASE_REF_READ_FAILED' }; $baseRef = ($baseRefRaw -join [Environment]::NewLine) | ConvertFrom-Json
$prBeforeRaw = @(gh api "repos/$repo/pulls/$prNumber" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PR_READ_BEFORE_MERGE_FAILED' }; $prBefore = ($prBeforeRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($prBefore.head.repo.full_name -ne $repo -or $prBefore.base.repo.full_name -ne $repo -or $prBefore.head.ref -ne 'codex/gwo-v8-beta1' -or $prBefore.base.ref -ne 'main' -or $prBefore.base.sha -ne $state.identities.base.sha -or $prBefore.head.sha -ne $headSha -or $prBefore.draft -ne $false) { throw 'PR_HEAD_BASE_CHANGED' }
$alreadyMerged = $prBefore.merged -eq $true; $policyReceipt = $state.mutation_policy.pr_merge; if ($alreadyMerged) { $orphanDirectory = Join-Path $evidence 'authorizations'; $orphanPaths = @(); if (Test-Path -LiteralPath $orphanDirectory -PathType Container) { $orphanPaths = @(Get-ChildItem -LiteralPath $orphanDirectory -Filter 'pr-merge-*.json' -File | Sort-Object FullName | Where-Object { $null -eq $state.mutation_authorization.pr_merge -or $_.FullName.Replace([char]92,'/') -ne $state.mutation_authorization.pr_merge.authorization_path }) }; if ($orphanPaths.Count -gt 0) { throw 'MERGE_AUTHORIZATION_ORPHAN_CONFLICT' } }; if ($alreadyMerged) { if ($null -eq $policyReceipt) { throw 'MERGE_POLICY_MISSING_FOR_MERGED_PR' }; $policyReceipt = Assert-MergePolicyAttempt $policyReceipt }; $mergeAuthorization = Read-MergeAuthorization
if (-not $alreadyMerged) {
    if ($baseRef.object.sha -ne $state.identities.base.sha) { throw 'BASE_MOVED_BEFORE_MERGE' }
    $orphanDirectory = Join-Path $evidence 'authorizations'; $orphanPaths = @(); if (Test-Path -LiteralPath $orphanDirectory -PathType Container) { $orphanPaths = @(Get-ChildItem -LiteralPath $orphanDirectory -Filter 'pr-merge-*.json' -File | Sort-Object FullName | Where-Object { $null -eq $state.mutation_authorization.pr_merge -or $_.FullName.Replace('\','/') -ne $state.mutation_authorization.pr_merge.authorization_path }) }; if ($orphanPaths.Count -gt 1 -or ($null -ne $state.mutation_authorization.pr_merge -and $orphanPaths.Count -gt 0)) { throw 'MERGE_AUTHORIZATION_ORPHAN_CONFLICT' }
    if ($null -ne $mergeAuthorization) {
        if ($null -eq $policyReceipt) { $policyReceipt = Assert-MergePolicyAttempt $mergeAuthorization.policy; $state.mutation_policy.pr_merge = $policyReceipt; Save-State $state } else { $policyReceipt = Assert-MergePolicyAttempt $policyReceipt; if ($policyReceipt.path -ne $mergeAuthorization.policy.path -or $policyReceipt.sha256 -ne $mergeAuthorization.policy.sha256) { throw 'MERGE_POLICY_AUTHORIZATION_MISMATCH' } }
        $null = Assert-MergeCurrentPolicy 'MERGE_CURRENT_POLICY_INVALID'; Write-Output 'REUSE_RECOVERED_MERGE_ATTEMPT'
    } elseif ($orphanPaths.Count -eq 1) {
        $mergeAuthorization = Read-MergeAuthorization $orphanPaths[0].FullName; $canonicalPolicy = $policyReceipt
        if ($null -ne $canonicalPolicy) { $canonicalPolicy = Assert-MergePolicyAttempt $canonicalPolicy; if ($mergeAuthorization.policy.path -ne $canonicalPolicy.path -or $mergeAuthorization.policy.sha256 -ne $canonicalPolicy.sha256) { throw 'MERGE_AUTHORIZATION_ORPHAN_POLICY_CONFLICT' }; $policyReceipt = $canonicalPolicy } else { $policyReceipt = Assert-MergePolicyAttempt $mergeAuthorization.policy }
        $null = Assert-MergeCurrentPolicy 'MERGE_CURRENT_POLICY_INVALID'; if ($null -ne $state.mutation_authorization.pr_merge) { throw 'MERGE_AUTHORIZATION_ORPHAN_CANONICAL_CONFLICT' }; $state.mutation_authorization.pr_merge = $mergeAuthorization.reference; if ($null -eq $state.mutation_policy.pr_merge) { $state.mutation_policy.pr_merge = $policyReceipt } elseif ($state.mutation_policy.pr_merge.path -ne $policyReceipt.path -or $state.mutation_policy.pr_merge.sha256 -ne $policyReceipt.sha256) { throw 'MERGE_AUTHORIZATION_ORPHAN_POLICY_CONFLICT' }; Save-State $state; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; $mergeAuthorization = Read-MergeAuthorization; Write-Output 'ADOPTED_MERGE_ORPHAN_ATTEMPT'
    } elseif ($null -ne $policyReceipt) {
        $policyReceipt = Assert-MergePolicyAttempt $policyReceipt; $null = Assert-MergeCurrentPolicy 'MERGE_CURRENT_POLICY_INVALID'; Write-Output 'REUSE_RECOVERED_MERGE_POLICY'
    } else {
    $policyDir = Join-Path $evidence ('policy-before-merge-' + [guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $policyDir -ErrorAction Stop | Out-Null
    $actions = @(gh api repos/$repo/actions/permissions 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'ACTIONS_READBACK_FAILED' }; $workflows = @(gh api repos/$repo/actions/workflows 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'WORKFLOW_READBACK_FAILED' }; $ruleset = @(gh api repos/$repo/rulesets/20160628 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'RULESET_READBACK_FAILED' }; $repoRaw = @(gh api repos/$repo 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REPOSITORY_READ_FAILED' }
    [IO.File]::WriteAllText((Join-Path $policyDir 'actions.json'),($actions -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $policyDir 'workflows.json'),($workflows -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $policyDir 'ruleset.json'),($ruleset -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)); [IO.File]::WriteAllText((Join-Path $policyDir 'repository.json'),($repoRaw -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false))
    $a = ($actions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($workflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($ruleset -join [Environment]::NewLine) | ConvertFrom-Json; $repoObject = ($repoRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($repoObject.full_name -ne $repo -or $repoObject.default_branch -ne 'main' -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0) { throw 'POLICY_BEFORE_MERGE_INVALID' }; $types = @($r.rules | ForEach-Object type); if (@(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0) { throw 'RULESET_BEFORE_MERGE_INVALID' }; $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }; if ($allowed -notcontains 'squash') { throw 'SQUASH_NOT_ALLOWED' }; $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; if (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main')) { throw 'RULESET_DEFAULT_BRANCH_NOT_APPLICABLE' }
    $policyReceipt = New-PolicyReceipt 'pr-merge' $policyDir $a $w $r $repoObject
    $state.mutation_policy.pr_merge = $policyReceipt; Save-State $state; $policyState = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($policyState.mutation_policy.pr_merge.sha256 -ne $policyReceipt.sha256) { throw 'MERGE_POLICY_STATE_READBACK_INVALID' }
    }
    $baseFinalRaw = @(gh api "repos/$repo/git/ref/heads/main" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'FINAL_BASE_READ_FAILED' }; $baseFinal = ($baseFinalRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($baseFinal.object.sha -ne $state.identities.base.sha) { throw 'FINAL_BASE_MOVED' }
    $prFinalRaw = @(gh api "repos/$repo/pulls/$prNumber" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'FINAL_PR_READ_FAILED' }; $prFinal = ($prFinalRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($prFinal.head.sha -ne $headSha -or $prFinal.base.sha -ne $state.identities.base.sha -or $prFinal.head.repo.full_name -ne $repo -or $prFinal.base.repo.full_name -ne $repo -or $prFinal.base.ref -ne 'main' -or $prFinal.draft -ne $false -or $prFinal.merged -eq $true) { throw 'FINAL_PR_IDENTITY_INVALID' }
    $betaRemote = @(git -C $root ls-remote --heads origin refs/heads/codex/gwo-v8-beta1); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $betaRemote.Count -ne 1 -or (($betaRemote[0] -split '\s+')[0]) -ne $state.identities.beta1.sha) { throw 'BETA1_REMOTE_CHANGED' }; $gaRemote = @(git -C $root ls-remote --heads origin refs/heads/codex/gwo-v8-ga-plan); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $gaRemote.Count -ne 1 -or (($gaRemote[0] -split '\s+')[0]) -ne $state.identities.protected_ga.sha) { throw 'PROTECTED_GA_REMOTE_CHANGED' }
    $approvalImmediate = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $leaseImmediate = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json; $approvalHashImmediate = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseHashImmediate = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); $fromImmediate = [DateTime]::Parse([string]$leaseImmediate.valid_from).ToUniversalTime(); $untilImmediate = [DateTime]::Parse([string]$leaseImmediate.valid_until).ToUniversalTime(); if ($approvalHashImmediate -ne $approvalHash -or $leaseHashImmediate -ne $leaseHash -or $leaseImmediate.id -ne $approvalImmediate.integration_lease_id -or $leaseImmediate.owner -ne $approvalImmediate.owner -or $leaseImmediate.scope -ne 'repository-global-remote-writer' -or $leaseImmediate.exclusive_remote_writer_control -ne $true -or $leaseImmediate.base_sha -ne $baseFinal.object.sha -or $leaseImmediate.head_sha -ne $prFinal.head.sha -or [DateTime]::UtcNow -lt $fromImmediate -or [DateTime]::UtcNow -ge $untilImmediate) { throw 'INTEGRATION_LEASE_FINAL_RELOAD_INVALID' }
    if ($null -eq $mergeAuthorization) { $mergeAuthorization = Save-MergeAuthorization $policyReceipt $fromImmediate $untilImmediate }
    $mergeFinalApproval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $mergeFinalLease = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json; $mergeFinalApprovalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $mergeFinalLeaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); $mergeFinalFrom = [DateTime]::Parse([string]$mergeFinalLease.valid_from).ToUniversalTime(); $mergeFinalUntil = [DateTime]::Parse([string]$mergeFinalLease.valid_until).ToUniversalTime(); $mergeFinalNow = [DateTime]::UtcNow; if ($mergeFinalApprovalHash -ne $approvalHash -or $mergeFinalLeaseHash -ne $leaseHash -or $mergeFinalLease.id -ne $mergeFinalApproval.integration_lease_id -or $mergeFinalLease.approval_sha256 -ne $mergeFinalApprovalHash -or $mergeFinalNow -lt $mergeFinalFrom -or $mergeFinalNow -ge $mergeFinalUntil) { throw 'MERGE_FINAL_IMMEDIATE_LEASE_INVALID' }
    gh pr merge $prNumber --repo $repo --squash --match-head-commit $headSha; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'SQUASH_MERGE_FAILED' }
} else {
    if ([string]$prBefore.merge_commit_sha -notmatch '^[0-9a-f]{40}$' -or $baseRef.object.sha -ne $prBefore.merge_commit_sha) { throw 'MERGE_CANONICAL_RESUME_INVALID' }
    if ($null -eq $mergeAuthorization) { throw 'MERGE_EFFECT_WITHOUT_AUTHORIZATION' }; $policyReceipt = $mergeAuthorization.policy
}
$pr = @(gh api "repos/$repo/pulls/$prNumber" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MERGED_PR_READ_FAILED' }; $pr = ($pr -join [Environment]::NewLine) | ConvertFrom-Json; $mergeSha = [string]$pr.merge_commit_sha; if ([string]::IsNullOrWhiteSpace($mergeSha) -or $pr.merge_commit_sha -notmatch '^[0-9a-f]{40}$' -or $pr.head.sha -ne $headSha -or $pr.head.repo.full_name -ne $repo -or $pr.base.repo.full_name -ne $repo) { throw 'MERGE_PR_IDENTITY_FAILED' }
$ref = @(gh api "repos/$repo/git/ref/heads/main" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MAIN_REF_READ_FAILED' }; $ref = ($ref -join [Environment]::NewLine) | ConvertFrom-Json; if ($ref.object.sha -ne $mergeSha) { throw 'MAIN_REF_NOT_MERGE_SHA' }; $commit = @(gh api "repos/$repo/git/commits/$mergeSha" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MAIN_COMMIT_READ_FAILED' }; $commit = ($commit -join [Environment]::NewLine) | ConvertFrom-Json; if (@($commit.parents).Count -ne 1 -or $commit.parents[0].sha -ne $state.identities.base.sha -or $commit.tree.sha -ne $state.identities.beta1.tree) { throw 'SQUASH_TREE_PARENT_FAILED' }
git -C $root fetch --no-tags origin refs/heads/main; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MERGED_MAIN_FETCH_FAILED' }; $fetched = (git -C $root rev-parse FETCH_HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $fetched -ne $mergeSha) { throw 'MERGED_MAIN_FETCH_IDENTITY_FAILED' }
$authorizationRef = $mergeAuthorization.reference; if ($null -eq $authorizationRef) { throw 'MERGE_EFFECT_WITHOUT_AUTHORIZATION' }
$mergeReceipt = [ordered]@{ method = 'squash'; merge_sha = $mergeSha; merge_commit_sha = $pr.merge_commit_sha; tree = $commit.tree.sha; parents = @($commit.parents | ForEach-Object sha); source_sha = $state.identities.beta1.sha; base_sha = $state.identities.base.sha; integration_lease_id = $lease.id; approval_sha256 = $approvalHash; lease_sha256 = $leaseHash; authorization_path = $authorizationRef.authorization_path; authorization_sha256 = $authorizationRef.authorization_sha256; fetched_main_sha = $fetched }; $mergeEffectPath = Join-Path $evidence 'merge-effect.json'; $mergeEffectText = $mergeReceipt | ConvertTo-Json -Depth 30; if (Test-Path -LiteralPath $mergeEffectPath -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $mergeEffectPath) -ne $mergeEffectText) { throw 'MERGE_CANONICAL_RESUME_INVALID' } } else { [IO.File]::WriteAllText($mergeEffectPath,$mergeEffectText,[Text.UTF8Encoding]::new($false)) }; $state.pr.merge = $mergeReceipt; $state.mutation_policy.pr_merge = $policyReceipt; $state.mutation_authorization.pr_merge = $authorizationRef; Save-State $state; $check = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($check.pr.merge.merge_commit_sha -ne $mergeSha -or $check.pr.merge.fetched_main_sha -ne $mergeSha -or $check.mutation_policy.pr_merge.sha256 -ne $policyReceipt.sha256 -or $check.mutation_authorization.pr_merge.authorization_sha256 -ne $authorizationRef.authorization_sha256) { throw 'MERGE_STATE_READBACK_INVALID' }
~~~

Expected: the remote main commit equals REST `merge_commit_sha`, has exactly one
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
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
if ($state.identities.base.ref -ne 'refs/heads/main' -or $state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.integration.tree -ne '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577') { throw 'FROZEN_IDENTITY_INVALID' }
$merged = $state.pr.merge.merge_sha; if ([string]::IsNullOrWhiteSpace($merged)) { throw 'MERGE_SHA_MISSING' }
function Save-State([object]$value) { $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp'); [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false)); if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }; try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }; [IO.File]::Replace($tmp,$statePath,$null,$true); try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' } }
git -C $root fetch --no-tags origin refs/heads/main; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MERGED_MAIN_FETCH_FAILED' }
$fetched = (git -C $root rev-parse FETCH_HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $fetched -ne $merged) { throw 'MERGED_MAIN_FETCH_IDENTITY_FAILED' }
$python = Join-Path $evidence 'python313/Scripts/python.exe'; if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'PYTHON_ENV_MISSING' }
$version = (& $python --version 2>&1) -join ' '; $exit = $LASTEXITCODE; if ($exit -ne 0 -or $version -ne 'Python 3.13.11') { throw 'PYTHON_VERSION_INVALID' }
function Assert-CommandManifest([object]$manifest,[string]$diffArgument) { $expectedNames = @('package','full','quick','sync','diff','status'); $expectedPython = $python.Replace('\','/'); $expectedExecutables = @($expectedPython,$expectedPython,$expectedPython,$expectedPython,'git','git'); $expectedArgs = @(@('-m','pytest','tests/test_orchestrator_package.py','-q'),@('-m','pytest','-q'),@('scripts/quick_validate.py'),@('scripts/sync_orchestrator.py','--check'),@('-C','SUBJECT','diff','--check',$diffArgument),@('-C','SUBJECT','status','--porcelain=v1','--untracked-files=all')); $commands = @($manifest.commands); if ($commands.Count -ne 6) { throw 'COMMAND_COUNT_INVALID' }; for ($i = 0; $i -lt 6; $i++) { $actualArgs = @($commands[$i].arguments | ForEach-Object { if ($_ -eq $commands[$i].arguments[1] -and $commands[$i].name -in @('diff','status')) { 'SUBJECT' } else { [string]$_ } }); $actualExecutable = ([string]$commands[$i].executable).Replace('\','/'); if ($commands[$i].name -ne $expectedNames[$i] -or $actualExecutable -cne $expectedExecutables[$i]) { throw "COMMAND_IDENTITY_INVALID:$i" }; if (@(Compare-Object $expectedArgs[$i] $actualArgs -SyncWindow 0).Count -ne 0) { throw "COMMAND_ARGUMENTS_INVALID:$i" } }; $status = $commands[5]; if ($status.output_empty -ne $true -or -not (Test-Path -LiteralPath $status.log -PathType Leaf) -or (Get-Item -LiteralPath $status.log).Length -ne 0) { throw 'STATUS_OUTPUT_NOT_EMPTY' } }
$manifestPath = Join-Path $evidence 'merged-main-local-verification.json'; if (Test-Path -LiteralPath $manifestPath -PathType Leaf) { try { $existingManifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json } catch { throw 'EXISTING_MERGED_MANIFEST_MALFORMED' }; if ($existingManifest.schema -ne 'gwo-c1-local-verification.v2' -or $existingManifest.mode -ne 'Local Verification Only' -or $existingManifest.subject_sha -ne $merged -or $existingManifest.subject_tree -ne $state.identities.beta1.tree -or $existingManifest.base_sha -ne $state.identities.base.sha -or $existingManifest.base_tree -ne $state.identities.base.tree -or $existingManifest.python_version -ne 'Python 3.13.11' -or $existingManifest.requirements_sha256 -ne 'ee3c9f14db38950f5869759a5a94347197c9d4db3f138147b614ad6c4d862534' -or $existingManifest.final_outcome -ne 'pass' -or $existingManifest.workflow_count -ne 0 -or @($existingManifest.commands).Count -ne 6) { throw 'EXISTING_MERGED_MANIFEST_IDENTITY_INVALID' }; Assert-CommandManifest $existingManifest "$($state.identities.base.sha)..HEAD"; foreach ($record in @($existingManifest.commands)) { if ($record.exit_code -ne 0 -or -not (Test-Path -LiteralPath $record.log -PathType Leaf)) { throw 'EXISTING_MERGED_LOG_INVALID' }; $actualLogHash = (Get-FileHash -LiteralPath $record.log -Algorithm SHA256).Hash.ToLowerInvariant(); if ($actualLogHash -ne $record.sha256) { throw 'EXISTING_MERGED_LOG_HASH_INVALID' } }; $existingHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant(); $state.local_verification.merged_main = [ordered]@{ manifest = $manifestPath.Replace('\','/'); manifest_sha256 = $existingHash; requirements_sha256 = $existingManifest.requirements_sha256; command_count = @($existingManifest.commands).Count }; Save-State $state; exit 0 }
function Run-Log([string]$name,[string]$exe,[string[]]$args,[string]$cwd,[string]$log) { Push-Location $cwd; try { & $exe @args *> $log; $code = $LASTEXITCODE } finally { Pop-Location }; $tail = @(Get-Content -LiteralPath $log -ErrorAction Stop | Select-Object -Last 20) -join [Environment]::NewLine; $hash = (Get-FileHash -LiteralPath $log -Algorithm SHA256).Hash.ToLowerInvariant(); return [ordered]@{ name = $name; executable = $exe; arguments = @($args); log = $log.Replace('\','/'); exit_code = $code; summary = $tail; output_empty = ((Get-Item -LiteralPath $log).Length -eq 0); sha256 = $hash } }
$checkout = Join-Path $evidence 'worktrees/merged-main'; if (Test-Path -LiteralPath $checkout) { throw 'MERGED_CHECKOUT_EXISTS' }; New-Item -ItemType Directory -Path (Split-Path $checkout) -ErrorAction Stop | Out-Null
git -C $root worktree add --detach $checkout $merged; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MERGED_CHECKOUT_CREATE_FAILED' }
$requirements = Join-Path $checkout '.github/requirements-ci-win-py313.txt'; if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) { throw 'SUBJECT_REQUIREMENTS_MISSING' }; $reqHash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash.ToLowerInvariant(); if ($reqHash -ne 'ee3c9f14db38950f5869759a5a94347197c9d4db3f138147b614ad6c4d862534') { throw 'SUBJECT_REQUIREMENTS_HASH_INVALID' }
$tree = (git -C $checkout rev-parse 'HEAD^{tree}').Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $tree -ne $state.identities.beta1.tree) { throw 'MERGED_TREE_INVALID' }
$parents = ((git -C $checkout show -s --format=%P HEAD)); $exit = $LASTEXITCODE; if ($exit -ne 0 -or @($parents -split '\s+' | Where-Object { $_ }).Count -ne 1 -or ($parents -split '\s+')[0] -ne $state.identities.base.sha) { throw 'MERGED_PARENT_INVALID' }
$logs = Join-Path $evidence 'logs/merged-main'; New-Item -ItemType Directory -Path $logs -ErrorAction Stop | Out-Null; if (@(Get-ChildItem -LiteralPath $logs -File -ErrorAction SilentlyContinue).Count -ne 0) { throw 'EXISTING_MERGED_LOG_REQUIRES_MANIFEST_RESUME' }; $records = @(); $previous = $env:GWO_CONVERGENCE_ARCHIVE_ROOT
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
$manifest = [ordered]@{ schema = 'gwo-c1-local-verification.v2'; mode = 'Local Verification Only'; subject_sha = $merged; subject_tree = $tree; parent_shas = @($parents -split '\s+' | Where-Object { $_ }); base_sha = $state.identities.base.sha; base_tree = $state.identities.base.tree; python_version = $version; requirements_path = '.github/requirements-ci-win-py313.txt'; requirements_sha256 = $reqHash; commands = $records; workflow_count = 0; final_outcome = 'pass' }
Assert-CommandManifest $manifest "$($state.identities.base.sha)..HEAD"
if (Test-Path -LiteralPath $manifestPath -PathType Leaf) { throw 'MERGED_MANIFEST_APPEARED_DURING_RUN' }; [IO.File]::WriteAllText($manifestPath,($manifest | ConvertTo-Json -Depth 30),[Text.UTF8Encoding]::new($false)); $manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
$clean = @(git -C $checkout status --porcelain=v1 --untracked-files=all); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $clean.Count -ne 0) { throw 'MERGED_CHECKOUT_NOT_CLEAN' }
git -C $root worktree remove $checkout; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MERGED_CLEAN_CHECKOUT_REMOVE_FAILED' }
$state.local_verification.merged_main = [ordered]@{ manifest = $manifestPath.Replace('\','/'); manifest_sha256 = $manifestHash; requirements_sha256 = $reqHash; command_count = $records.Count }; Save-State $state
~~~

Expected: the exact squash tree passes the complete local gate, all six
commands and hashes are in the manifest, no workflow file is in the subject,
and only the successful clean temporary checkout is removed. Task 5 must
persist each tracker mutation's immutable `authorized_at` receipt before its
remote write; an expired-lease read-only resume validates that historical
receipt without treating this merged-main prerequisite as a mutation gate.

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

The approval and lease hashes/window are immutable historical identity. This
fence does not require the closure/resume clock to remain in that window; each
effect-absent mutation in 5.2 performs its own final active lease reload.

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
if ($state.identities.base.ref -ne 'refs/heads/main' -or $state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or (@($state.identities.base.parents) -join ',') -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -or $state.identities.beta1.ref -ne 'refs/heads/codex/gwo-v8-beta1' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.integration.tree -ne '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577') { throw 'FROZEN_IDENTITY_INVALID' }
$fullIdentityValid = $root -eq $state.coordinator_root -and (@($state.identities.beta1.parents) -join ',') -eq '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -and (@($state.identities.integration.parents) -join ',') -eq 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875' -and $state.identities.protected_ga.ref -eq 'refs/heads/codex/gwo-v8-ga-plan' -and (@($state.identities.protected_ga.parents) -join ',') -eq '3b7097213ac482b3a9dcc31320e7bd84191bf2c0' -and $state.identities.boundaries.implementation -eq 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2' -and $state.identities.boundaries.beta1 -eq 'ddc1785f84b6a82a7b5c34d5928b046d4e9a781d'; if (-not $fullIdentityValid) { throw 'FULL_FROZEN_IDENTITY_INVALID' }
$approvalPath = Join-Path $evidence 'approvals/tracker-owner.json'; if (-not (Test-Path -LiteralPath $approvalPath -PathType Leaf)) { throw 'TRACKER_APPROVAL_MISSING' }
$leasePath = Join-Path $evidence 'approvals/tracker-lease.json'; if (-not (Test-Path -LiteralPath $leasePath -PathType Leaf)) { throw 'TRACKER_LEASE_MISSING' }
$approval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $lease = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json; $approvalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant()
$expectedMutationSet = @('milestone:#113=GWO V8 Beta2','milestone:#114=GWO V8 Beta2','milestone:#115=GWO V8 Beta2','milestone:#116=GWO V8 Beta2','milestone:#117=GWO V8 Beta2','milestone:#137=GWO V8 Beta2','milestone:#118=GWO V8 Beta3','milestone:#119=GWO V8 GA','reopen:#137=when_closed_and_#114_or_#115_open'); $actualMutationSet = @($approval.mutation_set | ForEach-Object { [string]$_ }); if ($approval.schema -ne 'gwo-v8-c1-tracker-owner-approval.v1' -or $approval.approved -ne $true -or $approval.repository -ne $state.repository -or $approval.merged_main_sha -ne $state.pr.merge.merge_sha -or [string]::IsNullOrWhiteSpace([string]$approval.owner) -or [string]::IsNullOrWhiteSpace([string]$approval.tracker_lease_id) -or [string]::IsNullOrWhiteSpace([string]$approval.before_snapshot_sha256) -or @(Compare-Object ($expectedMutationSet | Sort-Object) ($actualMutationSet | Sort-Object)).Count -ne 0) { throw 'TRACKER_APPROVAL_INVALID' }
$leaseUntil = [DateTime]::Parse([string]$lease.valid_until).ToUniversalTime(); $leaseFrom = [DateTime]::Parse([string]$lease.valid_from).ToUniversalTime(); if ($lease.schema -ne 'gwo-v8-c1-tracker-lease.v1' -or $lease.state -ne 'active' -or $lease.id -ne $approval.tracker_lease_id -or $lease.owner -ne $approval.owner -or $lease.repository -ne $state.repository -or $lease.merged_main_sha -ne $state.pr.merge.merge_sha -or @(Compare-Object ($expectedMutationSet | Sort-Object) (@($lease.mutation_set | ForEach-Object { [string]$_ } | Sort-Object))).Count -ne 0 -or $lease.approval_sha256 -ne $approvalHash -or $leaseFrom -ge $leaseUntil) { throw 'TRACKER_LEASE_INVALID' }
if ($null -ne $state.approvals.tracker -and ($state.approvals.tracker.approval_sha256 -ne $approvalHash -or $state.approvals.tracker.lease_sha256 -ne $leaseHash)) { throw 'TRACKER_APPROVAL_RESUME_CONFLICT' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
$repo = $state.repository
function Optional-Collection([string]$endpoint,[string]$name) { $raw = @(gh api $endpoint 2>&1); $code = $LASTEXITCODE; if ($code -eq 0) { try { return @(($raw -join [Environment]::NewLine) | ConvertFrom-Json) } catch { throw "${name}_MALFORMED" } }; if (($raw -join [Environment]::NewLine) -match '404') { return @() }; throw "${name}_READ_FAILED" }
function Issue-Record([int]$number) { $raw = @(gh api "repos/$repo/issues/$number" 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw "ISSUE_READ_FAILED:$number" }; $issue = ($raw -join [Environment]::NewLine) | ConvertFrom-Json; $commentPages = @(gh api --paginate --slurp "repos/$repo/issues/$number/comments?per_page=100" 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw "COMMENTS_READ_FAILED:$number" }; $commentArrays = ($commentPages -join [Environment]::NewLine) | ConvertFrom-Json; $comments = @($commentArrays | ForEach-Object { $_ } | ForEach-Object { [ordered]@{ id = $_.id; user = $_.user.login; body = $_.body; created_at = $_.created_at; updated_at = $_.updated_at; html_url = $_.html_url } }); $blockedBy = Optional-Collection "repos/$repo/issues/$number/dependencies/blocked_by" "BLOCKED_BY_$number"; $blocking = Optional-Collection "repos/$repo/issues/$number/dependencies/blocking" "BLOCKING_$number"; return [ordered]@{ number = [int]$issue.number; state = [string]$issue.state; title = [string]$issue.title; body = [string]$issue.body; html_url = [string]$issue.html_url; url = [string]$issue.url; labels = @($issue.labels | ForEach-Object { [ordered]@{ id = $_.id; name = $_.name; color = $_.color; description = $_.description } }); comments = $comments; comments_count = $comments.Count; milestone = if ($null -eq $issue.milestone) { $null } else { [ordered]@{ id = $issue.milestone.id; number = $issue.milestone.number; title = $issue.milestone.title; state = $issue.milestone.state } }; native_blockers = [ordered]@{ blocked_by = @($blockedBy | ForEach-Object { [ordered]@{ id = $_.id; number = $_.number; state = $_.state; html_url = $_.html_url } }); blocking = @($blocking | ForEach-Object { [ordered]@{ id = $_.id; number = $_.number; state = $_.state; html_url = $_.html_url } }) } } }
$snapshotPath = Join-Path $evidence 'tracker-before.json'
if (Test-Path -LiteralPath $snapshotPath -PathType Leaf) {
    $beforeHash = (Get-FileHash -LiteralPath $snapshotPath -Algorithm SHA256).Hash.ToLowerInvariant(); if ($beforeHash -ne $approval.before_snapshot_sha256 -or $state.tracker.before_snapshot_sha256 -ne $beforeHash -or $state.approvals.tracker.approval_sha256 -ne $approvalHash -or $state.approvals.tracker.lease_sha256 -ne $leaseHash) { throw 'TRACKER_SNAPSHOT_RESUME_BINDING_INVALID' }
    $frozenBefore = Get-Content -Raw -LiteralPath $snapshotPath | ConvertFrom-Json; if ($frozenBefore.schema -ne 'gwo-v8-c1-tracker-snapshot.v2' -or $frozenBefore.repository -ne $repo -or $frozenBefore.merged_main_sha -ne $state.pr.merge.merge_sha) { throw 'TRACKER_SNAPSHOT_RESUME_IDENTITY_INVALID' }
    Write-Output 'TRACKER_EXPIRED-LEASE_READ-ONLY_RESUME'; exit 0
}
$items = @(); foreach ($number in 113,114,115,116,117,118,119,137) { $items += Issue-Record ([int]$number) }
$milestonesRaw = @(gh api "repos/$repo/milestones?state=all&per_page=100" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MILESTONE_READ_FAILED' }; $milestoneObjects = ($milestonesRaw -join [Environment]::NewLine) | ConvertFrom-Json; $milestoneRecords = @($milestoneObjects | ForEach-Object { [ordered]@{ id = $_.id; number = $_.number; title = $_.title; state = $_.state; description = $_.description; open_issues = $_.open_issues; closed_issues = $_.closed_issues; html_url = $_.html_url } })
$before = [ordered]@{ schema = 'gwo-v8-c1-tracker-snapshot.v2'; repository = $repo; merged_main_sha = $state.pr.merge.merge_sha; issue_numbers = @(113,114,115,116,117,118,119,137); issues = $items; milestones = $milestoneRecords }
$beforeText = $before | ConvertTo-Json -Depth 50; [IO.File]::WriteAllText($snapshotPath,$beforeText,[Text.UTF8Encoding]::new($false)); $beforeHash = (Get-FileHash -LiteralPath $snapshotPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($beforeHash -ne $approval.before_snapshot_sha256) { throw 'TRACKER_SNAPSHOT_DIGEST_INVALID' }
$state.approvals.tracker = [ordered]@{ approval_path = $approvalPath.Replace('\','/'); approval_sha256 = $approvalHash; lease_path = $leasePath.Replace('\','/'); lease_sha256 = $leaseHash; owner = [string]$approval.owner; lease_id = [string]$lease.id; valid_from = $leaseFrom.ToString('o'); valid_until = $leaseUntil.ToString('o'); action_scope = @($actualMutationSet | Sort-Object) }
$state.tracker = [ordered]@{ before_snapshot = $snapshotPath.Replace('\','/'); before_schema = $before.schema; before_snapshot_sha256 = $beforeHash; tracker_lease_id = $approval.tracker_lease_id; merged_main_sha = $state.pr.merge.merge_sha; mutation_set = @($actualMutationSet | Sort-Object); before_issue_numbers = @(113,114,115,116,117,118,119,137); after_snapshot = $null; after_schema = $null; after_snapshot_sha256 = $null; effects = @(); after_captured_at = $null; mutation_set_verified = $false }; Save-State $state; $check = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($check.approvals.tracker.approval_sha256 -ne $approvalHash -or $check.approvals.tracker.lease_sha256 -ne $leaseHash -or $check.tracker.before_snapshot_sha256 -ne $beforeHash) { throw 'TRACKER_APPROVAL_STATE_READBACK_INVALID' }
~~~

Expected: full issue JSON, URLs, bodies, labels, comments, states,
milestones, and native blockers are preserved; conflicts stop before any
mutation.

- [ ] **5.2 Apply only approved idempotent milestone effects with policy and
  immediate semantic readback.**

For every missing milestone, save new policy responses for
actions/permissions, actions/workflows, and rulesets/20160628, parse the
disabled/zero-workflow/active-ruleset/squash semantics, then POST only the
missing named milestone. Check LASTEXITCODE immediately. For every issue
assignment, use the tracker writer lease as the serialization boundary,
re-read the complete issue semantics immediately before PATCH, check the exit
code, and read back the complete semantics immediately after. GitHub Issue
PATCH has no endpoint-specific conditional-request CAS; stop rather than overwrite
any semantic or milestone conflict observed by the immediate preflight.

An expired-lease read-only resume is allowed only when the frozen before
snapshot proves the write was required and the already-visible effect has its
exact immutable authorization and policy hashes. If the effect is absent, the
branch reloads a fresh active lease, persists a new authorization first, and
only then performs the write; it never retroactively blesses an effect.

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
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
if ($state.identities.base.ref -ne 'refs/heads/main' -or $state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577') { throw 'FROZEN_IDENTITY_INVALID' }
$fullIdentityValid = $root -eq $state.coordinator_root -and (@($state.identities.base.parents) -join ',') -eq 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -and $state.identities.beta1.ref -eq 'refs/heads/codex/gwo-v8-beta1' -and (@($state.identities.beta1.parents) -join ',') -eq '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -and $state.identities.integration.tree -eq '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -and (@($state.identities.integration.parents) -join ',') -eq 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875' -and $state.identities.protected_ga.ref -eq 'refs/heads/codex/gwo-v8-ga-plan' -and (@($state.identities.protected_ga.parents) -join ',') -eq '3b7097213ac482b3a9dcc31320e7bd84191bf2c0' -and $state.identities.boundaries.implementation -eq 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2' -and $state.identities.boundaries.beta1 -eq 'ddc1785f84b6a82a7b5c34d5928b046d4e9a781d'; if (-not $fullIdentityValid) { throw 'FULL_FROZEN_IDENTITY_INVALID' }
$repo = $state.repository; if ($state.tracker.merged_main_sha -ne $state.pr.merge.merge_sha) { throw 'TRACKER_SHA_INVALID' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
$approvalPath = Join-Path $evidence 'approvals/tracker-owner.json'; $leasePath = Join-Path $evidence 'approvals/tracker-lease.json'; $approval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $lease = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json; $approvalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseFrom = [DateTime]::Parse([string]$lease.valid_from).ToUniversalTime(); $leaseUntil = [DateTime]::Parse([string]$lease.valid_until).ToUniversalTime(); if ($approval.schema -ne 'gwo-v8-c1-tracker-owner-approval.v1' -or $approval.approved -ne $true -or $approval.merged_main_sha -ne $state.pr.merge.merge_sha -or $lease.schema -ne 'gwo-v8-c1-tracker-lease.v1' -or $lease.id -ne $approval.tracker_lease_id -or $lease.state -ne 'active' -or $lease.approval_sha256 -ne $approvalHash -or $state.approvals.tracker.approval_sha256 -ne $approvalHash -or $state.approvals.tracker.lease_sha256 -ne $leaseHash -or $leaseFrom -ge $leaseUntil) { throw 'TRACKER_HISTORICAL_LEASE_RELOAD_INVALID' }
$expectedMutationSet = @('milestone:#113=GWO V8 Beta2','milestone:#114=GWO V8 Beta2','milestone:#115=GWO V8 Beta2','milestone:#116=GWO V8 Beta2','milestone:#117=GWO V8 Beta2','milestone:#137=GWO V8 Beta2','milestone:#118=GWO V8 Beta3','milestone:#119=GWO V8 GA','reopen:#137=when_closed_and_#114_or_#115_open'); if (@(Compare-Object ($expectedMutationSet | Sort-Object) (@($approval.mutation_set | ForEach-Object { [string]$_ } | Sort-Object))).Count -ne 0) { throw 'TRACKER_MUTATION_SET_CHANGED' }
$historicalLeaseScope = @($lease.mutation_set | ForEach-Object { [string]$_ } | Sort-Object); $savedTrackerApproval = $state.approvals.tracker; if ($approval.repository -ne $repo -or $approval.owner -ne $lease.owner -or $lease.repository -ne $repo -or $lease.merged_main_sha -ne $state.pr.merge.merge_sha -or @(Compare-Object ($expectedMutationSet | Sort-Object) $historicalLeaseScope).Count -ne 0 -or $savedTrackerApproval.owner -ne $approval.owner -or $savedTrackerApproval.lease_id -ne $lease.id -or [DateTime]::Parse([string]$savedTrackerApproval.valid_from).ToUniversalTime() -ne $leaseFrom -or [DateTime]::Parse([string]$savedTrackerApproval.valid_until).ToUniversalTime() -ne $leaseUntil) { throw 'TRACKER_HISTORICAL_LEASE_BINDING_INVALID' }
function Assert-TrackerLease {
    $approvalHashNow = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $leaseHashNow = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant()
    try { $approvalNow = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $leaseNow = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json; $from = [DateTime]::Parse([string]$leaseNow.valid_from).ToUniversalTime(); $until = [DateTime]::Parse([string]$leaseNow.valid_until).ToUniversalTime() } catch { throw 'TRACKER_LEASE_IMMEDIATE_RELOAD_INVALID' }
    $approvalScopeDiff = @(Compare-Object ($expectedMutationSet | Sort-Object) (@($approvalNow.mutation_set | ForEach-Object { [string]$_ } | Sort-Object)))
    $leaseScopeDiff = @(Compare-Object ($expectedMutationSet | Sort-Object) (@($leaseNow.mutation_set | ForEach-Object { [string]$_ } | Sort-Object)))
    if ($approvalHashNow -ne $approvalHash -or $leaseHashNow -ne $leaseHash -or $approvalNow.schema -ne 'gwo-v8-c1-tracker-owner-approval.v1' -or $approvalNow.approved -ne $true -or $approvalNow.repository -ne $repo -or $approvalNow.merged_main_sha -ne $state.pr.merge.merge_sha -or $approvalScopeDiff.Count -ne 0 -or $leaseNow.schema -ne 'gwo-v8-c1-tracker-lease.v1' -or $leaseNow.state -ne 'active' -or $leaseNow.id -ne $approvalNow.tracker_lease_id -or $leaseNow.owner -ne $approvalNow.owner -or $leaseNow.repository -ne $repo -or $leaseNow.merged_main_sha -ne $state.pr.merge.merge_sha -or $leaseNow.approval_sha256 -ne $approvalHashNow -or $leaseScopeDiff.Count -ne 0 -or [DateTime]::UtcNow -lt $from -or [DateTime]::UtcNow -ge $until) { throw 'TRACKER_LEASE_IMMEDIATE_RELOAD_INVALID' }
    return [ordered]@{ owner = [string]$approvalNow.owner; lease_id = [string]$leaseNow.id; approval_sha256 = $approvalHashNow; lease_sha256 = $leaseHashNow; valid_from = $from.ToString('o'); valid_until = $until.ToString('o') }
}
function New-PolicyReceipt([string]$label,[string]$directory,[object]$actionsObject,[object]$workflowsObject,[object]$rulesetObject,[object]$repositoryObject) {
    $files = [ordered]@{}
    foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) { $path = Join-Path $directory $name; $files[$name] = [ordered]@{ path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() } }
    $value = [ordered]@{ schema = 'gwo-v8-c1-mutation-policy.v1'; label = $label; repository = $repositoryObject.full_name; default_branch = $repositoryObject.default_branch; actions_enabled = $actionsObject.enabled; workflow_count = $workflowsObject.total_count; ruleset_id = $rulesetObject.id; files = $files }
    $path = Join-Path $directory 'receipt.json'; $text = $value | ConvertTo-Json -Depth 30
    if (Test-Path -LiteralPath $path -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $path) -ne $text) { throw 'POLICY_RECEIPT_CONFLICT' } } else { [IO.File]::WriteAllText($path,$text,[Text.UTF8Encoding]::new($false)) }
    $policyReceipt = [ordered]@{ label = $label; path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }; return $policyReceipt
}
function Assert-TrackerPolicyAttempt([object]$receipt,[string]$effectKey) {
    try {
        $labelByEffect = @{
            'milestone:GWO V8 Beta2' = ('tracker-' + 'create-GWO-V8-Beta2')
            'milestone:GWO V8 Beta3' = ('tracker-' + 'create-GWO-V8-Beta3')
            'milestone:GWO V8 GA' = ('tracker-' + 'create-GWO-V8-GA')
            'issue:113:milestone' = ('tracker-' + 'issue-113')
            'issue:114:milestone' = ('tracker-' + 'issue-114')
            'issue:115:milestone' = ('tracker-' + 'issue-115')
            'issue:116:milestone' = ('tracker-' + 'issue-116')
            'issue:117:milestone' = ('tracker-' + 'issue-117')
            'issue:118:milestone' = ('tracker-' + 'issue-118')
            'issue:119:milestone' = ('tracker-' + 'issue-119')
            'issue:137:milestone' = ('tracker-' + 'issue-137')
            'issue:137:reopen' = ('tracker-' + 'reopen-137')
        }
        $expectedLabel = $labelByEffect[[string]$effectKey]
        if ($null -eq $receipt -or [string]::IsNullOrWhiteSpace([string]$effectKey) -or $receipt.effect_key -ne $effectKey -or [string]::IsNullOrWhiteSpace([string]$expectedLabel) -or ([string]$receipt.label -cne [string]$expectedLabel) -or [string]::IsNullOrWhiteSpace([string]$receipt.path) -or [string]::IsNullOrWhiteSpace([string]$receipt.sha256)) { throw "TRACKER_POLICY_ATTEMPT_INVALID:$effectKey" }
        $receiptPath = ([IO.Path]::GetFullPath([string]$receipt.path)).Replace([char]92,'/')
        if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) { throw "TRACKER_POLICY_ATTEMPT_INVALID:$effectKey" }
        if ((Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$receipt.sha256) { throw "TRACKER_POLICY_ATTEMPT_INVALID:$effectKey" }
        $value = Get-Content -Raw -LiteralPath $receiptPath | ConvertFrom-Json
        if ($null -eq $value -or $value.schema -ne 'gwo-v8-c1-mutation-policy.v1' -or $value.label -ne [string]$receipt.label -or $value.repository -ne $repo -or $value.default_branch -ne 'main' -or $null -eq $value.PSObject.Properties['files'] -or @($value.files.PSObject.Properties).Count -ne 4) { throw "TRACKER_POLICY_ATTEMPT_INVALID:$effectKey" }
        $directory = Split-Path -Path $receiptPath -Parent; $values = [ordered]@{}
        foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) {
            $property = $value.files.PSObject.Properties[$name]; if ($null -eq $property -or $null -eq $property.Value) { throw "TRACKER_POLICY_ATTEMPT_INVALID:$effectKey" }
            $item = $property.Value; if ($null -eq $item.PSObject.Properties['path'] -or $null -eq $item.PSObject.Properties['sha256'] -or [string]::IsNullOrWhiteSpace([string]$item.path) -or [string]::IsNullOrWhiteSpace([string]$item.sha256)) { throw "TRACKER_POLICY_ATTEMPT_INVALID:$effectKey" }
            $expectedPath = ([IO.Path]::GetFullPath((Join-Path $directory $name))).Replace([char]92,'/'); $actualPath = ([IO.Path]::GetFullPath([string]$item.path)).Replace([char]92,'/')
            if ($actualPath -cne $expectedPath -or -not (Test-Path -LiteralPath $actualPath -PathType Leaf)) { throw "TRACKER_POLICY_ATTEMPT_INVALID:$effectKey" }
            if ((Get-FileHash -LiteralPath $actualPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$item.sha256) { throw "TRACKER_POLICY_ATTEMPT_INVALID:$effectKey" }
            try { $values[$name] = Get-Content -Raw -LiteralPath $actualPath | ConvertFrom-Json } catch { throw "TRACKER_POLICY_ATTEMPT_INVALID:$effectKey" }
        }
        $a = $values['actions.json']; $w = $values['workflows.json']; $r = $values['ruleset.json']; $repository = $values['repository.json']; $types = @($r.rules | ForEach-Object { [string]$_.type })
        $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }
        $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }
        if ($repository.full_name -ne $repo -or $repository.default_branch -ne 'main' -or $value.repository -ne $repository.full_name -or $value.default_branch -ne $repository.default_branch -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $value.actions_enabled -ne $a.enabled -or $value.workflow_count -ne $w.total_count -or $r.id -ne 20160628 -or $value.ruleset_id -ne $r.id -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0 -or @(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0 -or $allowed -notcontains 'squash' -or (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main'))) { throw "TRACKER_POLICY_ATTEMPT_INVALID:$effectKey" }
    } catch {
        throw "TRACKER_POLICY_ATTEMPT_INVALID:$effectKey"
    }
    return [ordered]@{ effect_key = $effectKey; label = [string]$receipt.label; path = $receiptPath; sha256 = [string]$receipt.sha256 }
}
function Assert-TrackerCurrentPolicy([string]$failure) {
    $liveActions = @(gh api repos/$repo/actions/permissions 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'TRACKER_CURRENT_POLICY_ACTIONS_READ_FAILED' }
    $liveWorkflows = @(gh api repos/$repo/actions/workflows 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'TRACKER_CURRENT_POLICY_WORKFLOWS_READ_FAILED' }
    $liveRuleset = @(gh api repos/$repo/rulesets/20160628 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'TRACKER_CURRENT_POLICY_RULESET_READ_FAILED' }
    $liveRepository = @(gh api repos/$repo 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'TRACKER_CURRENT_POLICY_REPOSITORY_READ_FAILED' }
    $a = ($liveActions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($liveWorkflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($liveRuleset -join [Environment]::NewLine) | ConvertFrom-Json; $repoObject = ($liveRepository -join [Environment]::NewLine) | ConvertFrom-Json
    $types = @($r.rules | ForEach-Object type); $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }
    if ($repoObject.full_name -ne $repo -or $repoObject.default_branch -ne 'main' -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0 -or @(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0 -or $allowed -notcontains 'squash' -or (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main'))) { throw $failure }
}
function Read-Policy([string]$label,[string]$effectKey) {
    $canonicalPolicies = @($state.mutation_policy.tracker | Where-Object effect_key -eq $effectKey); if ($canonicalPolicies.Count -gt 1) { throw "TRACKER_POLICY_DUPLICATE:$effectKey" }; $canonicalPolicy = if ($canonicalPolicies.Count -eq 1) { $canonicalPolicies[0] } else { $null }
    $canonicalAuth = @($state.mutation_authorization.tracker | Where-Object effect_key -eq $effectKey); if ($canonicalAuth.Count -gt 1) { throw "TRACKER_AUTHORIZATION_DUPLICATE:$effectKey" }
    $orphanDirectory = Join-Path $evidence 'tracker-authorizations'; $safe = $effectKey -replace '[^A-Za-z0-9.-]','-'; $orphanPaths = @(); if (Test-Path -LiteralPath $orphanDirectory -PathType Container) { $orphanPaths = @(Get-ChildItem -LiteralPath $orphanDirectory -Filter "$safe-*.json" -File | Sort-Object FullName | Where-Object { $canonicalAuth.Count -eq 0 -or $_.FullName.Replace([char]92,'/') -ne $canonicalAuth[0].authorization_path }) }; if ($orphanPaths.Count -gt 1 -or ($canonicalAuth.Count -eq 1 -and $orphanPaths.Count -gt 0)) { throw "TRACKER_AUTHORIZATION_ORPHAN_CONFLICT:$effectKey" }
    if ($canonicalAuth.Count -eq 1) {
        $authorization = Get-TrackerAuthorization $effectKey $true
        if ($null -eq $canonicalPolicy) { $policyReceipt = Assert-TrackerPolicyAttempt ([ordered]@{ effect_key = $effectKey; label = [string]$authorization.policy.label; path = $authorization.policy.path; sha256 = $authorization.policy.sha256 }) $effectKey; $state.mutation_policy.tracker = @($state.mutation_policy.tracker) + @($policyReceipt); Save-State $state } else { $policyReceipt = Assert-TrackerPolicyAttempt $canonicalPolicy $effectKey; if ($authorization.policy.path -ne $policyReceipt.path -or $authorization.policy.sha256 -ne $policyReceipt.sha256) { throw "TRACKER_POLICY_AUTHORIZATION_MISMATCH:$effectKey" } }
        $null = Assert-TrackerCurrentPolicy "TRACKER_CURRENT_POLICY_INVALID:$effectKey"; return $policyReceipt
    } elseif ($orphanPaths.Count -eq 1) {
        $authorization = Get-TrackerAuthorization $effectKey $true $orphanPaths[0].FullName
        if ($null -ne $canonicalPolicy) { $policyReceipt = Assert-TrackerPolicyAttempt $canonicalPolicy $effectKey; if ($authorization.policy.path -ne $policyReceipt.path -or $authorization.policy.sha256 -ne $policyReceipt.sha256) { throw "TRACKER_AUTHORIZATION_ORPHAN_POLICY_CONFLICT:$effectKey" } } else { $policyReceipt = Assert-TrackerPolicyAttempt ([ordered]@{ effect_key = $effectKey; label = [string]$authorization.policy.label; path = $authorization.policy.path; sha256 = $authorization.policy.sha256 }) $effectKey }
        $null = Assert-TrackerCurrentPolicy "TRACKER_CURRENT_POLICY_INVALID:$effectKey"; $state.mutation_authorization.tracker = @($state.mutation_authorization.tracker | Where-Object effect_key -ne $effectKey) + @($authorization.reference); if ($null -eq $canonicalPolicy) { $state.mutation_policy.tracker = @($state.mutation_policy.tracker) + @($policyReceipt) }; Save-State $state; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; return $policyReceipt
    } elseif ($null -ne $canonicalPolicy) {
        $policyReceipt = Assert-TrackerPolicyAttempt $canonicalPolicy $effectKey; $null = Assert-TrackerCurrentPolicy "TRACKER_CURRENT_POLICY_INVALID:$effectKey"; return $policyReceipt
    }
    $null = Assert-TrackerLease
    $dir = Join-Path $evidence ('policy-before-tracker-' + $label + '-' + [guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $dir -ErrorAction Stop | Out-Null
    $actions = @(gh api repos/$repo/actions/permissions 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'ACTIONS_READBACK_FAILED' }
    $workflows = @(gh api repos/$repo/actions/workflows 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'WORKFLOW_READBACK_FAILED' }
    $ruleset = @(gh api repos/$repo/rulesets/20160628 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'RULESET_READBACK_FAILED' }
    $repoRaw = @(gh api repos/$repo 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'REPOSITORY_READ_FAILED' }
    foreach ($item in @(@('actions.json',$actions),@('workflows.json',$workflows),@('ruleset.json',$ruleset),@('repository.json',$repoRaw))) { [IO.File]::WriteAllText((Join-Path $dir $item[0]),($item[1] -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)) }
    $a = ($actions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($workflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($ruleset -join [Environment]::NewLine) | ConvertFrom-Json; $repoObject = ($repoRaw -join [Environment]::NewLine) | ConvertFrom-Json
    if ($repoObject.full_name -ne $repo -or $repoObject.default_branch -ne 'main' -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0) { throw 'TRACKER_POLICY_INVALID' }
    $types = @($r.rules | ForEach-Object type); if (@(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0) { throw 'TRACKER_RULESET_INVALID' }
    $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }; if ($allowed -notcontains 'squash') { throw 'TRACKER_SQUASH_POLICY_INVALID' }
    $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; if (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main')) { throw 'TRACKER_RULESET_DEFAULT_BRANCH_INVALID' }
    $receipt = New-PolicyReceipt ('tracker-' + $label) $dir $a $w $r $repoObject; $receipt['effect_key'] = $effectKey
    $otherPolicies = @($state.mutation_policy.tracker | Where-Object { $_.effect_key -ne $effectKey }); $state.mutation_policy.tracker = @($otherPolicies) + @($receipt); Save-State $state
    $saved = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; $savedReceipts = @($saved.mutation_policy.tracker | Where-Object { $_.effect_key -eq $effectKey }); if ($savedReceipts.Count -ne 1 -or $savedReceipts[0].sha256 -ne $receipt.sha256) { throw 'TRACKER_POLICY_STATE_READBACK_INVALID' }
    return $receipt
}
function Optional-Collection([string]$endpoint,[string]$name) { $raw = @(gh api $endpoint 2>&1); $code = $LASTEXITCODE; if ($code -eq 0) { try { return @(($raw -join [Environment]::NewLine) | ConvertFrom-Json) } catch { throw "${name}_MALFORMED" } }; if (($raw -join [Environment]::NewLine) -match '404') { return @() }; throw "${name}_READ_FAILED" }
function Semantic([object]$json,[int]$number) { $commentPages = @(gh api --paginate --slurp "repos/$repo/issues/$number/comments?per_page=100" 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw "COMMENTS_READ_FAILED:$number" }; $commentArrays = ($commentPages -join [Environment]::NewLine) | ConvertFrom-Json; $comments = @($commentArrays | ForEach-Object { $_ } | ForEach-Object { [ordered]@{ id = $_.id; user = $_.user.login; body = $_.body; created_at = $_.created_at; updated_at = $_.updated_at; html_url = $_.html_url } }); $blockedBy = Optional-Collection "repos/$repo/issues/$number/dependencies/blocked_by" "BLOCKED_BY_$number"; $blocking = Optional-Collection "repos/$repo/issues/$number/dependencies/blocking" "BLOCKING_$number"; $milestone = if ($null -eq $json.milestone) { $null } else { [ordered]@{ id = $json.milestone.id; number = $json.milestone.number; title = $json.milestone.title; state = $json.milestone.state } }; return [ordered]@{ number = [int]$json.number; state = [string]$json.state; title = [string]$json.title; body = [string]$json.body; html_url = [string]$json.html_url; url = [string]$json.url; labels = @($json.labels | ForEach-Object { [ordered]@{ id = $_.id; name = $_.name; color = $_.color; description = $_.description } }); comments = $comments; comments_count = $comments.Count; milestone = $milestone; native_blockers = [ordered]@{ blocked_by = @($blockedBy | ForEach-Object { [ordered]@{ id = $_.id; number = $_.number; state = $_.state; html_url = $_.html_url } }); blocking = @($blocking | ForEach-Object { [ordered]@{ id = $_.id; number = $_.number; state = $_.state; html_url = $_.html_url } }) } } }
function Read-Issue([int]$number) { $raw = @(gh api "repos/$repo/issues/$number" 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw "ISSUE_READ_FAILED:$number" }; $json = ($raw -join [Environment]::NewLine) | ConvertFrom-Json; return [ordered]@{ json = $json; semantic = Semantic $json $number } }
function Same-Json([object]$left,[object]$right,[int]$depth) { return (($left | ConvertTo-Json -Depth $depth -Compress) -ceq ($right | ConvertTo-Json -Depth $depth -Compress)) }
function Save-Effect([string]$name,[object]$value) {
    if ($value.schema -ne 'gwo-v8-c1-tracker-effect.v2') { throw "TRACKER_EFFECT_SCHEMA_INVALID:$name" }
    $path = Join-Path $evidence ('tracker-effects/' + $name + '.json'); if (-not (Test-Path -LiteralPath (Split-Path $path) -PathType Container)) { New-Item -ItemType Directory -Path (Split-Path $path) -ErrorAction Stop | Out-Null }
    $text = $value | ConvertTo-Json -Depth 50; if (Test-Path -LiteralPath $path -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $path) -ne $text) { throw "TRACKER_EFFECT_CONFLICT:$name" } } else { [IO.File]::WriteAllText($path,$text,[Text.UTF8Encoding]::new($false)) }
    return [ordered]@{ path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
}
function Get-TrackerAuthorization([string]$effectKey,[bool]$required,[string]$candidatePath=$null) {
    $matches = @($state.mutation_authorization.tracker | Where-Object { $_.effect_key -eq $effectKey }); if ($matches.Count -gt 1) { throw "TRACKER_AUTHORIZATION_DUPLICATE:$effectKey" }
    if ($null -ne $candidatePath) { $candidate = ([IO.Path]::GetFullPath($candidatePath)).Replace([char]92,'/'); if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw "TRACKER_AUTHORIZATION_ORPHAN_INVALID:$effectKey" }; $reference = [ordered]@{ effect_key = $effectKey; authorization_path = $candidate; authorization_sha256 = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant() } } elseif ($matches.Count -eq 0) { if ($required) { throw "TRACKER_EFFECT_WITHOUT_AUTHORIZATION:$effectKey" }; return $null } else { $reference = $matches[0] }
    if (-not (Test-Path -LiteralPath $reference.authorization_path -PathType Leaf)) { throw "TRACKER_AUTHORIZATION_MISSING:$effectKey" }
    $authorizationHash = (Get-FileHash -LiteralPath $reference.authorization_path -Algorithm SHA256).Hash.ToLowerInvariant(); if ($authorizationHash -ne $reference.authorization_sha256) { throw "TRACKER_AUTHORIZATION_HASH_INVALID:$effectKey" }
    try { $value = Get-Content -Raw -LiteralPath $reference.authorization_path | ConvertFrom-Json; $authorizedAt = [DateTime]::Parse([string]$value.authorized_at).ToUniversalTime(); $authFrom = [DateTime]::Parse([string]$value.valid_from).ToUniversalTime(); $authUntil = [DateTime]::Parse([string]$value.valid_until).ToUniversalTime() } catch { throw "TRACKER_AUTHORIZATION_MALFORMED:$effectKey" }
    if ($value.schema -ne 'gwo-v8-c1-mutation-authorization.v1' -or $value.operation -ne 'tracker-write' -or $value.effect_key -ne $effectKey -or $value.repository -ne $repo -or $value.merged_main_sha -ne $state.pr.merge.merge_sha -or $value.owner -ne $approval.owner -or $value.lease_id -ne $lease.id -or $value.approval_sha256 -ne $approvalHash -or $value.lease_sha256 -ne $leaseHash -or $authFrom -ne $leaseFrom -or $authUntil -ne $leaseUntil -or $authorizedAt -lt $authFrom -or $authorizedAt -ge $authUntil) { throw "TRACKER_AUTHORIZATION_INVALID:$effectKey" }
    if (-not (Test-Path -LiteralPath $value.policy_path -PathType Leaf) -or (Get-FileHash -LiteralPath $value.policy_path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $value.policy_sha256) { throw "TRACKER_AUTHORIZATION_POLICY_INVALID:$effectKey" }
    return [ordered]@{ reference = $reference; value = $value; policy = [ordered]@{ label = [string]$value.policy_label; path = [string]$value.policy_path; sha256 = [string]$value.policy_sha256 } }
}
function Save-TrackerAuthorization([string]$effectKey,[object]$policy,[object]$leaseEvidence) {
    $directory = Join-Path $evidence 'tracker-authorizations'; $safe = $effectKey -replace '[^A-Za-z0-9.-]','-'; $canonical = @($state.mutation_authorization.tracker | Where-Object effect_key -eq $effectKey); if ($canonical.Count -gt 1) { throw "TRACKER_AUTHORIZATION_DUPLICATE:$effectKey" }; $orphanPaths = @(); if (Test-Path -LiteralPath $directory -PathType Container) { $orphanPaths = @(Get-ChildItem -LiteralPath $directory -Filter "$safe-*.json" -File | Sort-Object FullName | Where-Object { $canonical.Count -eq 0 -or $_.FullName.Replace([char]92,'/') -ne $canonical[0].authorization_path }) }; if ($orphanPaths.Count -gt 1 -or ($canonical.Count -eq 1 -and $orphanPaths.Count -gt 0)) { throw "TRACKER_AUTHORIZATION_ORPHAN_CONFLICT:$effectKey" }
    if ($canonical.Count -eq 1) { $existing = Get-TrackerAuthorization $effectKey $true; if ($existing.policy.path -ne $policy.path -or $existing.policy.sha256 -ne $policy.sha256 -or $existing.value.lease_sha256 -ne $leaseEvidence.lease_sha256) { throw "TRACKER_AUTHORIZATION_IMMUTABLE_CONFLICT:$effectKey" }; return $existing }
    if ($orphanPaths.Count -eq 1) { $orphan = Get-TrackerAuthorization $effectKey $true $orphanPaths[0].FullName; if ($orphan.policy.path -ne $policy.path -or $orphan.policy.sha256 -ne $policy.sha256 -or $orphan.value.lease_sha256 -ne $leaseEvidence.lease_sha256) { throw "TRACKER_AUTHORIZATION_ORPHAN_POLICY_CONFLICT:$effectKey" }; $state.mutation_authorization.tracker = @($state.mutation_authorization.tracker) + @($orphan.reference); Save-State $state; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; return Get-TrackerAuthorization $effectKey $true }
    $authorizedAt = [DateTime]::UtcNow; $authFrom = [DateTime]::Parse([string]$leaseEvidence.valid_from).ToUniversalTime(); $authUntil = [DateTime]::Parse([string]$leaseEvidence.valid_until).ToUniversalTime(); if ($authorizedAt -lt $authFrom -or $authorizedAt -ge $authUntil) { throw "TRACKER_AUTHORIZATION_OUTSIDE_LEASE:$effectKey" }
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { New-Item -ItemType Directory -Path $directory -ErrorAction Stop | Out-Null }; $path = Join-Path $directory ("$safe-$($policy.sha256).json")
    $value = [ordered]@{ schema = 'gwo-v8-c1-mutation-authorization.v1'; operation = 'tracker-write'; effect_key = $effectKey; repository = $repo; merged_main_sha = $state.pr.merge.merge_sha; owner = [string]$leaseEvidence.owner; lease_id = [string]$leaseEvidence.lease_id; approval_sha256 = [string]$leaseEvidence.approval_sha256; lease_sha256 = [string]$leaseEvidence.lease_sha256; policy_label = [string]$policy.label; policy_path = [string]$policy.path; policy_sha256 = [string]$policy.sha256; authorized_at = $authorizedAt.ToString('o'); valid_from = $authFrom.ToString('o'); valid_until = $authUntil.ToString('o') }
    $text = $value | ConvertTo-Json -Depth 30; if (Test-Path -LiteralPath $path -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $path) -ne $text) { throw "TRACKER_AUTHORIZATION_CONFLICT:$effectKey" } } else { [IO.File]::WriteAllText($path,$text,[Text.UTF8Encoding]::new($false)) }
    $reference = [ordered]@{ effect_key = $effectKey; authorization_path = $path.Replace([char]92,'/'); authorization_sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }; $other = @($state.mutation_authorization.tracker | Where-Object { $_.effect_key -ne $effectKey }); $state.mutation_authorization.tracker = @($other) + @($reference); Save-State $state
    $saved = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; $savedRef = @($saved.mutation_authorization.tracker | Where-Object { $_.effect_key -eq $effectKey }); if ($savedRef.Count -ne 1 -or $savedRef[0].authorization_sha256 -ne $reference.authorization_sha256) { throw "TRACKER_AUTHORIZATION_STATE_READBACK_INVALID:$effectKey" }
    return Get-TrackerAuthorization $effectKey $true
}
function Milestone-Effect([object]$item) { return [ordered]@{ id = [long]$item.id; number = [int]$item.number; title = [string]$item.title; state = [string]$item.state; description = [string]$item.description; html_url = [string]$item.html_url } }
$trackerBeforePath = [string]$state.tracker.before_snapshot; if (-not (Test-Path -LiteralPath $trackerBeforePath -PathType Leaf) -or (Get-FileHash -LiteralPath $trackerBeforePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $state.tracker.before_snapshot_sha256) { throw 'TRACKER_BEFORE_SNAPSHOT_INVALID' }; $trackerBefore = Get-Content -Raw -LiteralPath $trackerBeforePath | ConvertFrom-Json; if ($trackerBefore.schema -ne 'gwo-v8-c1-tracker-snapshot.v2' -or $trackerBefore.merged_main_sha -ne $state.pr.merge.merge_sha) { throw 'TRACKER_BEFORE_IDENTITY_INVALID' }; $frozenIssues = @{}; foreach ($item in @($trackerBefore.issues)) { $frozenIssues[[int]$item.number] = $item }
$map = [ordered]@{ 'GWO V8 Beta2' = @(113,114,115,116,117,137); 'GWO V8 Beta3' = @(118); 'GWO V8 GA' = @(119) }
$milestonesRaw = @(gh api "repos/$repo/milestones?state=all&per_page=100" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MILESTONE_READ_FAILED' }; $milestones = ($milestonesRaw -join [Environment]::NewLine) | ConvertFrom-Json; $effects = @()
foreach ($title in $map.Keys) {
    $effectKey = 'milestone:' + $title; $frozenMilestone = @($trackerBefore.milestones | Where-Object title -eq $title); if ($frozenMilestone.Count -gt 1) { throw "FROZEN_MILESTONE_CONFLICT:$title" }; $writeRequired = $frozenMilestone.Count -eq 0
    $found = @($milestones | Where-Object title -eq $title); if ($found.Count -gt 1) { throw "MILESTONE_CONFLICT:$title" }; $effectMissing = $found.Count -eq 0; $authorization = $null
    if ($effectMissing) {
        if (-not $writeRequired) { throw "MILESTONE_REMOVED_AFTER_FREEZE:$title" }
        $policyReceipt = Read-Policy ('create-' + ($title -replace '\s+','-')) $effectKey
        $freshRaw = @(gh api "repos/$repo/milestones?state=all&per_page=100" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MILESTONE_PREMUTATION_READ_FAILED' }; $freshMilestones = ($freshRaw -join [Environment]::NewLine) | ConvertFrom-Json
        if (@($freshMilestones | Where-Object title -eq $title).Count -ne 0) { throw "MILESTONE_PREMUTATION_SEMANTIC_DRIFT:$title" }
        $leaseEvidence = Assert-TrackerLease; $authorization = Save-TrackerAuthorization $effectKey $policyReceipt $leaseEvidence; $finalTrackerLease = Assert-TrackerLease; $finalTrackerApprovalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $finalTrackerLeaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); $finalTrackerNow = [DateTime]::UtcNow; $finalTrackerFrom = [DateTime]::Parse([string]$finalTrackerLease.valid_from).ToUniversalTime(); $finalTrackerUntil = [DateTime]::Parse([string]$finalTrackerLease.valid_until).ToUniversalTime(); if ($finalTrackerApprovalHash -ne $approvalHash -or $finalTrackerLeaseHash -ne $leaseHash -or $finalTrackerNow -lt $finalTrackerFrom -or $finalTrackerNow -ge $finalTrackerUntil) { throw 'TRACKER_FINAL_IMMEDIATE_LEASE_INVALID' }
        $created = @(gh api -X POST "repos/$repo/milestones" -f "title=$title" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw "MILESTONE_CREATE_FAILED:$title" }
        $createdMilestone = ($created -join [Environment]::NewLine) | ConvertFrom-Json; if ($createdMilestone.title -ne $title) { throw "MILESTONE_CREATE_READBACK_FAILED:$title" }
        $milestonesRaw = @(gh api "repos/$repo/milestones?state=all&per_page=100" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MILESTONE_REFRESH_FAILED' }; $milestones = ($milestonesRaw -join [Environment]::NewLine) | ConvertFrom-Json
        $found = @($milestones | Where-Object title -eq $title); if ($found.Count -ne 1 -or $found[0].id -ne $createdMilestone.id -or $found[0].number -ne $createdMilestone.number) { throw "MILESTONE_POSTMUTATION_SEMANTIC_DRIFT:$title" }
    } elseif ($writeRequired) { $authorization = Get-TrackerAuthorization $effectKey $true; $null = Assert-TrackerPolicyAttempt ([ordered]@{ effect_key = $effectKey; label = [string]$authorization.policy.label; path = [string]$authorization.policy.path; sha256 = [string]$authorization.policy.sha256 }) $effectKey }
    $finalMilestone = @($milestones | Where-Object title -eq $title); if ($finalMilestone.Count -ne 1) { throw "MILESTONE_CANONICAL_READBACK_INVALID:$title" }
    $authorizationRef = if ($null -eq $authorization) { $null } else { $authorization.reference }; $effect = [ordered]@{ schema = 'gwo-v8-c1-tracker-effect.v2'; effect_key = $effectKey; kind = 'milestone'; repository = $repo; merged_main_sha = $state.pr.merge.merge_sha; authorization_path = if ($null -eq $authorizationRef) { $null } else { $authorizationRef.authorization_path }; authorization_sha256 = if ($null -eq $authorizationRef) { $null } else { $authorizationRef.authorization_sha256 }; milestone = Milestone-Effect $finalMilestone[0] }
    $effects += Save-Effect ('milestone-' + ($title -replace '\s+','-')) $effect
}
foreach ($title in $map.Keys) {
    $milestone = @($milestones | Where-Object title -eq $title)[0]; if ($null -eq $milestone) { throw "MILESTONE_MISSING:$title" }; $milestoneNumber = [int]$milestone.number
    foreach ($number in $map[$title]) {
        if (-not $frozenIssues.ContainsKey([int]$number)) { throw "FROZEN_ISSUE_MISSING:$number" }; $frozenIssue = $frozenIssues[[int]$number]; if ($null -ne $frozenIssue.milestone -and $frozenIssue.milestone.title -ne $title) { throw "FROZEN_MILESTONE_CONFLICT:$number" }; $writeRequired = $null -eq $frozenIssue.milestone; $effectKey = "issue:${number}:milestone"; $authorization = $null
        $before = Read-Issue ([int]$number)
        if ($null -ne $before.semantic.milestone -and $before.semantic.milestone.title -ne $title) { throw "MILESTONE_CONFLICT:$number" }
        if ($null -eq $before.semantic.milestone) {
            if (-not $writeRequired) { throw "ISSUE_MILESTONE_REMOVED_AFTER_FREEZE:$number" }
            $policyReceipt = Read-Policy ('issue-' + $number) $effectKey
            $fresh = Read-Issue ([int]$number)
            if (-not (Same-Json $fresh.semantic $before.semantic 50) -or $null -ne $fresh.semantic.milestone) { throw "ISSUE_PREMUTATION_SEMANTIC_DRIFT:$number" }
            $leaseEvidence = Assert-TrackerLease; $authorization = Save-TrackerAuthorization $effectKey $policyReceipt $leaseEvidence; $finalTrackerLease = Assert-TrackerLease; $finalTrackerApprovalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $finalTrackerLeaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); $finalTrackerNow = [DateTime]::UtcNow; $finalTrackerFrom = [DateTime]::Parse([string]$finalTrackerLease.valid_from).ToUniversalTime(); $finalTrackerUntil = [DateTime]::Parse([string]$finalTrackerLease.valid_until).ToUniversalTime(); if ($finalTrackerApprovalHash -ne $approvalHash -or $finalTrackerLeaseHash -ne $leaseHash -or $finalTrackerNow -lt $finalTrackerFrom -or $finalTrackerNow -ge $finalTrackerUntil) { throw 'TRACKER_FINAL_IMMEDIATE_LEASE_INVALID' }
            $result = @(gh api -X PATCH "repos/$repo/issues/$number" -F "milestone=$milestoneNumber" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw "MILESTONE_PATCH_FAILED:$number" }
            $final = Read-Issue ([int]$number)
            $preserved = $final.semantic.state -eq $fresh.semantic.state -and $final.semantic.title -eq $fresh.semantic.title -and $final.semantic.body -eq $fresh.semantic.body -and $final.semantic.html_url -eq $fresh.semantic.html_url -and $final.semantic.url -eq $fresh.semantic.url -and (Same-Json $final.semantic.labels $fresh.semantic.labels 30) -and (Same-Json $final.semantic.comments $fresh.semantic.comments 50) -and (Same-Json $final.semantic.native_blockers $fresh.semantic.native_blockers 50)
            if (-not $preserved -or $final.semantic.milestone.title -ne $title -or [int]$final.semantic.milestone.number -ne $milestoneNumber) { throw "ISSUE_POSTMUTATION_SEMANTIC_DRIFT:$number" }
        } else { $final = $before; if ($writeRequired) { $authorization = Get-TrackerAuthorization $effectKey $true; $null = Assert-TrackerPolicyAttempt ([ordered]@{ effect_key = $effectKey; label = [string]$authorization.policy.label; path = [string]$authorization.policy.path; sha256 = [string]$authorization.policy.sha256 }) $effectKey } }
        $authorizationRef = if ($null -eq $authorization) { $null } else { $authorization.reference }; $effect = [ordered]@{ schema = 'gwo-v8-c1-tracker-effect.v2'; effect_key = $effectKey; kind = 'issue-milestone'; repository = $repo; merged_main_sha = $state.pr.merge.merge_sha; number = [int]$number; issue_url = [string]$final.semantic.html_url; authorization_path = if ($null -eq $authorizationRef) { $null } else { $authorizationRef.authorization_path }; authorization_sha256 = if ($null -eq $authorizationRef) { $null } else { $authorizationRef.authorization_sha256 }; milestone = [ordered]@{ id = [long]$milestone.id; number = $milestoneNumber; title = $title } }
        $effects += Save-Effect ('issue-' + $number) $effect
    }
}
$issue137 = Read-Issue 137; $issue114 = Read-Issue 114; $issue115 = Read-Issue 115; $reopenAllowed = $expectedMutationSet -contains 'reopen:#137=when_closed_and_#114_or_#115_open'; $reopenRequired = $frozenIssues[137].state -eq 'closed' -and ($frozenIssues[114].state -eq 'open' -or $frozenIssues[115].state -eq 'open'); $reopenEffectKey = 'issue:137:reopen'; $reopenAuthorization = $null
if ($reopenRequired -and $issue137.semantic.state -eq 'closed') {
    if (-not $reopenAllowed -or ($issue114.semantic.state -ne 'open' -and $issue115.semantic.state -ne 'open')) { throw 'REOPEN_FROZEN_CONDITION_DRIFTED' }
    $policyReceipt = Read-Policy 'reopen-137' $reopenEffectKey
    $fresh137 = Read-Issue 137; $fresh114 = Read-Issue 114; $fresh115 = Read-Issue 115
    if (-not (Same-Json $fresh137.semantic $issue137.semantic 50) -or -not (Same-Json $fresh114.semantic $issue114.semantic 50) -or -not (Same-Json $fresh115.semantic $issue115.semantic 50) -or $fresh137.semantic.state -ne 'closed' -or ($fresh114.semantic.state -ne 'open' -and $fresh115.semantic.state -ne 'open')) { throw 'ISSUE_PREMUTATION_SEMANTIC_DRIFT:137_REOPEN' }
    $leaseEvidence = Assert-TrackerLease; $reopenAuthorization = Save-TrackerAuthorization $reopenEffectKey $policyReceipt $leaseEvidence; $finalTrackerLease = Assert-TrackerLease; $finalTrackerApprovalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $finalTrackerLeaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); $finalTrackerNow = [DateTime]::UtcNow; $finalTrackerFrom = [DateTime]::Parse([string]$finalTrackerLease.valid_from).ToUniversalTime(); $finalTrackerUntil = [DateTime]::Parse([string]$finalTrackerLease.valid_until).ToUniversalTime(); if ($finalTrackerApprovalHash -ne $approvalHash -or $finalTrackerLeaseHash -ne $leaseHash -or $finalTrackerNow -lt $finalTrackerFrom -or $finalTrackerNow -ge $finalTrackerUntil) { throw 'TRACKER_FINAL_IMMEDIATE_LEASE_INVALID' }
    $reopened = @(gh api -X PATCH "repos/$repo/issues/137" -f state=open 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'ISSUE_137_REOPEN_FAILED' }
    $after137 = Read-Issue 137
    $preserved137 = $after137.semantic.title -eq $fresh137.semantic.title -and $after137.semantic.body -eq $fresh137.semantic.body -and $after137.semantic.html_url -eq $fresh137.semantic.html_url -and $after137.semantic.url -eq $fresh137.semantic.url -and (Same-Json $after137.semantic.labels $fresh137.semantic.labels 30) -and (Same-Json $after137.semantic.comments $fresh137.semantic.comments 50) -and (Same-Json $after137.semantic.milestone $fresh137.semantic.milestone 30) -and (Same-Json $after137.semantic.native_blockers $fresh137.semantic.native_blockers 50)
    if ($after137.semantic.state -ne 'open' -or -not $preserved137) { throw 'ISSUE_POSTMUTATION_SEMANTIC_DRIFT:137_REOPEN' }
} elseif ($reopenRequired -and $issue137.semantic.state -eq 'open') { $after137 = $issue137; $reopenAuthorization = Get-TrackerAuthorization $reopenEffectKey $true; $null = Assert-TrackerPolicyAttempt ([ordered]@{ effect_key = $reopenEffectKey; label = [string]$reopenAuthorization.policy.label; path = [string]$reopenAuthorization.policy.path; sha256 = [string]$reopenAuthorization.policy.sha256 }) $reopenEffectKey
} elseif (-not $reopenRequired -and $frozenIssues[137].state -eq 'open' -and $issue137.semantic.state -eq 'open') { $after137 = $issue137
} elseif ($issue137.semantic.state -ne $frozenIssues[137].state) { throw 'ISSUE_137_UNAUTHORIZED_STATE_DRIFT' }
if ($null -ne $after137 -and $after137.semantic.state -eq 'open') { $authorizationRef = if ($null -eq $reopenAuthorization) { $null } else { $reopenAuthorization.reference }; $effects += Save-Effect 'issue-137-reopen' ([ordered]@{ schema = 'gwo-v8-c1-tracker-effect.v2'; effect_key = $reopenEffectKey; kind = 'conditional-reopen'; repository = $repo; merged_main_sha = $state.pr.merge.merge_sha; number = 137; issue_url = [string]$after137.semantic.html_url; authorization_path = if ($null -eq $authorizationRef) { $null } else { $authorizationRef.authorization_path }; authorization_sha256 = if ($null -eq $authorizationRef) { $null } else { $authorizationRef.authorization_sha256 }; condition = '#137 closed and (#114 open or #115 open)'; final_state = 'open' }) }
$afterItems = @(); foreach ($number in 113,114,115,116,117,118,119,137) { $read = Read-Issue ([int]$number); $afterItems += $read.semantic }; $afterMilestonesRaw = @(gh api "repos/$repo/milestones?state=all&per_page=100" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MILESTONE_AFTER_READ_FAILED' }; $afterMilestones = (($afterMilestonesRaw -join [Environment]::NewLine) | ConvertFrom-Json | ForEach-Object { [ordered]@{ id = $_.id; number = $_.number; title = $_.title; state = $_.state; description = $_.description; open_issues = $_.open_issues; closed_issues = $_.closed_issues; html_url = $_.html_url } }); $after = [ordered]@{ schema = 'gwo-v8-c1-tracker-snapshot.v2'; repository = $repo; merged_main_sha = $state.pr.merge.merge_sha; issue_numbers = @(113,114,115,116,117,118,119,137); issues = $afterItems; milestones = $afterMilestones; effects = $effects }; $afterPath = Join-Path $evidence 'tracker-after.json'; $afterText = $after | ConvertTo-Json -Depth 50; if (Test-Path -LiteralPath $afterPath -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $afterPath) -ne $afterText) { throw 'TRACKER_AFTER_CONFLICT' } } else { [IO.File]::WriteAllText($afterPath,$afterText,[Text.UTF8Encoding]::new($false)) }; $afterHash = (Get-FileHash -LiteralPath $afterPath -Algorithm SHA256).Hash.ToLowerInvariant(); $state.tracker.after_snapshot = $afterPath.Replace('\','/'); $state.tracker.after_schema = $after.schema; $state.tracker.after_snapshot_sha256 = $afterHash; $state.tracker.effects = $effects; $state.tracker.after_captured_at = [DateTime]::UtcNow.ToString('o'); $state.tracker.mutation_set_verified = $true; Save-State $state; $check = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($check.tracker.after_snapshot_sha256 -ne $afterHash -or $check.tracker.mutation_set_verified -ne $true) { throw 'TRACKER_AFTER_STATE_READBACK_INVALID' }
~~~

Expected: only the prescribed mapping is read back, #137 obeys the approved
conditional rule, issue content/native blockers remain unchanged, and every
write is protected by an immediate lease/policy/semantic-readback sequence.

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

Freeze `initial_tag_present` and `initial_release_present` only once. Later
read-only resume re-hashes the original approval/lease and ordered historical
window without requiring it to be current; an absent tag or Release mutation
still performs a final fresh active lease reload in its own branch.

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
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
$repo = $state.repository; $repoRaw = @(gh api repos/$repo 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REPOSITORY_READ_FAILED' }; $repoObject = ($repoRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($repoObject.full_name -ne $repo -or $repoObject.default_branch -ne 'main') { throw 'DEFAULT_BRANCH_INVALID' }
if ($state.identities.base.ref -ne 'refs/heads/main' -or $state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.integration.tree -ne '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577') { throw 'FROZEN_IDENTITY_INVALID' }
$fullIdentityValid = $root -eq $state.coordinator_root -and (@($state.identities.base.parents) -join ',') -eq 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -and $state.identities.beta1.ref -eq 'refs/heads/codex/gwo-v8-beta1' -and (@($state.identities.beta1.parents) -join ',') -eq '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -and (@($state.identities.integration.parents) -join ',') -eq 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875' -and $state.identities.protected_ga.ref -eq 'refs/heads/codex/gwo-v8-ga-plan' -and (@($state.identities.protected_ga.parents) -join ',') -eq '3b7097213ac482b3a9dcc31320e7bd84191bf2c0' -and $state.identities.boundaries.implementation -eq 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2' -and $state.identities.boundaries.beta1 -eq 'ddc1785f84b6a82a7b5c34d5928b046d4e9a781d'; if (-not $fullIdentityValid) { throw 'FULL_FROZEN_IDENTITY_INVALID' }
$approvalPath = Join-Path $evidence 'approvals/publication-owner.json'; if (-not (Test-Path -LiteralPath $approvalPath -PathType Leaf)) { throw 'PUBLICATION_APPROVAL_MISSING' }
$leasePath = Join-Path $evidence 'approvals/publication-lease.json'; if (-not (Test-Path -LiteralPath $leasePath -PathType Leaf)) { throw 'PUBLICATION_LEASE_MISSING' }
$approval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $lease = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json
$approvalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant()
$publicationScope = @('tag:v8.0.0-beta.1','release:v8.0.0-beta.1')
$approvalScopeDiff = @(Compare-Object ($publicationScope | Sort-Object) (@($approval.mutation_set | ForEach-Object { [string]$_ } | Sort-Object)))
if ($approval.schema -ne 'gwo-v8-c1-publication-owner-approval.v1' -or $approval.approved -ne $true -or $approval.repository -ne $repo -or $approval.merged_main_sha -ne $state.pr.merge.merge_sha -or [string]::IsNullOrWhiteSpace([string]$approval.owner) -or [string]::IsNullOrWhiteSpace([string]$approval.publication_lease_id) -or $approvalScopeDiff.Count -ne 0) { throw 'PUBLICATION_APPROVAL_INVALID' }
$leaseUntil = [DateTime]::Parse([string]$lease.valid_until).ToUniversalTime(); $leaseFrom = [DateTime]::Parse([string]$lease.valid_from).ToUniversalTime()
$leaseScopeDiff = @(Compare-Object ($publicationScope | Sort-Object) (@($lease.mutation_set | ForEach-Object { [string]$_ } | Sort-Object)))
if ($lease.schema -ne 'gwo-v8-c1-publication-lease.v1' -or $lease.state -ne 'active' -or $lease.id -ne $approval.publication_lease_id -or $lease.owner -ne $approval.owner -or $lease.repository -ne $repo -or $lease.merged_main_sha -ne $state.pr.merge.merge_sha -or $lease.approval_sha256 -ne $approvalHash -or $leaseScopeDiff.Count -ne 0 -or $leaseFrom -ge $leaseUntil) { throw 'PUBLICATION_LEASE_INVALID' }
if ($null -ne $approval.local_writer_authorization) { if ($approval.local_writer_authorization.schema -ne 'gwo-v8-c1-local-writer-authorization.v1' -or $approval.local_writer_authorization.from_sha -ne $state.identities.base.sha -or $approval.local_writer_authorization.target_sha -ne $state.pr.merge.merge_sha -or $approval.local_writer_authorization.branch -ne 'main' -or $approval.local_writer_authorization.approved -ne $true) { throw 'LOCAL_WRITER_AUTHORIZATION_INVALID' } }
if ($null -ne $state.approvals.publication -and ($state.approvals.publication.approval_sha256 -ne $approvalHash -or $state.approvals.publication.lease_sha256 -ne $leaseHash)) { throw 'PUBLICATION_APPROVAL_RESUME_CONFLICT' }
function Save-State([object]$value) {
    $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 40),[Text.UTF8Encoding]::new($false))
    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf)) { throw 'STATE_TEMP_WRITE_FAILED' }
    try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }
    [IO.File]::Replace($tmp,$statePath,$null,$true)
    try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' }
}
git -C $root fetch --no-tags origin refs/heads/main; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PUBLICATION_MAIN_FETCH_FAILED' }
$publicationMain = (git -C $root rev-parse FETCH_HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $publicationMain -ne $state.pr.merge.merge_sha) { throw 'PUBLICATION_MAIN_FETCH_IDENTITY_INVALID' }
$notesLines = @(git show "$($state.pr.merge.merge_sha):docs/releases/v8.0.0-beta.1.md"); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOTES_READ_FAILED' }
$notes = $notesLines -join [Environment]::NewLine; $blocks = [regex]::Matches($notes,'(?ms)^(?<fence>```|~~~)json\s*\r?\n(?<json>\{.*?\})\s*\r?\n\k<fence>\s*(?:\r?\n|$)'); if ($blocks.Count -ne 1) { throw 'NOTES_JSON_COUNT_INVALID' }
$releaseEvidence = $blocks[0].Groups['json'].Value | ConvertFrom-Json
if ($releaseEvidence.schema -ne 'gwo-beta1-release-evidence.v2' -or $releaseEvidence.verification_mode -ne 'local-only' -or $releaseEvidence.core_baseline_sha -ne $state.identities.base.sha -or $releaseEvidence.core_baseline_tree -ne $state.identities.base.tree -or $releaseEvidence.python_version -ne 'Python 3.13.11' -or $releaseEvidence.requirements_sha256 -ne 'ee3c9f14db38950f5869759a5a94347197c9d4db3f138147b614ad6c4d862534' -or $releaseEvidence.local_verification_manifest_sha256 -ne '1f01205bc9846bebfd8e767744a60d4d1e4c185f081f6083606047cd37e9d4a3' -or $releaseEvidence.main_attestation_sha256 -ne '689ccbdf84667d9931b83f18b4234816a853ca61ba6cca8382117f2179e15818' -or $releaseEvidence.non_goal -ne 'Lean V8 production cutover') { throw 'RELEASE_EVIDENCE_INVALID' }
foreach ($number in 113,114,115,116,117,118,119) { if ($releaseEvidence.issues."$number" -ne 'OPEN') { throw "RELEASE_ISSUE_STATE_INVALID:$number" } }
if ($notes -notmatch 'no production admission' -or $notes -notmatch 'default-writer') { throw 'RELEASE_NON_GOALS_MISSING' }
$notesPath = Join-Path $evidence 'release-notes-from-merged-sha.md'; if (Test-Path -LiteralPath $notesPath -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $notesPath) -ne $notes) { throw 'NOTES_RECEIPT_CONFLICT' } } else { [IO.File]::WriteAllText($notesPath,$notes,[Text.UTF8Encoding]::new($false)) }; $normalizedNotes = $notes -replace '\r\n', ([char]10); $notesHash = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($normalizedNotes)); $notesHash = ([BitConverter]::ToString($notesHash) -replace '-','').ToLowerInvariant()
$tagDirectRows = @(git -C $root ls-remote --tags origin refs/tags/v8.0.0-beta.1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'TAG_READ_FAILED' }; $tagPeeledRows = @(git -C $root ls-remote --tags origin 'refs/tags/v8.0.0-beta.1^{}'); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'TAG_PEELED_READ_FAILED' }
$releaseProbe = @(gh api "repos/$repo/releases/tags/v8.0.0-beta.1" 2>&1); $releaseExit = $LASTEXITCODE; if ($releaseExit -ne 0 -and (($releaseProbe -join [Environment]::NewLine) -notmatch 'HTTP 404')) { throw 'RELEASE_READ_FAILED' }
$tagPresent = $tagDirectRows.Count -eq 1; $peeledPresent = $tagPeeledRows.Count -eq 1; $releasePresent = $releaseExit -eq 0; if (($tagPresent -and -not $peeledPresent) -or (-not $tagPresent -and $peeledPresent) -or ($releasePresent -and -not $tagPresent)) { throw 'PUBLICATION_CONFLICT' }; if ($tagDirectRows.Count -gt 1 -or $tagPeeledRows.Count -gt 1) { throw 'TAG_REF_CONFLICT' }
$state.approvals.publication = [ordered]@{ approval_path = $approvalPath.Replace('\','/'); approval_sha256 = $approvalHash; lease_path = $leasePath.Replace('\','/'); lease_sha256 = $leaseHash; owner = [string]$approval.owner; lease_id = [string]$lease.id; valid_from = $leaseFrom.ToString('o'); valid_until = $leaseUntil.ToString('o'); action_scope = @($publicationScope) }
$initialObservation = $state.publication.PSObject.Properties['initial_tag_present']
if ($null -eq $initialObservation) {
    $state.publication = [ordered]@{ owner_receipt = $approvalPath.Replace('\','/'); owner_receipt_sha256 = $approvalHash; lease_receipt = $leasePath.Replace('\','/'); lease_receipt_sha256 = $leaseHash; publication_lease_id = $approval.publication_lease_id; notes_path = $notesPath.Replace('\','/'); notes_sha256 = $notesHash; release_evidence = $releaseEvidence; initial_tag_present = [bool]$tagPresent; initial_release_present = [bool]$releasePresent; local_writer_authorization = $approval.local_writer_authorization; tag = $null; release = $null }
} elseif ($state.publication.owner_receipt_sha256 -ne $approvalHash -or $state.publication.lease_receipt_sha256 -ne $leaseHash -or $state.publication.notes_path -ne $notesPath.Replace('\','/') -or $state.publication.notes_sha256 -ne $notesHash -or $null -eq $state.publication.PSObject.Properties['initial_release_present'] -or $state.publication.initial_tag_present -isnot [bool] -or $state.publication.initial_release_present -isnot [bool]) { throw 'PUBLICATION_INITIAL_OBSERVATION_CONFLICT' }
Save-State $state; $check = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($check.approvals.publication.approval_sha256 -ne $approvalHash -or $check.approvals.publication.lease_sha256 -ne $leaseHash -or $null -eq $check.publication.PSObject.Properties['initial_tag_present']) { throw 'PUBLICATION_APPROVAL_STATE_READBACK_INVALID' }
~~~

If a matching tag and Release already exist, read them back and do not
recreate them. A correct tag with an absent Release resumes at Release
creation; a Release without its tag or any identity/body conflict stops.

- [ ] **6.2 Create/read back the annotated tag under a fresh policy readback.**

Immediately before the tag mutation, save and parse all three policy API
responses again, verify disabled Actions, zero workflows, active ruleset,
required_linear_history, pull_request, deletion, non_fast_forward, zero
bypass actors, no required status rule, and squash allowed. Re-read source,
protected GA, merged main, notes, and owner lease. The direct and peeled tag
reads happen first; an existing tag required after `initial_tag_present=false`
needs the exact tag policy and pre-mutation authorization or STOP, while a
proven initially-present tag is read-only. An absent required tag persists its
authorization before the final lease reload. Then create the annotated tag
from the exact squash SHA and push only the approved tag:

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
$repo = $state.repository; $repoRaw = @(gh api repos/$repo 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REPOSITORY_READ_FAILED' }; $repoObject = ($repoRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($repoObject.full_name -ne $repo -or $repoObject.default_branch -ne 'main') { throw 'DEFAULT_BRANCH_INVALID' }
if ($state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577' -or $state.pr.merge.method -ne 'squash' -or $state.pr.merge.tree -ne $state.identities.beta1.tree) { throw 'FROZEN_OR_MERGE_IDENTITY_INVALID' }
$fullIdentityValid = $root -eq $state.coordinator_root -and $state.identities.base.ref -eq 'refs/heads/main' -and (@($state.identities.base.parents) -join ',') -eq 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -and $state.identities.beta1.ref -eq 'refs/heads/codex/gwo-v8-beta1' -and (@($state.identities.beta1.parents) -join ',') -eq '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -and $state.identities.integration.tree -eq '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -and (@($state.identities.integration.parents) -join ',') -eq 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875' -and $state.identities.protected_ga.ref -eq 'refs/heads/codex/gwo-v8-ga-plan' -and (@($state.identities.protected_ga.parents) -join ',') -eq '3b7097213ac482b3a9dcc31320e7bd84191bf2c0' -and $state.identities.boundaries.implementation -eq 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2' -and $state.identities.boundaries.beta1 -eq 'ddc1785f84b6a82a7b5c34d5928b046d4e9a781d'; if (-not $fullIdentityValid) { throw 'FULL_FROZEN_IDENTITY_INVALID' }
$approvalPath = Join-Path $evidence 'approvals/publication-owner.json'; $leasePath = Join-Path $evidence 'approvals/publication-lease.json'; $approval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $lease = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json; $approvalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseFrom = [DateTime]::Parse([string]$lease.valid_from).ToUniversalTime(); $leaseUntil = [DateTime]::Parse([string]$lease.valid_until).ToUniversalTime(); if ($approval.approved -ne $true -or $approval.merged_main_sha -ne $state.pr.merge.merge_sha -or $lease.state -ne 'active' -or $lease.id -ne $approval.publication_lease_id -or $lease.approval_sha256 -ne $approvalHash -or $state.approvals.publication.approval_sha256 -ne $approvalHash -or $state.approvals.publication.lease_sha256 -ne $leaseHash -or $leaseFrom -ge $leaseUntil) { throw 'PUBLICATION_HISTORICAL_LEASE_RELOAD_INVALID' }
$publicationScope = @('tag:v8.0.0-beta.1','release:v8.0.0-beta.1'); if ($approval.schema -ne 'gwo-v8-c1-publication-owner-approval.v1' -or $approval.repository -ne $repo -or $lease.schema -ne 'gwo-v8-c1-publication-lease.v1' -or $lease.owner -ne $approval.owner -or $lease.repository -ne $repo -or $lease.merged_main_sha -ne $state.pr.merge.merge_sha -or @(Compare-Object ($publicationScope | Sort-Object) (@($approval.mutation_set | ForEach-Object { [string]$_ } | Sort-Object))).Count -ne 0 -or @(Compare-Object ($publicationScope | Sort-Object) (@($lease.mutation_set | ForEach-Object { [string]$_ } | Sort-Object))).Count -ne 0 -or $state.approvals.publication.owner -ne $approval.owner -or $state.approvals.publication.lease_id -ne $lease.id) { throw 'PUBLICATION_HISTORICAL_LEASE_BINDING_INVALID' }
function Save-State([object]$value) { $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp'); [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 50),[Text.UTF8Encoding]::new($false)); try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }; [IO.File]::Replace($tmp,$statePath,$null,$true); try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' } }
function Assert-PublicationLease {
    $approvalHashNow = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseHashNow = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant()
    try { $approvalNow = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $leaseNow = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json; $from = [DateTime]::Parse([string]$leaseNow.valid_from).ToUniversalTime(); $until = [DateTime]::Parse([string]$leaseNow.valid_until).ToUniversalTime() } catch { throw 'PUBLICATION_LEASE_IMMEDIATE_RELOAD_INVALID' }
    $scope = @('tag:v8.0.0-beta.1','release:v8.0.0-beta.1'); $approvalScopeDiff = @(Compare-Object ($scope | Sort-Object) (@($approvalNow.mutation_set | ForEach-Object { [string]$_ } | Sort-Object))); $leaseScopeDiff = @(Compare-Object ($scope | Sort-Object) (@($leaseNow.mutation_set | ForEach-Object { [string]$_ } | Sort-Object)))
    if ($approvalHashNow -ne $approvalHash -or $leaseHashNow -ne $leaseHash -or $approvalNow.schema -ne 'gwo-v8-c1-publication-owner-approval.v1' -or $approvalNow.approved -ne $true -or $approvalNow.repository -ne $repo -or $approvalNow.merged_main_sha -ne $state.pr.merge.merge_sha -or $approvalScopeDiff.Count -ne 0 -or $leaseNow.schema -ne 'gwo-v8-c1-publication-lease.v1' -or $leaseNow.state -ne 'active' -or $leaseNow.id -ne $approvalNow.publication_lease_id -or $leaseNow.owner -ne $approvalNow.owner -or $leaseNow.repository -ne $repo -or $leaseNow.merged_main_sha -ne $state.pr.merge.merge_sha -or $leaseNow.approval_sha256 -ne $approvalHashNow -or $leaseScopeDiff.Count -ne 0 -or [DateTime]::UtcNow -lt $from -or [DateTime]::UtcNow -ge $until) { throw 'PUBLICATION_LEASE_IMMEDIATE_RELOAD_INVALID' }
    return [ordered]@{ owner = [string]$approvalNow.owner; lease_id = [string]$leaseNow.id; approval_sha256 = $approvalHashNow; lease_sha256 = $leaseHashNow; valid_from = $from.ToString('o'); valid_until = $until.ToString('o') }
}
function New-PolicyReceipt([string]$label,[string]$directory,[object]$actionsObject,[object]$workflowsObject,[object]$rulesetObject,[object]$repositoryObject) { $files = [ordered]@{}; foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) { $path = Join-Path $directory $name; $files[$name] = [ordered]@{ path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() } }; $value = [ordered]@{ schema = 'gwo-v8-c1-mutation-policy.v1'; label = $label; repository = $repositoryObject.full_name; default_branch = $repositoryObject.default_branch; actions_enabled = $actionsObject.enabled; workflow_count = $workflowsObject.total_count; ruleset_id = $rulesetObject.id; files = $files }; $path = Join-Path $directory 'receipt.json'; $text = $value | ConvertTo-Json -Depth 30; if (Test-Path -LiteralPath $path -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $path) -ne $text) { throw 'POLICY_RECEIPT_CONFLICT' } } else { [IO.File]::WriteAllText($path,$text,[Text.UTF8Encoding]::new($false)) }; $policyReceipt = [ordered]@{ label = $label; path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }; return $policyReceipt }
function New-PolicyReceipt([string]$label,[string]$directory,[object]$actionsObject,[object]$workflowsObject,[object]$rulesetObject,[object]$repositoryObject) {
    $files = [ordered]@{}; foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) { $path = Join-Path $directory $name; $files[$name] = [ordered]@{ path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() } }; $value = [ordered]@{ schema = 'gwo-v8-c1-mutation-policy.v1'; label = $label; repository = $repositoryObject.full_name; default_branch = $repositoryObject.default_branch; actions_enabled = $actionsObject.enabled; workflow_count = $workflowsObject.total_count; ruleset_id = $rulesetObject.id; files = $files }; $path = Join-Path $directory 'receipt.json'; $text = $value | ConvertTo-Json -Depth 30; if (Test-Path -LiteralPath $path -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $path) -ne $text) { throw 'POLICY_RECEIPT_CONFLICT' } } else { [IO.File]::WriteAllText($path,$text,[Text.UTF8Encoding]::new($false)) }; return [ordered]@{ label = $label; path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
}
function Assert-TagPolicyAttempt([object]$receipt) {
    if ($null -eq $receipt -or $receipt.label -ne 'tag-push' -or [string]::IsNullOrWhiteSpace([string]$receipt.path) -or [string]::IsNullOrWhiteSpace([string]$receipt.sha256) -or -not (Test-Path -LiteralPath $receipt.path -PathType Leaf)) { throw 'TAG_POLICY_ATTEMPT_INVALID' }; if ((Get-FileHash -LiteralPath $receipt.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $receipt.sha256) { throw 'TAG_POLICY_ATTEMPT_INVALID' }; try { $value = Get-Content -Raw -LiteralPath $receipt.path | ConvertFrom-Json } catch { throw 'TAG_POLICY_ATTEMPT_INVALID' }; if ($value.schema -ne 'gwo-v8-c1-mutation-policy.v1' -or $value.label -ne 'tag-push' -or $value.repository -ne $repo -or $value.default_branch -ne 'main' -or $value.actions_enabled -ne $false -or $value.workflow_count -ne 0 -or $value.ruleset_id -ne 20160628) { throw 'TAG_POLICY_ATTEMPT_INVALID' }
    $directory = Split-Path -Path $receipt.path -Parent; $values = [ordered]@{}; foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) { $property = $value.files.PSObject.Properties[$name]; if ($null -eq $property) { throw 'TAG_POLICY_ATTEMPT_INVALID' }; $item = $property.Value; $expectedPath = (Join-Path $directory $name).Replace('\','/'); if ([string]$item.path -cne $expectedPath -or -not (Test-Path -LiteralPath $item.path -PathType Leaf) -or (Get-FileHash -LiteralPath $item.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.sha256) { throw 'TAG_POLICY_ATTEMPT_INVALID' }; try { $values[$name] = Get-Content -Raw -LiteralPath $item.path | ConvertFrom-Json } catch { throw 'TAG_POLICY_ATTEMPT_INVALID' } }; $a = $values['actions.json']; $w = $values['workflows.json']; $r = $values['ruleset.json']; $repository = $values['repository.json']; $types = @($r.rules | ForEach-Object type); $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }; if ($repository.full_name -ne $repo -or $repository.default_branch -ne 'main' -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0 -or @(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0 -or $allowed -notcontains 'squash' -or (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main'))) { throw 'TAG_POLICY_ATTEMPT_INVALID' }; return [ordered]@{ label = 'tag-push'; path = ([string]$receipt.path).Replace('\','/'); sha256 = [string]$receipt.sha256 }
}
function Assert-CurrentPublicationPolicy([string]$failure) {
    $liveActions = @(gh api repos/$repo/actions/permissions 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'TAG_CURRENT_POLICY_ACTIONS_READ_FAILED' }; $liveWorkflows = @(gh api repos/$repo/actions/workflows 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'TAG_CURRENT_POLICY_WORKFLOWS_READ_FAILED' }; $liveRuleset = @(gh api repos/$repo/rulesets/20160628 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'TAG_CURRENT_POLICY_RULESET_READ_FAILED' }; $liveRepository = @(gh api repos/$repo 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'TAG_CURRENT_POLICY_REPOSITORY_READ_FAILED' }; $a = ($liveActions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($liveWorkflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($liveRuleset -join [Environment]::NewLine) | ConvertFrom-Json; $repository = ($liveRepository -join [Environment]::NewLine) | ConvertFrom-Json; $types = @($r.rules | ForEach-Object type); $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }; if ($repository.full_name -ne $repo -or $repository.default_branch -ne 'main' -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0 -or @(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0 -or $allowed -notcontains 'squash' -or (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main'))) { throw $failure }; return $true
}
function Read-TagAuthorization([bool]$required,[string]$candidatePath=$null) {
    $reference = $state.mutation_authorization.tag; if ($null -ne $candidatePath) { $candidate = ([IO.Path]::GetFullPath($candidatePath)).Replace('\','/'); if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw 'TAG_AUTHORIZATION_ORPHAN_INVALID' }; $reference = [ordered]@{ effect_key = 'tag:v8.0.0-beta.1'; authorization_path = $candidate; authorization_sha256 = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant() } }; if ($null -eq $reference) { if ($required) { throw 'TAG_EXISTING_EFFECT_WITHOUT_AUTHORIZATION' }; return $null }; if ($reference.effect_key -ne 'tag:v8.0.0-beta.1' -or -not (Test-Path -LiteralPath $reference.authorization_path -PathType Leaf)) { throw 'TAG_AUTHORIZATION_REFERENCE_INVALID' }; $authorizationHash = (Get-FileHash -LiteralPath $reference.authorization_path -Algorithm SHA256).Hash.ToLowerInvariant(); if ($authorizationHash -ne $reference.authorization_sha256) { throw 'TAG_AUTHORIZATION_HASH_INVALID' }
    try { $value = Get-Content -Raw -LiteralPath $reference.authorization_path | ConvertFrom-Json; $authorizedAt = [DateTime]::Parse([string]$value.authorized_at).ToUniversalTime(); $authFrom = [DateTime]::Parse([string]$value.valid_from).ToUniversalTime(); $authUntil = [DateTime]::Parse([string]$value.valid_until).ToUniversalTime() } catch { throw 'TAG_AUTHORIZATION_MALFORMED' }; $expectedName = "tag-v8.0.0-beta.1-$($value.policy_sha256)-$($value.lease_sha256).json"; if ($value.schema -ne 'gwo-v8-c1-mutation-authorization.v1' -or $value.operation -ne 'tag-push' -or $value.effect_key -ne 'tag:v8.0.0-beta.1' -or $value.repository -ne $repo -or $value.merged_main_sha -ne $mergedSha -or $value.tag_name -ne $tagName -or $value.owner -ne $approval.owner -or $value.lease_id -ne $lease.id -or $value.approval_sha256 -ne $approvalHash -or $value.lease_sha256 -ne $leaseHash -or $value.policy_label -ne 'tag-push' -or [IO.Path]::GetFileName($reference.authorization_path) -cne $expectedName -or $authFrom -ne $leaseFrom -or $authUntil -ne $leaseUntil -or $authorizedAt -lt $authFrom -or $authorizedAt -ge $authUntil) { throw 'TAG_AUTHORIZATION_INVALID' }; if (-not (Test-Path -LiteralPath $value.policy_path -PathType Leaf) -or (Get-FileHash -LiteralPath $value.policy_path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $value.policy_sha256) { throw 'TAG_AUTHORIZATION_POLICY_INVALID' }; return [ordered]@{ reference = $reference; value = $value; policy = [ordered]@{ label = 'tag-push'; path = [string]$value.policy_path; sha256 = [string]$value.policy_sha256 } }
}
function Save-TagAuthorization([object]$policy,[object]$leaseEvidence) {
    $directory = Join-Path $evidence 'authorizations'; $orphanPaths = @(); if (Test-Path -LiteralPath $directory -PathType Container) { $orphanPaths = @(Get-ChildItem -LiteralPath $directory -Filter 'tag-v8.0.0-beta.1-*.json' -File | Sort-Object FullName | Where-Object { $null -eq $state.mutation_authorization.tag -or $_.FullName.Replace('\','/') -ne $state.mutation_authorization.tag.authorization_path }) }; if ($orphanPaths.Count -gt 1 -or ($null -ne $state.mutation_authorization.tag -and $orphanPaths.Count -gt 0)) { throw 'TAG_AUTHORIZATION_ORPHAN_CONFLICT' }
    if ($null -ne $state.mutation_authorization.tag) { $existing = Read-TagAuthorization $true; if ($existing.policy.path -ne $policy.path -or $existing.policy.sha256 -ne $policy.sha256 -or $existing.value.lease_sha256 -ne $leaseEvidence.lease_sha256) { throw 'TAG_AUTHORIZATION_IMMUTABLE_CONFLICT' }; return $existing }; if ($orphanPaths.Count -eq 1) { $orphan = Read-TagAuthorization $true $orphanPaths[0].FullName; if ($orphan.policy.path -ne $policy.path -or $orphan.policy.sha256 -ne $policy.sha256 -or $orphan.value.lease_sha256 -ne $leaseEvidence.lease_sha256) { throw 'TAG_AUTHORIZATION_ORPHAN_POLICY_CONFLICT' }; $state.mutation_authorization.tag = $orphan.reference; Save-State $state; $saved = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($saved.mutation_authorization.tag.authorization_sha256 -ne $orphan.reference.authorization_sha256) { throw 'TAG_AUTHORIZATION_ORPHAN_STATE_INVALID' }; return Read-TagAuthorization $true }
    $authorizedAt = [DateTime]::UtcNow; $authFrom = [DateTime]::Parse([string]$leaseEvidence.valid_from).ToUniversalTime(); $authUntil = [DateTime]::Parse([string]$leaseEvidence.valid_until).ToUniversalTime(); if ($authorizedAt -lt $authFrom -or $authorizedAt -ge $authUntil) { throw 'TAG_AUTHORIZATION_OUTSIDE_LEASE' }; if (-not (Test-Path -LiteralPath $directory -PathType Container)) { New-Item -ItemType Directory -Path $directory -ErrorAction Stop | Out-Null }; $path = Join-Path $directory ("tag-v8.0.0-beta.1-$($policy.sha256)-$($leaseEvidence.lease_sha256).json"); $value = [ordered]@{ schema = 'gwo-v8-c1-mutation-authorization.v1'; operation = 'tag-push'; effect_key = 'tag:v8.0.0-beta.1'; repository = $repo; merged_main_sha = $mergedSha; tag_name = $tagName; owner = [string]$leaseEvidence.owner; lease_id = [string]$leaseEvidence.lease_id; approval_sha256 = [string]$leaseEvidence.approval_sha256; lease_sha256 = [string]$leaseEvidence.lease_sha256; policy_label = 'tag-push'; policy_path = [string]$policy.path; policy_sha256 = [string]$policy.sha256; authorized_at = $authorizedAt.ToString('o'); valid_from = $authFrom.ToString('o'); valid_until = $authUntil.ToString('o') }; $json = $value | ConvertTo-Json -Depth 30; if (Test-Path -LiteralPath $path -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $path) -ne $json) { throw 'TAG_AUTHORIZATION_CONFLICT' } } else { [IO.File]::WriteAllText($path,$json,[Text.UTF8Encoding]::new($false)) }; $reference = [ordered]@{ effect_key = 'tag:v8.0.0-beta.1'; authorization_path = $path.Replace('\','/'); authorization_sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }; $state.mutation_authorization.tag = $reference; Save-State $state; $saved = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($saved.mutation_authorization.tag.authorization_sha256 -ne $reference.authorization_sha256) { throw 'TAG_AUTHORIZATION_STATE_READBACK_INVALID' }; return Read-TagAuthorization $true
}
function Read-PublicationPolicy([string]$label) {
    $null = Assert-PublicationLease; $dir = Join-Path $evidence ('policy-before-' + $label + '-' + [guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $dir -ErrorAction Stop | Out-Null; $actions = @(gh api repos/$repo/actions/permissions 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'ACTIONS_READBACK_FAILED' }; $workflows = @(gh api repos/$repo/actions/workflows 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'WORKFLOW_READBACK_FAILED' }; $ruleset = @(gh api repos/$repo/rulesets/20160628 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'RULESET_READBACK_FAILED' }; $repoRaw = @(gh api repos/$repo 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'REPOSITORY_READ_FAILED' }; foreach ($item in @(@('actions.json',$actions),@('workflows.json',$workflows),@('ruleset.json',$ruleset),@('repository.json',$repoRaw))) { [IO.File]::WriteAllText((Join-Path $dir $item[0]),($item[1] -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)) }; $a = ($actions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($workflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($ruleset -join [Environment]::NewLine) | ConvertFrom-Json; $repoObject = ($repoRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($repoObject.full_name -ne $repo -or $repoObject.default_branch -ne 'main' -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0) { throw 'PUBLICATION_POLICY_INVALID' }; $types = @($r.rules | ForEach-Object type); if (@(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0) { throw 'PUBLICATION_RULESET_INVALID' }; $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }; if ($allowed -notcontains 'squash') { throw 'PUBLICATION_SQUASH_POLICY_INVALID' }; $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; if (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main')) { throw 'PUBLICATION_RULESET_DEFAULT_BRANCH_INVALID' }; return New-PolicyReceipt $label $dir $a $w $r $repoObject
}
$tagName = 'v8.0.0-beta.1'; $mergedSha = [string]$state.pr.merge.merge_sha; $direct = @(git -C $root ls-remote --tags origin "refs/tags/$tagName"); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'TAG_DIRECT_READ_FAILED' }; $peeled = @(git -C $root ls-remote --tags origin "refs/tags/$tagName^{}"); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'TAG_PEELED_READ_FAILED' }; if ($direct.Count -gt 1 -or $peeled.Count -gt 1 -or (($direct.Count -eq 1) -xor ($peeled.Count -eq 1))) { throw 'TAG_REMOTE_CONFLICT' }
$releaseProbe = @(gh api "repos/$repo/releases/tags/$tagName" 2>&1); $releaseExit = $LASTEXITCODE; if ($releaseExit -ne 0 -and (($releaseProbe -join [Environment]::NewLine) -notmatch 'HTTP 404')) { throw 'RELEASE_PROBE_FAILED' }; if ($direct.Count -eq 0 -and $releaseExit -eq 0) { throw 'RELEASE_WITHOUT_TAG_CONFLICT' }
$initialTagPresent = [bool]$state.publication.initial_tag_present; $tagAuthorization = $null; $policyReceipt = $null; if ($direct.Count -gt 0 -and -not $initialTagPresent) { if ($null -eq $state.mutation_policy.tag) { throw 'TAG_EXISTING_EFFECT_POLICY_MISSING' }; $tagAuthorization = Read-TagAuthorization $true; $policyReceipt = Assert-TagPolicyAttempt $tagAuthorization.policy; if ($policyReceipt.path -ne $state.mutation_policy.tag.path -or $policyReceipt.sha256 -ne $state.mutation_policy.tag.sha256) { throw 'TAG_EXISTING_EFFECT_POLICY_AUTHORIZATION_MISMATCH' } } elseif ($direct.Count -gt 0 -and $initialTagPresent) { $orphanDirectory = Join-Path $evidence 'authorizations'; $orphanPaths = @(); if (Test-Path -LiteralPath $orphanDirectory -PathType Container) { $orphanPaths = @(Get-ChildItem -LiteralPath $orphanDirectory -Filter 'tag-v8.0.0-beta.1-*.json' -File | Sort-Object FullName | Where-Object { $null -eq $state.mutation_authorization.tag -or $_.FullName.Replace([char]92,'/') -ne $state.mutation_authorization.tag.authorization_path }) }; if ($orphanPaths.Count -gt 0) { throw 'TAG_AUTHORIZATION_ORPHAN_CONFLICT' }; if ($null -ne $state.mutation_policy.tag -or $null -ne $state.mutation_authorization.tag) { throw 'PROVEN_READ_ONLY_TAG_HAS_MUTATION_EVIDENCE' } }
if ($direct.Count -eq 0) {
    if ($initialTagPresent) { throw 'INITIAL_TAG_DISAPPEARED' }
    $orphanDirectory = Join-Path $evidence 'authorizations'; $orphanPaths = @(); if (Test-Path -LiteralPath $orphanDirectory -PathType Container) { $orphanPaths = @(Get-ChildItem -LiteralPath $orphanDirectory -Filter 'tag-v8.0.0-beta.1-*.json' -File | Sort-Object FullName | Where-Object { $null -eq $state.mutation_authorization.tag -or $_.FullName.Replace('\','/') -ne $state.mutation_authorization.tag.authorization_path }) }; if ($orphanPaths.Count -gt 1 -or ($null -ne $state.mutation_authorization.tag -and $orphanPaths.Count -gt 0)) { throw 'TAG_AUTHORIZATION_ORPHAN_CONFLICT' }
    $tagAuthorization = Read-TagAuthorization $false; $leaseEvidence = $null
    if ($null -ne $tagAuthorization) { if ($null -eq $state.mutation_policy.tag) { $policyReceipt = Assert-TagPolicyAttempt $tagAuthorization.policy; $state.mutation_policy.tag = $policyReceipt; Save-State $state } else { $policyReceipt = Assert-TagPolicyAttempt $state.mutation_policy.tag; if ($policyReceipt.path -ne $tagAuthorization.policy.path -or $policyReceipt.sha256 -ne $tagAuthorization.policy.sha256) { throw 'TAG_POLICY_AUTHORIZATION_MISMATCH' } }; $null = Assert-CurrentPublicationPolicy 'TAG_CURRENT_POLICY_INVALID'; $leaseEvidence = Assert-PublicationLease; Write-Output 'REUSE_RECOVERED_TAG_ATTEMPT'
    } elseif ($orphanPaths.Count -eq 1) {
        $tagAuthorization = Read-TagAuthorization $true $orphanPaths[0].FullName; $canonicalPolicy = $state.mutation_policy.tag
        if ($null -ne $canonicalPolicy) { $canonicalPolicy = Assert-TagPolicyAttempt $canonicalPolicy; if ($tagAuthorization.policy.path -ne $canonicalPolicy.path -or $tagAuthorization.policy.sha256 -ne $canonicalPolicy.sha256) { throw 'TAG_AUTHORIZATION_ORPHAN_POLICY_CONFLICT' }; $policyReceipt = $canonicalPolicy } else { $policyReceipt = Assert-TagPolicyAttempt $tagAuthorization.policy }
        if ($tagAuthorization.value.lease_sha256 -ne $leaseHash) { throw 'TAG_AUTHORIZATION_ORPHAN_LEASE_CONFLICT' }; $null = Assert-CurrentPublicationPolicy 'TAG_CURRENT_POLICY_INVALID'
        if ($null -ne $state.mutation_authorization.tag) { throw 'TAG_AUTHORIZATION_ORPHAN_CANONICAL_CONFLICT' }; $state.mutation_authorization.tag = $tagAuthorization.reference; if ($null -eq $state.mutation_policy.tag) { $state.mutation_policy.tag = $policyReceipt } elseif ($state.mutation_policy.tag.path -ne $policyReceipt.path -or $state.mutation_policy.tag.sha256 -ne $policyReceipt.sha256) { throw 'TAG_AUTHORIZATION_ORPHAN_POLICY_CONFLICT' }; Save-State $state; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; $tagAuthorization = Read-TagAuthorization $true; Write-Output 'ADOPTED_TAG_ORPHAN_ATTEMPT'
    } elseif ($null -ne $state.mutation_policy.tag) { $policyReceipt = Assert-TagPolicyAttempt $state.mutation_policy.tag; $null = Assert-CurrentPublicationPolicy 'TAG_CURRENT_POLICY_INVALID'; $leaseEvidence = Assert-PublicationLease; Write-Output 'REUSE_RECOVERED_TAG_POLICY'
    } else {
        $policyReceipt = Read-PublicationPolicy 'tag-push'; $state.mutation_policy.tag = $policyReceipt; Save-State $state; $policyState = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($policyState.mutation_policy.tag.sha256 -ne $policyReceipt.sha256) { throw 'TAG_POLICY_STATE_READBACK_INVALID' }
    }
    if ($null -eq $leaseEvidence) { $leaseEvidence = Assert-PublicationLease }; $tagAuthorization = Save-TagAuthorization $policyReceipt $leaseEvidence
    $remoteRows = @(git -C $root ls-remote --heads origin refs/heads/main refs/heads/codex/gwo-v8-beta1 refs/heads/codex/gwo-v8-ga-plan); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $remoteRows.Count -ne 3) { throw 'REMOTE_IDENTITY_READ_FAILED' }; $remoteMap = @{}; foreach ($row in $remoteRows) { $parts = $row -split '\s+'; if ($parts.Count -ne 2) { throw 'REMOTE_IDENTITY_ROW_INVALID' }; $remoteMap[$parts[1]] = $parts[0] }; if ($remoteMap['refs/heads/main'] -ne $mergedSha -or $remoteMap['refs/heads/codex/gwo-v8-beta1'] -ne $state.identities.beta1.sha -or $remoteMap['refs/heads/codex/gwo-v8-ga-plan'] -ne $state.identities.protected_ga.sha) { throw 'REMOTE_IDENTITY_CHANGED_BEFORE_TAG' }
    git -C $root fetch --no-tags origin refs/heads/main; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'MAIN_FETCH_BEFORE_TAG_FAILED' }; $fetched = (git -C $root rev-parse FETCH_HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $fetched -ne $mergedSha) { throw 'MAIN_FETCH_BEFORE_TAG_INVALID' }; $localType = @(git -C $root cat-file -t "refs/tags/$tagName" 2>&1); $localExit = $LASTEXITCODE; if ($localExit -eq 0) { $localTarget = (git -C $root rev-parse "refs/tags/$tagName^{}").Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $localType[0] -ne 'tag' -or $localTarget -ne $mergedSha) { throw 'LOCAL_TAG_CONFLICT' } } elseif ($localExit -eq 128) { git -C $root tag -a $tagName $mergedSha -m 'GWO V8 Beta1 - Core Preview'; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'TAG_CREATE_FAILED' } } else { throw 'LOCAL_TAG_PROBE_FAILED' }
    $directImmediate = @(git -C $root ls-remote --tags origin "refs/tags/$tagName"); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $directImmediate.Count -ne 0) { throw 'TAG_PREMUTATION_CONFLICT' }; $finalTagLease = Assert-PublicationLease; $finalTagApprovalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $finalTagLeaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); $finalTagNow = [DateTime]::UtcNow; $finalTagFrom = [DateTime]::Parse([string]$finalTagLease.valid_from).ToUniversalTime(); $finalTagUntil = [DateTime]::Parse([string]$finalTagLease.valid_until).ToUniversalTime(); if ($finalTagApprovalHash -ne $approvalHash -or $finalTagLeaseHash -ne $leaseHash -or $finalTagNow -lt $finalTagFrom -or $finalTagNow -ge $finalTagUntil) { throw 'TAG_FINAL_IMMEDIATE_LEASE_INVALID' }; git -C $root push origin "refs/tags/$tagName"; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'TAG_PUSH_FAILED' }
}
$direct = @(git -C $root ls-remote --tags origin "refs/tags/$tagName"); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $direct.Count -ne 1) { throw 'TAG_DIRECT_READBACK_FAILED' }; $peeled = @(git -C $root ls-remote --tags origin "refs/tags/$tagName^{}"); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $peeled.Count -ne 1) { throw 'TAG_PEELED_READBACK_FAILED' }; $directSha = (($direct[0] -split '\s+')[0]); $peeledSha = (($peeled[0] -split '\s+')[0]); if ($peeledSha -ne $mergedSha) { throw 'TAG_PEELED_TARGET_INVALID' }
$refRaw = @(gh api "repos/$repo/git/ref/tags/$tagName" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'TAG_REF_API_FAILED' }; $tagRef = ($refRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($tagRef.object.type -ne 'tag' -or $tagRef.object.sha -ne $directSha) { throw 'ANNOTATED_TAG_REF_INVALID' }; $tagRaw = @(gh api "repos/$repo/git/tags/$directSha" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'TAG_OBJECT_API_FAILED' }; $tagObject = ($tagRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($tagObject.sha -ne $directSha -or $tagObject.object.type -ne 'commit' -or $tagObject.object.sha -ne $mergedSha -or ([string]$tagObject.message).Trim() -ne 'GWO V8 Beta1 - Core Preview') { throw 'ANNOTATED_TAG_OBJECT_INVALID' }
$refPath = Join-Path $evidence 'tag-ref-readback.json'; $tagPath = Join-Path $evidence 'tag-object-readback.json'; $refText = $tagRef | ConvertTo-Json -Depth 30; $tagText = $tagObject | ConvertTo-Json -Depth 30; foreach ($pair in @(@($refPath,$refText),@($tagPath,$tagText))) { if (Test-Path -LiteralPath $pair[0] -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $pair[0]) -ne $pair[1]) { throw 'TAG_READBACK_RECEIPT_CONFLICT' } } else { [IO.File]::WriteAllText($pair[0],$pair[1],[Text.UTF8Encoding]::new($false)) } }
$tagAuthorizationRef = if ($null -eq $tagAuthorization) { $null } else { $tagAuthorization.reference }; $state.publication.tag = [ordered]@{ schema = 'gwo-v8-c1-annotated-tag.v1'; name = $tagName; object_type = 'tag'; object_sha = $directSha; direct_sha = $directSha; peeled_sha = $peeledSha; target_type = $tagObject.object.type; target_sha = $tagObject.object.sha; ref_path = $refPath.Replace('\','/'); ref_sha256 = (Get-FileHash -LiteralPath $refPath -Algorithm SHA256).Hash.ToLowerInvariant(); object_path = $tagPath.Replace('\','/'); object_sha256 = (Get-FileHash -LiteralPath $tagPath -Algorithm SHA256).Hash.ToLowerInvariant(); authorization_path = if ($null -eq $tagAuthorizationRef) { $null } else { $tagAuthorizationRef.authorization_path }; authorization_sha256 = if ($null -eq $tagAuthorizationRef) { $null } else { $tagAuthorizationRef.authorization_sha256 } }; Save-State $state; $check = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($check.publication.tag.object_type -ne 'tag' -or $check.publication.tag.peeled_sha -ne $mergedSha) { throw 'TAG_STATE_READBACK_INVALID' }
~~~

Read direct and peeled tag refs, tag object API, and annotated tag content.
Require object type tag, tag object SHA, and peeled target exactly equal to
the squash commit. Persist tag object/type/peel and all response hashes.
Never move, delete, overwrite, or recreate a conflicting object.

- [ ] **6.3 Create/read back the prerelease with exact body equality.**

Probe the Release before any current-clock lease requirement. If it is already
present but `initial_release_present=false`, require the exact pre-mutation
authorization/policy receipt or STOP; never retroactively bless it. If it is
absent, re-read policy and tag/release state, reload the active lease, persist
the authorization/intention first, and only then mutate. Run:

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/')
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only') { throw 'STATE_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
$repo = $state.repository; $repoRaw = @(gh api repos/$repo 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REPOSITORY_READ_FAILED' }; $repoObject = ($repoRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($repoObject.full_name -ne $repo -or $repoObject.default_branch -ne 'main') { throw 'DEFAULT_BRANCH_INVALID' }
if ($state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.publication.tag.object_type -ne 'tag' -or $state.publication.tag.peeled_sha -ne $state.pr.merge.merge_sha) { throw 'FROZEN_OR_TAG_STATE_INVALID' }
$fullIdentityValid = $root -eq $state.coordinator_root -and $state.identities.base.ref -eq 'refs/heads/main' -and (@($state.identities.base.parents) -join ',') -eq 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -and $state.identities.beta1.ref -eq 'refs/heads/codex/gwo-v8-beta1' -and (@($state.identities.beta1.parents) -join ',') -eq '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -and $state.identities.integration.tree -eq '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -and (@($state.identities.integration.parents) -join ',') -eq 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875' -and $state.identities.protected_ga.ref -eq 'refs/heads/codex/gwo-v8-ga-plan' -and $state.identities.protected_ga.tree -eq 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577' -and (@($state.identities.protected_ga.parents) -join ',') -eq '3b7097213ac482b3a9dcc31320e7bd84191bf2c0' -and $state.identities.boundaries.implementation -eq 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2' -and $state.identities.boundaries.beta1 -eq 'ddc1785f84b6a82a7b5c34d5928b046d4e9a781d'; if (-not $fullIdentityValid) { throw 'FULL_FROZEN_IDENTITY_INVALID' }
$approvalPath = Join-Path $evidence 'approvals/publication-owner.json'; $leasePath = Join-Path $evidence 'approvals/publication-lease.json'; $approval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $lease = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json; $approvalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseFrom = [DateTime]::Parse([string]$lease.valid_from).ToUniversalTime(); $leaseUntil = [DateTime]::Parse([string]$lease.valid_until).ToUniversalTime(); if ($approval.schema -ne 'gwo-v8-c1-publication-owner-approval.v1' -or $approval.approved -ne $true -or $approval.merged_main_sha -ne $state.pr.merge.merge_sha -or $lease.schema -ne 'gwo-v8-c1-publication-lease.v1' -or $lease.state -ne 'active' -or $lease.id -ne $approval.publication_lease_id -or $lease.approval_sha256 -ne $approvalHash -or $state.approvals.publication.approval_sha256 -ne $approvalHash -or $state.approvals.publication.lease_sha256 -ne $leaseHash -or $leaseFrom -ge $leaseUntil) { throw 'PUBLICATION_HISTORICAL_LEASE_RELOAD_INVALID' }
$publicationScope = @('tag:v8.0.0-beta.1','release:v8.0.0-beta.1'); $approvalScopeDiff = @(Compare-Object ($publicationScope | Sort-Object) (@($approval.mutation_set | ForEach-Object { [string]$_ } | Sort-Object))); $leaseScopeDiff = @(Compare-Object ($publicationScope | Sort-Object) (@($lease.mutation_set | ForEach-Object { [string]$_ } | Sort-Object))); $savedPublicationApproval = $state.approvals.publication; if ($approval.repository -ne $repo -or $approvalScopeDiff.Count -ne 0 -or $lease.owner -ne $approval.owner -or $lease.repository -ne $repo -or $lease.merged_main_sha -ne $state.pr.merge.merge_sha -or $leaseScopeDiff.Count -ne 0 -or $savedPublicationApproval.owner -ne $approval.owner -or $savedPublicationApproval.lease_id -ne $lease.id -or [DateTime]::Parse([string]$savedPublicationApproval.valid_from).ToUniversalTime() -ne $leaseFrom -or [DateTime]::Parse([string]$savedPublicationApproval.valid_until).ToUniversalTime() -ne $leaseUntil) { throw 'PUBLICATION_HISTORICAL_LEASE_BINDING_INVALID' }
function Save-State([object]$value) { $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp'); [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 50),[Text.UTF8Encoding]::new($false)); try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }; [IO.File]::Replace($tmp,$statePath,$null,$true); try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' } }
function Assert-PublicationLease {
    $approvalHashNow = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $leaseHashNow = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant()
    try { $approvalNow = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $leaseNow = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json; $from = [DateTime]::Parse([string]$leaseNow.valid_from).ToUniversalTime(); $until = [DateTime]::Parse([string]$leaseNow.valid_until).ToUniversalTime() } catch { throw 'PUBLICATION_LEASE_IMMEDIATE_RELOAD_INVALID' }
    $scope = @('tag:v8.0.0-beta.1','release:v8.0.0-beta.1'); $approvalScopeDiff = @(Compare-Object ($scope | Sort-Object) (@($approvalNow.mutation_set | ForEach-Object { [string]$_ } | Sort-Object))); $leaseScopeDiff = @(Compare-Object ($scope | Sort-Object) (@($leaseNow.mutation_set | ForEach-Object { [string]$_ } | Sort-Object)))
    if ($approvalHashNow -ne $approvalHash -or $leaseHashNow -ne $leaseHash -or $approvalNow.schema -ne 'gwo-v8-c1-publication-owner-approval.v1' -or $approvalNow.approved -ne $true -or $approvalNow.repository -ne $repo -or $approvalNow.merged_main_sha -ne $state.pr.merge.merge_sha -or $approvalScopeDiff.Count -ne 0 -or $leaseNow.schema -ne 'gwo-v8-c1-publication-lease.v1' -or $leaseNow.state -ne 'active' -or $leaseNow.id -ne $approvalNow.publication_lease_id -or $leaseNow.owner -ne $approvalNow.owner -or $leaseNow.repository -ne $repo -or $leaseNow.merged_main_sha -ne $state.pr.merge.merge_sha -or $leaseNow.approval_sha256 -ne $approvalHashNow -or $leaseScopeDiff.Count -ne 0 -or [DateTime]::UtcNow -lt $from -or [DateTime]::UtcNow -ge $until) { throw 'PUBLICATION_LEASE_IMMEDIATE_RELOAD_INVALID' }
    return [ordered]@{ owner = [string]$approvalNow.owner; lease_id = [string]$leaseNow.id; approval_sha256 = $approvalHashNow; lease_sha256 = $leaseHashNow; valid_from = $from.ToString('o'); valid_until = $until.ToString('o') }
}
function New-PolicyReceipt([string]$label,[string]$directory,[object]$actionsObject,[object]$workflowsObject,[object]$rulesetObject,[object]$repositoryObject) { $files = [ordered]@{}; foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) { $path = Join-Path $directory $name; $files[$name] = [ordered]@{ path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() } }; $value = [ordered]@{ schema = 'gwo-v8-c1-mutation-policy.v1'; label = $label; repository = $repositoryObject.full_name; default_branch = $repositoryObject.default_branch; actions_enabled = $actionsObject.enabled; workflow_count = $workflowsObject.total_count; ruleset_id = $rulesetObject.id; files = $files }; $path = Join-Path $directory 'receipt.json'; $text = $value | ConvertTo-Json -Depth 30; if (Test-Path -LiteralPath $path -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $path) -ne $text) { throw 'POLICY_RECEIPT_CONFLICT' } } else { [IO.File]::WriteAllText($path,$text,[Text.UTF8Encoding]::new($false)) }; $policyReceipt = [ordered]@{ label = $label; path = $path.Replace('\','/'); sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }; return $policyReceipt }
function Read-ReleasePolicy {
    $null = Assert-PublicationLease
    $dir = Join-Path $evidence ('policy-before-release-' + [guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $dir -ErrorAction Stop | Out-Null
    $actions = @(gh api repos/$repo/actions/permissions 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'ACTIONS_READBACK_FAILED' }; $workflows = @(gh api repos/$repo/actions/workflows 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'WORKFLOW_READBACK_FAILED' }; $ruleset = @(gh api repos/$repo/rulesets/20160628 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'RULESET_READBACK_FAILED' }; $repoRaw = @(gh api repos/$repo 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'REPOSITORY_READ_FAILED' }
    foreach ($item in @(@('actions.json',$actions),@('workflows.json',$workflows),@('ruleset.json',$ruleset),@('repository.json',$repoRaw))) { [IO.File]::WriteAllText((Join-Path $dir $item[0]),($item[1] -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)) }
    $a = ($actions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($workflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($ruleset -join [Environment]::NewLine) | ConvertFrom-Json; $repoObject = ($repoRaw -join [Environment]::NewLine) | ConvertFrom-Json
    if ($repoObject.full_name -ne $repo -or $repoObject.default_branch -ne 'main' -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0) { throw 'RELEASE_POLICY_INVALID' }
    $types = @($r.rules | ForEach-Object type); if (@(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0) { throw 'RELEASE_RULESET_INVALID' }; $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }; if ($allowed -notcontains 'squash') { throw 'RELEASE_SQUASH_POLICY_INVALID' }; $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; if (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main')) { throw 'RELEASE_RULESET_DEFAULT_BRANCH_INVALID' }
    return New-PolicyReceipt 'release-create' $dir $a $w $r $repoObject
}
function Assert-ReleasePolicyAttempt([object]$receipt) {
    try {
        if ($null -eq $receipt -or $receipt.label -ne 'release-create' -or [string]::IsNullOrWhiteSpace([string]$receipt.path) -or [string]::IsNullOrWhiteSpace([string]$receipt.sha256)) { throw 'RELEASE_POLICY_ATTEMPT_INVALID' }
        $receiptPath = ([IO.Path]::GetFullPath([string]$receipt.path)).Replace([char]92,'/')
        if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) { throw 'RELEASE_POLICY_ATTEMPT_INVALID' }
        if ((Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$receipt.sha256) { throw 'RELEASE_POLICY_ATTEMPT_INVALID' }
        $value = Get-Content -Raw -LiteralPath $receiptPath | ConvertFrom-Json
        if ($null -eq $value -or $value.schema -ne 'gwo-v8-c1-mutation-policy.v1' -or $value.label -ne 'release-create' -or $value.repository -ne $repo -or $value.default_branch -ne 'main' -or $null -eq $value.PSObject.Properties['files'] -or @($value.files.PSObject.Properties).Count -ne 4) { throw 'RELEASE_POLICY_ATTEMPT_INVALID' }
        $directory = Split-Path -Path $receiptPath -Parent; $values = [ordered]@{}
        foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) {
            $property = $value.files.PSObject.Properties[$name]; if ($null -eq $property -or $null -eq $property.Value) { throw 'RELEASE_POLICY_ATTEMPT_INVALID' }
            $item = $property.Value; if ($null -eq $item.PSObject.Properties['path'] -or $null -eq $item.PSObject.Properties['sha256'] -or [string]::IsNullOrWhiteSpace([string]$item.path) -or [string]::IsNullOrWhiteSpace([string]$item.sha256)) { throw 'RELEASE_POLICY_ATTEMPT_INVALID' }
            $expectedPath = ([IO.Path]::GetFullPath((Join-Path $directory $name))).Replace([char]92,'/'); $actualPath = ([IO.Path]::GetFullPath([string]$item.path)).Replace([char]92,'/')
            if ($actualPath -cne $expectedPath -or -not (Test-Path -LiteralPath $actualPath -PathType Leaf)) { throw 'RELEASE_POLICY_ATTEMPT_INVALID' }
            if ((Get-FileHash -LiteralPath $actualPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$item.sha256) { throw 'RELEASE_POLICY_ATTEMPT_INVALID' }
            try { $values[$name] = Get-Content -Raw -LiteralPath $actualPath | ConvertFrom-Json } catch { throw 'RELEASE_POLICY_ATTEMPT_INVALID' }
        }
        $a = $values['actions.json']; $w = $values['workflows.json']; $r = $values['ruleset.json']; $repository = $values['repository.json']; $types = @($r.rules | ForEach-Object { [string]$_.type })
        $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }
        $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }
        if ($repository.full_name -ne $repo -or $repository.default_branch -ne 'main' -or $value.repository -ne $repository.full_name -or $value.default_branch -ne $repository.default_branch -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $value.actions_enabled -ne $a.enabled -or $value.workflow_count -ne $w.total_count -or $r.id -ne 20160628 -or $value.ruleset_id -ne $r.id -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0 -or @(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0 -or $allowed -notcontains 'squash' -or (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main'))) { throw 'RELEASE_POLICY_ATTEMPT_INVALID' }
    } catch {
        throw 'RELEASE_POLICY_ATTEMPT_INVALID'
    }
    return [ordered]@{ label = 'release-create'; path = $receiptPath; sha256 = [string]$receipt.sha256 }
}
function Assert-CurrentReleasePolicy([string]$failure) {
    $liveActions = @(gh api repos/$repo/actions/permissions 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'RELEASE_CURRENT_POLICY_ACTIONS_READ_FAILED' }
    $liveWorkflows = @(gh api repos/$repo/actions/workflows 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'RELEASE_CURRENT_POLICY_WORKFLOWS_READ_FAILED' }
    $liveRuleset = @(gh api repos/$repo/rulesets/20160628 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'RELEASE_CURRENT_POLICY_RULESET_READ_FAILED' }
    $liveRepository = @(gh api repos/$repo 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw 'RELEASE_CURRENT_POLICY_REPOSITORY_READ_FAILED' }
    $a = ($liveActions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($liveWorkflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($liveRuleset -join [Environment]::NewLine) | ConvertFrom-Json; $repoObject = ($liveRepository -join [Environment]::NewLine) | ConvertFrom-Json
    $types = @($r.rules | ForEach-Object type); $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }
    if ($repoObject.full_name -ne $repo -or $repoObject.default_branch -ne 'main' -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0 -or @(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0 -or $allowed -notcontains 'squash' -or (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main'))) { throw $failure }
}
function Read-ReleaseAuthorization([bool]$required,[string]$candidatePath=$null) {
    $reference = $state.mutation_authorization.release; if ($null -ne $candidatePath) { $candidate = ([IO.Path]::GetFullPath($candidatePath)).Replace([char]92,'/'); if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw 'RELEASE_AUTHORIZATION_ORPHAN_INVALID' }; $reference = [ordered]@{ effect_key = 'release:v8.0.0-beta.1'; authorization_path = $candidate; authorization_sha256 = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant() } }; if ($null -eq $reference) { if ($required) { throw 'RELEASE_EFFECT_WITHOUT_AUTHORIZATION' }; return $null }
    if ($reference.effect_key -ne 'release:v8.0.0-beta.1' -or -not (Test-Path -LiteralPath $reference.authorization_path -PathType Leaf)) { throw 'RELEASE_AUTHORIZATION_REFERENCE_INVALID' }; $authorizationHash = (Get-FileHash -LiteralPath $reference.authorization_path -Algorithm SHA256).Hash.ToLowerInvariant(); if ($authorizationHash -ne $reference.authorization_sha256) { throw 'RELEASE_AUTHORIZATION_HASH_INVALID' }
    try { $value = Get-Content -Raw -LiteralPath $reference.authorization_path | ConvertFrom-Json; $authorizedAt = [DateTime]::Parse([string]$value.authorized_at).ToUniversalTime(); $authFrom = [DateTime]::Parse([string]$value.valid_from).ToUniversalTime(); $authUntil = [DateTime]::Parse([string]$value.valid_until).ToUniversalTime() } catch { throw 'RELEASE_AUTHORIZATION_MALFORMED' }
    if ($value.schema -ne 'gwo-v8-c1-mutation-authorization.v1' -or $value.operation -ne 'release-create' -or $value.effect_key -ne 'release:v8.0.0-beta.1' -or $value.repository -ne $repo -or $value.merged_main_sha -ne $state.pr.merge.merge_sha -or $value.tag_name -ne 'v8.0.0-beta.1' -or $value.owner -ne $approval.owner -or $value.lease_id -ne $lease.id -or $value.approval_sha256 -ne $approvalHash -or $value.lease_sha256 -ne $leaseHash -or $value.policy_label -ne 'release-create' -or $authFrom -ne $leaseFrom -or $authUntil -ne $leaseUntil -or $authorizedAt -lt $authFrom -or $authorizedAt -ge $authUntil) { throw 'RELEASE_AUTHORIZATION_INVALID' }
    if (-not (Test-Path -LiteralPath $value.policy_path -PathType Leaf) -or (Get-FileHash -LiteralPath $value.policy_path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $value.policy_sha256) { throw 'RELEASE_AUTHORIZATION_POLICY_INVALID' }
    return [ordered]@{ reference = $reference; value = $value; policy = [ordered]@{ label = 'release-create'; path = [string]$value.policy_path; sha256 = [string]$value.policy_sha256 } }
}
function Save-ReleaseAuthorization([object]$policy,[object]$leaseEvidence) {
    $directory = Join-Path $evidence 'authorizations'; $orphanPaths = @(); if (Test-Path -LiteralPath $directory -PathType Container) { $orphanPaths = @(Get-ChildItem -LiteralPath $directory -Filter 'release-v8.0.0-beta.1-*.json' -File | Sort-Object FullName | Where-Object { $null -eq $state.mutation_authorization.release -or $_.FullName.Replace([char]92,'/') -ne $state.mutation_authorization.release.authorization_path }) }; if ($orphanPaths.Count -gt 1 -or ($null -ne $state.mutation_authorization.release -and $orphanPaths.Count -gt 0)) { throw 'RELEASE_AUTHORIZATION_ORPHAN_CONFLICT' }
    if ($null -ne $state.mutation_authorization.release) { $existing = Read-ReleaseAuthorization $true; if ($existing.policy.path -ne $policy.path -or $existing.policy.sha256 -ne $policy.sha256 -or $existing.value.lease_sha256 -ne $leaseEvidence.lease_sha256) { throw 'RELEASE_AUTHORIZATION_IMMUTABLE_CONFLICT' }; return $existing }
    if ($orphanPaths.Count -eq 1) { $orphan = Read-ReleaseAuthorization $true $orphanPaths[0].FullName; if ($orphan.policy.path -ne $policy.path -or $orphan.policy.sha256 -ne $policy.sha256 -or $orphan.value.lease_sha256 -ne $leaseEvidence.lease_sha256) { throw 'RELEASE_AUTHORIZATION_ORPHAN_POLICY_CONFLICT' }; $state.mutation_authorization.release = $orphan.reference; Save-State $state; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; return Read-ReleaseAuthorization $true }
    $authorizedAt = [DateTime]::UtcNow; $authFrom = [DateTime]::Parse([string]$leaseEvidence.valid_from).ToUniversalTime(); $authUntil = [DateTime]::Parse([string]$leaseEvidence.valid_until).ToUniversalTime(); if ($authorizedAt -lt $authFrom -or $authorizedAt -ge $authUntil) { throw 'RELEASE_AUTHORIZATION_OUTSIDE_LEASE' }
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { New-Item -ItemType Directory -Path $directory -ErrorAction Stop | Out-Null }; $path = Join-Path $directory ("release-v8.0.0-beta.1-$($policy.sha256).json")
    $value = [ordered]@{ schema = 'gwo-v8-c1-mutation-authorization.v1'; operation = 'release-create'; effect_key = 'release:v8.0.0-beta.1'; repository = $repo; merged_main_sha = $state.pr.merge.merge_sha; tag_name = 'v8.0.0-beta.1'; owner = [string]$leaseEvidence.owner; lease_id = [string]$leaseEvidence.lease_id; approval_sha256 = [string]$leaseEvidence.approval_sha256; lease_sha256 = [string]$leaseEvidence.lease_sha256; policy_label = 'release-create'; policy_path = [string]$policy.path; policy_sha256 = [string]$policy.sha256; authorized_at = $authorizedAt.ToString('o'); valid_from = $authFrom.ToString('o'); valid_until = $authUntil.ToString('o') }
    $text = $value | ConvertTo-Json -Depth 30; if (Test-Path -LiteralPath $path -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $path) -ne $text) { throw 'RELEASE_AUTHORIZATION_CONFLICT' } } else { [IO.File]::WriteAllText($path,$text,[Text.UTF8Encoding]::new($false)) }
    $reference = [ordered]@{ effect_key = 'release:v8.0.0-beta.1'; authorization_path = $path.Replace([char]92,'/'); authorization_sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }; $state.mutation_authorization.release = $reference; Save-State $state
    $saved = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($saved.mutation_authorization.release.authorization_sha256 -ne $reference.authorization_sha256) { throw 'RELEASE_AUTHORIZATION_STATE_READBACK_INVALID' }
    return Read-ReleaseAuthorization $true
}
$tagName = 'v8.0.0-beta.1'; $mergedSha = [string]$state.pr.merge.merge_sha; $notesPath = [string]$state.publication.notes_path; if (-not (Test-Path -LiteralPath $notesPath -PathType Leaf)) { throw 'NOTES_FILE_MISSING' }
$normalizedNotes = (Get-Content -Raw -LiteralPath $notesPath) -replace '\r\n', ([char]10); $notesBytes = [Text.Encoding]::UTF8.GetBytes($normalizedNotes); $notesHash = ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($notesBytes)) -replace '-','').ToLowerInvariant(); if ($notesHash -ne $state.publication.notes_sha256) { throw 'NOTES_HASH_CHANGED' }
$direct = @(git -C $root ls-remote --tags origin "refs/tags/$tagName"); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $direct.Count -ne 1) { throw 'TAG_DIRECT_READ_FAILED' }; $peeled = @(git -C $root ls-remote --tags origin "refs/tags/$tagName^{}"); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $peeled.Count -ne 1 -or (($peeled[0] -split '\s+')[0]) -ne $mergedSha) { throw 'TAG_PEELED_READ_FAILED' }
$probe = @(gh api "repos/$repo/releases/tags/$tagName" 2>&1); $probeExit = $LASTEXITCODE; if ($probeExit -ne 0 -and (($probe -join [Environment]::NewLine) -notmatch 'HTTP 404')) { throw 'RELEASE_PROBE_FAILED' }
$releaseWriteRequired = -not [bool]$state.publication.initial_release_present; $releaseAuthorization = $null; $policyReceipt = $null; $leaseEvidence = $null
if ($probeExit -ne 0) {
    if (-not $releaseWriteRequired) { throw 'INITIAL_RELEASE_DISAPPEARED' }
    $orphanDirectory = Join-Path $evidence 'authorizations'; $orphanPaths = @(); if (Test-Path -LiteralPath $orphanDirectory -PathType Container) { $orphanPaths = @(Get-ChildItem -LiteralPath $orphanDirectory -Filter 'release-v8.0.0-beta.1-*.json' -File | Sort-Object FullName | Where-Object { $null -eq $state.mutation_authorization.release -or $_.FullName.Replace([char]92,'/') -ne $state.mutation_authorization.release.authorization_path }) }; if ($orphanPaths.Count -gt 1 -or ($null -ne $state.mutation_authorization.release -and $orphanPaths.Count -gt 0)) { throw 'RELEASE_AUTHORIZATION_ORPHAN_CONFLICT' }
    $releaseAuthorization = Read-ReleaseAuthorization $false
    if ($null -ne $releaseAuthorization) {
        if ($null -eq $state.mutation_policy.release) { $policyReceipt = Assert-ReleasePolicyAttempt $releaseAuthorization.policy; $state.mutation_policy.release = $policyReceipt; Save-State $state } else { $policyReceipt = Assert-ReleasePolicyAttempt $state.mutation_policy.release; if ($policyReceipt.path -ne $releaseAuthorization.policy.path -or $policyReceipt.sha256 -ne $releaseAuthorization.policy.sha256) { throw 'RELEASE_POLICY_AUTHORIZATION_MISMATCH' } }
        $null = Assert-CurrentReleasePolicy 'RELEASE_CURRENT_POLICY_INVALID'; Write-Output 'REUSE_RECOVERED_RELEASE_ATTEMPT'
    } elseif ($orphanPaths.Count -eq 1) {
        $releaseAuthorization = Read-ReleaseAuthorization $true $orphanPaths[0].FullName; $canonicalPolicy = $state.mutation_policy.release
        if ($null -ne $canonicalPolicy) { $canonicalPolicy = Assert-ReleasePolicyAttempt $canonicalPolicy; if ($releaseAuthorization.policy.path -ne $canonicalPolicy.path -or $releaseAuthorization.policy.sha256 -ne $canonicalPolicy.sha256) { throw 'RELEASE_AUTHORIZATION_ORPHAN_POLICY_CONFLICT' }; $policyReceipt = $canonicalPolicy } else { $policyReceipt = Assert-ReleasePolicyAttempt $releaseAuthorization.policy }
        $null = Assert-CurrentReleasePolicy 'RELEASE_CURRENT_POLICY_INVALID'; if ($null -ne $state.mutation_authorization.release) { throw 'RELEASE_AUTHORIZATION_ORPHAN_CANONICAL_CONFLICT' }; $state.mutation_authorization.release = $releaseAuthorization.reference; if ($null -eq $state.mutation_policy.release) { $state.mutation_policy.release = $policyReceipt } elseif ($state.mutation_policy.release.path -ne $policyReceipt.path -or $state.mutation_policy.release.sha256 -ne $policyReceipt.sha256) { throw 'RELEASE_AUTHORIZATION_ORPHAN_POLICY_CONFLICT' }; Save-State $state; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; $releaseAuthorization = Read-ReleaseAuthorization $true; Write-Output 'ADOPTED_RELEASE_ORPHAN_ATTEMPT'
    } elseif ($null -ne $state.mutation_policy.release) {
        $policyReceipt = Assert-ReleasePolicyAttempt $state.mutation_policy.release; $null = Assert-CurrentReleasePolicy 'RELEASE_CURRENT_POLICY_INVALID'; Write-Output 'REUSE_RECOVERED_RELEASE_POLICY'
    } else {
        $policyReceipt = Read-ReleasePolicy; $state.mutation_policy.release = $policyReceipt; Save-State $state
        $policyState = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($policyState.mutation_policy.release.sha256 -ne $policyReceipt.sha256) { throw 'RELEASE_POLICY_STATE_READBACK_INVALID' }
    }
    $directAgain = @(git -C $root ls-remote --tags origin "refs/tags/$tagName"); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $directAgain.Count -ne 1 -or (($directAgain[0] -split '\s+')[0]) -ne $state.publication.tag.direct_sha) { throw 'TAG_CHANGED_BEFORE_RELEASE' }
    $peeledAgain = @(git -C $root ls-remote --tags origin "refs/tags/$tagName^{}"); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $peeledAgain.Count -ne 1 -or (($peeledAgain[0] -split '\s+')[0]) -ne $mergedSha) { throw 'TAG_PEELED_CHANGED_BEFORE_RELEASE' }
    $remoteRows = @(git -C $root ls-remote --heads origin refs/heads/main refs/heads/codex/gwo-v8-beta1 refs/heads/codex/gwo-v8-ga-plan); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $remoteRows.Count -ne 3) { throw 'RELEASE_REMOTE_IDENTITY_READ_FAILED' }; $remoteMap = @{}; foreach ($row in $remoteRows) { $parts = $row -split '\s+'; if ($parts.Count -ne 2) { throw 'RELEASE_REMOTE_ROW_INVALID' }; $remoteMap[$parts[1]] = $parts[0] }; if ($remoteMap['refs/heads/main'] -ne $mergedSha -or $remoteMap['refs/heads/codex/gwo-v8-beta1'] -ne $state.identities.beta1.sha -or $remoteMap['refs/heads/codex/gwo-v8-ga-plan'] -ne $state.identities.protected_ga.sha) { throw 'RELEASE_REMOTE_IDENTITY_CHANGED' }
    $immediateProbe = @(gh api "repos/$repo/releases/tags/$tagName" 2>&1); $immediateExit = $LASTEXITCODE; if ($immediateExit -eq 0 -or (($immediateProbe -join [Environment]::NewLine) -notmatch 'HTTP 404')) { throw 'RELEASE_PREMUTATION_CONFLICT' }
    if ($null -eq $releaseAuthorization) { $leaseEvidence = Assert-PublicationLease; $releaseAuthorization = Save-ReleaseAuthorization $policyReceipt $leaseEvidence }
    $releaseFinalLease = Assert-PublicationLease; $releaseFinalApprovalHash = (Get-FileHash -LiteralPath $approvalPath -Algorithm SHA256).Hash.ToLowerInvariant(); $releaseFinalLeaseHash = (Get-FileHash -LiteralPath $leasePath -Algorithm SHA256).Hash.ToLowerInvariant(); $releaseFinalNow = [DateTime]::UtcNow; $releaseFinalFrom = [DateTime]::Parse([string]$releaseFinalLease.valid_from).ToUniversalTime(); $releaseFinalUntil = [DateTime]::Parse([string]$releaseFinalLease.valid_until).ToUniversalTime(); if ($releaseFinalApprovalHash -ne $approvalHash -or $releaseFinalLeaseHash -ne $leaseHash -or $releaseFinalNow -lt $releaseFinalFrom -or $releaseFinalNow -ge $releaseFinalUntil) { throw 'RELEASE_FINAL_IMMEDIATE_LEASE_INVALID' }
    gh release create $tagName --repo $repo --verify-tag --prerelease --title 'GWO V8 Beta1 - Core Preview' --notes-file $notesPath; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'RELEASE_CREATE_FAILED' }
} elseif ($releaseWriteRequired) {
    if ($null -eq $state.mutation_policy.release) { throw 'RELEASE_POLICY_MISSING_FOR_EXISTING_EFFECT' }
    $policyReceipt = Assert-ReleasePolicyAttempt $state.mutation_policy.release
    $releaseAuthorization = Read-ReleaseAuthorization $true
    if ($policyReceipt.path -ne $releaseAuthorization.policy.path -or $policyReceipt.sha256 -ne $releaseAuthorization.policy.sha256) { throw 'RELEASE_POLICY_AUTHORIZATION_MISMATCH' }
} else { $orphanDirectory = Join-Path $evidence 'authorizations'; $orphanPaths = @(); if (Test-Path -LiteralPath $orphanDirectory -PathType Container) { $orphanPaths = @(Get-ChildItem -LiteralPath $orphanDirectory -Filter 'release-v8.0.0-beta.1-*.json' -File | Sort-Object FullName | Where-Object { $null -eq $state.mutation_authorization.release -or $_.FullName.Replace([char]92,'/') -ne $state.mutation_authorization.release.authorization_path }) }; if ($orphanPaths.Count -gt 0) { throw 'RELEASE_AUTHORIZATION_ORPHAN_CONFLICT' }; if ($null -ne $state.mutation_policy.release) { throw 'PROVEN_READ_ONLY_RELEASE_HAS_POLICY' }; if ($null -ne $state.mutation_authorization.release) { throw 'PROVEN_READ_ONLY_RELEASE_HAS_AUTHORIZATION' }; if ($null -ne $state.publication.release -and ($null -ne $state.publication.release.authorization_path -or $null -ne $state.publication.release.authorization_sha256)) { throw 'PROVEN_READ_ONLY_RELEASE_HAS_AUTHORIZATION' } }
$releaseRaw = @(gh api "repos/$repo/releases/tags/$tagName" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'RELEASE_READBACK_FAILED' }; $release = ($releaseRaw -join [Environment]::NewLine) | ConvertFrom-Json; $releaseId = [long]$release.id; $releaseUrl = [string]$release.html_url; if ($releaseId -le 0 -or [string]::IsNullOrWhiteSpace($releaseUrl) -or $release.tag_name -ne $tagName -or $release.name -ne 'GWO V8 Beta1 - Core Preview' -or $release.prerelease -ne $true -or $release.draft -ne $false) { throw 'RELEASE_FIELDS_INVALID' }
$body = ([string]$release.body) -replace '\r\n', ([char]10); $notes = (Get-Content -Raw -LiteralPath $notesPath) -replace '\r\n', ([char]10); if ($body -ne $notes) { throw 'RELEASE_BODY_NOT_EXACT_NOTES' }; $bodyBytes = [Text.Encoding]::UTF8.GetBytes($body); $bodyHash = ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($bodyBytes)) -replace '-','').ToLowerInvariant(); if ($bodyHash -ne $state.publication.notes_sha256) { throw 'RELEASE_BODY_DIGEST_INVALID' }
$releaseAuthorizationRef = if ($null -eq $releaseAuthorization) { $null } else { $releaseAuthorization.reference }
$releaseCore = [ordered]@{ schema = 'gwo-v8-c1-release-readback.v2'; id = $releaseId; url = $releaseUrl; tag_name = $tagName; title = [string]$release.name; prerelease = [bool]$release.prerelease; draft = [bool]$release.draft; annotated_tag_object_sha = $state.publication.tag.object_sha; peeled_sha = $state.publication.tag.peeled_sha; notes_sha256 = $state.publication.notes_sha256; body_sha256 = $bodyHash; merged_sha = $mergedSha; authorization_path = if ($null -eq $releaseAuthorizationRef) { $null } else { $releaseAuthorizationRef.authorization_path }; authorization_sha256 = if ($null -eq $releaseAuthorizationRef) { $null } else { $releaseAuthorizationRef.authorization_sha256 } }
$receiptPath = Join-Path $evidence 'release-readback.json'; $receiptText = $releaseCore | ConvertTo-Json -Depth 30; if (Test-Path -LiteralPath $receiptPath -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $receiptPath) -ne $receiptText) { throw 'RELEASE_CANONICAL_RESUME_INVALID' } } else { [IO.File]::WriteAllText($receiptPath,$receiptText,[Text.UTF8Encoding]::new($false)) }
$receiptHash = (Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant(); $state.publication.release = [ordered]@{ schema = $releaseCore.schema; id = $releaseCore.id; url = $releaseCore.url; tag_name = $releaseCore.tag_name; title = $releaseCore.title; prerelease = $releaseCore.prerelease; draft = $releaseCore.draft; annotated_tag_object_sha = $releaseCore.annotated_tag_object_sha; peeled_sha = $releaseCore.peeled_sha; notes_sha256 = $releaseCore.notes_sha256; body_sha256 = $releaseCore.body_sha256; merged_sha = $releaseCore.merged_sha; authorization_path = $releaseCore.authorization_path; authorization_sha256 = $releaseCore.authorization_sha256; receipt_path = $receiptPath.Replace('\','/'); receipt_sha256 = $receiptHash }; if ($releaseWriteRequired) { $state.mutation_policy.release = $policyReceipt; $state.mutation_authorization.release = $releaseAuthorizationRef }; Save-State $state
$check = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($check.publication.release.id -ne $releaseId -or $check.publication.release.peeled_sha -ne $mergedSha -or $check.publication.release.receipt_sha256 -ne $receiptHash -or ($releaseWriteRequired -and $check.mutation_authorization.release.authorization_sha256 -ne $releaseAuthorizationRef.authorization_sha256)) { throw 'RELEASE_STATE_READBACK_INVALID' }
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

Treat all three leases as historical evidence: re-hash/reparse exact schemas,
identities, scopes, and windows, but do not compare closure's clock with those
old windows. Validate every PR-create/PR-ready/merge, tracker, tag, and
Release `authorized_at` against its own original window. `pr_ready` and
`pr_merge` authorization/policy receipts are MANDATORY; `pr_create` follows
`created_by_plan`, tracker receipts follow the frozen before-snapshot
deltas/effects, and tag/Release receipts follow the frozen
`initial_tag_present`/`initial_release_present` observations. Null is valid
only for the corresponding PROVEN_READ_ONLY path.

Require C0 receipt/archive hashes and the approved exception again. Require
exactly three clean execution worktrees: canonical main, active GA, and this
coordinator. A failed temporary checkout is preserved, not deleted. Save a
new closure-preflight directory and digest; do not replace prior evidence.

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'NOT_A_GIT_WORKTREE' }
$root = ([IO.Path]::GetFullPath($root).Replace('\','/')).TrimEnd('/'); $evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'; $statePath = Join-Path $evidence 'state.json'; $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.schema -ne 'gwo-v8-c1-state.v2' -or $state.mode -ne 'Local Verification Only' -or $state.coordinator_root -ne $root) { throw 'STATE_ROOT_INVALID' }
$branch = (git symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }; $head = (git rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head) { throw 'COORDINATOR_HEAD_INVALID' }; $origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
if ($state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or (@($state.identities.base.parents) -join ',') -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or (@($state.identities.beta1.parents) -join ',') -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.integration.tree -ne '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -or (@($state.identities.integration.parents) -join ',') -ne 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577' -or (@($state.identities.protected_ga.parents) -join ',') -ne '3b7097213ac482b3a9dcc31320e7bd84191bf2c0') { throw 'FROZEN_IDENTITY_INVALID' }
if ($state.identities.boundaries.implementation -ne 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2' -or $state.identities.boundaries.beta1 -ne 'ddc1785f84b6a82a7b5c34d5928b046d4e9a781d') { throw 'FROZEN_BOUNDARY_INVALID' }
function Hash-Is([string]$path,[string]$expected) { if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "MISSING:$path" }; $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant(); if ($actual -ne $expected) { throw "HASH_CHANGED:$path" }; return $actual }
function Assert-CommandManifest([object]$manifest,[string]$diffArgument) {
    $python = (Join-Path $evidence 'python313/Scripts/python.exe').Replace('\','/')
    $expectedCommandNames = @('package','full','quick','sync','diff','status')
    $expectedExecutables = @($python,$python,$python,$python,'git','git')
    $expectedArgs = @(@('-m','pytest','tests/test_orchestrator_package.py','-q'),@('-m','pytest','-q'),@('scripts/quick_validate.py'),@('scripts/sync_orchestrator.py','--check'),@('-C','SUBJECT','diff','--check',$diffArgument),@('-C','SUBJECT','status','--porcelain=v1','--untracked-files=all'))
    $commands = @($manifest.commands); if ($commands.Count -ne 6) { throw 'COMMAND_COUNT_INVALID' }
    for ($i = 0; $i -lt 6; $i++) { $actualArgs = @($commands[$i].arguments | ForEach-Object { [string]$_ }); if ($i -in @(4,5)) { if ($actualArgs.Count -lt 2) { throw "COMMAND_ARGUMENTS_INVALID:$i" }; $actualArgs[1] = 'SUBJECT' }; $actualExecutable = ([string]$commands[$i].executable).Replace('\','/'); if ($commands[$i].name -ne $expectedCommandNames[$i] -or $actualExecutable -ne $expectedExecutables[$i]) { throw "COMMAND_IDENTITY_INVALID:$i" }; if (@(Compare-Object $expectedArgs[$i] $actualArgs -SyncWindow 0).Count -ne 0) { throw "COMMAND_ARGUMENTS_INVALID:$i" } }
    $status = $commands[5]; if ($status.output_empty -ne $true -or -not (Test-Path -LiteralPath $status.log -PathType Leaf) -or (Get-Item -LiteralPath $status.log).Length -ne 0) { throw 'STATUS_OUTPUT_NOT_EMPTY' }
}
function Assert-PolicyReceipt([object]$receipt) {
    if ($null -eq $receipt -or [string]::IsNullOrWhiteSpace([string]$receipt.path) -or [string]::IsNullOrWhiteSpace([string]$receipt.sha256) -or -not (Test-Path -LiteralPath $receipt.path -PathType Leaf)) { throw 'POLICY_RECEIPT_HASH_INVALID' }
    $actualReceiptHash = (Get-FileHash -LiteralPath $receipt.path -Algorithm SHA256).Hash.ToLowerInvariant(); if ($actualReceiptHash -ne $receipt.sha256) { throw 'POLICY_RECEIPT_HASH_INVALID' }
    try { $value = Get-Content -Raw -LiteralPath $receipt.path | ConvertFrom-Json } catch { throw 'POLICY_RECEIPT_HASH_INVALID' }
    if ($value.schema -ne 'gwo-v8-c1-mutation-policy.v1' -or $value.label -ne $receipt.label -or $value.repository -ne $state.repository -or $value.default_branch -ne 'main' -or $value.actions_enabled -ne $false -or $value.workflow_count -ne 0 -or $value.ruleset_id -ne 20160628) { throw 'POLICY_RECEIPT_HASH_INVALID' }
    $directory = Split-Path -Path $receipt.path -Parent; $fileValues = [ordered]@{}
    foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) { $property = $value.files.PSObject.Properties[$name]; if ($null -eq $property) { throw 'POLICY_RECEIPT_HASH_INVALID' }; $item = $property.Value; $expectedPath = (Join-Path $directory $name).Replace('\','/'); if ([string]$item.path -cne $expectedPath -or -not (Test-Path -LiteralPath $item.path -PathType Leaf)) { throw 'POLICY_RECEIPT_HASH_INVALID' }; $hash = (Get-FileHash -LiteralPath $item.path -Algorithm SHA256).Hash.ToLowerInvariant(); if ($hash -ne $item.sha256) { throw 'POLICY_RECEIPT_HASH_INVALID' }; $fileValues[$name] = Get-Content -Raw -LiteralPath $item.path | ConvertFrom-Json }
    $actionsObject = $fileValues['actions.json']; $workflowsObject = $fileValues['workflows.json']; $rulesetObject = $fileValues['ruleset.json']; $repositoryObject = $fileValues['repository.json']
    if ($repositoryObject.full_name -ne $state.repository -or $repositoryObject.default_branch -ne 'main' -or $actionsObject.enabled -ne $false -or $workflowsObject.total_count -ne 0 -or $rulesetObject.id -ne 20160628 -or $rulesetObject.enforcement -ne 'active' -or $rulesetObject.source -ne $state.repository -or @($rulesetObject.bypass_actors).Count -ne 0 -or @($rulesetObject.rules | Where-Object type -eq 'required_status_checks').Count -ne 0) { throw 'POLICY_RECEIPT_HASH_INVALID' }
    $types = @($rulesetObject.rules | ForEach-Object type); if (@(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0) { throw 'POLICY_RECEIPT_HASH_INVALID' }; $pullRule = @($rulesetObject.rules | Where-Object type -eq 'pull_request')[0]; $allowedMethods = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowedMethods = @($pullRule.parameters.allowed_merge_methods) }; if ($allowedMethods -notcontains 'squash') { throw 'POLICY_RECEIPT_HASH_INVALID' }; $includes = @(); if ($null -ne $rulesetObject.conditions -and $null -ne $rulesetObject.conditions.ref_name -and $null -ne $rulesetObject.conditions.ref_name.include) { $includes = @($rulesetObject.conditions.ref_name.include) }; if (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main')) { throw 'POLICY_RECEIPT_HASH_INVALID' }
    return [ordered]@{ label = [string]$receipt.label; path = ([string]$receipt.path).Replace('\','/'); sha256 = $actualReceiptHash }
}
function Save-State([object]$value) { $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp'); [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 60),[Text.UTF8Encoding]::new($false)); $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json; [IO.File]::Replace($tmp,$statePath,$null,$true); $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json }
$repo = $state.repository; $repoRaw = @(gh api repos/$repo 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REPOSITORY_READ_FAILED' }; $repoObject = ($repoRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($repoObject.full_name -ne $repo -or $repoObject.default_branch -ne 'main') { throw 'DEFAULT_BRANCH_INVALID' }
$remoteRows = @(git -C $root ls-remote --heads origin refs/heads/main refs/heads/codex/gwo-v8-beta1 refs/heads/codex/gwo-v8-ga-plan); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REMOTE_REFS_FAILED' }; $remote = @{}; foreach ($row in $remoteRows) { $parts = $row -split '\s+'; $remote[$parts[1]] = $parts[0] }; if ($remote['refs/heads/main'] -ne $state.pr.merge.merge_sha -or $remote['refs/heads/codex/gwo-v8-beta1'] -ne $state.identities.beta1.sha -or $remote['refs/heads/codex/gwo-v8-ga-plan'] -ne $state.identities.protected_ga.sha) { throw 'REMOTE_REFS_CHANGED' }
git -C $root fetch --no-tags origin refs/heads/main; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'CLOSURE_MAIN_FETCH_FAILED' }; $merged = (git -C $root rev-parse FETCH_HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $merged -ne $state.pr.merge.merge_sha) { throw 'CLOSURE_MAIN_SHA_INVALID' }; $mergedTree = (git -C $root rev-parse "$merged^{tree}").Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $mergedTree -ne $state.identities.beta1.tree) { throw 'CLOSURE_MAIN_TREE_INVALID' }; $mergedParents = (git -C $root show -s --format=%P $merged).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $mergedParents -ne $state.identities.base.sha) { throw 'CLOSURE_MAIN_PARENT_INVALID' }
$policyDir = Join-Path $evidence ('closure-preflight-' + [guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $policyDir -ErrorAction Stop | Out-Null; $actions = @(gh api repos/$repo/actions/permissions 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'ACTIONS_READBACK_FAILED' }; $workflows = @(gh api repos/$repo/actions/workflows 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'WORKFLOW_READBACK_FAILED' }; $rulesetRaw = @(gh api repos/$repo/rulesets/20160628 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'RULESET_READBACK_FAILED' }; $repositoryRaw = @(gh api repos/$repo 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REPOSITORY_READBACK_FAILED' }; foreach ($item in @(@('actions.json',$actions),@('workflows.json',$workflows),@('ruleset.json',$rulesetRaw),@('repository.json',$repositoryRaw))) { [IO.File]::WriteAllText((Join-Path $policyDir $item[0]),($item[1] -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)) }; $a = ($actions -join [Environment]::NewLine) | ConvertFrom-Json; $w = ($workflows -join [Environment]::NewLine) | ConvertFrom-Json; $r = ($rulesetRaw -join [Environment]::NewLine) | ConvertFrom-Json; $repositoryNow = ($repositoryRaw -join [Environment]::NewLine) | ConvertFrom-Json; if ($repositoryNow.full_name -ne $repo -or $repositoryNow.default_branch -ne 'main' -or $a.enabled -ne $false -or $w.total_count -ne 0 -or $r.id -ne 20160628 -or $r.enforcement -ne 'active' -or $r.source -ne $repo -or @($r.bypass_actors).Count -ne 0 -or @($r.rules | Where-Object type -eq 'required_status_checks').Count -ne 0) { throw 'CLOSURE_POLICY_INVALID' }; $types = @($r.rules | ForEach-Object type); if (@(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0) { throw 'CLOSURE_RULESET_TYPES_INVALID' }; $pullRule = @($r.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }; if ($allowed -notcontains 'squash') { throw 'CLOSURE_SQUASH_POLICY_INVALID' }; $includes = @(); if ($null -ne $r.conditions -and $null -ne $r.conditions.ref_name -and $null -ne $r.conditions.ref_name.include) { $includes = @($r.conditions.ref_name.include) }; if (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main')) { throw 'CLOSURE_RULESET_DEFAULT_BRANCH_INVALID' }
$policyReceiptDigests = [ordered]@{}
foreach ($name in @('beta1','merged_main')) {
    $record = $state.local_verification.$name; Hash-Is ([string]$record.manifest) ([string]$record.manifest_sha256) | Out-Null; $manifest = Get-Content -Raw -LiteralPath $record.manifest | ConvertFrom-Json
    $expectedSubject = if ($name -eq 'beta1') { $state.identities.beta1.sha } else { $merged }
    if ($manifest.schema -ne 'gwo-c1-local-verification.v2' -or $manifest.mode -ne 'Local Verification Only' -or $manifest.subject_sha -ne $expectedSubject -or $manifest.subject_tree -ne $state.identities.beta1.tree -or $manifest.base_sha -ne $state.identities.base.sha -or $manifest.base_tree -ne $state.identities.base.tree -or $manifest.python_version -ne 'Python 3.13.11' -or $manifest.final_outcome -ne 'pass' -or $manifest.workflow_count -ne 0 -or $manifest.requirements_sha256 -ne 'ee3c9f14db38950f5869759a5a94347197c9d4db3f138147b614ad6c4d862534' -or @($manifest.commands).Count -ne 6) { throw "LOCAL_MANIFEST_INVALID:$name" }
    $diffArgument = if ($name -eq 'beta1') { "$($state.identities.base.sha)...$($state.identities.beta1.sha)" } else { "$($state.identities.base.sha)..HEAD" }; Assert-CommandManifest $manifest $diffArgument
    foreach ($command in @($manifest.commands)) { if ($command.exit_code -ne 0) { throw "LOCAL_COMMAND_FAILED:$name" }; Hash-Is ([string]$command.log) ([string]$command.sha256) | Out-Null }
}
$disable = 'D:/gwo-release-evidence/2026-08-05-disable-github-ci'; $successor = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-beta1-successor'; Hash-Is (Join-Path $disable 'manifest.json') '1f01205bc9846bebfd8e767744a60d4d1e4c185f081f6083606047cd37e9d4a3' | Out-Null; Hash-Is (Join-Path $disable 'main-attestation.json') '689ccbdf84667d9931b83f18b4234816a853ca61ba6cca8382117f2179e15818' | Out-Null; Hash-Is (Join-Path $disable 'closure.json') 'dd5dd6724567fee050fe42deecc8bd91baaae674ecba15c0a07cfae474ee386d' | Out-Null; Hash-Is (Join-Path $successor 'manifest.json') '413dd208f18ff6d82d4a64491e03dbfbf06f82712f71b8990d6e95716ecef024' | Out-Null; Hash-Is (Join-Path $successor 'push-receipt.json') '9bee5bd4f6b3a95236b7125cec2f8549fac8914941f8b104582466901a2f26ca' | Out-Null
foreach ($manifestRoot in @($disable,$successor)) { $externalManifest = Get-Content -Raw -LiteralPath (Join-Path $manifestRoot 'manifest.json') | ConvertFrom-Json; foreach ($log in @($externalManifest.logs)) { Hash-Is (Join-Path $manifestRoot ([string]$log.name)) ([string]$log.sha256) | Out-Null } }
$archive = 'D:/gwo-convergence-archive/20260804T185544Z'; Hash-Is (Join-Path $archive 'convergence-manifest.json') 'e6939fbd27eedca2198b87f17de0d14bd3e367a65a37fc51542aa87ade889409' | Out-Null; Hash-Is (Join-Path $archive 'pre-clean.bundle') '5eb64cffaed0ac2fd2748a575cb9cd041b2f7463d4d46d7dbfabf9dbdc0e8530' | Out-Null; Hash-Is (Join-Path $archive 'post-clean.bundle') '9c91a126003e867a3c5736a4e4a69f5c3c079ce1adf5667c1108351181ac4f40' | Out-Null; Hash-Is (Join-Path $archive 'inventory/remote-ga-ref-after.txt') '9b0152f0553f18c1ac6a9aac0c5c2ec3b4ecdb4491835d3ebe0318d2d031c1ea' | Out-Null
$reviewProperties = @($state.reviews.lanes.PSObject.Properties); if ($reviewProperties.Count -ne 5) { throw 'REVIEW_COUNT_INVALID' }; foreach ($lane in $reviewProperties) { $review = $lane.Value; Hash-Is ([string]$review.path) ([string]$review.sha256) | Out-Null; $lines = @(Get-Content -LiteralPath $review.path | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }); if ($lines[-1] -cne 'Verdict: PASS' -or $review.base_sha -ne $state.identities.base.sha -or $review.base_tree -ne $state.identities.base.tree -or $review.beta1_sha -ne $state.identities.beta1.sha -or $review.beta1_tree -ne $state.identities.beta1.tree -or $review.beta1_manifest_sha256 -ne '413dd208f18ff6d82d4a64491e03dbfbf06f82712f71b8990d6e95716ecef024') { throw "REVIEW_INVALID:$($lane.Name)" } }; Hash-Is ([string]$state.reviews.state_path) ([string]$state.reviews.state_sha256) | Out-Null
$approvalSchemas = @{ pr = 'gwo-v8-c1-pr-owner-approval.v1'; tracker = 'gwo-v8-c1-tracker-owner-approval.v1'; publication = 'gwo-v8-c1-publication-owner-approval.v1' }; $leaseSchemas = @{ pr = 'gwo-v8-c1-integration-lease.v1'; tracker = 'gwo-v8-c1-tracker-lease.v1'; publication = 'gwo-v8-c1-publication-lease.v1' }
$expectedScopes = @{ pr = @('create','merge:squash','ready'); tracker = @('milestone:#113=GWO V8 Beta2','milestone:#114=GWO V8 Beta2','milestone:#115=GWO V8 Beta2','milestone:#116=GWO V8 Beta2','milestone:#117=GWO V8 Beta2','milestone:#137=GWO V8 Beta2','milestone:#118=GWO V8 Beta3','milestone:#119=GWO V8 GA','reopen:#137=when_closed_and_#114_or_#115_open'); publication = @('tag:v8.0.0-beta.1','release:v8.0.0-beta.1') }
function Assert-MutationAuthorization([string]$gate,[string]$effectKey,[string]$operation,[object]$reference) {
    if ($null -eq $reference -or $reference.effect_key -ne $effectKey -or [string]::IsNullOrWhiteSpace([string]$reference.authorization_path) -or -not (Test-Path -LiteralPath $reference.authorization_path -PathType Leaf)) { throw "AUTHORIZATION_REFERENCE_INVALID:$effectKey" }
    $authorizationHash = (Get-FileHash -LiteralPath $reference.authorization_path -Algorithm SHA256).Hash.ToLowerInvariant(); if ($authorizationHash -ne $reference.authorization_sha256) { throw "AUTHORIZATION_HASH_INVALID:$effectKey" }
    $gateReceipt = $state.approvals.$gate
    try { $value = Get-Content -Raw -LiteralPath $reference.authorization_path | ConvertFrom-Json; $authorizedAt = [DateTime]::Parse([string]$value.authorized_at).ToUniversalTime(); $leaseFrom = [DateTime]::Parse([string]$gateReceipt.valid_from).ToUniversalTime(); $leaseUntil = [DateTime]::Parse([string]$gateReceipt.valid_until).ToUniversalTime(); $authFrom = [DateTime]::Parse([string]$value.valid_from).ToUniversalTime(); $authUntil = [DateTime]::Parse([string]$value.valid_until).ToUniversalTime() } catch { throw "AUTHORIZATION_MALFORMED:$effectKey" }
    if ($value.schema -ne 'gwo-v8-c1-mutation-authorization.v1' -or $value.operation -ne $operation -or $value.effect_key -ne $effectKey -or $value.repository -ne $repo -or $value.owner -ne $gateReceipt.owner -or $value.lease_id -ne $gateReceipt.lease_id -or $value.approval_sha256 -ne $gateReceipt.approval_sha256 -or $value.lease_sha256 -ne $gateReceipt.lease_sha256 -or $authFrom -ne $leaseFrom -or $authUntil -ne $leaseUntil -or $authorizedAt -lt $leaseFrom -or $authorizedAt -ge $leaseUntil) { throw "AUTHORIZATION_HISTORICAL_WINDOW_INVALID:$effectKey" }
    if (-not (Test-Path -LiteralPath $value.policy_path -PathType Leaf) -or (Get-FileHash -LiteralPath $value.policy_path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $value.policy_sha256) { throw "AUTHORIZATION_POLICY_INVALID:$effectKey" }
    if ($gate -eq 'pr' -and ($value.base_sha -ne $state.identities.base.sha -or $value.head_sha -ne $state.identities.beta1.sha)) { throw 'MERGE_AUTHORIZATION_IDENTITY_INVALID' }; if ($gate -eq 'pr' -and $effectKey -ne 'pr-create' -and [int]$value.pr_number -ne [int]$state.pr.number) { throw 'PR_AUTHORIZATION_NUMBER_INVALID' }
    if ($gate -in @('tracker','publication') -and $value.merged_main_sha -ne $merged) { throw "AUTHORIZATION_MERGED_SHA_INVALID:$effectKey" }; if ($gate -eq 'publication' -and $value.tag_name -ne 'v8.0.0-beta.1') { throw 'RELEASE_AUTHORIZATION_TAG_INVALID' }
    return [ordered]@{ effect_key = $effectKey; authorization_path = ([string]$reference.authorization_path).Replace('\','/'); authorization_sha256 = $authorizationHash; authorized_at = $authorizedAt.ToString('o'); valid_from = $leaseFrom.ToString('o'); valid_until = $leaseUntil.ToString('o'); policy = [ordered]@{ label = [string]$value.policy_label; path = ([string]$value.policy_path).Replace('\','/'); sha256 = [string]$value.policy_sha256 } }
}
foreach ($gate in @('pr','tracker','publication')) {
    $receipt = $state.approvals.$gate; Hash-Is ([string]$receipt.approval_path) ([string]$receipt.approval_sha256) | Out-Null; Hash-Is ([string]$receipt.lease_path) ([string]$receipt.lease_sha256) | Out-Null
    try { $approval = Get-Content -Raw -LiteralPath $receipt.approval_path | ConvertFrom-Json; $lease = Get-Content -Raw -LiteralPath $receipt.lease_path | ConvertFrom-Json; $leaseFrom = [DateTime]::Parse([string]$lease.valid_from).ToUniversalTime(); $leaseUntil = [DateTime]::Parse([string]$lease.valid_until).ToUniversalTime(); $savedFrom = [DateTime]::Parse([string]$receipt.valid_from).ToUniversalTime(); $savedUntil = [DateTime]::Parse([string]$receipt.valid_until).ToUniversalTime() } catch { throw "CLOSURE_LEASE_WINDOW_INVALID:$gate" }
    $scopeField = if ($gate -eq 'pr') { 'action_scope' } else { 'mutation_set' }; $approvalScope = @($approval.$scopeField | ForEach-Object { [string]$_ }); $leaseScope = @($lease.$scopeField | ForEach-Object { [string]$_ }); $savedScope = @($receipt.action_scope | ForEach-Object { [string]$_ }); $expectedScope = @($expectedScopes[$gate])
    if ($approvalScope.Count -ne $expectedScope.Count -or $leaseScope.Count -ne $expectedScope.Count -or $savedScope.Count -ne $expectedScope.Count -or @(Compare-Object ($expectedScope | Sort-Object) ($approvalScope | Sort-Object)).Count -ne 0 -or @(Compare-Object ($expectedScope | Sort-Object) ($leaseScope | Sort-Object)).Count -ne 0 -or @(Compare-Object ($expectedScope | Sort-Object) ($savedScope | Sort-Object)).Count -ne 0) { throw "CLOSURE_APPROVAL_SCOPE_INVALID:$gate" }
    $approvalLeaseId = if ($gate -eq 'pr') { [string]$approval.integration_lease_id } elseif ($gate -eq 'tracker') { [string]$approval.tracker_lease_id } else { [string]$approval.publication_lease_id }
    if ($approval.schema -ne $approvalSchemas[$gate] -or $approval.approved -ne $true -or $approval.repository -ne $repo -or [string]::IsNullOrWhiteSpace([string]$approval.owner) -or $approval.owner -ne $receipt.owner -or $approvalLeaseId -ne $receipt.lease_id -or $lease.schema -ne $leaseSchemas[$gate] -or $lease.state -ne 'active' -or $lease.id -ne $receipt.lease_id -or $lease.owner -ne $receipt.owner -or $lease.repository -ne $repo -or $lease.approval_sha256 -ne $receipt.approval_sha256) { throw "APPROVAL_LEASE_INVALID:$gate" }
    if ($leaseFrom -ge $leaseUntil -or $savedFrom -ne $leaseFrom -or $savedUntil -ne $leaseUntil) { throw "CLOSURE_HISTORICAL_LEASE_WINDOW_INVALID:$gate" }
    if ($gate -eq 'pr') { if ($approval.base_sha -ne $state.identities.base.sha -or $approval.head_sha -ne $state.identities.beta1.sha -or $lease.scope -ne 'repository-global-remote-writer' -or $lease.exclusive_remote_writer_control -ne $true -or $lease.base_sha -ne $state.identities.base.sha -or $lease.base_tree -ne $state.identities.base.tree -or $lease.head_sha -ne $state.identities.beta1.sha -or $lease.head_tree -ne $state.identities.beta1.tree) { throw 'PR_APPROVAL_BINDING_INVALID' } } elseif ($approval.merged_main_sha -ne $merged -or $lease.merged_main_sha -ne $merged) { throw "MERGED_APPROVAL_BINDING_INVALID:$gate" }
}
# MANDATORY campaign policies: pr_ready and pr_merge can never be null.
if ($null -eq $state.mutation_policy.pr_ready -or $null -eq $state.mutation_policy.pr_merge) { throw 'MANDATORY_PR_POLICY_MISSING' }
$readyAuthorization = Assert-MutationAuthorization 'pr' 'pr-ready' 'pr-ready' $state.mutation_authorization.pr_ready; $readyPolicy = Assert-PolicyReceipt $readyAuthorization.policy; if ($readyPolicy.path -ne $state.mutation_policy.pr_ready.path -or $readyPolicy.sha256 -ne $state.mutation_policy.pr_ready.sha256 -or $state.pr.ready_authorization_sha256 -ne $readyAuthorization.authorization_sha256) { throw 'MANDATORY_PR_READY_EVIDENCE_INVALID' }; $policyReceiptDigests['pr_ready'] = [ordered]@{ policy = $readyPolicy; authorization = $readyAuthorization }
$mergeAuthorization = Assert-MutationAuthorization 'pr' 'pr-merge' 'squash-merge' $state.mutation_authorization.pr_merge; $mergePolicy = Assert-PolicyReceipt $mergeAuthorization.policy
if ($mergePolicy.path -ne $state.mutation_policy.pr_merge.path -or $mergePolicy.sha256 -ne $state.mutation_policy.pr_merge.sha256 -or $state.pr.merge.authorization_sha256 -ne $mergeAuthorization.authorization_sha256) { throw 'MANDATORY_PR_MERGE_EVIDENCE_INVALID' }; $policyReceiptDigests['pr_merge'] = [ordered]@{ policy = $mergePolicy; authorization = $mergeAuthorization }
if ($state.pr.created_by_plan -isnot [bool]) { throw 'PR_CREATE_PROVENANCE_INVALID' }
if ($state.pr.created_by_plan -eq $true) {
    if ($null -eq $state.mutation_policy.pr_create) { throw 'PR_CREATE_POLICY_MISSING' }; $createAuthorization = Assert-MutationAuthorization 'pr' 'pr-create' 'pr-create' $state.mutation_authorization.pr_create; $createPolicy = Assert-PolicyReceipt $createAuthorization.policy; if ($createPolicy.path -ne $state.mutation_policy.pr_create.path -or $createPolicy.sha256 -ne $state.mutation_policy.pr_create.sha256 -or $state.pr.create_authorization_sha256 -ne $createAuthorization.authorization_sha256) { throw 'PR_CREATE_POLICY_AUTHORIZATION_BINDING_INVALID' }; $policyReceiptDigests['pr_create'] = [ordered]@{ policy = $createPolicy; authorization = $createAuthorization }
} elseif ($state.pr.created_by_plan -eq $false -and $null -eq $state.mutation_policy.pr_create -and $null -eq $state.mutation_authorization.pr_create) { $policyReceiptDigests['pr_create'] = $null # PROVEN_READ_ONLY PR reuse
} else { throw 'PR_CREATE_PROVENANCE_INVALID' }
$trackerBeforePath = [string]$state.tracker.before_snapshot; Hash-Is $trackerBeforePath ([string]$state.tracker.before_snapshot_sha256) | Out-Null; $trackerBefore = Get-Content -Raw -LiteralPath $trackerBeforePath | ConvertFrom-Json; if ($trackerBefore.schema -ne 'gwo-v8-c1-tracker-snapshot.v2' -or $trackerBefore.merged_main_sha -ne $merged) { throw 'TRACKER_BEFORE_IDENTITY_INVALID' }
$beforeIssues = @{}; foreach ($item in @($trackerBefore.issues)) { $beforeIssues[[int]$item.number] = $item }; $trackerTargets = [ordered]@{ 'GWO V8 Beta2' = @(113,114,115,116,117,137); 'GWO V8 Beta3' = @(118); 'GWO V8 GA' = @(119) }; $requiredTracker = [System.Collections.Generic.List[string]]::new()
foreach ($title in $trackerTargets.Keys) { $frozenMilestone = @($trackerBefore.milestones | Where-Object title -eq $title); if ($frozenMilestone.Count -gt 1) { throw "TRACKER_BEFORE_MILESTONE_CONFLICT:$title" }; if ($frozenMilestone.Count -eq 0) { $requiredTracker.Add('milestone:' + $title) }; foreach ($number in $trackerTargets[$title]) { if (-not $beforeIssues.ContainsKey([int]$number)) { throw "TRACKER_BEFORE_ISSUE_MISSING:$number" }; if ($null -eq $beforeIssues[[int]$number].milestone) { $requiredTracker.Add("issue:${number}:milestone") } elseif ($beforeIssues[[int]$number].milestone.title -ne $title) { throw "TRACKER_BEFORE_MILESTONE_CONFLICT:$number" } } }
if ($beforeIssues[137].state -eq 'closed' -and ($beforeIssues[114].state -eq 'open' -or $beforeIssues[115].state -eq 'open')) { $requiredTracker.Add('issue:137:reopen') }; $requiredTrackerKeys = @($requiredTracker | Sort-Object -Unique); $trackerAuthorizationRefs = @($state.mutation_authorization.tracker)
$trackerStatePolicies = @($state.mutation_policy.tracker); if ($trackerAuthorizationRefs.Count -ne $requiredTrackerKeys.Count -or $trackerStatePolicies.Count -ne $requiredTrackerKeys.Count) { throw 'TRACKER_AUTHORIZATION_SET_INVALID' }; $trackerPolicyDigests = @()
foreach ($effectKey in $requiredTrackerKeys) { $references = @($trackerAuthorizationRefs | Where-Object effect_key -eq $effectKey); if ($references.Count -ne 1) { throw "TRACKER_AUTHORIZATION_SET_INVALID:$effectKey" }; $authorization = Assert-MutationAuthorization 'tracker' $effectKey 'tracker-write' $references[0]; $policy = Assert-PolicyReceipt $authorization.policy; $statePolicy = @($trackerStatePolicies | Where-Object { $_.effect_key -eq $effectKey -and $_.path -eq $policy.path -and $_.sha256 -eq $policy.sha256 }); if ($statePolicy.Count -ne 1) { throw "TRACKER_POLICY_AUTHORIZATION_BINDING_INVALID:$effectKey" }; $trackerPolicyDigests += [ordered]@{ effect_key = $effectKey; policy = $policy; authorization = $authorization } }
$policyReceiptDigests['tracker'] = $trackerPolicyDigests
if ($null -eq $state.publication.PSObject.Properties['initial_tag_present'] -or $null -eq $state.publication.PSObject.Properties['initial_release_present'] -or $state.publication.initial_tag_present -isnot [bool] -or $state.publication.initial_release_present -isnot [bool]) { throw 'PUBLICATION_INITIAL_OBSERVATION_MISSING' }
if (-not [bool]$state.publication.initial_tag_present) {
    if ($null -eq $state.mutation_policy.tag) { throw 'TAG_POLICY_REQUIRED_BY_INITIAL_STATE' }; $tagAuthorization = Assert-MutationAuthorization 'publication' 'tag:v8.0.0-beta.1' 'tag-push' $state.mutation_authorization.tag; $tagPolicy = Assert-PolicyReceipt $tagAuthorization.policy; if ($tagPolicy.path -ne $state.mutation_policy.tag.path -or $tagPolicy.sha256 -ne $state.mutation_policy.tag.sha256 -or $state.publication.tag.authorization_sha256 -ne $tagAuthorization.authorization_sha256) { throw 'TAG_POLICY_AUTHORIZATION_BINDING_INVALID' }; $policyReceiptDigests['tag'] = [ordered]@{ policy = $tagPolicy; authorization = $tagAuthorization }
} elseif ($null -eq $state.mutation_policy.tag -and $null -eq $state.mutation_authorization.tag -and $null -eq $state.publication.tag.authorization_path -and $null -eq $state.publication.tag.authorization_sha256) { $policyReceiptDigests['tag'] = $null # PROVEN_READ_ONLY tag
} else { throw 'PROVEN_READ_ONLY_TAG_HAS_POLICY_OR_AUTHORIZATION' }
if (-not [bool]$state.publication.initial_release_present) { if ($null -eq $state.mutation_policy.release) { throw 'RELEASE_POLICY_REQUIRED_BY_INITIAL_STATE' }; $releaseAuthorization = Assert-MutationAuthorization 'publication' 'release:v8.0.0-beta.1' 'release-create' $state.mutation_authorization.release; $releasePolicy = Assert-PolicyReceipt $releaseAuthorization.policy; if ($releasePolicy.path -ne $state.mutation_policy.release.path -or $releasePolicy.sha256 -ne $state.mutation_policy.release.sha256) { throw 'RELEASE_POLICY_AUTHORIZATION_BINDING_INVALID' }; $policyReceiptDigests['release'] = [ordered]@{ policy = $releasePolicy; authorization = $releaseAuthorization }
} elseif ($null -eq $state.mutation_policy.release -and $null -eq $state.mutation_authorization.release) { $policyReceiptDigests['release'] = $null # PROVEN_READ_ONLY Release
} else { throw 'PROVEN_READ_ONLY_RELEASE_HAS_MUTATION_EVIDENCE' }
Hash-Is ([string]$state.tracker.before_snapshot) ([string]$state.tracker.before_snapshot_sha256) | Out-Null; Hash-Is ([string]$state.tracker.after_snapshot) ([string]$state.tracker.after_snapshot_sha256) | Out-Null; $tracker = Get-Content -Raw -LiteralPath $state.tracker.after_snapshot | ConvertFrom-Json; if ($tracker.schema -ne 'gwo-v8-c1-tracker-snapshot.v2') { throw 'TRACKER_AFTER_SCHEMA_INVALID' }; $expectedMilestones = @{ 113='GWO V8 Beta2';114='GWO V8 Beta2';115='GWO V8 Beta2';116='GWO V8 Beta2';117='GWO V8 Beta2';137='GWO V8 Beta2';118='GWO V8 Beta3';119='GWO V8 GA' }; foreach ($issue in @($tracker.issues)) { $key = [int]$issue.number; if ($expectedMilestones.ContainsKey($key) -and $issue.milestone.title -ne $expectedMilestones[$key]) { throw "TRACKER_MAPPING_INVALID:$key" }; if ($key -ge 113 -and $key -le 119 -and $issue.state -ne 'open') { throw "ISSUE_WAS_CLOSED:$key" } }
$trackerEffects = @{}; foreach ($effectReference in @($state.tracker.effects)) { Hash-Is ([string]$effectReference.path) ([string]$effectReference.sha256) | Out-Null; $effect = Get-Content -Raw -LiteralPath $effectReference.path | ConvertFrom-Json; if ($effect.schema -ne 'gwo-v8-c1-tracker-effect.v2' -or [string]::IsNullOrWhiteSpace([string]$effect.effect_key) -or $trackerEffects.ContainsKey([string]$effect.effect_key)) { throw 'TRACKER_EFFECT_RECEIPT_INVALID' }; $trackerEffects[[string]$effect.effect_key] = $effect }
foreach ($effectKey in $requiredTrackerKeys) { if (-not $trackerEffects.ContainsKey($effectKey)) { throw "TRACKER_REQUIRED_EFFECT_MISSING:$effectKey" }; $effect = $trackerEffects[$effectKey]; $authorization = @($trackerPolicyDigests | Where-Object effect_key -eq $effectKey)[0].authorization; if ($effect.authorization_path -ne $authorization.authorization_path -or $effect.authorization_sha256 -ne $authorization.authorization_sha256) { throw "TRACKER_EFFECT_AUTHORIZATION_BINDING_INVALID:$effectKey" } }; foreach ($effectKey in @($trackerEffects.Keys)) { if ($requiredTrackerKeys -notcontains $effectKey -and ($null -ne $trackerEffects[$effectKey].authorization_path -or $null -ne $trackerEffects[$effectKey].authorization_sha256)) { throw "PROVEN_READ_ONLY_TRACKER_EFFECT_HAS_AUTHORIZATION:$effectKey" } }
Hash-Is ([string]$state.publication.tag.ref_path) ([string]$state.publication.tag.ref_sha256) | Out-Null; Hash-Is ([string]$state.publication.tag.object_path) ([string]$state.publication.tag.object_sha256) | Out-Null; Hash-Is ([string]$state.publication.release.receipt_path) ([string]$state.publication.release.receipt_sha256) | Out-Null
$releaseReceipt = Get-Content -Raw -LiteralPath $state.publication.release.receipt_path | ConvertFrom-Json
if (-not [bool]$state.publication.initial_release_present) { $requiredReleaseAuthorization = $policyReceiptDigests['release'].authorization; if ($releaseReceipt.authorization_path -ne $requiredReleaseAuthorization.authorization_path -or $releaseReceipt.authorization_sha256 -ne $requiredReleaseAuthorization.authorization_sha256 -or $state.publication.release.authorization_sha256 -ne $requiredReleaseAuthorization.authorization_sha256) { throw 'RELEASE_RECEIPT_AUTHORIZATION_BINDING_INVALID' }
} elseif ($null -ne $releaseReceipt.authorization_path -or $null -ne $releaseReceipt.authorization_sha256) { throw 'PROVEN_READ_ONLY_RELEASE_RECEIPT_HAS_AUTHORIZATION' }
$tagName = 'v8.0.0-beta.1'; $direct = @(git -C $root ls-remote --tags origin "refs/tags/$tagName"); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $direct.Count -ne 1 -or (($direct[0] -split '\s+')[0]) -ne $state.publication.tag.object_sha) { throw 'CLOSURE_TAG_DIRECT_INVALID' }; $peeled = @(git -C $root ls-remote --tags origin "refs/tags/$tagName^{}"); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $peeled.Count -ne 1 -or (($peeled[0] -split '\s+')[0]) -ne $merged) { throw 'CLOSURE_TAG_PEELED_INVALID' }
$releaseRaw = @(gh api "repos/$repo/releases/tags/$tagName" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'CLOSURE_RELEASE_READ_FAILED' }; $release = ($releaseRaw -join [Environment]::NewLine) | ConvertFrom-Json
$body = ([string]$release.body) -replace '\r\n',([char]10); $bodyHash = ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($body))) -replace '-','').ToLowerInvariant(); $notes = (Get-Content -Raw -LiteralPath $state.publication.notes_path) -replace '\r\n',([char]10)
if ($release.tag_name -ne $tagName -or $release.name -ne 'GWO V8 Beta1 - Core Preview' -or $release.prerelease -ne $true -or $release.draft -ne $false -or $release.body -eq $null -or $body -cne $notes -or $release.tag_name -ne $state.publication.release.tag_name -or $release.name -ne $state.publication.release.title -or $release.prerelease -ne $state.publication.release.prerelease -or $release.draft -ne $state.publication.release.draft -or $release.html_url -ne $state.publication.release.url -or [long]$release.id -ne [long]$state.publication.release.id -or $bodyHash -ne $state.publication.release.body_sha256 -or $releaseReceipt.schema -ne 'gwo-v8-c1-release-readback.v2' -or $releaseReceipt.tag_name -ne $release.tag_name -or $releaseReceipt.title -ne $release.name -or $releaseReceipt.prerelease -ne $release.prerelease -or $releaseReceipt.draft -ne $release.draft -or $releaseReceipt.url -ne $release.html_url -or [long]$releaseReceipt.id -ne [long]$release.id -or $releaseReceipt.body_sha256 -ne $bodyHash -or $releaseReceipt.notes_sha256 -ne $state.publication.notes_sha256 -or $releaseReceipt.peeled_sha -ne $merged) { throw 'RELEASE_CLOSURE_READBACK_INVALID' }
$c0Text = @(git -C $root show "$($state.identities.beta1.sha):docs/releases/gwo-v8-workspace-convergence.md"); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'C0_RECEIPT_READ_FAILED' }; $c0Matches = [regex]::Matches(($c0Text -join [Environment]::NewLine),'(?ms)^(?<fence>```|~~~)json\s*\r?\n(?<json>\{.*?\})\s*\r?\n\k<fence>\s*(?:\r?\n|$)'); if ($c0Matches.Count -ne 1) { throw 'C0_RECEIPT_JSON_INVALID' }; $c0 = $c0Matches[0].Groups['json'].Value | ConvertFrom-Json; if ($c0.source_sha -ne $state.identities.boundaries.implementation -or $c0.protected_remote_sha -ne $state.identities.protected_ga.sha -or $c0.refs_deleted -ne $false) { throw 'C0_RECEIPT_FIELDS_INVALID' }; $c0Report = 'D:/Workstation/gwo-worktrees/issue-136/.superpowers/sdd/2026-08-04-gwo-v8-workspace-convergence-gate/task-8-report.md'; if (@(Select-String -LiteralPath $c0Report -Pattern '^Phase 1 is \*\*PASS\*\* under the approved verification-subject exception\.' -CaseSensitive).Count -ne 1) { throw 'C0_APPROVAL_MISSING' }
$rows = @(git -C $root worktree list --porcelain); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'WORKTREE_LIST_FAILED' }; $worktreeRoots = @($rows | Where-Object { $_ -like 'worktree *' } | ForEach-Object { ([IO.Path]::GetFullPath($_.Substring(9)).Replace('\','/')).TrimEnd('/') } | Sort-Object -Unique); $expectedRoots = @('D:/Workstation/github-work-orchestrator','D:/Workstation/gwo-worktrees/issue-136',$root) | Sort-Object -Unique; if (@(Compare-Object $expectedRoots $worktreeRoots).Count -ne 0) { throw 'WORKTREE_SET_INVALID' }; foreach ($path in $expectedRoots) { $dirty = @(git -C $path status --porcelain=v1 --untracked-files=all); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $dirty.Count -ne 0) { throw "DIRTY_WORKTREE:$path" } }
$preflight = [ordered]@{ schema = 'gwo-v8-c1-closure-preflight.v1'; merged_sha = $merged; merged_tree = $mergedTree; merged_parent = $mergedParents; remote_refs = $remote; actions_enabled = $a.enabled; workflow_count = $w.total_count; rule_types = @($types | Sort-Object); allowed_merge_methods = $allowed; mutation_policy = $policyReceiptDigests; reviews_state_sha256 = $state.reviews.state_sha256; tracker_after_sha256 = $state.tracker.after_snapshot_sha256; tag_object_sha = $state.publication.tag.object_sha; tag_peeled_sha = $state.publication.tag.peeled_sha; release_id = $state.publication.release.id; worktrees = $expectedRoots }; $preflightPath = Join-Path $policyDir 'preflight.json'; [IO.File]::WriteAllText($preflightPath,($preflight | ConvertTo-Json -Depth 50),[Text.UTF8Encoding]::new($false)); $preflightHash = (Get-FileHash -LiteralPath $preflightPath -Algorithm SHA256).Hash.ToLowerInvariant(); $state.closure_preflight = [ordered]@{ directory = $policyDir.Replace('\','/'); path = $preflightPath.Replace('\','/'); sha256 = $preflightHash; mutation_policy = $policyReceiptDigests }; Save-State $state; $check = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($check.closure_preflight.sha256 -ne $preflightHash) { throw 'CLOSURE_PREFLIGHT_STATE_INVALID' }
~~~

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
$branch = (git -C $root symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git -C $root rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head -or $root -ne $state.coordinator_root) { throw 'COORDINATOR_IDENTITY_INVALID' }
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
if ($state.identities.base.ref -ne 'refs/heads/main' -or $state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or (@($state.identities.base.parents) -join ',') -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -or $state.identities.beta1.ref -ne 'refs/heads/codex/gwo-v8-beta1' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.integration.tree -ne '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -or (@($state.identities.integration.parents) -join ',') -ne 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.protected_ga.ref -ne 'refs/heads/codex/gwo-v8-ga-plan' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577' -or (@($state.identities.protected_ga.parents) -join ',') -ne '3b7097213ac482b3a9dcc31320e7bd84191bf2c0') { throw 'FROZEN_IDENTITY_INVALID' }
if ($state.identities.boundaries.implementation -ne 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2' -or $state.identities.boundaries.beta1 -ne 'ddc1785f84b6a82a7b5c34d5928b046d4e9a781d') { throw 'FROZEN_BOUNDARY_INVALID' }
if ($state.pr.merge.method -ne 'squash' -or $state.pr.merge.tree -ne $state.identities.beta1.tree -or $state.pr.merge.parents.Count -ne 1 -or $state.pr.merge.parents[0] -ne $state.identities.base.sha -or $state.pr.merge.merge_sha -notmatch '^[0-9a-f]{40}$') { throw 'MERGE_RECEIPT_INVALID' }
function Hash-File([string]$path) { if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "FILE_MISSING:$path" }; return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Save-State([object]$value) { $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp'); [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 60),[Text.UTF8Encoding]::new($false)); try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }; [IO.File]::Replace($tmp,$statePath,$null,$true); try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' } }
$approvalPath = Join-Path $evidence 'approvals/publication-owner.json'; $leasePath = Join-Path $evidence 'approvals/publication-lease.json'; $approvalHash = Hash-File $approvalPath; $leaseHash = Hash-File $leasePath
try { $approval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json; $lease = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json } catch { throw 'PUBLICATION_APPROVAL_OR_LEASE_MALFORMED' }
$publicationScope = @('tag:v8.0.0-beta.1','release:v8.0.0-beta.1'); $approvalScopeDiff = @(Compare-Object ($publicationScope | Sort-Object) (@($approval.mutation_set | ForEach-Object { [string]$_ } | Sort-Object))); $leaseScopeDiff = @(Compare-Object ($publicationScope | Sort-Object) (@($lease.mutation_set | ForEach-Object { [string]$_ } | Sort-Object)))
if ($approval.schema -ne 'gwo-v8-c1-publication-owner-approval.v1' -or $approval.approved -ne $true -or $approval.repository -ne $state.repository -or $approval.merged_main_sha -ne $state.pr.merge.merge_sha -or [string]::IsNullOrWhiteSpace([string]$approval.owner) -or [string]::IsNullOrWhiteSpace([string]$approval.publication_lease_id) -or $approvalScopeDiff.Count -ne 0) { throw 'PUBLICATION_APPROVAL_INVALID' }
try { $leaseFrom = [DateTime]::Parse([string]$lease.valid_from).ToUniversalTime(); $leaseUntil = [DateTime]::Parse([string]$lease.valid_until).ToUniversalTime() } catch { throw 'PUBLICATION_LEASE_WINDOW_INVALID' }
if ($lease.schema -ne 'gwo-v8-c1-publication-lease.v1' -or $lease.state -ne 'active' -or $lease.id -ne $approval.publication_lease_id -or $lease.owner -ne $approval.owner -or $lease.repository -ne $state.repository -or $lease.merged_main_sha -ne $state.pr.merge.merge_sha -or $lease.approval_sha256 -ne $approvalHash -or $leaseScopeDiff.Count -ne 0 -or $state.approvals.publication.approval_sha256 -ne $approvalHash -or $state.approvals.publication.lease_sha256 -ne $leaseHash) { throw 'PUBLICATION_LEASE_INVALID' }
$preflightPath = [string]$state.closure_preflight.path; $preflightHash = Hash-File $preflightPath; if ($preflightHash -ne $state.closure_preflight.sha256) { throw 'CLOSURE_PREFLIGHT_HASH_INVALID' }; $preflight = Get-Content -Raw -LiteralPath $preflightPath | ConvertFrom-Json; if ($preflight.schema -ne 'gwo-v8-c1-closure-preflight.v1' -or $preflight.merged_sha -ne $state.pr.merge.merge_sha) { throw 'CLOSURE_PREFLIGHT_INVALID' }
$canonicalExpected = 'D:/Workstation/github-work-orchestrator'; $canonicalRoot = (git -C $canonicalExpected rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'CANONICAL_ROOT_UNAVAILABLE' }; $canonicalRoot = ([IO.Path]::GetFullPath($canonicalRoot).Replace('\','/')).TrimEnd('/'); if ($canonicalRoot -cne $canonicalExpected) { throw 'CANONICAL_ROOT_INVALID' }
$canonicalOrigin = (git -C $canonicalRoot remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $canonicalOrigin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'CANONICAL_ORIGIN_INVALID' }
$canonicalBranch = (git -C $canonicalRoot symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $canonicalBranch -cne 'main') { throw 'CANONICAL_BRANCH_INVALID' }
$canonicalDirty = @(git -C $canonicalRoot status --porcelain=v1 --untracked-files=all); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $canonicalDirty.Count -ne 0) { throw 'CANONICAL_WORKTREE_NOT_CLEAN' }
$canonicalOld = (git -C $canonicalRoot rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or ($canonicalOld -ne $state.identities.base.sha -and $canonicalOld -ne $state.pr.merge.merge_sha)) { throw 'CANONICAL_OLD_SHA_INVALID' }
$receiptPath = Join-Path $evidence 'canonical-main-readback.json'; $existingReceipt = $null; if (Test-Path -LiteralPath $receiptPath -PathType Leaf) { $existingReceiptHash = Hash-File $receiptPath; if ($null -ne $state.canonical_main -and $state.canonical_main.receipt_sha256 -ne $existingReceiptHash) { throw 'CANONICAL_RECEIPT_STATE_CONFLICT' }; try { $existingReceipt = Get-Content -Raw -LiteralPath $receiptPath | ConvertFrom-Json } catch { throw 'CANONICAL_RECEIPT_MALFORMED' }; if ($existingReceipt.schema -ne 'gwo-v8-c1-canonical-main.v1' -or $existingReceipt.approval_sha256 -ne $approvalHash -or $existingReceipt.lease_sha256 -ne $leaseHash) { throw 'CANONICAL_RECEIPT_IDENTITY_INVALID' } }
$repo = $state.repository; if ($null -eq $existingReceipt) { $policyDir = Join-Path $evidence ('policy-before-canonical-main-' + [guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $policyDir -ErrorAction Stop | Out-Null } else { $policyDir = [string]$existingReceipt.policy_directory; if (-not (Test-Path -LiteralPath $policyDir -PathType Container)) { throw 'CANONICAL_POLICY_DIRECTORY_MISSING' } }
$actionsRaw = @(gh api "repos/$repo/actions/permissions" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'ACTIONS_READBACK_FAILED' }; $workflowsRaw = @(gh api "repos/$repo/actions/workflows" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'WORKFLOW_READBACK_FAILED' }; $rulesetRaw = @(gh api "repos/$repo/rulesets/20160628" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'RULESET_READBACK_FAILED' }; $repoRaw = @(gh api "repos/$repo" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REPOSITORY_READBACK_FAILED' }
$actions = ($actionsRaw -join [Environment]::NewLine) | ConvertFrom-Json; $workflows = ($workflowsRaw -join [Environment]::NewLine) | ConvertFrom-Json; $ruleset = ($rulesetRaw -join [Environment]::NewLine) | ConvertFrom-Json; $repoObject = ($repoRaw -join [Environment]::NewLine) | ConvertFrom-Json
if ($repoObject.full_name -ne $repo -or $repoObject.default_branch -ne 'main' -or $actions.enabled -ne $false -or $workflows.total_count -ne 0 -or $ruleset.id -ne 20160628 -or $ruleset.enforcement -ne 'active' -or $ruleset.source -ne $repo -or @($ruleset.bypass_actors).Count -ne 0 -or @($ruleset.rules | Where-Object type -eq 'required_status_checks').Count -ne 0) { throw 'CANONICAL_POLICY_INVALID' }
$types = @($ruleset.rules | ForEach-Object type); if (@(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0) { throw 'CANONICAL_RULESET_TYPES_INVALID' }; $pullRule = @($ruleset.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }; if ($allowed -notcontains 'squash') { throw 'CANONICAL_SQUASH_NOT_ALLOWED' }; $includes = @(); if ($null -ne $ruleset.conditions -and $null -ne $ruleset.conditions.ref_name -and $null -ne $ruleset.conditions.ref_name.include) { $includes = @($ruleset.conditions.ref_name.include) }; if (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main')) { throw 'CANONICAL_RULESET_DEFAULT_BRANCH_INVALID' }
if ($null -eq $existingReceipt) { foreach ($item in @(@('actions.json',$actionsRaw),@('workflows.json',$workflowsRaw),@('ruleset.json',$rulesetRaw),@('repository.json',$repoRaw))) { [IO.File]::WriteAllText((Join-Path $policyDir $item[0]),($item[1] -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false)) } }
$remoteRows = @(git -C $root ls-remote --heads origin refs/heads/main refs/heads/codex/gwo-v8-beta1 refs/heads/codex/gwo-v8-ga-plan); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $remoteRows.Count -ne 3) { throw 'REMOTE_REF_READ_FAILED' }; $remote = @{}; foreach ($row in $remoteRows) { $parts = $row -split '\s+'; if ($parts.Count -ne 2) { throw 'REMOTE_REF_ROW_INVALID' }; $remote[$parts[1]] = $parts[0] }; if ($remote['refs/heads/main'] -ne $state.pr.merge.merge_sha -or $remote['refs/heads/codex/gwo-v8-beta1'] -ne $state.identities.beta1.sha -or $remote['refs/heads/codex/gwo-v8-ga-plan'] -ne $state.identities.protected_ga.sha) { throw 'REMOTE_IDENTITY_INVALID' }
git -C $canonicalRoot fetch --no-tags origin refs/heads/main; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'CANONICAL_MAIN_FETCH_FAILED' }; $fetched = (git -C $canonicalRoot rev-parse FETCH_HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $fetched -ne $state.pr.merge.merge_sha) { throw 'CANONICAL_FETCHED_SHA_INVALID' }
$auth = $approval.local_writer_authorization; $action = if ($null -eq $existingReceipt) { 'read-only' } else { [string]$existingReceipt.action }; if ($action -notin @('read-only','authorized-fast-forward','already-target')) { throw 'CANONICAL_RECEIPT_ACTION_INVALID' }
if ($null -ne $existingReceipt -and ([bool]$existingReceipt.authorization_present -ne ($null -ne $auth) -or $canonicalOld -ne $existingReceipt.canonical_sha)) { throw 'CANONICAL_RECEIPT_RESUME_INVALID' }
if ($null -ne $auth) { if ($auth.schema -ne 'gwo-v8-c1-local-writer-authorization.v1' -or $auth.approved -ne $true -or $auth.branch -ne 'main' -or $auth.from_sha -ne $state.identities.base.sha -or $auth.target_sha -ne $state.pr.merge.merge_sha) { throw 'LOCAL_WRITER_AUTHORIZATION_INVALID' }; if ([DateTime]::UtcNow -lt $leaseFrom -or [DateTime]::UtcNow -ge $leaseUntil) { throw 'LOCAL_WRITER_LEASE_NOT_ACTIVE' }; if ($null -eq $existingReceipt -and $canonicalOld -eq $state.identities.base.sha) { git -C $canonicalRoot merge-base --is-ancestor $canonicalOld $fetched; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'CANONICAL_FAST_FORWARD_ANCESTRY_INVALID' }; git -C $canonicalRoot merge --ff-only $fetched; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'CANONICAL_FAST_FORWARD_FAILED' }; $action = 'authorized-fast-forward' } elseif ($null -eq $existingReceipt) { $action = 'already-target' } }
$canonicalAfterBranch = (git -C $canonicalRoot symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $canonicalAfterBranch -ne 'main') { throw 'CANONICAL_BRANCH_READBACK_INVALID' }; $canonicalAfter = (git -C $canonicalRoot rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; $expectedAfter = if ($null -eq $auth) { $canonicalOld } else { $state.pr.merge.merge_sha }; if ($exit -ne 0 -or $canonicalAfter -ne $expectedAfter) { throw 'CANONICAL_SHA_READBACK_INVALID' }; $dirtyAfter = @(git -C $canonicalRoot status --porcelain=v1 --untracked-files=all); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $dirtyAfter.Count -ne 0) { throw 'CANONICAL_STATUS_READBACK_INVALID' }
$remoteAfterRows = @(git -C $root ls-remote --heads origin refs/heads/main refs/heads/codex/gwo-v8-beta1 refs/heads/codex/gwo-v8-ga-plan); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $remoteAfterRows.Count -ne 3) { throw 'REMOTE_READBACK_FAILED' }; $remoteAfter = @{}; foreach ($row in $remoteAfterRows) { $parts = $row -split '\s+'; $remoteAfter[$parts[1]] = $parts[0] }; if ($remoteAfter['refs/heads/main'] -ne $state.pr.merge.merge_sha -or $remoteAfter['refs/heads/codex/gwo-v8-beta1'] -ne $state.identities.beta1.sha -or $remoteAfter['refs/heads/codex/gwo-v8-ga-plan'] -ne $state.identities.protected_ga.sha) { throw 'REMOTE_READBACK_IDENTITY_INVALID' }
$policyHashes = [ordered]@{}; foreach ($name in @('actions.json','workflows.json','ruleset.json','repository.json')) { $policyHashes[$name] = Hash-File (Join-Path $policyDir $name); if ($null -ne $existingReceipt -and $existingReceipt.policy_sha256.PSObject.Properties[$name].Value -ne $policyHashes[$name]) { throw "CANONICAL_POLICY_RECEIPT_HASH_INVALID:$name" } }; $receiptOld = if ($null -eq $existingReceipt) { $canonicalOld } else { [string]$existingReceipt.old_sha }; $receipt = [ordered]@{ schema = 'gwo-v8-c1-canonical-main.v1'; action = $action; authorization_present = ($null -ne $auth); approval_sha256 = $approvalHash; lease_sha256 = $leaseHash; canonical_root = $canonicalRoot; branch = $canonicalAfterBranch; old_sha = $receiptOld; canonical_sha = $canonicalAfter; fetched_main_sha = $fetched; remote_main_sha = $remoteAfter['refs/heads/main']; beta1_sha = $remoteAfter['refs/heads/codex/gwo-v8-beta1']; protected_ga_sha = $remoteAfter['refs/heads/codex/gwo-v8-ga-plan']; policy_directory = $policyDir.Replace('\','/'); policy_sha256 = $policyHashes }
$receiptText = $receipt | ConvertTo-Json -Depth 40; if (Test-Path -LiteralPath $receiptPath -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $receiptPath) -ne $receiptText) { throw 'CANONICAL_RECEIPT_CONFLICT' } } else { [IO.File]::WriteAllText($receiptPath,$receiptText,[Text.UTF8Encoding]::new($false)) }; $receiptHash = Hash-File $receiptPath; $state.canonical_main = [ordered]@{ action = $action; receipt_path = $receiptPath.Replace('\','/'); receipt_sha256 = $receiptHash; canonical_sha = $canonicalAfter; remote_main_sha = $remoteAfter['refs/heads/main'] }; Save-State $state; $check = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($check.canonical_main.receipt_sha256 -ne $receiptHash -or $check.canonical_main.canonical_sha -ne $canonicalAfter -or $check.canonical_main.remote_main_sha -ne $state.pr.merge.merge_sha) { throw 'CANONICAL_STATE_READBACK_INVALID' }
~~~

This is a local branch update only and never a remote push. An absent
authorization records a read-only receipt without moving the branch; an
existing matching receipt is verified and resumed without overwrite.

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
$branch = (git -C $root symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $branch -ne $state.coordinator_branch) { throw 'COORDINATOR_BRANCH_INVALID' }
$head = (git -C $root rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $head -ne $state.coordinator_head -or $root -ne $state.coordinator_root) { throw 'COORDINATOR_IDENTITY_INVALID' }
$origin = (git -C $root remote get-url origin).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $origin -notmatch '^https://github\.com/NOirBRight/github-work-orchestrator(?:\.git)?$') { throw 'ORIGIN_REPOSITORY_INVALID' }
if ($state.identities.base.ref -ne 'refs/heads/main' -or $state.identities.base.sha -ne '2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.base.tree -ne '1905079fa3cd0d90dd9b1930ed5dd726fad9f114' -or (@($state.identities.base.parents) -join ',') -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22' -or $state.identities.beta1.ref -ne 'refs/heads/codex/gwo-v8-beta1' -or $state.identities.beta1.sha -ne '70eaa70d5e87ff4f7a6791facd254abab8ff1377' -or $state.identities.beta1.tree -ne '663c5b12502554890bdd92fad6bffc5d6aa9c5f1' -or (@($state.identities.beta1.parents) -join ',') -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.integration.sha -ne '3fe3bb829f844627cac82a2d5a24bac8e58564b9' -or $state.identities.integration.tree -ne '5bbf203cf06b65e5e7c7e0c05059d0a1ce0b4b10' -or (@($state.identities.integration.parents) -join ',') -ne 'e081e39054b7f9f0a49824eed8354a8a33378ea3,2c72d9a153dac07e507c746548258efc44b62875' -or $state.identities.protected_ga.ref -ne 'refs/heads/codex/gwo-v8-ga-plan' -or $state.identities.protected_ga.sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $state.identities.protected_ga.tree -ne 'd59a7414cf7f4873d0e1fc03cc2be8a9f18a6577' -or (@($state.identities.protected_ga.parents) -join ',') -ne '3b7097213ac482b3a9dcc31320e7bd84191bf2c0' -or $state.identities.boundaries.implementation -ne 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2' -or $state.identities.boundaries.beta1 -ne 'ddc1785f84b6a82a7b5c34d5928b046d4e9a781d') { throw 'FROZEN_IDENTITY_INVALID' }
function Hash-File([string]$path) { if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "FILE_MISSING:$path" }; return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Save-State([object]$value) { $tmp = Join-Path $evidence ('.state.' + [guid]::NewGuid().ToString('N') + '.tmp'); [IO.File]::WriteAllText($tmp,($value | ConvertTo-Json -Depth 80),[Text.UTF8Encoding]::new($false)); try { $null = Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json } catch { throw 'STATE_TEMP_PARSE_FAILED' }; [IO.File]::Replace($tmp,$statePath,$null,$true); try { $null = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } catch { throw 'STATE_READBACK_FAILED' } }
function Optional-Collection([string]$endpoint,[string]$name) { $raw = @(gh api $endpoint 2>&1); $code = $LASTEXITCODE; if ($code -eq 0) { try { return @(($raw -join [Environment]::NewLine) | ConvertFrom-Json) } catch { throw "${name}_MALFORMED" } }; if (($raw -join [Environment]::NewLine) -match 'HTTP 404|status.?404') { return @() }; throw "${name}_READ_FAILED" }
function Read-Ticket([int]$number) { $raw = @(gh api "repos/$repo/issues/$number" 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw "ISSUE_READ_FAILED:$number" }; $issue = ($raw -join [Environment]::NewLine) | ConvertFrom-Json; $commentRaw = @(gh api --paginate --slurp "repos/$repo/issues/$number/comments?per_page=100" 2>&1); $code = $LASTEXITCODE; if ($code -ne 0) { throw "COMMENTS_READ_FAILED:$number" }; $pages = ($commentRaw -join [Environment]::NewLine) | ConvertFrom-Json; $comments = @($pages | ForEach-Object { $_ } | ForEach-Object { [ordered]@{ id = $_.id; user = $_.user.login; body = $_.body; created_at = $_.created_at; updated_at = $_.updated_at; html_url = $_.html_url } }); $blockedBy = Optional-Collection "repos/$repo/issues/$number/dependencies/blocked_by" "BLOCKED_BY_$number"; $blocking = Optional-Collection "repos/$repo/issues/$number/dependencies/blocking" "BLOCKING_$number"; $milestone = if ($null -eq $issue.milestone) { $null } else { [ordered]@{ id = $issue.milestone.id; number = $issue.milestone.number; title = $issue.milestone.title; state = $issue.milestone.state } }; return [ordered]@{ number = [int]$issue.number; state = [string]$issue.state; title = [string]$issue.title; body = [string]$issue.body; html_url = [string]$issue.html_url; url = [string]$issue.url; labels = @($issue.labels | ForEach-Object { [ordered]@{ id = $_.id; name = $_.name; color = $_.color; description = $_.description } }); comments = $comments; comments_count = $comments.Count; milestone = $milestone; native_blockers = [ordered]@{ blocked_by = @($blockedBy | ForEach-Object { [ordered]@{ id = $_.id; number = $_.number; state = $_.state; html_url = $_.html_url } }); blocking = @($blocking | ForEach-Object { [ordered]@{ id = $_.id; number = $_.number; state = $_.state; html_url = $_.html_url } }) } } }
$repo = $state.repository; $repoRaw = @(gh api "repos/$repo" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'REPOSITORY_READ_FAILED' }; $actionsRaw = @(gh api "repos/$repo/actions/permissions" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'ACTIONS_READBACK_FAILED' }; $workflowsRaw = @(gh api "repos/$repo/actions/workflows" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'WORKFLOW_READBACK_FAILED' }; $rulesetRaw = @(gh api "repos/$repo/rulesets/20160628" 2>&1); $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'RULESET_READBACK_FAILED' }
$repoObject = ($repoRaw -join [Environment]::NewLine) | ConvertFrom-Json; $actions = ($actionsRaw -join [Environment]::NewLine) | ConvertFrom-Json; $workflows = ($workflowsRaw -join [Environment]::NewLine) | ConvertFrom-Json; $ruleset = ($rulesetRaw -join [Environment]::NewLine) | ConvertFrom-Json
if ($repoObject.full_name -ne $repo -or $repoObject.default_branch -ne 'main' -or $actions.enabled -ne $false -or $workflows.total_count -ne 0 -or $ruleset.id -ne 20160628 -or $ruleset.enforcement -ne 'active' -or $ruleset.source -ne $repo -or @($ruleset.bypass_actors).Count -ne 0 -or @($ruleset.rules | Where-Object type -eq 'required_status_checks').Count -ne 0) { throw 'FINAL_POLICY_INVALID' }; $types = @($ruleset.rules | ForEach-Object type); if (@(Compare-Object (@('deletion','non_fast_forward','pull_request','required_linear_history') | Sort-Object) ($types | Sort-Object)).Count -ne 0) { throw 'FINAL_RULESET_TYPES_INVALID' }; $pullRule = @($ruleset.rules | Where-Object type -eq 'pull_request')[0]; $allowed = @(); if ($null -ne $pullRule -and $null -ne $pullRule.parameters -and $null -ne $pullRule.parameters.allowed_merge_methods) { $allowed = @($pullRule.parameters.allowed_merge_methods) }; if ($allowed -notcontains 'squash') { throw 'FINAL_SQUASH_POLICY_INVALID' }; $includes = @(); if ($null -ne $ruleset.conditions -and $null -ne $ruleset.conditions.ref_name -and $null -ne $ruleset.conditions.ref_name.include) { $includes = @($ruleset.conditions.ref_name.include) }; if (-not ($includes -contains '~DEFAULT_BRANCH' -or $includes -contains 'refs/heads/main' -or $includes -contains 'main')) { throw 'FINAL_RULESET_DEFAULT_BRANCH_INVALID' }
$policySummary = [ordered]@{ schema = 'gwo-v8-c1-final-policy.v1'; repository = $repoObject.full_name; default_branch = $repoObject.default_branch; actions_enabled = $actions.enabled; workflow_count = $workflows.total_count; ruleset_id = $ruleset.id; enforcement = $ruleset.enforcement; rule_types = @($types | Sort-Object); allowed_merge_methods = @($allowed | Sort-Object); default_branch_includes = @($includes | Sort-Object); bypass_actor_count = @($ruleset.bypass_actors).Count; required_status_rule_count = @($ruleset.rules | Where-Object type -eq 'required_status_checks').Count }; $policyPath = Join-Path $evidence 'closure-final-policy.json'; $policyText = $policySummary | ConvertTo-Json -Depth 30; if (Test-Path -LiteralPath $policyPath -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $policyPath) -ne $policyText) { throw 'FINAL_POLICY_RECEIPT_CONFLICT' } } else { [IO.File]::WriteAllText($policyPath,$policyText,[Text.UTF8Encoding]::new($false)) }; $policyHash = Hash-File $policyPath
$remoteRows = @(git -C $root ls-remote --heads origin refs/heads/main refs/heads/codex/gwo-v8-beta1 refs/heads/codex/gwo-v8-ga-plan); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $remoteRows.Count -ne 3) { throw 'FINAL_REMOTE_READ_FAILED' }; $remote = @{}; foreach ($row in $remoteRows) { $parts = $row -split '\s+'; if ($parts.Count -ne 2) { throw 'FINAL_REMOTE_ROW_INVALID' }; $remote[$parts[1]] = $parts[0] }; if ($remote['refs/heads/main'] -ne $state.pr.merge.merge_sha -or $remote['refs/heads/codex/gwo-v8-beta1'] -ne $state.identities.beta1.sha -or $remote['refs/heads/codex/gwo-v8-ga-plan'] -ne $state.identities.protected_ga.sha) { throw 'FINAL_REMOTE_IDENTITY_INVALID' }
git -C $root fetch --no-tags origin refs/heads/codex/gwo-v8-ga-plan; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'PROTECTED_GA_FETCH_FAILED' }; $fetchedGa = (git -C $root rev-parse FETCH_HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $fetchedGa -ne $state.identities.protected_ga.sha) { throw 'PROTECTED_GA_FETCH_IDENTITY_INVALID' }
$expectedBoundaries = [ordered]@{ foundation = [ordered]@{ short = '77ac3e3'; full = '77ac3e3ef14241d1840150b22cb227d2e5088fb4' }; issue_113 = [ordered]@{ short = '07086ce'; full = '07086ce1036198a41547ca1d9a9a506acfb8fcf7' }; issue_114 = [ordered]@{ short = '657bf23'; full = '657bf236d765735cdee117910a5939c6c2cd3292' }; issue_115 = [ordered]@{ short = 'a0f6976'; full = 'a0f697656be6471bed601103c169185988a9e4ac' }; issue_116_wip = [ordered]@{ short = 'e58c596'; full = 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2' } }; $resolved = [ordered]@{}
foreach ($name in $expectedBoundaries.Keys) { $item = $expectedBoundaries[$name]; $full = (git -C $root rev-parse "$($item.short)^{commit}").Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $full -ne $item.full -or $full -notmatch '^[0-9a-f]{40}$') { throw "BOUNDARY_RESOLUTION_INVALID:$name" }; $type = (git -C $root cat-file -t $full).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $type -ne 'commit') { throw "BOUNDARY_TYPE_INVALID:$name" }; $resolved[$name] = $full }
$chain = @('foundation','issue_113','issue_114','issue_115','issue_116_wip'); for ($i = 0; $i -lt ($chain.Count - 1); $i++) { git -C $root merge-base --is-ancestor $resolved[$chain[$i]] $resolved[$chain[$i + 1]]; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw "BOUNDARY_ANCESTRY_INVALID:$($chain[$i]):$($chain[$i + 1])" } }; git -C $root merge-base --is-ancestor $resolved.issue_116_wip $state.identities.protected_ga.sha; $exit = $LASTEXITCODE; if ($exit -ne 0) { throw 'BOUNDARY_NOT_IN_PROTECTED_GA' }
$trackerPath = [string]$state.tracker.after_snapshot; $trackerHash = Hash-File $trackerPath; if ($trackerHash -ne $state.tracker.after_snapshot_sha256) { throw 'TRACKER_AFTER_HASH_INVALID' }; $trackerAfter = Get-Content -Raw -LiteralPath $trackerPath | ConvertFrom-Json; if ($trackerAfter.schema -ne 'gwo-v8-c1-tracker-snapshot.v2' -or $trackerAfter.merged_main_sha -ne $state.pr.merge.merge_sha) { throw 'TRACKER_AFTER_IDENTITY_INVALID' }; $snapshot = @{}; foreach ($item in @($trackerAfter.issues)) { $snapshot[[int]$item.number] = $item }
$currentTickets = @(); foreach ($number in 113,114,115,116,117,118,119,137) { $current = Read-Ticket ([int]$number); if (-not $snapshot.ContainsKey([int]$number)) { throw "TRACKER_TICKET_MISSING:$number" }; $beforeJson = $snapshot[[int]$number] | ConvertTo-Json -Depth 60 -Compress; $currentJson = $current | ConvertTo-Json -Depth 60 -Compress; if ($beforeJson -cne $currentJson) { throw "TRACKER_TICKET_DRIFTED:$number" }; if ($number -ge 113 -and $number -le 119 -and $current.state -ne 'open') { throw "IMPLEMENTATION_TICKET_NOT_OPEN:$number" }; $currentTickets += $current }
$issue137 = @($currentTickets | Where-Object number -eq 137); if ($issue137.Count -ne 1 -or [string]::IsNullOrWhiteSpace([string]$issue137[0].body) -or [string]::IsNullOrWhiteSpace([string]$issue137[0].html_url) -or $issue137[0].milestone.title -ne 'GWO V8 Beta2') { throw 'ISSUE_137_PRESERVATION_INVALID' }
$ticketReadback = [ordered]@{ schema = 'gwo-v8-c1-c2-ticket-readback.v1'; repository = $repo; merged_main_sha = $state.pr.merge.merge_sha; tracker_after_sha256 = $trackerHash; issues = $currentTickets }; $ticketPath = Join-Path $evidence 'c2-ticket-readback.json'; $ticketText = $ticketReadback | ConvertTo-Json -Depth 70; if (Test-Path -LiteralPath $ticketPath -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $ticketPath) -ne $ticketText) { throw 'C2_TICKET_READBACK_CONFLICT' } } else { [IO.File]::WriteAllText($ticketPath,$ticketText,[Text.UTF8Encoding]::new($false)) }; $ticketHash = Hash-File $ticketPath
$canonicalReceiptPath = [string]$state.canonical_main.receipt_path; $canonicalReceiptHash = Hash-File $canonicalReceiptPath; if ($canonicalReceiptHash -ne $state.canonical_main.receipt_sha256) { throw 'CANONICAL_RECEIPT_HASH_INVALID' }; $canonicalReceipt = Get-Content -Raw -LiteralPath $canonicalReceiptPath | ConvertFrom-Json; $canonicalRoot = 'D:/Workstation/github-work-orchestrator'; $canonicalResolved = (git -C $canonicalRoot rev-parse --show-toplevel).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or ([IO.Path]::GetFullPath($canonicalResolved).Replace('\','/')).TrimEnd('/') -cne $canonicalRoot) { throw 'FINAL_CANONICAL_ROOT_INVALID' }; $canonicalBranch = (git -C $canonicalRoot symbolic-ref --quiet --short HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $canonicalBranch -ne 'main') { throw 'FINAL_CANONICAL_BRANCH_INVALID' }; $canonicalSha = (git -C $canonicalRoot rev-parse HEAD).Trim(); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $canonicalSha -ne $canonicalReceipt.canonical_sha) { throw 'FINAL_CANONICAL_SHA_INVALID' }; $canonicalDirty = @(git -C $canonicalRoot status --porcelain=v1 --untracked-files=all); $exit = $LASTEXITCODE; if ($exit -ne 0 -or $canonicalDirty.Count -ne 0) { throw 'FINAL_CANONICAL_STATUS_INVALID' }
$preflightHash = Hash-File ([string]$state.closure_preflight.path); if ($preflightHash -ne $state.closure_preflight.sha256) { throw 'CLOSURE_PREFLIGHT_HASH_INVALID' }; $betaManifestHash = Hash-File ([string]$state.local_verification.beta1.manifest); if ($betaManifestHash -ne $state.local_verification.beta1.manifest_sha256) { throw 'BETA1_MANIFEST_HASH_INVALID' }; $mergedManifestHash = Hash-File ([string]$state.local_verification.merged_main.manifest); if ($mergedManifestHash -ne $state.local_verification.merged_main.manifest_sha256) { throw 'MERGED_MANIFEST_HASH_INVALID' }
$handoff = [ordered]@{ schema = 'gwo-v8-c2-handoff.v1'; existing_completed_boundaries = $resolved; ancestry = @('foundation->issue_113','issue_113->issue_114','issue_114->issue_115','issue_115->issue_116_wip','issue_116_wip->protected_ga'); beta2_scope = @(113,114,115,116,117,137); beta3_scope = @(118); ga_scope = @(119); unfinished_scope = @([ordered]@{ item = 'issue_117_completion'; status = 'unfinished'; completed_boundary_sha = $null },[ordered]@{ item = 'final_issue_137_revalidation'; status = 'unfinished'; completed_boundary_sha = $null }); ticket_readback_path = $ticketPath.Replace('\','/'); ticket_readback_sha256 = $ticketHash }
$approvalDigests = [ordered]@{}; foreach ($gate in @('pr','tracker','publication')) { $receipt = $state.approvals.$gate; if ((Hash-File ([string]$receipt.approval_path)) -ne $receipt.approval_sha256 -or (Hash-File ([string]$receipt.lease_path)) -ne $receipt.lease_sha256) { throw "APPROVAL_HASH_INVALID:$gate" }; $approvalDigests[$gate] = [ordered]@{ approval_sha256 = $receipt.approval_sha256; lease_sha256 = $receipt.lease_sha256; lease_id = $receipt.lease_id } }
$closure = [ordered]@{ schema = 'gwo-v8-c1-closure.v2'; mode = 'Local Verification Only'; repository = $repo; merged_sha = $state.pr.merge.merge_sha; merged_tree = $state.identities.beta1.tree; merged_parent = $state.identities.base.sha; remote_refs = $remote; protected_ga_sha = $state.identities.protected_ga.sha; closure_preflight_sha256 = $preflightHash; mutation_policy_receipts = $state.closure_preflight.mutation_policy; final_policy_path = $policyPath.Replace('\','/'); final_policy_sha256 = $policyHash; beta1_manifest_sha256 = $betaManifestHash; merged_manifest_sha256 = $mergedManifestHash; review_state_sha256 = $state.reviews.state_sha256; approvals = $approvalDigests; tracker_after_sha256 = $trackerHash; c2_ticket_readback_sha256 = $ticketHash; tag_object_sha = $state.publication.tag.object_sha; tag_peeled_sha = $state.publication.tag.peeled_sha; release_id = $state.publication.release.id; release_receipt_sha256 = $state.publication.release.receipt_sha256; canonical_main_action = $state.canonical_main.action; canonical_main_sha = $canonicalSha; canonical_receipt_sha256 = $canonicalReceiptHash; c2_handoff = $handoff; non_goals = @('no production admission','no default-writer activation','no protected GA movement','no closure of #113-#119') }
$closurePath = Join-Path $evidence 'closure.json'; $closureText = $closure | ConvertTo-Json -Depth 80; if (Test-Path -LiteralPath $closurePath -PathType Leaf) { if ((Get-Content -Raw -LiteralPath $closurePath) -ne $closureText) { throw 'CLOSURE_RECEIPT_CONFLICT' } } else { [IO.File]::WriteAllText($closurePath,$closureText,[Text.UTF8Encoding]::new($false)) }; $closureHash = Hash-File $closurePath
$state.closure = [ordered]@{ schema = $closure.schema; path = $closurePath.Replace('\','/'); sha256 = $closureHash; merged_sha = $closure.merged_sha; c2_ticket_readback_sha256 = $ticketHash; canonical_main_sha = $canonicalSha }; $state.c2_handoff = $handoff; Save-State $state; $check = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json; if ($check.closure.sha256 -ne $closureHash -or $check.closure.merged_sha -ne $state.pr.merge.merge_sha -or $check.c2_handoff.existing_completed_boundaries.issue_116_wip -ne 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2' -or @($check.c2_handoff.unfinished_scope | Where-Object status -ne 'unfinished').Count -ne 0) { throw 'CLOSURE_STATE_READBACK_FAILED' }; if ((Hash-File ([string]$check.closure.path)) -ne $check.closure.sha256) { throw 'CLOSURE_RELOAD_HASH_INVALID' }
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
- a milestone conflicts, a semantic preflight changes before a conditional write, #137
  does not satisfy the approved reopen condition, or issue content/closure is
  requested;
- a tag or Release conflicts with an existing object, a Release exists without
  its tag, the tag is not annotated, the peeled SHA differs, the body differs
  from exact merged-SHA notes, or target identity relies only on
  targetCommitish; tag-only partial publication is a supported resume state;
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
Verify no unresolved marker, stale exact identity, omitted helper, cross-fence
variable, unbound approval/lease, or missing exact path remains.

Commit only this plan file with:

docs: re-freeze C1 for local verification

No push is part of this task.
