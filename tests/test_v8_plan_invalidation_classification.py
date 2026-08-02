from __future__ import annotations

from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_replanning_prompt_declares_only_the_closed_disposition_contract():
    from gwo_v8.planning_protocol import (
        replanning_output_payload_schema,
        replanning_prompt,
    )

    prompt = replanning_prompt(
        subject_digest="a" * 64,
        authority_digest="b" * 64,
        snapshot_artifact_digest="c" * 64,
        policy_witness_artifact_digest="d" * 64,
    )

    payload = prompt["payload"]
    assert payload["output_contract"]["allowed_fields"] == [
        "evidence_digests",
        "disposition",
        "reason",
        "successor",
        "decision",
    ]
    assert payload["output_contract"]["payload_schema"] == (
        replanning_output_payload_schema()
    )
    assert "worker_steps" in payload["output_contract"]["forbidden_facts"]
    assert "delegation" in payload["output_contract"]["forbidden_facts"]
    branches = {
        branch["properties"]["disposition"]["const"]
        for branch in replanning_output_payload_schema()["oneOf"]
    }
    assert branches == {
        "resume_unchanged",
        "defer_non_blocking",
        "use_approved_successor",
        "require_human_decision",
        "reject_invalid_evidence",
    }


def test_coordinator_capability_proof_rejects_any_write_or_delegation():
    from gwo_v8.runtime_gateway import CoordinatorCapabilityProof

    proof = CoordinatorCapabilityProof(
        subject_digest="a" * 64,
        repository_read_only=True,
        tracker_read_only=True,
        can_activate_plan_revision=False,
        can_edit_tracker=False,
        can_expand_authority=False,
        delegation_enabled=False,
    )
    assert proof.is_proven is True

    with pytest.raises(Exception):
        CoordinatorCapabilityProof(
            subject_digest="a" * 64,
            repository_read_only=True,
            tracker_read_only=True,
            can_activate_plan_revision=False,
            can_edit_tracker=False,
            can_expand_authority=False,
            delegation_enabled=True,
        )


def test_classification_has_typed_disposition_and_stable_canonical_identity():
    from gwo_v8.plan_control import (
        PlanInvalidationClassification,
        PlanInvalidationDisposition,
    )

    classification = PlanInvalidationClassification(
        action_id="replan:one",
        snapshot_digest="a" * 64,
        plan_revision_digest="b" * 64,
        evidence_digests=("c" * 64,),
        disposition=PlanInvalidationDisposition.RESUME_UNCHANGED,
        reason="The report does not prove a contract change.",
        capability_proof_digest="d" * 64,
    )

    assert classification.disposition is PlanInvalidationDisposition.RESUME_UNCHANGED
    assert classification.canonical()["kind"] == "plan_invalidation_classification.v1"
    assert classification.digest == classification.digest


def test_plancontrol_coalesces_pending_evidence_into_one_bounded_snapshot():
    from test_v8_plancontrol_rebuild import _Artifacts, _Gateway, _Source, _snapshot

    from gwo_v8._canonical import load_canonical_json
    from gwo_v8.execution_kernel import PlanInvalidationObservation
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl
    from gwo_v8.runtime_gateway import CoordinatorCapabilityProof, PlanningReceipt

    artifacts = _Artifacts()
    repository = InMemoryPlanRepository(writer_generation="writer:one")

    class Gateway(_Gateway):
        def read_coordinator_capability(self, subject):
            return CoordinatorCapabilityProof(
                subject_digest=subject.digest,
                repository_read_only=True,
                tracker_read_only=True,
                can_activate_plan_revision=False,
                can_edit_tracker=False,
                can_expand_authority=False,
                delegation_enabled=False,
            )

        def progress(self, subject, preflight):
            if not subject.stable_action_id.startswith("replan:"):
                return super().progress(subject, preflight)
            output = artifacts.put_canonical(
                {
                    "schema_version": "gwo.runtime.output.v1",
                    "subject_digest": subject.digest,
                    "stable_action_id": subject.stable_action_id,
                    "authority_digest": subject.authority_digest,
                    "payload": {
                        "evidence_digests": ["c" * 64, "d" * 64],
                        "disposition": "defer_non_blocking",
                        "reason": "The concern is real but outside frozen acceptance.",
                        "successor": None,
                        "decision": None,
                    },
                }
            )
            self.progresses.append(subject)
            return PlanningReceipt(
                subject_digest=subject.digest,
                stable_action_id=subject.stable_action_id,
                status="completed",
                receipt_digest="8" * 64,
                output_artifact_digest=output.digest,
                planning_output_artifact_digest=output.digest,
            )

    gateway = Gateway(artifacts)
    control = PlanControl(
        source=_Source(_snapshot()),
        artifacts=artifacts,
        gateway=gateway,
        repository=repository,
    )
    handle = control.start("owner/repository", ["issue:109"])
    active = control.read_active(handle)
    plan = load_canonical_json(active.plan_spec_bytes)
    worker_authority = plan["work"][0]["authority"]["worker"]["subtree_digest"]

    def observation(evidence_digest, dedup_identity):
        return PlanInvalidationObservation(
            repository=handle.repository,
            campaign_key=handle.campaign_key,
            plan_revision_digest=active.current_revision_digest,
            ticket_key="issue:109",
            work_run_key="work-run:issue:109",
            runtime_binding_id="binding:issue:109",
            authority_subtree_digest=worker_authority,
            reporter_role="worker",
            report_digest=evidence_digest,
            evidence_digest=evidence_digest,
            dedup_identity=dedup_identity,
            invalidated_obligation="issue:109 obligation",
            required_effects=("workspace.write.v1",),
            workspace_identity="workspace:issue:109",
        )

    invalidations = (observation("c" * 64, "one"), observation("d" * 64, "two"))
    execution = {
        "runs": [
            {
                "ticket_key": "issue:109",
                "work_run_key": "work-run:issue:109",
                "phase": "quiescent",
                "slot_held": False,
                "reason": "PlanInvalidation",
                "next_check_at": None,
                "runtime_binding_id": "binding:issue:109",
                "claim_state": "released",
                "exclusive_resources": [],
            }
        ],
        "claims": [
            {
                "ticket_key": "issue:109",
                "repository": handle.repository,
                "campaign_key": handle.campaign_key,
                "plan_revision_digest": active.current_revision_digest,
            }
        ],
        "accepted_results": [],
    }

    first = control.classify_plan_invalidations(handle, invalidations, execution)
    second = control.classify_plan_invalidations(handle, invalidations, execution)

    assert first == second
    assert first.evidence_digests == ("c" * 64, "d" * 64)
    assert first.disposition.value == "defer_non_blocking"
    assert len([item for item in gateway.progresses if item.stable_action_id.startswith("replan:")]) == 1
    assert first.action_id.startswith("replan:")


def test_plancontrol_replays_a_completed_classification_after_unrelated_run_readback_changes():
    from copy import deepcopy

    from test_v8_plancontrol_rebuild import _Artifacts, _Gateway, _Source, _snapshot

    from gwo_v8.execution_kernel import PlanInvalidationObservation
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl
    from gwo_v8.runtime_gateway import CoordinatorCapabilityProof, PlanningReceipt

    artifacts = _Artifacts()
    repository = InMemoryPlanRepository(writer_generation="writer:one")

    class Gateway(_Gateway):
        def read_coordinator_capability(self, subject):
            return CoordinatorCapabilityProof(
                subject_digest=subject.digest,
                repository_read_only=True,
                tracker_read_only=True,
                can_activate_plan_revision=False,
                can_edit_tracker=False,
                can_expand_authority=False,
                delegation_enabled=False,
            )

        def progress(self, subject, preflight):
            if subject.stable_action_id.startswith("replan:"):
                output = artifacts.put_canonical(
                    {
                        "schema_version": "gwo.runtime.output.v1",
                        "subject_digest": subject.digest,
                        "stable_action_id": subject.stable_action_id,
                        "authority_digest": subject.authority_digest,
                        "payload": {
                            "evidence_digests": ["c" * 64],
                            "disposition": "defer_non_blocking",
                            "reason": "The concern is outside frozen acceptance.",
                            "successor": None,
                            "decision": None,
                        },
                    }
                )
                self.progresses.append(subject)
                return PlanningReceipt(
                    subject_digest=subject.digest,
                    stable_action_id=subject.stable_action_id,
                    status="completed",
                    receipt_digest="8" * 64,
                    output_artifact_digest=output.digest,
                    planning_output_artifact_digest=output.digest,
                )
            return super().progress(subject, preflight)

    gateway = Gateway(artifacts)
    control = PlanControl(
        source=_Source(_snapshot()),
        artifacts=artifacts,
        gateway=gateway,
        repository=repository,
    )
    handle = control.start("owner/repository", ["issue:109"])
    active = control.read_active(handle)
    plan = __import__("gwo_v8._canonical", fromlist=["load_canonical_json"]).load_canonical_json(
        active.plan_spec_bytes
    )
    authority = plan["work"][0]["authority"]["worker"]["subtree_digest"]
    observation = PlanInvalidationObservation(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        plan_revision_digest=active.current_revision_digest,
        ticket_key="issue:109",
        work_run_key="work-run:issue:109",
        runtime_binding_id="binding:issue:109",
        authority_subtree_digest=authority,
        reporter_role="worker",
        report_digest="e" * 64,
        evidence_digest="c" * 64,
        dedup_identity="replay:one",
        invalidated_obligation="issue:109 obligation",
        required_effects=("workspace.write.v1",),
        workspace_identity="workspace:issue:109",
    )
    execution = {
        "runs": [
            {
                "ticket_key": "issue:109",
                "work_run_key": "work-run:issue:109",
                "phase": "quiescent",
                "slot_held": False,
                "reason": "PlanInvalidation",
                "next_check_at": None,
                "runtime_binding_id": "binding:issue:109",
                "claim_state": "released",
                "exclusive_resources": [],
            }
        ],
        "claims": [
            {
                "ticket_key": "issue:109",
                "repository": handle.repository,
                "campaign_key": handle.campaign_key,
                "plan_revision_digest": active.current_revision_digest,
            }
        ],
        "accepted_results": [],
    }
    first = control.classify_plan_invalidations(handle, (observation,), execution)
    changed_execution = deepcopy(execution)
    changed_execution["runs"][0].update(
        {"phase": "running", "slot_held": True, "claim_state": "held"}
    )
    second = control.classify_plan_invalidations(
        handle, (observation,), changed_execution
    )

    assert second == first
    assert len([item for item in gateway.progresses if item.stable_action_id.startswith("replan:")]) == 1


def test_execution_kernel_reads_back_resume_classification_before_reacquiring_slot(tmp_path):
    from gwo_v8._canonical import digest_value
    from gwo_v8.execution_kernel import (
        ExecutionKernel,
        PlanInvalidationObservation,
    )
    from gwo_v8.plan_control import (
        PlanInvalidationClassification,
        PlanInvalidationDisposition,
    )
    from test_v8_execution_kernel import _Effects, _active_campaign, _summary

    active, handle = _active_campaign(("issue:1",))

    class Plans:
        def __init__(self):
            self.calls = 0

        def read_active(self, requested):
            assert requested == handle
            return active

        def classify_plan_invalidations(self, requested, invalidations, snapshot):
            self.calls += 1
            assert requested == handle
            assert snapshot["runs"][0]["phase"] == "quiescent"
            evidence = tuple(sorted(item.evidence_digest for item in invalidations))
            return PlanInvalidationClassification(
                action_id="replan:" + digest_value(
                    {
                        "repository": handle.repository,
                        "campaign_key": handle.campaign_key,
                        "plan_revision_digest": active.current_revision_digest,
                        "evidence_digests": list(evidence),
                    }
                ),
                snapshot_digest="c" * 64,
                plan_revision_digest=active.current_revision_digest,
                evidence_digests=evidence,
                disposition=PlanInvalidationDisposition.RESUME_UNCHANGED,
                reason="The frozen contract remains sufficient.",
                capability_proof_digest="d" * 64,
            )

    effects = _Effects()
    plans = Plans()
    kernel = ExecutionKernel(
        store_path=tmp_path / "resume.sqlite3",
        plan_control=plans,
        effects=effects,
    )
    kernel.advance(handle)
    binding = effects.executed[0].stable_action_id
    observation = PlanInvalidationObservation(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        plan_revision_digest=active.current_revision_digest,
        ticket_key="issue:1",
        work_run_key="work-run:issue:1",
        runtime_binding_id=binding,
        authority_subtree_digest="a" * 64,
        reporter_role="worker",
        report_digest="e" * 64,
        evidence_digest="f" * 64,
        dedup_identity="resume:one",
        invalidated_obligation="issue:1 obligation",
        required_effects=("workspace.write.v1",),
        workspace_identity="workspace:issue:1",
    )

    outcome = kernel.advance(handle, plan_invalidation=observation)
    assert outcome.status.value == "Running"
    assert plans.calls == 1
    assert _summary(kernel, handle, "issue:1").plan_invalidation is not None
    assert _summary(kernel, handle, "issue:1").phase == "running"
    diagnostics = kernel.inspect(handle)
    assert diagnostics.invalidation_classification is not None
    assert diagnostics.invalidation_classification.disposition is PlanInvalidationDisposition.RESUME_UNCHANGED

    kernel.advance(handle)
    assert plans.calls == 1


def test_execution_kernel_keeps_successor_classification_quiescent_and_visible(tmp_path):
    from gwo_v8._canonical import digest_value
    from gwo_v8.execution_kernel import ExecutionKernel, PlanInvalidationObservation
    from gwo_v8.plan_control import (
        PlanInvalidationClassification,
        PlanInvalidationDisposition,
    )
    from test_v8_execution_kernel import _Effects, _active_campaign, _summary

    active, handle = _active_campaign(("issue:1",))
    effects = _Effects()

    class Plans:
        def __init__(self):
            self.calls = 0

        def read_active(self, requested):
            return active

        def classify_plan_invalidations(self, requested, invalidations, snapshot):
            self.calls += 1
            evidence = tuple(sorted(item.evidence_digest for item in invalidations))
            return PlanInvalidationClassification(
                action_id="replan:" + digest_value(
                    {
                        "repository": handle.repository,
                        "campaign_key": handle.campaign_key,
                        "plan_revision_digest": active.current_revision_digest,
                        "evidence_digests": list(evidence),
                    }
                ),
                snapshot_digest="1" * 64,
                plan_revision_digest=active.current_revision_digest,
                evidence_digests=evidence,
                disposition=PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR,
                reason="An approved Ticket owns the required behavior.",
                capability_proof_digest="2" * 64,
                successor_ticket_keys=("issue:1",),
            )

    plans = Plans()
    kernel = ExecutionKernel(
        store_path=tmp_path / "successor.sqlite3",
        plan_control=plans,
        effects=effects,
    )
    kernel.advance(handle)
    binding = effects.executed[0].stable_action_id
    observation = PlanInvalidationObservation(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        plan_revision_digest=active.current_revision_digest,
        ticket_key="issue:1",
        work_run_key="work-run:issue:1",
        runtime_binding_id=binding,
        authority_subtree_digest="a" * 64,
        reporter_role="worker",
        report_digest="3" * 64,
        evidence_digest="4" * 64,
        dedup_identity="successor:one",
        invalidated_obligation="issue:1 obligation",
        required_effects=("workspace.write.v1",),
        workspace_identity="workspace:issue:1",
    )

    outcome = kernel.advance(handle, plan_invalidation=observation)
    assert outcome.status.value == "Decision"
    assert _summary(kernel, handle, "issue:1").phase == "quiescent"
    classification = kernel.inspect(handle).invalidation_classification
    assert classification is not None
    assert classification.successor_ticket_keys == ("issue:1",)
    kernel.advance(handle)
    assert plans.calls == 1
    assert len(effects.executed) == 1


def test_plancontrol_and_execution_kernel_share_the_replanning_readback(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel, PlanInvalidationObservation
    from gwo_v8.plan_control import PlanControl, InMemoryPlanRepository
    from gwo_v8.runtime_gateway import PlanningReceipt, CoordinatorCapabilityProof
    from test_v8_plancontrol_rebuild import _Artifacts, _Gateway, _Source, _snapshot
    from test_v8_execution_kernel import _Effects, _summary

    artifacts = _Artifacts()
    repository = InMemoryPlanRepository(writer_generation="writer:one")

    class Gateway(_Gateway):
        def _read_coordinator_capability(self, subject):
            return CoordinatorCapabilityProof(
                subject_digest=subject.digest,
                repository_read_only=True,
                tracker_read_only=True,
                can_activate_plan_revision=False,
                can_edit_tracker=False,
                can_expand_authority=False,
                delegation_enabled=False,
            )

        def progress(self, subject, preflight):
            if subject.stable_action_id.startswith("replan:"):
                snapshot = artifacts.read_json(subject.snapshot_artifact_digest)
                output = artifacts.put_canonical(
                    {
                        "schema_version": "gwo.runtime.output.v1",
                        "subject_digest": subject.digest,
                        "stable_action_id": subject.stable_action_id,
                        "authority_digest": subject.authority_digest,
                        "payload": {
                            "evidence_digests": sorted(
                                item["evidence_digest"]
                                for item in snapshot["pending_invalidations"]
                            ),
                            "disposition": "defer_non_blocking",
                            "reason": "The concern is outside frozen acceptance.",
                            "successor": None,
                            "decision": None,
                        },
                    }
                )
                return PlanningReceipt(
                    subject_digest=subject.digest,
                    stable_action_id=subject.stable_action_id,
                    status="completed",
                    receipt_digest="8" * 64,
                    output_artifact_digest=output.digest,
                    planning_output_artifact_digest=output.digest,
                )
            return super().progress(subject, preflight)

    control = PlanControl(
        source=_Source(_snapshot()),
        artifacts=artifacts,
        gateway=Gateway(artifacts),
        repository=repository,
    )
    handle = control.start("owner/repository", ["issue:109"])
    active = control.read_active(handle)
    plan = __import__("gwo_v8._canonical", fromlist=["load_canonical_json"]).load_canonical_json(
        active.plan_spec_bytes
    )
    effects = _Effects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "composed.sqlite3",
        plan_control=control,
        effects=effects,
    )
    kernel.advance(handle)
    binding = effects.executed[0].stable_action_id
    evidence = "9" * 64
    observation = PlanInvalidationObservation(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        plan_revision_digest=active.current_revision_digest,
        ticket_key="issue:109",
        work_run_key="work-run:issue:109",
        runtime_binding_id=binding,
        authority_subtree_digest=plan["work"][0]["authority"]["worker"]["subtree_digest"],
        reporter_role="worker",
        report_digest="a" * 64,
        evidence_digest=evidence,
        dedup_identity="composed:one",
        invalidated_obligation="issue:109 obligation",
        required_effects=("workspace.write.v1",),
        workspace_identity="workspace:issue:109",
    )

    kernel.advance(handle, plan_invalidation=observation)
    assert _summary(kernel, handle, "issue:109").phase == "running"
    assert kernel.inspect(handle).invalidation_classification is not None
    assert repository.read_invalidation_classification(
        handle,
        kernel.inspect(handle).invalidation_classification.action_id,
    ) is not None


def test_execution_kernel_coalesces_a_second_pending_invalidation_after_quiescence(tmp_path):
    from gwo_v8._canonical import digest_value
    from gwo_v8.execution_kernel import ExecutionKernel, PlanInvalidationObservation
    from gwo_v8.plan_control import (
        PlanInvalidationClassification,
        PlanInvalidationDisposition,
    )
    from test_v8_execution_kernel import _Effects, _active_campaign, _summary

    active, handle = _active_campaign(("issue:1",))
    effects = _Effects()

    class Plans:
        def __init__(self):
            self.calls = 0

        def read_active(self, requested):
            return active

        def classify_plan_invalidations(self, requested, invalidations, snapshot):
            self.calls += 1
            if self.calls == 1:
                return None
            evidence = tuple(sorted(item.evidence_digest for item in invalidations))
            return PlanInvalidationClassification(
                action_id="replan:" + digest_value(
                    {
                        "repository": handle.repository,
                        "campaign_key": handle.campaign_key,
                        "plan_revision_digest": active.current_revision_digest,
                        "evidence_digests": list(evidence),
                    }
                ),
                snapshot_digest="c" * 64,
                plan_revision_digest=active.current_revision_digest,
                evidence_digests=evidence,
                disposition=PlanInvalidationDisposition.RESUME_UNCHANGED,
                reason="The frozen contract remains sufficient.",
                capability_proof_digest="d" * 64,
            )

    kernel = ExecutionKernel(
        store_path=tmp_path / "multiple-invalidations.sqlite3",
        plan_control=Plans(),
        effects=effects,
    )
    kernel.advance(handle)
    binding = effects.executed[0].stable_action_id

    def observation(evidence_digest, report_digest, dedup_identity):
        return PlanInvalidationObservation(
            repository=handle.repository,
            campaign_key=handle.campaign_key,
            plan_revision_digest=active.current_revision_digest,
            ticket_key="issue:1",
            work_run_key="work-run:issue:1",
            runtime_binding_id=binding,
            authority_subtree_digest="a" * 64,
            reporter_role="worker",
            report_digest=report_digest,
            evidence_digest=evidence_digest,
            dedup_identity=dedup_identity,
            invalidated_obligation="issue:1 obligation",
            required_effects=("workspace.write.v1",),
            workspace_identity="workspace:issue:1",
        )

    first = observation("e" * 64, "1" * 64, "first")
    second = observation("f" * 64, "2" * 64, "second")

    first_outcome = kernel.advance(handle, plan_invalidation=first)
    assert first_outcome.status.value == "Decision"
    assert _summary(kernel, handle, "issue:1").phase == "quiescent"

    second_outcome = kernel.advance(handle, plan_invalidation=second)
    assert second_outcome.status.value == "Running"
    assert kernel.inspect(handle).invalidation_classification is not None
