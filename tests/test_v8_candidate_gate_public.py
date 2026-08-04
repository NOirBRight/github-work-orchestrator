from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest


pytest_plugins = ("v8_successor_test_support",)


def _campaign(tmp_path, payload):
    import gwo_v8
    from gwo_v8.plan_control import _install_start_host
    from v8_successor_test_support import _direct_setup

    control, repository, gateway, artifacts, source, host, handle, harness = _direct_setup(
        payload
    )
    _install_start_host(host)
    kernel = gwo_v8.install_execution_kernel(
        store_path=tmp_path / "candidate-gate-public.sqlite3",
        plan_control=host,
        effects=harness.effects,
    )
    handle = gwo_v8.start(
        "owner/repository",
        ("issue:108", "issue:109", "issue:110"),
    )
    gwo_v8.advance(handle)
    harness._kernel = kernel
    return control, repository, gateway, artifacts, source, host, handle, harness


def _candidate_parent(host, handle, ticket_key="issue:109"):
    from gwo_v8._canonical import load_canonical_json
    from gwo_v8.candidate_gate import CandidateGateParent
    from gwo_v8.runtime_gateway import WorkRunPurpose, WorkRunSubject
    import gwo_v8

    active = host.read_active(handle)
    plan = load_canonical_json(active.plan_spec_bytes)
    item = next(item for item in plan["work"] if item["key"] == ticket_key)
    run = next(
        run for run in gwo_v8.inspect(handle).work_runs if run.ticket_key == ticket_key
    )
    subject = WorkRunSubject(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        campaign_handle=f"campaign-handle:{handle.campaign_key}",
        plan_revision_digest=active.current_revision_digest,
        work_run_key=run.work_run_key,
        ticket_key=ticket_key,
        purpose=WorkRunPurpose.implementation(),
        prompt_artifact_digest="b" * 64,
        authority_subtree_digest=item["authority"]["worker"]["subtree_digest"],
        stable_action_id=run.runtime_binding_id,
    )
    return CandidateGateParent(
        runtime_subject=subject,
        ticket_contract_digest=item["source"]["digest"],
        policy_witness_digest=plan["policy"]["digest"],
        workspace_identity=f"workspace:{ticket_key}",
    )


class _ReceiptReporter:
    def __init__(self):
        self.calls = 0
        self.evidence = []
        self.reports = []

    def report_plan_invalidation(self, subject, evidence, report):
        from gwo_v8._canonical import digest_value
        from gwo_v8.runtime_gateway import (
            CapabilityPolicy,
            CapabilityPolicyProof,
            PlanInvalidationReceipt,
        )

        self.calls += 1
        self.evidence.append(evidence)
        self.reports.append(report)
        proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="9" * 64,
        )
        observation = {
            "kind": "plan_invalidation_observation.v1",
            "repository": report.repository,
            "campaign_key": report.campaign_key,
            "plan_revision_digest": report.plan_revision_digest,
            "ticket_key": report.ticket_key,
            "work_run_key": report.work_run_key,
            "runtime_binding_id": report.runtime_binding_id,
            "authority_subtree_digest": report.authority_subtree_digest,
            "reporter_role": report.reporter_role,
            "report_digest": report.digest,
            "evidence_digest": report.evidence_digest,
            "dedup_identity": report.dedup_identity,
            "invalidated_obligation": report.invalidated_obligation,
            "required_effects": list(report.required_effects),
            "workspace_identity": report.workspace_identity,
            "source_evidence_digests": list(evidence.source_evidence_digests),
        }
        return PlanInvalidationReceipt(
            report_digest=report.digest,
            receipt_digest=digest_value(
                {
                    "kind": "plan_invalidation_receipt.v1",
                    "report_digest": report.digest,
                    "subject_digest": subject.digest,
                    "authority_record_digest": proof.authority_record_digest,
                }
            ),
            capability_policy_proof=proof,
            observation=observation,
        )


def _audit(parent, *, kind="scope", route=None, code="PERSISTENT_SCOPE"):
    from gwo_v8.candidate_gate import (
        AuditFailureKind,
        AuditFailureRoute,
        CandidateAuditReport,
        CandidateIdentity,
        DeterministicAuditFailure,
    )

    route = route or AuditFailureRoute.TICKET_UNSATISFIABLE
    return CandidateAuditReport(
        parent_digest=parent.digest,
        candidate=CandidateIdentity(
            reported_reference="refs/heads/candidate",
            base_commit_oid="1" * 40,
            base_tree_oid="2" * 40,
            candidate_commit_oid="3" * 40,
            candidate_tree_oid="4" * 40,
            changed_paths=("src/protocol.py",),
        ),
        failures=(
            DeterministicAuditFailure(
                kind=AuditFailureKind(kind),
                route=route,
                code=code,
                detail="The frozen Ticket cannot safely satisfy the discovered obligation.",
                invalidated_obligation="issue:109 persistent owner",
                required_effects=("protocol.persist.v1",),
            ),
        ),
    )


def _payload_with_evidence(payload, evidence_digest):
    changed = deepcopy(payload)
    changed["evidence_digests"] = [evidence_digest]
    return changed


def _successor_payload():
    from v8_successor_test_support import successor_payload

    return successor_payload(
        dependencies=(
            (
                "issue:109",
                "issue:110",
                "The invalidated work consumes the existing owner's result.",
            ),
        )
    )


def test_candidate_gate_early_scope_receipt_enters_public_kernel_without_review(
    tmp_path,
):
    import gwo_v8
    from gwo_v8.candidate_gate import CandidateGate, CandidateGateStatus
    from v8_successor_test_support import successor_payload

    _control, _repository, gateway, _artifacts, _source, host, handle, _harness = _campaign(
        tmp_path, _successor_payload()
    )
    parent = _candidate_parent(host, handle)
    reporter = _ReceiptReporter()
    reviewer = type(
        "NeverReviewer",
        (),
        {"review": lambda self, _request: pytest.fail("deterministic audit invoked review")},
    )()
    result = CandidateGate(
        invalidation_reporter=reporter,
        formal_reviewer=reviewer,
    ).audit_candidate(parent, _audit(parent))

    assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    assert result.plan_invalidation_receipt is not None
    assert result.classification is None
    assert reporter.calls == 1
    assert [item.kind for item in result.evidence] == ["candidate_audit", "plan_invalidation"]
    gateway.payload = _payload_with_evidence(
        _successor_payload(), result.plan_invalidation_report.evidence_digest
    )

    outcome = gwo_v8.advance(handle, plan_invalidation=result.plan_invalidation_receipt)
    diagnostics = gwo_v8.inspect(handle)
    assert outcome.status is diagnostics.status
    assert diagnostics.plan_revision_digest != parent.runtime_subject.plan_revision_digest
    assert diagnostics.work_runs
    assert gateway.replan_progresses == 1


@pytest.mark.parametrize("failure_kind", ("scope", "protected_effect", "authority", "affected_check"))
def test_candidate_gate_deterministic_audits_never_consume_formal_review(
    failure_kind,
):
    from gwo_v8.candidate_gate import CandidateGate, CandidateGateStatus

    parent = _candidate_parent
    # This test intentionally exercises the deep module with an independent
    # parent so it does not couple the deterministic audit contract to Kernel
    # persistence details.
    from test_v8_candidate_gate import _parent as make_parent

    candidate_parent = make_parent()
    reporter = _ReceiptReporter()
    calls = []

    class Reviewer:
        def review(self, _request):
            calls.append("review")
            raise AssertionError("deterministic audit must stop before review")

    result = CandidateGate(
        invalidation_reporter=reporter,
        formal_reviewer=Reviewer(),
    ).audit_candidate(candidate_parent, _audit(candidate_parent, kind=failure_kind))
    assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    assert result.plan_invalidation_receipt is not None
    assert calls == []
    assert reporter.calls == 1


def test_candidate_gate_formal_review_scope_escape_enters_same_public_path_without_repair(
    tmp_path,
):
    import gwo_v8
    from gwo_v8.candidate_gate import (
        CandidateGate,
        CandidateGateStatus,
        FormalReviewFinding,
        FormalReviewResult,
    )
    from gwo_v8._canonical import digest_value
    from v8_successor_test_support import successor_payload

    _control, _repository, gateway, _artifacts, _source, host, handle, _harness = _campaign(
        tmp_path, _successor_payload()
    )
    parent = _candidate_parent(host, handle)
    audit = _audit(parent, route=None)
    audit = type(audit)(parent_digest=audit.parent_digest, candidate=audit.candidate)
    reporter = _ReceiptReporter()

    class Reviewer:
        capability_policy_proof = __import__(
            "gwo_v8.runtime_gateway", fromlist=["CapabilityPolicyProof"]
        ).CapabilityPolicyProof(
            capability_policy=__import__(
                "gwo_v8.runtime_gateway", fromlist=["CapabilityPolicy"]
            ).CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="9" * 64,
        )

        def __init__(self):
            self.calls = 0

        def review(self, request):
            self.calls += 1
            finding = FormalReviewFinding(
                parent_digest=request.parent_digest,
                candidate_digest=request.candidate_digest,
                review_subject_digest=request.digest,
                finding_id="finding:scope",
                severity="hard",
                code="OWNER_OUTSIDE_TICKET",
                message="The persistent owner is outside this frozen Ticket.",
                scope_escape=True,
                invalidated_obligation="issue:109 persistent owner",
                required_effects=("owner.persist.v1",),
            )
            return FormalReviewResult(
                subject_digest=request.digest,
                findings=(finding,),
            )

    reviewer = Reviewer()
    result = CandidateGate(
        invalidation_reporter=reporter,
        formal_reviewer=reviewer,
    ).audit_candidate(parent, audit)
    assert result.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    assert result.repair_packet is None
    assert result.plan_invalidation_report is not None
    assert result.review_subject is None
    assert result.accepted_candidate_receipt is None
    assert reviewer.calls == 1
    assert result.formal_review_request is None
    assert result.evidence[1].kind == "formal_review_finding"
    assert result.classification is None
    gateway.payload = _payload_with_evidence(
        _successor_payload(), result.plan_invalidation_report.evidence_digest
    )

    outcome = gwo_v8.advance(handle, plan_invalidation=result.receipt)
    assert outcome.status is gwo_v8.inspect(handle).status


def test_ordinary_unauthorized_candidate_does_not_enter_public_replanning(tmp_path):
    import gwo_v8
    from gwo_v8.candidate_gate import (
        AuditFailureKind,
        AuditFailureRoute,
        CandidateGate,
        CandidateGateStatus,
    )
    from v8_successor_test_support import successor_payload

    _control, _repository, gateway, _artifacts, _source, host, handle, harness = _campaign(
        tmp_path, _successor_payload()
    )
    parent = _candidate_parent(host, handle)
    result = CandidateGate(invalidation_reporter=_ReceiptReporter()).audit_candidate(
        parent,
        _audit(
            parent,
            route=AuditFailureRoute.ORDINARY_UNAUTHORIZED,
            kind=AuditFailureKind.SCOPE.value,
            code="OUTSIDE_TICKET_SCOPE",
        ),
    )
    assert result.status is CandidateGateStatus.ORDINARY_REJECTED
    assert result.receipt is None
    before = gwo_v8.inspect(handle)
    gwo_v8.advance(handle)
    after = gwo_v8.inspect(handle)
    assert after.plan_revision_digest == before.plan_revision_digest
    assert gateway.replan_progresses == 0
    assert not any(
        run.plan_invalidation is not None for run in after.work_runs
    )


def test_candidate_gate_receipt_replay_is_idempotent_through_public_advance(
    tmp_path,
):
    import gwo_v8
    from gwo_v8.candidate_gate import CandidateGate
    from v8_successor_test_support import _direct_setup

    from gwo_v8.plan_control import _install_start_host

    payload = {
        "evidence_digests": [],
        "disposition": "require_human_decision",
        "reason": "The Candidate discovery needs human scope approval.",
        "successor": None,
        "decision": {
            "code": "HUMAN_DECISION_REQUIRED",
            "detail": "Approve the durable scope change.",
            "required_change": "new_ticket",
        },
    }
    control, _repository, gateway, _artifacts, _source, host, handle, harness = _direct_setup(
        payload
    )
    _install_start_host(host)
    kernel = gwo_v8.install_execution_kernel(
        store_path=tmp_path / "candidate-gate-replay.sqlite3",
        plan_control=host,
        effects=harness.effects,
    )
    handle = gwo_v8.start(
        "owner/repository", ("issue:108", "issue:109", "issue:110")
    )
    gwo_v8.advance(handle)
    harness._kernel = kernel
    parent = _candidate_parent(host, handle)
    reporter = _ReceiptReporter()
    result = CandidateGate(invalidation_reporter=reporter).audit_candidate(
        parent, _audit(parent)
    )
    gateway.payload = _payload_with_evidence(payload, result.plan_invalidation_report.evidence_digest)

    first = gwo_v8.advance(handle, plan_invalidation=result.receipt)
    first_diagnostics = gwo_v8.inspect(handle)
    second = gwo_v8.advance(handle, plan_invalidation=result.receipt)
    second_diagnostics = gwo_v8.inspect(handle)

    assert first.status is second.status
    assert first_diagnostics == second_diagnostics
    assert reporter.calls == 1
    assert gateway.replan_progresses == 1


def test_public_candidate_gate_advance_inspect_retains_source_evidence_lineage(
    tmp_path,
):
    """The public invalidation readback must still name its CandidateGate source."""

    import gwo_v8
    from gwo_v8.candidate_gate import (
        CandidateAuditReport,
        CandidateGate,
        FormalReviewFinding,
        FormalReviewResult,
    )
    from gwo_v8.runtime_gateway import CapabilityPolicy, CapabilityPolicyProof

    payload = {
        "evidence_digests": [],
        "disposition": "require_human_decision",
        "reason": "The Candidate discovery needs human scope approval.",
        "successor": None,
        "decision": {
            "code": "HUMAN_DECISION_REQUIRED",
            "detail": "Approve the durable scope change.",
            "required_change": "new_ticket",
        },
    }
    _control, _repository, gateway, _artifacts, _source, host, handle, _harness = _campaign(
        tmp_path, payload
    )
    parent = _candidate_parent(host, handle)
    audit = _audit(parent)
    audit = CandidateAuditReport(
        parent_digest=audit.parent_digest,
        candidate=audit.candidate,
    )

    class Reviewer:
        capability_policy_proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="9" * 64,
        )

        def review(self, request):
            finding = FormalReviewFinding(
                parent_digest=request.parent_digest,
                candidate_digest=request.candidate_digest,
                review_subject_digest=request.digest,
                finding_id="finding:owner",
                severity="hard",
                code="OWNER_OUTSIDE_TICKET",
                message="The persistent owner is outside the frozen Ticket.",
                scope_escape=True,
                invalidated_obligation="issue:109 persistent owner",
                required_effects=("owner.persist.v1",),
            )
            return FormalReviewResult(
                subject_digest=request.digest,
                findings=(finding,),
            )

    reporter = _ReceiptReporter()
    result = CandidateGate(
        invalidation_reporter=reporter,
        formal_reviewer=Reviewer(),
    ).audit_candidate(parent, audit)
    plan_evidence = result.evidence[-1]
    finding = result.evidence[1]
    report = reporter.reports[-1]

    assert report.evidence_digest == plan_evidence.digest
    assert result.receipt is not None
    assert result.receipt.report_digest == report.digest
    assert result.receipt.observation["evidence_digest"] == plan_evidence.digest

    gateway.payload = _payload_with_evidence(payload, plan_evidence.digest)
    outcome = gwo_v8.advance(handle, plan_invalidation=result.receipt)
    diagnostics = gwo_v8.inspect(handle)

    assert outcome.status is diagnostics.status
    run = next(item for item in diagnostics.work_runs if item.ticket_key == "issue:109")
    assert run.plan_invalidation is not None
    assert run.plan_invalidation.evidence_digest == plan_evidence.digest
    assert run.plan_invalidation.source_evidence_digests == (finding.digest,)


def test_public_repair_scope_escape_enters_same_advance_inspect_path(tmp_path):
    import gwo_v8
    from gwo_v8.candidate_gate import (
        CandidateGate,
        CandidateDiffEntryV1,
        CandidateDiffRecordV1,
        CandidateReadback,
        FormalReviewFinding,
        FormalReviewResult,
        RepairVerificationResult,
    )
    from gwo_v8.runtime_gateway import CapabilityPolicy, CapabilityPolicyProof

    _control, _repository, gateway, _artifacts, _source, host, handle, _harness = _campaign(
        tmp_path, _successor_payload()
    )
    parent = _candidate_parent(host, handle)
    audit = _audit(parent)
    audit = type(audit)(parent_digest=audit.parent_digest, candidate=audit.candidate)

    class Reviewer:
        capability_policy_proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="9" * 64,
        )

        def review(self, request):
            return FormalReviewResult(
                subject_digest=request.digest,
                findings=(
                    FormalReviewFinding(
                        parent_digest=request.parent_digest,
                        candidate_digest=request.candidate_digest,
                        review_subject_digest=request.digest,
                        finding_id="finding:repair",
                        severity="hard",
                        code="CHECK_REQUIRES_REPAIR",
                        message="The local check needs an ordinary repair.",
                    ),
                ),
            )

    class Verifier:
        capability_policy_proof = CapabilityPolicyProof(
            capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
            authority_record_digest="9" * 64,
        )

        def verify(self, request):
            # Deliberately omit the escape: CandidateGate must derive it from
            # the authoritative repaired Candidate delta, not trust the port.
            return RepairVerificationResult(
                request_digest=request.digest,
                accepted=True,
                details=("repair verifier omitted the extra path",),
                invalidated_obligation="repair packet allowed scope",
                required_effects=("owner.persist.v1",),
            )

    repaired = type(audit.candidate)(
        reported_reference="refs/heads/repaired",
        base_commit_oid=audit.candidate.base_commit_oid,
        base_tree_oid=audit.candidate.base_tree_oid,
        candidate_commit_oid="5" * 40,
        candidate_tree_oid="6" * 40,
        # The caller deliberately omits the out-of-scope path.
        changed_paths=("src/protocol.py",),
    )

    class Reader:
        def __init__(self):
            self.calls = []

        def read_candidate(self, repository, reference):
            self.calls.append((repository, reference))
            candidate = audit.candidate if reference.endswith("candidate") else repaired
            paths = candidate.changed_paths
            diff = CandidateDiffRecordV1(
                repository=repository,
                object_format="sha1",
                base_commit_oid=candidate.base_commit_oid,
                base_tree_oid=candidate.base_tree_oid,
                candidate_commit_oid=candidate.candidate_commit_oid,
                candidate_tree_oid=candidate.candidate_tree_oid,
                entries=tuple(
                    CandidateDiffEntryV1(
                        side="candidate",
                        path=path,
                        mode="100644",
                        object_type="blob",
                        object_oid=("7" if path == "src/protocol.py" else "8") * 40,
                    )
                    for path in paths
                ),
            )
            if reference.endswith("repaired"):
                candidate = type(candidate)(
                    reported_reference=candidate.reported_reference,
                    base_commit_oid=candidate.base_commit_oid,
                    base_tree_oid=candidate.base_tree_oid,
                    candidate_commit_oid=candidate.candidate_commit_oid,
                    candidate_tree_oid=candidate.candidate_tree_oid,
                    changed_paths=("src/outside.py", "src/protocol.py"),
                )
                diff = CandidateDiffRecordV1(
                    repository=repository,
                    object_format="sha1",
                    base_commit_oid=candidate.base_commit_oid,
                    base_tree_oid=candidate.base_tree_oid,
                    candidate_commit_oid=candidate.candidate_commit_oid,
                    candidate_tree_oid=candidate.candidate_tree_oid,
                    entries=(
                        CandidateDiffEntryV1(
                            side="candidate",
                            path="src/outside.py",
                            mode="100644",
                            object_type="blob",
                            object_oid="8" * 40,
                        ),
                        CandidateDiffEntryV1(
                            side="candidate",
                            path="src/protocol.py",
                            mode="100644",
                            object_type="blob",
                            object_oid="7" * 40,
                        ),
                    ),
                )
            return CandidateReadback(
                repository=repository,
                candidate=candidate,
                diff_record=diff,
            )

    reporter = _ReceiptReporter()
    reader = Reader()
    gate = CandidateGate(
        invalidation_reporter=reporter,
        candidate_reader=reader,
        formal_reviewer=Reviewer(),
        repair_verifier=Verifier(),
    )
    reviewed = gate.audit_candidate(parent, audit)
    result = gate.verify_repair(parent, reviewed.repair_packet, repaired)

    assert result.status.value == "plan_invalidation_reported"
    assert result.plan_invalidation_receipt is not None
    verification_evidence = result.evidence[0]
    plan_evidence = result.evidence[-1]
    assert verification_evidence.kind == "repair_scope_escape"
    assert verification_evidence.scope_escape_paths == ("src/outside.py",)
    assert plan_evidence.source_kind == "repair_verification"
    assert plan_evidence.invalidated_obligation == "repair packet allowed scope"
    assert plan_evidence.required_effects == ("owner.persist.v1",)
    assert plan_evidence.source_evidence_digest == verification_evidence.digest
    assert {
        item.get("kind") for item in plan_evidence.lineage_artifacts
    } >= {
        "candidate_identity.v1",
        "repair_packet.v1",
        "repair_verification_subject.v1",
        "repair_verification_result.v1",
        "repair_scope_escape.v1",
        "candidate_diff_record.v1",
    }
    assert reader.calls == [
        (parent.runtime_subject.repository, "refs/heads/candidate"),
        (parent.runtime_subject.repository, "refs/heads/repaired"),
    ]
    assert reporter.calls == 1
    gateway.payload = _payload_with_evidence(
        _successor_payload(), result.plan_invalidation_report.evidence_digest
    )
    outcome = gwo_v8.advance(handle, plan_invalidation=result.plan_invalidation_receipt)
    diagnostics = gwo_v8.inspect(handle)
    run = next(item for item in diagnostics.work_runs if item.ticket_key == "issue:109")

    assert outcome.status is diagnostics.status
    # The public advance first persists the invalidation/quiescence boundary,
    # then consumes the approved successor payload in the same call.  Therefore
    # the current Work Run is the successor's fresh run, while the predecessor
    # invalidation remains visible in revision lineage.
    assert diagnostics.plan_revision_digest != parent.runtime_subject.plan_revision_digest
    assert gateway.replan_progresses == 1
    assert run.phase == "running"
    assert diagnostics.revision_lineage[0].source_evidence_digests == (
        result.evidence[0].digest,
    )


def test_public_candidate_invalidation_duplicate_advance_and_restart_does_not_repeat_transitions(
    tmp_path,
):
    """A replayed Candidate receipt is one reporter/classifier/activation effect."""

    import gwo_v8
    from gwo_v8.candidate_gate import CandidateGate

    _control, _repository, gateway, _artifacts, _source, host, handle, harness = _campaign(
        tmp_path, _successor_payload()
    )
    counters = {"classification": 0, "activation": 0}
    classify = host.classify_plan_invalidations
    activate = host.activate_successor

    def counted_classify(campaign_handle, invalidations, execution_snapshot):
        counters["classification"] += 1
        return classify(campaign_handle, invalidations, execution_snapshot)

    def counted_activate(campaign_handle, classification):
        counters["activation"] += 1
        return activate(campaign_handle, classification)

    host.classify_plan_invalidations = counted_classify
    host.activate_successor = counted_activate

    parent = _candidate_parent(host, handle)
    reporter = _ReceiptReporter()
    result = CandidateGate(
        invalidation_reporter=reporter,
    ).audit_candidate(parent, _audit(parent))
    gateway.payload = _payload_with_evidence(
        _successor_payload(), result.plan_invalidation_report.evidence_digest
    )

    first = gwo_v8.advance(handle, plan_invalidation=result.receipt)
    first_readback = gwo_v8.inspect(handle)
    assert first.status is first_readback.status
    assert counters == {"classification": 1, "activation": 1}
    assert reporter.calls == 1
    assert gateway.replan_progresses == 1

    restarted = gwo_v8.install_execution_kernel(
        store_path=tmp_path / "candidate-gate-public.sqlite3",
        plan_control=host,
        effects=harness.effects,
    )
    harness._kernel = restarted

    replay = gwo_v8.advance(handle, plan_invalidation=result.receipt)
    replay_readback = gwo_v8.inspect(handle)

    assert replay.status is replay_readback.status
    assert replay_readback == first_readback
    assert counters == {"classification": 1, "activation": 1}
    assert reporter.calls == 1
    assert gateway.replan_progresses == 1


def test_public_candidate_invalidation_duplicate_restart_does_not_consume_budget_or_slot(
    tmp_path,
):
    """Duplicate public invalidation retains one Decision, budget count, and slot state."""

    import gwo_v8
    from gwo_v8.candidate_gate import CandidateGate

    payload = {
        "evidence_digests": [],
        "disposition": "require_human_decision",
        "reason": "The Candidate discovery needs human scope approval.",
        "successor": None,
        "decision": {
            "code": "HUMAN_DECISION_REQUIRED",
            "detail": "Approve the durable scope change.",
            "required_change": "new_ticket",
        },
    }
    _control, _repository, gateway, _artifacts, _source, host, handle, harness = _campaign(
        tmp_path, payload
    )
    calls = {"classification": 0}
    classify = host.classify_plan_invalidations

    def counted_classify(campaign_handle, invalidations, execution_snapshot):
        calls["classification"] += 1
        return classify(campaign_handle, invalidations, execution_snapshot)

    host.classify_plan_invalidations = counted_classify
    parent = _candidate_parent(host, handle)
    reporter = _ReceiptReporter()
    result = CandidateGate(invalidation_reporter=reporter).audit_candidate(
        parent, _audit(parent)
    )
    gateway.payload = _payload_with_evidence(payload, result.plan_invalidation_report.evidence_digest)

    first = gwo_v8.advance(handle, plan_invalidation=result.receipt)
    first_readback = gwo_v8.inspect(handle)
    first_run = next(item for item in first_readback.work_runs if item.ticket_key == "issue:109")
    assert first.status is first_readback.status
    assert first_readback.human_gate is not None
    assert first_readback.human_gate.repeated_invalidations == 0
    assert first_run.phase == "quiescent"
    assert first_run.slot_held is False
    assert first_run.claim_state == "released"

    restarted = gwo_v8.install_execution_kernel(
        store_path=tmp_path / "candidate-gate-public.sqlite3",
        plan_control=host,
        effects=harness.effects,
    )
    harness._kernel = restarted
    replay = gwo_v8.advance(handle, plan_invalidation=result.receipt)
    replay_readback = gwo_v8.inspect(handle)

    assert replay.status is replay_readback.status
    assert replay_readback == first_readback
    assert replay_readback.human_gate.repeated_invalidations == 0
    assert calls["classification"] == 1
    assert reporter.calls == 1
    assert gateway.replan_progresses == 1
