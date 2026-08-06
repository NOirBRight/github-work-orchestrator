from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_candidate_assurance_domain_contract_names_exact_interfaces():
    text = "\n".join(
        path.read_text("utf-8")
        for path in (
            ROOT / "docs" / "adr" / "0063-candidate-review-repair-boundary.md",
            ROOT / "docs" / "design" / "gwo-v8-lean-architecture.md",
            ROOT / "docs" / "design" / "gwo-v8-lean-stabilization-spec.md",
            ROOT / "docs" / "design" / "gwo-v8-lean-roadmap.md",
        )
    )
    for required in (
        "CandidateReceipt",
        "candidate_tree_oid",
        "CandidateDiffRecordV1",
        "ReviewSubject",
        "ReviewFindingLedger",
        "AssuranceRequirement",
        "repair_verify",
        "CandidateBudgetExhausted",
        "Beta2",
        "Beta3",
        "root Canary",
    ):
        assert required in text
