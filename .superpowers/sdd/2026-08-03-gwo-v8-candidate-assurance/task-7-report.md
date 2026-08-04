# Task 7 implementation report

## RED

The disjoint ledger and Repair Packet regressions were added before changing
CandidateGate:

```powershell
py -3.13 -m pytest tests/test_v8_review_finding_ledger.py tests/test_v8_repair_packet.py -q
```

Result: exit code `2`; collection failed because `ReviewFinding` and the
complete ledger/packet contract were absent (`2 errors during collection`).

## GREEN

The new ledger and packet tests pass after the minimal implementation:

```powershell
py -3.13 -m pytest tests/test_v8_review_finding_ledger.py tests/test_v8_repair_packet.py -q
```

Result: `3 passed`.

The requested focused suites pass:

```powershell
py -3.13 -m pytest tests/test_v8_review_finding_ledger.py tests/test_v8_repair_packet.py tests/test_v8_candidate_strict_review.py tests/test_v8_candidate_assurance_standard.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py tests/test_v8_candidate_gate_acceptance.py tests/test_v8_candidate_review_reuse.py tests/test_v8_candidate_receipt_kernel.py -q
```

Result: `97 passed, 1 warning`. The warning is the existing pytest plugin
rewrite warning for the already-imported support module.

The requested package and diff checks pass:

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/quick_validate.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
```

`quick validation passed`; sync, sync check, and diff check exited
successfully.

## Changes

- Added `ReviewFinding`, retained `FormalReviewFinding` as its compatibility
  alias, and added the complete typed Finding ledger and dispositions.
- Added complete `RepairPacket.from_review`, ledger replacement, digest and
  contract validation while retaining existing legacy repair fixture seams.
- CandidateGate now preserves hard and advisory Findings in the repair ledger;
  hard Findings alone select `REPAIR_REQUIRED`, and accepted results carry the
  ledger digest.
- Updated the generated orchestrator manifest.

Task 8 was not started.

## Review fix round

### RED

The review regressions were added before the fix and run together:

```powershell
py -3.13 -m pytest tests/test_v8_repair_packet.py::test_packet_rejects_subject_parent_mismatch tests/test_v8_candidate_gate.py::test_legacy_clean_review_never_emits_an_incomplete_repair_packet tests/test_v8_candidate_assurance_standard.py::test_no_review_allowlist_uses_zero_calls -q
```

Result: `3 failed`:

- `RepairPacket.from_review` accepted a Subject whose parent differed from
  the packet parent.
- the legacy clean-review hard branch emitted its incomplete packet.
- a NO_REVIEW accepted result omitted the empty ledger digest.

### GREEN

The complete scoped regression set passes after the minimal fix and public
repair-flow migration:

```powershell
py -3.13 -m pytest tests/test_v8_review_finding_ledger.py tests/test_v8_repair_packet.py tests/test_v8_candidate_strict_review.py tests/test_v8_candidate_assurance_standard.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py tests/test_v8_candidate_gate_acceptance.py tests/test_v8_candidate_review_reuse.py tests/test_v8_candidate_receipt_kernel.py -q
```

Result: `99 passed, 1 warning`.

The warning is the existing pytest plugin rewrite warning for the already
imported `v8_candidate_assurance_test_support` module.

The fix also passed:

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/quick_validate.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
```

The legacy clean-review seam now delegates to `gate_candidate` only when the
authoritative reader, checks, and Assurance policy are present; otherwise it
preserves report-only scope invalidation and fails closed before producing a
repair packet. `RepairPacket.from_review` now binds the Subject parent, and
NO_REVIEW accepted results carry the empty ledger digest. The public repair
flow uses the complete `CandidateReceipt`/Assurance/ledger `from_review`
packet path.
