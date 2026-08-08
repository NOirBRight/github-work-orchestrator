# Task 3 implementation report

## RED

Command:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_assurance_standard.py -q
```

Result: exit code `1`; test collection completed, then all six tests errored
at fixture setup because the exact `AssuranceMode` interface was not yet
defined:

```text
EEEEEE                                                                   [100%]
ImportError: cannot import name 'AssuranceMode' from 'gwo_v8.candidate_gate'
...
6 errors in 1.14s
```

This is the expected RED for the missing Task 3 assurance/review boundary.

## GREEN

Focused Task 3 and CandidateGate regressions:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_assurance_standard.py -q
```

Result: exit code `0`; `6 passed in 0.55s`.

```powershell
py -3.13 -m pytest tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py -q
```

Result: exit code `0`; `49 passed in 9.21s`.

```powershell
py -3.13 -m pytest tests/test_v8_candidate_assurance_standard.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py -q
```

Result: exit code `0`; `55 passed in 7.67s`.

The focused suite proves one authoritative readback, one Standard primary
review, deterministic rejection before review, zero calls for NO_REVIEW, the
exact accepted-Candidate receipt field set, concrete diff-derived Interaction
Keys, and no Kernel-state methods on CandidateGate.

Foundation Kernel receipt regression:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_receipt_kernel.py -q
```

Result: exit code `0`; `5 passed, 1 warning in 0.83s`. The warning is the
existing pytest plugin rewrite warning for the already-imported
`v8_candidate_assurance_test_support` module.

## Fix round 1 RED

Regression tests were added before production changes and run with:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_assurance_standard.py -q
```

Result: exit code `1`; `9 failed, 5 passed in 1.03s`.

The exact failures exposed the review findings:

```text
FAILED test_no_review_allowlist_uses_zero_calls
  AssertionError: assert 'standard' == 'no_review'
FAILED test_missing_required_check_fails_before_reviewer
  Failed: DID NOT RAISE CandidateGateError
FAILED test_duplicate_check_id_fails_before_reviewer
  CandidateGateError.code == CANDIDATE_GATE_EVIDENCE_INVALID
  (expected CANDIDATE_GATE_CHECK_INVALID or CANDIDATE_GATE_ASSURANCE_INVALID)
FAILED test_unexpected_check_id_fails_before_reviewer
  Failed: DID NOT RAISE CandidateGateError
FAILED test_no_required_check_evidence_fails_before_reviewer
  Failed: DID NOT RAISE CandidateGateError
FAILED test_assurance_requirement_rejects_noncanonical_required_checks[0..2]
  Failed: DID NOT RAISE CandidateGateError
FAILED test_tampered_check_observation_digest_fails_before_reviewer
  Failed: DID NOT RAISE CandidateGateError
```

The unmodified baseline therefore falsely accepted incomplete or tampered
check Evidence, called the reviewer for those cases, and serialized
`NO_REVIEW` as `standard`.

## Fix round 1 GREEN

The first post-fix focused run exposed one incorrect regression input: the
test value intended to be unsorted was lexicographically sorted. After fixing
that test input (without changing production behavior), the focused command
passed:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_assurance_standard.py -q
```

Result: exit code `0`; `14 passed in 0.52s`.

CandidateGate regression command:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_assurance_standard.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py -q
```

Result: exit code `0`; `63 passed in 6.56s`.
