import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
import sys

import pytest

pytest_plugins = ("v8_successor_test_support", "v8_candidate_assurance_test_support")

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.execution_kernel import (
    ExecutionKernel,
    ExecutionKernelError,
    StaleDiagnosisPacket,
    StaleBindingObservation,
    StaleDiagnosisDisposition,
    StaleDiagnosisObservation,
    StaleFollowUpKind,
    StaleFollowUpObservation,
    StaleReadbackState,
    TerminalBindingEvidence,
    WorkRunAction,
    WorkRunObservation,
)
from gwo_v8._canonical import canonical_bytes, digest_bytes, digest_value, load_canonical_json
from gwo_v8.candidate_gate import CandidateReceipt
from v8_candidate_assurance_test_support import kernel_with_candidate_receipt
from v8_successor_test_support import (
    _StaticPlanReader,
    _minimal_active_campaign,
    kernel_with_one_ticket,
)


@dataclass
class _MutableUtcClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, minutes: int) -> None:
        self.value += timedelta(minutes=minutes)


class _StaleRecordingEffects:
    def __init__(self) -> None:
        self.readbacks: dict[str, object] = {}
        self.stale_state = StaleReadbackState.AMBIGUOUS_RUNNING
        self.diagnosis_disposition = StaleDiagnosisDisposition.CONTINUE
        self.omit_candidate_receipt = False
        self.raise_diagnosis = False
        self.zero_llm_readbacks = 0
        self.coordinator_diagnoses = 0
        self.calls: list[tuple[str, str]] = []
        self.executed: list[WorkRunAction] = []
        self.diagnosis_packets: list[object] = []

    def readback(self, action: WorkRunAction) -> object | None:
        self.calls.append(("readback", action.kind))
        return self.readbacks.get(action.stable_action_id)

    def execute(self, action: WorkRunAction) -> object:
        self.calls.append(("execute", action.kind))
        self.executed.append(action)
        if action.kind in {"semantic_execution", "semantic_resume"}:
            observation: object = WorkRunObservation(
                phase="running",
                stable_action_id=action.stable_action_id,
                receipt_digest=digest_value({"action": action.stable_action_id}),
                runtime_binding_id="binding:initial",
            )
        elif action.kind == "stale_readback":
            self.zero_llm_readbacks += 1
            candidate_receipt = None
            if (
                self.stale_state is StaleReadbackState.CANDIDATE_RECEIVED
                and not self.omit_candidate_receipt
            ):
                candidate_receipt = CandidateReceipt(
                    parent_digest=digest_value({"parent": action.work_run_key}),
                    repository=action.repository,
                    campaign_key=action.campaign_key,
                    campaign_handle=action.campaign_key,
                    plan_revision_digest=action.plan_revision_digest,
                    work_run_key=action.work_run_key,
                    ticket_key=action.ticket_key,
                    reported_reference="refs/heads/candidate",
                    base_commit_oid="1" * 40,
                    base_tree_oid="2" * 40,
                    candidate_commit_oid="3" * 40,
                    candidate_tree_oid="4" * 40,
                    diff_schema_version="CandidateDiffRecordV1",
                    diff_record_digest="5" * 64,
                    authority_subtree_digest="6" * 64,
                    runtime_subject_digest=action.work_subject_digest,
                )
            stale_receipt_digest = digest_value(
                {
                    "action": action.stable_action_id,
                    "state": self.stale_state.value,
                }
            )
            if candidate_receipt is not None:
                stale_receipt_digest = candidate_receipt.digest
            observation = StaleBindingObservation(
                stable_action_id=action.stable_action_id,
                runtime_binding_id="binding:initial",
                state=self.stale_state,
                runtime_readback_digest="2" * 64,
                process_readback_digest="3" * 64,
                workspace_readback_digest="4" * 64,
                campaign_readback_digest="5" * 64,
                receipt_digest=stale_receipt_digest,
                candidate_receipt=candidate_receipt,
            )
        elif action.kind == "stale_diagnosis":
            self.coordinator_diagnoses += 1
            self.diagnosis_packets.append(action.stale_diagnosis_packet)
            if self.raise_diagnosis:
                raise RuntimeError("stale diagnosis crashed")
            observation = StaleDiagnosisObservation(
                stable_action_id=action.stable_action_id,
                runtime_binding_id="binding:initial",
                disposition=self.diagnosis_disposition,
                receipt_digest=digest_value(
                    {
                        "action": action.stable_action_id,
                        "disposition": self.diagnosis_disposition.value,
                    }
                ),
            )
        elif action.kind in {"stale_guidance", "stale_same_binding_recovery"}:
            kind = (
                StaleFollowUpKind.GUIDANCE
                if action.kind == "stale_guidance"
                else StaleFollowUpKind.SAME_BINDING_RECOVERY
            )
            observation = StaleFollowUpObservation(
                stable_action_id=action.stable_action_id,
                runtime_binding_id="binding:initial",
                kind=kind,
                receipt_digest=digest_value(
                    {"action": action.stable_action_id, "kind": kind.value}
                ),
            )
        else:
            raise AssertionError(f"unexpected WorkRunAction kind: {action.kind}")
        self.readbacks[action.stable_action_id] = observation
        return observation


@dataclass
class _StaleKernelHarness:
    kernel: ExecutionKernel
    effects: _StaleRecordingEffects
    handle: object
    clock: _MutableUtcClock

    def advance_clock(self, *, minutes: int) -> None:
        self.clock.advance(minutes=minutes)


@pytest.fixture
def stale_kernel(tmp_path):
    active, campaign = _minimal_active_campaign(("issue:109",))
    clock = _MutableUtcClock(datetime.fromisoformat("2026-08-03T09:30:00+00:00"))
    effects = _StaleRecordingEffects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "stale-kernel.db",
        plan_control=_StaticPlanReader(active),
        effects=effects,
        _clock=clock,
    )
    _bind_successor_fixture_to_campaign(kernel, campaign)
    kernel.advance(campaign)
    return _StaleKernelHarness(kernel, effects, campaign, clock)


def _bind_successor_fixture_to_campaign(kernel, campaign):
    """Keep the predecessor fixture's static PlanSpec bound to its handle."""

    active = kernel._plan_control.active
    plan = load_canonical_json(active.plan_spec_bytes)
    if plan["campaign"]["key"] == campaign.campaign_key:
        return
    plan["campaign"]["key"] = campaign.campaign_key
    payload = canonical_bytes(plan)
    revision = digest_bytes(payload)
    kernel._plan_control.active = replace(
        active,
        current_revision_digest=revision,
        plan_spec_bytes=payload,
        activation_receipt=replace(active.activation_receipt, revision_digest=revision),
        claim_proofs=tuple(
            replace(proof, plan_revision_digest=revision)
            for proof in active.claim_proofs
        ),
    )


def test_stale_binding_uses_zero_llm_readback_before_one_diagnosis(stale_kernel):
    stale_kernel.advance_clock(minutes=30)
    stale_kernel.effects.stale_state = StaleReadbackState.AMBIGUOUS_RUNNING
    stale_kernel.kernel.advance(stale_kernel.handle)
    assert stale_kernel.effects.zero_llm_readbacks == 1
    assert stale_kernel.effects.coordinator_diagnoses == 1
    stale_kernel.kernel.advance(stale_kernel.handle)
    assert stale_kernel.effects.coordinator_diagnoses == 1


@pytest.mark.parametrize(
    "state",
    (
        StaleReadbackState.TERMINAL,
        StaleReadbackState.IDLE,
        StaleReadbackState.PERMISSION_WAITING,
        StaleReadbackState.CANDIDATE_RECEIVED,
        StaleReadbackState.PROVIDER_UNAVAILABLE,
    ),
)
def test_classified_stale_readback_uses_no_coordinator(stale_kernel, state):
    stale_kernel.advance_clock(minutes=30)
    stale_kernel.effects.stale_state = state
    stale_kernel.kernel.advance(stale_kernel.handle)
    assert stale_kernel.effects.zero_llm_readbacks == 1
    assert stale_kernel.effects.coordinator_diagnoses == 0


def test_stale_effects_have_no_transcript_or_daemon_restart_surface(stale_kernel):
    assert not hasattr(stale_kernel.effects, "read_transcript")
    assert not hasattr(stale_kernel.effects, "restart_daemon")


@pytest.mark.parametrize("disposition", tuple(StaleDiagnosisDisposition))
def test_stale_diagnosis_covers_all_closed_dispositions(stale_kernel, disposition):
    stale_kernel.advance_clock(minutes=30)
    stale_kernel.effects.diagnosis_disposition = disposition
    stale_kernel.kernel.advance(stale_kernel.handle)
    assert stale_kernel.effects.coordinator_diagnoses == 1
    state = stale_kernel.kernel._load(stale_kernel.handle)
    assert state is not None
    run = next(iter(state["runs"].values()))
    assert run["stale_disposition"] == disposition.value
    if disposition is StaleDiagnosisDisposition.DECISION:
        assert run["phase"] == "decision"
        assert run["reason"] == "RuntimeBindingStale"
    else:
        assert run["phase"] == "running"
        assert run["slot_held"] is True


def test_watchdog_snapshot_does_not_create_or_migrate_kernel_state(
    kernel_with_one_ticket,
):
    kernel, _effects, campaign = kernel_with_one_ticket
    _bind_successor_fixture_to_campaign(kernel, campaign)
    kernel.advance(campaign)
    before = kernel._store_path.read_bytes()
    snapshot = kernel.watchdog_snapshot(campaign)
    after = kernel._store_path.read_bytes()
    assert after == before
    assert snapshot.campaign == campaign


def test_active_campaigns_reads_existing_nonterminal_campaigns(
    kernel_with_one_ticket,
):
    kernel, _effects, campaign = kernel_with_one_ticket
    _bind_successor_fixture_to_campaign(kernel, campaign)
    kernel.advance(campaign)
    before = kernel._store_path.read_bytes()
    assert kernel.active_campaigns() == (campaign,)
    assert kernel._store_path.read_bytes() == before


@pytest.mark.parametrize(
    "hint", ["worker-report", "workspace-head", "raw-log", "duplicate-callback"]
)
def test_hint_does_not_change_trusted_progress_digest(kernel_with_one_ticket, hint):
    kernel, _effects, campaign = kernel_with_one_ticket
    _bind_successor_fixture_to_campaign(kernel, campaign)
    kernel.advance(campaign)
    before = kernel.watchdog_snapshot(campaign)
    kernel.advance(campaign, f"hint:{hint}")
    after = kernel.watchdog_snapshot(campaign)
    assert after.trusted_progress_digest == before.trusted_progress_digest


def test_exact_persisted_candidate_receipt_is_a_trusted_progress_input(
    kernel_with_candidate_receipt,
):
    kernel, _effects, campaign, receipt = kernel_with_candidate_receipt
    snapshot = kernel.watchdog_snapshot(campaign)
    assert snapshot.candidate_receipt_digests == (receipt.digest,)


def test_changed_persisted_candidate_receipt_fails_closed(
    kernel_with_candidate_receipt,
):
    kernel, _effects, campaign, _receipt = kernel_with_candidate_receipt
    with sqlite3.connect(kernel._store_path) as connection:
        row = connection.execute(
            "SELECT state_json FROM v8_execution_kernel_campaigns WHERE repository=? AND campaign_key=?",
            (campaign.repository, campaign.campaign_key),
        ).fetchone()
        state = json.loads(row[0])
        run = next(iter(state["runs"].values()))
        run["candidate_receipt"]["candidate_tree_oid"] = "f" * 40
        connection.execute(
            "UPDATE v8_execution_kernel_campaigns SET state_json=? WHERE repository=? AND campaign_key=?",
            (
                json.dumps(state, separators=(",", ":"), sort_keys=True),
                campaign.repository,
                campaign.campaign_key,
            ),
        )
    with pytest.raises(ExecutionKernelError) as raised:
        kernel.watchdog_snapshot(campaign)
    assert raised.value.code == "EXECUTION_STORE_INVALID"


@pytest.mark.parametrize(
    ("binding_field", "replacement"),
    (
        ("repository", "owner/other-repository"),
        ("campaign_key", "campaign:other"),
        ("campaign_handle", "campaign:other"),
        ("plan_revision_digest", "f" * 64),
        ("work_run_key", "work-run:other"),
        ("ticket_key", "issue:other"),
        ("runtime_subject_digest", "e" * 64),
    ),
)
def test_candidate_receipt_binding_tamper_fails_closed(
    kernel_with_candidate_receipt,
    binding_field,
    replacement,
):
    kernel, _effects, campaign, receipt = kernel_with_candidate_receipt
    with sqlite3.connect(kernel._store_path) as connection:
        row = connection.execute(
            "SELECT state_json FROM v8_execution_kernel_campaigns WHERE repository=? AND campaign_key=?",
            (campaign.repository, campaign.campaign_key),
        ).fetchone()
        state = json.loads(row[0])
        run = state["runs"][receipt.ticket_key]
        tampered = replace(
            receipt,
            **{binding_field: replacement, "receipt_digest": None},
        )
        run["candidate_receipt"] = tampered.canonical()
        run["candidate_receipt_digest"] = tampered.digest
        state["candidate_receipts"] = [tampered.canonical()]
        connection.execute(
            "UPDATE v8_execution_kernel_campaigns SET state_json=? WHERE repository=? AND campaign_key=?",
            (
                json.dumps(state, separators=(",", ":"), sort_keys=True),
                campaign.repository,
                campaign.campaign_key,
            ),
        )
    with pytest.raises(ExecutionKernelError) as raised:
        kernel.watchdog_snapshot(campaign)
    assert raised.value.code == "EXECUTION_STORE_INVALID"


def test_missing_persisted_candidate_receipt_digest_fails_closed(
    kernel_with_candidate_receipt,
):
    kernel, _effects, campaign, _receipt = kernel_with_candidate_receipt
    with sqlite3.connect(kernel._store_path) as connection:
        row = connection.execute(
            "SELECT state_json FROM v8_execution_kernel_campaigns WHERE repository=? AND campaign_key=?",
            (campaign.repository, campaign.campaign_key),
        ).fetchone()
        state = json.loads(row[0])
        run = next(iter(state["runs"].values()))
        del run["candidate_receipt_digest"]
        connection.execute(
            "UPDATE v8_execution_kernel_campaigns SET state_json=? WHERE repository=? AND campaign_key=?",
            (
                json.dumps(state, separators=(",", ":"), sort_keys=True),
                campaign.repository,
                campaign.campaign_key,
            ),
        )
    with pytest.raises(ExecutionKernelError) as raised:
        kernel.watchdog_snapshot(campaign)
    assert raised.value.code == "EXECUTION_STORE_INVALID"
    with pytest.raises(ExecutionKernelError) as raised:
        kernel.read_candidate_receipt(campaign, "issue:114")
    assert raised.value.code == "EXECUTION_STORE_INVALID"


def test_watchdog_projections_do_not_create_missing_kernel_store(
    tmp_path,
    kernel_with_one_ticket,
):
    kernel, effects, campaign = kernel_with_one_ticket
    original_store = kernel._store_path
    original_bytes = original_store.read_bytes()
    missing_store = tmp_path / "never-created.sqlite3"
    kernel._store_path = missing_store
    assert not missing_store.exists()

    with pytest.raises(ExecutionKernelError) as active_error:
        kernel.active_campaigns()
    assert active_error.value.code == "EXECUTION_STORE_INVALID"
    assert not missing_store.exists()

    with pytest.raises(ExecutionKernelError) as snapshot_error:
        kernel.watchdog_snapshot(campaign)
    assert snapshot_error.value.code == "EXECUTION_STORE_INVALID"
    assert not missing_store.exists()
    assert original_store.read_bytes() == original_bytes
    assert effects.executed == []


def test_watchdog_projections_do_not_invoke_real_effects(
    kernel_with_one_ticket,
):
    kernel, effects, campaign = kernel_with_one_ticket
    _bind_successor_fixture_to_campaign(kernel, campaign)
    kernel.advance(campaign)
    executed_before = tuple(effects.executed)
    store_before = kernel._store_path.read_bytes()

    assert kernel.active_campaigns() == (campaign,)
    snapshot = kernel.watchdog_snapshot(campaign)

    assert snapshot.campaign == campaign
    assert tuple(effects.executed) == executed_before
    assert kernel._store_path.read_bytes() == store_before


def test_last_wake_ref_is_diagnostic_but_not_trusted_progress(kernel_with_one_ticket):
    kernel, _effects, campaign = kernel_with_one_ticket
    _bind_successor_fixture_to_campaign(kernel, campaign)
    kernel.advance(campaign)
    before = kernel.watchdog_snapshot(campaign)
    kernel.advance(campaign, "watchdog:runtime:7:semantic:issue:113")
    after = kernel.watchdog_snapshot(campaign)
    run = kernel.inspect(campaign).work_runs[0]
    assert run.last_wake_ref == "watchdog:runtime:7:semantic:issue:113"
    assert after.last_wake_refs == (run.last_wake_ref,)
    assert after.trusted_progress_digest == before.trusted_progress_digest


def test_raw_wake_preserves_stale_progress_fields_and_state_version(
    kernel_with_one_ticket,
):
    kernel, effects, campaign = kernel_with_one_ticket
    _bind_successor_fixture_to_campaign(kernel, campaign)
    kernel.advance(campaign)
    state = kernel._load(campaign)
    assert state is not None
    run = next(iter(state["runs"].values()))
    run["last_trusted_progress_at"] = "2026-08-03T09:00:00+00:00"
    run["stale_due_at"] = kernel._timestamp(
        kernel._clock_value() + timedelta(hours=1)
    )
    state["state_version"] = 7
    kernel._save(campaign, state)
    before = kernel._load(campaign)
    assert before is not None
    before_run = next(iter(before["runs"].values()))
    executed_before = tuple(effects.executed)

    kernel.advance(campaign, "watchdog:runtime:8:semantic:issue:113")

    after = kernel._load(campaign)
    assert after is not None
    after_run = next(iter(after["runs"].values()))
    assert after_run["last_trusted_progress_at"] == before_run["last_trusted_progress_at"]
    assert after_run["stale_due_at"] == before_run["stale_due_at"]
    assert after["state_version"] == before["state_version"]
    assert after["trusted_progress_revision"] == before["trusted_progress_revision"]
    assert tuple(effects.executed) == executed_before


def test_watchdog_snapshot_projects_binding_due_and_diagnosis_state(
    kernel_with_one_ticket,
):
    kernel, _effects, campaign = kernel_with_one_ticket
    _bind_successor_fixture_to_campaign(kernel, campaign)
    kernel.advance(campaign)
    state = kernel._load(campaign)
    assert state is not None
    run = next(iter(state["runs"].values()))
    run["semantic_action_id"] = "binding:active"
    run["next_check_at"] = "2026-08-03T10:00:00+00:00"
    state["diagnosed_binding_ids"] = ["binding:diagnosed"]
    kernel._save(campaign, state)

    snapshot = kernel.watchdog_snapshot(campaign)

    assert snapshot.next_check_at == "2026-08-03T10:00:00+00:00"
    assert snapshot.active_binding_ids == ("binding:active",)
    assert snapshot.diagnosed_binding_ids == ("binding:diagnosed",)


def test_replayed_wake_ref_does_not_repeat_read_back_effect(kernel_with_one_ticket):
    kernel, effects, campaign = kernel_with_one_ticket
    wake_ref = "watchdog:runtime:9:semantic:issue:113"
    _bind_successor_fixture_to_campaign(kernel, campaign)

    kernel.advance(campaign, wake_ref)
    kernel.advance(campaign, wake_ref)

    assert len(effects.executed) == 1


def test_due_stale_binding_cannot_be_bypassed_by_a_wake_hint(stale_kernel):
    stale_kernel.advance_clock(minutes=30)
    stale_kernel.effects.calls.clear()
    stale_kernel.effects.stale_state = StaleReadbackState.AMBIGUOUS_RUNNING

    stale_kernel.kernel.advance(
        stale_kernel.handle,
        "watchdog:runtime:due:semantic:issue:109",
    )

    assert stale_kernel.effects.calls[0] == ("readback", "stale_readback")
    assert [kind for _operation, kind in stale_kernel.effects.calls].index(
        "stale_readback"
    ) < [kind for _operation, kind in stale_kernel.effects.calls].index(
        "semantic_execution"
    )


def test_candidate_received_requires_an_exact_persisted_candidate_receipt(stale_kernel):
    stale_kernel.advance_clock(minutes=30)
    stale_kernel.effects.stale_state = StaleReadbackState.CANDIDATE_RECEIVED
    stale_kernel.effects.omit_candidate_receipt = True

    with pytest.raises(ExecutionKernelError) as raised:
        stale_kernel.kernel.advance(stale_kernel.handle)

    assert raised.value.code == "EFFECT_READBACK_INVALID"
    state = stale_kernel.kernel._load(stale_kernel.handle)
    assert state is not None
    run = next(iter(state["runs"].values()))
    assert run["candidate_receipt"] is None


@pytest.mark.parametrize(
    "state",
    (StaleReadbackState.PERMISSION_WAITING, StaleReadbackState.PROVIDER_UNAVAILABLE),
)
def test_stale_wait_readback_keeps_the_worker_slot_until_park_or_terminal(
    stale_kernel, state
):
    stale_kernel.advance_clock(minutes=30)
    stale_kernel.effects.stale_state = state
    stale_kernel.kernel.advance(stale_kernel.handle)

    run = next(iter(stale_kernel.kernel._load(stale_kernel.handle)["runs"].values()))
    assert run["slot_held"] is True
    assert run["claim_state"] == "held"

    semantic = next(
        action
        for action in reversed(stale_kernel.effects.executed)
        if action.kind == "semantic_execution"
    )
    stale_kernel.effects.readbacks[semantic.stable_action_id] = WorkRunObservation(
        phase="parked",
        stable_action_id=semantic.stable_action_id,
        receipt_digest=digest_value({"action": semantic.stable_action_id, "phase": "parked"}),
        runtime_binding_id="binding:initial",
    )
    stale_kernel.kernel.advance(stale_kernel.handle, "watchdog:runtime:park-proof")
    run = next(iter(stale_kernel.kernel._load(stale_kernel.handle)["runs"].values()))
    assert run["slot_held"] is False
    assert run["claim_state"] == "released"


def test_post_identity_wait_readback_keeps_the_worker_slot(stale_kernel):
    semantic = next(
        action
        for action in reversed(stale_kernel.effects.executed)
        if action.kind == "semantic_execution"
    )
    stale_kernel.effects.readbacks[semantic.stable_action_id] = WorkRunObservation(
        phase="wait",
        stable_action_id=semantic.stable_action_id,
        receipt_digest=digest_value(
            {"action": semantic.stable_action_id, "phase": "wait"}
        ),
        binding_established=True,
        runtime_binding_id="binding:initial",
    )

    stale_kernel.kernel.advance(stale_kernel.handle, "watchdog:runtime:wait")

    run = next(iter(stale_kernel.kernel._load(stale_kernel.handle)["runs"].values()))
    assert run["phase"] == "wait"
    assert run["slot_held"] is True
    assert run["claim_state"] == "held"


def test_stale_diagnosis_crash_fences_the_exact_action_to_readback_only(stale_kernel):
    stale_kernel.advance_clock(minutes=30)
    stale_kernel.effects.stale_state = StaleReadbackState.AMBIGUOUS_RUNNING
    stale_kernel.effects.raise_diagnosis = True

    with pytest.raises(RuntimeError, match="stale diagnosis crashed"):
        stale_kernel.kernel.advance(stale_kernel.handle)

    stale_kernel.effects.raise_diagnosis = False
    stale_kernel.kernel.advance(stale_kernel.handle)
    assert stale_kernel.effects.coordinator_diagnoses == 1
    state = stale_kernel.kernel._load(stale_kernel.handle)
    assert state is not None
    run = next(iter(state["runs"].values()))
    diagnosis_id = run["stale_diagnosis_action_id"]
    assert state["effects"][diagnosis_id]["execute_attempted"] is True
    assert run["stale_disposition"] is None

    stale_kernel.effects.readbacks[diagnosis_id] = StaleDiagnosisObservation(
        stable_action_id=diagnosis_id,
        runtime_binding_id="binding:initial",
        disposition=StaleDiagnosisDisposition.CONTINUE,
        receipt_digest=digest_value({"action": diagnosis_id, "disposition": "continue"}),
    )
    stale_kernel.kernel.advance(stale_kernel.handle)
    assert stale_kernel.effects.coordinator_diagnoses == 1


def test_stale_diagnosis_action_carries_a_bounded_persisted_packet(stale_kernel):
    stale_kernel.advance_clock(minutes=30)
    stale_kernel.effects.stale_state = StaleReadbackState.AMBIGUOUS_RUNNING
    stale_kernel.kernel.advance(stale_kernel.handle)

    packet = stale_kernel.effects.diagnosis_packets[0]
    assert isinstance(packet, StaleDiagnosisPacket)
    assert packet.ticket_key == "issue:109"
    assert packet.runtime_binding_id == "binding:initial"
    assert len(packet.transcript_tail) <= StaleDiagnosisPacket.MAX_TRANSCRIPT_ITEMS
    assert sum(len(item) for item in packet.transcript_tail) <= StaleDiagnosisPacket.MAX_TRANSCRIPT_BYTES
    assert packet.packet_digest is not None
    state = stale_kernel.kernel._load(stale_kernel.handle)
    assert state is not None
    diagnosis = next(
        action
        for action in state["effects"].values()
        if action.get("kind") == "stale_diagnosis"
    )
    assert diagnosis["packet_digest"] == packet.digest
    assert diagnosis["packet_identity"] == packet.identity


@pytest.mark.parametrize("field", ("candidate_identities", "transcript_tail"))
def test_stale_diagnosis_packet_requires_json_array_canonical_fields(
    stale_kernel, field
):
    stale_kernel.advance_clock(minutes=30)
    stale_kernel.effects.stale_state = StaleReadbackState.AMBIGUOUS_RUNNING
    stale_kernel.kernel.advance(stale_kernel.handle)
    packet = stale_kernel.effects.diagnosis_packets[0]
    canonical = packet.canonical()
    malformed = dict(canonical)
    malformed[field] = ("candidate:one",) if field == "candidate_identities" else (
        "tail",
    )
    body = {
        key: value
        for key, value in malformed.items()
        if key not in {"packet_digest", "packet_identity"}
    }
    malformed["packet_digest"] = digest_value(body)
    malformed["packet_identity"] = f"stale-diagnosis-packet:{malformed['packet_digest'][:32]}"

    with pytest.raises(ExecutionKernelError) as raised:
        StaleDiagnosisPacket.from_canonical(malformed)
    assert raised.value.code == "EXECUTION_STORE_INVALID"


def test_restart_does_not_rekey_stale_diagnosis_as_semantic_resume(stale_kernel):
    stale_kernel.advance_clock(minutes=30)
    stale_kernel.effects.stale_state = StaleReadbackState.AMBIGUOUS_RUNNING
    stale_kernel.kernel.advance(stale_kernel.handle)
    before = stale_kernel.kernel._load(stale_kernel.handle)
    assert before is not None
    run = next(iter(before["runs"].values()))
    diagnosis_id = run["stale_diagnosis_action_id"]
    assert diagnosis_id is not None
    assert before["effects"][diagnosis_id]["kind"] == "stale_diagnosis"

    restarted = ExecutionKernel(
        store_path=stale_kernel.kernel._store_path,
        plan_control=stale_kernel.kernel._plan_control,
        effects=stale_kernel.effects,
        _clock=stale_kernel.clock,
    )
    restarted.advance(stale_kernel.handle)

    after = restarted._load(stale_kernel.handle)
    assert after is not None
    assert after["runs"]["issue:109"]["last_action_id"] == diagnosis_id
    assert after["effects"][diagnosis_id]["kind"] == "stale_diagnosis"


@pytest.mark.parametrize(
    ("disposition", "follow_up_kind", "action_kind"),
    (
        (
            StaleDiagnosisDisposition.GUIDE_SAME_WORKER,
            StaleFollowUpKind.GUIDANCE,
            "stale_guidance",
        ),
        (
            StaleDiagnosisDisposition.RECOVER_SAME_BINDING,
            StaleFollowUpKind.SAME_BINDING_RECOVERY,
            "stale_same_binding_recovery",
        ),
    ),
)
def test_stale_follow_up_dispositions_invoke_typed_idempotent_effects(
    stale_kernel, disposition, follow_up_kind, action_kind
):
    stale_kernel.advance_clock(minutes=30)
    stale_kernel.effects.stale_state = StaleReadbackState.AMBIGUOUS_RUNNING
    stale_kernel.effects.diagnosis_disposition = disposition
    stale_kernel.kernel.advance(stale_kernel.handle)

    follow_ups = [
        action for action in stale_kernel.effects.executed if action.kind == action_kind
    ]
    assert len(follow_ups) == 1
    assert follow_ups[0].runtime_binding_id == "binding:initial"
    assert follow_ups[0].stale_follow_up_kind is follow_up_kind
    stale_kernel.kernel.advance(stale_kernel.handle, "watchdog:runtime:follow-up-replay")
    assert len(
        [action for action in stale_kernel.effects.executed if action.kind == action_kind]
    ) == 1
    run = next(iter(stale_kernel.kernel._load(stale_kernel.handle)["runs"].values()))
    assert run["runtime_binding_id"] == "binding:initial"


def _terminal_binding_evidence(prior_action_id: str) -> TerminalBindingEvidence:
    return TerminalBindingEvidence(
        prior_action_id=prior_action_id,
        prior_runtime_binding_id="binding:initial",
        agent_id="agent:initial",
        session_id="session:initial",
        workspace_id="workspace:initial",
        terminal_state="terminal",
        fence_digest="7" * 64,
        checkpoint_digest="8" * 64,
    )


class _BindingReplacementEffects:
    def __init__(self, *, replacement_evidence: bool) -> None:
        self.replacement_evidence = replacement_evidence
        self.executed: list[WorkRunAction] = []
        self.readbacks: dict[str, object] = {}
        self.next_phase: str | None = None
        self.next_binding = "binding:replacement"
        self.replacement_count = 0
        self.prior_action_id: str | None = None

    def readback(self, action: WorkRunAction) -> object | None:
        return self.readbacks.get(action.stable_action_id)

    def execute(self, action: WorkRunAction) -> WorkRunObservation:
        self.executed.append(action)
        if action.kind == "semantic_execution" and self.next_phase is not None:
            phase = self.next_phase
            self.next_phase = None
            self.prior_action_id = action.stable_action_id
            observation = WorkRunObservation(
                phase=phase,
                stable_action_id=action.stable_action_id,
                receipt_digest=digest_value({"action": action.stable_action_id, "phase": phase}),
                runtime_binding_id="binding:initial",
                agent_id="agent:initial",
                session_id="session:initial",
                workspace_id="workspace:initial",
            )
        elif action.kind == "semantic_resume":
            self.replacement_count += 1
            evidence = (
                _terminal_binding_evidence(self.prior_action_id or "")
                if self.replacement_evidence
                else None
            )
            observation = WorkRunObservation(
                phase="running",
                stable_action_id=action.stable_action_id,
                receipt_digest=digest_value({"action": action.stable_action_id, "phase": "running"}),
                runtime_binding_id=self.next_binding,
                agent_id="agent:replacement",
                session_id="session:replacement",
                workspace_id="workspace:replacement",
                terminal_binding_evidence=evidence,
            )
        else:
            observation = WorkRunObservation(
                phase="running",
                stable_action_id=action.stable_action_id,
                receipt_digest=digest_value({"action": action.stable_action_id, "phase": "running"}),
                runtime_binding_id="binding:initial",
            )
        self.readbacks[action.stable_action_id] = observation
        return observation


def _prepare_parked_binding_replacement(kernel, effects, campaign):
    effects.next_phase = "parked"
    kernel.advance(campaign)


def test_runtime_binding_replacement_requires_terminal_binding_evidence(tmp_path):
    active, campaign = _minimal_active_campaign(("issue:109",))
    effects = _BindingReplacementEffects(replacement_evidence=False)
    kernel = ExecutionKernel(
        store_path=tmp_path / "binding-replacement.db",
        plan_control=_StaticPlanReader(active),
        effects=effects,
    )
    _bind_successor_fixture_to_campaign(kernel, campaign)
    _prepare_parked_binding_replacement(kernel, effects, campaign)
    with pytest.raises(ExecutionKernelError) as raised:
        kernel.advance(campaign, "watchdog:runtime:replacement")
    assert raised.value.code == "EFFECT_READBACK_INVALID"


def test_terminal_binding_evidence_allows_only_one_persisted_replacement(tmp_path):
    active, campaign = _minimal_active_campaign(("issue:109",))
    effects = _BindingReplacementEffects(replacement_evidence=True)
    kernel = ExecutionKernel(
        store_path=tmp_path / "binding-replacement-once.db",
        plan_control=_StaticPlanReader(active),
        effects=effects,
    )
    _bind_successor_fixture_to_campaign(kernel, campaign)
    _prepare_parked_binding_replacement(kernel, effects, campaign)
    kernel.advance(campaign, "watchdog:runtime:replacement")

    state = kernel._load(campaign)
    assert state is not None
    run = next(iter(state["runs"].values()))
    assert run["binding_replacement_ordinal"] == 1
    first_execution = next(
        action for action in effects.executed if action.kind == "semantic_execution"
    )
    assert run["terminal_binding_evidence"]["prior_action_id"] == first_execution.stable_action_id
    assert run["runtime_agent_id"] == "agent:replacement"
    assert run["runtime_session_id"] == "session:replacement"
    assert run["runtime_workspace_id"] == "workspace:replacement"
    assert run["terminal_binding_evidence"]["agent_id"] == "agent:initial"

    effects.next_binding = "binding:replacement:again"
    effects.readbacks[first_execution.stable_action_id] = WorkRunObservation(
        phase="parked",
        stable_action_id=first_execution.stable_action_id,
        receipt_digest=digest_value(
            {"action": first_execution.stable_action_id, "phase": "parked"}
        ),
        runtime_binding_id="binding:replacement",
    )
    kernel.advance(campaign, "watchdog:runtime:park-again")
    with pytest.raises(ExecutionKernelError) as raised:
        kernel.advance(campaign, "watchdog:runtime:replacement-again")
    assert raised.value.code == "EFFECT_READBACK_INVALID"


def test_campaign_save_uses_durable_compare_and_swap_across_kernel_instances(tmp_path):
    active, campaign = _minimal_active_campaign(("issue:109",))
    first = ExecutionKernel(
        store_path=tmp_path / "campaign-cas.db",
        plan_control=_StaticPlanReader(active),
        effects=_StaleRecordingEffects(),
    )
    _bind_successor_fixture_to_campaign(first, campaign)
    first.advance(campaign)
    second = ExecutionKernel(
        store_path=tmp_path / "campaign-cas.db",
        plan_control=_StaticPlanReader(first._plan_control.active),
        effects=_StaleRecordingEffects(),
    )
    winner = first._load(campaign)
    loser = second._load(campaign)
    assert winner is not None and loser is not None
    winner["race_marker"] = "winner"
    loser["race_marker"] = "loser"
    first._save(campaign, winner)

    with pytest.raises(ExecutionKernelError) as raised:
        second._save(campaign, loser)
    assert raised.value.code == "EXECUTION_STORE_CAS_CONFLICT"
    persisted = first._load(campaign)
    assert persisted is not None
    assert persisted["race_marker"] == "winner"
