from __future__ import annotations

from dataclasses import dataclass, replace
import io
import json
import math
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
    monkeypatch.setattr(renderer.os, "fsync", lambda descriptor: fsync_calls.append(descriptor))

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
    pre_tag.write_bytes(
        verifier.canonical_json_bytes(_post_release_receipt_payload())
    )

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
    pre_tag.write_bytes(
        verifier.canonical_json_bytes(_post_release_receipt_payload())
    )
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
