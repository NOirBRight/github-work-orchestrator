from __future__ import annotations

import json
import re
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


def test_quick_validation_ignores_brackets_and_parentheses_in_fenced_code():
    quick = _load(
        "quick_validate_fenced_code_test", ROOT / "scripts" / "quick_validate.py"
    )
    findings = quick.findings(ROOT)
    assert not any(
        finding.startswith("broken link:")
        and "Measure-Object Length -Sum" in finding
        for finding in findings
    )


def test_phase_two_exposes_implement_gwo_and_one_release_alias_only():
    assert not (ROOT / "SKILL.md").exists()
    assert not (ROOT / "skills" / "agile-orchestrator" / "SKILL.md").exists()
    skill_files = sorted((ROOT / "skills").glob("*/SKILL.md"))
    assert skill_files == [ENTRY_PACKAGE / "SKILL.md", PACKAGE / "SKILL.md"]


def test_v8_package_root_and_cutover_cli_surfaces_are_closed():
    package_root = (PACKAGE / "scripts" / "gwo_v8" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert '__all__ = ("advance", "inspect", "start")' in package_root
    for forbidden in (
        "ImplementGwoEntry",
        "ImplementGwoLauncher",
        "GoalDriver",
        "Kernel",
        "StoreReconstructor",
        "WriterCutoverController",
        "LegacyWriterControl",
        "V8OwnershipControl",
    ):
        assert forbidden not in package_root

    cli_path = ROOT / "scripts" / "cutover_guard.py"
    compile(cli_path.read_text(encoding="utf-8"), str(cli_path), "exec")


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


def test_repository_acceptance_is_local_only():
    workflows = ROOT / ".github" / "workflows"
    workflow_files = sorted(
        path for pattern in ("*.yml", "*.yaml") for path in workflows.glob(pattern)
    )

    assert workflow_files == []


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


def test_v8_release_train_names_exact_gates():
    text = (ROOT / "docs" / "releases" / "gwo-v8-release-train.md").read_text(
        "utf-8"
    )
    for required in (
        "v8.0.0-beta.1",
        "v8.0.0-beta.2",
        "v8.0.0-beta.3",
        "v8.0.0",
        "#113",
        "#114",
        "#115",
        "#116",
        "#117",
        "#118",
        "#119",
        "#123",
        "#136",
        "#137",
        "no production admission",
        "root Canary acceptance readback",
    ):
        assert required in text


def test_beta1_tracker_and_publication_gates_require_pending_owner_readback():
    note = (ROOT / "docs" / "releases" / "v8.0.0-beta.1.md").read_text("utf-8")
    train = (ROOT / "docs" / "releases" / "gwo-v8-release-train.md").read_text(
        "utf-8"
    )
    compact_note = " ".join(note.split())
    publication = " ".join(
        train.split("## Immutable tags and publication boundary", 1)[1].split()
    ).casefold()

    assert "pending explicit owner approval/readback" in compact_note
    assert "owner-approved #137" not in compact_note
    assert "same explicit owner approval/readback gate" in publication
    assert "before any remote publication or mutation" in publication
    assert "this lane does not perform it" in publication


def test_release_train_blocker_graph_contains_native_prerequisite_edges():
    text = (ROOT / "docs" / "releases" / "gwo-v8-release-train.md").read_text(
        "utf-8"
    )
    for required_edge in (
        'T108["#108 Contract"] --> T111["#111 RuntimeGateway"]',
        'T126["#126 CI headroom"] --> T111',
        'T111 --> T109["#109 PlanControl"]',
        'T109 --> T110["#110 ExecutionKernel"]',
        'T111 --> T112["#112 Runtime recovery"]',
        'T136 --> T118',
        'T137 --> T118',
        'T118 --> T119["#119 root Canary"]',
        'T123["#123 Canary prerequisite"] --> T119',
    ):
        assert required_edge in text


def test_beta1_release_contract_has_structured_local_evidence_v2_issue_and_nongoal():
    note = (ROOT / "docs" / "releases" / "v8.0.0-beta.1.md").read_text("utf-8")
    blocks = re.findall(r"```json\n(\{.*?\})\n```", note, re.DOTALL)
    assert len(blocks) == 1
    evidence = json.loads(blocks[0])
    assert set(evidence) == {
        "schema",
        "verification_mode",
        "core_baseline_sha",
        "core_baseline_tree",
        "python_version",
        "requirements_sha256",
        "local_verification_manifest_sha256",
        "main_attestation_sha256",
        "full_pytest_summary",
        "issues",
        "non_goal",
    }
    assert evidence["schema"] == "gwo-beta1-release-evidence.v2"
    assert evidence["verification_mode"] == "local-only"
    assert evidence["core_baseline_sha"] == (
        "2c72d9a153dac07e507c746548258efc44b62875"
    )
    assert evidence["core_baseline_tree"] == (
        "1905079fa3cd0d90dd9b1930ed5dd726fad9f114"
    )
    assert evidence["python_version"] == "Python 3.13.11"
    assert evidence["requirements_sha256"] == (
        "ee3c9f14db38950f5869759a5a94347197c9d4db3f138147b614ad6c4d862534"
    )
    assert evidence["local_verification_manifest_sha256"] == (
        "1f01205bc9846bebfd8e767744a60d4d1e4c185f081f6083606047cd37e9d4a3"
    )
    assert evidence["main_attestation_sha256"] == (
        "689ccbdf84667d9931b83f18b4234816a853ca61ba6cca8382117f2179e15818"
    )
    assert evidence["full_pytest_summary"] == "1521 passed in 1987.16s (0:33:07)"
    assert re.fullmatch(r"[0-9a-f]{40}", evidence["core_baseline_sha"])
    assert re.fullmatch(r"[0-9a-f]{40}", evidence["core_baseline_tree"])
    baseline_tree = subprocess.run(
        ["git", "rev-parse", f"{evidence['core_baseline_sha']}^{{tree}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert baseline_tree == evidence["core_baseline_tree"]
    for key in (
        "requirements_sha256",
        "local_verification_manifest_sha256",
        "main_attestation_sha256",
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", evidence[key])
    assert "ci_url" not in evidence
    assert "dynamic_pass_summary" not in evidence
    assert '"ci_url"' not in note
    assert '"dynamic_pass_summary"' not in note
    compact_note = " ".join(note.split()).casefold()
    assert (
        "repository release acceptance is local verification only"
        in compact_note
    )
    assert "successful main ci readback" not in compact_note
    assert "dynamic main-ci" not in compact_note
    assert "GWO product Hosted CI".casefold() in compact_note
    assert evidence["issues"] == {
        "113": "OPEN",
        "114": "OPEN",
        "115": "OPEN",
        "116": "OPEN",
        "117": "OPEN",
        "118": "OPEN",
        "119": "OPEN",
    }
    assert evidence["non_goal"] == "Lean V8 production cutover"


def test_release_gates_separate_repository_acceptance_from_gwo_product_hosted_ci():
    note = (ROOT / "docs" / "releases" / "v8.0.0-beta.1.md").read_text("utf-8")
    train = (ROOT / "docs" / "releases" / "gwo-v8-release-train.md").read_text(
        "utf-8"
    )
    roadmap = (ROOT / "docs" / "design" / "gwo-v8-lean-roadmap.md").read_text(
        "utf-8"
    )
    program = (
        ROOT / "docs" / "superpowers" / "plans" / "2026-08-04-gwo-v8-ga-release-program.md"
    ).read_text("utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text("utf-8")

    compact_note = " ".join(note.split()).casefold()
    assert "repository release acceptance is local verification only" in compact_note
    assert "exact sha/tree" in compact_note
    assert "before the immutable prerelease tag" in compact_note
    assert "GWO product Hosted CI".casefold() in compact_note
    assert "repository release acceptance" in " ".join(train.split()).casefold()
    assert "GWO product Hosted CI" in train
    assert "GWO product Hosted CI" in roadmap
    assert "GWO product Hosted CI" in program
    assert "through pull requests" in contributing
    assert "GitHub Actions acceptance is disabled" in contributing
    assert "repository release acceptance is local verification only" in (
        " ".join(contributing.split()).casefold()
    )
    assert "Python 3.13" in contributing
    assert "--require-hashes" in contributing
    assert ".github/requirements-ci-win-py313.txt" in contributing


def test_beta1_requires_structured_workspace_convergence_receipt():
    import hashlib
    import os
    import re
    from pathlib import PurePosixPath, PureWindowsPath

    receipt_path = ROOT / "docs" / "releases" / "gwo-v8-workspace-convergence.md"
    release_train = (ROOT / "docs" / "releases" / "gwo-v8-release-train.md").read_text("utf-8")
    assert "Workspace Convergence Gate" in release_train
    assert "gwo-v8-workspace-convergence.md" in release_train

    text = receipt_path.read_text("utf-8")
    blocks = re.findall(r"```json\n(\{.*?\})\n```", text, re.DOTALL)
    assert len(blocks) == 1
    receipt = json.loads(blocks[0])
    assert set(receipt) == {
        "schema",
        "source_sha",
        "protected_remote_ref",
        "protected_remote_sha",
        "kept_worktrees",
        "removed_worktree_count",
        "removed_test_root_count",
        "retained_green_runs",
        "refs_deleted",
        "archive_manifest_sha256",
        "pre_clean_bundle_sha256",
        "post_clean_bundle_sha256",
        "evidence",
        "completed_at",
    }
    assert receipt["schema"] == "gwo-workspace-convergence.v1"
    assert receipt["source_sha"] == "e58c596998df90e65349bdb4b5f25d3d9dc1f7e2"
    assert receipt["protected_remote_ref"] == "refs/heads/codex/gwo-v8-ga-plan"
    assert re.fullmatch(r"[0-9a-f]{40}", receipt["protected_remote_sha"])
    assert receipt["kept_worktrees"] == ["canonical-main", "active-ga"]
    assert receipt["removed_worktree_count"] == 36
    assert receipt["removed_test_root_count"] == 48
    assert receipt["retained_green_runs"] == [
        "gwo-109-r14-full-run1",
        "gwo-109-r13-full-run3",
        "gwo-109-round7-full-final-race",
        "gwo-109-r12-full-synced",
    ]
    assert receipt["refs_deleted"] is False
    for key in (
        "archive_manifest_sha256",
        "pre_clean_bundle_sha256",
        "post_clean_bundle_sha256",
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", receipt[key])
    evidence = receipt["evidence"]
    assert set(evidence) == {
        "manifest",
        "pre_clean_bundle",
        "post_clean_bundle",
        "remote_ga_readback",
    }
    assert evidence["manifest"] == {
        "path": "convergence-manifest.json",
        "sha256": receipt["archive_manifest_sha256"],
    }
    assert evidence["pre_clean_bundle"] == {
        "path": "pre-clean.bundle",
        "sha256": receipt["pre_clean_bundle_sha256"],
    }
    assert evidence["post_clean_bundle"] == {
        "path": "post-clean.bundle",
        "sha256": receipt["post_clean_bundle_sha256"],
    }
    assert evidence["remote_ga_readback"] == {
        "path": "inventory/remote-ga-ref-after.txt",
        "ref": receipt["protected_remote_ref"],
        "sha256": receipt["protected_remote_sha"],
    }

    def relative_path(value):
        assert not Path(value).is_absolute()
        assert not PureWindowsPath(value).is_absolute()
        relative = PurePosixPath(value)
        assert ".." not in relative.parts
        return Path(*relative.parts)

    for item in evidence.values():
        relative_path(item["path"])

    archive_root_value = os.environ.get("GWO_CONVERGENCE_ARCHIVE_ROOT", "").strip()
    assert archive_root_value, "GWO_CONVERGENCE_ARCHIVE_ROOT is required"
    archive_root = Path(archive_root_value)
    assert archive_root.is_dir(), archive_root
    manifest_path = archive_root / relative_path(evidence["manifest"]["path"])
    assert manifest_path.is_file(), manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == receipt["archive_manifest_sha256"]
    assert manifest["source_sha"] == receipt["source_sha"]
    assert manifest["protected_remote_ref"] == receipt["protected_remote_ref"]
    assert manifest["protected_remote_sha"] == receipt["protected_remote_sha"]
    assert manifest["pre_clean_bundle_sha256"] == receipt["pre_clean_bundle_sha256"]
    assert manifest["post_clean_bundle_sha256"] == receipt["post_clean_bundle_sha256"]
    for receipt_key, evidence_key in (
        ("pre_clean_bundle_sha256", "pre_clean_bundle"),
        ("post_clean_bundle_sha256", "post_clean_bundle"),
    ):
        bundle_path = archive_root / relative_path(evidence[evidence_key]["path"])
        assert bundle_path.is_file(), bundle_path
        assert hashlib.sha256(bundle_path.read_bytes()).hexdigest() == receipt[receipt_key]
    readback_path = archive_root / relative_path(evidence["remote_ga_readback"]["path"])
    assert readback_path.is_file(), readback_path
    readback_lines = readback_path.read_text(encoding="utf-8").splitlines()
    assert len(readback_lines) == 1
    readback_parts = readback_lines[0].split()
    assert len(readback_parts) == 2
    readback_sha, readback_ref = readback_parts
    assert readback_sha == receipt["protected_remote_sha"]
    assert readback_ref == receipt["protected_remote_ref"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T.+Z", receipt["completed_at"])
