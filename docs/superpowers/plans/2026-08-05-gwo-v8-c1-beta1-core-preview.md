# GWO V8 C1 Beta1 Core Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Publish the C0-validated GWO V8 Core Preview as immutable v8.0.0-beta.1 without merging the protected GA implementation wholesale or activating the V8 writer.

**Architecture:** C1 is a metadata and release-control campaign, not a production implementation campaign. It consumes codex/gwo-v8-beta1, opens one PR against main, verifies exact PR/main CI and tracker readbacks, and then creates the immutable Beta1 tag and prerelease after the explicit owner gate. The protected codex/gwo-v8-ga-plan ref remains an immutable source boundary for later stacked PR construction.

**Tech Stack:** Git 2.x, GitHub CLI, GitHub Actions workflow GWO CI, Python 3.13, pytest, PowerShell 7, SHA-256, canonical JSON, GitHub Issues/PRs/milestones/releases.

## Global Constraints

- C0 is already PASS under the approved verification-subject exception; do not rerun cleanup, delete test roots, delete worktrees, or mutate the protected GA ref as part of C1.
- The protected GA ref remains exactly refs/heads/codex/gwo-v8-ga-plan -> 2cd6c46e1484ca140c3a197bbdeb171191d70c20; never force-push or advance it.
- C1 source is codex/gwo-v8-beta1@e081e39054b7f9f0a49824eed8354a8a33378ea3; do not replace it with codex/gwo-v8-ga-plan.
- The Beta1 slice contains release metadata and validator/package-contract coverage only; it does not add production admission or change the default writer.
- Never commit or push directly to main; merge through a PR with a normal merge commit. Never use --no-verify, git clean, wildcard deletion, or force-push.
- Every remote mutation requires the named owner approval/readback gate in Task 3 before the first remote mutation. Plan text and caller identity are not approval evidence.
- Use at most five parallel read-only Luna Max reviewers. The C1 release mutation path remains serial.
- Existing tags or Releases are verified in place and are never moved, deleted, recreated, or replaced.
- C1 does not close #113–#119. It records their canonical state and hands the release train to C2.

---

## C0 Closure Record

C0 is closed and must not be reopened:

- Verdict: PASS — approved verification-subject exception.
- C0 receipt: docs/releases/gwo-v8-workspace-convergence.md.
- C0 independent report: .superpowers/sdd/2026-08-04-gwo-v8-workspace-convergence-gate/task-8-report.md.
- Implementation boundary e58c596998df90e65349bdb4b5f25d3d9dc1f7e2 is an ancestor of protected GA 2cd6c46e1484ca140c3a197bbdeb171191d70c20.
- Archive manifest SHA-256: e6939fbd27eedca2198b87f17de0d14bd3e367a65a37fc51542aa87ade889409.
- Pre-clean bundle SHA-256: 5eb64cffaed0ac2fd2748a575cb9cd041b2f7463d4d46d7dbfabf9dbdc0e8530.
- Post-clean bundle SHA-256: 9c91a126003e867a3c5736a4e4a69f5c3c079ce1adf5667c1108351181ac4f40.
- The sole protected-GA failure is the known fenced-PowerShell validator false positive; Beta1 package tests, quick validation, sync check, and diff check are the approved C1 subject.

C1 never changes these values. Any mismatch stops C1.

## C1 Identity and Scope

| Surface | Required identity |
| --- | --- |
| C1 base | main@a48c7d6142ae3538725cb876a8782f4ca804cd22 |
| C1 source | codex/gwo-v8-beta1@e081e39054b7f9f0a49824eed8354a8a33378ea3 |
| Protected full implementation | codex/gwo-v8-ga-plan@2cd6c46e1484ca140c3a197bbdeb171191d70c20 |
| Implementation ancestor for C2 | e58c596998df90e65349bdb4b5f25d3d9dc1f7e2 |
| Baseline GWO CI | run 30778312688, 1521 passed in 704.60s, for a48c7d6 |
| Release object | annotated tag and prerelease v8.0.0-beta.1 |

The exact Beta1 PR diff is limited to these paths:

- .superpowers/sdd/2026-08-03-gwo-v8-ga-delivery-program/task-1-report.md
- docs/design/gwo-v8-lean-roadmap.md
- docs/releases/gwo-v8-release-train.md
- docs/releases/gwo-v8-workspace-convergence.md
- docs/releases/v8.0.0-beta.1.md
- docs/superpowers/plans/2026-08-03-gwo-v8-batch-delivery.md
- docs/superpowers/plans/2026-08-03-gwo-v8-campaign-watchdog.md
- docs/superpowers/plans/2026-08-03-gwo-v8-candidate-assurance.md
- docs/superpowers/plans/2026-08-03-gwo-v8-cutover-guard.md
- docs/superpowers/plans/2026-08-03-gwo-v8-ga-delivery-program.md
- docs/superpowers/plans/2026-08-03-gwo-v8-production-composition.md
- docs/superpowers/plans/2026-08-03-gwo-v8-root-canary-ga.md
- docs/superpowers/plans/2026-08-04-gwo-v8-ga-release-program.md
- docs/superpowers/plans/2026-08-04-gwo-v8-workspace-convergence-gate.md
- scripts/quick_validate.py
- tests/test_orchestrator_package.py

Any production module, package manifest, generated export, migration, CI workflow, or unrelated path blocks C1.

## Safe Parallel Schedule

After Task 0, run these five read-only lanes concurrently:

1. Scope: exact path set and no production implementation.
2. Contract: five-key Beta1 JSON, C0 receipt, release train, package tests.
3. Git: ancestry, clean worktrees, remote refs, protected GA immutability.
4. Tracker: complete #113–#119 and #137 bodies/comments/native blockers.
5. Release: baseline CI, package/quick/sync evidence, and tag/Release preflight.

No reviewer changes source, branches, Issues, milestones, tags, or Releases. Tasks 3–6 are serial.

---

### Task 0: Freeze C0 and C1 identities

**Files:**
- Read from codex/gwo-v8-beta1: docs/releases/gwo-v8-workspace-convergence.md
- Read: D:/Workstation/gwo-worktrees/issue-136/.superpowers/sdd/2026-08-04-gwo-v8-workspace-convergence-gate/task-8-report.md
- Read from codex/gwo-v8-beta1: docs/releases/gwo-v8-release-train.md
- Create: .superpowers/sdd/2026-08-05-gwo-v8-c1-beta1-core-preview/task-0-report.md

**Interfaces:**
- Consumes: C0 receipt/report, local refs, remote refs, and C1 source.
- Produces: read-only identity evidence proving C0 is closed and the Beta1 subject is unchanged.

- [ ] Step 1: Verify exactly the canonical main and active GA worktrees.

~~~powershell
$expected = @('D:/Workstation/github-work-orchestrator','D:/Workstation/gwo-worktrees/issue-136','D:/Workstation/gwo-worktrees/c1-beta1-plan')
$rows = @(git worktree list --porcelain | Select-String '^worktree ' | ForEach-Object { $_.Line.Substring(9) })
if (($rows | Where-Object { $_ -notin $expected }).Count -ne 0 -or
    ($expected | Where-Object { $_ -notin $rows }).Count -ne 0 -or $rows.Count -ne 3) {
    throw 'C1 requires exactly the two C0 worktrees.'
}
git -C D:/Workstation/github-work-orchestrator status --short --branch
git -C D:/Workstation/gwo-worktrees/issue-136 status --short --branch
~~~

Expected: the two C0-owned worktrees and the current C1 plan worktree are clean; main is a48c7d6 and active GA is 2cd6c46.

- [ ] Step 2: Verify the three remote identities without moving refs.

~~~powershell
git fetch origin main codex/gwo-v8-beta1 --tags
$main = git rev-parse refs/remotes/origin/main
$beta1 = git rev-parse refs/remotes/origin/codex/gwo-v8-beta1
$ga = git rev-parse refs/remotes/origin/codex/gwo-v8-ga-plan
if ($main -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22') { throw 'C0 main boundary moved.' }
if ($beta1 -ne 'e081e39054b7f9f0a49824eed8354a8a33378ea3') { throw 'Beta1 source moved.' }
if ($ga -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20') { throw 'Protected GA ref moved.' }
~~~

Expected: all exact SHA checks pass.

- [ ] Step 3: Verify the C0 receipt and report.

~~~powershell
$receiptText = git show codex/gwo-v8-beta1:docs/releases/gwo-v8-workspace-convergence.md
$fence = ([string][char]96) * 3
$match = [regex]::Match(($receiptText -join [Environment]::NewLine), "(?s)$fence" + "json\s*(\{.*?\})\s*$fence")
if (-not $match.Success) { throw 'C0 receipt JSON block is missing.' }
$receipt = $match.Groups[1].Value | ConvertFrom-Json
if ($receipt.schema -ne 'gwo-workspace-convergence.v1' -or
    $receipt.protected_remote_sha -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or
    $receipt.removed_worktree_count -ne 36 -or
    $receipt.removed_test_root_count -ne 48 -or
    $receipt.archive_manifest_sha256 -ne 'e6939fbd27eedca2198b87f17de0d14bd3e367a65a37fc51542aa87ade889409') {
    throw 'C0 receipt drifted.'
}
Select-String -Path D:/Workstation/gwo-worktrees/issue-136/.superpowers/sdd/2026-08-04-gwo-v8-workspace-convergence-gate/task-8-report.md -Pattern 'Phase 1 is \*\*PASS\*\*'
~~~

Expected: receipt and report prove the approved C0 PASS; do not traverse or delete retained-protected roots.

- [ ] Step 4: Read existing Beta1 tag/Release without creating either.

~~~powershell
git ls-remote --tags origin refs/tags/v8.0.0-beta.1 'refs/tags/v8.0.0-beta.1^{}'
gh release view v8.0.0-beta.1 --repo NOirBRight/github-work-orchestrator --json tagName,targetCommitish,isPrerelease,url 2>$null
if ($LASTEXITCODE -eq 0) { Write-Warning 'Beta1 Release already exists; verify it and never recreate it.' }
~~~

Expected at planning time: no tag/Release. An existing object is a readback subject, never a creation target.

- [ ] Step 5: Record exact outputs in the Task 0 report.

The ignored report records refs, worktree status, C0 receipt values, and tag/Release readback. It is operational evidence, not release authority.

---

### Task 1: Audit the Beta1 slice and run its focused gate

**Files:**
- Read: the 16 paths listed in C1 Identity and Scope
- Test: tests/test_orchestrator_package.py
- Verify: scripts/quick_validate.py and scripts/sync_orchestrator.py
- Create: .superpowers/sdd/2026-08-05-gwo-v8-c1-beta1-core-preview/task-1-report.md

**Interfaces:**
- Consumes: main@a48c7d6 and codex/gwo-v8-beta1@e081e390.
- Produces: an exact-path candidate with no production implementation change.

- [ ] Step 1: Compare the frozen base and source.

~~~powershell
$base = 'a48c7d6142ae3538725cb876a8782f4ca804cd22'
$candidate = 'e081e39054b7f9f0a49824eed8354a8a33378ea3'
git diff --stat "$base...$candidate"
git diff --name-status "$base...$candidate"
~~~

Expected: only the 16 approved paths appear; no production module or package manifest appears.

- [ ] Step 2: Enforce the allowlist.

~~~powershell
$expected = @(
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
$actual = @(git diff --name-only "$base...$candidate" | Sort-Object)
$unexpected = Compare-Object $expected $actual
if ($unexpected) { $unexpected; throw 'Beta1 path allowlist failed.' }
~~~

Expected: no Compare-Object output and no exception.

- [ ] Step 3: Verify the source in a temporary clean worktree.

~~~powershell
$verify = 'D:/Workstation/gwo-worktrees/c1-beta1-verify'
if (Test-Path -LiteralPath $verify) { throw "Verification worktree already exists: $verify" }
git worktree add --detach $verify codex/gwo-v8-beta1
try {
    py -3.13 -m pytest tests/test_orchestrator_package.py -q
    if ($LASTEXITCODE -ne 0) { throw 'Beta1 package tests failed.' }
    py -3.13 scripts/quick_validate.py
    if ($LASTEXITCODE -ne 0) { throw 'Beta1 quick validation failed.' }
    py -3.13 scripts/sync_orchestrator.py --check
    if ($LASTEXITCODE -ne 0) { throw 'Beta1 package synchronization failed.' }
    git diff --check "$base...HEAD"
    if ($LASTEXITCODE -ne 0) { throw 'Beta1 diff check failed.' }
} finally {
    Set-Location D:/Workstation/github-work-orchestrator
    git worktree remove --force $verify
    git worktree prune
}
~~~

Expected: 17 passed, quick validation passed, synchronized package output, and zero diff-check errors.

- [ ] Step 4: Record exact candidate/base SHAs, paths, test count, and command output. Do not create an empty commit when the source branch is already clean at the approved SHA.

---

### Task 2: Run five independent read-only reviews in parallel

**Files:**
- Read: Task 0/1 evidence, exact Beta1 diff, release docs, GitHub readbacks
- Create: five ignored reports under .superpowers/sdd/2026-08-05-gwo-v8-c1-beta1-core-preview/

**Interfaces:**
- Consumes: frozen C1 subject.
- Produces: five PASS reports; any HOLD blocks remote mutation.

- [ ] Step 1: Dispatch at most five gpt-5.6-luna reviewers with max reasoning:
  - Scope: path fence, no production files, no manifest drift, no GA mutation.
  - Contract: five-key Beta1 JSON, C0 receipt, release train, package tests.
  - Git: ancestry, clean worktrees, remote refs, tag immutability.
  - Tracker: full #113–#119 and #137 body/comments/native blockers, read-only.
  - Release: baseline GWO CI 30778312688 and publication command safety.

- [ ] Step 2: Reconcile all reports.

~~~powershell
$reports = Get-ChildItem .superpowers/sdd/2026-08-05-gwo-v8-c1-beta1-core-preview/task-2-review-*.md
if ($reports.Count -ne 5) { throw 'C1 requires five independent review reports.' }
$holds = Select-String -Path $reports.FullName -Pattern '\b(HOLD|FAIL|BLOCKED)\b'
if ($holds) { $holds; throw 'Resolve every review hold before PR mutation.' }
~~~

Expected: five PASS reports and no unresolved HOLD, FAIL, or BLOCKED result.

---

### Task 3: Prepare and submit the Beta1 PR

**Files:**
- Read: docs/releases/gwo-v8-release-train.md and docs/releases/v8.0.0-beta.1.md
- Remote artifact: one PR from codex/gwo-v8-beta1 to main
- Create: .superpowers/sdd/2026-08-05-gwo-v8-c1-beta1-core-preview/task-3-pr.md

**Interfaces:**
- Consumes: five PASS reviews and exact Beta1 source.
- Produces: one PR with base main, head codex/gwo-v8-beta1, and the 16-path allowlist.

- [ ] Step 1: Recheck all identities immediately before mutation.

~~~powershell
git fetch origin main codex/gwo-v8-beta1 --tags
if ((git rev-parse refs/remotes/origin/main) -ne 'a48c7d6142ae3538725cb876a8782f4ca804cd22') { throw 'main moved; stop.' }
if ((git rev-parse refs/remotes/origin/codex/gwo-v8-beta1) -ne 'e081e39054b7f9f0a49824eed8354a8a33378ea3') { throw 'Beta1 moved; stop.' }
if ((git rev-parse refs/remotes/origin/codex/gwo-v8-ga-plan) -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20') { throw 'GA ref moved; stop.' }
~~~

- [ ] Step 2: Require explicit owner approval for remote mutation.

Record approver identity, exact approval text, and timestamp. Without it, stop after Tasks 0–2; do not create or merge a PR, mutate Issues, create milestones, push a tag, or publish a Release.

- [ ] Step 3: Push the reviewed branch without force.

~~~powershell
git push origin refs/heads/codex/gwo-v8-beta1:refs/heads/codex/gwo-v8-beta1
~~~

Expected: no force option and remote head remains e081e390.

- [ ] Step 4: Create one Draft PR.

~~~powershell
$body = @(
'## Summary',
'- publish the C0-validated GWO V8 Core Preview metadata',
'- bind the workspace-convergence receipt and Beta1 baseline evidence',
'- preserve the no-production-admission and no-writer-activation boundary',
'',
'## Validation',
'- py -3.13 -m pytest tests/test_orchestrator_package.py -q — 17 passed',
'- py -3.13 scripts/quick_validate.py — passed',
'- py -3.13 scripts/sync_orchestrator.py --check — passed',
'- git diff --check — passed',
'',
'## Non-goals',
'- no production admission',
'- no V8 writer activation',
'- no #113–#119 closure'
) -join ([Environment]::NewLine)
gh pr create --repo NOirBRight/github-work-orchestrator --base main --head codex/gwo-v8-beta1 --draft --title 'V8 Beta1: publish the C0-validated Core Preview' --body $body
~~~

Expected: one Draft PR URL. If GitHub reports an existing PR, inspect it and do not create a second one.

- [ ] Step 5: Verify PR identity and paths.

~~~powershell
$pr = gh pr list --repo NOirBRight/github-work-orchestrator --head codex/gwo-v8-beta1 --base main --state open --json number,url,headRefName,baseRefName,headRefOid,isDraft | ConvertFrom-Json
if (@($pr).Count -ne 1 -or $pr.headRefName -ne 'codex/gwo-v8-beta1' -or
    $pr.baseRefName -ne 'main' -or $pr.headRefOid -ne 'e081e39054b7f9f0a49824eed8354a8a33378ea3') {
    throw 'Beta1 PR identity is wrong.'
}
gh pr diff $pr.number --repo NOirBRight/github-work-orchestrator --name-only
~~~

Expected: exactly one PR and exactly the 16 approved paths.

---

### Task 4: Pass checks, merge normally, and verify merged main

**Files:**
- Read: PR checks and reviews
- Remote artifact: normal merge commit on main
- Create: .superpowers/sdd/2026-08-05-gwo-v8-c1-beta1-core-preview/task-4-merge.md

**Interfaces:**
- Consumes: exact-head Beta1 PR and five PASS reviews.
- Produces: exact merged-main SHA with successful post-merge GWO CI.

- [ ] Step 1: Mark ready and wait for exact-head checks.

~~~powershell
gh pr ready $pr.number --repo NOirBRight/github-work-orchestrator
gh pr checks $pr.number --repo NOirBRight/github-work-orchestrator --watch
gh pr view $pr.number --repo NOirBRight/github-work-orchestrator --json headRefOid,statusCheckRollup,reviews
~~~

Expected: head remains e081e390 and every required check/review is successful. Any failure stops the merge.

- [ ] Step 2: Merge with a normal merge commit and retain the branch.

~~~powershell
gh pr merge $pr.number --repo NOirBRight/github-work-orchestrator --merge --delete-branch=false
~~~

Expected: merged PR, no squash/rebase, no branch deletion.

- [ ] Step 3: Read back merged main and exact GWO CI.

~~~powershell
git fetch origin main --tags
$merged = git rev-parse refs/remotes/origin/main
$run = gh run list --repo NOirBRight/github-work-orchestrator --commit $merged --workflow 'GWO CI' --status success --limit 1 --json databaseId,url,headSha,conclusion | ConvertFrom-Json
if ($run.Count -ne 1 -or $run.headSha -ne $merged -or $run.conclusion -ne 'success') { throw 'Exact merged-main GWO CI is not green.' }
$summary = @(gh run view $run.databaseId --repo NOirBRight/github-work-orchestrator --log | Select-String -Pattern '[0-9]+ passed')
if ($summary.Count -eq 0) { throw 'Exact merged-main CI has no dynamic pytest summary.' }
[ordered]@{ merged_main_sha = $merged; ci_url = $run.url; dynamic_pass_summary = $summary[-1].Line.Trim() } | ConvertTo-Json -Compress
~~~

Expected: run.headSha equals origin/main and the pass count is read from the run log.

- [ ] Step 4: Run the merged package gate in a temporary checkout.

~~~powershell
$verify = 'D:/Workstation/gwo-worktrees/c1-beta1-merged-verify'
if (Test-Path -LiteralPath $verify) { throw "Verification worktree already exists: $verify" }
git worktree add --detach $verify $merged
try {
    py -3.13 -m pytest tests/test_orchestrator_package.py -q
    if ($LASTEXITCODE -ne 0) { throw 'Merged Beta1 package tests failed.' }
    py -3.13 scripts/quick_validate.py
    if ($LASTEXITCODE -ne 0) { throw 'Merged Beta1 quick validation failed.' }
    py -3.13 scripts/sync_orchestrator.py --check
    if ($LASTEXITCODE -ne 0) { throw 'Merged Beta1 sync check failed.' }
} finally {
    Set-Location D:/Workstation/github-work-orchestrator
    git worktree remove --force $verify
    git worktree prune
}
~~~

Expected: the exact merged SHA passes the package/quick/sync gate without altering canonical main.

---

### Task 5: Execute the approved tracker and milestone checkpoint

**Files:**
- Read: complete #113–#119 and #137 body/comments/native blockers
- Remote artifacts: #137 state, three milestones, issue milestone assignments
- Create: .superpowers/sdd/2026-08-05-gwo-v8-c1-beta1-core-preview/task-5-tracker.md

**Interfaces:**
- Consumes: exact merged-main evidence and owner approval.
- Produces: preserved #137 semantics and idempotent Beta2/Beta3/GA milestone assignments.

- [ ] Step 1: Capture #137 without mutating it.

~~~powershell
$repo = 'NOirBRight/github-work-orchestrator'
$issue137 = gh issue view 137 --repo $repo --json number,state,body,comments | ConvertFrom-Json
$blockers = gh api graphql -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){issue(number:$number){blockedBy(first:20){nodes{number state}}}}}' -F owner=NOirBRight -F name=github-work-orchestrator -F number=137 | ConvertFrom-Json
[ordered]@{ issue = $issue137; blocked_by = $blockers.data.repository.issue.blockedBy.nodes } | ConvertTo-Json -Depth 10
~~~

Expected: complete body/comments and native blockers are saved. If #137 is CLOSED while a native blocker is OPEN, no mutation occurs before Step 2.

- [ ] Step 2: Require the named approval.

~~~powershell
$approval = 'REOPEN-137-PRESERVE-NATIVE-BLOCKERS'
if (-not $env:GWO_V8_TRACKER_APPROVER -or $env:GWO_V8_TRACKER_APPROVAL -ne $approval) {
    throw 'STOP: explicit owner approval is absent; no Issue, milestone, tag, or Release mutation is allowed.'
}
~~~

Expected before approval: intentional stop. After approval record approver, exact string, timestamp, and pre-mutation JSON.

- [ ] Step 3: Reopen #137 only for the approved anomaly and verify preservation.

~~~powershell
if ($issue137.state -eq 'CLOSED' -and @($blockers.data.repository.issue.blockedBy.nodes | Where-Object state -eq 'OPEN').Count -gt 0) {
    gh issue reopen 137 --repo $repo
}
$after137 = gh issue view 137 --repo $repo --json number,state,body,comments | ConvertFrom-Json
if ($after137.number -ne 137 -or $after137.state -ne 'OPEN' -or -not $after137.body) { throw '#137 preservation readback failed.' }
~~~

- [ ] Step 4: Create milestones idempotently and verify every assignment.

~~~powershell
$plan = [ordered]@{ 'GWO V8 Beta2' = @(113,114,115,116,117,137); 'GWO V8 Beta3' = @(118); 'GWO V8 GA' = @(119) }
$existing = @(gh api "repos/$repo/milestones?state=all&per_page=100" | ConvertFrom-Json)
foreach ($title in $plan.Keys) {
    $matches = @($existing | Where-Object title -eq $title)
    if ($matches.Count -eq 0) { gh api "repos/$repo/milestones" -f title=$title -f description='See docs/releases/gwo-v8-release-train.md' | Out-Null }
    elseif ($matches.Count -ne 1) { throw "Milestone $title is not unique." }
}
$all = @(gh api "repos/$repo/milestones?state=all&per_page=100" | ConvertFrom-Json)
foreach ($title in $plan.Keys) {
    $milestone = @($all | Where-Object title -eq $title)
    foreach ($number in $plan[$title]) {
        gh api -X PATCH "repos/$repo/issues/$number" -F milestone=$milestone[0].number | Out-Null
        $readback = gh issue view $number --repo $repo --json number,state,milestone | ConvertFrom-Json
        if ($readback.milestone.title -ne $title) { throw "Issue #$number did not read back under $title." }
    }
}
~~~

Expected: #113–#117/#137 under Beta2, #118 under Beta3, #119 under GA; no Issue is closed.

- [ ] Step 5: Record before/after #137, approval, blockers, milestones, and all assignment readbacks.

---

### Task 6: Create and verify immutable Beta1 publication

**Files:**
- Read: docs/releases/v8.0.0-beta.1.md
- Remote artifacts: refs/tags/v8.0.0-beta.1 and GitHub prerelease v8.0.0-beta.1
- Create: .superpowers/sdd/2026-08-05-gwo-v8-c1-beta1-core-preview/task-6-publication.md

**Interfaces:**
- Consumes: merged main SHA, exact successful GWO CI, tracker/milestone readbacks, and owner approval.
- Produces: one immutable annotated Beta1 tag and one prerelease pointing to it.

- [ ] Step 1: Verify tag/Release absence or exact correctness.

~~~powershell
$repo = 'NOirBRight/github-work-orchestrator'
$tag = @(git ls-remote --tags origin refs/tags/v8.0.0-beta.1 'refs/tags/v8.0.0-beta.1^{}')
$release = gh release view v8.0.0-beta.1 --repo $repo --json tagName,targetCommitish,isPrerelease,url 2>$null
$tag
if ($LASTEXITCODE -eq 0) { $release }
~~~

Existing objects are never recreated; they must already target the approved merged main SHA and prerelease state.

- [ ] Step 2: Read exact main and CI before tag creation.

~~~powershell
git fetch origin main --tags
$sha = git rev-parse refs/remotes/origin/main
$run = gh run list --repo $repo --commit $sha --workflow 'GWO CI' --status success --limit 1 --json databaseId,url,headSha,conclusion | ConvertFrom-Json
if ($run.Count -ne 1 -or $run.headSha -ne $sha -or $run.conclusion -ne 'success') { throw 'Beta1 exact-main CI is not green.' }
$summary = @(gh run view $run.databaseId --repo $repo --log | Select-String -Pattern '[0-9]+ passed')
if ($summary.Count -eq 0) { throw 'Beta1 CI has no dynamic pytest summary.' }
~~~

- [ ] Step 3: Create, push, peel-check, and publish once.

~~~powershell
if (@(git ls-remote --tags origin refs/tags/v8.0.0-beta.1)) { throw 'Beta1 tag exists; verify it, never move it.' }
git tag -a v8.0.0-beta.1 $sha -m 'GWO V8 Beta1 - Core Preview'
git push origin refs/tags/v8.0.0-beta.1
$peeled = ((git ls-remote --tags origin 'refs/tags/v8.0.0-beta.1^{}' | Select-Object -First 1) -split '\s+')[0]
if ($peeled -ne $sha) { throw 'Beta1 tag does not peel to merged main.' }
gh release create v8.0.0-beta.1 --repo $repo --verify-tag --prerelease --title 'GWO V8 Beta1 - Core Preview' --notes-file docs/releases/v8.0.0-beta.1.md
gh release view v8.0.0-beta.1 --repo $repo --json tagName,targetCommitish,isPrerelease,url
~~~

Expected: tagName=v8.0.0-beta.1, isPrerelease=true, and tag/Release target the exact merged main SHA. Production admission and writer activation remain disabled.

---

### Task 7: Close C1 and hand off to C2

**Files:**
- Read: Tasks 0–6 evidence and remote readbacks
- Create: .superpowers/sdd/2026-08-05-gwo-v8-c1-beta1-core-preview/task-7-report.md

**Interfaces:**
- Consumes: C1 publication evidence.
- Produces: traceable C1 PASS and exact C2 handoff subject.

- [ ] Step 1: Verify final ref and Release identity.

~~~powershell
git fetch origin main codex/gwo-v8-beta1 codex/gwo-v8-ga-plan --tags
$main = git rev-parse refs/remotes/origin/main
$ga = git rev-parse refs/remotes/origin/codex/gwo-v8-ga-plan
$peeled = ((git ls-remote --tags origin 'refs/tags/v8.0.0-beta.1^{}' | Select-Object -First 1) -split '\s+')[0]
if ($ga -ne '2cd6c46e1484ca140c3a197bbdeb171191d70c20' -or $peeled -ne $main) { throw 'C1 final identity failed.' }
gh release view v8.0.0-beta.1 --repo NOirBRight/github-work-orchestrator --json tagName,targetCommitish,isPrerelease,url,body
~~~

Expected: protected GA is unchanged, the tag peels to main, and the Release is a prerelease.

- [ ] Step 2: Write the closure report with exact PR number, head, merge SHA, CI URL/summary, tracker approval/readbacks, tag SHA, and Release URL. Include this statement:

~~~text
C1 Beta1 Core Preview is published; Lean V8 production admission and default-writer activation remain disabled.
~~~

- [ ] Step 3: Hand off C2 boundaries without merging them:
  - Candidate foundation: 77ac3e3
  - #113 Watchdog: 07086ce
  - #114 CandidateGate: 657bf23
  - #115 Review/Repair: a0f6976
  - #116 Batch WIP: e58c596

- [ ] Step 4: Verify the C0-owned worktrees remain clean and no protected ref moved.

## C1 Stop Rules

Stop immediately if main, Beta1, or protected GA differs from the frozen SHA; the PR contains an unapproved path; package/quick/sync/diff/CI fails; a reviewer reports HOLD/FAIL/BLOCKED; #137 has open native blockers without approval; a milestone/tag/Release exists with a mismatched target; or any readback is incomplete. Never compensate with force-push, direct main push, wildcard deletion, ACL changes, daemon restart, or manual .git/Paseo registry edits.

## C1 Completion Checklist

- [ ] C0 receipt and approved exception re-read; protected GA unchanged.
- [ ] Beta1 exact SHA and 16-path scope independently verified.
- [ ] Focused package/quick/sync/diff checks pass.
- [ ] Five read-only reviews PASS.
- [ ] One PR targets main from codex/gwo-v8-beta1.
- [ ] Normal merge and exact merged-main GWO CI read back.
- [ ] Owner approval and #137/blocker readback recorded.
- [ ] Beta2/Beta3/GA milestones assigned idempotently.
- [ ] v8.0.0-beta.1 tag is annotated, immutable, and peels to merged main.
- [ ] GitHub prerelease is verified as isPrerelease=true.
- [ ] C1 closure report keeps production admission and default-writer activation disabled.
