from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import v8_production_test_support as support
from v8_production_test_support import write_beta2_evidence_bundle


def _valid_evidence_arguments() -> dict[str, object]:
    return {
        "issue_states": {
            str(number): "CLOSED"
            for number in (113, 114, 115, 116, 117, 136, 137)
        },
        "campaign_handle": "owner/repo:campaign:beta2",
        "plan_revision_digest": "d" * 64,
        "writer_generation_before": "v6.1",
        "writer_generation_after": "v6.1",
        "result_integrity_digests": ("e" * 64,),
        "batch_delivery_proof_digests": ("f" * 64,),
        "issue_137_revalidation": {
            "open_approval_digest": "1" * 64,
            "open_readback_digest": "2" * 64,
            "candidate_route_digest": "3" * 64,
            "formal_review_route_digest": "4" * 64,
            "repair_route_digest": "5" * 64,
            "ordinary_rejection_digest": "6" * 64,
            "replay_restart_digest": "7" * 64,
            "close_approval_digest": "8" * 64,
            "closed_readback_digest": "9" * 64,
        },
        "local_verification_manifest_digest": "a" * 64,
        "workflow_count": 0,
    }


def test_beta2_evidence_manifest_has_exact_release_gate_fields(tmp_path):
    exact_subject = {"sha": "a" * 40, "tree": "b" * 40, "parents": ["c" * 40]}
    arguments = _valid_evidence_arguments()
    path = write_beta2_evidence_bundle(
        tmp_path,
        subject=exact_subject,
        **arguments,
    )
    expected = {
        "schema_version": "gwo-v8-beta2-composition-evidence.v2",
        "verification_mode": "Local Verification Only",
        "preview_mode": "beta2_isolated_preview",
        "subject": exact_subject,
        "issue_states": arguments["issue_states"],
        "campaign_handle": arguments["campaign_handle"],
        "plan_revision_digest": arguments["plan_revision_digest"],
        "writer_generation_before": arguments["writer_generation_before"],
        "writer_generation_after": arguments["writer_generation_after"],
        "writer_activation_enabled": False,
        "result_integrity_digests": list(arguments["result_integrity_digests"]),
        "batch_delivery_proof_digests": list(
            arguments["batch_delivery_proof_digests"]
        ),
        "issue_137_revalidation": arguments["issue_137_revalidation"],
        "local_verification_manifest_digest": arguments[
            "local_verification_manifest_digest"
        ],
        "workflow_count": arguments["workflow_count"],
        "full_gate": {
            "pytest": {"status": "passed"},
            "quick_validate": {"status": "passed"},
            "package_sync": {"status": "passed"},
            "diff_check": {"status": "passed"},
            "clean_status": {"status": "passed", "output": ""},
        },
        "target_isolation": True,
    }
    canonical_bytes = (
        json.dumps(
            expected,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    final_bytes = path.read_bytes()
    assert path.name == "beta2-composition-fixture.json"
    assert not (tmp_path / "beta2-evidence.json").exists()
    assert json.loads(final_bytes.decode("utf-8")) == expected
    assert final_bytes == canonical_bytes
    assert final_bytes.endswith(b"\n")
    assert hashlib.sha256(final_bytes).hexdigest() == hashlib.sha256(
        canonical_bytes
    ).hexdigest()
    assert not list(tmp_path.glob(".beta2-evidence-*.tmp"))
    forbidden_fields = (
        "ci_url",
        "hosted_repository_check",
        "hosted_check",
        "workflow_run",
        "workflow_runs",
    )
    rendered = final_bytes.decode("utf-8")
    for field in forbidden_fields:
        assert field not in rendered


def test_beta2_fixture_cleans_same_directory_temp_on_replace_failure(
    tmp_path, monkeypatch
):
    def fail_replace(source, destination):
        raise OSError("injected replacement failure")

    monkeypatch.setattr(support.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replacement failure"):
        write_beta2_evidence_bundle(
            tmp_path,
            subject={"sha": "a" * 40, "tree": "b" * 40, "parents": []},
            **_valid_evidence_arguments(),
        )
    assert not list(tmp_path.glob(".beta2-evidence-*.tmp"))
    assert not (tmp_path / "beta2-composition-fixture.json").exists()


def test_production_composition_runbook_contains_beta2_safety_gates():
    runbook = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "operations"
        / "gwo-v8-production-composition.md"
    )
    assert runbook.exists()
    content = runbook.read_text(encoding="utf-8")
    required_fragments = (
        "start(repository, ready_refs, options?)",
        "advance(campaign_handle, wake_ref?)",
        "inspect(campaign_handle)",
        "PlanControl",
        "ExecutionKernel",
        "RuntimeGateway",
        "CandidateGate",
        "BatchIntegrator",
        "compare-and-swap",
        "immediate readback",
        "restart",
        "Candidate receipt",
        "accepted-Candidate receipt",
        "Batch delivery proof",
        "target readback",
        'preview_mode="beta2_isolated_preview"',
        "target_isolation_root",
        "writer_activation_enabled=False",
        "create_temporary_target",
        "before calling mkdir",
        "does not mutate that checkout",
        "assert_isolated_e2e_target",
        "GWO_V8_REAL_PROVIDER_E2E=1",
        "GWO_V8_REAL_PROVIDER_COMMAND",
        "skips",
        "REAL_PROVIDER_UNSUPPORTED",
        "fails closed",
        "gwo-v8-beta2-composition-evidence.v2",
        "beta2-composition-fixture.json",
        "partial composition artifact",
        "not Task 10 GO evidence",
        "Local Verification Only",
        "py -3.13 -m pytest tests/test_v8_production_composition_e2e.py tests/test_v8_production_docs.py -q",
        "py -3.13 -m pytest -q",
        "py -3.13 scripts/quick_validate.py",
        "py -3.13 scripts/sync_orchestrator.py --check",
        "git diff --check",
        "#137",
        "open approval",
        "revalidation",
        "close approval",
        "no production",
        "no writer",
        "#118",
        "no CI URL",
    )
    missing = [fragment for fragment in required_fragments if fragment not in content]
    assert not missing, f"runbook is missing: {missing}"
    assert "http://" not in content
    assert "https://" not in content
