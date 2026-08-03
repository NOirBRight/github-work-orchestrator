from __future__ import annotations

import pytest

from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.candidate_gate import CandidateGateError, CandidateReceipt
from v8_candidate_assurance_test_support import (
    make_candidate_diff_record,
    make_candidate_receipt,
)


def test_candidate_receipt_canonical_exposes_candidate_tree_oid_at_root():
    receipt = make_candidate_receipt()
    value = receipt.canonical()
    assert value["candidate_tree_oid"] == receipt.candidate_tree_oid
    assert value["diff_record_digest"] == receipt.diff_record_digest
    assert value["authority_subtree_digest"] == receipt.authority_subtree_digest
    assert value["receipt_digest"] == receipt.digest


def test_candidate_receipt_round_trip_recomputes_digest():
    receipt = make_candidate_receipt()
    assert CandidateReceipt.from_canonical(receipt.canonical()) == receipt


@pytest.mark.parametrize(
    "field",
    [
        "parent_digest",
        "candidate_commit_oid",
        "candidate_tree_oid",
        "diff_record_digest",
        "runtime_subject_digest",
    ],
)
def test_candidate_receipt_rejects_adversarial_identity_tamper(field):
    receipt = make_candidate_receipt()
    value = receipt.canonical()
    value[field] = "f" * (40 if field.endswith("_oid") else 64)
    with pytest.raises(CandidateGateError) as raised:
        CandidateReceipt.from_canonical(value)
    assert raised.value.code == "CANDIDATE_RECEIPT_INVALID"


def test_candidate_diff_record_contains_complete_old_new_entry_identity():
    record = make_candidate_diff_record()
    assert record.canonical()["entries"][0] == {
        "old_path": None,
        "new_path": "c3JjL21haW4ucHk",
        "change_kind": "add",
        "old_mode": None,
        "new_mode": "100644",
        "old_object_type": None,
        "new_object_type": "blob",
        "old_oid": None,
        "new_oid": "3" * 40,
    }
