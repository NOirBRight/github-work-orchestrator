from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    EvidenceVerifier,
    GoalDriver,
    GoalDriverError,
    GoalSnapshot,
    InMemoryCoordinatorRuntime,
    InMemoryDeliveryControl,
    InMemoryDurableGoalControl,
    InMemoryPaseoClient,
    InMemoryRuntimeAdapter,
    Kernel,
    LocalPlanPublication,
    PlanCompiler,
    PaseoRuntimeAdapter,
    ReconcileOutcome,
    ReviewAxisBinding,
    ReviewAxisObservation,
    RuntimeAdapterError,
    RuntimeProfile,
    resolve_active_turn_pools,
)
import orch_core  # noqa: E402


def _multi_ready_inputs(count: int = 3) -> tuple[dict, dict, dict]:
    work_items = []
    nodes = []
    for ordinal in range(1, count + 1):
        path = f"module-{ordinal}.txt"
        content = f"module {ordinal}\n"
        work_item_key = f"issue:{100 + ordinal}"
        work_items.append(
            {
                "work_item_key": work_item_key,
                "tracker_state": "ready-for-agent",
                "source_ref": f"synthetic://issue/{100 + ordinal}",
                "title": f"Build independent module {ordinal}",
                "outcome_contract": {"path": path, "content": content},
            }
        )
        nodes.append(
            {
                "goal_key": "goal:phase-4a",
                "work_item_key": work_item_key,
                "kind": "work",
                "inputs": {
                    "file_changes": [{"path": path, "content": content}],
                },
                "output_contract": {
                    "required_evidence": [
                        {"kind": "candidate"},
                        {"kind": "check", "check_id": f"module-{ordinal}"},
                    ],
                    "checks": [
                        {
                            "check_id": f"module-{ordinal}",
                            "command": [
                                "python",
                                "-c",
                                (
                                    "from pathlib import Path; "
                                    f"assert Path('{path}').read_text() "
                                    f"== {content!r}"
                                ),
                            ],
                        }
                    ],
                },
                "effect_contract": {
                    "write_scopes": [path],
                    "external_effects": [],
                },
                "resource_claims": [],
                "runtime_requirements": {
                    "capabilities": ["git", "local_check"],
                },
                "difficulty": "standard",
                "risk": "low",
                "recovery_policy": {
                    "semantic_attempts": 2,
                    "repair_rounds": 1,
                },
                "skill_reference": None,
            }
        )
    intent = {
        "parent_plan_digest": None,
        "goals": [
            {
                "goal_key": "goal:phase-4a",
                "objective": "Build the compatible ready frontier in parallel.",
                "acceptance": ["Every independent module is integrated."],
            }
        ],
        "nodes": nodes,
        "edges": [],
    }
    source = {
        "repository": "local/phase-four-a",
        "work_items": work_items,
    }
    return intent, source, {"version": 2}


def _local_first_policy(count: int) -> dict:
    definitions = []
    for ordinal in range(1, count + 1):
        path = f"module-{ordinal}.txt"
        command = [
            "python",
            "-c",
            (
                "from pathlib import Path; "
                f"assert Path('{path}').read_text() == {'module ' + str(ordinal) + chr(10)!r}"
            ),
        ]
        definitions.extend(
            [
                {
                    "check_id": f"module-{ordinal}",
                    "version": 1,
                    "command": command,
                    "hosted_name": None,
                    "environment_requirements": ["python"],
                    "input_selector": [path],
                    "base_sensitive": False,
                    "risk": "low",
                    "hosted_only": False,
                    "suite": "repository",
                },
                {
                    "check_id": f"module-{ordinal}-hosted",
                    "version": 1,
                    "command": ["python", "-c", "raise SystemExit(0)"],
                    "hosted_name": f"Module {ordinal} CI",
                    "environment_requirements": [],
                    "input_selector": [path],
                    "base_sensitive": False,
                    "risk": "low",
                    "hosted_only": True,
                    "suite": "hosted",
                },
            ]
        )
    return {
        "version": 3,
        "low_risk_allowlist": ["module-*.txt"],
        "check_definitions": definitions,
        "strict_review": {
            "specialist_requirements": [],
            "human_decision_required": True,
        },
    }


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _temporary_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(repository)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    _git(repository, "config", "user.name", "Phase Four A")
    _git(repository, "config", "user.email", "phase-four-a@example.invalid")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "base")
    return repository


def _runtime_profile() -> RuntimeProfile:
    return RuntimeProfile(
        name="worker-standard",
        provider="kimi-cli",
        model="kimi-code/k2.7",
        thinking="max",
        mode="yolo",
        features={},
    )


def _coordinator_profile() -> RuntimeProfile:
    return RuntimeProfile(
        name="coordinator_auto",
        provider="kimi-cli",
        model="kimi-code/k3",
        thinking="on",
        mode="yolo",
        features={},
    )


class _CoordinatorNeededReconciler:
    def __init__(self, goal_key: str):
        self.goal_key = goal_key

    def reconcile_once(self, repository: str) -> ReconcileOutcome:
        return ReconcileOutcome(
            status="waiting",
            directive="invoke_coordinator",
            repository=repository,
            plan_digest="a" * 64,
            goal_key=self.goal_key,
            goal_state="active",
            work_item_key=f"issue:{self.goal_key}",
            work_item_state="active",
            node_key=f"node:{self.goal_key}",
            admission_id=f"admission:{self.goal_key}",
            admission_state="consumed",
            attempt_id=f"attempt:{self.goal_key}",
            attempt_state="running",
            candidate_sha=None,
            result_digest=None,
            materialization_executions=1,
            wait_condition=None,
        )


class _RepairCountingRuntime(InMemoryRuntimeAdapter):
    def __init__(self, workspace_root: Path):
        super().__init__(workspace_root)
        self.repair_count = 0

    def repair(self, binding, prompt) -> None:
        self.repair_count += 1
        super().repair(binding, prompt)


class _ReviewingInMemoryRuntime(InMemoryRuntimeAdapter):
    def materialize_review_axis(
        self,
        request,
        profile,
        *,
        parent_agent_id,
    ):
        prompt = request.to_prompt()
        suffix = request.action_key[-12:]
        return ReviewAxisBinding(
            action_key=request.action_key,
            axis=request.axis,
            candidate_sha=request.candidate_sha,
            fixed_input_digest=request.fixed_input_digest,
            runtime_id=f"review:{suffix}",
            agent_id=f"review:{suffix}",
            session_id=f"session:{suffix}",
            workspace_id=f"workspace:{suffix}",
            workspace=str(request.workspace),
            parent_agent_id=parent_agent_id,
            runtime_profile=profile.name,
            profile_digest=profile.digest,
            provider=profile.provider,
            model=profile.model,
            thinking=profile.thinking,
            mode=profile.mode,
            prompt_digest=prompt.digest,
        )

    def observe_review_axis(self, request, binding):
        return ReviewAxisObservation(
            lifecycle="completed",
            axis=request.axis,
            attempt_id=request.attempt_id,
            candidate_sha=request.candidate_sha,
            base_sha=request.base_sha,
            recovery_ordinal=request.recovery_ordinal,
            spec_digest=request.spec_digest,
            check_manifest_digest=request.check_manifest_digest,
            fixed_input_digest=request.fixed_input_digest,
            action_key=request.action_key,
            runtime_id=binding.runtime_id,
            agent_id=binding.agent_id,
            session_id=binding.session_id,
            profile_digest=binding.profile_digest,
            provider=binding.provider,
            model=binding.model,
            thinking=binding.thinking,
            mode=binding.mode,
            output_digest=request.fixed_input_digest,
            findings=(),
        )

    def retire_review_axis(self, _binding):
        return None


class _BarrierRuntime(InMemoryRuntimeAdapter):
    def __init__(self, workspace_root: Path):
        super().__init__(workspace_root)
        self.barrier = threading.Barrier(2)
        self.materialized_nodes: list[str] = []

    def materialize(self, admission, prompt=None):
        self.materialized_nodes.append(admission.node_key)
        self.barrier.wait(timeout=5)
        return super().materialize(admission, prompt)


class _BarrierCoordinatorReconciler(_CoordinatorNeededReconciler):
    def __init__(self, goal_key: str, barrier: threading.Barrier):
        super().__init__(goal_key)
        self.barrier = barrier

    def reconcile_once(self, repository: str) -> ReconcileOutcome:
        self.barrier.wait(timeout=5)
        return super().reconcile_once(repository)


class _SweepThenCompleteReconciler:
    def __init__(self):
        self.calls = 0

    def reconcile_once(self, repository: str) -> ReconcileOutcome:
        self.calls += 1
        complete = self.calls > 1
        return ReconcileOutcome(
            status="complete" if complete else "waiting",
            directive="goal_complete" if complete else "reconcile_again",
            repository=repository,
            plan_digest="a" * 64,
            goal_key="goal:sweep",
            goal_state="completed" if complete else "active",
            work_item_key="issue:goal:sweep",
            work_item_state="integrated" if complete else "active",
            node_key="node:goal:sweep",
            admission_id="admission:goal:sweep",
            admission_state="consumed",
            attempt_id="attempt:goal:sweep",
            attempt_state="verified" if complete else "parked",
            candidate_sha=None,
            result_digest=None,
            materialization_executions=1,
            wait_condition=None if complete else "kernel_sweep",
            wait_source_ref=None if complete else "store://kernel-sweep/test",
            wait_event_identity=None if complete else "kernel-sweep:test",
            next_check_at=None,
            completed_work_item_keys=(
                ("issue:goal:sweep",) if complete else ()
            ),
        )


class _CrashAfterCoordinatorCreate(InMemoryCoordinatorRuntime):
    def __init__(self):
        super().__init__()
        self.crash_once = True

    def create_auto(self, snapshot, profile, *, action_key):
        session = super().create_auto(
            snapshot,
            profile,
            action_key=action_key,
        )
        if self.crash_once:
            self.crash_once = False
            raise GoalDriverError(
                "SYNTHETIC_CRASH",
                "process stopped after Runtime creation",
            )
        return session


def _goal_snapshot(goal_key: str) -> GoalSnapshot:
    return GoalSnapshot(
        repository="local/phase-four-a",
        goal_key=goal_key,
        objective=f"Complete {goal_key}.",
        acceptance=("The Goal is complete.",),
        plan_digest="a" * 64,
        work_items=((f"issue:{goal_key}", "active"),),
        decision_inputs=(),
    )


def test_compiler_emits_one_work_and_integration_pair_per_ready_item():
    intent, source, policy = _multi_ready_inputs()

    compiled = PlanCompiler().compile(intent, source, policy)
    plan = json.loads(compiled.canonical_bytes)

    work_nodes = [node for node in plan["nodes"] if node["kind"] == "work"]
    integration_nodes = [
        node for node in plan["nodes"] if node["kind"] == "integration"
    ]
    assert len(work_nodes) == 3
    assert len(integration_nodes) == 3
    assert {node["work_item_key"] for node in work_nodes} == {
        "issue:101",
        "issue:102",
        "issue:103",
    }
    assert {
        (
            edge["from_node"],
            edge["to_node"],
            edge["type"],
        )
        for edge in plan["edges"]
    } == {
        (
            work["node_key"],
            next(
                integration["node_key"]
                for integration in integration_nodes
                if integration["work_item_key"] == work["work_item_key"]
            ),
            "result_required",
        )
        for work in work_nodes
    }


def test_one_pass_admits_the_ready_frontier_up_to_worker_capacity(tmp_path):
    repository = _temporary_repository(tmp_path)
    intent, source, policy = _multi_ready_inputs()
    compiled = PlanCompiler().compile(intent, source, policy)
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-4a",
    )
    client = InMemoryPaseoClient()
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=PaseoRuntimeAdapter(client),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-4a",
        runtime_profile=_runtime_profile(),
        runtime_config={
            "active_turn_pools": {"workers": 2, "coordinators": 1},
            "repositories": {},
        },
    )

    outcome = kernel.reconcile_once("local/phase-four-a")

    workers = client.find_by_labels({"gwo.repository": "local/phase-four-a"})
    assert len(workers) == 2
    assert len(outcome.admitted_node_keys) == 2
    assert outcome.active_worker_turns == 2
    assert outcome.worker_turn_capacity == 2
    assert outcome.directive == "reconcile_again"
    assert outcome.wait_condition == "kernel_sweep"


def test_default_pool_demonstrates_high_parallel_utilization(tmp_path):
    repository = _temporary_repository(tmp_path)
    intent, source, policy = _multi_ready_inputs(count=5)
    compiled = PlanCompiler().compile(intent, source, policy)
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-4a",
    )
    client = InMemoryPaseoClient()
    outcome = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=PaseoRuntimeAdapter(client),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-4a",
        runtime_profile=_runtime_profile(),
    ).reconcile_once("local/phase-four-a")

    assert outcome.worker_turn_capacity == 8
    assert len(outcome.admitted_node_keys) == 5
    assert outcome.active_worker_turns == 5
    assert len(client.find_by_labels({"gwo.repository": "local/phase-four-a"})) == 5


def test_committed_frontier_materializes_without_head_of_line_blocking(tmp_path):
    repository = _temporary_repository(tmp_path)
    intent, source, policy = _multi_ready_inputs(count=2)
    compiled = PlanCompiler().compile(intent, source, policy)
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-4a",
    )
    runtime = _BarrierRuntime(tmp_path / "runtime")
    outcome = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=runtime,
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-4a",
        runtime_profile=_runtime_profile(),
        runtime_config={
            "active_turn_pools": {"workers": 2, "coordinators": 1},
            "repositories": {},
        },
    ).reconcile_once("local/phase-four-a")

    assert len(outcome.admitted_node_keys) == 2
    assert len(runtime.materialized_nodes) == 2


def test_repository_and_observed_capacity_bound_the_ready_frontier(tmp_path):
    repository = _temporary_repository(tmp_path)
    intent, source, policy = _multi_ready_inputs(count=5)
    compiled = PlanCompiler().compile(intent, source, policy)
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-4a",
    )
    client = InMemoryPaseoClient(worker_turn_capacity=2)
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=PaseoRuntimeAdapter(client),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-4a",
        runtime_profile=_runtime_profile(),
        runtime_config={
            "active_turn_pools": {"workers": 8, "coordinators": 1},
            "repositories": {
                "local/phase-four-a": {
                    "active_turn_pools": {
                        "workers": 3,
                        "coordinators": 2,
                    }
                }
            },
        },
    )

    outcome = kernel.reconcile_once("local/phase-four-a")

    assert len(outcome.admitted_node_keys) == 2
    assert outcome.worker_turn_capacity == 2
    assert outcome.coordinator_turn_capacity == 2


def test_active_turn_pools_have_global_defaults_and_repository_overrides():
    config = orch_core.default_config()

    assert config["active_turn_pools"] == {
        "workers": 8,
        "coordinators": 1,
    }
    config["repositories"]["local/phase-four-a"] = {
        "active_turn_pools": {
            "workers": 5,
            "coordinators": 2,
        }
    }
    orch_core.validate_config(config)

    config["repositories"]["local/phase-four-a"]["active_turn_pools"]["workers"] = 0
    with pytest.raises(orch_core.PolicyError) as rejected:
        orch_core.validate_config(config)
    assert rejected.value.code == "ACTIVE_TURN_CAPACITY_INVALID"
    for invalid in ([], "", 0, {"active_turn_pools": {}}):
        with pytest.raises(RuntimeAdapterError):
            resolve_active_turn_pools(invalid, repository="local/phase-four-a")


def test_batch_wait_releases_worker_turns_and_refills_before_one_hosted_ci(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    intent, source, _policy = _multi_ready_inputs(count=3)
    compiled = PlanCompiler().compile(
        intent,
        source,
        _local_first_policy(3),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-4a",
    )
    delivery = InMemoryDeliveryControl(hosted_outcomes=("pending",) * 8)
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=InMemoryRuntimeAdapter(tmp_path / "runtime"),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-4a",
        runtime_profile=_runtime_profile(),
        delivery_control=delivery,
        runtime_config={
            "active_turn_pools": {"workers": 2, "coordinators": 1},
            "repositories": {},
        },
    )

    first = kernel.reconcile_once("local/phase-four-a")
    second = kernel.reconcile_once("local/phase-four-a")

    assert len(first.admitted_node_keys) == 2
    assert first.active_worker_turns == 0
    assert {item.wait_condition for item in first.node_outcomes} == {
        "integration_batch"
    }
    assert len(second.admitted_node_keys) == 1
    assert second.active_worker_turns == 0
    assert {item.wait_condition for item in second.node_outcomes} == {
        "hosted_ci"
    }
    assert delivery.publication_count == 1


def test_batch_hosted_failure_stops_without_blind_worker_repair(tmp_path):
    repository = _temporary_repository(tmp_path)
    intent, source, _policy = _multi_ready_inputs(count=2)
    compiled = PlanCompiler().compile(
        intent,
        source,
        _local_first_policy(2),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-4a",
    )
    delivery = InMemoryDeliveryControl(
        hosted_outcomes=(
            "pending",
            "code_failure",
            "pending",
            "code_failure",
            "pending",
        )
    )
    runtime = _RepairCountingRuntime(tmp_path / "runtime")
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=runtime,
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-4a",
        runtime_profile=_runtime_profile(),
        delivery_control=delivery,
        runtime_config={
            "active_turn_pools": {"workers": 1, "coordinators": 1},
            "repositories": {},
        },
    )

    first = kernel.reconcile_once("local/phase-four-a")
    second = kernel.reconcile_once("local/phase-four-a")

    assert len(first.admitted_node_keys) == 1
    assert len(second.admitted_node_keys) == 1
    assert runtime.repair_count == 0
    assert second.active_worker_turns == 0
    assert {
        item.wait_condition for item in second.node_outcomes
    } == {"hosted_ci"}

    third = kernel.reconcile_once("local/phase-four-a")

    assert runtime.repair_count == 0
    assert third.admitted_node_keys == ()
    assert third.active_worker_turns == 0
    assert {item.status for item in third.node_outcomes} == {"blocked"}
    assert {item.attempt_state for item in third.node_outcomes} == {
        "integration_batch_failed"
    }


def test_same_node_recovery_reservation_is_compare_and_swap(tmp_path):
    repository = _temporary_repository(tmp_path)
    intent, source, _policy = _multi_ready_inputs(count=1)
    compiled = PlanCompiler().compile(
        intent,
        source,
        _local_first_policy(1),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-4a",
    )
    runtime = InMemoryRuntimeAdapter(tmp_path / "runtime")
    delivery = InMemoryDeliveryControl(hosted_outcomes=("pending",))

    def new_kernel() -> Kernel:
        return Kernel(
            store_path=store_path,
            publication=publication,
            runtime=runtime,
            verifier=EvidenceVerifier(),
            repository_path=repository,
            integration_branch="main",
            writer_generation="phase-4a",
            runtime_profile=_runtime_profile(),
            delivery_control=delivery,
            runtime_config={
                "active_turn_pools": {"workers": 1, "coordinators": 1},
                "repositories": {},
            },
        )

    first_kernel = new_kernel()
    first = first_kernel.reconcile_once("local/phase-four-a")
    state = first_kernel._read_state(
        "local/phase-four-a",
        compiled.digest,
        first.node_key,
    )
    assert state is not None
    state.update(
        {
            "status": "rejected",
            "attempt_state": "candidate_rejected",
            "wait_condition": None,
        }
    )
    first_kernel._write_state("local/phase-four-a", compiled.digest, state)
    kernels = (first_kernel, new_kernel())
    barrier = threading.Barrier(2)

    def reserve(kernel: Kernel) -> str:
        candidate = dict(state)
        barrier.wait(timeout=5)
        return kernel._reserve_or_park_recovery_turn(
            candidate,
            worker_turn_capacity=1,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        reservations = tuple(executor.map(reserve, kernels))

    assert set(reservations) == {"reserved", "adopted"}


def test_overlapping_write_scopes_are_advisory_for_admission(tmp_path):
    repository = _temporary_repository(tmp_path)
    intent, source, policy = _multi_ready_inputs(count=2)
    for ordinal, (work_item, node) in enumerate(
        zip(source["work_items"], intent["nodes"], strict=True),
        start=1,
    ):
        path = f"modules/module-{ordinal}.txt"
        work_item["outcome_contract"]["path"] = path
        node["inputs"]["file_changes"][0]["path"] = path
        node["effect_contract"]["write_scopes"] = ["modules"]
        node["output_contract"]["checks"][0]["command"] = [
            "python",
            "-c",
            f"from pathlib import Path; assert Path('{path}').is_file()",
        ]
    compiled = PlanCompiler().compile(intent, source, policy)
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-4a",
    )
    client = InMemoryPaseoClient()
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=PaseoRuntimeAdapter(client),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-4a",
        runtime_profile=_runtime_profile(),
        runtime_config={
            "active_turn_pools": {"workers": 2, "coordinators": 1},
            "repositories": {},
        },
    )

    outcome = kernel.reconcile_once("local/phase-four-a")

    assert len(outcome.admitted_node_keys) == 2


def test_explicit_non_shareable_resource_hard_excludes_second_admission(tmp_path):
    repository = _temporary_repository(tmp_path)
    intent, source, policy = _multi_ready_inputs(count=2)
    for node in intent["nodes"]:
        node["resource_claims"] = ["external:test-environment"]
    compiled = PlanCompiler().compile(intent, source, policy)
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-4a",
    )
    client = InMemoryPaseoClient()
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=PaseoRuntimeAdapter(client),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-4a",
        runtime_profile=_runtime_profile(),
        runtime_config={
            "active_turn_pools": {"workers": 2, "coordinators": 1},
            "repositories": {},
        },
    )

    outcome = kernel.reconcile_once("local/phase-four-a")

    assert len(outcome.admitted_node_keys) == 1
    assert outcome.active_worker_turns == 1


def test_one_batch_performs_only_one_target_branch_mutation(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    intent, source, _policy = _multi_ready_inputs(count=2)
    compiled = PlanCompiler().compile(
        intent,
        source,
        _local_first_policy(2),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-4a",
    )
    delivery = InMemoryDeliveryControl(hosted_outcomes=("passed", "passed"))
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=InMemoryRuntimeAdapter(tmp_path / "runtime"),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-4a",
        runtime_profile=_runtime_profile(),
        delivery_control=delivery,
        runtime_config={
            "active_turn_pools": {"workers": 2, "coordinators": 1},
            "repositories": {},
        },
    )

    outcome = kernel.reconcile_once("local/phase-four-a")

    assert len(delivery.integrated_candidates) == 1
    assert delivery.publication_count == 1
    assert outcome.active_worker_turns == 0
    assert {item.status for item in outcome.node_outcomes} == {"complete"}
    assert len(
        {item.integration_batch_sha for item in outcome.node_outcomes}
    ) == 1


def test_three_node_e2e_reaches_the_serial_integration_boundary(tmp_path):
    repository = _temporary_repository(tmp_path)
    intent, source, _policy = _multi_ready_inputs(count=3)
    compiled = PlanCompiler().compile(
        intent,
        source,
        _local_first_policy(3),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-4a",
    )
    delivery = InMemoryDeliveryControl(
        hosted_outcomes=(
            "pending",
            "pending",
            "passed",
            "passed",
            "pending",
        )
    )
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=InMemoryRuntimeAdapter(tmp_path / "runtime"),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-4a",
        runtime_profile=_runtime_profile(),
        delivery_control=delivery,
        runtime_config={
            "active_turn_pools": {"workers": 2, "coordinators": 1},
            "repositories": {},
        },
    )

    first = kernel.reconcile_once("local/phase-four-a")
    second = kernel.reconcile_once("local/phase-four-a")
    third = kernel.reconcile_once("local/phase-four-a")
    fourth = kernel.reconcile_once("local/phase-four-a")

    assert len(first.admitted_node_keys) == 2
    assert first.active_worker_turns == 0
    assert len(second.admitted_node_keys) == 1
    assert delivery.publication_count == 1
    assert len(delivery.integrated_candidates) == 1
    assert second.wait_condition == "kernel_sweep"
    assert third.wait_condition == "kernel_sweep"
    assert fourth.status == "complete"
    assert {item.status for item in fourth.node_outcomes} == {"complete"}
    batch_shas = {
        item.integration_batch_sha for item in fourth.node_outcomes
    }
    assert len(batch_shas) == 1
    assert next(iter(batch_shas)) == _git(repository, "rev-parse", "main")


def test_three_standard_candidates_keep_dual_axis_review_in_one_batch(tmp_path):
    repository = _temporary_repository(tmp_path)
    intent, source, _policy = _multi_ready_inputs(count=3)
    for node in intent["nodes"]:
        node["risk"] = "standard"
    policy = _local_first_policy(3)
    # Every Work Node needs one applicable affected definition. The synthetic
    # commands are equivalent, so expose the repository definitions as affected
    # and add one shared repository-equivalent definition per node.
    for definition in policy["check_definitions"]:
        if definition["hosted_only"] is not True:
            definition["suite"] = "affected"
    for ordinal in range(1, 4):
        path = f"module-{ordinal}.txt"
        policy["check_definitions"].append(
            {
                "check_id": f"module-{ordinal}-repository",
                "version": 1,
                "command": [
                    "python",
                    "-c",
                    f"from pathlib import Path; assert Path('{path}').is_file()",
                ],
                "hosted_name": None,
                "environment_requirements": ["python"],
                "input_selector": [path],
                "base_sensitive": False,
                "risk": "low",
                "hosted_only": False,
                "suite": "repository",
            }
        )
    compiled = PlanCompiler().compile(intent, source, policy)
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-4a",
    )
    delivery = InMemoryDeliveryControl(hosted_outcomes=("passed",))
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=_ReviewingInMemoryRuntime(tmp_path / "runtime"),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-4a",
        runtime_profile=_runtime_profile(),
        delivery_control=delivery,
        runtime_config={
            "active_turn_pools": {"workers": 3, "coordinators": 1},
            "repositories": {},
            "runtime_profiles": {
                "reviewer_standard": {
                    "provider": "codex",
                    "settings": {
                        "model": "gpt-5.6-sol",
                        "thinkingOptionId": "high",
                        "modeId": "full-access",
                        "features": {},
                    },
                }
            },
            "review_profiles": {"standard_axis": "reviewer_standard"},
        },
    )

    completed = kernel.reconcile_once("local/phase-four-a")

    assert completed.status == "complete"
    assert delivery.publication_count == 1
    assert len(delivery.integrated_candidates) == 1
    states = kernel._read_states("local/phase-four-a", compiled.digest)
    assert len({state["integration_batch_sha"] for state in states}) == 1
    for state in states:
        review = next(
            item
            for item in state["candidate_observation"]["evidence"]
            if item["kind"] == "review"
        )
        assert {axis["axis"] for axis in review["payload"]["axes"]} == {
            "standards",
            "spec",
        }


def test_saturated_workers_cannot_consume_reserved_coordinator_capacity(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    intent, source, policy = _multi_ready_inputs(count=1)
    compiled = PlanCompiler().compile(intent, source, policy)
    store_path = tmp_path / "driver.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-4a",
    )
    config = {
        "active_turn_pools": {"workers": 1, "coordinators": 1},
        "repositories": {},
    }
    worker_outcome = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=PaseoRuntimeAdapter(InMemoryPaseoClient()),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-4a",
        runtime_profile=_runtime_profile(),
        runtime_config=config,
    ).reconcile_once("local/phase-four-a")
    assert worker_outcome.active_worker_turns == 1

    first_runtime = InMemoryCoordinatorRuntime()
    first = GoalDriver(
        store_path=store_path,
        reconciler=_CoordinatorNeededReconciler("goal:first"),
        coordinators=first_runtime,
        auto_profile=_coordinator_profile(),
        durable=InMemoryDurableGoalControl(),
        runtime_config=config,
    )
    second_runtime = InMemoryCoordinatorRuntime()
    second = GoalDriver(
        store_path=store_path,
        reconciler=_CoordinatorNeededReconciler("goal:second"),
        coordinators=second_runtime,
        auto_profile=_coordinator_profile(),
        durable=InMemoryDurableGoalControl(),
        runtime_config=config,
    )

    first_directive = first.run_once(_goal_snapshot("goal:first"))
    second_directive = second.run_once(_goal_snapshot("goal:second"))

    assert first_directive.kind == "continue_coordinator"
    assert first_runtime.auto_create_count == 1
    assert second_directive.kind == "wait"
    assert second_directive.wait_condition == "coordinator_capacity"
    assert second_runtime.auto_create_count == 0


def test_concurrent_goal_drivers_atomically_reserve_coordinator_capacity(
    tmp_path,
):
    barrier = threading.Barrier(2)
    runtime = InMemoryCoordinatorRuntime()
    config = {
        "active_turn_pools": {"workers": 8, "coordinators": 1},
        "repositories": {},
    }
    drivers = tuple(
        GoalDriver(
            store_path=tmp_path / "driver.sqlite3",
            reconciler=_BarrierCoordinatorReconciler(goal_key, barrier),
            coordinators=runtime,
            auto_profile=_coordinator_profile(),
            durable=InMemoryDurableGoalControl(),
            runtime_config=config,
        )
        for goal_key in ("goal:first", "goal:second")
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        directives = tuple(
            executor.map(
                lambda pair: pair[0].run_once(_goal_snapshot(pair[1])),
                zip(drivers, ("goal:first", "goal:second"), strict=True),
            )
        )

    assert {directive.kind for directive in directives} == {
        "continue_coordinator",
        "wait",
    }
    assert runtime.auto_create_count == 1
    assert {
        directive.wait_condition
        for directive in directives
        if directive.kind == "wait"
    } == {"coordinator_capacity"}


def test_concurrent_calls_for_same_goal_materialize_one_coordinator_turn(
    tmp_path,
):
    barrier = threading.Barrier(2)
    runtime = InMemoryCoordinatorRuntime()
    drivers = tuple(
        GoalDriver(
            store_path=tmp_path / "driver.sqlite3",
            reconciler=_BarrierCoordinatorReconciler("goal:same", barrier),
            coordinators=runtime,
            auto_profile=_coordinator_profile(),
            durable=InMemoryDurableGoalControl(),
        )
        for _ in range(2)
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        directives = tuple(
            executor.map(
                lambda driver: driver.run_once(_goal_snapshot("goal:same")),
                drivers,
            )
        )

    assert runtime.auto_create_count == 1
    assert {directive.kind for directive in directives} == {
        "continue_coordinator",
        "wait",
    }
    assert {
        directive.wait_condition
        for directive in directives
        if directive.kind == "wait"
    } == {"coordinator_turn"}


def test_sessionless_coordinator_reservation_recovers_by_stable_action_key(
    tmp_path,
):
    runtime = _CrashAfterCoordinatorCreate()
    driver = GoalDriver(
        store_path=tmp_path / "driver.sqlite3",
        reconciler=_CoordinatorNeededReconciler("goal:restart"),
        coordinators=runtime,
        auto_profile=_coordinator_profile(),
        durable=InMemoryDurableGoalControl(),
        runtime_config={
            "active_turn_pools": {"workers": 8, "coordinators": 1},
            "repositories": {},
        },
    )

    with pytest.raises(GoalDriverError) as stopped:
        driver.run_once(_goal_snapshot("goal:restart"))
    assert stopped.value.code == "SYNTHETIC_CRASH"
    reserved = driver.read_status("local/phase-four-a", "goal:restart")
    assert reserved is not None
    assert reserved.continuation_outstanding is True
    assert reserved.session_id is None

    recovered = driver.run_once(_goal_snapshot("goal:restart"))

    assert recovered.kind == "wait"
    assert recovered.wait_condition == "coordinator_turn"
    assert recovered.session_id is not None
    assert runtime.auto_create_count == 1


def test_kernel_sweep_rechecks_without_an_external_wake(tmp_path):
    reconciler = _SweepThenCompleteReconciler()
    runtime = InMemoryCoordinatorRuntime()
    driver = GoalDriver(
        store_path=tmp_path / "driver.sqlite3",
        reconciler=reconciler,
        coordinators=runtime,
        auto_profile=_coordinator_profile(),
        durable=InMemoryDurableGoalControl(),
    )
    snapshot = GoalSnapshot(
        repository="local/phase-four-a",
        goal_key="goal:sweep",
        objective="Complete all independently waiting nodes.",
        acceptance=("All work is integrated.",),
        plan_digest="a" * 64,
        work_items=(("issue:goal:sweep", "active"),),
        decision_inputs=(),
    )

    waiting = driver.run_once(snapshot)
    finished = driver.run_once(snapshot)

    assert waiting.kind == "wait"
    assert waiting.wait_condition == "kernel_sweep"
    assert finished.kind == "finish"
    assert reconciler.calls == 2
    assert runtime.auto_create_count == 0


def test_kernel_sweep_never_hides_decision_or_coordinator_directives():
    base = _CoordinatorNeededReconciler("goal:sweep").reconcile_once(
        "local/phase-four-a"
    )
    hosted = replace(
        base,
        node_key="node:hosted",
        directive="wait_for_hosted_ci",
        wait_condition="hosted_ci",
        wait_event_identity="hosted:one",
    )
    human_decision = replace(
        base,
        node_key="node:decision",
        directive="wait_for_decision",
        wait_condition="human_decision",
        wait_event_identity="decision:one",
    )
    integration_refresh = replace(
        base,
        node_key="node:integration-refresh",
        directive="invoke_coordinator",
        wait_condition="integration_refresh",
        wait_event_identity="refresh:one",
    )
    runtime_wait = replace(
        base,
        node_key="node:runtime",
        directive="wait_for_runtime",
        wait_condition="runtime_result",
        wait_event_identity="runtime:one",
    )

    assert not Kernel._kernel_sweep_allowed((human_decision, hosted))
    assert not Kernel._kernel_sweep_allowed((integration_refresh, runtime_wait))
    assert Kernel._kernel_sweep_allowed((hosted, runtime_wait))
    assert (
        Kernel._representative_outcome((hosted, human_decision))
        == human_decision
    )
    assert (
        Kernel._representative_outcome((runtime_wait, integration_refresh))
        == integration_refresh
    )
