from __future__ import annotations

from pathlib import Path
import copy
import json
import os
import subprocess
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


def _document_readbacks() -> dict[str, object]:
    document = (ROOT / "docs" / "e2e" / "gwo-v8-batch-integrator.md").read_text(
        encoding="utf-8"
    )
    fenced = document.split("## Exact Git, CI, Target, Recovery, and Receipt Readbacks", 1)[1]
    payload = fenced.split("```json\n", 1)[1].split("\n```", 1)[0]
    return json.loads(payload)


PUBLICATION_SUBJECT = {
    "parents": ["514f1162fe563f27edd35b4d6683df2786b7dcc0"],
    "sha": "bcc7e719ecd5176f29d496e7ec6d7c3819c96439",
    "tree": "81dca3a6296aa02182141975ae3d402ebd16c7ff",
}


def _document_with_publication_subject(subject: dict[str, object]) -> str:
    document = (ROOT / "docs" / "e2e" / "gwo-v8-batch-integrator.md").read_text(
        encoding="utf-8"
    )
    prefix, merged_results = document.split("## Merged Results", 1)
    title = prefix.split("## Verification Boundary", 1)[0]
    return (
        title
        + "## Verification Boundary\n\n"
        + "- Schema: `gwo-v8-batch-beta2-evidence.v1`.\n"
        + "- Mode: `Local Verification Only`.\n\n"
        + "## Publication Subject\n\n"
        + "```json\n"
        + json.dumps(subject, indent=2, sort_keys=True)
        + "\n```\n\n"
        + "## Merged Results"
        + merged_results
    )


def _run_evidence_check(document: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["GWO_BATCH_EVIDENCE_WRITING"] = "1"
    return subprocess.run(
        [
            "py",
            "-3.13",
            "scripts/write_v8_batch_evidence.py",
            "--check",
            "--output",
            str(document),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
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


@pytest.mark.parametrize(
    "section",
    (
        "infrastructure_retry",
        "successful_fallback",
        "singleton_fallback",
        "restart_adoption",
    ),
)
def test_beta2_evidence_rejects_unknown_nested_keys(section):
    from write_v8_batch_evidence import _validate_readbacks

    readbacks = _document_readbacks()
    readbacks[section]["unexpected"] = True

    with pytest.raises(SystemExit, match="exact|fields"):
        _validate_readbacks(readbacks, 40)


def test_beta2_evidence_binds_fallback_candidates_to_standard_partition():
    from write_v8_batch_evidence import _validate_readbacks

    readbacks = _document_readbacks()
    readbacks["singleton_fallback"]["singleton_candidate_shas"][0] = "0" * 40

    with pytest.raises(SystemExit, match="Candidate|partition|standard"):
        _validate_readbacks(readbacks, 40)


def test_beta2_evidence_binds_restart_and_retry_to_standard_batch():
    from write_v8_batch_evidence import _validate_readbacks

    readbacks = _document_readbacks()
    readbacks["restart_adoption"]["batch_sha"] = readbacks["strict_batch"]["batch_sha"]

    with pytest.raises(SystemExit, match="standard|Batch SHA|batch"):
        _validate_readbacks(readbacks, 40)


def test_beta2_check_rejects_tampered_readback_input(tmp_path):
    readbacks = _document_readbacks()
    readbacks["standard_batch"]["batch_sha"] = "0" * 40
    tampered = tmp_path / "tampered-readbacks.json"
    tampered.write_text(json.dumps(readbacks), encoding="utf-8")

    completed = subprocess.run(
        [
            "py",
            "-3.13",
            "scripts/write_v8_batch_evidence.py",
            "--check",
            "--readbacks",
            str(tampered),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert any(
        token in (completed.stderr + completed.stdout)
        for token in ("Batch SHA", "batch", "standard")
    )


def test_beta2_check_accepts_historical_sources_with_explicit_publication_subject(
    tmp_path,
):
    canonical = tmp_path / "canonical.md"
    canonical.write_text(
        _document_with_publication_subject(PUBLICATION_SUBJECT), encoding="utf-8"
    )

    completed = _run_evidence_check(canonical)

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_beta2_legacy_renderer_keeps_the_original_subject_section():
    from write_v8_batch_evidence import _render

    rendered = _render({}, PUBLICATION_SUBJECT, {}, [], {})

    assert "\n- Subject:\n" in rendered
    assert "## Publication Subject" not in rendered


def test_beta2_publication_subject_null_is_rejected(tmp_path):
    from write_v8_batch_evidence import _document_publication_subject

    canonical = tmp_path / "malformed.md"
    canonical.write_text(
        "## Publication Subject\n\n```json\nnull\n```\n\n"
        "## Merged Results\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="valid publication subject"):
        _document_publication_subject(canonical)


@pytest.mark.parametrize("mutation", ["missing", "sha", "tree", "parents"])
def test_beta2_check_rejects_missing_or_tampered_publication_subject(
    tmp_path, mutation
):
    subject = copy.deepcopy(PUBLICATION_SUBJECT)
    canonical = _document_with_publication_subject(subject)
    if mutation == "missing":
        publication = (
            "## Publication Subject\n\n```json\n"
            + json.dumps(subject, indent=2, sort_keys=True)
            + "\n```\n\n"
        )
        canonical = canonical.replace(publication, "")
    elif mutation == "sha":
        subject["sha"] = "c802171cb0262c32906c49e86403ec3567804a02"
        canonical = _document_with_publication_subject(subject)
    elif mutation == "tree":
        subject["tree"] = "0" * 40
        canonical = _document_with_publication_subject(subject)
    else:
        subject["parents"] = []
        canonical = _document_with_publication_subject(subject)
    path = tmp_path / f"{mutation}.md"
    path.write_text(canonical, encoding="utf-8")

    completed = _run_evidence_check(path)

    assert completed.returncode != 0


def test_beta2_evidence_check_is_self_contained_without_arguments():
    completed = subprocess.run(
        ["py", "-3.13", "scripts/write_v8_batch_evidence.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


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
