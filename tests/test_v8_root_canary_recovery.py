from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import sys
import threading
import time

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

pytest_plugins = ("v8_production_test_support",)

from gwo_v8.execution_kernel import (  # noqa: E402
    CampaignProofReadback,
    ExecutionKernelError,
    _campaign_proof,
)
from gwo_v8.production_host import ProductionGwoHost  # noqa: E402
from scripts.v8_root_canary_fault_proxy import (  # noqa: E402
    FaultProxy,
    FaultProxyProcessExit,
    FaultRequest,
)
from v8_production_test_support import (  # noqa: E402
    OneCandidateOnlyEffects,
    make_production_effects,
)


def test_inspect_exposes_an_immutable_campaign_proof(
    tmp_path,
    handle,
    active_plan,
    make_kernel,
):
    kernel = make_kernel(
        tmp_path / "execution-kernel.sqlite3",
        active_plan,
        effects=OneCandidateOnlyEffects(),
    )

    before = kernel.inspect(handle)

    assert isinstance(before.proof, CampaignProofReadback)
    assert before.proof.ticket_keys == ()

    kernel.advance(handle)
    after = kernel.inspect(handle)

    assert after.proof.ticket_keys == ("issue:109",)
    assert after.proof.worker_slot_limit == 4
    assert isinstance(after.proof.semantic_effect_ids, tuple)
    assert after.proof.semantic_effect_ids == tuple(
        sorted(set(after.proof.semantic_effect_ids))
    )
    assert after.proof.external_effect_ids == tuple(
        sorted(set(after.proof.external_effect_ids))
    )
    with pytest.raises(FrozenInstanceError):
        after.proof.semantic_effect_ids = ("unexpected",)

    restarted = make_kernel(
        tmp_path / "execution-kernel.sqlite3",
        active_plan,
        effects=OneCandidateOnlyEffects(),
    )
    assert restarted.inspect(handle).proof == after.proof


@pytest.mark.parametrize(
    ("role", "point"),
    (
        ("worker", "candidate_persisted_before_ack"),
        ("review", "finding_ledger_persisted_before_ack"),
        ("delivery", "hosted_receipt_persisted_before_ack"),
    ),
)
def test_fault_proxy_persists_before_injecting_and_replays_after_restart(
    tmp_path,
    role,
    point,
):
    calls: list[tuple[str, ...]] = []
    request = FaultRequest(
        role,
        point,
        "action:1",
        "payload:1",
        ("echo", "ok"),
        plan_revision_digest="a" * 64,
    )
    journal = tmp_path / "journal.json"

    first = FaultProxy(
        journal_path=journal,
        events=(
            {"role": role, "point": point},
        ),
        run_command=lambda command: calls.append(command) or "response",
    )
    with pytest.raises(FaultProxyProcessExit) as error:
        first.execute(request)
    assert error.value.exit_code == 75

    restarted = FaultProxy(
        journal_path=journal,
        events=first.events,
        run_command=lambda command: calls.append(command) or "unexpected",
    )
    assert restarted.execute(request) == first.execute(request)
    assert calls == [("echo", "ok")]


def test_production_host_binds_fault_paths_without_changing_preview_contract(
    tmp_path,
    planning_host,
):
    fault_plan = tmp_path / "fault-plan.json"
    fault_plan.write_text(json.dumps({"events": []}), encoding="utf-8")
    journal = tmp_path / "fault-journal.json"
    calls: list[object] = []
    arguments = planning_host.install_arguments()
    arguments["fault_admission_mode"] = "named_canary"
    arguments["approved_run_root"] = tmp_path
    arguments["fault_plan_path"] = fault_plan
    arguments["journal_path"] = journal
    arguments["worker_command"] = lambda request: calls.append(request) or "worker"

    installed = ProductionGwoHost.install(**arguments)

    assert installed.fault_plan_path == fault_plan.resolve()
    assert installed.journal_path == journal.resolve()
    request = FaultRequest(
        "worker",
        "none",
        "action:1",
        "payload:1",
        ("echo", "ok"),
        plan_revision_digest="a" * 64,
    )
    assert installed.worker_command(request) == installed.worker_command(request)
    assert len(calls) == 1
    assert installed._target_path.is_relative_to(tmp_path.resolve())
    assert arguments["host_configuration"].writer_activation_enabled is False


def test_campaign_proof_fails_closed_on_missing_or_malformed_sources(
    tmp_path,
    handle,
    active_plan,
    make_kernel,
):
    kernel = make_kernel(
        tmp_path / "execution-kernel.sqlite3",
        active_plan,
        effects=OneCandidateOnlyEffects(),
    )
    kernel.advance(handle)
    state = kernel._load(handle)
    assert state is not None

    missing_runtime = dict(state)
    missing_runtime.pop("runtime_selector_receipt", None)
    with pytest.raises(ExecutionKernelError):
        _campaign_proof(active_plan, missing_runtime)

    malformed_runtime = dict(state)
    malformed_runtime["runtime_selector_receipt"] = {
        "receipt_digest": "not-a-digest",
    }
    with pytest.raises(ExecutionKernelError):
        _campaign_proof(active_plan, malformed_runtime)

    with pytest.raises(ExecutionKernelError):
        CampaignProofReadback(runtime_selector_digest="not-a-digest")

    with pytest.raises(ExecutionKernelError):
        CampaignProofReadback(peak_worker_slots=False)

    proof = kernel.inspect(handle).proof
    with pytest.raises(ExecutionKernelError):
        replace(proof, ticket_keys=(1, "issue:109"))


def test_campaign_proof_uses_authoritative_runtime_review_and_permission_readbacks(
    tmp_path,
    handle,
    active_plan,
    make_kernel,
):
    class AuthoritativeEffects(OneCandidateOnlyEffects):
        def campaign_proof_readback(self, campaign, plan_revision_digest):
            assert campaign == handle
            assert plan_revision_digest == active_plan.current_revision_digest
            return {
                "runtime_selector_digest": "1" * 64,
                "permission_binding_pairs": [("requested:binding", "readback:binding")],
                "review_finding_ledger_digests": ["2" * 64],
                "batch_receipt_digests": ["3" * 64],
                "semantic_effect_ids": ["semantic:authoritative"],
                "external_effect_ids": ["external:authoritative"],
                "duplicate_effect_ids": [],
            }

    effects = AuthoritativeEffects()
    kernel = make_kernel(
        tmp_path / "execution-kernel.sqlite3",
        active_plan,
        effects=effects,
    )
    kernel.advance(handle)
    proof = kernel.inspect(handle).proof

    assert proof.runtime_selector_digest == "1" * 64
    assert proof.permission_binding_pairs == (
        ("requested:binding", "readback:binding"),
    )
    assert proof.review_finding_ledger_digests == ("2" * 64,)
    assert proof.batch_receipt_digests == ("3" * 64,)
    assert proof.semantic_effect_ids == ("semantic:authoritative",)
    assert proof.external_effect_ids == ("external:authoritative",)


def test_named_canary_fault_is_injected_by_public_advance_and_replayed(
    tmp_path,
    composition_harness,
):
    fault_plan = tmp_path / "fault-plan.json"
    fault_plan.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "role": "worker",
                        "point": "candidate_persisted_before_ack",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    journal = tmp_path / "fault-journal.json"
    arguments = composition_harness.install_arguments()
    arguments.update(
        fault_admission_mode="named_canary",
        approved_run_root=tmp_path,
        fault_plan_path=fault_plan,
        journal_path=journal,
    )
    installed = ProductionGwoHost.install(**arguments)

    with pytest.raises(FaultProxyProcessExit):
        installed.advance(composition_harness.handle, "runtime:canary-fault")

    assert journal.exists()
    restarted = composition_harness.restart()
    restarted_arguments = restarted.install_arguments()
    restarted_arguments.update(
        fault_admission_mode="named_canary",
        approved_run_root=tmp_path,
        fault_plan_path=fault_plan,
        journal_path=journal,
    )
    restarted_host = ProductionGwoHost.install(**restarted_arguments)
    restarted_host.advance(restarted.handle, "runtime:canary-replay")

    assert restarted_host.inspect(restarted.handle).work_runs[0].phase == (
        "accepted_awaiting_delivery"
    )


def test_fault_proxy_concurrent_journal_updates_do_not_lose_effects(tmp_path):
    journal = tmp_path / "journal.json"
    started = threading.Event()

    def run(request: FaultRequest) -> str:
        proxy = FaultProxy(
            journal_path=journal,
            events=(),
            run_command=lambda command: started.set() or time.sleep(0.1) or command[1],
        )
        return proxy.execute(request)

    requests = tuple(
        FaultRequest(
            "worker",
            "none",
            f"action:{index}",
            f"payload:{index}",
            ("echo", str(index)),
            plan_revision_digest="a" * 64,
        )
        for index in range(2)
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(run, requests))

    assert len(results) == 2
    journal_value = json.loads(journal.read_text(encoding="utf-8"))
    assert set(journal_value["effects"]) == {"action:0", "action:1"}


def test_ambiguous_provider_failure_keeps_the_authoritative_effect_claim(
    tmp_path,
    action,
    support,
    monkeypatch,
):
    from gwo_v8.production_effects import ProductionCompositionError

    monkeypatch.setattr(
        "gwo_v8.production_effects._EFFECT_CLAIM_WAIT_SECONDS",
        0.01,
    )
    calls: list[str] = []

    class AmbiguousRuntime:
        def progress(self, subject, *, wake_cursor=None):
            calls.append(subject.stable_action_id)
            raise RuntimeError("provider acknowledgement is ambiguous")

    support.runtime_factory.gateway = AmbiguousRuntime()
    effects = make_production_effects(tmp_path, support)

    with pytest.raises(RuntimeError, match="ambiguous"):
        effects.execute(action)

    with pytest.raises(ProductionCompositionError) as raised:
        effects.execute(action)

    assert raised.value.code == "EFFECT_EXECUTION_IN_PROGRESS"
    # The restart path performs one exact Runtime readback before retaining
    # the duplicate fence; it must not dispatch a second provider effect.
    assert calls == [action.stable_action_id, action.stable_action_id]


@pytest.mark.parametrize(
    "change",
    (
        {"role": "review"},
        {"point": "other"},
        {"command": ("different",)},
        {"plan_revision_digest": "b" * 64},
        {"payload_digest": "different-payload"},
    ),
)
def test_fault_proxy_replay_binds_role_point_command_plan_and_payload(
    tmp_path,
    change,
):
    request = FaultRequest(
        "worker",
        "candidate_persisted_before_ack",
        "action:bound",
        "payload:bound",
        ("echo", "ok"),
        plan_revision_digest="a" * 64,
    )
    journal = tmp_path / "journal.json"
    proxy = FaultProxy(
        journal_path=journal,
        events=(),
        run_command=lambda _command: "response",
    )
    proxy.execute(request)

    with pytest.raises(ValueError):
        proxy.execute(replace(request, **change))


def test_fault_proxy_replay_rejects_a_tampered_response_digest(tmp_path):
    request = FaultRequest(
        "worker",
        "none",
        "action:response",
        "payload:response",
        ("echo", "ok"),
        plan_revision_digest="a" * 64,
    )
    journal = tmp_path / "journal.json"
    proxy = FaultProxy(
        journal_path=journal,
        events=(),
        run_command=lambda _command: "response",
    )
    proxy.execute(request)
    value = json.loads(journal.read_text(encoding="utf-8"))
    value["effects"][request.stable_action_id]["response_digest"] = "0" * 64
    journal.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError):
        proxy.execute(request)


def test_fault_proxy_rejects_a_replay_without_plan_identity(tmp_path):
    request = FaultRequest(
        "worker",
        "none",
        "action:unbound-plan",
        "payload:unbound-plan",
        ("echo", "ok"),
    )
    proxy = FaultProxy(
        journal_path=tmp_path / "journal.json",
        events=(),
        run_command=lambda _command: "response",
    )

    with pytest.raises(ValueError):
        proxy.execute(request)


def test_fault_proxy_rejects_reparse_path_even_when_target_is_below_root(tmp_path):
    from scripts.v8_root_canary_fault_proxy import _require_child

    plan = tmp_path / "fault-plan.json"
    plan.write_text("{}", encoding="utf-8")
    linked = tmp_path / "linked-plan.json"
    try:
        linked.symlink_to(plan)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    with pytest.raises(ValueError):
        _require_child(linked, tmp_path)


def test_fault_proxy_rejects_a_hardlinked_durable_record(tmp_path):
    from scripts.v8_root_canary_fault_proxy import _require_child

    plan = tmp_path / "fault-plan.json"
    plan.write_text("{}", encoding="utf-8")
    linked = tmp_path / "linked-plan.json"
    try:
        linked.hardlink_to(plan)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"hard-link creation unavailable: {error}")

    with pytest.raises(ValueError):
        _require_child(linked, tmp_path)


def test_fault_proxy_fails_closed_when_the_approved_root_is_replaced_after_validation(
    tmp_path,
    monkeypatch,
):
    from scripts import v8_root_canary_fault_proxy as fault_proxy_module

    approved = tmp_path / "approved"
    replacement = tmp_path / "replacement"
    approved.mkdir()
    replacement.mkdir()
    (approved / "plan.json").write_text(
        json.dumps({"events": []}),
        encoding="utf-8",
    )
    (replacement / "plan.json").write_text(
        json.dumps({"events": []}),
        encoding="utf-8",
    )

    original_require_child = fault_proxy_module._require_child
    swapped = False

    def replace_root_after_validation(path, root):
        nonlocal swapped
        validated = original_require_child(path, root)
        if not swapped and Path(path).name == "plan.json":
            approved.rename(tmp_path / "approved-original")
            replacement.rename(approved)
            swapped = True
        return validated

    monkeypatch.setattr(
        fault_proxy_module,
        "_require_child",
        replace_root_after_validation,
    )

    request = FaultRequest(
        "worker",
        "none",
        "action:root-race",
        "payload:root-race",
        ("echo", "ok"),
        plan_revision_digest="a" * 64,
    )

    with pytest.raises(ValueError):
        proxy = FaultProxy.from_files(
            approved / "plan.json",
            approved / "journal.json",
            run_root=approved,
        )
        proxy.execute(request, run_command=lambda _command: "response")

    assert swapped is True
    assert not (approved / "journal.json").exists()
    assert not (tmp_path / "approved-original" / "journal.json").exists()


def test_fault_proxy_fails_closed_when_parent_swaps_at_temp_create(
    tmp_path,
    monkeypatch,
):
    from scripts import v8_root_canary_fault_proxy as fault_proxy_module

    approved = tmp_path / "approved"
    replacement = tmp_path / "replacement"
    approved.mkdir()
    replacement.mkdir()
    for root in (approved, replacement):
        (root / "plan.json").write_text(
            json.dumps({"events": []}),
            encoding="utf-8",
        )

    original_open_lock = getattr(fault_proxy_module, "_open_lock_file", None)
    boundary_reached = False
    swapped = False

    def open_lock_after_parent_swap(name, parent):
        nonlocal boundary_reached, swapped
        if not boundary_reached:
            boundary_reached = True
            # Swap the actual approved parent immediately before the
            # descriptor-relative journal lock open.  The held lease must
            # reject the operation before any journal side effect.
            approved.rename(tmp_path / "approved-original")
            replacement.rename(approved)
            swapped = True
        if original_open_lock is None:
            return None
        return original_open_lock(name, parent)

    monkeypatch.setattr(
        fault_proxy_module,
        "_open_lock_file",
        open_lock_after_parent_swap,
        raising=False,
    )

    request = FaultRequest(
        "worker",
        "none",
        "action:operation-race",
        "payload:operation-race",
        ("echo", "ok"),
        plan_revision_digest="a" * 64,
    )

    with pytest.raises(ValueError):
        proxy = FaultProxy.from_files(
            approved / "plan.json",
            approved / "journal.json",
            run_root=approved,
        )
        proxy.execute(request, run_command=lambda _command: "response")

    assert boundary_reached is True
    assert swapped is True
    assert not (approved / "journal.json").exists()
    assert not (tmp_path / "approved-original" / "journal.json").exists()
