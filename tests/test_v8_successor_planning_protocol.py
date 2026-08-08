from __future__ import annotations

from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

pytest_plugins = ("v8_successor_test_support",)


def _expected_completed_result(effects, ticket_key: str):
    from v8_production_test_support import make_completed_observation

    action = next(
        action
        for action in effects.executed
        if action.kind == "batch_delivery" and action.ticket_key == ticket_key
    )
    observation = make_completed_observation(
        action,
        evidence_digests=("8" * 64,),
    )
    return observation.result_digest, observation.evidence_digests


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


@pytest.mark.parametrize("mutation", ("missing", "invalid"))
def test_github_attempt_decoder_rejects_missing_or_invalid_protocol(mutation):
    from gwo_v8.plan_control import CampaignHandle, _PlanningAttempt, PlanControlError
    from gwo_v8.plan_control_github import _attempt_from, _attempt_value
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
    encoded = _attempt_value(
        _PlanningAttempt(
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
    )
    if mutation == "missing":
        del encoded["planning_protocol_id"]
    else:
        encoded["planning_protocol_id"] = "campaign.unknown-output.v1"

    with pytest.raises(PlanControlError) as raised:
        _attempt_from(encoded)

    assert raised.value.code == "PLANNING_ATTEMPT_PROTOCOL_INVALID"


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


def test_shared_successor_support_exposes_the_complete_fixture_surface():
    import v8_successor_test_support as support

    methods = {
        "invalidation_for",
        "active_plan",
        "ledger_snapshot",
        "set_successor_payload",
        "arm_crash",
        "arm_activation_readback_tamper",
        "mutate_source",
        "install_competing_successor",
        "reinstall",
    }
    assert methods.issubset(vars(support.SuccessorHarness))
    assert callable(support.ScriptedPlanningGateway().planning_preflight)
    assert callable(support.ScriptedPlanningGateway().progress)
    assert callable(support.RevisionBoundEffects().replay_predecessor_candidate)
    assert callable(support.CrashBoundaryRepository().save_attempt)
    assert issubclass(support.InjectedCrash, RuntimeError)
    assert all(
        name in vars(support)
        for name in (
            "successor_control",
            "github_successor_repository",
            "kernel_with_one_ticket",
            "kernel_with_completed_result",
            "successor_kernel",
            "successor_host",
            "public_successor",
            "public_dependency_successor",
        )
    )


def test_three_ticket_replanning_builder_freezes_execution_and_source_facts():
    from v8_successor_test_support import three_ticket_replanning_snapshot

    snapshot = three_ticket_replanning_snapshot()
    tickets = {item["key"]: item for item in snapshot["tickets"]}
    runs = {item["ticket_key"]: item for item in snapshot["work_runs"]}
    claims = {item["ticket_key"]: item for item in snapshot["claims"]}

    assert tuple(tickets) == ("issue:108", "issue:109", "issue:110")
    assert snapshot["campaign_source"] == {
        "repository": "owner/repository",
        "input_ref": "refs/heads/main",
        "resolved_commit_oid": "a" * 40,
        "tree_oid": "b" * 40,
        "digest": snapshot["campaign_source"]["digest"],
    }
    assert snapshot["policy_witness"] == snapshot["policy"]
    assert tickets["issue:109"]["native_blockers"][0]["key"] == "issue:900"
    assert tickets["issue:109"]["native_blockers"][0]["state"] == "closed"
    assert runs["issue:108"]["phase"] == "completed"
    assert runs["issue:108"]["result_digest"] == "7" * 64
    assert runs["issue:108"]["evidence_digests"] == ["8" * 64]
    assert runs["issue:109"]["phase"] == "quiescent"
    assert runs["issue:109"]["candidate_identity"] == "candidate:r0:109"
    assert runs["issue:110"]["phase"] == "pending"
    assert claims["issue:109"]["plan_revision_digest"] == snapshot["plan_revision_digest"]
    accepted = snapshot["accepted_results"]
    assert len(accepted) == 1
    assert accepted[0]["kind"] == "accepted_result_binding.v1"
    assert accepted[0]["ticket_key"] == "issue:108"
    assert accepted[0]["result_digest"] == "7" * 64
    assert accepted[0]["evidence_digests"] == ["8" * 64]
    assert accepted[0]["work_subject_digest"] == runs["issue:108"]["work_subject_digest"]
    assert len(accepted[0]["target_facts_digest"]) == 64
    assert snapshot["external_dependencies"] == [
        {
            "key": "issue:900",
            "state": "closed",
            "repository": "owner/repository",
            "source": tickets["issue:109"]["native_blockers"][0]["source"],
        }
    ]


def test_successor_harness_source_mutation_is_delayed_until_following_readback():
    import v8_successor_test_support as support

    _control, _repository, _gateway, _artifacts, source, _host, _handle, harness = (
        support._direct_setup()
    )
    frozen = source.snapshot(
        "owner/repository",
        ("issue:108", "issue:109", "issue:110"),
    )

    harness.mutate_source("target_branch")

    first_readback = source.snapshot(
        "owner/repository",
        ("issue:108", "issue:109", "issue:110"),
    )
    second_readback = source.snapshot(
        "owner/repository",
        ("issue:108", "issue:109", "issue:110"),
    )

    assert first_readback == frozen
    assert second_readback["target_branch"] == "release"


@pytest.mark.parametrize("fixture_name", ("public_successor", "public_dependency_successor"))
def test_public_successor_fixtures_use_github_repository_and_production_host(
    request,
    fixture_name,
):
    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.plan_control_host import ProductionPlanControlStartHost

    harness = request.getfixturevalue(fixture_name)

    assert isinstance(harness.repository, GitHubPlanRepository)
    assert isinstance(harness.host, ProductionPlanControlStartHost)


def test_public_successor_fixture_installs_exported_start_advance_and_inspect(
    public_successor,
):
    import gwo_v8
    import gwo_v8.plan_control as plan_control

    assert plan_control._default_start_host is public_successor.host
    outcome = gwo_v8.advance(public_successor.handle)
    diagnostics = gwo_v8.inspect(public_successor.handle)
    runs = {run.ticket_key: run for run in diagnostics.work_runs}

    assert outcome.status == diagnostics.status
    assert runs["issue:108"].phase == "completed"
    assert public_successor.effects.completed_results["issue:108"] == (
        _expected_completed_result(public_successor.effects, "issue:108")
    )
    assert public_successor.effects.candidate_identities["issue:109"] == (
        "candidate:r0:109"
    )
    assert runs["issue:110"].phase not in {"completed", "failed"}


def test_public_successor_reinstall_recomposes_host_and_exported_kernel(
    public_successor,
):
    import gwo_v8
    import gwo_v8.plan_control as plan_control
    from gwo_v8.plan_control_host import ProductionPlanControlStartHost

    old_host = public_successor.host
    old_kernel = public_successor._kernel
    old_repository = public_successor.repository
    old_gateway = public_successor.gateway
    old_effects = public_successor.effects

    public_successor.reinstall()

    assert public_successor.host is not old_host
    assert public_successor._kernel is not old_kernel
    assert public_successor.repository is old_repository
    assert public_successor.gateway is old_gateway
    assert public_successor.effects is old_effects
    assert isinstance(public_successor.host, ProductionPlanControlStartHost)
    assert plan_control._default_start_host is public_successor.host

    diagnostics = gwo_v8.inspect(public_successor.handle)
    runs = {run.ticket_key: run for run in diagnostics.work_runs}
    assert public_successor.effects.completed_results["issue:108"] == (
        _expected_completed_result(public_successor.effects, "issue:108")
    )
    assert public_successor.effects.candidate_identities["issue:109"] == (
        "candidate:r0:109"
    )
