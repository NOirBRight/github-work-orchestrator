from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from beta3_release_subject import (  # noqa: E402
    RELEASE_SUBJECT_SCHEMA,
    ReleaseFileIdentity,
    ReleaseSubject,
    ReleaseSubjectError,
    ReviewedProvenanceIdentity,
    canonical_json_bytes,
    parse_release_subject,
    release_subject_digest,
)


def _fixture_canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _canonical_fixture_body(tmp_path: Path) -> dict[str, object]:
    repository_root = (tmp_path / "repository").resolve()
    evidence_root = (tmp_path / "evidence").resolve()
    return {
        "schema": "gwo-v8-release-subject.v1",
        "repository": "NOirBRight/github-work-orchestrator",
        "repository_root": str(repository_root),
        "evidence_root": str(evidence_root),
        "merged_main_sha": "a" * 40,
        "merged_main_git_tree": "b" * 40,
        "audited_source_tree_digest": "c" * 64,
        "remote_ref": "origin/main",
        "runner": {
            "module": "run_beta3_live_guard",
            "path": str(repository_root / "scripts" / "run_beta3_live_guard.py"),
            "sha256": "d" * 64,
        },
        "attestors": [
            {
                "module": module_name.removesuffix(".py"),
                "path": str(repository_root / "scripts" / module_name),
                "sha256": digest,
            }
            for module_name, digest in zip(
                (
                    "beta3_bootstrap_model.py",
                    "beta3_control_ownership_attestor.py",
                    "beta3_legacy_attestor.py",
                    "beta3_replay_guard.py",
                ),
                ("e" * 64, "f" * 64, "1" * 64, "2" * 64),
                strict=True,
            )
        ],
        "attestor_bundle_sha256": "3" * 64,
        "reviewed_provenance": {
            "path": str(repository_root / "scripts" / "beta3_reviewed_provenance.json"),
            "sha256": "4" * 64,
        },
    }


def _canonical_fixture_payload(tmp_path: Path) -> dict[str, object]:
    body = _canonical_fixture_body(tmp_path)
    return {
        **body,
        "subject_digest": hashlib.sha256(
            _fixture_canonical_json_bytes(body)
        ).hexdigest(),
    }


def test_subject_digest_excludes_only_subject_digest(tmp_path: Path):
    repository_root = (tmp_path / "repository").resolve()
    evidence_root = (tmp_path / "evidence").resolve()
    body = {
        "schema": "gwo-v8-release-subject.v1",
        "repository": "NOirBRight/github-work-orchestrator",
        "repository_root": str(repository_root),
        "evidence_root": str(evidence_root),
        "merged_main_sha": "a" * 40,
        "merged_main_git_tree": "b" * 40,
        "audited_source_tree_digest": "c" * 64,
        "remote_ref": "origin/main",
        "runner": {
            "module": "run_beta3_live_guard",
            "path": str(repository_root / "scripts" / "run_beta3_live_guard.py"),
            "sha256": "d" * 64,
        },
        "attestors": [
            {
                "module": module_name.removesuffix(".py"),
                "path": str(repository_root / "scripts" / module_name),
                "sha256": digest,
            }
            for module_name, digest in zip(
                (
                    "beta3_bootstrap_model.py",
                    "beta3_control_ownership_attestor.py",
                    "beta3_legacy_attestor.py",
                    "beta3_replay_guard.py",
                ),
                ("e" * 64, "f" * 64, "1" * 64, "2" * 64),
                strict=True,
            )
        ],
        "attestor_bundle_sha256": "3" * 64,
        "reviewed_provenance": {
            "path": str(repository_root / "scripts" / "beta3_reviewed_provenance.json"),
            "sha256": "4" * 64,
        },
    }
    body_bytes = canonical_json_bytes(body)
    payload = {**body, "subject_digest": hashlib.sha256(body_bytes).hexdigest()}
    parsed = parse_release_subject(
        canonical_json_bytes(payload),
        expected_repository_root=repository_root,
        expected_evidence_root=evidence_root,
    )
    assert parsed.canonical_body() == body
    assert parsed.canonical() == payload


def test_subject_schema_rejects_extra_key_and_swapped_identity_domains(tmp_path: Path):
    payload = _canonical_fixture_payload(tmp_path)
    payload["unexpected"] = True
    with pytest.raises(ReleaseSubjectError) as extra:
        parse_release_subject(
            canonical_json_bytes(payload),
            tmp_path / "repository",
            tmp_path / "evidence",
        )
    assert extra.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"

    swapped = _canonical_fixture_payload(tmp_path)
    swapped["merged_main_git_tree"], swapped["audited_source_tree_digest"] = (
        swapped["audited_source_tree_digest"],
        swapped["merged_main_git_tree"],
    )
    with pytest.raises(ReleaseSubjectError) as identity:
        parse_release_subject(
            canonical_json_bytes(swapped),
            tmp_path / "repository",
            tmp_path / "evidence",
        )
    assert identity.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"


def test_canonical_json_bytes_is_sorted_utf8_and_terminated():
    assert canonical_json_bytes({"z": "é", "a": 1}) == b'{"a":1,"z":"\xc3\xa9"}\n'


def test_release_subject_digest_hashes_only_the_canonical_body():
    body = {"b": [2, 1], "a": {"é": "value"}}
    assert (
        release_subject_digest(body)
        == hashlib.sha256(_fixture_canonical_json_bytes(body)).hexdigest()
    )


def test_parsed_subject_has_frozen_typed_nested_identities(tmp_path: Path):
    repository_root = (tmp_path / "repository").resolve()
    evidence_root = (tmp_path / "evidence").resolve()
    parsed = parse_release_subject(
        canonical_json_bytes(_canonical_fixture_payload(tmp_path)),
        repository_root,
        evidence_root,
    )
    assert type(parsed) is ReleaseSubject
    assert type(parsed.runner) is ReleaseFileIdentity
    assert type(parsed.reviewed_provenance) is ReviewedProvenanceIdentity
    assert type(parsed.attestors) is tuple
    assert [item.module for item in parsed.attestors] == [
        "beta3_bootstrap_model",
        "beta3_control_ownership_attestor",
        "beta3_legacy_attestor",
        "beta3_replay_guard",
    ]
    with pytest.raises(FrozenInstanceError):
        parsed.schema = RELEASE_SUBJECT_SCHEMA  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("merged_main_sha", "A" * 40),
        ("merged_main_sha", "a" * 39),
        ("merged_main_git_tree", "b" * 39),
        ("audited_source_tree_digest", "C" * 64),
        ("audited_source_tree_digest", "c" * 63),
        ("attestor_bundle_sha256", "3" * 63),
        ("subject_digest", "G" * 64),
    ),
)
def test_subject_schema_rejects_wrong_digest_domains(
    tmp_path: Path, field: str, value: str
):
    payload = _canonical_fixture_payload(tmp_path)
    payload[field] = value
    with pytest.raises(ReleaseSubjectError) as error:
        parse_release_subject(
            canonical_json_bytes(payload),
            tmp_path / "repository",
            tmp_path / "evidence",
        )
    assert error.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"


def test_subject_schema_rejects_wrong_nested_digest_length(tmp_path: Path):
    payload = _canonical_fixture_payload(tmp_path)
    payload["runner"]["sha256"] = "d" * 63  # type: ignore[index]
    with pytest.raises(ReleaseSubjectError) as error:
        parse_release_subject(
            canonical_json_bytes(payload),
            tmp_path / "repository",
            tmp_path / "evidence",
        )
    assert error.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"


def test_subject_schema_rejects_nested_extra_key(tmp_path: Path):
    payload = _canonical_fixture_payload(tmp_path)
    payload["runner"]["extra"] = True  # type: ignore[index]
    with pytest.raises(ReleaseSubjectError) as error:
        parse_release_subject(
            canonical_json_bytes(payload),
            tmp_path / "repository",
            tmp_path / "evidence",
        )
    assert error.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"


def test_subject_schema_rejects_attestor_reordering(tmp_path: Path):
    payload = _canonical_fixture_payload(tmp_path)
    payload["attestors"] = list(reversed(payload["attestors"]))  # type: ignore[arg-type]
    with pytest.raises(ReleaseSubjectError) as error:
        parse_release_subject(
            canonical_json_bytes(payload),
            tmp_path / "repository",
            tmp_path / "evidence",
        )
    assert error.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"


def test_subject_schema_rejects_wrong_roots_and_fixed_literals(tmp_path: Path):
    cases = (
        ("repository_root", str((tmp_path / "other-repository").resolve())),
        ("evidence_root", str((tmp_path / "other-evidence").resolve())),
        ("repository", "other/repository"),
        ("schema", "gwo-v8-release-subject.v2"),
        ("remote_ref", "refs/heads/main"),
    )
    for field, value in cases:
        payload = _canonical_fixture_payload(tmp_path)
        payload[field] = value
        with pytest.raises(ReleaseSubjectError) as error:
            parse_release_subject(
                canonical_json_bytes(payload),
                tmp_path / "repository",
                tmp_path / "evidence",
            )
        assert error.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"


def test_subject_digest_mismatch_is_reported_after_schema_validation(tmp_path: Path):
    payload = _canonical_fixture_payload(tmp_path)
    payload["subject_digest"] = "0" * 64
    with pytest.raises(ReleaseSubjectError) as error:
        parse_release_subject(
            canonical_json_bytes(payload),
            tmp_path / "repository",
            tmp_path / "evidence",
        )
    assert error.value.code == "RELEASE_SUBJECT_DIGEST_MISMATCH"


def test_subject_parser_requires_canonical_json_bytes(tmp_path: Path):
    payload = _canonical_fixture_payload(tmp_path)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with pytest.raises(ReleaseSubjectError) as error:
        parse_release_subject(raw, tmp_path / "repository", tmp_path / "evidence")
    assert error.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"


def test_subject_parser_rejects_duplicate_json_keys(tmp_path: Path):
    payload = _canonical_fixture_payload(tmp_path)
    raw = canonical_json_bytes(payload).replace(
        b'"schema":"gwo-v8-release-subject.v1",',
        b'"schema":"gwo-v8-release-subject.v1","schema":"gwo-v8-release-subject.v1",',
    )
    with pytest.raises(ReleaseSubjectError) as error:
        parse_release_subject(raw, tmp_path / "repository", tmp_path / "evidence")
    assert error.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"
