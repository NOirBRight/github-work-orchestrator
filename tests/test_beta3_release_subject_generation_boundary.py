from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import beta3_release_subject as release_subject  # noqa: E402


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git failed: {arguments}: {result.stdout!r} {result.stderr!r}"
        )
    return result.stdout.strip()


def _authoritative_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repository_root = (tmp_path / "repository").resolve()
    evidence_root = (tmp_path / "evidence").resolve()
    repository_root.mkdir()
    evidence_root.mkdir()

    source_root = ROOT / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    target_source_root = repository_root / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    target_source_root.parent.mkdir(parents=True)
    shutil.copytree(
        source_root,
        target_source_root,
        ignore=shutil.ignore_patterns("__pycache__", "*.py[cod]"),
    )
    (repository_root / "skills" / "implement-gwo").mkdir(parents=True)
    (repository_root / "skills" / "orchestrator").mkdir(exist_ok=True)
    (repository_root / "skills" / "implement-gwo" / "SKILL.md").write_text(
        "# implement-gwo\n", encoding="utf-8"
    )
    (repository_root / "skills" / "orchestrator" / "SKILL.md").write_text(
        "# orchestrator\n", encoding="utf-8"
    )

    scripts_root = repository_root / "scripts"
    scripts_root.mkdir()
    observer_names = ("run_beta3_live_guard.py", *release_subject.ATTESTOR_FILENAMES)
    for name in observer_names:
        shutil.copy2(ROOT / "scripts" / name, scripts_root / name)

    bundle = hashlib.sha256()
    attestors: list[dict[str, str]] = []
    for name in release_subject.ATTESTOR_FILENAMES:
        content = (scripts_root / name).read_bytes()
        encoded_name = name.encode("utf-8")
        bundle.update(len(encoded_name).to_bytes(4, "big"))
        bundle.update(encoded_name)
        bundle.update(len(content).to_bytes(8, "big"))
        bundle.update(content)
        attestors.append(
            {
                "module": name.removesuffix(".py"),
                "path": str(scripts_root / name),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    runner_path = scripts_root / "run_beta3_live_guard.py"
    reviewed = {
        "schema": "gwo-beta3-reviewed-provenance.v1",
        "runner": {
            "module": "run_beta3_live_guard",
            "path": str(runner_path),
            "sha256": hashlib.sha256(runner_path.read_bytes()).hexdigest(),
        },
        "attestors": attestors,
        "attestor_bundle_sha256": bundle.hexdigest(),
    }
    (scripts_root / "beta3_reviewed_provenance.json").write_bytes(
        release_subject.canonical_json_bytes(reviewed)
    )

    _git(repository_root, "init", "-b", "main")
    _git(repository_root, "config", "user.email", "gwo-test@example.invalid")
    _git(repository_root, "config", "user.name", "GWO Test")
    _git(repository_root, "add", ".")
    _git(repository_root, "commit", "-m", "initial authoritative fixture")
    head = _git(repository_root, "rev-parse", "HEAD")
    _git(repository_root, "update-ref", "refs/remotes/origin/main", head)
    return repository_root, evidence_root


def _patch_roots(
    monkeypatch: pytest.MonkeyPatch,
    repository_root: Path,
    evidence_root: Path,
) -> None:
    monkeypatch.setattr(release_subject, "REPOSITORY_ROOT", repository_root)
    monkeypatch.setattr(release_subject, "EVIDENCE_ROOT", evidence_root)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits replacing a held directory")
def test_generation_rejects_replaced_evidence_root_without_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _authoritative_fixture(tmp_path)
    _patch_roots(monkeypatch, repository_root, evidence_root)
    subject = release_subject.generate_production_subject()
    moved_root = tmp_path / "moved-evidence"
    evidence_root.rename(moved_root)
    replacement_root = tmp_path / "evidence"
    replacement_root.mkdir()
    manifest = replacement_root / release_subject.RELEASE_SUBJECT_FILENAME
    binding = None
    try:
        with pytest.raises(release_subject.ReleaseSubjectError) as error:
            binding = release_subject.write_subject_for_test_exclusive(subject, manifest)
        assert error.value.code in {
            "RELEASE_SUBJECT_DRIFT",
            "RELEASE_SUBJECT_PATH_INVALID",
        }
        assert not manifest.exists()
    finally:
        if binding is not None:
            binding.close()
        if manifest.exists():
            manifest.unlink()
        replacement_root.rmdir()
        moved_root.rename(evidence_root)


def test_write_failure_leaves_no_public_manifest_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _authoritative_fixture(tmp_path)
    _patch_roots(monkeypatch, repository_root, evidence_root)
    subject = release_subject.generate_production_subject()
    manifest = evidence_root / release_subject.RELEASE_SUBJECT_FILENAME

    def fail_write(_descriptor: int, _raw: bytes) -> None:
        raise release_subject.ReleaseSubjectError(
            "RELEASE_SUBJECT_WRITE_FAILED", "test write failure"
        )

    monkeypatch.setattr(release_subject, "_write_all", fail_write)
    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.write_subject_for_test_exclusive(subject, manifest)
    assert error.value.code == "RELEASE_SUBJECT_WRITE_FAILED"
    assert not manifest.exists()


def test_loader_failure_leaves_no_public_manifest_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _authoritative_fixture(tmp_path)
    _patch_roots(monkeypatch, repository_root, evidence_root)
    subject = release_subject.generate_production_subject()
    manifest = evidence_root / release_subject.RELEASE_SUBJECT_FILENAME

    def fail_loader(*_args: object, **_kwargs: object) -> object:
        raise release_subject.ReleaseSubjectError(
            "RELEASE_SUBJECT_SCHEMA_INVALID", "test loader failure"
        )

    monkeypatch.setattr(release_subject, "load_release_subject_for_test", fail_loader)
    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.write_subject_for_test_exclusive(subject, manifest)
    assert error.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"
    assert not manifest.exists()
