from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.batch_integrator import BatchIntegratorError
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
