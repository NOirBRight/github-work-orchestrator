from __future__ import annotations

from dataclasses import replace
import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import gwo_v8  # noqa: E402
import gwo_v8.runtime_gateway as gateway_module  # noqa: E402
from gwo_v8._canonical import digest_value  # noqa: E402
from gwo_v8 import (  # noqa: E402
    CampaignPlanningSubject,
    CampaignStartRuntimeOverrides,
    PermissionResponse,
    ProfileMapping,
    RuntimeCommand,
    RuntimeConfiguration,
    RuntimeGateway,
    RuntimeGatewayError,
    RuntimeRepositoryContext,
    WorkRunSubject,
    build_runtime_gateway,
)
from gwo_v8.runtime import RuntimeProfile  # noqa: E402
from gwo_v8.runtime_gateway import (  # noqa: E402
    ArtifactStore,
    _PermissionRequest,
    _CommandReceipt,
    _RuntimeActionSpec,
    _RuntimeFailure,
    _InMemoryRuntimeProviderAdapter,
    _PaseoCliTransport,
    _PaseoRuntimeProviderAdapter,
)


def _profile() -> RuntimeProfile:
    return RuntimeProfile(
        name="coordinator",
        provider="test",
        model="test-model",
        thinking="high",
        mode="safe",
        features={},
    )


def _subject() -> CampaignPlanningSubject:
    # Artifact refs are filled by _gateway after its semantic identity exists.
    placeholder = "0" * 64
    return CampaignPlanningSubject(
        repository="owner/repository",
        campaign_key="campaign:repair",
        campaign_handle="handle:repair",
        expected_previous_plan_revision_digest=None,
        snapshot_artifact_digest=placeholder,
        policy_witness_digest=placeholder,
        planning_request_artifact_digest=placeholder,
        stable_action_id="planning:repair",
    )


def _put_subject_artifacts(store: ArtifactStore, subject: CampaignPlanningSubject):
    snapshot = store.put_canonical({"tickets": ["issue:111"]})
    policy = store.put_canonical({"policy": "frozen"})
    unsigned = CampaignPlanningSubject(
        repository=subject.repository,
        campaign_key=subject.campaign_key,
        campaign_handle=subject.campaign_handle,
        expected_previous_plan_revision_digest=subject.expected_previous_plan_revision_digest,
        snapshot_artifact_digest=snapshot.digest,
        policy_witness_digest=policy.digest,
        planning_request_artifact_digest="0" * 64,
        stable_action_id=subject.stable_action_id,
    )
    prompt = store.put_canonical(
        {
            "schema_version": "gwo.runtime.prompt.v1",
            "subject_digest": unsigned.prompt_binding_digest,
            "authority_digest": policy.digest,
            "payload": {"complete_contract": "x" * 200_000},
        }
    )
    return CampaignPlanningSubject(
        repository=unsigned.repository,
        campaign_key=unsigned.campaign_key,
        campaign_handle=unsigned.campaign_handle,
        expected_previous_plan_revision_digest=unsigned.expected_previous_plan_revision_digest,
        snapshot_artifact_digest=snapshot.digest,
        policy_witness_digest=policy.digest,
        planning_request_artifact_digest=prompt.digest,
        stable_action_id=unsigned.stable_action_id,
    )


def _gateway(tmp_path: Path):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    profile = _profile()
    adapter = _InMemoryRuntimeProviderAdapter(store)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={"coordinator": ProfileMapping(profile.digest)},
        ),
        _artifacts=store,
    )
    return gateway, store, adapter


def _repository_worktree(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    source.mkdir()
    for arguments in (
        ("init", "--initial-branch", "main"),
        ("config", "user.email", "runtime-gateway@example.test"),
        ("config", "user.name", "Runtime Gateway Test"),
    ):
        subprocess.run(["git", "-C", str(source), *arguments], check=True, capture_output=True)
    (source / "README.md").write_text("runtime gateway\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(source), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "worktree", "add", "-b", "runtime-gateway", str(workspace), "main"],
        check=True,
        capture_output=True,
    )
    return source, workspace


def test_public_package_hides_raw_provider_seam_and_gateway_has_only_three_operations():
    assert gwo_v8.build_runtime_gateway is build_runtime_gateway
    assert gwo_v8.RuntimeRepositoryContext is RuntimeRepositoryContext
    assert not hasattr(gwo_v8, "build_paseo_runtime_gateway")
    assert not hasattr(gwo_v8, "PaseoRepositoryContext")
    assert all(
        "paseo" not in name.casefold()
        and "provider" not in name.casefold()
        and "command" not in name.casefold()
        for name in inspect.signature(build_runtime_gateway).parameters
    )
    gateway_constructor = inspect.signature(RuntimeGateway)
    assert "adapter" not in gateway_constructor.parameters
    assert "artifacts" not in gateway_constructor.parameters
    assert {"_adapter", "_artifacts"} <= set(gateway_constructor.parameters)
    assert tuple(inspect.signature(RuntimeRepositoryContext).parameters) == (
        "path",
        "base_ref",
    )
    assert not hasattr(gwo_v8, "RuntimeProviderAdapter")
    assert not hasattr(gwo_v8, "RuntimeActionSpec")
    assert not hasattr(gwo_v8, "PrepareReceipt")
    assert not hasattr(gwo_v8, "CommandReceipt")
    assert not hasattr(gwo_v8, "GatewayRuntimeObservation")
    assert not hasattr(gwo_v8, "InMemoryRuntimeProviderAdapter")
    assert all(
        not hasattr(gateway_module, name)
        for name in (
            "RuntimeProviderAdapter",
            "RuntimeActionSpec",
            "PrepareReceipt",
            "CommandReceipt",
            "RuntimeObservation",
            "RuntimeEventPage",
            "RuntimeFailure",
            "PermissionRequest",
            "PaseoRuntimeProviderAdapter",
            "InMemoryRuntimeProviderAdapter",
        )
    )
    assert {
        name
        for name in RuntimeGateway.__dict__
        if not name.startswith("_") and callable(getattr(RuntimeGateway, name))
    } == {"planning_preflight", "progress", "transition"}


def test_successor_gateway_does_not_import_predecessor_runtime_adapters():
    source = inspect.getsource(gateway_module)
    assert "PaseoCliClient" not in source
    assert "PaseoRuntimeAdapter" not in source
    assert "InMemoryRuntimeAdapter" not in source


def test_preflight_is_campaign_planning_only_and_cas_binds_subject_options_and_config(tmp_path):
    gateway, store, _adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    override = CampaignStartRuntimeOverrides()

    first = gateway.planning_preflight(subject, override)
    retry = gateway.planning_preflight(subject, override)

    assert retry == first
    changed = CampaignPlanningSubject(
        repository=subject.repository,
        campaign_key=subject.campaign_key,
        campaign_handle=subject.campaign_handle,
        expected_previous_plan_revision_digest=subject.expected_previous_plan_revision_digest,
        snapshot_artifact_digest=subject.snapshot_artifact_digest,
        policy_witness_digest=subject.policy_witness_digest,
        planning_request_artifact_digest=subject.planning_request_artifact_digest,
        stable_action_id=subject.stable_action_id,
    )
    # A changed snapshot is a distinct subject under the same stable action.
    changed = CampaignPlanningSubject(
        **{**changed.__dict__, "snapshot_artifact_digest": store.put_canonical({"changed": True}).digest}
    )
    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.planning_preflight(changed, override)
    assert stopped.value.code == "RUNTIME_PREFLIGHT_IDENTITY_MISMATCH"

    work = WorkRunSubject(
        repository=subject.repository,
        campaign_key=subject.campaign_key,
        campaign_handle=subject.campaign_handle,
        plan_revision_digest=store.put_canonical({"revision": 1}).digest,
        work_run_key="work-run:repair",
        ticket_key="issue:111",
        role="worker",
        prompt_artifact_digest=subject.planning_request_artifact_digest,
        authority_subtree_digest=subject.policy_witness_digest,
        stable_action_id="work:repair",
    )
    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.planning_preflight(work)  # type: ignore[arg-type]
    assert stopped.value.code == "RUNTIME_PREFLIGHT_SUBJECT_INVALID"

    alternate = _profile()
    alternate = replace(alternate, name="alternate")
    restarted = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=_adapter,
        configuration=RuntimeConfiguration(
            profiles={alternate.digest: alternate},
            host_mappings={"coordinator": ProfileMapping(alternate.digest)},
        ),
        _artifacts=store,
    )
    with pytest.raises(RuntimeGatewayError) as stopped:
        restarted.planning_preflight(subject, override)
    assert stopped.value.code == "RUNTIME_PREFLIGHT_IDENTITY_MISMATCH"


def test_artifact_backed_prompt_and_output_are_durable_and_tampering_fails_closed(tmp_path):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    receipt = gateway.planning_preflight(subject)

    completed = gateway.progress(subject, receipt)
    assert completed.planning_output_artifact_digest is not None
    assert store.get(completed.planning_output_artifact_digest)
    assert adapter.last_prompt_byte_lengths[0] > 200_000

    output_path = store.path_for(completed.planning_output_artifact_digest)
    output_path.write_bytes(b"tampered")
    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(subject, receipt)
    assert stopped.value.code == "RUNTIME_ARTIFACT_DIGEST_MISMATCH"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        (lambda path, limit: path.unlink(), "RUNTIME_ARTIFACT_MISSING"),
        (lambda path, limit: path.write_bytes(b"{}"), "RUNTIME_ARTIFACT_DIGEST_MISMATCH"),
        (
            lambda path, limit: path.write_bytes(b"x" * (limit + 1)),
            "RUNTIME_ARTIFACT_TOO_LARGE",
        ),
    ),
)
def test_all_prompt_artifact_read_failures_stop_before_prepare(
    tmp_path, mutation, expected_code
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    receipt = gateway.planning_preflight(subject)
    mutation(store.path_for(subject.planning_request_artifact_digest), 300_000)

    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(subject, receipt)

    assert stopped.value.code == expected_code
    assert adapter.prepare_calls == []


def test_planning_snapshot_and_policy_artifacts_require_canonical_readback(tmp_path):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    receipt = gateway.planning_preflight(subject)
    store.path_for(subject.snapshot_artifact_digest).write_bytes(b"not canonical JSON")

    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(subject, receipt)

    assert stopped.value.code == "RUNTIME_ARTIFACT_DIGEST_MISMATCH"
    assert adapter.prepare_calls == []


def test_missing_completed_output_artifact_fails_closed_on_restart(tmp_path):
    gateway, store, _adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    receipt = gateway.planning_preflight(subject)
    completed = gateway.progress(subject, receipt)
    assert completed.planning_output_artifact_digest is not None
    store.path_for(completed.planning_output_artifact_digest).unlink()

    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(subject, receipt)

    assert stopped.value.code == "RUNTIME_ARTIFACT_MISSING"


def test_authoritative_absence_is_the_only_prepare_authority(tmp_path):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    receipt = gateway.planning_preflight(subject)
    adapter.observe_failure = _RuntimeFailure.transport("synthetic")

    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(subject, receipt)
    assert stopped.value.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert adapter.prepare_calls == []

    adapter.observe_failure = None
    gateway.progress(subject, receipt)
    assert adapter.prepare_calls == [subject.stable_action_id]


@pytest.mark.parametrize(
    "mutate",
    (
        lambda observation: replace(observation, fenced=1),
        lambda observation: replace(
            observation,
            permission_requests=(
                _PermissionRequest(
                    request_id="permission:other",
                    operation_id="write",
                    resource_id="repository:other",
                    binding_ref=observation.binding_ref,
                    authority_subtree_digest="0" * 64,
                    stable_action_id=observation.stable_action_id,
                    subject_digest=observation.subject_digest,
                ),
            ),
        ),
        lambda observation: replace(
            observation,
            permission_requests=tuple(
                _PermissionRequest(
                    request_id="permission:duplicate",
                    operation_id="write",
                    resource_id="repository:one",
                    binding_ref=observation.binding_ref,
                    authority_subtree_digest=observation.authority_subtree_digest,
                    stable_action_id=observation.stable_action_id,
                    subject_digest=observation.subject_digest,
                )
                for _ in range(2)
            ),
        ),
        lambda observation: replace(
            observation,
            permission_requests=("malformed permission request",),
        ),
    ),
)
def test_invalid_typed_fence_or_permission_readback_fails_closed(tmp_path, mutate):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    receipt = gateway.planning_preflight(subject)
    gateway.progress(subject, receipt)
    observe = adapter.observe

    def invalid(stable_action_id):
        observed = observe(stable_action_id)
        assert not isinstance(observed, _RuntimeFailure)
        return mutate(observed)

    adapter.observe = invalid  # type: ignore[method-assign]
    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(subject, receipt)
    assert stopped.value.code == "RUNTIME_OBSERVATION_INVALID"


def test_ambiguous_restart_never_reprepares_an_existing_action(tmp_path):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    receipt = gateway.planning_preflight(subject)
    gateway.progress(subject, receipt)
    adapter.observe_failure = _RuntimeFailure.ambiguous(subject.stable_action_id)
    restarted = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=gateway._configuration,
        _artifacts=store,
    )

    with pytest.raises(RuntimeGatewayError) as stopped:
        restarted.progress(subject, receipt)

    assert stopped.value.code == "RUNTIME_IDENTITY_AMBIGUOUS"
    assert adapter.prepare_calls == [subject.stable_action_id]


@pytest.mark.parametrize(
    "command",
    (
        RuntimeCommand.INTERRUPT,
        RuntimeCommand.FENCE,
        RuntimeCommand.RETIRE,
    ),
)
def test_transition_keeps_binding_private_for_bound_closed_commands(tmp_path, command):
    gateway, store, _adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    receipt = gateway.planning_preflight(subject)
    gateway.progress(subject, receipt)

    result = gateway.transition(subject.stable_action_id, command)

    assert result.stable_action_id == subject.stable_action_id
    assert result.command is command


def test_start_and_resume_require_their_exact_private_observation_states(tmp_path):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    receipt = gateway.planning_preflight(subject)
    record = gateway._assignment_for_progress(subject, gateway._require_preflight(subject, receipt))
    prompt, inputs = gateway._resolve_input_artifacts(subject)
    adapter.prepare(
        _RuntimeActionSpec(
            stable_action_id=subject.stable_action_id,
            subject=subject,
            profile=gateway._profile(record["profile_digest"]),
            prompt_artifact=prompt,
            input_artifacts=inputs,
        )
    )
    started = gateway.transition(subject.stable_action_id, RuntimeCommand.START)
    assert started.command is RuntimeCommand.START
    gateway.transition(subject.stable_action_id, RuntimeCommand.PARK)
    resumed = gateway.transition(subject.stable_action_id, RuntimeCommand.RESUME)
    assert resumed.command is RuntimeCommand.RESUME


def test_transition_readback_rejects_noop_command_receipts_and_fenced_resume(tmp_path):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    receipt = gateway.planning_preflight(subject)
    gateway.progress(subject, receipt)
    gateway.transition(subject.stable_action_id, RuntimeCommand.PARK)
    gateway.transition(subject.stable_action_id, RuntimeCommand.FENCE)

    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.transition(subject.stable_action_id, RuntimeCommand.RESUME)
    assert stopped.value.code == "RUNTIME_COMMAND_INVALID"
    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(subject, receipt)
    assert stopped.value.code == "RUNTIME_COMMAND_INVALID"

    original = adapter.command
    adapter.command = lambda stable_action_id, command: _CommandReceipt(  # type: ignore[method-assign]
        stable_action_id, command
    )
    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.transition(subject.stable_action_id, RuntimeCommand.RETIRE)
    assert stopped.value.code == "RUNTIME_OBSERVATION_INVALID"
    adapter.command = original  # type: ignore[method-assign]


def test_in_memory_conforms_for_permission_and_each_closed_transition(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    subject = _put_subject_artifacts(store, _subject())
    profile = _profile()
    adapter = _InMemoryRuntimeProviderAdapter(
        store,
        pending_permissions={
            subject.stable_action_id: (("request:one", "write", "repository:one"),)
        },
    )
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={"coordinator": ProfileMapping(profile.digest)},
        ),
        _artifacts=store,
    )
    preflight = gateway.planning_preflight(subject)
    gateway.progress(subject, preflight)
    gateway.transition(
        subject.stable_action_id,
        PermissionResponse(request_id="request:one", decision="allow"),
    )
    assert adapter.observe(subject.stable_action_id).permission_requests == ()
    assert gateway.transition(subject.stable_action_id, RuntimeCommand.PARK).status == "parked"
    assert gateway.transition(subject.stable_action_id, RuntimeCommand.RESUME).status == "running"
    assert gateway.transition(subject.stable_action_id, RuntimeCommand.INTERRUPT).status == "parked"
    assert gateway.transition(subject.stable_action_id, RuntimeCommand.FENCE).status == "parked"
    assert gateway.transition(subject.stable_action_id, RuntimeCommand.RETIRE).status == "retired"


class _RecordingPaseoCli:
    def __init__(
        self,
        workspace: Path,
        *,
        fail_workspace: bool = False,
        lose_workspace_ack: bool = False,
        lose_fence_ack_after_effect: bool = False,
        lose_start_ack_after_effect: bool = False,
        hide_agent_queries: int = 0,
        lose_resume_ack_while_idle: bool = False,
        hide_workspace_queries: int = 0,
    ):
        self.workspace = workspace
        self.fail_workspace = fail_workspace
        self.lose_workspace_ack = lose_workspace_ack
        self.lose_fence_ack_after_effect = lose_fence_ack_after_effect
        self.lose_start_ack_after_effect = lose_start_ack_after_effect
        self.hide_agent_queries = hide_agent_queries
        self.lose_resume_ack_while_idle = lose_resume_ack_while_idle
        self.hide_workspace_queries = hide_workspace_queries
        self.commands: list[list[str]] = []
        self.agent: SimpleNamespace | None = None
        self._agent_labels: dict[str, str] = {}
        self.workspaces: list[dict[str, str]] = []
        self.permissions: list[dict[str, str]] = []

    def _run(self, args):
        self.commands.append(list(args))
        if self.fail_workspace:
            raise OSError("provider executable vanished")
        if args[:2] == ["workspace", "ls"]:
            return [] if self._consume_hidden_workspace_query() else list(self.workspaces)
        if args[:2] == ["workspace", "create"]:
            self.workspace.mkdir(parents=True, exist_ok=True)
            slug = args[args.index("--worktree-slug") + 1]
            self.workspaces = [
                {
                    "workspaceId": "workspace:one",
                    "name": slug,
                    "isolation": "worktree",
                    "project": "project:one",
                    "cwd": str(self.workspace),
                }
            ]
            if self.lose_workspace_ack:
                self.lose_workspace_ack = False
                raise OSError("workspace acknowledgement vanished")
            return {"workspace": {"id": "workspace:one", "path": str(self.workspace)}}
        if args[:2] == ["ls", "--global"]:
            requested = {
                args[index + 1].split("=", 1)[0]: args[index + 1].split("=", 1)[1]
                for index, value in enumerate(args)
                if value == "--label"
            }
            return (
                []
                if self.agent is None
                or self._consume_hidden_agent_query()
                or any(self._agent_labels.get(key) != value for key, value in requested.items())
                else [{"id": self.agent.agent_id}]
            )
        if args[:2] == ["permit", "ls"]:
            return list(self.permissions)
        if args[0] == "run":
            labels = {
                args[index + 1].split("=", 1)[0]: args[index + 1].split("=", 1)[1]
                for index, value in enumerate(args)
                if value == "--label"
            }
            self.agent = SimpleNamespace(
                agent_id="agent:one",
                provider=args[args.index("--provider") + 1],
                model=args[args.index("--model") + 1],
                thinking=args[args.index("--thinking") + 1],
                mode=args[args.index("--mode") + 1],
                cwd=args[args.index("--cwd") + 1],
                lifecycle="running",
                archived=False,
            )
            self._agent_labels = labels
            if self.lose_start_ack_after_effect:
                self.lose_start_ack_after_effect = False
                raise TimeoutError("run acknowledgement vanished")
            return {"agent": {"id": self.agent.agent_id}}
        if args[0] == "archive" and self.agent is not None:
            self.agent.lifecycle = "idle"
            self.agent.archived = True
        if args[0] == "stop" and self.agent is not None:
            self.agent.lifecycle = "idle"
        if args[0] == "send" and self.agent is not None:
            if self.lose_resume_ack_while_idle:
                self.lose_resume_ack_while_idle = False
                self.agent.lifecycle = "idle"
                raise TimeoutError("send acknowledgement vanished")
            self.agent.lifecycle = "busy"
        if args[:2] == ["permit", "allow"] or args[:2] == ["permit", "deny"]:
            self.permissions = [item for item in self.permissions if item["id"] != args[3]]
        return {}

    def _consume_hidden_agent_query(self) -> bool:
        if self.hide_agent_queries < 1:
            return False
        self.hide_agent_queries -= 1
        return True

    def _consume_hidden_workspace_query(self) -> bool:
        if not self.workspaces or self.hide_workspace_queries < 1:
            return False
        self.hide_workspace_queries -= 1
        return True

    def _agent(self, value):
        assert value["id"] == "agent:one"
        assert self.agent is not None
        return self.agent

    def inspect(self, _agent_id):
        assert self.agent is not None
        return self.agent

    def update_labels(self, _agent_id, labels):
        assert self.agent is not None
        self._agent_labels.update(labels)
        if self.lose_fence_ack_after_effect:
            self.lose_fence_ack_after_effect = False
            raise TimeoutError("label update acknowledgement vanished")


@pytest.mark.parametrize(
    "payload",
    (
        {
            "Id": "agent:one",
            "Provider": "test",
            "Model": "model",
            "Thinking": "high",
            "Mode": "safe",
            "Cwd": "C:/workspace",
            "Archived": False,
        },
        {
            "Id": "agent:one",
            "Provider": "test",
            "Model": "model",
            "Thinking": "high",
            "Mode": "safe",
            "Cwd": "C:/workspace",
            "Status": "running",
            "Archived": "false",
        },
    ),
)
def test_paseo_inspect_requires_exact_status_and_archived_readback(payload):
    transport = _PaseoCliTransport("paseo")
    transport._run = lambda _args: payload  # type: ignore[method-assign]

    with pytest.raises(ValueError):
        transport.inspect("agent:one")


def test_production_adapter_constructs_staged_paseo_commands_and_readbacks(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    profile = _profile()
    prompt = store.get(subject.planning_request_artifact_digest)
    prepared = adapter.prepare(
        _RuntimeActionSpec(
            stable_action_id=subject.stable_action_id,
            subject=subject,
            profile=profile,
            prompt_artifact=prompt,
            input_artifacts=(
                prompt,
                store.get(subject.snapshot_artifact_digest),
                store.get(subject.policy_witness_digest),
            ),
        )
    )

    assert not isinstance(prepared, _RuntimeFailure)
    observed = adapter.observe(subject.stable_action_id)
    assert observed.agent_id is None
    assert observed.binding_ref is None
    started = adapter.command(subject.stable_action_id, RuntimeCommand.START)
    assert not isinstance(started, _RuntimeFailure)
    bound = adapter.observe(subject.stable_action_id)
    assert bound.agent_id == "agent:one"
    assert bound.binding_ref == "paseo:agent:one"
    assert bound.session_id == "paseo-agent:agent:one"
    assert set(adapter._actions[subject.stable_action_id]["input_files"]) == {
        subject.planning_request_artifact_digest,
        subject.snapshot_artifact_digest,
        subject.policy_witness_digest,
    }
    command_text = "\n".join(" ".join(item) for item in client.commands)
    assert "workspace create --isolation worktree" in command_text
    assert "run --background" in command_text
    assert "--workspace workspace:one" in command_text
    assert "--output-schema" in command_text
    assert "gwo.runtime_action=planning:repair" in command_text
    assert "x" * 10_000 not in command_text
    assert "logs agent:one" not in command_text


def test_production_adapter_recovers_workspace_ack_loss_and_uses_exact_permission_id(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace, lose_workspace_ack=True)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    profile = _profile()
    prompt = store.get(subject.planning_request_artifact_digest)
    prepared = adapter.prepare(
        _RuntimeActionSpec(subject.stable_action_id, subject, profile, prompt, (prompt,))
    )
    assert not isinstance(prepared, _RuntimeFailure)
    assert sum(command[:2] == ["workspace", "create"] for command in client.commands) == 1
    adapter.command(subject.stable_action_id, RuntimeCommand.START)
    client.permissions = [
        {
            "agentId": "agent:one",
            "id": "request:one",
            "operation": "write",
            "resource": "repository:one",
        }
    ]
    bound = adapter.observe(subject.stable_action_id)
    assert not isinstance(bound, _RuntimeFailure)
    assert [item.request_id for item in bound.permission_requests] == ["request:one"]
    response = adapter.command(
        subject.stable_action_id,
        PermissionResponse(request_id="request:one", decision="allow"),
    )
    assert not isinstance(response, _RuntimeFailure)
    assert ["permit", "allow", "agent:one", "request:one", "--json"] in client.commands
    assert all(
        "--all" not in command
        for command in client.commands
        if command[:2] in (["permit", "allow"], ["permit", "deny"])
    )


def test_production_workspace_ack_loss_with_temporary_absence_never_recreates(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(
        workspace, lose_workspace_ack=True, hide_workspace_queries=1
    )
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)

    first = adapter.prepare(
        _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))
    )

    assert isinstance(first, _RuntimeFailure)
    assert first.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert subject.stable_action_id in adapter._workspace_intents
    recovered = adapter.prepare(
        _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))
    )
    assert not isinstance(recovered, _RuntimeFailure)
    assert sum(command[:2] == ["workspace", "create"] for command in client.commands) == 1
    assert subject.stable_action_id not in adapter._workspace_intents


def test_production_permission_item_without_owner_fails_closed(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    assert not isinstance(
        adapter.prepare(_RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))),
        _RuntimeFailure,
    )
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.START), _RuntimeFailure)
    client.permissions = [{"id": "permission:unknown", "operation": "write", "resource": "repo"}]

    observed = adapter.observe(subject.stable_action_id)

    assert isinstance(observed, _RuntimeFailure)
    assert observed.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"


def test_production_workspace_slug_ambiguity_fails_before_create(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    slug = digest_value(
        {"repository": subject.repository, "stable_action_id": subject.stable_action_id}
    )[:24]
    client.workspaces = [
        {
            "workspaceId": f"workspace:{index}",
            "name": slug,
            "isolation": "worktree",
            "cwd": str(workspace),
        }
        for index in ("one", "two")
    ]

    result = adapter.prepare(
        _RuntimeActionSpec(
            subject.stable_action_id,
            subject,
            _profile(),
            store.get(subject.planning_request_artifact_digest),
            (),
        )
    )

    assert isinstance(result, _RuntimeFailure)
    assert result.code == "RUNTIME_IDENTITY_AMBIGUOUS"
    assert all(command[:2] != ["workspace", "create"] for command in client.commands)


def test_production_prepared_readback_revalidates_all_staged_artifacts(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    assert not isinstance(
        adapter.prepare(
            _RuntimeActionSpec(
                subject.stable_action_id,
                subject,
                _profile(),
                prompt,
                (prompt, store.get(subject.snapshot_artifact_digest)),
            )
        ),
        _RuntimeFailure,
    )
    record = adapter._actions[subject.stable_action_id]
    Path(record["input_files"][subject.snapshot_artifact_digest]).write_bytes(b"tampered")

    observed = adapter.observe(subject.stable_action_id)

    assert isinstance(observed, _RuntimeFailure)
    assert observed.code == "RUNTIME_ARTIFACT_DIGEST_MISMATCH"


def test_production_missing_state_never_adopts_an_existing_labeled_agent(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    first = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "first-actions.json",
    )
    assert not isinstance(
        first.prepare(_RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))),
        _RuntimeFailure,
    )
    assert not isinstance(first.command(subject.stable_action_id, RuntimeCommand.START), _RuntimeFailure)
    restarted_without_state = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "missing-actions.json",
    )

    result = restarted_without_state.prepare(
        _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))
    )

    assert isinstance(result, _RuntimeFailure)
    assert result.code == "RUNTIME_ACTION_STATE_MISSING"


def test_production_events_persist_authoritative_lifecycle_and_fence_wakes(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    profile = _profile()
    prompt = store.get(subject.planning_request_artifact_digest)
    assert not isinstance(
        adapter.prepare(_RuntimeActionSpec(subject.stable_action_id, subject, profile, prompt, (prompt,))),
        _RuntimeFailure,
    )
    prepared_events = adapter.events(None)
    assert [event.kind for event in prepared_events.events] == ["state:prepared"]
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.START), _RuntimeFailure)
    running_events = adapter.events(prepared_events.next_cursor)
    assert [event.kind for event in running_events.events] == ["state:running"]
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.FENCE), _RuntimeFailure)
    fence_events = adapter.events(running_events.next_cursor)
    assert [event.kind for event in fence_events.events] == ["state:running"]


def test_production_fence_timeout_after_effect_recovers_through_pending_intent(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(
        workspace, lose_fence_ack_after_effect=True
    )
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    assert not isinstance(
        adapter.prepare(_RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))),
        _RuntimeFailure,
    )
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.START), _RuntimeFailure)

    result = adapter.command(subject.stable_action_id, RuntimeCommand.FENCE)

    assert isinstance(result, _RuntimeFailure)
    assert result.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert adapter._actions[subject.stable_action_id]["pending_fence"] is True
    observed = adapter.observe(subject.stable_action_id)
    assert not isinstance(observed, _RuntimeFailure)
    assert observed.fenced is True
    assert adapter._actions[subject.stable_action_id]["pending_fence"] is False


def test_production_start_ack_loss_with_temporary_absence_never_relaunches(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace, lose_start_ack_after_effect=True)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    assert not isinstance(
        adapter.prepare(_RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))),
        _RuntimeFailure,
    )
    client.hide_agent_queries = 1

    result = adapter.command(subject.stable_action_id, RuntimeCommand.START)

    assert isinstance(result, _RuntimeFailure)
    assert adapter._actions[subject.stable_action_id]["pending_start"] is True
    pending = adapter.observe(subject.stable_action_id)
    assert isinstance(pending, _RuntimeFailure)
    assert pending.code == "RUNTIME_MATERIALIZATION_PENDING"
    assert sum(command[0] == "run" for command in client.commands) == 1
    recovered = adapter.observe(subject.stable_action_id)
    assert not isinstance(recovered, _RuntimeFailure)
    assert adapter._actions[subject.stable_action_id]["pending_start"] is False
    assert adapter._actions[subject.stable_action_id]["bound_agent_id"] == "agent:one"


def test_production_resume_ack_loss_with_idle_status_never_resends(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    assert not isinstance(
        adapter.prepare(_RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))),
        _RuntimeFailure,
    )
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.START), _RuntimeFailure)
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.PARK), _RuntimeFailure)
    assert adapter.observe(subject.stable_action_id).lifecycle == "parked"
    client.lose_resume_ack_while_idle = True

    result = adapter.command(subject.stable_action_id, RuntimeCommand.RESUME)

    assert isinstance(result, _RuntimeFailure)
    pending = adapter.observe(subject.stable_action_id)
    assert isinstance(pending, _RuntimeFailure)
    assert pending.code == "RUNTIME_MATERIALIZATION_PENDING"
    assert sum(command[0] == "send" for command in client.commands) == 1


def test_production_archived_readback_wins_over_idle_status_for_retirement(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    assert not isinstance(
        adapter.prepare(_RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))),
        _RuntimeFailure,
    )
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.START), _RuntimeFailure)
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.RETIRE), _RuntimeFailure)

    observed = adapter.observe(subject.stable_action_id)

    assert not isinstance(observed, _RuntimeFailure)
    assert observed.lifecycle == "retired"


def test_production_bound_agent_cannot_disappear_or_change_identity(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    assert not isinstance(
        adapter.prepare(_RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))),
        _RuntimeFailure,
    )
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.START), _RuntimeFailure)
    assert not isinstance(adapter.observe(subject.stable_action_id), _RuntimeFailure)
    assert adapter._actions[subject.stable_action_id]["bound_agent_id"] == "agent:one"

    client.agent = None
    missing = adapter.observe(subject.stable_action_id)
    assert isinstance(missing, _RuntimeFailure)
    assert missing.code == "RUNTIME_BINDING_MISSING"

    client.agent = SimpleNamespace(
        agent_id="agent:two",
        provider="test",
        model="test-model",
        thinking="high",
        mode="safe",
        cwd=str(workspace),
        lifecycle="running",
        archived=False,
    )
    changed = adapter.observe(subject.stable_action_id)
    assert isinstance(changed, _RuntimeFailure)
    assert changed.code == "RUNTIME_IDENTITY_AMBIGUOUS"


def test_production_profile_features_fail_closed_when_cli_cannot_prove_them(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    adapter = _PaseoRuntimeProviderAdapter(
        client=_RecordingPaseoCli(workspace),  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    result = adapter.prepare(
        _RuntimeActionSpec(
            subject.stable_action_id,
            subject,
            replace(_profile(), features={"unsupported": True}),
            store.get(subject.planning_request_artifact_digest),
            (),
        )
    )
    assert isinstance(result, _RuntimeFailure)
    assert result.code == "RUNTIME_CONFIGURATION_INVALID"


def test_production_adapter_only_accepts_workspace_result_artifact_for_completion(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    profile = _profile()
    prompt = store.get(subject.planning_request_artifact_digest)
    adapter.prepare(_RuntimeActionSpec(subject.stable_action_id, subject, profile, prompt, (prompt,)))
    adapter.command(subject.stable_action_id, RuntimeCommand.START)
    assert client.agent is not None
    client.agent.lifecycle = "idle"
    missing = adapter.observe(subject.stable_action_id)
    assert isinstance(missing, _RuntimeFailure)
    assert missing.code == "RUNTIME_LIFECYCLE_UNKNOWN"
    result_ref = store.put_canonical(
        {
            "schema_version": "gwo.runtime.output.v1",
            "subject_digest": subject.digest,
            "stable_action_id": subject.stable_action_id,
            "authority_digest": subject.authority_digest,
            "payload": {"result": "complete"},
        }
    )
    result_path = Path(adapter._actions[subject.stable_action_id]["result_file"])
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_bytes(Path(result_ref.path).read_bytes())
    completed = adapter.observe(subject.stable_action_id)
    assert not isinstance(completed, _RuntimeFailure)
    assert completed.output_artifact_digest is not None


def test_production_adapter_normalizes_native_failures_without_vendor_detail(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    adapter = _PaseoRuntimeProviderAdapter(
        client=_RecordingPaseoCli(workspace, fail_workspace=True),  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    result = adapter.prepare(
        _RuntimeActionSpec(
            stable_action_id=subject.stable_action_id,
            subject=subject,
            profile=_profile(),
            prompt_artifact=store.get(subject.planning_request_artifact_digest),
            input_artifacts=(),
        )
    )

    assert isinstance(result, _RuntimeFailure)
    assert result.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert "executable" not in result.detail


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
def test_private_adapter_shared_lifecycle_conformance(adapter_kind, tmp_path):
    """The private seam has one lifecycle contract across both implementations."""

    store = ArtifactStore(tmp_path / "artifacts")
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    client: _RecordingPaseoCli | None = None
    if adapter_kind == "memory":
        adapter = _InMemoryRuntimeProviderAdapter(
            store,
            pending_permissions={
                subject.stable_action_id: (("request:one", "write", "repository:one"),)
            },
        )
    else:
        source, workspace = _repository_worktree(tmp_path)
        client = _RecordingPaseoCli(workspace)
        adapter = _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
            state_path=tmp_path / "paseo-actions.json",
        )
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))

    assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
    prepared = adapter.observe(subject.stable_action_id)
    assert not isinstance(prepared, _RuntimeFailure)
    assert prepared.binding_ref is None
    assert isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.PARK), _RuntimeFailure)
    initial_events = adapter.events(None)
    assert [event.kind for event in initial_events.events] == ["state:prepared"]
    assert adapter.events(initial_events.next_cursor).events == ()

    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.START), _RuntimeFailure)
    if client is not None:
        client.permissions = [
            {
                "agentId": "agent:one",
                "id": "request:one",
                "operation": "write",
                "resource": "repository:one",
            }
        ]
    bound = adapter.observe(subject.stable_action_id)
    assert not isinstance(bound, _RuntimeFailure)
    assert bound.binding_ref is not None
    assert bound.lifecycle == "running"
    assert [request.request_id for request in bound.permission_requests] == ["request:one"]

    assert not isinstance(
        adapter.command(
            subject.stable_action_id,
            PermissionResponse(request_id="request:one", decision="allow"),
        ),
        _RuntimeFailure,
    )
    after_permission = adapter.observe(subject.stable_action_id)
    assert not isinstance(after_permission, _RuntimeFailure)
    assert after_permission.permission_requests == ()
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.INTERRUPT), _RuntimeFailure)
    assert adapter.observe(subject.stable_action_id).lifecycle == "parked"
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.RESUME), _RuntimeFailure)
    assert adapter.observe(subject.stable_action_id).lifecycle == "running"
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.PARK), _RuntimeFailure)
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.RESUME), _RuntimeFailure)
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.FENCE), _RuntimeFailure)
    assert adapter.observe(subject.stable_action_id).fenced is True
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.RETIRE), _RuntimeFailure)
    assert adapter.observe(subject.stable_action_id).lifecycle == "retired"
    if client is not None:
        assert ["archive", "agent:one", "--force", "--json"] in client.commands

    changed_events = adapter.events(initial_events.next_cursor)
    assert [event.kind for event in changed_events.events] == ["state:retired"]
    assert adapter.events(changed_events.next_cursor).events == ()
    invalid_cursor = adapter.events("-1")
    assert isinstance(invalid_cursor, _RuntimeFailure)
    assert invalid_cursor.code == "RUNTIME_EVENT_CURSOR_INVALID"


def test_production_workspace_pins_exact_base_commit_across_main_movement(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    state_path = tmp_path / "paseo-actions.json"
    context = RuntimeRepositoryContext(source, "main")
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": context},
        state_path=state_path,
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    assert not isinstance(
        adapter.prepare(_RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))),
        _RuntimeFailure,
    )
    pinned = adapter._actions[subject.stable_action_id]["workspace_base_commit"]
    create = next(command for command in client.commands if command[:2] == ["workspace", "create"])
    assert create[create.index("--base") + 1] == pinned
    assert context.path == source.resolve()

    (source / "README.md").write_text("main moved\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(source), "commit", "-m", "move main"],
        check=True,
        capture_output=True,
    )
    moved = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "main^{commit}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert moved != pinned
    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": context},
        state_path=state_path,
    )
    observed = restarted.observe(subject.stable_action_id)
    assert not isinstance(observed, _RuntimeFailure)
    assert observed.workspace_id == "workspace:one"
    assert restarted._actions[subject.stable_action_id]["workspace_base_commit"] == pinned


def test_production_rejects_source_checkout_as_workspace_before_staging(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, _workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(source)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    result = adapter.prepare(
        _RuntimeActionSpec(
            subject.stable_action_id,
            subject,
            _profile(),
            store.get(subject.planning_request_artifact_digest),
            (),
        )
    )
    assert isinstance(result, _RuntimeFailure)
    assert result.code == "RUNTIME_IDENTITY_AMBIGUOUS"
    assert not (source / ".gwo").exists()


def test_production_schema_tamper_stops_start_before_run(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    assert not isinstance(
        adapter.prepare(_RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))),
        _RuntimeFailure,
    )
    Path(adapter._actions[subject.stable_action_id]["output_schema_file"]).write_bytes(b"tampered")

    result = adapter.command(subject.stable_action_id, RuntimeCommand.START)

    assert isinstance(result, _RuntimeFailure)
    assert result.code == "RUNTIME_ARTIFACT_DIGEST_MISMATCH"
    assert all(command[0] != "run" for command in client.commands)


@pytest.mark.parametrize(
    ("surface", "payload"),
    (
        ("agents", [{"id": "agent:one"}, "malformed"]),
        ("workspaces", [{"workspaceId": "workspace:one", "cwd": "C:/missing-name"}]),
    ),
)
def test_production_list_readback_rejects_non_dict_and_malformed_identity(
    surface, payload, tmp_path
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    native_run = client._run

    def malformed_run(args):
        if surface == "agents" and args[:2] == ["ls", "--global"]:
            return payload
        if surface == "workspaces" and args[:2] == ["workspace", "ls"]:
            return payload
        return native_run(args)

    client._run = malformed_run  # type: ignore[method-assign]
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    result = adapter.prepare(
        _RuntimeActionSpec(
            subject.stable_action_id,
            subject,
            _profile(),
            store.get(subject.planning_request_artifact_digest),
            (),
        )
    )
    assert isinstance(result, _RuntimeFailure)
    assert result.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert all(command[:2] != ["workspace", "create"] for command in client.commands)


def test_production_permission_ordering_and_events_hash_full_request_records(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    assert not isinstance(
        adapter.prepare(_RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))),
        _RuntimeFailure,
    )
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.START), _RuntimeFailure)
    client.permissions = [
        {"agentId": "agent:one", "id": "request:z", "operation": "write", "resource": "repo:z"},
        {"agentId": "agent:one", "id": "request:a", "operation": "read", "resource": "repo:a"},
    ]
    observed = adapter.observe(subject.stable_action_id)
    assert not isinstance(observed, _RuntimeFailure)
    assert [request.request_id for request in observed.permission_requests] == ["request:a", "request:z"]
    first = adapter.events(None)
    assert not isinstance(first, _RuntimeFailure)
    client.permissions.reverse()
    assert adapter.events(first.next_cursor).events == ()
    client.permissions[0]["operation"] = "write+review"
    changed = adapter.events(first.next_cursor)
    assert [event.kind for event in changed.events] == ["state:running"]


def test_production_idle_permission_response_readback_proves_effect_but_closed_fails(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    assert not isinstance(
        adapter.prepare(_RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))),
        _RuntimeFailure,
    )
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.START), _RuntimeFailure)
    assert client.agent is not None
    client.agent.lifecycle = "idle"
    client.permissions = [
        {"agentId": "agent:one", "id": "request:idle", "operation": "write", "resource": "repo"}
    ]
    pending = adapter.observe(subject.stable_action_id)
    assert not isinstance(pending, _RuntimeFailure)
    assert pending.lifecycle == "running"
    assert not isinstance(
        adapter.command(
            subject.stable_action_id,
            PermissionResponse(request_id="request:idle", decision="allow"),
        ),
        _RuntimeFailure,
    )
    recovered = adapter.observe(subject.stable_action_id)
    assert not isinstance(recovered, _RuntimeFailure)
    assert recovered.lifecycle == "running"
    record = adapter._actions[subject.stable_action_id]
    assert "pending_permission_response" not in record

    client.permissions = [
        {"agentId": "agent:one", "id": "request:closed", "operation": "write", "resource": "repo"}
    ]
    client.agent.lifecycle = "closed"
    result = adapter.command(
        subject.stable_action_id,
        PermissionResponse(request_id="request:closed", decision="allow"),
    )
    assert isinstance(result, _RuntimeFailure)
    assert result.code == "RUNTIME_LIFECYCLE_UNKNOWN"


def test_paseo_cli_argument_safety_and_length_prevent_subprocess(monkeypatch):
    transport = _PaseoCliTransport("paseo")
    calls: list[list[str]] = []

    def subprocess_should_not_run(args, **_kwargs):
        calls.append(args)
        raise AssertionError("Paseo subprocess must not run")

    monkeypatch.setattr(gateway_module.subprocess, "run", subprocess_should_not_run)
    with pytest.raises(RuntimeGatewayError) as unsafe:
        transport._run(["inspect", "agent&bad", "--json"])
    assert unsafe.value.code == "RUNTIME_VENDOR_ARGUMENT_INVALID"
    with pytest.raises(RuntimeGatewayError) as oversized:
        transport._run(["run", "x" * 8_000, "--json"])
    assert oversized.value.code == "RUNTIME_VENDOR_ARGUMENT_INVALID"
    assert calls == []


def test_production_rejects_unsafe_subject_identity_before_any_cli_call(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(
        store, replace(_subject(), stable_action_id="planning&unsafe")
    )
    result = adapter.prepare(
        _RuntimeActionSpec(
            subject.stable_action_id,
            subject,
            _profile(),
            store.get(subject.planning_request_artifact_digest),
            (),
        )
    )
    assert isinstance(result, _RuntimeFailure)
    assert result.code == "RUNTIME_VENDOR_ARGUMENT_INVALID"
    assert client.commands == []


def test_production_rejects_provider_ids_before_retransmitting_them_to_paseo(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    assert not isinstance(
        adapter.prepare(_RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))),
        _RuntimeFailure,
    )
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.START), _RuntimeFailure)
    assert client.agent is not None
    client.agent.agent_id = "agent&bad"
    before = list(client.commands)

    result = adapter.observe(subject.stable_action_id)

    assert isinstance(result, _RuntimeFailure)
    assert result.code == "RUNTIME_VENDOR_ARGUMENT_INVALID"
    assert all(command[0] != "inspect" for command in client.commands[len(before):])


def test_permission_response_receipts_bind_exact_request_and_decision(tmp_path):
    digests: set[str] = set()
    for index, response in enumerate(
        (
            PermissionResponse(request_id="request:one", decision="allow"),
            PermissionResponse(request_id="request:two", decision="allow"),
            PermissionResponse(request_id="request:one", decision="deny"),
        )
    ):
        case_path = tmp_path / str(index)
        store = ArtifactStore(case_path / "artifacts")
        subject = _put_subject_artifacts(store, _subject())
        profile = _profile()
        adapter = _InMemoryRuntimeProviderAdapter(
            store,
            pending_permissions={
                subject.stable_action_id: (
                    ("request:one", "write", "repo:one"),
                    ("request:two", "write", "repo:two"),
                )
            },
        )
        gateway = RuntimeGateway(
            store_path=case_path / "gateway.journal",
            _adapter=adapter,
            configuration=RuntimeConfiguration(
                profiles={profile.digest: profile},
                host_mappings={"coordinator": ProfileMapping(profile.digest)},
            ),
            _artifacts=store,
        )
        preflight = gateway.planning_preflight(subject)
        gateway.progress(subject, preflight)
        receipt = gateway.transition(subject.stable_action_id, response)
        digests.add(receipt.receipt_digest)
        assert receipt.command == response
    assert len(digests) == 3


def test_gateway_wakes_filter_other_campaigns_and_advance_unknown_cursor(tmp_path):
    gateway, store, adapter = _gateway(tmp_path)
    first_subject = _put_subject_artifacts(store, _subject())
    second_subject = _put_subject_artifacts(
        store,
        replace(
            _subject(),
            campaign_key="campaign:other",
            campaign_handle="handle:other",
            stable_action_id="planning:other",
        ),
    )
    first_preflight = gateway.planning_preflight(first_subject)
    second_preflight = gateway.planning_preflight(second_subject)
    gateway.progress(first_subject, first_preflight)
    gateway.progress(second_subject, second_preflight)

    isolated = gateway.progress(first_subject, first_preflight, wake_cursor=None)

    assert isolated.wake_hints
    assert all(first_subject.stable_action_id in hint for hint in isolated.wake_hints)
    assert all(second_subject.stable_action_id not in hint for hint in isolated.wake_hints)
    assert isolated.wake_cursor is not None
    adapter._events.append(
        gateway_module._RuntimeEvent(  # type: ignore[attr-defined]
            cursor=str(len(adapter._events) + 1),
            stable_action_id="unknown:action",
            kind="state:running",
        )
    )
    unknown = gateway.progress(
        first_subject, first_preflight, wake_cursor=isolated.wake_cursor
    )
    assert unknown.wake_hints == ()
    assert unknown.wake_cursor == str(len(adapter._events))


def test_gateway_permission_response_idle_readback_and_unremoved_request_fail_closed(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "paseo-actions.json",
    )
    profile = _profile()
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={"coordinator": ProfileMapping(profile.digest)},
        ),
        _artifacts=store,
    )
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    gateway.progress(subject, preflight)
    assert client.agent is not None
    client.agent.lifecycle = "idle"
    client.permissions = [
        {"agentId": "agent:one", "id": "request:idle", "operation": "write", "resource": "repo"}
    ]
    accepted = gateway.transition(
        subject.stable_action_id,
        PermissionResponse(request_id="request:idle", decision="allow"),
    )
    assert accepted.status == "running"
    assert "pending_permission_response" not in adapter._actions[subject.stable_action_id]

    client.permissions = [
        {"agentId": "agent:one", "id": "request:kept", "operation": "write", "resource": "repo"}
    ]
    native_run = client._run

    def retain_permission(args):
        if args[:2] == ["permit", "allow"]:
            client.commands.append(list(args))
            return {}
        return native_run(args)

    client._run = retain_permission  # type: ignore[method-assign]
    with pytest.raises(RuntimeGatewayError) as kept:
        gateway.transition(
            subject.stable_action_id,
            PermissionResponse(request_id="request:kept", decision="allow"),
        )
    assert kept.value.code == "RUNTIME_OBSERVATION_INVALID"
