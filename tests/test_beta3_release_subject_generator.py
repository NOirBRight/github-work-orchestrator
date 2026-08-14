from __future__ import annotations

import builtins
import hashlib
import importlib._bootstrap_external
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import threading
from types import ModuleType
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
    (evidence_root / release_subject.FRESH_RECEIPT_FILENAME).write_bytes(
        release_subject.canonical_json_bytes({"receipt": "fresh"})
    )

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


def test_generator_binds_exact_fresh_receipt_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    receipt_path = evidence_root / "fresh-store-exact-main-receipt.json"
    receipt_raw = release_subject.canonical_json_bytes({"receipt": "fresh"})
    receipt_path.write_bytes(receipt_raw)

    assert generator.main([]) == 0

    subject_path = evidence_root / release_subject.RELEASE_SUBJECT_FILENAME
    binding = release_subject.load_release_subject_for_test(
        subject_path,
        expected_repository_root=repository_root,
        expected_evidence_root=evidence_root,
    )
    try:
        assert binding.subject.fresh_receipt_sha256 == hashlib.sha256(
            receipt_raw
        ).hexdigest()
    finally:
        binding.close()


def test_generator_fails_closed_when_fresh_receipt_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    (evidence_root / release_subject.FRESH_RECEIPT_FILENAME).unlink()

    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.generate_production_subject()

    assert error.value.code == "RELEASE_SUBJECT_FRESH_RECEIPT_UNAVAILABLE"


def test_generator_fails_closed_for_noncanonical_fresh_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    (evidence_root / release_subject.FRESH_RECEIPT_FILENAME).write_bytes(
        b'{"receipt":"fresh"}'
    )

    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.generate_production_subject()

    assert error.value.code == "RELEASE_SUBJECT_FRESH_RECEIPT_INVALID"


def test_generator_rejects_fresh_receipt_replaced_during_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    receipt_path = evidence_root / release_subject.FRESH_RECEIPT_FILENAME
    original_git_snapshot = release_subject._git_snapshot
    replaced = False

    def replace_after_git_snapshot(
        *, repository_lease: object = None
    ) -> tuple[str, str]:
        nonlocal replaced
        observed = original_git_snapshot(repository_lease=repository_lease)
        if not replaced:
            replaced = True
            receipt_path.write_bytes(
                release_subject.canonical_json_bytes({"receipt": "replacement"})
            )
        return observed

    monkeypatch.setattr(
        release_subject, "_git_snapshot", replace_after_git_snapshot
    )

    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.generate_production_subject()

    assert error.value.code == "RELEASE_SUBJECT_EVIDENCE_DRIFT"


def test_source_digest_does_not_write_bytecode_to_authoritative_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)

    assert release_subject.source_tree_digest(repository_root)
    assert not tuple(repository_root.rglob("__pycache__"))


def test_source_digest_fails_closed_when_guard_changes_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, _evidence_root = _write_authoritative_repository(tmp_path)
    guard_path = (
        repository_root
        / "skills"
        / "orchestrator"
        / "scripts"
        / "gwo_v8"
        / "cutover_guard.py"
    )
    marker = tmp_path / "replacement-executed.txt"
    replacement = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        "def source_tree_digest(root, *, root_handle=None):\n"
        "    return 'f' * 64\n"
    ).encode("utf-8")
    original_compile = builtins.compile
    replaced = False

    def replace_after_validation(
        source: object,
        filename: object,
        mode: str,
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal replaced
        if not replaced and Path(str(filename)).resolve() == guard_path.resolve():
            guard_path.write_bytes(replacement)
            replaced = True
        return original_compile(source, filename, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "compile", replace_after_validation)

    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.source_tree_digest(repository_root)

    assert error.value.code == "RELEASE_SUBJECT_SOURCE_UNAVAILABLE"
    assert replaced
    assert not marker.exists()


def test_source_digest_rejects_file_replaced_after_discovery_before_held_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, _evidence_root = _write_authoritative_repository(tmp_path)
    package_root = (
        repository_root
        / "skills"
        / "orchestrator"
        / "scripts"
        / "gwo_v8"
    )
    guard_path = package_root / "cutover_guard.py"
    marker = tmp_path / "replaced-file-executed.txt"
    replacement = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        "def source_tree_digest(root, *, root_handle=None):\n"
        "    return 'f' * 64\n"
    ).encode("utf-8")
    original_open_path_handle = release_subject._open_path_handle
    replaced = False

    def replace_after_discovery_before_open(
        path: Path,
        code: str,
        **kwargs: object,
    ):
        nonlocal replaced
        if not replaced and not kwargs.get("directory") and Path(path).name == guard_path.name:
            replacement_path = package_root / "cutover_guard.replacement"
            replacement_path.write_bytes(replacement)
            os.replace(replacement_path, guard_path)
            replaced = True
        return original_open_path_handle(path, code, **kwargs)

    monkeypatch.setattr(
        release_subject, "_open_path_handle", replace_after_discovery_before_open
    )

    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.source_tree_digest(repository_root)

    assert error.value.code == "RELEASE_SUBJECT_SOURCE_UNAVAILABLE"
    assert replaced
    assert not marker.exists()


def test_source_digest_rejects_directory_replaced_after_discovery_before_held_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, _evidence_root = _write_authoritative_repository(tmp_path)
    package_root = (
        repository_root
        / "skills"
        / "orchestrator"
        / "scripts"
        / "gwo_v8"
    )
    replacement_package = tmp_path / "replacement-package"
    shutil.copytree(package_root, replacement_package)
    marker = tmp_path / "replaced-directory-executed.txt"
    replacement_guard = replacement_package / "cutover_guard.py"
    replacement_guard.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        "def source_tree_digest(root, *, root_handle=None):\n"
        "    return 'f' * 64\n",
        encoding="utf-8",
    )
    original_open_path_handle = release_subject._open_path_handle
    replaced = False

    def replace_after_discovery_before_open(
        path: Path,
        code: str,
        **kwargs: object,
    ):
        nonlocal replaced
        if not replaced and kwargs.get("directory") and Path(path).name == package_root.name:
            original_package = tmp_path / "original-package"
            package_root.rename(original_package)
            replacement_package.rename(package_root)
            replaced = True
        return original_open_path_handle(path, code, **kwargs)

    monkeypatch.setattr(
        release_subject, "_open_path_handle", replace_after_discovery_before_open
    )

    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.source_tree_digest(repository_root)

    assert error.value.code == "RELEASE_SUBJECT_SOURCE_UNAVAILABLE"
    assert replaced
    assert not marker.exists()


def test_source_digest_never_uses_planted_pyc_or_source_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, _evidence_root = _write_authoritative_repository(tmp_path)
    guard_path = (
        repository_root
        / "skills"
        / "orchestrator"
        / "scripts"
        / "gwo_v8"
        / "cutover_guard.py"
    )
    marker = tmp_path / "forged-pyc-executed.txt"
    forged = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        "def source_tree_digest(root, *, root_handle=None):\n"
        "    return 'f' * 64\n"
    )
    source_stat = guard_path.stat()
    cache_path = Path(importlib.util.cache_from_source(str(guard_path)))
    cache_path.parent.mkdir()
    cache_path.write_bytes(
        importlib._bootstrap_external._code_to_timestamp_pyc(
            compile(forged, str(guard_path), "exec"),
            int(source_stat.st_mtime),
            source_stat.st_size,
        )
    )
    planted_cache = cache_path.read_bytes()
    original_exec_module = importlib.machinery.SourceFileLoader.exec_module
    loader_calls = 0

    def reject_source_loader(self: object, module: ModuleType) -> None:
        nonlocal loader_calls
        loader_calls += 1
        return original_exec_module(self, module)

    monkeypatch.setattr(
        importlib.machinery.SourceFileLoader,
        "exec_module",
        reject_source_loader,
    )

    observed = release_subject.source_tree_digest(repository_root)

    assert observed != "f" * 64
    assert not marker.exists()
    assert loader_calls == 0
    assert cache_path.read_bytes() == planted_cache
    assert tuple(repository_root.rglob("__pycache__")) == (cache_path.parent,)


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


@pytest.mark.parametrize("dependency", ("_source_snapshot", "runtime_gateway"))
def test_source_digest_rejects_preloaded_shadow_alias_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dependency: str,
):
    repository_root, _evidence_root = _write_authoritative_repository(tmp_path)
    scripts_root = repository_root / "skills" / "orchestrator" / "scripts"
    alias = "_gwo_v8_release_subject_" + hashlib.sha256(
        str(scripts_root).encode("utf-8")
    ).hexdigest()[:16]
    shadow_path = tmp_path / "shadow" / f"{dependency}.py"
    shadow_path.parent.mkdir()
    shadow_path.write_text("# shadow dependency\n", encoding="utf-8")
    shadow_module = ModuleType(f"{alias}.{dependency}")
    shadow_module.__file__ = str(shadow_path)
    shadow_module.__spec__ = SimpleNamespace(origin=str(shadow_path))

    if dependency == "_source_snapshot":

        class ShadowSnapshot:
            @classmethod
            def capture(cls, *_args: object, **_kwargs: object):
                return cls()

            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> bool:
                return False

            def digest(self) -> str:
                return "f" * 64

        shadow_module.HeldSourceSnapshot = ShadowSnapshot
        shadow_module.SourceSnapshotError = type(
            "ShadowSourceSnapshotError", (Exception,), {}
        )
    else:
        shadow_module.RuntimeConfiguration = type("RuntimeConfiguration", (), {})
        shadow_module.RuntimeSelector = type("RuntimeSelector", (), {})
        shadow_module._runtime_configuration_canonical = lambda _value: {}

    monkeypatch.setitem(sys.modules, f"{alias}.{dependency}", shadow_module)

    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.source_tree_digest(repository_root)

    assert error.value.code == "RELEASE_SUBJECT_SOURCE_UNAVAILABLE"


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor identity contract")
def test_source_digest_rejects_a_root_handle_for_a_different_tree_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, _evidence_root = _write_authoritative_repository(tmp_path)
    wrong_root = tmp_path / "wrong-root"
    shutil.copytree(repository_root / "skills", wrong_root / "skills")
    marker = tmp_path / "wrong-root-executed.txt"
    guard_path = (
        wrong_root
        / "skills"
        / "orchestrator"
        / "scripts"
        / "gwo_v8"
        / "cutover_guard.py"
    )
    guard_path.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
        + guard_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    root_handle = os.open(wrong_root, os.O_RDONLY)
    try:
        with pytest.raises(release_subject.ReleaseSubjectError) as error:
            release_subject.source_tree_digest(repository_root, root_handle=root_handle)
    finally:
        os.close(root_handle)

    assert error.value.code == "RELEASE_SUBJECT_SOURCE_UNAVAILABLE"
    assert not marker.exists()


def test_source_digest_does_not_close_caller_owned_root_handle(tmp_path: Path):
    repository_root, _evidence_root = _write_authoritative_repository(tmp_path)
    root_handle = release_subject._open_path_handle(
        repository_root,
        "RELEASE_SUBJECT_SOURCE_UNAVAILABLE",
        directory=True,
    )
    try:
        assert release_subject.source_tree_digest(
            repository_root,
            root_handle=root_handle,
        )
        os.fstat(root_handle)
    finally:
        os.close(root_handle)


def test_source_digest_serializes_process_global_import_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root, _evidence_root = _write_authoritative_repository(tmp_path)
    guard_path = (
        repository_root
        / "skills"
        / "orchestrator"
        / "scripts"
        / "gwo_v8"
        / "cutover_guard.py"
    ).resolve()
    original_compile = builtins.compile
    first_guard_compile = threading.Event()
    release_first_compile = threading.Event()
    second_guard_compile = threading.Event()
    compile_lock = threading.Lock()
    guard_compile_count = 0

    def controlled_compile(
        source: object,
        filename: object,
        mode: str,
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal guard_compile_count
        if Path(str(filename)).resolve() == guard_path:
            with compile_lock:
                guard_compile_count += 1
                ordinal = guard_compile_count
            if ordinal == 1:
                first_guard_compile.set()
                assert release_first_compile.wait(timeout=5)
            elif ordinal == 2:
                second_guard_compile.set()
        return original_compile(source, filename, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "compile", controlled_compile)
    results: list[str] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(release_subject.source_tree_digest(repository_root))
        except BaseException as error:  # pragma: no cover - diagnostic capture
            errors.append(error)

    first = threading.Thread(target=run)
    second = threading.Thread(target=run)
    first.start()
    assert first_guard_compile.wait(timeout=5)
    second.start()
    assert not second_guard_compile.wait(timeout=0.5)
    release_first_compile.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert not errors
    assert len(results) == 2


def test_source_digest_rejects_an_alias_dependency_removed_during_execution(
    tmp_path: Path,
):
    repository_root, _evidence_root = _write_authoritative_repository(tmp_path)
    guard_path = (
        repository_root
        / "skills"
        / "orchestrator"
        / "scripts"
        / "gwo_v8"
        / "cutover_guard.py"
    )
    guard_path.write_text(
        guard_path.read_text(encoding="utf-8")
        + "\nimport sys as _gwo_test_sys\n"
        + "_gwo_test_sys.modules.pop(__package__ + '._canonical', None)\n",
        encoding="utf-8",
    )

    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.source_tree_digest(repository_root)

    assert error.value.code == "RELEASE_SUBJECT_SOURCE_UNAVAILABLE"


def test_source_digest_rejects_an_unloader_alias_module_injected_during_execution(
    tmp_path: Path,
):
    repository_root, _evidence_root = _write_authoritative_repository(tmp_path)
    guard_path = (
        repository_root
        / "skills"
        / "orchestrator"
        / "scripts"
        / "gwo_v8"
        / "cutover_guard.py"
    )
    guard_path.write_text(
        guard_path.read_text(encoding="utf-8")
        + "\nimport sys as _gwo_test_sys\n"
        + "from types import ModuleType as _gwo_test_module_type\n"
        + "_gwo_test_sys.modules[__package__ + '.injected'] = _gwo_test_module_type(__package__ + '.injected')\n",
        encoding="utf-8",
    )

    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.source_tree_digest(repository_root)

    assert error.value.code == "RELEASE_SUBJECT_SOURCE_UNAVAILABLE"


def test_source_digest_restores_original_meta_path_object_after_execution(
    tmp_path: Path,
):
    repository_root, _evidence_root = _write_authoritative_repository(tmp_path)
    package_root = (
        repository_root
        / "skills"
        / "orchestrator"
        / "scripts"
        / "gwo_v8"
    )
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "cutover_guard.py").write_text(
        "import sys as _gwo_test_sys\n"
        "def source_tree_digest(root, *, root_handle=None):\n"
        "    _gwo_test_sys.meta_path = []\n"
        "    return 'a' * 64\n",
        encoding="utf-8",
    )
    original_meta_path = sys.meta_path
    original_meta_path_contents = list(sys.meta_path)

    assert release_subject.source_tree_digest(repository_root) == "a" * 64

    assert sys.meta_path is original_meta_path
    assert sys.meta_path == original_meta_path_contents


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX cleanup TOCTOU contract")
def test_generator_does_not_remove_subject_replaced_between_cleanup_check_and_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repository_root, evidence_root = _write_authoritative_repository(tmp_path)
    _patch_fixed_roots(monkeypatch, repository_root, evidence_root)
    subject = release_subject.generate_production_subject()
    subject_path = evidence_root / release_subject.RELEASE_SUBJECT_FILENAME
    replacement = evidence_root / "replacement-subject.json"
    replacement_bytes = b"replacement inserted after cleanup check\n"
    original_unlink = release_subject.os.unlink
    unlink_calls = 0

    def replace_checked_leaf_before_unlink(
        name: object, *, dir_fd: int | None = None
    ) -> None:
        nonlocal unlink_calls
        if Path(name).name == release_subject.RELEASE_SUBJECT_FILENAME and dir_fd is not None:
            unlink_calls += 1
            replacement.write_bytes(replacement_bytes)
            os.replace(replacement, subject_path)
        original_unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr(release_subject.os, "unlink", replace_checked_leaf_before_unlink)

    def fail_loader(*_args: object, **_kwargs: object) -> object:
        raise release_subject.ReleaseSubjectError(
            "RELEASE_SUBJECT_WRITE_FAILED", "test loader failure"
        )

    monkeypatch.setattr(release_subject, "load_release_subject_for_test", fail_loader)
    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject.write_subject_for_test_exclusive(subject, subject_path)

    assert error.value.code == "RELEASE_SUBJECT_WRITE_FAILED"
    assert unlink_calls == 1
    assert subject_path.read_bytes() == replacement_bytes


@pytest.mark.skipif(os.name == "nt", reason="POSIX cleanup special-entry contract")
@pytest.mark.parametrize(
    "replacement_kind",
    ("symlink", "fifo", "directory", "identity-mismatch"),
)
def test_posix_cleanup_restores_special_entry_and_removes_private_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
):
    subject_path = tmp_path / release_subject.RELEASE_SUBJECT_FILENAME
    candidate_bytes = b"candidate subject bytes\n"
    subject_path.write_bytes(candidate_bytes)
    parent_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent = os.open(tmp_path, parent_flags)
    candidate_descriptor = os.open(
        subject_path.name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent,
    )
    original_rename = release_subject.os.rename
    replaced = False
    created_identity = release_subject._windows_handle_identity(
        candidate_descriptor,
        "RELEASE_SUBJECT_WRITE_FAILED",
        directory=False,
    )

    def replace_detached_entry(
        source: object,
        destination: object,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal replaced
        original_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if replaced:
            return
        replaced = True
        assert dst_dir_fd is not None
        os.unlink(subject_path.name, dir_fd=dst_dir_fd)
        if replacement_kind == "symlink":
            os.symlink("replacement-target", subject_path.name, dir_fd=dst_dir_fd)
        elif replacement_kind == "fifo":
            os.mkfifo(subject_path.name, 0o600, dir_fd=dst_dir_fd)
        elif replacement_kind == "directory":
            os.mkdir(subject_path.name, 0o700, dir_fd=dst_dir_fd)
        else:
            replacement_descriptor = os.open(
                subject_path.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=dst_dir_fd,
            )
            try:
                os.write(replacement_descriptor, b"different subject bytes\n")
            finally:
                os.close(replacement_descriptor)

    monkeypatch.setattr(release_subject.os, "rename", replace_detached_entry)
    try:
        release_subject._remove_created_subject_posix(
            subject_path,
            parent,
            created_identity,
            candidate_bytes,
        )
    finally:
        os.close(candidate_descriptor)
        os.close(parent)

    if replacement_kind == "symlink":
        assert subject_path.is_symlink()
        assert os.readlink(subject_path) == "replacement-target"
    elif replacement_kind == "fifo":
        assert stat.S_ISFIFO(os.lstat(subject_path).st_mode)
    elif replacement_kind == "directory":
        assert subject_path.is_dir()
    else:
        assert subject_path.read_bytes() == b"different subject bytes\n"
    assert not list(tmp_path.glob(f".{release_subject.RELEASE_SUBJECT_FILENAME}.cleanup-*"))


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX open-flag contract")
@pytest.mark.parametrize(
    ("missing_flag", "directory"),
    (
        ("O_DIRECTORY", True),
        ("O_NOFOLLOW", True),
        ("O_NOFOLLOW", False),
        ("O_NONBLOCK", False),
    ),
)
def test_release_subject_posix_open_fails_closed_when_required_flag_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_flag: str,
    directory: bool,
):
    path = tmp_path if directory else tmp_path / "source.py"
    if not directory:
        path.write_bytes(b"value = 1\n")

    monkeypatch.delattr(release_subject.os, missing_flag)

    def unexpected_open(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("os.open must not be reached without required flags")

    monkeypatch.setattr(release_subject.os, "open", unexpected_open)

    with pytest.raises(release_subject.ReleaseSubjectError) as error:
        release_subject._open_path_handle(
            path,
            "TEST_REQUIRED_POSIX_FLAG",
            directory=directory,
        )

    assert error.value.code == "TEST_REQUIRED_POSIX_FLAG"


@pytest.mark.skipif(os.name == "nt", reason="POSIX open-flag contract")
@pytest.mark.parametrize("missing_flag", ("O_NOFOLLOW", "O_NONBLOCK"))
def test_subject_absence_probe_fails_closed_without_required_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_flag: str,
):
    parent = os.open(tmp_path, os.O_RDONLY)
    try:
        monkeypatch.delattr(release_subject.os, missing_flag)

        def unexpected_open(*_args: object, **_kwargs: object) -> int:
            raise AssertionError(
                "os.open must not be reached without required subject flags"
            )

        monkeypatch.setattr(release_subject.os, "open", unexpected_open)
        lease = SimpleNamespace(
            handles=(parent,),
            assert_stable=lambda: None,
        )

        with pytest.raises(release_subject.ReleaseSubjectError) as error:
            release_subject._require_subject_absent_held(
                tmp_path / "subject",
                lease,
            )

        assert error.value.code == "RELEASE_SUBJECT_PATH_INVALID"
    finally:
        os.close(parent)


@pytest.mark.skipif(os.name == "nt", reason="POSIX open-flag contract")
@pytest.mark.parametrize("missing_flag", ("O_DIRECTORY", "O_NOFOLLOW"))
def test_cleanup_directory_open_fails_closed_without_required_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_flag: str,
):
    parent = os.open(tmp_path, os.O_RDONLY)
    try:
        monkeypatch.delattr(release_subject.os, missing_flag)

        def unexpected_open(*_args: object, **_kwargs: object) -> int:
            raise AssertionError(
                "os.open must not be reached without cleanup directory flags"
            )

        monkeypatch.setattr(release_subject.os, "open", unexpected_open)

        with pytest.raises(release_subject.ReleaseSubjectError) as error:
            release_subject._open_posix_cleanup_directory(parent)

        assert error.value.code == "RELEASE_SUBJECT_WRITE_FAILED"
    finally:
        os.close(parent)
