from __future__ import annotations

import copy
import hashlib
import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.verify_v8_root_canary import (
    ROOT_REPOSITORY,
    RootCanaryAcceptanceReceiptV1,
    RootCanaryVerificationError,
    digest_value,
    verify_main,
    verify_root_canary,
    write_acceptance_document,
)


ROOT = Path(__file__).resolve().parents[1]
ROOT_TICKETS = ROOT / "tests" / "fixtures" / "gwo-v8-root-canary-tickets-195-198.json"


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
        "plan_revision_digest": PLAN_DIGEST,
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
    return {**body, "receipt_digest": _task_digest(body)}


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


def _task_digest(value: object) -> str:
    encoded = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha(label: str) -> str:
    return _task_digest({"fixture": label})


def _oid(label: str) -> str:
    return hashlib.sha1(label.encode("utf-8")).hexdigest()


PLAN_DIGEST = _sha("plan")
POLICY_WITNESS_BODY = {
    "allowed_capabilities": ["git", "local_check"],
    "authority_grants": {
        "campaign": [
            {
                "operation_id": "repository.read.v1",
                "resource_id": "campaign.snapshot.v1",
            }
        ],
        "recovery_worker": [
            {
                "operation_id": "workspace.write.v1",
                "resource_id": "work-run.workspace.v1",
            }
        ],
        "review": [
            {
                "operation_id": "repository.read.v1",
                "resource_id": "review.subject.v1",
            }
        ],
        "worker": [
            {
                "operation_id": "workspace.write.v1",
                "resource_id": "work-run.workspace.v1",
            }
        ],
    },
    "exclusive_resources": ["repository.target.v1"],
    "ref": "policy:gwo-v8-root-canary",
    "schema_version": 1,
}
POLICY_DIGEST = _task_digest(POLICY_WITNESS_BODY)
POLICY_WITNESS = {**POLICY_WITNESS_BODY, "digest": POLICY_DIGEST}
AUTHORITY_ROOT_BODY = {
    "policy_witness_digest": POLICY_DIGEST,
    "grants": [
        {
            "operation_id": "repository.read.v1",
            "resource_id": "campaign.snapshot.v1",
        },
        {
            "operation_id": "workspace.write.v1",
            "resource_id": "work-run.workspace.v1",
        },
    ],
}
AUTHORITY_DIGEST = _task_digest(AUTHORITY_ROOT_BODY)
AUTHORITY_ROOT = {**AUTHORITY_ROOT_BODY, "subtree_digest": AUTHORITY_DIGEST}
RUNTIME_SELECTOR_BODY = {
    "kind": "gwo.runtime-selector-readback.v1",
    "repository": ROOT_REPOSITORY,
    "campaign_key": "campaign:test-root-canary",
    "plan_revision_digest": PLAN_DIGEST,
    "assignments": [],
}
RUNTIME_SELECTOR_DIGEST = _task_digest(RUNTIME_SELECTOR_BODY)
RUNTIME_SELECTOR = {**RUNTIME_SELECTOR_BODY, "digest": RUNTIME_SELECTOR_DIGEST}
FAULT_JOURNAL_BODY = {
    "kind": "fault-journal-readback.v1",
    "repository": ROOT_REPOSITORY,
    "campaign_key": "campaign:test-root-canary",
    "plan_revision_digest": PLAN_DIGEST,
    "effects": {},
    "consumed_faults": [],
}
FAULT_JOURNAL_DIGEST = _task_digest(FAULT_JOURNAL_BODY)
FAULT_JOURNAL = {**FAULT_JOURNAL_BODY, "digest": FAULT_JOURNAL_DIGEST}


def _ticket(key: str, number: int) -> dict[str, object]:
    repository = {
        "full_name": ROOT_REPOSITORY,
        "url": f"https://api.github.com/repos/{ROOT_REPOSITORY}",
    }
    label = {
        "id": 1000 + number,
        "node_id": f"MDU6TGFiZWw{number}",
        "url": f"https://api.github.com/repos/{ROOT_REPOSITORY}/labels/ready-for-agent",
        "name": "ready-for-agent",
        "color": "0052cc",
        "default": False,
        "description": None,
    }
    comment = {
        "id": 2000 + number,
        "node_id": f"MDEyOklzc3VlQ29tbWVudA{number}",
        "url": f"https://api.github.com/repos/{ROOT_REPOSITORY}/issues/comments/{2000 + number}",
        "html_url": f"https://github.com/{ROOT_REPOSITORY}/issues/{number}#issuecomment-{2000 + number}",
        "body": f"authoritative comment {key}",
        "user": {"login": "root-canary"},
        "created_at": "2026-08-17T00:00:00Z",
        "updated_at": "2026-08-17T00:00:00Z",
        "author_association": "OWNER",
    }
    contract = {
        "id": 3000 + number,
        "node_id": f"MDU6SXNzdWUz{number}",
        "number": number,
        "title": f"Root Canary Ticket issue:{number}",
        "body": f"root canary contract {key}",
        "state": "open",
        "state_reason": None,
        "type": None,
        "repository": repository,
        "labels": [label],
        "comments": [comment],
        "updated_at": "2026-08-17T00:00:00Z",
    }
    ticket_key = f"issue:{number}"
    projection = {
        "number": number,
        "contract": contract,
        "labels": ["ready-for-agent"],
        "source_ref": ticket_key,
        "native_blockers": [],
    }
    return {
        "key": ticket_key,
        "labels": ["ready-for-agent"],
        "source": {"ref": ticket_key, "digest": _task_digest(projection)},
        "contract": contract,
        "native_blockers": [],
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
        "parent_digest": _sha(f"parent:{key}"),
        "repository": ROOT_REPOSITORY,
        "campaign_key": "campaign:test-root-canary",
        "campaign_handle": "campaign:test-root-canary",
        "plan_revision_digest": PLAN_DIGEST,
        "work_run_key": f"work-run:{key}",
        "ticket_key": ticket_key,
        "reported_reference": f"refs/heads/gwo-v8-root-canary-{key}",
        "base_commit_oid": _oid(f"base-commit:{key}"),
        "base_tree_oid": _oid(f"base-tree:{key}"),
        "candidate_commit_oid": _oid(f"candidate-commit:{key}"),
        "candidate_tree_oid": _oid(f"candidate-tree:{key}"),
        "diff_schema_version": "CandidateDiffRecordV1",
        "diff_record_digest": _sha(f"diff:{key}"),
        "authority_subtree_digest": _sha(f"authority-subtree:{key}"),
        "runtime_subject_digest": _sha(f"runtime-subject:{key}"),
    }
    candidate_receipt = {
        **candidate_body,
        "receipt_digest": _task_digest(candidate_body),
    }
    finding_digest = _task_digest(
        {"kind": "review_finding_ledger.v1", "entries": []}
    )
    accepted_body = {
        "kind": "accepted_candidate_receipt.v1",
        "repository": ROOT_REPOSITORY,
        "campaign_key": "campaign:test-root-canary",
        "plan_revision_digest": PLAN_DIGEST,
        "target_branch": "main",
        "ticket_key": ticket_key,
        "work_run_key": candidate_body["work_run_key"],
        "integration_node_key": f"integration:{key}",
        "accepted_sequence": number - 9,
        "base_sha": candidate_body["base_commit_oid"],
        "base_tree_oid": candidate_body["base_tree_oid"],
        "candidate_sha": candidate_body["candidate_commit_oid"],
        "candidate_tree_oid": candidate_body["candidate_tree_oid"],
        "candidate_receipt_digest": candidate_receipt["receipt_digest"],
        "diff_schema_version": "CandidateDiffRecordV1",
        "diff_record_digest": candidate_body["diff_record_digest"],
        "authority_subtree_digest": candidate_body["authority_subtree_digest"],
        "policy_witness_digest": POLICY_DIGEST,
        "review_subject_digest": _sha(f"review-subject:{key}"),
        "assurance": assurance,
        "assurance_requirement_digest": _sha(f"assurance-requirement:{key}"),
        "check_environment_digest": _sha(f"check-environment:{key}"),
        "delivery_identity_digest": _sha(f"delivery-identity:{key}"),
        "interaction_keys": [],
        "protected_surfaces": [],
        "gitlink_change": False,
        "evidence_digests": [_sha(f"candidate-evidence:{key}")],
        "review_finding_ledger_digest": finding_digest,
    }
    accepted_receipt = {
        **accepted_body,
        "receipt_digest": _task_digest(accepted_body),
    }
    return {
        "ticket_key": ticket_key,
        "assurance": assurance,
        "candidate_receipt_digest": candidate_receipt["receipt_digest"],
        "candidate_receipt": candidate_receipt,
        "accepted_candidate_receipt": accepted_receipt,
    }


def _review(key: str, number: int, candidate_digest: str) -> dict[str, object]:
    finding_digest = _task_digest(
        {"kind": "review_finding_ledger.v1", "entries": []}
    )
    ledger = {
        "kind": "review_finding_ledger.v1",
        "entries": [],
        "ledger_digest": finding_digest,
    }
    return {
        "ticket_key": f"issue:{number}",
        "candidate_receipt_digest": candidate_digest,
        "open_finding_ids": [],
        "finding_ledger_digest": finding_digest,
        "finding_ledger": ledger,
    }


def _recovery_proof(
    tickets: list[dict[str, object]],
    candidates: list[dict[str, object]],
    reviews: list[dict[str, object]],
    batches: list[dict[str, object]],
) -> dict[str, object]:
    ticket_refs = [ticket["key"] for ticket in tickets]
    binding_ids = [f"binding:{key}" for key in ("alpha", "beta", "delta", "gamma")]
    return {
        "ticket_keys": ticket_refs,
        "worker_slot_limit": 4,
        "peak_worker_slots": 4,
        "refill_ticket_order": ticket_refs,
        "runtime_selector_digest": RUNTIME_SELECTOR_DIGEST,
        "authority_root_digest": AUTHORITY_DIGEST,
        "candidate_receipt_digests": sorted(
            candidate["candidate_receipt_digest"] for candidate in candidates
        ),
        "candidate_sha_count_by_ticket": [[ref, 1] for ref in ticket_refs],
        "binding_count_by_ticket": [
            [key, 1] for key in ("alpha", "beta", "gamma", "delta")
        ],
        "permission_binding_pairs": [["binding:beta", "binding:beta"]],
        "review_finding_ledger_digests": sorted(
            {review["finding_ledger_digest"] for review in reviews}
        ),
        "stale_diagnosed_binding_ids": binding_ids,
        "stale_diagnosis_count_by_binding": [[binding, 1] for binding in binding_ids],
        "terminal_replacement_receipt_digests": [],
        "semantic_effect_ids": ["semantic:alpha"],
        "external_effect_ids": ["external:alpha"],
        "duplicate_effect_ids": [],
        "batch_receipt_digests": sorted(batch["receipt_digest"] for batch in batches),
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
            key,
            int(ticket["contract"]["number"]),
            ticket["source"]["digest"],
            assurance="strict" if key == "delta" else "standard",
        )
        for key, ticket in zip(
            ("alpha", "beta", "gamma", "delta"), tickets, strict=True
        )
    ]
    candidate_digests = [
        candidate["candidate_receipt_digest"] for candidate in candidates
    ]
    reviews = [
        _review(
            key,
            int(ticket["contract"]["number"]),
            candidate["candidate_receipt_digest"],
        )
        for key, ticket, candidate in zip(
            ("alpha", "beta", "gamma", "delta"), tickets, candidates, strict=True
        )
    ]
    finding_digests = [review["finding_ledger_digest"] for review in reviews]
    batches = [
        _batch(
            "multi",
            ["issue:10", "issue:11", "issue:12"],
            _oid("batch:multi"),
            201,
            301,
            _oid("target:multi"),
            candidate_digests[:3],
            finding_digests[:3],
        ),
        _batch(
            "singleton",
            ["issue:13"],
            _oid("batch:singleton"),
            202,
            302,
            _oid("target:singleton"),
            candidate_digests[3:],
            finding_digests[3:],
        ),
    ]
    proof = _recovery_proof(tickets, candidates, reviews, batches)
    proof["authoritative_note"] = "complete recovery and effect proof"
    return AcceptanceFixture(
        {
            "repository": ROOT_REPOSITORY,
            "campaign_key": "campaign:test-root-canary",
            "plan_revision_digest": PLAN_DIGEST,
            "activation_id": "activation:1",
            "writer_generation": "v8",
            "canary_target_sha": _oid("canary-target"),
            "policy_witness_digest": POLICY_DIGEST,
            "authority_root_digest": AUTHORITY_DIGEST,
            "runtime_selector_digest": RUNTIME_SELECTOR_DIGEST,
            "fault_journal_digest": FAULT_JOURNAL_DIGEST,
            "policy_witness": POLICY_WITNESS,
            "authority_root": AUTHORITY_ROOT,
            "runtime_selector": RUNTIME_SELECTOR,
            "fault_journal": FAULT_JOURNAL,
            "tickets": tickets,
            "candidates": candidates,
            "accepted_candidate_receipts": [
                candidate["accepted_candidate_receipt"] for candidate in candidates
            ],
            "reviews": reviews,
            "batches": batches,
            "diagnostics": {"status": "Complete", "proof": proof},
        }
    )


@pytest.fixture(scope="module")
def local_only_bundle(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    runner = importlib.import_module("scripts.run_v8_local_acceptance")
    record = runner.run_local_acceptance(
        root=tmp_path_factory.mktemp("local-root-producer"),
        run_id="task3-local-verifier",
        scenario="root",
        tickets=ROOT_TICKETS,
    )
    manifest = json.loads(ROOT_TICKETS.read_text(encoding="utf-8"))
    record["tickets"] = copy.deepcopy(manifest["tickets"])
    record["ready_refs"] = list(manifest["ready_refs"])
    return record


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
        (key, ticket["source"]["digest"])
        for key, ticket in zip(
            ("alpha", "beta", "gamma", "delta"),
            valid_bundle.data["tickets"],
            strict=True,
        )
    )
    assert receipt.candidate_receipt_digests == tuple(
        (key, candidate["candidate_receipt_digest"])
        for key, candidate in zip(
            ("alpha", "beta", "gamma", "delta"),
            valid_bundle.data["candidates"],
            strict=True,
        )
    )
    assert receipt.policy_witness_digest == POLICY_DIGEST
    assert receipt.fault_journal_digest == FAULT_JOURNAL_DIGEST


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
    ("path", "code"),
    (
        (("tickets", 0, "contract", "title"), "TICKET_CONTRACT_DIGEST_MISMATCH"),
        (("candidates", 0, "candidate_receipt", "reported_reference"), "CANDIDATE_RECEIPT_INCOMPLETE"),
        (("batches", 0, "target_readback", "target_branch"), "TARGET_SHA_MISMATCH"),
    ),
)
def test_acceptance_digest_binds_complete_authoritative_evidence(
    valid_bundle: AcceptanceFixture,
    path: tuple[object, ...],
    code: str,
):
    data = valid_bundle.copy()
    target: object = data
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = "tampered authoritative evidence"  # type: ignore[index]

    with pytest.raises(RootCanaryVerificationError, match=code):
        verify_root_canary(data)


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
                "schema": "gwo-v8-root-canary-tickets.v2",
                "repository": ROOT_REPOSITORY,
                "ready_refs": [ticket["key"] for ticket in manifest],
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


def test_strict_assurance_is_required_on_the_accepted_receipt(
    valid_bundle: AcceptanceFixture,
):
    data = valid_bundle.copy()
    for candidate in data["candidates"]:
        candidate["accepted_candidate_receipt"].pop("assurance")

    with pytest.raises(RootCanaryVerificationError, match="ASSURANCE_SHAPE_INVALID"):
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
    data["tickets"][3]["key"] = "issue:10"
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
                "schema": "gwo-v8-root-canary-tickets.v2",
                "repository": ROOT_REPOSITORY,
                "ready_refs": [ticket["key"] for ticket in tickets],
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
    rich_tickets = copy.deepcopy(valid_bundle.data["tickets"])

    tickets_path.write_text(
        json.dumps(
            {
                "schema": "gwo-v8-root-canary-tickets.v2",
                "repository": ROOT_REPOSITORY,
                "ready_refs": [ticket["key"] for ticket in rich_tickets],
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


def _v2_ticket_manifest_entry(ticket: dict[str, object]) -> dict[str, object]:
    """Return an independent copy of Task 1's complete v2 entry."""

    value = copy.deepcopy(ticket)
    contract = value["contract"]
    projection = {
        "number": contract["number"],
        "contract": contract,
        "labels": value["labels"],
        "source_ref": value["key"],
        "native_blockers": value["native_blockers"],
    }
    value["source"]["digest"] = _task_digest(projection)
    return value


def test_task1_v2_manifest_is_authoritative_without_flattening_or_overwriting(
    tmp_path: Path,
    valid_bundle: AcceptanceFixture,
):
    tickets_path = tmp_path / "tickets.json"
    diagnostics_path = tmp_path / "diagnostics.json"
    output_path = tmp_path / "receipt.json"
    manifest_tickets = [
        _v2_ticket_manifest_entry(ticket)
        for ticket in valid_bundle.data["tickets"]
    ]
    tickets_path.write_text(
        json.dumps(
            {
                "schema": "gwo-v8-root-canary-tickets.v2",
                "repository": ROOT_REPOSITORY,
                "ready_refs": [item["key"] for item in manifest_tickets],
                "tickets": manifest_tickets,
            }
        ),
        encoding="utf-8",
    )
    diagnostics = valid_bundle.copy()
    diagnostics.pop("tickets")
    diagnostics_path.write_text(
        json.dumps({"acceptance_bundle": diagnostics}), encoding="utf-8"
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


def test_task1_v2_manifest_binds_full_contract_and_source_digest(
    tmp_path: Path,
    valid_bundle: AcceptanceFixture,
):
    tickets_path = tmp_path / "tickets.json"
    diagnostics_path = tmp_path / "diagnostics.json"
    output_path = tmp_path / "receipt.json"
    manifest_tickets = [
        _v2_ticket_manifest_entry(ticket)
        for ticket in valid_bundle.data["tickets"]
    ]
    manifest_tickets[0]["contract"]["title"] = "tampered title"
    tickets_path.write_text(
        json.dumps(
            {
                "schema": "gwo-v8-root-canary-tickets.v2",
                "repository": ROOT_REPOSITORY,
                "ready_refs": [item["key"] for item in manifest_tickets],
                "tickets": manifest_tickets,
            }
        ),
        encoding="utf-8",
    )
    diagnostics = valid_bundle.copy()
    diagnostics.pop("tickets")
    diagnostics_path.write_text(
        json.dumps({"acceptance_bundle": diagnostics}), encoding="utf-8"
    )

    with pytest.raises(
        RootCanaryVerificationError, match="TICKET_CONTRACT_DIGEST_MISMATCH"
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


def test_actual_campaign_proof_readback_does_not_require_nonexistent_link_records(
    valid_bundle: AcceptanceFixture,
):
    data = valid_bundle.copy()
    proof = data["diagnostics"]["proof"]
    for field in (
        "permission_authorization_links",
        "stale_diagnosis_authorization_links",
        "binding_ids_by_ticket",
        "semantic_effect_records",
        "external_effect_records",
        "effect_history",
    ):
        proof.pop(field, None)
    proof.update(
        {
            "ticket_keys": ["alpha", "beta", "gamma", "delta"],
            "candidate_receipt_digests": sorted(
                candidate["candidate_receipt_digest"]
                for candidate in data["candidates"]
            ),
            "review_finding_ledger_digests": sorted(
                {review["finding_ledger_digest"] for review in data["reviews"]}
            ),
            "batch_receipt_digests": sorted(
                batch["receipt_digest"] for batch in data["batches"]
            ),
            "runtime_selector_digest": data["runtime_selector_digest"],
            "authority_root_digest": data["authority_root_digest"],
        }
    )

    verify_root_canary(data)


def test_candidate_receipt_rejects_policy_witness_field_not_in_task3_schema(
    valid_bundle: AcceptanceFixture,
):
    data = valid_bundle.copy()
    data["candidates"][0]["candidate_receipt"]["policy_witness_digest"] = (
        data["policy_witness_digest"]
    )

    with pytest.raises(
        RootCanaryVerificationError, match="CANDIDATE_RECEIPT_INCOMPLETE"
    ):
        verify_root_canary(data)


def test_policy_witness_requires_a_canonical_evidence_object(
    valid_bundle: AcceptanceFixture,
):
    data = valid_bundle.copy()
    data.pop("policy_witness")

    with pytest.raises(
        RootCanaryVerificationError, match="POLICY_EVIDENCE_INCOMPLETE"
    ):
        verify_root_canary(data)


def test_assurance_is_not_defaulted_when_candidate_readback_omits_it(
    valid_bundle: AcceptanceFixture,
):
    data = valid_bundle.copy()
    candidate = data["candidates"][0]
    candidate.pop("assurance", None)
    candidate["candidate_receipt"].pop("assurance", None)
    candidate["accepted_candidate_receipt"].pop("assurance", None)

    with pytest.raises(RootCanaryVerificationError, match="ASSURANCE_SHAPE_INVALID"):
        verify_root_canary(data)


def test_local_only_verifier_accepts_the_manifest_backed_local_projection(
    local_only_bundle: dict[str, object],
    tmp_path: Path,
):
    receipt = verify_root_canary(copy.deepcopy(local_only_bundle))

    assert receipt.acceptance_mode == "local-only-v1"
    assert receipt.standard_ticket_keys == (
        "issue:195",
        "issue:196",
        "issue:197",
    )
    assert receipt.strict_ticket_key == "issue:198"
    assert receipt.standard_batch.member_count == 3
    assert receipt.strict_batch.member_count == 1
    assert receipt.standard_batch.pull_request_number is None
    assert receipt.standard_batch.hosted_run_id is None
    assert receipt.strict_batch.pull_request_number is None
    assert receipt.strict_batch.hosted_run_id is None
    assert (
        receipt.authoritative_evidence["local_evidence"]
        == local_only_bundle["local_evidence"]
    )

    document = tmp_path / "local-receipt.md"
    write_acceptance_document(document, receipt)
    assert "gwo-v8-root-canary-acceptance.v2" in document.read_text("utf-8")


def test_local_only_mode_is_required_and_invalid_modes_do_not_use_legacy_hosted_path(
    local_only_bundle: dict[str, object],
):
    missing = copy.deepcopy(local_only_bundle)
    missing.pop("acceptance_mode")
    with pytest.raises(
        RootCanaryVerificationError, match="ROOT_ACCEPTANCE_MODE_REQUIRED"
    ):
        verify_root_canary(missing)

    invalid = copy.deepcopy(local_only_bundle)
    invalid["acceptance_mode"] = "hosted-v1"
    with pytest.raises(
        RootCanaryVerificationError, match="ROOT_ACCEPTANCE_MODE_INVALID"
    ):
        verify_root_canary(invalid)

    explicit_local_without_local_evidence = copy.deepcopy(local_only_bundle)
    explicit_local_without_local_evidence.pop("local_evidence")
    with pytest.raises(
        RootCanaryVerificationError, match="LOCAL_EVIDENCE_INCOMPLETE"
    ):
        verify_root_canary(explicit_local_without_local_evidence)


@pytest.mark.parametrize(
    "forbidden_key",
    (
        "hosted_ci",
        "pull_request",
        "publication_receipt",
        "remote_target_sha",
        "url",
        "workflow_url",
        "run_id",
        "check_id",
    ),
)
def test_local_only_rejects_forbidden_fields_recursively(
    local_only_bundle: dict[str, object],
    forbidden_key: str,
):
    data = copy.deepcopy(local_only_bundle)
    data["local_evidence"]["batches"][0]["target_readback"][forbidden_key] = "forbidden"  # type: ignore[index]

    with pytest.raises(
        RootCanaryVerificationError, match="LOCAL_BATCH_HOSTED_FIELD_FORBIDDEN"
    ):
        verify_root_canary(data)


@pytest.mark.parametrize(
    ("mutation", "code"),
    (
        ("batch_ref_sha", "LOCAL_BATCH_SHA_MISMATCH"),
        ("suite_sha", "LOCAL_BATCH_SHA_MISMATCH"),
        ("lease", "LOCAL_INTEGRATION_LEASE_INVALID"),
        ("target_after", "LOCAL_TARGET_READBACK_INVALID"),
        ("target_cas", "LOCAL_TARGET_READBACK_INVALID"),
        ("target_ancestry", "LOCAL_TARGET_ANCESTRY_INVALID"),
    ),
)
def test_local_only_rejects_batch_lease_and_target_tampering(
    local_only_bundle: dict[str, object],
    mutation: str,
    code: str,
):
    data = copy.deepcopy(local_only_bundle)
    batch = data["local_evidence"]["batches"][0]  # type: ignore[index]
    if mutation == "batch_ref_sha":
        batch["batch_ref"]["sha"] = "0" * 40
    elif mutation == "suite_sha":
        batch["local_suite"]["batch_sha"] = "0" * 40
    elif mutation == "lease":
        batch["integration_lease"]["serialized"]["holder"] = "0" * 64
    elif mutation == "target_after":
        batch["target_readback"]["target_after"]["commit_sha"] = "0" * 40
    elif mutation == "target_cas":
        batch["target_readback"]["cas"]["readback_sha"] = "0" * 40
    elif mutation == "target_ancestry":
        batch["target_readback"]["ancestry"]["is_ancestor"] = False

    with pytest.raises(RootCanaryVerificationError, match=code):
        verify_root_canary(data)


def test_local_only_receipt_digest_binds_mode_and_complete_local_evidence(
    local_only_bundle: dict[str, object],
):
    receipt = verify_root_canary(copy.deepcopy(local_only_bundle))
    payload = receipt.canonical_digest_payload()

    assert payload["acceptance_mode"] == "local-only-v1"
    assert payload["authoritative_evidence"] == receipt.authoritative_evidence

    mode_changed = copy.deepcopy(payload)
    mode_changed["acceptance_mode"] = "different-mode"
    assert digest_value(mode_changed) != receipt.receipt_digest

    evidence_changed = copy.deepcopy(payload)
    evidence_changed["authoritative_evidence"]["local_evidence"]["batches"][0][
        "batch_sha"
    ] = "0" * 40
    assert digest_value(evidence_changed) != receipt.receipt_digest


def test_local_only_cli_round_trip_uses_the_real_ticket_fixture(
    tmp_path: Path,
    local_only_bundle: dict[str, object],
):
    diagnostics_path = tmp_path / "local-record.json"
    output_path = tmp_path / "local-receipt.json"
    diagnostics_path.write_text(json.dumps(local_only_bundle), encoding="utf-8")

    assert (
        verify_main(
            [
                "--tickets",
                str(ROOT_TICKETS),
                "--diagnostics",
                str(diagnostics_path),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )

    output = json.loads(output_path.read_text("utf-8"))
    assert output["acceptance_mode"] == "local-only-v1"
    assert output["schema"] == "gwo-v8-root-canary-acceptance.v2"
    assert [
        batch["member_ticket_keys"]
        for batch in output["authoritative_evidence"]["local_evidence"]["batches"]
    ] == [
        ["issue:195", "issue:196", "issue:197"],
        ["issue:198"],
    ]
    assert output["receipt_digest"] == verify_root_canary(
        copy.deepcopy(local_only_bundle)
    ).receipt_digest
