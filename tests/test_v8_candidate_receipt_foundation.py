from __future__ import annotations

import pytest

from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.candidate_gate import (
    CandidateDiffEntryV1,
    CandidateDiffRecordV1,
    CandidateGateError,
    CandidateIdentity,
    CandidateReadback,
    CandidateReceipt,
)
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


def test_exact_v1_record_rejects_legacy_entry_with_raw_path():
    legacy_entry = CandidateDiffEntryV1(
        side="candidate",
        path="src/main.py",
        mode="100644",
        object_type="blob",
        object_oid="3" * 40,
    )

    with pytest.raises(CandidateGateError) as raised:
        CandidateDiffRecordV1(
            schema_version="CandidateDiffRecordV1",
            repository_object_format="sha1",
            base_commit_oid="a" * 40,
            base_tree_oid="b" * 40,
            candidate_commit_oid="c" * 40,
            candidate_tree_oid="d" * 40,
            entries=(legacy_entry,),
        )

    assert raised.value.code == "CANDIDATE_GATE_DIFF_INVALID"


def test_legacy_compatibility_is_explicit_and_not_global_id_state():
    import gwo_v8.candidate_gate as candidate_gate

    legacy_entry = CandidateDiffEntryV1(
        side="candidate",
        path="src/main.py",
        mode="100644",
        object_type="blob",
        object_oid="3" * 40,
    )
    legacy_record = CandidateDiffRecordV1(
        repository="owner/repository",
        object_format="sha1",
        base_commit_oid="a" * 40,
        base_tree_oid="b" * 40,
        candidate_commit_oid="c" * 40,
        candidate_tree_oid="d" * 40,
        entries=(legacy_entry,),
    )

    assert legacy_record.schema_version == "gwo.candidate-diff.v1"
    assert legacy_record.canonical()["kind"] == "candidate_diff_record.v1"
    assert legacy_entry._legacy_mode is True
    assert legacy_record._legacy_mode is True
    assert not hasattr(candidate_gate, "_LEGACY_DIFF_ENTRY_IDS")
    assert not hasattr(candidate_gate, "_LEGACY_DIFF_RECORD_REPOSITORIES")

    candidate = CandidateIdentity(
        reported_reference="refs/heads/candidate",
        base_commit_oid="a" * 40,
        base_tree_oid="b" * 40,
        candidate_commit_oid="c" * 40,
        candidate_tree_oid="d" * 40,
        changed_paths=("src/main.py",),
    )
    readback = CandidateReadback(
        repository="owner/repository",
        candidate=candidate,
        diff_record=legacy_record,
    )
    assert readback._legacy_compatibility is True
    assert readback.canonical()["diff_record"]["kind"] == "candidate_diff_record.v1"


def test_old_positional_schema_is_explicit_legacy_mode():
    entry = CandidateDiffEntryV1(
        "candidate",
        "src/main.py",
        "100644",
        "blob",
        "3" * 40,
    )
    record = CandidateDiffRecordV1(
        "gwo.candidate-diff.v1",
        "sha1",
        "a" * 40,
        "b" * 40,
        "c" * 40,
        "d" * 40,
        (entry,),
    )

    assert record._legacy_mode is True
    assert record.schema_version == "gwo.candidate-diff.v1"
    assert record.canonical()["kind"] == "candidate_diff_record.v1"
    assert record.canonical()["object_format"] == "sha1"
