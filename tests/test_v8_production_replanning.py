from __future__ import annotations

from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))


pytest_plugins = ("v8_production_test_support",)


def _assert_bounded_invalidation(host, result):
    assert result.classification is None
    report = result.plan_invalidation_report
    assert report is not None
    for field_name in (
        "replacement_planspec",
        "ticket_owner",
        "dependency_edit",
        "campaign_membership",
        "merge_request",
        "campaign_order",
    ):
        assert getattr(report, field_name) is None
    assert host.inspect(host.handle).plan_invalidation_classification is None


def test_reopened_137_deterministic_scope_invalidation_uses_zero_reviewer_calls(
    tmp_path,
    reopened_137_host,
):
    from gwo_v8.candidate_gate import CandidateGateStatus, PlanInvalidationEvidence

    reopened_137_host.submit_candidate(
        "issue:109",
        "refs/heads/candidate",
    )
    reopened_137_host.advance(
        reopened_137_host.handle,
        wake_ref="candidate:137:deterministic",
    )
    result = reopened_137_host.result_for("issue:109")
    run = reopened_137_host.run_for("issue:109")
    assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    _assert_bounded_invalidation(reopened_137_host, result)
    assert reopened_137_host.candidate_calls_for("issue:109") == 1
    assert reopened_137_host.reviewer_calls == 0
    assert reopened_137_host.reporter_calls == 1
    assert run.phase == "quiescent"
    assert run.slot_held is False
    assert run.plan_invalidation is not None
    evidence = next(
        item for item in result.evidence if isinstance(item, PlanInvalidationEvidence)
    )
    assert run.plan_invalidation.source_evidence_digests == evidence.source_evidence_digests


def test_reopened_137_formal_review_scope_escape_keeps_complete_evidence_lineage(
    tmp_path,
    reopened_137_host,
):
    from gwo_v8.candidate_gate import CandidateGateStatus, PlanInvalidationEvidence

    reopened_137_host.submit_formal_review_scope_escape("issue:109")
    reopened_137_host.advance(
        reopened_137_host.handle,
        "candidate:137:review",
    )
    result = reopened_137_host.result_for("issue:109")
    run = reopened_137_host.run_for("issue:109")
    assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    _assert_bounded_invalidation(reopened_137_host, result)
    assert reopened_137_host.candidate_calls_for("issue:109") == 1
    assert result.repair_packet is None
    assert reopened_137_host.formal_review_calls == 1
    assert reopened_137_host.repair_verification_calls == 0
    assert reopened_137_host.reporter_calls == 1
    evidence = next(
        item for item in result.evidence if isinstance(item, PlanInvalidationEvidence)
    )
    assert run.plan_invalidation.source_evidence_digests == evidence.source_evidence_digests


def test_reopened_137_repair_scope_escape_does_not_reopen_exploratory_review(
    tmp_path,
    reopened_137_host,
):
    from gwo_v8.candidate_gate import CandidateGateStatus

    reopened_137_host.submit_repair_scope_escape("issue:109")
    reopened_137_host.advance(
        reopened_137_host.handle,
        "candidate:137:repair",
    )
    result = reopened_137_host.result_for("issue:109")
    assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    _assert_bounded_invalidation(reopened_137_host, result)
    assert reopened_137_host.candidate_calls_for("issue:109") == 1
    assert reopened_137_host.formal_review_calls == 0
    assert reopened_137_host.repair_verification_calls == 1
    assert reopened_137_host.reporter_calls == 1
    assert reopened_137_host.run_for("issue:109").phase == "quiescent"


def test_reopened_137_ordinary_unauthorized_candidate_never_enters_campaign_replanning(
    tmp_path,
    reopened_137_host,
):
    reopened_137_host.submit_ordinary_unauthorized_candidate("issue:109")
    before = reopened_137_host.inspect(reopened_137_host.handle)
    reopened_137_host.advance(
        reopened_137_host.handle,
        "candidate:137:ordinary",
    )
    from gwo_v8.candidate_gate import CandidateGateStatus

    result = reopened_137_host.result_for("issue:109")
    after = reopened_137_host.inspect(reopened_137_host.handle)
    assert result.status is CandidateGateStatus.ORDINARY_REJECTED
    assert reopened_137_host.candidate_calls_for("issue:109") == 1
    assert reopened_137_host.reporter_calls == 0
    assert after.plan_revision_digest == before.plan_revision_digest
    assert after.invalidation_classification is None


def test_reopened_137_restart_replay_is_idempotent_and_preserves_unaffected_work(
    tmp_path,
    reopened_137_host,
):
    reopened_137_host.submit_candidate(
        "issue:109",
        "refs/heads/candidate",
    )
    first = reopened_137_host.advance(
        reopened_137_host.handle,
        "candidate:137:replay",
    )
    receipt = reopened_137_host.result_for(
        "issue:109"
    ).plan_invalidation_receipt
    assert receipt is not None
    unaffected_before = reopened_137_host.run_for("issue:108")
    restarted = reopened_137_host.restart()
    second = restarted.advance(
        restarted.handle,
        "candidate:137:replay",
    )
    assert first == second
    assert restarted.reporter_calls == 1
    assert restarted.candidate_calls_for("issue:109") == 1
    assert restarted.run_for("issue:108") == unaffected_before
    assert (
        restarted.run_for("issue:109").plan_invalidation.report_digest
        == receipt.report_digest
    )
