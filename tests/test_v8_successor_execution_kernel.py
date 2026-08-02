from __future__ import annotations

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
    from gwo_v8._canonical import canonical_bytes, digest_bytes
    from gwo_v8.plan_control import (
        ActivationReceipt,
        ActivePlanReadback,
        CampaignHandle,
        TicketClaimProof,
    )

    handle = CampaignHandle("owner/repository", "campaign:successor-kernel")
    work = [
        {
            "key": key,
            "source": {"issue": key, "commit": "1" * 40},
            "contract": {"acceptance": ["the Ticket is satisfied"]},
            "depends_on": [],
            "exclusive_resources": [],
            "capabilities": ["repository.read"],
            "authority": {"worker": {"subtree_digest": "a" * 64}},
        }
        for key in ticket_keys
    ]
    spec = {
        "schema_version": 3,
        "repository": handle.repository,
        "target_branch": "main",
        "campaign": {
            "key": handle.campaign_key,
            "source": {"commit": "2" * 40, "tree": "3" * 40},
            "authority": {"worker": {"subtree_digest": "b" * 64}},
        },
        "policy": {"version": "policy:v1", "digest": "c" * 64},
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
        ready_refs=ticket_keys,
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
