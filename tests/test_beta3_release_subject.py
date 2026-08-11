from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import beta3_release_subject as release_subject  # noqa: E402
from beta3_release_subject import (  # noqa: E402
    RELEASE_SUBJECT_SCHEMA,
    ReleaseFileIdentity,
    ReleaseSubject,
    ReleaseSubjectError,
    ReviewedProvenanceIdentity,
    canonical_json_bytes,
    load_release_subject_for_test,
    parse_release_subject,
    release_subject_digest,
    write_subject_for_test_exclusive,
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


def _valid_subject_files(tmp_path: Path) -> tuple[Path, Path, dict[str, bytes]]:
    repository_root = (tmp_path / "repository").resolve()
    evidence_root = (tmp_path / "evidence").resolve()
    scripts_root = repository_root / "scripts"
    scripts_root.mkdir(parents=True)
    evidence_root.mkdir()
    names = (
        "run_beta3_live_guard.py",
        "beta3_bootstrap_model.py",
        "beta3_control_ownership_attestor.py",
        "beta3_legacy_attestor.py",
        "beta3_replay_guard.py",
    )
    contents = {name: f"{name}\n".encode("ascii") for name in names}
    for name, content in contents.items():
        (scripts_root / name).write_bytes(content)
    attestors = []
    bundle = hashlib.sha256()
    for name in names[1:]:
        content = contents[name]
        digest = hashlib.sha256(content).hexdigest()
        attestors.append(
            {
                "module": name.removesuffix(".py"),
                "path": str(scripts_root / name),
                "sha256": digest,
            }
        )
        encoded_name = name.encode("utf-8")
        bundle.update(len(encoded_name).to_bytes(4, "big"))
        bundle.update(encoded_name)
        bundle.update(len(content).to_bytes(8, "big"))
        bundle.update(content)
    reviewed = {
        "schema": "gwo-beta3-reviewed-provenance.v1",
        "runner": {
            "module": "run_beta3_live_guard",
            "path": str(scripts_root / names[0]),
            "sha256": hashlib.sha256(contents[names[0]]).hexdigest(),
        },
        "attestors": attestors,
        "attestor_bundle_sha256": bundle.hexdigest(),
    }
    reviewed_raw = canonical_json_bytes(reviewed)
    (scripts_root / "beta3_reviewed_provenance.json").write_bytes(reviewed_raw)
    contents["beta3_reviewed_provenance.json"] = reviewed_raw
    return repository_root, evidence_root, contents


def _valid_subject_value(tmp_path: Path) -> ReleaseSubject:
    repository_root, evidence_root, contents = _valid_subject_files(tmp_path)
    scripts_root = repository_root / "scripts"
    body = {
        "schema": RELEASE_SUBJECT_SCHEMA,
        "repository": "NOirBRight/github-work-orchestrator",
        "repository_root": str(repository_root),
        "evidence_root": str(evidence_root),
        "merged_main_sha": "a" * 40,
        "merged_main_git_tree": "b" * 40,
        "audited_source_tree_digest": "c" * 64,
        "remote_ref": "origin/main",
        "runner": {
            "module": "run_beta3_live_guard",
            "path": str(scripts_root / "run_beta3_live_guard.py"),
            "sha256": hashlib.sha256(contents["run_beta3_live_guard.py"]).hexdigest(),
        },
        "attestors": [
            {
                "module": name.removesuffix(".py"),
                "path": str(scripts_root / name),
                "sha256": hashlib.sha256(contents[name]).hexdigest(),
            }
            for name in (
                "beta3_bootstrap_model.py",
                "beta3_control_ownership_attestor.py",
                "beta3_legacy_attestor.py",
                "beta3_replay_guard.py",
            )
        ],
        "attestor_bundle_sha256": json.loads(
            (scripts_root / "beta3_reviewed_provenance.json").read_text()
        )["attestor_bundle_sha256"],
        "reviewed_provenance": {
            "path": str(scripts_root / "beta3_reviewed_provenance.json"),
            "sha256": hashlib.sha256(
                contents["beta3_reviewed_provenance.json"]
            ).hexdigest(),
        },
    }
    payload = {
        **body,
        "subject_digest": release_subject_digest(body),
    }
    return ReleaseSubject.from_canonical(payload)


def _write_valid_subject_fixture(tmp_path: Path) -> Path:
    subject = _valid_subject_value(tmp_path)
    path = tmp_path / "evidence" / "gwo-v8-release-subject.json"
    path.write_bytes(canonical_json_bytes(subject.canonical()))
    return path


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


def test_release_subject_constructor_rejects_wrong_attestor_count(tmp_path: Path):
    subject = ReleaseSubject.from_canonical(_canonical_fixture_payload(tmp_path))
    attestors = subject.attestors[:-1]
    body = subject.canonical_body()
    body["attestors"] = [attestor.canonical() for attestor in attestors]

    with pytest.raises(ReleaseSubjectError) as error:
        replace(
            subject,
            attestors=attestors,
            subject_digest=hashlib.sha256(
                _fixture_canonical_json_bytes(body)
            ).hexdigest(),
        )
    assert error.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_attestor",
        "swapped_attestors",
        "runner_path",
        "attestor_path",
        "reviewed_provenance_path",
    ),
)
def test_release_subject_from_canonical_rejects_noncanonical_fixed_identities(
    tmp_path: Path, mutation: str
):
    payload = _canonical_fixture_payload(tmp_path)
    if mutation == "missing_attestor":
        payload["attestors"] = payload["attestors"][:-1]  # type: ignore[index]
    elif mutation == "swapped_attestors":
        payload["attestors"] = list(reversed(payload["attestors"]))  # type: ignore[arg-type]
    elif mutation == "runner_path":
        payload["runner"]["path"] = str(tmp_path / "other-runner.py")  # type: ignore[index]
    elif mutation == "attestor_path":
        payload["attestors"][0]["path"] = str(tmp_path / "other-attestor.py")  # type: ignore[index]
    else:
        payload["reviewed_provenance"]["path"] = str(  # type: ignore[index]
            tmp_path / "other-provenance.json"
        )
    body = dict(payload)
    body.pop("subject_digest")
    payload["subject_digest"] = hashlib.sha256(
        _fixture_canonical_json_bytes(body)
    ).hexdigest()

    with pytest.raises(ReleaseSubjectError) as error:
        ReleaseSubject.from_canonical(payload)
    assert error.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"


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


def test_subject_parser_rejects_escaped_unpaired_unicode_surrogate(tmp_path: Path):
    raw = canonical_json_bytes(_canonical_fixture_payload(tmp_path)).replace(
        b'"repository":"NOirBRight/github-work-orchestrator"',
        b'"repository":"\\ud800"',
    )
    with pytest.raises(ReleaseSubjectError) as error:
        parse_release_subject(raw, tmp_path / "repository", tmp_path / "evidence")
    assert error.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"


def test_production_loader_uses_one_fixed_path_and_rejects_absence(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        release_subject,
        "EVIDENCE_ROOT",
        Path(r"C:\tmp\gwo-subject-test-evidence"),
    )
    with pytest.raises(ReleaseSubjectError) as error:
        release_subject.load_production_release_subject()
    assert error.value.code == "RELEASE_SUBJECT_UNAVAILABLE"


def _load_binding_fixture(
    tmp_path: Path,
) -> tuple[Path, release_subject.ReleaseSubjectBinding]:
    manifest = _write_valid_subject_fixture(tmp_path)
    return manifest, load_release_subject_for_test(
        manifest,
        expected_repository_root=tmp_path / "repository",
        expected_evidence_root=tmp_path / "evidence",
    )


def _mutated_manifest_bytes(manifest: Path) -> bytes:
    original = manifest.read_bytes()
    return original.replace(b'"' + b"a" * 40 + b'"', b'"' + b"b" * 40 + b'"', 1)


def test_binding_rejects_same_inode_manifest_mutation_after_first_read(
    tmp_path: Path,
):
    manifest, binding = _load_binding_fixture(tmp_path)
    try:
        writer = os.open(manifest, os.O_RDWR)
        try:
            mutated = _mutated_manifest_bytes(manifest)
            os.ftruncate(writer, 0)
            os.write(writer, mutated)
            os.fsync(writer)
            with pytest.raises(ReleaseSubjectError) as error:
                binding.assert_stable()
            assert error.value.code == "RELEASE_SUBJECT_DRIFT"
        finally:
            os.close(writer)
    finally:
        binding.close()


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX permits replacement while a handle is held"
)
def test_binding_rejects_os_replace_manifest_identity_change(tmp_path: Path):
    manifest, binding = _load_binding_fixture(tmp_path)
    try:
        replacement = tmp_path / "replacement.json"
        replacement.write_bytes(_mutated_manifest_bytes(manifest))
        os.replace(replacement, manifest)
        with pytest.raises(ReleaseSubjectError) as error:
            binding.assert_stable()
        assert error.value.code == "RELEASE_SUBJECT_DRIFT"
    finally:
        binding.close()


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX permits replacement while a handle is held"
)
def test_binding_rejects_identical_os_replace_manifest_identity_change(
    tmp_path: Path,
):
    manifest, binding = _load_binding_fixture(tmp_path)
    try:
        replacement = tmp_path / "replacement.json"
        replacement.write_bytes(manifest.read_bytes())
        os.replace(replacement, manifest)
        assert manifest.read_bytes() == binding.raw_bytes
        with pytest.raises(ReleaseSubjectError) as error:
            binding.assert_stable()
        assert error.value.code == "RELEASE_SUBJECT_DRIFT"
    finally:
        binding.close()


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX permits delete/recreate while a handle is held"
)
def test_binding_rejects_delete_recreate_manifest_identity_change(tmp_path: Path):
    manifest, binding = _load_binding_fixture(tmp_path)
    try:
        mutated = _mutated_manifest_bytes(manifest)
        manifest.unlink()
        manifest.write_bytes(mutated)
        with pytest.raises(ReleaseSubjectError) as error:
            binding.assert_stable()
        assert error.value.code == "RELEASE_SUBJECT_DRIFT"
    finally:
        binding.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows held-handle sharing contract")
def test_windows_held_binding_blocks_manifest_replace(tmp_path: Path):
    manifest, binding = _load_binding_fixture(tmp_path)
    try:
        replacement = tmp_path / "replacement.json"
        replacement.write_bytes(_mutated_manifest_bytes(manifest))
        with pytest.raises(OSError):
            os.replace(replacement, manifest)
        binding.assert_stable()
    finally:
        binding.close()


def test_binding_rejects_identity_only_fresh_observation_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manifest, binding = _load_binding_fixture(tmp_path)
    fresh_identity = dict(binding.identity)
    if "file_id" in fresh_identity:
        file_id = str(fresh_identity["file_id"])
        replacement = "0" * len(file_id)
        if replacement == file_id:
            replacement = "1" * len(file_id)
        fresh_identity["file_id"] = replacement
    else:
        fresh_identity["st_ino"] = int(fresh_identity["st_ino"]) + 1

    def same_bytes_with_different_identity(
        path: Path, code: str
    ) -> tuple[bytes, dict[str, object]]:
        assert path == manifest
        assert code == "RELEASE_SUBJECT_DRIFT"
        return binding.raw_bytes, fresh_identity

    monkeypatch.setattr(
        release_subject,
        "_read_regular_file_once",
        same_bytes_with_different_identity,
    )
    try:
        with pytest.raises(ReleaseSubjectError) as error:
            binding.assert_stable()
        assert error.value.code == "RELEASE_SUBJECT_DRIFT"
    finally:
        binding.close()


def test_exclusive_generator_does_not_replace_existing_subject(tmp_path: Path):
    subject = _valid_subject_value(tmp_path)
    path = tmp_path / "evidence" / "gwo-v8-release-subject.json"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"existing subject bytes\n")
    with pytest.raises(ReleaseSubjectError) as error:
        write_subject_for_test_exclusive(subject, path)
    assert error.value.code == "RELEASE_SUBJECT_EXISTS"
    assert path.read_bytes() == b"existing subject bytes\n"


def test_exclusive_generator_writes_canonical_subject_and_returns_binding(
    tmp_path: Path,
):
    subject = _valid_subject_value(tmp_path)
    path = tmp_path / "evidence" / "gwo-v8-release-subject.json"
    binding = write_subject_for_test_exclusive(subject, path)
    try:
        assert binding.subject == subject
        assert path.read_bytes() == canonical_json_bytes(subject.canonical())
        binding.assert_stable()
    finally:
        binding.close()


def _assert_path_rejected_before_json_decode(
    manifest: Path,
    repository_root: Path,
    evidence_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def parser_must_not_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("manifest JSON parsing ran before path rejection")

    monkeypatch.setattr(release_subject, "parse_release_subject", parser_must_not_run)
    with pytest.raises(ReleaseSubjectError) as error:
        load_release_subject_for_test(
            manifest,
            expected_repository_root=repository_root,
            expected_evidence_root=evidence_root,
        )
    assert error.value.code == "RELEASE_SUBJECT_PATH_INVALID"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction/reparse contract")
def test_loader_rejects_windows_junction_ancestor_before_json_decoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    real_root = tmp_path / "real"
    manifest = _write_valid_subject_fixture(real_root)
    junction = tmp_path / "junction"
    result = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(junction), str(real_root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not junction.is_dir():
        pytest.fail(
            "Windows junction fixture could not be created: "
            f"exit={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
    _assert_path_rejected_before_json_decode(
        junction / manifest.relative_to(real_root),
        real_root / "repository",
        real_root / "evidence",
        monkeypatch,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX O_NOFOLLOW contract")
def test_loader_rejects_posix_symlink_ancestor_before_json_decoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    real_root = tmp_path / "real"
    manifest = _write_valid_subject_fixture(real_root)
    redirected = tmp_path / "redirected"
    try:
        redirected.symlink_to(real_root, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        pytest.fail(f"POSIX symlink fixture could not be created: {error}")
    _assert_path_rejected_before_json_decode(
        redirected / manifest.relative_to(real_root),
        real_root / "repository",
        real_root / "evidence",
        monkeypatch,
    )


def test_loader_maps_observer_identity_mismatch_to_provenance_mismatch(tmp_path: Path):
    manifest = _write_valid_subject_fixture(tmp_path)
    runner = tmp_path / "repository" / "scripts" / "run_beta3_live_guard.py"
    runner.write_bytes(b"changed runner bytes\n")
    with pytest.raises(ReleaseSubjectError) as error:
        load_release_subject_for_test(
            manifest,
            expected_repository_root=tmp_path / "repository",
            expected_evidence_root=tmp_path / "evidence",
        )
    assert error.value.code == "RELEASE_SUBJECT_PROVENANCE_MISMATCH"


def test_loader_maps_malformed_reviewed_provenance_to_provenance_mismatch(
    tmp_path: Path,
):
    manifest = _write_valid_subject_fixture(tmp_path)
    reviewed = tmp_path / "repository" / "scripts" / "beta3_reviewed_provenance.json"
    reviewed.write_bytes(b"{}\n")
    with pytest.raises(ReleaseSubjectError) as error:
        load_release_subject_for_test(
            manifest,
            expected_repository_root=tmp_path / "repository",
            expected_evidence_root=tmp_path / "evidence",
        )
    assert error.value.code == "RELEASE_SUBJECT_PROVENANCE_MISMATCH"
