# Task 9 implementation report

## RED

Created `tests/test_v8_repair_verification.py` with a complete typed
RepairPacket/ledger fixture, authoritative repaired Candidate reader, diff
Artifact store, required-check runner, Assurance policy, Formal Reviewer trap,
and Repair Verifier. Before the CandidateGate implementation, the required
three regressions were run with:

```powershell
py -3.13 -m pytest tests/test_v8_repair_verification.py -q
```

Result: `3 failed` (missing complete Repair Verification request identity,
unresolved ledger accepted, and verifier reached before scope rejection).

## GREEN

Implemented the bounded Task 9 continuation in
`skills/orchestrator/scripts/gwo_v8/candidate_gate.py`:

- added canonical, typed `RepairDelta` and complete
  `RepairVerificationRequest` contracts;
- read back and stored the repaired Candidate diff, bound the prior diff
  Artifact through the CandidateReceipt, and rejected exact scope escapes
  before the Repair Verifier;
- required a complete disposition ledger, exact passing checks, unchanged
  Assurance, and a `repair_verify` ReviewSubject;
- invoked only `RepairVerifier.verify` on the complete path and bound an
  accepted Candidate receipt to the repaired receipt, diff, Subject,
  Assurance, evidence, and ledger;
- retained a private read-only compatibility path for manually constructed
  legacy RepairPackets; CandidateGate does not produce legacy packets.

The focused Task 9 tests passed:

```powershell
py -3.13 -m pytest tests/test_v8_repair_verification.py -q
py -3.13 -m pytest tests/test_v8_repair_packet.py -q
```

Results: `3 passed`; `3 passed`.

The complete requested focused regression set passed:

```powershell
py -3.13 -m pytest tests/test_v8_repair_verification.py tests/test_v8_repair_packet.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_acceptance.py tests/test_v8_candidate_strict_review.py tests/test_v8_candidate_assurance_standard.py tests/test_v8_candidate_gate_public.py tests/test_v8_candidate_review_reuse.py tests/test_v8_candidate_receipt_kernel.py tests/test_v8_candidate_budget_kernel.py tests/test_v8_watchdog_execution_kernel.py tests/test_v8_execution_kernel.py tests/test_v8_successor_execution_kernel.py -q
```

Result: `222 passed, 1 warning`.

The Candidate Receipt foundation suite also passed:

```powershell
py -3.13 -m pytest tests/test_v8_candidate_receipt_foundation.py -q
```

Result: `11 passed`.

After the final decorator and public-fixture adjustments, the combined
Task 9 focused set including that foundation suite passed with `233 passed,
1 warning`.

The generated package manifest was synchronized with:

```powershell
py -3.13 scripts/sync_orchestrator.py
```

No `execution_kernel.py`, #113, or Task 10 files were changed.

## Review fix round

The review regressions were added before the fix and run with:

```powershell
py -3.13 -m pytest tests/test_v8_repair_verification.py -q
```

Result: `5 failed, 3 passed`. The failures covered duplicate check IDs,
repaired-base drift, and the missing RepairVerificationRequest cross-field
bindings.

The minimal fix added raw exact-tuple and duplicate-ID rejection before the
check map, prior Artifact and repaired-base binding, and parent/Subject/
Candidate/RepairDelta identity invariants. The same command then produced
`8 passed`.

The complete requested fix-round focused set passed:

```powershell
py -3.13 -m pytest tests/test_v8_repair_verification.py tests/test_v8_repair_packet.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py tests/test_v8_candidate_strict_review.py tests/test_v8_candidate_assurance_standard.py tests/test_v8_candidate_gate_acceptance.py tests/test_v8_candidate_review_reuse.py tests/test_v8_candidate_receipt_foundation.py tests/test_v8_candidate_receipt_kernel.py tests/test_v8_candidate_budget_kernel.py tests/test_v8_watchdog_execution_kernel.py tests/test_v8_execution_kernel.py tests/test_v8_successor_execution_kernel.py -q
```

Result: `238 passed, 1 warning`.
