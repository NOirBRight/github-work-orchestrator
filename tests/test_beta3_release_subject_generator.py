from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generate_beta3_release_subject as generator  # noqa: E402
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


def _write_authoritative_repository(tmp_path: Path) -> tuple[Path, Path]:
    repository_root = (tmp_path / "repository").resolve()
    evidence_root = (tmp_path / "evidence").resolve()
    repository_root.mkdir()
    evidence_root.mkdir()

    source_root = ROOT / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    target_source_root = (
        repository_root / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    )
    target_source_root.parent.mkdir(parents=True)
    shutil.copytree(source_root, target_source_root)
    (repository_root / "skills" / "implement-gwo").mkdir(parents=True)
    (repository_root / "skills" / "orchestrator").mkdir(exist_ok=True)
    (repository_root / "skills" / "implement-gwo" / "SKILL.md").write_text(
        "# implement-gwo\n",
        encoding="utf-8",
    )
    (repository_root / "skills" / "orchestrator" / "SKILL.md").write_text(
        "# orchestrator\n",
        encoding="utf-8",
    )

    scripts_root = repository_root / "scripts"
    scripts_root.mkdir()
    observer_names = (
        "run_beta3_live_guard.py",
        *release_subject.ATTESTOR_FILENAMES,
    )
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
    runner_content = (scripts_root / "run_beta3_live_guard.py").read_bytes()
    reviewed = {
        "schema": "gwo-beta3-reviewed-provenance.v1",
        "runner": {
            "module": "run_beta3_live_guard",
            "path": str(scripts_root / "run_beta3_live_guard.py"),
            "sha256": hashlib.sha256(runner_content).hexdigest(),
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


def _patch_fixed_roots(
    monkeypatch: pytest.MonkeyPatch,
    repository_root: Path,
    evidence_root: Path,
) -> None:
    monkeypatch.setattr(release_subject, "REPOSITORY_ROOT", repository_root)
    monkeypatch.setattr(release_subject, "EVIDENCE_ROOT", evidence_root)


def test_generator_main_rejects_options_without_reading_or_writing(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        generator,
        "generate_production_subject",
        lambda: pytest.fail("options must be rejected before generation"),
    )
    monkeypatch.setattr(
        generator,
        "write_production_subject_exclusive",
        lambda _subject: pytest.fail("options must be rejected before writing"),
    )
    assert generator.main(["--path", "elsewhere"]) == 1


def test_generator_main_uses_only_fixed_generation_and_writer_seams(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    calls: list[str] = []
    subject = SimpleNamespace(subject_digest="a" * 64)

    def generate():
        calls.append("generate")
        return subject

    class Binding:
        def assert_stable(self) -> None:
            calls.append("assert_stable")

        def close(self) -> None:
            calls.append("close")

    def write(observed):
        assert observed is subject
        calls.append("write")
        return Binding()

    monkeypatch.setattr(generator, "generate_production_subject", generate)
    monkeypatch.setattr(generator, "write_production_subject_exclusive", write)

    assert generator.main([]) == 0
    assert calls == ["generate", "write", "assert_stable", "close"]
    assert capsys.readouterr().out == f"{'a' * 64}\n"


def test_generator_reads_real_git_source_and_observer_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)

    assert generator.main([]) == 0

    subject_path = evidence_root / release_subject.RELEASE_SUBJECT_FILENAME
    subject = release_subject.load_release_subject_for_test(
        subject_path,
        expected_repository_root=repository_root,
        expected_evidence_root=evidence_root,
    )
    try:
        assert subject.subject.merged_main_sha == _git(
            repository_root, "rev-parse", "HEAD"
        )
        assert subject.subject.merged_main_git_tree == _git(
            repository_root,
            "rev-parse",
            "HEAD^{tree}",
        )
        assert len(subject.subject.audited_source_tree_digest) == 64
        assert capsys.readouterr().out == f"{subject.subject.subject_digest}\n"
    finally:
        subject.close()


def test_generator_rejects_origin_mismatch_from_real_git_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    (repository_root / "tracked.txt").write_text("second\n", encoding="utf-8")
    _git(repository_root, "add", "tracked.txt")
    _git(repository_root, "commit", "-m", "second authoritative commit")
    old_head = _git(repository_root, "rev-parse", "HEAD^")
    _git(repository_root, "update-ref", "refs/remotes/origin/main", old_head)

    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.generate_production_subject()
    assert error.value.code == "RELEASE_SUBJECT_ORIGIN_MISMATCH"


def test_generator_rejects_real_dirty_status_outside_codex_tmp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    (repository_root / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.generate_production_subject()
    assert error.value.code == "RELEASE_SUBJECT_GIT_DIRTY"


def test_generator_rejects_existing_subject_before_authoritative_producers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    subject_path = evidence_root / release_subject.RELEASE_SUBJECT_FILENAME
    subject_path.write_bytes(b"existing\n")

    monkeypatch.setattr(
        release_subject,
        "_git_snapshot",
        lambda: pytest.fail("Git producer ran before existing-subject rejection"),
    )
    monkeypatch.setattr(
        release_subject,
        "source_tree_digest",
        lambda _root: pytest.fail(
            "source producer ran before existing-subject rejection"
        ),
    )
    monkeypatch.setattr(
        release_subject,
        "_observer_snapshot",
        lambda _root: pytest.fail(
            "observer producer ran before existing-subject rejection"
        ),
    )

    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.generate_production_subject()
    assert error.value.code == "RELEASE_SUBJECT_EXISTS"
    assert subject_path.read_bytes() == b"existing\n"


def test_generator_rejects_missing_fixed_evidence_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, _evidence_root = _write_authoritative_repository(tmp_path)
    missing_evidence_root = tmp_path / "missing-evidence"
    _patch_fixed_roots(monkeypatch, repository_root, missing_evidence_root)

    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.generate_production_subject()
    assert error.value.code == "RELEASE_SUBJECT_EVIDENCE_INVALID"
    assert not missing_evidence_root.exists()
