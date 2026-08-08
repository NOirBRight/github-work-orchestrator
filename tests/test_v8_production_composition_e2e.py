from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8.integration_batch import GitIntegrationBatchAssembler
from gwo_v8.production_host import ProductionCompositionError, ProductionGwoHost
from v8_production_test_support import (
    CompositionCrash,
    assert_isolated_e2e_target,
    composition_harness,
    create_temporary_target,
    install_real_provider_composition,
)


def test_real_provider_e2e_refuses_a_non_temporary_target(tmp_path):
    with pytest.raises(ProductionCompositionError) as raised:
        assert_isolated_e2e_target(
            Path("D:/Workstation/github-work-orchestrator"),
            tmp_path,
        )
    assert raised.value.code == "REAL_E2E_TARGET_NOT_ISOLATED"


def test_real_provider_public_path_is_opt_in_and_uses_a_temporary_target(tmp_path):
    if (
        os.environ.get("GWO_V8_REAL_PROVIDER_E2E") != "1"
        or not os.environ.get("GWO_V8_REAL_PROVIDER_COMMAND", "").strip()
    ):
        pytest.skip(
            "real-provider E2E requires GWO_V8_REAL_PROVIDER_E2E=1 "
            "and GWO_V8_REAL_PROVIDER_COMMAND"
        )
    target = create_temporary_target(tmp_path)
    assert_isolated_e2e_target(target, tmp_path)
    harness = install_real_provider_composition(
        target,
        evidence_dir=tmp_path / "evidence",
    )
    handle = harness.host.start(harness.repository, harness.ready_refs)
    harness.host.advance(handle, "real-provider:ready")
    diagnostics = harness.host.inspect(handle)
    assert diagnostics.campaign == handle


def test_watchdog_runtime_wake_calls_the_same_public_advance_path(composition_harness):
    composition_harness.publish_runtime_wake(
        cursor="41",
        stable_action_id="action:109",
    )
    outcomes = composition_harness.host.run_watchdog_once(
        "2026-08-03T10:00:00+00:00"
    )
    assert len(outcomes) == 1
    assert composition_harness.advance_calls == [
        (
            composition_harness.handle,
            "watchdog:runtime:41:action:109",
        )
    ]


def test_install_replaces_an_external_watchdog_advancer_with_the_public_host_path(
    composition_harness,
):
    supplied_advancer = composition_harness.watchdog_advancer

    assert composition_harness.watchdog._advancer is not supplied_advancer

    composition_harness.publish_runtime_wake(
        cursor="42",
        stable_action_id="action:110",
    )
    composition_harness.host.run_watchdog_once(
        "2026-08-03T10:00:00+00:00"
    )

    assert supplied_advancer.calls == []
    assert composition_harness.advance_calls == [
        (
            composition_harness.handle,
            "watchdog:runtime:42:action:110",
        )
    ]


def test_lost_batch_callback_is_recovered_from_next_check_at_after_restart(
    composition_harness,
):
    composition_harness.advance_to_accepted_candidate()
    composition_harness.kill_before_batch_callback()
    restarted = composition_harness.restart()
    restarted.host.run_watchdog_once("2026-08-03T10:01:00+00:00")
    diagnostics = restarted.host.inspect(restarted.handle)
    assert diagnostics.work_runs[0].phase == "completed"
    assert restarted.batch.execute_calls == 1


def test_crash_after_delivery_receipt_does_not_duplicate_target_integration(
    composition_harness,
):
    composition_harness.advance_to_batch_delivery()
    composition_harness.arm_crash("after_batch_terminal_readback")
    with pytest.raises(CompositionCrash):
        composition_harness.host.advance(
            composition_harness.handle,
            "hosted-check:lost",
        )
    restarted = composition_harness.restart()
    restarted.host.advance(restarted.handle, "hosted-check:replay")
    assert restarted.batch.target_integration_calls == 1
    assert (
        restarted.host.inspect(restarted.handle).work_runs[0].result_digest
        is not None
    )


def test_production_host_rejects_predecessor_batch_assembler(composition_harness):
    arguments = composition_harness.install_arguments()
    arguments["batch_integrator"] = GitIntegrationBatchAssembler(
        composition_harness.target
    )
    with pytest.raises(ProductionCompositionError) as raised:
        ProductionGwoHost.install(**arguments)
    assert raised.value.code == "PRODUCTION_PREDECESSOR_PATH_REJECTED"


def test_crash_after_effect_ledger_write_reuses_exact_durable_observation(
    composition_harness,
):
    before = composition_harness.effect_ledger_row_count()
    composition_harness.arm_crash("after_effect_ledger_write")
    with pytest.raises(CompositionCrash) as raised:
        composition_harness.host.advance(
            composition_harness.handle,
            "runtime:effect-ledger-crash",
        )
    assert raised.value.point == "after_effect_ledger_write"
    after_crash = composition_harness.effect_ledger_row_count()
    assert after_crash == before + 1

    restarted = composition_harness.restart()
    restarted.host.advance(
        restarted.handle,
        "runtime:effect-ledger-replay",
    )
    assert restarted.effect_ledger_row_count() == after_crash


def test_crash_after_terminal_batch_readback_reopens_owner_journal_once(
    composition_harness,
):
    composition_harness.advance_to_batch_delivery()
    composition_harness.arm_crash("after_batch_terminal_readback")
    with pytest.raises(CompositionCrash) as raised:
        composition_harness.host.advance(
            composition_harness.handle,
            "hosted-check:terminal-readback-crash",
        )
    assert raised.value.point == "after_batch_terminal_readback"
    assert composition_harness.batch.execute_calls == 1
    assert composition_harness.batch.target_integration_calls == 1

    restarted = composition_harness.restart()
    restarted.host.advance(
        restarted.handle,
        "hosted-check:terminal-readback-replay",
    )
    assert restarted.batch.persisted_observation is not None
    assert restarted.batch.execute_calls == 1
    assert restarted.batch.target_integration_calls == 1
    assert restarted.effect_ledger_row_count() >= 1
