# Task 5 implementation report

## RED

Command:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_gate_acceptance.py -q
```

Result: exit code `1`; `5 failed, 1 passed`. The failures were the intended
regressions for the missing Plan Invalidation readback pair, the
report-only ReviewSubject boundary, the repository/reference readback fence,
and the accepted-Candidate receipt cross-field bindings.

## GREEN

The acceptance regressions pass:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_gate_acceptance.py -q
```

Result: `6 passed`.

The CandidateGate regression suite passes:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_gate.py -q
```

Result: `43 passed`.

The Candidate receipt Kernel regression passes:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_receipt_kernel.py -q
```

Result: `5 passed, 1 warning`; the warning is the existing pytest plugin
rewrite warning for the already-imported support module.

The requested package checks pass:

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
```

## Changes

- Added the exact `CandidateGateResult` cross-field invariants from the brief.
- Rejected authoritative Candidate repository/reference drift before audit.
- Kept Plan Invalidation report-only with `classification is None` and a
  complete receipt/report readback pair, including replay.
- Preserved the private Candidate receipt, complete diff, Assurance,
  ReviewSubject, and accepted-Candidate receipt identity boundary.

## Concern

The combined command including `tests/test_v8_candidate_gate_public.py`
reports `54 passed, 1 failed` because that excluded, pre-existing test still
expects `formal_review_request` on a Plan Invalidation result. Task 5's exact
invariant requires that result to have no `review_subject`, so the compatibility
property correctly returns `None`. The excluded public test was not modified.
