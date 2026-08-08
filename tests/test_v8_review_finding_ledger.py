from __future__ import annotations

from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.candidate_gate import (  # noqa: E402
    FormalReviewFinding,
    FormalReviewResult,
    ReviewFinding,
    ReviewFindingDisposition,
    ReviewFindingLedger,
)


def _finding(*, finding_id: str, severity: str) -> FormalReviewFinding:
    return ReviewFinding(
        parent_digest="1" * 64,
        candidate_digest="2" * 64,
        review_subject_digest="3" * 64,
        finding_id=finding_id,
        severity=severity,
        code=finding_id.upper().replace(":", "_"),
        message=f"message for {finding_id}",
    )


def test_ledger_preserves_all_findings_with_unresolved_dispositions():
    review_result = FormalReviewResult(
        subject_digest="3" * 64,
        findings=(
            _finding(finding_id="finding:test", severity="advisory"),
            _finding(finding_id="finding:authority", severity="hard"),
        ),
    )

    ledger = ReviewFindingLedger.from_review_result(review_result)

    assert FormalReviewFinding is ReviewFinding
    assert tuple(entry.finding.finding_id for entry in ledger.entries) == (
        "finding:authority",
        "finding:test",
    )
    assert all(
        entry.disposition is ReviewFindingDisposition.UNRESOLVED
        for entry in ledger.entries
    )
    assert not ledger.is_complete
