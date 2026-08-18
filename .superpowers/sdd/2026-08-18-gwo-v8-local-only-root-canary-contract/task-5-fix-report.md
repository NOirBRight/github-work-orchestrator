# Task 5 fix report — release-gate section

## Release-gate fix wave

- **Status:** release parser and local pre-tag canary checks are GREEN.
- **Worktree:** `D:\Workstation\gwo-worktrees\gwo-v8-local-only-contract`
- **Scope:** only the release verifier, metadata renderer, release metadata
  tests, and this release report were included. The dirty root verifier,
  local runner, and their tests were left untouched.
- **Files:** scripts/verify_v8_ga_release.py, scripts/render_v8_ga_metadata.py, tests/test_v8_release_metadata.py, and this report.

The release gate now rejects the remaining pytest early-stop, selection,
configuration, and help aliases, including short and `--option=value`
spellings, while still rejecting conflicting `command`/`arguments`/`argv`
representations. Local pre-tag verification requires the canary payload to
declare exactly `acceptance_mode: local-only-v1` and recursively rejects
Hosted-CI, PR, remote, publication, and workflow fields. Compact forbidden
field aliases are covered and rejected. Existing renderer rejection of
Hosted/PR evidence remains in the release write set.

## TDD evidence

### RED

```text
py -3.13 -m pytest tests/test_v8_release_metadata.py -q
7 failed, 84 passed

py -3.13 -m pytest tests/test_v8_release_metadata.py -k "remaining_pytest_gate_options or extended_forbidden_field_aliases or local_pre_tag" -q
48 failed, 51 passed, 65 deselected

py -3.13 -m pytest tests/test_v8_release_metadata.py -k extended_forbidden_field_aliases -q
11 failed, 20 passed, 133 deselected

py -3.13 -m pytest tests/test_v8_release_metadata.py -k remaining_pytest_gate_options -q
4 failed, 64 passed, 100 deselected
```

The failures were the intended missing mode/recursive canary checks, pytest
spellings, and compact aliases.

### GREEN

```text
py -3.13 -m pytest tests/test_v8_release_metadata.py -k "remaining_pytest_gate_options or extended_forbidden_field_aliases or local_pre_tag or local_verification_subject_tree" -q
100 passed, 64 deselected

py -3.13 -m pytest tests/test_v8_release_metadata.py -k "remaining_pytest_gate_options or extended_forbidden_field_aliases" -q
99 passed, 69 deselected

py -3.13 -m pytest tests/test_v8_release_metadata.py -q
168 passed

py -3.13 -m ruff check scripts/verify_v8_ga_release.py scripts/render_v8_ga_metadata.py tests/test_v8_release_metadata.py
All checks passed

git diff --check
PASS
```

## Deferred findings and concerns

- The approved ledger's general diagnostic JSON canonicalization and exact
  Ticket title/body pinning remain deferred.
- Pytest emits a Windows `PermissionError` from its atexit temporary-directory
  cleanup after successful runs; all listed pytest commands returned exit 0.
- No root verifier/runner changes, production mutation, activation, tag,
  merge, push, or agent dispatch was performed.

## Root producer/verifier fix-wave evidence

The root-side fix wave closed the remaining local-only contract gaps in the
producer and read-only verifier. The producer now emits the authoritative
manifest fields in the record digest, isolates system/global Git configuration
and hooks for the complete root run, binds the exact local suite definition and
receipt, and records lease acquisition/release state. The verifier now checks
the accepted-Candidate digest (not the nested Candidate digest), recomputes the
canonical Batch delivery proof using its `member_ticket_keys` contract, and
rejects incomplete or inconsistent recovery, result, suite, lease, and
manifest-backed evidence.

TDD RED was observed before these corrections:

```text
py -3.13 -B -m pytest tests/test_v8_root_canary_acceptance.py tests/test_v8_local_acceptance.py -q
7 failed, 90 passed in 400.13s
```

The intended failures were the result cross-link, producer record-digest,
recovery/readback, and lease-readback regressions. After the minimum fixes:

```text
py -3.13 -B -m pytest tests/test_v8_root_canary_acceptance.py -k "local_only_verifier_accepts_the_manifest_backed_local_projection or requires_and_recomputes_the_producer_record_digest or requires_complete_public_root_facts or receipt_digest_binds_mode_and_complete_local_evidence or cli_round_trip_uses_the_real_ticket_fixture or v2_receipt_has_no_hosted_or_pr_batch_placeholders" -q
10 passed, 58 deselected

py -3.13 -B -m pytest tests/test_v8_local_acceptance.py -k "exact_suite_receipt_and_lease_release_readbacks" -q
1 passed, 96 deselected

py -3.13 -B -m pytest tests/test_v8_root_canary_tickets.py tests/test_v8_local_acceptance.py tests/test_v8_root_canary_acceptance.py tests/test_v8_release_metadata.py -q
311 passed in 429.30s
```

The changed-file checks are green:

```text
py -3.13 -m ruff check --isolated --no-cache <seven changed Python files>
All checks passed
py -3.13 -B -m compileall -q scripts skills tests
PASS
git diff --check
PASS
```

Two independent root runs used the fixed run id
`task5-root-fixed-20260818` and the real #195–#198 manifest. Both producer
records were byte-identical (canonical SHA-256
`ad8c480819e3e78ec8292f42d291eea2d4b1786343c3cccfc0b76ff8a4d8fac7`; producer
`record_digest`
`3857cb1f85fa2e341eb2894027f88e34cf29e8d1c2a6f82302cc1c9b0e0550d5`). Both
read-only verifier outputs were byte-identical (canonical SHA-256
`22f8b96fc6e89b7916e898e7e4b77e75d6831040c5f6fd30141d3ad2c9753337`; receipt
digest `81ddafa50bacb6cdf9f42be9420d1142e1065afe14216ef9a09378035d3467df`).

The full local suite was also executed with
`GWO_CONVERGENCE_ARCHIVE_ROOT=D:\\gwo-convergence-archive\\20260804T185544Z`:

```text
py -3.13 -B -m pytest -q
3144 passed, 52 skipped, 1 failed, 3 warnings in 1867.29s
```

The single failure is the pre-existing environment-bound
`tests/test_beta3_live_guard_runner.py::test_reviewed_provenance_hashes_match_current_observer_bytes` check: the current isolated
worktree is not the canonical production checkout path, so the unchanged
Beta3 runner rejects its origin before comparing hashes. No file in that
write set was changed by this fix wave. Pytest also emits the known Windows
temporary-directory cleanup `PermissionError` after completion.

## Bounded GA parser conflict fix

The reviewed release-parser P1 was that canonical local verification collected
`full_suite`, `full_pytest`, and designated full-command counts but returned the
last count, allowing conflicting evidence to pass based on field order. The
minimum fix now rejects any disagreement with the existing
`GA_LOCAL_VERIFICATION_PYTEST_COUNT_MISMATCH` rule and returns the shared count
only when all candidates agree. Hosted-CI parsing and the existing per-result,
summary/log, chunk, and command-representation conflict rules are unchanged.

### TDD evidence

**RED** — before the parser change:

```text
py -3.13 -m pytest tests/test_v8_release_metadata.py -k conflicting_full_pytest_counts -q
1 failed, 168 deselected
```

The regression failed because conflicting counts were accepted instead of
raising `GA_LOCAL_VERIFICATION_PYTEST_COUNT_MISMATCH`.

**GREEN and focused checks**:

```text
py -3.13 -m pytest tests/test_v8_release_metadata.py -k conflicting_full_pytest_counts -q
1 passed, 168 deselected

py -3.13 -m pytest tests/test_v8_release_metadata.py -q
169 passed

py -3.13 -m ruff check --isolated --no-cache scripts/verify_v8_ga_release.py tests/test_v8_release_metadata.py
All checks passed!

git diff --check -- scripts/verify_v8_ga_release.py tests/test_v8_release_metadata.py
PASS
```

Only the release parser, release metadata test, and this report are in the
bounded fix scope. No root verifier/runner files, production state, merge,
push, or tag were changed.

## Task 5 local-only contract classification fix

- **Status:** GREEN.
- **Root causes:** A missing top-level `transcript` was passed directly to the
  transcript shape validator, so absence was classified as
  `LOCAL_TRANSCRIPT_INVALID` instead of incomplete evidence. An accepted
  Review Subject's valid-but-mismatched `parent_digest` was only detected by
  the subject validator, whose broad caller mapping converted the link error
  to `LOCAL_REVIEW_SUBJECT_INVALID`.
- **Fix:** Missing `transcript` now fails as `LOCAL_EVIDENCE_INCOMPLETE` while
  malformed present transcripts remain `LOCAL_TRANSCRIPT_INVALID`. The local
  candidate/review linker now validates the subject parent digest and checks
  its binding before subject-schema validation, returning
  `LOCAL_CANDIDATE_REVIEW_LINK_INVALID` for a valid mismatched parent while
  preserving subject-schema rejection and digest validation.

### TDD evidence

**RED**

```text
py -3.13 -B -m pytest tests/test_v8_root_canary_acceptance.py -k "requires_complete_public_root_facts or binds_candidate_parent_and_review_subject_identity_to_manifest" -q
2 failed, 3 passed, 77 deselected in 22.80s
```

**GREEN**

```text
py -3.13 -B -m pytest tests/test_v8_root_canary_acceptance.py -k "requires_complete_public_root_facts or binds_candidate_parent_and_review_subject_identity_to_manifest" -q
5 passed, 77 deselected in 21.92s

py -3.13 -B -m pytest tests/test_v8_root_canary_acceptance.py -q
82 passed in 22.16s

py -3.13 -m ruff check --isolated --no-cache scripts/verify_v8_root_canary.py
All checks passed!

git diff --check -- scripts/verify_v8_root_canary.py .superpowers/sdd/2026-08-18-gwo-v8-local-only-root-canary-contract/task-5-fix-report.md
PASS
```

### Changed files

- `scripts/verify_v8_root_canary.py`
- `.superpowers/sdd/2026-08-18-gwo-v8-local-only-root-canary-contract/task-5-fix-report.md`

### Concerns

- Pytest still emits the known Windows `PermissionError` from its atexit
  temporary-directory cleanup after successful runs; all listed pytest
  commands returned exit 0.
- No production mutation, activation, merge, push, or tag was performed.

## Post-fix focused verification

The scoped Luna Max implementer was unavailable after the initial dispatch and
was closed; the already-observed RED results were then completed with the
minimum inline changes. The verifier's candidate-diff validation call was
also made explicitly side-effect-only so the changed-file lint gate is clean.

```text
py -3.13 -B -m pytest tests/test_v8_root_canary_acceptance.py -k "requires_complete_public_root_facts or binds_candidate_parent_and_review_subject_identity_to_manifest" -q
5 passed, 77 deselected in 21.71s

py -3.13 -m ruff check --isolated --no-cache scripts/run_v8_local_acceptance.py scripts/verify_v8_root_canary.py tests/test_v8_local_acceptance.py tests/test_v8_root_canary_acceptance.py scripts/verify_v8_ga_release.py scripts/render_v8_ga_metadata.py tests/test_v8_release_metadata.py
All checks passed!

py -3.13 -B -m compileall -q scripts skills tests
PASS

py -3.13 -B -m pytest tests/test_v8_root_canary_tickets.py tests/test_v8_local_acceptance.py tests/test_v8_root_canary_acceptance.py tests/test_v8_release_metadata.py -q
326 passed in 402.27s (0:06:42)

git diff --check
PASS
```

The known Windows pytest temporary-directory cleanup `PermissionError` was
emitted after successful runs; pytest returned exit 0. No production mutation,
activation, merge, push, or tag was performed.

## Authoritative readback fallback closure

An additional TDD regression checked that an explicit local root cannot replace
`facts.readback.candidate_receipts` or
`facts.readback.accepted_candidate_receipts` with `null` and recover through a
top-level compatibility field. The result-integrity reader was also made
readback-only for the same boundary.

**RED**

```text
py -3.13 -B -m pytest tests/test_v8_root_canary_acceptance.py -k does_not_fallback_from_null_authoritative_candidate_readback -q
2 failed, 82 deselected in 21.60s
```

**GREEN**

```text
py -3.13 -B -m pytest tests/test_v8_root_canary_acceptance.py -k does_not_fallback_from_null_authoritative_candidate_readback -q
2 passed, 82 deselected in 22.44s
```

The focused regression and the existing verifier suite continue to emit only
the known Windows temporary-directory cleanup warning after successful exit.

Final post-fallback focused gate:

```text
py -3.13 -B -m pytest tests/test_v8_root_canary_tickets.py tests/test_v8_local_acceptance.py tests/test_v8_root_canary_acceptance.py tests/test_v8_release_metadata.py -q
328 passed in 398.08s (0:06:38)

py -3.13 -m ruff check --isolated --no-cache scripts/run_v8_local_acceptance.py scripts/verify_v8_root_canary.py tests/test_v8_local_acceptance.py tests/test_v8_root_canary_acceptance.py scripts/verify_v8_ga_release.py scripts/render_v8_ga_metadata.py tests/test_v8_release_metadata.py
All checks passed!

py -3.13 -B -m compileall -q scripts skills tests
PASS

git diff --check
PASS
```

## Fresh deterministic local root evidence

Using the checked-in real manifest
`tests/fixtures/gwo-v8-root-canary-tickets-195-198.json`, two independent
temporary roots under `%TEMP%\\gwo-v8-local-evidence-20260818-final` were run
with the fixed run id `phase5-local-root-final` and verified through the
read-only CLI. The producer records and verifier receipts were byte-identical:

```text
record_a_sha256=2d2cde7a0874c909b2d1ebdd64046bd9e61444499995257f7e5b509bd8372551
record_b_sha256=2d2cde7a0874c909b2d1ebdd64046bd9e61444499995257f7e5b509bd8372551
receipt_a_sha256=67a0d9017a49033af55573af7904130d7fb02048443a65a0f01cbb4e50a90317
receipt_b_sha256=67a0d9017a49033af55573af7904130d7fb02048443a65a0f01cbb4e50a90317
canonical_records_equal=True
gate=LOCAL_ROOT_CANARY_GO
acceptance_mode=local-only-v1
status=Complete
record_digest=824ae8af2b72ba68e80e002c6311c4eca7b9cc0cae2a115249a672c5683f4c7e
receipt_digest=355ee542ff7f44368789f12e539279b12bb721c737240777dbc21c58e4b4433a
```

Recursive scans of the local evidence projection and v2 receipt found zero
forbidden Hosted-CI/PR/publication/remote/workflow/check/URL keys and zero null
placeholders.
