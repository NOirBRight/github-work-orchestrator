# Task 3 fix report — local-only root verifier

## TDD evidence

- RED: in a temporary worktree at `HEAD`, the new focused regression produced
  `1 failed, 49 deselected`. The failure identified the four leaked paths:
  `standard_batch.hosted_run_id`, `standard_batch.pull_request_number`,
  `strict_batch.hosted_run_id`, and `strict_batch.pull_request_number`.
- GREEN: `py -3.13 -m pytest tests/test_v8_root_canary_acceptance.py -k
  "local_only_v2_receipt_has_no_hosted_or_pr_batch_placeholders" -q`
  produced `1 passed, 49 deselected`.

## Fix

- Added one shared batch serializer for the local v2 document and CLI receipt.
- Local serialization removes only `pull_request_number` and `hosted_run_id`;
  hosted serialization continues to return `dataclasses.asdict(batch)`.

## Verification

- Ruff passed for both changed Python files.
- `git diff --check` passed.
- The full verifier rerun was started but interrupted at the user's request;
  the local acceptance suite was not rerun.

## Files changed

- `scripts/verify_v8_root_canary.py`
- `tests/test_v8_root_canary_acceptance.py`
- `.superpowers/sdd/2026-08-18-gwo-v8-local-only-root-canary-contract/task-3-fix-report.md`
