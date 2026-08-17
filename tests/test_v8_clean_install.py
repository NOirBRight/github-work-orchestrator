from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts.verify_v8_ga_release import clean_install_and_smoke
from scripts.verify_v8_ga_release import ReleaseGateError


ROOT = Path(__file__).resolve().parents[1]


def _valid_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(
            ".git",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "*.pyc",
            ".gwo-worktrees",
            ".tmp",
            ".gwo-test-pycache-111-20260728-1",
        ),
    )
    subprocess.run(
        [
            "py",
            "-3.13",
            str(source / "scripts" / "sync_orchestrator.py"),
            "--root",
            str(source),
            "--install",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return source


def test_clean_install_uses_agents_codex_claude_and_public_smoke(tmp_path):
    result = clean_install_and_smoke(_valid_source(tmp_path), tmp_path / "run")

    assert result.surfaces == (".agents", ".codex", ".claude")
    assert result.public_names == ("advance", "inspect", "start")
    assert result.source_checkout_imported is False


def test_clean_install_rejects_stale_existing_package_before_regeneration(tmp_path):
    source = _valid_source(tmp_path)
    run_root = tmp_path / "run"
    installed = run_root / ".agents" / "skills" / "orchestrator"
    installed.parent.mkdir(parents=True)
    shutil.copytree(source / "skills" / "orchestrator", installed)
    manifest = installed / ".skill-package.json"
    manifest.write_text(
        json.dumps(
            {
                "content_sha256": "0" * 64,
                "schema_version": 1,
                "skill": "orchestrator",
                "version": "8.0.0",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReleaseGateError) as error:
        clean_install_and_smoke(source, run_root)

    assert error.value.code == "GA_COMMAND_FAILED"
    assert json.loads(manifest.read_text(encoding="utf-8"))["content_sha256"] == (
        "0" * 64
    )
