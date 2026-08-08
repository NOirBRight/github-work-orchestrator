from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pytest

from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.candidate_gate import CandidateReceipt
from gwo_v8.execution_kernel import (
    CampaignStatus,
    ExecutionKernel,
    StaleBindingObservation,
    StaleReadbackState,
    WorkRunAction,
    WorkRunObservation,
)
from v8_candidate_assurance_test_support import (
    CandidateReceiptEffects,
    _minimal_candidate_campaign,
    make_candidate_receipt,
    read_kernel_state,
    write_kernel_state,
)
from v8_successor_test_support import _StaticPlanReader


pytest_plugins = ("v8_candidate_assurance_test_support",)


@dataclass
class _SequencedCandidateEffects:
    receipts: tuple[CandidateReceipt, ...]
    executed: list[WorkRunAction] = field(default_factory=list)

    def readback(self, _action: WorkRunAction) -> WorkRunObservation | None:
        return None

    def execute(self, action: WorkRunAction) -> WorkRunObservation:
        if len(self.executed) >= len(self.receipts):
            raise AssertionError("the effect owner was called after the budget boundary")
        self.executed.append(action)
        receipt = self.receipts[len(self.executed) - 1]
        return WorkRunObservation(
            phase="parked",
            stable_action_id=action.stable_action_id,
            receipt_digest=receipt.digest,
            candidate_receipt=receipt,
        )


@dataclass
class _RuntimeUnavailableCandidateEffects:
    receipts: tuple[CandidateReceipt, ...]
    executed: list[WorkRunAction] = field(default_factory=list)

    def readback(self, _action: WorkRunAction) -> WorkRunObservation | None:
        return None

    def execute(self, action: WorkRunAction) -> WorkRunObservation:
        if len(self.executed) >= len(self.receipts):
            raise AssertionError("the effect owner was called after the budget boundary")
        self.executed.append(action)
        receipt = self.receipts[len(self.executed) - 1]
        return WorkRunObservation(
            phase=(
                "runtime_unavailable"
                if len(self.executed) == len(self.receipts)
                else "parked"
            ),
            stable_action_id=action.stable_action_id,
            receipt_digest=receipt.digest,
            binding_established=True,
            candidate_receipt=receipt,
            runtime_binding_id="binding:initial",
        )


@dataclass
class _StaleCandidateEffects:
    receipt: CandidateReceipt
    executed: list[WorkRunAction] = field(default_factory=list)

    def readback(self, _action: WorkRunAction) -> StaleBindingObservation | None:
        return None

    def execute(self, action: WorkRunAction) -> StaleBindingObservation:
        assert action.kind == "stale_readback"
        self.executed.append(action)
        return StaleBindingObservation(
            stable_action_id=action.stable_action_id,
            runtime_binding_id=action.runtime_binding_id or "binding:initial",
            state=StaleReadbackState.CANDIDATE_RECEIVED,
            runtime_readback_digest="2" * 64,
            process_readback_digest="3" * 64,
            workspace_readback_digest="4" * 64,
            campaign_readback_digest="5" * 64,
            receipt_digest=self.receipt.digest,
            candidate_receipt=self.receipt,
        )


@pytest.fixture
def candidate_sequence_kernel(tmp_path):
    ticket_key = "issue:115"
    active, campaign = _minimal_candidate_campaign(ticket_key)
    foundation_receipt = make_candidate_receipt(active, campaign, ticket_key)
    effects = CandidateReceiptEffects(foundation_receipt)
    kernel = ExecutionKernel(
        store_path=tmp_path / "candidate-budget.sqlite3",
        plan_control=_StaticPlanReader(active),
        effects=effects,
    )
    kernel.advance(campaign)

    def run_sequence(candidate_oids: tuple[str, ...]):
        _, work = kernel._authoritative_active(campaign)
        state = read_kernel_state(kernel, campaign)
        run = state["runs"][ticket_key]
        state["effects"] = {}
        run["candidate_receipt"] = None
        run["candidate_receipt_digest"] = None
        run["candidate_commit_oids"] = []
        run["candidate_receipt_digests"] = []
        run["phase"] = "parked"
        run["slot_held"] = True
        run["claim_state"] = "held"
        run["last_action_id"] = None
        run["semantic_action_id"] = None
        run["resume_ordinal"] = 0
        effects.executed.clear()
        write_kernel_state(kernel, campaign, state)

        receipts: list[CandidateReceipt] = []
        for ordinal, candidate_oid in enumerate(candidate_oids, start=1):
            receipt = make_candidate_receipt(
                active,
                campaign,
                ticket_key,
                candidate_commit_oid=candidate_oid,
                candidate_tree_oid=candidate_oid,
            )
            receipts.append(receipt)
            state = read_kernel_state(kernel, campaign)
            run = state["runs"][ticket_key]
            run["candidate_receipt"] = receipt.canonical()
            run["phase"] = "parked"
            run["slot_held"] = True
            run["claim_state"] = "held"
            effects.receipt = receipt
            write_kernel_state(kernel, campaign, state)
            kernel._perform_due_effect(
                active,
                state,
                work,
                ticket_key,
                wake_ref=f"candidate-sequence:{ordinal}",
            )
        return (
            kernel,
            effects,
            campaign,
            kernel.advance(campaign),
            tuple(receipts),
        )

    return run_sequence


def test_kernel_records_distinct_candidate_oids_only(candidate_sequence_kernel):
    kernel, effects, campaign, _outcome, receipts = candidate_sequence_kernel(
        ("4" * 40, "4" * 40, "5" * 40)
    )
    state = read_kernel_state(kernel, campaign)
    assert state["runs"]["issue:115"]["candidate_commit_oids"] == [
        "4" * 40,
        "5" * 40,
    ]
    assert state["runs"]["issue:115"]["candidate_receipt_digests"] == list(
        dict.fromkeys(receipt.digest for receipt in receipts)
    )
    assert len(effects.executed) == 3


def test_fourth_distinct_candidate_returns_decision_before_effect(
    candidate_sequence_kernel,
):
    kernel, effects, campaign, outcome, receipts = candidate_sequence_kernel(
        ("4" * 40, "5" * 40, "6" * 40, "7" * 40)
    )
    assert outcome.status == CampaignStatus.DECISION
    assert outcome.reason == "CandidateBudgetExhausted:issue:115"
    assert len(effects.executed) == 3
    state = read_kernel_state(kernel, campaign)
    run = state["runs"]["issue:115"]
    assert run["phase"] == "decision"
    assert run["slot_held"] is False
    assert run["claim_state"] == "released"
    assert run["candidate_receipt_digests"][-1] == receipts[-1].digest


def test_restart_does_not_reset_candidate_bound(candidate_sequence_kernel):
    kernel, effects, campaign, _outcome, _receipts = candidate_sequence_kernel(
        ("4" * 40, "5" * 40)
    )
    restarted = ExecutionKernel(
        store_path=kernel._store_path,
        plan_control=kernel._plan_control,
        effects=effects,
    )
    state = read_kernel_state(restarted, campaign)
    assert state["runs"]["issue:115"]["candidate_commit_oids"] == [
        "4" * 40,
        "5" * 40,
    ]


def test_due_candidate_readback_records_history_before_the_next_advance(tmp_path):
    ticket_key = "issue:115"
    active, campaign = _minimal_candidate_campaign(ticket_key)
    candidate_oids = ("4" * 40, "4" * 40, "5" * 40, "6" * 40, "7" * 40)
    receipts = tuple(
        make_candidate_receipt(
            active,
            campaign,
            ticket_key,
            candidate_commit_oid=oid,
            candidate_tree_oid=oid,
        )
        for oid in candidate_oids
    )
    effects = _SequencedCandidateEffects(receipts)
    kernel = ExecutionKernel(
        store_path=tmp_path / "due-candidate-budget.sqlite3",
        plan_control=_StaticPlanReader(active),
        effects=effects,
    )

    expected_oids: list[str] = []
    expected_digests: list[str] = []
    for ordinal, receipt in enumerate(receipts, start=1):
        outcome = kernel.advance(campaign, wake_ref=f"candidate:{ordinal}")
        expected_oids.append(receipt.candidate_commit_oid)
        if receipt.candidate_commit_oid not in expected_oids[:-1]:
            expected_digests.append(receipt.digest)
        state = read_kernel_state(kernel, campaign)
        run = state["runs"][ticket_key]
        assert run["candidate_commit_oids"] == list(dict.fromkeys(expected_oids))
        assert run["candidate_receipt_digests"] == expected_digests
        if ordinal == 5:
            assert outcome.status == CampaignStatus.DECISION
            assert outcome.reason == f"CandidateBudgetExhausted:{ticket_key}"

    assert len(effects.executed) == 5


def test_stale_candidate_readback_after_restart_cannot_bypass_budget(tmp_path):
    ticket_key = "issue:115"
    active, campaign = _minimal_candidate_campaign(ticket_key)
    candidate_oids = ("4" * 40, "5" * 40, "6" * 40)
    receipts = tuple(
        make_candidate_receipt(
            active,
            campaign,
            ticket_key,
            candidate_commit_oid=oid,
            candidate_tree_oid=oid,
        )
        for oid in candidate_oids
    )
    effects = _SequencedCandidateEffects(receipts)
    kernel = ExecutionKernel(
        store_path=tmp_path / "stale-candidate-budget.sqlite3",
        plan_control=_StaticPlanReader(active),
        effects=effects,
    )
    for ordinal in range(1, 4):
        kernel.advance(campaign, wake_ref=f"candidate:{ordinal}")

    state = read_kernel_state(kernel, campaign)
    run = state["runs"][ticket_key]
    assert run["candidate_commit_oids"] == list(candidate_oids)
    run["phase"] = "running"
    run["slot_held"] = True
    run["claim_state"] = "held"
    run["runtime_binding_id"] = "binding:initial"
    run["stale_due_at"] = "2026-08-06T00:00:00+00:00"
    run["stale_disposition"] = None
    run["stale_readback_action_id"] = None
    write_kernel_state(kernel, campaign, state)

    fourth_receipt = make_candidate_receipt(
        active,
        campaign,
        ticket_key,
        candidate_commit_oid="7" * 40,
        candidate_tree_oid="7" * 40,
    )
    stale_effects = _StaleCandidateEffects(fourth_receipt)
    restarted = ExecutionKernel(
        store_path=kernel._store_path,
        plan_control=_StaticPlanReader(active),
        effects=stale_effects,
        _clock=lambda: datetime.fromisoformat("2026-08-06T00:01:00+00:00"),
    )

    outcome = restarted.advance(campaign)

    assert outcome.status == CampaignStatus.DECISION
    assert outcome.reason == f"CandidateBudgetExhausted:{ticket_key}"
    state = read_kernel_state(restarted, campaign)
    run = state["runs"][ticket_key]
    assert run["candidate_commit_oids"] == [*candidate_oids, "7" * 40]
    assert run["candidate_receipt_digest"] == fourth_receipt.digest
    assert run["phase"] == "decision"
    assert run["slot_held"] is False
    assert len(stale_effects.executed) == 1


def test_candidate_budget_exhaustion_does_not_retain_runtime_unavailable_slot(tmp_path):
    ticket_key = "issue:115"
    active, campaign = _minimal_candidate_campaign(ticket_key)
    receipts = tuple(
        make_candidate_receipt(
            active,
            campaign,
            ticket_key,
            candidate_commit_oid=oid,
            candidate_tree_oid=oid,
        )
        for oid in ("4" * 40, "5" * 40, "6" * 40, "7" * 40)
    )
    effects = _RuntimeUnavailableCandidateEffects(receipts)
    kernel = ExecutionKernel(
        store_path=tmp_path / "runtime-unavailable-budget.sqlite3",
        plan_control=_StaticPlanReader(active),
        effects=effects,
    )

    for ordinal in range(1, 5):
        outcome = kernel.advance(campaign, wake_ref=f"candidate:{ordinal}")

    assert outcome.status == CampaignStatus.DECISION
    assert outcome.reason == f"CandidateBudgetExhausted:{ticket_key}"
    state = read_kernel_state(kernel, campaign)
    run = state["runs"][ticket_key]
    assert run["phase"] == "decision"
    assert run["slot_held"] is False
    assert run["claim_state"] == "released"
    assert len(effects.executed) == 4
