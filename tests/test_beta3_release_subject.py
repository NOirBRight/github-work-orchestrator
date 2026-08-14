from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import stat
from types import SimpleNamespace

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


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO contract")
def test_loader_rejects_held_open_fifo_without_blocking(tmp_path: Path):
    if not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"):
        pytest.skip("POSIX FIFO contract")
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    manifest = evidence_root / release_subject.RELEASE_SUBJECT_FILENAME
    os.mkfifo(manifest)
    child_code = """
import sys
from pathlib import Path
import beta3_release_subject as release_subject

try:
    release_subject._read_held_regular_file(
        Path(sys.argv[1]), "RELEASE_SUBJECT_UNAVAILABLE"
    )
except release_subject.ReleaseSubjectError as error:
    raise SystemExit(0 if error.code == "RELEASE_SUBJECT_UNAVAILABLE" else 2)
raise SystemExit(3)
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SCRIPTS)
    result = subprocess.run(
        [sys.executable, "-c", child_code, str(manifest)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=1,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)


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


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX-only parent replacement; Windows handle-sharing behavior stays skipped",
)
def test_binding_rejects_parent_delete_recreate_after_binding(tmp_path: Path):
    manifest, binding = _load_binding_fixture(tmp_path)
    parent = manifest.parent
    displaced = tmp_path / "evidence-displaced"
    parent.rename(displaced)
    parent.mkdir()
    try:
        with pytest.raises(ReleaseSubjectError) as error:
            binding.assert_stable()
        assert error.value.code == "RELEASE_SUBJECT_DRIFT"
    finally:
        binding.close()
        parent.rmdir()
        displaced.rename(parent)


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX-only descriptor-relative observation; Windows handle-sharing behavior stays skipped",
)
def test_binding_fresh_read_uses_the_held_parent_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manifest, binding = _load_binding_fixture(tmp_path)
    parent_fd = os.open(
        manifest.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    expected_parent_identity = os.fstat(parent_fd)
    observed_parent_identities: list[tuple[int, int]] = []
    original_open = release_subject.os.open

    def observing_open(*args: object, **kwargs: object) -> int:
        dir_fd = kwargs.get("dir_fd")
        path = args[0] if args else None
        if dir_fd is not None and path is not None:
            try:
                observed = os.fstat(int(dir_fd))
            except OSError:
                pass
            else:
                if Path(path).name == manifest.name:
                    observed_parent_identities.append((observed.st_dev, observed.st_ino))
        return original_open(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(release_subject.os, "open", observing_open)
    try:
        binding.assert_stable()
    finally:
        os.close(parent_fd)
        binding.close()

    assert observed_parent_identities == [
        (expected_parent_identity.st_dev, expected_parent_identity.st_ino)
    ]


def test_binding_close_closes_leaf_and_parent_descriptors_at_most_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _manifest, binding = _load_binding_fixture(tmp_path)
    close_events: list[tuple[int, bool]] = []
    original_close = release_subject.os.close

    def observing_close(descriptor: int) -> None:
        try:
            is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        except OSError:
            is_directory = False
        close_events.append((descriptor, is_directory))
        original_close(descriptor)

    monkeypatch.setattr(release_subject.os, "close", observing_close)
    binding.close()
    binding.close()

    close_counts: dict[int, int] = {}
    for descriptor, _is_directory in close_events:
        close_counts[descriptor] = close_counts.get(descriptor, 0) + 1
    assert close_events
    assert sum(not is_directory for _descriptor, is_directory in close_events) == 1
    assert sum(is_directory for _descriptor, is_directory in close_events) >= 1
    assert all(count <= 1 for count in close_counts.values())


def test_posix_cleanup_does_not_restore_a_detached_directory_over_a_replacement(
    monkeypatch,
):
    monkeypatch.setattr(
        release_subject.os,
        "stat",
        lambda *_args, **_kwargs: SimpleNamespace(st_mode=stat.S_IFDIR),
    )

    def forbidden_rename(*_args, **_kwargs):
        raise AssertionError("directory cleanup must not overwrite the public path")

    monkeypatch.setattr(release_subject.os, "rename", forbidden_rename)

    release_subject._restore_posix_detached_subject(
        "subject.json",
        cleanup_parent=11,
        parent=12,
    )


def test_loader_exception_cleanup_does_not_close_any_descriptor_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manifest = _write_valid_subject_fixture(tmp_path)
    original_open_bound = release_subject._open_path_handle
    original_close = release_subject.os.close

    class OpenHandle:
        def __init__(self, generation: int) -> None:
            self.generation = generation
            self.close_count = 0

    opened_handles: list[OpenHandle] = []
    active_by_descriptor: dict[int, list[OpenHandle]] = {}
    close_events: list[OpenHandle] = []
    unmatched_close_events: list[int] = []

    def tracking_open(*args: object, **kwargs: object) -> int:
        descriptor = original_open_bound(*args, **kwargs)  # type: ignore[arg-type]
        handle = OpenHandle(len(opened_handles) + 1)
        opened_handles.append(handle)
        active_by_descriptor.setdefault(descriptor, []).append(handle)
        return descriptor

    def tracking_close(descriptor: int) -> None:
        handles = active_by_descriptor.get(descriptor)
        if not handles:
            unmatched_close_events.append(descriptor)
            try:
                original_close(descriptor)
            except OSError:
                pass
            return
        handle = handles.pop()
        if not handles:
            del active_by_descriptor[descriptor]
        handle.close_count += 1
        close_events.append(handle)
        try:
            original_close(descriptor)
        except OSError:
            pass

    def fail_binding_construction(*_args: object, **_kwargs: object) -> object:
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_SCHEMA_INVALID", "forced loader failure"
        )

    monkeypatch.setattr(release_subject, "_open_path_handle", tracking_open)
    monkeypatch.setattr(release_subject.os, "close", tracking_close)
    monkeypatch.setattr(
        release_subject,
        "ReleaseSubjectBinding",
        fail_binding_construction,
    )

    try:
        with pytest.raises(ReleaseSubjectError) as error:
            load_release_subject_for_test(
                manifest,
                expected_repository_root=tmp_path / "repository",
                expected_evidence_root=tmp_path / "evidence",
            )

        assert error.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"
        assert opened_handles
        assert not unmatched_close_events
        assert not active_by_descriptor
        assert len(close_events) == len(opened_handles)
        assert all(handle.close_count == 1 for handle in opened_handles)
        assert sorted(handle.generation for handle in close_events) == list(
            range(1, len(opened_handles) + 1)
        )
    finally:
        for descriptor in tuple(active_by_descriptor):
            try:
                original_close(descriptor)
            except OSError:
                pass


def test_loader_cleanup_continues_after_leaf_close_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manifest = _write_valid_subject_fixture(tmp_path)
    (tmp_path / "repository" / "scripts" / "run_beta3_live_guard.py").write_bytes(
        b"changed runner bytes\n"
    )

    original_open_path_handle = release_subject._open_path_handle
    original_read_held_regular_file = release_subject._read_held_regular_file
    original_directory_lease = release_subject._directory_lease
    original_close = release_subject.os.close

    class Descriptor:
        def __init__(self, descriptor: int) -> None:
            self.descriptor = descriptor
            self.active = True

    active_by_descriptor: dict[int, list[Descriptor]] = {}
    descriptors: list[Descriptor] = []
    close_events: list[Descriptor] = []
    leaf_descriptor: Descriptor | None = None
    parent_descriptors: tuple[Descriptor, ...] = ()
    repository_leases: list[tuple[object, tuple[Descriptor, ...]]] = []

    def current_descriptor(descriptor: int) -> Descriptor:
        return active_by_descriptor[descriptor][-1]

    def tracking_open(*args: object, **kwargs: object) -> int:
        descriptor = original_open_path_handle(*args, **kwargs)  # type: ignore[arg-type]
        record = Descriptor(descriptor)
        descriptors.append(record)
        active_by_descriptor.setdefault(descriptor, []).append(record)
        return descriptor

    def tracking_read(*args: object, **kwargs: object):
        nonlocal leaf_descriptor, parent_descriptors
        result = original_read_held_regular_file(*args, **kwargs)  # type: ignore[arg-type]
        leaf_descriptor = current_descriptor(result[2])
        parent_descriptors = tuple(
            current_descriptor(descriptor) for descriptor in result[3].handles
        )
        return result

    def tracking_directory_lease(*args: object, **kwargs: object):
        lease = original_directory_lease(*args, **kwargs)  # type: ignore[arg-type]
        repository_leases.append(
            (lease, tuple(current_descriptor(descriptor) for descriptor in lease.handles))
        )
        return lease

    def failing_close(descriptor: int) -> None:
        record = current_descriptor(descriptor)
        close_events.append(record)
        if record is leaf_descriptor:
            raise OSError("forced leaf close failure")
        active_by_descriptor[descriptor].pop()
        if not active_by_descriptor[descriptor]:
            del active_by_descriptor[descriptor]
        original_close(descriptor)
        record.active = False

    monkeypatch.setattr(release_subject, "_open_path_handle", tracking_open)
    monkeypatch.setattr(
        release_subject,
        "_read_held_regular_file",
        tracking_read,
    )
    monkeypatch.setattr(release_subject, "_directory_lease", tracking_directory_lease)
    monkeypatch.setattr(release_subject.os, "close", failing_close)

    try:
        with pytest.raises(ReleaseSubjectError) as error:
            load_release_subject_for_test(
                manifest,
                expected_repository_root=tmp_path / "repository",
                expected_evidence_root=tmp_path / "evidence",
            )

        assert error.value.code == "RELEASE_SUBJECT_PROVENANCE_MISMATCH"
        assert leaf_descriptor is not None
        assert close_events.count(leaf_descriptor) == 1
        assert parent_descriptors
        assert repository_leases
        repository_lease, repository_descriptors = repository_leases[0]
        assert getattr(repository_lease, "_closed") is True
        assert all(not descriptor.active for descriptor in parent_descriptors)
        assert all(not descriptor.active for descriptor in repository_descriptors)
    finally:
        for record in descriptors:
            if record.active:
                original_close(record.descriptor)
                record.active = False


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
        path: Path,
        code: str,
        *,
        parent_lease: object = None,
    ) -> tuple[bytes, dict[str, object]]:
        assert path == manifest
        assert code == "RELEASE_SUBJECT_DRIFT"
        assert parent_lease is not None
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


def test_binding_maps_missing_fresh_observation_to_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manifest, binding = _load_binding_fixture(tmp_path)

    def missing_fresh_observation(
        path: Path,
        code: str,
        *,
        parent_lease: object = None,
    ) -> tuple[bytes, dict[str, object]]:
        assert path == manifest
        assert code == "RELEASE_SUBJECT_DRIFT"
        assert parent_lease is not None
        raise FileNotFoundError(path)

    monkeypatch.setattr(
        release_subject,
        "_read_regular_file_once",
        missing_fresh_observation,
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
