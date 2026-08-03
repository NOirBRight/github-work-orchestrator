import json
import sqlite3
from dataclasses import replace
from pathlib import Path
import sys

import pytest

pytest_plugins = ("v8_successor_test_support", "v8_candidate_assurance_test_support")

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.execution_kernel import ExecutionKernelError
from gwo_v8._canonical import canonical_bytes, digest_bytes, load_canonical_json
from v8_candidate_assurance_test_support import kernel_with_candidate_receipt
from v8_successor_test_support import kernel_with_one_ticket


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
