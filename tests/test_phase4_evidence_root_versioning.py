from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "skills" / "orchestrator" / "scripts", ROOT / "scripts"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import beta3_control_ownership_attestor as control_attestor  # noqa: E402
import beta3_release_subject as release_subject  # noqa: E402
import run_beta3_live_guard as live_guard  # noqa: E402


CURRENT_EVIDENCE_ROOT = Path(
    r"D:\gwo-release-evidence\2026-08-17-gwo-v8-beta3-production-cutover"
).resolve()
LEGACY_EVIDENCE_ROOT = Path(
    r"D:\gwo-release-evidence\2026-08-09-gwo-v8-beta3-production-cutover"
).resolve()
CURRENT_RECEIPT_RUNBOOK_SHA256 = (
    "8bf363dd1b3b69ccca2d03d5c8814d75428980e3d2b11ad129daa28ff4d9a2ba"
)
LEGACY_RECEIPT_RUNBOOK_SHA256 = (
    "329bade311df03d0b52a344ce7062c7c7984e2fa35b3d0fa9cbb5386a88e0c6c"
)


def test_phase4_production_paths_use_current_versioned_evidence_root():
    production_paths = (
        release_subject.EVIDENCE_ROOT,
        live_guard.EVIDENCE_ROOT,
        control_attestor.PRODUCTION_RECEIPT.parent,
    )

    assert production_paths == (CURRENT_EVIDENCE_ROOT,) * 3
    assert all(LEGACY_EVIDENCE_ROOT not in path.parents for path in production_paths)


def test_phase4_receipt_runbook_hash_is_unified_for_live_guard_and_attestor():
    production_hashes = (
        live_guard.EXPECTED_FRESH_RECEIPT_RUNBOOK_SHA256,
        control_attestor.PRODUCTION_RECEIPT_RUNBOOK_SHA256,
    )

    assert production_hashes == (CURRENT_RECEIPT_RUNBOOK_SHA256,) * 2
    assert all(runbook_hash != LEGACY_RECEIPT_RUNBOOK_SHA256 for runbook_hash in production_hashes)
