from __future__ import annotations

import pytest

pytest_plugins = ("v8_candidate_assurance_test_support",)

from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.execution_kernel import ExecutionKernel, ExecutionKernelError
from v8_candidate_assurance_test_support import (
    read_kernel_state,
    write_kernel_state,
)


def test_kernel_persists_exact_candidate_receipt_at_run_root(
    kernel_with_candidate_receipt,
):
    kernel, effects, campaign, receipt = kernel_with_candidate_receipt
    assert effects.executed
    assert kernel.read_candidate_receipt(campaign, "issue:114") == receipt
    state = read_kernel_state(kernel, campaign)
    assert state["runs"]["issue:114"]["candidate_receipt"] == receipt.canonical()
    assert state["runs"]["issue:114"]["candidate_receipt"]["candidate_tree_oid"] == (
        receipt.candidate_tree_oid
    )


def test_kernel_receipt_readback_is_read_only_and_sorted(
    kernel_with_candidate_receipt,
):
    kernel, _effects, campaign, receipt = kernel_with_candidate_receipt
    before = kernel._store_path.read_bytes()
    assert kernel.read_candidate_receipts(campaign) == (("issue:114", receipt),)
    assert kernel._store_path.read_bytes() == before


def test_kernel_receipt_readback_survives_restart(kernel_with_candidate_receipt):
    kernel, effects, campaign, receipt = kernel_with_candidate_receipt
    restarted = ExecutionKernel(
        store_path=kernel._store_path,
        plan_control=kernel._plan_control,
        effects=effects,
    )
    assert restarted.read_candidate_receipt(campaign, "issue:114") == receipt


def test_kernel_rejects_corrupt_candidate_receipt_at_direct_run_path(
    kernel_with_candidate_receipt,
):
    kernel, _effects, campaign, _receipt = kernel_with_candidate_receipt
    state = read_kernel_state(kernel, campaign)
    state["runs"]["issue:114"]["candidate_receipt"]["candidate_tree_oid"] = "f" * 40
    write_kernel_state(kernel, campaign, state)
    with pytest.raises(ExecutionKernelError) as raised:
        kernel.read_candidate_receipt(campaign, "issue:114")
    assert raised.value.code == "EXECUTION_STORE_INVALID"


def test_kernel_fixture_keeps_effects_executed_inspectable(
    kernel_with_candidate_receipt,
):
    _kernel, effects, _campaign, _receipt = kernel_with_candidate_receipt
    assert len(effects.executed) == 1
