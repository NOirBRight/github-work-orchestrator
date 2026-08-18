# Task 3 report — local-only root verifier

## TDD evidence

- RED: `py -3.13 -m pytest tests/test_v8_root_canary_acceptance.py -k "local_only or local_batch" -q` produced `18 failed, 31 deselected`; the new local contract tests correctly failed because the verifier had no explicit local-only dispatch.
- Focused GREEN: the same command produced `18 passed, 31 deselected`.
- Verifier suite: `py -3.13 -m pytest tests/test_v8_root_canary_acceptance.py -q` was independently confirmed at `49 passed` by the controller; the combined run below also covered the full verifier suite.
- Local integration suite: `py -3.13 -m pytest tests/test_v8_root_canary_acceptance.py tests/test_v8_local_acceptance.py -q` produced `75 passed`.
- Static checks: `py -3.13 -m ruff check scripts/verify_v8_root_canary.py tests/test_v8_root_canary_acceptance.py` and `py -3.13 -m compileall -q scripts tests` passed.
- CLI round-trip: a Task 2 root record generated with `run_v8_local_acceptance.py --scenario root --tickets tests/fixtures/gwo-v8-root-canary-tickets-195-198.json` was verified by `verify_v8_root_canary.py`; the receipt reported `acceptance_mode: local-only-v1`, schema `gwo-v8-root-canary-acceptance.v2`, and Batch boundaries `[issue:195, issue:196, issue:197]` and `[issue:198]`.

## Implementation

- Added explicit `local-only-v1` / `gwo.v8.local-root-acceptance.v1` dispatch with fail-closed mode/schema handling; absent mode continues to use the legacy Hosted fixture path.
- Validated the manifest-backed local suite, exact standard/singleton Batch boundaries, Batch ref/SHA/tree and receipt links, serialized integration Lease, target before/after/CAS/ancestry/readback chain, candidate/review/result cross-links, and local receipt digests.
- Added recursive normalized rejection of Hosted/CI/PR/publication/remote-target/URL/run/check aliases, including nested local evidence.
- Bound local mode and complete authoritative local evidence into the immutable receipt digest and emitted local receipt/document schema v2 without changing legacy hosted wording.

## Task 2 integration assumption

Task 2 was available as committed output at `6efa8c9` (`feat: emit manifest-backed local root acceptance evidence`) and its report was present. The verifier consumes that producer shape directly; no Task 2 files were modified.

## Remaining integration concerns

No functional blocker remains. Producer-backed tests are intentionally slow because they execute the deterministic local acceptance harness; on this Windows environment pytest also prints a non-fatal temporary-directory cleanup `PermissionError` after successful runs.

## Files changed

- `scripts/verify_v8_root_canary.py`
- `tests/test_v8_root_canary_acceptance.py`
- `.superpowers/sdd/2026-08-18-gwo-v8-local-only-root-canary-contract/task-3-report.md`

