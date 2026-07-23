from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from conftest import load_module


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "skills" / "orchestrator"


def _load(name: str, path: Path):
    return load_module(name, path, register=True)


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
        "version": "6.1.0",
    }


def test_only_one_skill_and_no_compatibility_entry_remain():
    assert not (ROOT / "SKILL.md").exists()
    assert not (ROOT / "skills" / "agile-orchestrator" / "SKILL.md").exists()
    skill_files = sorted((ROOT / "skills").glob("*/SKILL.md"))
    assert skill_files == [PACKAGE / "SKILL.md"]


def test_skill_description_uses_a_strict_yaml_safe_scalar():
    lines = (PACKAGE / "SKILL.md").read_text(encoding="utf-8").splitlines()
    closing_fence = lines.index("---", 1)
    frontmatter = lines[1:closing_fence]
    description_line = next(
        line for line in frontmatter if line.startswith("description: ")
    )
    scalar = description_line.removeprefix("description: ")

    assert scalar.startswith('"')
    assert scalar.endswith('"')
    assert "repository: preflight" in scalar


def test_skill_and_templates_keep_lightweight_line_budgets():
    budgets = {
        PACKAGE / "SKILL.md": 220,
        PACKAGE / "templates" / "worker-prompt.md": 60,
        PACKAGE / "templates" / "reviewer-prompt.md": 40,
        ROOT / "docs" / "orchestrator-v6-living-design.md": 220,
    }
    for path, limit in budgets.items():
        assert len(path.read_text(encoding="utf-8").splitlines()) <= limit


def test_skill_forbids_old_control_plane_and_documents_role_profiles():
    skill = (PACKAGE / "SKILL.md").read_text(encoding="utf-8")
    compact = " ".join(skill.split())
    config = (PACKAGE / "references" / "runtime-config.md").read_text(encoding="utf-8")
    compact_config = " ".join(config.split())
    for required in (
        '"frontier"',
        '"role_profiles"',
        '"coordinator_auto"',
        '"reviewer_standard"',
        "current manually created Coordinator",
    ):
        assert required in compact_config
    for required in (
        "not a permanent Agent",
        "never holds a long Lease",
        "Do not poll busy Workers",
        "one no-ACK wake",
        "foreign-parent Agent is a manual candidate",
    ):
        assert required in compact


def test_config_example_exposes_the_phase_zero_runtime_profiles():
    config = json.loads(
        (PACKAGE / "templates" / "config.example.json").read_text(encoding="utf-8")
    )

    assert {
        name: (
            binding["provider"],
            binding["settings"]["model"],
            binding["settings"]["thinkingOptionId"],
            binding["settings"]["modeId"],
        )
        for name, binding in config["tiers"].items()
    } == {
        "light": ("kimi-cli", "kimi-code/kimi-for-coding", "high", "yolo"),
        "standard": ("kimi-cli", "kimi-code/kimi-for-coding", "max", "yolo"),
        "heavy": ("kimi-cli", "kimi-code/k3", "high", "yolo"),
        "frontier": ("codex", "gpt-5.6-sol", "xhigh", "full-access"),
    }
    assert {
        name: (
            binding["provider"],
            binding["settings"]["model"],
            binding["settings"]["thinkingOptionId"],
            binding["settings"]["modeId"],
        )
        for name, binding in config["role_profiles"].items()
    } == {
        "coordinator_auto": ("kimi-cli", "kimi-code/k3", "max", "yolo"),
        "reviewer_standard": ("codex", "gpt-5.6-sol", "high", "full-access"),
        "reviewer_strict": ("codex", "gpt-5.6-sol", "max", "full-access"),
        "reviewer_recovery": ("codex", "gpt-5.6-sol", "max", "full-access"),
    }
    assert config["repositories"]["owner/repo"]["role_profiles"] == {}


def test_skill_ends_dispatch_turn_instead_of_polling_workers():
    text = (PACKAGE / "SKILL.md").read_text(encoding="utf-8")
    assert "End the current turn after dispatch or Reviewer creation" in text
    assert "Never sleep, loop, or poll while waiting for an Agent" in text


def test_openai_metadata_is_explicit_and_invocable():
    text = (PACKAGE / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert 'display_name: "Orchestrator"' in text
    assert "$orchestrator" in text
    assert "allow_implicit_invocation: true" in text


def test_v61_parallel_frontier_is_documented_as_one_consistent_model():
    skill = (PACKAGE / "SKILL.md").read_text(encoding="utf-8")
    design = (ROOT / "docs" / "orchestrator-v6-living-design.md").read_text(
        encoding="utf-8"
    )
    context = (ROOT / "docs" / "CONTEXT.md").read_text(encoding="utf-8")

    for required in (
        "frontier scan",
        "frontier admit",
        "Ready Reserve",
        "execution slots",
        "integration WIP",
        "orchestrator:issue:v2",
    ):
        assert required in skill
        assert required in design
    for term in (
        "Candidate Pool",
        "Admission",
        "Ready Reserve",
        "Parallel Width",
        "Conflict Claim",
        "Execution Slot",
        "Integration WIP Limit",
    ):
        assert term in context
    assert (
        ROOT / "docs" / "adr" / "0010-adopt-parallel-frontier-admission.md"
    ).is_file()


def test_three_install_surfaces_are_byte_identical_and_cli_smokes(tmp_path):
    sync = _load(
        "sync_orchestrator_v61_install_test",
        ROOT / "scripts" / "sync_orchestrator.py",
    )
    roots = [
        tmp_path / surface / "skills" for surface in (".agents", ".codex", ".claude")
    ]
    for root in roots:
        sync.install_atomic(PACKAGE, root, tmp_path / "backups")
        assert sync.install_drift(PACKAGE, root) == []

    cli = roots[0] / "orchestrator" / "scripts" / "orch.py"
    result = subprocess.run(
        [
            sys.executable,
            str(cli),
            "reconcile",
            "--repo",
            "owner/repo",
            "--read-only",
            "--snapshot",
            "-",
        ],
        input=json.dumps(
            {
                "repository": "owner/repo",
                "execution_slots": 3,
                "integration_wip_limit": 6,
                "issues": [],
                "closed_issues": [],
            }
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "idle"
    assert payload["actions"] == []
    assert payload["warnings"] == []
