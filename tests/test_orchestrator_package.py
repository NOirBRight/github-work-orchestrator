from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from conftest import load_module


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "skills" / "orchestrator"
ENTRY_PACKAGE = ROOT / "skills" / "implement-gwo"


def _load(name: str, path: Path):
    return load_module(name, path, register=True)


def test_quick_validation_and_manifest_are_at_fixed_point():
    quick = _load("quick_validate_v6_test", ROOT / "scripts" / "quick_validate.py")
    sync = _load("sync_orchestrator_v6_test", ROOT / "scripts" / "sync_orchestrator.py")
    assert quick.findings(ROOT) == []
    for package, skill in (
        (PACKAGE, "orchestrator"),
        (ENTRY_PACKAGE, "implement-gwo"),
    ):
        assert sync.manifest_drift(package) == []
        manifest = json.loads(
            (package / ".skill-package.json").read_text(encoding="utf-8")
        )
        assert manifest == {
            "content_sha256": sync.package_digest(package),
            "schema_version": 1,
            "skill": skill,
            "version": "8.0.0",
        }


def test_phase_two_exposes_implement_gwo_and_one_release_alias_only():
    assert not (ROOT / "SKILL.md").exists()
    assert not (ROOT / "skills" / "agile-orchestrator" / "SKILL.md").exists()
    skill_files = sorted((ROOT / "skills").glob("*/SKILL.md"))
    assert skill_files == [ENTRY_PACKAGE / "SKILL.md", PACKAGE / "SKILL.md"]


def test_skill_description_uses_a_strict_yaml_safe_scalar():
    for package in (PACKAGE, ENTRY_PACKAGE):
        lines = (package / "SKILL.md").read_text(encoding="utf-8").splitlines()
        closing_fence = lines.index("---", 1)
        frontmatter = lines[1:closing_fence]
        description_line = next(
            line for line in frontmatter if line.startswith("description: ")
        )
        scalar = description_line.removeprefix("description: ")
        assert scalar.startswith('"')
        assert scalar.endswith('"')


def test_skill_and_templates_keep_lightweight_line_budgets():
    budgets = {
        PACKAGE / "SKILL.md": 220,
        ENTRY_PACKAGE / "SKILL.md": 120,
        PACKAGE / "templates" / "worker-prompt.md": 60,
        PACKAGE / "templates" / "reviewer-prompt.md": 40,
        ROOT / "docs" / "orchestrator-v6-living-design.md": 220,
    }
    for path, limit in budgets.items():
        assert len(path.read_text(encoding="utf-8").splitlines()) <= limit


def test_entry_is_fail_closed_and_runtime_role_profiles_remain_configured():
    entry = (ENTRY_PACKAGE / "SKILL.md").read_text(encoding="utf-8")
    alias = (PACKAGE / "SKILL.md").read_text(encoding="utf-8")
    compact_entry = " ".join(entry.split())
    compact_alias = " ".join(alias.split())
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
        "never fall back to `/implement`",
        "Retry one unchanged Materialization action at most three executions",
        "Do not poll Agents",
        "coordinator_auto",
    ):
        assert required in compact_entry
    assert "compatibility alias" in compact_alias
    assert "$implement-gwo" in compact_alias
    assert "V8.1" in compact_alias


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
        "light": ("kimi-cli", "kimi-code/kimi-for-coding", "on", "yolo"),
        "standard": ("kimi-cli", "kimi-code/kimi-for-coding", "on", "yolo"),
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


def test_goal_entry_waits_without_polling_or_token_based_progress():
    text = (ENTRY_PACKAGE / "SKILL.md").read_text(encoding="utf-8")
    assert "wait on the named condition without an LLM turn" in text
    assert "treat token use as progress" in text
    assert "elapsed time to fail" in text


def test_openai_metadata_is_explicit_and_invocable():
    entry = (ENTRY_PACKAGE / "agents" / "openai.yaml").read_text(encoding="utf-8")
    alias = (PACKAGE / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert 'display_name: "Implement with GWO"' in entry
    assert "$implement-gwo" in entry
    assert "allow_implicit_invocation: false" in entry
    assert 'display_name: "Orchestrator (compatibility alias)"' in alias
    assert "$orchestrator" in alias
    assert "$implement-gwo" in alias
    assert "allow_implicit_invocation: false" in alias


def test_ci_resolves_a_validated_python313_before_creating_its_venv():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    for required in (
        "Get-Command python3.13 -CommandType Application",
        "$env:LOCALAPPDATA",
        "Programs\\Python\\Python313\\python.exe",
        "[System.IO.Path]::IsPathFullyQualified($candidate)",
        "[System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)",
        "-not $seen.Add($candidate)",
        '($version | Out-String).Trim() -ne "3.13"',
        '"GWO_RUNNER_PYTHON=$runnerPython"',
        "& $env:GWO_RUNNER_PYTHON -m venv $venvPath",
    ):
        assert required in workflow
    assert "& python -m venv" not in workflow


def test_v61_parallel_frontier_remains_documented_during_compatibility_release():
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


def test_three_install_surfaces_receive_both_packages_and_cli_smokes(tmp_path):
    sync = _load(
        "sync_orchestrator_v61_install_test",
        ROOT / "scripts" / "sync_orchestrator.py",
    )
    roots = [
        tmp_path / surface / "skills" for surface in (".agents", ".codex", ".claude")
    ]
    for root in roots:
        for package in (PACKAGE, ENTRY_PACKAGE):
            sync.install_atomic(package, root, tmp_path / "backups")
            assert sync.install_drift(package, root) == []

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
