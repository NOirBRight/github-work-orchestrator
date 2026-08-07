from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
for scripts_path in (ROOT / "scripts", ROOT / "skills" / "orchestrator" / "scripts"):
    if str(scripts_path) not in sys.path:
        sys.path.insert(0, str(scripts_path))

from gwo_v8._canonical import digest_value
from v8_batch_test_support import (
    make_accepted_candidate_receipt,
    make_batch_request,
    make_integrator,
    make_three_standard_receipts,
)


def test_beta2_evidence_schema_rejects_unknown_readback_keys():
    from write_v8_batch_evidence import _validate_readbacks

    with pytest.raises(SystemExit, match="exact Beta2 evidence schema"):
        _validate_readbacks({"unexpected": True}, 40)


def test_beta2_evidence_schema_uses_the_exact_contract_name():
    from write_v8_batch_evidence import BATCH_EVIDENCE_SCHEMA

    assert BATCH_EVIDENCE_SCHEMA == "gwo-v8-batch-beta2-evidence.v1"


def test_beta2_evidence_rejects_remote_repository_check_urls():
    from write_v8_batch_evidence import _validate_readbacks

    with pytest.raises(SystemExit, match="remote"):
        _validate_readbacks(
            {"repository_check_url": "https://github.com/owner/repo/actions"},
            40,
        )


def test_beta2_batch_boundary_has_three_standard_members_and_one_strict_singleton(
    tmp_path,
):
    standard = make_three_standard_receipts()
    strict = make_accepted_candidate_receipt(
        ticket_key="issue:4", assurance="strict", accepted_sequence=4
    )
    integrator, drivers = make_integrator(tmp_path, hosted_outcomes=("passed", "passed"))

    standard_action = integrator.prepare(
        make_batch_request(accepted_candidates=standard)
    )
    strict_action = integrator.prepare(
        make_batch_request(
            stable_action_id="delivery-action:strict",
            accepted_candidates=(strict,),
        )
    )
    standard_observation = integrator.execute(standard_action)
    strict_observation = integrator.execute(strict_action)

    assert standard_observation.phase == strict_observation.phase == "complete"
    assert len(standard_observation.members) == 3
    assert len(strict_observation.members) == 1
    assert standard_action.batch_sha != strict_action.batch_sha
    assert {item.candidate_sha for item in standard_observation.members} == {
        item.candidate_sha for item in standard
    }
    assert strict_observation.members[0].candidate_sha == strict.candidate_sha
    assert drivers.target_mutations == [standard_action.batch_sha, strict_action.batch_sha]
    assert len(standard_observation.delivery_proofs) == 1
    assert len(strict_observation.delivery_proofs) == 1
    assert standard_observation.delivery_proofs[0].batch_sha == standard_action.batch_sha
    assert strict_observation.delivery_proofs[0].batch_sha == strict_action.batch_sha


def test_beta2_successful_fallback_exports_exact_singleton_proof_partition(tmp_path):
    candidates = make_three_standard_receipts()
    integrator, drivers = make_integrator(
        tmp_path,
        hosted_outcomes=("code_failure", "passed", "passed", "passed"),
    )
    action = integrator.prepare(
        make_batch_request(accepted_candidates=candidates)
    )

    observations = [integrator.execute(action) for _ in range(4)]
    final = observations[-1]

    assert final.phase == "complete"
    assert final.fallback_generation == 1
    assert tuple(proof.member_ticket_keys for proof in final.delivery_proofs) == tuple(
        (candidate.ticket_key,) for candidate in candidates
    )
    assert [proof.batch_sha for proof in final.delivery_proofs] == (
        drivers.target_mutations
    )
    assert all(
        proof.delivery_stable_action_id != action.stable_action_id
        for proof in final.delivery_proofs
    )
    assert final.receipt_digest == digest_value(
        {"kind": "batch-observation.v1", **final.body()}
    )


def test_beta2_restart_and_failure_evidence_preserve_unaffected_member_facts(tmp_path):
    integrator, drivers = make_integrator(
        tmp_path,
        hosted_outcomes=("code_failure", "code_failure", "passed", "passed"),
    )
    candidates = make_three_standard_receipts()
    action = integrator.prepare(make_batch_request(accepted_candidates=candidates))

    [integrator.execute(action) for _ in range(4)]
    restarted, _restarted_drivers = make_integrator(tmp_path)
    final = restarted.readback(action)

    assert final is not None
    assert {member.candidate_sha for member in final.members} == {
        candidate.candidate_sha for candidate in candidates
    }
    assert drivers.resume_directives == [
        ("work-run:1", candidates[0].review_finding_ledger_digest)
    ]
    unaffected = {
        member.ticket_key: member.evidence_digests
        for observation in [final]
        for member in observation.members
        if member.ticket_key in {"issue:2", "issue:3"}
    }
    assert unaffected == {
        "issue:2": candidates[1].evidence_digests,
        "issue:3": candidates[2].evidence_digests,
    }
