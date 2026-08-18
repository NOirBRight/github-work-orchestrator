# Task 4 review — repository release contract alignment

Reviewed commit `101b66b8bc88d2ede136566884b3df8151f56957` against parent
`109640f9565d6f07d4fb4f40595edde6983afde6`, using the approved design and
plan. The review is limited to Task 4. Existing unrelated working-tree edits
in `scripts/run_v8_local_acceptance.py` and
`tests/test_v8_local_acceptance.py` were left untouched.

## Verdict

- **SPEC: FAIL** — the canonical local-only gate can accept partial pytest
  evidence and does not reject all explicitly forbidden Hosted/CI/PR field
  forms.
- **QUALITY/SECURITY: FAIL** — both gaps are fail-open acceptance-boundary
  issues; the focused tests do not cover them.

## Findings

### P1 — canonical mode does not require a full pytest command

**File:** `scripts/verify_v8_ga_release.py:702-724`

`_canonical_full_pytest_count()` treats any command whose name is `pytest` or
whose text contains `pytest` as the full suite. It does not require the command
to be the `full`/`full-suite` command or reject selectors such as `-k`. A
canonical manifest containing only `py -3.13 -m pytest -k one -q` with exit code
0 and `1 passed` is accepted as `local-only-v1`; a `package` pytest command can
also satisfy the check when the required `full` command is absent. This
violates the approved requirement for a successful **full** pytest readback.

### P1 — recursive forbidden-field rejection is incomplete

**Files:** `scripts/verify_v8_ga_release.py:75-110,454-462`

The recursive walk is present, but it compares normalized keys against a
closed set of exact names. It therefore accepts forbidden fields such as
`hosted_ci_suite`, `ci_run`, `pull_request_merge_mapping`,
`publication_receipt_digest`, and `remote_target_sha` when nested in the local
manifest. These are Hosted/CI/PR or remote-publication identity forms expressly
excluded by the approved design/plan. A canonical manifest with each of those
fields plus otherwise valid local evidence was accepted in direct probes.

### P2 — regression tests do not exercise the fail-open cases

**File:** `tests/test_v8_release_metadata.py:706-724,781-805`

The new tests cover one valid `name=full` command, the absence of all command
evidence, and one exact nested `pull_request` key. They do not cover a focused
pytest selector, a `package` command standing in for `full`, or normalized/
extended Hosted/CI/PR/publication aliases. Consequently the reported GREEN
suite does not protect the two P1 requirements above. The TDD report documents
RED/GREEN runs for the named cases, but not these necessary boundary cases.

## Verified strengths

- `local-only-v1` is accepted and preserved in a canonical receipt.
- Canonical mode requires `workflow_count == 0`, an explicit false Actions
  readback, and subject SHA/tree readback; the existing legacy spelling path
  remains compatible.
- The renderer emits `local-only-v1`, removes the old “exact CI” claim, and
  states that product Hosted-CI is separate. The committed release contract
  matches `write_release_contract`.
- The CLI rejects `--ci-run` on the local pre-tag path and the receipt omits
  CI fields for local verification.

## Commands and results

- `git diff 101b66b^ 101b66b` — inspected the Task 4-only diff; the commit
  contains the planned release verifier, renderer, metadata tests, four docs,
  and Task 4 report.
- `py -3.13 -m pytest tests/test_v8_release_metadata.py -k "canonical_local_verification" -q` — **7 passed, 41 deselected**.
- `py -3.13 -m pytest tests/test_v8_release_metadata.py -q` — **48 passed**.
- `py -3.13 -m ruff check scripts/verify_v8_ga_release.py scripts/render_v8_ga_metadata.py tests/test_v8_release_metadata.py` — **passed**.
- `git diff --check` and `git show --check --oneline 101b66b` — **passed**.
- `py -3.13 -m pytest tests/test_v8_release_metadata.py tests/test_orchestrator_package.py -q` — **66 passed, 1 failed**; the failure is the unrelated pre-existing `GWO_CONVERGENCE_ARCHIVE_ROOT` requirement in `tests/test_orchestrator_package.py::test_beta1_requires_structured_workspace_convergence_receipt`.
- Adversarial direct probes against the committed verifier — **confirmed** a
  focused-only pytest manifest and the listed extended forbidden keys are
  accepted, supporting the P1 findings.
