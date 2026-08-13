from __future__ import annotations

import hashlib
import os
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
    shutil.copytree(
        source_root,
        target_source_root,
        ignore=shutil.ignore_patterns("__pycache__", "*.py[cod]"),
    )
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
        pass
        subject.close()


def test_source_digest_does_not_write_bytecode_to_authoritative_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)

    assert release_subject.source_tree_digest(repository_root)
    assert not tuple(repository_root.rglob("__pycache__"))


def test_source_digest_does_not_use_preloaded_shadowed_gwo_v8_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    shadow_path = tmp_path / "shadow" / "cutover_guard.py"
    shadow_path.parent.mkdir()
    shadow_path.write_text("# shadow\n", encoding="utf-8")
    shadow_package = SimpleNamespace(
        __path__=[str(shadow_path.parent)],
        __file__=str(shadow_path.parent / "__init__.py"),
    )
    shadow_module = SimpleNamespace(
        __file__=str(shadow_path),
        __spec__=SimpleNamespace(origin=str(shadow_path)),
        source_tree_digest=lambda *_args, **_kwargs: "f" * 64,
    )
    monkeypatch.setitem(sys.modules, "gwo_v8", shadow_package)
    monkeypatch.setitem(sys.modules, "gwo_v8.cutover_guard", shadow_module)

    observed = release_subject.source_tree_digest(repository_root)

    assert observed != "f" * 64


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


def test_generator_holds_repository_and_evidence_boundaries_until_subject_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    events: list[str] = []

    original_git = release_subject._git_snapshot
    original_source = release_subject.source_tree_digest
    original_observer = release_subject._observer_snapshot

    def git(*, repository_lease=None):
        events.append(f"git:{repository_lease is not None}")
        return original_git(repository_lease=repository_lease)

    def source(root, *, root_handle=None):
        events.append(f"source:{root_handle is not None}")
        return original_source(root, root_handle=root_handle)

    def observer(root, *, repository_lease=None):
        events.append(f"observer:{repository_lease is not None}")
        return original_observer(root, repository_lease=repository_lease)

    monkeypatch.setattr(release_subject, "_git_snapshot", git)
    monkeypatch.setattr(release_subject, "source_tree_digest", source)
    monkeypatch.setattr(release_subject, "_observer_snapshot", observer)

    subject = release_subject.generate_production_subject()
    assert events
    assert all(event.endswith(":True") for event in events)
    assert getattr(subject, "_generation_lease", None) is not None
    binding = release_subject.write_subject_for_test_exclusive(subject, evidence_root / release_subject.RELEASE_SUBJECT_FILENAME)
    binding.close()


def test_generator_closes_transferred_lease_after_successful_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)

    subject = release_subject.generate_production_subject()
    lease = getattr(subject, "_generation_lease", None)
    assert lease is not None
    binding = release_subject.write_subject_for_test_exclusive(
        subject,
        evidence_root / release_subject.RELEASE_SUBJECT_FILENAME,
    )
    try:
        assert getattr(subject, "_generation_lease", None) is None
        assert getattr(lease, "_closed", False) is True
    finally:
        binding.close()


def test_generator_loader_shares_delete_with_writer_manifest_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    subject = release_subject.generate_production_subject()
    loader_share_delete: list[bool] = []
    original_open = release_subject._open_path_handle

    def tracking_open(path: Path, code: str, **kwargs: object) -> int:
        if (
            Path(path).name == release_subject.RELEASE_SUBJECT_FILENAME
            and code == "RELEASE_SUBJECT_UNAVAILABLE"
        ):
            loader_share_delete.append(bool(kwargs.get("share_delete", False)))
        return original_open(path, code, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(release_subject, "_open_path_handle", tracking_open)
    binding = release_subject.write_subject_for_test_exclusive(
        subject,
        evidence_root / release_subject.RELEASE_SUBJECT_FILENAME,
    )
    try:
        assert loader_share_delete == [True]
    finally:
        binding.close()


def test_generator_closes_transferred_lease_when_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)

    subject = release_subject.generate_production_subject()
    lease = getattr(subject, "_generation_lease", None)
    assert lease is not None

    def fail_write(_descriptor: int, _raw: bytes) -> None:
        raise release_subject.ReleaseSubjectError(
            "RELEASE_SUBJECT_WRITE_FAILED",
            "test write failure",
        )

    monkeypatch.setattr(release_subject, "_write_all", fail_write)
    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.write_subject_for_test_exclusive(
            subject,
            evidence_root / release_subject.RELEASE_SUBJECT_FILENAME,
        )
    assert error.value.code == "RELEASE_SUBJECT_WRITE_FAILED"
    assert not (evidence_root / release_subject.RELEASE_SUBJECT_FILENAME).exists()
    assert getattr(subject, "_generation_lease", None) is None
    assert getattr(lease, "_closed", False) is True


def test_generator_publicly_holds_evidence_boundary_until_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)

    subject = release_subject.generate_production_subject()
    moved_root = tmp_path / "moved-evidence"
    renamed = True
    try:
        evidence_root.rename(moved_root)
    except OSError:
        renamed = False
    if not renamed:
        binding = release_subject.write_subject_for_test_exclusive(
            subject,
            evidence_root / release_subject.RELEASE_SUBJECT_FILENAME,
        )
        binding.close()
        assert (evidence_root / release_subject.RELEASE_SUBJECT_FILENAME).is_file()
        return
    evidence_root.mkdir()
    replacement_subject = evidence_root / release_subject.RELEASE_SUBJECT_FILENAME
    try:
        binding = None
        try:
            binding = release_subject.write_subject_for_test_exclusive(
                subject,
                replacement_subject,
            )
        except release_subject.ReleaseSubjectError as error:
            assert error.code in {
                "RELEASE_SUBJECT_DRIFT",
                "RELEASE_SUBJECT_PATH_INVALID",
            }
        else:
            binding.close()
            pytest.fail("writer accepted a replaced evidence root")
        assert not replacement_subject.exists()
    finally:
        if replacement_subject.exists():
            try:
                replacement_subject.unlink()
            except OSError:
                pass
        try:
            evidence_root.rmdir()
        except OSError:
            pass
        if not evidence_root.exists():
            try:
                moved_root.rename(evidence_root)
            except OSError:
                pass


def test_generator_public_cleanup_after_loader_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    subject = release_subject.generate_production_subject()
    subject_path = evidence_root / release_subject.RELEASE_SUBJECT_FILENAME

    def fail_loader(*_args: object, **_kwargs: object) -> object:
        raise release_subject.ReleaseSubjectError(
            "RELEASE_SUBJECT_SCHEMA_INVALID",
            "test loader failure",
        )

    monkeypatch.setattr(release_subject, "load_release_subject_for_test", fail_loader)
    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.write_subject_for_test_exclusive(subject, subject_path)
    assert error.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"
    assert not subject_path.exists()


def test_generator_public_cleanup_after_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    subject = release_subject.generate_production_subject()
    subject_path = evidence_root / release_subject.RELEASE_SUBJECT_FILENAME

    def fail_write(_descriptor: int, _raw: bytes) -> None:
        raise release_subject.ReleaseSubjectError(
            "RELEASE_SUBJECT_WRITE_FAILED",
            "test write failure",
        )

    monkeypatch.setattr(release_subject, "_write_all", fail_write)
    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.write_subject_for_test_exclusive(subject, subject_path)
    assert error.value.code == "RELEASE_SUBJECT_WRITE_FAILED"
    assert not subject_path.exists()


def test_generator_public_cleanup_after_create_identity_capture_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    subject = release_subject.generate_production_subject()
    subject_path = evidence_root / release_subject.RELEASE_SUBJECT_FILENAME
    original_identity = release_subject._windows_handle_identity
    identity_calls = 0

    def fail_once(
        descriptor: int,
        code: str,
        *,
        directory: bool,
    ) -> dict[str, int | str]:
        nonlocal identity_calls
        if not directory and code == "RELEASE_SUBJECT_WRITE_FAILED":
            identity_calls += 1
            if identity_calls == 1:
                raise release_subject.ReleaseSubjectError(
                    "RELEASE_SUBJECT_WRITE_FAILED",
                    "test identity capture failure",
                )
        return original_identity(descriptor, code, directory=directory)

    monkeypatch.setattr(release_subject, "_windows_handle_identity", fail_once)
    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.write_subject_for_test_exclusive(subject, subject_path)

    assert error.value.code == "RELEASE_SUBJECT_WRITE_FAILED"
    assert identity_calls == 1
    assert not subject_path.exists()


def test_generator_public_close_is_idempotent_after_successful_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    subject = release_subject.generate_production_subject()
    binding = release_subject.write_subject_for_test_exclusive(
        subject,
        evidence_root / release_subject.RELEASE_SUBJECT_FILENAME,
    )
    binding.close()
    binding.close()

    moved_root = tmp_path / "closed-evidence"
    evidence_root.rename(moved_root)
    moved_root.rename(evidence_root)


@pytest.mark.skipif(os.name == "nt", reason="POSIX replacement safety contract")
def test_generator_does_not_remove_replaced_subject_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    subject = release_subject.generate_production_subject()
    subject_path = evidence_root / release_subject.RELEASE_SUBJECT_FILENAME
    replacement = evidence_root / "replacement-subject.json"
    replacement_bytes = b"replacement subject bytes\n"

    def replace_and_fail(*_args: object, **_kwargs: object) -> object:
        replacement.write_bytes(replacement_bytes)
        os.replace(replacement, subject_path)
        raise release_subject.ReleaseSubjectError(
            "RELEASE_SUBJECT_WRITE_FAILED", "test loader failure"
        )

    monkeypatch.setattr(release_subject, "load_release_subject_for_test", replace_and_fail)
    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.write_subject_for_test_exclusive(subject, subject_path)
    assert error.value.code == "RELEASE_SUBJECT_WRITE_FAILED"
    assert subject_path.read_bytes() == replacement_bytes


def test_generator_rejects_fifo_subject_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    if not hasattr(os, "mkfifo") or os.name == "nt":
        pytest.skip("POSIX FIFO contract")
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    subject_path = evidence_root / release_subject.RELEASE_SUBJECT_FILENAME
    os.mkfifo(subject_path)
    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.generate_production_subject()
    assert error.value.code == "RELEASE_SUBJECT_PATH_INVALID"


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO contract")
def test_open_path_handle_opens_fifo_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    if not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"):
        pytest.skip("POSIX FIFO contract")
    fifo_path = tmp_path / "subject"
    os.mkfifo(fifo_path)
    original_open = release_subject.os.open
    observed_flags: list[int] = []

    def tracking_open(
        path: object,
        flags: int,
        mode: int = 0o644,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if Path(path).name == fifo_path.name:
            observed_flags.append(flags)
            assert flags & os.O_NONBLOCK
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(release_subject.os, "open", tracking_open)
    descriptor = release_subject._open_path_handle(
        fifo_path,
        "RELEASE_SUBJECT_PATH_INVALID",
        directory=False,
    )
    try:
        assert observed_flags
    finally:
        os.close(descriptor)
