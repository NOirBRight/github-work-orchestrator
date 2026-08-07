from __future__ import annotations

from dataclasses import replace
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from v8_production_test_support import (
    action,
    delivery_action,
    make_production_effects,
    support,
)


def test_production_effects_requires_the_merged_candidate_and_batch_ports(tmp_path):
    from gwo_v8.production_effects import (
        ProductionCompositionError,
        ProductionWorkRunEffects,
    )

    with pytest.raises(ProductionCompositionError) as raised:
        ProductionWorkRunEffects(
            store_path=tmp_path / "effects.sqlite3",
            runtime_gateways=object(),
            runtime_stale_readbacks=object(),
            work_run_subjects=object(),
            candidate_references=object(),
            candidate_parents=object(),
            candidate_gate=object(),
            batch_requests=object(),
            batch_integrator=object(),
        )
    assert raised.value.code == "PRODUCTION_COMPOSITION_INPUT_INVALID"


def test_runtime_completion_enters_candidate_gate_not_completed_result(
    tmp_path,
    action,
    support,
):
    support.runtime.receipt = support.runtime_completed_receipt(action)
    support.candidate.result = support.accepted_candidate_result(action)
    effects = make_production_effects(tmp_path, support)

    observation = effects.execute(action)

    assert observation.phase == "accepted_awaiting_delivery"
    assert observation.runtime_binding_id == action.stable_action_id
    assert (
        observation.accepted_candidate_receipt_digest
        == support.candidate.result.accepted_candidate_receipt.digest
    )
    assert observation.candidate_receipt == support.candidate.result.candidate_receipt
    assert observation.result_integrity is None
    assert support.runtime.calls == [("progress", action.stable_action_id)]
    assert support.candidate.calls == [(action.stable_action_id, "gate_candidate")]
    assert support.batch.prepare_calls == 0


def test_candidate_commit_mismatch_is_rejected_before_batch_side_effects(
    tmp_path,
    action,
    support,
):
    support.runtime.receipt = support.runtime_completed_receipt(action)
    result = support.accepted_candidate_result(action)
    assert result.accepted_candidate_receipt is not None
    support.candidate.result = replace(
        result,
        accepted_candidate_receipt=replace(
            result.accepted_candidate_receipt,
            candidate_sha="6" * 40,
        ),
    )
    effects = make_production_effects(tmp_path, support)

    from gwo_v8.production_effects import ProductionCompositionError

    with pytest.raises(ProductionCompositionError) as raised:
        effects.execute(action)

    assert raised.value.code == "CANDIDATE_GATE_READBACK_INVALID"
    assert support.batch.prepare_calls == 0
    assert support.batch.execute_calls == 0


def test_quiescent_semantic_observation_requires_plan_invalidation(
    action,
):
    from gwo_v8.execution_kernel import WorkRunObservation
    from gwo_v8.production_effects import ProductionCompositionError, ProductionWorkRunEffects

    observation = WorkRunObservation(
        phase="quiescent",
        stable_action_id=action.stable_action_id,
        runtime_binding_id=action.stable_action_id,
        receipt_digest="1" * 64,
    )

    with pytest.raises(ProductionCompositionError) as raised:
        ProductionWorkRunEffects._validate_effect_observation(action, observation)

    assert raised.value.code == "EFFECT_READBACK_INVALID"


def test_batch_delivery_maps_only_exact_complete_receipt_to_completed(
    tmp_path,
    delivery_action,
    support,
):
    semantic_action = replace(
        delivery_action,
        stable_action_id="action:109",
        kind="semantic_execution",
        runtime_binding_id=None,
        wake_ref="runtime:completed",
        accepted_candidate_receipt_digest=None,
    )
    support.runtime.receipt = support.runtime_completed_receipt(semantic_action)
    support.candidate.result = support.accepted_candidate_result(semantic_action)
    support.batch.observation = support.complete_batch_observation(delivery_action)
    effects = make_production_effects(tmp_path, support)
    effects.execute(semantic_action)

    observation = effects.execute(delivery_action)

    assert observation.phase == "completed"
    assert observation.result_integrity is not None
    assert observation.result_digest == observation.result_integrity.result_digest
    assert observation.delivery_receipt_digest == support.batch.observation.receipt_digest
    assert len(support.batch.observation.delivery_proofs) == 1
    delivered = support.batch.observation.delivery_proofs[0]
    assert observation.result_integrity.delivery_proof_body() == delivered.body()
    assert (
        observation.result_integrity.batch_delivery_proof_digest
        == delivered.proof_digest
    )
    assert (
        observation.result_integrity.target_head_sha
        != support.batch_requests.request.target.target_head_sha
    )
    assert support.batch.prepare_calls == 1
    assert support.batch.execute_calls == 1


def test_effect_readback_does_not_call_runtime_candidate_or_batch(
    tmp_path,
    action,
    support,
):
    effects = make_production_effects(tmp_path, support)

    assert effects.readback(action) is None
    assert support.all_calls == []


def test_scope_escape_returns_plan_invalidation_observation_without_delivery(
    tmp_path,
    action,
    support,
):
    support.runtime.receipt = support.runtime_completed_receipt(action)
    support.candidate.result = support.plan_invalidation_result(action)
    effects = make_production_effects(tmp_path, support)

    observation = effects.execute(action)

    assert observation.phase == "quiescent"
    assert observation.plan_invalidation is not None
    assert support.batch.prepare_calls == 0
    assert support.batch.execute_calls == 0


def test_repeated_runtime_execution_uses_the_persisted_effect_readback(
    tmp_path,
    action,
    support,
):
    support.runtime.receipt = support.runtime_completed_receipt(action)
    support.candidate.result = support.accepted_candidate_result(action)
    effects = make_production_effects(tmp_path, support)

    first = effects.execute(action)
    second = effects.execute(action)
    restarted = make_production_effects(tmp_path, support)
    third = restarted.execute(action)

    assert second == first
    assert third == first
    assert support.runtime.calls == [("progress", action.stable_action_id)]
    assert support.candidate.calls == [(action.stable_action_id, "gate_candidate")]


def test_repeated_batch_execution_uses_the_persisted_delivery_readback(
    tmp_path,
    delivery_action,
    support,
):
    semantic_action = replace(
        delivery_action,
        stable_action_id="action:109",
        kind="semantic_execution",
        runtime_binding_id=None,
        wake_ref="runtime:completed",
        accepted_candidate_receipt_digest=None,
    )
    support.runtime.receipt = support.runtime_completed_receipt(semantic_action)
    support.candidate.result = support.accepted_candidate_result(semantic_action)
    support.batch.observation = support.complete_batch_observation(delivery_action)
    effects = make_production_effects(tmp_path, support)
    effects.execute(semantic_action)

    first = effects.execute(delivery_action)
    second = effects.execute(delivery_action)
    restarted = make_production_effects(tmp_path, support)
    third = restarted.execute(delivery_action)

    assert second == first
    assert third == first
    assert support.batch.prepare_calls == 1
    assert support.batch.execute_calls == 1


def test_batch_request_binding_reads_the_exact_persisted_candidate(
    tmp_path,
    delivery_action,
    support,
):
    semantic_action = replace(
        delivery_action,
        stable_action_id="action:109",
        kind="semantic_execution",
        runtime_binding_id=None,
        wake_ref="runtime:completed",
        accepted_candidate_receipt_digest=None,
    )
    support.runtime.receipt = support.runtime_completed_receipt(semantic_action)
    support.candidate.result = support.accepted_candidate_result(semantic_action)
    support.batch.observation = support.complete_batch_observation(delivery_action)
    effects = make_production_effects(tmp_path, support)
    effects.execute(semantic_action)

    unbound = replace(delivery_action, batch_delivery_request_digest=None)

    assert (
        effects.bind_batch_delivery_request_digest(unbound)
        == support.batch_requests.request.request_digest
    )
    assert support.batch.prepare_calls == 0
    assert support.batch.execute_calls == 0
