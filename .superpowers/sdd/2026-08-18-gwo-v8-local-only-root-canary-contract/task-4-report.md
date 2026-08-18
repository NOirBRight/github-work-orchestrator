# Task 4 report — repository release contract alignment

## TDD record

Focused RED runs were observed before the corresponding implementation:

- `py -3.13 -m pytest tests/test_v8_release_metadata.py -k canonical_local_verification_accepts_local_only_v1_mode -q` — failed with `GA_LOCAL_VERIFICATION_MODE_INVALID`.
- `py -3.13 -m pytest tests/test_v8_release_metadata.py -k canonical_local_verification_requires_zero_workflows -q` — failed because missing `workflow_count` was accepted.
- `py -3.13 -m pytest tests/test_v8_release_metadata.py -k canonical_local_verification_requires_actions_disabled_readback -q` — failed because missing disabled-Actions readback was accepted.
- `py -3.13 -m pytest tests/test_v8_release_metadata.py -k canonical_local_verification_requires_full_pytest_readback -q` — failed because a bare pytest count was accepted without a full-suite readback.
- `py -3.13 -m pytest tests/test_v8_release_metadata.py -k canonical_local_verification_rejects_nested_pull_request_evidence -q` — failed because nested pull-request evidence was accepted.
- `py -3.13 -m pytest tests/test_v8_release_metadata.py -k renderer_labels_repository_verification_local_only -q` — failed because rendered metadata lacked `local-only-v1` and still claimed exact CI.
- `py -3.13 -m pytest tests/test_v8_release_metadata.py -k release_contract_template_matches_committed_contract -q` — failed because the committed contract differed from `write_release_contract`.

GREEN verification:

- Canonical local verification tests: `5 passed`.
- Canonical receipt and legacy compatibility tests: `3 passed`.
- Contract/template tests: `2 passed`.
- Full release metadata suite: `py -3.13 -m pytest tests/test_v8_release_metadata.py -q --basetemp $env:TEMP\\gwo-task4-pytest-focused-final` — `48 passed`.
- `py -3.13 -m ruff check scripts/verify_v8_ga_release.py scripts/render_v8_ga_metadata.py tests/test_v8_release_metadata.py` — passed.
- `git diff --check` — passed.
- Extended release regression command (`tests/test_v8_release_metadata.py`
  plus `tests/test_orchestrator_package.py`) reached `66 passed`; one
  unrelated pre-existing convergence test failed because the environment did
  not define `GWO_CONVERGENCE_ARCHIVE_ROOT`.

## Changes

- Added strict `local-only-v1` repository manifest semantics: required zero
  workflows, explicit disabled Actions/workflow readback, successful full
  pytest evidence, exact subject SHA/tree, and recursive CI/Hosted/PR field
  rejection.
- Preserved the existing hosted `CiReadback` path and legacy local spelling
  compatibility at the receipt boundary; canonical receipts retain
  `local-only-v1`.
- Updated the metadata renderer, generated GA contract, release train, Root
  Canary runbook, and roadmap to distinguish repository Local Verification Only
  from product Hosted-CI delivery.

## Task 4 files changed

- `scripts/verify_v8_ga_release.py`
- `scripts/render_v8_ga_metadata.py`
- `tests/test_v8_release_metadata.py`
- `docs/releases/gwo-v8-ga-release-contract.md`
- `docs/releases/gwo-v8-release-train.md`
- `docs/e2e/gwo-v8-root-canary.md`
- `docs/design/gwo-v8-lean-roadmap.md`
- `.superpowers/sdd/2026-08-18-gwo-v8-local-only-root-canary-contract/task-4-report.md`
