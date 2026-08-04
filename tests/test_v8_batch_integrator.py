from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.batch_integrator import (
    BatchIntegrator,
    BatchIntegratorConfiguration,
    BatchDeliveryObservation,
    BatchDeliveryProof,
    BatchIntegratorError,
    MemberDeliveryObservation,
)
from gwo_v8._canonical import digest_value
from v8_batch_test_support import (
    make_accepted_candidate_receipt,
    make_batch_request,
)


def test_accepted_candidate_receipt_digest_binds_every_delivery_fact():
    first = make_accepted_candidate_receipt(
        ticket_key="issue:1", candidate_sha="a" * 40
    )
    changed = make_accepted_candidate_receipt(
        ticket_key="issue:1",
        candidate_sha="a" * 40,
        delivery_identity_digest="b" * 64,
    )

    assert first.digest != changed.digest
    assert first.canonical()["diff_schema_version"] == "CandidateDiffRecordV1"
    assert (
        first.canonical()["review_finding_ledger_digest"]
        == first.review_finding_ledger_digest
    )


def test_accepted_candidate_receipt_rejects_noncanonical_evidence_and_sequence():
    with pytest.raises(BatchIntegratorError, match="accepted_sequence"):
        make_accepted_candidate_receipt(accepted_sequence=-1)
    with pytest.raises(BatchIntegratorError, match="evidence_digests"):
        make_accepted_candidate_receipt(evidence_digests=("f" * 64, "e" * 64))


def test_batch_request_digest_changes_when_member_set_or_target_changes():
    request = make_batch_request(
        accepted_candidates=(
            make_accepted_candidate_receipt(ticket_key="issue:1"),
        )
    )
    changed_members = replace(
        request,
        accepted_candidates=(
            request.accepted_candidates[0],
            make_accepted_candidate_receipt(ticket_key="issue:2", accepted_sequence=2),
        ),
    )
    changed_target = replace(
        request,
        target=replace(request.target, target_head_sha="b" * 40),
    )

    assert request.request_digest != changed_members.request_digest
    assert request.request_digest != changed_target.request_digest


def test_batch_action_preserves_oldest_first_member_order():
    request = make_batch_request(
        accepted_candidates=(
            make_accepted_candidate_receipt(ticket_key="issue:z", accepted_sequence=1),
            make_accepted_candidate_receipt(ticket_key="issue:a", accepted_sequence=2),
        )
    )
    integrator = BatchIntegrator(
        journal=object(),
        git=object(),
        local=object(),
        hosted=object(),
        configuration=BatchIntegratorConfiguration(),
    )

    action = integrator.prepare(request)

    assert action.member_ticket_keys == ("issue:z", "issue:a")


def test_batch_observation_preserves_exact_delivery_proof_partition():
    proof = BatchDeliveryProof.create(
        delivery_stable_action_id="delivery-action:1",
        delivery_request_digest="1" * 64,
        batch_id="2" * 64,
        batch_sha="a" * 40,
        member_ticket_keys=("issue:1",),
        local_check_receipt_digest="3" * 64,
        publication_receipt_digest="4" * 64,
        pull_request_number=1,
        pull_request_head_sha="a" * 40,
        hosted_result_receipt_digest="5" * 64,
        integration_lease_digest="6" * 64,
        target_branch="main",
        target_head_sha="b" * 40,
        target_readback_digest="7" * 64,
        target_contains_batch_sha=True,
        pull_request_merge_target_sha="b" * 40,
        merge_method="merge",
    )
    member = MemberDeliveryObservation(
        ticket_key="issue:1",
        work_run_key="work-run:1",
        candidate_sha="a" * 40,
        status="integrated",
        evidence_digests=("8" * 64,),
    )
    body = {
        "stable_action_id": "delivery-action:1",
        "batch_id": "2" * 64,
        "batch_sha": "a" * 40,
        "phase": "complete",
        "reason": "integrated",
        "retry_count": 0,
        "fallback_generation": 0,
        "members": [asdict(member)],
        "delivery_proofs": [proof.canonical()],
    }
    observation = BatchDeliveryObservation(
        stable_action_id="delivery-action:1",
        batch_id="2" * 64,
        batch_sha="a" * 40,
        phase="complete",
        reason="integrated",
        receipt_digest=digest_value({"kind": "batch-observation.v1", **body}),
        retry_count=0,
        fallback_generation=0,
        members=(member,),
        delivery_proofs=(proof,),
    )

    assert observation.canonical()["delivery_proofs"] == [proof.canonical()]
