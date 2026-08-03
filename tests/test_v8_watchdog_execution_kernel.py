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
    StaleBindingObservation,
    StaleDiagnosisDisposition,
    StaleDiagnosisObservation,
    StaleReadbackState,
    WorkRunAction,
    WorkRunObservation,
)
from gwo_v8._canonical import canonical_bytes, digest_bytes, digest_value, load_canonical_json
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
        self.zero_llm_readbacks = 0
        self.coordinator_diagnoses = 0

    def readback(self, action: WorkRunAction) -> object | None:
        return self.readbacks.get(action.stable_action_id)

    def execute(self, action: WorkRunAction) -> object:
        if action.kind in {"semantic_execution", "semantic_resume"}:
            observation: object = WorkRunObservation(
                phase="running",
                stable_action_id=action.stable_action_id,
                receipt_digest=digest_value({"action": action.stable_action_id}),
                runtime_binding_id="binding:initial",
            )
        elif action.kind == "stale_readback":
            self.zero_llm_readbacks += 1
            observation = StaleBindingObservation(
                stable_action_id=action.stable_action_id,
                runtime_binding_id="binding:initial",
                state=self.stale_state,
                runtime_readback_digest="2" * 64,
                process_readback_digest="3" * 64,
                workspace_readback_digest="4" * 64,
                campaign_readback_digest="5" * 64,
                receipt_digest=digest_value(
                    {
                        "action": action.stable_action_id,
                        "state": self.stale_state.value,
                    }
                ),
            )
        elif action.kind == "stale_diagnosis":
            self.coordinator_diagnoses += 1
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
    run["stale_due_at"] = "2026-08-03T10:00:00+00:00"
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
