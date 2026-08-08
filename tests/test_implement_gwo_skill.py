import inspect
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8.production_host import (
    ProductionCompositionError,
    ProductionGwoHost,
    ProductionHostConfiguration,
)
from v8_production_test_support import (
    ProductionCompositionHarness,
)


def isolated_beta2_install_arguments(
    *,
    target_path: Path,
    target_isolation_root: Path,
) -> dict[str, object]:
    harness = ProductionCompositionHarness.from_task7_dependencies(
        target_path=target_path.resolve(),
        evidence_dir=(target_isolation_root / "skill-admission").resolve(),
        provider_command="recording-provider --no-dispatch",
    )
    arguments = harness.install_arguments()
    arguments["target_path"] = target_path.resolve()
    arguments["host_configuration"] = ProductionHostConfiguration(
        preview_mode="beta2_isolated_preview",
        target_isolation_root=target_isolation_root.resolve(),
        writer_activation_enabled=False,
    )
    return arguments


def test_implement_gwo_skill_names_only_the_v8_public_path():
    text = Path("skills/implement-gwo/SKILL.md").read_text(encoding="utf-8")
    assert "start(repository, ready_refs, options?)" in text
    assert "advance(campaign_handle, wake_ref?)" in text
    assert "inspect(campaign_handle)" in text
    for module in ("PlanControl", "ExecutionKernel", "RuntimeGateway", "CandidateGate", "BatchIntegrator"):
        assert module in text
    assert "preview_mode=\"beta2_isolated_preview\"" in text
    assert "writer_activation_enabled=False" in text
    assert "reconcile_once" not in text
    assert "GoalDriver" not in text


def test_production_host_has_no_predecessor_driver_import():
    source = inspect.getsource(ProductionGwoHost)
    assert "GoalDriver" not in source
    assert "reconcile_once" not in source
    assert "GitIntegrationBatchAssembler" not in source


def test_normal_repository_is_rejected_by_beta2_install(tmp_path):
    arguments = isolated_beta2_install_arguments(
        target_path=Path("D:/Workstation/github-work-orchestrator"),
        target_isolation_root=tmp_path,
    )
    with pytest.raises(ProductionCompositionError) as raised:
        ProductionGwoHost.install(**arguments)
    assert raised.value.code == "V8_ISOLATED_PREVIEW_REQUIRED"
