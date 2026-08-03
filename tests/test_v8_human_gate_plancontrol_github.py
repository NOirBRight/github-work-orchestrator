from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "skills" / "orchestrator" / "scripts", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from test_v8_plancontrol_production import _RefContentClient
from test_v8_human_gate_plancontrol import (
    _ApprovedSource,
    _approved_readback,
    _decision_setup,
    _pending_readback,
)
from gwo_v8._canonical import digest_value


def _github_repository(client):
    from gwo_v8.plan_control_github import GitHubPlanRepository

    return GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
        maximum_state_bytes=16_384,
    )


def test_github_human_decision_and_readback_survive_restart_in_separate_categories():
    from gwo_v8.human_gate import HumanDecisionChoice

    control, _memory, _gateway, _artifacts, _source, _host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    choice = HumanDecisionChoice(
        decision.decision_id,
        "approve",
        "workflow://approval/one",
    )
    readback = _pending_readback(decision.decision_id)
    client = _RefContentClient()
    repository = _github_repository(client)

    assert repository.save_human_decision(decision) == decision
    assert repository.save_human_gate_readback(
        handle, decision, choice, readback
    ) == readback

    restarted = _github_repository(client)
    assert restarted.read_human_decision(handle, decision.decision_id) == decision
    assert restarted.read_human_gate_choice(handle, decision.decision_id) == choice
    assert restarted.read_human_gate_readback(handle, decision.decision_id) == readback


def test_github_human_decision_duplicate_is_exact_or_conflict():
    from dataclasses import replace
    from gwo_v8.plan_control import PlanControlError

    control, _memory, _gateway, _artifacts, _source, _host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    repository = _github_repository(_RefContentClient())
    repository.save_human_decision(decision)

    assert repository.save_human_decision(decision) == decision
    with pytest.raises(PlanControlError) as error:
        repository.save_human_decision(replace(decision, detail="changed"))
    assert error.value.code == "HUMAN_DECISION_CONFLICT"


def test_github_human_gate_attempt_is_exactly_durable_across_duplicate_and_restart():
    from dataclasses import replace

    from gwo_v8.human_gate import HumanDecisionChoice, HumanGateAttempt
    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.planning_protocol import REPLANNING_OUTPUT_PROTOCOL_ID

    control, memory, gateway, _artifacts, _source, _host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    source_readback = _approved_readback(control, handle, decision)
    planning_action_id = "replan:human:" + digest_value(
        {
            "decision_id": decision.decision_id,
            "source_readback_digest": source_readback.readback_digest,
            "previous_revision_digest": decision.plan_revision_digest,
        }
    )[:24]
    attempt = HumanGateAttempt(
        decision_id=decision.decision_id,
        campaign=handle,
        predecessor_revision_digest=decision.plan_revision_digest,
        source_readback_digest=source_readback.readback_digest,
        tracker_source_digest=source_readback.tracker_source_digest,
        policy_witness_digest=source_readback.policy_witness_digest,
        planning_action_id=planning_action_id,
        planning_protocol_id=REPLANNING_OUTPUT_PROTOCOL_ID,
        state="planning_validated_successor",
        compilation_record_artifact_digest=None,
        activation_receipt_digest=None,
    )
    client = _RefContentClient()
    repository = _github_repository(client)

    assert repository.save_human_decision(decision) == decision
    assert repository.save_human_gate_readback(
        handle,
        decision,
        HumanDecisionChoice(decision.decision_id, "approve", "workflow://approval/one"),
        source_readback,
    ) == source_readback
    assert repository.save_human_gate_attempt(attempt) == attempt
    assert repository.save_human_gate_attempt(attempt) == attempt

    from v8_successor_test_support import successor_payload

    gateway.payload = successor_payload(
        dependencies=(
            (
                "issue:109",
                "issue:110",
                "The approved authority permits the successor dependency.",
            ),
        )
    )
    control._human_source = _ApprovedSource(source_readback)
    control.advance_human_decision(
        handle,
        decision,
        HumanDecisionChoice(decision.decision_id, "approve", "workflow://approval/one"),
    )
    generic_attempt = memory.read_attempt(
        handle,
        decision.plan_revision_digest,
    )
    assert generic_attempt is not None
    assert generic_attempt.compilation_record_artifact_digest is not None
    assert repository.save_attempt(generic_attempt) == generic_attempt
    progressed = replace(
        attempt,
        state="planning_validated_successor",
        compilation_record_artifact_digest=generic_attempt.compilation_record_artifact_digest,
    )
    assert repository.save_human_gate_attempt(progressed) == progressed
    with pytest.raises(PlanControlError) as error:
        repository.save_human_gate_attempt(
            replace(progressed, tracker_source_digest="1" * 64)
        )
    assert error.value.code == "HUMAN_GATE_ATTEMPT_READBACK_INVALID"

    restarted = _github_repository(client)
    assert restarted.read_human_gate_attempt(
        handle,
        attempt.decision_id,
        attempt.source_readback_digest,
    ) == progressed


def test_github_hydration_rejects_an_attempt_whose_source_digest_is_tampered():
    from gwo_v8.human_gate import HumanDecisionChoice, HumanGateAttempt
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControlError
    from gwo_v8.plan_control_github import _repo_from_state, _repo_value
    from gwo_v8.planning_protocol import REPLANNING_OUTPUT_PROTOCOL_ID

    control, _memory, _gateway, _artifacts, _source, _host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    source = _approved_readback(control, handle, decision)
    action_id = "replan:human:" + digest_value(
        {
            "decision_id": decision.decision_id,
            "source_readback_digest": source.readback_digest,
            "previous_revision_digest": decision.plan_revision_digest,
        }
    )[:24]
    attempt = HumanGateAttempt(
        decision_id=decision.decision_id,
        campaign=handle,
        predecessor_revision_digest=decision.plan_revision_digest,
        source_readback_digest=source.readback_digest,
        tracker_source_digest=source.tracker_source_digest,
        policy_witness_digest=source.policy_witness_digest,
        planning_action_id=action_id,
        planning_protocol_id=REPLANNING_OUTPUT_PROTOCOL_ID,
        state="planning_validated_successor",
        compilation_record_artifact_digest=None,
        activation_receipt_digest=None,
    )
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    repository.save_human_decision(decision)
    repository.save_human_gate_readback(
        handle,
        decision,
        HumanDecisionChoice(decision.decision_id, "approve", "workflow://approval/one"),
        source,
    )
    repository.save_human_gate_attempt(attempt)
    state = _repo_value(handle.repository, "writer:one", repository)
    entry = next(
        item
        for item in state["human_gate_attempts"]
        if item.get("kind") == "gwo.human-gate-attempt-entry.v1"
    )
    entry["attempt"]["tracker_source_digest"] = "0" * 64

    with pytest.raises(PlanControlError) as error:
        _repo_from_state(state, handle.repository, "writer:one")
    assert error.value.code == "DURABLE_STATE_INVALID"


def test_github_save_human_gate_attempt_requires_durable_approved_choice_and_source():
    from gwo_v8.human_gate import HumanDecisionChoice, HumanGateAttempt
    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.planning_protocol import REPLANNING_OUTPUT_PROTOCOL_ID

    control, _repository, _gateway, _artifacts, _source, _host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    approved = _approved_readback(control, handle, decision)
    action_id = "replan:human:" + digest_value(
        {
            "decision_id": decision.decision_id,
            "source_readback_digest": approved.readback_digest,
            "previous_revision_digest": decision.plan_revision_digest,
        }
    )[:24]
    attempt = HumanGateAttempt(
        decision_id=decision.decision_id,
        campaign=handle,
        predecessor_revision_digest=decision.plan_revision_digest,
        source_readback_digest=approved.readback_digest,
        tracker_source_digest=approved.tracker_source_digest,
        policy_witness_digest=approved.policy_witness_digest,
        planning_action_id=action_id,
        planning_protocol_id=REPLANNING_OUTPUT_PROTOCOL_ID,
        state="planning_validated_successor",
        compilation_record_artifact_digest=None,
        activation_receipt_digest=None,
    )

    missing_repository = _github_repository(_RefContentClient())
    with pytest.raises(PlanControlError) as error:
        missing_repository.save_human_gate_attempt(attempt)
    assert error.value.code == "HUMAN_GATE_ATTEMPT_READBACK_INVALID"

    pending_client = _RefContentClient()
    pending_repository = _github_repository(pending_client)
    pending_repository.save_human_decision(decision)
    pending_repository.save_human_gate_readback(
        handle,
        decision,
        HumanDecisionChoice(decision.decision_id, "approve", "workflow://approval/one"),
        _pending_readback(decision.decision_id),
    )
    with pytest.raises(PlanControlError) as error:
        pending_repository.save_human_gate_attempt(attempt)
    assert error.value.code == "HUMAN_GATE_ATTEMPT_READBACK_INVALID"

    rejected_client = _RefContentClient()
    rejected_repository = _github_repository(rejected_client)
    rejected_repository.save_human_decision(decision)
    rejected_repository.save_human_gate_readback(
        handle,
        decision,
        HumanDecisionChoice(decision.decision_id, "reject", "workflow://approval/one"),
        approved,
    )
    with pytest.raises(PlanControlError) as error:
        rejected_repository.save_human_gate_attempt(attempt)
    assert error.value.code == "HUMAN_GATE_ATTEMPT_READBACK_INVALID"


def test_github_save_human_gate_attempt_requires_matching_generic_planning_attempt():
    from gwo_v8.human_gate import HumanDecisionChoice, HumanGateAttempt
    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.planning_protocol import REPLANNING_OUTPUT_PROTOCOL_ID

    control, _repository, _gateway, _artifacts, _source, _host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    source = _approved_readback(control, handle, decision)
    attempt = HumanGateAttempt(
        decision_id=decision.decision_id,
        campaign=handle,
        predecessor_revision_digest=decision.plan_revision_digest,
        source_readback_digest=source.readback_digest,
        tracker_source_digest=source.tracker_source_digest,
        policy_witness_digest=source.policy_witness_digest,
        planning_action_id="replan:human:"
        + digest_value(
            {
                "decision_id": decision.decision_id,
                "source_readback_digest": source.readback_digest,
                "previous_revision_digest": decision.plan_revision_digest,
            }
        )[:24],
        planning_protocol_id=REPLANNING_OUTPUT_PROTOCOL_ID,
        state="planning_validated_successor",
        compilation_record_artifact_digest="f" * 64,
        activation_receipt_digest=None,
    )
    repository = _github_repository(_RefContentClient())
    repository.save_human_decision(decision)
    repository.save_human_gate_readback(
        handle,
        decision,
        HumanDecisionChoice(decision.decision_id, "approve", "workflow://approval/one"),
        source,
    )

    with pytest.raises(PlanControlError) as error:
        repository.save_human_gate_attempt(attempt)
    assert error.value.code == "HUMAN_GATE_ATTEMPT_READBACK_INVALID"


def test_github_new_format_attempt_hydration_requires_generic_planning_attempt():
    from gwo_v8.human_gate import HumanDecisionChoice, HumanGateAttempt
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControlError
    from gwo_v8.plan_control_github import _repo_from_state, _repo_value
    from gwo_v8.planning_protocol import REPLANNING_OUTPUT_PROTOCOL_ID

    control, _repository, _gateway, _artifacts, _source, _host, handle, _harness, classification = (
        _decision_setup()
    )
    decision = control.require_human_decision(handle, classification)
    source = _approved_readback(control, handle, decision)
    attempt = HumanGateAttempt(
        decision_id=decision.decision_id,
        campaign=handle,
        predecessor_revision_digest=decision.plan_revision_digest,
        source_readback_digest=source.readback_digest,
        tracker_source_digest=source.tracker_source_digest,
        policy_witness_digest=source.policy_witness_digest,
        planning_action_id="replan:human:"
        + digest_value(
            {
                "decision_id": decision.decision_id,
                "source_readback_digest": source.readback_digest,
                "previous_revision_digest": decision.plan_revision_digest,
            }
        )[:24],
        planning_protocol_id=REPLANNING_OUTPUT_PROTOCOL_ID,
        state="planning_validated_successor",
        compilation_record_artifact_digest="f" * 64,
        activation_receipt_digest=None,
    )
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    repository.save_human_decision(decision)
    repository.save_human_gate_readback(
        handle,
        decision,
        HumanDecisionChoice(decision.decision_id, "approve", "workflow://approval/one"),
        source,
    )
    state = _repo_value(handle.repository, "writer:one", repository)
    assert state["schema_version"] == "gwo.plan.github-state.v5"
    assert state["attempts"] == []
    state["human_gate_attempts"].append(
        {
            "kind": "gwo.human-gate-attempt-entry.v1",
            "campaign_key": handle.campaign_key,
            "decision_id": decision.decision_id,
            "source_readback_digest": source.readback_digest,
            "attempt": attempt.canonical(),
        }
    )

    with pytest.raises(PlanControlError) as error:
        _repo_from_state(state, handle.repository, "writer:one")
    assert error.value.code == "DURABLE_STATE_INVALID"
