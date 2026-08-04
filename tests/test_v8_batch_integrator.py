from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.batch_integrator import (
    AncestorReadback,
    BatchIntegrator,
    BatchIntegratorConfiguration,
    BatchDeliveryObservation,
    BatchDeliveryProof,
    BatchIntegratorError,
    MemberDeliveryObservation,
    TargetDeltaReadback,
)
from gwo_v8.batch_patch_identity import patch_identity_v1, require_clean_base_advance
from gwo_v8._canonical import digest_value
from gwo_v8.candidate_gate import InteractionClassification
from v8_batch_test_support import (
    make_accepted_candidate_receipt,
    make_batch_request,
    make_ancestor_readback,
    make_interaction_key,
    make_patch_entry,
    make_target_delta,
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


def test_patch_identity_v1_is_independent_of_entry_input_order():
    entries = (
        make_patch_entry("b.txt", old_oid="1" * 40, new_oid="2" * 40),
        make_patch_entry("a.txt", old_oid="3" * 40, new_oid="4" * 40),
    )
    assert patch_identity_v1("sha1", entries) == patch_identity_v1(
        "sha1", tuple(reversed(entries))
    )


def test_patch_identity_v1_changes_for_mode_binary_and_gitlink_identity():
    base = make_patch_entry("tool", old_mode="100644", new_mode="100755")
    binary = make_patch_entry("image.bin", old_oid="1" * 40, new_oid="2" * 40)
    gitlink = make_patch_entry(
        "submodule",
        old_mode="160000",
        new_mode="160000",
        old_oid="3" * 40,
        new_oid="4" * 40,
        old_object_type="gitlink",
        new_object_type="gitlink",
    )

    assert len(
        {patch_identity_v1("sha1", (entry,)) for entry in (base, binary, gitlink)}
    ) == 3


def test_clean_base_advance_rejects_recomputed_patch_identity_mismatch():
    member = make_accepted_candidate_receipt()
    with pytest.raises(BatchIntegratorError, match="PatchIdentityV1"):
        require_clean_base_advance(
            member=member,
            original_patch_digest="a" * 64,
            recomputed_patch_digest="b" * 64,
            ancestor=make_ancestor_readback(member.base_sha, "b" * 40),
            target_delta=make_target_delta(member.base_sha, "b" * 40),
        )


def test_clean_base_advance_requires_authoritative_original_base_ancestor():
    member = make_accepted_candidate_receipt()
    ancestor = make_ancestor_readback(member.base_sha, "b" * 40, is_ancestor=False)

    with pytest.raises(BatchIntegratorError, match="CLEAN_BASE_ANCESTOR_REQUIRED"):
        require_clean_base_advance(
            member=member,
            original_patch_digest=member.diff_record_digest,
            recomputed_patch_digest=member.diff_record_digest,
            ancestor=ancestor,
            target_delta=make_target_delta(member.base_sha, "b" * 40),
        )


def test_clean_base_advance_rejects_protected_target_delta_interaction_key():
    member = make_accepted_candidate_receipt()
    protected = make_interaction_key(
        "schema:root", classification=InteractionClassification.PROTECTED
    )

    with pytest.raises(
        BatchIntegratorError, match="TARGET_DELTA_PROTECTED_INTERACTION"
    ):
        require_clean_base_advance(
            member=member,
            original_patch_digest=member.diff_record_digest,
            recomputed_patch_digest=member.diff_record_digest,
            ancestor=make_ancestor_readback(member.base_sha, "b" * 40),
            target_delta=make_target_delta(
                member.base_sha, "b" * 40, interaction_keys=(protected,)
            ),
        )


def test_clean_base_advance_rejects_forged_protected_partition():
    member = make_accepted_candidate_receipt()
    protected = make_interaction_key(
        "schema:root", classification=InteractionClassification.PROTECTED
    )
    body = {
        "base_sha": member.base_sha,
        "target_head_sha": "b" * 40,
        "interaction_keys": [protected.canonical()],
        "protected_interaction_keys": [],
    }
    forged_delta = TargetDeltaReadback(
        base_sha=member.base_sha,
        target_head_sha="b" * 40,
        interaction_keys=(protected,),
        protected_interaction_keys=(),
        facts_digest=digest_value(body),
        readback_digest=digest_value(
            {"kind": "target-delta-readback.v1", **body}
        ),
    )

    with pytest.raises(BatchIntegratorError, match="protected"):
        require_clean_base_advance(
            member=member,
            original_patch_digest=member.diff_record_digest,
            recomputed_patch_digest=member.diff_record_digest,
            ancestor=make_ancestor_readback(member.base_sha, "b" * 40),
            target_delta=forged_delta,
        )
