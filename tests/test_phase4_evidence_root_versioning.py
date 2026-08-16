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


def test_phase4_production_paths_use_current_versioned_evidence_root():
    production_paths = (
        release_subject.EVIDENCE_ROOT,
        live_guard.EVIDENCE_ROOT,
        control_attestor.PRODUCTION_RECEIPT.parent,
    )

    assert production_paths == (CURRENT_EVIDENCE_ROOT,) * 3
    assert all(LEGACY_EVIDENCE_ROOT not in path.parents for path in production_paths)
