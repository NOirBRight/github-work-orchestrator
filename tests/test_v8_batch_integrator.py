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
    DeliveryIdentityMismatch,
    MemberDeliveryObservation,
    TargetDeltaReadback,
    form_batch_members,
)
from gwo_v8.batch_patch_identity import patch_identity_v1, require_clean_base_advance
from gwo_v8._canonical import digest_value
from gwo_v8._batch_integrator_store import SqliteBatchDeliveryJournal
from gwo_v8.candidate_gate import InteractionClassification
from v8_batch_test_support import (
    make_accepted_candidate_receipt,
    make_batch_action,
    make_batch_request,
    make_ancestor_readback,
    make_batch_target,
    make_hosted_result_receipt,
    make_interaction_key,
    make_patch_entry,
    make_target_delta,
)


def test_forms_oldest_pairwise_compatible_candidates_up_to_four_without_waiting():
    queue = tuple(
        make_accepted_candidate_receipt(
            ticket_key=f"issue:{n}", accepted_sequence=n
        )
        for n in range(1, 6)
    )
    # The ordinary conflict is the exact same ordinary key as issue:1. Other
    # ordinary keys are independent and must remain pairwise compatible.
    queue = (
        queue[0],
        replace(queue[1], interaction_keys=queue[0].interaction_keys),
        queue[2],
        queue[3],
        queue[4],
    )

    selected = form_batch_members(queue, make_batch_target(), member_limit=4)

    assert [item.ticket_key for item in selected] == [
        "issue:1",
        "issue:3",
        "issue:4",
        "issue:5",
    ]


def test_formation_is_same_campaign_and_strict_or_gitlink_is_singleton():
    seed = make_accepted_candidate_receipt(ticket_key="issue:1")
    other_campaign = make_accepted_candidate_receipt(
        ticket_key="issue:2", campaign_key="campaign:b", accepted_sequence=2
    )
    strict = make_accepted_candidate_receipt(
        ticket_key="issue:3", assurance="strict", accepted_sequence=3
    )
    gitlink = make_accepted_candidate_receipt(
        ticket_key="issue:4", gitlink_change=True, accepted_sequence=4
    )

    assert form_batch_members((seed, other_campaign), make_batch_target(), member_limit=4) == (seed,)
    assert form_batch_members((seed, strict), make_batch_target(), member_limit=4) == (seed,)
    assert form_batch_members((strict,), make_batch_target(), member_limit=4) == (strict,)
    assert form_batch_members((seed, gitlink), make_batch_target(), member_limit=4) == (seed,)
    assert form_batch_members((gitlink,), make_batch_target(), member_limit=4) == (gitlink,)


def test_policy_classified_interaction_key_forces_singleton():
    protected = make_accepted_candidate_receipt(
        interaction_keys=(
            make_interaction_key(
                "schema:root", classification=InteractionClassification.PROTECTED
            ),
        )
    )
    ordinary = make_accepted_candidate_receipt(ticket_key="issue:2", accepted_sequence=2)

    assert form_batch_members(
        (protected, ordinary), make_batch_target(), member_limit=4
    ) == (protected,)


def test_member_limit_rejects_zero_or_more_than_four_and_accepts_repository_override():
    with pytest.raises(BatchIntegratorError, match="member limit"):
        BatchIntegratorConfiguration(host_member_limit=0)
    with pytest.raises(BatchIntegratorError, match="member limit"):
        BatchIntegratorConfiguration(host_member_limit=5)
    configuration = BatchIntegratorConfiguration(
        host_member_limit=4, repository_member_limits={"owner/repo": 2}
    )
    assert configuration.member_limit_for("owner/repo") == 2


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


def test_integration_lease_compare_and_swap_keeps_the_first_holder(tmp_path):
    journal = SqliteBatchDeliveryJournal(tmp_path / "v8.sqlite3")
    first = journal.acquire_integration_lease(
        "owner/repo", "action:one", "gen:1", "activation:1"
    )

    with pytest.raises(BatchIntegratorError, match="INTEGRATION_LEASE_UNAVAILABLE"):
        journal.acquire_integration_lease(
            "owner/repo", "action:two", "gen:1", "activation:1"
        )

    assert journal.read_integration_lease("owner/repo") == first


def test_same_holder_new_generation_cannot_replace_current_integration_lease(tmp_path):
    journal = SqliteBatchDeliveryJournal(tmp_path / "v8.sqlite3")
    first = journal.acquire_integration_lease(
        "owner/repo", "action:one", "gen:1", "activation:1"
    )

    with pytest.raises(BatchIntegratorError, match="INTEGRATION_LEASE_UNAVAILABLE"):
        journal.acquire_integration_lease(
            "owner/repo", "action:one", "gen:2", "activation:2"
        )

    assert journal.read_integration_lease("owner/repo") == first


def test_stale_lease_release_cannot_delete_reacquired_current_receipt(tmp_path):
    journal = SqliteBatchDeliveryJournal(tmp_path / "v8.sqlite3")
    first = journal.acquire_integration_lease(
        "owner/repo", "action:one", "gen:1", "activation:1"
    )
    journal.release_integration_lease("owner/repo", first)
    current = journal.acquire_integration_lease(
        "owner/repo", "action:one", "gen:2", "activation:2"
    )

    with pytest.raises(BatchIntegratorError, match="INTEGRATION_LEASE_OWNER_MISMATCH"):
        journal.release_integration_lease("owner/repo", first)

    assert journal.read_integration_lease("owner/repo") == current


def test_batch_journal_record_and_integration_lease_receipt_have_exact_bodies(tmp_path):
    journal = SqliteBatchDeliveryJournal(tmp_path / "v8.sqlite3")
    action = make_batch_action()
    record = journal.create_action(action, action.request_digest)
    lease = journal.acquire_integration_lease(
        "owner/repo", "action:one", "gen:1", "activation:1"
    )

    assert record.body() == {
        "stable_action_id": action.stable_action_id,
        "request_digest": action.request_digest,
        "batch_id": action.batch_id,
        "batch_sha": action.batch_sha,
        "phase": "prepared",
        "reason": "prepared",
        "retry_count": 0,
        "fallback_generation": 0,
        "state_json": "{}",
        "version": 0,
    }
    assert lease.body() == {
        "repository": "owner/repo",
        "holder": "action:one",
        "writer_generation": "gen:1",
        "activation_id": "activation:1",
    }
    assert lease.lease_digest == digest_value(
        {"kind": "integration-lease.v1", **lease.body()}
    )


def test_stale_batch_action_write_does_not_overwrite_newer_version(tmp_path):
    journal = SqliteBatchDeliveryJournal(tmp_path / "v8.sqlite3")
    action = make_batch_action()
    created = journal.create_action(action, action.request_digest)
    newer = journal.advance_action(
        created, phase="published", reason="publication read back"
    )

    with pytest.raises(BatchIntegratorError, match="BATCH_ACTION_CAS_CONFLICT"):
        journal.compare_and_swap_action(
            action.stable_action_id,
            expected_version=created.version,
            expected_phase="prepared",
            next_record=replace(
                newer, phase="integrating", version=created.version + 1
            ),
        )

    assert journal.read_action(action.stable_action_id).phase == "published"


def test_identical_terminal_hosted_receipt_replays_but_wrong_identity_fails(tmp_path):
    journal = SqliteBatchDeliveryJournal(tmp_path / "v8.sqlite3")
    receipt = make_hosted_result_receipt()
    assert journal.persist_hosted_result(receipt) == receipt
    assert journal.persist_hosted_result(receipt) == receipt

    with pytest.raises(DeliveryIdentityMismatch):
        journal.persist_hosted_result(replace(receipt, batch_sha="b" * 40))


def test_hosted_result_persistence_rejects_runtime_invalid_outcome(tmp_path):
    journal = SqliteBatchDeliveryJournal(tmp_path / "v8.sqlite3")
    forged = make_hosted_result_receipt(outcome="forged")  # type: ignore[arg-type]

    with pytest.raises(DeliveryIdentityMismatch, match="outcome"):
        journal.persist_hosted_result(forged)
