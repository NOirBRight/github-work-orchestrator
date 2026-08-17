from __future__ import annotations

from dataclasses import dataclass
import json
from types import SimpleNamespace

import pytest

import scripts.render_v8_ga_metadata as renderer
import scripts.verify_v8_ga_release as verifier
from scripts.render_v8_ga_metadata import render_ga_documents
from scripts.verify_v8_ga_release import (
    CiReadback,
    GaReleaseRecord,
    GitCliReadback,
    ReleaseGateError,
    digest_value,
    load_ga_release_record,
    parse_pytest_count,
    verify_pre_tag,
    verify_main,
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
    campaign_key: str = "campaign:root"
    activation_id: str = "activation:1"
    writer_generation: str = "v8"


def _readback(**payload: object) -> SimpleNamespace:
    return SimpleNamespace(**payload, receipt_digest=digest_value(payload))


def _canonical_pre_tag_case(
    *,
    canary_repository: str = "NOirBRight/github-work-orchestrator",
    activation_campaign: str = "campaign:root",
    admission_campaign: str = "campaign:root",
    main_sha: str = "3" * 40,
):
    repository = "NOirBRight/github-work-orchestrator"
    campaign = "campaign:root"
    activation_id = "activation:1"
    writer_generation = "v8"
    canary_body = {
        "repository": canary_repository,
        "campaign_key": campaign,
        "canary_target_sha": "2" * 40,
        "activation_id": activation_id,
        "subject": {"repository": canary_repository, "campaign_key": campaign},
    }
    activation_body = {
        "repository": repository,
        "campaign_key": activation_campaign,
        "activation_id": activation_id,
        "writer_generation": writer_generation,
        "readback": {"repository": repository, "campaign_key": activation_campaign},
    }
    admission_body = {
        "mode": "default_v8",
        "repository": repository,
        "campaign_key": admission_campaign,
        "writer_generation": writer_generation,
        "activation_id": activation_id,
        "acceptance_receipt_digest": digest_value(canary_body),
        "readback": {"repository": repository, "campaign_key": admission_campaign},
    }
    record = GaReleaseRecord(
        version="8.0.0",
        repository=repository,
        evidence_base_sha="1" * 40,
        canary_target_sha=canary_body["canary_target_sha"],
        canary_receipt_digest=digest_value(canary_body),
        activation_receipt_digest=digest_value(activation_body),
        default_writer_receipt_digest=digest_value(admission_body),
        campaign_key=campaign,
        activation_id=activation_id,
        writer_generation=writer_generation,
    )
    canary = _readback(**canary_body)
    activation = _readback(**activation_body)
    admission = _readback(**admission_body)
    ci = CiReadback(987, main_sha, "success", 42)
    git = SimpleNamespace(
        repository=repository,
        current_origin_main_sha=lambda: main_sha,
        is_ancestor=lambda ancestor, descendant: True,
        changed_paths=lambda base, candidate: (
            "CHANGELOG.md",
            "docs/e2e/gwo-v8-root-canary.md",
            "docs/releases/v8.0.0.md",
        ),
    )
    return record, canary, activation, admission, ci, git


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
    record, canary, activation, admission, ci, git = _canonical_pre_tag_case()

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
    record, canary, activation, admission, _ci, git = _canonical_pre_tag_case(
        main_sha="4" * 40
    )
    ci = CiReadback(987, "3" * 40, "success", 42)

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha="4" * 40,
            canary=canary,
            activation=activation,
            admission=admission,
            ci=ci,
            git=git,
        )

    assert error.value.code == "GA_EXACT_CI_REQUIRED"


def test_pre_tag_rejects_tampered_canary_payload_even_with_claimed_digest():
    record, canary, activation, admission, ci, git = _canonical_pre_tag_case()
    tampered = dict(vars(canary))
    tampered["subject"] = {"repository": "foreign/repository"}

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha=ci.head_sha,
            canary=SimpleNamespace(**tampered),
            activation=activation,
            admission=admission,
            ci=ci,
            git=git,
        )

    assert error.value.code == "GA_CANARY_RECEIPT_INVALID"


def test_pre_tag_rejects_foreign_canary_repository_with_valid_receipt_digest():
    record, _canary, activation, admission, ci, git = _canonical_pre_tag_case(
        canary_repository="foreign/repository"
    )
    canary = _readback(
        repository="foreign/repository",
        campaign_key="campaign:root",
        canary_target_sha=record.canary_target_sha,
        activation_id="activation:1",
        subject={"repository": "foreign/repository", "campaign_key": "campaign:root"},
    )
    record = GaReleaseRecord(
        version=record.version,
        repository=record.repository,
        evidence_base_sha=record.evidence_base_sha,
        canary_target_sha=record.canary_target_sha,
        canary_receipt_digest=canary.receipt_digest,
        activation_receipt_digest=record.activation_receipt_digest,
        default_writer_receipt_digest=record.default_writer_receipt_digest,
        campaign_key=record.campaign_key,
        activation_id=record.activation_id,
        writer_generation=record.writer_generation,
    )

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha=ci.head_sha,
            canary=canary,
            activation=activation,
            admission=admission,
            ci=ci,
            git=git,
        )

    assert error.value.code == "GA_CANARY_RECEIPT_MISMATCH"


def test_pre_tag_rejects_foreign_activation_campaign_with_valid_receipt_digest():
    record, canary, _activation, admission, ci, git = _canonical_pre_tag_case(
        activation_campaign="foreign-campaign"
    )
    activation = _readback(
        repository=record.repository,
        campaign_key="foreign-campaign",
        activation_id="activation:1",
        writer_generation="v8",
        readback={
            "repository": record.repository,
            "campaign_key": "foreign-campaign",
        },
    )
    record = GaReleaseRecord(
        version=record.version,
        repository=record.repository,
        evidence_base_sha=record.evidence_base_sha,
        canary_target_sha=record.canary_target_sha,
        canary_receipt_digest=record.canary_receipt_digest,
        activation_receipt_digest=activation.receipt_digest,
        default_writer_receipt_digest=record.default_writer_receipt_digest,
        campaign_key=record.campaign_key,
        activation_id=record.activation_id,
        writer_generation=record.writer_generation,
    )

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha=ci.head_sha,
            canary=canary,
            activation=activation,
            admission=admission,
            ci=ci,
            git=git,
        )

    assert error.value.code == "GA_ACTIVATION_READBACK_INVALID"


def test_pre_tag_binds_to_current_origin_main_and_git_repository():
    record, canary, activation, admission, ci, git = _canonical_pre_tag_case()
    git.current_origin_main_sha = lambda: "4" * 40

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha=ci.head_sha,
            canary=canary,
            activation=activation,
            admission=admission,
            ci=ci,
            git=git,
        )

    assert error.value.code == "GA_MAIN_SHA_READBACK_MISMATCH"


def test_pre_tag_rejects_git_readback_for_a_foreign_repository():
    record, canary, activation, admission, ci, git = _canonical_pre_tag_case()
    git.repository = "foreign/repository"

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha=ci.head_sha,
            canary=canary,
            activation=activation,
            admission=admission,
            ci=ci,
            git=git,
        )

    assert error.value.code == "GA_REPOSITORY_READBACK_INVALID"


def test_pre_tag_cli_rejects_ci_run_id_that_does_not_match_exact_readback(
    tmp_path, monkeypatch
):
    record, canary, activation, admission, ci, _git = _canonical_pre_tag_case()
    record_path = write_ga_release_record(tmp_path / "record.json", record)
    canary_path = tmp_path / "canary.json"
    activation_path = tmp_path / "activation.json"
    admission_path = tmp_path / "admission.json"
    for path, value in (
        (canary_path, canary),
        (activation_path, activation),
        (admission_path, admission),
    ):
        path.write_bytes(json.dumps(vars(value)).encode("utf-8"))

    def fake_check_output(arguments, *, text=True):
        del text
        if arguments[:3] == ("git", "rev-parse", "--verify"):
            return ci.head_sha
        if arguments[0:3] == ("gh", "run", "view") and "--json" in arguments:
            return json.dumps(
                {
                    "databaseId": ci.run_id + 1,
                    "headSha": ci.head_sha,
                    "conclusion": ci.conclusion,
                }
            )
        if arguments[0:3] == ("gh", "run", "view") and "--log" in arguments:
            return "42 passed in 1.0s\n"
        raise AssertionError(arguments)

    monkeypatch.setattr(verifier.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(
        GitCliReadback,
        "current_origin_main_sha",
        lambda self: ci.head_sha,
        raising=False,
    )
    monkeypatch.setattr(GitCliReadback, "is_ancestor", lambda self, a, d: True)
    monkeypatch.setattr(
        GitCliReadback,
        "changed_paths",
        lambda self, a, d: (
            "CHANGELOG.md",
            "docs/e2e/gwo-v8-root-canary.md",
            "docs/releases/v8.0.0.md",
        ),
    )

    result = verify_main(
        [
            "--pre-tag",
            "--main-sha",
            ci.head_sha,
            "--record",
            str(record_path),
            "--canary",
            str(canary_path),
            "--activation",
            str(activation_path),
            "--default-writer",
            str(admission_path),
            "--ci-run",
            str(ci.run_id),
            "--repository",
            record.repository,
            "--output",
            str(tmp_path / "receipt.json"),
        ]
    )

    assert result == 2
    assert not (tmp_path / "receipt.json").exists()


def test_pytest_count_comes_from_the_last_dynamic_ci_summary():
    assert parse_pytest_count("unit: 2 passed\nfull: 42 passed in 1.2s\n") == 42

    with pytest.raises(ReleaseGateError) as error:
        parse_pytest_count("CI log without a pytest summary")
    assert error.value.code == "GA_CI_PYTEST_COUNT_MISSING"


def test_renderer_writes_static_metadata_without_dynamic_sha_or_ci(tmp_path):
    paths = render_ga_documents(
        tmp_path,
        evidence_base_sha="4" * 40,
        tickets={"tickets": [{"number": 1}]},
        acceptance={
            "repository": "NOirBRight/github-work-orchestrator",
            "campaign_key": "campaign:root",
            "canary_target_sha": "5" * 40,
            "receipt_digest": "canary:1",
        },
        named_admission={"receipt_digest": "named:1"},
        default_writer={
            "receipt_digest": "default:1",
            "activation_id": "activation:1",
            "writer_generation": "v8",
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


@pytest.mark.parametrize(
    "dynamic_payload",
    [
        {"nested": {"tag_candidate_sha": "6" * 40}},
        {"ciEnvelope": {"headSha": "7" * 40}},
    ],
)
def test_renderer_rejects_nested_or_aliased_dynamic_metadata(tmp_path, dynamic_payload):
    with pytest.raises(ReleaseGateError) as error:
        render_ga_documents(
            tmp_path,
            evidence_base_sha="4" * 40,
            tickets={"tickets": [dynamic_payload]},
            acceptance={
                "repository": "NOirBRight/github-work-orchestrator",
                "campaign_key": "campaign:root",
                "canary_target_sha": "5" * 40,
                "receipt_digest": "canary:1",
            },
            named_admission={"receipt_digest": "named:1"},
            default_writer={
                "receipt_digest": "default:1",
                "activation_id": "activation:1",
                "writer_generation": "v8",
            },
        )

    assert error.value.code == "GA_DYNAMIC_METADATA_INPUT"


def test_renderer_retains_legitimate_nested_receipt_values(tmp_path):
    paths = render_ga_documents(
        tmp_path,
        evidence_base_sha="4" * 40,
        tickets={"tickets": [{"number": 1, "receipt_digest": "ticket:receipt"}]},
        acceptance={
            "repository": "NOirBRight/github-work-orchestrator",
            "campaign_key": "campaign:root",
            "canary_target_sha": "5" * 40,
            "receipt_digest": "canary:1",
            "readback": {"receipt_digest": "nested:receipt"},
        },
        named_admission={"receipt_digest": "named:1"},
        default_writer={
            "receipt_digest": "default:1",
            "activation_id": "activation:1",
            "writer_generation": "v8",
        },
    )

    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "nested:receipt" in combined
    assert "ticket:receipt" in combined


def test_renderer_does_not_leave_partial_documents_after_write_failure(
    tmp_path, monkeypatch
):
    calls = 0

    def fail_on_first_document(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise OSError("simulated metadata write failure")

    monkeypatch.setattr(renderer, "_write_markdown_json", fail_on_first_document)

    with pytest.raises(OSError, match="simulated metadata write failure"):
        render_ga_documents(
            tmp_path,
            evidence_base_sha="4" * 40,
            tickets={"tickets": [{"number": 1}]},
            acceptance={
                "repository": "NOirBRight/github-work-orchestrator",
                "campaign_key": "campaign:root",
                "canary_target_sha": "5" * 40,
                "receipt_digest": "canary:1",
            },
            named_admission={"receipt_digest": "named:1"},
            default_writer={
                "receipt_digest": "default:1",
                "activation_id": "activation:1",
                "writer_generation": "v8",
            },
        )

    assert calls == 1
    assert not (tmp_path / "CHANGELOG.md").exists()
    assert not (tmp_path / "docs/e2e/gwo-v8-root-canary.md").exists()
    assert not (tmp_path / "docs/releases/v8.0.0.md").exists()


def test_release_contract_freezes_dynamic_values_as_runtime_only(tmp_path):
    path = tmp_path / "gwo-v8-ga-release-contract.md"
    write_release_contract(path)
    text = path.read_text(encoding="utf-8")

    assert "evidence_base_sha" in text
    assert "tag-candidate SHA" in text
    assert "final metadata commit SHA" in text
