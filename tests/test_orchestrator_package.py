from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "skills" / "orchestrator"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_quick_validation_and_manifest_are_at_fixed_point():
    quick = _load("quick_validate_v6_test", ROOT / "scripts" / "quick_validate.py")
    sync = _load("sync_orchestrator_v6_test", ROOT / "scripts" / "sync_orchestrator.py")
    assert quick.findings(ROOT) == []
    assert sync.manifest_drift(PACKAGE) == []
    manifest = json.loads((PACKAGE / ".skill-package.json").read_text(encoding="utf-8"))
    assert manifest == {
        "content_sha256": sync.package_digest(PACKAGE),
        "schema_version": 1,
        "skill": "orchestrator",
        "version": "6.0.1",
    }


def test_only_one_skill_and_no_compatibility_entry_remain():
    assert not (ROOT / "SKILL.md").exists()
    assert not (ROOT / "skills" / "agile-orchestrator" / "SKILL.md").exists()
    skill_files = sorted((ROOT / "skills").glob("*/SKILL.md"))
    assert skill_files == [PACKAGE / "SKILL.md"]


def test_skill_and_templates_keep_lightweight_line_budgets():
    budgets = {
        PACKAGE / "SKILL.md": 220,
        PACKAGE / "templates" / "worker-prompt.md": 60,
        PACKAGE / "templates" / "reviewer-prompt.md": 40,
        ROOT / "docs" / "orchestrator-v6-living-design.md": 220,
    }
    for path, limit in budgets.items():
        assert len(path.read_text(encoding="utf-8").splitlines()) <= limit


def test_skill_forbids_old_control_plane_and_coordinator_binding():
    skill = (PACKAGE / "SKILL.md").read_text(encoding="utf-8")
    compact = " ".join(skill.split())
    config = (PACKAGE / "references" / "runtime-config.md").read_text(encoding="utf-8")
    assert "There is deliberately\nno `roles.coordinator` binding" in config
    for required in (
        "not a permanent Agent",
        "never holds a long Lease",
        "Do not poll busy Workers",
        "one no-ACK wake",
        "foreign-parent Agent is a manual candidate",
    ):
        assert required in compact


def test_skill_ends_dispatch_turn_instead_of_polling_workers():
    text = (PACKAGE / "SKILL.md").read_text(encoding="utf-8")
    assert "End the current turn after dispatch or Reviewer creation" in text
    assert "Never sleep, loop, or poll while waiting for an Agent" in text


def test_openai_metadata_is_explicit_and_invocable():
    text = (PACKAGE / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert 'display_name: "Orchestrator"' in text
    assert "$orchestrator" in text
    assert "allow_implicit_invocation: true" in text
