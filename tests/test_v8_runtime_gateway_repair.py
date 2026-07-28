from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from copy import deepcopy
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
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
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    adapter._pending_permissions[subject.stable_action_id] = [
        ("request:active", "write", "repository")
    ]
    receipt = gateway.planning_preflight(subject)
    gateway.progress(subject, receipt)

    result = gateway.transition(subject.stable_action_id, command)

    assert result.stable_action_id == subject.stable_action_id
    assert result.command is command


def test_start_and_resume_require_their_exact_private_observation_states(tmp_path):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    receipt = gateway.planning_preflight(subject)
    adapter._pending_permissions[subject.stable_action_id] = [
        ("request:active", "write", "repository")
    ]
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
    adapter._pending_permissions[subject.stable_action_id] = [
        ("request:active", "write", "repository")
    ]
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
            subject.stable_action_id: (
                ("request:one", "write", "repository:one"),
                ("request:final", "write", "repository:two"),
            )
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
    assert [
        request.request_id
        for request in adapter.observe(subject.stable_action_id).permission_requests
    ] == ["request:final"]
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
        # Internal fake state follows inspect's actionable full-id projection.
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
            return [
                {
                    "id": item["id"][:8],
                    "agentId": item["agentId"],
                    "agentShortId": item["agentId"][:7],
                    "name": item["tool"],
                    "description": item["description"],
                }
                for item in self.permissions
            ]
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
            exact = [item for item in self.permissions if item["id"] == args[3]]
            selected = exact or [item for item in self.permissions if item["id"].startswith(args[3])]
            if len(selected) != 1:
                raise OSError("permission id is ambiguous")
            self.permissions.remove(selected[0])
            return [{
                "requestId": args[3][:8],
                "agentId": args[2],
                "agentShortId": args[2][:7],
                "name": selected[0]["tool"],
                "result": "allowed" if args[1] == "allow" else "denied",
            }]
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
        pending = tuple(
            (item["id"], item["tool"])
            for item in self.permissions
            if item["agentId"] == self.agent.agent_id
        )
        payload = dict(vars(self.agent))
        payload["pending_permissions"] = pending
        return SimpleNamespace(**payload)

    def update_labels(self, _agent_id, labels):
        assert self.agent is not None
        self._agent_labels.update(labels)
        if self.lose_fence_ack_after_effect:
            self.lose_fence_ack_after_effect = False
            raise TimeoutError("label update acknowledgement vanished")


def _paseo_permission(
    request_id: str,
    *,
    tool: str = "write",
    description: str = "repository:one",
    agent_id: str = "agent:one",
) -> dict[str, str]:
    """One real-shape fake permission: inspect full ID plus permit descriptor."""

    return {
        "id": request_id,
        "agentId": agent_id,
        "tool": tool,
        "description": description,
    }


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


def test_paseo_inspect_reads_real_pending_permission_shape():
    transport = _PaseoCliTransport("paseo")
    full_id = "permit001-full-opaque-provider-id"
    transport._run = lambda _args: {  # type: ignore[method-assign]
        "id": "agent:one",
        "provider": "test",
        "model": "model",
        "thinking": "high",
        "mode": "safe",
        "cwd": "C:/workspace",
        "status": "running",
        "archived": False,
        "PendingPermissions": [{"id": full_id, "tool": "filesystem.write"}],
    }

    observed = transport.inspect("agent:one")

    assert observed.pending_permissions == ((full_id, "filesystem.write"),)


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
    client.permissions = [_paseo_permission("request:one")]
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
    # The fake's own provider-state shape is malformed; its permit-list
    # projection therefore fails before a policy request can be emitted.
    client.permissions = [{"id": "permission:unknown", "tool": "write", "description": "repo"}]

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


def test_production_fence_pre_effect_failure_clears_exact_absent_claim_and_retries(
    tmp_path,
):
    (
        store,
        source,
        _workspace,
        client,
        adapter,
        subject,
        _spec,
    ) = _prepared_paseo_adapter(tmp_path)
    action_id = subject.stable_action_id
    adapter._persist_record_update(
        adapter._actions[action_id],
        lambda updated: updated.__setitem__(
            "unrelated_reconciliation_marker", {"must": "survive"}
        ),
    )
    native_update_labels = client.update_labels
    update_attempts = 0
    successful_updates = 0

    def fail_once_before_effect(agent_id, labels):
        nonlocal update_attempts, successful_updates
        update_attempts += 1
        if update_attempts == 1:
            raise TimeoutError("label update failed before effect")
        successful_updates += 1
        return native_update_labels(agent_id, labels)

    client.update_labels = fail_once_before_effect  # type: ignore[method-assign]
    first = adapter.command(action_id, RuntimeCommand.FENCE)

    assert isinstance(first, _RuntimeFailure)
    assert first.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    durable_pending = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    assert durable_pending["actions"][action_id]["pending_fence"] is True
    assert isinstance(
        durable_pending["actions"][action_id]["pending_fence_claim_id"], str
    )
    assert durable_pending["actions"][action_id]["pending_fence_quiesced"] is True
    assert durable_pending["actions"][action_id]["fenced"] is False
    assert durable_pending["actions"][action_id][
        "unrelated_reconciliation_marker"
    ] == {"must": "survive"}

    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=adapter._state_path,
    )
    restarted._save = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
        OSError("negative fence reconciliation save failed")
    )
    failed_reconciliation = restarted.observe(action_id)

    assert isinstance(failed_reconciliation, _RuntimeFailure)
    assert failed_reconciliation.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert restarted._actions == durable_pending["actions"]
    assert json.loads(adapter._state_path.read_text(encoding="utf-8")) == durable_pending

    recovered = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=adapter._state_path,
    )
    exact_absence = recovered.observe(action_id)

    assert not isinstance(exact_absence, _RuntimeFailure)
    assert exact_absence.fenced is False
    cleared = recovered._actions[action_id]
    assert cleared["pending_fence"] is False
    assert cleared["pending_fence_claim_id"] is None
    assert cleared["pending_fence_quiesced"] is False
    assert cleared["fenced"] is False
    assert cleared["unrelated_reconciliation_marker"] == {"must": "survive"}

    retried = recovered.command(action_id, RuntimeCommand.FENCE)
    assert isinstance(retried, _CommandReceipt)
    assert update_attempts == 2
    assert successful_updates == 1
    final = recovered.observe(action_id)
    assert not isinstance(final, _RuntimeFailure)
    assert final.fenced is True
    assert recovered._actions[action_id]["pending_fence"] is False
    assert recovered._actions[action_id]["pending_fence_claim_id"] is None
    assert recovered._actions[action_id]["pending_fence_quiesced"] is False
    assert recovered._actions[action_id]["unrelated_reconciliation_marker"] == {
        "must": "survive"
    }


def test_inflight_fence_claim_cannot_be_cleared_or_reissued_until_owner_quiesces(
    tmp_path,
):
    (
        store,
        source,
        _workspace,
        client,
        owner,
        subject,
        _spec,
    ) = _prepared_paseo_adapter(tmp_path)
    contender = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=owner._state_path,
    )
    action_id = subject.stable_action_id
    native_update_labels = client.update_labels
    update_entered = threading.Event()
    release_update = threading.Event()
    update_attempts = 0
    successful_updates = 0

    def block_first_update_before_effect(agent_id, labels):
        nonlocal update_attempts, successful_updates
        update_attempts += 1
        if update_attempts == 1:
            update_entered.set()
            assert release_update.wait(5)
            raise TimeoutError("in-flight label update failed before effect")
        successful_updates += 1
        return native_update_labels(agent_id, labels)

    client.update_labels = block_first_update_before_effect  # type: ignore[method-assign]
    owner_results: list[object] = []
    worker = threading.Thread(
        target=lambda: owner_results.append(
            owner.command(action_id, RuntimeCommand.FENCE)
        )
    )
    worker.start()
    assert update_entered.wait(5)
    try:
        while_inflight = contender.observe(action_id)
        duplicate = contender.command(action_id, RuntimeCommand.FENCE)
    finally:
        release_update.set()
    worker.join(10)

    assert not worker.is_alive()
    assert not isinstance(while_inflight, _RuntimeFailure)
    assert while_inflight.fenced is False
    assert isinstance(duplicate, _RuntimeFailure)
    assert duplicate.code == "RUNTIME_MATERIALIZATION_PENDING"
    assert update_attempts == 1
    assert len(owner_results) == 1
    assert isinstance(owner_results[0], _RuntimeFailure)
    assert owner_results[0].code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    quiesced = json.loads(owner._state_path.read_text(encoding="utf-8"))[
        "actions"
    ][action_id]
    assert quiesced["pending_fence"] is True
    assert isinstance(quiesced["pending_fence_claim_id"], str)
    assert quiesced["pending_fence_quiesced"] is True
    assert quiesced["fenced"] is False

    recovered = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=owner._state_path,
    )
    exact_absence = recovered.observe(action_id)
    assert not isinstance(exact_absence, _RuntimeFailure)
    assert recovered._actions[action_id]["pending_fence"] is False
    assert recovered._actions[action_id]["pending_fence_claim_id"] is None
    assert recovered._actions[action_id]["pending_fence_quiesced"] is False

    retried = recovered.command(action_id, RuntimeCommand.FENCE)
    assert isinstance(retried, _CommandReceipt)
    assert update_attempts == 2
    assert successful_updates == 1
    final = recovered.observe(action_id)
    assert not isinstance(final, _RuntimeFailure)
    assert final.fenced is True


def test_successful_fence_call_without_label_readback_never_gets_negative_retry(
    tmp_path,
):
    (
        _store,
        _source,
        _workspace,
        client,
        adapter,
        subject,
        _spec,
    ) = _prepared_paseo_adapter(tmp_path)
    action_id = subject.stable_action_id
    update_attempts = 0

    def accept_without_visible_effect(_agent_id, _labels):
        nonlocal update_attempts
        update_attempts += 1

    client.update_labels = accept_without_visible_effect  # type: ignore[method-assign]
    accepted = adapter.command(action_id, RuntimeCommand.FENCE)
    assert isinstance(accepted, _CommandReceipt)

    absent = adapter.observe(action_id)
    assert not isinstance(absent, _RuntimeFailure)
    assert absent.fenced is False
    assert adapter._actions[action_id]["pending_fence"] is True
    assert isinstance(adapter._actions[action_id]["pending_fence_claim_id"], str)
    assert adapter._actions[action_id]["pending_fence_quiesced"] is False
    retry = adapter.command(action_id, RuntimeCommand.FENCE)
    assert isinstance(retry, _RuntimeFailure)
    assert retry.code == "RUNTIME_MATERIALIZATION_PENDING"
    assert update_attempts == 1


def test_legacy_pending_fence_without_quiesced_proof_never_gets_takeover(tmp_path):
    (
        store,
        source,
        _workspace,
        client,
        adapter,
        subject,
        _spec,
    ) = _prepared_paseo_adapter(tmp_path)
    action_id = subject.stable_action_id

    def make_legacy_pending(updated):
        updated["pending_fence"] = True
        updated.pop("pending_fence_quiesced", None)

    adapter._persist_record_update(adapter._actions[action_id], make_legacy_pending)
    update_attempts = 0

    def record_update(_agent_id, _labels):
        nonlocal update_attempts
        update_attempts += 1

    client.update_labels = record_update  # type: ignore[method-assign]
    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=adapter._state_path,
    )

    observed = restarted.observe(action_id)
    assert not isinstance(observed, _RuntimeFailure)
    assert observed.fenced is False
    assert restarted._actions[action_id]["pending_fence"] is True
    assert "pending_fence_claim_id" not in restarted._actions[action_id]
    assert "pending_fence_quiesced" not in restarted._actions[action_id]
    retry = restarted.command(action_id, RuntimeCommand.FENCE)
    assert isinstance(retry, _RuntimeFailure)
    assert retry.code == "RUNTIME_MATERIALIZATION_PENDING"
    assert update_attempts == 0


@pytest.mark.parametrize(
    ("pending", "claim_id", "quiesced"),
    (
        (False, None, True),
        (True, None, True),
        (False, "residual-fence-claim", False),
    ),
)
def test_invalid_fence_quiescence_evidence_fails_closed(
    tmp_path, pending, claim_id, quiesced
):
    (
        _store,
        _source,
        _workspace,
        _client,
        adapter,
        subject,
        _spec,
    ) = _prepared_paseo_adapter(tmp_path)
    action_id = subject.stable_action_id
    adapter._persist_record_update(
        adapter._actions[action_id],
        lambda updated: updated.update(
            {
                "pending_fence": pending,
                "pending_fence_claim_id": claim_id,
                "pending_fence_quiesced": quiesced,
            }
        ),
    )

    observed = adapter.observe(action_id)

    assert isinstance(observed, _RuntimeFailure)
    assert observed.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"


def test_label_present_observer_wins_before_fence_owner_returns_ack_loss(
    tmp_path,
):
    (
        store,
        source,
        _workspace,
        client,
        owner,
        subject,
        _spec,
    ) = _prepared_paseo_adapter(tmp_path)
    observer = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=owner._state_path,
    )
    action_id = subject.stable_action_id
    native_update_labels = client.update_labels
    effect_visible = threading.Event()
    release_owner = threading.Event()
    update_attempts = 0

    def apply_then_block_before_ack_loss(agent_id, labels):
        nonlocal update_attempts
        update_attempts += 1
        native_update_labels(agent_id, labels)
        effect_visible.set()
        assert release_owner.wait(5)
        raise TimeoutError("label effect applied but acknowledgement lost")

    client.update_labels = apply_then_block_before_ack_loss  # type: ignore[method-assign]
    owner_results: list[object] = []
    worker = threading.Thread(
        target=lambda: owner_results.append(
            owner.command(action_id, RuntimeCommand.FENCE)
        )
    )
    worker.start()
    assert effect_visible.wait(5)
    try:
        converged = observer.observe(action_id)
    finally:
        release_owner.set()
    worker.join(10)

    assert not worker.is_alive()
    assert not isinstance(converged, _RuntimeFailure)
    assert converged.fenced is True
    assert len(owner_results) == 1
    assert isinstance(owner_results[0], _RuntimeFailure)
    assert owner_results[0].code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    durable = json.loads(owner._state_path.read_text(encoding="utf-8"))["actions"][
        action_id
    ]
    assert durable["fenced"] is True
    assert durable["pending_fence"] is False
    assert durable["pending_fence_claim_id"] is None
    assert durable["pending_fence_quiesced"] is False
    assert update_attempts == 1


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
                subject.stable_action_id: (
                    ("permit01-one", "write", "repository:one"),
                    ("permit02-final", "write", "repository:two"),
                )
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

    if client is not None:
        client.permissions = [
            _paseo_permission("permit01-one"),
            _paseo_permission("permit02-final"),
        ]
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.START), _RuntimeFailure)
    bound = adapter.observe(subject.stable_action_id)
    assert not isinstance(bound, _RuntimeFailure)
    assert bound.binding_ref is not None
    assert bound.lifecycle == "running"
    assert [request.request_id for request in bound.permission_requests] == [
        "permit01-one",
        "permit02-final",
    ]
    assert bound.output_artifact_digest is None

    assert not isinstance(
        adapter.command(
            subject.stable_action_id,
            PermissionResponse(request_id="permit01-one", decision="allow"),
        ),
        _RuntimeFailure,
    )
    after_permission = adapter.observe(subject.stable_action_id)
    assert not isinstance(after_permission, _RuntimeFailure)
    assert [request.request_id for request in after_permission.permission_requests] == [
        "permit02-final"
    ]
    assert after_permission.lifecycle == "running"
    assert after_permission.output_artifact_digest is None
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.INTERRUPT), _RuntimeFailure)
    assert adapter.observe(subject.stable_action_id).lifecycle == "parked"
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.RESUME), _RuntimeFailure)
    assert adapter.observe(subject.stable_action_id).lifecycle == "running"
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.PARK), _RuntimeFailure)
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.RESUME), _RuntimeFailure)
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.FENCE), _RuntimeFailure)
    assert adapter.observe(subject.stable_action_id).fenced is True

    assert not isinstance(
        adapter.command(
            subject.stable_action_id,
            PermissionResponse(request_id="permit02-final", decision="allow"),
        ),
        _RuntimeFailure,
    )
    if client is not None:
        action_record = adapter._actions[subject.stable_action_id]
        Path(action_record["result_file"]).parent.mkdir(parents=True, exist_ok=True)
        Path(action_record["result_file"]).write_bytes(
            gateway_module.canonical_bytes(
                {
                    "schema_version": "gwo.runtime.output.v1",
                    "subject_digest": subject.digest,
                    "stable_action_id": subject.stable_action_id,
                    "authority_digest": subject.authority_digest,
                    "payload": {"completed": True},
                }
            )
        )
        client.agent.lifecycle = "idle"
    completed = adapter.observe(subject.stable_action_id)
    assert not isinstance(completed, _RuntimeFailure)
    assert completed.lifecycle == "completed"
    assert completed.output_artifact_digest is not None
    stop_count = len(
        [args for args in client.commands if args[0] == "stop"]
    ) if client is not None else None
    for invalid_command in (RuntimeCommand.PARK, RuntimeCommand.INTERRUPT):
        invalid = adapter.command(subject.stable_action_id, invalid_command)
        assert isinstance(invalid, _RuntimeFailure)
        assert invalid.code == "RUNTIME_COMMAND_INVALID"
    if client is not None:
        assert len([args for args in client.commands if args[0] == "stop"]) == stop_count

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
        _paseo_permission("permz001:z", description="repo:z"),
        _paseo_permission("perma001:a", tool="read", description="repo:a"),
    ]
    observed = adapter.observe(subject.stable_action_id)
    assert not isinstance(observed, _RuntimeFailure)
    assert [request.request_id for request in observed.permission_requests] == ["perma001:a", "permz001:z"]
    first = adapter.events(None)
    assert not isinstance(first, _RuntimeFailure)
    client.permissions.reverse()
    assert adapter.events(first.next_cursor).events == ()
    client.permissions[0]["tool"] = "write+review"
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
    client.permissions = [_paseo_permission("request:idle", description="repo")]
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

    client.permissions = [_paseo_permission("request:closed", description="repo")]
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

    monkeypatch.setattr(gateway_module.subprocess, "Popen", subprocess_should_not_run)
    with pytest.raises(RuntimeGatewayError) as unsafe:
        transport._run(["inspect", "agent&bad", "--json"])
    assert unsafe.value.code == "RUNTIME_VENDOR_ARGUMENT_INVALID"
    with pytest.raises(RuntimeGatewayError) as oversized:
        transport._run(["run", "x" * 8_000, "--json"])
    assert oversized.value.code == "RUNTIME_VENDOR_ARGUMENT_INVALID"
    for unsafe_value in ('agent" --mode bypass', "agent(test)"):
        with pytest.raises(RuntimeGatewayError) as unsafe:
            transport._run(["inspect", unsafe_value, "--json"])
        assert unsafe.value.code == "RUNTIME_VENDOR_ARGUMENT_INVALID"
    assert calls == []


def test_runtime_command_is_six_enum_commands_plus_typed_permission_response():
    assert {command.value for command in RuntimeCommand} == {
        "start", "resume", "park", "interrupt", "fence", "retire"
    }
    assert "permission_response" not in {command.value for command in RuntimeCommand}


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
    client.permissions = [_paseo_permission("request:idle", description="repo")]
    accepted = gateway.transition(
        subject.stable_action_id,
        PermissionResponse(request_id="request:idle", decision="allow"),
    )
    assert accepted.status == "running"
    assert "pending_permission_response" not in adapter._actions[subject.stable_action_id]

    client.permissions = [_paseo_permission("request:kept", description="repo")]
    native_run = client._run

    def retain_permission(args):
        if args[:2] == ["permit", "allow"]:
            client.commands.append(list(args))
            return [{
                "requestId": args[3][:8],
                "agentId": args[2],
                "agentShortId": args[2][:7],
                "name": "write",
                "result": "allowed",
            }]
        return native_run(args)

    client._run = retain_permission  # type: ignore[method-assign]
    with pytest.raises(RuntimeGatewayError) as kept:
        gateway.transition(
            subject.stable_action_id,
            PermissionResponse(request_id="request:kept", decision="allow"),
        )
    assert kept.value.code == "RUNTIME_OBSERVATION_INVALID"


def _prepared_paseo_adapter(tmp_path, *, state_name: str = "paseo-actions.json"):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / state_name,
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))
    assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.START), _RuntimeFailure)
    return store, source, workspace, client, adapter, subject, spec


def test_production_permission_real_five_field_fixture_joins_and_uses_full_id(tmp_path):
    _store, _source, _workspace, client, adapter, subject, _spec = _prepared_paseo_adapter(tmp_path)
    full_id = "permit01-full-provider-request-id"
    client.permissions = [_paseo_permission(full_id, tool="filesystem.write", description="repo/.gwo")]
    native_run = client._run

    def real_shape_run(args):
        if args[:2] == ["permit", "ls"]:
            return [
                {
                    "id": full_id[:8],
                    "agentId": "agent:one",
                    "agentShortId": "agent:o",
                    "name": "filesystem.write",
                    "description": "repo/.gwo",
                }
            ]
        return native_run(args)

    client._run = real_shape_run  # type: ignore[method-assign]
    observed = adapter.observe(subject.stable_action_id)
    assert not isinstance(observed, _RuntimeFailure)
    assert [request.request_id for request in observed.permission_requests] == [full_id]
    assert not isinstance(
        adapter.command(subject.stable_action_id, PermissionResponse(full_id, "allow")),
        _RuntimeFailure,
    )
    assert ["permit", "allow", "agent:one", full_id, "--json"] in client.commands


def test_production_permission_rejects_invalid_real_agent_short_id(tmp_path):
    _store, _source, _workspace, client, adapter, subject, _spec = _prepared_paseo_adapter(tmp_path)
    full_id = "permit03-provider-request-id"
    client.permissions = [_paseo_permission(full_id)]
    native_run = client._run

    def invalid_short_id_run(args):
        if args[:2] == ["permit", "ls"]:
            return [{
                "id": full_id[:8],
                "agentId": "agent:one",
                "agentShortId": "wrong:id",
                "name": "write",
                "description": "repository:one",
            }]
        return native_run(args)

    client._run = invalid_short_id_run  # type: ignore[method-assign]
    observed = adapter.observe(subject.stable_action_id)
    assert isinstance(observed, _RuntimeFailure)
    assert observed.code == "RUNTIME_IDENTITY_AMBIGUOUS"


@pytest.mark.parametrize(
    "permission_values, expected_code",
    (
        (
            (
                _paseo_permission("prefix01-first-provider-id"),
                _paseo_permission("prefix01-second-provider-id"),
            ),
            "RUNTIME_IDENTITY_AMBIGUOUS",
        ),
        ((_paseo_permission("permit02-provider-id"),), "RUNTIME_IDENTITY_AMBIGUOUS"),
    ),
)
def test_production_permission_prefix_collision_and_bijection_mismatch_fail_closed(
    tmp_path, permission_values, expected_code
):
    _store, _source, _workspace, client, adapter, subject, _spec = _prepared_paseo_adapter(tmp_path)
    client.permissions = list(permission_values)
    native_run = client._run
    if expected_code == "RUNTIME_IDENTITY_AMBIGUOUS" and len(permission_values) == 1:
        def mismatched_permit_list(args):
            if args[:2] == ["permit", "ls"]:
                item = permission_values[0]
                return [
                    {
                        "id": "mismatch",
                        "agentId": item["agentId"],
                        "agentShortId": item["agentId"][:7],
                        "name": item["tool"],
                        "description": item["description"],
                    }
                ]
            return native_run(args)

        client._run = mismatched_permit_list  # type: ignore[method-assign]
    observed = adapter.observe(subject.stable_action_id)
    assert isinstance(observed, _RuntimeFailure)
    assert observed.code == expected_code


@pytest.mark.parametrize("mutation", ("missing", "changed", "duplicate"))
def test_prepared_workspace_registry_must_remain_exact(tmp_path, mutation):
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
                (prompt,),
            )
        ),
        _RuntimeFailure,
    )
    if mutation == "missing":
        client.workspaces = []
    elif mutation == "changed":
        changed = workspace.parent / "changed-workspace"
        changed.mkdir()
        client.workspaces[0]["cwd"] = str(changed)
    else:
        client.workspaces.append(dict(client.workspaces[0]))

    observed = adapter.observe(subject.stable_action_id)

    assert isinstance(observed, _RuntimeFailure)
    assert observed.code == "RUNTIME_IDENTITY_AMBIGUOUS"


def test_workspace_action_save_failure_rolls_back_and_retains_pinned_intent(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    state_path = tmp_path / "paseo-actions.json"
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=state_path,
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))
    native_save = adapter._save
    saves = 0

    def fail_only_action_commit():
        nonlocal saves
        saves += 1
        if saves == 2:
            raise OSError("atomic replacement failed")
        native_save()

    adapter._save = fail_only_action_commit  # type: ignore[method-assign]
    failed = adapter.prepare(spec)
    assert isinstance(failed, _RuntimeFailure)
    assert failed.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert subject.stable_action_id not in adapter._actions
    assert subject.stable_action_id in adapter._workspace_intents
    assert adapter.observe(subject.stable_action_id).authoritative_absence is True
    durable = json.loads(state_path.read_text(encoding="utf-8"))
    assert durable["actions"] == {}
    assert subject.stable_action_id in durable["workspace_intents"]

    recovered = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=state_path,
    )
    assert not isinstance(recovered.prepare(spec), _RuntimeFailure)
    assert sum(command[:2] == ["workspace", "create"] for command in client.commands) == 1


@pytest.mark.parametrize("failure_code", ("RUNTIME_CONFIGURATION_INVALID", "RUNTIME_TRANSPORT_UNAVAILABLE"))
def test_prepare_failure_with_authoritative_absence_preserves_original_code(tmp_path, failure_code):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    adapter.prepare = lambda _spec: _RuntimeFailure(failure_code, "original failure")  # type: ignore[method-assign]

    with pytest.raises(RuntimeGatewayError) as failed:
        gateway.progress(subject, preflight)

    assert failed.value.code == failure_code


def test_gateway_rejects_unknown_and_permanent_permission_response_without_recovery(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    profile = _profile()
    subject = _put_subject_artifacts(store, _subject())
    adapter = _InMemoryRuntimeProviderAdapter(
        store,
        pending_permissions={subject.stable_action_id: (("request:one", "write", "repo"),)},
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
    with pytest.raises(RuntimeGatewayError) as unknown:
        gateway.transition(subject.stable_action_id, PermissionResponse("request:stale", "allow"))
    assert unknown.value.code == "RUNTIME_PERMISSION_REQUEST_UNKNOWN"

    adapter.command = lambda *_args: _RuntimeFailure("RUNTIME_COMMAND_INVALID", "permanent")  # type: ignore[method-assign]
    with pytest.raises(RuntimeGatewayError) as permanent:
        gateway.transition(subject.stable_action_id, PermissionResponse("request:one", "allow"))
    assert permanent.value.code == "RUNTIME_COMMAND_INVALID"


def test_durable_history_with_fresh_memory_adapter_never_reprepares(tmp_path):
    first_gateway, store, first_adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = first_gateway.planning_preflight(subject)
    first_gateway.progress(subject, preflight)
    assert first_adapter.prepare_calls == [subject.stable_action_id]

    fresh_adapter = _InMemoryRuntimeProviderAdapter(store)
    restarted = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=fresh_adapter,
        configuration=first_gateway._configuration,
        _artifacts=store,
    )
    with pytest.raises(RuntimeGatewayError) as missing:
        restarted.progress(subject, preflight)
    assert missing.value.code == "RUNTIME_BINDING_MISSING"
    assert fresh_adapter.prepare_calls == []


def test_durable_history_with_lost_production_state_never_reprepares(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    context = {"owner/repository": RuntimeRepositoryContext(source, "main")}
    client = _RecordingPaseoCli(workspace)
    first_adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts=context,
        state_path=tmp_path / "first-paseo-actions.json",
    )
    profile = _profile()
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=first_adapter,
        configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={"coordinator": ProfileMapping(profile.digest)},
        ),
        _artifacts=store,
    )
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    gateway.progress(subject, preflight)
    assert sum(command[0] == "run" for command in client.commands) == 1

    lost_state_adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts=context,
        state_path=tmp_path / "lost-paseo-actions.json",
    )
    restarted = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=lost_state_adapter,
        configuration=gateway._configuration,
        _artifacts=store,
    )
    with pytest.raises(RuntimeGatewayError) as missing:
        restarted.progress(subject, preflight)
    assert missing.value.code == "RUNTIME_BINDING_MISSING"
    assert sum(command[0] == "run" for command in client.commands) == 1


@pytest.mark.parametrize(
    "failure_kind, expected_code",
    (
        ("missing", "RUNTIME_ARTIFACT_MISSING"),
        ("tampered", "RUNTIME_ARTIFACT_DIGEST_MISMATCH"),
        ("features", "RUNTIME_CONFIGURATION_INVALID"),
    ),
)
def test_memory_artifact_and_profile_failures_share_strict_conformance_contract(
    tmp_path, failure_kind, expected_code
):
    store = ArtifactStore(tmp_path / "artifacts")
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    profile = replace(_profile(), features={"unsupported": True}) if failure_kind == "features" else _profile()
    adapter = _InMemoryRuntimeProviderAdapter(store)
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, profile, prompt, (prompt,))
    if failure_kind == "features":
        result = adapter.prepare(spec)
    else:
        assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
        target = store.path_for(prompt.digest)
        if failure_kind == "missing":
            target.unlink()
        else:
            target.write_bytes(b"tampered")
        result = adapter.observe(subject.stable_action_id)
    assert isinstance(result, _RuntimeFailure)
    assert result.code == expected_code


@dataclass(frozen=True)
class _CampaignPlanningSubjectSubclass(CampaignPlanningSubject):
    unexpected: str = "unexpected"


@dataclass(frozen=True)
class _WorkRunSubjectSubclass(WorkRunSubject):
    unexpected: str = "unexpected"


def test_public_subject_boundaries_reject_dataclass_subclasses(tmp_path):
    gateway, store, _adapter = _gateway(tmp_path)
    planning = _put_subject_artifacts(store, _subject())
    planning_subclass = _CampaignPlanningSubjectSubclass(**planning.__dict__)
    with pytest.raises(RuntimeGatewayError) as invalid_planning:
        gateway.planning_preflight(planning_subclass)
    assert invalid_planning.value.code == "RUNTIME_PREFLIGHT_SUBJECT_INVALID"

    work_subclass = _WorkRunSubjectSubclass(
        repository="owner/repository",
        campaign_key="campaign:repair",
        campaign_handle="handle:repair",
        plan_revision_digest="1" * 64,
        work_run_key="work:one",
        ticket_key="ticket:one",
        role="worker",
        prompt_artifact_digest="2" * 64,
        authority_subtree_digest="3" * 64,
        stable_action_id="work:one",
    )
    with pytest.raises(RuntimeGatewayError) as invalid_work:
        gateway.progress(work_subclass)
    assert invalid_work.value.code == "RUNTIME_SUBJECT_INVALID"


@pytest.mark.parametrize(
    "profile, contexts",
    (
        (_profile(), {}),
        (replace(_profile(), features={"unsupported": True}), "valid"),
        (replace(_profile(), provider='test" --mode bypass'), "valid"),
        (replace(_profile(), model="model(test)"), "valid"),
        (replace(_profile(), thinking=" high"), "valid"),
        (replace(_profile(), mode="safe "), "valid"),
        (replace(_profile(), mode="safe&bypass"), "valid"),
    ),
)
def test_production_preflight_rejects_static_context_feature_and_unsafe_profile_before_claim(
    tmp_path, profile, contexts
):
    source, _workspace = _repository_worktree(tmp_path)
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={"coordinator": ProfileMapping(profile.digest)},
    )
    gateway = build_runtime_gateway(
        store_path=tmp_path / "gateway.journal",
        configuration=configuration,
        repository_contexts=(
            {} if contexts == {} else {"owner/repository": RuntimeRepositoryContext(source, "main")}
        ),
    )
    with pytest.raises(RuntimeGatewayError) as invalid:
        gateway.planning_preflight(_subject())
    assert invalid.value.code == "RUNTIME_CONFIGURATION_INVALID"
    assert gateway._data["campaigns"] == {}
    assert gateway._data["preflights"] == {}


def test_bound_workspace_allows_worker_candidate_commit_but_prepared_rejects_base_drift(tmp_path):
    _store, _source, workspace, _client, adapter, subject, _spec = _prepared_paseo_adapter(tmp_path)
    candidate = workspace / "candidate.txt"
    candidate.write_text("candidate\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "candidate.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(workspace), "commit", "-m", "candidate"],
        check=True,
        capture_output=True,
    )
    bound = adapter.observe(subject.stable_action_id)
    assert not isinstance(bound, _RuntimeFailure)
    assert bound.binding_ref == "paseo:agent:one"

    store = ArtifactStore(tmp_path / "prepared-artifacts")
    prepared_root = tmp_path / "prepared"
    prepared_root.mkdir()
    source, prepared_workspace = _repository_worktree(prepared_root)
    prepared_client = _RecordingPaseoCli(prepared_workspace)
    prepared_adapter = _PaseoRuntimeProviderAdapter(
        client=prepared_client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "prepared-actions.json",
    )
    prepared_subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(prepared_subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(prepared_subject.stable_action_id, prepared_subject, _profile(), prompt, (prompt,))
    assert not isinstance(prepared_adapter.prepare(spec), _RuntimeFailure)
    (prepared_workspace / "base-drift.txt").write_text("drift\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(prepared_workspace), "add", "base-drift.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(prepared_workspace), "commit", "-m", "base drift"],
        check=True,
        capture_output=True,
    )
    prepared = prepared_adapter.observe(prepared_subject.stable_action_id)
    assert isinstance(prepared, _RuntimeFailure)
    assert prepared.code == "RUNTIME_IDENTITY_AMBIGUOUS"


def test_first_workspace_intent_save_failure_rolls_back_before_create(tmp_path):
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
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))
    adapter._save = lambda: (_ for _ in ()).throw(OSError("intent save failed"))  # type: ignore[method-assign]

    result = adapter.prepare(spec)

    assert isinstance(result, _RuntimeFailure)
    assert result.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert adapter._workspace_intents == {}
    assert adapter._actions == {}
    assert all(command[:2] != ["workspace", "create"] for command in client.commands)


def test_start_and_resume_save_failure_do_not_send_external_command(tmp_path):
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
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))
    assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
    before_start = list(client.commands)
    record_before_start = deepcopy(adapter._actions[subject.stable_action_id])
    adapter._save = lambda: (_ for _ in ()).throw(OSError("start save failed"))  # type: ignore[method-assign]
    start_failed = adapter.command(subject.stable_action_id, RuntimeCommand.START)
    assert isinstance(start_failed, _RuntimeFailure)
    assert adapter._actions[subject.stable_action_id] == record_before_start
    assert all(command[0] != "run" for command in client.commands[len(before_start):])

    # Recreate an unfenced Bound parked action, then fail the resume intent save.
    adapter._save = _PaseoRuntimeProviderAdapter._save.__get__(adapter)  # type: ignore[method-assign]
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.START), _RuntimeFailure)
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.PARK), _RuntimeFailure)
    assert adapter.observe(subject.stable_action_id).lifecycle == "parked"
    before_resume = list(client.commands)
    record_before_resume = deepcopy(adapter._actions[subject.stable_action_id])
    adapter._save = lambda: (_ for _ in ()).throw(OSError("resume save failed"))  # type: ignore[method-assign]
    resume_failed = adapter.command(subject.stable_action_id, RuntimeCommand.RESUME)
    assert isinstance(resume_failed, _RuntimeFailure)
    assert adapter._actions[subject.stable_action_id] == record_before_resume
    assert all(command[0] != "send" for command in client.commands[len(before_resume):])


def test_permission_response_requires_same_decision_receipt_before_removal(tmp_path):
    _store, _source, _workspace, client, adapter, subject, _spec = _prepared_paseo_adapter(tmp_path)
    request_id = "receipt1-full-provider-request"
    client.permissions = [_paseo_permission(request_id)]
    accepted = adapter.command(subject.stable_action_id, PermissionResponse(request_id, "allow"))
    assert not isinstance(accepted, _RuntimeFailure)
    pending = adapter._actions[subject.stable_action_id]["pending_permission_response"]
    assert pending["provider_receipt"] == {
        "requestId": request_id[:8],
        "agentId": "agent:one",
        "agentShortId": "agent:o",
        "name": "write",
        "result": "allowed",
    }
    observed = adapter.observe(subject.stable_action_id)
    assert not isinstance(observed, _RuntimeFailure)
    assert "pending_permission_response" not in adapter._actions[subject.stable_action_id]


@pytest.mark.parametrize(
    "receipt, expected_code",
    (
        ([{"requestId": "receipt2", "agentId": "agent:one", "agentShortId": "agent:o", "name": "write", "result": "denied"}], "RUNTIME_IDENTITY_AMBIGUOUS"),
        ([{"requestId": "receipt2"}], "RUNTIME_PROVIDER_PROTOCOL_INVALID"),
    ),
)
def test_permission_response_rejects_opposite_or_malformed_provider_receipt(tmp_path, receipt, expected_code):
    _store, _source, _workspace, client, adapter, subject, _spec = _prepared_paseo_adapter(tmp_path)
    request_id = "receipt2-full-provider-request"
    client.permissions = [_paseo_permission(request_id)]
    native_run = client._run

    def receipt_run(args):
        if args[:2] == ["permit", "allow"]:
            native_run(args)
            return receipt
        return native_run(args)

    client._run = receipt_run  # type: ignore[method-assign]
    result = adapter.command(subject.stable_action_id, PermissionResponse(request_id, "allow"))
    assert isinstance(result, _RuntimeFailure)
    assert result.code == expected_code


def test_permission_response_ack_loss_with_absence_is_effect_ambiguous(tmp_path):
    _store, _source, _workspace, client, adapter, subject, _spec = _prepared_paseo_adapter(tmp_path)
    request_id = "receipt3-full-provider-request"
    client.permissions = [_paseo_permission(request_id)]
    native_run = client._run

    def remove_then_lose_ack(args):
        if args[:2] == ["permit", "allow"]:
            native_run(args)
            raise TimeoutError("receipt acknowledgement lost")
        return native_run(args)

    client._run = remove_then_lose_ack  # type: ignore[method-assign]
    command = adapter.command(subject.stable_action_id, PermissionResponse(request_id, "allow"))
    assert isinstance(command, _RuntimeFailure)
    assert command.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    observed = adapter.observe(subject.stable_action_id)
    assert isinstance(observed, _RuntimeFailure)
    assert observed.code == "RUNTIME_EFFECT_AMBIGUOUS"


@pytest.mark.parametrize("kind", ("unsafe_fallback", "non_git", "bad_base", "unsafe_path"))
def test_production_preflight_validates_fallback_and_repository_context_before_persisting(
    tmp_path, kind
):
    source, _workspace = _repository_worktree(tmp_path)
    primary = _profile()
    fallback = replace(_profile(), name="fallback", mode="unsafe&mode")
    mapping = ProfileMapping(
        primary.digest,
        fallback.digest if kind == "unsafe_fallback" else None,
    )
    profiles = {primary.digest: primary}
    if kind == "unsafe_fallback":
        profiles[fallback.digest] = fallback
    if kind == "non_git":
        non_git = tmp_path / "not-a-repository"
        non_git.mkdir()
        context = RuntimeRepositoryContext(non_git, "main")
    elif kind == "bad_base":
        context = RuntimeRepositoryContext(source, "not-a-base")
    elif kind == "unsafe_path":
        unsafe = tmp_path / "unsafe&context"
        subprocess.run(
            ["git", "-C", str(source), "worktree", "add", "-b", "unsafe-context", str(unsafe), "main"],
            check=True,
            capture_output=True,
        )
        context = RuntimeRepositoryContext(unsafe, "main")
    else:
        context = RuntimeRepositoryContext(source, "main")
    gateway = build_runtime_gateway(
        store_path=tmp_path / "gateway.journal",
        configuration=RuntimeConfiguration(
            profiles=profiles,
            host_mappings={"coordinator": mapping},
        ),
        repository_contexts={"owner/repository": context},
    )
    with pytest.raises(RuntimeGatewayError) as invalid:
        gateway.planning_preflight(_subject())
    assert invalid.value.code == "RUNTIME_CONFIGURATION_INVALID"
    assert gateway._data["campaigns"] == {}
    assert gateway._data["preflights"] == {}


def test_paseo_nonzero_json_error_taxonomy_never_treats_permanent_rejection_as_transport(monkeypatch):
    daemon = _PaseoCliTransport._nonzero_failure(
        json.dumps({"error": {"code": "DAEMON_NOT_RUNNING", "message": "native detail"}}),
        "",
    )
    assert daemon.code == "RUNTIME_TRANSPORT_UNAVAILABLE"

    for code, expected in (("PERMISSION_NOT_FOUND", "RUNTIME_PERMISSION_REQUEST_UNKNOWN"), ("UNKNOWN_COMMAND", "RUNTIME_PROVIDER_COMMAND_FAILED")):
        permanent = _PaseoCliTransport._nonzero_failure(
            json.dumps({"error": {"code": code, "message": "native detail"}}),
            "",
        )
        assert permanent.code == expected
        assert "native detail" not in permanent.detail


@pytest.mark.parametrize(
    "failure_kind, expected_code",
    (("missing", "RUNTIME_ARTIFACT_MISSING"), ("tampered", "RUNTIME_ARTIFACT_DIGEST_MISMATCH")),
)
def test_memory_observe_validates_a_distinct_input_artifact(tmp_path, failure_kind, expected_code):
    store = ArtifactStore(tmp_path / "artifacts")
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    separate_input = store.put_canonical({"separate": "governed input"})
    adapter = _InMemoryRuntimeProviderAdapter(store)
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (separate_input,))
    assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
    input_path = store.path_for(separate_input.digest)
    if failure_kind == "missing":
        input_path.unlink()
    else:
        input_path.write_bytes(b"tampered input")

    result = adapter.observe(subject.stable_action_id)

    assert isinstance(result, _RuntimeFailure)
    assert result.code == expected_code


@dataclass(frozen=True)
class _PermissionResponseSubclass(PermissionResponse):
    unexpected: str = "unexpected"


def test_runtime_transition_boundary_rejects_permission_response_dataclass_subclass(tmp_path):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    gateway.progress(subject, preflight)
    subclass = _PermissionResponseSubclass("request:one", "allow")

    with pytest.raises(RuntimeGatewayError) as public_invalid:
        gateway.transition(subject.stable_action_id, subclass)
    assert public_invalid.value.code == "RUNTIME_COMMAND_INVALID"
    private_invalid = adapter.command(subject.stable_action_id, subclass)
    assert isinstance(private_invalid, _RuntimeFailure)
    assert private_invalid.code == "RUNTIME_COMMAND_INVALID"


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
def test_permission_response_completion_evidence_replays_once_after_gateway_restart(
    tmp_path, adapter_kind
):
    """A completed adapter effect survives the Gateway's unpersisted receipt window."""

    store = ArtifactStore(tmp_path / "artifacts")
    subject = _put_subject_artifacts(store, _subject())
    profile = _profile()
    client: _RecordingPaseoCli | None = None
    adapter_state = tmp_path / "paseo-actions.json"
    if adapter_kind == "memory":
        adapter = _InMemoryRuntimeProviderAdapter(
            store,
            pending_permissions={
                subject.stable_action_id: (("request:replay", "write", "repository:one"),)
            },
        )
    else:
        source, workspace = _repository_worktree(tmp_path)
        client = _RecordingPaseoCli(workspace)
        adapter = _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
            state_path=adapter_state,
        )
    gateway_path = tmp_path / "gateway.journal"
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={"coordinator": ProfileMapping(profile.digest)},
    )
    gateway = RuntimeGateway(
        store_path=gateway_path,
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )
    preflight = gateway.planning_preflight(subject)
    gateway.progress(subject, preflight)
    if client is not None:
        client.permissions = [_paseo_permission("request:replay")]

    command = PermissionResponse("request:replay", "allow")
    assert not isinstance(adapter.command(subject.stable_action_id, command), _RuntimeFailure)
    completed = adapter.observe(subject.stable_action_id)
    assert not isinstance(completed, _RuntimeFailure)
    assert completed.completed_permission_response is not None
    if client is not None:
        assert len([args for args in client.commands if args[:2] == ["permit", "allow"]]) == 1
        adapter = _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
            state_path=adapter_state,
        )

    restarted = RuntimeGateway(
        store_path=gateway_path,
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )
    replayed = restarted.transition(subject.stable_action_id, command)
    assert replayed.command == command
    if client is not None:
        assert len([args for args in client.commands if args[:2] == ["permit", "allow"]]) == 1
    with pytest.raises(RuntimeGatewayError) as opposite:
        restarted.transition(
            subject.stable_action_id,
            PermissionResponse("request:replay", "deny"),
        )
    assert opposite.value.code == "RUNTIME_PERMISSION_REQUEST_UNKNOWN"
    with pytest.raises(RuntimeGatewayError) as unknown:
        restarted.transition(
            subject.stable_action_id,
            PermissionResponse("request:unknown", "allow"),
        )
    assert unknown.value.code == "RUNTIME_PERMISSION_REQUEST_UNKNOWN"


def test_resume_prompt_write_failure_preserves_durable_state_and_can_retry(tmp_path, monkeypatch):
    _store, _source, _workspace, client, adapter, subject, _spec = _prepared_paseo_adapter(tmp_path)
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.PARK), _RuntimeFailure)
    assert adapter.observe(subject.stable_action_id).lifecycle == "parked"
    record_before = deepcopy(adapter._actions[subject.stable_action_id])
    state_before = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    command_count = len(client.commands)

    def fail_write(_record):
        raise OSError("local resume prompt write failed")

    monkeypatch.setattr(adapter, "_write_resume_file", fail_write)
    failed = adapter.command(subject.stable_action_id, RuntimeCommand.RESUME)
    assert isinstance(failed, _RuntimeFailure)
    assert failed.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert adapter._actions[subject.stable_action_id] == record_before
    assert json.loads(adapter._state_path.read_text(encoding="utf-8")) == state_before
    assert all(args[0] != "send" for args in client.commands[command_count:])

    monkeypatch.setattr(
        adapter,
        "_write_resume_file",
        _PaseoRuntimeProviderAdapter._write_resume_file,
    )
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.RESUME), _RuntimeFailure)
    assert any(args[0] == "send" for args in client.commands[command_count:])


def test_production_preflight_accepts_safe_git_base_ref_that_is_not_paseo_argv(tmp_path):
    source, _workspace = _repository_worktree(tmp_path)
    base_ref = "base&candidate"
    subprocess.run(
        ["git", "-C", str(source), "branch", base_ref, "main"],
        check=True,
        capture_output=True,
    )
    profile = _profile()
    gateway = build_runtime_gateway(
        store_path=tmp_path / "gateway.journal",
        configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={"coordinator": ProfileMapping(profile.digest)},
        ),
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, base_ref)},
    )
    receipt = gateway.planning_preflight(_subject())
    assert receipt.stable_action_id == _subject().stable_action_id
    assert gateway._data["campaigns"] != {}


def test_paseo_nonzero_error_json_over_byte_limit_is_never_parsed(monkeypatch):
    payload = json.dumps({"error": {"code": "DAEMON_NOT_RUNNING"}})
    payload += "x" * (gateway_module._MAXIMUM_PASEO_ERROR_JSON_BYTES + 1)
    monkeypatch.setattr(
        gateway_module.json,
        "loads",
        lambda _payload: pytest.fail("oversized Paseo error JSON was parsed"),
    )
    error = _PaseoCliTransport._nonzero_failure(payload, "")
    assert error.code == "RUNTIME_PROVIDER_COMMAND_FAILED"


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
@pytest.mark.parametrize(
    "failure_kind, expected_code",
    (("missing", "RUNTIME_ARTIFACT_MISSING"), ("tampered", "RUNTIME_ARTIFACT_DIGEST_MISMATCH")),
)
def test_private_adapters_observe_distinct_governed_input_matrix(
    tmp_path, adapter_kind, failure_kind, expected_code
):
    store = ArtifactStore(tmp_path / "artifacts")
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    governed_input = store.put_canonical({"governed": "separate input"})
    if adapter_kind == "memory":
        adapter = _InMemoryRuntimeProviderAdapter(store)
    else:
        source, workspace = _repository_worktree(tmp_path)
        adapter = _PaseoRuntimeProviderAdapter(
            client=_RecordingPaseoCli(workspace),  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
            state_path=tmp_path / "paseo-actions.json",
        )
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (governed_input,))
    assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
    if adapter_kind == "memory":
        input_path = store.path_for(governed_input.digest)
    else:
        input_path = Path(adapter._actions[subject.stable_action_id]["input_files"][governed_input.digest])
    if failure_kind == "missing":
        input_path.unlink()
    else:
        input_path.write_bytes(b"tampered governed input")

    observed = adapter.observe(subject.stable_action_id)

    assert isinstance(observed, _RuntimeFailure)
    assert observed.code == expected_code


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
def test_private_adapters_reject_permission_response_subclasses(adapter_kind, tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    if adapter_kind == "memory":
        adapter = _InMemoryRuntimeProviderAdapter(store)
    else:
        source, workspace = _repository_worktree(tmp_path)
        adapter = _PaseoRuntimeProviderAdapter(
            client=_RecordingPaseoCli(workspace),  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
            state_path=tmp_path / "paseo-actions.json",
        )

    invalid = adapter.command("action:subclass", _PermissionResponseSubclass("request:one", "allow"))

    assert isinstance(invalid, _RuntimeFailure)
    assert invalid.code == "RUNTIME_COMMAND_INVALID"


def test_permanent_start_rejection_rolls_back_pending_intent_and_allows_retry(tmp_path):
    # Use a fresh Prepared action for this exact start boundary so no provider
    # effect occurred before the rejection.
    store = ArtifactStore(tmp_path / "start-artifacts")
    start_root = tmp_path / "start"
    start_root.mkdir()
    source, workspace = _repository_worktree(start_root)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=tmp_path / "start-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    assert not isinstance(
        adapter.prepare(_RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))),
        _RuntimeFailure,
    )
    record_before = deepcopy(adapter._actions[subject.stable_action_id])
    native_run = client._run

    def reject_run(args):
        if args[0] == "run":
            raise RuntimeGatewayError("RUNTIME_PROVIDER_COMMAND_FAILED", "permanent provider reject")
        return native_run(args)

    client._run = reject_run  # type: ignore[method-assign]
    rejected = adapter.command(subject.stable_action_id, RuntimeCommand.START)
    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_PROVIDER_COMMAND_FAILED"
    assert adapter._actions[subject.stable_action_id] == record_before

    client._run = native_run  # type: ignore[method-assign]
    assert not isinstance(adapter.command(subject.stable_action_id, RuntimeCommand.START), _RuntimeFailure)


def test_permission_permanent_rejection_restores_record_and_retries_exactly_once(tmp_path):
    _store, _source, _workspace, client, adapter, subject, _spec = _prepared_paseo_adapter(tmp_path)
    request_id = "permit-reject-full-request"
    client.permissions = [_paseo_permission(request_id)]
    settled = adapter.observe(subject.stable_action_id)
    assert not isinstance(settled, _RuntimeFailure)
    record_before = deepcopy(adapter._actions[subject.stable_action_id])
    disk_before = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    native_run = client._run
    permit_calls: list[list[str]] = []

    def reject_permit(args):
        if args[:2] == ["permit", "allow"]:
            permit_calls.append(list(args))
            raise RuntimeGatewayError(
                "RUNTIME_PERMISSION_REQUEST_UNKNOWN", "provider deterministically rejected"
            )
        return native_run(args)

    client._run = reject_permit  # type: ignore[method-assign]
    rejected = adapter.command(
        subject.stable_action_id, PermissionResponse(request_id, "allow")
    )

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_PERMISSION_REQUEST_UNKNOWN"
    assert adapter._actions[subject.stable_action_id] == record_before
    assert json.loads(adapter._state_path.read_text(encoding="utf-8")) == disk_before
    assert permit_calls == [["permit", "allow", "agent:one", request_id, "--json"]]

    client._run = native_run  # type: ignore[method-assign]
    retried = adapter.command(
        subject.stable_action_id, PermissionResponse(request_id, "allow")
    )

    assert not isinstance(retried, _RuntimeFailure)
    assert [args for args in client.commands if args[:2] == ["permit", "allow"]] == permit_calls


def test_permission_receipt_verification_failure_retains_pending_ambiguity_evidence(tmp_path):
    _store, _source, _workspace, client, adapter, subject, _spec = _prepared_paseo_adapter(tmp_path)
    request_id = "permit-malformed-full-request"
    client.permissions = [_paseo_permission(request_id)]
    before_observation = adapter.observe(subject.stable_action_id)
    assert not isinstance(before_observation, _RuntimeFailure)
    request = next(
        request
        for request in before_observation.permission_requests
        if request.request_id == request_id
    )
    record_before = deepcopy(adapter._actions[subject.stable_action_id])
    native_run = client._run

    def malformed_success(args):
        if args[:2] == ["permit", "allow"]:
            native_run(args)
            return []
        return native_run(args)

    client._run = malformed_success  # type: ignore[method-assign]
    rejected = adapter.command(
        subject.stable_action_id, PermissionResponse(request_id, "allow")
    )

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    record_after = adapter._actions[subject.stable_action_id]
    assert record_after != record_before
    assert record_after["pending_permission_response"] == {
        "request_id": request_id,
        "decision": "allow",
        "request_digest": digest_value(asdict(request)),
        "provider_receipt": None,
    }
    observed = adapter.observe(subject.stable_action_id)
    assert isinstance(observed, _RuntimeFailure)
    assert observed.code == "RUNTIME_EFFECT_AMBIGUOUS"


def test_workspace_create_permanent_rejection_clears_exact_negative_intent_and_retries(tmp_path):
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
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))
    native_run = client._run
    create_calls: list[list[str]] = []

    def reject_create(args):
        if args[:2] == ["workspace", "create"]:
            create_calls.append(list(args))
            raise RuntimeGatewayError(
                "RUNTIME_PROVIDER_COMMAND_FAILED", "provider rejected create"
            )
        return native_run(args)

    client._run = reject_create  # type: ignore[method-assign]
    rejected = adapter.prepare(spec)

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_PROVIDER_COMMAND_FAILED"
    assert adapter._workspace_intents == {}
    durable_after_reject = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    assert durable_after_reject["workspace_intents"] == {}
    assert adapter._actions == {}
    assert len(create_calls) == 1

    client._run = native_run  # type: ignore[method-assign]
    retried = adapter.prepare(spec)

    assert not isinstance(retried, _RuntimeFailure)
    assert [args for args in client.commands if args[:2] == ["workspace", "create"]] == create_calls


@pytest.mark.parametrize("failure_kind", ("transport", "readback"))
def test_workspace_create_ambiguous_or_unreadable_recovery_retains_intent(tmp_path, failure_kind):
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
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))
    native_run = client._run
    workspace_list_calls = 0

    def ambiguous_create(args):
        nonlocal workspace_list_calls
        if args[:2] == ["workspace", "ls"]:
            workspace_list_calls += 1
            if failure_kind == "readback" and workspace_list_calls == 2:
                raise OSError("workspace registry readback unavailable")
        if args[:2] == ["workspace", "create"]:
            if failure_kind == "transport":
                raise TimeoutError("workspace create acknowledgement lost")
            raise RuntimeGatewayError(
                "RUNTIME_PROVIDER_COMMAND_FAILED", "provider rejected create"
            )
        return native_run(args)

    client._run = ambiguous_create  # type: ignore[method-assign]
    rejected = adapter.prepare(spec)

    assert isinstance(rejected, _RuntimeFailure)
    assert adapter._actions == {}
    assert set(adapter._workspace_intents) == {subject.stable_action_id}
    durable = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    assert set(durable["workspace_intents"]) == {subject.stable_action_id}


@pytest.mark.parametrize(
    "receipt_kind, expected_code",
    (("malformed", "RUNTIME_PROVIDER_PROTOCOL_INVALID"), ("mismatch", "RUNTIME_IDENTITY_AMBIGUOUS")),
)
def test_workspace_success_receipt_failure_waits_for_fresh_readback_before_adoption(
    tmp_path, receipt_kind, expected_code
):
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
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))
    native_run = client._run

    def invalid_success_receipt(args):
        if args[:2] != ["workspace", "create"]:
            return native_run(args)
        native_run(args)
        if receipt_kind == "malformed":
            return {"workspace": {"id": "workspace:one"}}
        return {"workspace": {"id": "workspace:other", "path": str(workspace)}}

    client._run = invalid_success_receipt  # type: ignore[method-assign]
    rejected = adapter.prepare(spec)

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == expected_code
    assert client.workspaces != []
    assert adapter._actions == {}
    intent = adapter._workspace_intents[subject.stable_action_id]
    assert set(intent) == {
        "repository_path",
        "base_commit",
        "slug",
        "spec_identity_digest",
        "phase",
    }
    durable = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    assert durable["workspace_intents"][subject.stable_action_id] == intent
    assert len([args for args in client.commands if args[:2] == ["workspace", "create"]]) == 1

    client._run = native_run  # type: ignore[method-assign]
    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=adapter._state_path,
    )
    retry = restarted.prepare(spec)

    assert not isinstance(retry, _RuntimeFailure)
    assert subject.stable_action_id in restarted._actions
    assert len([args for args in client.commands if args[:2] == ["workspace", "create"]]) == 1


def test_gateway_transaction_reloads_durable_cas_for_preconstructed_instances(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    first_subject = _put_subject_artifacts(store, _subject())
    second_subject = replace(
        first_subject,
        snapshot_artifact_digest=store.put_canonical({"snapshot": "second"}).digest,
    )
    profile = _profile()
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={"coordinator": ProfileMapping(profile.digest)},
    )
    second_profile = replace(profile, name="preconstructed-contender")
    second_configuration = RuntimeConfiguration(
        profiles={second_profile.digest: second_profile},
        host_mappings={"coordinator": ProfileMapping(second_profile.digest)},
    )
    first_adapter = _InMemoryRuntimeProviderAdapter(store)
    second_adapter = _InMemoryRuntimeProviderAdapter(store)
    journal = tmp_path / "gateway.journal"
    first = RuntimeGateway(
        store_path=journal,
        _adapter=first_adapter,
        configuration=configuration,
        _artifacts=store,
    )
    # Construct the contender before the first writer commits.  Its in-memory
    # view is intentionally stale and must not be allowed to overwrite CAS.
    second = RuntimeGateway(
        store_path=journal,
        _adapter=second_adapter,
        configuration=second_configuration,
        _artifacts=store,
    )

    accepted = first.planning_preflight(first_subject)
    with pytest.raises(RuntimeGatewayError) as rejected:
        second.planning_preflight(second_subject)

    assert rejected.value.code == "RUNTIME_PREFLIGHT_IDENTITY_MISMATCH"
    durable = json.loads(journal.read_text(encoding="utf-8"))
    assert second._data == durable
    restarted = RuntimeGateway(
        store_path=journal,
        _adapter=_InMemoryRuntimeProviderAdapter(store),
        configuration=configuration,
        _artifacts=store,
    )
    assert restarted.planning_preflight(first_subject) == accepted
    assert restarted._data == durable


def test_gateway_failed_assignment_transaction_never_publishes_or_prepares_on_retry(
    tmp_path,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    durable_before = json.loads(gateway._store_path.read_text(encoding="utf-8"))
    gateway._save = lambda: (_ for _ in ()).throw(OSError("replacement failed"))  # type: ignore[method-assign]

    for _ in range(2):
        with pytest.raises(OSError, match="replacement failed"):
            gateway.progress(subject, preflight)
        assert gateway._data == durable_before
        assert adapter.prepare_calls == []
        assert json.loads(gateway._store_path.read_text(encoding="utf-8")) == durable_before


def test_gateway_failed_observation_transaction_never_reprepares_or_starts_on_retry(
    tmp_path,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    native_save = gateway._save
    saves = 0

    def fail_observation_commits():
        nonlocal saves
        saves += 1
        if saves >= 2:
            raise OSError("observation replacement failed")
        native_save()

    gateway._save = fail_observation_commits  # type: ignore[method-assign]
    with pytest.raises(OSError, match="observation replacement failed"):
        gateway.progress(subject, preflight)
    durable_after_assignment = json.loads(
        gateway._store_path.read_text(encoding="utf-8")
    )
    assert gateway._data == durable_after_assignment
    assert adapter.prepare_calls == [subject.stable_action_id]
    assert adapter.created_agent_count == 0

    with pytest.raises(OSError, match="observation replacement failed"):
        gateway.progress(subject, preflight)
    assert gateway._data == durable_after_assignment
    assert adapter.prepare_calls == [subject.stable_action_id]
    assert adapter.created_agent_count == 0


@dataclass(frozen=True)
class _RuntimeFailureSubclass(_RuntimeFailure):
    unexpected: str = "unexpected"


@pytest.mark.parametrize(
    "failure",
    (
        _RuntimeFailure(
            "RUNTIME_ACTION_ABSENT",
            "authoritative stable-action absence",
            stable_action_id="planning:repair",
            authoritative_absence=False,
        ),
        _RuntimeFailure(
            "RUNTIME_ACTION_ABSENT",
            "different detail",
            stable_action_id="planning:repair",
            authoritative_absence=True,
        ),
        _RuntimeFailure(
            "RUNTIME_ACTION_ABSENT",
            "authoritative stable-action absence",
            stable_action_id="planning:other",
            authoritative_absence=True,
        ),
        _RuntimeFailure(
            "RUNTIME_TRANSPORT_UNAVAILABLE",
            "authoritative stable-action absence",
            stable_action_id="planning:repair",
            authoritative_absence=True,
        ),
        _RuntimeFailureSubclass(
            "RUNTIME_ACTION_ABSENT",
            "authoritative stable-action absence",
            stable_action_id="planning:repair",
            authoritative_absence=True,
        ),
    ),
)
def test_initial_malformed_absence_never_authorizes_prepare(tmp_path, failure):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    adapter.observe_failure = failure

    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(subject, preflight)

    assert stopped.value.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert adapter.prepare_calls == []


def test_post_prepare_malformed_absence_uses_the_same_exact_predicate(tmp_path):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    native_observe = adapter.observe
    observations = 0

    def malformed_second_observe(stable_action_id):
        nonlocal observations
        observations += 1
        if observations == 1:
            return _RuntimeFailure.absent(stable_action_id)
        return _RuntimeFailure(
            "RUNTIME_ACTION_ABSENT",
            "different detail",
            stable_action_id=stable_action_id,
            authoritative_absence=True,
        )

    adapter.observe = malformed_second_observe  # type: ignore[method-assign]
    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(subject, preflight)

    assert stopped.value.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert adapter.prepare_calls == [subject.stable_action_id]
    adapter.observe = native_observe  # type: ignore[method-assign]


def test_workspace_intent_is_durable_before_the_first_paseo_readback(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    state_path = tmp_path / "paseo-actions.json"
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=state_path,
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))
    native_run = client._run
    first_readback_state = None

    def inspect_durable_intent(args):
        nonlocal first_readback_state
        if first_readback_state is None:
            first_readback_state = json.loads(state_path.read_text(encoding="utf-8"))
        return native_run(args)

    client._run = inspect_durable_intent  # type: ignore[method-assign]
    result = adapter.prepare(spec)

    assert not isinstance(result, _RuntimeFailure)
    intent = first_readback_state["workspace_intents"][subject.stable_action_id]
    assert intent["phase"] == "recorded"
    assert intent["repository_path"] == str(source.resolve())
    assert intent["slug"]
    assert intent["spec_identity_digest"]


def test_first_paseo_readback_failure_leaves_only_recorded_workspace_intent(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    state_path = tmp_path / "paseo-actions.json"
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=state_path,
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))
    native_run = client._run

    def fail_first_readback(args):
        if args[0] == "ls":
            raise OSError("first readback unavailable")
        return native_run(args)

    client._run = fail_first_readback  # type: ignore[method-assign]
    failed = adapter.prepare(spec)

    assert isinstance(failed, _RuntimeFailure)
    assert failed.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    durable = json.loads(state_path.read_text(encoding="utf-8"))
    assert durable["workspace_intents"][subject.stable_action_id]["phase"] == "recorded"
    assert all(args[:2] != ["workspace", "create"] for args in client.commands)
    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=state_path,
    )
    assert restarted._workspace_intents == durable["workspace_intents"]
    assert all(args[:2] != ["workspace", "create"] for args in client.commands)


def test_create_pending_restart_readback_never_reissues_workspace_create(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    state_path = tmp_path / "paseo-actions.json"
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=state_path,
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))
    native_run = client._run
    create_attempts = 0

    def lose_create_ack_before_effect(args):
        nonlocal create_attempts
        if args[:2] == ["workspace", "create"]:
            create_attempts += 1
            raise TimeoutError("create acknowledgement lost")
        return native_run(args)

    client._run = lose_create_ack_before_effect  # type: ignore[method-assign]
    failed = adapter.prepare(spec)
    assert isinstance(failed, _RuntimeFailure)
    assert create_attempts == 1
    durable = json.loads(state_path.read_text(encoding="utf-8"))
    assert durable["workspace_intents"][subject.stable_action_id]["phase"] == "create_pending"

    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=state_path,
    )
    retry = restarted.prepare(spec)

    assert isinstance(retry, _RuntimeFailure)
    assert retry.code == "RUNTIME_MATERIALIZATION_PENDING"
    assert create_attempts == 1


def test_concurrent_workspace_claim_has_one_effect_owner_without_locking_provider(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    state_path = tmp_path / "paseo-actions.json"
    context = {"owner/repository": RuntimeRepositoryContext(source, "main")}
    first = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts=context,
        state_path=state_path,
    )
    second = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts=context,
        state_path=state_path,
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))
    native_run = client._run
    create_entered = threading.Event()
    release_create = threading.Event()
    create_attempts = 0

    def block_first_create(args):
        nonlocal create_attempts
        if args[:2] == ["workspace", "create"]:
            create_attempts += 1
            create_entered.set()
            assert release_create.wait(5)
        return native_run(args)

    client._run = block_first_create  # type: ignore[method-assign]
    first_result: list[object] = []
    worker = threading.Thread(target=lambda: first_result.append(first.prepare(spec)))
    worker.start()
    assert create_entered.wait(5)

    contender = second.prepare(spec)

    assert isinstance(contender, _RuntimeFailure)
    assert contender.code == "RUNTIME_MATERIALIZATION_PENDING"
    assert create_attempts == 1
    release_create.set()
    worker.join(10)
    assert not worker.is_alive()
    assert len(first_result) == 1
    assert not isinstance(first_result[0], _RuntimeFailure)
    assert create_attempts == 1


def test_stale_prepare_after_concurrent_commit_leaves_one_action_and_no_intent(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    state_path = tmp_path / "paseo-actions.json"
    context = {"owner/repository": RuntimeRepositoryContext(source, "main")}
    winner = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts=context,
        state_path=state_path,
    )
    stale = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts=context,
        state_path=state_path,
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id, subject, _profile(), prompt, (prompt,)
    )
    native_git_readback = stale._git_readback
    stale_resolved_base = threading.Event()
    release_stale = threading.Event()

    def pause_after_stale_base_resolution(path, *arguments):
        resolved = native_git_readback(path, *arguments)
        stale_resolved_base.set()
        assert release_stale.wait(5)
        return resolved

    stale._git_readback = pause_after_stale_base_resolution  # type: ignore[method-assign]
    stale_results: list[object] = []
    worker = threading.Thread(
        target=lambda: stale_results.append(stale.prepare(spec))
    )
    worker.start()
    assert stale_resolved_base.wait(5)
    try:
        winner_result = winner.prepare(spec)
    finally:
        release_stale.set()
    worker.join(10)

    assert not worker.is_alive()
    assert not isinstance(winner_result, _RuntimeFailure)
    assert len(stale_results) == 1
    assert not isinstance(stale_results[0], _RuntimeFailure)
    assert stale_results[0].workspace_id == winner_result.workspace_id
    durable = json.loads(state_path.read_text(encoding="utf-8"))
    assert list(durable["actions"]) == [subject.stable_action_id]
    assert durable["workspace_intents"] == {}
    assert sum(
        args[:2] == ["workspace", "create"] for args in client.commands
    ) == 1


def test_gateway_keeps_only_fixed_materialization_summary_during_many_readbacks(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    subject = _put_subject_artifacts(store, _subject())
    profile = _profile()
    adapter = _InMemoryRuntimeProviderAdapter(
        store,
        pending_permissions={
            subject.stable_action_id: (("request:bounded", "write", "repository"),)
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

    for _ in range(80):
        gateway.transition(subject.stable_action_id, RuntimeCommand.PARK)
        gateway.progress(subject, preflight)

    durable = json.loads(gateway._store_path.read_text(encoding="utf-8"))
    record = durable["actions"][subject.stable_action_id]
    assert "observations" not in record
    assert record["materialization_observed"] is True
    assert isinstance(record["observation_digest"], str)
    assert gateway._store_path.stat().st_size < 20_000


def test_production_events_use_absolute_bounded_ring_and_stale_cursor_pages(
    tmp_path,
):
    (
        store,
        source,
        _workspace,
        client,
        adapter,
        subject,
        _spec,
    ) = _prepared_paseo_adapter(tmp_path)
    page = adapter.events(None)
    cursor = page.next_cursor
    assert cursor is not None

    for index in range(gateway_module._MAXIMUM_RUNTIME_EVENTS + 25):
        request_id = f"{index:08x}-bounded-permission"
        client.permissions = [_paseo_permission(request_id)]
        page = adapter.events(cursor)
        assert len(page.events) <= gateway_module._MAXIMUM_RUNTIME_EVENT_PAGE
        assert page.next_cursor is not None
        cursor = page.next_cursor

    durable = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    assert len(durable["events"]) == gateway_module._MAXIMUM_RUNTIME_EVENTS
    cursors = [int(event["cursor"]) for event in durable["events"]]
    assert cursors == sorted(set(cursors))
    assert durable["next_event_cursor"] == cursors[-1] + 1
    assert adapter._state_path.stat().st_size < 100_000
    provider_effects_before = [
        args
        for args in client.commands
        if args[0] == "run" or args[:2] in (["permit", "allow"], ["permit", "deny"])
    ]

    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=adapter._state_path,
    )
    stale_cursor = "0"
    delivered: list[int] = []
    while stale_cursor != str(cursors[-1]):
        stale_page = restarted.events(stale_cursor)
        assert not isinstance(stale_page, _RuntimeFailure)
        assert len(stale_page.events) <= gateway_module._MAXIMUM_RUNTIME_EVENT_PAGE
        delivered.extend(int(event.cursor) for event in stale_page.events)
        assert stale_page.next_cursor is not None
        assert stale_page.next_cursor != stale_cursor
        stale_cursor = stale_page.next_cursor

    assert delivered == cursors
    assert len(delivered) == len(set(delivered))
    assert [
        args
        for args in client.commands
        if args[0] == "run" or args[:2] in (["permit", "allow"], ["permit", "deny"])
    ] == provider_effects_before


def test_failed_event_transaction_leaves_live_and_disk_identical(tmp_path):
    (
        _store,
        _source,
        _workspace,
        client,
        adapter,
        subject,
        _spec,
    ) = _prepared_paseo_adapter(tmp_path)
    adapter.events(None)
    live_before = (
        deepcopy(adapter._actions),
        deepcopy(adapter._events),
        adapter._next_event_cursor,
    )
    disk_before = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    client.permissions = [_paseo_permission("feed0001-bounded")]
    adapter._save = lambda: (_ for _ in ()).throw(OSError("event save failed"))  # type: ignore[method-assign]

    failed = adapter.events("0")

    assert isinstance(failed, _RuntimeFailure)
    assert failed.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert adapter._actions == live_before[0]
    assert adapter._events == live_before[1]
    assert adapter._next_event_cursor == live_before[2]
    assert json.loads(adapter._state_path.read_text(encoding="utf-8")) == disk_before


def _write_paseo_transport_helper(tmp_path: Path) -> Path:
    helper = tmp_path / "paseo_transport_helper.py"
    helper.write_text(
        "\n".join(
            (
                "import sys",
                "import time",
                "import json",
                "import os",
                "import subprocess",
                "mode = sys.argv[1]",
                "if mode == 'oversized-valid-json':",
                "    sys.stdout.buffer.write(b'{\"payload\":\"' + b'x' * 2097152 + b'\"}')",
                "    sys.stdout.buffer.flush()",
                "    time.sleep(30)",
                "elif mode == 'dual-stream':",
                "    sys.stderr.buffer.write(b'e' * 900000)",
                "    sys.stderr.buffer.flush()",
                "    sys.stdout.buffer.write(b'{}')",
                "    sys.stdout.buffer.flush()",
                "elif mode == 'timeout':",
                "    time.sleep(30)",
                "elif mode == 'invalid-utf8':",
                "    sys.stdout.buffer.write(b'\\xff')",
                "    sys.stdout.buffer.flush()",
                "elif mode == 'inherited-pipe':",
                "    child = subprocess.Popen(",
                "        [sys.executable, '-c', 'import time; time.sleep(1)'],",
                "        stdout=sys.stdout,",
                "        stderr=sys.stderr,",
                "        close_fds=False,",
                "    )",
                "    sys.stdout.write(json.dumps({'grandchild_pid': child.pid}))",
                "    sys.stdout.flush()",
            )
        ),
        encoding="utf-8",
    )
    return helper


def test_paseo_transport_caps_oversized_valid_json_before_parse_and_block(
    tmp_path, monkeypatch
):
    helper = _write_paseo_transport_helper(tmp_path)
    transport = _PaseoCliTransport(sys.executable, timeout_seconds=60)
    monkeypatch.setattr(
        gateway_module.json,
        "loads",
        lambda _payload: pytest.fail("oversized valid JSON must never be parsed"),
    )
    started = time.monotonic()

    with pytest.raises(RuntimeGatewayError) as oversized:
        transport._run([str(helper), "oversized-valid-json"])

    assert oversized.value.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert time.monotonic() - started < 5


def test_paseo_transport_drains_stdout_and_stderr_concurrently_within_caps(tmp_path):
    helper = _write_paseo_transport_helper(tmp_path)
    transport = _PaseoCliTransport(sys.executable, timeout_seconds=10)

    assert transport._run([str(helper), "dual-stream"]) == {}


def test_paseo_transport_timeout_and_strict_utf8_taxonomy(tmp_path):
    helper = _write_paseo_transport_helper(tmp_path)
    timeout_transport = _PaseoCliTransport(sys.executable, timeout_seconds=1)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        timeout_transport._run([str(helper), "timeout"])
    assert time.monotonic() - started < 5

    strict_transport = _PaseoCliTransport(sys.executable, timeout_seconds=10)
    with pytest.raises(ValueError, match="UTF-8"):
        strict_transport._run([str(helper), "invalid-utf8"])


def test_paseo_transport_parent_exit_with_inherited_pipe_is_bounded_and_reaped(
    tmp_path, monkeypatch
):
    helper = _write_paseo_transport_helper(tmp_path)
    native_popen = gateway_module.subprocess.Popen
    direct_children = []

    def recording_popen(*args, **kwargs):
        child = native_popen(*args, **kwargs)
        direct_children.append(child)
        return child

    monkeypatch.setattr(gateway_module.subprocess, "Popen", recording_popen)
    started = time.monotonic()
    result = _PaseoCliTransport(sys.executable, timeout_seconds=60)._run(
        [str(helper), "inherited-pipe"]
    )

    assert isinstance(result["grandchild_pid"], int)
    assert time.monotonic() - started < 2
    assert len(direct_children) == 1
    assert direct_children[0].poll() is not None
    assert all(
        not thread.name.startswith("gwo-paseo")
        for thread in threading.enumerate()
    )
    # Let the deliberately inherited grandchild close its pipe naturally so
    # the test itself leaves no helper process behind.
    time.sleep(1.1)


def test_paseo_transport_unreapable_direct_child_fails_bounded_and_closes_pipes(
    monkeypatch,
):
    class UnreapableProcess:
        def __init__(self):
            stdout_read, self.stdout_write = os.pipe()
            stderr_read, self.stderr_write = os.pipe()
            self.stdout = os.fdopen(stdout_read, "rb", buffering=0)
            self.stderr = os.fdopen(stderr_read, "rb", buffering=0)
            self.returncode = None
            self.terminate_calls = 0
            self.kill_calls = 0
            self.wait_calls = 0

        def poll(self):
            return None

        def terminate(self):
            self.terminate_calls += 1
            raise OSError("terminate failed")

        def kill(self):
            self.kill_calls += 1
            raise OSError("kill failed")

        def wait(self, timeout=None):
            self.wait_calls += 1
            raise subprocess.TimeoutExpired("fake-paseo", timeout)

        def close_writers(self):
            os.close(self.stdout_write)
            os.close(self.stderr_write)

    fake = UnreapableProcess()
    monkeypatch.setattr(
        gateway_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: fake,
    )
    started = time.monotonic()
    with pytest.raises(RuntimeGatewayError) as failed:
        _PaseoCliTransport("paseo", timeout_seconds=1)._run(
            ["inspect", "agent:one", "--json"]
        )

    assert failed.value.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert time.monotonic() - started < 3
    assert fake.terminate_calls == 1
    assert fake.kill_calls == 1
    assert fake.wait_calls >= 1
    assert fake.stdout.closed is True
    assert fake.stderr.closed is True
    assert all(
        not thread.name.startswith("gwo-paseo")
        for thread in threading.enumerate()
    )
    fake.close_writers()


@pytest.mark.parametrize(
    ("reconciliation", "expected_lifecycle"),
    (
        ("binding", "running"),
        ("lifecycle", "parked"),
        ("fence", "running"),
        ("output", "completed"),
    ),
)
def test_paseo_reconciliation_save_failure_has_fresh_restart_parity(
    tmp_path, reconciliation, expected_lifecycle
):
    (
        store,
        source,
        _workspace,
        client,
        adapter,
        subject,
        _spec,
    ) = _prepared_paseo_adapter(tmp_path)
    if reconciliation == "lifecycle":
        assert not isinstance(
            adapter.command(subject.stable_action_id, RuntimeCommand.PARK),
            _RuntimeFailure,
        )
    elif reconciliation == "fence":
        assert not isinstance(
            adapter.command(subject.stable_action_id, RuntimeCommand.FENCE),
            _RuntimeFailure,
        )
    elif reconciliation == "output":
        record = adapter._actions[subject.stable_action_id]
        Path(record["result_file"]).parent.mkdir(parents=True, exist_ok=True)
        Path(record["result_file"]).write_bytes(
            gateway_module.canonical_bytes(
                {
                    "schema_version": "gwo.runtime.output.v1",
                    "subject_digest": subject.digest,
                    "stable_action_id": subject.stable_action_id,
                    "authority_digest": subject.authority_digest,
                    "payload": {"completed": True},
                }
            )
        )
        client.agent.lifecycle = "idle"
    live_before = deepcopy(adapter._actions)
    disk_before = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    adapter._save = lambda: (_ for _ in ()).throw(OSError("reconcile save failed"))  # type: ignore[method-assign]

    failed = adapter.observe(subject.stable_action_id)

    assert isinstance(failed, _RuntimeFailure)
    assert failed.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert adapter._actions == live_before
    assert json.loads(adapter._state_path.read_text(encoding="utf-8")) == disk_before
    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=adapter._state_path,
    )
    recovered = restarted.observe(subject.stable_action_id)
    assert not isinstance(recovered, _RuntimeFailure)
    assert recovered.lifecycle == expected_lifecycle
    if reconciliation == "fence":
        assert recovered.fenced is True
    if reconciliation == "output":
        assert recovered.output_artifact_digest is not None


@pytest.mark.parametrize(
    "command",
    (
        RuntimeCommand.PARK,
        RuntimeCommand.INTERRUPT,
        RuntimeCommand.FENCE,
        RuntimeCommand.RETIRE,
    ),
)
def test_effect_applied_ack_loss_restart_retry_is_idempotent_without_provider_reissue(
    tmp_path, command
):
    (
        store,
        source,
        _workspace,
        client,
        adapter,
        subject,
        _spec,
    ) = _prepared_paseo_adapter(tmp_path)
    native_run = client._run
    native_update_labels = client.update_labels
    effect_calls = 0

    def lose_cli_ack(args):
        nonlocal effect_calls
        if (
            command in {RuntimeCommand.PARK, RuntimeCommand.INTERRUPT}
            and args[0] == "stop"
        ) or (command is RuntimeCommand.RETIRE and args[0] == "archive"):
            effect_calls += 1
            native_run(args)
            raise TimeoutError("provider effect acknowledgement lost")
        return native_run(args)

    def lose_label_ack(agent_id, labels):
        nonlocal effect_calls
        effect_calls += 1
        native_update_labels(agent_id, labels)
        raise TimeoutError("provider effect acknowledgement lost")

    if command is RuntimeCommand.FENCE:
        client.update_labels = lose_label_ack  # type: ignore[method-assign]
    else:
        client._run = lose_cli_ack  # type: ignore[method-assign]
    first = adapter.command(subject.stable_action_id, command)

    assert isinstance(first, _RuntimeFailure)
    assert first.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert effect_calls == 1
    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=adapter._state_path,
    )
    retried = restarted.command(subject.stable_action_id, command)

    assert isinstance(retried, _CommandReceipt)
    assert retried.stable_action_id == subject.stable_action_id
    assert retried.command is command
    assert effect_calls == 1


def test_gateway_park_ack_loss_and_explicit_retry_issue_one_provider_stop(tmp_path):
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
    assert gateway.progress(subject, preflight).status == "running"
    native_run = client._run

    def stop_then_lose_ack(args):
        result = native_run(args)
        if args[0] == "stop":
            raise TimeoutError("stop acknowledgement lost")
        return result

    client._run = stop_then_lose_ack  # type: ignore[method-assign]
    first = gateway.transition(subject.stable_action_id, RuntimeCommand.PARK)
    retried = gateway.transition(subject.stable_action_id, RuntimeCommand.PARK)

    assert first.status == "parked"
    assert retried.status == "parked"
    assert len([args for args in client.commands if args[0] == "stop"]) == 1
