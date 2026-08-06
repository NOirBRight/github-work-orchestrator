# Task 6 implementation report

## RED

The Strict transport fixture was added before the production change. It makes
the primary Formal Review fail in transport, lets its `review_strong` retry
succeed, then makes the Specialist Review fail in transport.

```powershell
python -m pytest -q tests/test_v8_candidate_strict_review.py
```

Result: exit code `1`; `1 failed, 4 passed`. The regression observed the
per-action retry bug: actions included a second `review_strong` instead of
failing closed after `formal_review`, `review_strong`, and
`specialist_review`.

## GREEN

The focused Strict suite passes after the minimal CandidateGate change:

```powershell
python -m pytest -q tests/test_v8_candidate_strict_review.py
```

Result: `5 passed`.

The scoped assurance, CandidateGate, public, acceptance, review-reuse, and
Candidate receipt Kernel suites pass:

```powershell
python -m pytest -q tests/test_v8_candidate_strict_review.py tests/test_v8_candidate_assurance_standard.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py tests/test_v8_candidate_gate_acceptance.py tests/test_v8_candidate_review_reuse.py tests/test_v8_candidate_receipt_kernel.py
```

Result: `94 passed, 1 warning`. The warning is the existing pytest plugin
rewrite warning for the already-imported support module.

The package and diff checks pass after synchronizing the generated manifest:

```powershell
python scripts/sync_orchestrator.py
python scripts/quick_validate.py
python scripts/sync_orchestrator.py --check
git diff --check
```

`quick validation passed`; sync check and diff check exited successfully.

## Changes

- Added a CandidateGate-owned Subject-keyed consumed retry set.
- Consumed the retry budget before constructing `review_strong`.
- Converted a second transport failure for the same `ReviewSubject.digest` to
  typed `CandidateGateError` with code
  `CANDIDATE_GATE_REVIEW_TRANSPORT_RETRY_EXHAUSTED`, without another reviewer
  call.
- Kept valid complete review results as the only entries in the existing
  review cache.
- Updated the generated orchestrator manifest.

Task 7 was not started.
