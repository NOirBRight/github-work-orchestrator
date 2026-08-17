from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.verify_v8_root_canary import (
    ROOT_REPOSITORY,
    RootCanaryAcceptanceReceiptV1,
    RootCanaryVerificationError,
    verify_main,
    verify_root_canary,
    write_acceptance_document,
)


def _batch(
    kind: str,
    keys: list[str],
    sha: str,
    pr_number: int,
    run_id: int,
    target: str,
    candidate_receipt_digests: list[str],
    finding_ledger_digests: list[str],
) -> dict[str, object]:
    body = {
        "batch_kind": kind,
        "batch_id": f"batch:{kind}",
        "repository": ROOT_REPOSITORY,
        "campaign_key": "campaign:test-root-canary",
        "plan_revision_digest": "plan:1",
        "member_ticket_keys": keys,
        "candidate_receipt_digests": candidate_receipt_digests,
        "finding_ledger_digests": finding_ledger_digests,
        "batch_sha": sha,
        "local_suite": {"status": "passed", "head_sha": sha},
        "pull_request": {
            "number": pr_number,
            "head_sha": sha,
            "repository": ROOT_REPOSITORY,
        },
        "hosted_ci": {
            "run_id": run_id,
            "head_sha": sha,
            "conclusion": "success",
            "repository": ROOT_REPOSITORY,
        },
        "integration_lease": {"serialized": True},
        "target_readback": {
            "merge_method": "merge",
            "batch_sha_is_ancestor": True,
            "remote_target_sha": target,
            "target_branch": "main",
            "pull_request_number": pr_number,
            "pull_request_head_sha": sha,
            "pull_request_merge_target_sha": target,
            "merge_commit_sha": target,
            "repository": ROOT_REPOSITORY,
        },
        "integrated_target_sha": target,
    }
    return {**body, "receipt_digest": _independent_digest(body)}


def _independent_digest(value: object) -> str:
    encoded = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ticket(key: str, number: int) -> dict[str, object]:
    contract = {
        "number": number,
        "state": "OPEN",
        "labels": ["ready-for-agent"],
        "body": f"root canary contract {key}",
        "comments": [f"authoritative comment {key}"],
        "blocked_by": [],
        "blocker_states": [],
    }
    return {
        "key": key,
        "ticket_key": f"issue:{number}",
        **contract,
        "contract_digest": _independent_digest(contract),
        "authoritative_id": f"github-node:{number}",
    }


def _candidate(
    key: str,
    number: int,
    contract_digest: str,
    *,
    assurance: str,
) -> dict[str, object]:
    ticket_key = f"issue:{number}"
    candidate_body = {
        "kind": "candidate_receipt.v1",
        "repository": ROOT_REPOSITORY,
        "campaign_key": "campaign:test-root-canary",
        "plan_revision_digest": "plan:1",
        "ticket_key": ticket_key,
        "work_run_key": f"work-run:{key}",
        "base_commit_oid": f"base-commit:{key}",
        "base_tree_oid": f"base-tree:{key}",
        "candidate_commit_oid": f"candidate-commit:{key}",
        "candidate_tree_oid": f"candidate-tree:{key}",
        "diff_schema_version": "CandidateDiffRecordV1",
        "diff_record_digest": f"diff:{key}",
        "authority_subtree_digest": f"authority-subtree:{key}",
        "policy_witness_digest": "policy:1",
        "ticket_contract_digest": contract_digest,
        "assurance": assurance,
        "assurance_requirement_digest": f"assurance-requirement:{key}",
        "review_subject_digest": f"review-subject:{key}",
        "authoritative_note": f"candidate note {key}",
    }
    candidate_receipt = {
        **candidate_body,
        "receipt_digest": _independent_digest(candidate_body),
    }
    finding_digest = f"finding:{key}"
    accepted_body = {
        "kind": "accepted_candidate_receipt.v1",
        "repository": ROOT_REPOSITORY,
        "campaign_key": "campaign:test-root-canary",
        "plan_revision_digest": "plan:1",
        "ticket_key": ticket_key,
        "candidate_receipt_digest": candidate_receipt["receipt_digest"],
        "authority_subtree_digest": candidate_body["authority_subtree_digest"],
        "policy_witness_digest": "policy:1",
        "review_finding_ledger_digest": finding_digest,
        "assurance": assurance,
        "authoritative_note": f"accepted note {key}",
    }
    accepted_receipt = {
        **accepted_body,
        "receipt_digest": _independent_digest(accepted_body),
    }
    return {
        "ticket_key": ticket_key,
        "assurance": assurance,
        "candidate_receipt_digest": candidate_receipt["receipt_digest"],
        "candidate_receipt": candidate_receipt,
        "accepted_candidate_receipt": accepted_receipt,
    }


def _review(key: str, number: int, candidate_digest: str) -> dict[str, object]:
    finding_digest = f"finding:{key}"
    ledger_body = {
        "kind": "review_finding_ledger.v1",
        "ticket_key": f"issue:{number}",
        "candidate_receipt_digest": candidate_digest,
        "open_finding_ids": [],
        "entries": [],
        "authoritative_note": f"ledger note {key}",
    }
    return {
        "ticket_key": f"issue:{number}",
        "candidate_receipt_digest": candidate_digest,
        "open_finding_ids": [],
        "finding_ledger_digest": finding_digest,
        "finding_ledger": {
            **ledger_body,
            "ledger_digest": finding_digest,
        },
    }


def _recovery_proof() -> dict[str, object]:
    return {
        "worker_slot_limit": 4,
        "peak_worker_slots": 4,
        "ticket_keys": ["alpha", "beta", "gamma", "delta"],
        "refill_ticket_order": ["alpha", "beta", "gamma", "delta"],
        "permission_binding_pairs": [["binding:beta", "binding:beta"]],
        "permission_authorization_links": [
            {
                "ticket_key": "beta",
                "request_id": "permission-request:beta",
                "binding_id": "binding:beta",
                "before_binding_id": "binding:beta",
                "after_binding_id": "binding:beta",
                "request_digest": "permission-request-digest:beta",
                "authorization_digest": "permission-authorization:beta",
            }
        ],
        "stale_diagnosis_count_by_binding": [
            [f"binding:{key}", 1] for key in ("alpha", "beta", "gamma", "delta")
        ],
        "stale_diagnosed_binding_ids": [
            f"binding:{key}" for key in ("alpha", "beta", "gamma", "delta")
        ],
        "stale_diagnosis_authorization_links": [
            {
                "ticket_key": key,
                "binding_id": f"binding:{key}",
                "diagnosis_id": f"stale-diagnosis:{key}",
                "diagnosis_digest": f"stale-diagnosis-digest:{key}",
                "authorized": True,
            }
            for key in ("alpha", "beta", "gamma", "delta")
        ],
        "binding_count_by_ticket": [
            [key, 1] for key in ("alpha", "beta", "gamma", "delta")
        ],
        "binding_ids_by_ticket": [
            [key, [f"binding:{key}"]]
            for key in ("alpha", "beta", "gamma", "delta")
        ],
        "terminal_replacement_receipt_digests": [],
        "terminal_replacement_authorization_links": [],
        "semantic_effect_ids": ["semantic:alpha"],
        "external_effect_ids": ["external:alpha"],
        "semantic_effect_records": [
            {
                "stable_action_id": "semantic:alpha",
                "effect_digest": "semantic-effect:alpha",
            }
        ],
        "external_effect_records": [
            {
                "stable_action_id": "external:alpha",
                "effect_digest": "external-effect:alpha",
            }
        ],
        "duplicate_effect_ids": [],
        "policy_witness_digest": "policy:1",
        "authority_root_digest": "authority:1",
        "runtime_selector_digest": "selector:1",
        "fault_journal_digest": "fault:1",
    }


@dataclass(frozen=True)
class AcceptanceFixture:
    data: dict[str, object]

    def copy(self) -> dict[str, object]:
        return copy.deepcopy(self.data)


@pytest.fixture
def valid_bundle() -> AcceptanceFixture:
    tickets = [
        _ticket(key, number)
        for key, number in zip(
            ("alpha", "beta", "gamma", "delta"),
            (10, 11, 12, 13),
            strict=True,
        )
    ]
    candidates = [
        _candidate(
            ticket["key"],
            ticket["number"],
            ticket["contract_digest"],
            assurance="strict" if ticket["key"] == "delta" else "standard",
        )
        for ticket in tickets
    ]
    candidate_digests = [
        candidate["candidate_receipt_digest"] for candidate in candidates
    ]
    reviews = [
        _review(ticket["key"], ticket["number"], candidate["candidate_receipt_digest"])
        for ticket, candidate in zip(tickets, candidates, strict=True)
    ]
    finding_digests = [review["finding_ledger_digest"] for review in reviews]
    proof = _recovery_proof()
    proof.update(
        {
            "campaign_key": "campaign:test-root-canary",
            "plan_revision_digest": "plan:1",
            "policy_witness": {
                "kind": "policy_witness.v1",
                "digest": "policy:1",
                "repository": ROOT_REPOSITORY,
            },
            "authority_root": {
                "kind": "authority_root.v1",
                "digest": "authority:1",
                "plan_revision_digest": "plan:1",
            },
            "runtime_selector": {
                "kind": "runtime_selector.v1",
                "digest": "selector:1",
                "campaign_key": "campaign:test-root-canary",
            },
            "fault_journal": {
                "kind": "fault_journal.v1",
                "digest": "fault:1",
                "events": ["worker:candidate_persisted_before_ack"],
            },
            "authoritative_note": "complete recovery and effect proof",
        }
    )
    policy_witness = {
        "kind": "policy_witness.v1",
        "digest": "policy:1",
        "repository": ROOT_REPOSITORY,
        "canonical_policy": {"assurance": "semantic-delta"},
    }
    authority_root = {
        "kind": "authority_root.v1",
        "digest": "authority:1",
        "plan_revision_digest": "plan:1",
        "grants": ["repository.read.v1", "workspace.write.v1"],
    }
    runtime_selector = {
        "kind": "runtime_selector.v1",
        "digest": "selector:1",
        "campaign_key": "campaign:test-root-canary",
        "selectors": ["worker", "recovery_worker", "review"],
    }
    fault_journal = {
        "kind": "fault_journal.v1",
        "digest": "fault:1",
        "events": [
            {"role": "worker", "point": "candidate_persisted_before_ack"}
        ],
    }
    return AcceptanceFixture(
        {
            "repository": ROOT_REPOSITORY,
            "campaign_key": "campaign:test-root-canary",
            "plan_revision_digest": "plan:1",
            "activation_id": "activation:1",
            "writer_generation": "v8",
            "canary_target_sha": "sha:canary",
            "policy_witness_digest": "policy:1",
            "authority_root_digest": "authority:1",
            "runtime_selector_digest": "selector:1",
            "fault_journal_digest": "fault:1",
            "policy_witness": policy_witness,
            "authority_root": authority_root,
            "runtime_selector": runtime_selector,
            "fault_journal": fault_journal,
            "tickets": tickets,
            "candidates": candidates,
            "accepted_candidate_receipts": [
                candidate["accepted_candidate_receipt"] for candidate in candidates
            ],
            "reviews": reviews,
            "batches": [
                _batch(
                    "multi",
                    ["issue:10", "issue:11", "issue:12"],
                    "sha:multi",
                    201,
                    301,
                    "target:multi",
                    candidate_digests[:3],
                    finding_digests[:3],
                ),
                _batch(
                    "singleton",
                    ["issue:13"],
                    "sha:singleton",
                    202,
                    302,
                    "target:singleton",
                    candidate_digests[3:],
                    finding_digests[3:],
                ),
            ],
            "diagnostics": {"status": "Complete", "proof": proof},
        }
    )


def test_acceptance_requires_three_standard_in_one_batch_and_strict_singleton(
    valid_bundle: AcceptanceFixture,
):
    receipt = verify_root_canary(valid_bundle.data)

    assert isinstance(receipt, RootCanaryAcceptanceReceiptV1)
    assert receipt.standard_ticket_keys == ("alpha", "beta", "gamma")
    assert receipt.standard_batch.member_count == 3
    assert receipt.strict_ticket_key == "delta"
    assert receipt.strict_batch.member_count == 1
    assert receipt.standard_batch.pull_request_number != receipt.strict_batch.pull_request_number
    assert receipt.standard_batch.hosted_run_id != receipt.strict_batch.hosted_run_id


def test_acceptance_rejects_missing_finding_or_target_readback(
    valid_bundle: AcceptanceFixture,
):
    data = valid_bundle.copy()
    data["reviews"][1]["open_finding_ids"] = ["finding:beta"]
    with pytest.raises(RootCanaryVerificationError, match="FINDING_LEDGER_INCOMPLETE"):
        verify_root_canary(data)

    data = valid_bundle.copy()
    data["batches"][0]["target_readback"]["remote_target_sha"] = "target:unexpected"
    with pytest.raises(RootCanaryVerificationError, match="TARGET_SHA_MISMATCH"):
        verify_root_canary(data)


def test_acceptance_requires_four_slots_refill_and_all_recovery_proofs(
    valid_bundle: AcceptanceFixture,
):
    receipt = verify_root_canary(valid_bundle.data)

    assert receipt.peak_worker_slots == 4
    assert receipt.refill_proven
    assert receipt.permission_same_binding
    assert receipt.stale_diagnosis_bounded
    assert receipt.terminal_replacement_bounded
    assert receipt.duplicate_effect_ids == ()
    assert receipt.ticket_contract_digests == tuple(
        (ticket["key"], ticket["contract_digest"])
        for ticket in valid_bundle.data["tickets"]
    )
    assert receipt.candidate_receipt_digests == tuple(
        (ticket["key"], candidate["candidate_receipt_digest"])
        for ticket, candidate in zip(
            valid_bundle.data["tickets"], valid_bundle.data["candidates"], strict=True
        )
    )
    assert receipt.policy_witness_digest == "policy:1"
    assert receipt.fault_journal_digest == "fault:1"


def test_acceptance_digest_is_canonical_and_binds_repository_identity(
    valid_bundle: AcceptanceFixture,
):
    receipt = verify_root_canary(valid_bundle.data)

    expected_payload = {
        "repository": receipt.repository,
        "campaign_key": receipt.campaign_key,
        "plan_revision_digest": receipt.plan_revision_digest,
        "activation_id": receipt.activation_id,
        "writer_generation": receipt.writer_generation,
        "standard_ticket_keys": list(receipt.standard_ticket_keys),
        "strict_ticket_key": receipt.strict_ticket_key,
        "standard_batch_digest": receipt.standard_batch.receipt_digest,
        "strict_batch_digest": receipt.strict_batch.receipt_digest,
        "ticket_contract_digests": [
            {"key": key, "digest": digest}
            for key, digest in receipt.ticket_contract_digests
        ],
        "candidate_receipt_digests": [
            {"key": key, "digest": digest}
            for key, digest in receipt.candidate_receipt_digests
        ],
        "policy_witness_digest": receipt.policy_witness_digest,
        "authority_root_digest": receipt.authority_root_digest,
        "runtime_selector_digest": receipt.runtime_selector_digest,
        "finding_ledger_digests": [
            {"key": key, "digest": digest}
            for key, digest in receipt.finding_ledger_digests
        ],
        "batch_receipt_digests": [
            {"kind": kind, "digest": digest}
            for kind, digest in receipt.batch_receipt_digests
        ],
        "fault_journal_digest": receipt.fault_journal_digest,
        "peak_worker_slots": receipt.peak_worker_slots,
        "refill_ticket_order": ["alpha", "beta", "gamma", "delta"],
        "permission_same_binding": receipt.permission_same_binding,
        "stale_diagnosis_bounded": receipt.stale_diagnosis_bounded,
        "terminal_replacement_bounded": receipt.terminal_replacement_bounded,
        "terminal_replacement_receipt_digests": list(
            receipt.terminal_replacement_receipt_digests
        ),
        "duplicate_effect_ids": list(receipt.duplicate_effect_ids),
        "canary_target_sha": receipt.canary_target_sha,
        "authoritative_evidence": receipt.authoritative_evidence,
    }
    assert receipt.receipt_digest == _independent_digest(expected_payload)
    receipt.validate_digest(receipt.receipt_digest)
    receipt.validate_for(
        SimpleNamespace(
            repository=ROOT_REPOSITORY,
            campaign_key="campaign:test-root-canary",
            activation_id="activation:1",
            writer_generation="v8",
        )
    )
    with pytest.raises(RootCanaryVerificationError, match="CANARY_ADMISSION_IDENTITY_MISMATCH"):
        receipt.validate_for(
            SimpleNamespace(
                repository="other/repository",
                campaign_key="campaign:test-root-canary",
                activation_id="activation:1",
                writer_generation="v8",
            )
        )


@pytest.mark.parametrize(
    "path",
    (
        ("tickets", 0, "authoritative_note"),
        ("candidates", 0, "candidate_receipt", "authoritative_note"),
        ("reviews", 0, "finding_ledger", "authoritative_note"),
        ("batches", 0, "target_readback", "authoritative_note"),
        ("diagnostics", "proof", "authoritative_note"),
    ),
)
def test_acceptance_digest_binds_complete_authoritative_evidence(
    valid_bundle: AcceptanceFixture,
    path: tuple[object, ...],
):
    original = verify_root_canary(valid_bundle.data)
    data = valid_bundle.copy()
    target: object = data
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = "tampered authoritative evidence"  # type: ignore[index]

    changed = verify_root_canary(data)

    assert changed.receipt_digest != original.receipt_digest


def test_diagnostics_status_is_required_and_nested_malformed_data_is_named(
    valid_bundle: AcceptanceFixture,
):
    data = valid_bundle.copy()
    data["diagnostics"].pop("status")
    with pytest.raises(
        RootCanaryVerificationError, match="DIAGNOSTICS_STATUS_REQUIRED"
    ):
        verify_root_canary(data)

    data = valid_bundle.copy()
    data["diagnostics"] = []
    with pytest.raises(
        RootCanaryVerificationError, match="DIAGNOSTICS_INVALID"
    ):
        verify_root_canary(data)

    data = valid_bundle.copy()
    data["diagnostics"]["proof"] = ["malformed"]
    with pytest.raises(
        RootCanaryVerificationError, match="RECOVERY_PROOF_INCOMPLETE"
    ):
        verify_root_canary(data)


def test_manifest_merge_verifies_full_ticket_identity_without_overwriting_readback(
    tmp_path: Path,
    valid_bundle: AcceptanceFixture,
):
    tickets_path = tmp_path / "tickets.json"
    diagnostics_path = tmp_path / "diagnostics.json"
    output_path = tmp_path / "receipt.json"
    manifest = valid_bundle.copy()["tickets"]
    tickets_path.write_text(
        json.dumps(
            {
                "schema": "gwo-v8-root-canary-tickets.v1",
                "repository": ROOT_REPOSITORY,
                "tickets": manifest,
            }
        ),
        encoding="utf-8",
    )
    diagnostics = valid_bundle.copy()
    diagnostics["tickets"][0]["authoritative_id"] = "tampered-node"
    diagnostics_path.write_text(
        json.dumps({"acceptance_bundle": diagnostics}), encoding="utf-8"
    )

    with pytest.raises(
        RootCanaryVerificationError, match="TICKET_READBACK_MISMATCH"
    ):
        verify_main(
            [
                "--tickets",
                str(tickets_path),
                "--diagnostics",
                str(diagnostics_path),
                "--output",
                str(output_path),
            ]
        )


def test_strict_assurance_is_derived_from_the_semantic_delta_key(
    valid_bundle: AcceptanceFixture,
):
    data = valid_bundle.copy()
    for candidate in data["candidates"]:
        candidate.pop("assurance")

    receipt = verify_root_canary(data)

    assert receipt.strict_ticket_key == "delta"


@pytest.mark.parametrize(
    ("field", "code"),
    (
        (
            "permission_authorization_links",
            "PERMISSION_AUTHORIZATION_INCOMPLETE",
        ),
        (
            "stale_diagnosis_authorization_links",
            "STALE_DIAGNOSIS_AUTHORIZATION_INCOMPLETE",
        ),
        (
            "terminal_replacement_authorization_links",
            "TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE",
        ),
    ),
)
def test_recovery_verdicts_require_complete_authorization_links(
    valid_bundle: AcceptanceFixture,
    field: str,
    code: str,
):
    data = valid_bundle.copy()
    if field == "terminal_replacement_authorization_links":
        data["diagnostics"]["proof"]["terminal_replacement_receipt_digests"] = [
            "replacement:gamma"
        ]
    else:
        data["diagnostics"]["proof"].pop(field)

    with pytest.raises(RootCanaryVerificationError, match=code):
        verify_root_canary(data)


def test_candidate_review_batch_and_target_crosslinks_are_complete(
    valid_bundle: AcceptanceFixture,
):
    mutations = (
        (
            ("candidates", 0, "candidate_receipt", "ticket_key"),
            "CANDIDATE_RECEIPT_INCOMPLETE",
            "issue:999",
        ),
        (
            ("batches", 0, "candidate_receipt_digests", 0),
            "BATCH_READBACK_INCOMPLETE",
            "wrong-candidate",
        ),
        (
            ("batches", 0, "target_readback", "pull_request_number"),
            "TARGET_SHA_MISMATCH",
            999,
        ),
    )
    for path, code, value in mutations:
        data = valid_bundle.copy()
        target: object = data
        for key in path[:-1]:
            target = target[key]  # type: ignore[index]
        target[path[-1]] = value  # type: ignore[index]
        with pytest.raises(RootCanaryVerificationError, match=code):
            verify_root_canary(data)

    data = valid_bundle.copy()
    data["batches"][1]["batch_id"] = data["batches"][0]["batch_id"]
    with pytest.raises(RootCanaryVerificationError, match="BATCH_BOUNDARY_COLLAPSED"):
        verify_root_canary(data)


@pytest.mark.parametrize(
    ("field", "code"),
    (
        ("policy_witness_digest", "POLICY_EVIDENCE_INCOMPLETE"),
        ("fault_journal_digest", "FAULT_EVIDENCE_INCOMPLETE"),
    ),
)
def test_policy_and_fault_evidence_are_cross_bound_between_bundle_and_proof(
    valid_bundle: AcceptanceFixture,
    field: str,
    code: str,
):
    data = valid_bundle.copy()
    data["diagnostics"]["proof"][field] = f"changed:{field}"

    with pytest.raises(RootCanaryVerificationError, match=code):
        verify_root_canary(data)


@pytest.mark.parametrize(
    ("path", "code", "value"),
    (
        (("diagnostics", "proof", "peak_worker_slots"), "WORKER_SLOT_PROOF_INVALID", 3),
        (("diagnostics", "proof", "refill_ticket_order"), "REFILL_PROOF_INVALID", ["alpha"]),
        (("diagnostics", "proof", "permission_binding_pairs"), "PERMISSION_BINDING_MISMATCH", [["a", "b"]]),
        (("diagnostics", "proof", "stale_diagnosis_count_by_binding"), "RECOVERY_BOUND_INVALID", [["binding:alpha", 2]]),
        (("diagnostics", "proof", "binding_count_by_ticket"), "RECOVERY_BOUND_INVALID", [["alpha", 3], ["beta", 1], ["gamma", 1], ["delta", 1]]),
        (("diagnostics", "proof", "duplicate_effect_ids"), "DUPLICATE_EFFECT", ["semantic:alpha"]),
    ),
)
def test_acceptance_fails_closed_for_recovery_and_effect_proof_gaps(
    valid_bundle: AcceptanceFixture,
    path: tuple[str, ...],
    code: str,
    value: object,
):
    data = valid_bundle.copy()
    target: object = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(RootCanaryVerificationError, match=code):
        verify_root_canary(data)


def test_acceptance_requires_exact_ticket_set_and_candidate_review_crosslinks(
    valid_bundle: AcceptanceFixture,
):
    data = valid_bundle.copy()
    data["tickets"][3]["ticket_key"] = "issue:10"
    with pytest.raises(RootCanaryVerificationError, match="ROOT_TICKET_READBACK_INVALID"):
        verify_root_canary(data)

    data = valid_bundle.copy()
    data["candidates"][0]["ticket_key"] = "issue:999"
    with pytest.raises(RootCanaryVerificationError, match="CANDIDATE_RECEIPT_INCOMPLETE"):
        verify_root_canary(data)

    data = valid_bundle.copy()
    data["candidates"][3]["assurance"] = "standard"
    with pytest.raises(RootCanaryVerificationError, match="ASSURANCE_SHAPE_INVALID"):
        verify_root_canary(data)


def test_acceptance_rejects_non_authoritative_local_pr_ci_and_batch_boundaries(
    valid_bundle: AcceptanceFixture,
):
    mutations = (
        (("batches", 0, "local_suite", "status"), "LOCAL_SUITE_SHA_MISMATCH", "failed"),
        (("batches", 0, "pull_request", "head_sha"), "HOSTED_SHA_MISMATCH", "sha:wrong"),
        (("batches", 0, "hosted_ci", "conclusion"), "HOSTED_SHA_MISMATCH", "failure"),
        (("batches", 0, "integration_lease", "serialized"), "INTEGRATION_NOT_SERIALIZED", False),
        (("batches", 0, "target_readback", "batch_sha_is_ancestor"), "TARGET_SHA_MISMATCH", False),
        (("batches", 0, "member_ticket_keys"), "BATCH_MEMBERS_INVALID", ["issue:10"]),
    )
    for path, code, value in mutations:
        data = valid_bundle.copy()
        target: object = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with pytest.raises(RootCanaryVerificationError, match=code):
            verify_root_canary(data)


def test_acceptance_rejects_missing_policy_runtime_authority_or_fault_evidence(
    valid_bundle: AcceptanceFixture,
):
    for field, code in (
        ("policy_witness_digest", "POLICY_EVIDENCE_INCOMPLETE"),
        ("authority_root_digest", "AUTHORITY_EVIDENCE_INCOMPLETE"),
        ("runtime_selector_digest", "RUNTIME_EVIDENCE_INCOMPLETE"),
        ("fault_journal_digest", "FAULT_EVIDENCE_INCOMPLETE"),
    ):
        data = valid_bundle.copy()
        data[field] = ""
        with pytest.raises(RootCanaryVerificationError, match=code):
            verify_root_canary(data)


def test_acceptance_document_contains_the_canonical_receipt_digest(
    tmp_path: Path,
    valid_bundle: AcceptanceFixture,
):
    receipt = verify_root_canary(valid_bundle.data)
    path = tmp_path / "root-canary.md"

    write_acceptance_document(path, receipt)

    text = path.read_text("utf-8")
    assert receipt.receipt_digest in text
    assert receipt.fault_journal_digest in text
    assert "does not claim live GitHub or Paseo execution" in text


def test_read_only_cli_combines_ticket_manifest_and_diagnostics(
    tmp_path: Path,
    valid_bundle: AcceptanceFixture,
):
    tickets_path = tmp_path / "tickets.json"
    diagnostics_path = tmp_path / "diagnostics.json"
    output_path = tmp_path / "receipt.json"
    tickets = valid_bundle.data["tickets"]
    tickets_path.write_text(
        json.dumps(
            {
                "schema": "gwo-v8-root-canary-tickets.v1",
                "repository": ROOT_REPOSITORY,
                "ready_refs": [
                    f"github://{ROOT_REPOSITORY}/issues/{number}"
                    for number in (10, 11, 12, 13)
                ],
                "tickets": tickets,
            }
        ),
        encoding="utf-8",
    )
    diagnostics_path.write_text(
        json.dumps({"acceptance_bundle": valid_bundle.data}),
        encoding="utf-8",
    )

    assert (
        verify_main(
            [
                "--tickets",
                str(tickets_path),
                "--diagnostics",
                str(diagnostics_path),
                "--output",
                str(output_path),
                "--repository",
                ROOT_REPOSITORY,
            ]
        )
        == 0
    )
    output = json.loads(output_path.read_text("utf-8"))
    assert output["receipt_digest"] == verify_root_canary(valid_bundle.data).receipt_digest
    assert output["repository"] == ROOT_REPOSITORY


def test_read_only_cli_accepts_authoritative_ticket_manifest_readbacks(
    tmp_path: Path,
    valid_bundle: AcceptanceFixture,
):
    tickets_path = tmp_path / "tickets.json"
    diagnostics_path = tmp_path / "diagnostics.json"
    output_path = tmp_path / "receipt.json"
    rich_tickets = []
    for ticket in valid_bundle.data["tickets"]:
        number = ticket["number"]
        rich_tickets.append(
            {
                **ticket,
                "id": number,
                "node_id": f"node-{number}",
                "title": f"Root Canary Ticket {ticket['key']}",
                "repository": {
                    "full_name": ROOT_REPOSITORY,
                    "url": f"https://api.github.com/repos/{ROOT_REPOSITORY}",
                },
                "url": f"https://api.github.com/repos/{ROOT_REPOSITORY}/issues/{number}",
                "html_url": f"https://github.com/{ROOT_REPOSITORY}/issues/{number}",
                "updated_at": "2026-08-17T00:00:00Z",
            }
        )

    tickets_path.write_text(
        json.dumps(
            {
                "schema": "gwo-v8-root-canary-tickets.v1",
                "repository": ROOT_REPOSITORY,
                "ready_refs": [
                    f"github://{ROOT_REPOSITORY}/issues/{number}"
                    for number in (10, 11, 12, 13)
                ],
                "tickets": rich_tickets,
            }
        ),
        encoding="utf-8",
    )
    bundle = valid_bundle.copy()
    bundle.pop("tickets")
    diagnostics_path.write_text(
        json.dumps({"acceptance_bundle": bundle}),
        encoding="utf-8",
    )

    assert (
        verify_main(
            [
                "--tickets",
                str(tickets_path),
                "--diagnostics",
                str(diagnostics_path),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
