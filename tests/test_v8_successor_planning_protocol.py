from __future__ import annotations

from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

pytest_plugins = ("v8_successor_test_support",)


def test_successor_schema_requires_closed_resource_additions():
    from gwo_v8.planning_protocol import replanning_output_payload_schema

    schema = replanning_output_payload_schema()
    successor = schema["properties"]["successor"]
    assert successor["required"] == [
        "approved_ticket_keys",
        "dependency_additions",
        "exclusive_resource_additions",
    ]
    resource = successor["properties"]["exclusive_resource_additions"]["items"]
    assert resource["required"] == ["ticket_key", "resource_id", "reason"]
    assert resource["additionalProperties"] is False


def test_classification_resource_round_trip_is_canonical():
    from gwo_v8.plan_control import (
        PlanInvalidationClassification,
        PlanInvalidationDisposition,
        PlanInvalidationExclusiveResource,
    )

    classification = PlanInvalidationClassification(
        action_id="replan:one",
        snapshot_digest="1" * 64,
        plan_revision_digest="2" * 64,
        evidence_digests=("3" * 64,),
        disposition=PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR,
        reason="The approved owner needs one serialized target resource.",
        capability_proof_digest="4" * 64,
        successor_ticket_keys=("issue:110",),
        exclusive_resource_additions=(
            PlanInvalidationExclusiveResource(
                ticket_key="issue:110",
                resource_id="repository.target.v1",
                reason="The owner writes the shared target.",
            ),
        ),
    )
    assert PlanInvalidationClassification.from_canonical(
        classification.canonical()
    ) == classification


def test_planning_attempt_protocol_is_explicit():
    from gwo_v8.plan_control import (
        CampaignHandle,
        PlanControlError,
        _PlanningAttempt,
    )

    from gwo_v8.runtime_gateway import CampaignPlanningSubject

    subject = CampaignPlanningSubject(
        repository="owner/repository",
        campaign_key="campaign:one",
        campaign_handle="campaign:one",
        expected_previous_plan_revision_digest=None,
        snapshot_artifact_digest="1" * 64,
        policy_witness_digest="2" * 64,
        planning_request_artifact_digest="3" * 64,
        stable_action_id="planning:one",
    )
    attempt = _PlanningAttempt(
        handle=CampaignHandle("owner/repository", "campaign:one"),
        ready_refs=("issue:108",),
        ticket_keys=("issue:108",),
        expected_previous_revision_digest=None,
        snapshot_bytes=b"{}",
        snapshot_artifact_digest="1" * 64,
        policy_witness_digest="2" * 64,
        planning_request_artifact_digest="3" * 64,
        subject=subject,
    )
    assert attempt.planning_protocol_id == "campaign.planning-output.v1"

    successor = _PlanningAttempt(
        handle=attempt.handle,
        ready_refs=attempt.ready_refs,
        ticket_keys=attempt.ticket_keys,
        expected_previous_revision_digest="4" * 64,
        snapshot_bytes=attempt.snapshot_bytes,
        snapshot_artifact_digest=attempt.snapshot_artifact_digest,
        policy_witness_digest=attempt.policy_witness_digest,
        planning_request_artifact_digest=attempt.planning_request_artifact_digest,
        subject=subject,
        planning_protocol_id="campaign.plan-invalidation-output.v1",
    )
    assert successor.planning_protocol_id == "campaign.plan-invalidation-output.v1"

    with pytest.raises(PlanControlError) as raised:
        _PlanningAttempt(
            handle=attempt.handle,
            ready_refs=attempt.ready_refs,
            ticket_keys=attempt.ticket_keys,
            expected_previous_revision_digest=None,
            snapshot_bytes=attempt.snapshot_bytes,
            snapshot_artifact_digest=attempt.snapshot_artifact_digest,
            policy_witness_digest=attempt.policy_witness_digest,
            planning_request_artifact_digest=attempt.planning_request_artifact_digest,
            subject=subject,
            planning_protocol_id="campaign.unknown-output.v1",
        )
    assert raised.value.code == "PLANNING_ATTEMPT_PROTOCOL_INVALID"


def test_planning_attempt_protocol_survives_github_state_round_trip():
    from gwo_v8.plan_control import CampaignHandle, _PlanningAttempt
    from gwo_v8.plan_control_github import _attempt_from, _attempt_value
    from gwo_v8.runtime_gateway import CampaignPlanningSubject

    subject = CampaignPlanningSubject(
        repository="owner/repository",
        campaign_key="campaign:one",
        campaign_handle="campaign:one",
        expected_previous_plan_revision_digest="4" * 64,
        snapshot_artifact_digest="1" * 64,
        policy_witness_digest="2" * 64,
        planning_request_artifact_digest="3" * 64,
        stable_action_id="replan:one",
    )
    attempt = _PlanningAttempt(
        handle=CampaignHandle("owner/repository", "campaign:one"),
        ready_refs=("issue:108",),
        ticket_keys=("issue:108",),
        expected_previous_revision_digest="4" * 64,
        snapshot_bytes=b"{}",
        snapshot_artifact_digest="1" * 64,
        policy_witness_digest="2" * 64,
        planning_request_artifact_digest="3" * 64,
        subject=subject,
        planning_protocol_id="campaign.plan-invalidation-output.v1",
    )
    encoded = _attempt_value(attempt)
    assert encoded["planning_protocol_id"] == "campaign.plan-invalidation-output.v1"
    assert _attempt_from(encoded) == attempt


def test_non_successor_disposition_rejects_resource_additions():
    from gwo_v8.plan_control import (
        PlanControlError,
        PlanInvalidationClassification,
        PlanInvalidationDisposition,
        PlanInvalidationExclusiveResource,
    )

    with pytest.raises(PlanControlError) as raised:
        PlanInvalidationClassification(
            action_id="replan:one",
            snapshot_digest="1" * 64,
            plan_revision_digest="2" * 64,
            evidence_digests=("3" * 64,),
            disposition=PlanInvalidationDisposition.RESUME_UNCHANGED,
            reason="No PlanSpec change is needed.",
            capability_proof_digest="4" * 64,
            exclusive_resource_additions=(
                PlanInvalidationExclusiveResource(
                    ticket_key="issue:110",
                    resource_id="repository.target.v1",
                    reason="The owner serializes target writes.",
                ),
            ),
        )
    assert raised.value.code == "PLAN_INVALIDATION_CLASSIFICATION_INVALID"


def test_shared_successor_builders_are_canonical_and_independent():
    from v8_successor_test_support import (
        active_plan_spec,
        changed_plan_spec,
        successor_classification_value,
        successor_payload,
        three_ticket_replanning_snapshot,
    )

    snapshot = three_ticket_replanning_snapshot()
    assert [item["key"] for item in snapshot["active_plan_revision"]["plan_spec"]["work"]] == [
        "issue:108",
        "issue:109",
        "issue:110",
    ]
    payload = successor_payload(
        dependencies=(("issue:109", "issue:110", "owner result"),),
        resources=(("issue:110", "repository.target.v1", "shared target"),),
    )
    assert list(payload["successor"]) == [
        "approved_ticket_keys",
        "dependency_additions",
        "exclusive_resource_additions",
    ]
    classification = successor_classification_value(
        dependencies=(("issue:109", "issue:110", "owner result"),),
        resources=(("issue:110", "repository.target.v1", "shared target"),),
    )
    assert classification.dependency_additions[0].from_ticket == "issue:109"
    assert classification.exclusive_resource_additions[0].resource_id == (
        "repository.target.v1"
    )
    assert active_plan_spec() != changed_plan_spec("target_branch")
