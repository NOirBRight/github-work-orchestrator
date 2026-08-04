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

## Fix follow-up

The legacy public assertion for a Formal Review scope escape was migrated to
the Task 5 contract: Plan Invalidation keeps its report/readback boundary,
`classification` remains `None`, and both `review_subject` and the compatibility
`formal_review_request` are `None`. The public test still exercises the full
scope-escape and public advance path.

## Review fix

The review regressions were written and run before production changes:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_gate_acceptance.py -q
```

Result: exit code `1`; `5 failed, 8 passed`. The failures covered isolated
receipt/report fields on ordinary and accepted results, and a receipt/report
digest mismatch.

The minimal invariant now requires receipt and report to be both absent or
both present for every status, requires both for Plan Invalidation, and binds
`PlanInvalidationReceipt.report_digest` to `PlanInvalidationReport.digest`.

The post-fix focused suite passed with `67 passed, 1 warning`. After regenerating
the manifest, `quick_validate.py`, `sync_orchestrator.py --check`, and
`git diff --check` all passed.
