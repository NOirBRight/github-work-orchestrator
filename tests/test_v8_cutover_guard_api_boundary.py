import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for scripts_path in (ROOT / "scripts", ROOT / "skills" / "orchestrator" / "scripts"):
    if str(scripts_path) not in sys.path:
        sys.path.insert(0, str(scripts_path))

from gwo_v8.cutover_guard import CutoverSubject, ProductionPathScanner, source_tree_digest


def real_static_subject(root: Path, entry_refs: tuple[str, ...]) -> CutoverSubject:
    return CutoverSubject(
        repository="owner/repo",
        control_branch="gwo-control",
        target_branch="main",
        source_writer_generation="v6.1",
        target_writer_generation="v8",
        store_generation="store:v8:0001",
        source_commit="a" * 40,
        source_tree_digest=source_tree_digest(root),
        production_entry_refs=entry_refs,
    )


def test_package_root_exports_only_the_three_public_workflow_operations():
    import gwo_v8

    assert gwo_v8.__all__ == ("advance", "inspect", "start")
    assert callable(gwo_v8.start)
    assert callable(gwo_v8.advance)
    assert callable(gwo_v8.inspect)
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
        assert not hasattr(gwo_v8, forbidden)


def test_v3_public_entrypoints_do_not_import_predecessor_driver_modules():
    audit = ProductionPathScanner(package_root=ROOT).read(
        real_static_subject(
            ROOT,
            (
                "gwo_v8.plan_control_host:ProductionPlanControlStartHost.start",
                "gwo_v8.execution_kernel:advance",
                "gwo_v8.execution_kernel:inspect",
            ),
        ),
    )

    assert audit.reachable_v2_projection_refs == ()
    assert audit.reachable_v3_compatibility_refs == ()
    assert audit.reachable_legacy_writer_refs == ()


def test_skill_surface_cannot_route_to_plancompiler_or_legacy_kernel():
    audit = ProductionPathScanner(package_root=ROOT).read(
        real_static_subject(ROOT, ("skills/implement-gwo/SKILL.md",))
    )

    assert audit.reachable_v2_projection_refs == ()
    assert audit.reachable_legacy_writer_refs == ()


def test_implement_skill_has_no_predecessor_execution_route():
    text = (ROOT / "skills" / "implement-gwo" / "SKILL.md").read_text("utf-8")

    for forbidden in (
        "PlanCompiler",
        "LocalPlanPublication",
        "Kernel.reconcile_once",
        "GoalDriver",
        "Matt `/implement` remains a\nseparate single-ticket workflow",
    ):
        assert forbidden not in text
    for required in (
        "start(repository, ready_refs, options?)",
        "advance(campaign_handle, wake_ref?)",
        "inspect(campaign_handle)",
    ):
        assert required in text
