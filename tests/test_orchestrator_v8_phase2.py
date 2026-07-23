from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    ActivationCheckpointCrash,
    ActivationError,
    GitHubContent,
    GitHubDurablePlanControl,
    CoordinatorSession,
    CoordinatorTurnObservation,
    EvidenceVerifier,
    GoalDriver,
    GoalSnapshot,
    InMemoryDurablePlanControl,
    InMemoryPaseoClient,
    InMemorySkillCatalog,
    InstalledSkillCatalog,
    ImplementGwoEntry,
    Kernel,
    LocalPlanPublication,
    PaseoRuntimeAdapter,
    PaseoCoordinatorRuntime,
    PlanCompiler,
    RuntimeAdmission,
    RuntimeProfile,
    RuntimePrompt,
    ReconcileOutcome,
    InMemoryCoordinatorRuntime,
)


class _MemoryGitHubContentClient:
    def __init__(self):
        self._blobs: dict[tuple[str, str, str], GitHubContent] = {}

    def read(
        self,
        repository: str,
        branch: str,
        path: str,
    ) -> GitHubContent | None:
        return self._blobs.get((repository, branch, path))

    def compare_and_swap(
        self,
        repository: str,
        branch: str,
        path: str,
        content: bytes,
        *,
        expected_blob_sha: str | None,
        message: str,
    ) -> GitHubContent:
        del message
        key = (repository, branch, path)
        current = self._blobs.get(key)
        current_sha = None if current is None else current.blob_sha
        if current_sha != expected_blob_sha:
            raise ActivationError("GITHUB_CAS_CONFLICT", "blob changed")
        written = GitHubContent(
            content=content,
            blob_sha=hashlib.sha256(content).hexdigest(),
        )
        self._blobs[key] = written
        return written


def _ready_source(*, state: str = "ready-for-agent") -> dict:
    return {
        "repository": "local/phase-two",
        "work_items": [
            {
                "work_item_key": "issue:42",
                "tracker_state": state,
                "source_ref": "synthetic://issue/42",
                "title": "Write the phase-two artifact",
                "outcome_contract": {"path": "result.txt", "content": "phase-2\n"},
            }
        ],
    }


def _plan_intent(*, skill_reference: str | None = None) -> dict:
    return {
        "parent_plan_digest": None,
        "goals": [
            {
                "goal_key": "goal:phase-2",
                "objective": "Integrate one durably activated candidate.",
                "acceptance": ["result.txt contains phase-2"],
            }
        ],
        "nodes": [
            {
                "goal_key": "goal:phase-2",
                "work_item_key": "issue:42",
                "kind": "work",
                "inputs": {
                    "file_changes": [{"path": "result.txt", "content": "phase-2\n"}]
                },
                "output_contract": {
                    "required_evidence": [
                        {"kind": "candidate"},
                        {"kind": "check", "check_id": "result-content"},
                    ],
                    "checks": [
                        {
                            "check_id": "result-content",
                            "command": [
                                "python",
                                "-c",
                                (
                                    "from pathlib import Path; "
                                    "assert Path('result.txt').read_text() == 'phase-2\\n'"
                                ),
                            ],
                        }
                    ],
                },
                "effect_contract": {
                    "write_scopes": ["result.txt"],
                    "external_effects": [],
                },
                "resource_claims": [],
                "runtime_requirements": {"capabilities": ["git", "local_check"]},
                "difficulty": "routine",
                "risk": "low",
                "recovery_policy": {"semantic_attempts": 1, "repair_rounds": 0},
                "skill_reference": skill_reference,
            }
        ],
        "edges": [],
    }


def _compiled():
    return PlanCompiler().compile(_plan_intent(), _ready_source(), {"version": 1})


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
    _git(repository, "config", "user.name", "Phase Two")
    _git(repository, "config", "user.email", "phase-two@example.invalid")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "base")
    return repository


def test_activation_commits_durable_receipt_before_store_activation(tmp_path):
    compiled = _compiled()
    durable = InMemoryDurablePlanControl()
    publication = LocalPlanPublication(tmp_path / "v8.sqlite3", durable=durable)

    outcome = publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="v8-generation-1",
    )

    receipt = durable.read_activation(
        compiled.repository,
        outcome.activation_id,
    )
    active = publication.read_active(compiled.repository)
    assert outcome.status == "active"
    assert receipt is not None
    assert receipt.plan_digest == compiled.digest
    assert receipt.writer_generation == "v8-generation-1"
    assert receipt.plan_record_ref == durable.plan_record_ref(
        compiled.repository,
        compiled.digest,
    )
    assert active is not None
    assert active.plan_digest == compiled.digest
    assert active.canonical_bytes == compiled.canonical_bytes
    assert durable.read_plan(
        compiled.repository,
        compiled.digest,
    ).canonical_bytes == compiled.canonical_bytes


@pytest.mark.parametrize(
    "checkpoint",
    [
        "pending_reserved",
        "plan_published",
        "plan_read_back",
        "receipt_published",
        "receipt_read_back",
    ],
)
def test_activation_rolls_forward_after_a_crash_at_every_boundary(
    tmp_path,
    checkpoint,
):
    compiled = _compiled()
    durable = InMemoryDurablePlanControl()
    crashed = False

    def crash_once(name: str) -> None:
        nonlocal crashed
        if name == checkpoint and not crashed:
            crashed = True
            raise ActivationCheckpointCrash(name)

    first = LocalPlanPublication(
        tmp_path / "v8.sqlite3",
        durable=durable,
        checkpoint=crash_once,
    )
    with pytest.raises(ActivationCheckpointCrash):
        first.publish_and_activate(
            compiled,
            expected_active_digest=None,
            writer_generation="v8-generation-1",
        )
    with pytest.raises(ActivationError) as pending:
        first.read_active(compiled.repository)
    assert pending.value.code == "ACTIVATION_PENDING"

    recovered = LocalPlanPublication(
        tmp_path / "v8.sqlite3",
        durable=durable,
    )
    outcome = recovered.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="v8-generation-1",
    )

    assert outcome.status == "active"
    assert recovered.read_active(compiled.repository).plan_digest == compiled.digest
    assert durable.activation_count(compiled.repository) == 1
    assert durable.plan_count(compiled.repository) == 1


def test_activation_ambiguous_commit_is_adopted_without_duplicate_receipt(tmp_path):
    compiled = _compiled()
    durable = InMemoryDurablePlanControl(
        fail_once_after={"publish_activation"},
    )
    publication = LocalPlanPublication(tmp_path / "v8.sqlite3", durable=durable)

    with pytest.raises(ActivationError) as ambiguous:
        publication.publish_and_activate(
            compiled,
            expected_active_digest=None,
            writer_generation="v8-generation-1",
        )
    assert ambiguous.value.code == "DURABLE_STATE_AMBIGUOUS"
    with pytest.raises(ActivationError) as pending:
        publication.read_active(compiled.repository)
    assert pending.value.code == "ACTIVATION_PENDING"

    outcome = publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="v8-generation-1",
    )

    assert outcome.status == "active"
    assert durable.activation_count(compiled.repository) == 1


def test_writer_generation_fences_activation_and_admission(tmp_path):
    compiled = _compiled()
    durable = InMemoryDurablePlanControl()
    owner = LocalPlanPublication(tmp_path / "v8.sqlite3", durable=durable)
    owner.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="v8-generation-1",
    )

    competing = LocalPlanPublication(tmp_path / "v8.sqlite3", durable=durable)
    with pytest.raises(ActivationError) as rejected:
        competing.publish_and_activate(
            compiled,
            expected_active_digest=compiled.digest,
            writer_generation="v8-generation-2",
        )

    assert rejected.value.code == "WRITER_GENERATION_CONFLICT"
    assert owner.read_active(compiled.repository).writer_generation == (
        "v8-generation-1"
    )


def test_durable_activation_receipts_are_immutable(tmp_path):
    compiled = _compiled()
    durable = InMemoryDurablePlanControl()
    publication = LocalPlanPublication(tmp_path / "v8.sqlite3", durable=durable)
    outcome = publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="v8-generation-1",
    )
    original = durable.read_activation(compiled.repository, outcome.activation_id)

    with pytest.raises(ActivationError) as rejected:
        durable.publish_activation(
            original.with_plan_digest("f" * 64),
            expected_previous_digest=compiled.digest,
        )

    assert rejected.value.code == "ACTIVATION_RECEIPT_IMMUTABLE"
    assert durable.read_activation(
        compiled.repository,
        outcome.activation_id,
    ) == original


def test_github_control_branch_preserves_exact_compiler_bytes_and_cas(tmp_path):
    compiled = _compiled()
    client = _MemoryGitHubContentClient()
    durable = GitHubDurablePlanControl(client, branch="gwo-control")
    publication = LocalPlanPublication(tmp_path / "v8.sqlite3", durable=durable)

    outcome = publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="v8-generation-1",
    )
    recovered_control = GitHubDurablePlanControl(client, branch="gwo-control")

    assert recovered_control.read_plan(
        compiled.repository,
        compiled.digest,
    ).canonical_bytes == compiled.canonical_bytes
    assert recovered_control.read_activation(
        compiled.repository,
        outcome.activation_id,
    ).plan_digest == compiled.digest


def test_paseo_adapter_round_trips_identity_prompt_lifecycle_and_retirement(
    tmp_path,
):
    compiled = _compiled()
    node = next(
        item
        for item in json.loads(compiled.canonical_bytes)["nodes"]
        if item["kind"] == "work"
    )
    profile = RuntimeProfile(
        name="worker-standard",
        provider="kimi-cli",
        model="kimi-code/kimi-for-coding",
        thinking="max",
        mode="yolo",
        features={},
    )
    admission = RuntimeAdmission(
        repository=compiled.repository,
        plan_digest=compiled.digest,
        node_key=node["node_key"],
        admission_id="admission:phase-two",
        repository_path=tmp_path,
        base_sha="a" * 40,
        runtime_profile=profile,
        parent_agent_id="coordinator:manual",
    )
    prompt = RuntimePrompt.from_node(
        node,
        skill_catalog=InMemorySkillCatalog({"tdd": "Use a red-green loop."}),
    )
    client = InMemoryPaseoClient()
    adapter = PaseoRuntimeAdapter(client)

    adapter.materialize(admission, prompt)
    binding = adapter.read_binding(admission.admission_id)

    assert binding is not None
    assert binding.repository == admission.repository
    assert binding.plan_digest == admission.plan_digest
    assert binding.node_key == admission.node_key
    assert binding.admission_id == admission.admission_id
    assert binding.parent_agent_id == "coordinator:manual"
    assert binding.provider == profile.provider
    assert binding.model == profile.model
    assert binding.thinking == profile.thinking
    assert binding.mode == profile.mode
    assert binding.features_digest == hashlib.sha256(b"{}").hexdigest()
    assert binding.runtime_profile == profile.name
    assert binding.profile_digest == profile.digest
    assert binding.prompt_accepted is True
    assert binding.prompt_digest == prompt.digest
    assert binding.agent_id
    assert binding.session_id
    assert binding.workspace_id

    adapter.attach_attempt(binding, "attempt:phase-two:1")
    binding = adapter.read_binding(admission.admission_id)
    assert binding is not None
    assert binding.attempt_id == "attempt:phase-two:1"
    adapter.interrupt(binding)
    assert adapter.observe(binding).lifecycle == "idle"
    adapter.resume(binding)
    assert adapter.observe(binding).lifecycle == "running"
    adapter.retire(binding)
    assert adapter.read_binding(admission.admission_id) is None


def test_prompt_snapshot_resolves_current_optional_skill_without_authority():
    compiled = PlanCompiler().compile(
        _plan_intent(skill_reference="tdd"),
        _ready_source(),
        {"version": 1},
    )
    node = next(
        item
        for item in json.loads(compiled.canonical_bytes)["nodes"]
        if item["kind"] == "work"
    )
    catalog = InMemorySkillCatalog({"tdd": "First guidance"})

    first = RuntimePrompt.from_node(node, skill_catalog=catalog)
    catalog.set("tdd", "Updated guidance")
    second = RuntimePrompt.from_node(node, skill_catalog=catalog)

    assert first.skill_name == "tdd"
    assert first.skill_digest != second.skill_digest
    assert first.digest != second.digest
    assert "First guidance" in first.text
    assert "Updated guidance" not in first.text
    assert first.authority_digest == node["contract_digest"]


def test_missing_optional_skill_warns_and_keeps_the_base_prompt():
    compiled = PlanCompiler().compile(
        _plan_intent(skill_reference="diagnosing-bugs"),
        _ready_source(),
        {"version": 1},
    )
    node = next(
        item
        for item in json.loads(compiled.canonical_bytes)["nodes"]
        if item["kind"] == "work"
    )

    prompt = RuntimePrompt.from_node(
        node,
        skill_catalog=InMemorySkillCatalog({}),
    )

    assert prompt.skill_name == "diagnosing-bugs"
    assert prompt.skill_digest is None
    assert prompt.warnings == ("SKILL_GUIDANCE_MISSING",)
    assert node["node_key"] in prompt.text


def test_installed_skill_catalog_reads_current_guidance_without_versioning(
    tmp_path,
):
    skill = tmp_path / "skills" / "tdd"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("First installed guidance\n", encoding="utf-8")
    catalog = InstalledSkillCatalog((tmp_path / "skills",))

    assert catalog.resolve("tdd") == "First installed guidance\n"
    (skill / "SKILL.md").write_text("Updated installed guidance\n", encoding="utf-8")
    assert catalog.resolve("tdd") == "Updated installed guidance\n"
    assert catalog.resolve("../tdd") is None


def _activated_publication(tmp_path: Path, compiled):
    publication = LocalPlanPublication(
        tmp_path / "v8.sqlite3",
        durable=InMemoryDurablePlanControl(),
    )
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="v8-generation-1",
    )
    return publication


def _paseo_kernel(
    tmp_path: Path,
    compiled,
    *,
    client: InMemoryPaseoClient,
    catalog: InMemorySkillCatalog | None = None,
):
    repository = _temporary_repository(tmp_path)
    publication = _activated_publication(tmp_path, compiled)
    profile = RuntimeProfile(
        name="worker-standard",
        provider="kimi-cli",
        model="kimi-code/kimi-for-coding",
        thinking="max",
        mode="yolo",
        features={},
    )
    return Kernel(
        store_path=tmp_path / "v8.sqlite3",
        publication=publication,
        runtime=PaseoRuntimeAdapter(client),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="v8-generation-1",
        runtime_profile=profile,
        parent_agent_id="coordinator:manual",
        skill_catalog=catalog,
    )


def test_materialization_retries_three_executions_without_consuming_attempt(
    tmp_path,
):
    compiled = _compiled()
    client = InMemoryPaseoClient(
        create_failures=("transient", "transient", "transient"),
    )
    kernel = _paseo_kernel(tmp_path, compiled, client=client)

    outcomes = [
        kernel.reconcile_once(compiled.repository)
        for _ in range(4)
    ]

    assert [item.materialization_executions for item in outcomes] == [1, 2, 3, 3]
    assert [item.admission_state for item in outcomes] == [
        "materialization_retry",
        "materialization_retry",
        "materialization_blocked",
        "materialization_blocked",
    ]
    assert [item.directive for item in outcomes] == [
        "retry_materialization",
        "wait_for_runtime_circuit",
        "request_decision",
        "request_decision",
    ]
    assert outcomes[1].runtime_circuit == "paseo:materialize:transient"
    assert all(item.attempt_id is None for item in outcomes)
    assert client.create_count == 3


def test_permanent_materialization_rejection_opens_circuit_immediately(
    tmp_path,
):
    compiled = _compiled()
    client = InMemoryPaseoClient(create_failures=("permanent",))
    kernel = _paseo_kernel(tmp_path, compiled, client=client)

    outcome = kernel.reconcile_once(compiled.repository)

    assert outcome.admission_state == "materialization_blocked"
    assert outcome.directive == "request_decision"
    assert outcome.attempt_id is None
    assert outcome.materialization_executions == 1
    assert outcome.runtime_circuit == "paseo:materialize:permanent"
    assert client.create_count == 1


def test_ambiguous_materialization_protects_admission_then_adopts_same_agent(
    tmp_path,
):
    compiled = _compiled()
    client = InMemoryPaseoClient(create_failures=("ambiguous_after_create",))
    kernel = _paseo_kernel(tmp_path, compiled, client=client)

    ambiguous = kernel.reconcile_once(compiled.repository)
    adopted = kernel.reconcile_once(compiled.repository)

    assert ambiguous.admission_state == "materialization_ambiguous"
    assert ambiguous.attempt_id is None
    assert adopted.admission_state == "consumed"
    assert adopted.attempt_id is not None
    assert client.create_count == 1
    assert len(client.find_by_labels({"gwo.admission": ambiguous.admission_id})) == 1


def test_kernel_restart_reuses_frozen_prompt_after_installed_skill_changes(
    tmp_path,
):
    compiled = PlanCompiler().compile(
        _plan_intent(skill_reference="tdd"),
        _ready_source(),
        {"version": 1},
    )
    catalog = InMemorySkillCatalog({"tdd": "Original guidance"})
    client = InMemoryPaseoClient(create_failures=("transient",))
    first_kernel = _paseo_kernel(
        tmp_path,
        compiled,
        client=client,
        catalog=catalog,
    )

    first = first_kernel.reconcile_once(compiled.repository)
    catalog.set("tdd", "Changed guidance")
    restarted = Kernel(
        store_path=tmp_path / "v8.sqlite3",
        publication=first_kernel.publication,
        runtime=PaseoRuntimeAdapter(client),
        verifier=EvidenceVerifier(),
        repository_path=first_kernel.repository_path,
        integration_branch="main",
        writer_generation="v8-generation-1",
        runtime_profile=first_kernel.runtime_profile,
        parent_agent_id="coordinator:manual",
        skill_catalog=catalog,
    )
    second = restarted.reconcile_once(compiled.repository)

    assert first.attempt_id is None
    assert second.attempt_id is not None
    assert len(set(client.create_prompt_digests)) == 1
    assert client.create_count == 2


@pytest.mark.parametrize(
    ("entry_input", "next_action"),
    [
        (
            {
                "kind": "work_item",
                "work_items": [{"key": "issue:7", "tracker_state": "needs-triage"}],
            },
            "/triage",
        ),
        (
            {
                "kind": "goal",
                "goal": {"objective": "Build it"},
                "work_items": [],
            },
            "/to-spec",
        ),
        (
            {
                "kind": "spec",
                "spec": {"status": "accepted", "acceptance": ["works"]},
                "work_items": [],
            },
            "/to-tickets",
        ),
    ],
)
def test_implement_gwo_unready_input_fails_closed_without_plain_implement(
    entry_input,
    next_action,
):
    decision = ImplementGwoEntry().route(entry_input)

    assert decision.status == "not_ready"
    assert decision.next_action == next_action
    assert decision.execution_entry is None
    assert "implement" not in decision.fallbacks


@pytest.mark.parametrize("kind", ["work_item", "ready_set", "goal"])
def test_implement_gwo_accepts_only_explicit_ready_work_items(kind):
    request = {
        "kind": kind,
        "goal": {"objective": "Integrate the ready set"},
        "work_items": [
            {"key": "issue:7", "tracker_state": "ready-for-agent"},
            {"key": "issue:8", "tracker_state": "ready-for-agent"},
        ],
    }
    if kind == "work_item":
        request["work_items"] = request["work_items"][:1]

    decision = ImplementGwoEntry().route(request)

    assert decision.status == "ready"
    assert decision.execution_entry == "implement-gwo"
    assert decision.next_action is None
    assert decision.work_item_keys == tuple(
        item["key"] for item in request["work_items"]
    )


def test_phase_two_skill_surface_has_new_entry_and_alias_only():
    root = Path(__file__).resolve().parents[1]
    implement_gwo = (root / "skills" / "implement-gwo" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    alias = (root / "skills" / "orchestrator" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "name: implement-gwo" in implement_gwo
    assert "never fall back to `/implement`" in implement_gwo
    assert "`ready-for-agent`" in implement_gwo
    assert "compatibility alias" in alias
    assert "$implement-gwo" in alias
    assert "V8.1" in alias
    assert "frontier scan" not in alias


class _SequenceReconciler:
    def __init__(self, outcomes: list[ReconcileOutcome]):
        self._outcomes = list(outcomes)
        self.calls = 0

    def reconcile_once(self, repository: str) -> ReconcileOutcome:
        del repository
        self.calls += 1
        if len(self._outcomes) > 1:
            return self._outcomes.pop(0)
        return self._outcomes[0]


def _reconcile_outcome(
    *,
    directive: str = "invoke_coordinator",
    status: str = "waiting",
    goal_state: str = "active",
    wait_condition: str | None = None,
) -> ReconcileOutcome:
    return ReconcileOutcome(
        status=status,
        directive=directive,
        repository="local/phase-two",
        plan_digest="a" * 64,
        goal_key="goal:phase-two",
        goal_state=goal_state,
        work_item_key="issue:42",
        work_item_state="active",
        node_key="node:work",
        admission_id="admission:phase-two",
        admission_state="consumed",
        attempt_id="attempt:phase-two:1",
        attempt_state="running",
        candidate_sha=None,
        result_digest=None,
        materialization_executions=1,
        wait_condition=wait_condition,
    )


def _goal_snapshot(*, work_state: str = "active") -> GoalSnapshot:
    return GoalSnapshot(
        repository="local/phase-two",
        goal_key="goal:phase-two",
        objective="Complete all accepted Phase 2 work.",
        acceptance=("All Ready Work Items are integrated.",),
        plan_digest="a" * 64,
        work_items=(("issue:42", work_state),),
        decision_inputs=(),
    )


def _coordinator_profile() -> RuntimeProfile:
    return RuntimeProfile(
        name="coordinator_auto",
        provider="kimi-cli",
        model="kimi-code/k3",
        thinking="max",
        mode="yolo",
        features={},
    )


def test_goal_driver_resumes_manual_coordinator_when_campaign_stops_silently(
    tmp_path,
):
    reconciler = _SequenceReconciler([_reconcile_outcome()])
    coordinators = InMemoryCoordinatorRuntime(
        manual_session=CoordinatorSession(
            session_id="coordinator:manual",
            agent_id="agent:manual",
            usable=True,
            manually_created=True,
            runtime_profile="manual-runtime",
        )
    )
    driver = GoalDriver(
        store_path=tmp_path / "driver.sqlite3",
        reconciler=reconciler,
        coordinators=coordinators,
        auto_profile=_coordinator_profile(),
    )

    directive = driver.run_once(_goal_snapshot())

    assert directive.kind == "continue_coordinator"
    assert directive.session_id == "coordinator:manual"
    assert directive.corrective is False
    assert coordinators.continue_count == 1
    assert coordinators.auto_create_count == 0


def test_goal_driver_named_wait_uses_no_coordinator_turn(tmp_path):
    reconciler = _SequenceReconciler(
        [
            _reconcile_outcome(
                directive="wait_for_runtime",
                wait_condition="runtime_result",
            )
        ]
    )
    coordinators = InMemoryCoordinatorRuntime()
    driver = GoalDriver(
        store_path=tmp_path / "driver.sqlite3",
        reconciler=reconciler,
        coordinators=coordinators,
        auto_profile=_coordinator_profile(),
    )

    directive = driver.run_once(_goal_snapshot())

    assert directive.kind == "wait"
    assert directive.wait_condition == "runtime_result"
    assert coordinators.continue_count == 0
    assert coordinators.auto_create_count == 0


def test_goal_driver_named_wait_is_ineligible_until_new_durable_wake(tmp_path):
    reconciler = _SequenceReconciler(
        [
            _reconcile_outcome(
                directive="wait_for_runtime",
                wait_condition="runtime_result",
            ),
            _reconcile_outcome(),
        ]
    )
    coordinators = InMemoryCoordinatorRuntime()
    driver = GoalDriver(
        store_path=tmp_path / "driver.sqlite3",
        reconciler=reconciler,
        coordinators=coordinators,
        auto_profile=_coordinator_profile(),
    )

    first_wait = driver.run_once(_goal_snapshot())
    unchanged = driver.run_once(_goal_snapshot())
    woken = driver.run_once(
        _goal_snapshot(),
        wake_reference="paseo://event/result-1",
    )

    assert first_wait.kind == "wait"
    assert unchanged.kind == "wait"
    assert unchanged.wait_condition == "runtime_result"
    assert reconciler.calls == 2
    assert woken.kind == "continue_coordinator"
    assert coordinators.auto_create_count == 1


def test_goal_driver_bounds_unchanged_zero_outcome_then_opens_decision_gate(
    tmp_path,
):
    reconciler = _SequenceReconciler([_reconcile_outcome()])
    coordinators = InMemoryCoordinatorRuntime(
        manual_session=CoordinatorSession(
            session_id="coordinator:manual",
            agent_id="agent:manual",
            usable=True,
            manually_created=True,
            runtime_profile="manual-runtime",
        )
    )
    driver = GoalDriver(
        store_path=tmp_path / "driver.sqlite3",
        reconciler=reconciler,
        coordinators=coordinators,
        auto_profile=_coordinator_profile(),
    )
    snapshot = _goal_snapshot()
    digest = driver.semantic_input_digest(snapshot)

    first = driver.run_once(snapshot)
    corrective = driver.run_once(
        snapshot,
        observation=CoordinatorTurnObservation(
            goal_key=snapshot.goal_key,
            semantic_input_digest=digest,
            session_id=first.session_id,
            outcome="zero_outcome",
            durable_reference="paseo://turn/1",
            token_use=100_000,
            tool_calls=500,
            agent_liveness="running",
        ),
    )
    decision = driver.run_once(
        snapshot,
        observation=CoordinatorTurnObservation(
            goal_key=snapshot.goal_key,
            semantic_input_digest=digest,
            session_id=corrective.session_id,
            outcome="zero_outcome",
            durable_reference="paseo://turn/2",
            token_use=1,
            tool_calls=0,
            agent_liveness="idle",
        ),
    )

    assert corrective.kind == "continue_coordinator"
    assert corrective.corrective is True
    assert corrective.session_id == first.session_id
    assert decision.kind == "decision"
    assert decision.decision_gate == "coordinator_zero_outcome"
    assert coordinators.continue_count == 2
    status = driver.read_status(snapshot.repository, snapshot.goal_key)
    assert status.zero_outcomes == 2
    assert status.semantic_input_digest == digest


def test_goal_driver_waits_for_outstanding_turn_instead_of_sampling_again(
    tmp_path,
):
    coordinators = InMemoryCoordinatorRuntime()
    driver = GoalDriver(
        store_path=tmp_path / "driver.sqlite3",
        reconciler=_SequenceReconciler([_reconcile_outcome()]),
        coordinators=coordinators,
        auto_profile=_coordinator_profile(),
    )

    first = driver.run_once(_goal_snapshot())
    duplicate_wake = driver.run_once(_goal_snapshot())

    assert first.kind == "continue_coordinator"
    assert duplicate_wake.kind == "wait"
    assert duplicate_wake.wait_condition == "coordinator_turn"
    assert coordinators.auto_create_count == 1
    assert coordinators.continue_count == 1


def test_goal_driver_auto_creation_uses_exact_kimi_k3_max_role_profile(tmp_path):
    profile = _coordinator_profile()
    coordinators = InMemoryCoordinatorRuntime()
    driver = GoalDriver(
        store_path=tmp_path / "driver.sqlite3",
        reconciler=_SequenceReconciler([_reconcile_outcome()]),
        coordinators=coordinators,
        auto_profile=profile,
    )

    directive = driver.run_once(_goal_snapshot())

    assert directive.kind == "continue_coordinator"
    assert coordinators.auto_profiles == [profile]
    assert directive.runtime_profile == "coordinator_auto"


def test_goal_driver_production_paseo_coordinator_reads_back_auto_profile(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    client = InMemoryPaseoClient()
    coordinators = PaseoCoordinatorRuntime(
        client,
        repository_path=repository,
        base_sha=_git(repository, "rev-parse", "HEAD"),
    )
    driver = GoalDriver(
        store_path=tmp_path / "driver.sqlite3",
        reconciler=_SequenceReconciler([_reconcile_outcome()]),
        coordinators=coordinators,
        auto_profile=_coordinator_profile(),
    )

    directive = driver.run_once(_goal_snapshot())
    agents = client.find_by_labels(
        {
            "gwo.goal": "goal:phase-two",
            "gwo.role": "coordinator",
            "gwo.auto": "true",
        }
    )

    assert directive.kind == "continue_coordinator"
    assert directive.runtime_profile == "coordinator_auto"
    assert len(agents) == 1
    assert agents[0].provider == "kimi-cli"
    assert agents[0].model == "kimi-code/k3"


def test_goal_driver_finishes_only_from_verified_kernel_completion(tmp_path):
    driver = GoalDriver(
        store_path=tmp_path / "driver.sqlite3",
        reconciler=_SequenceReconciler(
            [
                _reconcile_outcome(
                    directive="goal_complete",
                    status="complete",
                    goal_state="completed",
                )
            ]
        ),
        coordinators=InMemoryCoordinatorRuntime(),
        auto_profile=_coordinator_profile(),
    )

    directive = driver.run_once(_goal_snapshot(work_state="integrated"))

    assert directive.kind == "finish"
    assert directive.wait_condition is None
    assert directive.session_id is None
