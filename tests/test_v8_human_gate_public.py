from __future__ import annotations

import pytest


pytest_plugins = ("v8_successor_test_support",)


def _human_payload() -> dict[str, object]:
    return {
        "evidence_digests": ["9" * 64],
        "disposition": "require_human_decision",
        "reason": "The requested authority exceeds the frozen Policy Witness.",
        "successor": None,
        "decision": {
            "code": "HUMAN_DECISION_REQUIRED",
            "detail": "A human must approve the broader authority.",
            "required_change": "authority",
        },
    }


def _public_direct_campaign(tmp_path):
    import gwo_v8
    from gwo_v8.execution_kernel import install_execution_kernel
    from gwo_v8.plan_control import _install_start_host
    from v8_successor_test_support import _direct_setup

    control, repository, gateway, _artifacts, _source, host, _created_handle, harness = (
        _direct_setup(_human_payload())
    )
    _install_start_host(host)
    kernel = install_execution_kernel(
        store_path=tmp_path / "human-gate-public.sqlite3",
        plan_control=host,
        effects=harness.effects,
    )
    handle = gwo_v8.start(
        "owner/repository",
        ("issue:108", "issue:109", "issue:110"),
    )
    gwo_v8.advance(handle)
    harness._kernel = kernel
    return control, repository, gateway, host, handle, harness


def _open_named_decision(gwo_v8, handle, harness):
    outcome = gwo_v8.advance(
        handle,
        plan_invalidation=harness.invalidation_for("issue:109"),
    )
    diagnostics = gwo_v8.inspect(handle)
    assert outcome.status.value == "Decision"
    assert diagnostics.human_gate is not None
    assert diagnostics.human_gate.phase == "awaiting_human_choice"
    return diagnostics


def test_public_human_gate_types_remain_in_their_own_module():
    import gwo_v8
    from gwo_v8.human_gate import HumanDecisionChoice, HumanGateSummary

    assert HumanDecisionChoice.__module__ == "gwo_v8.human_gate"
    assert HumanGateSummary.__module__ == "gwo_v8.human_gate"
    assert gwo_v8.__all__ == ("advance", "inspect", "start")
    assert not hasattr(gwo_v8, "HumanDecisionChoice")
    assert not hasattr(gwo_v8, "HumanGateSummary")

    for private_name in (
        "HumanDecisionRecord",
        "HumanSourceReadback",
        "HumanGateAttempt",
        "HumanGatePlanReadback",
        "ReplanBudgetPolicy",
        "HumanApprovalSource",
    ):
        assert private_name not in gwo_v8.__all__
        assert not hasattr(gwo_v8, private_name)


def test_public_advance_without_choice_returns_one_named_decision_and_inspect_gate(
    tmp_path,
):
    import gwo_v8
    from gwo_v8.human_gate import HumanGateSummary

    _control, _repository, gateway, _host, handle, harness = _public_direct_campaign(
        tmp_path
    )

    diagnostics = _open_named_decision(gwo_v8, handle, harness)

    assert diagnostics.human_gate.decision_id.startswith("decision:")
    assert diagnostics.human_gate.required_change == "authority"
    assert isinstance(diagnostics.human_gate, HumanGateSummary)
    assert gateway.replan_progresses == 1


class _PendingSource:
    def __init__(self, result):
        self.result = result
        self.reads = 0

    def read(self, handle, decision, readback_ref):
        self.reads += 1
        assert handle == decision.campaign
        assert readback_ref == "workflow://approval/one"
        return self.result


def test_public_advance_typed_choice_exposes_pending_source_wait(tmp_path):
    import gwo_v8
    from gwo_v8.human_gate import HumanDecisionChoice
    from test_v8_human_gate_plancontrol import _pending_readback

    control, repository, _gateway, _host, handle, harness = _public_direct_campaign(
        tmp_path
    )
    diagnostics = _open_named_decision(gwo_v8, handle, harness)
    decision = repository.read_human_decision(
        handle,
        diagnostics.human_gate.decision_id,
    )
    source = _PendingSource(_pending_readback(decision.decision_id))
    control._human_source = source

    outcome = gwo_v8.advance(
        handle,
        human_decision=HumanDecisionChoice(
            decision_id=decision.decision_id,
            choice="approve",
            readback_ref="workflow://approval/one",
        ),
    )
    readback = gwo_v8.inspect(handle)

    assert outcome.status.value == "Wait"
    assert readback.human_gate is not None
    assert readback.human_gate.phase == "awaiting_durable_tracker_policy_readback"
    assert readback.human_gate.reason_code == "HUMAN_SOURCE_READBACK_PENDING"
    assert source.reads == 1


class _FixedSource:
    def __init__(self, result):
        self.result = result

    def read(self, handle, decision, readback_ref):
        assert handle == decision.campaign
        assert readback_ref == "workflow://approval/one"
        return self.result


def test_public_approved_choice_activates_one_active_successor(tmp_path):
    import gwo_v8
    from gwo_v8.human_gate import HumanDecisionChoice
    from test_v8_human_gate_plancontrol import _approved_readback
    from v8_successor_test_support import successor_payload

    control, repository, gateway, _host, handle, harness = _public_direct_campaign(
        tmp_path
    )
    diagnostics = _open_named_decision(gwo_v8, handle, harness)
    decision = repository.read_human_decision(
        handle,
        diagnostics.human_gate.decision_id,
    )
    control._human_source = _FixedSource(_approved_readback(control, handle, decision))
    gateway.payload = successor_payload(
        dependencies=(
            (
                "issue:109",
                "issue:110",
                "The approved authority permits the successor dependency.",
            ),
        )
    )
    predecessor = gwo_v8.inspect(handle)

    outcome = gwo_v8.advance(
        handle,
        human_decision=HumanDecisionChoice(
            decision.decision_id,
            "approve",
            "workflow://approval/one",
        ),
    )
    successor = gwo_v8.inspect(handle)

    assert outcome.status == successor.status
    assert successor.campaign == handle
    assert successor.plan_revision_digest != predecessor.plan_revision_digest
    assert successor.human_gate is not None
    assert successor.human_gate.phase == "active_successor"
    assert successor.human_gate.successor_revision_limit == 1
    assert successor.human_gate.repeated_invalidation_limit == 1
    assert gateway.replan_progresses == 2
