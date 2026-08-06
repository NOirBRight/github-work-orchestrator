from __future__ import annotations

import pytest

from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.candidate_gate import CandidateReceipt
from gwo_v8.execution_kernel import CampaignStatus, ExecutionKernel
from v8_candidate_assurance_test_support import (
    CandidateReceiptEffects,
    _minimal_candidate_campaign,
    make_candidate_receipt,
    read_kernel_state,
    write_kernel_state,
)
from v8_successor_test_support import _StaticPlanReader


pytest_plugins = ("v8_candidate_assurance_test_support",)


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
        state = read_kernel_state(kernel, campaign)
        run = state["runs"][ticket_key]
        state["effects"] = {}
        run["candidate_receipt"] = None
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
