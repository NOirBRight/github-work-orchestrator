from __future__ import annotations

import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from v8_production_test_support import write_beta2_evidence_bundle


def test_beta2_evidence_manifest_has_exact_release_gate_fields(tmp_path):
    exact_subject = {"sha": "a" * 40, "tree": "b" * 40, "parents": ["c" * 40]}
    path = write_beta2_evidence_bundle(
        tmp_path,
        subject=exact_subject,
        issue_states={str(n): "CLOSED" for n in (113, 114, 115, 116, 117, 136, 137)},
        campaign_handle="owner/repo:campaign:beta2",
        plan_revision_digest="d" * 64,
        writer_generation_before="v6.1",
        writer_generation_after="v6.1",
        result_integrity_digests=("e" * 64,),
        batch_delivery_proof_digests=("f" * 64,),
        issue_137_revalidation={
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
        local_verification_manifest_digest="a" * 64,
        workflow_count=0,
    )
    evidence = json.loads(path.read_text(encoding="utf-8"))
    assert evidence["schema_version"] == "gwo-v8-beta2-composition-evidence.v2"
    assert evidence["verification_mode"] == "Local Verification Only"
    assert evidence["subject"] == exact_subject
    assert evidence["workflow_count"] == 0
    assert evidence["writer_generation_before"] == evidence["writer_generation_after"]
    assert evidence["writer_activation_enabled"] is False
    assert set(evidence["full_gate"]) == {
        "pytest",
        "quick_validate",
        "package_sync",
        "diff_check",
        "clean_status",
    }


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
        "assert_isolated_e2e_target",
        "GWO_V8_REAL_PROVIDER_E2E=1",
        "GWO_V8_REAL_PROVIDER_COMMAND",
        "skips",
        "gwo-v8-beta2-composition-evidence.v2",
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
