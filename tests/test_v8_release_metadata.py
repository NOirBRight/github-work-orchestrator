from __future__ import annotations

from dataclasses import dataclass
import json
from types import SimpleNamespace

import pytest

from scripts.render_v8_ga_metadata import render_ga_documents
from scripts.verify_v8_ga_release import (
    CiReadback,
    GaReleaseRecord,
    ReleaseGateError,
    load_ga_release_record,
    parse_pytest_count,
    verify_pre_tag,
    write_ga_release_record,
    write_release_contract,
)


@dataclass(frozen=True)
class CompleteReleaseFixture:
    version: str = "8.0.0"
    repository: str = "NOirBRight/github-work-orchestrator"
    evidence_base_sha: str = "1" * 40
    canary_target_sha: str = "2" * 40
    canary_receipt_digest: str = "canary:receipt"
    activation_receipt_digest: str = "activation:receipt"
    default_writer_receipt_digest: str = "default:receipt"


def test_ga_record_binds_static_receipts_without_dynamic_sha_or_ci(tmp_path):
    fixture = CompleteReleaseFixture()
    path = write_ga_release_record(tmp_path / "ga-record.json", fixture)

    record = load_ga_release_record(path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert record.version == fixture.version
    assert record.evidence_base_sha == fixture.evidence_base_sha
    assert record.canary_target_sha == fixture.canary_target_sha
    assert record.canary_receipt_digest == fixture.canary_receipt_digest
    assert not hasattr(record, "tag_candidate_sha")
    assert not hasattr(record, "pytest_pass_count")
    assert not {
        "main_sha",
        "ci_head_sha",
        "ci_run_id",
        "pytest_pass_count",
        "tag_candidate_sha",
        "final_metadata_commit_sha",
    }.intersection(payload)


def test_static_ga_record_rejects_dynamic_fields(tmp_path):
    fixture = CompleteReleaseFixture()
    path = write_ga_release_record(tmp_path / "ga-record.json", fixture)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["tag_candidate_sha"] = "3" * 40
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReleaseGateError) as error:
        load_ga_release_record(path)

    assert error.value.code == "GA_STATIC_RECORD_CONTAINS_DYNAMIC_SHA_OR_CI"


def test_pre_tag_receipt_binds_dynamic_tag_candidate_and_exact_ci():
    record = GaReleaseRecord.from_fixture(CompleteReleaseFixture())
    canary = SimpleNamespace(
        canary_target_sha=record.canary_target_sha,
        receipt_digest=record.canary_receipt_digest,
    )
    activation = SimpleNamespace(
        activation_id="activation:1",
        repository=record.repository,
        writer_generation="v8",
        receipt_digest=record.activation_receipt_digest,
    )
    admission = SimpleNamespace(
        mode="default_v8",
        repository=record.repository,
        writer_generation="v8",
        activation_id="activation:1",
        acceptance_receipt_digest=record.canary_receipt_digest,
        receipt_digest=record.default_writer_receipt_digest,
    )
    ci = CiReadback(
        run_id=987,
        head_sha="3" * 40,
        conclusion="success",
        pytest_pass_count=42,
    )
    git = SimpleNamespace(
        is_ancestor=lambda ancestor, descendant: True,
        changed_paths=lambda base, candidate: (
            "CHANGELOG.md",
            "docs/e2e/gwo-v8-root-canary.md",
            "docs/releases/v8.0.0.md",
        ),
    )

    receipt = verify_pre_tag(
        record,
        main_sha=ci.head_sha,
        canary=canary,
        activation=activation,
        admission=admission,
        ci=ci,
        git=git,
    )

    assert receipt.tag_candidate_sha == ci.head_sha
    assert receipt.ci_head_sha == ci.head_sha
    assert receipt.ci_run_id == 987
    assert receipt.pytest_pass_count == 42


def test_pre_tag_rejects_ci_for_a_different_main_sha():
    record = GaReleaseRecord.from_fixture(CompleteReleaseFixture())
    ci = CiReadback(987, "3" * 40, "success", 42)

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha="4" * 40,
            canary=SimpleNamespace(
                canary_target_sha=record.canary_target_sha,
                receipt_digest=record.canary_receipt_digest,
            ),
            activation=SimpleNamespace(
                activation_id="activation:1",
                repository=record.repository,
                writer_generation="v8",
                receipt_digest=record.activation_receipt_digest,
            ),
            admission=SimpleNamespace(
                mode="default_v8",
                repository=record.repository,
                writer_generation="v8",
                activation_id="activation:1",
                acceptance_receipt_digest=record.canary_receipt_digest,
                receipt_digest=record.default_writer_receipt_digest,
            ),
            ci=ci,
            git=SimpleNamespace(
                is_ancestor=lambda ancestor, descendant: True,
                changed_paths=lambda base, candidate: (),
            ),
        )

    assert error.value.code == "GA_EXACT_CI_REQUIRED"


def test_pytest_count_comes_from_the_last_dynamic_ci_summary():
    assert parse_pytest_count("unit: 2 passed\nfull: 42 passed in 1.2s\n") == 42

    with pytest.raises(ReleaseGateError) as error:
        parse_pytest_count("CI log without a pytest summary")
    assert error.value.code == "GA_CI_PYTEST_COUNT_MISSING"


def test_renderer_writes_static_metadata_without_dynamic_sha_or_ci(tmp_path):
    paths = render_ga_documents(
        tmp_path,
        evidence_base_sha="4" * 40,
        tickets={"tickets": [{"number": 1, "ci_run_id": 99}]},
        acceptance={
            "repository": "NOirBRight/github-work-orchestrator",
            "campaign_key": "campaign:root",
            "canary_target_sha": "5" * 40,
            "receipt_digest": "canary:1",
            "tag_candidate_sha": "6" * 40,
        },
        named_admission={"receipt_digest": "named:1", "ci_head_sha": "7" * 40},
        default_writer={
            "receipt_digest": "default:1",
            "activation_id": "activation:1",
            "writer_generation": "v8",
            "pytest_pass_count": 42,
        },
    )

    assert [path.relative_to(tmp_path).as_posix() for path in paths] == [
        "CHANGELOG.md",
        "docs/e2e/gwo-v8-root-canary.md",
        "docs/releases/v8.0.0.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "tag_candidate_sha" not in combined
    assert "ci_run_id" not in combined
    assert "ci_head_sha" not in combined
    assert "pytest_pass_count" not in combined


def test_release_contract_freezes_dynamic_values_as_runtime_only(tmp_path):
    path = tmp_path / "gwo-v8-ga-release-contract.md"
    write_release_contract(path)
    text = path.read_text(encoding="utf-8")

    assert "evidence_base_sha" in text
    assert "tag-candidate SHA" in text
    assert "final metadata commit SHA" in text
