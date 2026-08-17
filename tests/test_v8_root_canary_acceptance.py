from __future__ import annotations

import copy
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


def _batch(
    kind: str,
    keys: list[str],
    sha: str,
    pr_number: int,
    run_id: int,
    target: str,
) -> dict[str, object]:
    return {
        "batch_kind": kind,
        "batch_id": f"batch:{kind}",
        "member_ticket_keys": keys,
        "batch_sha": sha,
        "local_suite": {"status": "passed", "head_sha": sha},
        "pull_request": {"number": pr_number, "head_sha": sha},
        "hosted_ci": {"run_id": run_id, "head_sha": sha, "conclusion": "success"},
        "integration_lease": {"serialized": True},
        "target_readback": {
            "merge_method": "merge",
            "batch_sha_is_ancestor": True,
            "remote_target_sha": target,
        },
        "integrated_target_sha": target,
        "receipt_digest": f"receipt:{kind}",
    }


@dataclass(frozen=True)
class AcceptanceFixture:
    data: dict[str, object]

    def copy(self) -> dict[str, object]:
        return copy.deepcopy(self.data)


@pytest.fixture
def valid_bundle() -> AcceptanceFixture:
    tickets = [
        {
            "key": "alpha",
            "ticket_key": "issue:10",
            "number": 10,
            "state": "OPEN",
            "labels": ["ready-for-agent"],
            "blocked_by": [],
            "contract_digest": "contract:alpha",
        },
        {
            "key": "beta",
            "ticket_key": "issue:11",
            "number": 11,
            "state": "OPEN",
            "labels": ["ready-for-agent"],
            "blocked_by": [],
            "contract_digest": "contract:beta",
        },
        {
            "key": "gamma",
            "ticket_key": "issue:12",
            "number": 12,
            "state": "OPEN",
            "labels": ["ready-for-agent"],
            "blocked_by": [],
            "contract_digest": "contract:gamma",
        },
        {
            "key": "delta",
            "ticket_key": "issue:13",
            "number": 13,
            "state": "OPEN",
            "labels": ["ready-for-agent"],
            "blocked_by": [],
            "contract_digest": "contract:delta",
        },
    ]
    candidates = [
        {
            "ticket_key": "issue:10",
            "assurance": "standard",
            "candidate_receipt_digest": "candidate:alpha",
        },
        {
            "ticket_key": "issue:11",
            "assurance": "standard",
            "candidate_receipt_digest": "candidate:beta",
        },
        {
            "ticket_key": "issue:12",
            "assurance": "standard",
            "candidate_receipt_digest": "candidate:gamma",
        },
        {
            "ticket_key": "issue:13",
            "assurance": "strict",
            "candidate_receipt_digest": "candidate:delta",
        },
    ]
    proof = {
        "worker_slot_limit": 4,
        "peak_worker_slots": 4,
        "refill_ticket_order": ["alpha", "beta", "gamma", "delta"],
        "permission_binding_pairs": [["binding:alpha", "binding:alpha"]],
        "stale_diagnosis_count_by_binding": [["binding:alpha", 1]],
        "binding_count_by_ticket": [
            ["alpha", 1],
            ["beta", 1],
            ["gamma", 1],
            ["delta", 1],
        ],
        "semantic_effect_ids": ["semantic:alpha"],
        "external_effect_ids": ["external:alpha"],
        "duplicate_effect_ids": [],
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
            "tickets": tickets,
            "candidates": candidates,
            "reviews": [
                {
                    "ticket_key": "issue:10",
                    "open_finding_ids": [],
                    "finding_ledger_digest": "finding:alpha",
                },
                {
                    "ticket_key": "issue:11",
                    "open_finding_ids": [],
                    "finding_ledger_digest": "finding:beta",
                },
                {
                    "ticket_key": "issue:12",
                    "open_finding_ids": [],
                    "finding_ledger_digest": "finding:gamma",
                },
                {
                    "ticket_key": "issue:13",
                    "open_finding_ids": [],
                    "finding_ledger_digest": "finding:delta",
                },
            ],
            "batches": [
                _batch(
                    "multi",
                    ["issue:10", "issue:11", "issue:12"],
                    "sha:multi",
                    201,
                    301,
                    "target:multi",
                ),
                _batch("singleton", ["issue:13"], "sha:singleton", 202, 302, "target:singleton"),
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
    assert receipt.ticket_contract_digests == (
        ("alpha", "contract:alpha"),
        ("beta", "contract:beta"),
        ("gamma", "contract:gamma"),
        ("delta", "contract:delta"),
    )
    assert receipt.candidate_receipt_digests == (
        ("alpha", "candidate:alpha"),
        ("beta", "candidate:beta"),
        ("gamma", "candidate:gamma"),
        ("delta", "candidate:delta"),
    )
    assert receipt.policy_witness_digest == "policy:1"
    assert receipt.fault_journal_digest == "fault:1"


def test_acceptance_digest_is_canonical_and_binds_repository_identity(
    valid_bundle: AcceptanceFixture,
):
    receipt = verify_root_canary(valid_bundle.data)

    assert receipt.receipt_digest == digest_value(receipt.canonical_digest_payload())
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
                "key": ticket["key"],
                "ticket_key": ticket["ticket_key"],
                "id": number,
                "node_id": f"node-{number}",
                "number": number,
                "title": f"Root Canary Ticket {ticket['key']}",
                "repository": {
                    "full_name": ROOT_REPOSITORY,
                    "url": f"https://api.github.com/repos/{ROOT_REPOSITORY}",
                },
                "state": "OPEN",
                "body": "root canary contract",
                "url": f"https://api.github.com/repos/{ROOT_REPOSITORY}/issues/{number}",
                "html_url": f"https://github.com/{ROOT_REPOSITORY}/issues/{number}",
                "updated_at": "2026-08-17T00:00:00Z",
                "labels": [{"name": "ready-for-agent"}],
                "comments": [],
                "blockers": [],
                "contract_digest": ticket["contract_digest"],
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
