# GWO V8 C1 Beta1 Core Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the C0-validated GWO V8 Core Preview as immutable `v8.0.0-beta.1` without merging the protected GA implementation wholesale or activating the V8 writer.

**Architecture:** C1 is a metadata and release-control campaign, not a production implementation campaign. It audits the exact Beta1 history, merges `codex/gwo-v8-beta1` through one normal PR, waits for exact-SHA CI, performs separately approved tracker follow-ups, and publishes one annotated tag and prerelease. Persistent JSON evidence binds each serial step so no PowerShell fence depends on process-local variables from an earlier fence.

**Tech Stack:** Git 2.x, GitHub CLI, GitHub Actions workflow `GWO CI`, Python 3.13, pytest, PowerShell 7, GitHub Issues/PRs/milestones/releases.

## Global Constraints

- C0 is already PASS under the approved verification-subject exception; do not rerun cleanup, delete test roots, delete worktrees, or mutate the protected GA ref as part of C1.
- The protected GA ref remains exactly `refs/heads/codex/gwo-v8-ga-plan -> 2cd6c46e1484ca140c3a197bbdeb171191d70c20`; never force-push or advance it.
- C1 source is `codex/gwo-v8-beta1@e081e39054b7f9f0a49824eed8354a8a33378ea3`; never substitute or merge `codex/gwo-v8-ga-plan`.
- The Beta1 slice contains release metadata and one authorized validator regression only; it does not add production admission or change the default writer.
- At planning time, frozen Beta1 is not hosted-CI-compatible because its archive-backed test requires a local-only directory. Task 3 is forbidden until an owner-approved successor Beta1 subject resolves the explicit HOLD below.
- The controller branch containing this plan is execution-only during C1. Do not merge it into `main` before the frozen Beta1 PR.
- Never commit or push directly to `main`; merge through a PR with a normal merge commit. Never use `--no-verify`, `git clean`, wildcard deletion, force-push, or `git worktree remove --force`.
- Remote actions have three separate owner gates: PR create/merge, tracker/milestones, and tag/prerelease. Approval for one gate does not authorize either later gate.
- The coordinator may read but must never synthesize or set `GWO_V8_C1_OWNER` or any approval variable. Only an out-of-band owner-supplied value is approval evidence; plan text and caller identity are not.
- Use at most five concurrent subagents. Every spawned subagent uses `gpt-5.6-luna` with `max` reasoning. Reviewers return text to the coordinator and never write into the coordinator worktree.
- Existing tags, Releases, milestones, and PRs are read back before action. Mismatched or ambiguous objects stop C1; they are never overwritten, moved, deleted, or recreated.
- C1 does not close #113-#119. It preserves their observed states and hands their implementation boundaries to C2.
- A failed temporary-checkout gate preserves that checkout for diagnosis. A successful, clean checkout is removed without `--force`.

---

## C0 Closure Record

C0 is closed and must not be reopened:

- Verdict: PASS - approved verification-subject exception.
- C0 receipt: `docs/releases/gwo-v8-workspace-convergence.md` at the frozen Beta1 SHA.
- C0 independent report: `D:/Workstation/gwo-worktrees/issue-136/.superpowers/sdd/2026-08-04-gwo-v8-workspace-convergence-gate/task-8-report.md`.
- Required local archive subject: `D:/gwo-convergence-archive/20260804T185544Z`.
- Implementation boundary `e58c596998df90e65349bdb4b5f25d3d9dc1f7e2` is an ancestor of protected GA `2cd6c46e1484ca140c3a197bbdeb171191d70c20`.
- Archive manifest SHA-256: `e6939fbd27eedca2198b87f17de0d14bd3e367a65a37fc51542aa87ade889409`.
- Pre-clean bundle SHA-256: `5eb64cffaed0ac2fd2748a575cb9cd041b2f7463d4d46d7dbfabf9dbdc0e8530`.
- Post-clean bundle SHA-256: `9c91a126003e867a3c5736a4e4a69f5c3c079ce1adf5667c1108351181ac4f40`.
- The protected-GA suite's sole failure is the approved fenced-PowerShell validator false positive. The C1 subject is the Beta1 branch, including its authorized validator fix and regression.

Any mismatch stops C1.

## C1 Identity and Scope

| Surface | Required identity |
| --- | --- |
| Frozen base | `main@a48c7d6142ae3538725cb876a8782f4ca804cd22` |
| Beta1 boundary | `ddc1785f84b6a82a7b5c34d5928b046d4e9a781d` |
| Beta1 source | `codex/gwo-v8-beta1@e081e39054b7f9f0a49824eed8354a8a33378ea3` |
| Protected full implementation | `codex/gwo-v8-ga-plan@2cd6c46e1484ca140c3a197bbdeb171191d70c20` |
| Implementation ancestor for C2 | `e58c596998df90e65349bdb4b5f25d3d9dc1f7e2` |
| Baseline GWO CI | run `30778312688`, 1521 passed in 704.60s, for `a48c7d6` |
| Release object | annotated tag and prerelease `v8.0.0-beta.1` |

The aggregate `a48c7d6...e081e390` PR diff is exactly these 16 paths:

- `.superpowers/sdd/2026-08-03-gwo-v8-ga-delivery-program/task-1-report.md`
- `docs/design/gwo-v8-lean-roadmap.md`
- `docs/releases/gwo-v8-release-train.md`
- `docs/releases/gwo-v8-workspace-convergence.md`
- `docs/releases/v8.0.0-beta.1.md`
- `docs/superpowers/plans/2026-08-03-gwo-v8-batch-delivery.md`
- `docs/superpowers/plans/2026-08-03-gwo-v8-campaign-watchdog.md`
- `docs/superpowers/plans/2026-08-03-gwo-v8-candidate-assurance.md`
- `docs/superpowers/plans/2026-08-03-gwo-v8-cutover-guard.md`
- `docs/superpowers/plans/2026-08-03-gwo-v8-ga-delivery-program.md`
- `docs/superpowers/plans/2026-08-03-gwo-v8-production-composition.md`
- `docs/superpowers/plans/2026-08-03-gwo-v8-root-canary-ga.md`
- `docs/superpowers/plans/2026-08-04-gwo-v8-ga-release-program.md`
- `docs/superpowers/plans/2026-08-04-gwo-v8-workspace-convergence-gate.md`
- `scripts/quick_validate.py`
- `tests/test_orchestrator_package.py`

For every commit after `ddc1785`, the narrower approved set is the two 2026-08-04 plans, the convergence receipt, release train, package test, and the user-authorized `scripts/quick_validate.py` fix. Auditing only the final aggregate diff is insufficient because a later commit could hide an earlier out-of-scope write.

## Planning-Time Hosted CI Blocker - HOLD

The frozen subject cannot currently satisfy its own PR/main CI gate:

- `e081e390:tests/test_orchestrator_package.py::test_beta1_requires_structured_workspace_convergence_receipt` requires nonempty `GWO_CONVERGENCE_ARCHIVE_ROOT` and rehashes the real convergence archive.
- The authoritative archive exists only at `D:/gwo-convergence-archive/20260804T185544Z`.
- `e081e390:.github/workflows/ci.yml` runs on GitHub-hosted `windows-2025`, invokes full pytest, and neither provisions the archive nor sets `GWO_CONVERGENCE_ARCHIVE_ROOT`.
- Therefore local C1 verification can pass with the real archive, but the frozen PR and post-merge `GWO CI` runs cannot pass.

This plan remains read-only through Task 2 and blocks before Task 3. The recommended unblock is a separately approved successor Beta1 SHA that keeps the real local archive gate authoritative, marks only that external-evidence test outside hosted acceptance, updates the release documents with an explicit split-verification exception, and then re-freezes every SHA/path/CI assertion in this plan. Committing the archive publicly or silently weakening the test is not authorized.

## Persistent Evidence Contract

The coordinator creates external operational evidence under this fixed path so reports never dirty a Git worktree:

`D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview/`

`state.json` uses schema `gwo-v8-c1-state.v1` and carries exact SHAs, PR identity, merge SHA, CI run identity, scoped approvals, tracker completion, tag identity, and Release URL. Every later PowerShell fence resolves its own repository root and reloads this file; no fence consumes a variable from a previous fence.

## Safe Parallel Schedule

After Task 0, run Task 1 Step 1 (history/scope audit) and Task 1 Step 2 (detached local verification) concurrently. They write disjoint evidence (`task-1-audit.json` versus test logs/state fields) and neither mutates a remote object. The coordinator runs Task 1 Step 3 after both finish.

Then run these five read-only lanes concurrently:

1. Standards review: repository instructions, plan executability, safety, and no hidden process state.
2. Spec review: GA program, release train, Beta1 notes, C0 decision, and release boundaries.
3. Git/scope review: merge-base, ancestry, every post-boundary commit, aggregate allowlist, and protected ref identity.
4. Tracker review: complete #113-#119 and #137 body, labels, comments, milestones, states, and native blockers.
5. Release review: exact-SHA CI, PR/merge commands, approvals, tag annotation/peel, Release target, and resumption safety.

Tasks 3-6 are serial. Tracker work in Task 5 is an explicitly approved post-merge release-control follow-up; it is not part of the Beta1 metadata PR, resolving the release-note statement that the metadata lane itself does not perform tracker mutation.

---

### Task 0: Freeze C0 and C1 identities

**Files:**
- Read from the frozen Beta1 commit: `docs/releases/gwo-v8-workspace-convergence.md`
- Read: `D:/Workstation/gwo-worktrees/issue-136/.superpowers/sdd/2026-08-04-gwo-v8-workspace-convergence-gate/task-8-report.md`
- Create: `D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview/state.json`

**Interfaces:**
- Consumes: C0 receipt/report, local/remote refs, current worktree registry, and publication readback.
- Produces: a frozen state file proving C0 is closed and C1 has not begun remotely.

- [ ] **Step 1: Verify the dynamic coordinator root, both C0 roots, and every frozen Git identity.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) { throw 'The coordinator root is not a Git worktree.' }
$canonical = 'D:/Workstation/github-work-orchestrator'
$gaRoot = 'D:/Workstation/gwo-worktrees/issue-136'
function Normalize-Path([string]$Path) {
    return ([IO.Path]::GetFullPath($Path).Replace('\','/')).TrimEnd([char[]]'/')
}
$root = Normalize-Path $root
$expectedRoots = @((Normalize-Path $canonical), (Normalize-Path $gaRoot), $root) | Sort-Object -Unique
if ($expectedRoots.Count -ne 3) { throw 'C1 must run from its isolated coordinator worktree.' }
$actualRoots = @(git -C $root worktree list --porcelain |
    Select-String '^worktree ' |
    ForEach-Object { Normalize-Path $_.Line.Substring(9) } |
    Sort-Object -Unique)
if ($LASTEXITCODE -ne 0) { throw 'Cannot read the worktree registry.' }
$rootDiff = @(Compare-Object $expectedRoots $actualRoots)
if ($rootDiff) { $rootDiff; throw 'C1 requires exactly canonical main, active GA, and the current coordinator worktree.' }
foreach ($path in $expectedRoots) {
    $dirty = @(git -C $path status --porcelain=v1 --untracked-files=all)
    if ($LASTEXITCODE -ne 0 -or $dirty.Count -ne 0) { $dirty; throw "Worktree is not clean: $path" }
}
if ((git -C $root symbolic-ref --short HEAD).Trim() -ne 'codex/gwo-v8-c1-beta1-plan') { throw 'Coordinator worktree is on the wrong branch.' }
if ((git -C $canonical symbolic-ref --short HEAD).Trim() -ne 'main') { throw 'Canonical root is not on main.' }
if ((git -C $canonical rev-parse HEAD).Trim() -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22') { throw 'Canonical main moved.' }
if ((git -C $gaRoot symbolic-ref --short HEAD).Trim() -ne 'codex/gwo-v8-ga-plan') { throw 'Active GA root is on the wrong branch.' }
if ((git -C $gaRoot rev-parse HEAD).Trim() -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20') { throw 'Protected GA moved.' }
if ((git -C $root rev-parse refs/heads/codex/gwo-v8-beta1).Trim() -ne 'e081e39054b7f9f0a49824eed8354a8a33378ea3') { throw 'Local Beta1 moved.' }
git -C $root merge-base --is-ancestor e58c596998df90e65349bdb4b5f25d3d9dc1f7e2 2cd6c46e1484ca140c3a197bbdeb171191d70c20
if ($LASTEXITCODE -ne 0) { throw 'Required implementation boundary is not an ancestor of protected GA.' }
function Read-RemoteHead([string]$Name) {
    $rows = @(git -C $root ls-remote --heads origin "refs/heads/$Name")
    if ($LASTEXITCODE -ne 0 -or $rows.Count -ne 1) { throw "Remote head is missing or ambiguous: $Name" }
    return (($rows[0] -split '\s+')[0])
}
$remoteMain = Read-RemoteHead 'main'
$remoteBeta1 = Read-RemoteHead 'codex/gwo-v8-beta1'
$remoteGa = Read-RemoteHead 'codex/gwo-v8-ga-plan'
if ($remoteMain -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22') { throw 'Remote main moved.' }
if ($remoteBeta1 -ne 'e081e39054b7f9f0a49824eed8354a8a33378ea3') { throw 'Remote Beta1 moved.' }
if ($remoteGa -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20') { throw 'Remote protected GA moved.' }
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
if (Test-Path -LiteralPath $evidence) { throw "C1 evidence root already exists; inspect and resume it instead of overwriting: $evidence" }
New-Item -ItemType Directory -Path $evidence | Out-Null
$state = [ordered]@{
    schema = 'gwo-v8-c1-state.v1'
    coordinator_root = $root
    coordinator_head = (git -C $root rev-parse HEAD).Trim()
    base_sha = $remoteMain
    beta1_boundary_sha = 'ddc1785f84b6a82a7b5c34d5928b046d4e9a781d'
    beta1_sha = $remoteBeta1
    protected_ga_sha = $remoteGa
    implementation_boundary_sha = 'e58c596998df90e65349bdb4b5f25d3d9dc1f7e2'
    initialized_at = (Get-Date -AsUTC -Format o)
}
$state | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $evidence 'state.json') -Encoding utf8NoBOM
~~~

Expected: exactly three clean registered worktrees; canonical main, Beta1, and protected GA match all local/remote frozen SHAs.

- [ ] **Step 2: Parse and verify the C0 receipt and approved decision without an end-of-file assumption.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$statePath = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview/state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$receiptLines = @(git -C $root show "$($state.beta1_sha):docs/releases/gwo-v8-workspace-convergence.md")
if ($LASTEXITCODE -ne 0) { throw 'Cannot read the frozen C0 receipt.' }
$receiptText = $receiptLines -join [Environment]::NewLine
$fence = ([string][char]96) * 3
$pattern = '(?ms)^' + [regex]::Escape($fence) + 'json\s*\r?\n(?<json>\{.*?\})\s*\r?\n' + [regex]::Escape($fence) + '\s*(?:\r?\n|$)'
$matches = [regex]::Matches($receiptText, $pattern)
if ($matches.Count -ne 1) { throw 'The C0 receipt must contain exactly one fenced JSON object.' }
$receipt = $matches[0].Groups['json'].Value | ConvertFrom-Json
$keptDiff = @(Compare-Object @('active-ga','canonical-main') @($receipt.kept_worktrees | Sort-Object))
if ($keptDiff -or $receipt.schema -ne 'gwo-workspace-convergence.v1' -or
    $receipt.source_sha -ne $state.implementation_boundary_sha -or
    $receipt.protected_remote_ref -ne 'refs/heads/codex/gwo-v8-ga-plan' -or
    $receipt.protected_remote_sha -ne $state.protected_ga_sha -or
    $receipt.removed_worktree_count -ne 36 -or
    $receipt.removed_test_root_count -ne 48 -or
    $receipt.refs_deleted -ne $false -or
    $receipt.archive_manifest_sha256 -ne 'e6939fbd27eedca2198b87f17de0d14bd3e367a65a37fc51542aa87ade889409' -or
    $receipt.pre_clean_bundle_sha256 -ne '5eb64cffaed0ac2fd2748a575cb9cd041b2f7463d4d46d7dbfabf9dbdc0e8530' -or
    $receipt.post_clean_bundle_sha256 -ne '9c91a126003e867a3c5736a4e4a69f5c3c079ce1adf5667c1108351181ac4f40') {
    throw 'C0 receipt identity drifted.'
}
$decision = 'D:/Workstation/gwo-worktrees/issue-136/.superpowers/sdd/2026-08-04-gwo-v8-workspace-convergence-gate/task-8-report.md'
$pass = @(Select-String -LiteralPath $decision -Pattern '^Phase 1 is \*\*PASS\*\* under the approved verification-subject exception\. The$' -CaseSensitive)
if ($pass.Count -ne 1) { throw 'The approved C0 PASS line is absent or ambiguous.' }
$state | Add-Member -NotePropertyName c0_receipt_verified_at -NotePropertyValue (Get-Date -AsUTC -Format o) -Force
$state | Add-Member -NotePropertyName c0_archive_manifest_sha256 -NotePropertyValue $receipt.archive_manifest_sha256 -Force
$state | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $statePath -Encoding utf8NoBOM
~~~

Expected: the exact receipt values and exact C0 PASS decision are read successfully; explanatory prose after the JSON fence does not invalidate parsing.

- [ ] **Step 3: Prove the Beta1 tag and Release are both absent before C1 mutation.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$repo = 'NOirBRight/github-work-orchestrator'
$tagName = 'v8.0.0-beta.1'
$statePath = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview/state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$tagRows = @(git -C $root ls-remote --tags origin "refs/tags/$tagName" "refs/tags/$tagName^{}")
if ($LASTEXITCODE -ne 0) { throw 'Cannot read remote Beta1 tag state.' }
$releaseProbe = @(gh api "repos/$repo/releases/tags/$tagName" 2>&1)
$releaseProbeExit = $LASTEXITCODE
if ($releaseProbeExit -ne 0 -and (($releaseProbe -join "`n") -notmatch '\(HTTP 404\)')) { throw 'Cannot read GitHub Release state.' }
$releaseExists = $releaseProbeExit -eq 0
$tagExists = $tagRows.Count -gt 0
if ($tagExists -xor $releaseExists) { throw 'Partial Beta1 publication exists; stop without recreating either object.' }
if ($tagExists -and $releaseExists) {
    $parsed = @($tagRows | ForEach-Object { $parts = $_ -split '\s+'; [pscustomobject]@{ sha = $parts[0]; ref = $parts[1] } })
    $direct = @($parsed | Where-Object ref -eq "refs/tags/$tagName")
    $peeled = @($parsed | Where-Object ref -eq "refs/tags/$tagName^{}")
    $release = ($releaseProbe -join [Environment]::NewLine) | ConvertFrom-Json
    if ($direct.Count -ne 1 -or $peeled.Count -ne 1 -or $release.tag_name -ne $tagName -or
        $release.target_commitish -ne $peeled[0].sha -or -not $release.prerelease -or $release.draft) {
        throw 'Existing Beta1 publication is malformed.'
    }
    throw 'Beta1 is already published. Do not rerun C1 mutations; perform a dedicated readback audit.'
}
$state | Add-Member -NotePropertyName initial_tag_absent -NotePropertyValue $true -Force
$state | Add-Member -NotePropertyName initial_release_absent -NotePropertyValue $true -Force
$state | Add-Member -NotePropertyName publication_preflight_at -NotePropertyValue (Get-Date -AsUTC -Format o) -Force
$state | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $statePath -Encoding utf8NoBOM
~~~

Expected at C1 start: no remote tag and no GitHub Release.

---

### Task 1: Audit every Beta1 commit and pass the complete Beta1 gate

**Files:**
- Read: the 16 aggregate paths and six post-boundary approved paths
- Test: `tests/test_orchestrator_package.py` and the complete pytest suite
- Verify: `scripts/quick_validate.py`, `scripts/sync_orchestrator.py`, and `git diff --check`
- Create: `task-1-audit.json` and Task 1 command logs under the evidence directory

**Interfaces:**
- Consumes: frozen `state.json` and C0 authorization for the validator fix.
- Produces: exact ancestry/path evidence and a clean, fully tested Beta1 SHA.

- [ ] **Step 1: Verify merge-base, boundary ancestry, every post-boundary commit, aggregate allowlist, and protected plan blobs.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$statePath = Join-Path $evidence 'state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$base = $state.base_sha
$candidate = $state.beta1_sha
$boundary = $state.beta1_boundary_sha
if ((git -C $root merge-base $base $candidate).Trim() -ne $base) { throw 'Beta1 merge-base is not the frozen main SHA.' }
git -C $root merge-base --is-ancestor $boundary $candidate
if ($LASTEXITCODE -ne 0) { throw 'The Beta1 boundary is not an ancestor of the candidate.' }
$postBoundaryAllowed = @(
    'docs/releases/gwo-v8-release-train.md',
    'docs/releases/gwo-v8-workspace-convergence.md',
    'docs/superpowers/plans/2026-08-04-gwo-v8-ga-release-program.md',
    'docs/superpowers/plans/2026-08-04-gwo-v8-workspace-convergence-gate.md',
    'scripts/quick_validate.py',
    'tests/test_orchestrator_package.py'
) | Sort-Object
$commits = @(git -C $root rev-list --reverse "$boundary..$candidate")
if ($LASTEXITCODE -ne 0 -or $commits.Count -eq 0) { throw 'Cannot enumerate post-boundary commits.' }
$commitAudit = @()
foreach ($commit in $commits) {
    $parentText = (git -C $root show -s --format=%P $commit).Trim()
    $parents = @($parentText -split ' ' | Where-Object { $_ })
    $paths = @(
        if ($parents.Count -eq 0) {
            git -C $root show --pretty=format: --name-only $commit
        } else {
            foreach ($parent in $parents) { git -C $root diff --name-only $parent $commit }
        }
    ) | Where-Object { $_ } | Sort-Object -Unique
    $bad = @($paths | Where-Object { $_ -notin $postBoundaryAllowed })
    if ($bad) { $bad; throw "Post-boundary commit writes outside the approved set: $commit" }
    $commitAudit += [pscustomobject]@{ commit = $commit; paths = $paths }
}
$aggregateExpected = @(
    '.superpowers/sdd/2026-08-03-gwo-v8-ga-delivery-program/task-1-report.md',
    'docs/design/gwo-v8-lean-roadmap.md',
    'docs/releases/gwo-v8-release-train.md',
    'docs/releases/gwo-v8-workspace-convergence.md',
    'docs/releases/v8.0.0-beta.1.md',
    'docs/superpowers/plans/2026-08-03-gwo-v8-batch-delivery.md',
    'docs/superpowers/plans/2026-08-03-gwo-v8-campaign-watchdog.md',
    'docs/superpowers/plans/2026-08-03-gwo-v8-candidate-assurance.md',
    'docs/superpowers/plans/2026-08-03-gwo-v8-cutover-guard.md',
    'docs/superpowers/plans/2026-08-03-gwo-v8-ga-delivery-program.md',
    'docs/superpowers/plans/2026-08-03-gwo-v8-production-composition.md',
    'docs/superpowers/plans/2026-08-03-gwo-v8-root-canary-ga.md',
    'docs/superpowers/plans/2026-08-04-gwo-v8-ga-release-program.md',
    'docs/superpowers/plans/2026-08-04-gwo-v8-workspace-convergence-gate.md',
    'scripts/quick_validate.py',
    'tests/test_orchestrator_package.py'
) | Sort-Object
$aggregateActual = @(git -C $root diff --name-only "$base...$candidate" | Sort-Object)
$aggregateDiff = @(Compare-Object $aggregateExpected $aggregateActual)
if ($aggregateDiff) { $aggregateDiff; throw 'The aggregate Beta1 path allowlist failed.' }
foreach ($path in @('docs/superpowers/plans/2026-08-04-gwo-v8-ga-release-program.md','docs/superpowers/plans/2026-08-04-gwo-v8-workspace-convergence-gate.md')) {
    $betaBlob = (git -C $root rev-parse "${candidate}:$path").Trim()
    $gaBlob = (git -C $root rev-parse "$($state.protected_ga_sha):$path").Trim()
    if ($betaBlob -ne $gaBlob) { throw "Protected plan blob mismatch: $path" }
}
$audit = [ordered]@{
    merge_base = (git -C $root merge-base $base $candidate).Trim()
    boundary = $boundary
    candidate = $candidate
    commits = $commitAudit
    aggregate_paths = $aggregateActual
    verified_at = (Get-Date -AsUTC -Format o)
}
$audit | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $evidence 'task-1-audit.json') -Encoding utf8NoBOM
~~~

Expected: merge-base equals `a48c7d6`, `ddc1785` is an ancestor, every later commit is within the six-path authorization, the aggregate diff is exactly 16 paths, and both final plan blobs match protected GA.

- [ ] **Step 2: Run focused and full verification inside an exact detached checkout.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$statePath = Join-Path $evidence 'state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$archiveRoot = 'D:/gwo-convergence-archive/20260804T185544Z'
if (-not (Test-Path -LiteralPath $archiveRoot -PathType Container)) { throw "Required C0 archive is absent: $archiveRoot" }
$verify = 'D:/Workstation/gwo-worktrees/c1-beta1-verify'
if (Test-Path -LiteralPath $verify) { throw "Verification checkout already exists: $verify" }
git -C $root worktree add --detach $verify $state.beta1_sha
if ($LASTEXITCODE -ne 0) { throw 'Cannot create the exact Beta1 verification checkout.' }
$completed = $false
Push-Location -LiteralPath $verify
try {
    if ((git rev-parse HEAD).Trim() -ne $state.beta1_sha) { throw 'Verification checkout is on the wrong SHA.' }
    $previousArchiveRoot = $env:GWO_CONVERGENCE_ARCHIVE_ROOT
    try {
        $env:GWO_CONVERGENCE_ARCHIVE_ROOT = $archiveRoot
    $packageOutput = @(& py -3.13 -m pytest tests/test_orchestrator_package.py -q 2>&1)
    $packageExit = $LASTEXITCODE
    $packageOutput | Set-Content -LiteralPath (Join-Path $evidence 'task-1-package.log') -Encoding utf8NoBOM
    if ($packageExit -ne 0) { throw 'Beta1 package tests failed.' }
    $fullOutput = @(& py -3.13 -m pytest -q 2>&1)
    $fullExit = $LASTEXITCODE
    $fullOutput | Set-Content -LiteralPath (Join-Path $evidence 'task-1-full-pytest.log') -Encoding utf8NoBOM
    if ($fullExit -ne 0) { throw 'Beta1 full pytest failed.' }
    $quickOutput = @(& py -3.13 scripts/quick_validate.py 2>&1)
    $quickExit = $LASTEXITCODE
    $quickOutput | Set-Content -LiteralPath (Join-Path $evidence 'task-1-quick-validate.log') -Encoding utf8NoBOM
    if ($quickExit -ne 0) { throw 'Beta1 quick validation failed.' }
    $syncOutput = @(& py -3.13 scripts/sync_orchestrator.py --check 2>&1)
    $syncExit = $LASTEXITCODE
    $syncOutput | Set-Content -LiteralPath (Join-Path $evidence 'task-1-sync-check.log') -Encoding utf8NoBOM
    if ($syncExit -ne 0) { throw 'Beta1 package synchronization failed.' }
    $diffOutput = @(git diff --check "$($state.base_sha)...HEAD" 2>&1)
    $diffExit = $LASTEXITCODE
    $diffOutput | Set-Content -LiteralPath (Join-Path $evidence 'task-1-diff-check.log') -Encoding utf8NoBOM
    if ($diffExit -ne 0) { throw 'Beta1 diff check failed.' }
    $dirty = @(git status --porcelain=v1 --untracked-files=all)
    if ($dirty.Count -ne 0) { $dirty; throw 'Verification changed the detached checkout.' }
    $packageSummary = @($packageOutput | Select-String -Pattern '[0-9][0-9,]* passed')
    $fullSummary = @($fullOutput | Select-String -Pattern '[0-9][0-9,]* passed')
    if ($packageSummary.Count -eq 0 -or $fullSummary.Count -eq 0) { throw 'Dynamic pytest summaries are missing.' }
    $state | Add-Member -NotePropertyName beta1_package_summary -NotePropertyValue $packageSummary[-1].Line.Trim() -Force
    $state | Add-Member -NotePropertyName beta1_full_summary -NotePropertyValue $fullSummary[-1].Line.Trim() -Force
    $state | Add-Member -NotePropertyName beta1_verified_at -NotePropertyValue (Get-Date -AsUTC -Format o) -Force
    $state | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $statePath -Encoding utf8NoBOM
    } finally {
        if ($null -eq $previousArchiveRoot) {
            Remove-Item -LiteralPath Env:\GWO_CONVERGENCE_ARCHIVE_ROOT -ErrorAction SilentlyContinue
        } else {
            $env:GWO_CONVERGENCE_ARCHIVE_ROOT = $previousArchiveRoot
        }
    }
    $completed = $true
} finally {
    Pop-Location
    if ($completed) {
        git -C $root worktree remove $verify
        if ($LASTEXITCODE -ne 0) { throw "Verified checkout stayed registered: $verify" }
    } else {
        Write-Warning "Verification failed; preserved checkout for diagnosis: $verify"
    }
}
~~~

Expected: focused package tests and full pytest pass with dynamic summaries; quick validation, sync, and diff checks pass; the exact checkout stays clean and is removed without force.

- [ ] **Step 3: Persist the deterministic hosted-CI compatibility HOLD.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$statePath = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview/state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$testText = @(git -C $root show "$($state.beta1_sha):tests/test_orchestrator_package.py") -join "`n"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the frozen Beta1 package test.' }
$workflowText = @(git -C $root show "$($state.beta1_sha):.github/workflows/ci.yml") -join "`n"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the frozen hosted CI workflow.' }
$frozenBeta1 = 'e081e39054b7f9f0a49824eed8354a8a33378ea3'
if ($state.beta1_sha -ne $frozenBeta1) { throw 'Beta1 SHA changed; rewrite and re-review this plan instead of reusing the frozen compatibility decision.' }
$requiresArchive = $testText -match 'GWO_CONVERGENCE_ARCHIVE_ROOT is required'
$provisionsArchive = $workflowText -match 'GWO_CONVERGENCE_ARCHIVE_ROOT'
if (-not $requiresArchive -or $provisionsArchive) { throw 'Frozen hosted-CI facts changed; re-freeze the C1 plan.' }
$compatible = $false
$state | Add-Member -NotePropertyName hosted_ci_compatible -NotePropertyValue $compatible -Force
$state | Add-Member -NotePropertyName hosted_ci_checked_at -NotePropertyValue (Get-Date -AsUTC -Format o) -Force
if (-not $compatible) {
    $state | Add-Member -NotePropertyName c1_hold_reason -NotePropertyValue 'Frozen Beta1 requires a local convergence archive that hosted GWO CI does not provision.' -Force
}
$state | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $statePath -Encoding utf8NoBOM
if (-not $compatible) { Write-Warning 'C1 HOLD: Tasks 0-2 may complete, but Task 3 remote mutation is forbidden.' }
~~~

Expected for frozen `e081e390`: `hosted_ci_compatible=false` and the exact HOLD reason is persisted. This is a detected release blocker, not a passing gate.

---

### Task 2: Run five independent read-only reviews in parallel

**Files:**
- Read: Tasks 0-1 evidence, exact Beta1 history/diff, release docs, C0 decision, and live readbacks
- Coordinator creates: `task-2-review-1-standards.md` through `task-2-review-5-release.md`

**Interfaces:**
- Consumes: the frozen, fully tested C1 subject.
- Produces: five reports whose last non-empty line is exactly `Verdict: PASS`; any other final line blocks mutation.

- [ ] **Step 1: Dispatch the five lanes concurrently.**

Use no more than five `gpt-5.6-luna` agents with `max` reasoning. Give each agent the frozen SHAs, its lane above, absolute read paths, and this contract:

1. Read-only: no file, Git, GitHub, Issue, milestone, tag, Release, or worktree mutation.
2. Report every finding with severity and exact evidence.
3. End with exactly one structured line: `Verdict: PASS` or `Verdict: HOLD`.
4. Return the report to the coordinator; do not write into any shared worktree.

The coordinator persists each returned response under the exact five filenames after the agents finish.

- [ ] **Step 2: Require five exact PASS verdicts.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$expectedNames = @(
    'task-2-review-1-standards.md',
    'task-2-review-2-spec.md',
    'task-2-review-3-git-scope.md',
    'task-2-review-4-tracker.md',
    'task-2-review-5-release.md'
)
$reports = @($expectedNames | ForEach-Object { Get-Item -LiteralPath (Join-Path $evidence $_) })
if ($reports.Count -ne 5) { throw 'C1 requires exactly five review reports.' }
foreach ($report in $reports) {
    $lines = @(Get-Content -LiteralPath $report.FullName | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($lines.Count -eq 0 -or $lines[-1] -cne 'Verdict: PASS') {
        throw "Review does not end with exact PASS: $($report.Name)"
    }
}
$statePath = Join-Path $evidence 'state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if (-not $state.beta1_verified_at -or -not $state.beta1_full_summary -or -not $state.beta1_package_summary) { throw 'Task 1 verification evidence is incomplete.' }
if ($null -eq $state.hosted_ci_compatible) { throw 'Hosted CI compatibility preflight is absent.' }
$state | Add-Member -NotePropertyName reviews_complete -NotePropertyValue $true -Force
$state | Add-Member -NotePropertyName reviews_verified_at -NotePropertyValue (Get-Date -AsUTC -Format o) -Force
$state | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $statePath -Encoding utf8NoBOM
~~~

Expected: five files exist and each last non-empty line is exactly `Verdict: PASS`; phrases such as `no HOLD` cannot create a false result.

---

### Task 3: Obtain PR authorization and submit the exact Beta1 PR

**Files:**
- Read: Task 2 reports and frozen `state.json`
- Create: `task-3-pr-approval.json`; persist PR identity in `state.json`
- Remote artifact: one PR from `codex/gwo-v8-beta1` to `main`

**Interfaces:**
- Consumes: five PASS reviews and exact Beta1 source.
- Produces: one exact Draft PR and persisted PR identity.

- [ ] **Step 1: Require SHA-bound owner approval and recheck remote identities.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$statePath = Join-Path $evidence 'state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$reviewNames = @('task-2-review-1-standards.md','task-2-review-2-spec.md','task-2-review-3-git-scope.md','task-2-review-4-tracker.md','task-2-review-5-release.md')
if (-not $state.reviews_complete) { throw 'Five PASS reviews are not persisted.' }
if ($state.hosted_ci_compatible -ne $true) { throw "C1 HOLD: $($state.c1_hold_reason) Approve and re-freeze a successor Beta1 subject before any remote mutation." }
foreach ($name in $reviewNames) {
    $lines = @(Get-Content -LiteralPath (Join-Path $evidence $name) | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($lines.Count -eq 0 -or $lines[-1] -cne 'Verdict: PASS') { throw "Review gate drifted: $name" }
}
$expectedApproval = "APPROVE-GWO-V8-C1-PR:$($state.beta1_sha)->main@$($state.base_sha)"
if ($env:GWO_V8_C1_OWNER -cne 'NOirBRight' -or $env:GWO_V8_C1_PR_APPROVAL -cne $expectedApproval) {
    throw "STOP: set GWO_V8_C1_OWNER=NOirBRight and provide exact PR approval: $expectedApproval"
}
function Read-RemoteHead([string]$Name) {
    $rows = @(git -C $root ls-remote --heads origin "refs/heads/$Name")
    if ($LASTEXITCODE -ne 0 -or $rows.Count -ne 1) { throw "Remote head missing or ambiguous: $Name" }
    return (($rows[0] -split '\s+')[0])
}
if ((Read-RemoteHead 'main') -ne $state.base_sha -or
    (Read-RemoteHead 'codex/gwo-v8-beta1') -ne $state.beta1_sha -or
    (Read-RemoteHead 'codex/gwo-v8-ga-plan') -ne $state.protected_ga_sha) {
    throw 'A frozen remote identity moved before PR authorization.'
}
$approval = [ordered]@{ approver = $env:GWO_V8_C1_OWNER; text = $expectedApproval; approved_at = (Get-Date -AsUTC -Format o) }
$approvalPath = Join-Path $evidence 'task-3-pr-approval.json'
if (Test-Path -LiteralPath $approvalPath) {
    $recordedApproval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json
    if ($recordedApproval.approver -cne $approval.approver -or $recordedApproval.text -cne $approval.text) { throw 'Existing PR approval evidence conflicts.' }
    $approval = $recordedApproval
} else {
    $approval | ConvertTo-Json | Set-Content -LiteralPath $approvalPath -Encoding utf8NoBOM
}
$state | Add-Member -NotePropertyName pr_approval -NotePropertyValue $approval -Force
$state | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $statePath -Encoding utf8NoBOM
~~~

Expected before approval: intentional stop with no remote action. Expected after approval: identity-bound evidence names `NOirBRight`, the exact source/base SHAs, and a UTC timestamp.

- [ ] **Step 2: Push the already-reviewed branch without force and verify exact remote readback.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$state = Get-Content -Raw -LiteralPath 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview/state.json' | ConvertFrom-Json
$expectedApproval = "APPROVE-GWO-V8-C1-PR:$($state.beta1_sha)->main@$($state.base_sha)"
if ($env:GWO_V8_C1_OWNER -cne 'NOirBRight' -or $env:GWO_V8_C1_PR_APPROVAL -cne $expectedApproval -or
    -not $state.reviews_complete -or $state.pr_approval.approver -cne 'NOirBRight' -or $state.pr_approval.text -cne $expectedApproval) { throw 'Review or PR approval evidence is invalid.' }
function Read-RemoteHead([string]$Name) {
    $rows = @(git -C $root ls-remote --heads origin "refs/heads/$Name")
    if ($rows.Count -ne 1) { throw "Remote head missing or ambiguous: $Name" }
    return (($rows[0] -split '\s+')[0])
}
if ((Read-RemoteHead 'main') -ne $state.base_sha -or
    (Read-RemoteHead 'codex/gwo-v8-beta1') -ne $state.beta1_sha -or
    (Read-RemoteHead 'codex/gwo-v8-ga-plan') -ne $state.protected_ga_sha) { throw 'Remote refs drifted before Beta1 push.' }
if ((git -C $root rev-parse refs/heads/codex/gwo-v8-beta1).Trim() -ne $state.beta1_sha) { throw 'Local Beta1 branch moved.' }
git -C $root push origin refs/heads/codex/gwo-v8-beta1:refs/heads/codex/gwo-v8-beta1
if ($LASTEXITCODE -ne 0) { throw 'Non-force Beta1 push failed.' }
$rows = @(git -C $root ls-remote --heads origin refs/heads/codex/gwo-v8-beta1)
if ($rows.Count -ne 1 -or (($rows[0] -split '\s+')[0]) -ne $state.beta1_sha) { throw 'Remote Beta1 readback differs from the reviewed SHA.' }
~~~

Expected: a no-op or fast-forward-safe normal push; the remote head remains exactly `e081e390`.

- [ ] **Step 3: Create or reuse exactly one Draft PR, enforce identity and all 16 paths, and persist the PR number.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$repo = 'NOirBRight/github-work-orchestrator'
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$statePath = Join-Path $evidence 'state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$expectedApproval = "APPROVE-GWO-V8-C1-PR:$($state.beta1_sha)->main@$($state.base_sha)"
if ($env:GWO_V8_C1_OWNER -cne 'NOirBRight' -or $env:GWO_V8_C1_PR_APPROVAL -cne $expectedApproval -or
    -not $state.reviews_complete -or $state.pr_approval.approver -cne 'NOirBRight' -or $state.pr_approval.text -cne $expectedApproval) { throw 'Review or PR approval evidence is invalid.' }
function Read-RemoteHead([string]$Name) {
    $rows = @(git -C $root ls-remote --heads origin "refs/heads/$Name")
    if ($rows.Count -ne 1) { throw "Remote head missing or ambiguous: $Name" }
    return (($rows[0] -split '\s+')[0])
}
if ((Read-RemoteHead 'main') -ne $state.base_sha -or
    (Read-RemoteHead 'codex/gwo-v8-beta1') -ne $state.beta1_sha -or
    (Read-RemoteHead 'codex/gwo-v8-ga-plan') -ne $state.protected_ga_sha) { throw 'Remote refs drifted before PR creation.' }
$existing = @(gh pr list --repo $repo --head codex/gwo-v8-beta1 --base main --state all --limit 100 --json number,url,state,headRefName,baseRefName,headRefOid,isDraft | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw 'Cannot read existing Beta1 PRs.' }
if ($existing.Count -gt 1) { throw 'More than one Beta1-to-main PR exists.' }
if ($existing.Count -eq 1 -and $existing[0].state -ne 'OPEN') { throw 'A closed or merged Beta1 PR already exists; stop instead of creating another.' }
if ($existing.Count -eq 0) {
    $body = @(
        '## Summary',
        '- publish the C0-validated GWO V8 Core Preview metadata',
        '- bind the convergence receipt and authorized validator regression',
        '- preserve no production admission and no writer activation',
        '',
        '## Validation',
        "- package: $($state.beta1_package_summary)",
        "- full pytest: $($state.beta1_full_summary)",
        '- quick validation: passed',
        '- package synchronization: passed',
        '- diff check: passed',
        '',
        '## Non-goals',
        '- no production admission',
        '- no V8 writer activation',
        '- no #113-#119 closure'
    ) -join [Environment]::NewLine
    gh pr create --repo $repo --base main --head codex/gwo-v8-beta1 --draft --title 'V8 Beta1: publish the C0-validated Core Preview' --body $body
    if ($LASTEXITCODE -ne 0) { throw 'Draft PR creation failed.' }
}
$prs = @(gh pr list --repo $repo --head codex/gwo-v8-beta1 --base main --state open --limit 100 --json number,url,state,headRefName,baseRefName,headRefOid,isDraft | ConvertFrom-Json)
if ($prs.Count -ne 1) { throw 'Exactly one open Beta1 PR is required.' }
$pr = $prs[0]
if ($pr.headRefName -ne 'codex/gwo-v8-beta1' -or $pr.baseRefName -ne 'main' -or
    $pr.headRefOid -ne $state.beta1_sha -or -not $pr.isDraft) { throw 'Beta1 Draft PR identity is wrong.' }
$expectedPaths = @(
    '.superpowers/sdd/2026-08-03-gwo-v8-ga-delivery-program/task-1-report.md','docs/design/gwo-v8-lean-roadmap.md',
    'docs/releases/gwo-v8-release-train.md','docs/releases/gwo-v8-workspace-convergence.md','docs/releases/v8.0.0-beta.1.md',
    'docs/superpowers/plans/2026-08-03-gwo-v8-batch-delivery.md','docs/superpowers/plans/2026-08-03-gwo-v8-campaign-watchdog.md',
    'docs/superpowers/plans/2026-08-03-gwo-v8-candidate-assurance.md','docs/superpowers/plans/2026-08-03-gwo-v8-cutover-guard.md',
    'docs/superpowers/plans/2026-08-03-gwo-v8-ga-delivery-program.md','docs/superpowers/plans/2026-08-03-gwo-v8-production-composition.md',
    'docs/superpowers/plans/2026-08-03-gwo-v8-root-canary-ga.md','docs/superpowers/plans/2026-08-04-gwo-v8-ga-release-program.md',
    'docs/superpowers/plans/2026-08-04-gwo-v8-workspace-convergence-gate.md','scripts/quick_validate.py','tests/test_orchestrator_package.py'
) | Sort-Object
$actualPaths = @(gh pr diff $pr.number --repo $repo --name-only | Sort-Object)
if ($LASTEXITCODE -ne 0) { throw 'Cannot read the Beta1 PR diff.' }
$pathDiff = @(Compare-Object $expectedPaths $actualPaths)
if ($pathDiff) { $pathDiff; throw 'The remote PR path allowlist failed.' }
$state | Add-Member -NotePropertyName pr_number -NotePropertyValue $pr.number -Force
$state | Add-Member -NotePropertyName pr_url -NotePropertyValue $pr.url -Force
$state | Add-Member -NotePropertyName pr_head_sha -NotePropertyValue $pr.headRefOid -Force
$state | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $statePath -Encoding utf8NoBOM
~~~

Expected: one Draft PR URL, exact head/base SHAs, and exactly the approved 16 paths.

---

### Task 4: Pass exact-head checks, merge normally, and verify merged main

**Files:**
- Read: PR checks/reviews and `state.json`
- Create: Task 4 merge/CI/local-gate logs; persist merge and CI identities in `state.json`
- Remote artifact: one normal merge commit on `main`

**Interfaces:**
- Consumes: exact Draft PR and PR-scoped owner approval.
- Produces: a two-parent merge SHA plus one successful exact-SHA `GWO CI` run.

- [ ] **Step 1: Mark ready, wait for exact-head checks, merge with head matching, and persist the merge SHA.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$repo = 'NOirBRight/github-work-orchestrator'
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$statePath = Join-Path $evidence 'state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$expectedApproval = "APPROVE-GWO-V8-C1-PR:$($state.beta1_sha)->main@$($state.base_sha)"
if ($env:GWO_V8_C1_OWNER -cne 'NOirBRight' -or $env:GWO_V8_C1_PR_APPROVAL -cne $expectedApproval -or
    -not $state.reviews_complete -or $state.pr_approval.approver -cne 'NOirBRight' -or $state.pr_approval.text -cne $expectedApproval -or -not $state.pr_number) { throw 'Review, PR approval, or PR identity evidence is invalid.' }
$reviewNames = @('task-2-review-1-standards.md','task-2-review-2-spec.md','task-2-review-3-git-scope.md','task-2-review-4-tracker.md','task-2-review-5-release.md')
foreach ($name in $reviewNames) {
    $lines = @(Get-Content -LiteralPath (Join-Path $evidence $name) | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($lines.Count -eq 0 -or $lines[-1] -cne 'Verdict: PASS') { throw "Review gate drifted: $name" }
}
$pr = gh pr view $state.pr_number --repo $repo --json number,state,isDraft,headRefOid,baseRefName,baseRefOid,reviewDecision,statusCheckRollup | ConvertFrom-Json
if ($pr.state -ne 'OPEN' -or $pr.headRefOid -ne $state.beta1_sha -or $pr.baseRefName -ne 'main' -or $pr.baseRefOid -ne $state.base_sha) { throw 'PR identity drifted before checks.' }
if ($pr.isDraft) {
    gh pr ready $state.pr_number --repo $repo
    if ($LASTEXITCODE -ne 0) { throw 'Cannot mark the exact PR ready.' }
}
gh pr checks $state.pr_number --repo $repo --watch --fail-fast
if ($LASTEXITCODE -ne 0) { throw 'Required PR checks did not pass.' }
$checked = gh pr view $state.pr_number --repo $repo --json state,isDraft,headRefOid,baseRefName,baseRefOid,reviewDecision,statusCheckRollup | ConvertFrom-Json
# The five exact external review reports above are the required Standards/Spec gate.
# GitHub reviewDecision may therefore be empty when the repository has no separate required-review rule;
# REVIEW_REQUIRED and CHANGES_REQUESTED remain blocking, while APPROVED is accepted.
if ($checked.state -ne 'OPEN' -or $checked.isDraft -or $checked.headRefOid -ne $state.beta1_sha -or
    $checked.baseRefName -ne 'main' -or $checked.baseRefOid -ne $state.base_sha -or
    $checked.reviewDecision -notin @($null,'','APPROVED')) {
    throw 'Exact-head PR review/check readback failed.'
}
function Read-RemoteHead([string]$Name) {
    $rows = @(git -C $root ls-remote --heads origin "refs/heads/$Name")
    if ($rows.Count -ne 1) { throw "Remote head missing or ambiguous: $Name" }
    return (($rows[0] -split '\s+')[0])
}
if ((Read-RemoteHead 'main') -ne $state.base_sha -or
    (Read-RemoteHead 'codex/gwo-v8-beta1') -ne $state.beta1_sha -or
    (Read-RemoteHead 'codex/gwo-v8-ga-plan') -ne $state.protected_ga_sha) { throw 'A frozen ref moved before merge.' }
gh pr merge $state.pr_number --repo $repo --merge --match-head-commit $state.beta1_sha
if ($LASTEXITCODE -ne 0) { throw 'Normal exact-head merge failed.' }
$mergedPr = gh pr view $state.pr_number --repo $repo --json state,mergedAt,mergeCommit,headRefOid,url | ConvertFrom-Json
if ($mergedPr.state -ne 'MERGED' -or $mergedPr.headRefOid -ne $state.beta1_sha -or -not $mergedPr.mergeCommit.oid) { throw 'Merged PR readback failed.' }
$merged = $mergedPr.mergeCommit.oid
git -C $root fetch origin main
if ($LASTEXITCODE -ne 0) { throw 'Cannot fetch merged main.' }
if ((git -C $root rev-parse refs/remotes/origin/main).Trim() -ne $merged) { throw 'Remote main is not the PR merge commit.' }
$parents = @((git -C $root show -s --format=%P $merged).Trim() -split ' ' | Where-Object { $_ })
if ($parents.Count -ne 2 -or $parents[0] -ne $state.base_sha -or $parents[1] -ne $state.beta1_sha) {
    throw 'Beta1 was not merged as the required normal two-parent merge.'
}
$state | Add-Member -NotePropertyName merged_main_sha -NotePropertyValue $merged -Force
$state | Add-Member -NotePropertyName merged_at -NotePropertyValue $mergedPr.mergedAt -Force
$state | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $statePath -Encoding utf8NoBOM
~~~

Expected: checks pass at `e081e390`; five exact external reviews remain PASS; GitHub reports neither `REVIEW_REQUIRED` nor `CHANGES_REQUESTED`; `main` is still the frozen base immediately before merge; and the resulting merge has parents `a48c7d6` then `e081e390`. Omitting `--delete-branch` retains the source branch.

- [ ] **Step 2: Bounded-wait for the exact merged-main `GWO CI` run and persist its dynamic summary.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$repo = 'NOirBRight/github-work-orchestrator'
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$statePath = Join-Path $evidence 'state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$sha = $state.merged_main_sha
if (-not $sha) { throw 'Merged-main SHA is absent.' }
$deadline = (Get-Date).ToUniversalTime().AddMinutes(30)
$run = $null
do {
    $runs = @(gh run list --repo $repo --commit $sha --workflow 'GWO CI' --limit 20 --json databaseId,url,headSha,status,conclusion,createdAt,name | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot query exact-SHA GWO CI.' }
    $run = @($runs | Where-Object headSha -eq $sha | Sort-Object { [datetime]$_.createdAt } -Descending | Select-Object -First 1)
    if ($run.Count -eq 1 -and $run[0].status -eq 'completed') { break }
    if ((Get-Date).ToUniversalTime() -ge $deadline) { throw 'Timed out after 30 minutes waiting for exact-SHA GWO CI.' }
    Start-Sleep -Seconds 15
} while ($true)
$run = $run[0]
if ($run.conclusion -ne 'success') { throw "Exact merged-main GWO CI completed as $($run.conclusion)." }
$readback = gh run view $run.databaseId --repo $repo --json databaseId,url,headSha,status,conclusion,name | ConvertFrom-Json
if ($readback.headSha -ne $sha -or $readback.status -ne 'completed' -or $readback.conclusion -ne 'success') { throw 'Exact CI readback is not green.' }
$log = @(gh run view $run.databaseId --repo $repo --log 2>&1)
$logExit = $LASTEXITCODE
$log | Set-Content -LiteralPath (Join-Path $evidence 'task-4-ci.log') -Encoding utf8NoBOM
if ($logExit -ne 0) { throw 'Cannot read exact CI logs.' }
$summaries = @($log | Select-String -Pattern '[0-9][0-9,]* passed')
if ($summaries.Count -eq 0) { throw 'Exact CI log has no dynamic pytest summary.' }
$state | Add-Member -NotePropertyName ci_run_id -NotePropertyValue $run.databaseId -Force
$state | Add-Member -NotePropertyName ci_url -NotePropertyValue $run.url -Force
$state | Add-Member -NotePropertyName ci_summary -NotePropertyValue $summaries[-1].Line.Trim() -Force
$state | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $statePath -Encoding utf8NoBOM
~~~

Expected: one completed successful `GWO CI` run whose `headSha` equals the persisted merge SHA; queued/in-progress CI is waited for rather than misclassified as failure.

- [ ] **Step 3: Run the merged package/quick/sync/diff gate in an exact detached checkout.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$state = Get-Content -Raw -LiteralPath (Join-Path $evidence 'state.json') | ConvertFrom-Json
$archiveRoot = 'D:/gwo-convergence-archive/20260804T185544Z'
if (-not (Test-Path -LiteralPath $archiveRoot -PathType Container)) { throw "Required C0 archive is absent: $archiveRoot" }
$verify = 'D:/Workstation/gwo-worktrees/c1-beta1-merged-verify'
if (Test-Path -LiteralPath $verify) { throw "Merged verification checkout already exists: $verify" }
git -C $root worktree add --detach $verify $state.merged_main_sha
if ($LASTEXITCODE -ne 0) { throw 'Cannot create merged verification checkout.' }
$completed = $false
Push-Location -LiteralPath $verify
try {
    if ((git rev-parse HEAD).Trim() -ne $state.merged_main_sha) { throw 'Merged checkout is on the wrong SHA.' }
    $previousArchiveRoot = $env:GWO_CONVERGENCE_ARCHIVE_ROOT
    try {
        $env:GWO_CONVERGENCE_ARCHIVE_ROOT = $archiveRoot
    $packageOutput = @(& py -3.13 -m pytest tests/test_orchestrator_package.py -q 2>&1)
    $packageExit = $LASTEXITCODE
    $packageOutput | Set-Content -LiteralPath (Join-Path $evidence 'task-4-local-package.log') -Encoding utf8NoBOM
    if ($packageExit -ne 0) { throw 'Merged package tests failed.' }
    $quickOutput = @(& py -3.13 scripts/quick_validate.py 2>&1)
    $quickExit = $LASTEXITCODE
    $quickOutput | Set-Content -LiteralPath (Join-Path $evidence 'task-4-local-quick.log') -Encoding utf8NoBOM
    if ($quickExit -ne 0) { throw 'Merged quick validation failed.' }
    $syncOutput = @(& py -3.13 scripts/sync_orchestrator.py --check 2>&1)
    $syncExit = $LASTEXITCODE
    $syncOutput | Set-Content -LiteralPath (Join-Path $evidence 'task-4-local-sync.log') -Encoding utf8NoBOM
    if ($syncExit -ne 0) { throw 'Merged sync check failed.' }
    $diffOutput = @(git diff --check "$($state.base_sha)...HEAD" 2>&1)
    $diffExit = $LASTEXITCODE
    $diffOutput | Set-Content -LiteralPath (Join-Path $evidence 'task-4-local-diff.log') -Encoding utf8NoBOM
    if ($diffExit -ne 0) { throw 'Merged diff check failed.' }
    $dirty = @(git status --porcelain=v1 --untracked-files=all)
    if ($dirty.Count -ne 0) { $dirty; throw 'Merged verification changed the checkout.' }
    } finally {
        if ($null -eq $previousArchiveRoot) {
            Remove-Item -LiteralPath Env:\GWO_CONVERGENCE_ARCHIVE_ROOT -ErrorAction SilentlyContinue
        } else {
            $env:GWO_CONVERGENCE_ARCHIVE_ROOT = $previousArchiveRoot
        }
    }
    $completed = $true
} finally {
    Pop-Location
    if ($completed) {
        git -C $root worktree remove $verify
        if ($LASTEXITCODE -ne 0) { throw "Merged verification checkout stayed registered: $verify" }
    } else {
        Write-Warning "Merged verification failed; preserved checkout for diagnosis: $verify"
    }
}
~~~

Expected: the exact merge SHA passes package, quick, sync, and diff checks, remains clean, and its temporary checkout is removed without force.

---

### Task 5: Execute the separately approved tracker and milestone follow-up

**Files:**
- Read: complete #113-#119 and #137 body, labels, comments, state, milestone, URL, and #137 native blockers
- Create: `task-5-tracker-before.json`, `task-5-tracker-approval.json`, `task-5-issue137-after.json`, and `task-5-tracker-after.json`
- Remote artifacts: conditional #137 reopen and three release milestones/assignments

**Interfaces:**
- Consumes: exact merged-main/CI evidence and a tracker-specific owner approval.
- Produces: preserved tracker semantics and conflict-safe, idempotent milestone assignments.

- [ ] **Step 1: Capture all tracker fields and reject milestone conflicts before any mutation.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$repo = 'NOirBRight/github-work-orchestrator'
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$numbers = @(113..119) + 137
$expectedMilestone = @{
    '113'='GWO V8 Beta2'; '114'='GWO V8 Beta2'; '115'='GWO V8 Beta2'; '116'='GWO V8 Beta2';
    '117'='GWO V8 Beta2'; '137'='GWO V8 Beta2'; '118'='GWO V8 Beta3'; '119'='GWO V8 GA'
}
$issues = @($numbers | ForEach-Object {
    gh issue view $_ --repo $repo --json number,state,title,body,labels,comments,milestone,url | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw "Cannot read Issue #$_" }
})
foreach ($issue in $issues) {
    $wanted = $expectedMilestone[[string]$issue.number]
    if ($issue.milestone -and $issue.milestone.title -ne $wanted) {
        throw "Issue #$($issue.number) already has conflicting milestone '$($issue.milestone.title)'."
    }
}
$blockers = gh api graphql -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){issue(number:$number){blockedBy(first:100){nodes{number state}}}}}' -F owner=NOirBRight -F name=github-work-orchestrator -F number=137 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot read #137 native blockers.' }
$milestones = @(gh api "repos/$repo/milestones?state=all&per_page=100" | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw 'Cannot read milestones.' }
foreach ($title in @('GWO V8 Beta2','GWO V8 Beta3','GWO V8 GA')) {
    $matches = @($milestones | Where-Object title -eq $title)
    if ($matches.Count -gt 1) { throw "Milestone title is ambiguous: $title" }
    if ($matches.Count -eq 1 -and $matches[0].state -ne 'open') { throw "Required milestone is closed: $title" }
}
$snapshot = [ordered]@{
    captured_at = (Get-Date -AsUTC -Format o)
    issues = $issues
    blocked_by = @($blockers.data.repository.issue.blockedBy.nodes)
    milestones = $milestones
}
$snapshot | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath (Join-Path $evidence 'task-5-tracker-before.json') -Encoding utf8NoBOM
~~~

Expected: complete bodies, labels, comments, states, milestones, URLs, and native blockers are persisted. Any nonempty wrong milestone or duplicate/closed required milestone stops before mutation.

- [ ] **Step 2: Require tracker-specific approval, reopen #137 only for the approved anomaly, and prove semantic preservation.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$repo = 'NOirBRight/github-work-orchestrator'
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$state = Get-Content -Raw -LiteralPath (Join-Path $evidence 'state.json') | ConvertFrom-Json
function Read-RemoteHead([string]$Name) {
    $rows = @(git -C $root ls-remote --heads origin "refs/heads/$Name")
    if ($rows.Count -ne 1) { throw "Remote head missing or ambiguous: $Name" }
    return (($rows[0] -split '\s+')[0])
}
if ((Read-RemoteHead 'main') -ne $state.merged_main_sha -or
    (Read-RemoteHead 'codex/gwo-v8-beta1') -ne $state.beta1_sha -or
    (Read-RemoteHead 'codex/gwo-v8-ga-plan') -ne $state.protected_ga_sha) { throw 'Release refs drifted before tracker mutation.' }
$ci = gh run view $state.ci_run_id --repo $repo --json headSha,status,conclusion | ConvertFrom-Json
if ($ci.headSha -ne $state.merged_main_sha -or $ci.status -ne 'completed' -or $ci.conclusion -ne 'success') { throw 'Exact-main CI is not valid before tracker mutation.' }
$expectedApproval = 'APPROVE-GWO-V8-C1-TRACKER-AND-MILESTONES-V1'
if ($env:GWO_V8_C1_OWNER -cne 'NOirBRight' -or $env:GWO_V8_C1_TRACKER_APPROVAL -cne $expectedApproval) {
    throw "STOP: tracker approval must be exactly $expectedApproval from NOirBRight."
}
$snapshot = Get-Content -Raw -LiteralPath (Join-Path $evidence 'task-5-tracker-before.json') | ConvertFrom-Json
$before = @($snapshot.issues | Where-Object number -eq 137)[0]
$fresh = gh issue view 137 --repo $repo --json number,state,title,body,labels,comments,milestone,url | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot re-read #137 before mutation.' }
$freshBlockersRaw = gh api graphql -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){issue(number:$number){blockedBy(first:100){nodes{number state}}}}}' -F owner=NOirBRight -F name=github-work-orchestrator -F number=137 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot re-read #137 blockers before mutation.' }
$freshBlockers = @($freshBlockersRaw.data.repository.issue.blockedBy.nodes)
function Canonical-Json($Value) { return ($Value | ConvertTo-Json -Depth 100 -Compress) }
$snapshotOpenBlockers = @($snapshot.blocked_by | Where-Object state -eq 'OPEN')
$needsReopen = $before.state -eq 'CLOSED' -and $snapshotOpenBlockers.Count -gt 0
$allowedFreshStates = if ($needsReopen) { @('CLOSED','OPEN') } else { @($before.state) }
if ($fresh.state -notin $allowedFreshStates -or $fresh.title -cne $before.title -or $fresh.body -cne $before.body -or
    (Canonical-Json $fresh.labels) -cne (Canonical-Json $before.labels) -or
    (Canonical-Json $fresh.comments) -cne (Canonical-Json $before.comments) -or
    $fresh.milestone.title -cne $before.milestone.title) { throw '#137 drifted after the pre-mutation snapshot.' }
$beforeBlockerKeys = @($snapshot.blocked_by | Sort-Object number | ForEach-Object { "$($_.number):$($_.state)" })
$freshBlockerKeys = @($freshBlockers | Sort-Object number | ForEach-Object { "$($_.number):$($_.state)" })
if (Compare-Object $beforeBlockerKeys $freshBlockerKeys) { throw '#137 native blockers drifted before mutation.' }
$approval = [ordered]@{ approver = $env:GWO_V8_C1_OWNER; text = $expectedApproval; approved_at = (Get-Date -AsUTC -Format o) }
$approvalPath = Join-Path $evidence 'task-5-tracker-approval.json'
if (Test-Path -LiteralPath $approvalPath) {
    $recordedApproval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json
    if ($recordedApproval.approver -cne $approval.approver -or $recordedApproval.text -cne $approval.text) { throw 'Existing tracker approval evidence conflicts.' }
    $approval = $recordedApproval
} else {
    $approval | ConvertTo-Json | Set-Content -LiteralPath $approvalPath -Encoding utf8NoBOM
}
if ($needsReopen -and $fresh.state -eq 'CLOSED') {
    gh issue reopen 137 --repo $repo
    if ($LASTEXITCODE -ne 0) { throw 'Approved #137 reopen failed.' }
}
$after = gh issue view 137 --repo $repo --json number,state,title,body,labels,comments,milestone,url | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot read #137 after conditional reopen.' }
$afterBlockersRaw = gh api graphql -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){issue(number:$number){blockedBy(first:100){nodes{number state}}}}}' -F owner=NOirBRight -F name=github-work-orchestrator -F number=137 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot read #137 blockers after conditional reopen.' }
$afterBlockers = @($afterBlockersRaw.data.repository.issue.blockedBy.nodes)
$expectedState = if ($needsReopen) { 'OPEN' } else { $before.state }
if ($after.state -ne $expectedState -or $after.title -cne $fresh.title -or $after.body -cne $fresh.body -or
    (Canonical-Json $after.labels) -cne (Canonical-Json $fresh.labels) -or
    (Canonical-Json $after.comments) -cne (Canonical-Json $fresh.comments) -or
    $after.milestone.title -cne $fresh.milestone.title) { throw '#137 semantic preservation failed.' }
$afterBlockerKeys = @($afterBlockers | Sort-Object number | ForEach-Object { "$($_.number):$($_.state)" })
if (Compare-Object $freshBlockerKeys $afterBlockerKeys) { throw '#137 native blockers changed.' }
[ordered]@{ issue = $after; blocked_by = $afterBlockers; reopened = $needsReopen; approval = $approval } |
    ConvertTo-Json -Depth 100 | Set-Content -LiteralPath (Join-Path $evidence 'task-5-issue137-after.json') -Encoding utf8NoBOM
~~~

Expected: #137 changes from CLOSED to OPEN only when at least one native blocker is OPEN. Otherwise its state is preserved. Body, labels, comments, milestone, and blocker relations are not rewritten.

- [ ] **Step 3: Create missing milestones and assign only unassigned Issues after a second conflict preflight.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$repo = 'NOirBRight/github-work-orchestrator'
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$statePath = Join-Path $evidence 'state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$before = Get-Content -Raw -LiteralPath (Join-Path $evidence 'task-5-tracker-before.json') | ConvertFrom-Json
$issue137After = Get-Content -Raw -LiteralPath (Join-Path $evidence 'task-5-issue137-after.json') | ConvertFrom-Json
$approval = Get-Content -Raw -LiteralPath (Join-Path $evidence 'task-5-tracker-approval.json') | ConvertFrom-Json
$expectedApproval = 'APPROVE-GWO-V8-C1-TRACKER-AND-MILESTONES-V1'
if ($env:GWO_V8_C1_OWNER -cne 'NOirBRight' -or $env:GWO_V8_C1_TRACKER_APPROVAL -cne $expectedApproval -or
    $approval.approver -cne 'NOirBRight' -or $approval.text -cne $expectedApproval) { throw 'Tracker approval evidence is invalid.' }
function Read-RemoteHead([string]$Name) {
    $rows = @(git -C $root ls-remote --heads origin "refs/heads/$Name")
    if ($rows.Count -ne 1) { throw "Remote head missing or ambiguous: $Name" }
    return (($rows[0] -split '\s+')[0])
}
if ((Read-RemoteHead 'main') -ne $state.merged_main_sha -or
    (Read-RemoteHead 'codex/gwo-v8-beta1') -ne $state.beta1_sha -or
    (Read-RemoteHead 'codex/gwo-v8-ga-plan') -ne $state.protected_ga_sha) { throw 'Release refs drifted before milestone mutation.' }
$ci = gh run view $state.ci_run_id --repo $repo --json headSha,status,conclusion | ConvertFrom-Json
if ($ci.headSha -ne $state.merged_main_sha -or $ci.status -ne 'completed' -or $ci.conclusion -ne 'success') { throw 'Exact-main CI is not valid before milestone mutation.' }
$plan = [ordered]@{ 'GWO V8 Beta2' = @(113,114,115,116,117,137); 'GWO V8 Beta3' = @(118); 'GWO V8 GA' = @(119) }
$expectedByNumber = @{}
foreach ($title in $plan.Keys) { foreach ($number in $plan[$title]) { $expectedByNumber[[string]$number] = $title } }
$currentIssues = @((@(113..119) + 137) | ForEach-Object {
    gh issue view $_ --repo $repo --json number,state,title,body,labels,comments,milestone,url | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw "Cannot re-read Issue #$_ before milestone mutation." }
})
function Canonical-Json($Value) { return ($Value | ConvertTo-Json -Depth 100 -Compress) }
foreach ($issue in $currentIssues) {
    $wanted = $expectedByNumber[[string]$issue.number]
    if ($issue.milestone -and $issue.milestone.title -ne $wanted) { throw "Conflicting milestone appeared on #$($issue.number)." }
    $original = @($before.issues | Where-Object number -eq $issue.number)[0]
    $reference = if ($issue.number -eq 137) { $issue137After.issue } else { $original }
    $expectedState = $reference.state
    if ($issue.state -ne $expectedState -or $issue.title -cne $reference.title -or $issue.body -cne $reference.body -or
        (Canonical-Json $issue.labels) -cne (Canonical-Json $reference.labels) -or
        (Canonical-Json $issue.comments) -cne (Canonical-Json $reference.comments)) {
        throw "Issue semantics drifted before milestone assignment: #$($issue.number)"
    }
}
$existing = @(gh api "repos/$repo/milestones?state=all&per_page=100" | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw 'Cannot re-read milestones before mutation.' }
foreach ($title in $plan.Keys) {
    $matches = @($existing | Where-Object title -eq $title)
    if ($matches.Count -gt 1 -or ($matches.Count -eq 1 -and $matches[0].state -ne 'open')) { throw "Milestone conflict: $title" }
}
foreach ($title in $plan.Keys) {
    if (@($existing | Where-Object title -eq $title).Count -eq 0) {
        gh api "repos/$repo/milestones" -f "title=$title" -f 'description=See docs/releases/gwo-v8-release-train.md' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Milestone creation failed: $title" }
    }
}
$all = @(gh api "repos/$repo/milestones?state=all&per_page=100" | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw 'Cannot read milestones after creation.' }
foreach ($title in $plan.Keys) {
    $milestone = @($all | Where-Object title -eq $title)
    if ($milestone.Count -ne 1 -or $milestone[0].state -ne 'open') { throw "Milestone readback failed: $title" }
    foreach ($number in $plan[$title]) {
        $freshIssue = gh issue view $number --repo $repo --json number,state,title,body,labels,comments,milestone,url | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw "Cannot re-read Issue #$number immediately before assignment." }
        $original = @($before.issues | Where-Object number -eq $number)[0]
        $reference = if ($number -eq 137) { $issue137After.issue } else { $original }
        if ($freshIssue.state -ne $reference.state -or $freshIssue.title -cne $reference.title -or
            $freshIssue.body -cne $reference.body -or
            (Canonical-Json $freshIssue.labels) -cne (Canonical-Json $reference.labels) -or
            (Canonical-Json $freshIssue.comments) -cne (Canonical-Json $reference.comments)) {
            throw "Issue semantics drifted immediately before assignment: #$number"
        }
        if ($freshIssue.milestone -and $freshIssue.milestone.title -ne $title) { throw "Concurrent milestone conflict appeared on #$number." }
        if (-not $freshIssue.milestone) {
            gh api -X PATCH "repos/$repo/issues/$number" -F "milestone=$($milestone[0].number)" | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Milestone assignment failed: #$number" }
        }
    }
}
$afterIssues = @((@(113..119) + 137) | ForEach-Object {
    gh issue view $_ --repo $repo --json number,state,title,body,labels,comments,milestone,url | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw "Cannot read Issue #$_ after milestone mutation." }
})
foreach ($issue in $afterIssues) {
    $wanted = $expectedByNumber[[string]$issue.number]
    $original = @($before.issues | Where-Object number -eq $issue.number)[0]
    $reference = if ($issue.number -eq 137) { $issue137After.issue } else { $original }
    if ($issue.milestone.title -ne $wanted -or $issue.state -ne $reference.state -or
        $issue.title -cne $reference.title -or $issue.body -cne $reference.body -or
        (Canonical-Json $issue.labels) -cne (Canonical-Json $reference.labels) -or
        (Canonical-Json $issue.comments) -cne (Canonical-Json $reference.comments)) {
        throw "Tracker readback failed: #$($issue.number)"
    }
}
$blockersAfterRaw = gh api graphql -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){issue(number:$number){blockedBy(first:100){nodes{number state}}}}}' -F owner=NOirBRight -F name=github-work-orchestrator -F number=137 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot read final #137 native blockers.' }
$expectedBlockerKeys = @($issue137After.blocked_by | Sort-Object number | ForEach-Object { "$($_.number):$($_.state)" })
$actualBlockerKeys = @($blockersAfterRaw.data.repository.issue.blockedBy.nodes | Sort-Object number | ForEach-Object { "$($_.number):$($_.state)" })
if (Compare-Object $expectedBlockerKeys $actualBlockerKeys) { throw '#137 native blockers changed during milestone assignment.' }
$afterSnapshot = [ordered]@{ captured_at = (Get-Date -AsUTC -Format o); issues = $afterIssues; milestones = $all; issue137 = $issue137After; blocked_by = @($blockersAfterRaw.data.repository.issue.blockedBy.nodes) }
$afterSnapshot | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath (Join-Path $evidence 'task-5-tracker-after.json') -Encoding utf8NoBOM
$state | Add-Member -NotePropertyName tracker_complete -NotePropertyValue $true -Force
$state | Add-Member -NotePropertyName tracker_completed_at -NotePropertyValue (Get-Date -AsUTC -Format o) -Force
$state | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $statePath -Encoding utf8NoBOM
~~~

Expected: #113-#117/#137 read back under Beta2, #118 under Beta3, and #119 under GA. Existing correct assignments are no-ops; conflicting nonempty assignments stop before any overwrite; Issue states remain unchanged except the approved conditional #137 reopen.

---

### Task 6: Create and verify the immutable Beta1 publication

**Files:**
- Read from the exact merged SHA: `docs/releases/v8.0.0-beta.1.md`
- Create: `task-6-publication-approval.json` and `v8.0.0-beta.1.notes.md`; persist tag and Release identities in `state.json`
- Remote artifacts: annotated `refs/tags/v8.0.0-beta.1` and GitHub prerelease `v8.0.0-beta.1`

**Interfaces:**
- Consumes: exact merge/CI evidence, completed tracker readback, and publication-specific SHA-bound owner approval.
- Produces: one immutable annotated tag and one non-draft prerelease targeting the same merged SHA.

- [ ] **Step 1: Recheck main, protected refs, CI, tracker assignments, and require publication-specific approval.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$repo = 'NOirBRight/github-work-orchestrator'
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$statePath = Join-Path $evidence 'state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if (-not $state.tracker_complete -or -not $state.merged_main_sha -or -not $state.ci_run_id) { throw 'Merge, CI, or tracker evidence is incomplete.' }
function Read-RemoteHead([string]$Name) {
    $rows = @(git -C $root ls-remote --heads origin "refs/heads/$Name")
    if ($rows.Count -ne 1) { throw "Remote head missing or ambiguous: $Name" }
    return (($rows[0] -split '\s+')[0])
}
if ((Read-RemoteHead 'main') -ne $state.merged_main_sha -or
    (Read-RemoteHead 'codex/gwo-v8-beta1') -ne $state.beta1_sha -or
    (Read-RemoteHead 'codex/gwo-v8-ga-plan') -ne $state.protected_ga_sha) { throw 'Release refs drifted.' }
$ci = gh run view $state.ci_run_id --repo $repo --json databaseId,url,headSha,status,conclusion,name | ConvertFrom-Json
if ($ci.headSha -ne $state.merged_main_sha -or $ci.status -ne 'completed' -or $ci.conclusion -ne 'success') { throw 'Persisted exact-main CI is no longer valid.' }
$expectedAssignments = @{ '113'='GWO V8 Beta2';'114'='GWO V8 Beta2';'115'='GWO V8 Beta2';'116'='GWO V8 Beta2';'117'='GWO V8 Beta2';'137'='GWO V8 Beta2';'118'='GWO V8 Beta3';'119'='GWO V8 GA' }
foreach ($number in @(113,114,115,116,117,137,118,119)) {
    $issue = gh issue view $number --repo $repo --json number,milestone | ConvertFrom-Json
    if ($issue.milestone.title -ne $expectedAssignments[[string]$number]) { throw "Milestone drifted before publication: #$number" }
}
$expectedApproval = "APPROVE-GWO-V8-C1-PUBLISH-V8.0.0-BETA.1@$($state.merged_main_sha)"
if ($env:GWO_V8_C1_OWNER -cne 'NOirBRight' -or $env:GWO_V8_C1_PUBLICATION_APPROVAL -cne $expectedApproval) {
    throw "STOP: publication approval must be exactly $expectedApproval from NOirBRight."
}
$approval = [ordered]@{ approver = $env:GWO_V8_C1_OWNER; text = $expectedApproval; approved_at = (Get-Date -AsUTC -Format o) }
$approvalPath = Join-Path $evidence 'task-6-publication-approval.json'
if (Test-Path -LiteralPath $approvalPath) {
    $recordedApproval = Get-Content -Raw -LiteralPath $approvalPath | ConvertFrom-Json
    if ($recordedApproval.approver -cne $approval.approver -or $recordedApproval.text -cne $approval.text) { throw 'Existing publication approval evidence conflicts.' }
    $approval = $recordedApproval
} else {
    $approval | ConvertTo-Json | Set-Content -LiteralPath $approvalPath -Encoding utf8NoBOM
}
$state | Add-Member -NotePropertyName publication_approval -NotePropertyValue $approval -Force
$state | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $statePath -Encoding utf8NoBOM
~~~

Expected: main still equals the merge SHA, Beta1 and protected GA are unchanged, exact CI remains successful, milestone assignments are exact, and approval text is bound to the merge SHA.

- [ ] **Step 2: Verify or create the annotated tag, generate notes from the merged SHA, and verify or create the prerelease.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$repo = 'NOirBRight/github-work-orchestrator'
$tagName = 'v8.0.0-beta.1'
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$statePath = Join-Path $evidence 'state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$sha = $state.merged_main_sha
$expectedApproval = "APPROVE-GWO-V8-C1-PUBLISH-V8.0.0-BETA.1@$sha"
if ($env:GWO_V8_C1_OWNER -cne 'NOirBRight' -or $env:GWO_V8_C1_PUBLICATION_APPROVAL -cne $expectedApproval -or
    $state.publication_approval.approver -cne 'NOirBRight' -or $state.publication_approval.text -cne $expectedApproval) { throw 'Publication approval evidence is invalid.' }
function Read-RemoteHead([string]$Name) {
    $rows = @(git -C $root ls-remote --heads origin "refs/heads/$Name")
    if ($rows.Count -ne 1) { throw "Remote head missing or ambiguous: $Name" }
    return (($rows[0] -split '\s+')[0])
}
if ((Read-RemoteHead 'main') -ne $sha -or
    (Read-RemoteHead 'codex/gwo-v8-beta1') -ne $state.beta1_sha -or
    (Read-RemoteHead 'codex/gwo-v8-ga-plan') -ne $state.protected_ga_sha) { throw 'Release refs drifted before publication.' }
$ci = gh run view $state.ci_run_id --repo $repo --json headSha,status,conclusion | ConvertFrom-Json
if ($ci.headSha -ne $sha -or $ci.status -ne 'completed' -or $ci.conclusion -ne 'success') { throw 'Exact-main CI is not valid before publication.' }
$expectedAssignments = @{ '113'='GWO V8 Beta2';'114'='GWO V8 Beta2';'115'='GWO V8 Beta2';'116'='GWO V8 Beta2';'117'='GWO V8 Beta2';'137'='GWO V8 Beta2';'118'='GWO V8 Beta3';'119'='GWO V8 GA' }
foreach ($number in @(113,114,115,116,117,137,118,119)) {
    $issue = gh issue view $number --repo $repo --json number,milestone | ConvertFrom-Json
    if ($issue.milestone.title -ne $expectedAssignments[[string]$number]) { throw "Milestone drifted before publication: #$number" }
}
$tagRows = @(git -C $root ls-remote --tags origin "refs/tags/$tagName" "refs/tags/$tagName^{}")
if ($LASTEXITCODE -ne 0) { throw 'Cannot read remote tag state.' }
$parsedTags = @($tagRows | ForEach-Object { $parts = $_ -split '\s+'; [pscustomobject]@{ sha = $parts[0]; ref = $parts[1] } })
$direct = @($parsedTags | Where-Object ref -eq "refs/tags/$tagName")
$peeled = @($parsedTags | Where-Object ref -eq "refs/tags/$tagName^{}")
$releaseProbe = @(gh api "repos/$repo/releases/tags/$tagName" 2>&1)
$releaseProbeExit = $LASTEXITCODE
if ($releaseProbeExit -ne 0 -and (($releaseProbe -join "`n") -notmatch '\(HTTP 404\)')) { throw 'Cannot read GitHub Release state.' }
$releaseExists = $releaseProbeExit -eq 0
if ($direct.Count -eq 0 -and $peeled.Count -gt 0) { throw 'Remote tag has a peeled ref without its direct ref.' }
if ($direct.Count -eq 0 -and $releaseExists) { throw 'Release exists without the required remote tag.' }
if ($direct.Count -gt 0) {
    if ($direct.Count -ne 1 -or $peeled.Count -ne 1 -or $peeled[0].sha -ne $sha) { throw 'Existing remote tag is not the required annotated tag target.' }
} else {
    git -C $root show-ref --verify --quiet "refs/tags/$tagName"
    $localTagExists = $LASTEXITCODE -eq 0
    if ($localTagExists) {
        if ((git -C $root cat-file -t "refs/tags/$tagName").Trim() -ne 'tag' -or
            (git -C $root rev-parse "refs/tags/$tagName^{}").Trim() -ne $sha) { throw 'Existing local tag is not the required annotated target.' }
    } else {
        git -C $root tag -a $tagName $sha -m 'GWO V8 Beta1 - Core Preview'
        if ($LASTEXITCODE -ne 0) { throw 'Annotated local Beta1 tag creation failed.' }
    }
    git -C $root push origin "refs/tags/$tagName:refs/tags/$tagName"
    if ($LASTEXITCODE -ne 0) { throw 'Remote tag push failed; stop and read back any concurrent object.' }
}
$remoteAfter = @(git -C $root ls-remote --tags origin "refs/tags/$tagName" "refs/tags/$tagName^{}")
$parsedAfter = @($remoteAfter | ForEach-Object { $parts = $_ -split '\s+'; [pscustomobject]@{ sha = $parts[0]; ref = $parts[1] } })
$directAfter = @($parsedAfter | Where-Object ref -eq "refs/tags/$tagName")
$peeledAfter = @($parsedAfter | Where-Object ref -eq "refs/tags/$tagName^{}")
if ($directAfter.Count -ne 1 -or $peeledAfter.Count -ne 1 -or $peeledAfter[0].sha -ne $sha) { throw 'Remote annotated-tag readback failed.' }
$notesPath = Join-Path $evidence 'v8.0.0-beta.1.notes.md'
$notes = @(git -C $root show "${sha}:docs/releases/v8.0.0-beta.1.md")
if ($LASTEXITCODE -ne 0 -or $notes.Count -eq 0) { throw 'Cannot read release notes from the exact merged SHA.' }
$notes | Set-Content -LiteralPath $notesPath -Encoding utf8NoBOM
if (-not $releaseExists) {
    gh release create $tagName --repo $repo --verify-tag --prerelease --target $sha --title 'GWO V8 Beta1 - Core Preview' --notes-file $notesPath
    if ($LASTEXITCODE -ne 0) { throw 'GitHub prerelease creation failed.' }
}
$release = gh release view $tagName --repo $repo --json tagName,targetCommitish,isDraft,isPrerelease,url,body | ConvertFrom-Json
if ($release.tagName -ne $tagName -or $release.targetCommitish -ne $sha -or $release.isDraft -or -not $release.isPrerelease) {
    throw 'GitHub prerelease identity readback failed.'
}
$expectedBody = (($notes -join "`n").TrimEnd())
$actualBody = (($release.body -replace "`r`n","`n").TrimEnd())
if ($actualBody -cne $expectedBody) { throw 'GitHub prerelease body differs from exact merged-SHA notes.' }
$state | Add-Member -NotePropertyName tag_name -NotePropertyValue $tagName -Force
$state | Add-Member -NotePropertyName tag_peeled_sha -NotePropertyValue $peeledAfter[0].sha -Force
$state | Add-Member -NotePropertyName release_url -NotePropertyValue $release.url -Force
$state | Add-Member -NotePropertyName published_at -NotePropertyValue (Get-Date -AsUTC -Format o) -Force
$state | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $statePath -Encoding utf8NoBOM
~~~

Expected: the remote tag has both direct and peeled refs and peels to the merge SHA; notes come from that SHA rather than the controller checkout; the Release is non-draft, prerelease, exact-target, and body-identical.

---

### Task 7: Close C1 and hand off the frozen C2 boundaries

**Files:**
- Read: all Tasks 0-6 evidence and fresh remote readbacks
- Create: `task-7-report.md`

**Interfaces:**
- Consumes: complete C1 publication evidence.
- Produces: a traceable C1 PASS, current canonical main, and exact C2 handoff subjects.

- [ ] **Step 1: Perform final refs, CI, tag, Release, tracker, and clean-worktree readback; then fast-forward only canonical local main.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$repo = 'NOirBRight/github-work-orchestrator'
$canonical = 'D:/Workstation/github-work-orchestrator'
$gaRoot = 'D:/Workstation/gwo-worktrees/issue-136'
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$statePath = Join-Path $evidence 'state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
function Read-RemoteHead([string]$Name) {
    $rows = @(git -C $root ls-remote --heads origin "refs/heads/$Name")
    if ($rows.Count -ne 1) { throw "Remote head missing or ambiguous: $Name" }
    return (($rows[0] -split '\s+')[0])
}
if ((Read-RemoteHead 'main') -ne $state.merged_main_sha -or
    (Read-RemoteHead 'codex/gwo-v8-beta1') -ne $state.beta1_sha -or
    (Read-RemoteHead 'codex/gwo-v8-ga-plan') -ne $state.protected_ga_sha) { throw 'Final remote ref identity failed.' }
if ((git -C $root rev-parse refs/heads/codex/gwo-v8-beta1).Trim() -ne $state.beta1_sha -or
    (git -C $root rev-parse refs/heads/codex/gwo-v8-ga-plan).Trim() -ne $state.protected_ga_sha) { throw 'Final local protected branch identity failed.' }
$ci = gh run view $state.ci_run_id --repo $repo --json databaseId,url,headSha,status,conclusion,name | ConvertFrom-Json
if ($ci.headSha -ne $state.merged_main_sha -or $ci.status -ne 'completed' -or $ci.conclusion -ne 'success') { throw 'Final CI identity failed.' }
$tagRows = @(git -C $root ls-remote --tags origin "refs/tags/$($state.tag_name)" "refs/tags/$($state.tag_name)^{}")
$parsed = @($tagRows | ForEach-Object { $parts = $_ -split '\s+'; [pscustomobject]@{ sha = $parts[0]; ref = $parts[1] } })
$direct = @($parsed | Where-Object ref -eq "refs/tags/$($state.tag_name)")
$peeled = @($parsed | Where-Object ref -eq "refs/tags/$($state.tag_name)^{}")
if ($direct.Count -ne 1 -or $peeled.Count -ne 1 -or $peeled[0].sha -ne $state.merged_main_sha) { throw 'Final tag identity failed.' }
$release = gh release view $state.tag_name --repo $repo --json tagName,targetCommitish,isDraft,isPrerelease,url,body | ConvertFrom-Json
if ($release.tagName -ne $state.tag_name -or $release.targetCommitish -ne $state.merged_main_sha -or
    $release.url -ne $state.release_url -or $release.isDraft -or -not $release.isPrerelease) { throw 'Final Release identity failed.' }
$expectedBody = ((Get-Content -Raw -LiteralPath (Join-Path $evidence 'v8.0.0-beta.1.notes.md') -Encoding utf8).Replace("`r`n","`n").TrimEnd())
$actualBody = (($release.body -replace "`r`n","`n").TrimEnd())
if ($actualBody -cne $expectedBody) { throw 'Final Release body identity failed.' }
$expectedAssignments = @{ '113'='GWO V8 Beta2';'114'='GWO V8 Beta2';'115'='GWO V8 Beta2';'116'='GWO V8 Beta2';'117'='GWO V8 Beta2';'137'='GWO V8 Beta2';'118'='GWO V8 Beta3';'119'='GWO V8 GA' }
$trackerAfter = Get-Content -Raw -LiteralPath (Join-Path $evidence 'task-5-tracker-after.json') | ConvertFrom-Json
function Canonical-Json($Value) { return ($Value | ConvertTo-Json -Depth 100 -Compress) }
foreach ($number in @(113,114,115,116,117,137,118,119)) {
    $issue = gh issue view $number --repo $repo --json number,state,title,body,labels,comments,milestone,url | ConvertFrom-Json
    $reference = @($trackerAfter.issues | Where-Object number -eq $number)[0]
    if ($issue.milestone.title -ne $expectedAssignments[[string]$number] -or $issue.state -ne $reference.state -or
        $issue.title -cne $reference.title -or $issue.body -cne $reference.body -or
        (Canonical-Json $issue.labels) -cne (Canonical-Json $reference.labels) -or
        (Canonical-Json $issue.comments) -cne (Canonical-Json $reference.comments)) {
        throw "Final tracker identity failed: #$number"
    }
}
$blockersRaw = gh api graphql -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){issue(number:$number){blockedBy(first:100){nodes{number state}}}}}' -F owner=NOirBRight -F name=github-work-orchestrator -F number=137 | ConvertFrom-Json
$expectedBlockers = @($trackerAfter.blocked_by | Sort-Object number | ForEach-Object { "$($_.number):$($_.state)" })
$actualBlockers = @($blockersRaw.data.repository.issue.blockedBy.nodes | Sort-Object number | ForEach-Object { "$($_.number):$($_.state)" })
if (Compare-Object $expectedBlockers $actualBlockers) { throw 'Final #137 native blocker identity failed.' }
if ((git -C $canonical symbolic-ref --short HEAD).Trim() -ne 'main') { throw 'Canonical checkout left main.' }
$canonicalDirty = @(git -C $canonical status --porcelain=v1 --untracked-files=all)
if ($canonicalDirty.Count -ne 0) { $canonicalDirty; throw 'Canonical main is dirty.' }
$localMain = (git -C $canonical rev-parse HEAD).Trim()
if ($localMain -notin @($state.base_sha,$state.merged_main_sha)) { throw 'Canonical local main has an unexpected commit.' }
git -C $canonical fetch origin main
if ($LASTEXITCODE -ne 0 -or (git -C $canonical rev-parse refs/remotes/origin/main).Trim() -ne $state.merged_main_sha) { throw 'Canonical fetch readback failed.' }
if ($localMain -eq $state.base_sha) {
    git -C $canonical merge --ff-only $state.merged_main_sha
    if ($LASTEXITCODE -ne 0) { throw 'Canonical main did not fast-forward to the Beta1 merge.' }
}
function Normalize-Path([string]$Path) { return ([IO.Path]::GetFullPath($Path).Replace('\','/')).TrimEnd([char[]]'/') }
$expectedRoots = @((Normalize-Path $canonical),(Normalize-Path $gaRoot),(Normalize-Path $root)) | Sort-Object -Unique
$actualRoots = @(git -C $root worktree list --porcelain | Select-String '^worktree ' | ForEach-Object { Normalize-Path $_.Line.Substring(9) } | Sort-Object -Unique)
if (Compare-Object $expectedRoots $actualRoots) { throw 'Unexpected worktree exists at C1 closure.' }
foreach ($path in $expectedRoots) {
    $dirty = @(git -C $path status --porcelain=v1 --untracked-files=all)
    if ($dirty.Count -ne 0) { $dirty; throw "Closure worktree is dirty: $path" }
}
if ((git -C $canonical rev-parse HEAD).Trim() -ne $state.merged_main_sha -or
    (git -C $gaRoot rev-parse HEAD).Trim() -ne $state.protected_ga_sha) { throw 'Final local ref identity failed.' }
$state | Add-Member -NotePropertyName final_verified -NotePropertyValue $true -Force
$state | Add-Member -NotePropertyName final_verified_at -NotePropertyValue (Get-Date -AsUTC -Format o) -Force
$state | Add-Member -NotePropertyName canonical_main_sha -NotePropertyValue (git -C $canonical rev-parse HEAD).Trim() -Force
$state | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $statePath -Encoding utf8NoBOM
~~~

Expected: remote and local main equal the Beta1 merge, Beta1/GA refs remain frozen, exact CI is green, tag/Release/milestones are exact, and only the two C0 roots plus the current coordinator root exist and are clean.

- [ ] **Step 2: Write the C1 closure report and exact C2 handoff.**

~~~powershell
$ErrorActionPreference = 'Stop'
$root = (git rev-parse --show-toplevel).Trim()
$evidence = 'D:/gwo-release-evidence/2026-08-05-gwo-v8-c1-beta1-core-preview'
$state = Get-Content -Raw -LiteralPath (Join-Path $evidence 'state.json') | ConvertFrom-Json
$requiredState = @('pr_number','pr_url','beta1_sha','merged_main_sha','ci_run_id','ci_url','ci_summary','tag_name','tag_peeled_sha','release_url','canonical_main_sha','final_verified_at')
foreach ($name in $requiredState) {
    if ([string]::IsNullOrWhiteSpace([string]$state.$name)) { throw "Closure state is missing: $name" }
}
if (-not $state.final_verified -or -not $state.tracker_complete -or $state.canonical_main_sha -ne $state.merged_main_sha) { throw 'Closure gates are not complete.' }
foreach ($name in @('task-3-pr-approval.json','task-5-tracker-approval.json','task-5-tracker-after.json','task-6-publication-approval.json','v8.0.0-beta.1.notes.md')) {
    if (-not (Test-Path -LiteralPath (Join-Path $evidence $name) -PathType Leaf)) { throw "Closure evidence file is absent: $name" }
}
$reportPath = Join-Path $evidence 'task-7-report.md'
if (Test-Path -LiteralPath $reportPath) { throw "Closure report already exists; do not overwrite it: $reportPath" }
$lines = @(
    '# C1 Beta1 Core Preview Closure',
    '',
    '## Verdict',
    '',
    '**PASS**',
    '',
    'C1 Beta1 Core Preview is published; Lean V8 production admission and default-writer activation remain disabled.',
    '',
    '## Exact evidence',
    '',
    "- PR: #$($state.pr_number) $($state.pr_url)",
    "- PR head: $($state.beta1_sha)",
    "- Merge SHA: $($state.merged_main_sha)",
    "- GWO CI: $($state.ci_url)",
    "- Dynamic summary: $($state.ci_summary)",
    "- Tag: $($state.tag_name) -> $($state.tag_peeled_sha)",
    "- Release: $($state.release_url)",
    '- Tracker evidence: task-5-tracker-before.json and task-5-tracker-after.json',
    '- Approval evidence: task-3-pr-approval.json, task-5-tracker-approval.json, task-6-publication-approval.json',
    '',
    '## C2 frozen handoff',
    '',
    '- Candidate foundation: 77ac3e3ef14241d1840150b22cb227d2e5088fb4',
    '- #113 Watchdog: 07086ce1036198a41547ca1d9a9a506acfb8fcf7',
    '- #114 CandidateGate: 657bf236d765735cdee117910a5939c6c2cd3292',
    '- #115 Review/Repair: a0f697656be6471bed601103c169185988a9e4ac',
    '- #116 Batch WIP: e58c596998df90e65349bdb4b5f25d3d9dc1f7e2'
)
$lines | Set-Content -LiteralPath $reportPath -Encoding utf8NoBOM
~~~

Expected: the report contains the exact PR, merge, CI, tracker, approval, tag, Release, no-writer statement, and full C2 boundary SHAs.

## C1 Stop Rules

Stop immediately when `hosted_ci_compatible` is false, or on any frozen ref drift, wrong merge-base/ancestry/path, failed focused/full/quick/sync/diff/CI gate, non-PASS structured reviewer verdict, absent scoped approval, PR head/base movement, non-normal merge, incomplete Issue readback, changed #137 semantics, conflicting milestone, partial/mismatched tag or Release, or dirty verification checkout. Never compensate with force-push, direct main push, object deletion/recreation, wildcard deletion, ACL changes, daemon restart, or manual `.git`/Paseo registry edits.

## C1 Completion Checklist

- [ ] C0 receipt and approved exception re-read; protected GA unchanged.
- [ ] Owner-approved successor Beta1 resolves the hosted-CI archive blocker; every frozen SHA and path assertion in this plan is updated and re-reviewed. Current `e081e390` does not satisfy this item.
- [ ] `ddc1785` ancestry, every post-boundary commit, exact merge-base, six-path history fence, and 16-path aggregate fence pass.
- [ ] Focused package, full pytest, quick, sync, and diff checks pass on exact Beta1 SHA.
- [ ] Five read-only Luna Max reviews end with exact `Verdict: PASS`.
- [ ] SHA-bound PR approval is recorded; one exact PR targets `main` from `codex/gwo-v8-beta1`.
- [ ] Exact-head checks pass; normal two-parent merge and exact merged-main `GWO CI` read back.
- [ ] Tracker-specific approval is recorded; #137 semantics are preserved and milestones are assigned without overwriting conflicts.
- [ ] Publication approval is bound to the merge SHA.
- [ ] `v8.0.0-beta.1` is annotated, immutable, and peels to merged main.
- [ ] GitHub Release is non-draft, prerelease, exact-target, and body-identical to merged-SHA notes.
- [ ] Canonical main is fast-forwarded, all three retained worktrees are clean, and protected GA remains unchanged.
- [ ] C1 closure report preserves the no-production-admission and no-default-writer boundary.
