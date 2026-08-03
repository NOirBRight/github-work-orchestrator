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
