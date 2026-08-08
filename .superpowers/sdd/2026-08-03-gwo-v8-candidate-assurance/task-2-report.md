# Task 2 implementation report

## RED

Command:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_git_readback.py -q
```

Result: exit code `1` during test collection, before any test body ran.

```text
ImportError while importing test module 'D:\Workstation\gwo-worktrees\issue-136\tests\test_v8_candidate_git_readback.py'.
tests\test_v8_candidate_git_readback.py:17: in <module>
    from gwo_v8.candidate_git import CandidateBasePort, GitCandidateReader
E   ModuleNotFoundError: No module named 'gwo_v8.candidate_git'
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.76s
```

This is the expected RED: the new `candidate_git` module and its two requested
interfaces do not yet exist.

## GREEN

Focused implementation validation:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_git_readback.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py -q
```

Result: exit code `0`; `49 passed in 23.56s`.

Foundation/package-lane regression validation:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_receipt_foundation.py tests/test_v8_candidate_receipt_kernel.py -q
```

Result: exit code `0`; `16 passed, 1 warning in 0.89s`. The warning is the
existing pytest plugin rewrite warning for the already-imported
`v8_candidate_assurance_test_support` module.

## Round 1 RED — canonical diff/readback regressions

Tests were added before the production fixes and run with:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_git_readback.py tests/test_v8_candidate_gate.py -k "exact_diff_record_sorts_add_before_delete or oid_width_mismatch or raw_tab_path or file_type_mode_changes" -q
```

Result: exit code `1`; `13 failed, 1 passed, 37 deselected in 7.68s`.
The expected failures were: add/delete ordering returned delete first; raw tab
paths were rejected as malformed framing; `100644 -> 120000` was reported as
`modify`; and 40/64-character OID width mismatches were accepted.

Round 1 focused regression GREEN:

```text
14 passed, 37 deselected in 6.53s
```

Task 2 focused suite:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_git_readback.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py -q
```

Result: exit code `0`; `63 passed in 34.10s`.

Package regression suite:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_receipt_foundation.py tests/test_v8_candidate_receipt_kernel.py -q
```

Result: exit code `0`; `16 passed, 1 warning in 1.07s`. The warning is the
existing pytest plugin rewrite warning for the already-imported support module.
