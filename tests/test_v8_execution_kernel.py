from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_one_advance_admits_four_independent_claimed_tickets(tmp_path):
    """RED: #110 owns concurrent Work Run admission after #109 readback."""

    from gwo_v8.execution_kernel import ExecutionKernel

    active, handle = _active_campaign(("issue:1", "issue:2", "issue:3", "issue:4"))
    plans = _Plans(active)
    effects = _Effects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=plans,
        effects=effects,
    )

    outcome = kernel.advance(handle)

    assert outcome.status == "Running"
    assert [action.ticket_key for action in effects.executed] == [
        "issue:1",
        "issue:2",
        "issue:3",
        "issue:4",
    ]
    diagnostics = kernel.inspect(handle)
    assert diagnostics.status == "Running"
    assert diagnostics.worker_slots == {"limit": 4, "held": 4, "available": 0}


def test_released_capacity_refills_a_fifth_ticket_in_the_same_advance(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    active, handle = _active_campaign(
        ("issue:1", "issue:2", "issue:3", "issue:4", "issue:5")
    )
    effects = _ScriptedEffects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )

    kernel.advance(handle)
    assert [action.ticket_key for action in effects.executed] == [
        "issue:1",
        "issue:2",
        "issue:3",
        "issue:4",
    ]
    assert kernel.inspect(handle).status == "Running"

    effects.observe("issue:1", "accepted_awaiting_delivery")
    outcome = kernel.advance(handle, "runtime-wake:one")

    assert outcome.status == "Running"
    assert [action.ticket_key for action in effects.executed] == [
        "issue:1",
        "issue:2",
        "issue:3",
        "issue:4",
        "issue:5",
    ]
    assert kernel.inspect(handle).worker_slots == {"limit": 4, "held": 4, "available": 0}


def test_dependencies_and_exclusive_resources_serialize_only_affected_runs(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    active, handle = _active_campaign(
        ("issue:1", "issue:2", "issue:3", "issue:4"),
        work_facts={
            "issue:1": {"exclusive_resources": ["repository.target.v1"]},
            "issue:2": {"exclusive_resources": ["repository.target.v1"]},
            "issue:4": {"depends_on": ["issue:1"]},
        },
    )
    effects = _ScriptedEffects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )

    kernel.advance(handle)

    assert [action.ticket_key for action in effects.executed] == ["issue:1", "issue:3"]
    summaries = {run.ticket_key: run for run in kernel.inspect(handle).work_runs}
    assert summaries["issue:2"].reason == "ExclusiveResource"
    assert summaries["issue:4"].reason == "TicketDependency"

    effects.observe("issue:1", "completed")
    kernel.advance(handle, "runtime-wake:resource-released")

    assert [action.ticket_key for action in effects.executed] == [
        "issue:1",
        "issue:3",
        "issue:2",
        "issue:4",
    ]


def test_same_predicted_paths_do_not_serialize_isolated_workspaces(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    # Predicted paths are deliberately absent from PlanSpec v3.  Two Tickets
    # that happen to be expected to touch the same path therefore remain
    # independently eligible unless PlanControl declared a real resource.
    active, handle = _active_campaign(("issue:1", "issue:2"))
    effects = _ScriptedEffects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )

    kernel.advance(handle)

    assert [action.ticket_key for action in effects.executed] == ["issue:1", "issue:2"]


def test_public_advance_and_inspect_route_only_through_the_active_reader(tmp_path):
    from gwo_v8.execution_kernel import advance, inspect, install_execution_kernel

    active, handle = _active_campaign(("issue:1",))
    plans = _Plans(active)
    install_execution_kernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=plans,
        effects=_ScriptedEffects(),
    )

    assert advance(handle).status == "Running"
    assert inspect(handle).status == "Running"
    assert plans.reads == 2


def test_slot_is_retained_through_candidate_review_and_repair_then_released(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    active, handle = _active_campaign(("issue:1", "issue:2"))
    effects = _ScriptedEffects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    kernel.advance(handle)

    for ordinal, phase in enumerate(
        ("candidate_checks", "formal_review", "repair"), start=1
    ):
        effects.observe("issue:1", phase)
        kernel.advance(handle, f"runtime-wake:phase-{ordinal}")
        assert _summary(kernel, handle, "issue:1").slot_held is True
        assert kernel.inspect(handle).worker_slots["held"] == 2

    effects.observe("issue:1", "accepted_awaiting_delivery")
    kernel.advance(handle, "runtime-wake:accepted")

    assert _summary(kernel, handle, "issue:1").slot_held is False
    assert kernel.inspect(handle).worker_slots["held"] == 1


def test_proven_park_releases_slot_and_resume_waits_to_reacquire(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel, ExecutionKernelConfiguration

    active, handle = _active_campaign(("issue:1", "issue:2"))
    effects = _ScriptedEffects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
        configuration=ExecutionKernelConfiguration(host_worker_slots=1),
    )
    kernel.advance(handle)
    assert [action.ticket_key for action in effects.executed] == ["issue:1"]

    effects.observe("issue:1", "parked")
    kernel.advance(handle, "runtime-wake:first-park")
    assert [action.ticket_key for action in effects.executed] == ["issue:1", "issue:2"]
    assert _summary(kernel, handle, "issue:1").slot_held is False

    # The first Work Run has a wake, but capacity is held by the second and
    # no resume effect is issued.
    kernel.advance(handle, "runtime-wake:resume-without-capacity")
    assert [action.kind for action in effects.executed].count("semantic_resume") == 0

    effects.observe("issue:2", "parked")
    kernel.advance(handle, "runtime-wake:second-park")

    resumes = [action for action in effects.executed if action.kind == "semantic_resume"]
    assert len(resumes) == 1
    assert resumes[0].ticket_key == "issue:1"
    assert resumes[0].semantic_action_id == effects.executed[0].stable_action_id


def test_formal_review_and_coordinator_do_not_consume_extra_worker_slots(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    active, handle = _active_campaign(("issue:1",))
    effects = _ScriptedEffects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    kernel.advance(handle)
    effects.observe("issue:1", "formal_review")
    kernel.advance(handle, "candidate-gate:review")

    diagnostics = kernel.inspect(handle)
    assert diagnostics.worker_slots == {"limit": 4, "held": 1, "available": 3}
    assert all(action.kind != "coordinator" for action in effects.executed)


@pytest.mark.parametrize(
    "mutator, code",
    [
        ("missing", "TICKET_CLAIM_READBACK_INVALID"),
        ("foreign", "TICKET_CLAIM_READBACK_INVALID"),
        ("stale", "TICKET_CLAIM_READBACK_INVALID"),
        ("receipt", "ACTIVATION_READBACK_INVALID"),
    ],
)
def test_bad_activation_or_ticket_claim_readback_fails_before_effect(tmp_path, mutator, code):
    from dataclasses import replace

    from gwo_v8.execution_kernel import ExecutionKernel, ExecutionKernelError

    active, handle = _active_campaign(("issue:1",))
    if mutator == "missing":
        active = replace(active, claim_proofs=())
    elif mutator == "foreign":
        proof = replace(active.claim_proofs[0], campaign_key="campaign:foreign")
        active = replace(active, claim_proofs=(proof,))
    elif mutator == "stale":
        proof = replace(active.claim_proofs[0], plan_revision_digest="0" * 64)
        active = replace(active, claim_proofs=(proof,))
    else:
        receipt = replace(active.activation_receipt, campaign_key="campaign:foreign")
        active = replace(active, activation_receipt=receipt)
    effects = _ScriptedEffects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )

    with pytest.raises(ExecutionKernelError) as rejected:
        kernel.advance(handle)

    assert rejected.value.code == code
    assert effects.executed == []


def test_effect_intent_recovers_both_crash_windows_without_duplicate_action(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    active, handle = _active_campaign(("issue:1",))
    effects = _CrashEffects(crash_before_execute=True)
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    with pytest.raises(RuntimeError, match="before execute"):
        kernel.advance(handle)

    recovered = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    recovered.advance(handle)
    assert len(effects.executed) == 1

    after = _CrashEffects(crash_after_execute=True)
    kernel = ExecutionKernel(
        store_path=tmp_path / "after.sqlite3",
        plan_control=_Plans(active),
        effects=after,
    )
    with pytest.raises(RuntimeError, match="after execute"):
        kernel.advance(handle)

    ExecutionKernel(
        store_path=tmp_path / "after.sqlite3",
        plan_control=_Plans(active),
        effects=after,
    ).advance(handle)
    assert len(after.executed) == 1


def test_duplicate_and_concurrent_advance_never_exceed_four_slots(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    active, handle = _active_campaign(tuple(f"issue:{number}" for number in range(1, 9)))
    effects = _BlockingEffects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    failures = []

    def invoke():
        try:
            kernel.advance(handle, "same-wake")
        except Exception as error:  # pragma: no cover - asserted below
            failures.append(error)

    threads = [threading.Thread(target=invoke) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert len(effects.executed) == 4
    assert kernel.inspect(handle).worker_slots == {"limit": 4, "held": 4, "available": 0}


def test_host_default_and_repository_override_are_outside_planspec(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel, ExecutionKernelConfiguration

    active, handle = _active_campaign(("issue:1", "issue:2", "issue:3"))
    effects = _ScriptedEffects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
        configuration=ExecutionKernelConfiguration(
            repository_worker_slots={"owner/repository": 2},
        ),
    )

    kernel.advance(handle)

    assert [action.ticket_key for action in effects.executed] == ["issue:1", "issue:2"]
    assert kernel.inspect(handle).worker_slots == {"limit": 2, "held": 2, "available": 0}
    assert b"capacity" not in active.plan_spec_bytes


def test_preidentity_runtime_unavailability_waits_without_stalling_disjoint_run(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    active, handle = _active_campaign(("issue:1", "issue:2"))
    effects = _ScriptedEffects(initial_phases={"issue:1": "runtime_unavailable"})
    effects.binding_established["issue:1"] = False
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )

    outcome = kernel.advance(handle)

    assert outcome.status == "Running"
    assert _summary(kernel, handle, "issue:1").slot_held is False
    assert _summary(kernel, handle, "issue:2").slot_held is True


def test_restart_at_admission_release_and_refill_boundaries_converges(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    active, handle = _active_campaign(
        ("issue:1", "issue:2", "issue:3", "issue:4", "issue:5")
    )
    effects = _ScriptedEffects()
    first = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    first.advance(handle)
    assert len(effects.executed) == 4

    after_admission_restart = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    after_admission_restart.advance(handle)
    assert len(effects.executed) == 4

    effects.observe("issue:1", "accepted_awaiting_delivery")
    after_admission_restart.advance(handle, "runtime-wake:released")
    assert len(effects.executed) == 5

    after_refill_restart = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    assert after_refill_restart.inspect(handle).worker_slots == {
        "limit": 4,
        "held": 4,
        "available": 0,
    }
    after_refill_restart.advance(handle, "runtime-wake:released")
    assert len(effects.executed) == 5


def test_kernel_has_no_legacy_scheduling_or_completion_entrypoint(tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    active, _handle = _active_campaign(("issue:1",))
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=_ScriptedEffects(),
    )

    assert not hasattr(kernel, "reconcile_once")
    assert not hasattr(kernel, "run_once")
    assert not hasattr(kernel, "goal_driver")


@pytest.mark.parametrize(
    "phases, expected",
    [
        (("completed",), "Complete"),
        (("running", "decision", "parked", "blocked"), "Running"),
        (("decision", "parked", "blocked"), "Decision"),
        (("parked", "blocked"), "Wait"),
        (("blocked",), "Blocked"),
    ],
)
def test_status_precedence_and_machine_inspection(phases, expected, tmp_path):
    from gwo_v8.execution_kernel import ExecutionKernel

    keys = tuple(f"issue:{index}" for index in range(1, len(phases) + 1))
    active, handle = _active_campaign(keys)
    effects = _ScriptedEffects(initial_phases=dict(zip(keys, phases)))
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )

    outcome = kernel.advance(handle)
    before = len(effects.executed)
    diagnostics = kernel.inspect(handle)

    assert outcome.status == expected
    assert diagnostics.status == expected
    assert diagnostics.work_runs
    assert len(effects.executed) == before


def _summary(kernel, handle, ticket_key):
    return next(run for run in kernel.inspect(handle).work_runs if run.ticket_key == ticket_key)


class _Plans:
    def __init__(self, active):
        self.active = active
        self.reads = 0

    def read_active(self, handle):
        self.reads += 1
        assert handle == self.active.handle
        return self.active


class _Effects:
    def __init__(self):
        self.executed = []

    def readback(self, action):
        return None

    def execute(self, action):
        self.executed.append(action)
        from gwo_v8.execution_kernel import WorkRunObservation

        return WorkRunObservation.running(action.stable_action_id)


class _ScriptedEffects:
    def __init__(self, *, initial_phases=None):
        self.executed = []
        self.observed = {}
        self.started = set()
        self.initial_phases = initial_phases or {}
        self.binding_established = {}

    def observe(self, ticket_key, phase):
        self.observed[ticket_key] = phase

    def readback(self, action):
        if action.kind == "semantic_resume":
            return None
        if action.ticket_key not in self.started:
            return None
        phase = self.observed.get(action.ticket_key)
        if phase is None:
            return None
        return _observation(
            phase,
            action.stable_action_id,
            binding_established=self.binding_established.get(action.ticket_key, True),
        )

    def execute(self, action):
        self.executed.append(action)
        self.started.add(action.ticket_key)
        return _observation(
            self.initial_phases.get(action.ticket_key, "running"),
            action.stable_action_id,
            binding_established=self.binding_established.get(action.ticket_key, True),
        )


class _CrashEffects(_ScriptedEffects):
    def __init__(self, *, crash_before_execute=False, crash_after_execute=False):
        super().__init__()
        self.crash_before_execute = crash_before_execute
        self.crash_after_execute = crash_after_execute

    def readback(self, action):
        if self.crash_before_execute:
            self.crash_before_execute = False
            raise RuntimeError("before execute")
        if action.stable_action_id in {item.stable_action_id for item in self.executed}:
            return _observation("running", action.stable_action_id)
        return None

    def execute(self, action):
        observation = super().execute(action)
        if self.crash_after_execute:
            self.crash_after_execute = False
            raise RuntimeError("after execute")
        return observation


class _BlockingEffects(_ScriptedEffects):
    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()

    def execute(self, action):
        with self._lock:
            return super().execute(action)


def _observation(phase, action_id, *, binding_established=True):
    from gwo_v8._canonical import digest_value
    from gwo_v8.execution_kernel import WorkRunObservation

    return WorkRunObservation(
        phase=phase,
        stable_action_id=action_id,
        receipt_digest=digest_value({"phase": phase, "action": action_id}),
        binding_established=binding_established,
    )


def _active_campaign(ticket_keys, *, work_facts=None):
    from gwo_v8._canonical import canonical_bytes, digest_bytes
    from gwo_v8.plan_control import (
        ActivationReceipt,
        ActivePlanReadback,
        CampaignHandle,
        TicketClaimProof,
    )

    handle = CampaignHandle("owner/repository", "campaign:issue-110")
    work_facts = work_facts or {}
    work = [
        {
            "key": key,
            "depends_on": [],
            "exclusive_resources": [],
            "authority": {"worker": {"subtree_digest": "a" * 64}},
            **work_facts.get(key, {}),
        }
        for key in ticket_keys
    ]
    spec = {
        "schema_version": 3,
        "repository": handle.repository,
        "campaign": {"key": handle.campaign_key},
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
        planning_subject_digest="b" * 64,
        planning_stable_action_id="campaign-plan:one",
        planning_preflight_receipt_digest="c" * 64,
        compilation_record_artifact_digest="d" * 64,
        planning_receipt_digest="e" * 64,
        planning_output_artifact_digest="f" * 64,
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


def test_module_level_advance_hands_gateway_receipt_to_kernel(tmp_path):
    """The public advance wrapper must not drop the #133 receipt keyword."""

    from types import SimpleNamespace

    from gwo_v8.execution_kernel import (
        PlanInvalidationObservation,
        advance,
        install_execution_kernel,
    )

    active, handle = _active_campaign(("issue:1",))
    effects = _Effects()
    install_execution_kernel(
        store_path=tmp_path / "receipt.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    advance(handle)
    binding = effects.executed[0].stable_action_id
    observation = PlanInvalidationObservation(
        repository=active.handle.repository,
        campaign_key=active.handle.campaign_key,
        plan_revision_digest=active.current_revision_digest,
        ticket_key="issue:1",
        work_run_key="work-run:issue:1",
        runtime_binding_id=binding,
        authority_subtree_digest="a" * 64,
        reporter_role="worker",
        report_digest="a" * 64,
        evidence_digest="b" * 64,
        dedup_identity="receipt:one",
        invalidated_obligation="issue:1 contract is incomplete",
        required_effects=("workspace.write.v1",),
        workspace_identity="workspace:one",
    )
    receipt = SimpleNamespace(
        report_digest=observation.report_digest,
        receipt_digest="c" * 64,
        observation=observation.canonical(),
    )

    advance(handle, plan_invalidation=receipt)

    summary = _summary(
        __import__("gwo_v8.execution_kernel", fromlist=["ExecutionKernel"])
        .ExecutionKernel(
            store_path=tmp_path / "receipt.sqlite3",
            plan_control=_Plans(active),
            effects=effects,
        ),
        handle,
        "issue:1",
    )
    assert summary.phase == "quiescent"
    assert summary.claim_state == "released"


def test_plan_invalidation_reconciles_after_report_save_crash(tmp_path):
    """A crash after the durable report write cannot strand the Work Run."""

    from gwo_v8.execution_kernel import ExecutionKernel, PlanInvalidationObservation

    active, handle = _active_campaign(("issue:1",))
    effects = _Effects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "crash.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    kernel.advance(handle)
    binding = effects.executed[0].stable_action_id
    observation = PlanInvalidationObservation(
        repository=active.handle.repository,
        campaign_key=active.handle.campaign_key,
        plan_revision_digest=active.current_revision_digest,
        ticket_key="issue:1",
        work_run_key="work-run:issue:1",
        runtime_binding_id=binding,
        authority_subtree_digest="a" * 64,
        reporter_role="worker",
        report_digest="a" * 64,
        evidence_digest="b" * 64,
        dedup_identity="crash:one",
        invalidated_obligation="issue:1 contract is incomplete",
        required_effects=("workspace.write.v1",),
        workspace_identity="workspace:one",
    )
    original_save = kernel._save
    crashed = {"value": False}

    def save_then_crash(handle_value, state):
        original_save(handle_value, state)
        if not crashed["value"]:
            crashed["value"] = True
            raise RuntimeError("after plan invalidation record")

    kernel._save = save_then_crash
    with pytest.raises(RuntimeError, match="after plan invalidation record"):
        kernel.advance(handle, plan_invalidation=observation)

    recovered = ExecutionKernel(
        store_path=tmp_path / "crash.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    recovered.advance(handle, plan_invalidation=observation)
    assert len(effects.executed) == 1
    assert _summary(recovered, handle, "issue:1").phase == "quiescent"
    assert _summary(recovered, handle, "issue:1").slot_held is False
