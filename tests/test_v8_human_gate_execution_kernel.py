from __future__ import annotations

from dataclasses import replace

import pytest

from v8_successor_test_support import _direct_setup
from gwo_v8._canonical import digest_value


def _human_payload():
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


def _install_human_ports(host, control):
    """Expose the read-only source seam without changing shared test support."""

    host.read_human_decision_source = (
        lambda handle, decision, choice: control.read_human_decision_source(
            handle, decision, choice
        )
    )
    host.advance_human_decision = (
        lambda handle, decision, choice: control.advance_human_decision(
            handle, decision, choice
        )
    )


def test_outstanding_human_decision_is_quiescent_and_inspect_exposes_named_gate(
    tmp_path,
):
    control, _repository, gateway, _artifacts, _source, host, handle, harness = _direct_setup(
        _human_payload()
    )
    active = host.read_active(handle)
    kernel = host.install_execution_kernel(
        store_path=tmp_path / "human-gate.sqlite3",
        effects=harness.effects,
    )
    harness._kernel = kernel
    kernel.advance(handle)

    outcome = kernel.advance(
        handle,
        plan_invalidation=harness.invalidation_for("issue:109"),
    )
    diagnostics = kernel.inspect(handle)

    assert diagnostics.human_gate.phase == "awaiting_human_choice"
    assert outcome.status.value == "Decision"
    assert diagnostics.human_gate.required_change == "authority"
    assert diagnostics.human_gate.evidence_digests == ("9" * 64,)
    assert diagnostics.plan_revision_digest == active.current_revision_digest
    assert gateway.replan_progresses == 1
    assert diagnostics.revision_lineage == ()


def test_approval_choice_reads_only_authoritative_source_and_waits_when_pending(
    tmp_path,
):
    from gwo_v8.human_gate import HumanDecisionChoice, HumanSourceReadback

    class PendingSource:
        def __init__(self):
            self.reads = 0

        def read(self, handle, decision, readback_ref):
            self.reads += 1
            return HumanSourceReadback(
                decision_id=decision.decision_id,
                state="pending",
                approval_record_bytes=None,
                tracker_source_bytes=None,
                policy_witness_bytes=None,
                approval_record_digest=None,
                tracker_source_digest=None,
                policy_witness_digest=None,
                source_change_digest=None,
                readback_digest=digest_value(
                    {
                        "decision_id": decision.decision_id,
                        "state": "pending",
                        "approval_record_digest": None,
                        "tracker_source_digest": None,
                        "policy_witness_digest": None,
                        "source_change_digest": None,
                        "code": "HUMAN_SOURCE_READBACK_PENDING",
                    }
                ),
                code="HUMAN_SOURCE_READBACK_PENDING",
            )

    control, _repository, gateway, _artifacts, _source, host, handle, harness = _direct_setup(
        _human_payload()
    )
    source = PendingSource()
    control._human_source = source
    _install_human_ports(host, control)
    kernel = host.install_execution_kernel(
        store_path=tmp_path / "human-gate-pending.sqlite3",
        effects=harness.effects,
    )
    harness._kernel = kernel
    kernel.advance(handle)
    kernel.advance(handle, plan_invalidation=harness.invalidation_for("issue:109"))
    decision_id = kernel.inspect(handle).human_gate.decision_id

    outcome = kernel.advance(
        handle,
        human_decision=HumanDecisionChoice(
            decision_id=decision_id,
            choice="approve",
            readback_ref="workflow://approval/one",
        ),
    )
    diagnostics = kernel.inspect(handle)

    assert outcome.status.value == "Wait"
    assert diagnostics.human_gate.phase == "awaiting_durable_tracker_policy_readback"
    assert diagnostics.human_gate.reason_code == "HUMAN_SOURCE_READBACK_PENDING"
    assert source.reads == 1
    assert gateway.replan_progresses == 1


def test_approved_human_source_reconciles_the_kernel_to_the_new_revision_once(
    tmp_path,
):
    from gwo_v8.human_gate import HumanDecisionChoice
    from test_v8_human_gate_plancontrol import _approved_readback
    from v8_successor_test_support import successor_payload

    control, _repository, gateway, _artifacts, _source, host, handle, harness = _direct_setup(
        _human_payload()
    )
    source_readback_box = {}

    def install_source(decision):
        source_readback_box["readback"] = _approved_readback(
            control,
            handle,
            decision,
        )
        control._human_source = type(
            "ApprovedSource",
            (),
            {"read": lambda _self, _handle, _decision, _ref: source_readback_box["readback"]},
        )()

    host.read_human_decision_source = (
        lambda campaign_handle, decision, choice: (
            install_source(decision),
            control.read_human_decision_source(campaign_handle, decision, choice),
        )[1]
    )
    host.advance_human_decision = (
        lambda campaign_handle, decision, choice: control.advance_human_decision(
            campaign_handle, decision, choice
        )
    )
    host.require_human_decision = control.require_human_decision
    kernel = host.install_execution_kernel(
        store_path=tmp_path / "human-gate-approved.sqlite3",
        effects=harness.effects,
    )
    harness._kernel = kernel
    kernel.advance(handle)
    predecessor = host.read_active(handle)
    kernel.advance(
        handle,
        plan_invalidation=harness.invalidation_for("issue:109"),
    )
    decision_id = kernel.inspect(handle).human_gate.decision_id
    gateway.payload = successor_payload(
        dependencies=(
            (
                "issue:109",
                "issue:110",
                "Approved human scope permits the dependency.",
            ),
        )
    )

    outcome = kernel.advance(
        handle,
        human_decision=HumanDecisionChoice(
            decision_id,
            "approve",
            "workflow://approval/one",
        ),
    )
    diagnostics = kernel.inspect(handle)

    assert outcome.status.value in {"Running", "Wait", "Complete"}
    assert diagnostics.plan_revision_digest != predecessor.current_revision_digest
    assert diagnostics.human_gate.phase == "active_successor"
    assert gateway.replan_progresses == 2


def test_human_successor_intent_is_persisted_before_activation_and_replayed_after_restart(
    tmp_path,
):
    from gwo_v8.execution_kernel import ExecutionKernelError
    from gwo_v8.human_gate import HumanDecisionChoice
    from test_v8_human_gate_plancontrol import _approved_readback
    from v8_successor_test_support import successor_payload

    control, _repository, gateway, _artifacts, _source, host, handle, harness = _direct_setup(
        _human_payload()
    )
    kernel = host.install_execution_kernel(
        store_path=tmp_path / "human-gate-transition.sqlite3",
        effects=harness.effects,
    )
    harness._kernel = kernel
    kernel.advance(handle)
    kernel.advance(
        handle,
        plan_invalidation=harness.invalidation_for("issue:109"),
    )
    decision_id = kernel.inspect(handle).human_gate.decision_id
    choice = HumanDecisionChoice(
        decision_id,
        "approve",
        "workflow://approval/one",
    )
    gateway.payload = successor_payload(
        dependencies=(
            (
                "issue:109",
                "issue:110",
                "Approved human scope permits the dependency.",
            ),
        )
    )
    source_readback = _approved_readback(
        control,
        handle,
        control.require_human_decision(
            handle,
            kernel.inspect(handle).invalidation_classification,
        ),
    )
    control._human_source = type(
        "ApprovedSource",
        (),
        {"read": lambda _self, _handle, _decision, _ref: source_readback},
    )()
    _install_human_ports(host, control)
    original_publish = control._publish_activate_readback

    def crash_before_activation(**_kwargs):
        raise RuntimeError("crash before approved activation")

    control._publish_activate_readback = crash_before_activation
    host.advance_human_decision = (
        lambda campaign_handle, decision, approved_choice: control.advance_human_decision(
            campaign_handle,
            decision,
            approved_choice,
        )
    )

    with pytest.raises(ExecutionKernelError) as error:
        kernel.advance(handle, human_decision=choice)
    assert error.value.code == "HUMAN_SUCCESSOR_ACTIVATION_FAILED"
    predecessor = host.read_active(handle)
    state = kernel._load(handle)
    expected_human_action_id = "replan:human:" + digest_value(
        {
            "decision_id": decision_id,
            "source_readback_digest": source_readback.readback_digest,
            "previous_revision_digest": predecessor.current_revision_digest,
        }
    )[:24]
    assert state["human_successor_transition"]["state"] == "activation_due"
    assert state["human_successor_transition"]["classification_action_id"] == (
        expected_human_action_id
    )
    assert state["human_gate"]["summary"]["phase"] == "planning_validated_successor"
    assert gateway.replan_progresses == 2
    human_attempt = _repository.read_human_gate_attempt(
        handle,
        decision_id,
        source_readback.readback_digest,
    )
    assert human_attempt is not None
    assert human_attempt.state == "planning_validated_successor"
    assert human_attempt.compilation_record_artifact_digest is not None
    assert human_attempt.activation_receipt_digest is None

    # A durable approved transition without its corresponding attempt is not
    # recoverable by reconstructing a new attempt from the source bytes.
    _repository.human_gate_attempts.clear()
    missing_attempt_kernel = host.install_execution_kernel(
        store_path=tmp_path / "human-gate-transition.sqlite3",
        effects=harness.effects,
    )
    with pytest.raises(ExecutionKernelError) as missing_error:
        missing_attempt_kernel.advance(handle)
    assert missing_error.value.code == "HUMAN_GATE_ATTEMPT_READBACK_INVALID"
    _repository.human_gate_attempts[
        (
            handle.repository,
            handle.campaign_key,
            decision_id,
            source_readback.readback_digest,
        )
    ] = human_attempt

    control._publish_activate_readback = original_publish
    host.advance_human_decision = (
        lambda campaign_handle, decision, approved_choice: control.advance_human_decision(
            campaign_handle,
            decision,
            approved_choice,
        )
    )
    restarted = host.install_execution_kernel(
        store_path=tmp_path / "human-gate-transition.sqlite3",
        effects=harness.effects,
    )
    harness._kernel = restarted
    restarted.advance(handle)

    successor = host.read_active(handle)
    assert successor.current_revision_digest != predecessor.current_revision_digest
    assert successor.activation_receipt.planning_stable_action_id == (
        expected_human_action_id
    )
    assert restarted.inspect(handle).human_gate.phase == "active_successor"
    assert gateway.replan_progresses == 2
    restarted.advance(handle)
    assert gateway.replan_progresses == 2


def test_inspect_tolerates_activation_crossing_before_human_transition_finalize(
    tmp_path,
):
    from gwo_v8.execution_kernel import ExecutionKernelError
    from gwo_v8.human_gate import HumanDecisionChoice
    from test_v8_human_gate_plancontrol import _approved_readback
    from v8_successor_test_support import successor_payload

    control, _repository, gateway, _artifacts, _source, host, handle, harness = _direct_setup(
        _human_payload()
    )
    kernel = host.install_execution_kernel(
        store_path=tmp_path / "human-gate-inspect-crossing.sqlite3",
        effects=harness.effects,
    )
    harness._kernel = kernel
    kernel.advance(handle)
    kernel.advance(
        handle,
        plan_invalidation=harness.invalidation_for("issue:109"),
    )
    decision = control.require_human_decision(
        handle,
        kernel.inspect(handle).invalidation_classification,
    )
    choice = HumanDecisionChoice(
        decision.decision_id,
        "approve",
        "workflow://approval/one",
    )
    gateway.payload = successor_payload(
        dependencies=(
            (
                "issue:109",
                "issue:110",
                "Approved human scope permits the dependency.",
            ),
        )
    )
    source_readback = _approved_readback(control, handle, decision)
    control._human_source = type(
        "ApprovedSource",
        (),
        {"read": lambda _self, _handle, _decision, _ref: source_readback},
    )()
    _install_human_ports(host, control)
    predecessor = host.read_active(handle)

    def activate_then_crash(campaign_handle, durable_decision, approved_choice):
        result = control.advance_human_decision(
            campaign_handle,
            durable_decision,
            approved_choice,
        )
        assert result == source_readback
        raise RuntimeError("crash after approved activation")

    host.advance_human_decision = activate_then_crash

    with pytest.raises(ExecutionKernelError) as error:
        kernel.advance(handle, human_decision=choice)
    assert error.value.code == "HUMAN_SUCCESSOR_ACTIVATION_FAILED"
    assert host.read_active(handle).current_revision_digest != predecessor.current_revision_digest

    diagnostics = kernel.inspect(handle)
    assert diagnostics.status.value == "Wait"
    assert diagnostics.human_gate.phase == "planning_validated_successor"
    assert gateway.replan_progresses == 2


def test_replan_budget_is_durable_and_duplicate_evidence_does_not_consume_limit(
    tmp_path,
):
    from gwo_v8._canonical import digest_value, load_canonical_json

    control, _repository, gateway, _artifacts, _source, host, handle, harness = _direct_setup(
        _human_payload()
    )
    kernel = host.install_execution_kernel(
        store_path=tmp_path / "human-gate-budget.sqlite3",
        effects=harness.effects,
    )
    harness._kernel = kernel
    kernel.advance(handle)
    active = host.read_active(handle)
    plan = load_canonical_json(active.plan_spec_bytes)
    state = kernel._load(handle)
    assert state["replan_budgets"] == {
        "policy_witness_digest": plan["policy"]["digest"],
        "successor_revisions_used": 0,
        "successor_revision_limit": 1,
        "invalidation_limit": 1,
        "obligations": {},
    }

    invalidation = harness.invalidation_for("issue:109")
    kernel.advance(handle, plan_invalidation=invalidation)
    decision_id = kernel.inspect(handle).human_gate.decision_id
    run = kernel._load(handle)["runs"]["issue:109"]
    obligation_key = digest_value(
        {
            "ticket_key": invalidation.ticket_key,
            "invalidated_obligation": invalidation.invalidated_obligation,
            "work_subject_digest": run["work_subject_digest"],
        }
    )
    budget = kernel._load(handle)["replan_budgets"]
    assert budget["obligations"][obligation_key]["evidence_digests"] == [
        invalidation.evidence_digest
    ]

    kernel.advance(handle, plan_invalidation=invalidation)
    repeated = kernel.inspect(handle)
    assert repeated.human_gate.decision_id == decision_id
    assert kernel._load(handle)["replan_budgets"] == budget
    assert gateway.replan_progresses == 1

    restarted = host.install_execution_kernel(
        store_path=tmp_path / "human-gate-budget.sqlite3",
        effects=harness.effects,
    )
    harness._kernel = restarted
    restarted.advance(handle)
    assert restarted._load(handle)["replan_budgets"] == budget


def test_repeated_invalidation_exhaustion_is_stable_and_does_not_plan_again(tmp_path):
    from gwo_v8.human_gate import HumanGateSummary

    control, _repository, gateway, _artifacts, _source, host, handle, harness = _direct_setup(
        _human_payload()
    )
    kernel = host.install_execution_kernel(
        store_path=tmp_path / "human-gate-exhaustion.sqlite3",
        effects=harness.effects,
    )
    harness._kernel = kernel
    kernel.advance(handle)
    first = harness.invalidation_for("issue:109")
    kernel.advance(handle, plan_invalidation=first)
    second = replace(
        first,
        report_digest="a" * 64,
        evidence_digest="a" * 64,
        dedup_identity="successor:issue:109:second",
    )

    outcome = kernel.advance(handle, plan_invalidation=second)
    diagnostics = kernel.inspect(handle)
    assert outcome.status.value == "Decision"
    assert outcome.reason == "REPLAN_BUDGET_EXHAUSTED"
    assert diagnostics.human_gate.phase == "budget_exhausted"
    assert diagnostics.human_gate.required_change == "replan_budget"
    assert isinstance(HumanGateSummary.from_canonical(kernel._load(handle)["human_gate"]["summary"]), HumanGateSummary)
    decision_id = diagnostics.human_gate.decision_id
    assert gateway.replan_progresses == 1

    kernel.advance(handle, plan_invalidation=second)
    again = kernel.inspect(handle)
    assert again.human_gate.decision_id == decision_id
    assert gateway.replan_progresses == 1


def test_repeated_invalidation_exhaustion_is_durable_before_classification_readback(
    tmp_path,
):
    """A crash/restart before classification must still close the budget boundary."""

    control, _repository, gateway, _artifacts, _source, host, handle, harness = _direct_setup()
    classification_calls = []

    def lose_classification_before_durable_readback(
        campaign_handle, invalidations, execution_snapshot
    ):
        classification_calls.append(
            (campaign_handle, tuple(item.evidence_digest for item in invalidations))
        )
        return None

    host.classify_plan_invalidations = lose_classification_before_durable_readback
    kernel = host.install_execution_kernel(
        store_path=tmp_path / "human-gate-budget-before-classification.sqlite3",
        effects=harness.effects,
    )
    harness._kernel = kernel
    kernel.advance(handle)

    first = harness.invalidation_for("issue:109")
    kernel.advance(handle, plan_invalidation=first)
    second = replace(
        first,
        report_digest="a" * 64,
        evidence_digest="a" * 64,
        dedup_identity="successor:issue:109:second",
    )

    outcome = kernel.advance(handle, plan_invalidation=second)
    state = kernel._load(handle)
    diagnostics = kernel.inspect(handle)

    assert outcome.status.value == "Decision"
    assert outcome.reason == "REPLAN_BUDGET_EXHAUSTED"
    assert diagnostics.human_gate.phase == "budget_exhausted"
    assert diagnostics.invalidation_classification is None
    assert classification_calls == [(handle, ("9" * 64,))]
    assert {record["evidence_digest"] for record in state["plan_invalidation"].values()} == {
        "9" * 64,
        "a" * 64,
    }
    assert diagnostics.human_gate.evidence_digests == ("9" * 64, "a" * 64)
    assert gateway.replan_progresses == 0

    restarted = host.install_execution_kernel(
        store_path=tmp_path / "human-gate-budget-before-classification.sqlite3",
        effects=harness.effects,
    )
    harness._kernel = restarted
    repeated = restarted.advance(handle, plan_invalidation=second)

    assert repeated.status.value == "Decision"
    assert repeated.reason == "REPLAN_BUDGET_EXHAUSTED"
    assert restarted.inspect(handle).human_gate.decision_id == diagnostics.human_gate.decision_id
    assert classification_calls == [(handle, ("9" * 64,))]
    assert gateway.replan_progresses == 0


def test_successor_revision_budget_exhaustion_persists_before_activation(
    tmp_path,
):
    """A successor at its limit cannot cross PlanControl activation."""

    from v8_successor_test_support import successor_payload

    control, _repository, gateway, _artifacts, _source, host, handle, harness = _direct_setup()
    activation_calls = []
    original_activate = host.activate_successor

    def count_activation(campaign_handle, classification):
        activation_calls.append((campaign_handle, classification.action_id))
        return original_activate(campaign_handle, classification)

    host.activate_successor = count_activation
    kernel = host.install_execution_kernel(
        store_path=tmp_path / "human-gate-successor-budget.sqlite3",
        effects=harness.effects,
    )
    harness._kernel = kernel
    kernel.advance(handle)

    predecessor = host.read_active(handle)
    first = harness.invalidation_for("issue:109")
    kernel.advance(handle, plan_invalidation=first)
    successor = host.read_active(handle)
    assert successor.current_revision_digest != predecessor.current_revision_digest
    assert len(activation_calls) == 1
    assert kernel._load(handle)["replan_budgets"]["successor_revisions_used"] == 1

    gateway.payload = successor_payload(
        resources=(
            (
                "issue:110",
                "repository.target.v1",
                "Budget boundary successor resource",
            ),
        )
    )
    second = harness.invalidation_for("issue:109")
    outcome = kernel.advance(handle, plan_invalidation=second)
    diagnostics = kernel.inspect(handle)
    state = kernel._load(handle)

    assert outcome.status.value == "Decision"
    assert outcome.reason == "REPLAN_BUDGET_EXHAUSTED"
    assert diagnostics.human_gate.phase == "budget_exhausted"
    assert host.read_active(handle).current_revision_digest == successor.current_revision_digest
    assert len(activation_calls) == 1
    assert state["replan_budgets"]["successor_revisions_used"] == 1
    assert state["revision_lineage"]
    assert state["plan_invalidation"]
    assert gateway.replan_progresses == 1

    restarted = host.install_execution_kernel(
        store_path=tmp_path / "human-gate-successor-budget.sqlite3",
        effects=harness.effects,
    )
    harness._kernel = restarted
    repeated = restarted.advance(handle, plan_invalidation=second)

    assert repeated.status.value == "Decision"
    assert repeated.reason == "REPLAN_BUDGET_EXHAUSTED"
    assert len(activation_calls) == 1
    assert restarted.inspect(handle).human_gate.decision_id == diagnostics.human_gate.decision_id


def test_restart_with_exhausted_predecessor_budget_does_not_migrate_again(
    tmp_path,
):
    """A successor readback cannot make an exhausted predecessor migrate twice."""

    from v8_successor_test_support import successor_payload
    from gwo_v8.execution_kernel import ExecutionKernelError

    control, _repository, gateway, _artifacts, _source, host, handle, harness = _direct_setup()
    kernel = host.install_execution_kernel(
        store_path=tmp_path / "human-gate-successor-migration-window.sqlite3",
        effects=harness.effects,
    )
    harness._kernel = kernel
    kernel.advance(handle)
    first = harness.invalidation_for("issue:109")
    kernel.advance(handle, plan_invalidation=first)
    predecessor_successor = host.read_active(handle)
    assert kernel._load(handle)["replan_budgets"]["successor_revisions_used"] == 1

    gateway.payload = successor_payload(
        resources=(
            (
                "issue:110",
                "repository.target.v1",
                "Budget boundary successor resource",
            ),
        )
    )
    second = harness.invalidation_for("issue:109")

    original_budget_guard = kernel._successor_revision_budget_exhausted
    original_migration = kernel._reconcile_successor_revision
    kernel._successor_revision_budget_exhausted = lambda _state: False

    def crash_before_migration(*_args, **_kwargs):
        raise RuntimeError("crash before exhausted successor migration")

    kernel._reconcile_successor_revision = crash_before_migration
    with pytest.raises(RuntimeError):
        kernel.advance(handle, plan_invalidation=second)
    kernel._successor_revision_budget_exhausted = original_budget_guard
    kernel._reconcile_successor_revision = original_migration

    assert host.read_active(handle).current_revision_digest != predecessor_successor.current_revision_digest
    predecessor_state = kernel._load(handle)
    assert predecessor_state["plan_revision_digest"] == predecessor_successor.current_revision_digest
    assert predecessor_state["replan_budgets"]["successor_revisions_used"] == 1
    assert len(predecessor_state["revision_lineage"]) == 1

    restarted = host.install_execution_kernel(
        store_path=tmp_path / "human-gate-successor-migration-window.sqlite3",
        effects=harness.effects,
    )
    harness._kernel = restarted

    with pytest.raises(ExecutionKernelError) as raised:
        restarted.advance(handle)

    assert raised.value.code == "REPLAN_BUDGET_READBACK_INVALID"
