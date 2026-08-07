from __future__ import annotations

from pathlib import Path
import sys

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "orchestrator"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from gwo_v8.execution_kernel import CampaignOutcome, CampaignStatus
from gwo_v8.production_effects import ProductionCompositionError
from gwo_v8.production_host import ProductionGwoHost, ProductionHostConfiguration
from v8_production_test_support import (
    planning_host,
    reinstall_production_host,
)


def test_pending_planning_is_not_polled_by_advance_without_a_wake(
    tmp_path,
    planning_host,
):
    handle = planning_host.start("owner/repository", ("issue:108",))
    before = planning_host.planning_gateway_calls()
    outcome = planning_host.advance(handle)
    after = planning_host.planning_gateway_calls()
    assert outcome == CampaignOutcome(
        CampaignStatus.WAIT,
        "PlanningContinuationPending",
    )
    assert after == before


def test_wake_continues_the_same_persisted_planning_action_after_restart(
    tmp_path,
    planning_host,
):
    handle = planning_host.start("owner/repository", ("issue:108",))
    continuation = planning_host.start_host.read_planning_continuation(handle)
    assert continuation is not None
    restarted = reinstall_production_host(tmp_path, planning_host)
    restarted.advance(handle, wake_ref="runtime:planning:41")
    assert restarted.planning_action_ids() == [continuation.stable_action_id]
    assert restarted.planning_pass_count() == 1


def test_pending_planning_inspect_is_read_only(tmp_path, planning_host):
    handle = planning_host.start("owner/repository", ("issue:108",))
    before = planning_host.store_bytes()
    diagnostics = planning_host.inspect(handle)
    assert diagnostics.status is CampaignStatus.WAIT
    assert diagnostics.reason == "PlanningContinuationPending"
    assert diagnostics.work_runs == ()
    assert planning_host.store_bytes() == before


def test_normal_real_repository_stays_on_v61_authority(tmp_path, planning_host):
    arguments = planning_host.install_arguments()
    arguments["host_configuration"] = ProductionHostConfiguration(
        target_isolation_root=tmp_path,
        writer_activation_enabled=False,
    )
    arguments["target_path"] = Path("D:/Workstation/github-work-orchestrator")
    with pytest.raises(ProductionCompositionError) as raised:
        ProductionGwoHost.install(**arguments)
    assert raised.value.code == "V8_ISOLATED_PREVIEW_REQUIRED"
