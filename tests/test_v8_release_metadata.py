from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import io
import json
import math
from pathlib import Path
import tarfile
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


def _post_release_receipt_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": "8.0.0",
        "repository": "NOirBRight/github-work-orchestrator",
        "evidence_base_sha": "1" * 40,
        "canary_target_sha": "2" * 40,
        "tag_candidate_sha": "3" * 40,
        "tag_candidate_tree_sha": "4" * 40,
        "ci_run_id": 987,
        "ci_head_sha": "3" * 40,
        "pytest_pass_count": 42,
        "canary_receipt_digest": "canary:receipt",
        "activation_receipt_digest": "activation:receipt",
        "default_writer_receipt_digest": "default:receipt",
        "campaign_key": "campaign:root",
        "activation_id": "activation:1",
        "writer_generation": "v8",
    }
    payload.update(overrides)
    return payload


def _empty_tar_bytes() -> bytes:
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w"):
        pass
    return archive.getvalue()


def _canonical_pre_tag_case(
    *,
    canary_repository: str = "NOirBRight/github-work-orchestrator",
    activation_campaign: str = "campaign:root",
    admission_campaign: str = "campaign:root",
    main_sha: str = "3" * 40,
    canary_acceptance_mode: str | None = None,
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
    if canary_acceptance_mode is not None:
        canary_body["acceptance_mode"] = canary_acceptance_mode
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
        tree_sha=lambda commit: "a" * 40,
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
    path.write_bytes(verifier.canonical_json_bytes(payload))

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
    assert receipt.tag_candidate_tree_sha == "a" * 40


def test_pre_tag_accepts_default_v8_readback_with_no_campaign_key():
    record, canary, activation, admission, ci, git = _canonical_pre_tag_case()
    admission_body = dict(vars(admission))
    admission_body.pop("receipt_digest")
    admission_body["campaign_key"] = None
    admission_body["readback"] = {
        "repository": record.repository,
        "campaign_key": None,
    }
    admission = SimpleNamespace(
        **admission_body,
        receipt_digest=digest_value(admission_body),
    )
    record = replace(record, default_writer_receipt_digest=admission.receipt_digest)

    receipt = verify_pre_tag(
        record,
        main_sha=ci.head_sha,
        canary=canary,
        activation=activation,
        admission=admission,
        ci=ci,
        git=git,
    )

    assert receipt.campaign_key == record.campaign_key


def test_pre_tag_rejects_origin_main_drift_before_success():
    record, canary, activation, admission, ci, git = _canonical_pre_tag_case()
    main_readbacks = iter((ci.head_sha, "4" * 40))
    git.current_origin_main_sha = lambda: next(main_readbacks)

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


def test_git_readback_is_bound_to_explicit_checkout_and_origin_repository(
    tmp_path, monkeypatch
):
    calls = []
    repository = "NOirBRight/github-work-orchestrator"

    def fake_check_output(arguments, *, cwd=None, text=True):
        calls.append((arguments, cwd))
        if arguments == ("git", "remote", "get-url", "origin"):
            return "https://github.com/NOirBRight/github-work-orchestrator.git\n"
        if arguments == (
            "git",
            "rev-parse",
            "--verify",
            "refs/remotes/origin/main",
        ):
            return "3" * 40 + "\n"
        if arguments == ("git", "rev-parse", "--verify", "3" * 40 + "^{tree}"):
            return "a" * 40 + "\n"
        if arguments == (
            "git",
            "diff",
            "--name-only",
            "1" * 40 + ".." + "3" * 40,
        ):
            return "CHANGELOG.md\n"
        raise AssertionError(arguments)

    def fake_run(arguments, *, cwd=None, check=False):
        calls.append((arguments, cwd))
        assert arguments == (
            "git",
            "merge-base",
            "--is-ancestor",
            "1" * 40,
            "3" * 40,
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(verifier.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    git = GitCliReadback(repository, checkout=tmp_path)

    assert git.current_origin_main_sha() == "3" * 40
    assert git.tree_sha("3" * 40) == "a" * 40
    assert git.is_ancestor("1" * 40, "3" * 40)
    assert git.changed_paths("1" * 40, "3" * 40) == ("CHANGELOG.md",)
    assert calls
    assert all(cwd == tmp_path for _arguments, cwd in calls)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"repository":"repo","repository":"other"}',
        b'{ "repository": "repo" }\n',
    ],
)
def test_snapshot_rejects_duplicate_or_noncanonical_json(tmp_path, raw):
    path = tmp_path / "readback.json"
    path.write_bytes(raw)

    with pytest.raises(ReleaseGateError) as error:
        verifier._snapshot(path)

    assert error.value.code == "GA_RECEIPT_UNREADABLE"


def test_pre_tag_rejects_non_string_nested_identity():
    record, canary, activation, admission, ci, git = _canonical_pre_tag_case()
    payload = dict(vars(canary))
    payload.pop("receipt_digest")
    payload["subject"] = {
        "repository": 17,
        "campaign_key": record.campaign_key,
    }
    tampered = SimpleNamespace(**payload, receipt_digest=digest_value(payload))

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha=ci.head_sha,
            canary=tampered,
            activation=activation,
            admission=admission,
            ci=ci,
            git=git,
        )

    assert error.value.code == "GA_CANARY_RECEIPT_MISMATCH"


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
    tmp_path, monkeypatch, capsys
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
        path.write_bytes(verifier.canonical_json_bytes(vars(value)))

    def fake_check_output(arguments, *, text=True, cwd=None):
        del text, cwd
        if arguments == ("git", "remote", "get-url", "origin"):
            return "https://github.com/NOirBRight/github-work-orchestrator.git\n"
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
    assert "GA_GITHUB_CI_DISABLED" in capsys.readouterr().err


def test_pre_tag_cli_accepts_local_verification_without_github_ci(
    tmp_path, monkeypatch
):
    record, canary, activation, admission, ci, _git = _canonical_pre_tag_case(
        canary_acceptance_mode="local-only-v1"
    )
    record_path = write_ga_release_record(tmp_path / "record.json", record)
    canary_path = tmp_path / "canary.json"
    activation_path = tmp_path / "activation.json"
    admission_path = tmp_path / "admission.json"
    for path, value in (
        (canary_path, canary),
        (activation_path, activation),
        (admission_path, admission),
    ):
        path.write_bytes(verifier.canonical_json_bytes(vars(value)))

    local_verification_path = tmp_path / "local-verification.json"
    local_verification_path.write_bytes(
        verifier.canonical_json_bytes(
            {
                "schema": "gwo-c1-local-verification.v2",
                "mode": "Local Verification Only",
                "subject_sha": ci.head_sha,
                "subject_tree": "a" * 40,
                "workflow_count": 0,
                "final_outcome": "pass",
                "commands": [
                    {
                        "name": "full",
                        "arguments": ["-m", "pytest", "-q"],
                        "exit_code": 0,
                        "summary": "42 passed in 1.0s",
                    }
                ],
            }
        )
    )

    def fake_check_output(arguments, *, text=True, cwd=None):
        del text, cwd
        if arguments == ("git", "remote", "get-url", "origin"):
            return "https://github.com/NOirBRight/github-work-orchestrator.git\n"
        raise AssertionError(f"unexpected subprocess in local-only mode: {arguments}")

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
    monkeypatch.setattr(GitCliReadback, "tree_sha", lambda self, commit: "a" * 40)

    output = tmp_path / "receipt.json"
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
            "--local-verification",
            str(local_verification_path),
            "--repository",
            record.repository,
            "--output",
            str(output),
        ]
    )

    assert result == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["verification_mode"] == "local-only"
    assert receipt["tag_candidate_sha"] == ci.head_sha
    assert receipt["pytest_pass_count"] == 42
    assert "ci_run_id" not in receipt
    assert "ci_head_sha" not in receipt


def test_local_pre_tag_requires_a_local_only_canary_payload():
    record, canary, activation, admission, ci, git = _canonical_pre_tag_case()
    local = verifier.LocalVerificationReadback(
        schema="gwo-c1-local-verification.v2",
        verification_mode="local-only-v1",
        subject_sha=ci.head_sha,
        subject_tree_sha="a" * 40,
        pytest_pass_count=42,
        manifest_sha256="b" * 64,
    )

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha=ci.head_sha,
            canary=canary,
            activation=activation,
            admission=admission,
            git=git,
            local_verification=local,
        )

    assert error.value.code == "GA_LOCAL_VERIFICATION_CANARY_MODE_INVALID"


def test_local_pre_tag_rejects_hosted_fields_nested_in_a_local_canary():
    record, canary, activation, admission, ci, git = _canonical_pre_tag_case()
    canary_payload = vars(canary).copy()
    canary_payload["acceptance_mode"] = "local-only-v1"
    canary_payload["local_evidence"] = {"pull_request": {"number": 119}}
    canary_payload.pop("receipt_digest")
    canary_payload["receipt_digest"] = verifier.digest_value(canary_payload)
    canary = SimpleNamespace(**canary_payload)
    record = replace(record, canary_receipt_digest=canary.receipt_digest)
    local = verifier.LocalVerificationReadback(
        schema="gwo-c1-local-verification.v2",
        verification_mode="local-only-v1",
        subject_sha=ci.head_sha,
        subject_tree_sha="a" * 40,
        pytest_pass_count=42,
        manifest_sha256="b" * 64,
    )

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha=ci.head_sha,
            canary=canary,
            activation=activation,
            admission=admission,
            git=git,
            local_verification=local,
        )

    assert error.value.code == "GA_LOCAL_VERIFICATION_HOSTED_EVIDENCE"


def test_local_verification_reads_and_binds_a_digest_checked_pytest_log(tmp_path):
    log = tmp_path / "pytest.log"
    log_bytes = b"full: 42 passed in 1.0s\n"
    log.write_bytes(log_bytes)
    manifest = {
        "schema": "gwo-c1-local-verification.v2",
        "mode": "Local Verification Only",
        "subject_sha": "3" * 40,
        "subject_tree": "a" * 40,
        "workflow_count": 0,
        "final_outcome": "pass",
        "commands": [
            {
                "command": "py -3.13 -m pytest -q",
                "exit_code": 0,
                "log": str(log),
                "sha256": hashlib.sha256(log_bytes).hexdigest(),
            }
        ],
    }
    path = tmp_path / "local-verification.json"
    path.write_bytes(verifier.canonical_json_bytes(manifest))

    readback = verifier.load_local_verification(path)

    assert readback.verification_mode == "Local Verification Only"
    assert readback.subject_sha == "3" * 40
    assert readback.subject_tree_sha == "a" * 40
    assert readback.pytest_pass_count == 42
    assert readback.manifest_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()

    log.write_bytes(b"full: 41 passed in 1.0s\n")
    with pytest.raises(ReleaseGateError) as error:
        verifier.load_local_verification(path)
    assert error.value.code == "GA_LOCAL_VERIFICATION_LOG_MISMATCH"


def _canonical_local_verification_manifest(**overrides: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema": "gwo-c1-local-verification.v2",
        "mode": "local-only-v1",
        "subject_sha": "3" * 40,
        "subject_tree": "a" * 40,
        "workflow_count": 0,
        "actions_enabled": False,
        "final_outcome": "pass",
        "commands": [
            {
                "name": "full",
                "arguments": ["-m", "pytest", "-q"],
                "exit_code": 0,
                "status": "passed",
                "passed": 42,
                "summary": "42 passed in 1.0s",
            }
        ],
    }
    manifest.update(overrides)
    return manifest


def test_canonical_local_verification_accepts_local_only_v1_mode(tmp_path):
    path = tmp_path / "local-verification.json"
    path.write_bytes(
        verifier.canonical_json_bytes(_canonical_local_verification_manifest())
    )

    readback = verifier.load_local_verification(path)

    assert readback.verification_mode == "local-only-v1"
    assert readback.pytest_pass_count == 42


def test_canonical_local_verification_requires_zero_workflows(tmp_path):
    manifest = _canonical_local_verification_manifest()
    del manifest["workflow_count"]
    path = tmp_path / "local-verification.json"
    path.write_bytes(verifier.canonical_json_bytes(manifest))

    with pytest.raises(ReleaseGateError) as error:
        verifier.load_local_verification(path)

    assert error.value.code == "GA_LOCAL_VERIFICATION_WORKFLOW_INVALID"


def test_canonical_local_verification_requires_actions_disabled_readback(tmp_path):
    manifest = _canonical_local_verification_manifest()
    del manifest["actions_enabled"]
    path = tmp_path / "local-verification.json"
    path.write_bytes(verifier.canonical_json_bytes(manifest))

    with pytest.raises(ReleaseGateError) as error:
        verifier.load_local_verification(path)

    assert error.value.code == "GA_LOCAL_VERIFICATION_WORKFLOW_INVALID"


@pytest.mark.parametrize(
    "change",
    [{"workflow_count": 1}, {"actions_enabled": True}],
)
def test_canonical_local_verification_rejects_enabled_workflow_readback(
    tmp_path, change
):
    manifest = _canonical_local_verification_manifest(**change)
    path = tmp_path / "local-verification.json"
    path.write_bytes(verifier.canonical_json_bytes(manifest))

    with pytest.raises(ReleaseGateError) as error:
        verifier.load_local_verification(path)

    assert error.value.code == "GA_LOCAL_VERIFICATION_WORKFLOW_INVALID"


def test_canonical_local_verification_requires_full_pytest_readback(tmp_path):
    manifest = _canonical_local_verification_manifest(
        commands=None,
        pytest_pass_count=42,
    )
    path = tmp_path / "local-verification.json"
    path.write_bytes(verifier.canonical_json_bytes(manifest))

    with pytest.raises(ReleaseGateError) as error:
        verifier.load_local_verification(path)

    assert error.value.code == "GA_LOCAL_VERIFICATION_PYTEST_COUNT_MISSING"


@pytest.mark.parametrize("evidence_name", ["full_suite", "full_pytest"])
def test_canonical_local_verification_rejects_commandless_full_pytest_evidence(
    tmp_path, evidence_name
):
    manifest = _canonical_local_verification_manifest(
        commands=None,
        **{
            evidence_name: {
                "exit_code": 0,
                "status": "passed",
                "passed": 42,
                "summary": "42 passed in 1.0s",
            }
        },
    )
    path = tmp_path / "local-verification.json"
    path.write_bytes(verifier.canonical_json_bytes(manifest))

    with pytest.raises(ReleaseGateError) as error:
        verifier.load_local_verification(path)

    assert error.value.code == "GA_LOCAL_VERIFICATION_PYTEST_FAILED"


@pytest.mark.parametrize(
    "command",
    [
        {
            "name": "full",
            "arguments": ["-m", "pytest", "-k", "one", "-q"],
        },
        {
            "name": "full",
            "arguments": ["-m", "pytest", "--lf", "-q"],
        },
        {
            "name": "full",
            "arguments": ["-m", "pytest", "--ignore-glob=tests/slow/*", "-q"],
        },
        {
            "name": "full",
            "arguments": ["-m", "pytest", "--maxfail=1", "-q"],
        },
        {
            "name": "full",
            "arguments": ["-m", "pytest", "--collect-only", "-q"],
        },
        {
            "name": "full",
            "arguments": ["-m", "pytest", "--stepwise-skip", "-q"],
        },
        {
            "name": "full",
            "arguments": ["-m", "pytest", "--strict-markers", "-q"],
        },
        {
            "name": "full",
            "arguments": ["-m", "pytest", "--basetemp=tmp", "-q"],
        },
        {
            "name": "full",
            "arguments": ["-m", "pytest", "--fixtures", "-q"],
        },
        {
            "name": "package",
            "arguments": ["-m", "pytest", "-q", "tests/test_v8_release_metadata.py"],
        },
    ],
    ids=[
        "pytest-selector",
        "last-failed-selector",
        "ignore-glob-selector",
        "maxfail-selector",
        "collect-only",
        "stepwise-skip",
        "strict-markers",
        "basetemp",
        "fixtures",
        "package-only",
    ],
)
def test_canonical_local_verification_rejects_non_full_pytest_commands(
    tmp_path, command
):
    command = {
        **command,
        "exit_code": 0,
        "status": "passed",
        "passed": 1,
        "summary": "1 passed in 1.0s",
    }
    manifest = _canonical_local_verification_manifest(commands=[command])
    path = tmp_path / "local-verification.json"
    path.write_bytes(verifier.canonical_json_bytes(manifest))

    with pytest.raises(ReleaseGateError) as error:
        verifier.load_local_verification(path)

    expected_code = (
        "GA_LOCAL_VERIFICATION_PYTEST_FAILED"
        if command["name"] == "full"
        else "GA_LOCAL_VERIFICATION_PYTEST_COUNT_MISSING"
    )
    assert error.value.code == expected_code


@pytest.mark.parametrize(
    "option",
    [
        "-k=one",
        "--keyword=one",
        "-m=one",
        "--deselect=node",
        "--ignore=tests/slow",
        "--ignore-glob=tests/slow/*",
        "--pyargs",
        "--lf",
        "--last-failed",
        "--lfnf=none",
        "--last-failed-no-failures=none",
        "--ff",
        "--failed-first",
        "--nf",
        "--new-first",
        "--sw",
        "--stepwise",
        "--sw-skip",
        "--stepwise-skip",
        "--sw-reset",
        "--stepwise-reset",
        "-x",
        "--exitfirst",
        "--maxfail=1",
        "--continue-on-collection-errors",
        "--collect-only",
        "--co",
        "--fixtures",
        "--funcargs",
        "--fixtures-per-test",
        "--markers",
        "--keep-duplicates",
        "--collect-in-virtualenv",
        "--noconftest",
        "--doctest-modules",
        "--doctest-glob=*.txt",
        "--doctest-report=only_first_failure",
        "--doctest-ignore-import-errors",
        "--doctest-continue-on-failure",
        "--doctest-only",
        "-c=pytest.ini",
        "--config-file=pytest.ini",
        "--confcutdir=.",
        "--rootdir=.",
        "--basetemp=tmp",
        "--import-mode=importlib",
        "--strict-config",
        "--strict-markers",
        "--strict",
        "--override-ini=addopts=-q",
        "-o=addopts=-q",
        "--assert=plain",
        "--setup-show",
        "--testpaths=tests",
        "--trace-config",
        "--disable-plugin-autoload",
        "-p=no:plugin",
        "--pdb",
        "--pdbcls=module:Class",
        "--trace",
        "--runxfail",
        "--cache-show=*",
        "--cache-clear",
        "--help",
        "-h",
        "--version",
        "-V",
        "--debug=pytest.log",
    ],
)
def test_canonical_local_verification_rejects_remaining_pytest_gate_options(
    tmp_path, option
):
    manifest = _canonical_local_verification_manifest(
        commands=[
            {
                "name": "full",
                "arguments": ["-m", "pytest", option, "-q"],
                "exit_code": 0,
                "status": "passed",
                "passed": 42,
                "summary": "42 passed in 1.0s",
            }
        ]
    )
    path = tmp_path / "local-verification.json"
    path.write_bytes(verifier.canonical_json_bytes(manifest))

    with pytest.raises(ReleaseGateError) as error:
        verifier.load_local_verification(path)

    assert error.value.code == "GA_LOCAL_VERIFICATION_PYTEST_FAILED"


def test_canonical_local_verification_rejects_conflicting_command_representations(
    tmp_path,
):
    manifest = _canonical_local_verification_manifest(
        commands=[
            {
                "name": "full",
                "command": "py -3.13 -m pytest --lf -q",
                "arguments": ["-m", "pytest", "-q"],
                "exit_code": 0,
                "status": "passed",
                "passed": 42,
                "summary": "42 passed in 1.0s",
            }
        ]
    )
    path = tmp_path / "local-verification.json"
    path.write_bytes(verifier.canonical_json_bytes(manifest))

    with pytest.raises(ReleaseGateError) as error:
        verifier.load_local_verification(path)

    assert error.value.code == "GA_LOCAL_VERIFICATION_PYTEST_FAILED"


def test_canonical_local_verification_rejects_conflicting_full_pytest_counts(
    tmp_path,
):
    def result(count: int) -> dict[str, object]:
        return {
            "arguments": ["-m", "pytest", "-q"],
            "exit_code": 0,
            "status": "passed",
            "passed": count,
            "summary": f"{count} passed in 1.0s",
        }

    manifest = _canonical_local_verification_manifest(
        full_suite=result(40),
        full_pytest=result(41),
        commands=[{**result(42), "name": "full"}],
    )
    path = tmp_path / "local-verification.json"
    path.write_bytes(verifier.canonical_json_bytes(manifest))

    with pytest.raises(ReleaseGateError) as error:
        verifier.load_local_verification(path)

    assert error.value.code == "GA_LOCAL_VERIFICATION_PYTEST_COUNT_MISMATCH"


@pytest.mark.parametrize("missing", ["status", "passed"])
def test_canonical_local_verification_requires_full_pytest_result_fields(
    tmp_path, missing
):
    command = {
        "name": "full",
        "arguments": ["-m", "pytest", "-q"],
        "exit_code": 0,
        "status": "passed",
        "passed": 42,
        "summary": "42 passed in 1.0s",
    }
    del command[missing]
    manifest = _canonical_local_verification_manifest(commands=[command])
    path = tmp_path / "local-verification.json"
    path.write_bytes(verifier.canonical_json_bytes(manifest))

    with pytest.raises(ReleaseGateError) as error:
        verifier.load_local_verification(path)

    expected_code = (
        "GA_LOCAL_VERIFICATION_PYTEST_STATUS_INVALID"
        if missing == "status"
        else "GA_LOCAL_VERIFICATION_PYTEST_COUNT_MISSING"
    )
    assert error.value.code == expected_code


@pytest.mark.parametrize(
    "forbidden",
    [
        "hosted_ci_suite",
        "ci_run",
        "pull_request_merge_mapping",
        "publication_receipt_digest",
        "remote_target_sha",
        "HostedCISuite",
        "CIRun",
        "PullRequestMergeMapping",
        "PublicationReceiptDigest",
        "RemoteTargetSha",
        "GitHubActionsEnabled",
        "releaseURL",
        "pullrequest",
        "workflow",
        "hostedci",
        "publicationreceipt",
        "remotetarget",
        "githubactionsenabled",
        "checkrun",
        "pullrequestmergemapping",
        "releaseReceiptdigest",
        "workflowRun",
        "hostedcisuite",
        "publicationreceiptdigest",
        "remotetargetsha",
        "pullrequestheadsha",
        "pullrequestnumber",
        "checkrunid",
        "statuscheckid",
        "githubworkflowrun",
        "githubworkflowurl",
    ],
)
def test_canonical_local_verification_rejects_extended_forbidden_field_aliases(
    tmp_path, forbidden
):
    manifest = _canonical_local_verification_manifest(
        nested_evidence={"nested": [{forbidden: {"value": "blocked"}}]}
    )
    path = tmp_path / "local-verification.json"
    path.write_bytes(verifier.canonical_json_bytes(manifest))

    with pytest.raises(ReleaseGateError) as error:
        verifier.load_local_verification(path)

    assert error.value.code == "GA_LOCAL_VERIFICATION_HOSTED_EVIDENCE"


def test_canonical_local_verification_rejects_nested_pull_request_evidence(tmp_path):
    manifest = _canonical_local_verification_manifest(
        evidence={"pull_request": {"number": 119}}
    )
    path = tmp_path / "local-verification.json"
    path.write_bytes(verifier.canonical_json_bytes(manifest))

    with pytest.raises(ReleaseGateError) as error:
        verifier.load_local_verification(path)

    assert error.value.code == "GA_LOCAL_VERIFICATION_HOSTED_EVIDENCE"


def test_local_verification_subject_tree_must_match_git_readback(tmp_path):
    record, canary, activation, admission, ci, git = _canonical_pre_tag_case(
        canary_acceptance_mode="local-only-v1"
    )
    path = tmp_path / "local-verification.json"
    path.write_bytes(
        verifier.canonical_json_bytes(
            {
                "schema": "gwo-c1-local-verification.v2",
                "mode": "Local Verification Only",
                "subject_sha": ci.head_sha,
                "subject_tree": "b" * 40,
                "workflow_count": 0,
                "final_outcome": "pass",
                "pytest_pass_count": 42,
            }
        )
    )

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha=ci.head_sha,
            canary=canary,
            activation=activation,
            admission=admission,
            git=git,
            local_verification=verifier.load_local_verification(path),
        )

    assert error.value.code == "GA_LOCAL_VERIFICATION_SUBJECT_MISMATCH"


def test_local_pre_tag_receipt_round_trips_without_hosted_ci_fields():
    record, canary, activation, admission, ci, git = _canonical_pre_tag_case(
        canary_acceptance_mode="local-only-v1"
    )
    local = verifier.LocalVerificationReadback(
        schema="gwo-c1-local-verification.v2",
        verification_mode="local-only",
        subject_sha=ci.head_sha,
        subject_tree_sha="a" * 40,
        pytest_pass_count=42,
        manifest_sha256="b" * 64,
    )
    receipt = verify_pre_tag(
        record,
        main_sha=ci.head_sha,
        canary=canary,
        activation=activation,
        admission=admission,
        git=git,
        local_verification=local,
    )

    payload = receipt.to_mapping()
    reloaded = verifier.ReleaseGateReceipt.from_mapping(payload)

    assert payload["verification_mode"] == "local-only"
    assert payload["local_verification_manifest_sha256"] == "b" * 64
    assert "ci_run_id" not in payload
    assert "ci_head_sha" not in payload
    assert reloaded.verification_mode == "local-only"
    assert reloaded.ci_run_id is None
    assert reloaded.ci_head_sha is None


def test_canonical_local_pre_tag_receipt_preserves_v1_mode():
    record, canary, activation, admission, ci, git = _canonical_pre_tag_case(
        canary_acceptance_mode="local-only-v1"
    )
    local = verifier.LocalVerificationReadback(
        schema="gwo-c1-local-verification.v2",
        verification_mode="local-only-v1",
        subject_sha=ci.head_sha,
        subject_tree_sha="a" * 40,
        pytest_pass_count=42,
        manifest_sha256="b" * 64,
    )

    receipt = verify_pre_tag(
        record,
        main_sha=ci.head_sha,
        canary=canary,
        activation=activation,
        admission=admission,
        git=git,
        local_verification=local,
    )

    payload = receipt.to_mapping()
    reloaded = verifier.ReleaseGateReceipt.from_mapping(payload)

    assert payload["verification_mode"] == "local-only-v1"
    assert reloaded.verification_mode == "local-only-v1"


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


def test_renderer_accepts_canonical_ga_evidence_bridge_readback(tmp_path):
    bridge = {
        "bridge_digest": "30962c93b38ae16eaaa5dd0fdb805fdd22fa4108fd6374e37abafad6cfb2dea7",
        "default_writer": {
            "activation_id": "activation:47895d07122a3d9827ecdf63",
            "legacy_writer_fence_stopped": True,
            "previous_writer_generation": "v8-generation-1",
            "readback_receipt_digest": "42b595a7d4a93146200e2eaab629d804f1c0b9e383e7c7233af495e89a0c3084",
            "record_id": "writer-transition:ce14291c00b0c5bfe7251729",
            "source_file": (
                "D:\\gwo-release-evidence\\2026-08-19-gwo-v8-ga-production-cutover"
                "\\default-writer-readback.json"
            ),
            "source_file_sha256": "4c9f165b7e1df535cfcd3fe86cc43b2fb57dc21872cb65980b3d9abdec3d4ffc",
            "writer_generation": "v8-generation-1",
        },
        "local_root_canary": {
            "acceptance_mode": "local-only-v1",
            "activation_id": "campaign:fd16e735a23425ee5071e881",
            "campaign_key": "campaign:fd16e735a23425ee5071e881",
            "canary_target_sha": "d31d5787df8ff53f081ed45df42389ef2e505ffb",
            "producer_receipt_digest": "ea642b5606efc10adaf3671174b10e3df2f1a5f2dfc8b60a86b251db5845c938",
            "repository": "NOirBRight/github-work-orchestrator",
            "schema": "gwo-v8-root-canary-acceptance.v2",
            "source_file": (
                "D:\\gwo-release-evidence\\2026-08-19-gwo-v8-ga-production-cutover"
                "\\root-canary-acceptance.json"
            ),
            "source_file_sha256": "2e1d740729c22f60718097ab5bf3c6e3e404d54948154707a46a2dc38fb51c5f",
            "writer_generation": "writer:local",
        },
        "production_activation": {
            "activation_id": "activation:47895d07122a3d9827ecdf63",
            "previous_writer_generation": "v8-generation-1",
            "readback_receipt_digest": "98eb2d5f6a75f0e12b290836c72939c44bd03052f1d28257cae410ed30d25c06",
            "run_id": "phase5-production-activation-2df47f9",
            "source_file": (
                "D:\\gwo-release-evidence\\2026-08-19-gwo-v8-ga-production-cutover"
                "\\production-activation-readback.json"
            ),
            "source_file_sha256": "848536847b2fa47f3b10bb38d419234d94d81139d119528d4ff7575a78733319",
            "transition_record_id": "writer-transition:ce14291c00b0c5bfe7251729",
            "writer_generation": "v8-generation-1",
        },
        "production_canary": {
            "evidence_ref_count": 19,
            "manifest_ref": (
                "github://canary-manifest/"
                "2533a3e5f22cc0c5e8bf2e7cd7114f33f2895d394da3f0ab69a9742205069f30"
            ),
            "package_digest": "2533a3e5f22cc0c5e8bf2e7cd7114f33f2895d394da3f0ab69a9742205069f30",
            "package_repository": "NOirBRight/gwo-v8-canary",
            "readback_receipt_digest": "84e4b4e904679d2f841f843ca58da9dda0e5a81a47d251bc18cdd396c64c710e",
            "source_file": (
                "D:\\gwo-release-evidence\\2026-08-19-gwo-v8-ga-production-cutover"
                "\\production-canary-readback.json"
            ),
            "source_file_sha256": "354092b2e186096e7f7693683f1ad7d449b4ffe13ff56eca6254b4f83e77baca",
        },
        "activation_release_subject": {
            "merged_main_sha": "f81994db1bee226cd6ca429e79c9b1cdf6d02897",
            "merged_main_tree": "5c97df0ecd0a267f69e80de92d4325f3a6f86743",
            "release_subject_digest": "f4a260c1bfb39d6541c33f8f8f4449edc5453bd94cedbc1f1a244c9daf28a969",
        },
        "release_subject": {
            "merged_main_sha": "f81994db1bee226cd6ca429e79c9b1cdf6d02897",
            "merged_main_tree": "5c97df0ecd0a267f69e80de92d4325f3a6f86743",
            "release_subject_digest": "f4a260c1bfb39d6541c33f8f8f4449edc5453bd94cedbc1f1a244c9daf28a969",
        },
        "repository": "NOirBRight/github-work-orchestrator",
        "schema": "gwo-v8-ga-evidence-bridge.v1",
    }

    paths = render_ga_documents(
        tmp_path,
        evidence_base_sha="4" * 40,
        tickets={"tickets": [{"number": 1}]},
        evidence_bridge=bridge,
    )

    payload = json.loads(
        paths[1]
        .read_text(encoding="utf-8")
        .split("```json\n", 1)[1]
        .split("\n```\n", 1)[0]
    )

    assert payload["evidence_bridge_digest"] == _stable_bridge_digest(bridge)
    assert (
        payload["evidence_bridge_activation_subject"]
        == bridge["activation_release_subject"]
    )
    assert "evidence_bridge" not in payload
    assert payload["evidence_bridge_links"] == {
        "activation_id": "activation:47895d07122a3d9827ecdf63",
        "default_writer_readback_receipt_digest": "42b595a7d4a93146200e2eaab629d804f1c0b9e383e7c7233af495e89a0c3084",
        "local_root_canary_receipt_digest": "ea642b5606efc10adaf3671174b10e3df2f1a5f2dfc8b60a86b251db5845c938",
        "production_activation_readback_receipt_digest": "98eb2d5f6a75f0e12b290836c72939c44bd03052f1d28257cae410ed30d25c06",
        "production_canary_package_digest": "2533a3e5f22cc0c5e8bf2e7cd7114f33f2895d394da3f0ab69a9742205069f30",
        "production_canary_repository": "NOirBRight/gwo-v8-canary",
        "transition_record_id": "writer-transition:ce14291c00b0c5bfe7251729",
        "writer_generation": "v8-generation-1",
    }

    with pytest.raises(ReleaseGateError) as error:
        render_ga_documents(
            tmp_path / "conflicting-inputs",
            evidence_base_sha="4" * 40,
            tickets={"tickets": [{"number": 1}]},
            evidence_bridge=bridge,
            acceptance={
                "repository": "NOirBRight/github-work-orchestrator",
                "campaign_key": "campaign:foreign",
                "canary_target_sha": "6" * 40,
                "receipt_digest": "foreign-canary",
            },
            named_admission={"receipt_digest": "foreign-activation"},
            default_writer={
                "receipt_digest": "foreign-default",
                "activation_id": "activation:foreign",
                "writer_generation": "v8-generation-1",
            },
        )

    assert error.value.code == "GA_METADATA_BRIDGE_IDENTITY_MISMATCH"


def _rehash_metadata_bridge(bridge):
    body = {key: value for key, value in bridge.items() if key != "bridge_digest"}
    return body | {"bridge_digest": digest_value(body)}


def _stable_bridge_digest(bridge):
    body = {
        key: value
        for key, value in bridge.items()
        if key not in {"bridge_digest", "release_subject"}
    }
    return digest_value(body)


def _write_renderer_bridge_source(tmp_path, filename, payload):
    path = tmp_path / filename
    raw = verifier.canonical_json_bytes(payload)
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def _renderer_bridge_fixture(tmp_path):
    repository = "NOirBRight/github-work-orchestrator"
    activation_id = "activation:test"
    writer_generation = "v8-generation-1"
    previous_writer_generation = "v8-generation-1"
    record_id = "writer-transition:test"
    run_id = "phase5-production-activation-test"
    plan_digest = "a" * 64
    merged_main_sha = "b" * 40
    merged_main_tree = "c" * 40
    release_subject_digest = "d" * 64
    activation_receipt_digest = "e" * 64
    default_receipt_digest = "f" * 64
    local_receipt_digest = "1" * 64
    canary_package_digest = "2" * 64
    canary_receipt_digest = "3" * 64
    subject = {
        "merged_main_sha": merged_main_sha,
        "merged_main_tree": merged_main_tree,
        "release_subject_digest": release_subject_digest,
    }
    control_ref = {
        "branch": "gwo-control",
        "commit_sha": "4" * 40,
        "tree_sha": "5" * 40,
    }
    local_payload = {
        "acceptance_mode": "local-only-v1",
        "activation_id": "campaign:test",
        "campaign_key": "campaign:test",
        "canary_target_sha": "6" * 40,
        "receipt_digest": local_receipt_digest,
        "repository": repository,
        "schema": "gwo-v8-root-canary-acceptance.v2",
        "writer_generation": "writer:local",
    }
    canary_payload = {
        "accepted": True,
        "all_evidence_exact": True,
        "blockers": [],
        "coverage": [],
        "evidence_readback_count": 19,
        "evidence_ref_count": 19,
        "evidence_refs": [],
        "manifest_branch": "gwo-control",
        "manifest_ref": f"github://canary-manifest/{canary_package_digest}",
        "manifest_repository": "NOirBRight/gwo-v8-canary",
        "manifest_sha256": canary_package_digest,
        "node_keys": [],
        "package_digest": canary_package_digest,
        "package_repository": "NOirBRight/gwo-v8-canary",
        "readback_verification_file_sha256": "7" * 64,
        "readback_verification_schema": "gwo-v8-canary-readback-verification.v1",
        "receipt_digest": canary_receipt_digest,
        "repository": "NOirBRight/gwo-v8-canary",
        "schema": "gwo-v8-production-canary-readback.v1",
    }
    activation_payload = {
        "active_plan": {
            "active_plan_digest": plan_digest,
            "latest_activation_id": activation_id,
            "latest_plan_digest": plan_digest,
            "latest_plan_record_ref": "github://plan/test",
        },
        "authorization": {
            "canary_repository": "NOirBRight/gwo-v8-canary",
            "evidence_root": "D:\\evidence",
            "merged_main_git_tree": merged_main_tree,
            "merged_main_sha": merged_main_sha,
            "release_subject_digest": release_subject_digest,
            "repository": repository,
            "run_id": run_id,
            "target_repository": repository,
            "target_writer_generation": writer_generation,
            "writer_transition": "v6.1 -> v8",
        },
        "control_ref": control_ref,
        "execute_outcome": {
            "activation_id": activation_id,
            "record_id": record_id,
            "repository": repository,
            "status": "cut_over",
            "writer_generation": writer_generation,
        },
        "guard_receipt": {
            "source_writer_generation": "v6.1",
            "target_writer_generation": writer_generation,
        },
        "legacy_writer_fence": {"stopped": True},
        "receipt_digest": activation_receipt_digest,
        "release_subject": subject,
        "repository": repository,
        "schema": "gwo-v8-production-activation-readback.v1",
        "transition_current": {
            "record_id": record_id,
            "repository": repository,
            "writer_generation": writer_generation,
        },
        "transition_record": {
            "activation_id": activation_id,
            "canary_evidence_digest": canary_package_digest,
            "canary_evidence_refs": [],
            "canary_manifest_ref": canary_payload["manifest_ref"],
            "canary_repository": canary_payload["package_repository"],
            "plan_digest": plan_digest,
            "previous_writer_generation": previous_writer_generation,
            "record_id": record_id,
            "repository": repository,
            "status": "cut_over",
            "writer_generation": writer_generation,
        },
    }
    default_payload = {
        "activation_id": activation_id,
        "activation_readback_digest": activation_receipt_digest,
        "campaign_key": None,
        "control_ref": control_ref,
        "legacy_writer_fence_stopped": True,
        "mode": "default_v8",
        "plan_digest": plan_digest,
        "previous_writer_generation": previous_writer_generation,
        "receipt_digest": default_receipt_digest,
        "record_id": record_id,
        "repository": repository,
        "schema": "gwo-v8-default-writer-readback.v1",
        "status": "cut_over",
        "writer_generation": writer_generation,
    }

    sources = {}
    source_payloads = {
        "local_root_canary": ("root-canary-acceptance.json", local_payload),
        "production_canary": ("production-canary-readback.json", canary_payload),
        "production_activation": (
            "production-activation-readback.json",
            activation_payload,
        ),
        "default_writer": ("default-writer-readback.json", default_payload),
    }
    for section, (filename, payload) in source_payloads.items():
        sources[section] = _write_renderer_bridge_source(tmp_path, filename, payload)

    bridge = {
        "default_writer": {
            "activation_id": activation_id,
            "legacy_writer_fence_stopped": True,
            "previous_writer_generation": previous_writer_generation,
            "readback_receipt_digest": default_receipt_digest,
            "record_id": record_id,
            "source_file": str(sources["default_writer"][0]),
            "source_file_sha256": sources["default_writer"][1],
            "writer_generation": writer_generation,
        },
        "activation_release_subject": subject,
        "local_root_canary": {
            "acceptance_mode": local_payload["acceptance_mode"],
            "activation_id": local_payload["activation_id"],
            "campaign_key": local_payload["campaign_key"],
            "canary_target_sha": local_payload["canary_target_sha"],
            "producer_receipt_digest": local_receipt_digest,
            "repository": repository,
            "schema": local_payload["schema"],
            "source_file": str(sources["local_root_canary"][0]),
            "source_file_sha256": sources["local_root_canary"][1],
            "writer_generation": local_payload["writer_generation"],
        },
        "production_activation": {
            "activation_id": activation_id,
            "previous_writer_generation": previous_writer_generation,
            "readback_receipt_digest": activation_receipt_digest,
            "run_id": run_id,
            "source_file": str(sources["production_activation"][0]),
            "source_file_sha256": sources["production_activation"][1],
            "transition_record_id": record_id,
            "writer_generation": writer_generation,
        },
        "production_canary": {
            "evidence_ref_count": canary_payload["evidence_ref_count"],
            "manifest_ref": canary_payload["manifest_ref"],
            "package_digest": canary_package_digest,
            "package_repository": canary_payload["package_repository"],
            "readback_receipt_digest": canary_receipt_digest,
            "source_file": str(sources["production_canary"][0]),
            "source_file_sha256": sources["production_canary"][1],
        },
        "release_subject": subject,
        "repository": repository,
        "schema": "gwo-v8-ga-evidence-bridge.v1",
    }
    return _rehash_metadata_bridge(bridge), source_payloads


def _renderer_bridge_with_source_payload(
    tmp_path, bridge, section, payload, *, filename=None, raw=None
):
    if filename is None:
        filename = {
            "local_root_canary": "root-canary-acceptance.json",
            "production_canary": "production-canary-readback.json",
            "production_activation": "production-activation-readback.json",
            "default_writer": "default-writer-readback.json",
        }[section]
    path = tmp_path / filename
    if raw is None:
        raw = verifier.canonical_json_bytes(payload)
    path.write_bytes(raw)
    updated = json.loads(json.dumps(bridge))
    updated[section]["source_file"] = str(path)
    updated[section]["source_file_sha256"] = hashlib.sha256(raw).hexdigest()
    return _rehash_metadata_bridge(updated)


def _render_with_bridge(tmp_path, bridge):
    return render_ga_documents(
        tmp_path / "rendered",
        evidence_base_sha="8" * 40,
        tickets={"tickets": [{"number": 1}]},
        evidence_bridge=bridge,
    )


@pytest.mark.parametrize(
    "change",
    [
        "transition_previous_writer_generation",
        "guard_source_writer_generation",
        "schema",
        "repository",
        "receipt_digest",
        "activation_id",
        "record_id",
    ],
)
def test_renderer_rejects_activation_source_binding_drift(tmp_path, change):
    bridge, source_payloads = _renderer_bridge_fixture(tmp_path)
    payload = json.loads(json.dumps(source_payloads["production_activation"][1]))
    if change == "transition_previous_writer_generation":
        payload["transition_record"]["previous_writer_generation"] = "v6.1"
    elif change == "guard_source_writer_generation":
        payload["guard_receipt"]["source_writer_generation"] = "v8-generation-1"
    elif change == "schema":
        payload["schema"] = "foreign.activation.v1"
    elif change == "repository":
        payload["repository"] = "foreign/repository"
    elif change == "receipt_digest":
        payload["receipt_digest"] = "9" * 64
    elif change == "activation_id":
        payload["execute_outcome"]["activation_id"] = "activation:foreign"
    else:
        payload["transition_record"]["record_id"] = "writer-transition:foreign"
    bridge = _renderer_bridge_with_source_payload(
        tmp_path,
        bridge,
        "production_activation",
        payload,
    )

    with pytest.raises(ReleaseGateError):
        _render_with_bridge(tmp_path, bridge)


@pytest.mark.parametrize(
    "field",
    [
        "schema",
        "repository",
        "status",
        "mode",
        "writer_generation",
        "activation_id",
        "record_id",
        "plan_digest",
        "previous_writer_generation",
        "receipt_digest",
    ],
)
def test_renderer_rejects_default_writer_source_binding_drift(tmp_path, field):
    bridge, source_payloads = _renderer_bridge_fixture(tmp_path)
    payload = json.loads(json.dumps(source_payloads["default_writer"][1]))
    payload[field] = {
        "schema": "foreign.default.v1",
        "repository": "foreign/repository",
        "status": "pending",
        "mode": "legacy",
        "writer_generation": "v6.1",
        "activation_id": "activation:foreign",
        "record_id": "writer-transition:foreign",
        "plan_digest": "9" * 64,
        "previous_writer_generation": "v6.1",
        "receipt_digest": "9" * 64,
    }[field]
    bridge = _renderer_bridge_with_source_payload(
        tmp_path,
        bridge,
        "default_writer",
        payload,
    )

    with pytest.raises(ReleaseGateError):
        _render_with_bridge(tmp_path, bridge)


@pytest.mark.parametrize(
    ("section", "filename"),
    [
        ("production_activation", "not-the-activation-file.txt"),
        ("default_writer", "not-the-default-writer-file.txt"),
        ("local_root_canary", "not-the-root-canary-file.txt"),
        ("production_canary", "not-the-production-canary-file.txt"),
    ],
)
def test_renderer_rejects_role_mismatched_bridge_source_basename(
    tmp_path, section, filename
):
    bridge, source_payloads = _renderer_bridge_fixture(tmp_path)
    bridge = _renderer_bridge_with_source_payload(
        tmp_path,
        bridge,
        section,
        source_payloads[section][1],
        filename=filename,
    )

    with pytest.raises(ReleaseGateError):
        _render_with_bridge(tmp_path, bridge)


@pytest.mark.parametrize(
    "section", ("default_writer", "local_root_canary", "production_canary")
)
def test_renderer_rejects_noncanonical_bridge_source(tmp_path, section):
    bridge, source_payloads = _renderer_bridge_fixture(tmp_path)
    payload = source_payloads[section][1]
    raw = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    bridge = _renderer_bridge_with_source_payload(
        tmp_path,
        bridge,
        section,
        payload,
        raw=raw,
    )

    with pytest.raises(ReleaseGateError):
        _render_with_bridge(tmp_path, bridge)


@pytest.mark.parametrize(
    "section", ("default_writer", "local_root_canary", "production_canary")
)
def test_renderer_rejects_bridge_source_hash_mismatch(tmp_path, section):
    bridge, source_payloads = _renderer_bridge_fixture(tmp_path)
    bridge = json.loads(json.dumps(bridge))
    path, _digest = _write_renderer_bridge_source(
        tmp_path,
        {
            "local_root_canary": "root-canary-acceptance.json",
            "production_canary": "production-canary-readback.json",
            "default_writer": "default-writer-readback.json",
        }[section],
        source_payloads[section][1],
    )
    bridge[section]["source_file"] = str(path)
    bridge[section]["source_file_sha256"] = "0" * 64
    bridge = _rehash_metadata_bridge(bridge)

    with pytest.raises(ReleaseGateError):
        _render_with_bridge(tmp_path, bridge)


@pytest.mark.parametrize("section", ("local_root_canary", "production_canary"))
def test_renderer_binds_non_activation_bridge_source_projection(tmp_path, section):
    bridge, source_payloads = _renderer_bridge_fixture(tmp_path)
    payload = json.loads(json.dumps(source_payloads[section][1]))
    if section == "local_root_canary":
        payload["campaign_key"] = "campaign:foreign"
    else:
        payload["package_digest"] = "0" * 64
    bridge = _renderer_bridge_with_source_payload(tmp_path, bridge, section, payload)

    with pytest.raises(ReleaseGateError):
        _render_with_bridge(tmp_path, bridge)


def test_renderer_rejects_reparse_bridge_source(tmp_path):
    bridge, source_payloads = _renderer_bridge_fixture(tmp_path)
    target = tmp_path / "real-production-activation.json"
    raw = verifier.canonical_json_bytes(source_payloads["production_activation"][1])
    target.write_bytes(raw)
    link_dir = tmp_path / "linked-source"
    link_dir.mkdir()
    link = link_dir / "production-activation-readback.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"source symlinks are unavailable: {error}")
    bridge = json.loads(json.dumps(bridge))
    bridge["production_activation"]["source_file"] = str(link)
    bridge["production_activation"]["source_file_sha256"] = hashlib.sha256(
        raw
    ).hexdigest()
    bridge = _rehash_metadata_bridge(bridge)

    with pytest.raises(ReleaseGateError):
        _render_with_bridge(tmp_path, bridge)


def test_renderer_accepts_bridge_derived_inputs_when_explicitly_repeated(tmp_path):
    bridge = _load_renderer_bridge()
    context = renderer._renderer_evidence_bridge_context(bridge)

    render_ga_documents(
        tmp_path,
        evidence_base_sha="4" * 40,
        tickets={"tickets": [{"number": 1}]},
        acceptance=context["acceptance"],
        named_admission=context["named_admission"],
        default_writer=context["default_writer"],
        evidence_bridge=bridge,
    )


def _load_renderer_bridge():
    path = Path(
        r"D:\gwo-release-evidence\2026-08-19-gwo-v8-ga-production-cutover\ga-evidence-bridge.json"
    )
    if not path.exists():
        pytest.skip(f"real derived evidence is required: {path}")
    bridge = json.loads(path.read_text(encoding="utf-8"))
    if (
        "activation_release_subject" not in bridge
        or "previous_writer_generation" not in bridge.get("default_writer", {})
    ):
        pytest.skip("real bridge predates the renderer bridge contract")
    return bridge


def test_renderer_rejects_bridge_activation_lineage_drift(tmp_path):
    bridge = _load_renderer_bridge()
    bridge["production_activation"] = {
        **bridge["production_activation"],
        "previous_writer_generation": "v6.1",
    }
    bridge = _rehash_metadata_bridge(bridge)

    with pytest.raises(ReleaseGateError) as error:
        render_ga_documents(
            tmp_path,
            evidence_base_sha="4" * 40,
            tickets={"tickets": [{"number": 1}]},
            evidence_bridge=bridge,
        )

    assert error.value.code == "GA_METADATA_BRIDGE_WRITER_GENERATION_INVALID"


def test_renderer_rejects_activation_subject_not_authorized_by_readback(tmp_path):
    bridge = _load_renderer_bridge()
    bridge["activation_release_subject"]["merged_main_sha"] = "0" * 40
    bridge = _rehash_metadata_bridge(bridge)

    with pytest.raises(ReleaseGateError) as error:
        render_ga_documents(
            tmp_path,
            evidence_base_sha="4" * 40,
            tickets={"tickets": [{"number": 1}]},
            evidence_bridge=bridge,
        )

    assert error.value.code == "GA_METADATA_BRIDGE_IDENTITY_MISMATCH"


def test_renderer_rejects_activation_readback_source_digest_mismatch(tmp_path):
    bridge = _load_renderer_bridge()
    bridge["production_activation"] = {
        **bridge["production_activation"],
        "source_file_sha256": "0" * 64,
    }
    bridge = _rehash_metadata_bridge(bridge)

    with pytest.raises(ReleaseGateError) as error:
        render_ga_documents(
            tmp_path,
            evidence_base_sha="4" * 40,
            tickets={"tickets": [{"number": 1}]},
            evidence_bridge=bridge,
        )

    assert error.value.code == "GA_METADATA_BRIDGE_DIGEST_MISMATCH"


def test_renderer_preserves_activation_subject_when_final_subject_moves(tmp_path):
    bridge = _load_renderer_bridge()
    activation_subject = dict(bridge["activation_release_subject"])
    stable_bridge_digest = _stable_bridge_digest(bridge)
    bridge["release_subject"] = {
        "merged_main_sha": "a" * 40,
        "merged_main_tree": "b" * 40,
        "release_subject_digest": "c" * 64,
    }
    bridge = _rehash_metadata_bridge(bridge)

    paths = render_ga_documents(
        tmp_path,
        evidence_base_sha="4" * 40,
        tickets={"tickets": [{"number": 1}]},
        evidence_bridge=bridge,
    )
    payload = json.loads(
        paths[1]
        .read_text(encoding="utf-8")
        .split("```json\n", 1)[1]
        .split("\n```\n", 1)[0]
    )

    assert payload["evidence_bridge_activation_subject"] == activation_subject
    assert payload["evidence_bridge_digest"] == stable_bridge_digest
    assert "evidence_bridge" not in payload
    serialized = json.dumps(payload, sort_keys=True)
    assert "a" * 40 not in serialized
    assert "b" * 40 not in serialized
    assert "c" * 64 not in serialized


def test_renderer_labels_repository_verification_local_only(tmp_path):
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

    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert "local-only-v1" in combined
    assert "exact CI" not in combined
    assert "Product Hosted-CI" in combined


@pytest.mark.parametrize("forbidden", ["hosted_ci", "pull_request"])
def test_renderer_rejects_hosted_or_pr_acceptance_evidence(tmp_path, forbidden):
    acceptance = {
        "repository": "NOirBRight/github-work-orchestrator",
        "campaign_key": "campaign:root",
        "canary_target_sha": "5" * 40,
        "receipt_digest": "canary:1",
        forbidden: {"status": "success"},
    }

    with pytest.raises(ReleaseGateError) as error:
        render_ga_documents(
            tmp_path,
            evidence_base_sha="4" * 40,
            tickets={"tickets": [{"number": 1}]},
            acceptance=acceptance,
            named_admission={"receipt_digest": "named:1"},
            default_writer={
                "receipt_digest": "default:1",
                "activation_id": "activation:1",
                "writer_generation": "v8",
            },
        )

    assert error.value.code == "GA_LOCAL_VERIFICATION_HOSTED_EVIDENCE"


@pytest.mark.parametrize("forbidden", ["hostedci", "pullrequest"])
def test_live_release_record_rejects_hosted_or_pr_acceptance_evidence(
    tmp_path, forbidden
):
    with pytest.raises(ReleaseGateError) as error:
        renderer.write_live_release_record(
            tmp_path / "release-record.json",
            evidence_base_sha="4" * 40,
            acceptance={
                "repository": "NOirBRight/github-work-orchestrator",
                "campaign_key": "campaign:root",
                "canary_target_sha": "5" * 40,
                "receipt_digest": "canary:1",
                forbidden: {"status": "success"},
            },
            named_admission={"receipt_digest": "named:1"},
            default_writer={
                "receipt_digest": "default:1",
                "activation_id": "activation:1",
                "writer_generation": "v8",
            },
        )

    assert error.value.code == "GA_LOCAL_VERIFICATION_HOSTED_EVIDENCE"


@pytest.mark.parametrize(
    "dynamic_payload",
    [
        {"nested": {"tag_candidate_sha": "6" * 40}},
        {"ciEnvelope": {"headSha": "7" * 40}},
        {"nested": {"commitHash": "8" * 40}},
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


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_renderer_rejects_nonstandard_json_numbers(tmp_path, value):
    with pytest.raises(ReleaseGateError) as error:
        render_ga_documents(
            tmp_path,
            evidence_base_sha="4" * 40,
            tickets={"tickets": [{"metric": value}]},
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

    assert error.value.code == "GA_METADATA_INPUT_INVALID"


def test_renderer_cross_binds_named_admission_identity(tmp_path):
    with pytest.raises(ReleaseGateError) as error:
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
            named_admission={
                "repository": "foreign/repository",
                "receipt_digest": "named:1",
            },
            default_writer={
                "receipt_digest": "default:1",
                "activation_id": "activation:1",
                "writer_generation": "v8",
            },
        )

    assert error.value.code == "GA_METADATA_IDENTITY_MISMATCH"


def test_renderer_syncs_staged_documents_before_publication(tmp_path, monkeypatch):
    fsync_calls = []
    monkeypatch.setattr(
        renderer.os, "fsync", lambda descriptor: fsync_calls.append(descriptor)
    )

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

    assert fsync_calls


def test_post_release_rejects_pre_tag_receipt_not_bound_to_static_record(
    tmp_path, monkeypatch
):
    record_path = write_ga_release_record(
        tmp_path / "record.json", CompleteReleaseFixture()
    )
    pre_tag = tmp_path / "pre-tag.json"
    pre_tag.write_bytes(
        verifier.canonical_json_bytes(
            _post_release_receipt_payload(evidence_base_sha="9" * 40)
        )
    )

    class FakeGit:
        repository = "NOirBRight/github-work-orchestrator"

        def __init__(self, repository, checkout=None):
            assert repository == self.repository
            del checkout

        def tag_subject(self, tag):
            assert tag == "v8.0.0"
            return "3" * 40, "4" * 40

        def archive_tag(self, subject):
            assert subject == "v8.0.0"
            return _empty_tar_bytes()

    monkeypatch.setattr(verifier, "GitCliReadback", FakeGit)
    monkeypatch.setattr(
        verifier,
        "clean_install_and_smoke",
        lambda source, run_root: verifier.CleanInstallResult(
            (".agents", ".codex", ".claude"),
            ("advance", "inspect", "start"),
            False,
        ),
    )

    output = tmp_path / "result.json"
    result = verify_main(
        [
            "--post-release",
            "--tag",
            "v8.0.0",
            "--record",
            str(record_path),
            "--pre-tag-receipt",
            str(pre_tag),
            "--run-root",
            str(tmp_path / "run"),
            "--output",
            str(output),
        ]
    )

    assert result == 2
    assert not output.exists()


def test_post_release_rechecks_pre_tag_commit_tree_invariants_before_archive(
    tmp_path, monkeypatch
):
    record_path = write_ga_release_record(
        tmp_path / "record.json", CompleteReleaseFixture()
    )
    pre_tag = tmp_path / "pre-tag.json"
    pre_tag.write_bytes(verifier.canonical_json_bytes(_post_release_receipt_payload()))

    class FakeGit:
        repository = "NOirBRight/github-work-orchestrator"

        def __init__(self, repository, checkout=None):
            assert repository == self.repository
            del checkout

        def tag_subject(self, tag):
            assert tag == "v8.0.0"
            return "3" * 40, "4" * 40

        def tree_sha(self, commit):
            assert commit == "3" * 40
            return "9" * 40

        def archive_tag(self, subject):
            raise AssertionError("pre-tag tree mismatch must fail before archive")

    monkeypatch.setattr(verifier, "GitCliReadback", FakeGit)

    output = tmp_path / "result.json"
    result = verify_main(
        [
            "--post-release",
            "--tag",
            "v8.0.0",
            "--record",
            str(record_path),
            "--pre-tag-receipt",
            str(pre_tag),
            "--run-root",
            str(tmp_path / "run"),
            "--output",
            str(output),
        ]
    )

    assert result == 2
    assert not output.exists()


def test_post_release_archives_tag_subject_by_immutable_commit_sha(
    tmp_path, monkeypatch
):
    record_path = write_ga_release_record(
        tmp_path / "record.json", CompleteReleaseFixture()
    )
    pre_tag = tmp_path / "pre-tag.json"
    pre_tag.write_bytes(verifier.canonical_json_bytes(_post_release_receipt_payload()))
    archived_subjects: list[str] = []

    class FakeGit:
        repository = "NOirBRight/github-work-orchestrator"

        def __init__(self, repository, checkout=None):
            assert repository == self.repository
            del checkout

        def tag_subject(self, tag):
            assert tag == "v8.0.0"
            return "3" * 40, "4" * 40

        def tree_sha(self, commit):
            assert commit == "3" * 40
            return "4" * 40

        def is_ancestor(self, ancestor, descendant):
            assert ancestor in {"1" * 40, "2" * 40}
            assert descendant == "3" * 40
            return True

        def changed_paths(self, ancestor, descendant):
            assert ancestor == "1" * 40
            assert descendant == "3" * 40
            return (
                "CHANGELOG.md",
                "docs/e2e/gwo-v8-root-canary.md",
                "docs/releases/v8.0.0.md",
            )

        def archive_tag(self, subject):
            archived_subjects.append(subject)
            return _empty_tar_bytes()

    monkeypatch.setattr(verifier, "GitCliReadback", FakeGit)
    monkeypatch.setattr(
        verifier,
        "clean_install_and_smoke",
        lambda source, run_root: verifier.CleanInstallResult(
            (".agents", ".codex", ".claude"),
            ("advance", "inspect", "start"),
            False,
        ),
    )

    output = tmp_path / "result.json"
    result = verify_main(
        [
            "--post-release",
            "--tag",
            "v8.0.0",
            "--record",
            str(record_path),
            "--pre-tag-receipt",
            str(pre_tag),
            "--run-root",
            str(tmp_path / "run"),
            "--output",
            str(output),
        ]
    )

    assert result == 0
    assert archived_subjects == ["3" * 40]


def test_post_release_rejects_tag_tree_different_from_pre_tag_subject(
    tmp_path, monkeypatch
):
    repository = "NOirBRight/github-work-orchestrator"
    pre_tag = tmp_path / "pre-tag.json"
    pre_tag.write_bytes(
        verifier.canonical_json_bytes(
            {
                "version": "8.0.0",
                "repository": repository,
                "evidence_base_sha": "1" * 40,
                "canary_target_sha": "2" * 40,
                "tag_candidate_sha": "3" * 40,
                "tag_candidate_tree_sha": "4" * 40,
                "ci_run_id": 987,
                "ci_head_sha": "3" * 40,
                "pytest_pass_count": 42,
                "canary_receipt_digest": "canary:1",
                "activation_receipt_digest": "activation:1",
                "default_writer_receipt_digest": "default:1",
                "campaign_key": "campaign:root",
                "activation_id": "activation:1",
                "writer_generation": "v8",
            }
        )
    )
    record_path = write_ga_release_record(
        tmp_path / "record.json", CompleteReleaseFixture()
    )
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w"):
        pass

    def fake_check_output(arguments, *, text=True, cwd=None):
        if arguments == ("git", "remote", "get-url", "origin"):
            return "https://github.com/NOirBRight/github-work-orchestrator.git\n"
        if arguments == ("git", "rev-parse", "--verify", "refs/tags/v8.0.0^{commit}"):
            return "3" * 40 + "\n"
        if arguments == ("git", "rev-parse", "--verify", "refs/tags/v8.0.0^{tree}"):
            return "5" * 40 + "\n"
        if arguments[:2] == ("git", "archive"):
            return archive_buffer.getvalue()
        raise AssertionError(arguments)

    monkeypatch.setattr(verifier.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(
        verifier,
        "clean_install_and_smoke",
        lambda source, run_root: (_ for _ in ()).throw(
            AssertionError("tag mismatch must fail before installation")
        ),
    )

    result = verify_main(
        [
            "--post-release",
            "--tag",
            "v8.0.0",
            "--record",
            str(record_path),
            "--pre-tag-receipt",
            str(pre_tag),
            "--run-root",
            str(tmp_path / "run"),
            "--output",
            str(tmp_path / "result.json"),
        ]
    )

    assert result == 2
    assert not (tmp_path / "result.json").exists()


def test_renderer_rejects_symlinked_output_target_before_backup_or_replace(tmp_path):
    output_root = tmp_path / "output"
    output_root.mkdir()
    outside = tmp_path / "outside-changelog.md"
    outside.write_text("outside sentinel\n", encoding="utf-8")
    target = output_root / "CHANGELOG.md"
    try:
        target.symlink_to(outside)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"file symlinks are unavailable: {error}")

    with pytest.raises(ReleaseGateError) as error:
        render_ga_documents(
            output_root,
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

    assert error.value.code == "GA_METADATA_PUBLICATION_TARGET_INVALID"
    assert outside.read_text(encoding="utf-8") == "outside sentinel\n"
    assert target.is_symlink()


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


def test_release_contract_template_matches_committed_contract(tmp_path):
    generated = tmp_path / "gwo-v8-ga-release-contract.md"
    write_release_contract(generated)

    committed = Path("docs/releases/gwo-v8-ga-release-contract.md")

    assert generated.read_text(encoding="utf-8") == committed.read_text(
        encoding="utf-8"
    )
