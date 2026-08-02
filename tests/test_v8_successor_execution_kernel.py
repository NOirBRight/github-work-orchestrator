from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_work_action_binds_revision_run_and_subject(tmp_path):
    from gwo_v8._canonical import canonical_bytes, digest_bytes, digest_value
    from gwo_v8.execution_kernel import ExecutionKernel

    active, handle = _active_campaign(("issue:109",))
    effects = _Effects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )

    kernel.advance(handle)

    action = effects.executed[0]
    assert action.plan_revision_digest == digest_bytes(active.plan_spec_bytes)
    assert action.work_run_key.startswith("work-run:")
    assert len(action.work_subject_digest) == 64
    assert action.stable_action_id == digest_value(
        {
            "kind": "work-run.semantic_execution.v1",
            "repository": handle.repository,
            "campaign_key": handle.campaign_key,
            "plan_revision_digest": active.current_revision_digest,
            "ticket_key": "issue:109",
            "work_run_key": action.work_run_key,
            "work_subject_digest": action.work_subject_digest,
            "ordinal": 0,
        }
    )


def test_completed_readback_persists_exact_result_and_evidence_binding(tmp_path):
    from gwo_v8._canonical import digest_value, load_canonical_json
    from gwo_v8.execution_kernel import ExecutionKernel

    active, handle = _active_campaign(("issue:108",))
    effects = _Effects(
        phase="completed",
        result_digest="7" * 64,
        evidence_digests=("8" * 64,),
    )
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )

    kernel.advance(handle)

    diagnostics = kernel.inspect(handle)
    run = diagnostics.work_runs[0]
    assert run.result_digest == "7" * 64
    assert run.evidence_digests == ("8" * 64,)
    plan = load_canonical_json(active.plan_spec_bytes)
    expected_target_facts_digest = digest_value(
        {
            "kind": "gwo.target-facts.v1",
            "repository": active.handle.repository,
            "target_branch": plan["target_branch"],
            "campaign_source": plan["campaign"]["source"],
        }
    )
    state = kernel._load(handle)
    assert state["accepted_results"] == [
        {
            "kind": "accepted_result_binding.v1",
            "ticket_key": "issue:108",
            "result_digest": "7" * 64,
            "evidence_digests": ["8" * 64],
            "work_subject_digest": run.work_subject_digest,
            "target_facts_digest": expected_target_facts_digest,
        }
    ]


def test_predecessor_candidate_readback_cannot_match_successor_action(tmp_path):
    from gwo_v8._canonical import digest_value
    from gwo_v8.execution_kernel import ExecutionKernel, ExecutionKernelError

    active, handle = _active_campaign(("issue:109",))
    stale_action_id = digest_value(
        {
            "kind": "work-run.semantic_execution.v1",
            "repository": handle.repository,
            "campaign_key": handle.campaign_key,
            "plan_revision_digest": active.current_revision_digest,
            "ticket_key": "issue:109",
            "ordinal": 0,
        }
    )
    effects = _Effects(
        readback_observation=_observation(
            "candidate_checks",
            stale_action_id,
            candidate_identity="candidate:r0:109",
        )
    )
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )

    with pytest.raises(ExecutionKernelError) as raised:
        kernel.advance(handle)

    assert raised.value.code == "EFFECT_READBACK_INVALID"
    assert effects.executed == []
    assert kernel.inspect(handle).work_runs[0].candidate_identity is None


def test_current_candidate_identity_is_inspectable_without_becoming_result(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    active, handle = _active_campaign(("issue:109",))
    effects = _Effects(
        phase="candidate_checks",
        candidate_identity="candidate:r0:109",
    )
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )

    kernel.advance(handle)

    run = kernel.inspect(handle).work_runs[0]
    assert run.candidate_identity == "candidate:r0:109"
    assert run.result_digest is None
    assert run.evidence_digests == ()
    assert kernel._load(handle)["accepted_results"] == []


def test_completed_readback_rejects_noncanonical_evidence(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel, ExecutionKernelError

    active, handle = _active_campaign(("issue:108",))
    effects = _Effects(
        phase="completed",
        result_digest="7" * 64,
        evidence_digests=("not-a-sha256-digest",),
    )
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )

    with pytest.raises(ExecutionKernelError) as raised:
        kernel.advance(handle)

    assert raised.value.code == "WORK_RUN_OBSERVATION_INVALID"
    assert kernel._load(handle)["accepted_results"] == []


def test_execution_snapshot_retains_exact_accepted_result_binding(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    active, handle = _active_campaign(("issue:108",))
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=_Effects(
            phase="completed",
            result_digest="7" * 64,
            evidence_digests=("8" * 64,),
        ),
    )

    kernel.advance(handle)
    state = kernel._load(handle)
    _active, work = kernel._authoritative_active(handle)

    snapshot = kernel._execution_snapshot(active, state, work)

    assert snapshot["accepted_results"] == state["accepted_results"]
    assert snapshot["accepted_results"][0] == {
        "kind": "accepted_result_binding.v1",
        "ticket_key": "issue:108",
        "result_digest": "7" * 64,
        "evidence_digests": ["8" * 64],
        "work_subject_digest": state["runs"]["issue:108"]["work_subject_digest"],
        "target_facts_digest": snapshot["accepted_results"][0]["target_facts_digest"],
    }


def test_historical_effect_intent_is_migrated_to_revision_bound_key_once(tmp_path):
    from gwo_v8._canonical import digest_value, load_canonical_json
    from gwo_v8.execution_kernel import (
        ExecutionKernel,
        _work_subject_digest_for_kernel,
    )

    active, handle = _active_campaign(("issue:109",))
    effects = _ReadBackOnlyEffects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    _active, work = kernel._authoritative_active(handle)
    state = kernel._load_or_initialize(active, work)
    run = state["runs"]["issue:109"]
    plan = load_canonical_json(active.plan_spec_bytes)
    legacy_action_id = digest_value(
        {
            "kind": "work-run.semantic_execution.v1",
            "repository": handle.repository,
            "campaign_key": handle.campaign_key,
            "plan_revision_digest": active.current_revision_digest,
            "ticket_key": "issue:109",
            "ordinal": 0,
        }
    )
    run.update(
        {
            "phase": "running",
            "slot_held": True,
            "last_action_id": legacy_action_id,
            "semantic_action_id": legacy_action_id,
            "work_run_key": "work-run:issue:109",
        }
    )
    run.pop("work_subject_digest", None)
    state["effects"] = {
        legacy_action_id: {
            "state": "read_back",
            "ticket_key": "issue:109",
            "receipt_digest": "9" * 64,
        }
    }
    kernel._save(handle, state)

    kernel.advance(handle, "historical-effect-readback")

    expected_subject = _work_subject_digest_for_kernel(plan, work["issue:109"])
    migrated = kernel._load(handle)
    expected_work_run = "work-run:issue:109"
    expected_action_id = digest_value(
        {
            "kind": "work-run.semantic_execution.v1",
            "repository": handle.repository,
            "campaign_key": handle.campaign_key,
            "plan_revision_digest": active.current_revision_digest,
            "ticket_key": "issue:109",
            "work_run_key": expected_work_run,
            "work_subject_digest": expected_subject,
            "ordinal": 0,
        }
    )

    assert effects.executed == []
    assert set(migrated["effects"]) == {expected_action_id}
    assert migrated["runs"]["issue:109"]["work_subject_digest"] == expected_subject
    assert migrated["runs"]["issue:109"]["work_run_key"] == expected_work_run
    assert migrated["runs"]["issue:109"]["last_action_id"] == expected_action_id
    assert migrated["effects"][expected_action_id]["state"] == "read_back"
    assert migrated["effects"][expected_action_id]["plan_revision_digest"] == (
        active.current_revision_digest
    )
    assert migrated["effects"][expected_action_id]["work_run_key"] == expected_work_run
    assert migrated["effects"][expected_action_id]["work_subject_digest"] == expected_subject

    kernel.advance(handle, "historical-effect-readback-again")
    assert effects.executed == []
    assert set(kernel._load(handle)["effects"]) == {expected_action_id}


def test_inspect_backfills_historical_subject_but_retains_legacy_key_without_effects(tmp_path):
    from gwo_v8._canonical import load_canonical_json
    from gwo_v8.execution_kernel import (
        ExecutionKernel,
        _work_subject_digest_for_kernel,
    )

    active, handle = _active_campaign(("issue:109",))
    effects = _ReadBackOnlyEffects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    _active, work = kernel._authoritative_active(handle)
    state = kernel._load_or_initialize(active, work)
    historical = state["runs"]["issue:109"]
    historical["work_run_key"] = "work-run:issue:109"
    historical.pop("work_subject_digest", None)
    kernel._save(handle, state)

    first = kernel.inspect(handle)
    second = kernel.inspect(handle)
    run = first.work_runs[0]
    expected_subject = _work_subject_digest_for_kernel(
        load_canonical_json(active.plan_spec_bytes), work["issue:109"]
    )
    expected_work_run = "work-run:issue:109"

    assert effects.executed == []
    assert run.work_subject_digest == expected_subject
    assert run.work_run_key == expected_work_run
    assert second.work_runs[0] == run
    assert kernel._load(handle)["runs"]["issue:109"]["work_run_key"] == expected_work_run


def test_historical_invalidation_record_keeps_legacy_work_run_key_on_advance(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel, PlanInvalidationObservation

    active, handle = _active_campaign(("issue:109",))
    effects = _ReadBackOnlyEffects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    _active, work = kernel._authoritative_active(handle)
    state = kernel._load_or_initialize(active, work)
    run = state["runs"]["issue:109"]
    legacy_work_run = "work-run:issue:109"
    run.update(
        {
            "phase": "quiescent",
            "slot_held": False,
            "semantic_action_id": "binding:historical",
            "work_run_key": legacy_work_run,
            "claim_state": "released",
        }
    )
    observation = PlanInvalidationObservation(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        plan_revision_digest=active.current_revision_digest,
        ticket_key="issue:109",
        work_run_key=legacy_work_run,
        runtime_binding_id="binding:historical",
        authority_subtree_digest=work["issue:109"]["authority"]["worker"]["subtree_digest"],
        reporter_role="worker",
        report_digest="a" * 64,
        evidence_digest="b" * 64,
        dedup_identity="historical-invalidation:one",
        invalidated_obligation="historical invalidation remains diagnostic",
        required_effects=("workspace.write.v1",),
        workspace_identity="workspace:historical",
    )
    record = observation.canonical()
    record.pop("kind")
    record["observation_digest"] = observation.digest
    dedup_key = kernel._scoped_dedup_key(observation)
    state["plan_invalidation"] = {dedup_key: record}
    kernel._save(handle, state)

    first = kernel.inspect(handle)
    assert first.work_runs[0].work_run_key == legacy_work_run
    assert kernel._load(handle)["plan_invalidation"][dedup_key] == record

    kernel.advance(handle, "historical-invalidation-replay")
    migrated = kernel._load(handle)
    assert migrated["runs"]["issue:109"]["work_run_key"] == legacy_work_run
    assert migrated["plan_invalidation"][dedup_key] == record
    assert effects.executed == []


def test_successor_transition_is_durable_before_activation(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    kernel, plans, effects, handle, invalidation = _successor_fixture(tmp_path)
    plans.kernel = kernel

    kernel.advance(handle, plan_invalidation=invalidation)

    expected = {
        "kind": "successor_transition.v1",
        "classification_action_id": plans.classification.action_id,
        "classification_digest": plans.classification.digest,
        "snapshot_digest": plans.classification.snapshot_digest,
        "previous_revision_digest": plans.predecessor.current_revision_digest,
        "evidence_digests": list(plans.classification.evidence_digests),
        "state": "activation_due",
    }
    assert plans.intent_at_activation == expected
    assert plans.activate_calls == 1


def test_exact_successor_readback_replaces_revision_once(tmp_path):
    from gwo_v8._canonical import digest_value

    kernel, plans, effects, handle, invalidation = _successor_fixture(tmp_path)

    kernel.advance(handle, plan_invalidation=invalidation)
    state = kernel._load(handle)

    assert plans.activate_calls == 1
    assert plans.active == plans.successor
    assert state["plan_revision_digest"] == plans.successor.current_revision_digest
    assert state["activation_receipt_digest"] == digest_value(
        plans.successor.activation_receipt.__dict__
    )
    assert len(state["revision_lineage"]) == 1
    assert state["runs"]["issue:109"]["phase"] == "running"

    kernel.advance(handle)

    assert plans.activate_calls == 1
    assert len(kernel._load(handle)["revision_lineage"]) == 1


def test_restart_after_activation_before_migration_rolls_forward(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel, ExecutionKernelError

    kernel, plans, effects, handle, invalidation = _successor_fixture(tmp_path)
    plans.kernel = kernel
    plans.return_value = object()

    with pytest.raises(ExecutionKernelError) as raised:
        kernel.advance(handle, plan_invalidation=invalidation)
    assert raised.value.code == "SUCCESSOR_ACTIVATION_READBACK_INVALID"
    assert kernel._load(handle)["plan_revision_digest"] == plans.predecessor.current_revision_digest
    assert plans.active == plans.successor

    restarted = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=plans,
        effects=effects,
    )
    plans.kernel = restarted
    plans.return_value = plans.successor

    restarted.advance(handle)

    migrated = restarted._load(handle)
    assert plans.activate_calls == 1
    assert migrated["plan_revision_digest"] == plans.successor.current_revision_digest
    assert len(migrated["revision_lineage"]) == 1


def test_affected_run_rekeys_and_unaffected_result_survives(tmp_path):
    kernel, plans, effects, handle, invalidation = _successor_fixture(tmp_path)
    before = kernel.inspect(handle)
    result_run = next(run for run in before.work_runs if run.ticket_key == "issue:108")
    affected_run = next(run for run in before.work_runs if run.ticket_key == "issue:109")

    kernel.advance(handle, plan_invalidation=invalidation)
    after = kernel.inspect(handle)
    retained = next(run for run in after.work_runs if run.ticket_key == "issue:108")
    replaced = next(run for run in after.work_runs if run.ticket_key == "issue:109")

    assert after.plan_revision_digest != before.plan_revision_digest
    assert retained.result_digest == result_run.result_digest
    assert retained.evidence_digests == result_run.evidence_digests
    assert retained.work_run_key == result_run.work_run_key
    assert replaced.work_run_key != affected_run.work_run_key
    assert replaced.phase in {"pending", "running"}


def test_old_workspace_and_candidate_are_lineage_only(tmp_path):
    kernel, plans, effects, handle, invalidation = _successor_fixture(tmp_path)

    kernel.advance(handle, plan_invalidation=invalidation)
    diagnostics = kernel.inspect(handle)
    current = next(run for run in diagnostics.work_runs if run.ticket_key == "issue:109")

    assert current.candidate_identity is None
    assert diagnostics.revision_lineage[0].candidate_identities == (
        "candidate:r0:109",
    )
    assert diagnostics.revision_lineage[0].workspace_identities == (
        "workspace:r0:109",
    )
    assert "candidate:r0:109" not in {
        run.candidate_identity for run in diagnostics.work_runs if run.candidate_identity
    }


def test_stale_candidate_never_enters_checks_review_or_delivery(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel, ExecutionKernelError

    kernel, plans, effects, handle, invalidation = _successor_fixture(tmp_path)
    stale_action_id = kernel._load(handle)["runs"]["issue:109"]["last_action_id"]
    effects.stale_action_id = stale_action_id

    with pytest.raises(ExecutionKernelError) as raised:
        kernel.advance(handle, plan_invalidation=invalidation)

    assert raised.value.code == "EFFECT_READBACK_INVALID"
    assert plans.active == plans.successor
    run = kernel.inspect(handle).work_runs[1]
    assert run.ticket_key == "issue:109"
    assert run.phase == "pending"
    assert run.candidate_identity is None
    assert all(action.plan_revision_digest != plans.successor.current_revision_digest for action in effects.executed)


def test_unrelated_revision_change_still_fails_closed(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel, ExecutionKernelError

    kernel, plans, effects, handle, _ = _successor_fixture(tmp_path)
    kernel.advance(handle)
    foreign = _successor_readback(
        plans.predecessor,
        plans.classification,
        changed_ticket="issue:108",
        planning_action_id="foreign-planning-action",
        expected_previous="f" * 64,
    )
    plans.active = foreign

    with pytest.raises(ExecutionKernelError) as raised:
        kernel.advance(handle)
    assert raised.value.code == "CAMPAIGN_REVISION_CHANGED"

    with pytest.raises(ExecutionKernelError) as raised:
        kernel.inspect(handle)
    assert raised.value.code == "CAMPAIGN_REVISION_CHANGED"
    assert effects.executed


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_schema_version",
        "missing_repository",
        "missing_target_branch",
        "missing_campaign_key",
        "missing_campaign_source",
        "missing_campaign_authority",
        "missing_policy",
        "missing_work_source",
        "missing_work_contract",
        "missing_work_capabilities",
        "missing_work_authority",
        "missing_work_depends_on",
        "missing_work_exclusive_resources",
        "extra_plan_field",
        "extra_work_field",
    ),
)
def test_successor_readback_requires_closed_plan_identity(tmp_path, mutation):
    from gwo_v8.execution_kernel import ExecutionKernelError

    kernel, plans, effects, handle, invalidation = _successor_fixture(tmp_path)
    plans.successor = _tampered_successor(plans.successor, mutation)
    plans.return_value = plans.successor
    effects_before = tuple(effects.executed)

    with pytest.raises(ExecutionKernelError) as raised:
        kernel.advance(handle, plan_invalidation=invalidation)

    assert raised.value.code == "SUCCESSOR_ACTIVATION_READBACK_INVALID"
    assert effects.executed == list(effects_before)
    assert kernel._load(handle)["plan_revision_digest"] == (
        plans.predecessor.current_revision_digest
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("writer_generation", "writer:foreign"),
        ("writer_generation", None),
        ("ready_refs", ("issue:108",)),
        ("ready_refs", ()),
        ("planning_subject_digest", "not-a-digest"),
        ("planning_preflight_receipt_digest", "not-a-digest"),
        ("compilation_record_artifact_digest", "not-a-digest"),
        ("planning_receipt_digest", "not-a-digest"),
        ("planning_output_artifact_digest", "not-a-digest"),
    ),
)
def test_successor_readback_requires_closed_activation_receipt(
    tmp_path, field, value
):
    from gwo_v8.execution_kernel import ExecutionKernelError

    kernel, plans, effects, handle, invalidation = _successor_fixture(tmp_path)
    plans.successor = replace(
        plans.successor,
        activation_receipt=replace(
            plans.successor.activation_receipt,
            **{field: value},
        ),
    )
    plans.return_value = plans.successor
    effects_before = tuple(effects.executed)

    with pytest.raises(ExecutionKernelError) as raised:
        kernel.advance(handle, plan_invalidation=invalidation)

    assert raised.value.code == "SUCCESSOR_ACTIVATION_READBACK_INVALID"
    assert effects.executed == list(effects_before)
    assert kernel._load(handle)["plan_revision_digest"] == (
        plans.predecessor.current_revision_digest
    )


@pytest.mark.parametrize(
    "mutation",
    ("missing_target_branch", "missing_work_authority", "writer_generation"),
)
def test_restart_revalidates_successor_authority_before_migration(tmp_path, mutation):
    from gwo_v8.execution_kernel import ExecutionKernel, ExecutionKernelError

    kernel, plans, effects, handle, invalidation = _successor_fixture(tmp_path)
    if mutation == "writer_generation":
        plans.successor = replace(
            plans.successor,
            activation_receipt=replace(
                plans.successor.activation_receipt,
                writer_generation=None,
            ),
        )
    else:
        plans.successor = _tampered_successor(plans.successor, mutation)
    plans.return_value = plans.successor

    with pytest.raises(ExecutionKernelError) as raised:
        kernel.advance(handle, plan_invalidation=invalidation)
    assert raised.value.code == "SUCCESSOR_ACTIVATION_READBACK_INVALID"
    assert plans.active == plans.successor

    restarted = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=plans,
        effects=effects,
    )
    plans.kernel = restarted
    with pytest.raises(ExecutionKernelError) as raised:
        restarted.advance(handle)

    assert raised.value.code == "SUCCESSOR_ACTIVATION_READBACK_INVALID"
    assert restarted._load(handle)["plan_revision_digest"] == (
        plans.predecessor.current_revision_digest
    )


def _successor_fixture(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel, PlanInvalidationObservation

    predecessor, handle = _active_campaign(("issue:108", "issue:109"))
    classification = _approved_successor_classification(predecessor)
    successor = _successor_readback(predecessor, classification)
    plans = _SuccessorPlans(predecessor, successor, classification)
    effects = _SuccessorEffects(successor.current_revision_digest)
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=plans,
        effects=effects,
    )
    plans.kernel = kernel
    kernel.advance(handle)
    state = kernel._load(handle)
    run = state["runs"]["issue:109"]
    work = kernel._authoritative_active(handle)[1]
    invalidation = PlanInvalidationObservation(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        plan_revision_digest=predecessor.current_revision_digest,
        ticket_key="issue:109",
        work_run_key=run["work_run_key"],
        runtime_binding_id=run["semantic_action_id"],
        authority_subtree_digest=work["issue:109"]["authority"]["worker"]["subtree_digest"],
        reporter_role="worker",
        report_digest="a" * 64,
        evidence_digest="b" * 64,
        dedup_identity="successor-fixture:issue-109",
        invalidated_obligation="successor fixture requires an exact successor",
        required_effects=("workspace.write.v1",),
        workspace_identity="workspace:r0:109",
    )
    return kernel, plans, effects, handle, invalidation


def _tampered_successor(successor, mutation):
    from gwo_v8._canonical import canonical_bytes, digest_bytes, load_canonical_json

    spec = deepcopy(load_canonical_json(successor.plan_spec_bytes))
    if mutation == "missing_schema_version":
        spec.pop("schema_version")
    elif mutation == "missing_repository":
        spec.pop("repository")
    elif mutation == "missing_target_branch":
        spec.pop("target_branch")
    elif mutation == "missing_campaign_key":
        spec["campaign"].pop("key")
    elif mutation == "missing_campaign_source":
        spec["campaign"].pop("source")
    elif mutation == "missing_campaign_authority":
        spec["campaign"].pop("authority")
    elif mutation == "missing_policy":
        spec.pop("policy")
    elif mutation == "missing_work_source":
        spec["work"][0].pop("source")
    elif mutation == "missing_work_contract":
        spec["work"][0].pop("contract")
    elif mutation == "missing_work_capabilities":
        spec["work"][0].pop("capabilities")
    elif mutation == "missing_work_authority":
        spec["work"][0].pop("authority")
    elif mutation == "missing_work_depends_on":
        spec["work"][0].pop("depends_on")
    elif mutation == "missing_work_exclusive_resources":
        spec["work"][0].pop("exclusive_resources")
    elif mutation == "extra_plan_field":
        spec["unexpected"] = True
    elif mutation == "extra_work_field":
        spec["work"][0]["unexpected"] = True
    else:  # pragma: no cover - the parameter list is closed above
        raise AssertionError(mutation)
    payload = canonical_bytes(spec)
    revision_digest = digest_bytes(payload)
    receipt = replace(
        successor.activation_receipt,
        revision_digest=revision_digest,
    )
    claims = tuple(
        replace(proof, plan_revision_digest=revision_digest)
        for proof in successor.claim_proofs
    )
    return replace(
        successor,
        current_revision_digest=revision_digest,
        plan_spec_bytes=payload,
        activation_receipt=receipt,
        claim_proofs=claims,
    )


def _approved_successor_classification(active):
    from gwo_v8.execution_kernel import ExecutionKernel
    from gwo_v8.plan_control import PlanInvalidationClassification, PlanInvalidationDisposition

    evidence_digests = ("b" * 64,)
    return PlanInvalidationClassification(
        action_id=ExecutionKernel._replanning_action_id(active, evidence_digests),
        snapshot_digest="c" * 64,
        plan_revision_digest=active.current_revision_digest,
        evidence_digests=evidence_digests,
        disposition=PlanInvalidationDisposition.USE_APPROVED_SUCCESSOR,
        reason="approved successor fixture",
        capability_proof_digest="d" * 64,
        successor_ticket_keys=("issue:108", "issue:109"),
    )


def _successor_readback(
    predecessor,
    classification,
    *,
    changed_ticket="issue:109",
    planning_action_id=None,
    expected_previous=None,
):
    from gwo_v8._canonical import canonical_bytes, digest_bytes, load_canonical_json
    from gwo_v8.plan_control import (
        ActivePlanReadback,
        TicketClaimProof,
        frozen_ticket_contract_digest,
    )

    spec = deepcopy(load_canonical_json(predecessor.plan_spec_bytes))
    for item in spec["work"]:
        if item["key"] == changed_ticket:
            item["contract"]["body"] = "the successor Ticket is satisfied"
            item["source"]["digest"] = frozen_ticket_contract_digest(
                key=item["key"],
                contract=item["contract"],
                labels=["ready-for-agent"],
                native_blockers=[],
            )
    payload = canonical_bytes(spec)
    revision_digest = digest_bytes(payload)
    receipt = replace(
        predecessor.activation_receipt,
        revision_digest=revision_digest,
        expected_previous_revision_digest=(
            predecessor.current_revision_digest
            if expected_previous is None
            else expected_previous
        ),
        planning_stable_action_id=(
            classification.action_id if planning_action_id is None else planning_action_id
        ),
        ticket_keys=tuple(item["key"] for item in spec["work"]),
        ready_refs=tuple(item["key"] for item in spec["work"]),
    )
    return ActivePlanReadback(
        handle=predecessor.handle,
        current_revision_digest=revision_digest,
        plan_spec_bytes=payload,
        activation_receipt=receipt,
        claim_proofs=tuple(
            TicketClaimProof(
                ticket_key=item["key"],
                repository=predecessor.handle.repository,
                campaign_key=predecessor.handle.campaign_key,
                plan_revision_digest=revision_digest,
            )
            for item in spec["work"]
        ),
    )


class _SuccessorPlans:
    def __init__(self, predecessor, successor, classification):
        self.predecessor = predecessor
        self.successor = successor
        self.active = predecessor
        self.classification = classification
        self.activate_calls = 0
        self.intent_at_activation = None
        self.return_value = successor
        self.kernel = None

    def read_active(self, handle):
        assert handle == self.active.handle
        return self.active

    def classify_plan_invalidations(self, handle, invalidations, execution_snapshot):
        assert handle == self.active.handle
        assert invalidations
        return self.classification

    def activate_successor(self, handle, classification):
        assert handle == self.active.handle
        assert classification == self.classification
        self.activate_calls += 1
        self.intent_at_activation = deepcopy(self.kernel._load(handle)["successor_transition"])
        self.active = self.successor
        return self.return_value


class _SuccessorEffects:
    def __init__(self, successor_digest):
        self.successor_digest = successor_digest
        self.executed = []
        self.stale_action_id = None

    def readback(self, action):
        if self.stale_action_id is None:
            return None
        return _observation(
            "candidate_checks",
            self.stale_action_id,
            candidate_identity="candidate:r0:109",
        )

    def execute(self, action):
        self.executed.append(action)
        if action.ticket_key == "issue:108":
            return _observation(
                "completed",
                action.stable_action_id,
                result_digest="7" * 64,
                evidence_digests=("8" * 64,),
            )
        return _observation(
            "running",
            action.stable_action_id,
            candidate_identity=(
                "candidate:r0:109"
                if action.plan_revision_digest != self.successor_digest
                else None
            ),
        )


class _Plans:
    def __init__(self, active):
        self.active = active

    def read_active(self, handle):
        assert handle == self.active.handle
        return self.active


class _Effects:
    def __init__(
        self,
        *,
        phase="running",
        candidate_identity=None,
        result_digest=None,
        evidence_digests=(),
        readback_observation=None,
    ):
        self.phase = phase
        self.candidate_identity = candidate_identity
        self.result_digest = result_digest
        self.evidence_digests = evidence_digests
        self.readback_observation = readback_observation
        self.executed = []

    def readback(self, _action):
        return self.readback_observation

    def execute(self, action):
        self.executed.append(action)
        return _observation(
            self.phase,
            action.stable_action_id,
            candidate_identity=self.candidate_identity,
            result_digest=self.result_digest,
            evidence_digests=self.evidence_digests,
        )


class _ReadBackOnlyEffects:
    def __init__(self):
        self.executed = []

    def readback(self, _action):
        return None

    def execute(self, action):
        self.executed.append(action)
        return _observation("running", action.stable_action_id)


def _observation(
    phase,
    action_id,
    *,
    candidate_identity=None,
    result_digest=None,
    evidence_digests=(),
):
    from gwo_v8._canonical import digest_value
    from gwo_v8.execution_kernel import WorkRunObservation

    return WorkRunObservation(
        phase=phase,
        stable_action_id=action_id,
        receipt_digest=digest_value({"phase": phase, "action": action_id}),
        candidate_identity=candidate_identity,
        result_digest=result_digest,
        evidence_digests=tuple(evidence_digests),
    )


def _active_campaign(ticket_keys):
    from gwo_v8._canonical import canonical_bytes, digest_bytes, digest_value
    from gwo_v8.plan_control import (
        ActivationReceipt,
        ActivePlanReadback,
        CampaignHandle,
        frozen_ticket_contract_digest,
        TicketClaimProof,
    )

    handle = CampaignHandle("owner/repository", "campaign:successor-kernel")
    policy_core = {
        "schema_version": 1,
        "ref": "policy:successor-kernel",
        "authority_grants": {
            "campaign": [
                {
                    "operation_id": "repository.read.v1",
                    "resource_id": "campaign.snapshot.v1",
                }
            ],
            "worker": [
                {
                    "operation_id": "workspace.write.v1",
                    "resource_id": "work-run.workspace.v1",
                }
            ],
            "recovery_worker": [
                {
                    "operation_id": "workspace.write.v1",
                    "resource_id": "work-run.workspace.v1",
                }
            ],
            "review": [
                {
                    "operation_id": "repository.read.v1",
                    "resource_id": "review.subject.v1",
                }
            ],
        },
        "allowed_capabilities": ["git", "local_check"],
        "exclusive_resources": ["repository.target.v1"],
    }
    policy = {**policy_core, "digest": digest_value(policy_core)}
    campaign_source_core = {
        "repository": handle.repository,
        "input_ref": "refs/heads/main",
        "resolved_commit_oid": "2" * 40,
        "tree_oid": "3" * 40,
    }
    campaign_source = {
        **campaign_source_core,
        "digest": digest_value(campaign_source_core),
    }

    def authority(grants):
        core = {
            "policy_witness_digest": policy["digest"],
            "grants": grants,
        }
        return {**core, "subtree_digest": digest_value(core)}

    work = [
        {
            "key": key,
            "source": {
                "ref": key,
                "digest": frozen_ticket_contract_digest(
                    key=key,
                    contract={
                        "id": int(key.split(":")[1]),
                        "node_id": f"ISSUE_{key.split(':')[1]}",
                        "number": int(key.split(":")[1]),
                        "title": f"Successor kernel {key}",
                        "body": f"Complete successor kernel work {key}",
                        "state": "open",
                        "state_reason": None,
                        "type": None,
                        "repository": {
                            "full_name": handle.repository,
                            "url": "https://api.github.com/repos/owner/repository",
                        },
                        "labels": [
                            {
                                "id": 1,
                                "node_id": "LABEL_READY",
                                "url": "https://api.github.com/repos/owner/repository/labels/ready-for-agent",
                                "name": "ready-for-agent",
                                "color": "0052cc",
                                "default": False,
                                "description": "ready",
                            }
                        ],
                        "comments": [],
                        "updated_at": "2026-08-02T00:00:00Z",
                    },
                    labels=["ready-for-agent"],
                    native_blockers=[],
                ),
            },
            "contract": {
                "id": int(key.split(":")[1]),
                "node_id": f"ISSUE_{key.split(':')[1]}",
                "number": int(key.split(":")[1]),
                "title": f"Successor kernel {key}",
                "body": f"Complete successor kernel work {key}",
                "state": "open",
                "state_reason": None,
                "type": None,
                "repository": {
                    "full_name": handle.repository,
                    "url": "https://api.github.com/repos/owner/repository",
                },
                "labels": [
                    {
                        "id": 1,
                        "node_id": "LABEL_READY",
                        "url": "https://api.github.com/repos/owner/repository/labels/ready-for-agent",
                        "name": "ready-for-agent",
                        "color": "0052cc",
                        "default": False,
                        "description": "ready",
                    }
                ],
                "comments": [],
                "updated_at": "2026-08-02T00:00:00Z",
            },
            "depends_on": [],
            "exclusive_resources": [],
            "capabilities": ["git", "local_check"],
            "authority": {
                "policy_witness_digest": policy["digest"],
                "worker": authority(policy["authority_grants"]["worker"]),
                "recovery_worker": authority(
                    policy["authority_grants"]["recovery_worker"]
                ),
                "review": authority(policy["authority_grants"]["review"]),
            },
        }
        for key in ticket_keys
    ]
    spec = {
        "schema_version": 3,
        "repository": handle.repository,
        "target_branch": "main",
        "campaign": {
            "key": handle.campaign_key,
            "source": campaign_source,
            "authority": authority(policy["authority_grants"]["campaign"]),
        },
        "policy": {"ref": policy["ref"], "digest": policy["digest"]},
        "work": work,
    }
    payload = canonical_bytes(spec)
    revision = digest_bytes(payload)
    receipt = ActivationReceipt(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        revision_digest=revision,
        expected_previous_revision_digest=None,
        writer_generation="writer:one",
        ready_refs=tuple(sorted(item["source"]["ref"] for item in work)),
        ticket_keys=ticket_keys,
        planning_subject_digest="d" * 64,
        planning_stable_action_id="campaign-plan:one",
        planning_preflight_receipt_digest="e" * 64,
        compilation_record_artifact_digest="f" * 64,
        planning_receipt_digest="0" * 64,
        planning_output_artifact_digest="1" * 64,
    )
    return (
        ActivePlanReadback(
            handle=handle,
            current_revision_digest=revision,
            plan_spec_bytes=payload,
            activation_receipt=receipt,
            claim_proofs=tuple(
                TicketClaimProof(
                    ticket_key=key,
                    repository=handle.repository,
                    campaign_key=handle.campaign_key,
                    plan_revision_digest=revision,
                )
                for key in ticket_keys
            ),
        ),
        handle,
    )
