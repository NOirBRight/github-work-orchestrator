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
