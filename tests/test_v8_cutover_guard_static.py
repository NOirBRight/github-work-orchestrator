from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for scripts_path in (ROOT / "scripts", ROOT / "skills" / "orchestrator" / "scripts"):
    if str(scripts_path) not in sys.path:
        sys.path.insert(0, str(scripts_path))

from gwo_v8.cutover_guard import (  # noqa: E402
    CutoverGuardError,
    CutoverSubject,
    ProductionPathScanner,
    ReadOnlyPackageValidator,
)


def test_production_path_scanner_reports_a_reachable_predecessor_edge(tmp_path):
    package = tmp_path / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    package.mkdir(parents=True)
    (package / "public.py").write_text(
        "from .legacy import LegacyWriter\n\n"
        "def start():\n    return LegacyWriter().write()\n",
        encoding="utf-8",
    )
    (package / "legacy.py").write_text(
        "class LegacyWriter:\n"
        "    def write(self):\n        return None\n",
        encoding="utf-8",
    )
    subject = scanned_subject(tmp_path, ("gwo_v8.public:start",))

    readback = ProductionPathScanner(package_root=tmp_path).read(subject)

    assert readback.reachable_legacy_writer_refs == (
        "gwo_v8.legacy:LegacyWriter.write",
    )


def test_production_path_scanner_rejects_a_missing_entry_module_fail_closed(tmp_path):
    package = tmp_path / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    package.mkdir(parents=True)
    (package / "public.py").write_text(
        "def start():\n    return None\n",
        encoding="utf-8",
    )
    subject = scanned_subject(tmp_path, ("gwo_v8.missing:start",))

    with pytest.raises(CutoverGuardError) as error:
        ProductionPathScanner(package_root=tmp_path).read(subject)

    assert error.value.code == "CUTOVER_COMPATIBILITY_AUDIT_INVALID"


def test_production_path_scanner_rejects_an_unresolved_alias_edge_fail_closed(tmp_path):
    package = tmp_path / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    package.mkdir(parents=True)
    (package / "public.py").write_text(
        "from .legacy import LegacyWriter as Writer\n\n"
        "def start():\n"
        "    factory = Writer\n"
        "    return factory().write()\n",
        encoding="utf-8",
    )
    (package / "legacy.py").write_text(
        "class LegacyWriter:\n"
        "    def write(self):\n        return None\n",
        encoding="utf-8",
    )
    subject = scanned_subject(tmp_path, ("gwo_v8.public:start",))

    with pytest.raises(CutoverGuardError) as error:
        ProductionPathScanner(package_root=tmp_path).read(subject)

    assert error.value.code == "CUTOVER_COMPATIBILITY_AUDIT_INVALID"


def test_package_validator_detects_manifest_drift_without_rewriting_any_file(tmp_path):
    source = make_two_package_tree(tmp_path / "source")
    installed = make_installed_surfaces(tmp_path)
    manifest = source / "implement-gwo" / ".skill-package.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "8.0.0",
            "8.0.0-drift",
        ),
        encoding="utf-8",
    )
    drifted = manifest.read_bytes()

    readback = ReadOnlyPackageValidator(
        source_root=source,
        install_roots=installed,
    ).read(static_subject(tmp_path, ()))

    assert "source:implement-gwo" in readback.drift
    assert manifest.read_bytes() == drifted
    assert not (source / "implement-gwo" / ".skill-package.json.tmp").exists()


def test_package_validator_reads_all_install_surfaces_and_never_installs(tmp_path, monkeypatch):
    source = make_two_package_tree(tmp_path / "source")
    installed = make_installed_surfaces(tmp_path)
    writes = []
    monkeypatch.setattr(
        "shutil.copytree",
        lambda *args, **kwargs: writes.append("copytree"),
    )
    monkeypatch.setattr(
        "os.replace",
        lambda *args, **kwargs: writes.append("replace"),
    )

    readback = ReadOnlyPackageValidator(
        source_root=source,
        install_roots=installed,
    ).read(static_subject(tmp_path, ()))

    assert readback.drift == ()
    assert {item.package_name for item in readback.installed_packages} == {
        "implement-gwo",
        "orchestrator",
    }
    assert writes == []


def static_subject(root: Path, entry_refs: tuple[str, ...]) -> CutoverSubject:
    del root
    return CutoverSubject(
        repository="owner/repo",
        control_branch="gwo-control",
        target_branch="main",
        source_writer_generation="v6.1",
        target_writer_generation="v8",
        store_generation="store:v8:0001",
        source_commit="a" * 40,
        source_tree_digest="b" * 64,
        production_entry_refs=entry_refs,
    )


def scanned_subject(root: Path, entry_refs: tuple[str, ...]) -> CutoverSubject:
    from dataclasses import replace
    from gwo_v8.cutover_guard import source_tree_digest

    subject = static_subject(root, entry_refs)
    return replace(subject, source_tree_digest=source_tree_digest(root))


def make_two_package_tree(root: Path) -> Path:
    import json
    from scripts.sync_orchestrator import expected_manifest

    root.mkdir(parents=True, exist_ok=True)
    for package in ("implement-gwo", "orchestrator"):
        package_root = root / package
        package_root.mkdir()
        (package_root / "SKILL.md").write_text(
            f"# {package}\n", encoding="utf-8"
        )
        (package_root / ".skill-package.json").write_text(
            json.dumps(expected_manifest(package_root), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    return root


def make_installed_surfaces(root: Path) -> dict[str, Path]:
    surfaces = {
        ".agents": root / ".agents" / "skills",
        ".codex": root / ".codex" / "skills",
        ".claude": root / ".claude" / "skills",
    }
    for path in surfaces.values():
        path.mkdir(parents=True, exist_ok=True)
    source = root / "source"
    if not source.is_dir():
        make_two_package_tree(source)
    for surface in surfaces.values():
        for package in ("implement-gwo", "orchestrator"):
            target = surface / package
            target.mkdir()
            for item in (source / package).iterdir():
                target.joinpath(item.name).write_bytes(item.read_bytes())
    return surfaces
