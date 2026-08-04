## RED

Command:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_review_reuse.py -q
```

Result: exit code `1`; all eight tests reached fixture setup and failed with
the expected missing Task 4 constructor seam:

```text
TypeError: CandidateGate.__init__() got an unexpected keyword argument
'diff_artifacts'
8 errors in 1.32s
```

The RED tests cover identical-subject/diff readback reuse, each requested
ReviewSubject identity fence, and corrupt diff rejection before Reviewer use.

## GREEN

Focused Task 4 and Task 3 command:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_review_reuse.py tests/test_v8_candidate_assurance_standard.py -q
```

Result: exit code `0`; `22 passed in 1.02s`.

The first focused run exposed that the existing Task 3 fixtures do not yet
inject the new optional Artifact Store. The compatibility path preserves those
legacy calls while enforcing put/readback whenever `diff_artifacts` is
configured.

Generated package sync and checks:

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
```

All three commands exited `0`.
